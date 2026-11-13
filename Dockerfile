# Backend Đại Việt Sử Ký (FastAPI + WebSocket) — dùng để deploy lên Render / Hugging Face Space / bất kỳ host Docker nào.
# 5 API key KHÔNG nằm trong image: truyền lúc chạy qua biến môi trường bí mật GEMINI_API_KEYS (hoặc GEMINI_KEY_1..5).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Cài thư viện trước (tận dụng cache layer khi chỉ đổi mã nguồn)
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Mã nguồn backend + ảnh mẫu anh hùng gốc (rig/meta) để phục vụ /assets và dựng cảnh
COPY backend ./backend
COPY frontend/public/assets/heroes ./frontend/public/assets/heroes

EXPOSE 8000

# Render/Spaces cấp cổng qua biến PORT; mặc định 8000 khi chạy cục bộ.
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
