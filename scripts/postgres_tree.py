#!/usr/bin/env python3
"""Собирает SQL, который ставит наше дерево категорий в их базу.

Их бэкенд берёт категории из классификатора 1С, а пока классификатора нет —
из первого слова названия товара: «Фитинг», «Клапан», «Прочее». Разделов,
подкатегорий и порядка у них нет, и API их менять не умеет.

Наше дерево при этом обычная таблица с parent_id — такая же, как у них.
Значит его можно перенести напрямую в базу, минуя API:

    python3 scripts/postgres_tree.py -o tree.sql
    psql -U shop -d shop -f tree.sql        # на их машине

Файл делает три вещи в одной транзакции: создаёт наши категории, переставляет
товары по артикулу и убирает их старые категории, оставшиеся пустыми. Если
что-то пойдёт не так, не применится ничего.
"""

import argparse
import os
import re
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, 'backend', 'data', 'products.db')

# Артикулы тестовых позиций: в их базе их нет и переносить нечего
TEST_PREFIXES = ('SPEC-', 'FORM-', 'TBL-')

TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e',
    'ю': 'yu', 'я': 'ya',
}


def slugify(text):
    latin = ''.join(TRANSLIT.get(letter, letter) for letter in text.lower())
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', latin)).strip('-') or 'category'


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def read_catalogue(path):
    """Дерево категорий и привязка артикулов к полному пути"""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row

    nodes = {row['id']: dict(row) for row in
             db.execute('SELECT id, name, parent_id, sort_order FROM categories')}

    def full_path(node_id):
        trail, seen = [], set()
        while node_id and node_id not in seen:
            seen.add(node_id)
            node = nodes[node_id]
            trail.append(node['name'])
            node_id = node['parent_id']
        return ' / '.join(reversed(trail))

    tree = []
    for node_id, node in nodes.items():
        tree.append({
            'path': full_path(node_id),
            'name': node['name'],
            'parent_path': full_path(node['parent_id']) if node['parent_id'] else None,
            'depth': full_path(node_id).count(' / '),
            'sort_order': node['sort_order'] or 0,
        })

    # Slug должен быть уникален: одинаковые имена в разных ветках встречаются
    taken = set()
    for node in sorted(tree, key=lambda item: (item['depth'], item['path'])):
        base = slugify(node['path'].replace(' / ', '-'))[:80]
        slug, number = base, 1
        while slug in taken:
            number += 1
            slug = f'{base}-{number}'
        taken.add(slug)
        node['slug'] = slug

    products = []
    for row in db.execute(
            'SELECT sku, category_id FROM products WHERE sku IS NOT NULL'):
        if row['sku'].upper().startswith(TEST_PREFIXES):
            continue
        if row['category_id'] in nodes:
            products.append((row['sku'].strip(), full_path(row['category_id'])))

    # Ветка, в которой не лежит ни одного товара, — пустая плитка на сайте
    filled = set()
    for _, path in products:
        parts = path.split(' / ')
        for depth in range(len(parts)):
            filled.add(' / '.join(parts[:depth + 1]))

    tree = [node for node in tree if node['path'] in filled]

    return tree, products


