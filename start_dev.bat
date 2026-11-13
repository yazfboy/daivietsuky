@echo off
REM Bat dong thoi Backend (FastAPI :8000) va Frontend (Vite :5173) cua Dai Viet Su Ky.
cd /d "%~dp0"
python run_project.py %*
