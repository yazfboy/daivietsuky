import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DynamicHeroStage } from './components/DynamicHeroStage';
import { GlassChatBox } from './components/GlassChatBox';
import { HeroSidebar } from './components/HeroSidebar';
import { IntroMotion } from './components/IntroMotion';
import {
  HEROES,
  buildDefaultSceneData,
  inferSceneAndReply,
} from './data/heroes';
import {
  ChatMessage,
  HeroId,
  PreloadProgress,
  SceneData,
  SceneMode,
  SpeechChunkData,
  SuggestedPrompt,
} from './types/hero';
import { heroAssetCache } from './utils/assetPreloader';
import { BackendEvent, ChatTurnHandle, openChatTurn, resolveMediaUrl } from './utils/backendClient';

function createInitialGreetingMessage(characterId: HeroId): ChatMessage {
  const hero = HEROES[characterId];
  return {
    id: `greeting_${characterId}`,
    role: 'assistant',
    characterId,
    content: hero.openingGreeting,
    timestamp: new Date().toLocaleTimeString('vi-VN', {
      hour: '2-digit',
      minute: '2-digit',
    }),
    sceneMode: 'FOREGROUND_TALKING',
    contextTitle: hero.homeland,
    isStreaming: false,
  };
}

/**
 * Chia văn bản trả lời thành từng cụm từ tự nhiên (tương ứng PhraseChunker của Backend)
 * để tạo nhịp streaming mượt mà và đồng bộ với trạng thái isStreaming trên sân khấu.
 */
function chunkIntoPhrases(text: string, maxWords = 5): string[] {
  const tokens = text.split(/(\s+)/);
  const chunks: string[] = [];
  let buffer = '';
  let wordCount = 0;

  for (const token of tokens) {
    buffer += token;
    if (token.trim().length > 0) {
      wordCount += 1;
      const endsWithPunct = /[,.;:!?…]$/.test(token.trim());
      if (endsWithPunct || wordCount >= maxWords) {
        chunks.push(buffer);
        buffer = '';
        wordCount = 0;
      }
    }
  }
  if (buffer.length > 0) {
    chunks.push(buffer);
  }
  return chunks;
}

