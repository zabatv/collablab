#!/usr/bin/env python3
"""Сервис брендов.

Каталог живёт на их бэкенде, а он про бренды не знает: ни поля у товара,
ни справочника, ни эндпоинта, и из 1С бренды тоже не придут. Заводить их
вручную в базе нельзя — менять бренды должен заказчик, из админки.

Поэтому бренды держит этот маленький сервис: справочник с логотипами и
правила привязки к артикулам. Привязка именно к артикулу, а не к товару:
товары теперь чужие, их id нам не принадлежат, а артикул — то общее, что
есть и у них, и у нас, и в 1С.

    ADMIN_USER=admin ADMIN_PASSWORD=... python3 services/brands/app.py

Логин и пароль те же, что у их админского сервера, — тогда в админке один
вход на оба. Проверка HTTP Basic, как у них.
"""

import base64
import hmac
import os
import re
import secrets
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOGO_DIR = os.path.join(DATA_DIR, 'logos')
os.makedirs(LOGO_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATA_DIR}/brands.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024  # логотип, не видео

db = SQLAlchemy(app)

ADMIN_USER = os.getenv('ADMIN_USER', '')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
LOGO_TYPES = {'.png', '.jpg', '.jpeg', '.webp', '.svg'}
NAME_LIMIT = 60


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


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    if not ADMIN_PASSWORD:
        print('ВНИМАНИЕ: ADMIN_PASSWORD не задан — правка брендов закрыта')

    app.run(host=os.getenv('HOST', '127.0.0.1'),
            port=int(os.getenv('PORT', 5001)),
            debug=os.getenv('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes'))
