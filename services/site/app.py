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
import json
import os
import re
import secrets
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOGO_DIR = os.path.join(DATA_DIR, 'logos')
SLIDE_DIR = os.path.join(DATA_DIR, 'banners')
os.makedirs(LOGO_DIR, exist_ok=True)
os.makedirs(SLIDE_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATA_DIR}/brands.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024  # картинки, не видео

db = SQLAlchemy(app)

ADMIN_USER = os.getenv('ADMIN_USER', '')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
LOGO_TYPES = {'.png', '.jpg', '.jpeg', '.webp', '.svg'}
NAME_LIMIT = 60

# Откуда брать товары для разбора каталога. Внутри туннеля, без пароля.
CATALOG_API = os.getenv('CATALOG_API', 'http://10.8.0.2:3000').rstrip('/')

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


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    if not ADMIN_PASSWORD:
        print('ВНИМАНИЕ: ADMIN_PASSWORD не задан — правка брендов закрыта')

    app.run(host=os.getenv('HOST', '127.0.0.1'),
            port=int(os.getenv('PORT', 5001)),
            debug=os.getenv('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes'))
