import type { SceneData, SceneMode } from '../types/hero';

/**
 * Luật chế độ cảnh phía frontend — KHÔNG chứa bảng HERO_EVENTS (bảng chỉ nằm ở backend).
 * Frontend chỉ đọc cờ `actionAllowed` do backend trả kèm mỗi cảnh: true khi `context_topic_key` hiện tại nằm trong
 * HERO_EVENTS[hero_id] (trận vị anh hùng trực tiếp chỉ huy).
 */

/** Người dùng bấm "Đàm đạo" / "Hành động": chỉ vào BACKGROUND_ACTION khi backend cho phép. */
export function applyManualSceneMode(scene: SceneData, mode: SceneMode): SceneData {
  if (mode === 'BACKGROUND_ACTION' && scene.actionAllowed !== true) return scene;
  if (scene.sceneMode === mode) return scene;
  return { ...scene, sceneMode: mode, mouthSyncEnabled: mode === 'FOREGROUND_TALKING' };
}

/** Nút "Hành động" có bấm được không (đang ở chế độ hành động thì vẫn hiện trạng thái bật). */
export function canEnterAction(scene: SceneData | null | undefined): boolean {
  return scene?.actionAllowed === true;
}

/**
 * Cảnh do frontend tự dựng khi không có backend (gợi ý câu hỏi, engine trong trình duyệt): frontend không biết
 * HERO_EVENTS nên luôn để nhân vật đứng tiền cảnh trò chuyện, cảnh phía sau chỉ là minh họa.
 */
export function toLocalSafeScene(scene: SceneData): SceneData {
  return {
    ...scene,
    sceneMode: 'FOREGROUND_TALKING',
    mouthSyncEnabled: true,
    actionAllowed: false,
    characterAction:
      'the hero from the reference image stands in the foreground, waist-up, facing the viewer and speaking with calm dignity, subtle hand gestures, mouth clearly visible and unobstructed for lip-sync',
  };
}