def build_sql(tree, products):
    out = []
    add = out.append

    add('-- Дерево каталога ROBOT для базы магазина.')
    add('-- Создаёт категории, переставляет товары по артикулу, убирает')
    add('-- пустые категории, оставшиеся от прежней разбивки.')
    add('-- Всё в одной транзакции: при ошибке не применяется ничего.')
    add('')
    add('\\set ON_ERROR_STOP on')
    add('BEGIN;')
    add('')
    add('CREATE TEMP TABLE tree_import (')
    add('  path text PRIMARY KEY, name text NOT NULL, slug text NOT NULL,')
    add('  parent_path text, depth int NOT NULL, sort_order int NOT NULL DEFAULT 0,')
    add('  new_id int')
    add(') ON COMMIT DROP;')
    add('')

    add('INSERT INTO tree_import (path, name, slug, parent_path, depth, sort_order) VALUES')
    rows = [f'  ({quote(node["path"])}, {quote(node["name"])}, {quote(node["slug"])}, '
            f'{quote(node["parent_path"]) if node["parent_path"] else "NULL"}, '
            f'{node["depth"]}, {node["sort_order"]})'
            for node in sorted(tree, key=lambda item: (item['depth'], item['path']))]
    add(',\n'.join(rows) + ';')
    add('')

    add('CREATE TEMP TABLE product_category (')
    add('  article text PRIMARY KEY, path text NOT NULL')
    add(') ON COMMIT DROP;')
    add('')
    add('INSERT INTO product_category (article, path) VALUES')
    pairs = [f'  ({quote(sku)}, {quote(path)})' for sku, path in sorted(products)]
    add(',\n'.join(pairs) + ';')
    add('')

    add("""-- 0. Второй запуск создал бы дерево заново, рядом с первым. Такие
-- слаги, как у нас, взяться в базе больше неоткуда — значит файл уже
-- применяли, и работа прекращается, не тронув ничего.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM categories c JOIN tree_import t ON t.slug = c.slug) THEN
    RAISE EXCEPTION 'Дерево уже установлено: этот файл применяли раньше. '
      'Чтобы поставить его заново, сначала удалите прежние наши категории.';
  END IF;
END $$;""")
    add('')

    add("""-- 1. Категории создаются сверху вниз, чтобы родитель уже существовал.
-- Slug при совпадении с чужим получает номер: ломать чужие ссылки нельзя.
DO $$
DECLARE
  node record;
  parent int;
  fresh int;
  candidate text;
  attempt int;
BEGIN
  FOR node IN SELECT * FROM tree_import ORDER BY depth, path LOOP
    SELECT new_id INTO parent FROM tree_import WHERE path = node.parent_path;

    candidate := node.slug;
    attempt := 1;
    WHILE EXISTS (SELECT 1 FROM categories WHERE slug = candidate) LOOP
      attempt := attempt + 1;
      candidate := node.slug || '-' || attempt;
    END LOOP;

    INSERT INTO categories (name, slug, parent_id)
         VALUES (node.name, candidate, parent)
      RETURNING id INTO fresh;

    UPDATE tree_import SET new_id = fresh WHERE path = node.path;
  END LOOP;
END $$;""")
    add('')

    add("""-- 2. Товары переставляются по точному артикулу
UPDATE products p
   SET category_id = t.new_id
  FROM product_category pc
  JOIN tree_import t ON t.path = pc.path
 WHERE p.article = pc.article;""")
    add('')

    add("""-- Затем по мягкому ключу: регистр, пробелы, дефисы и знаки не в счёт
-- (FA40 против FA-40). Берётся только однозначная пара с обеих сторон.
UPDATE products p
   SET category_id = t.new_id
  FROM (
        SELECT lower(regexp_replace(article, '[^[:alnum:]]', '', 'g')) AS key,
               min(path) AS path
          FROM product_category
         GROUP BY 1
        HAVING count(*) = 1
       ) pc
  JOIN tree_import t ON t.path = pc.path
 WHERE lower(regexp_replace(p.article, '[^[:alnum:]]', '', 'g')) = pc.key
   AND NOT EXISTS (SELECT 1 FROM product_category exact WHERE exact.article = p.article)
   AND (SELECT count(*) FROM products other
         WHERE lower(regexp_replace(other.article, '[^[:alnum:]]', '', 'g')) = pc.key) = 1;""")
    add('')

    add("""-- 3. Старые категории убираются, только если в них никого не осталось
DELETE FROM categories c
 WHERE c.id NOT IN (SELECT new_id FROM tree_import WHERE new_id IS NOT NULL)
   AND NOT EXISTS (SELECT 1 FROM products p WHERE p.category_id = c.id)
   AND NOT EXISTS (SELECT 1 FROM categories k WHERE k.parent_id = c.id);""")
    add('')

    add("""-- Что получилось. Смотреть до COMMIT: временные таблицы после него исчезнут.
SELECT count(*) AS "категорий создано" FROM tree_import WHERE new_id IS NOT NULL;

SELECT count(*) AS "товаров в нашем дереве"
  FROM products p JOIN tree_import t ON t.new_id = p.category_id;

SELECT p.article AS "артикул", p.name AS "товар"
  FROM products p
 WHERE p.category_id IS NULL
    OR p.category_id NOT IN (SELECT new_id FROM tree_import WHERE new_id IS NOT NULL)
 ORDER BY p.article;

-- Товары лежат в подкатегориях, поэтому раздел считается по всей ветке
SELECT root.path AS "раздел", count(p.id) AS "товаров"
  FROM tree_import root
  JOIN tree_import branch
    ON branch.path = root.path OR branch.path LIKE root.path || ' / %'
  LEFT JOIN products p ON p.category_id = branch.new_id
 WHERE root.depth = 0
 GROUP BY root.path ORDER BY 2 DESC;""")
    add('')
    add('COMMIT;')
    add('')
    return '\n'.join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', default=DEFAULT_DB, help='наша база SQLite')
    parser.add_argument('-o', '--out', help='куда написать SQL (по умолчанию на экран)')
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        sys.exit(f'Нет базы {args.db}')

    tree, products = read_catalogue(args.db)
    sql = build_sql(tree, products)

    if args.out:
        with open(args.out, 'w', encoding='utf-8') as target:
            target.write(sql)
        print(f'{args.out}: категорий {len(tree)}, товаров {len(products)}')
    else:
        print(sql)


if __name__ == '__main__':
    main()
