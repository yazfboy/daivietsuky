# Triển khai Đại Việt Sử Ký

Dự án gồm hai phần tách biệt để **API key không bao giờ lộ ra trình duyệt**:

| Phần | Nơi chạy | Key API? |
| --- | --- | --- |
| **Frontend** (Vite + React) | GitHub Pages — tĩnh, công khai | ❌ Không bao giờ chứa key |
| **Backend** (FastAPI + 5-key pool, AI Nhân vật/Đạo diễn, DP, visual) | Máy của bạn hoặc một host (Render/HF Space) | ✅ Key là biến môi trường bí mật phía server |

Frontend tự dò backend qua biến `VITE_API_BASE_URL`:
- **Có** địa chỉ backend → gọi Gemini thật (đa luồng 5 key, khẩu hình, cảnh động).
- **Trống** → tự chạy engine sẵn có trong trình duyệt (Intro Motion, chat, sân khấu động) ở chế độ demo, không cần server.

---

## 1. Chạy đầy đủ ở máy (có AI thật)

```bash
python run_project.py
```

Mở http://localhost:5173 — frontend gọi backend localhost:8000 qua proxy Vite. Key đọc từ `./.env` (5 dòng key).

## 2. Trang công khai trên GitHub Pages

Đã có sẵn workflow `.github/workflows/deploy.yml`: mỗi lần đẩy lên `main`, nó build frontend (base `/daivietsuky/`) và publish lên nhánh `gh-pages`.

Bật Pages một lần: **repo → Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `gh-pages` / `/ (root)`**.

Trang chạy tại **https://yazfboy.github.io/daivietsuky/**. Khi chưa cấu hình backend, trang chạy chế độ demo trong trình duyệt.

## 3. Cho AI thật chạy trên trang công khai (không cần bật máy)

Deploy backend lên một host, đặt 5 key làm **biến môi trường bí mật** (không commit):

**Render** (có sẵn `render.yaml` + `Dockerfile`):
1. https://dashboard.render.com → New → Blueprint → chọn repo `yazfboy/daivietsuky`.
2. Environment → `GEMINI_API_KEYS` = `key1,key2,key3,key4,key5` (bí mật).
3. Deploy → nhận URL, ví dụ `https://daivietsuky-backend.onrender.com`.

**Hugging Face Space (Docker)** cũng dùng chung `Dockerfile`; đặt 5 key trong **Settings → Variables and secrets** (`GEMINI_API_KEYS`).

Rồi trỏ frontend tới backend đó:
- repo → **Settings → Secrets and variables → Actions → Variables** → thêm `VITE_API_BASE_URL` = URL backend.
- Chạy lại workflow (**Actions → Deploy frontend to GitHub Pages → Run workflow**).

Xong: https://yazfboy.github.io/daivietsuky/ gọi Gemini thật, key nằm an toàn ở server.

---

**Bảo mật:** `.env` nằm trong `.gitignore`, không bao giờ commit. Frontend và bản build `dist/` không chứa bất kỳ chuỗi key nào. Key chỉ tồn tại ở `./.env` trên máy bạn hoặc biến môi trường bí mật của host.