export const App: React.FC = () => {
  const [introCompleted, setIntroCompleted] = useState(false);
  const [preloadProgress, setPreloadProgress] = useState<PreloadProgress | null>(null);
  const [selectedHeroId, setSelectedHeroId] = useState<HeroId>('tran_hung_dao');
  const [isSidebarOpen, setIsSidebarOpen] = useState<boolean>(true);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);

  const [messagesByHero, setMessagesByHero] = useState<Record<HeroId, ChatMessage[]>>(() => ({
    tran_hung_dao: [createInitialGreetingMessage('tran_hung_dao')],
    nguyen_trai: [createInitialGreetingMessage('nguyen_trai')],
    quang_trung: [createInitialGreetingMessage('quang_trung')],
  }));

  const [sceneByHero, setSceneByHero] = useState<Record<HeroId, SceneData>>(() => ({
    tran_hung_dao: buildDefaultSceneData(HEROES.tran_hung_dao),
    nguyen_trai: buildDefaultSceneData(HEROES.nguyen_trai),
    quang_trung: buildDefaultSceneData(HEROES.quang_trung),
  }));

  const streamTimerRef = useRef<number | null>(null);
  // Phần chữ đã stream của câu trả lời hiện tại → DynamicHeroStage khớp khẩu hình theo đúng luồng chữ.
  const [streamChunk, setStreamChunk] = useState<SpeechChunkData | null>(null);

  const turnRef = useRef<ChatTurnHandle | null>(null);

  const stopActiveStream = useCallback(() => {
    if (streamTimerRef.current !== null) {
      window.clearInterval(streamTimerRef.current);
      streamTimerRef.current = null;
    }
    if (turnRef.current) {
      turnRef.current.close();
      turnRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  useEffect(() => {
    return () => {
      if (streamTimerRef.current !== null) {
        window.clearInterval(streamTimerRef.current);
      }
      turnRef.current?.close();
    };
  }, []);

  const currentHero = HEROES[selectedHeroId];
  const currentMessages = messagesByHero[selectedHeroId];

  // Ghép dữ liệu rigData & metaData đã Pre-load vào sceneData cho DynamicHeroStage
  const currentSceneData: SceneData = useMemo(() => {
    const baseScene = sceneByHero[selectedHeroId];
    const cachedAsset = heroAssetCache.getStatus(selectedHeroId);
    return {
      ...baseScene,
      rigData: cachedAsset?.rigData ?? baseScene.rigData ?? null,
      metaData: cachedAsset?.metaData ?? baseScene.metaData ?? null,
      activeChunk: streamChunk && streamChunk.character_id === selectedHeroId ? streamChunk : null,
    };
  }, [sceneByHero, selectedHeroId, preloadProgress, streamChunk]);

  const currentSceneMode: SceneMode = currentSceneData.sceneMode;
  const currentMouthSyncEnabled: boolean = currentSceneData.mouthSyncEnabled;

  const handleIntroComplete = useCallback((finalProgress: PreloadProgress) => {
    setPreloadProgress(finalProgress);
    setIntroCompleted(true);
  }, []);

  const handleSelectHero = useCallback(
    (heroId: HeroId) => {
      if (heroId === selectedHeroId) return;
      stopActiveStream();
      setSelectedHeroId(heroId);
    },
    [selectedHeroId, stopActiveStream]
  );

  const handleToggleSceneMode = useCallback(
    (mode: SceneMode) => {
      setSceneByHero((prev) => {
        const current = prev[selectedHeroId];
        const hero = HEROES[selectedHeroId];
        const matchingPrompt =
          hero.suggestedPrompts.find((p) => p.expectedSceneMode === mode) ||
          hero.suggestedPrompts[0];

        return {
          ...prev,
          [selectedHeroId]: {
            ...current,
            sceneMode: mode,
            mouthSyncEnabled: mode === 'FOREGROUND_TALKING',
            backgroundType:
              mode === 'FOREGROUND_TALKING'
                ? 'HOMELAND'
                : matchingPrompt.backgroundType,
            contextKey:
              mode === 'FOREGROUND_TALKING'
                ? 'homeland'
                : matchingPrompt.contextKey,
            contextTitle:
              mode === 'FOREGROUND_TALKING'
                ? hero.homeland
                : matchingPrompt.contextTitle,
            contextYear:
              mode === 'FOREGROUND_TALKING'
                ? hero.era
                : matchingPrompt.contextYear,
          },
        };
      });
    },
    [selectedHeroId]
  );

  /** Phát chữ cục bộ theo nhịp cụm từ (dự phòng khi không kết nối được backend). */
  const streamLocalReply = useCallback(
    (heroId: HeroId, assistantMessageId: string, nextSceneData: SceneData, fullReplyText: string) => {
      const phrases = chunkIntoPhrases(fullReplyText, 5);
      setSceneByHero((prev) => ({ ...prev, [heroId]: { ...nextSceneData, decidedBy: 'local' } }));
      const makeChunk = (seq: number, text: string, isFinal: boolean): SpeechChunkData => ({
        seq,
        text,
        char_start: 0,
        char_end: text.length,
        syllables: (text.match(/\p{N}+(?:[.,]\p{N}+)*|\p{L}+/gu) ?? []).length,
        speak_ms: 0,
        pause_ms: 0,
        boundary: /[.!?…]\s*$/.test(text) ? 'sentence' : /[,;:]\s*$/.test(text) ? 'clause' : 'phrase',
        is_final: isFinal,
        character_id: heroId,
      });
      const updateMessage = (content: string, streaming: boolean) =>
        setMessagesByHero((prev) => ({
          ...prev,
          [heroId]: prev[heroId].map((msg) =>
            msg.id === assistantMessageId
              ? {
                  ...msg,
                  content,
                  isStreaming: streaming,
                  sceneMode: nextSceneData.sceneMode,
                  contextTitle: nextSceneData.contextTitle,
                }
              : msg
          ),
        }));

      updateMessage(phrases[0] ?? fullReplyText, phrases.length > 1);
      if (phrases.length <= 1) {
        setStreamChunk(null);
        setIsStreaming(false);
        return;
      }
      setStreamChunk(makeChunk(1, phrases[0], false));
      setIsStreaming(true);
      let phraseIndex = 1;
      streamTimerRef.current = window.setInterval(() => {
        phraseIndex += 1;
        const partialText = phrases.slice(0, phraseIndex).join('');
        const done = phraseIndex >= phrases.length;
        setStreamChunk(makeChunk(phraseIndex, partialText, done));
        updateMessage(partialText, !done);
        if (done) {
          if (streamTimerRef.current !== null) {
            window.clearInterval(streamTimerRef.current);
            streamTimerRef.current = null;
          }
          setIsStreaming(false);
        }
      }, 65);
    },
    []
  );

  /**
   * Một lượt hỏi–đáp thật qua backend (WebSocket): AI Đạo diễn quyết định cảnh, AI Nhân vật stream lời thoại
   * kèm khẩu hình, visual_pipeline cập nhật cảnh động. Không kết nối được backend → dùng câu trả lời cục bộ.
   */
  const executeConversationTurn = useCallback(
    (questionText: string, localScene: SceneData, localReplyText: string) => {
      stopActiveStream();
      const heroId = selectedHeroId;
      const hero = HEROES[heroId];
      const nowTime = new Date().toLocaleTimeString('vi-VN', {
        hour: '2-digit',
        minute: '2-digit',
      });
      const userMessage: ChatMessage = {
        id: `user_${Date.now()}`,
        role: 'user',
        characterId: heroId,
        content: questionText,
        timestamp: nowTime,
      };
      const assistantMessageId = `assistant_${Date.now() + 1}`;
      const assistantMessage: ChatMessage = {
        id: assistantMessageId,
        role: 'assistant',
        characterId: heroId,
        content: '',
        timestamp: nowTime,
        sceneMode: localScene.sceneMode,
        contextTitle: localScene.contextTitle,
        isStreaming: true,
      };
      setMessagesByHero((prev) => ({
        ...prev,
        [heroId]: [...prev[heroId], userMessage, assistantMessage],
      }));
      setStreamChunk(null);
      setIsStreaming(true);

      let spoken = '';
      let finalReceived = false;
      let gotSpeech = false;
      const patchMessage = (patch: Partial<ChatMessage>) =>
        setMessagesByHero((prev) => ({
          ...prev,
          [heroId]: prev[heroId].map((msg) => (msg.id === assistantMessageId ? { ...msg, ...patch } : msg)),
        }));
      const finishSpeech = () => {
        finalReceived = true;
        setIsStreaming(false);
        patchMessage({ isStreaming: false });
      };

      const handle = openChatTurn(
        heroId,
        questionText,
        (event: BackendEvent) => {
          switch (event.type) {
            case 'scene': {
              const data = event.sceneData;
              const isHomeland = data.contextKey === 'homeland';
              setSceneByHero((prev) => {
                const before = prev[heroId];
                const sameState = before.stateId === data.stateId;
                return {
                  ...prev,
                  [heroId]: {
                    ...before,
                    stateId: data.stateId,
                    characterId: heroId,
                    sceneMode: data.sceneMode,
                    mouthSyncEnabled: data.mouthSyncEnabled,
                    characterReferencePath: data.characterReferencePath || hero.heroImageUrl,
                    backgroundType: data.backgroundType,
                    contextKey: data.contextKey,
                    contextTitle: isHomeland ? hero.homeland : data.contextTitle,
                    contextYear: isHomeland ? hero.era : data.contextYear,
                    backgroundPrompt: data.backgroundPrompt,
                    characterAction: data.characterAction,
                    scenePrompt: data.scenePrompt,
                    reason: data.reason,
                    decidedBy: data.decidedBy,
                    rigData: data.rigData ?? before.rigData,
                    metaData: data.metaData ?? before.metaData,
                    beats: sameState ? before.beats : undefined,
                    animationWebpUrl: sameState ? before.animationWebpUrl : undefined,
                    backgroundPngUrl: sameState ? before.backgroundPngUrl : undefined,
                  },
                };
              });
              patchMessage({ sceneMode: data.sceneMode, contextTitle: isHomeland ? hero.homeland : data.contextTitle });
              break;
            }
            case 'speech': {
              gotSpeech = true;
              spoken += event.chunk.text;
              setStreamChunk({ ...event.chunk, text: spoken, char_start: 0, char_end: spoken.length });
              patchMessage({ content: spoken });
              // Cụm cuối → isStreaming = false: miệng nhân vật dừng ngay khi trả lời xong.
              if (event.chunk.is_final) finishSpeech();
              break;
            }
            case 'storyboard': {
              const board = event.storyboard;
              setSceneByHero((prev) =>
                prev[heroId].stateId === board.stateId
                  ? { ...prev, [heroId]: { ...prev[heroId], beats: board.beats } }
                  : prev
              );
              break;
            }
            case 'visual': {
              const visual = event.sceneData;
              if (event.status === 'ready' && visual) {
                setSceneByHero((prev) =>
                  prev[heroId].stateId === visual.stateId
                    ? {
                        ...prev,
                        [heroId]: {
                          ...prev[heroId],
                          animationWebpUrl: resolveMediaUrl(visual.animationWebpUrl),
                          backgroundPngUrl: resolveMediaUrl(visual.backgroundPngUrl),
                        },
                      }
                    : prev
                );
              }
              break;
            }
            case 'done':
              if (!spoken.trim() && event.answer) {
                spoken = event.answer;
                patchMessage({ content: spoken });
              }
              if (!finalReceived) finishSpeech();
              break;
            default:
              break;
          }
        },
        (receivedAny: boolean) => {
          if (turnRef.current === handle) turnRef.current = null;
          if (!receivedAny || !gotSpeech) {
            // Backend không phản hồi: phát câu trả lời cục bộ vào đúng tin nhắn đang chờ.
            streamLocalReply(heroId, assistantMessageId, localScene, localReplyText);
            return;
          }
          if (!finalReceived) finishSpeech();
        }
      );
      turnRef.current = handle;
    },
    [selectedHeroId, stopActiveStream, streamLocalReply]
  );

  const handleSendMessage = useCallback(
    (question: string) => {
      const hero = HEROES[selectedHeroId];
      const { sceneData, replyText } = inferSceneAndReply(hero, question);
      executeConversationTurn(question, sceneData, replyText);
    },
    [selectedHeroId, executeConversationTurn]
  );

  const handleSelectPrompt = useCallback(
    (prompt: SuggestedPrompt) => {
      const hero = HEROES[selectedHeroId];
      const isTalking = prompt.expectedSceneMode === 'FOREGROUND_TALKING';
      const nextSceneData: SceneData = {
        stateId: `state_${hero.id}_${prompt.contextKey}_${Date.now()}`,
        characterId: hero.id,
        sceneMode: prompt.expectedSceneMode,
        mouthSyncEnabled: isTalking,
        characterReferencePath: hero.heroImageUrl,
        backgroundType: prompt.backgroundType,
        contextKey: prompt.contextKey,
        contextTitle: prompt.contextTitle,
        contextYear: prompt.contextYear,
        backgroundPrompt: prompt.backgroundPrompt,
        characterAction: prompt.characterAction,
        scenePrompt: prompt.scenePrompt,
        reason: prompt.reason,
      };
      executeConversationTurn(prompt.question, nextSceneData, prompt.sampleAnswer);
    },
    [selectedHeroId, executeConversationTurn]
  );

  const handleResetChat = useCallback(() => {
    stopActiveStream();
    const hero = HEROES[selectedHeroId];
    setMessagesByHero((prev) => ({
      ...prev,
      [selectedHeroId]: [createInitialGreetingMessage(selectedHeroId)],
    }));
    setSceneByHero((prev) => ({
      ...prev,
      [selectedHeroId]: buildDefaultSceneData(hero),
    }));
  }, [selectedHeroId, stopActiveStream]);

  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-[#0B0C0E] text-zinc-100">
      {/* 1. IntroMotion khi vừa mở web lên: hiện tên sản phẩm ĐẠI VIỆT SỬ KÝ ở chính giữa xong rời đi + Pre-load đa luồng */}
      {!introCompleted && (
        <IntroMotion
          onComplete={handleIntroComplete}
          onProgressUpdate={setPreloadProgress}
        />
      )}

      {/* 2. Khung chờ Sân khấu toàn màn hình nằm phía sau (DynamicHeroStage) */}
      <DynamicHeroStage
        characterId={selectedHeroId}
        heroImageUrl={currentHero.heroImageUrl}
        sceneMode={currentSceneMode}
        mouthSyncEnabled={currentMouthSyncEnabled}
        isStreaming={isStreaming}
        sceneData={currentSceneData}
      />

      {/* 3. Bố cục chính phía trước: Sidebar 3 nhân vật lịch sử & Khung chat hơi mờ (GlassChatBox) */}
      <div className="relative z-10 flex h-full w-full overflow-hidden">
        <HeroSidebar
          selectedHeroId={selectedHeroId}
          onSelectHero={handleSelectHero}
          onResetChat={handleResetChat}
          isOpen={isSidebarOpen}
          onToggleSidebar={() => setIsSidebarOpen((prev) => !prev)}
          preloadProgress={preloadProgress}
          sceneMode={currentSceneMode}
          mouthSyncEnabled={currentMouthSyncEnabled}
        />

        <GlassChatBox
          hero={currentHero}
          messages={currentMessages}
          isStreaming={isStreaming}
          sceneMode={currentSceneMode}
          mouthSyncEnabled={currentMouthSyncEnabled}
          sceneData={currentSceneData}
          isSidebarOpen={isSidebarOpen}
          onToggleSidebar={() => setIsSidebarOpen((prev) => !prev)}
          onSendMessage={handleSendMessage}
          onSelectPrompt={handleSelectPrompt}
          onToggleSceneMode={handleToggleSceneMode}
          onResetChat={handleResetChat}
        />
      </div>
    </div>
  );
};

export default App;
