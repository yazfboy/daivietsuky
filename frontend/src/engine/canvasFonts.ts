/**
 * Font cho chữ vẽ trên canvas (tên trận / địa danh trên nền giấy dó).
 *
 * Canvas KHÔNG tự chờ font web: nếu vẽ khi font chưa tải, trình duyệt lấy font dự phòng (trước đây là Georgia, thiếu
 * glyph dựng sẵn nên "Đằng" bị tách dấu thành "Đă`ng"). Vì vậy chữ chỉ được vẽ sau khi `document.fonts.load()` đã nạp
 * xong "Noto Serif" (Google Fonts, có subset vietnamese — khai báo trong index.html) cho đúng các ký tự cần vẽ.
 * Nếu không nạp được (mất mạng, bị chặn) sau CANVAS_FONT_TIMEOUT_MS: dùng font hệ thống có đủ dấu tiếng Việt —
 * danh sách dự phòng không có Georgia.
 */
export const CANVAS_SERIF = '"Noto Serif"';
export const CANVAS_FALLBACK = '"Times New Roman", "Segoe UI", Arial, sans-serif';
export const CANVAS_FONT_TIMEOUT_MS = 4000;

export type CanvasFontState = 'pending' | 'ready' | 'fallback';

/** Chuỗi `ctx.font`: khi font web đã sẵn sàng chỉ dùng Noto Serif; khi dự phòng thì không bao giờ có Georgia. */
export function canvasFont(weight: number, sizePx: number, state: CanvasFontState): string {
  const size = Math.round(sizePx * 10) / 10;
  return state === 'ready' ? `${weight} ${size}px ${CANVAS_SERIF}` : `${weight} ${size}px ${CANVAS_SERIF}, ${CANVAS_FALLBACK}`;
}

/**
 * Nạp Noto Serif cho các độ đậm + đúng các ký tự sẽ vẽ. `onChange` được gọi khi trạng thái đổi:
 * 'fallback' (hết thời gian chờ) rồi có thể 'ready' (font về muộn) → nơi gọi vẽ lại chữ.
 */
export function loadCanvasFont(
  weights: readonly number[],
  text: string,
  onChange: (state: CanvasFontState) => void,
  fonts: FontFaceSet | undefined = typeof document !== 'undefined' ? document.fonts : undefined
): () => void {
  let state: CanvasFontState = 'pending';
  let cancelled = false;
  const set = (next: CanvasFontState) => {
    if (cancelled || state === 'ready' || state === next) return;
    state = next;
    onChange(next);
  };
  if (!fonts || typeof fonts.load !== 'function') {
    set('fallback');
    return () => undefined;
  }
  const timer = setTimeout(() => set('fallback'), CANVAS_FONT_TIMEOUT_MS);
  Promise.all(weights.map((weight) => fonts.load(`${weight} 32px ${CANVAS_SERIF}`, text)))
    .then((loaded) => set(loaded.every((faces) => faces.length > 0) ? 'ready' : 'fallback'))
    .catch(() => set('fallback'))
    .finally(() => clearTimeout(timer));
  return () => {
    cancelled = true;
    clearTimeout(timer);
  };
}
