"""Đại Việt Sử Ký — Luật lịch sử chốt bằng code (không phụ thuộc vào lời AI).

* ``HERO_EVENTS``: bảng tra cố định các trận / cuộc kháng chiến mà từng vị anh hùng **trực tiếp có mặt và chỉ huy**.
  ``scene_mode`` / ``mouth_sync_enabled`` CHỈ do bảng này quyết định:
    - ``context_topic_key`` ∈ ``HERO_EVENTS[hero_id]`` → ``BACKGROUND_ACTION`` (tắt khẩu hình, nhân vật hành động);
    - mọi trường hợp khác → ``FOREGROUND_TALKING`` (nhân vật đứng trước kể chuyện, cảnh phía sau chỉ minh họa).
  Muốn thêm key vào bảng thì nhóm duyệt rồi mới sửa; code không tự mở rộng bảng.
* ``detect_era_boundary``: phát hiện câu hỏi về xứ sở / sự vật mà sinh thời nhân vật chưa biết tới (vd. nước Nga)
  để AI Nhân vật bắt buộc mở đầu bằng lời xác định ranh giới thời đại rồi mới kể theo góc nhìn hậu thế.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..core.dp_optimizer import (
    SCENE_BACKGROUND_ACTION,
    SCENE_FOREGROUND_TALKING,
    is_valid_topic_key,
    normalize_text,
    normalize_topic_key,
    strip_diacritics,
)

__all__ = [
    "ERA_BOUNDARY_OPENING",
    "GENERAL_TOPIC_KEY",
    "HERO_EVENTS",
    "HERO_HOMELAND_TOPIC",
    "HERO_LIFETIME",
    "QUESTION_KINDS",
    "QUESTION_KIND_BIOGRAPHY",
    "QUESTION_KIND_HISTORICAL",
    "QUESTION_KIND_OFF_TOPIC",
    "EraBoundary",
    "clean_topic_key",
    "detect_era_boundary",
    "looks_biographical",
    "mouth_sync_for",
    "scene_mode_for",
]

# Bảng tra cố định — liệt kê đích danh, KHÔNG tự thêm key.
# Trần Hưng Đạo: Quốc công Tiết chế cả hai cuộc kháng chiến 1285 và 1287–1288, trực tiếp chỉ huy trận Bạch Đằng 1288.
#   Các trận do tướng khác chỉ huy (Chương Dương — Trần Quang Khải, Hàm Tử — Trần Nhật Duật, Vân Đồn — Trần Khánh Dư)
#   KHÔNG thuộc ông.
# Nguyễn Trãi: quân sư, nhà văn chính luận — không trực tiếp cầm quân trong trận nào ở bảng → luôn FOREGROUND_TALKING.
HERO_EVENTS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "tran_hung_dao": frozenset(
            {"bach_dang_1288", "khang_chien_mong_nguyen_1285", "khang_chien_mong_nguyen_1287_1288"}
        ),
        "quang_trung": frozenset({"rach_gam_xoai_mut_1785", "ngoc_hoi_dong_da_1789"}),
        "nguyen_trai": frozenset(),
    }
)

# Mã quê hương của từng nhân vật (chỉ dùng cho câu hỏi tiểu sử).
HERO_HOMELAND_TOPIC: Mapping[str, str] = MappingProxyType(
    {
        "tran_hung_dao": "que_huong_tuc_mac",
        "nguyen_trai": "que_huong_nhi_khe",
        "quang_trung": "que_huong_tay_son",
    }
)

# Năm sinh – năm mất (ước lượng theo sử liệu) để xét "nhân vật biết đến đâu".
HERO_LIFETIME: Mapping[str, tuple[int, int]] = MappingProxyType(
    {"tran_hung_dao": (1228, 1300), "nguyen_trai": (1380, 1442), "quang_trung": (1753, 1792)}
)

GENERAL_TOPIC_KEY = "chu_de_chung"  # câu hỏi chung / lạc đề → phông trung tính, không tìm ảnh

QUESTION_KIND_BIOGRAPHY = "biography"
QUESTION_KIND_HISTORICAL = "historical_topic"
QUESTION_KIND_OFF_TOPIC = "off_topic"
QUESTION_KINDS = (QUESTION_KIND_BIOGRAPHY, QUESTION_KIND_HISTORICAL, QUESTION_KIND_OFF_TOPIC)

ERA_BOUNDARY_OPENING = "Thời ta chưa biết đến xứ ấy..."


def scene_mode_for(hero_id: str, topic_key: str) -> str:
    """Luật 1 (chốt bằng code): chỉ trận mà nhân vật trực tiếp chỉ huy mới là BACKGROUND_ACTION."""
    return SCENE_BACKGROUND_ACTION if topic_key in HERO_EVENTS.get(hero_id, frozenset()) else SCENE_FOREGROUND_TALKING


def mouth_sync_for(hero_id: str, topic_key: str) -> bool:
    return scene_mode_for(hero_id, topic_key) == SCENE_FOREGROUND_TALKING


_BIOGRAPHY_MARKERS = (
    "năm sinh", "sinh năm", "sinh ra", "ra đời", "năm mất", "mất năm", "qua đời", "tạ thế", "mất khi", "bao nhiêu tuổi",
    "quê", "quê quán", "quê hương", "sinh ở", "người ở đâu", "gia đình", "cha ông", "cha của ông", "mẹ ông",
    "mẹ của ông", "vợ ông", "con ông", "tên thật", "tên hiệu", "tên húy", "tuổi thơ", "thời trẻ", "tiểu sử",
    "cuộc đời", "xuất thân", "dòng dõi",
)


def _haystack(text: str) -> str:
    return f" {strip_diacritics(normalize_text(text))} "


def looks_biographical(question: str) -> bool:
    haystack = _haystack(question)
    return any(f" {strip_diacritics(marker)} " in haystack or strip_diacritics(marker) in haystack.strip()
               for marker in _BIOGRAPHY_MARKERS)


@dataclass(frozen=True, slots=True)
class EraBoundary:
    """Một xứ sở / sự vật mà sinh thời nhân vật chưa biết tới."""

    label: str  # tên gọi tiếng Việt để nhắc AI Nhân vật, vd. "nước Nga"
    topic_key: str  # mã bối cảnh minh họa phía sau ("" = không có ảnh, dùng phông trung tính)
    topic_title: str
    wikimedia_search_query: str
    known_from: int | None  # năm Đại Việt bắt đầu biết tới; None = cả 3 nhân vật đều chưa biết
    aliases: tuple[str, ...]


_ERA_TERMS: tuple[EraBoundary, ...] = (
    EraBoundary(
        "nước Nga", "moscow_red_square", "Mát-xcơ-va (nước Nga)", "Red Square Moscow Kremlin", None,
        ("nước nga", "xứ nga", "người nga", "nga la tư", "liên xô", "liên bang nga", "russia", "moscow",
         "mát-xcơ-va", "mát xcơ va", "matxcova", "mạc tư khoa", "kremlin", "điện kremlin", "quảng trường đỏ",
         "thánh basil"),
    ),
    EraBoundary(
        "nước Mỹ (châu Mỹ)", "", "Châu Mỹ", "", None,
        ("nước mỹ", "hoa kỳ", "châu mỹ", "người mỹ", "america", "new york", "washington"),
    ),
    EraBoundary(
        "các nước châu Âu", "", "Châu Âu", "", 1600,
        ("châu âu", "nước pháp", "pháp quốc", "người pháp", "paris", "nước anh", "anh quốc", "người anh",
         "luân đôn", "london", "nước đức", "tây ban nha", "bồ đào nha", "europe"),
    ),
    EraBoundary(
        "những máy móc của đời sau", "", "Khái niệm hiện đại", "", None,
        ("máy bay", "tàu hỏa", "tàu hoả", "xe lửa", "ô tô", "xe hơi", "điện thoại", "internet", "máy tính",
         "vô tuyến", "ti vi", "tivi", "tên lửa", "bom nguyên tử", "vệ tinh", "điện thoại di động", "robot",
         "trí tuệ nhân tạo"),
    ),
)


def _contains_phrase(haystack: str, phrase: str) -> bool:
    needle = strip_diacritics(normalize_text(phrase))
    return bool(needle) and f" {needle} " in haystack.replace(",", " ").replace("?", " ").replace(".", " ")


def detect_era_boundary(hero_id: str, question: str) -> EraBoundary | None:
    """Luật 2: câu hỏi nhắc tới xứ sở / sự vật ngoài sự hiểu biết sinh thời của nhân vật."""
    lifetime = HERO_LIFETIME.get(hero_id)
    if lifetime is None:
        return None
    haystack = _haystack(question)
    for term in _ERA_TERMS:
        if term.known_from is not None and term.known_from <= lifetime[1]:
            continue
        if any(_contains_phrase(haystack, alias) for alias in term.aliases):
            return term
    return None


def clean_topic_key(value: object) -> str:
    """Chuẩn hóa một mã bối cảnh do AI / frontend gửi lên; rỗng nếu không thể làm hợp lệ."""
    key = normalize_topic_key(value)
    return key if is_valid_topic_key(key) else ""
