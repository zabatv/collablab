from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from sqlalchemy import event
from sqlalchemy.engine import Engine
from models import (db, Product, Category, Brand, ProductImage, ProductVideo,
                    Specification, ProductView, SearchQuery, Banner)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps
import io
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.drawing.image import Image as XLImage
import requests
import zipfile
import posixpath
from xml.etree import ElementTree as ET

app = Flask(__name__)
CORS(app)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{BASE_DIR}/data/products.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB, product videos are large
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, '..', 'frontend', 'uploads')

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)

# Initialize database
db.init_app(app)

@event.listens_for(Engine, 'connect')
def register_unicode_lower(dbapi_connection, connection_record):
    """SQLite folds case for ASCII only, so a search for "ТРОЙНИК" would miss
    "тройник". Replacing lower() with Python's makes every ilike Unicode-aware."""
    if hasattr(dbapi_connection, 'create_function'):
        dbapi_connection.create_function(
            'lower', 1, lambda value: value.lower() if isinstance(value, str) else value)

# ===== ADMIN AUTHENTICATION =====
#
# Credentials come from the environment and never from the source: a default
# password in a public repository is an open door to every deployment that
# forgot to override it. Without both variables set, nobody can sign in.
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '').strip()
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')

# Only the hash is kept in memory; the plain password is not held after startup
ADMIN_PASSWORD_HASH = generate_password_hash(ADMIN_PASSWORD) if ADMIN_PASSWORD else None
AUTH_CONFIGURED = bool(ADMIN_USERNAME and ADMIN_PASSWORD_HASH)

if not AUTH_CONFIGURED:
    print('ВНИМАНИЕ: ADMIN_USERNAME и ADMIN_PASSWORD не заданы — вход в админку закрыт.')

SESSION_LIFETIME = timedelta(hours=12)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT = timedelta(minutes=15)

# Sessions live in memory: a restart signs everyone out, which is acceptable
# here and avoids persisting anything that grants access
active_sessions = {}
failed_logins = {}

def issue_session():
    token = secrets.token_urlsafe(32)
    active_sessions[token] = datetime.utcnow() + SESSION_LIFETIME
    return token

def constant_time_equal(left, right):
    """compare_digest rejects strings holding non-ASCII, so compare their bytes"""
    return secrets.compare_digest(left.encode('utf-8'), right.encode('utf-8'))

def session_is_valid(token):
    if not token:
        return False

    # Compare against every known token in constant time, so a wrong token
    # cannot be narrowed down by how long the answer takes
    match = None
    for known in list(active_sessions):
        if constant_time_equal(known, token):
            match = known

    if match is None:
        return False

    if datetime.utcnow() > active_sessions[match]:
        active_sessions.pop(match, None)
        return False

    return True

def login_blocked(client):
    attempts, blocked_until = failed_logins.get(client, (0, None))
    return blocked_until is not None and datetime.utcnow() < blocked_until

def record_failed_login(client):
    attempts, _ = failed_logins.get(client, (0, None))
    attempts += 1
    blocked_until = datetime.utcnow() + LOGIN_LOCKOUT if attempts >= LOGIN_MAX_ATTEMPTS else None
    failed_logins[client] = (attempts, blocked_until)

def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        header = request.headers.get('Authorization', '')
        token = header[7:] if header.startswith('Bearer ') else None

        if not session_is_valid(token):
            return jsonify({'error': 'Требуется вход'}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Exchange credentials for a session token"""
    if not AUTH_CONFIGURED:
        return jsonify({'error': 'Вход не настроен на сервере'}), 503

    client = request.remote_addr or 'unknown'
    if login_blocked(client):
        return jsonify({'error': 'Слишком много попыток. Попробуйте через 15 минут'}), 429

    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))

    username_ok = constant_time_equal(username, ADMIN_USERNAME)
    password_ok = check_password_hash(ADMIN_PASSWORD_HASH, password)

    # One message for both cases, so a wrong name cannot be told from a wrong password
    if not (username_ok and password_ok):
        record_failed_login(client)
        return jsonify({'error': 'Неверный логин или пароль'}), 401

    failed_logins.pop(client, None)
    return jsonify({'token': issue_session(), 'expires_in': int(SESSION_LIFETIME.total_seconds())})

@app.route('/api/auth/logout', methods=['POST'])
@require_admin
def logout():
    """Revoke the current session token"""
    header = request.headers.get('Authorization', '')
    active_sessions.pop(header[7:], None)
    return '', 204

@app.route('/api/auth/check', methods=['GET'])
@require_admin
def auth_check():
    """Used by the admin page to confirm a stored token still works"""
    return jsonify({'ok': True})

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.ogg', '.ogv', '.mov', '.m4v'}

def save_upload(file, product_id, allowed_extensions):
    """Save an uploaded file under the uploads folder. Returns '/uploads/<name>'.
    Uploads are served as static files, so only known media types are accepted."""
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        raise ValueError(f'Недопустимый формат файла: {ext or "без расширения"}')

    filename = secure_filename(f"{product_id}_{datetime.now().timestamp()}{ext}")
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    return f'/uploads/{filename}'

def save_image_bytes(product_id, data, ext):
    """Store raw image bytes in the uploads folder. Returns '/uploads/<name>'."""
    filename = secure_filename(f"{product_id}_{datetime.now().timestamp()}{ext}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    with open(filepath, 'wb') as f:
        f.write(data)
    return f'/uploads/{filename}'

def download_image_to_uploads(product_id, url):
    """Download an image from a URL and save it to the uploads folder.
    Returns the stored path (e.g. '/uploads/xyz.jpg') or None if it fails."""
    resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
    if resp.status_code != 200:
        return None

    content_type = resp.headers.get('Content-Type', '')
    if 'image' not in content_type:
        return None

    ext = '.jpg'
    if 'png' in content_type: ext = '.png'
    elif 'webp' in content_type: ext = '.webp'
    elif 'gif' in content_type: ext = '.gif'

    return save_image_bytes(product_id, resp.content, ext)

def product_image_path(product):
    """Absolute path of a product's main image file, or None when unavailable."""
    if not product.image or not product.image.startswith('/uploads/'):
        return None
    path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(product.image))
    return path if os.path.exists(path) else None

