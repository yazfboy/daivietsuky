import React from 'react';
import { Compass, Eye, Film, Image as ImageIcon, Shield, Sparkles, VideoOff } from 'lucide-react';
import type { ApprovedVideo, SceneData, SceneMode } from '../types/hero';
import { effectiveBackdrop } from '../utils/sceneRules';
import { AI_IMAGE_LABEL, AI_VIDEO_LABEL } from '../utils/videoLibrary';

/** Chỉ nhận link http(s) cho dòng ghi nguồn (chặn javascript:, data:...). */
export const safeHttpUrl = (value?: string): string | undefined => {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : undefined;
  } catch {
    return undefined;
  }
};

interface StageInfoBarProps {
  sceneData: SceneData | null;
  sceneMode: SceneMode;
  mouthSyncEnabled: boolean;
  fallbackTitle: string;
  /** Clip AI đã duyệt cho bối cảnh hiện tại (null → không hiện nút "Xem video"). */
  video?: ApprovedVideo | null;
  videoOn?: boolean;
  onToggleVideo?: () => void;
}

const pill = 'flex min-w-0 max-w-full items-center gap-1.5 border border-white/[0.08] bg-[#12141A]/80 px-2.5 py-1 backdrop-blur-md';

/** "Công cụ: … · Thực hiện: … · Duyệt: …" — chỉ ghép các trường có giá trị. */
const aiCredits = (fields: Array<[string, string | undefined]>): string =>
  fields
    .filter(([, value]) => value)
    .map(([name, value]) => `${name}: ${value}`)
    .join(' · ');

/**
 * Thẻ thông tin sân khấu + chú thích & ghi nguồn ảnh nền. Nằm ngay dưới khung chat, cùng lớp với khung chat nên
 * không bị khung chat che (trước đây nằm trong lớp sân khấu z-0 ở góc phải và bị khung chat phủ lên), không bị mờ khi
 * bật "Ngắm sân khấu", và tự xuống dòng ở khổ điện thoại. Mọi chữ là chữ thuần (không render HTML).
 */
