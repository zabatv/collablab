#!/usr/bin/env python3
"""Готовит Excel, в котором проставляют бренды — по семействам артикулов.

Ставить бренд каждому из трёхсот товаров вручную никто не станет. Но
артикулы сбиваются в семейства: NG-8, NG-10, NG-12 — это один ряд одного
производителя. Семейств около семидесяти, и заполнить надо их, а не товары.

    python3 scripts/brand_worksheet.py -o brands.xlsx

Обратно файл читает scripts/brand_map.py: он разворачивает бренды семейств
на товары и собирает frontend/data/brands.json, который знает витрина.
"""

import argparse
import os
import re
import sqlite3
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, 'backend', 'data', 'products.db')

HEAD_FILL = PatternFill('solid', fgColor='1A1A1A')
HEAD_FONT = Font(color='FFFFFF', bold=True, size=11)
FILL_ME = PatternFill('solid', fgColor='FFF4CC')
THIN = Side(style='thin', color='D0D0D0')
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def family_of(sku):
    """Голова артикула: буквы и знаки до первого числа.

    NG-8 и NG-10 дают «NG», TKC-PY-4 даёт «TKC», 200M盲板 даёт «200M».
    Если головы нет вовсе, семейством становится сам артикул."""
    text = (sku or '').strip().upper()
    if not text:
        return '—'

    head = re.match(r'^[^\d]*', text).group(0).strip(' -_./*')
    if head:
        return head

    # Артикул начинается с числа: берём число с буквами сразу за ним
    lead = re.match(r'^\d+[A-ZА-Я]*', text)
    return lead.group(0) if lead else text


def read_products(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    rows = db.execute("""
        SELECT p.sku, p.name, p.price, b.name AS brand,
               c.name AS category
          FROM products p
          LEFT JOIN brands b ON b.id = p.brand_id
          LEFT JOIN categories c ON c.id = p.category_id
         ORDER BY p.sku
    """).fetchall()

    # Тестовые позиции в каталоге магазина не значатся: заполняющему они
    # только мешают
    test = ('SPEC-', 'FORM-', 'TBL-')
    return [row for row in rows if not row['sku'].upper().startswith(test)]


def group_families(rows):
    families = {}
    for row in rows:
        families.setdefault(family_of(row['sku']), []).append(row)

    packed = []
    for name, items in families.items():
        brands = {item['brand'] for item in items if item['brand']}
        packed.append({
            'family': name,
            'count': len(items),
            # Бренд подставляется, только если у всей семьи он один и тот же
            'brand': brands.pop() if len(brands) == 1 else '',
            'sample': items[0]['name'],
            'skus': ', '.join(item['sku'] for item in items[:6])
                    + (' …' if len(items) > 6 else ''),
            'categories': ', '.join(sorted({item['category'] or '—'
                                            for item in items})[:3]),
        })

    packed.sort(key=lambda item: (-item['count'], item['family']))
    return packed


def style_header(sheet, widths):
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for cell in sheet[1]:
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(vertical='center', horizontal='center',
                                   wrap_text=True)
    sheet.row_dimensions[1].height = 30
    sheet.freeze_panes = 'A2'


def build(rows, path):
    book = Workbook()

    sheet = book.active
    sheet.title = 'Бренды по семействам'
    sheet.append(['Семейство', 'БРЕНД — заполнить', 'Товаров',
                  'Пример названия', 'Артикулы в семействе', 'Разделы'])
    style_header(sheet, [16, 22, 9, 52, 44, 34])

    families = group_families(rows)
    for item in families:
        sheet.append([item['family'], item['brand'], item['count'],
                      item['sample'], item['skus'], item['categories']])

    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.border = GRID
            cell.alignment = Alignment(vertical='top', wrap_text=True)
        row[1].fill = FILL_ME

    # Вторая вкладка — на случай, когда в семействе товары разных марок
    single = book.create_sheet('Отдельные товары')
    single.append(['Артикул', 'БРЕНД — если отличается от семейства',
                   'Название', 'Семейство', 'Раздел'])
    style_header(single, [26, 30, 58, 16, 30])

    for row in rows:
        single.append([row['sku'], row['brand'] or '', row['name'],
                       family_of(row['sku']), row['category'] or '—'])

    for row in single.iter_rows(min_row=2):
        for cell in row:
            cell.border = GRID
            cell.alignment = Alignment(vertical='top', wrap_text=True)
        row[1].fill = FILL_ME

    guide = book.create_sheet('Как заполнять')
    for line in [
        ['Как заполнять этот файл'],
        [''],
        ['1. Вкладка «Бренды по семействам» — основная. Заполните жёлтый'],
        ['   столбец «БРЕНД» напротив каждого семейства артикулов.'],
        ['   Один бренд на семейство расходится на все его товары.'],
        [''],
        ['2. Пишите название производителя так, как оно должно выглядеть'],
        ['   на сайте: CAMOZZI, SMC, PNEUMAX, AirTAC. Регистр сохранится.'],
        [''],
        ['3. Не знаете бренд — оставьте пусто. Пустая ячейка означает'],
        ['   «производитель неизвестен», и товар просто не попадёт в фильтр.'],
        ['   Не пишите наугад: покупатель выбирает по этой строке.'],
        [''],
        ['4. Если внутри семейства товары разных марок — вкладка'],
        ['   «Отдельные товары», там бренд ставится поштучно и'],
        ['   перекрывает семейство.'],
        [''],
        ['5. Заполненный файл верните мне, я соберу из него данные для'],
        ['   сайта. Логотипы пришлите отдельно картинками, если они есть:'],
        ['   без логотипа бренд показывается текстом, это нормально.'],
    ]:
        guide.append(line)
    guide.column_dimensions['A'].width = 76
    guide['A1'].font = Font(bold=True, size=13)

    book.save(path)
    return families


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=DEFAULT_DB)
    parser.add_argument('-o', '--out', default='brands.xlsx')
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        sys.exit(f'Нет базы {args.db}')

    rows = read_products(args.db)
    families = build(rows, args.out)

    filled = sum(item['count'] for item in families if item['brand'])
    print(f'{args.out}: семейств {len(families)}, товаров {len(rows)}, '
          f'бренд уже известен у {filled}')
    print('Крупнейшие семейства:')
    for item in families[:8]:
        mark = item['brand'] or '—'
        print(f'  {item["family"]:10} {item["count"]:4} товаров   {mark}')


if __name__ == '__main__':
    main()
