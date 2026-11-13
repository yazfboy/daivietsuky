"""Đại Việt Sử Ký — Phase 1 — AI Đạo diễn (Director Agent).

Hai vai trò đạo diễn, đều chạy trên key khác với AI Nhân vật (Least-Connections của key pool tự
tách key khi các request chạy song song; storyboard còn chủ động tránh key mà AI Nhân vật vừa dùng):

1. **Đạo diễn phân cảnh** — ``direct()``: chạy SONG SONG với AI Nhân vật ngay khi có câu hỏi.
   Quyết định ``scene_mode`` (FOREGROUND_TALKING / BACKGROUND_ACTION), ``mouth_sync_enabled``,
   giữ cố định ``character_reference_path = /assets/heroes/{character_id}.png``, viết
   ``background_prompt`` / ``character_action`` / ``scene_prompt`` và tự dựng system prompt +
   negative prompt cho mô hình tạo ảnh. Trước khi gọi AI, tra ``VisualStateCache`` (Thuật toán 2):
   câu hỏi cùng bối cảnh được tái sử dụng trạng thái cảnh trong O(1), không tốn request.
2. **Đạo diễn storyboard** — ``storyboard()``: khi đã có câu trả lời, chia lời thoại thành các phân
   cảnh tối ưu bằng ``dp_scene_segmentation`` (Thuật toán 1) rồi viết prompt hành động cho từng
   phân cảnh, khớp đúng nội dung câu trả lời.

``run_scene()`` điều phối cả hệ multi-agent cho một lượt hỏi–đáp và phát luồng sự kiện
(``scene`` / ``speech`` / ``storyboard`` / ``done``) cho frontend.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..core.dp_optimizer import (
    HOMELAND_CONTEXT_KEY,
    INTENT_ACTION,
    SCENE_BACKGROUND_ACTION,
    SCENE_FOREGROUND_TALKING,
    SCENE_MODES,
    ContextAnalyzer,
    ContextEntity,
    ContextFingerprint,
    SegmentationConfig,
    SegmentationResult,
    VisualStateCache,
    dp_scene_segmentation,
    normalize_text,
    strip_diacritics,
)
from ..core.key_pool import GeminiKeyPool, KeyPoolError
from .actor_agent import CHARACTERS, ActorAgent, CharacterProfile, get_character

__all__ = [
    "BACKGROUND_BATTLEFIELD",
    "BACKGROUND_HISTORICAL_EVENT",
    "BACKGROUND_HOMELAND",
    "BACKGROUND_TYPES",
    "OTHER_EVENT_KEY",
    "DirectorAgent",
    "DirectorOutputError",
    "SceneBeat",
    "SceneDirective",
    "Storyboard",
    "TurnEvent",
    "TurnSummary",
    "character_reference_path",
]

BACKGROUND_HOMELAND = "HOMELAND"
BACKGROUND_BATTLEFIELD = "BATTLEFIELD"
BACKGROUND_HISTORICAL_EVENT = "HISTORICAL_EVENT"
BACKGROUND_TYPES = (BACKGROUND_HOMELAND, BACKGROUND_BATTLEFIELD, BACKGROUND_HISTORICAL_EVENT)
OTHER_EVENT_KEY = "other_event"

_FOREGROUND_ACTION = (
    "the hero from the reference image stands in the foreground, waist-up, facing the viewer and speaking with "
    "calm dignity, subtle hand gestures, mouth clearly visible and unobstructed for lip-sync"
)
_TALKING_MARKERS = ("speak", "talk", "facing the viewer", "facing the camera", "addressing the viewer", "foreground")
_CONSISTENCY_CLAUSE = (
    "Keep the hero exactly identical to the reference image {path} (same face, age, hairstyle, costume, colors and "
    "proportions); only the hero's pose, action, expression and the background change."
)

_SCENE_SYSTEM_PROMPT = """Bạn là AI ĐẠO DIỄN HÌNH ẢNH của ứng dụng giáo dục lịch sử "Đại Việt Sử Ký". Ứng dụng có đúng 3 nhân vật: Trần Hưng Đạo, Nguyễn Trãi, Quang Trung – Nguyễn Huệ. Người dùng đặt câu hỏi cho nhân vật; một AI Nhân vật khác trả lời bằng lời thoại. Bạn KHÔNG trả lời câu hỏi. Nhiệm vụ của bạn là quyết định trạng thái khung hình và viết prompt hình ảnh động cho cảnh.