PHOTO_CELL_SIZE = 96  # pixels, for pictures embedded in exported spreadsheets

XL_NS = {
    'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'rel': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
}

def extract_embedded_images(file_bytes, sheet_title):
    """Map Excel row number -> (image bytes, extension) for pictures anchored in a sheet.

    Pictures pasted into a sheet are stored as drawings rather than cell values,
    so they are read straight from the xlsx package."""
    images = {}

    with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
        names = set(z.namelist())

        def rels_for(part):
            base, filename = part.rsplit('/', 1)
            rels_path = f'{base}/_rels/{filename}.rels'
            if rels_path not in names:
                return {}
            # Targets are either relative to the part or absolute from the package root
            return {
                rel.get('Id'): posixpath.normpath(
                    posixpath.join(base, rel.get('Target'))
                ).lstrip('/')
                for rel in ET.fromstring(z.read(rels_path))
            }

        book_rels = rels_for('xl/workbook.xml')
        sheet_part = None
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).iter(f"{{{XL_NS['main']}}}sheet"):
            if sheet.get('name') == sheet_title:
                sheet_part = book_rels.get(sheet.get(f"{{{XL_NS['rel']}}}id"))
                break

        if not sheet_part or sheet_part not in names:
            return images

        drawing_part = next((t for t in rels_for(sheet_part).values() if '/drawings/' in t), None)
        if not drawing_part or drawing_part not in names:
            return images

        drawing_rels = rels_for(drawing_part)
        for anchor in ET.fromstring(z.read(drawing_part)):
            origin = anchor.find('xdr:from', XL_NS)
            blip = anchor.find('.//a:blip', XL_NS)
            if origin is None or blip is None:
                continue

            row = int(origin.find('xdr:row', XL_NS).text) + 1
            media = drawing_rels.get(blip.get(f"{{{XL_NS['rel']}}}embed"))
            if row in images or not media or media not in names:
                continue

            ext = os.path.splitext(media)[1].lower() or '.jpg'
            images[row] = (z.read(media), ext)

    return images

# ===== API Routes =====

# ===== PUBLIC ROUTES =====

def ordered_children(parent_id):
    return (Category.query.filter_by(parent_id=parent_id)
            .order_by(Category.sort_order, Category.name).all())

@app.route('/api/categories', methods=['GET'])
def get_categories():
    """The catalogue tree, in the order the admin arranged it.

    ?flat=1 returns every category in one list, which is what the older
    screens expect."""
    if request.args.get('flat'):
        categories = Category.query.order_by(Category.sort_order, Category.name).all()
        return jsonify([category.to_dict() for category in categories])

    return jsonify([category.to_dict(with_children=True)
                    for category in ordered_children(None)])

@app.route('/api/categories/<int:category_id>', methods=['GET'])
def get_category(category_id):
    """One category with its subcategories and the path back to the root"""
    category = Category.query.get_or_404(category_id)
    return jsonify({
        **category.to_dict(),
        'path': [node.to_dict() for node in category.path()],
        'children': [child.to_dict() for child in
                     ordered_children(category.id)],
    })

@app.route('/api/brands', methods=['GET'])
def get_brands():
    """Get all brands"""
    brands = Brand.query.all()
    return jsonify([brand.to_dict() for brand in brands])

