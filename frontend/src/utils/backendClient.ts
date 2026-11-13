import {
  BackdropInfo,
  BackgroundType,
  HeroId,
  HeroMetaData,
  HeroRigData,
  SceneBeatData,
  SceneMode,
  SpeechChunkData,
} from '../types/hero';

/**
 * Kết nối thời gian thực tới backend FastAPI (`backend/main.py`) qua WebSocket `/ws/chat/{characterId}`.
 * Vite proxy chuyển `/ws` và `/api` sang http://127.0.0.1:8000 (xem vite.config.ts).
 */

export interface BackendSceneData {
  phase: 'preview' | 'final';
  stateId: string;
  characterId: HeroId;
  sceneMode: SceneMode;
  mouthSyncEnabled: boolean;
  characterReferencePath: string;
  backgroundType: BackgroundType;
  contextKey: string;
  contextTopicKey: string;
  previousContextTopicKey: string | null;
  topicChanged: boolean;
  beyondEra: boolean;
  contextTitle: string;
  contextYear: string;
  backgroundPrompt: string;
  characterAction: string;
  scenePrompt: string;
  reason: string;
  decidedBy: string;
  rigData: HeroRigData | null;
  metaData: HeroMetaData | null;
}

export interface BackendVisualData {
  stateId: string;
  sceneKey: string;
  backgroundSource: string;
  fromCache: boolean;
  backgroundPngUrl?: string;
  animationWebpUrl?: string;
}

export type BackendEvent =
  | { type: 'turn_start'; characterId: HeroId; question: string; t_ms: number }
  | { type: 'scene'; t_ms: number; sceneData: BackendSceneData }
  | { type: 'speech'; t_ms: number; chunk: SpeechChunkData }
  | { type: 'storyboard'; t_ms: number; storyboard: { stateId: string; sceneMode: SceneMode; beats: SceneBeatData[] } }
  | { type: 'visual'; t_ms: number; status: 'ready' | 'rendering' | 'unavailable'; sceneData?: BackendVisualData; detail?: string }
  | { type: 'backdrop'; t_ms: number; stateId: string; backdrop: BackdropInfo }
  | { type: 'error'; t_ms: number; agent?: string; error: string }
  | {
      type: 'done';
      t_ms: number;
      answer: string;
      sceneMode: SceneMode;
      mouthSyncEnabled: boolean;
      directorDecidedBy: string;
      errors: string[];
    }
  | { type: 'turn_end'; t_ms?: number };

export interface ChatTurnHandle {
  close: () => void;
}

const CONNECT_TIMEOUT_MS = 4000;

/**
 * Địa chỉ backend FastAPI đã deploy (vd. https://daivietsuky.onrender.com), đặt qua biến build VITE_API_BASE_URL.
 * - Có giá trị  → gọi thẳng backend đó (WebSocket + tải ảnh cảnh động từ đó). Dùng khi chạy trên GitHub Pages.
 * - Để trống    → cùng origin (dev ở máy đi qua proxy Vite /ws → :8000). Trên Pages mà trống thì không có backend,
 *                 kết nối thất bại nhanh và App tự chuyển sang engine trong trình duyệt.
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');

export function hasBackend(): boolean {
  return API_BASE.length > 0;
}

function isLocalDevHost(): boolean {
  return /^(localhost|127\.0\.0\.1|\[::1\])$/.test(window.location.hostname);
}

/** Có backend để gọi không: có VITE_API_BASE_URL lúc build, hoặc đang dev ở máy (proxy Vite → :8000). */
export function backendReachable(): boolean {
  return API_BASE.length > 0 || isLocalDevHost();
}

export type ServerStatus = 'waking' | 'ready' | 'unavailable' | 'offline';

export const WAKE_RETRY_MS = 5000;
export const WAKE_MAX_MS = 120_000;
const WAKE_ATTEMPT_TIMEOUT_MS = 4500;

/**
 * Đánh thức backend (Render gói free ngủ sau ~15 phút): gọi `GET /health` ngay, cứ 5 giây thử lại, tối đa 2 phút.
 * `onStatus`: 'waking' khi bắt đầu → 'ready' khi /health trả {"status":"ok"} → 'unavailable' nếu hết 2 phút;
 * 'offline' nếu bản build không có backend (chạy engine trong trình duyệt). Trả về hàm huỷ.
 */
