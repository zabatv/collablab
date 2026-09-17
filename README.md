# ROBOT - E-commerce Electronics Store

A modern, responsive e-commerce website for selling electronics. Built with Flask backend, vanilla JavaScript frontend, and SQLite database.

## Features

- **Product Catalog** - Browse and filter products by category, brand, price, and search
- **Product Details** - View detailed product information with images, videos, and specifications
- **Admin Panel** - Manage products, categories, brands, and inventory
- **Image Management** - Upload and manage product images
- **Responsive Design** - Works on desktop, tablet, and mobile devices
- **Fast Search** - Real-time product search functionality
- **Stock Management** - Track product inventory
- **Discount Support** - Display original and discounted prices

## Prerequisites

- **Python 3.8+** (for backend)
- **Node.js 14+** (optional, for development)
- A modern web browser (Chrome, Firefox, Safari, Edge)

## Project Structure

```
collablab/
├── backend/
│   ├── app.py                 # Flask application
│   ├── models.py              # Database models
│   ├── requirements.txt        # Python dependencies
│   ├── .env.example           # Environment template
│   ├── .env                   # Environment config (create from .env.example)
│   ├── data/                  # SQLite database storage
│   └── 1c_sync/              # 1C integration (future)
├── frontend/
│   ├── index.html            # Homepage
│   ├── catalog.html          # Product catalog
│   ├── product.html          # Product detail page
│   ├── admin.html            # Admin panel
│   ├── css/
│   │   └── style.css         # All styling
│   ├── js/
│   │   ├── api.js            # API client
│   │   └── main.js           # Utilities and shared functions
│   ├── uploads/              # User-uploaded images
│   └── img/                  # Static images (logos, etc)
└── README.md                 # This file
```

## Backend Setup

### 1. Install Python Dependencies

```bash
cd backend
pip install -r requirements.txt
```

Or using a virtual environment (recommended):

```bash
cd backend
python -m venv venv

# On Linux/Mac:
source venv/bin/activate

# On Windows:
venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
cd backend
cp .env.example .env
```

Edit `.env` to customize settings:

```
ADMIN_KEY=your_secret_admin_key_here
FLASK_ENV=development
FLASK_DEBUG=1
PORT=5000
```

**IMPORTANT:** Change `ADMIN_KEY` to a strong password for production!

### 3. Initialize Database

The database will be created automatically on first run. To add sample data, run:

```bash
python
>>> from app import app, db
>>> from models import Category, Brand
>>> with app.app_context():
...     db.create_all()
...     # Add sample categories
...     cat1 = Category(name='Смартфоны', slug='smartphones')
...     cat2 = Category(name='Ноутбуки', slug='laptops')
...     db.session.add_all([cat1, cat2])
...     db.session.commit()
...     print('Database initialized!')
>>> exit()
```

### 4. Run Backend Server

```bash
python app.py
```

Server will start at: `http://localhost:5000`

Check health: `http://localhost:5000/health`

## Frontend Setup

The frontend is purely client-side (HTML, CSS, JavaScript) and doesn't require build steps.

### 1. Serve Frontend Files

You can use any static file server. Here are common options:

**Option A: Python's built-in server**

```bash
cd frontend
python -m http.server 8000
```

Access at: `http://localhost:8000`

**Option B: Node.js http-server**

```bash
cd frontend
npx http-server -p 8000
```

**Option C: VS Code Live Server Extension**

- Install "Live Server" extension in VS Code
- Right-click on `index.html` → "Open with Live Server"

### 2. Configure API Endpoint

The frontend is configured to communicate with the backend at `http://localhost:5000`. 

If your backend is on a different host/port, edit `frontend/js/api.js`:

```javascript
const API_BASE_URL = 'http://localhost:5000';  // Change this line
```

## Running the Complete Application

### Terminal 1 - Backend

```bash
cd backend
source venv/bin/activate  # or venv\Scripts\activate on Windows
python app.py
```

### Terminal 2 - Frontend

```bash
cd frontend
python -m http.server 8000
```

Then open: `http://localhost:8000`

## Admin Panel

### Access Admin Panel

1. Navigate to: `http://localhost:8000/admin.html`
2. Enter the admin key when prompted (default: `admin_secret_key_2024`)
3. Key is stored in localStorage for convenience

### Admin Features

**Products Tab:**
- View all products in a table
- Create new products with form
- Edit existing products
- Delete products
- Upload product images

**Categories Tab:**
- View all categories
- Create new categories
- Manage product categories

**Brands Tab:**
- View all brands
- Create new brands

### Adding Your First Product

1. Go to Admin Panel → Products → "+ Добавить товар"
2. Fill in product details:
   - **SKU** - Unique identifier (e.g., `ROBOT-001`)
   - **Название** - Product name
   - **Категория** - Select category (create if needed)
   - **Бренд** - Select brand (create if needed)
   - **Цена** - Current price
   - **Старая цена** (optional) - Original price for discount display
   - **Остаток** - Stock quantity
   - **Активный** - Enable/disable product
3. Click "Сохранить"
4. After creation, you can upload images

## API Documentation

### Public Routes (No Authentication)

#### Get Categories
```
GET /api/categories

Response:
[
  {
    "id": 1,
    "name": "Смартфоны",
    "slug": "smartphones",
    "product_count": 5
  }
]
```

#### Get Brands
```
GET /api/brands

Response:
[
  {"id": 1, "name": "Apple", "logo": null},
  {"id": 2, "name": "Samsung", "logo": null}
]
```

