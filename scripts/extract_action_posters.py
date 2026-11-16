"""Trích 1 khung hình từ clip AI đã duyệt làm ảnh tĩnh mặc định cho các key ACTION.

    python scripts/extract_action_posters.py --dry-run   # chỉ báo sẽ làm gì
    python scripts/extract_action_posters.py             # trích khung hình + cập nhật scene_library.json

Áp dụng cho key thuộc ``HERO_EVENTS`` (trận nhân vật trực tiếp chỉ huy) mà ảnh nền ở BACKGROUND_ACTION hiện là phông
trung tính (``neutral_only`` hoặc ``foreground_only``) VÀ có clip hợp lệ trong ``video_library.json`` (đủ ``reviewed_by``,
``has_hero_face: false``, có file .mp4, không âm thanh). Với mỗi key:

1. Trích 1 khung hình (mặc định giữa clip) ra ``poster_path`` = ``/assets/scenes/{key}.jpg`` — KHÔNG ghi đè file có sẵn.
2. Ghi ``ai_still`` (nhãn "Ảnh minh họa do AI tạo", ``tool``, ``creator``, ``reviewed_by``, ``created_at``) vào mục
   của key trong ``scene_library.json`` và gỡ ``neutral_only``.

Cần ``ffmpeg`` trong PATH (máy chưa có thì báo lỗi cho từng key, không sửa gì). Script không tải hay sinh video.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, UnidentifiedImageError  # noqa: E402

from backend.agents.history_rules import HERO_EVENTS  # noqa: E402
from backend.core.scene_library import AI_IMAGE_LABEL, DEFAULT_LIBRARY_PATH  # noqa: E402
from backend.core.video_library import DEFAULT_VIDEO_LIBRARY_PATH, PUBLIC_DIR, VideoLibrary  # noqa: E402

FrameExtractor = Callable[[Path, Path, float], None]
ACTION_KEYS: tuple[str, ...] = tuple(sorted({key for keys in HERO_EVENTS.values() for key in keys}))


class FrameExtractionError(RuntimeError):
    pass


def ffmpeg_extractor(video: Path, out: Path, at_s: float) -> None:
    """Trích đúng 1 khung hình JPEG bằng ffmpeg (bỏ mọi luồng âm thanh)."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise FrameExtractionError("chưa cài ffmpeg (cần ffmpeg trong PATH để trích khung hình)")
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.3f}", "-i", str(video),
           "-frames:v", "1", "-an", "-q:v", "2", "-n", str(out)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if done.returncode != 0:
        raise FrameExtractionError(f"ffmpeg lỗi: {done.stderr.strip()[:200]}")


@dataclass(frozen=True)
class PosterResult:
    key: str
    status: str  # "extracted" | "would_extract" | "skipped" | "error"
    detail: str


def _check_jpeg(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
            if image.format != "JPEG":
                raise FrameExtractionError(f"khung hình không phải JPEG ({image.format})")
    except (OSError, UnidentifiedImageError) as exc:
        raise FrameExtractionError(f"khung hình hỏng: {exc}") from exc


def extract_action_posters(
    *,
    library_path: Path = DEFAULT_LIBRARY_PATH,
    video_library_path: Path = DEFAULT_VIDEO_LIBRARY_PATH,
    public_dir: Path = PUBLIC_DIR,
    extractor: FrameExtractor = ffmpeg_extractor,
    action_keys: Iterable[str] = ACTION_KEYS,
    dry_run: bool = False,
) -> list[PosterResult]:
    data = json.loads(library_path.read_text(encoding="utf-8"))
    scenes = data["scenes"]
    videos = VideoLibrary.load(video_library_path)
    report = videos.report(public_dir)
    results: list[PosterResult] = []
    changed = False

    for key in action_keys:
        item = scenes.get(key)
        if not isinstance(item, dict):
            results.append(PosterResult(key, "skipped", "không có trong scene_library.json"))
            continue
        if key not in report:
            results.append(PosterResult(key, "skipped", "chưa có clip trong video_library.json"))
            continue
        if report[key]:
            results.append(PosterResult(key, "skipped", "clip chưa hợp lệ: " + "; ".join(report[key])))
            continue
        if not (item.get("neutral_only") is True or item.get("foreground_only") is True):
            results.append(PosterResult(key, "skipped", "không ở phông trung tính khi hành động — giữ nguyên ảnh hiện có"))
            continue
        video = videos.get(key)
        assert video is not None
        existing = item.get("ai_still")
        if isinstance(existing, dict) and existing.get("poster_path") == video.poster_path:
            results.append(PosterResult(key, "skipped", "đã có ảnh tĩnh AI"))
            continue
        poster = public_dir / video.poster_path.lstrip("/")
        if item.get("image") == video.poster_path:
            results.append(PosterResult(key, "error", f"{video.poster_path} đang là ảnh chụp của mục, không ghi đè"))
            continue
        if poster.exists():
            results.append(PosterResult(key, "error", f"{video.poster_path} đã tồn tại, không ghi đè (xóa tay nếu muốn trích lại)"))
            continue
        if dry_run:
            results.append(PosterResult(key, "would_extract", f"sẽ trích khung giữa clip → {video.poster_path}"))
            continue

        poster.parent.mkdir(parents=True, exist_ok=True)
        temp = poster.with_name(f".{poster.stem}.tmp.jpg")
        temp.unlink(missing_ok=True)
        try:
            extractor(public_dir / video.video_path.lstrip("/"), temp, video.duration_seconds / 2)
            if not temp.is_file():
                raise FrameExtractionError("không sinh được file khung hình")
            _check_jpeg(temp)
            temp.replace(poster)
        except (FrameExtractionError, OSError, subprocess.SubprocessError) as exc:
            temp.unlink(missing_ok=True)
            results.append(PosterResult(key, "error", str(exc)))
            continue

        item.pop("neutral_only", None)
        item["ai_still"] = {
            "poster_path": video.poster_path,
            "video_key": key,
            "label": AI_IMAGE_LABEL,
            "tool": video.tool,
            "creator": video.creator,
            "reviewed_by": video.reviewed_by,
            "created_at": video.created_at,
        }
        changed = True
        results.append(PosterResult(key, "extracted", f"{video.poster_path} · gỡ neutral_only"))

    if changed:
        library_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    results = extract_action_posters(dry_run=args.dry_run)
    labels = {"extracted": "TRÍCH", "would_extract": "SẼ TRÍCH", "skipped": "BỎ QUA", "error": "LỖI"}
    for result in results:
        print(f"  {labels[result.status]:<8} {result.key}: {result.detail}")
    return 1 if any(result.status == "error" for result in results) else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
