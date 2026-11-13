import React, { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { HERO_LIST } from '../data/heroes';
import { PreloadProgress } from '../types/hero';
import { preloadAllHeroAssets } from '../utils/assetPreloader';

export interface IntroMotionProps {
  onComplete: (progress: PreloadProgress) => void;
  onProgressUpdate?: (progress: PreloadProgress) => void;
}

type IntroPhase = 'entering' | 'holding' | 'exiting' | 'done';

export const IntroMotion: React.FC<IntroMotionProps> = ({
  onComplete,
  onProgressUpdate,
}) => {
  const [phase, setPhase] = useState<IntroPhase>('entering');
  const [progress, setProgress] = useState<PreloadProgress>({
    completed: 0,
    total: HERO_LIST.length,
    percentage: 0,
    allReady: false,
    items: {} as PreloadProgress['items'],
  });

  useEffect(() => {
    let isMounted = true;
    let holdTimer: number | undefined;
    let exitTimer: number | undefined;

    const minDisplayTimeMs = 1550;
    const exitAnimationTimeMs = 750;
    const startTimestamp = performance.now();

    const holdPhaseTimeout = window.setTimeout(() => {
      if (isMounted) {
        setPhase((prev) => (prev === 'entering' ? 'holding' : prev));
      }
    }, 650);

    preloadAllHeroAssets((snapshot) => {
      if (!isMounted) return;
      setProgress(snapshot);
      onProgressUpdate?.(snapshot);
    }).then((finalSnapshot) => {
      if (!isMounted) return;
      const elapsed = performance.now() - startTimestamp;
      const remainingHold = Math.max(0, minDisplayTimeMs - elapsed);

      holdTimer = window.setTimeout(() => {
        if (!isMounted) return;
        setPhase('exiting');

        exitTimer = window.setTimeout(() => {
          if (!isMounted) return;
          setPhase('done');
          onComplete(finalSnapshot);
        }, exitAnimationTimeMs);
      }, remainingHold);
    });

    return () => {
      isMounted = false;
      window.clearTimeout(holdPhaseTimeout);
      if (holdTimer !== undefined) window.clearTimeout(holdTimer);
      if (exitTimer !== undefined) window.clearTimeout(exitTimer);
    };
  }, [onComplete, onProgressUpdate]);

  const handleSkip = () => {
    if (phase === 'exiting' || phase === 'done') return;
    setPhase('exiting');
    window.setTimeout(() => {
      setPhase('done');
      onComplete(progress);
    }, 450);
  };

  return (
    <AnimatePresence>
      {phase !== 'done' && (
        <motion.div
          key="intro-motion-overlay"
          data-testid="intro-motion"
          data-intro-phase={phase}
          initial={{ opacity: 1 }}
          animate={{
            opacity: phase === 'exiting' ? 0 : 1,
            scale: phase === 'exiting' ? 1.03 : 1,
          }}
          exit={{ opacity: 0 }}
          transition={{
            duration: 0.72,
            ease: [0.22, 1, 0.36, 1],
          }}
          className="fixed inset-0 z-50 flex flex-col items-center justify-between bg-[#0B0C0E] px-6 py-10 text-zinc-100 select-none overflow-hidden"
        >
          {/* Ánh sáng nền tối giản, tinh tế */}
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                'radial-gradient(circle at 50% 46%, rgba(212, 185, 130, 0.09) 0%, rgba(19, 21, 26, 0.4) 45%, rgba(11, 12, 14, 1) 80%)',
            }}
          />

          {/* Thanh trên cùng trống để cân đối bố cục trung tâm */}
          <div className="relative z-10 flex w-full max-w-5xl items-center justify-between text-[11px] uppercase tracking-[0.25em] text-zinc-500">
            <span>SCIC · Không Gian Đối Thoại Lịch Sử</span>
            <button
              type="button"
              onClick={handleSkip}
              className="rounded-full border border-white/[0.08] bg-white/[0.03] px-3.5 py-1 text-[11px] tracking-[0.15em] text-zinc-400 transition-colors duration-200 hover:border-white/20 hover:bg-white/[0.07] hover:text-zinc-200"
            >
              Vào thẳng →
            </button>
          </div>

          {/* Cụm Tên Sản Phẩm Chính Giữa Trang Web */}
          <motion.div
            initial={{ opacity: 0, y: 24, filter: 'blur(8px)' }}
            animate={
              phase === 'exiting'
                ? {
                    opacity: 0,
                    y: -38,
                    scale: 0.96,
                    filter: 'blur(6px)',
                  }
                : {
                    opacity: 1,
                    y: 0,
                    scale: 1,
                    filter: 'blur(0px)',
                  }
            }
            transition={{
              duration: phase === 'exiting' ? 0.65 : 0.85,
              ease: [0.22, 1, 0.36, 1],
            }}
            className="relative z-10 flex flex-col items-center text-center"
          >
            <motion.span
              initial={{ opacity: 0, letterSpacing: '0.15em' }}
              animate={{
                opacity: phase === 'exiting' ? 0 : 0.75,
                letterSpacing: '0.38em',
              }}
              transition={{ duration: 0.9, ease: 'easeOut' }}
              className="mb-4 text-xs font-medium uppercase text-[#D4B982]"
            >
              Khởi nguyên hào khí ngàn năm
            </motion.span>

            <h1
              data-testid="intro-product-title"
              className="font-display text-4xl font-semibold tracking-[0.18em] text-zinc-100 sm:text-6xl md:text-7xl"
            >
              ĐẠI VIỆT SỬ KÝ
            </h1>

            {/* Đường kẻ chỉ vàng tinh tế */}
            <motion.div
              initial={{ width: 0, opacity: 0 }}
              animate={{
                width: phase === 'exiting' ? '0px' : '180px',
                opacity: phase === 'exiting' ? 0 : 1,
              }}
              transition={{ duration: 0.75, delay: 0.15, ease: [0.22, 1, 0.36, 1] }}
              className="my-6 h-[1px] bg-gradient-to-r from-transparent via-[#D4B982]/70 to-transparent"
            />

            <motion.p
              initial={{ opacity: 0, y: 8 }}
              animate={{
                opacity: phase === 'exiting' ? 0 : 0.7,
                y: phase === 'exiting' ? -10 : 0,
              }}
              transition={{ duration: 0.7, delay: 0.2 }}
              className="max-w-md text-xs font-normal tracking-wide text-zinc-400 sm:text-sm"
            >
              Đối thoại trực tiếp cùng các bậc anh hùng dân tộc qua sân khấu tương tác thời gian thực
            </motion.p>
          </motion.div>

          {/* Tiến trình đa luồng tải ngầm (Pre-load) 3 bức ảnh nhân vật */}
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{
              opacity: phase === 'exiting' ? 0 : 1,
              y: phase === 'exiting' ? 16 : 0,
            }}
            transition={{ duration: 0.55 }}
            className="relative z-10 flex w-full max-w-md flex-col items-center gap-3"
          >
            <div className="flex w-full items-center justify-between text-[11px] text-zinc-400">
              <span className="tracking-wider uppercase">
                Đang nạp đa luồng chân dung danh nhân
              </span>
              <span className="font-mono text-[#D4B982]">
                {progress.completed}/{progress.total} ({progress.percentage}%)
              </span>
            </div>

            <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.07]">
              <motion.div
                initial={{ width: '0%' }}
                animate={{ width: `${Math.max(15, progress.percentage)}%` }}
                transition={{ duration: 0.3, ease: 'easeOut' }}
                className="h-full rounded-full bg-gradient-to-r from-[#9E8656] to-[#D4B982]"
              />
            </div>

            <div className="flex flex-wrap items-center justify-center gap-3 pt-1">
              {HERO_LIST.map((hero) => {
                const itemStatus = progress.items?.[hero.id];
                const isReady = Boolean(itemStatus?.loaded && itemStatus?.decoded);
                return (
                  <div
                    key={hero.id}
                    className="flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.02] px-2.5 py-1 text-[11px] text-zinc-400"
                  >
                    <span
                      className={`h-1.5 w-1.5 rounded-full transition-colors duration-300 ${
                        isReady ? 'bg-emerald-400' : 'bg-[#D4B982] animate-pulse'
                      }`}
                    />
                    <span>{hero.name}</span>
                  </div>
                );
              })}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
