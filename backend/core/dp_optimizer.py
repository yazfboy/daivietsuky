"""Đại Việt Sử Ký — Phase 1 — Tối ưu bằng Quy hoạch động (Dynamic Programming).

Thuật toán 1 — ``dp_scene_segmentation`` (chia câu trả lời dài thành các phân cảnh)
-------------------------------------------------------------------------------
Câu trả lời được tách thành n đơn vị (câu; câu quá dài thì tách theo mệnh đề / cửa sổ từ).
Gọi T(j, i) là thời lượng nói của các đơn vị j..i-1 (số âm tiết × ms/âm tiết + quãng nghỉ).

    DP[0] = 0
    DP[i] = min_{max(0, i-W) <= j < i, (j, i) hợp lệ} DP[j] + cost(j, i)

    cost(j, i) = w_len · ((T(j,i) − T*) / T*)²                 # gần độ dài cảnh mục tiêu T*
               + w_short · ((T_min − T(j,i)) / T_min)²          # (mềm) phạt cảnh quá ngắn
               + w_coh · Σ_{k=j}^{i-2} (1 − sim(u_k, u_{k+1}))  # cảnh phải liền mạch thị giác
               + w_cut · phạt_điểm_cắt(i)                       # cắt ở cuối câu thì rẻ nhất
               + c_scene                                        # chi phí mỗi lần chuyển cảnh

(j, i) hợp lệ khi T(j, i) <= T_max (một đơn vị đứng riêng luôn hợp lệ). Dùng tổng tiền tố cho
thời lượng và độ “lệch chủ đề” nên mỗi cost(j, i) là O(1) → tổng O(n·W) thời gian, O(n) bộ nhớ,
truy vết nghiệm bằng mảng back-pointer.

Thuật toán 2 — ``dp_visual_state_cache`` (tái sử dụng trạng thái cảnh)
---------------------------------------------------------------------
* Mỗi trạng thái cảnh được lưu trong bảng băm theo chữ ký (nhân vật, scene_mode, context_key):
  câu hỏi mới có chữ ký trùng → trả về trạng thái cũ bằng một phép tra băm O(1).
* Nếu không suy ra được chữ ký chắc chắn, chuỗi token ngữ cảnh của câu hỏi được so khớp với
  tối đa K anchor gần nhất của cùng nhân vật bằng DP edit distance có trọng số
  (Levenshtein / sequence alignment):

      E[i][0] = Σ w(a_1..a_i),   E[0][j] = Σ w(b_1..b_j)
      E[i][j] = min(E[i−1][j] + w(a_i), E[i][j−1] + w(b_j), E[i−1][j−1] + sub(a_i, b_j))
      sim = 1 − E[m][n] / max(W_a, W_b)

  Token thực thể lịch sử (Bạch Đằng, Chi Lăng...) nặng ×3 nên đổi địa danh là đổi bối cảnh.
  K và độ dài chuỗi L bị chặn bởi hằng số → chi phí O(K·L²) = O(1) theo kích thước cache;
  khi trúng, trạng thái được lấy ra bằng tham chiếu băm O(1), không gọi lại AI Đạo diễn.
"""

from __future__ import annotations

import itertools
import math
import re
import threading
import unicodedata
from collections import OrderedDict, deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

__all__ = [
    "DPOptimizer",
    "DEFAULT_MS_PER_SYLLABLE",
    "HOMELAND_CONTEXT_KEY",
    "INTENT_ACTION",
    "INTENT_GENERAL",
    "INTENT_TALK",
    "PAUSE_MS",
    "SCENE_BACKGROUND_ACTION",
    "SCENE_FOREGROUND_TALKING",
    "SCENE_MODES",
    "VISUAL_CATEGORIES",
    "AlignmentResult",
    "CacheAnchor",
    "CacheDecision",
    "CacheLookup",
    "ContextAnalyzer",
    "ContextEntity",
    "ContextFingerprint",
    "ScanToken",
    "SceneSegment",
    "SegmentationConfig",
    "SegmentationResult",
    "TextUnit",
    "VisualStateCache",
    "count_syllables",
    "dp_scene_segmentation",
    "dp_visual_state_cache",
    "dp_weighted_edit_distance",
    "has_vietnamese_diacritics",
    "is_sentence_punct",
    "normalize_text",
    "scan_text",
    "segmentation_cost",
    "split_text_units",
    "strip_diacritics",
    "unit_similarity",
    "vietnamese_number_syllables",
]

# --------------------------------------------------------------------------------------
# Hằng số dùng chung (AI Nhân vật dùng cùng nhịp nói để đồng bộ cử động miệng)
# --------------------------------------------------------------------------------------
SCENE_FOREGROUND_TALKING = "FOREGROUND_TALKING"
SCENE_BACKGROUND_ACTION = "BACKGROUND_ACTION"
SCENE_MODES = (SCENE_FOREGROUND_TALKING, SCENE_BACKGROUND_ACTION)

INTENT_TALK = "TALK"
INTENT_ACTION = "ACTION"
INTENT_GENERAL = "GENERAL"
SCENE_MODE_TO_INTENT = {SCENE_FOREGROUND_TALKING: INTENT_TALK, SCENE_BACKGROUND_ACTION: INTENT_ACTION}

HOMELAND_CONTEXT_KEY = "homeland"

