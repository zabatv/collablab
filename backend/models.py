from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    slug = db.Column(db.String(100), unique=True)
    description = db.Column(db.Text)
    icon = db.Column(db.String(255))
    products = db.relationship('Product', backref='category', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'icon': self.icon,
            'product_count': len(self.products)
        }

class Brand(db.Model):
    __tablename__ = 'brands'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    logo = db.Column(db.String(255))
    products = db.relationship('Product', backref='brand', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'logo': self.logo
        }

class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), unique=True, nullable=False)  # код из 1С
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    brand_id = db.Column(db.Integer, db.ForeignKey('brands.id'))

    price = db.Column(db.Float, nullable=False)
    old_price = db.Column(db.Float)
    stock = db.Column(db.Integer, default=0)

    image = db.Column(db.String(255))  # основное фото
    images = db.relationship('ProductImage', backref='product', lazy=True, cascade='all, delete-orphan')
    videos = db.relationship('ProductVideo', backref='product', lazy=True, cascade='all, delete-orphan')

    specifications = db.relationship('Specification', backref='product', lazy=True, cascade='all, delete-orphan')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    is_active = db.Column(db.Boolean, default=True)
    sync_with_1c = db.Column(db.Boolean, default=False)  # синхронизирован ли с 1С

    def to_dict(self, full=False):
        data = {
            'id': self.id,
            'sku': self.sku,
            'name': self.name,
            'category': self.category.to_dict() if self.category else None,
            'brand': self.brand.to_dict() if self.brand else None,
            'price': self.price,
            'old_price': self.old_price,
            'stock': self.stock,
            'in_stock': self.stock > 0,
            'image': self.image,
            'discount': round((1 - self.price / self.old_price) * 100) if self.old_price else None,
        }

        if full:
            data['description'] = self.description
            data['images'] = [img.to_dict() for img in self.images]
            data['videos'] = [vid.to_dict() for vid in self.videos]
            data['specifications'] = [spec.to_dict() for spec in self.specifications]
            data['created_at'] = self.created_at.isoformat()
            data['updated_at'] = self.updated_at.isoformat()

        return data

class ProductImage(db.Model):
    __tablename__ = 'product_images'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    image_url = db.Column(db.String(255), nullable=False)
    alt_text = db.Column(db.String(255))
    order = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'id': self.id,
            'url': self.image_url,
            'alt': self.alt_text,
            'order': self.order
        }

class ProductVideo(db.Model):
    __tablename__ = 'product_videos'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    video_url = db.Column(db.String(255), nullable=False)
    title = db.Column(db.String(255))

    def to_dict(self):
        return {
            'id': self.id,
            'url': self.video_url,
            'title': self.title
        }

class Specification(db.Model):
    __tablename__ = 'specifications'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    value = db.Column(db.String(255), nullable=False)

    def to_dict(self):
        return {
            'name': self.name,
            'value': self.value
        }
