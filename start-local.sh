#!/bin/bash

echo "========================================"
echo "ROBOT E-commerce Store - Local Setup"
echo "========================================"
echo ""

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed"
    exit 1
fi

echo "Step 1: Setting up backend..."
cd backend

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Check if .env exists, create from template if not
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "NOTE: Edit .env to customize your admin key if needed"
fi

# Install dependencies
echo "Installing Python dependencies..."
pip install -q -r requirements.txt

# Create data directory
mkdir -p data

echo "Backend setup complete!"
echo ""
echo "Step 2: Starting backend server..."
echo "Backend will run on http://localhost:5000"
echo "Press Ctrl+C to stop"
echo ""

python app.py &
BACKEND_PID=$!

sleep 2

# Go back to project root
cd ..

echo ""
echo "Step 3: Starting frontend server..."
cd frontend

# Check if Python's http.server is available
echo "Frontend will run on http://localhost:8000"
echo "Press Ctrl+C to stop"
echo ""
echo "========================================"
echo "Open browser to: http://localhost:8000"
echo "Admin panel: http://localhost:8000/admin.html"
echo "Default admin key: admin_secret_key_2024"
echo "========================================"
echo ""

python3 -m http.server 8000 &
FRONTEND_PID=$!

# Handle Ctrl+C to clean up both processes
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT

# Wait for both processes
wait
