import React from 'react';
import { motion } from 'framer-motion';
import {
  BookOpen,
  CheckCircle2,
  ChevronLeft,
  MessageSquarePlus,
  Sparkles,
} from 'lucide-react';
import { HERO_LIST } from '../data/heroes';
import { HeroCharacter, HeroId, PreloadProgress, SceneMode } from '../types/hero';

export interface HeroSidebarProps {
  selectedHeroId: HeroId;
  onSelectHero: (heroId: HeroId) => void;
  onResetChat: () => void;
  isOpen: boolean;
  onToggleSidebar: () => void;
  preloadProgress: PreloadProgress | null;
  sceneMode: SceneMode;
  mouthSyncEnabled: boolean;
}

export const HeroSidebar: React.FC<HeroSidebarProps> = ({
  selectedHeroId,
  onSelectHero,
  onResetChat,
  isOpen,
  onToggleSidebar,
  preloadProgress,
  sceneMode,
  mouthSyncEnabled,
}) => {
  return (
    <>
      {/* Lớp phủ mờ trên thiết bị nhỏ khi mở Sidebar */}
      {isOpen && (
        <div
          onClick={onToggleSidebar}
          className="fixed inset-0 z-20 bg-black/50 backdrop-blur-xs transition-opacity duration-200 lg:hidden"
        />
      )}

      <motion.aside
        data-testid="hero-sidebar"
        initial={false}
        animate={{
          width: isOpen ? 280 : 0,
          opacity: isOpen ? 1 : 0,
        }}
        transition={{
          duration: 0.24,
          ease: [0.22, 1, 0.36, 1],
        }}
        className="fixed left-0 top-0 bottom-0 z-30 flex flex-col overflow-hidden border-r border-white/[0.08] bg-[#111318]/82 backdrop-blur-xl lg:relative"
      >
        <div className="flex h-full w-[280px] flex-col justify-between p-4">
          {/* Phần đầu Sidebar: Thương hiệu Đại Việt Sử Ký + Nút Tạo Mới kiểu pill của character.ai */}
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between px-1 pt-1">
              <div className="flex items-center gap-2.5">
                <div className="flex h-8 w-8 items-center justify-center rounded-full border border-[#D4B982]/35 bg-[#D4B982]/10 text-[#D4B982]">
                  <BookOpen className="h-4 w-4" />
                </div>
                <div>
                  <h2 className="font-display text-sm font-semibold tracking-[0.12em] text-zinc-100">
                    ĐẠI VIỆT SỬ KÝ
                  </h2>
                  <p className="text-[11px] text-zinc-400">
                     Tam Vị Anh Hùng Dân Tộc
                  </p>
                </div>
              </div>

              <button
                type="button"
                onClick={onToggleSidebar}
                aria-label="Thu gọn thanh điều hướng"
                className="flex h-8 w-8 items-center justify-center rounded-full text-zinc-400 transition-colors duration-200 hover:bg-white/[0.07] hover:text-zinc-200"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            </div>

            {/* Nút Làm Mới Đối Thoại (Pill Button format từ character.ai) */}
            <button
              type="button"
              onClick={onResetChat}
              className="flex w-full items-center justify-center gap-2 rounded-full border border-white/[0.09] bg-white/[0.05] px-4 py-2.5 text-xs font-medium text-zinc-200 transition-colors duration-200 hover:border-[#D4B982]/40 hover:bg-white/[0.09] hover:text-white"
            >
              <MessageSquarePlus className="h-3.5 w-3.5 text-[#D4B982]" />
              <span>Làm mới cuộc đối thoại</span>
            </button>

            {/* Danh mục 3 nhân vật lịch sử cố định */}
            <div className="mt-1 flex flex-col gap-1.5">
              <div className="flex items-center justify-between px-2 pb-1">
                <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Danh Nhân Lịch Sử (3)
                </span>
                <span className="text-[10px] text-zinc-500">ID Cố Định</span>
              </div>

              {HERO_LIST.map((hero: HeroCharacter) => {
                const isSelected = hero.id === selectedHeroId;
                const assetLoaded =
                  preloadProgress?.items?.[hero.id]?.loaded ?? true;

                return (
                  <button
                    key={hero.id}
                    type="button"
                    data-testid={`sidebar-hero-${hero.id}`}
                    data-character-id={hero.id}
                    onClick={() => onSelectHero(hero.id)}
                    className={`group relative flex w-full items-center gap-3 rounded-2xl p-2.5 text-left transition-all duration-200 ${
                      isSelected
                        ? 'border border-[#D4B982]/35 bg-white/[0.09] text-zinc-100 shadow-md'
                        : 'border border-transparent bg-transparent text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200'
                    }`}
                  >
                    {/* Thanh chỉ báo đang chọn */}
                    {isSelected && (
                      <motion.span
                        layoutId="active-hero-indicator"
                        className="absolute left-0 top-3 bottom-3 w-1 rounded-r-full bg-[#D4B982]"
                        transition={{ duration: 0.2 }}
                      />
                    )}

                    {/* Avatar tròn chuẩn character.ai */}
                    <div
                      className={`relative h-11 w-11 shrink-0 overflow-hidden rounded-full border transition-transform duration-200 group-hover:scale-105 ${
                        isSelected
                          ? 'border-[#D4B982] ring-2 ring-[#D4B982]/25'
                          : 'border-white/[0.12]'
                      } bg-[#181B22]`}
                    >
                      <img
                        src={hero.heroImageUrl}
                        alt={hero.name}
                        className="h-full w-full object-cover object-top"
                      />
                      {assetLoaded && (
                        <span
                          title="Đã tải trước vào bộ nhớ đệm"
                          className="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border border-[#111318] bg-emerald-400"
                        />
                      )}
                    </div>

                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-1">
                        <p
                          className={`truncate text-sm font-medium ${
                            isSelected ? 'text-white' : 'text-zinc-200'
                          }`}
                        >
                          {hero.name}
                        </p>
                      </div>
                      <p className="truncate text-[11px] text-zinc-400">
                        {hero.era}
                      </p>
                      <p className="mt-0.5 truncate font-mono text-[10px] text-zinc-500">
                        #{hero.id}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Phần chân Sidebar: Trạng thái Sân khấu & Bộ nhớ đệm Pre-load */}
          <div className="flex flex-col gap-2.5 rounded-2xl border border-white/[0.07] bg-white/[0.03] p-3 text-[11px] text-zinc-400">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 font-medium text-zinc-300">
                <Sparkles className="h-3.5 w-3.5 text-[#D4B982]" />
                <span>Trạng thái Sân khấu</span>
              </span>
              <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                Sẵn sàng
              </span>
            </div>

            <div className="flex flex-col gap-1 border-t border-white/[0.06] pt-2 text-[11px]">
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Chế độ cảnh:</span>
                <span className="font-mono text-[10px] text-zinc-200">
                  {sceneMode}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Đồng bộ môi:</span>
                <span
                  className={`font-mono text-[10px] ${
                    mouthSyncEnabled ? 'text-emerald-300' : 'text-amber-300'
                  }`}
                >
                  {mouthSyncEnabled ? 'BẬT (Talking)' : 'TẮT (Action)'}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Pre-load 3 ảnh:</span>
                <span className="flex items-center gap-1 text-emerald-300">
                  <CheckCircle2 className="h-3 w-3" />
                  <span>100% Cache</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </motion.aside>
    </>
  );
};