DEFAULT_MS_PER_SYLLABLE = 230
PAUSE_MS: dict[str, int] = {"sentence": 380, "paragraph": 450, "clause": 160, "soft": 0, "end": 0}
_BOUNDARY_STRENGTH = {"soft": 0, "clause": 1, "sentence": 2, "paragraph": 3, "end": 4}
_SENTENCE_PUNCT = frozenset(".!?…")

# --------------------------------------------------------------------------------------
# Từ vựng tiếng Việt
# --------------------------------------------------------------------------------------
TALK_CUES: tuple[str, ...] = (
    "sinh năm", "năm sinh", "sinh ra", "sinh ở", "sinh tại", "ra đời", "chào đời", "ngày sinh",
    "mất năm", "năm mất", "mất ở", "mất tại", "mất lúc", "mất khi", "mất vào", "qua đời", "từ trần",
    "tạ thế", "băng hà", "hưởng thọ", "hưởng dương", "ngày mất", "ngày giỗ", "bao nhiêu tuổi",
    "mấy tuổi", "tuổi thơ", "thời niên thiếu", "quê", "quê quán", "quê hương", "nguyên quán",
    "người ở đâu", "người làng", "gia đình", "cha", "mẹ", "thân phụ", "thân mẫu", "vợ", "con cái",
    "các con", "anh em", "dòng họ", "dòng dõi", "tổ tiên", "tên thật", "tên húy", "tên hiệu", "tên tự",
    "biệt hiệu", "hiệu là", "tước hiệu", "danh hiệu", "tiểu sử", "thân thế", "cuộc đời", "tính cách",
    "sở thích", "ước mơ", "tâm sự", "cảm nghĩ", "suy nghĩ", "tư tưởng", "quan niệm", "lời khuyên",
    "lời dạy", "bài học", "nhắn nhủ", "di chúc", "trăng trối", "lăng mộ", "phần mộ", "đền thờ", "thờ ở",
    "an táng", "di tích",
)
ACTION_CUES: tuple[str, ...] = (
    "trận", "trận đánh", "trận chiến", "chiến trận", "đánh", "đánh trận", "đánh giặc", "đánh bại",
    "đánh tan", "đánh thắng", "đánh đuổi", "đánh úp", "chiến dịch", "chiến đấu", "giao chiến",
    "giao tranh", "tác chiến", "thủy chiến", "kháng chiến", "khởi nghĩa", "chiến thắng", "tấn công",
    "phản công", "tiến công", "tổng tiến công", "tiến quân", "hành quân", "xuất quân", "kéo quân",
    "dẫn quân", "cầm quân", "điều quân", "duyệt binh", "luyện quân", "chỉ huy", "mai phục", "phục binh",
    "đại phá", "phá tan", "vây thành", "công thành", "hạ thành", "cọc gỗ", "bãi cọc", "đóng cọc",
    "dùng kế", "bày kế", "nghi binh", "rút lui", "truy kích", "lên ngôi", "đăng quang", "xưng đế",
    "viết hịch", "soạn hịch", "thảo hịch", "đọc hịch", "hội thề", "diễn ra thế nào",
    "diễn ra như thế nào", "diễn ra ra sao", "diễn biến",
)
CUE_NEUTRALIZERS: tuple[str, ...] = (
    "đánh giá", "đánh đổi", "đánh thức", "đánh dấu", "đánh vần", "đánh số", "đánh máy",
)
STOPWORDS: frozenset[str] = frozenset(
    {
        "ông", "bà", "ngài", "người", "ngươi", "ta", "tôi", "chúng", "các", "những", "mọi", "của", "và",
        "với", "cùng", "là", "thì", "mà", "có", "không", "chưa", "được", "bị", "đã", "đang", "sẽ", "vừa",
        "này", "kia", "đó", "ấy", "nọ", "nào", "gì", "sao", "thế", "như", "bao", "nhiêu", "mấy", "ở", "đâu",
        "ra", "vào", "lên", "xuống", "cho", "hãy", "xin", "kể", "nói", "biết", "về", "trong", "ngoài",
        "khi", "lúc", "một", "rồi", "nữa", "lại", "cũng", "vậy", "à", "ạ", "nhé", "nhỉ", "hả", "ư", "hỏi",
        "thưa", "vì", "nên", "để", "do", "tại", "từ", "đến", "tới", "theo", "trên", "dưới", "hơn", "rất",
        "lắm", "quá", "điều", "việc", "cái", "chiếc", "sự",
    }
)
VISUAL_CUES: dict[str, tuple[str, ...]] = {
    "naval": (
        "sông", "biển", "cửa biển", "cửa sông", "thuyền", "chiến thuyền", "thuyền chiến", "thuyền nhẹ",
        "mái chèo", "cọc", "cọc gỗ", "bãi cọc", "thủy triều", "nước triều", "triều lên", "triều rút",
        "bến", "thủy quân", "thủy binh", "đầm", "lạch", "rạch",
    ),
    "land_battle": (
        "voi", "voi chiến", "ngựa", "kỵ binh", "bộ binh", "đồn", "thành", "ải", "núi", "đèo", "rừng", "gò",
        "giáo", "gươm", "kiếm", "cung", "nỏ", "khiên", "mộc", "hỏa hổ", "đại bác", "pháo", "súng",
    ),
    "strategy": (
        "kế", "mưu", "mai phục", "phục binh", "nhử", "dụ địch", "nghi binh", "bàn bạc", "hội nghị", "lệnh",
        "quân lệnh", "hịch", "binh thư", "bản đồ", "tướng sĩ", "chỉ huy", "điều binh", "kế sách", "thế trận",
    ),
    "combat": (
        "đánh", "giao chiến", "giao tranh", "tấn công", "xông", "xông lên", "phá", "chém", "bắn", "vây",
        "truy kích", "đánh úp", "đánh ập", "tiêu diệt", "bắt sống", "tan tác", "đại phá", "hỏa công", "đốt",
    ),
    "victory": (
        "thắng", "chiến thắng", "đại thắng", "khải hoàn", "thái bình", "giải phóng", "tháo chạy",
        "rút chạy", "bỏ chạy", "đầu hàng", "xin hàng", "rút quân",
    ),
    "homeland": (
        "quê", "làng", "hương", "phủ", "ấp", "ruộng", "đồng lúa", "cánh đồng", "lũy tre", "mái đình",
        "con đê", "gia đình", "cha", "mẹ", "tuổi thơ", "sinh ra", "lớn lên", "dòng họ",
    ),
    "court": (
        "vua", "triều", "triều đình", "điện", "cung", "ngai", "lên ngôi", "chiếu", "kinh đô", "kinh thành",
        "thượng hoàng", "hoàng đế", "quan", "phong tước", "tước",
    ),
    "letters": ("thơ", "văn", "bút", "mực", "sách", "cáo", "viết", "soạn", "chữ", "học", "thi cử", "đỗ", "trước tác"),
    "legacy": (
        "mất", "qua đời", "từ trần", "tạ thế", "đền", "thờ", "giỗ", "hậu thế", "muôn đời", "tưởng nhớ",
        "lăng", "mộ", "tôn vinh", "ghi nhớ",
    ),
}
VISUAL_CATEGORIES: tuple[str, ...] = tuple(VISUAL_CUES)

