import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  ArrowUp,
  Eye,
  Film,
  MapPin,
  MessageSquare,
  PanelLeft,
  RotateCcw,
  Sparkles,
  Swords,
} from 'lucide-react';
import {
  ChatMessage,
  HeroCharacter,
  SceneData,
  SceneMode,
  SuggestedPrompt,
} from '../types/hero';

export interface GlassChatBoxProps {
  hero: HeroCharacter;
  messages: ChatMessage[];
  isStreaming: boolean;
  sceneMode: SceneMode;
  mouthSyncEnabled: boolean;
  sceneData: SceneData | null;
  isSidebarOpen: boolean;
  onToggleSidebar: () => void;
  onSendMessage: (question: string) => void;
  onSelectPrompt: (prompt: SuggestedPrompt) => void;
  onToggleSceneMode: (mode: SceneMode) => void;
  onResetChat: () => void;
}

/**
 * GlassChatBox — Khung chat hơi mờ (backdrop-blur, nền bán trong suốt tinh tế)
 * nằm phía trước DynamicHeroStage, kết hợp bố cục trung tâm & thanh nhập liệu dạng viên thuốc
 * (pill capsule) tinh chỉnh từ character.ai sang phong cách "Đại Việt Sử Ký".
 */
export const GlassChatBox: React.FC<GlassChatBoxProps> = ({
  hero,
  messages,
  isStreaming,
  sceneMode,
  mouthSyncEnabled,
  sceneData,
  isSidebarOpen,
  onToggleSidebar,
  onSendMessage,
  onSelectPrompt,
  onToggleSceneMode,
  onResetChat,
}) => {
  const [inputText, setInputText] = useState('');
  const [isStagePeekMode, setIsStagePeekMode] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = inputText.trim();
    if (!trimmed || isStreaming) return;
    setInputText('');
    onSendMessage(trimmed);
  };

  return (
    <div
      data-testid="glass-chat-wrapper"
      className="relative z-10 flex h-screen flex-1 flex-col justify-between overflow-hidden"
    >
      {/* Thanh điều hướng phía trên (Top Navigation Bar tinh chỉnh từ character.ai) */}
      <header className="flex items-center justify-between border-b border-white/[0.07] bg-[#0B0C0E]/55 px-4 py-3 backdrop-blur-md sm:px-6">
        <div className="flex items-center gap-3">
          {!isSidebarOpen && (
            <button
              type="button"
              onClick={onToggleSidebar}
              aria-label="Mở danh sách nhân vật"
              className="flex h-9 w-9 items-center justify-center rounded-full border border-white/[0.09] bg-white/[0.04] text-zinc-300 transition-colors duration-200 hover:bg-white/[0.1] hover:text-white"
            >
              <PanelLeft className="h-4 w-4" />
            </button>
          )}

          {/* Huy hiệu nhân vật đang đối thoại */}
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 overflow-hidden rounded-full border border-[#D4B982]/45 bg-[#181B22]">
              <img
                src={hero.heroImageUrl}
                alt={hero.name}
                className="h-full w-full object-cover object-top"
              />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-sm font-semibold text-zinc-100">
                  {hero.name}
                </h1>
                <span className="rounded-full border border-[#D4B982]/30 bg-[#D4B982]/10 px-2 py-0.5 text-[10px] font-medium text-[#D4B982]">
                  Sử Ký AI
                </span>
              </div>
              <p className="text-[11px] text-zinc-400">
                {hero.title} · {hero.years}
              </p>
            </div>
          </div>
        </div>

        {/* Cụm nút điều khiển chế độ Sân khấu & độ mờ khung chat */}
        <div className="flex items-center gap-2">
          <div className="hidden sm:flex items-center rounded-full border border-white/[0.09] bg-[#13151A]/75 p-0.5 text-[11px] backdrop-blur-md">
            <button
              type="button"
              onClick={() => onToggleSceneMode('FOREGROUND_TALKING')}
              className={`flex items-center gap-1.5 rounded-full px-3 py-1 transition-colors duration-200 ${
                sceneMode === 'FOREGROUND_TALKING'
                  ? 'bg-[#D4B982]/20 text-[#F3E5C8] font-medium'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <MessageSquare className="h-3 w-3" />
              <span>Đàm đạo</span>
            </button>
            <button
              type="button"
              onClick={() => onToggleSceneMode('BACKGROUND_ACTION')}
              className={`flex items-center gap-1.5 rounded-full px-3 py-1 transition-colors duration-200 ${
                sceneMode === 'BACKGROUND_ACTION'
                  ? 'bg-amber-500/20 text-amber-200 font-medium'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <Swords className="h-3 w-3" />
              <span>Hành động</span>
            </button>
          </div>

          <button
            type="button"
            onClick={() => setIsStagePeekMode((prev) => !prev)}
            title="Chuyển đổi độ trong suốt để ngắm rõ toàn cảnh sân khấu phía sau"
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs transition-colors duration-200 ${
              isStagePeekMode
                ? 'border-[#D4B982]/50 bg-[#D4B982]/20 text-[#F3E5C8]'
                : 'border-white/[0.09] bg-white/[0.04] text-zinc-300 hover:bg-white/[0.09]'
            }`}
          >
            <Eye className="h-3.5 w-3.5" />
            <span className="hidden md:inline">
              {isStagePeekMode ? 'Hiện rõ khung chat' : 'Ngắm sân khấu'}
            </span>
          </button>

          <button
            type="button"
            onClick={onResetChat}
            title="Đặt lại cuộc trò chuyện"
            className="flex h-8 w-8 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.04] text-zinc-400 transition-colors duration-200 hover:bg-white/[0.09] hover:text-zinc-200"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {/* Vùng tương tác trung tâm chứa Khung Chat Hơi Mờ (GlassChatBox) */}
      <div className="flex flex-1 items-stretch justify-center lg:justify-end overflow-hidden px-3 py-3 sm:px-6 sm:py-4 lg:pr-10">
        <motion.section
          data-testid="glass-chat-box"
          animate={{
            opacity: isStagePeekMode ? 0.22 : 1,
            scale: isStagePeekMode ? 0.985 : 1,
          }}
          transition={{ duration: 0.25, ease: 'easeOut' }}
          className="flex h-full w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-white/[0.10] bg-[#12151C]/52 shadow-[0_24px_80px_rgba(0,0,0,0.55)] backdrop-blur-md"
        >
          {/* Thanh trạng thái phân cảnh gọn nhẹ bên trong khung chat mờ */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.07] bg-white/[0.02] px-4 py-2 text-[11px] text-zinc-400">
            <div className="flex items-center gap-2">
              <MapPin className="h-3.5 w-3.5 text-[#D4B982]" />
              <span>Bối cảnh:</span>
              <span className="font-medium text-zinc-200">
                {sceneData?.contextTitle || hero.homeland}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <Film className="h-3.5 w-3.5 text-zinc-400" />
              <span className="font-mono text-[10px] text-zinc-300">
                {sceneMode}
              </span>
              <span className="text-zinc-600">·</span>
              <span
                className={
                  mouthSyncEnabled ? 'text-emerald-300' : 'text-amber-300'
                }
              >
                {mouthSyncEnabled ? 'Khớp khẩu hình' : 'Cảnh hành động'}
              </span>
            </div>
          </div>

          {/* Luồng tin nhắn & Hồ sơ mở đầu (Theo format trung tâm của character.ai) */}
          <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6 space-y-5">
            {/* Thẻ Giới Thiệu Nhân Vật chính giữa (Character Profile Intro) */}
            <div className="mx-auto flex max-w-xl flex-col items-center text-center pt-1 pb-3">
              <div className="relative mb-3 h-20 w-20 overflow-hidden rounded-full border-2 border-[#D4B982]/45 bg-[#181B22] shadow-xl">
                <img
                  src={hero.heroImageUrl}
                  alt={hero.fullName}
                  className="h-full w-full object-cover object-top"
                />
              </div>

              <h2 className="font-display text-xl font-semibold tracking-wide text-zinc-100">
                {hero.fullName}
              </h2>
              <p className="mt-0.5 text-xs text-[#D4B982]">
                {hero.title} ({hero.era})
              </p>
              <p className="mt-2 max-w-lg text-xs italic leading-relaxed text-zinc-300/90">
                {hero.signatureQuote}
              </p>

              {/* Các gợi ý câu hỏi lịch sử dạng Pill Chips (theo pattern filter chips của character.ai) */}
              <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                {hero.suggestedPrompts.map((prompt) => {
                  const isAction =
                    prompt.expectedSceneMode === 'BACKGROUND_ACTION';
                  return (
                    <button
                      key={prompt.id}
                      type="button"
                      disabled={isStreaming}
                      onClick={() => onSelectPrompt(prompt)}
                      className="group flex items-center gap-1.5 rounded-full border border-white/[0.10] bg-white/[0.04] px-3.5 py-1.5 text-xs text-zinc-200 backdrop-blur-xs transition-all duration-200 hover:border-[#D4B982]/45 hover:bg-white/[0.10] hover:text-white disabled:opacity-50"
                    >
                      {isAction ? (
                        <Swords className="h-3 w-3 text-amber-400 transition-transform duration-200 group-hover:scale-110" />
                      ) : (
                        <Sparkles className="h-3 w-3 text-[#D4B982] transition-transform duration-200 group-hover:scale-110" />
                      )}
                      <span>{prompt.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Danh sách tin nhắn đối thoại */}
            <AnimatePresence initial={false}>
              {messages.map((message) => {
                const isUser = message.role === 'user';
                return (
                  <motion.div
                    key={message.id}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.22, ease: 'easeOut' }}
                    className={`flex items-start gap-3 ${
                      isUser ? 'justify-end' : 'justify-start'
                    }`}
                  >
                    {!isUser && (
                      <div className="h-8 w-8 shrink-0 overflow-hidden rounded-full border border-[#D4B982]/40 bg-[#181B22]">
                        <img
                          src={hero.heroImageUrl}
                          alt={hero.name}
                          className="h-full w-full object-cover object-top"
                        />
                      </div>
                    )}

                    <div
                      className={`flex max-w-[85%] flex-col ${
                        isUser ? 'items-end' : 'items-start'
                      }`}
                    >
                      <div className="mb-1 flex items-center gap-2 px-1">
                        <span className="text-xs font-medium text-zinc-300">
                          {isUser ? 'Người học sử' : hero.name}
                        </span>
                        {!isUser && (
                          <span className="rounded-full bg-white/[0.08] px-2 py-0.5 text-[10px] font-medium text-zinc-300">
                            Đại Việt Sử Ký
                          </span>
                        )}
                        {!isUser && message.sceneMode && (
                          <span className="rounded-full border border-white/[0.08] bg-black/30 px-2 py-0.5 font-mono text-[9px] text-zinc-400">
                            {message.sceneMode === 'FOREGROUND_TALKING'
                              ? 'Tiền cảnh'
                              : 'Tái hiện'}
                          </span>
                        )}
                      </div>

                      <div
                        className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                          isUser
                            ? 'rounded-br-xs border border-[#D4B982]/30 bg-[#D4B982]/18 text-zinc-100 backdrop-blur-md'
                            : 'rounded-bl-xs border border-white/[0.09] bg-[#161922]/65 text-zinc-100 backdrop-blur-md'
                        }`}
                      >
                        <p className="whitespace-pre-wrap">{message.content}</p>
                        {message.isStreaming && (
                          <span className="ml-1.5 inline-block h-3.5 w-1.5 translate-y-0.5 animate-pulse rounded-full bg-[#D4B982]" />
                        )}
                      </div>
                    </div>
                  </motion.div>
                );
              })}
            </AnimatePresence>

            <div ref={messagesEndRef} />
          </div>

          {/* Khung Nhập Liệu Dạng Viên Thuốc (Chat Input Capsule lấy từ format character.ai) */}
          <div className="border-t border-white/[0.07] bg-[#0E1015]/55 px-4 pt-3 pb-2.5 backdrop-blur-xl sm:px-6">
            <form
              onSubmit={handleSubmit}
              className="mx-auto flex w-full max-w-2xl items-center gap-2 rounded-full border border-white/[0.12] bg-[#171A22]/80 py-1.5 pr-2 pl-5 shadow-inner transition-colors duration-200 focus-within:border-[#D4B982]/50"
            >
              <input
                ref={inputRef}
                type="text"
                data-testid="chat-input"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                disabled={isStreaming}
                placeholder={`Đặt câu hỏi lịch sử cho ${hero.name}...`}
                className="flex-1 bg-transparent text-sm text-zinc-100 placeholder-zinc-400 outline-none disabled:opacity-60"
              />

              <button
                type="submit"
                data-testid="chat-send-button"
                disabled={!inputText.trim() || isStreaming}
                aria-label="Gửi câu hỏi"
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-all duration-200 ${
                  inputText.trim() && !isStreaming
                    ? 'bg-[#D4B982] text-[#0B0C0E] hover:bg-[#E2C994] scale-100'
                    : 'bg-white/[0.08] text-zinc-500 scale-95 cursor-not-allowed'
                }`}
              >
                <ArrowUp className="h-4 w-4 stroke-[2.5]" />
              </button>
            </form>

            {/* Dòng chú thích nhỏ phía dưới thanh chat theo bố cục character.ai */}
            <p className="mt-2 text-center text-[11px] text-zinc-400/80">
              Nhân vật AI tái hiện lịch sử dựa trên chính sử Đại Việt · Sân khấu đồng bộ trạng thái thời gian thực
            </p>
          </div>
        </motion.section>
      </div>
    </div>
  );
};
