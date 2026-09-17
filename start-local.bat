@echo off
setlocal enabledelayedexpansion

echo ========================================
echo ROBOT E-commerce Store - Local Setup
echo ========================================
echo.

REM Check Python version
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    pause
    exit /b 1
)

echo Step 1: Setting up backend...
cd backend

REM Create virtual environment if it doesn't exist
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Check if .env exists, create from template if not
if not exist ".env" (
    echo Creating .env file from template...
    copy .env.example .env
    echo NOTE: Edit .env to customize your admin key if needed
)

REM Install dependencies
echo Installing Python dependencies...
pip install -q -r requirements.txt

REM Create data directory
if not exist "data" mkdir data

echo Backend setup complete!
echo.
echo Step 2: Starting backend server...
echo Backend will run on http://localhost:5000
echo.

start cmd /k "python app.py"

timeout /t 2

cd ..

echo.
echo Step 3: Starting frontend server...
cd frontend

echo Frontend will run on http://localhost:8000
echo.
echo ========================================
echo Open browser to: http://localhost:8000
echo Admin panel: http://localhost:8000/admin.html
echo Default admin key: admin_secret_key_2024
echo ========================================
echo.

start cmd /k "python -m http.server 8000"

echo.
echo Servers started! Check the opened command windows.
echo Press Ctrl+C in each window to stop the servers.
pause