# --------------------------------------------------------------------------------------
# Tiện ích văn bản tiếng Việt
# --------------------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+")
_SCAN_RE = re.compile(
    r"(?P<nl>[ \t\r\f\v]*\n\s*)"
    r"|(?P<ws>[ \t\r\f\v]+)"
    r"|(?P<punct>[,;:.!?…]+[\"'”’»)\]]*)"
    r"|(?P<word>[^\s,;:.!?…]+)"
)


def normalize_text(text: str) -> str:
    """NFC + chữ thường + gộp khoảng trắng."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip().lower()


def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt (đ → d) để so khớp khi người dùng gõ không dấu."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", stripped.replace("đ", "d").replace("Đ", "D"))


def has_vietnamese_diacritics(text: str) -> bool:
    return strip_diacritics(text) != text


def _group_syllables(group: int, full: bool) -> int:
    hundreds, tens, units = group // 100, (group // 10) % 10, group % 10
    count = 0
    if hundreds or full:
        count += 2  # "hai trăm" / "không trăm"
    if tens == 0:
        if units:
            count += 2 if (hundreds or full) else 1  # "linh năm" / "năm"
    elif tens == 1:
        count += 1 + (1 if units else 0)  # "mười" (+ "lăm")
    else:
        count += 2 + (1 if units else 0)  # "tám mươi" (+ "tám")
    return count


def vietnamese_number_syllables(token: str) -> int:
    """Số âm tiết khi đọc một số bằng tiếng Việt (1288 → 'một nghìn hai trăm tám mươi tám' = 7)."""
    digits = re.sub(r"\D", "", token)
    if not digits:
        return 0
    if len(digits) > 12:
        return len(digits)
    value = int(digits)
    if value == 0:
        return 1
    groups: list[int] = []
    while value:
        groups.append(value % 1000)
        value //= 1000
    total = 0
    for position in range(len(groups) - 1, -1, -1):
        group = groups[position]
        if group == 0:
            continue
        total += _group_syllables(group, full=position != len(groups) - 1)
        if position > 0:
            total += 1  # nghìn / triệu / tỷ
    return total


def count_syllables(text: str) -> int:
    """Đếm âm tiết đọc thành tiếng (tiếng Việt viết tách âm tiết bằng khoảng trắng; số được đọc ra chữ)."""
    total = 0
    for token in _TOKEN_RE.findall(unicodedata.normalize("NFC", text or "")):
        total += vietnamese_number_syllables(token) if token[0].isdigit() else 1
    return total


@dataclass(frozen=True, slots=True)
class ScanToken:
    kind: str  # "nl" | "ws" | "punct" | "word"
    start: int
    end: int
    text: str


def scan_text(text: str, offset: int = 0) -> Iterator[ScanToken]:
    """Tách văn bản thành các token xuống dòng / khoảng trắng / dấu câu / từ — không mất ký tự nào."""
    for match in _SCAN_RE.finditer(text):
        kind = match.lastgroup or "word"
        yield ScanToken(kind, match.start() + offset, match.end() + offset, match.group(0))


def is_sentence_punct(punct: str) -> bool:
    return any(ch in _SENTENCE_PUNCT for ch in punct)


def _compile_phrases(phrases: Iterable[str]) -> re.Pattern[str] | None:
    items = sorted({normalize_text(p) for p in phrases if p and p.strip()}, key=len, reverse=True)
    if not items:
        return None
    return re.compile(r"(?<![^\W_])(?:" + "|".join(re.escape(item) for item in items) + r")(?![^\W_])")


def _compile_pair(phrases: Sequence[str]) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """(mẫu có dấu, mẫu không dấu)."""
    return _compile_phrases(phrases), _compile_phrases(strip_diacritics(normalize_text(p)) for p in phrases)


# --------------------------------------------------------------------------------------
# Phân tích ngữ cảnh câu hỏi (dùng cho Thuật toán 2)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ContextEntity:
    """Thực thể bối cảnh của một nhân vật: quê hương, trận đánh, sự kiện."""

    entity_id: str
    kind: str  # "homeland" | "battle" | "event"
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextFingerprint:
    character_id: str
    question: str
    intent: str  # TALK | ACTION | GENERAL
    entities: tuple[str, ...]  # theo thứ tự ưu tiên trong hồ sơ nhân vật
    tokens: tuple[str, ...]
    weights: tuple[float, ...]
    signature: tuple[str, str, str] | None


class ContextAnalyzer:
    """Suy ra ý định (nói chuyện / hành động), thực thể và chuỗi token có trọng số của câu hỏi."""

    MAX_TOKENS = 24
    ENTITY_WEIGHT = 3.0
    CUE_WEIGHT = 1.5
    BASE_WEIGHT = 1.0

    def __init__(self, entities: Sequence[ContextEntity], *, person_names: Sequence[str] = ()) -> None:
        self._entities = tuple(entities)
        self._entity_rank = {entity.entity_id: rank for rank, entity in enumerate(self._entities)}
        self._entity_kind = {entity.entity_id: entity.kind for entity in self._entities}
        self._entity_patterns = [(entity, _compile_pair(entity.aliases)) for entity in self._entities]
        self._names = _compile_pair(tuple(person_names))
        self._talk = _compile_pair(TALK_CUES)
        self._action = _compile_pair(ACTION_CUES)
        self._neutral = _compile_pair(CUE_NEUTRALIZERS)
        self._plain_stopwords = frozenset(strip_diacritics(word) for word in STOPWORDS)

    @property
    def entities(self) -> tuple[ContextEntity, ...]:
        return self._entities

    def analyze(self, character_id: str, question: str) -> ContextFingerprint:
        text = normalize_text(question)
        plain = not has_vietnamese_diacritics(text)
        pick = 1 if plain else 0
        work = text
        for pair in (self._names, self._neutral):
            pattern = pair[pick]
            if pattern is not None:
                work = pattern.sub(" ", work)

        entity_spans: list[tuple[int, int]] = []
        found: dict[str, int] = {}
        for entity, patterns in self._entity_patterns:
            pattern = patterns[pick]
            if pattern is None:
                continue
            for match in pattern.finditer(work):
                entity_spans.append(match.span())
                found[entity.entity_id] = self._entity_rank[entity.entity_id]
        entities = tuple(sorted(found, key=found.__getitem__))

        cue_spans: list[tuple[int, int]] = []
        talk_hits = action_hits = 0
        for pair, is_action in ((self._talk, False), (self._action, True)):
            pattern = pair[pick]
            if pattern is None:
                continue
            for match in pattern.finditer(work):
                cue_spans.append(match.span())
                if is_action:
                    action_hits += 1
                else:
                    talk_hits += 1

        kinds = {self._entity_kind[eid] for eid in entities}
        event_entities = [eid for eid in entities if self._entity_kind[eid] in ("battle", "event")]
        if event_entities or action_hits:
            intent = INTENT_ACTION
        elif talk_hits or "homeland" in kinds:
            intent = INTENT_TALK
        else:
            intent = INTENT_GENERAL

        signature: tuple[str, str, str] | None = None
        if intent == INTENT_TALK:
            signature = (character_id, SCENE_FOREGROUND_TALKING, HOMELAND_CONTEXT_KEY)
        elif intent == INTENT_ACTION and event_entities:
            signature = (character_id, SCENE_BACKGROUND_ACTION, event_entities[0])

        tokens: list[str] = []
        weights: list[float] = []
        stopwords = self._plain_stopwords if plain else STOPWORDS
        for match in _TOKEN_RE.finditer(work):
            token = match.group(0)
            if token in stopwords:
                continue
            start, end = match.span()
            if any(a < end and start < b for a, b in entity_spans):
                weight = self.ENTITY_WEIGHT
            elif any(a < end and start < b for a, b in cue_spans):
                weight = self.CUE_WEIGHT
            else:
                weight = self.BASE_WEIGHT
            tokens.append(token)
            weights.append(weight)
            if len(tokens) >= self.MAX_TOKENS:
                break

        return ContextFingerprint(
            character_id=character_id,
            question=question,
            intent=intent,
            entities=entities,
            tokens=tuple(tokens),
            weights=tuple(weights),
            signature=signature,
        )


# --------------------------------------------------------------------------------------
# Thuật toán 2 — DP edit distance có trọng số + cache trạng thái cảnh O(1)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AlignmentResult:
    distance: float
    similarity: float
    weight_a: float
    weight_b: float


def dp_weighted_edit_distance(
    a_tokens: Sequence[str],
    a_weights: Sequence[float],
    b_tokens: Sequence[str],
    b_weights: Sequence[float],
) -> AlignmentResult:
    """Levenshtein có trọng số (xoá/chèn = w(token), thay = max(w_a, w_b)); bộ nhớ O(min) hai hàng.

    Hai token khớp nếu giống hệt, hoặc một bên gõ không dấu trùng với dạng bỏ dấu của bên kia.
    """
    if len(a_tokens) != len(a_weights) or len(b_tokens) != len(b_weights):
        raise ValueError("Số token và số trọng số phải bằng nhau.")
    weight_a, weight_b = float(sum(a_weights)), float(sum(b_weights))
    if not a_tokens and not b_tokens:
        return AlignmentResult(0.0, 1.0, 0.0, 0.0)
    if not a_tokens or not b_tokens:
        return AlignmentResult(max(weight_a, weight_b), 0.0, weight_a, weight_b)

    a_plain = [strip_diacritics(t) for t in a_tokens]
    b_plain = [strip_diacritics(t) for t in b_tokens]
    a_ascii = [t.isascii() for t in a_tokens]
    b_ascii = [t.isascii() for t in b_tokens]

    previous = [0.0] * (len(b_tokens) + 1)
    for j in range(1, len(b_tokens) + 1):
        previous[j] = previous[j - 1] + b_weights[j - 1]
    for i in range(1, len(a_tokens) + 1):
        wa = a_weights[i - 1]
        token_a = a_tokens[i - 1]
        current = [previous[0] + wa] + [0.0] * len(b_tokens)
        for j in range(1, len(b_tokens) + 1):
            wb = b_weights[j - 1]
            token_b = b_tokens[j - 1]
            same = (
                token_a == token_b
                or (a_ascii[i - 1] and token_a == b_plain[j - 1])
                or (b_ascii[j - 1] and token_b == a_plain[i - 1])
            )
            substitution = 0.0 if same else max(wa, wb)
            current[j] = min(previous[j] + wa, current[j - 1] + wb, previous[j - 1] + substitution)
        previous = current
    distance = previous[-1]
    similarity = max(0.0, 1.0 - distance / max(weight_a, weight_b))
    return AlignmentResult(distance, similarity, weight_a, weight_b)


@dataclass(frozen=True, slots=True)
class CacheAnchor:
    state_id: str
    fingerprint: ContextFingerprint
    scene_intent: str  # ý định ứng với scene_mode mà AI Đạo diễn đã chốt


@dataclass(frozen=True, slots=True)
class CacheDecision:
    hit: bool
    state_id: str | None
    similarity: float
    match_type: str  # "signature" | "alignment" | "miss"
    compared: int


def dp_visual_state_cache(
    query: ContextFingerprint,
    anchors: Sequence[CacheAnchor],
    *,
    threshold: float = 0.6,
) -> CacheDecision:
    """Thuật toán 2: tìm trạng thái cảnh tái sử dụng được bằng DP alignment với ≤ K anchor.

    Anchor bị loại nếu khác nhân vật, khác ý định (nói chuyện ↔ hành động) hoặc cả hai bên đều nêu
    thực thể nhưng không chung thực thể nào (ví dụ Bạch Đằng ↔ Chi Lăng).
    """
    best_similarity = 0.0
    best_state: str | None = None
    compared = 0
    for anchor in anchors:
        anchor_fp = anchor.fingerprint
        if anchor_fp.character_id != query.character_id:
            continue
        if query.intent != INTENT_GENERAL and query.intent != anchor.scene_intent:
            continue
        if query.entities and anchor_fp.entities and set(query.entities).isdisjoint(anchor_fp.entities):
            continue
        compared += 1
        result = dp_weighted_edit_distance(query.tokens, query.weights, anchor_fp.tokens, anchor_fp.weights)
        if result.similarity > best_similarity:
            best_similarity, best_state = result.similarity, anchor.state_id
    if best_state is not None and best_similarity >= threshold:
        return CacheDecision(True, best_state, best_similarity, "alignment", compared)
    return CacheDecision(False, None, best_similarity, "miss", compared)


T = TypeVar("T")


@dataclass(frozen=True)
class CacheLookup(Generic[T]):
    state: T | None
    decision: CacheDecision
    fingerprint: ContextFingerprint


class VisualStateCache(Generic[T]):
    """Cache trạng thái cảnh: bảng băm chữ ký O(1) + K anchor/nhân vật cho DP alignment, LRU."""

    def __init__(
        self,
        *,
        capacity: int = 128,
        anchors_per_character: int = 8,
        similarity_threshold: float = 0.6,
    ) -> None:
        if capacity < 1 or anchors_per_character < 1:
            raise ValueError("capacity và anchors_per_character phải >= 1")
        if not 0.0 < similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold phải thuộc (0, 1]")
        self._capacity = capacity
        self._anchor_limit = anchors_per_character
        self._threshold = similarity_threshold
        self._lock = threading.Lock()
        self._states: OrderedDict[str, T] = OrderedDict()
        self._signature_index: dict[tuple[str, str, str], str] = {}
        self._signatures_of: dict[str, set[tuple[str, str, str]]] = {}
        self._anchors: dict[str, deque[CacheAnchor]] = {}
        self._ids = itertools.count(1)
        self.hits = 0
        self.misses = 0

    @property
    def similarity_threshold(self) -> float:
        return self._threshold

    def __len__(self) -> int:
        with self._lock:
            return len(self._states)

    def new_state_id(self) -> str:
        with self._lock:
            return f"vs-{next(self._ids):06d}"

    def get(self, state_id: str) -> T | None:
        with self._lock:
            return self._states.get(state_id)

    def lookup(self, fingerprint: ContextFingerprint) -> CacheLookup[T]:
        with self._lock:
            if fingerprint.signature is not None:
                state_id = self._signature_index.get(fingerprint.signature)
                if state_id is not None and state_id in self._states:
                    self._states.move_to_end(state_id)
                    self.hits += 1
                    decision = CacheDecision(True, state_id, 1.0, "signature", 0)
                    return CacheLookup(self._states[state_id], decision, fingerprint)
            anchors = tuple(self._anchors.get(fingerprint.character_id, ()))
        decision = dp_visual_state_cache(fingerprint, anchors, threshold=self._threshold)
        with self._lock:
            if decision.hit and decision.state_id in self._states:
                self._states.move_to_end(decision.state_id)
                self.hits += 1
                return CacheLookup(self._states[decision.state_id], decision, fingerprint)
            self.misses += 1
            miss = CacheDecision(False, None, decision.similarity, "miss", decision.compared)
            return CacheLookup(None, miss, fingerprint)

    def store(
        self,
        fingerprint: ContextFingerprint,
        state: T,
        *,
        state_id: str,
        scene_mode: str,
        context_key: str,
    ) -> None:
        if scene_mode not in SCENE_MODE_TO_INTENT:
            raise ValueError(f"scene_mode không hợp lệ: {scene_mode}")
        signature = (fingerprint.character_id, scene_mode, context_key)
        with self._lock:
            self._states[state_id] = state
            self._states.move_to_end(state_id)
            previous_owner = self._signature_index.get(signature)
            if previous_owner is not None and previous_owner != state_id:
                self._signatures_of.get(previous_owner, set()).discard(signature)
            self._signature_index[signature] = state_id
            self._signatures_of.setdefault(state_id, set()).add(signature)
            anchors = self._anchors.setdefault(fingerprint.character_id, deque(maxlen=self._anchor_limit))
            anchors.appendleft(CacheAnchor(state_id, fingerprint, SCENE_MODE_TO_INTENT[scene_mode]))
            while len(self._states) > self._capacity:
                evicted_id, _ = self._states.popitem(last=False)
                for evicted_signature in self._signatures_of.pop(evicted_id, set()):
                    if self._signature_index.get(evicted_signature) == evicted_id:
                        del self._signature_index[evicted_signature]
                for character_anchors in self._anchors.values():
                    stale = [anchor for anchor in character_anchors if anchor.state_id == evicted_id]
                    for anchor in stale:
                        character_anchors.remove(anchor)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"states": len(self._states), "hits": self.hits, "misses": self.misses}


# --------------------------------------------------------------------------------------
# Thuật toán 1 — DP phân cảnh
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SegmentationConfig:
    ms_per_syllable: int = DEFAULT_MS_PER_SYLLABLE
    target_ms: int = 7000
    min_ms: int = 3500
    max_ms: int = 13000
    max_units_per_segment: int = 8
    w_length: float = 1.0
    w_short: float = 4.0
    w_coherence: float = 0.8
    w_cut: float = 0.8
    clause_cut_penalty: float = 0.6
    soft_cut_penalty: float = 1.0
    scene_cost: float = 0.35

    def __post_init__(self) -> None:
        if self.ms_per_syllable <= 0 or self.target_ms <= 0 or self.min_ms <= 0:
            raise ValueError("Các tham số thời lượng phải > 0")
        if not self.min_ms <= self.target_ms <= self.max_ms:
            raise ValueError("Cần min_ms <= target_ms <= max_ms")
        if self.max_units_per_segment < 1:
            raise ValueError("max_units_per_segment phải >= 1")


@dataclass(frozen=True, slots=True)
class TextUnit:
    index: int
    text: str
    char_start: int
    char_end: int
    syllables: int
    duration_ms: int
    boundary: str  # "sentence" | "paragraph" | "clause" | "soft" | "end"
    visual_vector: tuple[float, ...]
    keywords: frozenset[str]


@dataclass(frozen=True, slots=True)
class SceneSegment:
    index: int
    unit_start: int
    unit_end: int
    char_start: int
    char_end: int
    text: str
    syllables: int
    start_ms: int
    duration_ms: int
    dominant_visual: str | None
    cost: float


@dataclass(frozen=True, slots=True)
class SegmentationResult:
    segments: tuple[SceneSegment, ...]
    units: tuple[TextUnit, ...]
    total_cost: float
    dp_table: tuple[float, ...]
    config: SegmentationConfig


_VISUAL_PATTERNS = {category: _compile_pair(phrases) for category, phrases in VISUAL_CUES.items()}


def _visual_vector(text: str) -> tuple[float, ...]:
    normalized = normalize_text(text)
    pick = 0 if has_vietnamese_diacritics(normalized) else 1
    vector: list[float] = []
    for category in VISUAL_CATEGORIES:
        pattern = _VISUAL_PATTERNS[category][pick]
        vector.append(float(len(pattern.findall(normalized))) if pattern is not None else 0.0)
    return tuple(vector)


def _keywords(text: str) -> frozenset[str]:
    return frozenset(token for token in _TOKEN_RE.findall(normalize_text(text)) if token not in STOPWORDS)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def unit_similarity(a: TextUnit, b: TextUnit) -> float:
    """Độ tương đồng thị giác giữa hai đơn vị liền kề ∈ [0, 1].

    0.7 · cosine(vector tín hiệu thị giác) + 0.3 · Jaccard(từ khoá). Đơn vị không có tín hiệu thị giác
    được coi là trung tính (0.5) chứ không phải đổi chủ đề.
    """
    if any(a.visual_vector) and any(b.visual_vector):
        visual = _cosine(a.visual_vector, b.visual_vector)
    else:
        visual = 0.5
    union = a.keywords | b.keywords
    lexical = len(a.keywords & b.keywords) / len(union) if union else 0.0
    return 0.7 * visual + 0.3 * lexical


def _stronger(first: str, second: str) -> str:
    return first if _BOUNDARY_STRENGTH.get(first, 0) >= _BOUNDARY_STRENGTH.get(second, 0) else second


def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    tokens = list(scan_text(text))
    spans: list[tuple[int, int, str]] = []
    start = 0
    for position, token in enumerate(tokens):
        if token.kind == "nl":
            if token.end > start:
                spans.append((start, token.end, "paragraph"))
                start = token.end
        elif token.kind == "punct" and is_sentence_punct(token.text):
            following = tokens[position + 1] if position + 1 < len(tokens) else None
            if following is None:
                spans.append((start, token.end, "sentence"))
                start = token.end
            elif following.kind == "ws":
                spans.append((start, following.end, "sentence"))
                start = following.end
    if start < len(text):
        spans.append((start, len(text), "end"))
    return spans


def _clause_spans(text: str, start: int, end: int, boundary: str) -> list[tuple[int, int, str]]:
    tokens = list(scan_text(text[start:end], offset=start))
    spans: list[tuple[int, int, str]] = []
    cursor = start
    for position, token in enumerate(tokens):
        if token.kind == "punct" and not is_sentence_punct(token.text):
            following = tokens[position + 1] if position + 1 < len(tokens) else None
            if following is not None and following.kind in ("ws", "nl") and following.end < end:
                spans.append((cursor, following.end, "clause"))
                cursor = following.end
    spans.append((cursor, end, boundary))
    return [span for span in spans if span[1] > span[0]]


def _window_spans(text: str, start: int, end: int, boundary: str, max_syllables: int) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    cursor = start
    syllables = 0
    for token in scan_text(text[start:end], offset=start):
        if token.kind == "word":
            syllables += count_syllables(token.text)
        elif token.kind in ("ws", "nl") and syllables >= max_syllables and token.end < end:
            spans.append((cursor, token.end, "soft"))
            cursor = token.end
            syllables = 0
    spans.append((cursor, end, boundary))
    return [span for span in spans if span[1] > span[0]]


def _merge_blank_spans(text: str, spans: Sequence[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    merged: list[list[object]] = []
    leading_start: int | None = None
    for start, end, boundary in spans:
        if text[start:end].strip():
            if leading_start is not None:
                start, leading_start = leading_start, None
            merged.append([start, end, boundary])
        elif merged:
            merged[-1][1] = end
            merged[-1][2] = _stronger(str(merged[-1][2]), boundary)
        elif leading_start is None:
            leading_start = start
    return [(int(s), int(e), str(b)) for s, e, b in merged]


def split_text_units(text: str, config: SegmentationConfig | None = None) -> tuple[TextUnit, ...]:
    """Tách câu trả lời thành đơn vị (câu → mệnh đề → cửa sổ từ); các đơn vị phủ kín văn bản gốc."""
    cfg = config or SegmentationConfig()
    if not text or not text.strip():
        return ()
    max_syllables = max(1, cfg.max_ms // cfg.ms_per_syllable)
    window_syllables = max(1, cfg.target_ms // cfg.ms_per_syllable)
    raw: list[tuple[int, int, str]] = []
    for start, end, boundary in _sentence_spans(text):
        if count_syllables(text[start:end]) <= max_syllables:
            raw.append((start, end, boundary))
            continue
        for c_start, c_end, c_boundary in _clause_spans(text, start, end, boundary):
            if count_syllables(text[c_start:c_end]) <= max_syllables:
                raw.append((c_start, c_end, c_boundary))
            else:
                raw.extend(_window_spans(text, c_start, c_end, c_boundary, window_syllables))
    units: list[TextUnit] = []
    for index, (start, end, boundary) in enumerate(_merge_blank_spans(text, raw)):
        chunk = text[start:end]
        syllables = count_syllables(chunk)
        units.append(
            TextUnit(
                index=index,
                text=chunk,
                char_start=start,
                char_end=end,
                syllables=syllables,
                duration_ms=syllables * cfg.ms_per_syllable + PAUSE_MS.get(boundary, 0),
                boundary=boundary,
                visual_vector=_visual_vector(chunk),
                keywords=_keywords(chunk),
            )
        )
    return tuple(units)


class _SegmentCostModel:
    """Tổng tiền tố để mỗi cost(j, i) tính trong O(1)."""

    def __init__(self, units: Sequence[TextUnit], config: SegmentationConfig) -> None:
        self.units = tuple(units)
        self.config = config
        self.count = len(self.units)
        self.prefix_ms = [0]
        for unit in self.units:
            self.prefix_ms.append(self.prefix_ms[-1] + unit.duration_ms)
        self.prefix_shift = [0.0]
        for left, right in zip(self.units, self.units[1:]):
            self.prefix_shift.append(self.prefix_shift[-1] + (1.0 - unit_similarity(left, right)))
        self.cut_penalty = {
            "sentence": 0.0,
            "paragraph": 0.0,
            "end": 0.0,
            "clause": config.clause_cut_penalty,
            "soft": config.soft_cut_penalty,
        }

    def duration(self, j: int, i: int) -> int:
        return self.prefix_ms[i] - self.prefix_ms[j]

    def feasible(self, j: int, i: int) -> bool:
        if i - j == 1:
            return True
        return i - j <= self.config.max_units_per_segment and self.duration(j, i) <= self.config.max_ms

    def cost(self, j: int, i: int) -> float:
        cfg = self.config
        duration = self.duration(j, i)
        length_term = cfg.w_length * ((duration - cfg.target_ms) / cfg.target_ms) ** 2
        short_term = 0.0
        if duration < cfg.min_ms and not (j == 0 and i == self.count):
            short_term = cfg.w_short * ((cfg.min_ms - duration) / cfg.min_ms) ** 2
        coherence_term = cfg.w_coherence * (self.prefix_shift[i - 1] - self.prefix_shift[j])
        cut_term = 0.0 if i == self.count else cfg.w_cut * self.cut_penalty.get(self.units[i - 1].boundary, 1.0)
        return length_term + short_term + coherence_term + cut_term + cfg.scene_cost


def segmentation_cost(
    units: Sequence[TextUnit],
    bounds: Sequence[tuple[int, int]],
    config: SegmentationConfig | None = None,
) -> float:
    """Tổng chi phí của một cách chia bất kỳ (dùng để kiểm chứng tính tối ưu của DP)."""
    model = _SegmentCostModel(units, config or SegmentationConfig())
    expected = 0
    total = 0.0
    for j, i in bounds:
        if j != expected or i <= j:
            raise ValueError("bounds phải phủ liên tiếp các đơn vị từ 0 đến n")
        if not model.feasible(j, i):
            return math.inf
        total += model.cost(j, i)
        expected = i
    if expected != len(units):
        raise ValueError("bounds phải kết thúc tại n")
    return total


def dp_scene_segmentation(text: str, config: SegmentationConfig | None = None) -> SegmentationResult:
    """Thuật toán 1: DP[i] = min DP[j] + cost(j, i) → các điểm chuyển cảnh tối ưu."""
    cfg = config or SegmentationConfig()
    units = split_text_units(text, cfg)
    count = len(units)
    if count == 0:
        return SegmentationResult((), (), 0.0, (0.0,), cfg)
    model = _SegmentCostModel(units, cfg)
    dp = [math.inf] * (count + 1)
    back = [-1] * (count + 1)
    dp[0] = 0.0
    for i in range(1, count + 1):
        lowest = max(0, i - cfg.max_units_per_segment)
        for j in range(i - 1, lowest - 1, -1):
            if not model.feasible(j, i):
                break  # thời lượng chỉ tăng khi j giảm → các j nhỏ hơn cũng vượt T_max
            candidate = dp[j] + model.cost(j, i)
            if candidate < dp[i]:
                dp[i] = candidate
                back[i] = j
    bounds: list[tuple[int, int]] = []
    cursor = count
    while cursor > 0:
        previous = back[cursor]
        bounds.append((previous, cursor))
        cursor = previous
    bounds.reverse()

    segments: list[SceneSegment] = []
    elapsed_ms = 0
    for index, (j, i) in enumerate(bounds):
        members = units[j:i]
        totals = [sum(unit.visual_vector[k] for unit in members) for k in range(len(VISUAL_CATEGORIES))]
        peak = max(totals) if totals else 0.0
        dominant = VISUAL_CATEGORIES[totals.index(peak)] if peak > 0 else None
        duration = model.duration(j, i)
        segments.append(
            SceneSegment(
                index=index,
                unit_start=j,
                unit_end=i,
                char_start=members[0].char_start,
                char_end=members[-1].char_end,
                text=text[members[0].char_start : members[-1].char_end],
                syllables=sum(unit.syllables for unit in members),
                start_ms=elapsed_ms,
                duration_ms=duration,
                dominant_visual=dominant,
                cost=model.cost(j, i),
            )
        )
        elapsed_ms += duration
    return SegmentationResult(tuple(segments), units, dp[count], tuple(dp), cfg)


class DPOptimizer:
    """Tên gọi dùng trong đặc tả dự án: gom các thuật toán quy hoạch động của Phase 1 (cùng hàm, không bản sao)."""

    scene_segmentation = staticmethod(dp_scene_segmentation)
    visual_state_cache = staticmethod(dp_visual_state_cache)
    weighted_edit_distance = staticmethod(dp_weighted_edit_distance)
    segmentation_cost = staticmethod(segmentation_cost)
