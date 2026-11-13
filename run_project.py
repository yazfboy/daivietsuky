"""Khởi chạy nhanh Đại Việt Sử Ký: bật đồng thời Backend (FastAPI :8000) và Frontend (Vite :5173).

    python run_project.py              # bật cả hai, mở trình duyệt khi sẵn sàng
    python run_project.py --no-browser

Ctrl+C để tắt cả hai tiến trình.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://localhost:5173"
IS_WINDOWS = os.name == "nt"


def npm_executable() -> str:
    npm = shutil.which("npm.cmd" if IS_WINDOWS else "npm") or shutil.which("npm")
    if not npm:
        sys.exit("Không tìm thấy npm. Hãy cài Node.js 18+ rồi chạy lại.")
    return npm


def ensure_dependencies(npm: str) -> None:
    missing = [name for name in ("fastapi", "uvicorn", "PIL", "gradio_client") if importlib.util.find_spec(name) is None]
    if missing:
        print(f"[setup] Cài thư viện Python còn thiếu: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "backend" / "requirements.txt")])
    if not (FRONTEND / "node_modules").is_dir():
        print("[setup] Cài thư viện frontend (npm install)…")
        subprocess.check_call([npm, "install"], cwd=FRONTEND)
    if not (ROOT / ".env").is_file():
        sys.exit("Thiếu file .env ở thư mục gốc (cần đúng 5 API key Google AI Studio, mỗi dòng một key).")


def wait_for(url: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.call(["taskkill", "/F", "/T", "/PID", str(process.pid)], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
    else:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bật đồng thời backend + frontend Đại Việt Sử Ký.")
    parser.add_argument("--no-browser", action="store_true", help="không tự mở trình duyệt")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            # console Windows mặc định cp1252; line_buffering để dòng "sẵn sàng" hiện ngay cả khi chuyển hướng ra file
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    npm = npm_executable()
    ensure_dependencies(npm)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT,
        env=env,
    )
    frontend = subprocess.Popen([npm, "run", "dev", "--", "--port", "5173", "--strictPort"], cwd=FRONTEND, env=env)
    processes = (backend, frontend)
    try:
        backend_ok = wait_for(f"{BACKEND_URL}/api/health", 60)
        frontend_ok = wait_for(FRONTEND_URL, 60)
        print()
        print(f"  Backend  : {BACKEND_URL}  ({'sẵn sàng' if backend_ok else 'chưa phản hồi — xem log phía trên'})")
        print(f"  Frontend : {FRONTEND_URL}  ({'sẵn sàng' if frontend_ok else 'chưa phản hồi — xem log phía trên'})")
        print("  Nhấn Ctrl+C để tắt.\n")
        if frontend_ok and not args.no_browser:
            webbrowser.open(FRONTEND_URL)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        return next(process.returncode for process in processes if process.poll() is not None) or 0
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            stop(process)


if __name__ == "__main__":
    sys.exit(main())
