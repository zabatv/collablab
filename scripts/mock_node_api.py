#!/usr/bin/env python3
"""Заглушка их бэкенда — два сервера по docs/api-contract.md.

Их настоящий бэкенд живёт на Windows рядом с 1С, и пока до него не
дотянуться, проверять слой совместимости не на чем. Этот скрипт поднимает
два сервера с теми же адресами, полями и кодами ошибок, что в контракте,
и кормит их нашим же каталогом.

    python3 scripts/mock_node_api.py

    :3000  публичный, только чтение, только активные товары
    :3001  админка: служебные поля, создание и правка, загрузка медиа

Работает он с копией базы во временном файле, поэтому что угодно можно
создавать и править: настоящий каталог не трогается. --db-file пишет в
указанный файл, если копию хочется сохранить между запусками.

Заглушка нужна для проверки фронтенда, а не для продакшена: пароль
сверяется без защиты от подбора, CORS открыт всем.
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DB = os.path.join(BASE_DIR, 'backend', 'data', 'products.db')
UPLOADS = os.path.join(BASE_DIR, 'frontend', 'uploads')

PHOTO_TYPES = {'.jpg': 'photo', '.jpeg': 'photo', '.png': 'photo', '.webp': 'photo'}
VIDEO_TYPES = {'.mp4': 'video', '.webm': 'video'}

SORTS = {
    'name': 'p.name COLLATE NOCASE ASC',
    '-name': 'p.name COLLATE NOCASE DESC',
    'price': 'p.price ASC',
    '-price': 'p.price DESC',
    '-updated': 'p.updated_at DESC',
}


class Store:
    """Каталог в той форме, в какой его отдаёт их API"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    # ---------- категории ----------

    def categories(self):
        with self.connect() as db:
            rows = db.execute("""
                SELECT c.id, c.name, c.slug, c.parent_id,
                       (SELECT COUNT(*) FROM products p
                         WHERE p.category_id = c.id AND p.is_active = 1)
                       AS products_count
                  FROM categories c
                 ORDER BY c.name COLLATE NOCASE
            """).fetchall()
        return [dict(row) for row in rows]

    def category_branch(self, value):
        """id категории и всех вложенных, по slug или по id"""
        with self.connect() as db:
            row = db.execute(
                'SELECT id FROM categories WHERE slug = ? OR id = ?',
                (str(value), value if str(value).isdigit() else -1)).fetchone()
            if not row:
                return None

            branch, edge = [row['id']], [row['id']]
            while edge:
                marks = ','.join('?' * len(edge))
                children = db.execute(
                    f'SELECT id FROM categories WHERE parent_id IN ({marks})',
                    edge).fetchall()
                edge = [child['id'] for child in children]
                branch.extend(edge)
            return branch

    # ---------- товары ----------

    def products(self, *, category=None, search=None, in_stock=None,
                 sort='name', page=1, limit=24, active_only=True,
                 linked_to_1c=None):
        where, args = [], []
        if active_only:
            where.append('p.is_active = 1')
        if category is not None:
            marks = ','.join('?' * len(category))
            where.append(f'p.category_id IN ({marks})')
            args.extend(category)
        if search:
            where.append('(p.name LIKE ? OR p.sku LIKE ?)')
            args.extend([f'%{search}%'] * 2)
        if in_stock:
            where.append('p.stock > 0')
        if linked_to_1c is not None:
            where.append('p.sync_with_1c = ?')
            args.append(1 if linked_to_1c else 0)

        clause = ('WHERE ' + ' AND '.join(where)) if where else ''
        # Товары без цены в конце — так сказано в контракте
        order = f'p.price IS NULL, {SORTS.get(sort, SORTS["name"])}'

        with self.connect() as db:
            total = db.execute(
                f'SELECT COUNT(*) FROM products p {clause}', args).fetchone()[0]
            rows = db.execute(
                f'SELECT p.* FROM products p {clause} ORDER BY {order} '
                f'LIMIT ? OFFSET ?', args + [limit, (page - 1) * limit]).fetchall()
            items = [self.brief(db, row) for row in rows]

        pages = max(1, -(-total // limit))
        return {'items': items, 'total': total, 'page': page,
                'limit': limit, 'pages': pages}

    def product(self, db, product_id, active_only=True):
        row = db.execute('SELECT * FROM products WHERE id = ?',
                         (product_id,)).fetchone()
        if not row or (active_only and not row['is_active']):
            return None

        card = self.brief(db, row)
        card.pop('photo', None)
        card['description'] = row['description'] or ''
        card['media'] = self.media(db, product_id)
        return card

    def brief(self, db, row):
        photos = [entry for entry in self.media(db, row['id'])
                  if entry['type'] == 'photo']
        return {
            'id': row['id'],
            'article': row['sku'],
            'name': row['name'],
            # У нас колонка NOT NULL, у них «цена по запросу» это null.
            # Ноль в каталоге не встречается, поэтому он и означает null.
            'price': row['price'] or None,
            'quantity': row['stock'] or 0,
            'in_stock': (row['stock'] or 0) > 0,
            'category': self.category_brief(db, row['category_id']),
            'photo': photos[0]['url'] if photos else None,
            'updated_at': iso(row['updated_at']),
            # Служебные поля: публичный сервер их вырежет
            'is_active': bool(row['is_active']),
            'manually_edited': not row['sync_with_1c'],
            'is_linked_to_1c': bool(row['sync_with_1c']),
        }

    def category_brief(self, db, category_id):
        if not category_id:
            return None
        row = db.execute('SELECT id, name, slug FROM categories WHERE id = ?',
                         (category_id,)).fetchone()
        return dict(row) if row else None

    def media(self, db, product_id):
        """Фото и видео одним списком, как у них"""
        entries = []
        for row in db.execute(
                'SELECT id, image_url, "order" AS sort_order FROM product_images '
                'WHERE product_id = ? ORDER BY "order", id', (product_id,)):
            entries.append({'id': row['id'], 'url': as_media(row['image_url']),
                            'type': 'photo', 'sort_order': row['sort_order'] or 0})

        base = len(entries)
        for shift, row in enumerate(db.execute(
                'SELECT id, video_url FROM product_videos WHERE product_id = ? '
                'ORDER BY id', (product_id,))):
            # id медиа сквозной, а у нас две таблицы: видео сдвигается
            entries.append({'id': VIDEO_ID_SHIFT + row['id'],
                            'url': as_media(row['video_url']),
                            'type': 'video', 'sort_order': base + shift})

        return entries


VIDEO_ID_SHIFT = 1_000_000


def as_media(url):
    """Наш /uploads/файл против их /media/products/файл"""
    if not url:
        return None
    if url.startswith('http'):
        return url
    return '/media/products/' + os.path.basename(url)


def iso(value):
    if not value:
        return None
    text = str(value).replace(' ', 'T')
    return text if text.endswith('Z') else text + 'Z'


class Fault(Exception):
    """Ошибка, которую надо отдать клиенту как есть"""

    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status = status
        self.body = {'error': message, **extra}


# Артикул, приведённый к виду, в котором FA-40 и fa40 совпадают
LOOSE = re.compile(r'[^a-zа-я0-9]+', re.IGNORECASE)
CELL_TAIL = re.compile(r'\s+\*[\d*]+$')


def loose_key(article):
    return LOOSE.sub('', article or '').lower()


class Handler(BaseHTTPRequestHandler):
    server_version = 'MockShopAPI/1.0'
    admin = False
    store = None
    password = ''
    user = 'admin'

    # ---------- каркас ----------

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % ('админ ' if self.admin else 'витрина',
                                      fmt % args))

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header('Access-Control-Allow-Methods',
                         'GET, HEAD, POST, PATCH, PUT, DELETE')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def cors(self):
        # Заглушка пускает кого угодно; настоящий сервер сверяется
        # с ADMIN_ALLOWED_ORIGINS
        self.send_header('Access-Control-Allow-Origin',
                         self.headers.get('Origin', '*'))
        self.send_header('Access-Control-Expose-Headers', 'Location')

    def reply(self, status, payload, extra_headers=()):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def guard(self, path=''):
        """Basic, если пароль задан — иначе 401, как в контракте.

        Картинки из-под пароля выведены нарочно: браузер не приложит
        заголовок к <img>, и вся админка осталась бы без фотографий."""
        if not self.admin or not self.password or path.startswith('/media/'):
            return

        header = self.headers.get('Authorization', '')
        if not header.startswith('Basic '):
            raise Fault(401, 'Требуется вход')
        try:
            user, _, password = base64.b64decode(header[6:]).decode('utf-8').partition(':')
        except Exception:
            raise Fault(401, 'Требуется вход')
        if user != self.user or password != self.password:
            raise Fault(401, 'Неверный логин или пароль')

    def run(self, method):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            self.guard(parsed.path)
            handler = self.route(method, parsed.path)
            handler(parsed.path, query)
        except Fault as fault:
            self.reply(fault.status, fault.body)
        except Exception as error:  # заглушка не должна падать молча
            self.log_message('сбой: %r', error)
            self.reply(500, {'error': f'Ошибка заглушки: {error}'})

    def route(self, method, path):
        if path == '/health' and method == 'GET':
            return self.health
        if path == '/categories' and method == 'GET':
            return self.categories
        if path == '/products' and method == 'GET':
            return self.product_list
        if path.startswith('/media/') and method == 'GET':
            return self.media_file

        product = re.fullmatch(r'/products/(\d+)', path)
        if product and method == 'GET':
            return self.product_card
        if path == '/products' and method == 'POST':
            return self.admin_only(self.create_product)
        if product and method == 'PATCH':
            return self.admin_only(self.patch_product)

        if re.fullmatch(r'/products/\d+/media', path) and method == 'POST':
            return self.admin_only(self.upload_media)
        if re.fullmatch(r'/products/\d+/media/\d+', path) and method == 'DELETE':
            return self.admin_only(self.delete_media)
        if re.fullmatch(r'/products/\d+/media/order', path) and method == 'PUT':
            return self.admin_only(self.order_media)

        raise Fault(404, 'Такого запроса нет')

    def admin_only(self, handler):
        if not self.admin:
            raise Fault(404, 'Такого запроса нет')
        return handler

    def do_GET(self):
        self.run('GET')

    def do_POST(self):
        self.run('POST')

    def do_PATCH(self):
        self.run('PATCH')

    def do_PUT(self):
        self.run('PUT')

    def do_DELETE(self):
        self.run('DELETE')

    # ---------- чтение ----------

    def health(self, path, query):
        self.reply(200, {'status': 'ok'})

    def categories(self, path, query):
        self.reply(200, {'items': self.store.categories()})

    def product_list(self, path, query):
        branch = None
        if 'category' in query:
            branch = self.store.category_branch(query['category'][0])
            if branch is None:
                raise Fault(404, 'Категория не найдена')

        sort = query.get('sort', ['name'])[0]
        if sort not in SORTS:
            raise Fault(400, f'Неизвестная сортировка «{sort}»')

        page = as_int(query.get('page', ['1'])[0], 'page', least=1)
        limit = as_int(query.get('limit', ['24'])[0], 'limit', least=1, most=100)

        search = query.get('search', [None])[0]
        if search and len(search) > 100:
            raise Fault(400, 'Слишком длинный поиск')

        linked = query.get('linked_to_1c', [None])[0]
        result = self.store.products(
            category=branch, search=search,
            in_stock=query.get('in_stock', [''])[0] == 'true',
            sort=sort, page=page, limit=limit,
            active_only=not self.admin,
            linked_to_1c=None if linked is None else linked == 'true')

        result['items'] = [self.trim(item) for item in result['items']]
        self.reply(200, result)

    def product_card(self, path, query):
        product_id = int(path.rsplit('/', 1)[1])
        with self.store.connect() as db:
            card = self.store.product(db, product_id, active_only=not self.admin)
        if not card:
            raise Fault(404, 'Товар не найден')
        self.reply(200, self.trim(card))

    def trim(self, item):
        """Служебные поля видит только админка"""
        if self.admin:
            return item
        for field in ('is_active', 'manually_edited', 'is_linked_to_1c'):
            item.pop(field, None)
        return item

    def media_file(self, path, query):
        name = os.path.basename(path)
        full = os.path.join(UPLOADS, name)
        if not os.path.isfile(full):
            raise Fault(404, 'Файл не найден')

        kind = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        with open(full, 'rb') as source:
            body = source.read()

        self.send_response(200)
        self.cors()
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'public, max-age=604800')
        self.end_headers()
        self.wfile.write(body)

    # ---------- запись ----------

    def json_body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > 256 * 1024:
            raise Fault(413, 'Тело запроса больше 256 КБ')
        try:
            return json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            raise Fault(400, 'Битый JSON')

    # multipart разбирается руками: модуль cgi выброшен из Python 3.13,
    # а заглушке нужно только имя файла и его содержимое
    def multipart(self):
        kind = self.headers.get('Content-Type', '')
        found = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', kind)
        if 'multipart/form-data' not in kind or not found:
            raise Fault(400, 'Ожидался multipart/form-data')

        length = int(self.headers.get('Content-Length') or 0)
        if length > 210 * 1024 * 1024:
            raise Fault(413, 'Загрузка больше 210 МБ')

        boundary = (found.group(1) or found.group(2)).strip().encode()
        parts = []
        for chunk in self.rfile.read(length).split(b'--' + boundary):
            head, split, body = chunk.partition(b'\r\n\r\n')
            if not split:
                continue  # пролог и хвост --
            headers = head.decode('utf-8', 'replace')
            name = re.search(r'name="([^"]*)"', headers)
            filename = re.search(r'filename="([^"]*)"', headers)
            parts.append({
                'name': name.group(1) if name else '',
                'filename': filename.group(1) if filename else '',
                'body': body[:-2] if body.endswith(b'\r\n') else body,
            })
        return parts

    CREATE_FIELDS = {'article', 'name', 'description', 'category_id',
                     'price', 'is_active'}
    PATCH_FIELDS = {'name', 'description', 'category_id', 'is_active',
                    'manually_edited'}

    def create_product(self, path, query):
        data = self.json_body()
        extra = set(data) - self.CREATE_FIELDS
        if extra:
            raise Fault(400, f'Лишние поля: {", ".join(sorted(extra))}')

        article = CELL_TAIL.sub('', ' '.join(str(data.get('article') or '').split()))
        if not article:
            raise Fault(400, 'Артикул обязателен')
        if len(article) > 100:
            raise Fault(400, 'Артикул длиннее 100 символов')

        name = str(data.get('name') or '').strip()
        if not name:
            raise Fault(400, 'Название обязательно')
        if len(name) > 500:
            raise Fault(400, 'Название длиннее 500 символов')

        description = data.get('description') or ''
        if len(description) > 20000:
            raise Fault(400, 'Описание длиннее 20 000 символов')

        price = data.get('price')
        if price is not None:
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise Fault(400, 'Цена должна быть числом или null')
            if price <= 0:
                raise Fault(400, 'Цена должна быть больше нуля')
            price = round(float(price), 2)

        with self.store.lock, self.store.connect() as db:
            category_id = data.get('category_id')
            if category_id is not None:
                if not db.execute('SELECT 1 FROM categories WHERE id = ?',
                                  (category_id,)).fetchone():
                    raise Fault(400, 'Такой категории нет')

            # У них товар может висеть без категории, у нас колонка NOT NULL.
            # Для заглушки этого хватает: пустая категория едет в «Без категории».
            if category_id is None:
                category_id = default_category(db)

            self.refuse_duplicate(db, article)

            now = time.strftime('%Y-%m-%d %H:%M:%S')
            cursor = db.execute(
                'INSERT INTO products (sku, name, description, category_id, '
                'price, stock, is_active, sync_with_1c, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, 0, ?, 0, ?, ?)',
                (article, name, description, category_id, price or 0,
                 1 if data.get('is_active', True) else 0, now, now))
            created = self.store.product(db, cursor.lastrowid, active_only=False)

        self.reply(201, created, [('Location', f'/products/{created["id"]}')])

    def refuse_duplicate(self, db, article):
        exact = db.execute(
            'SELECT id, sku, name FROM products WHERE sku = ?', (article,)).fetchone()
        if exact:
            raise Fault(409, f'Товар с артикулом «{article}» уже существует',
                        code='article_exists',
                        existing={'id': exact['id'], 'article': exact['sku'],
                                  'name': exact['name']})

        key = loose_key(article)
        for row in db.execute('SELECT id, sku, name FROM products'):
            if loose_key(row['sku']) == key:
                raise Fault(
                    409,
                    f'Артикул «{article}» почти совпадает с «{row["sku"]}»',
                    code='article_similar',
                    existing={'id': row['id'], 'article': row['sku'],
                              'name': row['name']})

    def patch_product(self, path, query):
        product_id = int(path.rsplit('/', 1)[1])
        data = self.json_body()
        extra = set(data) - self.PATCH_FIELDS
        if extra:
            raise Fault(400, f'Эти поля менять нельзя: {", ".join(sorted(extra))}')

        with self.store.lock, self.store.connect() as db:
            row = db.execute('SELECT * FROM products WHERE id = ?',
                             (product_id,)).fetchone()
            if not row:
                raise Fault(404, 'Товар не найден')

            changed, sets, args = [], [], []
            columns = {'name': 'name', 'description': 'description',
                       'category_id': 'category_id', 'is_active': 'is_active'}

            for field, column in columns.items():
                if field not in data:
                    continue
                value = data[field]
                if field == 'is_active':
                    value = 1 if value else 0
                if field == 'category_id':
                    if value is None:
                        value = default_category(db)
                    elif not db.execute('SELECT 1 FROM categories WHERE id = ?',
                                        (value,)).fetchone():
                        raise Fault(400, 'Такой категории нет')
                if (row[column] or None) != (value or None):
                    sets.append(f'{column} = ?')
                    args.append(value)
                    changed.append(field)

            # Правка содержимого сама защищает товар от 1С
            content = {'name', 'description', 'category_id'} & set(changed)
            manual = not row['sync_with_1c']
            if content and not manual:
                manual = True
            if 'manually_edited' in data:
                manual = bool(data['manually_edited'])
            if manual != (not row['sync_with_1c']):
                sets.append('sync_with_1c = ?')
                args.append(0 if manual else 1)
                changed.append('manually_edited')

            if sets:
                sets.append('updated_at = ?')
                args.append(time.strftime('%Y-%m-%d %H:%M:%S'))
                db.execute(f'UPDATE products SET {", ".join(sets)} WHERE id = ?',
                           args + [product_id])

            card = self.store.product(db, product_id, active_only=False)

        card['changed'] = changed
        self.reply(200, card)

    def upload_media(self, path, query):
        product_id = int(path.split('/')[2])
        files = [part for part in self.multipart() if part['filename']]
        if not files:
            raise Fault(400, 'Файлы не пришли')

        # Всё или ничего: сначала проверяются все файлы, потом пишется любой
        checked = []
        for item in files:
            name = os.path.basename(item['filename'])
            suffix = os.path.splitext(name)[1].lower()
            media_type = PHOTO_TYPES.get(suffix) or VIDEO_TYPES.get(suffix)
            if not media_type:
                raise Fault(400, f'Файл «{name}»: принимаются JPEG, PNG, WebP, MP4, WebM')

            cap = 10 if media_type == 'photo' else 200
            if len(item['body']) > cap * 1024 * 1024:
                raise Fault(413, f'Файл «{name}» больше {cap} МБ')
            checked.append((media_type, suffix, item['body']))

        os.makedirs(UPLOADS, exist_ok=True)
        with self.store.lock, self.store.connect() as db:
            if not db.execute('SELECT 1 FROM products WHERE id = ?',
                              (product_id,)).fetchone():
                raise Fault(404, 'Товар не найден')

            for media_type, suffix, body in checked:
                stored = f'{product_id}_{time.time()}{suffix}'
                with open(os.path.join(UPLOADS, stored), 'wb') as target:
                    target.write(body)
                url = f'/uploads/{stored}'

                if media_type == 'photo':
                    order = db.execute(
                        'SELECT COALESCE(MAX("order"), -1) + 1 FROM product_images '
                        'WHERE product_id = ?', (product_id,)).fetchone()[0]
                    db.execute('INSERT INTO product_images (product_id, image_url, '
                               '"order") VALUES (?, ?, ?)', (product_id, url, order))
                else:
                    db.execute('INSERT INTO product_videos (product_id, video_url, '
                               'title) VALUES (?, ?, ?)', (product_id, url, ''))

            card = self.store.product(db, product_id, active_only=False)

        self.reply(201, card)

    def delete_media(self, path, query):
        parts = path.split('/')
        product_id, media_id = int(parts[2]), int(parts[4])

        with self.store.lock, self.store.connect() as db:
            if media_id > VIDEO_ID_SHIFT:
                cursor = db.execute(
                    'DELETE FROM product_videos WHERE id = ? AND product_id = ?',
                    (media_id - VIDEO_ID_SHIFT, product_id))
            else:
                cursor = db.execute(
                    'DELETE FROM product_images WHERE id = ? AND product_id = ?',
                    (media_id, product_id))
            if not cursor.rowcount:
                raise Fault(404, 'У этого товара нет такого медиа')

            card = self.store.product(db, product_id, active_only=False)

        self.reply(200, card)

    def order_media(self, path, query):
        product_id = int(path.split('/')[2])
        ids = self.json_body().get('ids')
        if not isinstance(ids, list):
            raise Fault(400, 'Ожидался список ids')

        with self.store.lock, self.store.connect() as db:
            known = [entry['id'] for entry in self.store.media(db, product_id)]
            if sorted(ids) != sorted(known):
                raise Fault(400, 'Нужен полный список медиа этого товара')

            for order, media_id in enumerate(ids):
                if media_id <= VIDEO_ID_SHIFT:
                    db.execute('UPDATE product_images SET "order" = ? WHERE id = ?',
                               (order, media_id))

            card = self.store.product(db, product_id, active_only=False)

        self.reply(200, card)


