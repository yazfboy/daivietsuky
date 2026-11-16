import type { ApprovedVideo } from '../types/hero';

/**
 * Thư viện video minh họa đã duyệt. Danh sách do `scripts/check_video_library.py` sinh lúc build
 * (`assets/videos/approved.json`) — chỉ chứa mục đủ người duyệt, không có mặt nhân vật chính, có file .mp4,
 * không còn rãnh âm thanh. Frontend kiểm lại từng mục lần nữa: một file .mp4 không có mục hợp lệ thì không hiện nút.
 */
export const AI_VIDEO_LABEL = 'Video minh họa do AI tạo';
export const AI_IMAGE_LABEL = 'Ảnh minh họa do AI tạo';

export type ApprovedVideoMap = Readonly<Record<string, ApprovedVideo>>;

const KEY_RE = /^[a-z0-9][a-z0-9_]*$/;
const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : '');

/** Giữ lại đúng các mục hợp lệ của approved.json; mọi thứ khác bị bỏ qua. */
export function parseApprovedVideos(raw: unknown, baseUrl: string = import.meta.env.BASE_URL): ApprovedVideoMap {
  const out: Record<string, ApprovedVideo> = {};
  const videos = raw && typeof raw === 'object' ? (raw as { videos?: unknown }).videos : undefined;
  if (!videos || typeof videos !== 'object') return out;
  for (const [key, value] of Object.entries(videos as Record<string, unknown>)) {
    if (!KEY_RE.test(key) || !value || typeof value !== 'object') continue;
    const item = value as Record<string, unknown>;
    const videoPath = text(item.video_path);
    const entry: ApprovedVideo = {
      contextTopicKey: key,
      videoUrl: `${baseUrl}${videoPath.replace(/^\/+/, '')}`,
      durationSeconds: typeof item.duration_seconds === 'number' ? item.duration_seconds : 0,
      aiVideoLabel: text(item.ai_video_label),
      creator: text(item.creator),
      tool: text(item.tool),
      createdAt: text(item.created_at),
      reviewedBy: text(item.reviewed_by),
    };
    const valid =
      videoPath === `/assets/videos/${key}.mp4` &&
      item.has_hero_face === false &&
      entry.reviewedBy !== '' &&
      entry.creator !== '' &&
      entry.tool !== '' &&
      entry.aiVideoLabel === AI_VIDEO_LABEL;
    if (valid) out[key] = entry;
  }
  return out;
}

/** Tải approved.json; lỗi mạng, 404 hay JSON hỏng → không có video nào (không hiện nút). */
export async function loadApprovedVideos(
  fetchImpl: typeof fetch = fetch,
  baseUrl: string = import.meta.env.BASE_URL
): Promise<ApprovedVideoMap> {
  try {
    const response = await fetchImpl(`${baseUrl}assets/videos/approved.json`, { cache: 'no-cache' });
    if (!response.ok) return {};
    return parseApprovedVideos(await response.json(), baseUrl);
  } catch {
    return {};
  }
}

export function approvedVideoFor(map: ApprovedVideoMap, key: string | undefined | null): ApprovedVideo | null {
  if (!key || !Object.prototype.hasOwnProperty.call(map, key)) return null;
  return map[key];
}
