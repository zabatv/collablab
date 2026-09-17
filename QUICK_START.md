# ROBOT E-commerce - Quick Start Guide

Get the application running in 5 minutes!

## Prerequisites

- Python 3.8+ installed
- Terminal/Command Prompt access

## Option 1: Automatic Setup (Recommended)

### Linux/Mac

```bash
chmod +x start-local.sh
./start-local.sh
```

### Windows

```bash
start-local.bat
```

This will:
1. Create Python virtual environment
2. Install dependencies
3. Set up database
4. Start backend (port 5000)
5. Start frontend (port 8000)

Open browser to: **http://localhost:8000**

---

## Option 2: Manual Setup

### Step 1: Backend Setup

```bash
cd backend
python -m venv venv

# Linux/Mac:
source venv/bin/activate

# Windows:
venv\Scripts\activate

pip install -r requirements.txt
mkdir data
cp .env.example .env
```

### Step 2: Start Backend

```bash
python app.py
```

Expected output:
```
 * Running on http://0.0.0.0:5000
 * Debug mode: on
```

### Step 3: Start Frontend (in new terminal)

```bash
cd frontend
python -m http.server 8000
```

Expected output:
```
Serving HTTP on 0.0.0.0 port 8000
```

### Step 4: Open Browser

Go to: **http://localhost:8000**

---

## Using the Application

### View Homepage
- Homepage with featured products and categories

### Browse Catalog
- Click "КАТАЛОГ" button
- Use filters on left sidebar
- Search for products using search bar

### Admin Panel
- Open `/login` (or `login.html`)
- Sign in with ADMIN_USERNAME and ADMIN_PASSWORD from backend `.env`
- Add categories, brands, and products
- Upload product images

### Add First Product

1. Admin Panel → Products tab
2. Click "+ Добавить товар" button
3. Fill in form:
   - **SKU**: Unique identifier (e.g., `ROBOT-001`)
   - **Название**: Product name
   - **Категория**: Create new or select existing
   - **Цена**: Price in rubles
   - **Остаток**: Stock quantity
4. Click "Сохранить"

---

## API URLs

| Endpoint | Purpose |
|----------|---------|
| `http://localhost:5000/api/products` | Get all products |
| `http://localhost:5000/api/categories` | Get all categories |
| `http://localhost:5000/api/brands` | Get all brands |
| `http://localhost:5000/health` | Backend health check |

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'flask'"
Make sure virtual environment is activated:
```bash
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate      # Windows
```

### "Port 5000 already in use"
Change port in `backend/app.py` line 324:
```python
app.run(debug=True, host='0.0.0.0', port=5001)  # Use 5001 instead
```

### "Cannot connect to backend" error
Ensure:
- Backend is running on port 5000
- Frontend is running on port 8000
- Both on same machine

### CORS error in browser console
Backend already has CORS enabled. Check:
- Backend is actually running
- Ports match configuration in `frontend/js/api.js`

---

## Next Steps

1. Read full documentation: `README.md`
2. Customize admin key in `backend/.env`
3. Add your products via admin panel
4. Deploy to server (see README.md)
5. Integrate with 1C (future enhancement)

---

## File Structure

```
├── backend/              # Flask API server
├── frontend/             # Web pages & client JS
├── README.md            # Full documentation
├── start-local.sh       # Linux/Mac auto-setup
└── start-local.bat      # Windows auto-setup
```

---

## Need Help?

1. Check Troubleshooting section below
2. Review browser console (F12 → Console tab)
3. Check terminal output for error messages
4. See full README.md for comprehensive guide

---

**Ready to code?** Start by modifying `frontend/css/style.css` to match your brand colors!
