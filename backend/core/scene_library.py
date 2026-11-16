"""Đại Việt Sử Ký — Thư viện cảnh dựng sẵn (``backend/data/scene_library.json``).

Mỗi mục: ``context_topic_key`` → tên bối cảnh, năm, loại (battle / event / homeland / place), bí danh, từ khóa tìm
ảnh Wikimedia, và ảnh do nhóm tự chọn + duyệt (``image``, ``author``, ``license``, ``license_url``, ``source_url``).
Chỉ mục có đủ file ảnh trên đĩa + tác giả + giấy phép mới được dùng làm ảnh nền; còn lại hệ thống tìm Wikimedia,
không được thì dùng phông trung tính. Code KHÔNG tự sinh hay tự tải ảnh vào thư viện.

* ``neutral_only: true`` — nhóm chốt dùng phông trung tính (giấy dó + tên địa danh) cho key này: KHÔNG dùng ảnh
  thư viện và KHÔNG tìm Wikimedia (vd. ảnh cũ sai sử, nguồn gốc chưa xác minh, chưa có ảnh được duyệt).
* ``caption`` — chú thích hiện cạnh dòng ghi nguồn, vd. "Hoàng thành Thăng Long ngày nay" khi ảnh là công trình dựng
  sau sự kiện.
* ``foreground_only: true`` — ảnh chụp (vd. hiện trạng di tích ngày nay) chỉ hợp làm nền khi nhân vật đứng tiền cảnh
  trò chuyện (FOREGROUND_TALKING). Ở BACKGROUND_ACTION bỏ ảnh này: dùng ảnh tĩnh AI đã duyệt (``ai_still``) nếu có,
  không thì phông trung tính (KHÔNG tìm Wikimedia).
* ``ai_still`` — khung hình trích từ clip AI đã duyệt trong ``video_library.json`` (do
  ``scripts/extract_action_posters.py --approve`` ghi SAU KHI người thật đã xem khung hình): ``poster_path``, ``tool``,
  ``creator``, ``reviewed_by`` (người duyệt clip), ``poster_reviewed_by`` (người duyệt chính khung hình — clip đạt
  nhưng khung trích ra vẫn có thể nhòe), ``created_at``, ``label`` = "Ảnh minh họa do AI tạo". Chỉ dùng khi file tồn
  tại và đủ người tạo / công cụ / người duyệt clip / người duyệt ảnh.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .dp_optimizer import is_valid_topic_key, normalize_topic_key

__all__ = ["AI_IMAGE_LABEL", "DEFAULT_LIBRARY_PATH", "AiStill", "LibraryEntry", "SceneLibrary"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIBRARY_PATH = PROJECT_ROOT / "backend" / "data" / "scene_library.json"
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "public"
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
AI_IMAGE_LABEL = "Ảnh minh họa do AI tạo"


def _scene_file(public_dir: Path, image: str) -> Path | None:
    """File ảnh nằm trong ``/assets/scenes/`` (đúng đuôi, không thoát ra ngoài thư mục) và thực sự tồn tại."""
    if not image.startswith("/assets/scenes/") or not image.lower().endswith(_IMAGE_SUFFIXES):
        return None
    path = (public_dir / image.lstrip("/")).resolve()
    try:
        path.relative_to((public_dir / "assets" / "scenes").resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


@dataclass(frozen=True, slots=True)
class AiStill:
    """Ảnh tĩnh mặc định trích từ clip AI đã duyệt (không có mặt nhân vật chính)."""

    poster_path: str
    video_key: str
    label: str
    tool: str
    creator: str
    reviewed_by: str
    created_at: str
    poster_reviewed_by: str = ""
    poster_reviewed_at: str = ""

    def problems(self) -> list[str]:
        """Lý do ảnh tĩnh AI chưa được dùng (chưa tính file trên đĩa); rỗng = đủ điều kiện."""
        issues = []
        if self.label != AI_IMAGE_LABEL:
            issues.append(f"label phải là \"{AI_IMAGE_LABEL}\"")
        for name in ("tool", "creator", "reviewed_by"):
            if not getattr(self, name):
                issues.append(f"thiếu {name}")
        if not self.poster_reviewed_by:
            issues.append("thiếu poster_reviewed_by (chưa có người xem và duyệt khung hình trích ra)")
        return issues

    def to_json(self) -> dict[str, str]:
        return {
            "poster_path": self.poster_path,
            "video_key": self.video_key,
            "label": self.label,
            "tool": self.tool,
            "creator": self.creator,
            "reviewed_by": self.reviewed_by,
            "created_at": self.created_at,
            "poster_reviewed_by": self.poster_reviewed_by,
            "poster_reviewed_at": self.poster_reviewed_at,
        }


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    key: str
    title: str
    year: str
    kind: str  # "battle" | "event" | "homeland" | "place"
    hero_id: str | None
    aliases: tuple[str, ...]
    wikimedia_search_query: str
    image: str
    author: str
    license: str
    license_url: str
    source_url: str
    caption: str = ""
    neutral_only: bool = False
    foreground_only: bool = False
    ai_still: AiStill | None = None

    def image_file(self, public_dir: Path = PUBLIC_DIR) -> Path | None:
        """File ảnh trên đĩa nếu mục đã được nhóm điền đủ và ảnh thực sự tồn tại (``neutral_only`` → luôn None)."""
        if self.neutral_only or not (self.image and self.author and self.license):
            return None
        return _scene_file(public_dir, self.image)

    def ai_still_file(self, public_dir: Path = PUBLIC_DIR) -> Path | None:
        """File ảnh tĩnh AI nếu đủ nhãn, công cụ, người tạo, người duyệt clip + khung hình và file tồn tại."""
        still = self.ai_still
        if self.neutral_only or still is None or still.problems():
            return None
        return _scene_file(public_dir, still.poster_path)

    @property
    def is_homeland(self) -> bool:
        return self.kind == "homeland"


class SceneLibrary:
    """Bảng tra O(1) key → mục thư viện, kèm bảng bí danh (vd. ``bach_dang`` → ``bach_dang_1288``)."""

    def __init__(self, entries: Mapping[str, LibraryEntry]) -> None:
        self._entries = MappingProxyType(dict(entries))
        aliases: dict[str, str] = {}
        for key, entry in self._entries.items():
            for alias in entry.aliases:
                aliases.setdefault(alias, key)
        self._aliases = MappingProxyType(aliases)

    @classmethod
    def load(cls, path: Path = DEFAULT_LIBRARY_PATH) -> "SceneLibrary":
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("scenes") if isinstance(data, Mapping) else None
        if not isinstance(raw, Mapping):
            raise ValueError(f"{path} thiếu đối tượng 'scenes'.")
        entries: dict[str, LibraryEntry] = {}
        for key, item in raw.items():
            if not is_valid_topic_key(key) or not isinstance(item, Mapping):
                raise ValueError(f"Mục thư viện không hợp lệ: {key!r}")

            def text(name: str, item: Mapping[str, object] = item) -> str:
                value = item.get(name)
                return " ".join(value.split()) if isinstance(value, str) else ""

            aliases = tuple(
                alias for alias in (normalize_topic_key(a) for a in item.get("aliases") or ()) if is_valid_topic_key(alias)
            )
            hero = item.get("hero_id")
            still = item.get("ai_still")
            ai_still = None
            if isinstance(still, Mapping):
                ai_still = AiStill(
                    poster_path=text("poster_path", still),
                    video_key=text("video_key", still),
                    label=text("label", still),
                    tool=text("tool", still),
                    creator=text("creator", still),
                    reviewed_by=text("reviewed_by", still),
                    created_at=text("created_at", still),
                    poster_reviewed_by=text("poster_reviewed_by", still),
                    poster_reviewed_at=text("poster_reviewed_at", still),
                )
            entries[key] = LibraryEntry(
                key=key,
                title=text("title") or key,
                year=text("year"),
                kind=text("kind") or "place",
                hero_id=hero if isinstance(hero, str) and hero else None,
                aliases=aliases,
                wikimedia_search_query=text("wikimedia_search_query"),
                image=text("image"),
                author=text("author"),
                license=text("license"),
                license_url=text("license_url"),
                source_url=text("source_url"),
                caption=text("caption"),
                neutral_only=item.get("neutral_only") is True,
                foreground_only=item.get("foreground_only") is True,
                ai_still=ai_still,
            )
        return cls(entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> LibraryEntry | None:
        return self._entries.get(key)

    def resolve(self, key: str) -> str | None:
        """Mã chuẩn của thư viện cho một key hoặc bí danh; ``None`` nếu không thuộc thư viện."""
        if key in self._entries:
            return key
        return self._aliases.get(key)

    def entries(self) -> tuple[LibraryEntry, ...]:
        return tuple(self._entries.values())