def default_category(db):
    row = db.execute(
        'SELECT id FROM categories ORDER BY (name <> ?), id LIMIT 1',
        ('Без категории',)).fetchone()
    if not row:
        raise Fault(400, 'В каталоге нет ни одной категории')
    return row['id']


def as_int(value, name, least=None, most=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise Fault(400, f'Параметр {name} должен быть числом')
    if least is not None and number < least:
        raise Fault(400, f'Параметр {name} меньше {least}')
    if most is not None and number > most:
        raise Fault(400, f'Параметр {name} больше {most}')
    return number


def serve(port, admin, store, password, user):
    handler = type('Bound', (Handler,), {
        'admin': admin, 'store': store, 'password': password, 'user': user})
    server = ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=SOURCE_DB, help='откуда взять каталог')
    parser.add_argument('--db-file', help='файл рабочей копии (по умолчанию временный)')
    parser.add_argument('--public-port', type=int, default=3000)
    parser.add_argument('--admin-port', type=int, default=3001)
    parser.add_argument('--user', default='admin')
    parser.add_argument('--password', default='',
                        help='пустой пароль — вход без проверки, как у них локально')
    args = parser.parse_args()

    if not os.path.isfile(args.source):
        sys.exit(f'Нет базы {args.source}')

    path = args.db_file or os.path.join(tempfile.mkdtemp(prefix='mock-api-'),
                                        'products.db')
    if not (args.db_file and os.path.isfile(args.db_file)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy(args.source, path)

    store = Store(path)
    serve(args.public_port, False, store, '', args.user)
    serve(args.admin_port, True, store, args.password, args.user)

    print(f'Витрина  http://127.0.0.1:{args.public_port}')
    print(f'Админка  http://127.0.0.1:{args.admin_port}'
          + ('' if args.password else '  (без пароля)'))
    print(f'Копия базы: {path}')
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print('\nОстановлено')


if __name__ == '__main__':
    main()
