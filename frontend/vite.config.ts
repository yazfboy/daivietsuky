import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * - `base`: khi build/preview mặc định '/daivietsuky/' để chạy đúng trên https://yazfboy.github.io/daivietsuky/;
 *   khi `npm run dev` ở máy thì '/' cho tiện. Ghi đè bằng biến môi trường VITE_BASE nếu cần.
 * - Proxy `/api` + `/ws` → backend FastAPI (mặc định http://127.0.0.1:8000, đổi bằng DVSK_BACKEND) chỉ dùng cho
 *   môi trường dev ở máy. Khi deploy lên GitHub Pages, frontend gọi backend qua biến VITE_API_BASE_URL (xem
 *   src/utils/backendClient.ts); nếu không đặt, giao diện tự chạy bằng engine sẵn có trong trình duyệt.
 */
const backend = process.env.DVSK_BACKEND ?? 'http://127.0.0.1:8000';
const proxy = {
  '/api': { target: backend, changeOrigin: true },
  '/health': { target: backend, changeOrigin: true },
  '/ws': { target: backend.replace(/^http/, 'ws'), ws: true, changeOrigin: true },
};

export default defineConfig({
  // Mặc định '/daivietsuky/' cho build + preview (khớp GitHub Pages). Script "dev" ghi đè '--base=/' cho tiện ở máy.
  base: process.env.VITE_BASE ?? '/daivietsuky/',
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy,
  },
  preview: {
    port: 4173,
    proxy,
  },
});
