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
import re
import time
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..core.dp_optimizer import (
    HOMELAND_CONTEXT_KEY,
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
from ..core.dp_optimizer import TopicKeyCache, is_valid_topic_key
from ..core.key_pool import GeminiKeyPool, KeyPoolError
from ..core.scene_library import SceneLibrary
from .actor_agent import CHARACTERS, ActorAgent, CharacterProfile, HistoricalEvent, get_character
from .history_rules import (
    GENERAL_TOPIC_KEY,
    HERO_EVENTS,
    HERO_HOMELAND_TOPIC,
    QUESTION_KIND_BIOGRAPHY,
    QUESTION_KIND_HISTORICAL,
    QUESTION_KIND_OFF_TOPIC,
    QUESTION_KINDS,
    clean_topic_key,
    detect_era_boundary,
    looks_biographical,
    scene_mode_for,
)

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
    "TopicInfo",
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

_SCENE_SYSTEM_PROMPT = """Bạn là AI ĐẠO DIỄN BỐI CẢNH của ứng dụng giáo dục lịch sử "Đại Việt Sử Ký". Ứng dụng có đúng 3 nhân vật: Trần Hưng Đạo, Nguyễn Trãi, Quang Trung – Nguyễn Huệ. Người dùng hỏi một nhân vật; một AI Nhân vật khác trả lời bằng lời thoại. Bạn KHÔNG trả lời câu hỏi. Nhiệm vụ DUY NHẤT của bạn là chọn MÃ BỐI CẢNH (context_topic_key) cho ảnh nền phía sau nhân vật. Chế độ khung hình (nhân vật đứng nói hay hành động trong trận) do hệ thống tự quyết theo bảng sử liệu cố định, bạn không cần chọn.

CÁCH CHỌN context_topic_key
1. Mã là chuỗi tiếng Anh/không dấu, chữ thường, dạng snake_case, chỉ gồm a-z, 0-9 và dấu gạch dưới (regex ^[a-z0-9_]+$), vd: moscow_red_square, bach_dang_1288, ngoc_hoi_dong_da_1789, que_huong_tuc_mac.
2. LUÔN ưu tiên một mã trong THƯ VIỆN CẢNH CÓ SẴN. Chỉ đặt mã mới khi không mã nào khớp (vd. một địa danh nước ngoài chưa có trong thư viện).
3. Câu hỏi không ghi năm thì chọn trận / sự kiện gắn với chính nhân vật đang được hỏi (Trần Hưng Đạo + "Bạch Đằng" → bach_dang_1288). Nhân vật khác hỏi về Bạch Đằng cũng dùng bach_dang_1288.
4. GIỮ HAY ĐỔI BỐI CẢNH: nếu có MÃ BỐI CẢNH LƯỢT TRƯỚC và câu hỏi mới vẫn nói tiếp chủ đề / địa danh đó (hỏi tiếp diễn biến, chi tiết, "rồi sau đó thế nào"...) thì keep_previous_topic = true và context_topic_key = ĐÚNG NGUYÊN mã lượt trước. Câu hỏi chuyển sang chủ đề / địa danh khác thì keep_previous_topic = false và đặt mã mới.
5. question_kind:
   - "biography": hỏi về bản thân nhân vật (năm sinh, năm mất, quê quán, gia đình, tên hiệu, tuổi thơ...). Chỉ loại này mới dùng mã quê hương (que_huong_...) của nhân vật.
   - "historical_topic": hỏi về một trận đánh, sự kiện, địa danh, quốc gia, công trình có thật trong lịch sử — kể cả quốc gia / địa danh nước ngoài mà nhân vật chưa biết (vd. "Ông biết gì về nước Nga?" → historical_topic, moscow_red_square, KHÔNG phải off_topic).
   - "off_topic": câu hỏi lạc đề, đùa cợt, không phù hợp, hoặc không gắn với địa danh / sự kiện nào → context_topic_key = "chu_de_chung".
6. beyond_era = true khi câu hỏi nhắc tới quốc gia, địa danh, sự kiện, khái niệm mà sinh thời nhân vật chưa thể biết (vd. nước Nga, châu Mỹ, máy bay đối với cả 3 nhân vật).
7. wikimedia_search_query: CHỈ khi question_kind là "historical_topic" hoặc "biography": 2–6 từ khóa tiếng Anh để tìm ẢNH PHONG CẢNH / CÔNG TRÌNH của đúng địa danh đó trên Wikimedia Commons (vd. "Red Square Moscow Kremlin", "Bach Dang River Quang Ninh"). Không dùng từ map, flag, diagram, logo. "off_topic" thì để chuỗi rỗng.
8. topic_title_vi: tên bối cảnh ngắn gọn bằng tiếng Việt có dấu (vd. "Mát-xcơ-va (nước Nga)", "Trận Bạch Đằng").

QUY TẮC VIẾT PROMPT (tiếng Anh, giữ nguyên tên riêng tiếng Việt có dấu)
- background_prompt: mô tả hậu cảnh đúng địa danh, thời đại, kiến trúc; không có chi tiết lỗi thời; 30–60 từ; không mô tả nhân vật.
- character_action: một câu mô tả tư thế / hành động của "the hero from the reference image"; không mô tả lại khuôn mặt, trang phục.
- scene_prompt: 50–100 từ cho mô hình hình ảnh: bố cục, hậu cảnh, ánh sáng, góc máy; phải nhắc "the hero from the reference image".
- reason: một câu tiếng Việt giải thích vì sao chọn mã bối cảnh."""

_STORYBOARD_SYSTEM_PROMPT = """Bạn là AI ĐẠO DIỄN PHÂN CẢNH của ứng dụng giáo dục lịch sử "Đại Việt Sử Ký". Lời thoại của nhân vật lịch sử đã được thuật toán quy hoạch động chia thành các phân cảnh liên tiếp. Với MỖI phân cảnh, viết:
- character_action: hành động / tư thế của nhân vật trong phân cảnh (tiếng Anh, 1–2 câu).
- scene_prompt: prompt 50–100 từ (tiếng Anh) cho mô hình tạo ảnh / video, minh họa ĐÚNG nội dung lời thoại của phân cảnh đó và liền mạch với phân cảnh trước.

QUY TẮC
- FOREGROUND_TALKING: nhân vật luôn đứng tiền cảnh, nhìn người xem và đang nói (miệng không bị che); hậu cảnh luôn là bối cảnh đã chốt (chỉ minh họa, nhân vật không tham chiến); chỉ thay đổi chi tiết ánh sáng, góc máy và cử chỉ tay cho hợp lời thoại.
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
    # --- bối cảnh chuẩn hóa (context_topic_key) ---
    context_topic_key: str = ""
    previous_context_topic_key: str | None = None
    topic_changed: bool = True
    topic_title: str = ""
    topic_year: str = ""
    topic_kind: str = ""  # battle | event | homeland | place | other
    question_kind: str = QUESTION_KIND_HISTORICAL  # biography | historical_topic | off_topic
    wikimedia_search_query: str = ""
    image_allowed: bool = True  # False → chỉ phông trung tính (câu lạc đề / quê hương ngoài câu hỏi tiểu sử)
    beyond_era: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class TopicInfo:
    """Thông tin bối cảnh đã chốt, lưu theo (nhân vật, context_topic_key) để tra lại O(1)."""

    key: str
    title: str
    year: str
    kind: str
    wikimedia_search_query: str
    background_prompt: str


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


_SAFE_QUERY_RE = re.compile(r"[^A-Za-z0-9 \-'.,]+")
# Sự kiện trong hồ sơ nhân vật tương ứng với từng mã bối cảnh (để lấy gợi ý hình ảnh / hành động).
_TOPIC_TO_PROFILE_EVENT = {
    "khang_chien_mong_nguyen_1285": "khang_chien_mong_nguyen",
    "khang_chien_mong_nguyen_1287_1288": "khang_chien_mong_nguyen",
}
_PROFILE_EVENT_TO_TOPIC = {"khang_chien_mong_nguyen": "khang_chien_mong_nguyen_1287_1288"}


def _profile_event(profile: CharacterProfile, topic_key: str) -> HistoricalEvent | None:
    return profile.event(topic_key) or profile.event(_TOPIC_TO_PROFILE_EVENT.get(topic_key, ""))


def _safe_query(value: Any) -> str:
    """Từ khóa tìm ảnh: chỉ chữ Latinh/số/khoảng trắng, tối đa 8 từ, 80 ký tự."""
    text = strip_diacritics(_clean(value))
    text = " ".join(_SAFE_QUERY_RE.sub(" ", text).split()[:8])
    return text[:80].strip()


def _humanize_key(key: str) -> str:
    return key.replace("_", " ").strip().title() or "Đại Việt"


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
        library: SceneLibrary | None = None,
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
        self.library = library if library is not None else SceneLibrary.load()
        # Tra O(1) theo (nhân vật, context_topic_key): giữ nguyên chủ đề → lấy lại bối cảnh đã chốt, không dựng lại.
        self.topics: TopicKeyCache[TopicInfo] = TopicKeyCache()
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
                "mouth clearly visible and unobstructed for lip-sync animation; the background only illustrates the "
                "place or event being talked about — the hero does not take part in it."
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

    def build_scene_payload(
        self, character_id: str, question: str, previous_context_topic_key: str | None = None
    ) -> dict[str, Any]:
        profile = get_character(character_id)
        own_events = sorted(HERO_EVENTS.get(profile.character_id, frozenset()))
        catalog = "\n".join(
            f"- {entry.key} | {entry.title}{f' | {entry.year}' if entry.year else ''} | {entry.kind}"
            + (f" | quê hương của {get_character(entry.hero_id).display_name}" if entry.hero_id else "")
            for entry in self.library.entries()
        )
        previous = previous_context_topic_key or ""
        previous_line = "(không có — đây là lượt hỏi đầu tiên)"
        if previous:
            cached = self.topics.get(profile.character_id, previous)
            entry = self.library.get(previous)
            title = cached.title if cached else (entry.title if entry else _humanize_key(previous))
            previous_line = f"{previous} ({title})"
        user_text = (
            f"NHÂN VẬT ĐANG ĐƯỢC HỎI: {profile.display_name} ({profile.full_name}) — {profile.era}\n"
            f"MÃ QUÊ HƯƠNG CỦA NHÂN VẬT: {HERO_HOMELAND_TOPIC[profile.character_id]}\n"
            f"TRẬN NHÂN VẬT TRỰC TIẾP CHỈ HUY: {', '.join(own_events) if own_events else '(không có)'}\n"
            f"THƯ VIỆN CẢNH CÓ SẴN (context_topic_key | tên | năm | loại):\n{catalog}\n"
            f"- {GENERAL_TOPIC_KEY} | Chủ đề chung (câu hỏi lạc đề / không gắn địa danh) | other\n\n"
            f"MÃ BỐI CẢNH LƯỢT TRƯỚC (previous_context_topic_key): {previous_line}\n\n"
            f"CÂU HỎI NGƯỜI DÙNG GỬI {profile.display_name.upper()}: \"{question}\""
        )
        order = [
            "reason", "question_kind", "keep_previous_topic", "context_topic_key", "topic_title_vi",
            "wikimedia_search_query", "beyond_era", "background_prompt", "character_action", "scene_prompt",
        ]
        schema = {
            "type": "OBJECT",
            "properties": {
                "reason": {"type": "STRING"},
                "question_kind": {"type": "STRING", "enum": list(QUESTION_KINDS)},
                "keep_previous_topic": {"type": "BOOLEAN"},
                "context_topic_key": {"type": "STRING"},
                "topic_title_vi": {"type": "STRING"},
                "wikimedia_search_query": {"type": "STRING"},
                "beyond_era": {"type": "BOOLEAN"},
                "background_prompt": {"type": "STRING"},
                "character_action": {"type": "STRING"},
                "scene_prompt": {"type": "STRING"},
            },
            "required": order,
            "propertyOrdering": order,
        }
        return self._payload(_SCENE_SYSTEM_PROMPT, user_text, schema, max_output_tokens=1200)

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
    def _match_library(self, profile: CharacterProfile, question: str) -> str | None:
        """Tìm mã thư viện mà câu hỏi nhắc tới (bí danh dài nhất thắng; trận của chính nhân vật được ưu tiên)."""
        haystack = " " + re.sub(r"[^a-z0-9]+", " ", strip_diacritics(normalize_text(question))) + " "
        own = HERO_EVENTS.get(profile.character_id, frozenset())
        best: tuple[int, int, str] | None = None
        for entry in self.library.entries():
            for alias in (entry.key, *entry.aliases):
                phrase = f" {alias.replace('_', ' ')} "
                if phrase in haystack:
                    rank = (len(alias), 1 if entry.key in own else 0, entry.key)
                    if best is None or rank[:2] > best[:2]:
                        best = rank
        return best[2] if best else None

    def _rule_based_decision(
        self,
        profile: CharacterProfile,
        fingerprint: ContextFingerprint,
        question: str,
        previous: str | None,
    ) -> dict[str, Any]:
        """Phương án không cần AI (xem trước tức thời / dự phòng khi AI lỗi): suy từ từ khoá, thực thể, thư viện."""
        boundary = detect_era_boundary(profile.character_id, question)
        biography = looks_biographical(question) or HOMELAND_CONTEXT_KEY in fingerprint.entities
        kind = QUESTION_KIND_HISTORICAL
        query = ""
        title = ""
        if boundary is not None:
            key = boundary.topic_key or clean_topic_key(boundary.topic_title) or GENERAL_TOPIC_KEY
            title, query = boundary.topic_title, boundary.wikimedia_search_query
            reason = f"Quy tắc: câu hỏi về {boundary.label} — ngoài hiểu biết sinh thời của nhân vật."
        else:
            key = self._match_library(profile, question) or ""
            if not key:
                event_id = next((eid for eid in fingerprint.entities if profile.event(eid) is not None), None)
                if event_id is not None:
                    key = _PROFILE_EVENT_TO_TOPIC.get(event_id, event_id)
            if key == "khang_chien_mong_nguyen_1287_1288" and "1285" in question:
                key = "khang_chien_mong_nguyen_1285"
            entry = self.library.get(key) if key else None
            if key and not (entry is not None and entry.is_homeland and not biography):
                reason = "Quy tắc: câu hỏi nhắc tới bối cảnh có trong thư viện cảnh."
                if entry is not None and entry.is_homeland:
                    kind = QUESTION_KIND_BIOGRAPHY
            elif biography:
                key, kind = HERO_HOMELAND_TOPIC[profile.character_id], QUESTION_KIND_BIOGRAPHY
                reason = "Quy tắc: câu hỏi tiểu sử → bối cảnh quê hương của nhân vật."
            elif previous:
                key = previous
                reason = "Quy tắc: câu hỏi không nêu bối cảnh mới → giữ bối cảnh lượt trước."
            else:
                key = GENERAL_TOPIC_KEY
                reason = "Quy tắc: câu hỏi không gắn địa danh / sự kiện → phông trung tính."
        return {
            "reason": reason,
            "question_kind": kind,
            "keep_previous_topic": bool(previous) and key == previous,
            "context_topic_key": key,
            "topic_title_vi": title,
            "wikimedia_search_query": query,
            "beyond_era": boundary is not None,
            "background_prompt": "",
            "character_action": "",
            "scene_prompt": "",
        }

    def _finalize_directive(
        self,
        profile: CharacterProfile,
        question: str,
        data: Mapping[str, Any],
        *,
        previous: str | None,
        state_id: str,
        decided_by: str,
        key_label: str | None,
        model: str | None,
        latency_ms: float,
        reason_suffix: str = "",
    ) -> SceneDirective:
        """Chốt bối cảnh: chuẩn hóa mã, giữ/đổi theo lượt trước; scene_mode + khẩu hình do HERO_EVENTS quyết."""
        hero_id = profile.character_id
        question_kind = _clean(data.get("question_kind")).lower()
        if question_kind not in QUESTION_KINDS:
            question_kind = QUESTION_KIND_BIOGRAPHY if looks_biographical(question) else QUESTION_KIND_HISTORICAL
        keep = data.get("keep_previous_topic") is True and bool(previous)
        key = clean_topic_key(previous if keep else (data.get("context_topic_key") or data.get("context_key")))
        if key == HOMELAND_CONTEXT_KEY:
            key = HERO_HOMELAND_TOPIC[hero_id]
        if key:
            key = self.library.resolve(key) or _PROFILE_EVENT_TO_TOPIC.get(key, key)
        if not key:
            key = previous or (
                HERO_HOMELAND_TOPIC[hero_id] if question_kind == QUESTION_KIND_BIOGRAPHY else GENERAL_TOPIC_KEY
            )
        # Câu hỏi về xứ sở có thật ngoài thời đại (vd. nước Nga) là chủ đề lịch sử – địa lý, KHÔNG phải lạc đề:
        # AI xếp nhầm vào chủ đề chung / off_topic thì code đặt lại đúng bối cảnh minh họa.
        boundary = detect_era_boundary(hero_id, question)
        if boundary is not None and boundary.topic_key and (
            key == GENERAL_TOPIC_KEY or question_kind == QUESTION_KIND_OFF_TOPIC
        ):
            key, question_kind = boundary.topic_key, QUESTION_KIND_HISTORICAL
        if question_kind == QUESTION_KIND_OFF_TOPIC:
            key = GENERAL_TOPIC_KEY
        if not is_valid_topic_key(key):  # phòng thủ: không bao giờ phát mã sai chuẩn
            key = GENERAL_TOPIC_KEY

        entry = self.library.get(key)
        cached = self.topics.get(hero_id, key)
        is_general = key == GENERAL_TOPIC_KEY
        topic_kind = entry.kind if entry else ("other" if is_general else "place")
        # Ảnh quê hương CHỈ cho câu hỏi tiểu sử; câu lạc đề / chủ đề chung → phông trung tính, không tìm ảnh.
        image_allowed = not is_general and question_kind != QUESTION_KIND_OFF_TOPIC and not (
            entry is not None and entry.is_homeland and question_kind != QUESTION_KIND_BIOGRAPHY
        )
        if entry is not None:
            title, year = entry.title, entry.year
        elif is_general:
            title, year = f"Trò chuyện cùng {profile.display_name}", ""
        else:
            title = (cached.title if cached else "") or _clean(data.get("topic_title_vi"))[:80] or _humanize_key(key)
            year = ""
        query = ""
        if image_allowed:
            query = (
                (entry.wikimedia_search_query if entry is not None else "")
                or _safe_query(data.get("wikimedia_search_query"))
                or (cached.wikimedia_search_query if cached else "")
            )

        # LUẬT 1 — chốt bằng code: chỉ trận nhân vật trực tiếp chỉ huy mới là BACKGROUND_ACTION.
        scene_mode = scene_mode_for(hero_id, key)
        suggested = _clean(data.get("scene_mode")).upper()
        if suggested in SCENE_MODES and suggested != scene_mode:
            reason_suffix = f" (code ghi đè scene_mode {suggested} → {scene_mode} theo HERO_EVENTS){reason_suffix}"
        beyond_era = data.get("beyond_era") is True or boundary is not None

        event = _profile_event(profile, key)
        if topic_kind == "homeland":
            background_type = BACKGROUND_HOMELAND
        elif topic_kind == "battle":
            background_type = BACKGROUND_BATTLEFIELD
        else:
            background_type = BACKGROUND_HISTORICAL_EVENT
        reference = character_reference_path(hero_id)
        background = (
            _clean(data.get("background_prompt"))
            or (cached.background_prompt if cached else "")
            or (event.visual_brief if event is not None else "")
            or (profile.homeland_visual if topic_kind == "homeland" else "")
            or f"Historically accurate wide view of {strip_diacritics(title)}, natural light, no text, no watermark."
        )
        scene_prompt_raw = _clean(data.get("scene_prompt"))
        # Hậu cảnh phải nói đúng địa danh / sự kiện đã chốt (AI viết lệch → bổ sung gợi ý hình ảnh chuẩn).
        if event is not None and not _mentions_any(f"{background} {scene_prompt_raw}", (*event.aliases, event.name)):
            background = f"{event.visual_brief} {background}".strip()
        elif topic_kind == "homeland" and not _mentions_any(background, profile.homeland_aliases):
            background = f"{profile.homeland_visual} {background}".strip()
        action = _clean(data.get("character_action"))
        if scene_mode == SCENE_FOREGROUND_TALKING:
            if not action or not _mentions_any(action, _TALKING_MARKERS):
                action = _FOREGROUND_ACTION
            default_prompt = f"Medium shot at eye level, slow push-in. {action}. Illustrative background: {background}"
        else:
            if not action or _mentions_any(action, _TALKING_MARKERS):
                action = event.hero_action if event is not None else (
                    "the hero from the reference image leads his troops in the midst of the action, raising his "
                    "sword and giving commands"
                )
            default_prompt = f"Wide cinematic shot, dynamic camera. {action}. Setting: {background}"
        scene_prompt = scene_prompt_raw or default_prompt
        if event is not None and not _mentions_any(scene_prompt, (*event.aliases, event.name)):
            scene_prompt = f"{scene_prompt} Setting: {event.visual_brief}"
        reason = _clean(data.get("reason")) or "AI Đạo diễn không nêu lý do."

        return SceneDirective(
            state_id=state_id,
            character_id=hero_id,
            question=question,
            scene_mode=scene_mode,
            mouth_sync_enabled=scene_mode == SCENE_FOREGROUND_TALKING,
            character_reference_path=reference,
            background_type=background_type,
            context_key=key,
            background_prompt=background,
            character_action=action,
            scene_prompt=_ensure_reference(scene_prompt, reference),
            negative_prompt=self.build_negative_prompt(hero_id, scene_mode),
            image_system_prompt=self.build_image_system_prompt(hero_id, scene_mode),
            reason=f"{reason}{reason_suffix}",
            decided_by=decided_by,
            cache_hit=False,
            cache_match=None,
            cache_similarity=None,
            key_label=key_label,
            model=model,
            latency_ms=latency_ms,
            context_topic_key=key,
            previous_context_topic_key=previous,
            topic_changed=key != previous,
            topic_title=title,
            topic_year=year,
            topic_kind=topic_kind,
            question_kind=question_kind,
            wikimedia_search_query=query,
            image_allowed=image_allowed,
            beyond_era=beyond_era,
        )

    def _remember_topic(self, directive: SceneDirective) -> None:
        self.topics.put(
            directive.character_id,
            directive.context_topic_key,
            TopicInfo(
                key=directive.context_topic_key,
                title=directive.topic_title,
                year=directive.topic_year,
                kind=directive.topic_kind,
                wikimedia_search_query=directive.wikimedia_search_query,
                background_prompt=directive.background_prompt,
            ),
        )

    @staticmethod
    def _clean_question(question: str) -> str:
        cleaned = " ".join(str(question or "").split())
        if not cleaned:
            raise ValueError("Câu hỏi không được để trống.")
        return cleaned

    def preview(
        self, character_id: str, question: str, previous_context_topic_key: str | None = None
    ) -> SceneDirective:
        """Quyết định cảnh tức thời, KHÔNG gọi AI (từ khoá / thực thể / thư viện / mã lượt trước).

        Dùng để sân khấu đổi cảnh ngay khi người dùng vừa gửi câu hỏi; ``direct()`` sau đó chốt kết quả chính thức.
        """
        profile = get_character(character_id)
        cleaned = self._clean_question(question)
        previous = clean_topic_key(previous_context_topic_key) or None
        started = time.perf_counter()
        fingerprint = self.analyze(profile.character_id, cleaned)
        return self._finalize_directive(
            profile,
            cleaned,
            self._rule_based_decision(profile, fingerprint, cleaned, previous),
            previous=previous,
            state_id=self.cache.new_state_id(),
            decided_by="rule_preview",
            key_label=None,
            model=None,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    async def direct(
        self,
        character_id: str,
        question: str,
        *,
        previous_context_topic_key: str | None = None,
        avoid_keys: Iterable[int] = (),
    ) -> SceneDirective:
        """Đạo diễn bối cảnh: (câu hỏi nêu thực thể đã biết → tra cache O(1)) → gọi AI chọn mã → code chốt luật."""
        profile = get_character(character_id)
        cleaned = self._clean_question(question)
        previous = clean_topic_key(previous_context_topic_key) or None
        started = time.perf_counter()
        fingerprint = self.analyze(profile.character_id, cleaned)
        if fingerprint.entities:
            lookup = self.cache.lookup(fingerprint)
            if lookup.state is not None and lookup.state.context_topic_key:
                state = lookup.state
                return dataclasses.replace(
                    state,
                    question=cleaned,
                    decided_by="cache",
                    cache_hit=True,
                    cache_match=lookup.decision.match_type,
                    cache_similarity=round(lookup.decision.similarity, 4),
                    key_label=None,
                    model=None,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    previous_context_topic_key=previous,
                    topic_changed=state.context_topic_key != previous,
                    beyond_era=state.beyond_era or detect_era_boundary(profile.character_id, cleaned) is not None,
                )

        state_id = self.cache.new_state_id()
        try:
            result = await self._pool.generate(
                self.models,
                self.build_scene_payload(profile.character_id, cleaned, previous),
                tag=f"director:scene:{profile.character_id}",
                avoid_keys=avoid_keys,
                request_timeout_s=self.request_timeout_s,
            )
            directive = self._finalize_directive(
                profile,
                cleaned,
                _parse_json_object(result.text),
                previous=previous,
                state_id=state_id,
                decided_by="llm",
                key_label=result.key_label,
                model=result.model,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except (KeyPoolError, ValueError) as exc:
            return self._finalize_directive(
                profile,
                cleaned,
                self._rule_based_decision(profile, fingerprint, cleaned, previous),
                previous=previous,
                state_id=state_id,
                decided_by="rule_fallback",
                key_label=None,
                model=None,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                reason_suffix=f" (AI Đạo diễn không khả dụng: {type(exc).__name__}: {exc})",
            )
        self._remember_topic(directive)
        if fingerprint.entities:
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
    async def run_scene(
        self,
        actor: ActorAgent,
        character_id: str,
        question: str,
        *,
        previous_context_topic_key: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Một lượt hỏi–đáp: AI Nhân vật (stream) và AI Đạo diễn chạy song song, rồi storyboard.

        ``previous_context_topic_key`` do frontend gửi kèm mỗi request (backend không giữ trạng thái phiên).
        """
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
                directive = await self.direct(
                    profile.character_id, cleaned, previous_context_topic_key=previous_context_topic_key
                )
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
