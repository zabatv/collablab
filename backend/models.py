from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Category(db.Model):
    """A node of the catalogue tree.

    A category points at its parent, so the same table holds sections and the
    subsections under them. Names are unique only among siblings — «Прямые»
    may sit under Фитинги and under Клапаны at the same time — and sort_order
    is what the admin drags around to decide what comes first.
    """

    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True)
    description = db.Column(db.Text)
    icon = db.Column(db.String(255))

    parent_id = db.Column(db.Integer, db.ForeignKey('categories.id'), index=True)
    sort_order = db.Column(db.Integer, default=0, index=True)

    children = db.relationship(
        'Category', backref=db.backref('parent', remote_side=[id]),
        lazy='select', order_by='Category.sort_order, Category.name')

    products = db.relationship('Product', backref='category', lazy=True, cascade='all, delete-orphan')

    def descendants(self):
        """This category and everything under it, however deep."""
        found = [self]
        for child in self.children:
            found.extend(child.descendants())
        return found

    def total_product_count(self):
        """Products here plus in every subcategory — what a section shows."""
        return sum(len(node.products) for node in self.descendants())

    def preview_image(self):
        """A photo for the tile: the first product in the branch that has one."""
        for node in self.descendants():
            for product in node.products:
                if product.image:
                    return product.image
        return None

    def path(self):
        """From the root down to this category, for breadcrumbs."""
        chain, node = [], self
        seen = set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    def to_dict(self, with_children=False):
        data = {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'icon': self.icon,
            'parent_id': self.parent_id,
            'sort_order': self.sort_order,
            # own products, and the total including subcategories
            'product_count': len(self.products),
            'total_count': self.total_product_count(),
            'has_children': bool(self.children),
            'image': self.icon or self.preview_image(),
        }

        if with_children:
            data['children'] = [child.to_dict(with_children=True) for child in self.children]

        return data

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
            'logo': self.logo,
            # How many positions carry the brand — what the admin needs to see
            # before deleting one, and what hides an empty brand from a filter
            'product_count': len(self.products),
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

class Banner(db.Model):
    """A slide on the home page: news, an offer, a new product.

    The picture is the whole slide; the heading and the line under it are
    drawn over the bottom of it and may both be empty, for a banner that
    is already lettered. A slide with no link is not clickable.
    """

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
            'title': self.title,
            'subtitle': self.subtitle,
            'link': self.link,
            'sort_order': self.sort_order,
            'is_active': self.is_active,
        }

# Traffic is recorded as plain counts of what was opened or searched for.
# Nothing identifying a visitor is stored — no address, no identifier.

class ProductView(db.Model):
    __tablename__ = 'product_views'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

class SearchQuery(db.Model):
    __tablename__ = 'search_queries'

    id = db.Column(db.Integer, primary_key=True)
    query = db.Column(db.String(255), nullable=False, index=True)
    results_count = db.Column(db.Integer, default=0)
    searched_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