QUY TẮC CHỌN scene_mode (chỉ được chọn 1 trong 2 giá trị)
1. "FOREGROUND_TALKING": câu hỏi xin THÔNG TIN hoặc NHẬN ĐỊNH, không cần tái hiện hành động — năm sinh, năm mất, tuổi, quê quán, gia đình, tên hiệu, chức tước, tính cách, tư tưởng, tác phẩm (nội dung, ý nghĩa), bài học, lời khuyên, cảm nghĩ.
   → Nhân vật đứng ở tiền cảnh, nhìn người xem và mấp máy môi nói chuyện; hậu cảnh là ẢNH ĐỘNG QUÊ HƯƠNG của nhân vật.
   → background_type = "HOMELAND", context_key = "homeland".
2. "BACKGROUND_ACTION": câu hỏi về TRẬN ĐÁNH, chiến dịch, cuộc hành quân, hoặc một HÀNH ĐỘNG / SỰ KIỆN cụ thể cần tái hiện (diễn biến trận đánh, cách dùng kế, tiến quân, lên ngôi, viết hịch...).
   → Hậu cảnh chính là trận đánh / sự kiện đó; nhân vật xuất hiện TRONG cảnh và đang hành động (chỉ huy, giao chiến, cưỡi voi, vung gươm, viết hịch...), KHÔNG đứng nói chuyện trước màn hình và không cần cử động môi.
   → background_type = "BATTLEFIELD" nếu là trận đánh / chiến dịch, "HISTORICAL_EVENT" nếu là sự kiện không phải trận đánh; context_key = mã sự kiện khớp nhất trong danh sách, nếu không có thì "other_event".
Câu hỏi vừa hỏi tiểu sử vừa hỏi trận đánh thì chọn "BACKGROUND_ACTION".

VÍ DỤ
- "Quê ông ở đâu?" → FOREGROUND_TALKING, HOMELAND, homeland
- "Ông có lời khuyên gì cho học sinh?" → FOREGROUND_TALKING, HOMELAND, homeland
- "Ông đánh tan giặc trên sông bằng cách nào?" → BACKGROUND_ACTION, BATTLEFIELD, <mã trận tương ứng>
- "Ông lên ngôi hoàng đế ra sao?" → BACKGROUND_ACTION, HISTORICAL_EVENT, <mã sự kiện tương ứng>

QUY TẮC VIẾT PROMPT (tiếng Anh, giữ nguyên tên riêng tiếng Việt có dấu)
- Nhân vật luôn được gọi là "the hero from the reference image": tuyệt đối không mô tả lại hay thay đổi khuôn mặt, kiểu tóc, trang phục; chỉ mô tả tư thế, hành động, biểu cảm.
- background_prompt: hậu cảnh CÓ CHUYỂN ĐỘNG (nước chảy, cờ bay, khói, mây trôi, lúa đung đưa...), đúng địa danh, thời đại, kiến trúc, trang phục, vũ khí, thuyền bè; không có chi tiết lỗi thời; 40–80 từ.
- character_action: hành động / tư thế của nhân vật (FOREGROUND_TALKING: đứng tiền cảnh, nhìn thẳng người xem, đang nói, cử chỉ tay nhẹ, miệng không bị che; BACKGROUND_ACTION: hành động cụ thể trong trận / sự kiện); 1–2 câu.
- scene_prompt: prompt hoàn chỉnh 70–130 từ cho mô hình tạo ảnh / video: bố cục, hành động nhân vật, hậu cảnh động, ánh sáng, góc máy, chuyển động máy quay, phong cách tranh điện ảnh cổ trang; phải nhắc "the hero from the reference image".
- reason: một câu tiếng Việt giải thích vì sao chọn scene_mode."""

_STORYBOARD_SYSTEM_PROMPT = """Bạn là AI ĐẠO DIỄN PHÂN CẢNH của ứng dụng giáo dục lịch sử "Đại Việt Sử Ký". Lời thoại của nhân vật lịch sử đã được thuật toán quy hoạch động chia thành các phân cảnh liên tiếp. Với MỖI phân cảnh, viết:
- character_action: hành động / tư thế của nhân vật trong phân cảnh (tiếng Anh, 1–2 câu).
- scene_prompt: prompt 50–100 từ (tiếng Anh) cho mô hình tạo ảnh / video, minh họa ĐÚNG nội dung lời thoại của phân cảnh đó và liền mạch với phân cảnh trước.

