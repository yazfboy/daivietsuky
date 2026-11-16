"""Đại Việt Sử Ký — Thư viện video minh họa đã duyệt (``backend/data/video_library.json``).

* KHÔNG sinh video live, KHÔNG âm thanh. Nhóm tự tạo clip (công cụ AI), tự duyệt rồi mới khai báo ở đây; code không tự
  tải hay tự sinh video vào ``frontend/public/assets/videos/``.
* Một mục chỉ được dùng (nút "Xem video" mới hiện) khi qua đủ kiểm tra ``problems()``: có người duyệt, không có mặt nhân
  vật chính (``has_hero_face: false``), file .mp4 tồn tại đúng đường dẫn, KHÔNG còn rãnh âm thanh, thời lượng khớp.
* Kiểm tra MP4 bằng thư viện chuẩn (đọc box ``moov/mvhd`` và ``moov/trak/mdia/hdlr``), không cần ffmpeg.
"""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .dp_optimizer import is_valid_topic_key

__all__ = [
    "AI_IMAGE_LABEL",
    "AI_VIDEO_LABEL",
    "DEFAULT_VIDEO_LIBRARY_PATH",
    "VIDEO_DURATION_SECONDS",
    "Mp4Info",
    "VideoEntry",
    "VideoLibrary",
    "mp4_info",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VIDEO_LIBRARY_PATH = PROJECT_ROOT / "backend" / "data" / "video_library.json"
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "public"
AI_VIDEO_LABEL = "Video minh họa do AI tạo"
AI_IMAGE_LABEL = "Ảnh minh họa do AI tạo"
VIDEO_DURATION_SECONDS = 8
DURATION_TOLERANCE_S = 0.5
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf", b"udta"}


# --------------------------------------------------------------------------------------
# Đọc cấu trúc MP4 (ISO BMFF) — chỉ những gì cần để kiểm tra
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Mp4Info:
    handlers: tuple[str, ...]  # handler_type của từng track: "vide", "soun", ...
    duration_s: float | None

    @property
    def has_audio(self) -> bool:
        return "soun" in self.handlers

    @property
    def has_video(self) -> bool:
        return "vide" in self.handlers


def _boxes(data: bytes, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """Duyệt các box trong đoạn [start, end): trả (type, vị trí nội dung, vị trí kết thúc)."""
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        header = 8
        if size == 1:
            if pos + 16 > end:
                raise ValueError("box 64-bit bị cắt cụt")
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise ValueError(f"box {kind!r} có kích thước sai")
        yield kind, pos + header, pos + size
        pos += size


def mp4_info(path: Path) -> Mp4Info:
    """Đọc danh sách track và thời lượng của file MP4. Lỗi định dạng → ``ValueError``."""
    data = Path(path).read_bytes()
    top = {kind: (body, stop) for kind, body, stop in _boxes(data, 0, len(data))}
    if b"ftyp" not in top or b"moov" not in top:
        raise ValueError("không phải file MP4 hợp lệ (thiếu ftyp/moov)")
    handlers: list[str] = []
    duration: float | None = None

    def walk(start: int, end: int) -> None:
        nonlocal duration
        for kind, body, stop in _boxes(data, start, end):
            if kind == b"mvhd":
                version = data[body]
                if version == 1:
                    timescale, length = struct.unpack(">IQ", data[body + 20:body + 32])
                else:
                    timescale, length = struct.unpack(">II", data[body + 12:body + 20])
                duration = length / timescale if timescale else None
            elif kind == b"hdlr":
                handlers.append(data[body + 8:body + 12].decode("ascii", "replace"))
            elif kind in _CONTAINERS:
                walk(body, stop)

    walk(*top[b"moov"])
    return Mp4Info(tuple(handlers), duration)


# --------------------------------------------------------------------------------------
# Mục thư viện video
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class VideoEntry:
    key: str
    video_path: str
    poster_path: str
    duration_seconds: float
    ai_video_label: str
    ai_image_label: str
    creator: str
    tool: str
    created_at: str
    reviewed_by: str
    has_hero_face: bool | None

    def to_public(self) -> dict[str, Any]:
        """Dữ liệu gửi xuống frontend (approved.json)."""
        data = asdict(self)
        data.pop("key")
        return data

    def problems(self, public_dir: Path = PUBLIC_DIR) -> list[str]:
        """Lý do mục này KHÔNG được dùng; danh sách rỗng = được duyệt hợp lệ."""
        issues: list[str] = []
        if not is_valid_topic_key(self.key):
            issues.append("context_topic_key không hợp lệ")
        if self.video_path != f"/assets/videos/{self.key}.mp4":
            issues.append(f"video_path phải là /assets/videos/{self.key}.mp4")
        if self.poster_path != f"/assets/scenes/{self.key}.jpg":
            issues.append(f"poster_path phải là /assets/scenes/{self.key}.jpg")
        if not self.reviewed_by:
            issues.append("thiếu reviewed_by (chưa có người duyệt)")
        if self.has_hero_face is not False:
            issues.append("has_hero_face phải là false (clip không được có mặt nhân vật chính)")
        for name in ("creator", "tool"):
            if not getattr(self, name):
                issues.append(f"thiếu {name}")
        if not _DATE_RE.match(self.created_at):
            issues.append("created_at phải có dạng YYYY-MM-DD")
        if self.ai_video_label != AI_VIDEO_LABEL:
            issues.append(f"ai_video_label phải là \"{AI_VIDEO_LABEL}\"")
        if self.ai_image_label != AI_IMAGE_LABEL:
            issues.append(f"ai_image_label phải là \"{AI_IMAGE_LABEL}\"")
        if self.duration_seconds != VIDEO_DURATION_SECONDS:
            issues.append(f"duration_seconds phải là {VIDEO_DURATION_SECONDS}")
        video = (public_dir / self.video_path.lstrip("/")).resolve()
        try:
            video.relative_to((public_dir / "assets" / "videos").resolve())
        except ValueError:
            issues.append("video_path nằm ngoài thư mục assets/videos")
            return issues
        if not video.is_file():
            issues.append("thiếu file .mp4")
            return issues
        try:
            info = mp4_info(video)
        except (OSError, ValueError, struct.error) as exc:
            issues.append(f"file .mp4 hỏng: {exc}")
            return issues
        if not info.has_video:
            issues.append("file .mp4 không có rãnh hình")
        if info.has_audio:
            issues.append("file .mp4 còn rãnh âm thanh (phải bỏ hẳn rãnh âm thanh)")
        if info.duration_s is None or abs(info.duration_s - self.duration_seconds) > DURATION_TOLERANCE_S:
            shown = "?" if info.duration_s is None else f"{info.duration_s:.2f}"
            issues.append(f"thời lượng file {shown}s không khớp {self.duration_seconds}s")
        return issues


def _text(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    return " ".join(value.split()) if isinstance(value, str) else ""


class VideoLibrary:
    """Đọc ``video_library.json``; mục có tên bắt đầu bằng "_" là ghi chú/mẫu, bị bỏ qua."""

    def __init__(self, entries: Mapping[str, VideoEntry]) -> None:
        self._entries = MappingProxyType(dict(entries))

    @classmethod
    def load(cls, path: Path = DEFAULT_VIDEO_LIBRARY_PATH) -> "VideoLibrary":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw = data.get("videos") if isinstance(data, Mapping) else None
        if not isinstance(raw, Mapping):
            raise ValueError(f"{path} thiếu đối tượng 'videos'.")
        entries: dict[str, VideoEntry] = {}
        for key, item in raw.items():
            if key.startswith("_") or not isinstance(item, Mapping):
                continue
            duration = item.get("duration_seconds")
            face = item.get("has_hero_face")
            entries[key] = VideoEntry(
                key=key,
                video_path=_text(item, "video_path"),
                poster_path=_text(item, "poster_path"),
                duration_seconds=float(duration) if isinstance(duration, (int, float)) else -1.0,
                ai_video_label=_text(item, "ai_video_label"),
                ai_image_label=_text(item, "ai_image_label"),
                creator=_text(item, "creator"),
                tool=_text(item, "tool"),
                created_at=_text(item, "created_at"),
                reviewed_by=_text(item, "reviewed_by"),
                has_hero_face=face if isinstance(face, bool) else None,
            )
        return cls(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> tuple[VideoEntry, ...]:
        return tuple(self._entries.values())

    def get(self, key: str) -> VideoEntry | None:
        return self._entries.get(key)

    def report(self, public_dir: Path = PUBLIC_DIR) -> dict[str, list[str]]:
        return {entry.key: entry.problems(public_dir) for entry in self._entries.values()}

    def approved(self, public_dir: Path = PUBLIC_DIR) -> dict[str, VideoEntry]:
        return {entry.key: entry for entry in self._entries.values() if not entry.problems(public_dir)}
