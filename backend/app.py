from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from models import db, Product, Category, Brand, ProductImage, ProductVideo, Specification
from werkzeug.utils import secure_filename
import os
from datetime import datetime
from functools import wraps

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
