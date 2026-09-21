#!/usr/bin/env python3
"""Сервис сайта: бренды, слайдер на главной, счётчики и SEO.

Каталог живёт на их бэкенде — товары, цены, остатки, фотографии. Но сайт
это не только каталог: бренды, слайды на главной, статистика просмотров и
разбор готовности каталога к поиску. Ничего из этого их API не умеет и не
будет: из 1С такие вещи не приходят.

Держать это в файле у разработчика нельзя — менять всё перечисленное
должен заказчик, из админки. Поэтому здесь свой маленький сервис.

Бренды привязаны к артикулу, а не к товару: товары теперь чужие, их id нам
не принадлежат, а артикул — то общее, что есть и у них, и у нас, и в 1С.

    ADMIN_USER=admin ADMIN_PASSWORD=... CATALOG_API=http://10.8.0.2:3000 \
        python3 services/site/app.py

Логин и пароль те же, что у их админского сервера, — тогда в админке один
вход на оба. Проверка HTTP Basic, как у них. CATALOG_API нужен только
разбору каталога: за описаниями товаров сервис ходит к ним сам.
"""

import base64
import hmac
import io
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps
from xml.etree import ElementTree as ET

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOGO_DIR = os.path.join(DATA_DIR, 'logos')
SLIDE_DIR = os.path.join(DATA_DIR, 'banners')
DOC_DIR = os.path.join(DATA_DIR, 'docs')
os.makedirs(LOGO_DIR, exist_ok=True)
os.makedirs(SLIDE_DIR, exist_ok=True)
os.makedirs(DOC_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATA_DIR}/brands.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # картинки, паспорта, выгрузки 1С

db = SQLAlchemy(app)

ADMIN_USER = os.getenv('ADMIN_USER', '')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
LOGO_TYPES = {'.png', '.jpg', '.jpeg', '.webp', '.svg'}

# Паспорта, чертежи и каталоги. Список закрытый: этот сервис отдаёт файлы
# с того же домена, что и сайт, поэтому .html или .svg среди них означали
# бы чужой скрипт на robots07.com.
DOC_TYPES = {
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.csv': 'text/csv',
    '.txt': 'text/plain',
    '.dwg': 'image/vnd.dwg',
    '.dxf': 'image/vnd.dxf',
    '.zip': 'application/zip',
    '.rar': 'application/vnd.rar',
    '.7z': 'application/x-7z-compressed',
}

# Больше десятка паспортов на одну позицию — это уже не карточка товара
MAX_DOCS = 12
NAME_LIMIT = 60

# Откуда брать товары для разбора каталога. Внутри туннеля, без пароля.
CATALOG_API = os.getenv('CATALOG_API', 'http://10.8.0.2:3000').rstrip('/')

# База каталога, напрямую. Нужна ровно для одного: заводить и править
# категории. В их API этого нет — только чтение (docs/api-contract.md), — а
# раскладывать ассортимент по полкам заказчик должен сам, не дожидаясь
# правок на той стороне. Пусто — раздел категорий в админке остаётся
# read-only и честно об этом говорит.
#
#     CATALOG_DB=postgresql://shop:пароль@10.8.0.2:5432/shop
CATALOG_DB = os.getenv('CATALOG_DB', '').strip()

# Что считается непорядком в карточке товара
DESCRIPTION_MIN = 120
TITLE_MIN = 15
TITLE_MAX = 120


class Brand(db.Model):
    __tablename__ = 'brands'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(NAME_LIMIT), nullable=False, unique=True)
    logo = db.Column(db.String(255))
    sort_order = db.Column(db.Integer, default=0)

    rules = db.relationship('Rule', backref='brand', lazy='select',
                            cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'logo': self.logo,
            'sort_order': self.sort_order or 0,
            'prefixes': sorted(rule.value for rule in self.rules
                               if rule.kind == 'prefix'),
            'articles': sorted(rule.value for rule in self.rules
                               if rule.kind == 'article'),
        }


class Rule(db.Model):
    """Что относится к бренду: целый ряд артикулов или один артикул.

    Ряд — это начало артикула: NG- покрывает NG-8, NG-10, NG-PL-12. Ряды
    описывают каталог десятком строк вместо трёхсот, а отдельные артикулы
    правят исключения."""

    __tablename__ = 'brand_rules'

    id = db.Column(db.Integer, primary_key=True)
    brand_id = db.Column(db.Integer, db.ForeignKey('brands.id'), nullable=False,
                         index=True)
    kind = db.Column(db.String(10), nullable=False)  # prefix | article
    value = db.Column(db.String(120), nullable=False, index=True)



class Banner(db.Model):
    """Слайд на главной: новость, предложение, новинка.

    Картинка и есть слайд; заголовок и строка под ним рисуются поверх низа
    и могут быть пустыми — для баннера, на котором текст уже нарисован.
    Слайд без ссылки не кликается."""

    __tablename__ = 'banners'

    id = db.Column(db.Integer, primary_key=True)
    image = db.Column(db.String(255), nullable=False)
    title = db.Column(db.String(200))
    subtitle = db.Column(db.String(300))
    link = db.Column(db.String(500))
    sort_order = db.Column(db.Integer, default=0, index=True)
    is_active = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'image': self.image,
            'title': self.title or '',
            'subtitle': self.subtitle or '',
            'link': self.link or '',
            'sort_order': self.sort_order or 0,
            'is_active': bool(self.is_active),
        }


# Описание раздела. В их таблице categories такого поля нет, а 1С его не
# выгружает, поэтому текст живёт здесь и правится из админки. Ключ — их
# id категории: он не меняется, категории заведены вручную.

class CategoryText(db.Model):
    __tablename__ = 'category_texts'

    category_id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, default='')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'category_id': self.category_id,
            'description': self.description or '',
        }


