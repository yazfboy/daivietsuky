"""Kiểm ảnh chân dung nhân vật lúc build (chạy trong deploy.yml trước ``npm run build``).

    python scripts/check_hero_portraits.py

Ảnh cũ (đúng mã băm trong LEGACY_PORTRAITS) → GIỮ. Ảnh mới phải có ``reviewed_by`` + ``image_sha256`` khớp file trong
``{id}.meta.json`` và cờ ``portraitAiGenerated`` khớp nguồn ảnh; thiếu → thoát mã 1, build dừng, web giữ bản cũ.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.hero_portraits import HERO_IDS, portrait_status  # noqa: E402

LABELS = {"legacy": "GIỮ ẢNH CŨ", "reviewed": "ĐÃ DUYỆT", "blocked": "CHẶN"}


def main() -> int:
    ok = True
    for hero_id in HERO_IDS:
        status = portrait_status(hero_id)
        extra = f" · duyệt: {status.reviewed_by}" if status.reviewed_by else ""
        print(f"  {LABELS[status.status]:<11} {hero_id} ({status.source or '?'}){extra}")
        for problem in status.problems:
            print(f"      - {problem}")
        ok &= status.status != "blocked"
    print("Chân dung nhân vật: " + ("ĐẠT" if ok else "KHÔNG ĐẠT — dừng build"))
    return 0 if ok else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
