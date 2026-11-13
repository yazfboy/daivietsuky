export type HeroId = 'tran_hung_dao' | 'nguyen_trai' | 'quang_trung';

export type SceneMode = 'FOREGROUND_TALKING' | 'BACKGROUND_ACTION';

export type BackgroundType = 'HOMELAND' | 'BATTLEFIELD' | 'HISTORICAL_EVENT';

export interface HeroJoints {
  head_top: [number, number];
  chin: [number, number];
  neck: [number, number];
  shoulder_l: [number, number];
  shoulder_r: [number, number];
  elbow_l: [number, number];
  elbow_r: [number, number];
  wrist_l: [number, number];
  wrist_r: [number, number];
  pelvis: [number, number];
  upper_lip: [number, number];
  lower_lip: [number, number];
  mouth_l: [number, number];
  mouth_r: [number, number];
}

export interface HeroRigData {
  character_id: HeroId;
  width: number;
  height: number;
  joints: HeroJoints;
  face_box: [number, number, number, number];
  mouth_box: [number, number, number, number];
  source: string;
}

export interface HeroMetaData {
  character_id: HeroId;
  source: string;
  iconography: string;
  size: [number, number];
  created_at: string;
}

export interface VisemeSegmentData {
  viseme: string;
  start_ms: number;
  end_ms: number;
}

export interface SpeechChunkData {
  seq: number;
  text: string;
  char_start: number;
  char_end: number;
  syllables: number;
  speak_ms: number;
  pause_ms: number;
  boundary: string;
  is_final: boolean;
  character_id: HeroId;
  /** Khẩu hình Viterbi DP do backend (TextToVisemeDPSync) tính cho riêng cụm chữ này. */
  visemes?: VisemeSegmentData[];
  mouthSyncEnabled?: boolean;
  source?: string;
}

export interface SceneBeatData {
  beat_index: number;
  char_start: number;
  char_end: number;
  text: string;
  start_ms: number;
  duration_ms: number;
  dominant_visual: string | null;
  scene_mode: SceneMode;
  mouth_sync_enabled: boolean;
  character_reference_path: string;
  character_action: string;
  scene_prompt: string;
}

export interface SceneData {
  stateId: string;
  characterId: HeroId;
  sceneMode: SceneMode;
  mouthSyncEnabled: boolean;
  characterReferencePath: string;
  backgroundType: BackgroundType;
  contextKey: string;
  contextTitle: string;
  contextYear?: string;
  backgroundPrompt: string;
  characterAction: string;
  scenePrompt: string;
  reason: string;
  animationWebpUrl?: string;
  backgroundPngUrl?: string;
  rigData?: HeroRigData | null;
  metaData?: HeroMetaData | null;
  beats?: SceneBeatData[];
  activeChunk?: SpeechChunkData | null;
  /** Nguồn quyết định cảnh của AI Đạo diễn: llm | cache | rule_preview | rule_fallback | local */
  decidedBy?: string;
}

export interface DynamicHeroStageProps {
  characterId: HeroId;
  heroImageUrl: string;
  sceneMode: 'FOREGROUND_TALKING' | 'BACKGROUND_ACTION';
  mouthSyncEnabled: boolean;
  isStreaming: boolean;
  sceneData: SceneData | null;
}

export interface SuggestedPrompt {
  id: string;
  label: string;
  question: string;
  expectedSceneMode: SceneMode;
  contextKey: string;
  contextTitle: string;
  contextYear: string;
  backgroundType: BackgroundType;
  backgroundPrompt: string;
  characterAction: string;
  scenePrompt: string;
  reason: string;
  sampleAnswer: string;
}

export interface HeroCharacter {
  id: HeroId;
  name: string;
  fullName: string;
  title: string;
  era: string;
  years: string;
  homeland: string;
  homelandVisual: string;
  selfPronoun: string;
  listenerAddress: string;
  heroImageUrl: string;
  rigUrl: string;
  metaUrl: string;
  accentColor: string;
  ambientGlow: string;
  signatureQuote: string;
  shortBio: string;
  openingGreeting: string;
  suggestedPrompts: SuggestedPrompt[];
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  characterId: HeroId;
  content: string;
  timestamp: string;
  sceneMode?: SceneMode;
  contextTitle?: string;
  isStreaming?: boolean;
}

export interface PreloadAssetStatus {
  characterId: HeroId;
  heroImageUrl: string;
  loaded: boolean;
  decoded: boolean;
  width: number;
  height: number;
  elapsedMs: number;
  rigData: HeroRigData | null;
  metaData: HeroMetaData | null;
}

export interface PreloadProgress {
  completed: number;
  total: number;
  percentage: number;
  allReady: boolean;
  items: Record<HeroId, PreloadAssetStatus>;
}
