/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Đường dẫn gốc khi build (BASE_URL): '/' ở local, '/daivietsuky/' trên GitHub Pages. */
  readonly BASE_URL: string;
  /** Địa chỉ backend FastAPI đã deploy (vd. https://daivietsuky.onrender.com). Trống → chạy engine trong trình duyệt. */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
