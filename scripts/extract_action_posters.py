"""Trích 1 khung hình từ clip AI đã duyệt làm ảnh tĩnh mặc định cho các key ACTION — 2 bước, có người duyệt.

    python scripts/extract_action_posters.py --dry-run          # chỉ báo sẽ làm gì
    python scripts/extract_action_posters.py                    # BƯỚC 1: trích khung → scene_candidates/posters/{key}.jpg
    python scripts/extract_action_posters.py --approve bach_dang_1288 --reviewer "Tên người duyệt"
                                                                # BƯỚC 2: người thật đã xem khung hình ở cỡ 100% → đưa vào thư viện

Áp dụng cho key thuộc ``HERO_EVENTS`` (trận nhân vật trực tiếp chỉ huy) mà ảnh nền ở BACKGROUND_ACTION hiện là phông
trung tính (``neutral_only`` hoặc ``foreground_only``) VÀ có clip hợp lệ trong ``video_library.json`` (đủ ``reviewed_by``,
``has_hero_face: false``, có file .mp4, không âm thanh).

* Bước 1 chỉ ghi khung hình chờ duyệt vào ``scene_candidates/posters/`` (thư mục gitignore). KHÔNG sửa
  ``scene_library.json``, KHÔNG ghi vào ``frontend/public`` — clip đạt nhưng khung giữa clip vẫn có thể bị nhòe.
* Bước 2 (``--approve``) chỉ chạy khi có người duyệt: chép khung hình sang ``/assets/scenes/{key}.jpg`` (KHÔNG ghi đè
  file có sẵn), ghi ``ai_still`` (nhãn "Ảnh minh họa do AI tạo", ``tool``, ``creator``, ``reviewed_by`` của clip,
  ``poster_reviewed_by`` + ``poster_reviewed_at`` của người duyệt khung hình) và gỡ ``neutral_only``.

Cần ``ffmpeg`` trong PATH cho bước 1 (máy chưa có thì báo lỗi cho từng key, không sửa gì). Script không tải hay sinh video.
"""

from __future__ import annotations

import argparse
import datetime as dt
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
from backend.core.video_library import DEFAULT_VIDEO_LIBRARY_PATH, PUBLIC_DIR, VideoEntry, VideoLibrary  # noqa: E402

FrameExtractor = Callable[[Path, Path, float], None]
ACTION_KEYS: tuple[str, ...] = tuple(sorted({key for keys in HERO_EVENTS.values() for key in keys}))
DEFAULT_PENDING_DIR = PROJECT_ROOT / "scene_candidates" / "posters"


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
    status: str  # "pending_review" | "would_extract" | "approved" | "skipped" | "error"
    detail: str


