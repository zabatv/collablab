from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from models import db, Product, Category, Brand, ProductImage, ProductVideo, Specification
from werkzeug.utils import secure_filename
import os
from datetime import datetime
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
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, '..', 'frontend', 'uploads')

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)

# Initialize database
db.init_app(app)

# Admin authentication (простая защита)
ADMIN_KEY = os.getenv('ADMIN_KEY', 'admin_secret_key_2024')

def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        key = request.headers.get('X-Admin-Key')
        if key != ADMIN_KEY:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

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

@app.route('/api/categories', methods=['GET'])
def get_categories():
    """Get all product categories"""
    categories = Category.query.all()
    return jsonify([cat.to_dict() for cat in categories])

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
        query = query.filter_by(category_id=category_id)

    if brand_id:
        query = query.filter_by(brand_id=brand_id)

    if search:
        query = query.filter(
            (Product.name.ilike(f'%{search}%')) |
            (Product.description.ilike(f'%{search}%'))
        )

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
        filename = secure_filename(f"{product_id}_{datetime.now().timestamp()}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Set as main image if it's the first one
        if not product.image:
            product.image = f'/uploads/{filename}'
            db.session.commit()

        # Add to product images
        product_image = ProductImage(
            product_id=product_id,
            image_url=f'/uploads/{filename}',
            order=len(product.images)
        )
        db.session.add(product_image)
        db.session.commit()

        return jsonify({'url': f'/uploads/{filename}'}), 201
    except Exception as e:
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

@app.route('/api/admin/categories', methods=['GET', 'POST'])
@require_admin
def admin_categories():
    """Manage categories"""
    if request.method == 'GET':
        categories = Category.query.all()
        return jsonify([cat.to_dict() for cat in categories])

    data = request.get_json()
    try:
        category = Category(
            name=data['name'],
            slug=data.get('slug', data['name'].lower().replace(' ', '-')),
            description=data.get('description', '')
        )
        db.session.add(category)
        db.session.commit()
        return jsonify(category.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

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
    headers = ['ID', 'Артикул', 'Название', 'Фото товара', 'Описание', 'Категория', 'Бренд',
               'Цена', 'Старая цена', 'Остаток', 'Активен']
    photo_col = headers.index('Фото товара') + 1
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
            if col in [8, 9]:  # Price columns
                cell.number_format = '#,##0.00'
            elif col == 10:  # Stock
                cell.number_format = '#,##0'

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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print("Database initialized!")

    app.run(debug=True, host='0.0.0.0', port=5000)