QUY TẮC
- FOREGROUND_TALKING: nhân vật luôn đứng tiền cảnh, nhìn người xem và đang nói (miệng không bị che); hậu cảnh luôn là quê hương đã chốt; chỉ thay đổi chi tiết chuyển động, ánh sáng, góc máy và cử chỉ tay cho hợp lời thoại.
- BACKGROUND_ACTION: nhân vật ở trong cảnh và đang hành động, không nhìn vào máy quay để nói; mỗi phân cảnh là một nhịp diễn biến khác nhau của trận đánh / sự kiện, đúng trình tự trong lời thoại.
- Nhân vật luôn là "the hero from the reference image": không mô tả lại hay thay đổi khuôn mặt, trang phục.
- Đúng thời đại, địa danh, vũ khí, trang phục; không có chi tiết lỗi thời; không chữ, không watermark.
- Trả về đúng một beat cho mỗi segment_index được liệt kê."""


class DirectorOutputError(ValueError):
    """Đầu ra của AI Đạo diễn không hợp lệ."""


def character_reference_path(character_id: str) -> str:
    """Đường dẫn ảnh mẫu cố định của nhân vật (giữ nguyên model anh hùng ở mọi cảnh)."""
    return f"/assets/heroes/{get_character(character_id).character_id}.png"


# --------------------------------------------------------------------------------------
# Kết quả
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SceneDirective:
    state_id: str
    character_id: str
    question: str
    scene_mode: str
    mouth_sync_enabled: bool
    character_reference_path: str
    background_type: str
    context_key: str
    background_prompt: str
    character_action: str
    scene_prompt: str
    negative_prompt: str
    image_system_prompt: str
    reason: str
    decided_by: str  # "llm" | "cache" | "rule_fallback"
    cache_hit: bool
    cache_match: str | None
    cache_similarity: float | None
    key_label: str | None
    model: str | None
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class SceneBeat:
    beat_index: int
    char_start: int
    char_end: int
    text: str
    start_ms: int
    duration_ms: int
    dominant_visual: str | None
    scene_mode: str
    mouth_sync_enabled: bool
    character_reference_path: str
    character_action: str
    scene_prompt: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Storyboard:
    state_id: str
    character_id: str
    scene_mode: str
    beats: tuple[SceneBeat, ...]
    segmentation_cost: float
    decided_by: str  # "llm" | "rule_fallback" | "empty"
    key_label: str | None
    model: str | None
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class TurnEvent:
    kind: str  # "scene" | "speech" | "storyboard" | "error" | "done"
    t_ms: float
    data: Any


@dataclass(frozen=True)
class TurnSummary:
    character_id: str
    question: str
    answer_text: str
    directive: SceneDirective | None
    storyboard: Storyboard | None
    actor_key_label: str | None
    actor_model: str | None
    first_speech_ms: float | None
    scene_ready_ms: float | None
    total_ms: float
    errors: tuple[str, ...]


# --------------------------------------------------------------------------------------
# Tiện ích
# --------------------------------------------------------------------------------------
def _clean(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _mentions_any(text: str, phrases: Iterable[str]) -> bool:
    haystack = strip_diacritics(normalize_text(text))
    for phrase in phrases:
        needle = strip_diacritics(normalize_text(phrase)) if phrase else ""
        if needle and needle in haystack:
            return True
    return False


def _ensure_reference(prompt: str, reference_path: str) -> str:
    if reference_path in prompt:
        return prompt
    return f"{prompt} {_CONSISTENCY_CLAUSE.format(path=reference_path)}".strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("\n") + 1 :] if "\n" in raw else raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise DirectorOutputError("AI Đạo diễn không trả về JSON hợp lệ.") from None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DirectorOutputError(f"AI Đạo diễn không trả về JSON hợp lệ: {exc}") from None
    if not isinstance(data, dict):
        raise DirectorOutputError("AI Đạo diễn phải trả về một JSON object.")
    return data


def _parse_beats(text: str, segment_count: int) -> dict[int, Mapping[str, Any]]:
    data = _parse_json_object(text)
    items = data.get("beats")
    if not isinstance(items, list):
        raise DirectorOutputError("Storyboard thiếu mảng 'beats'.")
    beats: dict[int, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        try:
            index = int(item.get("segment_index"))
        except (TypeError, ValueError):
            continue
        if 0 <= index < segment_count and index not in beats:
            beats[index] = item
    return beats


def _entities_of(profile: CharacterProfile) -> list[ContextEntity]:
    entities = [ContextEntity(HOMELAND_CONTEXT_KEY, "homeland", profile.homeland_aliases)]
    entities.extend(ContextEntity(event.event_id, event.kind, event.aliases) for event in profile.events)
    return entities


# --------------------------------------------------------------------------------------
# Director Agent
# --------------------------------------------------------------------------------------
class DirectorAgent:
    """AI Đạo diễn: quyết định khung hình, viết prompt ảnh động, storyboard theo DP."""

    # Chuỗi model ưu tiên: model sau là dự phòng khi model trước quá tải (503) / timeout / ngừng cung cấp.
    DEFAULT_MODELS: tuple[str, ...] = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3-flash-preview")

    def __init__(
        self,
        pool: GeminiKeyPool,
        *,
        models: Sequence[str] = DEFAULT_MODELS,
        temperature: float = 0.3,
        thinking_level: str | None = "minimal",
        request_timeout_s: float = 20.0,
        cache: VisualStateCache[SceneDirective] | None = None,
        segmentation_config: SegmentationConfig | None = None,
    ) -> None:
        if not models:
            raise ValueError("Cần ít nhất một model cho AI Đạo diễn.")
        if request_timeout_s <= 0:
            raise ValueError("request_timeout_s phải > 0.")
        self._pool = pool
        self.models = tuple(models)
        self.temperature = temperature
        self.thinking_level = thinking_level
        self.request_timeout_s = request_timeout_s
        self.cache: VisualStateCache[SceneDirective] = cache if cache is not None else VisualStateCache()
        self.segmentation_config = segmentation_config or SegmentationConfig()
        all_names = [
            name
            for profile in CHARACTERS.values()
            for name in (profile.display_name, profile.full_name, *profile.name_aliases)
        ]
        self._analyzers = {
            character_id: ContextAnalyzer(_entities_of(profile), person_names=all_names)
            for character_id, profile in CHARACTERS.items()
        }

    # ------------------------------------------------------------------ prompt builders
    def analyze(self, character_id: str, question: str) -> ContextFingerprint:
        profile = get_character(character_id)
        return self._analyzers[profile.character_id].analyze(profile.character_id, question)

    def build_image_system_prompt(self, character_id: str, scene_mode: str) -> str:
        """System prompt cho mô hình tạo ảnh / video (Phase 2) — giữ nguyên model anh hùng."""
        profile = get_character(character_id)
        reference = character_reference_path(profile.character_id)
        common = (
            "You are the visual generation model of the Vietnamese history app 'Đại Việt Sử Ký'. "
            f"The hero is {profile.display_name}. Always keep the hero exactly identical to the reference image "
            f"{reference}: same face, age, beard and hairstyle, same costume ({profile.costume_hint}), same colors "
            "and proportions. Only change the hero's pose, action and expression, and the background scene. "
            f"Historical accuracy: {profile.era_en} — costumes, armor, weapons, boats and architecture of that "
            f"period only; no anachronisms ({', '.join(profile.anachronisms)}). Cinematic historical painting style, "
            "rich natural light, 16:9 frame, subtle looping motion (water, flags, smoke, clouds, swaying plants). "
            "Never render text, captions, logos or watermarks."
        )
        if scene_mode == SCENE_FOREGROUND_TALKING:
            rule = (
                " Mode FOREGROUND_TALKING: place the hero in the foreground, waist-up, facing the viewer with the "
                "mouth clearly visible and unobstructed for lip-sync animation, in front of an animated background "
                f"of his homeland: {profile.homeland_en}."
            )
        else:
            rule = (
                " Mode BACKGROUND_ACTION: the hero is inside the scene performing the described action (commanding, "
                "fighting, riding, writing...); he does not address the camera and there is no lip-sync."
            )
        return common + rule

    def build_negative_prompt(self, character_id: str, scene_mode: str) -> str:
        profile = get_character(character_id)
        items = [
            "text", "captions", "subtitles", "watermark", "logo", "changing the hero's face, hairstyle or costume",
            "deformed hands", "extra limbs", "blurry face", *profile.anachronisms,
        ]
        if scene_mode == SCENE_FOREGROUND_TALKING:
            items += ["hand covering the mouth", "face turned away from the viewer", "battle chaos in the foreground"]
        else:
            items += ["hero posing for the camera", "hero talking to the viewer"]
        return ", ".join(items)

    def build_scene_payload(self, character_id: str, question: str) -> dict[str, Any]:
        profile = get_character(character_id)
        reference = character_reference_path(profile.character_id)
        events = "\n".join(
            f"- {event.event_id} | {event.name} | {event.year} | "
            f"{'trận đánh' if event.kind == 'battle' else 'sự kiện'} | {event.visual_brief} | "
            f"gợi ý hành động: {event.hero_action}"
            for event in profile.events
        )
        user_text = (
            f"NHÂN VẬT: {profile.display_name} ({profile.full_name}) — {profile.era}\n"
            f"ẢNH MẪU CỐ ĐỊNH: {reference}\n"
            f"QUÊ HƯƠNG: {profile.homeland}\n"
            f"GỢI Ý HÌNH ẢNH QUÊ HƯƠNG: {profile.homeland_visual}\n"
            f"TRANG PHỤC THEO ẢNH MẪU: {profile.costume_hint}\n"
            "CÁC SỰ KIỆN ĐÃ BIẾT (context_key | tên | năm | loại | gợi ý hình ảnh | gợi ý hành động):\n"
            f"{events}\n\n"
            f"CÂU HỎI NGƯỜI DÙNG GỬI {profile.display_name.upper()}: \"{question}\""
        )
        context_keys = [HOMELAND_CONTEXT_KEY, *(event.event_id for event in profile.events), OTHER_EVENT_KEY]
        order = [
            "reason", "scene_mode", "background_type", "context_key", "background_prompt", "character_action",
            "scene_prompt",
        ]
        schema = {
            "type": "OBJECT",
            "properties": {
                "reason": {"type": "STRING"},
                "scene_mode": {"type": "STRING", "enum": list(SCENE_MODES)},
                "background_type": {"type": "STRING", "enum": list(BACKGROUND_TYPES)},
                "context_key": {"type": "STRING", "enum": context_keys},
                "background_prompt": {"type": "STRING"},
                "character_action": {"type": "STRING"},
                "scene_prompt": {"type": "STRING"},
            },
            "required": order,
            "propertyOrdering": order,
        }
        return self._payload(_SCENE_SYSTEM_PROMPT, user_text, schema, max_output_tokens=1400)

    def build_storyboard_payload(self, directive: SceneDirective, segmentation: SegmentationResult) -> dict[str, Any]:
        profile = get_character(directive.character_id)
        lines = "\n".join(
            f"[{segment.index}] {segment.start_ms / 1000:.1f}s–{(segment.start_ms + segment.duration_ms) / 1000:.1f}s"
            f" | gợi ý thị giác: {segment.dominant_visual or 'không rõ'} | \"{segment.text.strip()}\""
            for segment in segmentation.segments
        )
        user_text = (
            f"NHÂN VẬT: {profile.display_name} — {profile.era}\n"
            f"ẢNH MẪU CỐ ĐỊNH: {directive.character_reference_path}\n"
            f"CHẾ ĐỘ CẢNH ĐÃ CHỐT: {directive.scene_mode} (mouth_sync_enabled="
            f"{str(directive.mouth_sync_enabled).lower()}) — giữ nguyên cho mọi phân cảnh\n"
            f"LOẠI HẬU CẢNH: {directive.background_type} | context_key: {directive.context_key}\n"
            f"HẬU CẢNH GỐC: {directive.background_prompt}\n"
            f"HÀNH ĐỘNG GỐC: {directive.character_action}\n"
            f"CÂU HỎI: \"{directive.question}\"\n\n"
            f"LỜI THOẠI ĐÃ ĐƯỢC CHIA THÀNH {len(segmentation.segments)} PHÂN CẢNH BẰNG QUY HOẠCH ĐỘNG:\n{lines}"
        )
        beat_order = ["segment_index", "character_action", "scene_prompt"]
        schema = {
            "type": "OBJECT",
            "properties": {
                "beats": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "segment_index": {"type": "INTEGER"},
                            "character_action": {"type": "STRING"},
                            "scene_prompt": {"type": "STRING"},
                        },
                        "required": beat_order,
                        "propertyOrdering": beat_order,
                    },
                }
            },
            "required": ["beats"],
        }
        return self._payload(_STORYBOARD_SYSTEM_PROMPT, user_text, schema, max_output_tokens=2400)

    def _payload(self, system_prompt: str, user_text: str, schema: Mapping[str, Any], *, max_output_tokens: int) -> dict[str, Any]:
        generation_config: dict[str, Any] = {
            "temperature": self.temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
        if self.thinking_level:
            generation_config["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": generation_config,
        }

    # ------------------------------------------------------------------ quyết định cảnh
    def _rule_based_decision(self, profile: CharacterProfile, fingerprint: ContextFingerprint) -> dict[str, Any]:
        """Phương án dự phòng khi không gọi được AI: suy luận từ từ khoá và thực thể của câu hỏi."""
        event = next((profile.event(eid) for eid in fingerprint.entities if profile.event(eid) is not None), None)
        if fingerprint.intent == INTENT_ACTION:
            background = event.visual_brief if event else (
                f"A dramatic, historically accurate scene from the life of {profile.display_name} in "
                f"{profile.era_en}, with era-appropriate soldiers, banners and landscape in motion."
            )
            action = event.hero_action if event else (
                "the hero from the reference image leads his troops in the midst of the action, raising his sword "
                "and giving commands"
            )
            return {
                "scene_mode": SCENE_BACKGROUND_ACTION,
                "background_type": (
                    BACKGROUND_HISTORICAL_EVENT if event is not None and event.kind == "event" else BACKGROUND_BATTLEFIELD
                ),
                "context_key": event.event_id if event else OTHER_EVENT_KEY,
                "background_prompt": background,
                "character_action": action,
                "scene_prompt": f"Wide cinematic shot, dynamic camera. {action}. Setting: {background}",
                "reason": "Quy tắc dự phòng: câu hỏi về trận đánh / hành động nên tái hiện cảnh hành động.",
            }
        return {
            "scene_mode": SCENE_FOREGROUND_TALKING,
            "background_type": BACKGROUND_HOMELAND,
            "context_key": HOMELAND_CONTEXT_KEY,
            "background_prompt": profile.homeland_visual,
            "character_action": _FOREGROUND_ACTION,
            "scene_prompt": f"Medium shot at eye level, slow push-in. {_FOREGROUND_ACTION}. Animated background: "
            f"{profile.homeland_visual}",
            "reason": "Quy tắc dự phòng: câu hỏi về thông tin / tiểu sử nên nhân vật đứng tiền cảnh trò chuyện.",
        }

    def _finalize_directive(
        self,
        profile: CharacterProfile,
        question: str,
        data: Mapping[str, Any],
        *,
        state_id: str,
        decided_by: str,
        key_label: str | None,
        model: str | None,
        latency_ms: float,
        reason_suffix: str = "",
    ) -> SceneDirective:
        """Kiểm tra & ép các bất biến: mouth_sync ⇔ FOREGROUND_TALKING, ảnh mẫu cố định, hậu cảnh đúng."""
        scene_mode = _clean(data.get("scene_mode")).upper()
        if scene_mode not in SCENE_MODES:
            raise DirectorOutputError(f"scene_mode không hợp lệ: {data.get('scene_mode')!r}")
        reference = character_reference_path(profile.character_id)
        background = _clean(data.get("background_prompt"))
        action = _clean(data.get("character_action"))
        scene_prompt = _clean(data.get("scene_prompt"))
        reason = _clean(data.get("reason")) or "AI Đạo diễn không nêu lý do."

        if scene_mode == SCENE_FOREGROUND_TALKING:
            background_type = BACKGROUND_HOMELAND
            context_key = HOMELAND_CONTEXT_KEY
            if not background or not _mentions_any(background, profile.homeland_aliases):
                background = f"{profile.homeland_visual} {background}".strip()
            if not action or not _mentions_any(action, _TALKING_MARKERS):
                action = _FOREGROUND_ACTION
            if not scene_prompt:
                scene_prompt = f"Medium shot at eye level, slow push-in. {action}. Animated background: {background}"
            elif not _mentions_any(scene_prompt, profile.homeland_aliases):
                scene_prompt = f"{scene_prompt} Animated background: {background}"
        else:
            context_key = _clean(data.get("context_key"))
            event = profile.event(context_key)
            if event is None:
                context_key = OTHER_EVENT_KEY
            background_type = _clean(data.get("background_type")).upper()
            if background_type not in (BACKGROUND_BATTLEFIELD, BACKGROUND_HISTORICAL_EVENT):
                background_type = (
                    BACKGROUND_HISTORICAL_EVENT if event is not None and event.kind == "event" else BACKGROUND_BATTLEFIELD
                )
            if not background:
                background = event.visual_brief if event is not None else (
                    f"A dramatic, historically accurate scene from the life of {profile.display_name} in "
                    f"{profile.era_en}, with era-appropriate soldiers, banners and landscape in motion."
                )
            event_names = (*event.aliases, event.name) if event is not None else ()
            if event is not None and not _mentions_any(f"{background} {scene_prompt}", event_names):
                background = f"{event.visual_brief} {background}".strip()
            if not action:
                action = event.hero_action if event is not None else (
                    "the hero from the reference image leads his troops in the midst of the action, raising his "
                    "sword and giving commands"
                )
            if not scene_prompt:
                scene_prompt = f"Wide cinematic shot, dynamic camera. {action}. Setting: {background}"
            elif event is not None and not _mentions_any(scene_prompt, event_names):
                scene_prompt = f"{scene_prompt} Setting: {event.visual_brief}"

        return SceneDirective(
            state_id=state_id,
            character_id=profile.character_id,
            question=question,
            scene_mode=scene_mode,
            mouth_sync_enabled=scene_mode == SCENE_FOREGROUND_TALKING,
            character_reference_path=reference,
            background_type=background_type,
            context_key=context_key,
            background_prompt=background,
            character_action=action,
            scene_prompt=_ensure_reference(scene_prompt, reference),
            negative_prompt=self.build_negative_prompt(profile.character_id, scene_mode),
            image_system_prompt=self.build_image_system_prompt(profile.character_id, scene_mode),
            reason=f"{reason}{reason_suffix}",
            decided_by=decided_by,
            cache_hit=False,
            cache_match=None,
            cache_similarity=None,
            key_label=key_label,
            model=model,
            latency_ms=latency_ms,
        )

    def preview(self, character_id: str, question: str) -> SceneDirective:
        """Quyết định cảnh tức thời, KHÔNG gọi AI: tra DP cache O(1); trượt cache → suy luận theo từ khoá/thực thể.

        Dùng để sân khấu đổi cảnh ngay khi người dùng vừa gửi câu hỏi; ``direct()`` sau đó chốt kết quả chính thức.
        """
        profile = get_character(character_id)
        cleaned = " ".join(str(question or "").split())
        if not cleaned:
            raise ValueError("Câu hỏi không được để trống.")
        started = time.perf_counter()
        fingerprint = self.analyze(profile.character_id, cleaned)
        lookup = self.cache.lookup(fingerprint)
        if lookup.state is not None:
            return dataclasses.replace(
                lookup.state,
                question=cleaned,
                decided_by="cache",
                cache_hit=True,
                cache_match=lookup.decision.match_type,
                cache_similarity=round(lookup.decision.similarity, 4),
                key_label=None,
                model=None,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        return self._finalize_directive(
            profile,
            cleaned,
            self._rule_based_decision(profile, fingerprint),
            state_id=self.cache.new_state_id(),
            decided_by="rule_preview",
            key_label=None,
            model=None,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    async def direct(self, character_id: str, question: str, *, avoid_keys: Iterable[int] = ()) -> SceneDirective:
        """Đạo diễn phân cảnh: tra cache O(1) → gọi AI (JSON schema) → ép bất biến → lưu cache."""
        profile = get_character(character_id)
        cleaned = " ".join(str(question or "").split())
        if not cleaned:
            raise ValueError("Câu hỏi không được để trống.")
        started = time.perf_counter()
        fingerprint = self.analyze(profile.character_id, cleaned)
        lookup = self.cache.lookup(fingerprint)
        if lookup.state is not None:
            return dataclasses.replace(
                lookup.state,
                question=cleaned,
                decided_by="cache",
                cache_hit=True,
                cache_match=lookup.decision.match_type,
                cache_similarity=round(lookup.decision.similarity, 4),
                key_label=None,
                model=None,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        state_id = self.cache.new_state_id()
        try:
            result = await self._pool.generate(
                self.models,
                self.build_scene_payload(profile.character_id, cleaned),
                tag=f"director:scene:{profile.character_id}",
                avoid_keys=avoid_keys,
                request_timeout_s=self.request_timeout_s,
            )
            directive = self._finalize_directive(
                profile,
                cleaned,
                _parse_json_object(result.text),
                state_id=state_id,
                decided_by="llm",
                key_label=result.key_label,
                model=result.model,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except (KeyPoolError, ValueError) as exc:
            directive = self._finalize_directive(
                profile,
                cleaned,
                self._rule_based_decision(profile, fingerprint),
                state_id=state_id,
                decided_by="rule_fallback",
                key_label=None,
                model=None,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                reason_suffix=f" (AI Đạo diễn không khả dụng: {type(exc).__name__}: {exc})",
            )
            return directive
        self.cache.store(
            fingerprint,
            directive,
            state_id=state_id,
            scene_mode=directive.scene_mode,
            context_key=directive.context_key,
        )
        return directive

    # ------------------------------------------------------------------ storyboard
    async def storyboard(
        self,
        directive: SceneDirective,
        answer_text: str,
        *,
        avoid_keys: Iterable[int] = (),
    ) -> Storyboard:
        """Đạo diễn storyboard: DP chia lời thoại thành phân cảnh, AI viết prompt cho từng phân cảnh."""
        profile = get_character(directive.character_id)
        started = time.perf_counter()
        segmentation = dp_scene_segmentation(answer_text, self.segmentation_config)
        if not segmentation.segments:
            return Storyboard(
                directive.state_id, profile.character_id, directive.scene_mode, (), 0.0, "empty", None, None,
                (time.perf_counter() - started) * 1000.0,
            )
        beats_data: dict[int, Mapping[str, Any]] = {}
        decided_by, key_label, model = "rule_fallback", None, None
        try:
            result = await self._pool.generate(
                self.models,
                self.build_storyboard_payload(directive, segmentation),
                tag=f"director:storyboard:{profile.character_id}",
                avoid_keys=avoid_keys,
                request_timeout_s=self.request_timeout_s,
            )
            beats_data = _parse_beats(result.text, len(segmentation.segments))
            if beats_data:
                decided_by, key_label, model = "llm", result.key_label, result.model
        except (KeyPoolError, ValueError):
            beats_data = {}

        beats: list[SceneBeat] = []
        for segment in segmentation.segments:
            item = beats_data.get(segment.index, {})
            action = _clean(item.get("character_action")) or directive.character_action
            if directive.scene_mode == SCENE_FOREGROUND_TALKING and not _mentions_any(action, _TALKING_MARKERS):
                action = f"{_FOREGROUND_ACTION}; {action}"
            prompt = _clean(item.get("scene_prompt")) or (
                f"{directive.scene_prompt} Moment: \"{' '.join(segment.text.split())}\""
            )
            beats.append(
                SceneBeat(
                    beat_index=segment.index,
                    char_start=segment.char_start,
                    char_end=segment.char_end,
                    text=segment.text,
                    start_ms=segment.start_ms,
                    duration_ms=segment.duration_ms,
                    dominant_visual=segment.dominant_visual,
                    scene_mode=directive.scene_mode,
                    mouth_sync_enabled=directive.mouth_sync_enabled,
                    character_reference_path=directive.character_reference_path,
                    character_action=action,
                    scene_prompt=_ensure_reference(prompt, directive.character_reference_path),
                )
            )
        return Storyboard(
            state_id=directive.state_id,
            character_id=profile.character_id,
            scene_mode=directive.scene_mode,
            beats=tuple(beats),
            segmentation_cost=segmentation.total_cost,
            decided_by=decided_by,
            key_label=key_label,
            model=model,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    # ------------------------------------------------------------------ điều phối multi-agent
    async def run_scene(self, actor: ActorAgent, character_id: str, question: str) -> AsyncIterator[TurnEvent]:
        """Một lượt hỏi–đáp: AI Nhân vật (stream) và AI Đạo diễn chạy song song, rồi storyboard."""
        profile = get_character(character_id)
        cleaned = " ".join(str(question or "").split())
        if not cleaned:
            raise ValueError("Câu hỏi không được để trống.")
        started = time.perf_counter()

        def now_ms() -> float:
            return (time.perf_counter() - started) * 1000.0

        queue: asyncio.Queue[TurnEvent | None] = asyncio.Queue()
        answer_parts: list[str] = []
        actor_keys: set[int] = set()
        actor_meta: dict[str, str] = {}
        directives: list[SceneDirective] = []
        errors: list[str] = []

        async def actor_task() -> None:
            try:
                async for chunk in actor.stream_answer(profile.character_id, cleaned):
                    answer_parts.append(chunk.text)
                    if chunk.key_index >= 0:
                        actor_keys.add(chunk.key_index)
                        actor_meta["key_label"] = chunk.key_label
                        actor_meta["model"] = chunk.model
                    await queue.put(TurnEvent("speech", now_ms(), chunk))
            except Exception as exc:
                errors.append(f"actor: {exc}")
                await queue.put(TurnEvent("error", now_ms(), {"agent": "actor", "error": str(exc)}))
            finally:
                await queue.put(None)

        async def director_task() -> None:
            try:
                directive = await self.direct(profile.character_id, cleaned)
                directives.append(directive)
                await queue.put(TurnEvent("scene", now_ms(), directive))
            except Exception as exc:
                errors.append(f"director: {exc}")
                await queue.put(TurnEvent("error", now_ms(), {"agent": "director", "error": str(exc)}))
            finally:
                await queue.put(None)

        tasks = [
            asyncio.create_task(actor_task(), name=f"actor:{profile.character_id}"),
            asyncio.create_task(director_task(), name=f"director:{profile.character_id}"),
        ]
        first_speech_ms: float | None = None
        scene_ready_ms: float | None = None
        try:
            finished = 0
            while finished < len(tasks):
                event = await queue.get()
                if event is None:
                    finished += 1
                    continue
                if event.kind == "scene":
                    scene_ready_ms = event.t_ms
                elif event.kind == "speech" and first_speech_ms is None and event.data.text:
                    first_speech_ms = event.t_ms
                yield event

            answer_text = "".join(answer_parts)
            storyboard: Storyboard | None = None
            actor_failed = any(error.startswith("actor:") for error in errors)
            if directives and answer_text.strip() and not actor_failed:
                try:
                    storyboard = await self.storyboard(directives[0], answer_text, avoid_keys=actor_keys)
                    yield TurnEvent("storyboard", now_ms(), storyboard)
                except Exception as exc:
                    errors.append(f"storyboard: {exc}")
                    yield TurnEvent("error", now_ms(), {"agent": "storyboard", "error": str(exc)})
            yield TurnEvent(
                "done",
                now_ms(),
                TurnSummary(
                    character_id=profile.character_id,
                    question=cleaned,
                    answer_text=answer_text,
                    directive=directives[0] if directives else None,
                    storyboard=storyboard,
                    actor_key_label=actor_meta.get("key_label"),
                    actor_model=actor_meta.get("model"),
                    first_speech_ms=first_speech_ms,
                    scene_ready_ms=scene_ready_ms,
                    total_ms=now_ms(),
                    errors=tuple(errors),
                ),
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