def _check_jpeg(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
            if image.format != "JPEG":
                raise FrameExtractionError(f"khung hình không phải JPEG ({image.format})")
    except (OSError, UnidentifiedImageError) as exc:
        raise FrameExtractionError(f"khung hình hỏng: {exc}") from exc


def _eligible(key: str, scenes: dict, videos: VideoLibrary, report: dict[str, list[str]],
              public_dir: Path) -> tuple[VideoEntry | None, str]:
    """(clip, "") nếu key đủ điều kiện có ảnh tĩnh AI; (None, lý do) nếu không."""
    item = scenes.get(key)
    if not isinstance(item, dict):
        return None, "không có trong scene_library.json"
    if key not in report:
        return None, "chưa có clip trong video_library.json"
    if report[key]:
        return None, "clip chưa hợp lệ: " + "; ".join(report[key])
    if not (item.get("neutral_only") is True or item.get("foreground_only") is True):
        return None, "không ở phông trung tính khi hành động — giữ nguyên ảnh hiện có"
    video = videos.get(key)
    assert video is not None
    existing = item.get("ai_still")
    if isinstance(existing, dict) and existing.get("poster_path") == video.poster_path:
        return None, "đã có ảnh tĩnh AI"
    if item.get("image") == video.poster_path:
        return None, f"{video.poster_path} đang là ảnh chụp của mục, không ghi đè"
    if (public_dir / video.poster_path.lstrip("/")).exists():
        return None, f"{video.poster_path} đã tồn tại, không ghi đè (xóa tay nếu muốn trích lại)"
    return video, ""


def extract_pending_posters(
    *,
    library_path: Path = DEFAULT_LIBRARY_PATH,
    video_library_path: Path = DEFAULT_VIDEO_LIBRARY_PATH,
    public_dir: Path = PUBLIC_DIR,
    pending_dir: Path = DEFAULT_PENDING_DIR,
    extractor: FrameExtractor = ffmpeg_extractor,
    action_keys: Iterable[str] = ACTION_KEYS,
    dry_run: bool = False,
) -> list[PosterResult]:
    """BƯỚC 1: trích khung hình chờ duyệt. Không sửa scene_library.json, không ghi vào frontend/public."""
    scenes = json.loads(library_path.read_text(encoding="utf-8"))["scenes"]
    videos = VideoLibrary.load(video_library_path)
    report = videos.report(public_dir)
    results: list[PosterResult] = []
    for key in action_keys:
        video, reason = _eligible(key, scenes, videos, report, public_dir)
        if video is None:
            status = "error" if "không ghi đè" in reason else "skipped"
            results.append(PosterResult(key, status, reason))
            continue
        pending = pending_dir / f"{key}.jpg"
        if dry_run:
            results.append(PosterResult(key, "would_extract", f"sẽ trích khung giữa clip → {pending.as_posix()} (chờ duyệt)"))
            continue
        pending_dir.mkdir(parents=True, exist_ok=True)
        temp = pending.with_name(f".{key}.tmp.jpg")
        temp.unlink(missing_ok=True)
        try:
            extractor(public_dir / video.video_path.lstrip("/"), temp, video.duration_seconds / 2)
            if not temp.is_file():
                raise FrameExtractionError("không sinh được file khung hình")
            _check_jpeg(temp)
            temp.replace(pending)
        except (FrameExtractionError, OSError, subprocess.SubprocessError) as exc:
            temp.unlink(missing_ok=True)
            results.append(PosterResult(key, "error", str(exc)))
            continue
        results.append(PosterResult(
            key, "pending_review",
            f"{pending.as_posix()} — xem ở cỡ 100% (nhòe? lộ mặt nhân vật?) rồi chạy --approve {key} --reviewer \"Tên\"",
        ))
    return results


def approve_poster(
    key: str,
    reviewer: str,
    *,
    library_path: Path = DEFAULT_LIBRARY_PATH,
    video_library_path: Path = DEFAULT_VIDEO_LIBRARY_PATH,
    public_dir: Path = PUBLIC_DIR,
    pending_dir: Path = DEFAULT_PENDING_DIR,
    action_keys: Iterable[str] = ACTION_KEYS,
    today: str | None = None,
) -> PosterResult:
    """BƯỚC 2: người thật đã xem khung hình → đưa vào thư viện, gỡ neutral_only."""
    reviewer = " ".join(reviewer.split())
    if not reviewer:
        return PosterResult(key, "error", "thiếu --reviewer (tên người đã xem và duyệt khung hình)")
    if key not in set(action_keys):
        return PosterResult(key, "error", "key không thuộc HERO_EVENTS")
    data = json.loads(library_path.read_text(encoding="utf-8"))
    scenes = data["scenes"]
    videos = VideoLibrary.load(video_library_path)
    video, reason = _eligible(key, scenes, videos, videos.report(public_dir), public_dir)
    if video is None:
        return PosterResult(key, "error", reason)
    pending = pending_dir / f"{key}.jpg"
    if not pending.is_file():
        return PosterResult(key, "error", f"chưa có khung hình chờ duyệt {pending.as_posix()} (chạy bước 1 trước)")
    try:
        _check_jpeg(pending)
    except FrameExtractionError as exc:
        return PosterResult(key, "error", str(exc))
    poster = public_dir / video.poster_path.lstrip("/")
    poster.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pending, poster)
    item = scenes[key]
    item.pop("neutral_only", None)
    item["ai_still"] = {
        "poster_path": video.poster_path,
        "video_key": key,
        "label": AI_IMAGE_LABEL,
        "tool": video.tool,
        "creator": video.creator,
        "reviewed_by": video.reviewed_by,
        "created_at": video.created_at,
        "poster_reviewed_by": reviewer,
        "poster_reviewed_at": today or dt.date.today().isoformat(),
    }
    library_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.unlink()
    return PosterResult(key, "approved", f"{video.poster_path} · duyệt ảnh: {reviewer} · gỡ neutral_only")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--approve", metavar="KEY", help="đưa khung hình đã xem của KEY vào thư viện")
    parser.add_argument("--reviewer", default="", help="tên người đã xem và duyệt khung hình (bắt buộc với --approve)")
    args = parser.parse_args(argv)
    labels = {"pending_review": "CHỜ DUYỆT", "would_extract": "SẼ TRÍCH", "approved": "ĐÃ DUYỆT", "skipped": "BỎ QUA",
              "error": "LỖI"}
    if args.approve:
        results = [approve_poster(args.approve, args.reviewer)]
    else:
        results = extract_pending_posters(dry_run=args.dry_run)
    for result in results:
        print(f"  {labels[result.status]:<9} {result.key}: {result.detail}")
    return 1 if any(result.status == "error" for result in results) else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