export const StageInfoBar: React.FC<StageInfoBarProps> = ({
  sceneData,
  sceneMode,
  mouthSyncEnabled,
  fallbackTitle,
  video = null,
  videoOn = false,
  onToggleVideo,
}) => {
  const isAction = sceneMode === 'BACKGROUND_ACTION';
  const playing = Boolean(video && videoOn);
  // Ảnh foregroundOnly không bao giờ hiện ở BACKGROUND_ACTION (dùng ảnh tĩnh AI hoặc phông trung tính).
  const backdrop = effectiveBackdrop(sceneData?.backdrop, sceneMode);
  const showAiStill = !playing && backdrop?.source === 'ai_still' && Boolean(backdrop.imageUrl);
  const showCredit = !playing && !showAiStill && backdrop && backdrop.source !== 'neutral' && backdrop.imageUrl;
  const licenseUrl = safeHttpUrl(backdrop?.licenseUrl);
  const sourceUrl = safeHttpUrl(backdrop?.sourceUrl);
  const joints = sceneData?.rigData ? Object.keys(sceneData.rigData.joints).length : null;

  return (
    <div
      data-testid="stage-info-bar"
      className="relative z-20 flex min-w-0 flex-wrap items-center justify-center gap-x-2 gap-y-1 px-3 pb-2 text-[10px] text-zinc-300 sm:px-6 lg:justify-end lg:pr-10"
    >
      <div
        data-testid="stage-info-card"
        className="flex min-w-0 max-w-full items-center gap-1.5 rounded-full border border-white/[0.08] bg-[#12141A]/80 px-2.5 py-1 backdrop-blur-md"
      >
        {isAction ? <Compass className="h-3 w-3 shrink-0 text-amber-400" /> : <Shield className="h-3 w-3 shrink-0 text-[#D4B982]" />}
        <span className="truncate font-medium text-zinc-200">
          {sceneData?.contextTitle || fallbackTitle}
          {sceneData?.contextYear ? ` · ${sceneData.contextYear}` : ''}
        </span>
        <span className="hidden shrink-0 text-zinc-600 sm:inline">|</span>
        <Eye className="hidden h-3 w-3 shrink-0 text-zinc-400 sm:inline" />
        <span className="hidden shrink-0 sm:inline">{joints ? `Rig 2.5D: ${joints} khớp` : 'Rig 2.5D'}</span>
        <span className="shrink-0 text-zinc-600">|</span>
        <Sparkles className="h-3 w-3 shrink-0 text-[#D4B982]" />
        <span className="shrink-0">MouthSync: {mouthSyncEnabled ? 'ON' : 'OFF'}</span>
      </div>

      {video && onToggleVideo && (
        <button
          type="button"
          data-testid="video-toggle"
          aria-pressed={playing}
          onClick={onToggleVideo}
          className={`${pill} rounded-full font-medium transition-colors ${
            playing ? 'border-amber-400/40 text-amber-200 hover:text-amber-100' : 'text-zinc-200 hover:text-white'
          }`}
        >
          {playing ? <VideoOff className="h-3 w-3 shrink-0" /> : <Film className="h-3 w-3 shrink-0" />}
          {playing ? 'Tắt video' : 'Xem video'}
        </button>
      )}

      {playing && video && (
        <div data-testid="ai-video-label" className={`${pill} flex-wrap rounded-xl`}>
          <Film className="h-3 w-3 shrink-0 text-amber-300" />
          <span className="shrink-0 font-semibold text-amber-100">{AI_VIDEO_LABEL}</span>
          <span className="min-w-0 break-words">
            {aiCredits([
              ['Công cụ', video.tool],
              ['Thực hiện', video.creator],
              ['Ngày tạo', video.createdAt],
              ['Duyệt', video.reviewedBy],
            ])}
          </span>
        </div>
      )}

      {showAiStill && backdrop && (
        <div data-testid="ai-image-label" className={`${pill} flex-wrap rounded-xl`}>
          <ImageIcon className="h-3 w-3 shrink-0 text-amber-300" />
          <span className="shrink-0 font-semibold text-amber-100">{backdrop.aiLabel || AI_IMAGE_LABEL}</span>
          <span className="min-w-0 break-words">
            {aiCredits([
              ['Công cụ', backdrop.tool],
              ['Thực hiện', backdrop.creator],
              ['Duyệt clip', backdrop.reviewedBy],
              ['Duyệt ảnh', backdrop.posterReviewedBy],
            ])}
          </span>
        </div>
      )}

      {showCredit && backdrop && (
        <div
          data-testid="backdrop-attribution"
          className="flex min-w-0 max-w-full items-center gap-1.5 rounded-full border border-white/[0.08] bg-[#12141A]/80 px-2.5 py-1 backdrop-blur-md"
        >
          <ImageIcon className="h-3 w-3 shrink-0 text-zinc-400" />
          <span className="min-w-0 truncate" title={`${backdrop.caption ? `${backdrop.caption} · ` : ''}Ảnh: ${backdrop.author ?? ''} · ${backdrop.license ?? ''}`}>
            {backdrop.caption ? (
              <span data-testid="backdrop-caption" className="text-zinc-100">
                {backdrop.caption} ·{' '}
              </span>
            ) : null}
            {'Ảnh: '}
            {backdrop.author || 'không rõ tác giả'}
            {backdrop.license ? ' · ' : ''}
            {licenseUrl ? (
              <a href={licenseUrl} target="_blank" rel="noopener noreferrer" className="underline decoration-zinc-500 hover:text-white">
                {backdrop.license}
              </a>
            ) : (
              backdrop.license
            )}
            {sourceUrl ? (
              <>
                {' · '}
                <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className="underline decoration-zinc-500 hover:text-white">
                  {sourceUrl.startsWith('https://commons.wikimedia.org/') ? 'Wikimedia Commons' : 'Nguồn ảnh'}
                </a>
              </>
            ) : null}
          </span>
        </div>
      )}
    </div>
  );
};