#### Get Products (with filters)
```
GET /api/products?page=1&per_page=20&category_id=1&sort=newest&search=iphone

Query Parameters:
- page: Page number (default: 1)
- per_page: Items per page (default: 20, max: 100)
- category_id: Filter by category
- brand_id: Filter by brand
- search: Search by name or description
- sort: Sort order (newest, name, price_asc, price_desc)

Response:
{
  "products": [...],
  "total": 45,
  "pages": 3,
  "current_page": 1
}
```

#### Get Single Product
```
GET /api/products/{id}

Response:
{
  "id": 1,
  "sku": "ROBOT-001",
  "name": "iPhone 15 Pro",
  "price": 120000,
  "old_price": 150000,
  "stock": 50,
  "in_stock": true,
  "image": "/uploads/main.jpg",
  "images": [...],
  "specifications": [...],
  "discount": 20
}
```

#### Search Products
```
GET /api/products/search?q=iphone

Returns: [product1, product2, ...]
```

### Admin Routes (Require X-Admin-Key header)

All admin routes require the `X-Admin-Key` header:

```
X-Admin-Key: your_admin_key_here
```

#### Create Product
```
POST /api/admin/products
Content-Type: application/json

{
  "sku": "ROBOT-001",
  "name": "iPhone 15 Pro",
  "description": "Latest iPhone model",
  "category_id": 1,
  "brand_id": 1,
  "price": 120000,
  "old_price": 150000,
  "stock": 50,
  "is_active": true
}
```

#### Update Product
```
PUT /api/admin/products/{id}
Content-Type: application/json

{
  "name": "Updated Name",
  "price": 110000,
  "stock": 45
}
```

#### Delete Product
```
DELETE /api/admin/products/{id}
```

#### Upload Product Image
```
POST /api/admin/products/{id}/upload-image
Content-Type: multipart/form-data

Form Data:
- image: <file>

Response:
{"url": "/uploads/filename.jpg"}
```

#### Create Category
```
POST /api/admin/categories
Content-Type: application/json

{
  "name": "Смартфоны",
  "slug": "smartphones",
  "description": "Mobile phones and devices"
}
```

#### Create Brand
```
POST /api/admin/brands
Content-Type: application/json

{
  "name": "Apple"
}
```

## Deployment

### Local Network Access

To access from other devices on your network:

1. Find your computer's IP address:
   - Linux/Mac: `ifconfig` (look for inet address)
   - Windows: `ipconfig` (look for IPv4 Address)

2. Update API endpoint in frontend: Replace `localhost` with your IP
3. Access from another device: `http://YOUR_IP:8000`

### Production Deployment

For production, consider:

1. **Use a production server** (Gunicorn, uWSGI) instead of Flask dev server
2. **Use a reverse proxy** (Nginx, Apache)
3. **Enable HTTPS** with SSL certificates
4. **Set strong admin key** in environment variables
5. **Use environment variables** for all sensitive data
6. **Set FLASK_ENV=production** and FLASK_DEBUG=0
7. **Store uploads** on cloud storage (AWS S3, etc.)
8. **Setup database backups** (SQLite files or migrate to PostgreSQL)

Example Gunicorn command:
```bash
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

## Troubleshooting

### Backend won't start

**Error:** `ModuleNotFoundError: No module named 'flask'`

**Solution:** Ensure virtual environment is activated and dependencies are installed:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### Cannot connect to backend from frontend

**Error:** CORS error in browser console

**Solution:** 
- Ensure backend is running on port 5000
- Check that frontend API URL matches backend URL
- Backend already has CORS enabled for all origins

### Images not uploading

**Error:** Upload returns 400 or files not appearing

**Solution:**
- Check that `frontend/uploads/` directory has write permissions
- Verify file size is under 50MB
- Check browser console for specific error messages

### Admin key not working

**Error:** 401 Unauthorized when accessing admin routes

**Solution:**
- Verify key matches the one in backend `.env` file
- Clear browser localStorage and re-enter key
- Default key is: `admin_secret_key_2024`

## Database Schema

### Products Table
- `id` - Primary key
- `sku` - Unique SKU code
- `name` - Product name
- `description` - Full description
- `category_id` - Foreign key to categories
- `brand_id` - Foreign key to brands
- `price` - Current price (RUB)
- `old_price` - Original price for discount calculation
- `stock` - Quantity in stock
- `image` - Main product image URL
- `is_active` - Whether product is visible
- `created_at`, `updated_at` - Timestamps
- `sync_with_1c` - Flag for 1C integration

### Categories Table
- `id` - Primary key
- `name` - Category name
- `slug` - URL-friendly name
- `description` - Category description
- `icon` - Category icon (optional)

### Brands Table
- `id` - Primary key
- `name` - Brand name
- `logo` - Brand logo URL

### Product Images Table
- `id` - Primary key
- `product_id` - Foreign key
- `image_url` - URL to image
- `alt_text` - Alt text for accessibility
- `order` - Display order

### Product Videos Table
- `id` - Primary key
- `product_id` - Foreign key
- `video_url` - URL to video
- `title` - Video title

### Specifications Table
- `id` - Primary key
- `product_id` - Foreign key
- `name` - Spec name (e.g., "Color", "RAM")
- `value` - Spec value (e.g., "Black", "8GB")

## Future Enhancements

- [ ] 1C Database Synchronization
- [ ] Shopping Cart System
- [ ] User Accounts & Orders
- [ ] Payment Gateway Integration (Yandex.Kassa, Stripe)
- [ ] Email Notifications
- [ ] Reviews & Ratings
- [ ] Product Recommendations
- [ ] Mobile App
- [ ] Analytics Dashboard
- [ ] Multi-language Support

## Support

For issues or questions:
1. Check the Troubleshooting section
2. Review browser console errors (F12 → Console tab)
3. Check backend logs in terminal
4. Verify database file exists at `backend/data/products.db`

## License

Proprietary - ROBOT Electronics Store
