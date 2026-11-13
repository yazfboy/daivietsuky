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

**Render** (có sẵn `render.yaml`: build `pip install -r backend/requirements.txt`, start `uvicorn backend.main:app`):
1. https://dashboard.render.com → New → Blueprint → chọn repo `yazfboy/daivietsuky`.
2. Environment → khai báo biến bí mật `GEMINI_API_KEYS` (5 key, cách nhau dấu phẩy). Không ghi giá trị vào file nào.
3. Deploy → URL dự kiến `https://daivietsuky-backend.onrender.com` (đã đặt sẵn trong `frontend/.env.production`).
4. Trước buổi demo mở `https://<url>/health` để đánh thức server (gói free ngủ sau ~15 phút).

Trên Render (biến `RENDER=true` do Render tự đặt), backend chỉ nhận CORS / WebSocket từ `https://yazfboy.github.io`.

**Hugging Face Space (Docker)** cũng dùng chung `Dockerfile`; đặt 5 key trong **Settings → Variables and secrets** (`GEMINI_API_KEYS`).

Frontend build production (`npm run build`) đọc `VITE_API_BASE_URL` từ `frontend/.env.production`. Nếu Render cấp URL khác:
- sửa file đó, hoặc repo → **Settings → Secrets and variables → Actions → Variables** → `VITE_API_BASE_URL` = URL backend;
- chạy lại workflow (**Actions → Deploy frontend to GitHub Pages → Run workflow**).

⚠️ Chỉ merge vào `main` sau khi backend Render đã chạy: workflow sẽ build bản trỏ về Render; backend chưa có thì trang chờ
4 giây rồi mới tự chuyển sang engine trong trình duyệt.

## 4. Ảnh nền theo bối cảnh

Thứ tự: **thư viện dựng sẵn** (`backend/data/scene_library.json` + ảnh `frontend/public/assets/scenes/{key}.jpg`) →
**Wikimedia Commons** (chỉ jpg/png khổ ngang ≥ 800px, tỷ lệ ≥ 1.2, giấy phép tự do, loại bản đồ/sơ đồ/quốc huy/cờ/logo,
ghi tác giả + giấy phép + link gốc) → **phông trung tính** (giấy dó + tên địa danh). Một mục thư viện chỉ được dùng khi
nhóm đã đặt file ảnh và điền đủ `author` + `license`.

## 5. Kiểm thử

```bash
python tests/test_history_rules.py      # cố định, không gọi API
python tests/test_live_director.py      # gọi Gemini + Wikimedia thật
python tests/scan_secrets.py            # build production rồi quét key trong file Git, lịch sử commit, frontend/dist
```

Xong: https://yazfboy.github.io/daivietsuky/ gọi Gemini thật, key nằm an toàn ở server.

---

**Bảo mật:** `.env` nằm trong `.gitignore`, không bao giờ commit. Frontend và bản build `dist/` không chứa bất kỳ chuỗi key nào. Key chỉ tồn tại ở `./.env` trên máy bạn hoặc biến môi trường bí mật của host.