export function wakeBackend(onStatus: (status: ServerStatus) => void): () => void {
  if (!backendReachable()) {
    onStatus('offline');
    return () => undefined;
  }
  let cancelled = false;
  let timer: number | undefined;
  let controller: AbortController | null = null;
  const started = Date.now();
  onStatus('waking');

  const attempt = async () => {
    if (cancelled) return;
    controller = new AbortController();
    const abortTimer = window.setTimeout(() => controller?.abort(), WAKE_ATTEMPT_TIMEOUT_MS);
    let ok = false;
    try {
      const response = await fetch(`${API_BASE}/health`, { cache: 'no-store', signal: controller.signal });
      const body = response.ok ? ((await response.json()) as { status?: string }) : null;
      ok = body?.status === 'ok';
    } catch {
      ok = false;
    } finally {
      window.clearTimeout(abortTimer);
    }
    if (cancelled) return;
    if (ok) {
      onStatus('ready');
      return;
    }
    const elapsed = Date.now() - started;
    if (elapsed + WAKE_RETRY_MS > WAKE_MAX_MS) {
      onStatus('unavailable'); // mở khóa: các lượt hỏi sẽ tự dùng engine trong trình duyệt
      return;
    }
    timer = window.setTimeout(attempt, WAKE_RETRY_MS);
  };
  void attempt();
  return () => {
    cancelled = true;
    window.clearTimeout(timer);
    controller?.abort();
  };
}

/** Phân giải URL ảnh/cảnh động do backend trả (vd. "/assets/scenes/..") về đúng host backend khi deploy riêng. */
export function resolveMediaUrl(path?: string): string | undefined {
  if (!path) return undefined;
  if (/^https?:\/\//i.test(path)) return path;
  if (API_BASE) return `${API_BASE}${path.startsWith('/') ? '' : '/'}${path}`;
  return path;
}

function socketUrl(characterId: HeroId): string {
  if (API_BASE) {
    const url = new URL(API_BASE);
    const protocol = url.protocol === 'https:' ? 'wss' : 'ws';
    return `${protocol}://${url.host}/ws/chat/${characterId}`;
  }
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${protocol}://${window.location.host}/ws/chat/${characterId}`;
}

/**
 * Mở một lượt hỏi–đáp. `onEvent` nhận từng sự kiện; `onFailure(receivedAny)` khi không kết nối được hoặc
 * kết nối đứt trước `turn_end` (receivedAny = đã nhận ít nhất một sự kiện của lượt).
 */
export function openChatTurn(
  characterId: HeroId,
  question: string,
  previousContextTopicKey: string | null,
  onEvent: (event: BackendEvent) => void,
  onFailure: (receivedAny: boolean) => void,
): ChatTurnHandle {
  let settled = false;
  let receivedAny = false;
  let socket: WebSocket | null = null;

  // Trên host tĩnh (GitHub Pages) mà không khai báo VITE_API_BASE_URL thì không có backend để gọi:
  // chuyển ngay sang engine trong trình duyệt, khỏi chờ 4 giây timeout. (Dev ở localhost vẫn thử qua proxy Vite.)
  if (!backendReachable()) {
    window.setTimeout(() => onFailure(false), 0);
    return { close: () => undefined };
  }

  const finish = (failed: boolean) => {
    if (settled) return;
    settled = true;
    window.clearTimeout(connectTimer);
    if (socket && socket.readyState <= WebSocket.OPEN) socket.close(1000);
    if (failed) onFailure(receivedAny);
  };

  const connectTimer = window.setTimeout(() => {
    if (!socket || socket.readyState !== WebSocket.OPEN) finish(true);
  }, CONNECT_TIMEOUT_MS);

  try {
    socket = new WebSocket(socketUrl(characterId));
  } catch {
    window.setTimeout(() => finish(true), 0);
    return { close: () => finish(false) };
  }

  socket.onopen = () => {
    window.clearTimeout(connectTimer);
    // Backend không giữ trạng thái phiên: mỗi lượt gửi kèm mã bối cảnh của lượt trước (nếu có).
    socket?.send(JSON.stringify({ question, previousContextTopicKey }));
  };
  socket.onmessage = (message: MessageEvent<string>) => {
    if (settled) return;
    let event: BackendEvent;
    try {
      event = JSON.parse(message.data) as BackendEvent;
    } catch {
      return;
    }
    receivedAny = true;
    onEvent(event);
    if (event.type === 'turn_end') finish(false);
  };
  socket.onerror = () => finish(true);
  socket.onclose = () => finish(true);

  return { close: () => finish(false) };
}