# Характеристики товара. 1С отдаёт их одной строкой внутри наименования, а
# полями — не отдаёт вовсе, поэтому сайт разбирает эту строку сам. Здесь
# лежит то, что заказчик вписал руками: оно важнее разбора и переживает
# любую перевыгрузку, потому что ключ — артикул, а не id товара.

class ProductSpec(db.Model):
    __tablename__ = 'product_specs'

    article = db.Column(db.String(120), primary_key=True)   # ключ по loose()
    shown_article = db.Column(db.String(120), default='')   # как написано в 1С
    rows = db.Column(db.Text, default='[]')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        try:
            rows = json.loads(self.rows or '[]')
        except ValueError:
            rows = []
        return {'article': self.shown_article or self.article, 'rows': rows}


# Документация к товару: паспорт, чертёж, каталог производителя. Их медиа
# принимает только картинки и видео — PDF оно отклонит, — поэтому файлы
# лежат здесь. Ключ снова артикул: он переживает перевыгрузку из 1С.

class ProductDoc(db.Model):
    __tablename__ = 'product_docs'

    id = db.Column(db.Integer, primary_key=True)
    article = db.Column(db.String(120), nullable=False, index=True)  # loose()
    shown_article = db.Column(db.String(120), default='')
    title = db.Column(db.String(200), nullable=False)
    stored = db.Column(db.String(255), nullable=False)   # имя файла на диске
    suffix = db.Column(db.String(10), default='')
    size = db.Column(db.Integer, default=0)
    sort_order = db.Column(db.Integer, default=0, index=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'url': f'/files/docs/{self.stored}',
            'kind': (self.suffix or '').lstrip('.').upper(),
            'size': self.size or 0,
            'sort_order': self.sort_order or 0,
        }


# Посещаемость считается обезличенно: сколько раз открыли товар и что
# искали. Ни адреса, ни идентификатора посетителя здесь нет.

class ProductView(db.Model):
    __tablename__ = 'product_views'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, nullable=False, index=True)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class SearchQuery(db.Model):
    __tablename__ = 'search_queries'

    id = db.Column(db.Integer, primary_key=True)
    query = db.Column(db.String(255), nullable=False, index=True)
    results_count = db.Column(db.Integer, default=0)
    searched_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# Кириллица в адресе категории читается плохо и ломается при копировании,
# поэтому slug пишется латиницей. Он нужен только для уникальности: сайт
# строит адреса по id.
TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e',
    'ю': 'yu', 'я': 'ya',
}


def slugify(text):
    latin = ''.join(TRANSLIT.get(letter, letter) for letter in str(text).lower())
    clean = re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', latin)).strip('-')
    return clean[:80] or 'category'


def loose(article):
    """Артикул без регистра, пробелов и знаков.

    Их бэкенд сопоставляет артикулы с 1С так же: FA40 и FA-40 — одно и то
    же. Бренды должны совпадать по тому же правилу, иначе привязка будет
    разъезжаться на дефисах."""
    return re.sub(r'[^0-9a-zA-Zа-яА-ЯёЁ]+', '', article or '').lower()


