"""Đại Việt Sử Ký — Thư viện cảnh dựng sẵn (``backend/data/scene_library.json``).

Mỗi mục: ``context_topic_key`` → tên bối cảnh, năm, loại (battle / event / homeland / place), bí danh, từ khóa tìm
ảnh Wikimedia, và ảnh do nhóm tự chọn + duyệt (``image``, ``author``, ``license``, ``license_url``, ``source_url``).
Chỉ mục có đủ file ảnh trên đĩa + tác giả + giấy phép mới được dùng làm ảnh nền; còn lại hệ thống tìm Wikimedia,
không được thì dùng phông trung tính. Code KHÔNG tự sinh hay tự tải ảnh vào thư viện.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .dp_optimizer import is_valid_topic_key, normalize_topic_key

__all__ = ["DEFAULT_LIBRARY_PATH", "LibraryEntry", "SceneLibrary"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIBRARY_PATH = PROJECT_ROOT / "backend" / "data" / "scene_library.json"
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "public"
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


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

    def image_file(self, public_dir: Path = PUBLIC_DIR) -> Path | None:
        """File ảnh trên đĩa nếu mục đã được nhóm điền đủ và ảnh thực sự tồn tại."""
        if not (self.image and self.author and self.license):
            return None
        if not self.image.startswith("/assets/scenes/") or not self.image.lower().endswith(_IMAGE_SUFFIXES):
            return None
        path = (public_dir / self.image.lstrip("/")).resolve()
        try:
            path.relative_to((public_dir / "assets" / "scenes").resolve())
        except ValueError:
            return None
        return path if path.is_file() else None

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
