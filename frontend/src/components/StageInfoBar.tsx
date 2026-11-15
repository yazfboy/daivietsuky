import React from 'react';
import { Compass, Eye, Image as ImageIcon, Shield, Sparkles } from 'lucide-react';
import type { SceneData, SceneMode } from '../types/hero';

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
}

/**
 * Thẻ thông tin sân khấu + chú thích & ghi nguồn ảnh nền. Nằm ngay dưới khung chat, cùng lớp với khung chat nên
 * không bị khung chat che (trước đây nằm trong lớp sân khấu z-0 ở góc phải và bị khung chat phủ lên), không bị mờ khi
 * bật "Ngắm sân khấu", và tự xuống dòng ở khổ điện thoại. Mọi chữ là chữ thuần (không render HTML).
 */
export const StageInfoBar: React.FC<StageInfoBarProps> = ({ sceneData, sceneMode, mouthSyncEnabled, fallbackTitle }) => {
  const isAction = sceneMode === 'BACKGROUND_ACTION';
  const backdrop = sceneData?.backdrop;
  const showCredit = backdrop && backdrop.source !== 'neutral' && backdrop.imageUrl;
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

      {showCredit && (
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