@app.route('/api/products', methods=['GET'])
def get_products():
    """Get products with filters"""
    category_id = request.args.get('category_id', type=int)
    brand_id = request.args.get('brand_id', type=int)
    search = request.args.get('search', '')
    sort = request.args.get('sort', 'newest')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = Product.query.filter_by(is_active=True)

    if category_id:
        # A section shows what its subcategories hold too, or opening
        # «Фитинги» would come back empty once everything sits one level down
        category = Category.query.get(category_id)
        branch = [node.id for node in category.descendants()] if category else [category_id]
        query = query.filter(Product.category_id.in_(branch))

    if brand_id:
        query = query.filter_by(brand_id=brand_id)

    if search:
        query = query.filter(
            (Product.sku.ilike(f'%{search}%')) |
            (Product.name.ilike(f'%{search}%')) |
            (Product.description.ilike(f'%{search}%'))
        )
        # An exact article number is the one result the customer asked for
        query = query.order_by((Product.sku.ilike(search)).desc())

    # Sorting
    if sort == 'price_asc':
        query = query.order_by(Product.price.asc())
    elif sort == 'price_desc':
        query = query.order_by(Product.price.desc())
    elif sort == 'name':
        query = query.order_by(Product.name.asc())
    else:  # newest
        query = query.order_by(Product.created_at.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    # Only the first page, so paging through results is not counted as a new search
    if search and page == 1:
        record_search(search, pagination.total)

    return jsonify({
        'products': [product.to_dict() for product in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page
    })

@app.route('/api/products/<int:product_id>', methods=['GET'])
def get_product(product_id):
    """Get single product with full details"""
    product = Product.query.get_or_404(product_id)
    return jsonify(product.to_dict(full=True))

# ===== VARIANTS =====
# A subcategory usually holds one part in several sizes: the same fitting
# with a 1/8, 1/4, 3/8 and 1/2 thread. The supplier writes them as separate
# products, and their names differ in exactly one comma-separated piece.
# That piece is what a buyer is choosing between, so it becomes the label
# on the switch at the top of the product page.

def name_family(name):
    """The part of the name before the first comma — «Фитинг угловой ...»"""
    return (name or '').split(',')[0].strip().lower()

def variant_labels(names):
    """For each name, what is left once the words they all share are removed.

    The supplier writes «Фитинг угловой ..., 8-6 мм x R 1/4 ..., T=...» — the
    opening and the closing conditions repeat, and only the middle changes.
    Cutting the shared head and tail away leaves exactly the size, however
    many commas the middle happens to contain. An empty label means the
    names differ in nothing showable, and the caller falls back to the
    article number."""
    words = [(name or '').split() for name in names]

    head = 0
    while (all(len(row) > head for row in words)
           and len({row[head] for row in words}) == 1):
        head += 1

    tail = 0
    while (all(len(row) > head + tail for row in words)
           and len({row[-1 - tail] for row in words}) == 1):
        tail += 1

    return [' '.join(row[head:len(row) - tail]).strip(' ,;-') for row in words]

# 6 мм has to come before 10 мм, and the thread 1/8 before 1/4, so numbers
# sort as numbers and fractions as their value rather than as text
NUMBERS = re.compile(r'(\d+\s*/\s*\d+|\d+(?:[.,]\d+)?)')

def as_number(part):
    try:
        if '/' in part:
            top, bottom = part.split('/')
            return float(top) / float(bottom)
        return float(part.replace(',', '.'))
    except (ValueError, ZeroDivisionError):
        # Sorting must never be the thing that breaks a product page
        return 0.0

def natural_key(text):
    parts = NUMBERS.split(text or '')
    return [as_number(part) if NUMBERS.fullmatch(part) else part.lower()
            for part in parts]

@app.route('/api/products/<int:product_id>/variants', methods=['GET'])
def get_product_variants(product_id):
    """The same part in its other sizes, from the same subcategory"""
    product = Product.query.get_or_404(product_id)
    family = name_family(product.name)

    siblings = [item for item
                in Product.query.filter_by(category_id=product.category_id,
                                           is_active=True).all()
                if name_family(item.name) == family]

    # Alone in its family there is nothing to choose between
    if len(siblings) < 2:
        return jsonify([])

    labels = variant_labels([item.name for item in siblings])

    # Two variants reading the same on the switch would be a trap, so a
    # repeated label carries its article number
    seen = [labels.count(label) for label in labels]
    variants = [{
        'id': item.id,
        'sku': item.sku,
        'label': (f'{label} ({item.sku})' if label and count > 1
                  else label or item.sku),
        'price': item.price,
        'in_stock': item.stock > 0,
        'current': item.id == product.id,
    } for item, label, count in zip(siblings, labels, seen)]

    variants.sort(key=lambda variant: natural_key(variant['label']))
    return jsonify(variants)

@app.route('/api/track/view/<int:product_id>', methods=['POST'])
def track_view(product_id):
    """Count a product page opening. Nothing about the visitor is stored."""
    try:
        if Product.query.get(product_id):
            db.session.add(ProductView(product_id=product_id))
            db.session.commit()
    except Exception:
        # Counting must never break the page it is counting
        db.session.rollback()

    return '', 204

def record_search(query_text, results_count):
    try:
        db.session.add(SearchQuery(query=query_text[:255], results_count=results_count))
        db.session.commit()
    except Exception:
        db.session.rollback()

@app.route('/api/products/search', methods=['GET'])
def search_products():
    """Search products"""
    query = request.args.get('q', '')
    if len(query) < 2:
        return jsonify([])

    products = Product.query.filter(
        Product.is_active == True,
        (Product.name.ilike(f'%{query}%')) |
        (Product.sku.ilike(f'%{query}%'))
    ).limit(10).all()

    return jsonify([product.to_dict() for product in products])

# ===== ADMIN ROUTES =====

@app.route('/api/admin/products', methods=['GET'])
@require_admin
def admin_get_products():
    """Get all products (admin)"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    pagination = Product.query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'products': [product.to_dict(full=True) for product in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages
    })

@app.route('/api/admin/products', methods=['POST'])
@require_admin
def create_product():
    """Create new product"""
    data = request.get_json()

    try:
        product = Product(
            sku=data['sku'],
            name=data['name'],
            description=data.get('description', ''),
            category_id=data['category_id'],
            brand_id=data.get('brand_id'),
            price=data['price'],
            old_price=data.get('old_price'),
            stock=data.get('stock', 0),
            is_active=data.get('is_active', True)
        )

        db.session.add(product)
        db.session.flush()

        replace_specifications(product.id, data.get('specifications', ''))
        db.session.commit()

        return jsonify(product.to_dict(full=True)), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/admin/products/<int:product_id>', methods=['PUT'])
@require_admin
def update_product(product_id):
    """Update product"""
    product = Product.query.get_or_404(product_id)
    data = request.get_json()

    try:
        product.name = data.get('name', product.name)
        product.description = data.get('description', product.description)
        product.category_id = data.get('category_id', product.category_id)
        product.brand_id = data.get('brand_id', product.brand_id)
        product.price = data.get('price', product.price)
        product.old_price = data.get('old_price', product.old_price)
        product.stock = data.get('stock', product.stock)
        product.is_active = data.get('is_active', product.is_active)
        product.updated_at = datetime.utcnow()

        if 'specifications' in data:
            replace_specifications(product.id, data['specifications'])

        db.session.commit()
        return jsonify(product.to_dict(full=True))
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/admin/products/<int:product_id>', methods=['DELETE'])
@require_admin
def delete_product(product_id):
    """Delete product"""
    product = Product.query.get_or_404(product_id)

    try:
        # Delete all associated images and videos
        ProductImage.query.filter_by(product_id=product_id).delete()
        ProductVideo.query.filter_by(product_id=product_id).delete()
        Specification.query.filter_by(product_id=product_id).delete()

        db.session.delete(product)
        db.session.commit()
        return '', 204
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/admin/products/<int:product_id>/upload-image', methods=['POST'])
@require_admin
def upload_product_image(product_id):
    """Upload product image"""
    product = Product.query.get_or_404(product_id)

    if 'image' not in request.files:
        return jsonify({'error': 'No image provided'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    try:
        stored_path = save_upload(file, product_id, IMAGE_EXTENSIONS)

        if not product.image:
            product.image = stored_path

        product_image = ProductImage(
            product_id=product_id,
            image_url=stored_path,
            order=len(product.images)
        )
        db.session.add(product_image)
        db.session.commit()

        return jsonify({'url': stored_path}), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/admin/products/<int:product_id>/set-image-url', methods=['POST'])
@require_admin
def set_product_image_url(product_id):
    """Set product image from URL"""
    product = Product.query.get_or_404(product_id)
    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'URL not provided'}), 400

    try:
        stored_path = download_image_to_uploads(product_id, url)
        if not stored_path:
            return jsonify({'error': 'Failed to download image from URL'}), 400

        if not product.image:
            product.image = stored_path

        product_image = ProductImage(
            product_id=product_id,
            image_url=stored_path,
            order=len(product.images)
        )
        db.session.add(product_image)
        db.session.commit()

        return jsonify({'url': stored_path}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/admin/products/<int:product_id>/upload-video', methods=['POST'])
@require_admin
def upload_product_video(product_id):
    """Add video URL to product"""
    product = Product.query.get_or_404(product_id)
    data = request.get_json()

    try:
        video = ProductVideo(
            product_id=product_id,
            video_url=data['url'],
            title=data.get('title', '')
        )
        db.session.add(video)
        db.session.commit()

        return jsonify(video.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/admin/products/<int:product_id>/upload-video-file', methods=['POST'])
@require_admin
def upload_product_video_file(product_id):
    """Upload a video file from the admin's computer"""
    Product.query.get_or_404(product_id)

    if 'video' not in request.files:
        return jsonify({'error': 'Файл не передан'}), 400

    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400

    try:
        stored_path = save_upload(file, product_id, VIDEO_EXTENSIONS)

        # Stored files are renamed, so keep the original name as a readable label
        title = request.form.get('title', '').strip()
        if not title:
            title = os.path.splitext(os.path.basename(file.filename))[0]

        video = ProductVideo(
            product_id=product_id,
            video_url=stored_path,
            title=title
        )
        db.session.add(video)
        db.session.commit()

        return jsonify(video.to_dict()), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/admin/products/<int:product_id>/videos/<int:video_id>', methods=['DELETE'])
@require_admin
def delete_product_video(product_id, video_id):
    """Remove a video from a product"""
    video = ProductVideo.query.filter_by(id=video_id, product_id=product_id).first_or_404()

    try:
        db.session.delete(video)
        db.session.commit()
        return '', 204
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
    'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}

def make_slug(name):
    """A readable address for a Russian name, unique across the catalogue."""
    base = ''.join(TRANSLIT.get(char, char) for char in (name or '').lower())
    base = re.sub(r'[^a-z0-9]+', '-', base).strip('-') or 'category'

    slug, suffix = base, 2
    while Category.query.filter_by(slug=slug).first():
        slug = f'{base}-{suffix}'
        suffix += 1
    return slug

def next_sort_order(parent_id):
    last = (db.session.query(db.func.max(Category.sort_order))
            .filter(Category.parent_id.is_(parent_id) if parent_id is None
                    else Category.parent_id == parent_id).scalar())
    return (last or 0) + 10

def would_loop(category, new_parent_id):
    """A category cannot be moved inside one of its own subcategories."""
    if new_parent_id is None:
        return False
    return new_parent_id in {node.id for node in category.descendants()}

@app.route('/api/admin/categories', methods=['GET', 'POST'])
@require_admin
def admin_categories():
    """The catalogue tree: read it whole, or add a node to it"""
    if request.method == 'GET':
        if request.args.get('flat'):
            categories = Category.query.order_by(Category.sort_order, Category.name).all()
            return jsonify([category.to_dict() for category in categories])

        return jsonify([category.to_dict(with_children=True)
                        for category in ordered_children(None)])

    data = request.get_json() or {}
    name = str(data.get('name', '')).strip()
    if not name:
        return jsonify({'error': 'Название категории не может быть пустым'}), 400

    parent_id = data.get('parent_id') or None
    if parent_id and not Category.query.get(parent_id):
        return jsonify({'error': 'Родительская категория не найдена'}), 400

    try:
        category = Category(
            name=name[:100],
            slug=make_slug(data.get('slug') or name),
            description=data.get('description', ''),
            parent_id=parent_id,
            sort_order=next_sort_order(parent_id),
        )
        db.session.add(category)
        db.session.commit()
        return jsonify(category.to_dict()), 201
    except Exception as error:
        db.session.rollback()
        return jsonify({'error': str(error)}), 400

@app.route('/api/admin/categories/<int:category_id>', methods=['PUT', 'DELETE'])
@require_admin
def admin_category(category_id):
    """Rename a category, move it under another one, or remove it"""
    category = Category.query.get_or_404(category_id)

    if request.method == 'DELETE':
        if category.children:
            return jsonify({'error': 'Сначала удалите или перенесите подкатегории'}), 400
        if category.products:
            return jsonify({
                'error': f'В категории {len(category.products)} товаров — '
                         'перенесите их в другую категорию'}), 400

        db.session.delete(category)
        db.session.commit()
        return '', 204

    data = request.get_json() or {}

    if 'name' in data:
        name = str(data['name']).strip()
        if not name:
            return jsonify({'error': 'Название категории не может быть пустым'}), 400
        category.name = name[:100]

    if 'description' in data:
        category.description = data['description']

    if 'parent_id' in data:
        parent_id = data['parent_id'] or None
        if parent_id == category.id or would_loop(category, parent_id):
            return jsonify({'error': 'Категорию нельзя вложить саму в себя'}), 400
        if parent_id and not Category.query.get(parent_id):
            return jsonify({'error': 'Родительская категория не найдена'}), 400

        if parent_id != category.parent_id:
            category.parent_id = parent_id
            category.sort_order = next_sort_order(parent_id)

    db.session.commit()
    return jsonify(category.to_dict())

@app.route('/api/admin/categories/<int:category_id>/upload-icon', methods=['POST', 'DELETE'])
@require_admin
def upload_category_icon(category_id):
    """The picture on a category's tile.

    Without one the tile borrows a photograph from the first product in the
    branch, which is a guess. This is how the admin says which picture it
    should be — a brand mark for a section, a drawing of the part for a
    subcategory."""
    category = Category.query.get_or_404(category_id)

    if request.method == 'DELETE':
        category.icon = None
        db.session.commit()
        return jsonify(category.to_dict())

    if 'image' not in request.files:
        return jsonify({'error': 'Файл не передан'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400

    try:
        category.icon = save_upload(file, f'cat{category_id}', IMAGE_EXTENSIONS)
        db.session.commit()
        return jsonify(category.to_dict()), 201
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception as error:
        db.session.rollback()
        return jsonify({'error': str(error)}), 400

@app.route('/api/admin/categories/order', methods=['PUT'])
@require_admin
def admin_categories_order():
    """Store the order the admin dragged the siblings into"""
    data = request.get_json() or {}
    ids = data.get('ids')
    if not isinstance(ids, list):
        return jsonify({'error': 'Ожидается список id в нужном порядке'}), 400

    categories = {category.id: category for category
                  in Category.query.filter(Category.id.in_(ids)).all()}

    for position, category_id in enumerate(ids):
        category = categories.get(category_id)
        if category:
            category.sort_order = (position + 1) * 10

    db.session.commit()
    return jsonify({'updated': len(categories)})

# ===== BANNERS =====
# The slideshow at the top of the home page: news, an offer, a new product.

@app.route('/api/banners', methods=['GET'])
def get_banners():
    """The slides a visitor sees, in the order the admin arranged them"""
    banners = (Banner.query.filter_by(is_active=True)
               .order_by(Banner.sort_order, Banner.id).all())
    return jsonify([banner.to_dict() for banner in banners])

@app.route('/api/admin/banners', methods=['GET', 'POST'])
@require_admin
def admin_banners():
    """Every slide, hidden ones included — or a new one from an uploaded file"""
    if request.method == 'GET':
        banners = Banner.query.order_by(Banner.sort_order, Banner.id).all()
        return jsonify([banner.to_dict() for banner in banners])

    if 'image' not in request.files:
        return jsonify({'error': 'Картинка обязательна — она и есть слайд'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400

    try:
        last = (db.session.query(db.func.max(Banner.sort_order)).scalar() or 0)
        banner = Banner(
            image=save_upload(file, 'banner', IMAGE_EXTENSIONS),
            title=(request.form.get('title') or '').strip()[:200],
            subtitle=(request.form.get('subtitle') or '').strip()[:300],
            link=(request.form.get('link') or '').strip()[:500],
            sort_order=last + 10,
        )
        db.session.add(banner)
        db.session.commit()
        return jsonify(banner.to_dict()), 201
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception as error:
        db.session.rollback()
        return jsonify({'error': str(error)}), 400

@app.route('/api/admin/banners/<int:banner_id>', methods=['PUT', 'DELETE'])
@require_admin
def admin_banner(banner_id):
    """Change a slide's text, show or hide it, or remove it"""
    banner = Banner.query.get_or_404(banner_id)

    if request.method == 'DELETE':
        db.session.delete(banner)
        db.session.commit()
        return '', 204

    data = request.get_json() or {}
    for field, limit in (('title', 200), ('subtitle', 300), ('link', 500)):
        if field in data:
            setattr(banner, field, str(data[field] or '').strip()[:limit])

    if 'is_active' in data:
        banner.is_active = bool(data['is_active'])

    db.session.commit()
    return jsonify(banner.to_dict())

@app.route('/api/admin/banners/order', methods=['PUT'])
@require_admin
def admin_banners_order():
    """Store the order the admin moved the slides into"""
    data = request.get_json() or {}
    ids = data.get('ids')
    if not isinstance(ids, list):
        return jsonify({'error': 'Ожидается список id в нужном порядке'}), 400

    banners = {banner.id: banner for banner
               in Banner.query.filter(Banner.id.in_(ids)).all()}

    for position, banner_id in enumerate(ids):
        if banner_id in banners:
            banners[banner_id].sort_order = (position + 1) * 10

    db.session.commit()
    return jsonify({'updated': len(banners)})

@app.route('/api/admin/brands', methods=['GET', 'POST'])
@require_admin
def admin_brands():
    """Manage brands"""
    if request.method == 'GET':
        brands = Brand.query.all()
        return jsonify([brand.to_dict() for brand in brands])

    data = request.get_json()
    try:
        brand = Brand(name=data['name'])
        db.session.add(brand)
        db.session.commit()
        return jsonify(brand.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

# ===== EXCEL IMPORT/EXPORT =====

# Specifications travel in one cell as "параметр: значение", one pair per line
# or separated by a semicolon
SPEC_SEPARATORS = (':', '=', '—', '-')

def format_specifications(specifications):
    return '\n'.join(f'{spec.name}: {spec.value}' for spec in specifications)

def replace_specifications(product_id, source):
    """Swap a product's specifications. Takes the text an Excel cell holds, or
    the list of {name, value} the admin form sends. Does not commit."""
    if isinstance(source, list):
        pairs = [(str(item.get('name', '')).strip()[:100],
                  str(item.get('value', '')).strip()[:255])
                 for item in source if isinstance(item, dict)]
        pairs = [(name, value) for name, value in pairs if name and value]
    else:
        pairs = parse_specifications(source)

    Specification.query.filter_by(product_id=product_id).delete()
    for name, value in pairs:
        db.session.add(Specification(product_id=product_id, name=name, value=value))

def parse_specifications(text):
    """Turn a cell into (name, value) pairs, ignoring anything unreadable"""
    pairs = []

    for line in str(text or '').replace(';', '\n').split('\n'):
        line = line.strip()
        if not line:
            continue

        # The first separator present wins, so a value may contain the others
        position = min((line.find(s) for s in SPEC_SEPARATORS if line.find(s) > 0), default=-1)
        if position < 0:
            continue

        name = line[:position].strip()
        value = line[position + 1:].strip()
        if name and value:
            pairs.append((name[:100], value[:255]))

    return pairs

@app.route('/api/admin/export/excel', methods=['GET'])
@require_admin
def export_excel():
    """Export all products to Excel"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Товары"

    # Header style
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    # Headers
    headers = ['ID', 'Артикул', 'Название', 'Фото товара', 'Описание', 'Характеристики',
               'Категория', 'Бренд', 'Цена', 'Старая цена', 'Остаток', 'Активен']
    photo_col = headers.index('Фото товара') + 1
    specs_col = headers.index('Характеристики') + 1
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    # Data
    products = Product.query.all()
    rows_with_photo = []
    for row, product in enumerate(products, 2):
        data = [
            product.id,
            product.sku,
            product.name,
            '',
            product.description or '',
            format_specifications(product.specifications),
            product.category.name if product.category else '',
            product.brand.name if product.brand else '',
            product.price,
            product.old_price or '',
            product.stock,
            'Да' if product.is_active else 'Нет'
        ]
        for col, value in enumerate(data, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.border = thin_border
            if col in [9, 10]:  # Price columns
                cell.number_format = '#,##0.00'
            elif col == 11:  # Stock
                cell.number_format = '#,##0'
            elif col == specs_col:
                # One pair per line, so the cell has to show its line breaks
                cell.alignment = Alignment(wrap_text=True, vertical='top')

        image_path = product_image_path(product)
        if image_path:
            picture = XLImage(image_path)
            picture.width = PHOTO_CELL_SIZE
            picture.height = PHOTO_CELL_SIZE
            ws.add_image(picture, f'{openpyxl.utils.get_column_letter(photo_col)}{row}')
            rows_with_photo.append(row)

    # Auto-adjust column widths
    for col in range(1, len(headers) + 1):
        if col == photo_col:
            continue
        max_length = max(len(str(ws.cell(row=r, column=col).value or '')) for r in range(1, ws.max_row + 1))
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = min(max_length + 2, 50)

    # Give embedded pictures room to show
    ws.column_dimensions[openpyxl.utils.get_column_letter(photo_col)].width = PHOTO_CELL_SIZE / 7
    for row in rows_with_photo:
        ws.row_dimensions[row].height = PHOTO_CELL_SIZE * 0.78

    # Save to buffer
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'products_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    )

@app.route('/api/admin/import/excel', methods=['POST'])
@require_admin
def import_excel():
    """Import products from Excel"""
    if 'file' not in request.files:
        return jsonify({'error': 'Файл не загружен'}), 400

    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'Поддерживаются только файлы .xlsx'}), 400

    try:
        file_bytes = file.read()
        # data_only pulls the values Excel cached for formula cells (prices are often formulas)
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
        header_row = 1

        def header_of(col):
            raw = ws.cell(row=header_row, column=col).value
            return str(raw).lower().strip() if raw is not None else ''

        def column_values(col):
            sample = [ws.cell(row=r, column=col).value for r in range(2, min(500, ws.max_row + 1))]
            return [s for s in sample if s is not None and str(s).strip()]

        def find_by_header(*keywords, numeric=False):
            """First column whose header matches a keyword and that actually holds data."""
            for col in range(1, ws.max_column + 1):
                header = header_of(col)
                if not any(k in header for k in keywords):
                    continue
                values = column_values(col)
                if numeric:
                    values = [v for v in values if isinstance(v, (int, float))]
                if values:
                    return col
            return None

        # Step 1: named columns win, since headers say what a column means
        name_col = find_by_header('наименование', 'название', 'name', 'товар')
        sku_col = find_by_header('артикул', 'sku', 'код')
        price_col = find_by_header('розничная', 'цена', 'price', numeric=True)
        category_col = find_by_header('категория', 'category')
        brand_col = find_by_header('бренд', 'brand')
        old_price_col = find_by_header('старая', 'old')
        stock_col = find_by_header('остаток', 'количество', 'кол-во', 'stock')
        desc_col = find_by_header('описание', 'description')
        image_col = find_by_header('изображен', 'картин', 'фото', 'image', 'photo')
        specs_col = find_by_header('характеристик', 'параметр', 'spec')

        if old_price_col == price_col:
            old_price_col = None

        # Rows may lack a retail price; a cost column then serves as the fallback,
        # preferring one already stated in roubles over another currency
        cost_cols = [
            col for col in range(1, ws.max_column + 1)
            if any(k in header_of(col) for k in ('себестоимость', 'стоимость', 'cost'))
            and any(isinstance(v, (int, float)) for v in column_values(col))
        ]
        price_fallback_col = next(
            (col for col in cost_cols if 'руб' in header_of(col) or '₽' in header_of(col)),
            cost_cols[0] if cost_cols else None
        )
        if not price_col:
            price_col, price_fallback_col = price_fallback_col, None

        # Step 2: fall back to data patterns for the columns headers did not name
        NON_PRICE_HEADERS = ('вес', 'weight', 'курс', 'rate', 'контакт', 'id', 'артикул', 'остаток')
        for col in range(1, min(ws.max_column + 1, 25)):
            non_empty = column_values(col)
            if not non_empty:
                continue

            if not sku_col and all(isinstance(s, str) and 2 <= len(s) <= 30 for s in non_empty[:5]):
                if any(any(c.isalpha() for c in str(s)) for s in non_empty[:3]):
                    sku_col = col

            if not name_col and all(isinstance(s, str) and len(s) > 10 for s in non_empty[:3]):
                name_col = col

            numeric_vals = [s for s in non_empty if isinstance(s, (int, float)) and s > 1]
            if (not price_col and len(numeric_vals) >= 3
                    and col not in (sku_col, name_col, stock_col, old_price_col)
                    and not any(k in header_of(col) for k in NON_PRICE_HEADERS)):
                price_col = col

        if not name_col:
            name_col = 1
        if not sku_col:
            sku_col = 2 if name_col != 2 else 3

        embedded_images = extract_embedded_images(file_bytes, ws.title)

        imported = 0
        updated = 0
        errors = []

        for row in range(header_row + 1, ws.max_row + 1):
            sku = str(ws.cell(row=row, column=sku_col).value or '').strip()
            name = str(ws.cell(row=row, column=name_col).value or '').strip()

            if not sku or not name:
                continue

            try:
                # Get or create category
                category = None
                if category_col:
                    category_name = str(ws.cell(row=row, column=category_col).value or '').strip()
                    if category_name:
                        category = Category.query.filter_by(name=category_name).first()
                        if not category:
                            category = Category(
                                name=category_name,
                                slug=category_name.lower().replace(' ', '-')
                            )
                            db.session.add(category)
                            db.session.flush()

                # Get or create brand
                brand = None
                if brand_col:
                    brand_name = str(ws.cell(row=row, column=brand_col).value or '').strip()
                    if brand_name:
                        brand = Brand.query.filter_by(name=brand_name).first()
                        if not brand:
                            brand = Brand(name=brand_name)
                            db.session.add(brand)
                            db.session.flush()

                # Get price
                price = 0
                for col in (price_col, price_fallback_col):
                    if not col:
                        continue
                    price_val = ws.cell(row=row, column=col).value
                    if isinstance(price_val, (int, float)):
                        price = float(price_val)
                        break

                # Get old price
                old_price = None
                if old_price_col:
                    old_price_val = ws.cell(row=row, column=old_price_col).value
                    if isinstance(old_price_val, (int, float)):
                        old_price = float(old_price_val)

                # Get stock
                stock = 0
                if stock_col:
                    stock_val = ws.cell(row=row, column=stock_col).value
                    if stock_val:
                        stock = int(float(stock_val))

                # Get description
                description = ''
                if desc_col:
                    description = str(ws.cell(row=row, column=desc_col).value or '').strip()

                # Get image URL
                image_url = ''
                if image_col:
                    image_url = str(ws.cell(row=row, column=image_col).value or '').strip()

                # Check if product exists
                existing = Product.query.filter_by(sku=sku).first()
                if existing:
                    existing.name = name
                    existing.description = description or existing.description
                    if category:
                        existing.category_id = category.id
                    if brand:
                        existing.brand_id = brand.id
                    existing.price = price if price > 0 else existing.price
                    existing.old_price = old_price if old_price else existing.old_price
                    existing.stock = stock
                    existing.updated_at = datetime.utcnow()
                    db.session.flush()
                    product = existing
                    updated += 1
                else:
                    if not category:
                        category = Category.query.first()
                        if not category:
                            category = Category(name="Без категории", slug="without-category")
                            db.session.add(category)
                            db.session.flush()

                    product = Product(
                        sku=sku,
                        name=name,
                        description=description,
                        category_id=category.id,
                        brand_id=brand.id if brand else None,
                        price=price if price > 0 else 0,
                        old_price=old_price,
                        stock=stock
                    )
                    db.session.add(product)
                    db.session.flush()
                    imported += 1

                # A filled cell replaces what the product had; an empty one leaves it alone
                if specs_col:
                    cell_text = ws.cell(row=row, column=specs_col).value
                    if parse_specifications(cell_text):
                        replace_specifications(product.id, cell_text)

                if not product.image:
                    try:
                        stored_path = None
                        if image_url.startswith(('http://', 'https://')):
                            stored_path = download_image_to_uploads(product.id, image_url)
                        elif row in embedded_images:
                            data, ext = embedded_images[row]
                            stored_path = save_image_bytes(product.id, data, ext)

                        if stored_path:
                            product.image = stored_path
                            db.session.add(ProductImage(
                                product_id=product.id,
                                image_url=stored_path,
                                order=0
                            ))
                    except Exception as img_error:
                        errors.append(f"Строка {row}: не удалось сохранить картинку ({img_error})")

            except Exception as e:
                errors.append(f"Строка {row}: {str(e)}")

        db.session.commit()

        return jsonify({
            'message': f'Импорт завершен: добавлено {imported}, обновлено {updated}',
            'imported': imported,
            'updated': updated,
            'errors': errors[:10]  # Return first 10 errors
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Ошибка чтения файла: {str(e)}'}), 400

# ===== SEO ANALYTICS =====

# A search result shows roughly this much of a title before cutting it off
TITLE_MAX = 70
TITLE_MIN = 15
DESCRIPTION_MIN = 120

def product_brief(product):
    return {'id': product.id, 'sku': product.sku, 'name': product.name}

@app.route('/api/admin/seo/catalog', methods=['GET'])
@require_admin
def seo_catalog():
    """What in the catalogue keeps a product from ranking"""
    products = Product.query.filter_by(is_active=True).all()
    total = len(products)

    described = [p for p in products if (p.description or '').strip()]
    short_description = [p for p in described if len(p.description.strip()) < DESCRIPTION_MIN]

    seen_names, duplicate_names = {}, []
    seen_skus, duplicate_skus = {}, []
    for product in products:
        key = (product.name or '').strip().lower()
        seen_names.setdefault(key, []).append(product)
        seen_skus.setdefault((product.sku or '').strip().lower(), []).append(product)

    for group in seen_names.values():
        if len(group) > 1:
            duplicate_names.extend(group)
    for group in seen_skus.values():
        if len(group) > 1:
            duplicate_skus.extend(group)

    specs = {row[0] for row in db.session.query(Specification.product_id).distinct()}

    issues = [
        {'key': 'no_description', 'title': 'Без описания',
         'why': 'Поисковику нечего показать в сниппете и не за что ранжировать',
         'severity': 'high',
         'products': [p for p in products if not (p.description or '').strip()]},
        {'key': 'short_description', 'title': f'Описание короче {DESCRIPTION_MIN} символов',
         'why': 'Слишком мало текста, чтобы страница отвечала на запрос',
         'severity': 'medium', 'products': short_description},
        {'key': 'no_image', 'title': 'Без фотографии',
         'why': 'Товар не попадёт в поиск по картинкам и в товарную выдачу',
         'severity': 'high',
         'products': [p for p in products if not p.image]},
        {'key': 'no_specs', 'title': 'Без характеристик',
         'why': 'Нет параметров, по которым товар ищут: размер, резьба, давление',
         'severity': 'medium',
         'products': [p for p in products if p.id not in specs]},
        {'key': 'long_name', 'title': f'Название длиннее {TITLE_MAX} символов',
         'why': 'В выдаче обрежется многоточием, важное может не попасть',
         'severity': 'medium',
         'products': [p for p in products if len(p.name or '') > TITLE_MAX]},
        {'key': 'short_name', 'title': f'Название короче {TITLE_MIN} символов',
         'why': 'Слишком общее название проигрывает конкурентам в выдаче',
         'severity': 'low',
         'products': [p for p in products if len(p.name or '') < TITLE_MIN]},
        {'key': 'duplicate_name', 'title': 'Одинаковые названия',
         'why': 'Поисковик считает такие страницы дублями и показывает одну',
         'severity': 'high', 'products': duplicate_names},
        {'key': 'duplicate_sku', 'title': 'Одинаковые артикулы',
         'why': 'Два товара с одним артикулом путают и покупателя, и выгрузку',
         'severity': 'high', 'products': duplicate_skus},
        {'key': 'no_price', 'title': 'Без цены',
         'why': 'Товар без цены не попадает в товарную выдачу',
         'severity': 'high',
         'products': [p for p in products if not p.price]},
        {'key': 'out_of_stock', 'title': 'Нулевой остаток',
         'why': 'Отсутствие в наличии понижает товар в выдаче',
         'severity': 'low',
         'products': [p for p in products if not p.stock]},
    ]

    # A product is ready when nothing serious is wrong with it
    serious = {p.id for issue in issues if issue['severity'] == 'high' for p in issue['products']}

    return jsonify({
        'total': total,
        'ready': total - len(serious),
        'issues': [{
            'key': issue['key'],
            'title': issue['title'],
            'why': issue['why'],
            'severity': issue['severity'],
            'count': len(issue['products']),
            'products': [product_brief(p) for p in issue['products'][:100]],
        } for issue in issues]
    })

@app.route('/api/admin/seo/traffic', methods=['GET'])
@require_admin
def seo_traffic():
    """What visitors open and search for"""
    days = request.args.get('days', 30, type=int)
    since = datetime.utcnow() - timedelta(days=days)

    view_counts = (db.session.query(ProductView.product_id, db.func.count(ProductView.id))
                   .filter(ProductView.viewed_at >= since)
                   .group_by(ProductView.product_id)
                   .order_by(db.func.count(ProductView.id).desc())
                   .limit(10).all())

    products = {p.id: p for p in Product.query.filter(
        Product.id.in_([pid for pid, _ in view_counts])).all()} if view_counts else {}

    searches = (db.session.query(SearchQuery.query, db.func.count(SearchQuery.id),
                                 db.func.max(SearchQuery.results_count))
                .filter(SearchQuery.searched_at >= since)
                .group_by(SearchQuery.query)
                .order_by(db.func.count(SearchQuery.id).desc())
                .limit(20).all())

    # One row per calendar day, so the chart can draw a line rather than a total
    def daily(model, stamp):
        counts = dict(db.session.query(db.func.date(stamp), db.func.count(model.id))
                      .filter(stamp >= since)
                      .group_by(db.func.date(stamp)).all())
        return counts

    views_by_day = daily(ProductView, ProductView.viewed_at)
    searches_by_day = daily(SearchQuery, SearchQuery.searched_at)

    start = (datetime.utcnow() - timedelta(days=days - 1)).date()
    by_day = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        by_day.append({
            'date': day,
            'views': views_by_day.get(day, 0),
            'searches': searches_by_day.get(day, 0),
        })

    return jsonify({
        'days': days,
        'by_day': by_day,
        'total_views': db.session.query(db.func.count(ProductView.id))
                       .filter(ProductView.viewed_at >= since).scalar() or 0,
        'total_searches': db.session.query(db.func.count(SearchQuery.id))
                          .filter(SearchQuery.searched_at >= since).scalar() or 0,
        'top_products': [{
            'id': pid,
            'name': products[pid].name if pid in products else 'Товар удалён',
            'sku': products[pid].sku if pid in products else '',
            'views': count
        } for pid, count in view_counts],
        'top_searches': [{'query': q, 'count': count, 'results': results}
                         for q, count, results in searches],
        'empty_searches': [{'query': q, 'count': count}
                           for q, count, results in searches if not results][:10]
    })

# ===== HEALTH CHECK =====

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

# ===== ERROR HANDLERS =====

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500

# ===== SCHEMA MIGRATION =====
#
# The database on the server was created before categories had a parent, so it
# is brought forward in place rather than by hand. Every step checks first and
# does nothing when it has already run, so a restart is harmless.

CATEGORIES_TABLE = """
    CREATE TABLE categories_migrated (
        id INTEGER NOT NULL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        slug VARCHAR(100),
        description TEXT,
        icon VARCHAR(255),
        parent_id INTEGER REFERENCES categories(id),
        sort_order INTEGER DEFAULT 0,
        UNIQUE (slug)
    )
"""

def column_names(connection, table):
    rows = connection.exec_driver_sql(f'PRAGMA table_info({table})').fetchall()
    return {row[1] for row in rows}

def name_is_unique(connection):
    """True while the old UNIQUE(name) is still on the table."""
    for index in connection.exec_driver_sql('PRAGMA index_list(categories)').fetchall():
        if not index[2]:  # not unique
            continue
        columns = connection.exec_driver_sql(f'PRAGMA index_info("{index[1]}")').fetchall()
        if [column[2] for column in columns] == ['name']:
            return True
    return False

def ensure_schema():
    """Bring an existing database up to the current model."""
    inspector = db.inspect(db.engine)
    if 'categories' not in inspector.get_table_names():
        return

    with db.engine.begin() as connection:
        existing = column_names(connection, 'categories')

        for column, definition in (('parent_id', 'INTEGER REFERENCES categories(id)'),
                                   ('sort_order', 'INTEGER DEFAULT 0')):
            if column not in existing:
                connection.exec_driver_sql(
                    f'ALTER TABLE categories ADD COLUMN {column} {definition}')
                print(f'Схема: добавлен столбец categories.{column}')

        # A subcategory called «Прямые» may exist under more than one section,
        # so the name is no longer unique across the whole table. SQLite cannot
        # drop a constraint, so the table is rebuilt once.
        if name_is_unique(connection):
            connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
            connection.exec_driver_sql(CATEGORIES_TABLE)
            connection.exec_driver_sql(
                'INSERT INTO categories_migrated '
                '(id, name, slug, description, icon, parent_id, sort_order) '
                'SELECT id, name, slug, description, icon, parent_id, sort_order FROM categories')
            connection.exec_driver_sql('DROP TABLE categories')
            connection.exec_driver_sql('ALTER TABLE categories_migrated RENAME TO categories')
            connection.exec_driver_sql(
                'CREATE INDEX IF NOT EXISTS ix_categories_parent_id ON categories (parent_id)')
            connection.exec_driver_sql(
                'CREATE INDEX IF NOT EXISTS ix_categories_sort_order ON categories (sort_order)')
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            print('Схема: имя категории больше не обязано быть уникальным на весь каталог')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_schema()
        print("Database initialized!")

    # Debug must be opted into: the Werkzeug debugger hands an interactive Python
    # console to anyone who can reach the port and trigger an error
    debug = os.getenv('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')
    app.run(debug=debug, host=os.getenv('HOST', '0.0.0.0'), port=int(os.getenv('PORT', 5000)))
