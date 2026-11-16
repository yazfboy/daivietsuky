"""Kiểm tra thư viện video minh họa lúc build.

    python scripts/check_video_library.py                    # trước `npm run build`: sinh approved.json
    python scripts/check_video_library.py --dist frontend/dist  # sau build: gỡ .mp4 chưa duyệt khỏi bản phát hành

* Đọc ``backend/data/video_library.json``; mục nào thiếu ``reviewed_by``, có ``has_hero_face: true``, thiếu file .mp4,
  file còn rãnh âm thanh, sai đường dẫn hay sai thời lượng thì bị LOẠI và in lý do.
* Chỉ mục hợp lệ được ghi vào ``frontend/public/assets/videos/approved.json`` — frontend chỉ hiện nút "Xem video" cho
  key có trong file này, nên một file .mp4 bỏ vào thư mục mà chưa có mục duyệt hợp lệ sẽ không bao giờ hiện nút.
* Ảnh tĩnh AI (``ai_still`` trong ``scene_library.json``, khung hình trích từ clip): chỉ được dùng khi có
  ``poster_reviewed_by`` — người thật đã xem chính khung hình đó (clip đạt nhưng khung trích ra vẫn có thể nhòe).
  Mục thiếu người duyệt ảnh / thiếu file được báo "KHÔNG DÙNG" (backend cũng tự bỏ qua).
* ``--dist``: xóa khỏi thư mục build mọi file trong ``assets/videos/`` không phải .mp4 đã duyệt (hoặc approved.json),
  để clip chưa duyệt không bị đăng lên gh-pages.

Mục bị loại KHÔNG làm build thất bại (chỉ bị bỏ qua); JSON hỏng thì thoát mã 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.scene_library import DEFAULT_LIBRARY_PATH, SceneLibrary  # noqa: E402
from backend.core.video_library import DEFAULT_VIDEO_LIBRARY_PATH, PUBLIC_DIR, VideoLibrary  # noqa: E402

APPROVED_NAME = "approved.json"


def build_approved(library: VideoLibrary, public_dir: Path) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """(mục hợp lệ cho frontend, lý do loại từng mục bị loại)."""
    approved: dict[str, dict] = {}
    rejected: dict[str, list[str]] = {}
    for key, problems in library.report(public_dir).items():
        if problems:
            rejected[key] = problems
        else:
            entry = library.get(key)
            assert entry is not None
            approved[key] = entry.to_public()
    return approved, rejected


def unapproved_files(videos_dir: Path, approved: dict[str, dict]) -> list[Path]:
    """File trong thư mục video không thuộc danh sách đã duyệt."""
    if not videos_dir.is_dir():
        return []
    keep = {APPROVED_NAME} | {Path(item["video_path"]).name for item in approved.values()}
    return sorted(path for path in videos_dir.iterdir() if path.is_file() and path.name not in keep)


def ai_still_report(scene_library_path: Path, public_dir: Path) -> dict[str, list[str]]:
    """Ảnh tĩnh AI trong scene_library.json → lý do không dùng (rỗng = dùng được)."""
    report: dict[str, list[str]] = {}
    for entry in SceneLibrary.load(scene_library_path).entries():
        still = entry.ai_still
        if still is None:
            continue
        problems = list(still.problems())
        if entry.neutral_only:
            problems.append("mục còn neutral_only")
        if not problems and entry.ai_still_file(public_dir) is None:
            problems.append(f"thiếu file {still.poster_path}")
        report[entry.key] = problems
    return report


def run(library_path: Path, public_dir: Path, dist_dir: Path | None, out=sys.stdout,
        scene_library_path: Path = DEFAULT_LIBRARY_PATH) -> int:
    def say(line: str) -> None:
        print(line, file=out)

    try:
        library = VideoLibrary.load(library_path)
    except (OSError, ValueError) as exc:
        say(f"LỖI: không đọc được {library_path}: {exc}")
        return 1
    approved, rejected = build_approved(library, public_dir)
    videos_dir = public_dir / "assets" / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    payload = {"videos": approved}
    (videos_dir / APPROVED_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    say(f"Thư viện video: {len(library)} mục khai báo · {len(approved)} hợp lệ · {len(rejected)} bị loại")
    for key in approved:
        say(f"  DUYỆT  {key}")
    for key, problems in rejected.items():
        say(f"  LOẠI   {key}: " + "; ".join(problems))
    for path in unapproved_files(videos_dir, approved):
        say(f"  BỎ QUA {path.name}: file trong assets/videos không có mục duyệt hợp lệ → không hiện nút \"Xem video\"")

    try:
        stills = ai_still_report(scene_library_path, public_dir)
    except (OSError, ValueError) as exc:
        say(f"LỖI: không đọc được {scene_library_path}: {exc}")
        return 1
    for key, problems in stills.items():
        if problems:
            say(f"  ẢNH TĨNH AI KHÔNG DÙNG  {key}: " + "; ".join(problems))
        else:
            say(f"  ẢNH TĨNH AI DÙNG  {key}")

    if dist_dir is not None:
        dist_videos = dist_dir / "assets" / "videos"
        removed = unapproved_files(dist_videos, approved)
        for path in removed:
            path.unlink()
            say(f"  GỠ KHỎI BẢN BUILD  {path.relative_to(dist_dir).as_posix()}")
        if dist_videos.is_dir():
            (dist_videos / APPROVED_NAME).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", type=Path, default=DEFAULT_VIDEO_LIBRARY_PATH)
    parser.add_argument("--public", type=Path, default=PUBLIC_DIR)
    parser.add_argument("--scene-library", type=Path, default=DEFAULT_LIBRARY_PATH)
    parser.add_argument("--dist", type=Path, default=None, help="thư mục build (vd. frontend/dist) để gỡ clip chưa duyệt")
    args = parser.parse_args(argv)
    return run(args.library, args.public, args.dist, scene_library_path=args.scene_library)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