def require_admin(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        if not ADMIN_PASSWORD:
            return jsonify({'error': 'Сервис брендов запущен без пароля: '
                                     'запись закрыта'}), 403

        header = request.headers.get('Authorization', '')
        if not header.startswith('Basic '):
            return jsonify({'error': 'Требуется вход'}), 401

        try:
            user, _, password = base64.b64decode(
                header[6:]).decode('utf-8').partition(':')
        except Exception:
            return jsonify({'error': 'Требуется вход'}), 401

        ok = (hmac.compare_digest(user, ADMIN_USER)
              and hmac.compare_digest(password, ADMIN_PASSWORD))
        if not ok:
            return jsonify({'error': 'Неверный логин или пароль'}), 401

        return view(*args, **kwargs)

    return guarded


# ===== чтение =====

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/brands')
def list_brands():
    brands = Brand.query.order_by(Brand.sort_order, Brand.name).all()
    return jsonify([brand.to_dict() for brand in brands])


@app.route('/map')
def brand_map():
    """Всё, что нужно витрине, одним запросом.

    Витрина сама раскладывает товары по брендам: товаров она получает
    сотнями, а правил тут десятки."""
    brands = Brand.query.order_by(Brand.sort_order, Brand.name).all()
    rules = Rule.query.all()

    return jsonify({
        'brands': [{'id': brand.id, 'name': brand.name, 'logo': brand.logo}
                   for brand in brands],
        'prefixes': [{'value': loose(rule.value), 'brand_id': rule.brand_id}
                     for rule in rules if rule.kind == 'prefix'],
        'articles': {loose(rule.value): rule.brand_id
                     for rule in rules if rule.kind == 'article'},
    })


@app.route('/categories/text')
def category_texts():
    """Описания всех разделов разом.

    Витрина рисует дерево одним куском, так что и тексты просит одним
    запросом — их немного, отдельный запрос на категорию не нужен."""
    rows = CategoryText.query.filter(CategoryText.description != '').all()
    return jsonify({'items': [row.to_dict() for row in rows]})


@app.route('/admin/categories/<int:category_id>/text', methods=['PUT'])
@require_admin
def set_category_text(category_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Ожидался JSON с полем description'}), 400

    text = str(data.get('description') or '').strip()

    if len(text) > 4000:
        return jsonify({'error': 'Описание длиннее 4000 символов'}), 400

    row = db.session.get(CategoryText, category_id)
    if not row:
        row = CategoryText(category_id=category_id)
        db.session.add(row)

    row.description = text
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(row.to_dict())


@app.route('/specs/<path:article>')
def product_specs(article):
    """Характеристики одной позиции, если их вписали руками.

    Пусто — витрина показывает то, что разобрала из наименования 1С."""
    row = db.session.get(ProductSpec, loose(article))
    return jsonify(row.to_dict() if row else {'article': article, 'rows': []})


def clean_specs(raw):
    """Строки таблицы: имя параметра может быть пустым, значение — нет."""
    rows = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()[:120]
        value = str(item.get('value') or '').strip()[:500]
        if value:
            rows.append({'name': name, 'value': value})
    return rows[:60]


@app.route('/admin/specs/<path:article>', methods=['PUT', 'DELETE'])
@require_admin
def set_product_specs(article):
    key = loose(article)
    if not key:
        return jsonify({'error': 'Пустой артикул'}), 400

    row = db.session.get(ProductSpec, key)

    if request.method == 'DELETE':
        if row:
            db.session.delete(row)
            db.session.commit()
        return '', 204

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Ожидался JSON с полем rows'}), 400

    rows = clean_specs(data.get('rows'))

    # Пустой список — это «вернуть как было», а не «показывать пусто»:
    # иначе у товара пропала бы и таблица из 1С
    if not rows:
        if row:
            db.session.delete(row)
            db.session.commit()
        return jsonify({'article': article, 'rows': []})

    if not row:
        row = ProductSpec(article=key)
        db.session.add(row)

    row.shown_article = article[:120]
    row.rows = json.dumps(rows, ensure_ascii=False)
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(row.to_dict())


# ===== категории в их базе =====

"""Заводить и править категории приходится в обход их API: там их только
читают. Таблица у них обычная — id, name, slug, parent_id, — и вложенность
держит любую, поэтому третий уровень работает без правок на той стороне.

Соединение открывается на запрос и закрывается сразу: правят категории
раз в неделю, держать ради этого постоянный коннект к чужой базе незачем."""


class Refuse(Exception):
    """Отказ, который можно показать человеку как есть"""
    said_out_loud = True


def catalog_db():
    if not CATALOG_DB:
        return None
    try:
        import psycopg2
    except ImportError:
        # Драйвер ставится отдельно; без него сообщение «база не отвечает»
        # отправило бы искать поломку не там
        raise Refuse('На сервере не установлен драйвер PostgreSQL: '
                     'выполните pip install -r services/site/requirements.txt '
                     'и перезапустите shop-site')
    return psycopg2.connect(CATALOG_DB, connect_timeout=5)


def needs_db(view):
    """Без адреса базы раздел не работает — и говорит, чего не хватает."""
    @wraps(view)
    def guarded(*args, **kwargs):
        if not CATALOG_DB:
            return jsonify({'error': 'Сервису не задан адрес базы каталога '
                                     '(CATALOG_DB) — категории менять нечем'}), 503
        try:
            return view(*args, **kwargs)
        except Refuse as error:
            return jsonify({'error': str(error)}), 400
        except Exception as error:
            # В их таблице имя уникально в пределах родителя. Свою проверку
            # мы делаем раньше, но гонка двух вкладок обойдёт её — тогда
            # откажет сама база, и человеку это надо сказать словами.
            if error.__class__.__name__ == 'UniqueViolation':
                return jsonify({'error': 'Категория с таким именем '
                                         'в этом разделе уже есть'}), 400
            app.logger.exception('Категории: запрос к базе не удался')
            return jsonify({'error': 'База каталога не отвечает или отказала '
                                     'в запросе'}), 502
    return guarded


def category_rows(cursor):
    cursor.execute('SELECT id, name, parent_id FROM categories ORDER BY name')
    return [{'id': row[0], 'name': row[1], 'parent_id': row[2]}
            for row in cursor.fetchall()]


def free_slug(cursor, base):
    """Уникальный slug. Адреса на сайте строятся по id, так что достаточно,
    чтобы он ни с чем не совпал."""
    cursor.execute('SELECT 1 FROM categories WHERE slug = %s', (base,))
    if not cursor.fetchone():
        return base
    return f'{base}-{secrets.token_hex(3)}'


def check_free(cursor, parent_id, name, skip_id=None):
    """Тёзка в том же разделе — почти всегда опечатка, а не замысел.

    Сравнение делается здесь, а не запросом: lower() в PostgreSQL зависит от
    локали базы и при locale=C кириллицу не сворачивает вовсе — «Реле» и
    «реле» прошли бы как разные. casefold в Python не зависит ни от чего."""
    cursor.execute(
        'SELECT id, name FROM categories WHERE parent_id IS NOT DISTINCT FROM %s',
        (parent_id,))

    wanted = name.casefold()
    for other_id, other_name in cursor.fetchall():
        if other_id == skip_id:
            continue
        if ' '.join(str(other_name).split()).casefold() == wanted:
            where = 'в этом разделе' if parent_id else 'на верхнем уровне'
            raise Refuse(f'«{other_name}» уже есть {where}')


def check_parent(cursor, parent_id):
    if parent_id is None:
        return
    cursor.execute('SELECT 1 FROM categories WHERE id = %s', (parent_id,))
    if not cursor.fetchone():
        raise Refuse('Раздел, в который переносим, не найден')


def branch_ids(cursor, category_id):
    """Категория и всё, что под ней. Нужно, чтобы не перенести раздел внутрь
    самого себя: дерево замкнулось бы кольцом и пропало со страницы."""
    found, edge = {category_id}, [category_id]
    while edge:
        cursor.execute('SELECT id FROM categories WHERE parent_id = ANY(%s)', (edge,))
        edge = [row[0] for row in cursor.fetchall() if row[0] not in found]
        found.update(edge)
    return found


def clean_category_name(raw):
    name = ' '.join(str(raw or '').split())
    if not name:
        raise Refuse('Название категории не может быть пустым')
    if len(name) > 100:
        raise Refuse('Название длиннее 100 символов')
    return name


def wanted_parent(data, field='parent_id'):
    value = data.get(field)
    if value in (None, '', 0, '0'):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise Refuse('Неверный раздел')


@app.route('/admin/categories', methods=['POST'])
@require_admin
@needs_db
def create_category():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Ожидался JSON с полем name'}), 400

    name = clean_category_name(data.get('name'))
    parent_id = wanted_parent(data)

    connection = catalog_db()
    try:
        with connection, connection.cursor() as cursor:
            check_parent(cursor, parent_id)
            check_free(cursor, parent_id, name)

            cursor.execute(
                'INSERT INTO categories (name, slug, parent_id) VALUES (%s, %s, %s)'
                ' RETURNING id',
                (name, free_slug(cursor, slugify(name)), parent_id))
            new_id = cursor.fetchone()[0]
    finally:
        connection.close()

    return jsonify({'id': new_id, 'name': name, 'parent_id': parent_id}), 201


@app.route('/admin/categories/<int:category_id>', methods=['PUT', 'DELETE'])
@require_admin
@needs_db
def change_category(category_id):
    connection = catalog_db()
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute('SELECT name, parent_id FROM categories WHERE id = %s',
                           (category_id,))
            row = cursor.fetchone()
            if not row:
                raise Refuse('Категория не найдена')

            if request.method == 'DELETE':
                cursor.execute('SELECT count(*) FROM categories WHERE parent_id = %s',
                               (category_id,))
                if cursor.fetchone()[0]:
                    raise Refuse('Сначала удалите или перенесите подкатегории')

                cursor.execute('SELECT count(*) FROM products WHERE category_id = %s',
                               (category_id,))
                inside = cursor.fetchone()[0]
                if inside:
                    raise Refuse(f'В категории {inside} товаров — '
                                 'перенесите их в другую категорию')

                cursor.execute('DELETE FROM categories WHERE id = %s', (category_id,))
                return '', 204

            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                raise Refuse('Ожидался JSON')

            name = clean_category_name(data.get('name', row[0]))
            parent_id = wanted_parent(data) if 'parent_id' in data else row[1]

            if parent_id is not None and parent_id in branch_ids(cursor, category_id):
                raise Refuse('Раздел нельзя перенести внутрь самого себя')

            check_parent(cursor, parent_id)
            check_free(cursor, parent_id, name, skip_id=category_id)

            cursor.execute('UPDATE categories SET name = %s, parent_id = %s WHERE id = %s',
                           (name, parent_id, category_id))
    finally:
        connection.close()

    return jsonify({'id': category_id, 'name': name, 'parent_id': parent_id})


@app.route('/docs/<path:article>')
def product_docs(article):
    """Паспорта и чертежи позиции. Пусто — блока на странице не будет."""
    rows = (ProductDoc.query
            .filter_by(article=loose(article))
            .order_by(ProductDoc.sort_order, ProductDoc.id)
            .all())
    return jsonify({'article': article, 'items': [row.to_dict() for row in rows]})


@app.route('/files/docs/<path:name>')
def doc_file(name):
    """Файл документа.

    PDF открывается в браузере, остальное скачивается: показывать .docx
    или .csv страницей незачем, а отдавать их inline — лишний повод для
    браузера угадывать тип."""
    safe = secure_filename(name)
    suffix = os.path.splitext(safe)[1].lower()

    answer = send_from_directory(
        DOC_DIR, safe,
        mimetype=DOC_TYPES.get(suffix, 'application/octet-stream'),
        as_attachment=(suffix != '.pdf'))
    answer.headers['X-Content-Type-Options'] = 'nosniff'
    return answer


@app.route('/admin/docs/<path:article>', methods=['POST'])
@require_admin
def upload_doc(article):
    key = loose(article)
    if not key:
        return jsonify({'error': 'Пустой артикул'}), 400

    if 'file' not in request.files:
        return jsonify({'error': 'Файл не выбран'}), 400

    file = request.files['file']
    suffix = os.path.splitext(file.filename or '')[1].lower()

    if suffix not in DOC_TYPES:
        return jsonify({'error': 'Принимаются PDF, Word, Excel, txt, csv, '
                                 'чертежи DWG и DXF, архивы zip, rar, 7z'}), 400

    if ProductDoc.query.filter_by(article=key).count() >= MAX_DOCS:
        return jsonify({'error': f'У позиции уже {MAX_DOCS} документов — '
                                 'удалите лишние'}), 400

    stored = secure_filename(f'{key[:40] or "doc"}_{secrets.token_hex(4)}{suffix}')
    path = os.path.join(DOC_DIR, stored)
    file.save(path)

    # Имя файла — сносное название по умолчанию: «Паспорт SC-32.pdf» читается
    title = (request.form.get('title') or '').strip()
    if not title:
        title = os.path.splitext(os.path.basename(file.filename or ''))[0].strip()
    title = (title or 'Документ')[:200]

    last = (db.session.query(db.func.max(ProductDoc.sort_order))
            .filter_by(article=key).scalar())

    row = ProductDoc(article=key, shown_article=article[:120], title=title,
                     stored=stored, suffix=suffix,
                     size=os.path.getsize(path), sort_order=(last or 0) + 1)
    db.session.add(row)
    db.session.commit()
    return jsonify(row.to_dict()), 201


@app.route('/admin/docs/<int:doc_id>', methods=['PUT', 'DELETE'])
@require_admin
def change_doc(doc_id):
    row = db.session.get(ProductDoc, doc_id)
    if not row:
        return jsonify({'error': 'Документ не найден'}), 404

    if request.method == 'DELETE':
        # Файл уходит вместе с записью: иначе папка растёт молча
        try:
            os.remove(os.path.join(DOC_DIR, os.path.basename(row.stored)))
        except OSError:
            pass
        db.session.delete(row)
        db.session.commit()
        return '', 204

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Ожидался JSON с полем title'}), 400

    title = str(data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Название не может быть пустым'}), 400

    row.title = title[:200]
    db.session.commit()
    return jsonify(row.to_dict())


@app.route('/logos/<path:name>')
def logo_file(name):
    return send_from_directory(LOGO_DIR, name)


# ===== запись =====

def clean_name(raw):
    name = ' '.join(str(raw or '').split())
    if not name:
        return None, 'Название бренда обязательно'
    if len(name) > NAME_LIMIT:
        return None, f'Название длиннее {NAME_LIMIT} символов'
    return name, None


@app.route('/admin/brands', methods=['POST'])
@require_admin
def create_brand():
    source = request.form if request.files else (request.get_json() or {})
    name, problem = clean_name(source.get('name'))
    if problem:
        return jsonify({'error': problem}), 400

    if Brand.query.filter(db.func.lower(Brand.name) == name.lower()).first():
        return jsonify({'error': f'Бренд «{name}» уже есть'}), 409

    brand = Brand(name=name, sort_order=Brand.query.count())
    db.session.add(brand)
    db.session.commit()

    if 'image' in request.files:
        stored, problem = save_logo(brand, request.files['image'])
        if problem:
            return jsonify({'error': problem}), 400

    return jsonify(brand.to_dict()), 201


@app.route('/admin/brands/<int:brand_id>', methods=['PUT'])
@require_admin
def update_brand(brand_id):
    brand = db.session.get(Brand, brand_id)
    if not brand:
        return jsonify({'error': 'Бренд не найден'}), 404

    data = request.get_json() or {}
    if 'name' in data:
        name, problem = clean_name(data['name'])
        if problem:
            return jsonify({'error': problem}), 400

        twin = Brand.query.filter(db.func.lower(Brand.name) == name.lower()).first()
        if twin and twin.id != brand.id:
            return jsonify({'error': f'Бренд «{name}» уже есть'}), 409
        brand.name = name

    if 'sort_order' in data:
        brand.sort_order = int(data['sort_order'] or 0)

    db.session.commit()
    return jsonify(brand.to_dict())


@app.route('/admin/brands/<int:brand_id>', methods=['DELETE'])
@require_admin
def delete_brand(brand_id):
    brand = db.session.get(Brand, brand_id)
    if not brand:
        return jsonify({'error': 'Бренд не найден'}), 404

    drop_logo(brand)
    db.session.delete(brand)
    db.session.commit()
    return '', 204


@app.route('/admin/brands/<int:brand_id>/rules', methods=['PUT'])
@require_admin
def set_rules(brand_id):
    """Ряды артикулов и отдельные артикулы бренда — целиком, одним списком"""
    brand = db.session.get(Brand, brand_id)
    if not brand:
        return jsonify({'error': 'Бренд не найден'}), 404

    data = request.get_json() or {}
    prefixes = [str(value).strip() for value in data.get('prefixes', [])]
    articles = [str(value).strip() for value in data.get('articles', [])]

    for value in prefixes + articles:
        if len(value) > 120:
            return jsonify({'error': f'Слишком длинная строка: {value[:40]}…'}), 400

    Rule.query.filter_by(brand_id=brand.id).delete()
    for value in dict.fromkeys(prefixes):
        if value:
            db.session.add(Rule(brand_id=brand.id, kind='prefix', value=value))
    for value in dict.fromkeys(articles):
        if value:
            db.session.add(Rule(brand_id=brand.id, kind='article', value=value))

    db.session.commit()
    return jsonify(brand.to_dict())


def save_logo(brand, file):
    suffix = os.path.splitext(file.filename or '')[1].lower()
    if suffix not in LOGO_TYPES:
        return None, 'Логотип принимается в PNG, JPEG, WebP или SVG'

    drop_logo(brand)
    stored = f'{brand.id}_{secrets.token_hex(4)}{suffix}'
    file.save(os.path.join(LOGO_DIR, secure_filename(stored)))

    brand.logo = f'/logos/{stored}'
    db.session.commit()
    return brand.logo, None


def drop_logo(brand):
    if not brand.logo:
        return
    old = os.path.join(LOGO_DIR, os.path.basename(brand.logo))
    if os.path.isfile(old):
        os.remove(old)
    brand.logo = None


@app.route('/admin/brands/<int:brand_id>/logo', methods=['POST'])
@require_admin
def upload_logo(brand_id):
    brand = db.session.get(Brand, brand_id)
    if not brand:
        return jsonify({'error': 'Бренд не найден'}), 404
    if 'image' not in request.files:
        return jsonify({'error': 'Файл не пришёл'}), 400

    stored, problem = save_logo(brand, request.files['image'])
    if problem:
        return jsonify({'error': problem}), 400
    return jsonify(brand.to_dict())


@app.route('/admin/brands/<int:brand_id>/logo', methods=['DELETE'])
@require_admin
def clear_logo(brand_id):
    brand = db.session.get(Brand, brand_id)
    if not brand:
        return jsonify({'error': 'Бренд не найден'}), 404

    drop_logo(brand)
    db.session.commit()
    return jsonify(brand.to_dict())



# ===== слайдер на главной =====

@app.route('/banners')
def public_banners():
    """То, что видит посетитель: только включённые слайды, по порядку"""
    slides = (Banner.query.filter_by(is_active=True)
              .order_by(Banner.sort_order, Banner.id).all())
    return jsonify([slide.to_dict() for slide in slides])


@app.route('/admin/banners')
@require_admin
def admin_banners():
    slides = Banner.query.order_by(Banner.sort_order, Banner.id).all()
    return jsonify([slide.to_dict() for slide in slides])


def save_picture(file, folder, prefix):
    suffix = os.path.splitext(file.filename or '')[1].lower()
    if suffix not in LOGO_TYPES:
        return None, 'Картинка принимается в PNG, JPEG, WebP или SVG'

    stored = secure_filename(f'{prefix}_{secrets.token_hex(4)}{suffix}')
    file.save(os.path.join(folder, stored))
    return stored, None


@app.route('/admin/banners', methods=['POST'])
@require_admin
def create_banner():
    if 'image' not in request.files:
        return jsonify({'error': 'Картинка слайда обязательна'}), 400

    stored, problem = save_picture(request.files['image'], SLIDE_DIR, 'slide')
    if problem:
        return jsonify({'error': problem}), 400

    slide = Banner(
        image=f'/files/banners/{stored}',
        title=(request.form.get('title') or '').strip()[:200],
        subtitle=(request.form.get('subtitle') or '').strip()[:300],
        link=(request.form.get('link') or '').strip()[:500],
        sort_order=Banner.query.count(),
        is_active=True,
    )
    db.session.add(slide)
    db.session.commit()
    return jsonify(slide.to_dict()), 201


@app.route('/admin/banners/<int:banner_id>', methods=['PUT'])
@require_admin
def update_banner(banner_id):
    slide = db.session.get(Banner, banner_id)
    if not slide:
        return jsonify({'error': 'Слайд не найден'}), 404

    data = request.get_json() or {}
    if 'title' in data:
        slide.title = str(data['title'] or '').strip()[:200]
    if 'subtitle' in data:
        slide.subtitle = str(data['subtitle'] or '').strip()[:300]
    if 'link' in data:
        slide.link = str(data['link'] or '').strip()[:500]
    if 'is_active' in data:
        slide.is_active = bool(data['is_active'])

    db.session.commit()
    return jsonify(slide.to_dict())


@app.route('/admin/banners/<int:banner_id>', methods=['DELETE'])
@require_admin
def delete_banner(banner_id):
    slide = db.session.get(Banner, banner_id)
    if not slide:
        return jsonify({'error': 'Слайд не найден'}), 404

    picture = os.path.join(SLIDE_DIR, os.path.basename(slide.image or ''))
    if os.path.isfile(picture):
        os.remove(picture)

    db.session.delete(slide)
    db.session.commit()
    return '', 204


@app.route('/admin/banners/order', methods=['PUT'])
@require_admin
def order_banners():
    ids = (request.get_json() or {}).get('ids')
    if not isinstance(ids, list):
        return jsonify({'error': 'Ожидался список ids'}), 400

    for place, banner_id in enumerate(ids):
        slide = db.session.get(Banner, banner_id)
        if slide:
            slide.sort_order = place

    db.session.commit()
    return jsonify({'ok': True})


@app.route('/files/banners/<path:name>')
def banner_file(name):
    return send_from_directory(SLIDE_DIR, name)


# ===== счётчики =====

@app.route('/track/view/<int:product_id>', methods=['POST'])
def track_view(product_id):
    db.session.add(ProductView(product_id=product_id))
    db.session.commit()
    return '', 204


@app.route('/track/search', methods=['POST'])
def track_search():
    data = request.get_json(silent=True) or {}
    text = ' '.join(str(data.get('query') or '').split())[:255]
    if not text:
        return '', 204

    db.session.add(SearchQuery(query=text.lower(),
                               results_count=int(data.get('results') or 0)))
    db.session.commit()
    return '', 204


# ===== SEO =====

@app.route('/admin/seo/traffic')
@require_admin
def seo_traffic():
    """Что открывают и что ищут"""
    try:
        days = max(1, min(int(request.args.get('days', 30)), 365))
    except ValueError:
        return jsonify({'error': 'Параметр days должен быть числом'}), 400

    since = datetime.utcnow() - timedelta(days=days)

    views = (db.session.query(ProductView.product_id,
                              db.func.count(ProductView.id).label('n'))
             .filter(ProductView.viewed_at >= since)
             .group_by(ProductView.product_id)
             .order_by(db.text('n DESC')).limit(10).all())

    searches = (db.session.query(SearchQuery.query,
                                 db.func.count(SearchQuery.id).label('n'),
                                 db.func.max(SearchQuery.results_count))
                .filter(SearchQuery.searched_at >= since)
                .group_by(SearchQuery.query)
                .order_by(db.text('n DESC')).limit(20).all())

    # Имена товаров знает их каталог, а не мы: спрашиваем по id
    names = {}
    for product_id, _ in views:
        item = fetch_json(f'/products/{product_id}') or {}
        names[product_id] = (item.get('name') or 'Товар недоступен',
                             item.get('article') or '')

    def by_day(model, stamp):
        return dict(db.session.query(db.func.date(stamp), db.func.count(model.id))
                    .filter(stamp >= since)
                    .group_by(db.func.date(stamp)).all())

    seen = by_day(ProductView, ProductView.viewed_at)
    asked = by_day(SearchQuery, SearchQuery.searched_at)

    start = (datetime.utcnow() - timedelta(days=days - 1)).date()
    days_row = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        days_row.append({'date': day,
                         'views': seen.get(day, 0),
                         'searches': asked.get(day, 0)})

    return jsonify({
        'days': days,
        'by_day': days_row,
        'total_views': db.session.query(db.func.count(ProductView.id))
                       .filter(ProductView.viewed_at >= since).scalar() or 0,
        'total_searches': db.session.query(db.func.count(SearchQuery.id))
                          .filter(SearchQuery.searched_at >= since).scalar() or 0,
        'top_products': [{'id': product_id, 'views': count,
                          'name': names.get(product_id, ('', ''))[0],
                          'sku': names.get(product_id, ('', ''))[1]}
                         for product_id, count in views],
        'top_searches': [{'query': text, 'count': count, 'results': results}
                         for text, count, results in searches],
        'empty_searches': [{'query': text, 'count': count}
                           for text, count, results in searches if not results][:10],
    })


def fetch_json(path):
    """Запрос к их каталогу внутри туннеля. Молчит, если не ответили."""
    try:
        with urllib.request.urlopen(f'{CATALOG_API}{path}', timeout=10) as answer:
            return json.loads(answer.read().decode('utf-8'))
    except Exception:
        return None


# Разбор каталога перечитывает триста карточек, поэтому держится десять минут
CATALOG_CACHE = {'at': 0, 'items': []}
CATALOG_TTL = 600


def catalogue():
    if time.time() - CATALOG_CACHE['at'] < CATALOG_TTL and CATALOG_CACHE['items']:
        return CATALOG_CACHE['items']

    first = fetch_json('/products?limit=100&page=1')
    if not first:
        return []

    items = list(first.get('items') or [])
    for page in range(2, min(first.get('pages', 1), 20) + 1):
        more = fetch_json(f'/products?limit=100&page={page}')
        items.extend((more or {}).get('items') or [])

    # Описание есть только в карточке, а оно и решает, готов ли товар
    with ThreadPoolExecutor(max_workers=8) as pool:
        cards = pool.map(lambda item: fetch_json(f'/products/{item["id"]}'), items)

    full = [card or item for card, item in zip(cards, items)]
    CATALOG_CACHE.update(at=time.time(), items=full)
    return full


@app.route('/admin/seo/catalog')
@require_admin
def seo_catalog():
    """Что в каталоге мешает товару попасть в поиск"""
    products = [item for item in catalogue() if item.get('is_active', True)]
    total = len(products)

    if not total:
        return jsonify({'total': 0, 'ready': 0, 'issues': [],
                        'error': 'Каталог не отвечает'}), 200

    def text_of(item):
        return (item.get('description') or '').strip()

    def name_of(item):
        return (item.get('name') or '').strip()

    seen_names, seen_articles = {}, {}
    for item in products:
        seen_names.setdefault(name_of(item).lower(), []).append(item)
        seen_articles.setdefault(str(item.get('article') or '').strip().lower(),
                                 []).append(item)

    twins = lambda groups: [item for group in groups.values()
                            if len(group) > 1 for item in group]

    issues = [
        {'key': 'no_description', 'title': 'Без описания',
         'why': 'Поисковику нечего показать в сниппете и не за что ранжировать',
         'severity': 'high',
         'products': [item for item in products if not text_of(item)]},
        {'key': 'short_description',
         'title': f'Описание короче {DESCRIPTION_MIN} символов',
         'why': 'Слишком мало текста, чтобы страница отвечала на запрос',
         'severity': 'medium',
         'products': [item for item in products
                      if text_of(item) and len(text_of(item)) < DESCRIPTION_MIN]},
        {'key': 'no_image', 'title': 'Без фотографии',
         'why': 'Товар не попадёт в поиск по картинкам и в товарную выдачу',
         'severity': 'high',
         'products': [item for item in products
                      if not (item.get('photo') or item.get('media'))]},
        {'key': 'long_name', 'title': f'Название длиннее {TITLE_MAX} символов',
         'why': 'В выдаче обрежется многоточием, важное может не попасть',
         'severity': 'medium',
         'products': [item for item in products if len(name_of(item)) > TITLE_MAX]},
        {'key': 'short_name', 'title': f'Название короче {TITLE_MIN} символов',
         'why': 'Слишком общее название проигрывает конкурентам в выдаче',
         'severity': 'low',
         'products': [item for item in products if len(name_of(item)) < TITLE_MIN]},
        {'key': 'duplicate_name', 'title': 'Одинаковые названия',
         'why': 'Поисковик считает такие страницы дублями и показывает одну',
         'severity': 'high', 'products': twins(seen_names)},
        {'key': 'duplicate_sku', 'title': 'Одинаковые артикулы',
         'why': 'Два товара с одним артикулом путают и покупателя, и 1С',
         'severity': 'high', 'products': twins(seen_articles)},
        {'key': 'no_price', 'title': 'Без цены',
         'why': 'Товар без цены не попадает в товарную выдачу',
         'severity': 'high',
         'products': [item for item in products if not item.get('price')]},
        {'key': 'out_of_stock', 'title': 'Нулевой остаток',
         'why': 'Отсутствие в наличии понижает товар в выдаче',
         'severity': 'low',
         'products': [item for item in products if not item.get('quantity')]},
        {'key': 'no_brand', 'title': 'Без бренда',
         'why': 'По названию производителя ищут чаще, чем по типу детали',
         'severity': 'low',
         'products': [item for item in products
                      if not brand_for(str(item.get('article') or ''))]},
    ]

    serious = {item['id'] for issue in issues if issue['severity'] == 'high'
               for item in issue['products']}

    brief = lambda item: {'id': item['id'], 'sku': item.get('article') or '',
                          'name': item.get('name') or '',
                          'price': item.get('price'),
                          'image': item.get('photo')}

    return jsonify({
        'total': total,
        'ready': total - len(serious),
        'issues': [{
            'key': issue['key'], 'title': issue['title'], 'why': issue['why'],
            'severity': issue['severity'], 'count': len(issue['products']),
            'products': [brief(item) for item in issue['products'][:100]],
        } for issue in issues],
    })


def brand_for(article):
    """Бренд артикула по нашим же правилам — тем, что выше в этом файле"""
    key = loose(article)
    if not key:
        return None

    exact = Rule.query.filter_by(kind='article').all()
    for rule in exact:
        if loose(rule.value) == key:
            return rule.brand_id

    heads = sorted(Rule.query.filter_by(kind='prefix').all(),
                   key=lambda rule: -len(loose(rule.value)))
    for rule in heads:
        if key.startswith(loose(rule.value)):
            return rule.brand_id
    return None



# ===== выгрузка 1С =====

# Хвост складской ячейки, который 1С дописывает к артикулу: « *3*7*34»
CELL_TAIL = re.compile(r'\s+\*[\d*]+$')


def tag(element):
    """Имя узла без пространства имён: в CommerceML оно у каждого"""
    return element.tag.rsplit('}', 1)[-1]


def child_text(element, name):
    for node in element:
        if tag(node) == name:
            return (node.text or '').strip()
    return ''


def parse_commerceml(data):
    """Товары из import*.xml: артикул, название, описание, группа.

    Читается только то, что нужно для заведения карточки. Владелец с ИНН,
    реквизиты, налоги и прочее из файла не берутся вовсе.
    """
    root = ET.parse(io.BytesIO(data)).getroot()

    groups = {}

    def walk_groups(node, trail):
        for child in node:
            if tag(child) != 'Группа':
                continue
            name = child_text(child, 'Наименование')
            path = trail + [name] if name else trail
            ident = child_text(child, 'Ид')
            if ident:
                groups[ident] = ' / '.join(path)
            for deeper in child:
                if tag(deeper) == 'Группы':
                    walk_groups(deeper, path)

    # Классификатор лежит в <Классификатор><Группы>
    for element in root.iter():
        if tag(element) == 'Классификатор':
            for node in element:
                if tag(node) == 'Группы':
                    walk_groups(node, [])

    items = []
    for element in root.iter():
        if tag(element) != 'Товар':
            continue

        article = CELL_TAIL.sub('', ' '.join(child_text(element, 'Артикул').split()))
        name = ' '.join(child_text(element, 'Наименование').split())
        status = child_text(element, 'Статус')

        group = ''
        for node in element:
            if tag(node) == 'Группы':
                for ident in node:
                    group = groups.get((ident.text or '').strip(), '')
                    break

        items.append({
            'external_id': child_text(element, 'Ид').split('#')[0],
            'article': article[:100],
            'name': name[:500],
            'description': child_text(element, 'Описание')[:20000],
            'group': group,
            'deleted': status.lower().startswith('удал') or 'НА УДАЛЕНИЕ' in name.upper(),
        })

    return items


@app.route('/admin/1c/analyze', methods=['POST'])
@require_admin
def analyze_1c():
    """Что в выгрузке есть, а на сайте нет"""
    if 'file' not in request.files:
        return jsonify({'error': 'Файл выгрузки не пришёл'}), 400

    try:
        items = parse_commerceml(request.files['file'].read())
    except ET.ParseError as error:
        return jsonify({'error': f'Это не похоже на выгрузку 1С: {error}'}), 400

    if not items:
        return jsonify({'error': 'В файле нет товаров. Нужен import*.xml, '
                                 'а не offers*.xml'}), 400

    # Без каталога сверять не с чем, и все товары покажутся новыми. Лучше
    # честно отказаться, чем предложить завести три тысячи дублей.
    if fetch_json('/products?limit=1') is None:
        return jsonify({'error': 'Каталог не отвечает — сверить не с чем. '
                                 'Проверьте, запущен ли сервер каталога и '
                                 'поднят ли туннель.'}), 502

    # Что уже на сайте — по мягкому ключу артикула, как сопоставляет их 1С
    known = {loose(item.get('article')) for item in catalogue()}

    seen = {}
    ready, skipped = [], {'без артикула': 0, 'на удаление': 0, 'уже на сайте': 0,
                          'повторы в файле': 0}

    for item in items:
        if not item['article']:
            skipped['без артикула'] += 1
            continue
        if item['deleted']:
            skipped['на удаление'] += 1
            continue

        key = loose(item['article'])
        if key in known:
            skipped['уже на сайте'] += 1
            continue
        if key in seen:
            skipped['повторы в файле'] += 1
            continue

        seen[key] = True
        ready.append({
            'article': item['article'],
            'name': item['name'],
            'description': item['description'],
            'group': item['group'],
        })

    return jsonify({
        'total': len(items),
        'ready': ready,
        'skipped': skipped,
    })


@app.route('/admin/1c/create', methods=['POST'])
@require_admin
def create_from_1c():
    """Заводит отмеченные товары в их каталоге.

    Создание идёт через их же админский API, а не мимо него: там проверки
    артикула, длины полей и защита от дублей. Логин и пароль берутся из
    этого запроса и передаются дальше — своих паролей сервис не хранит.
    """
    data = request.get_json() or {}
    items = data.get('items') or []
    category_id = data.get('category_id')

    if not items:
        return jsonify({'error': 'Нечего заводить'}), 400
    if len(items) > 500:
        return jsonify({'error': 'За раз не больше 500 товаров'}), 400

    admin_api = os.getenv('ADMIN_API', 'http://10.8.0.2:3001').rstrip('/')
    auth = request.headers.get('Authorization', '')

    created, failed = [], []
    for item in items:
        body = json.dumps({
            'article': str(item.get('article') or '').strip(),
            'name': str(item.get('name') or '').strip(),
            'description': item.get('description') or '',
            'category_id': category_id,
            'is_active': bool(item.get('is_active', True)),
        }).encode('utf-8')

        call = urllib.request.Request(f'{admin_api}/products', data=body,
                                      method='POST')
        call.add_header('Content-Type', 'application/json')
        if auth:
            call.add_header('Authorization', auth)

        try:
            with urllib.request.urlopen(call, timeout=20) as answer:
                created.append(json.loads(answer.read().decode('utf-8'))['article'])
        except urllib.error.HTTPError as error:
            detail = {}
            try:
                detail = json.loads(error.read().decode('utf-8'))
            except Exception:
                pass
            failed.append({'article': item.get('article'),
                           'error': detail.get('error') or f'ошибка {error.code}'})
        except Exception as error:
            failed.append({'article': item.get('article'), 'error': str(error)})

    # Каталог изменился — разбор SEO и список для сверки перечитываем заново
    CATALOG_CACHE['at'] = 0

    return jsonify({'created': len(created), 'failed': failed})


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    if not ADMIN_PASSWORD:
        print('ВНИМАНИЕ: ADMIN_PASSWORD не задан — правка брендов закрыта')

    app.run(host=os.getenv('HOST', '127.0.0.1'),
            port=int(os.getenv('PORT', 5001)),
            debug=os.getenv('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes'))
