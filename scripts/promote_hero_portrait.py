"""Thay ảnh chân dung nhân vật bằng một ứng viên ĐÃ ĐƯỢC DUYỆT trong scene_candidates/heroes/.

    python scripts/promote_hero_portrait.py scene_candidates/heroes/nguyen_trai/03_xxx.png --reviewer "Tên giáo viên Sử" --dry-run
    python scripts/promote_hero_portrait.py scene_candidates/heroes/nguyen_trai/03_xxx.png --reviewer "Tên giáo viên Sử"

* Bắt buộc ``--reviewer`` (người/giáo viên Sử đã duyệt cổ phục ở review_heroes.html) — thiếu thì không làm gì.
* Ứng viên phải là PNG RGBA đã tách nền (4 góc trong suốt) và có file ``.json`` cùng tên (prompt, seed, mô hình...).
* Ghi ``{id}.png``, dò lại khung xương ``{id}.rig.json`` (AI Đạo diễn; lỗi thì dùng tỉ lệ cơ thể mặc định) và
  ``{id}.meta.json`` có ``reviewed_by``, ``reviewed_at``, ``image_sha256``; bật ``portraitAiGenerated`` trong heroes.ts
  để nhãn "Chân dung minh họa do AI tạo" hiện đúng. Ảnh cũ vẫn còn trong lịch sử Git.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image  # noqa: E402

from backend.core.hero_portraits import HERO_IDS, HEROES_DIR, HEROES_TS, sha256_file  # noqa: E402

RigBuilder = Callable[[str, Image.Image], dict[str, Any]]


def default_rig_builder(hero_id: str, layer: Image.Image) -> dict[str, Any]:
    """Dò khung xương bằng AI Đạo diễn (đọc key từ .env lúc chạy); lỗi → tỉ lệ cơ thể mặc định."""
    from backend.core.key_pool import GeminiKeyPool, load_api_keys
    from backend.engine.visual_pipeline import DirectorVision, DiskDPCache, alpha_bbox, build_rig

    async def analyze() -> dict[str, Any]:
        vision = DirectorVision(GeminiKeyPool(load_api_keys()), DiskDPCache())
        return await vision.analyze_rig(layer)

    try:
        analysis = asyncio.run(analyze())
    except Exception as exc:  # noqa: BLE001 — dò lỗi thì vẫn dựng rig theo tỉ lệ mặc định
        print(f"  (dò khung xương lỗi, dùng tỉ lệ mặc định: {type(exc).__name__})")
        analysis = {}
    return build_rig(hero_id, analysis, layer.width, layer.height, alpha_bbox(layer)).to_json()


def set_ai_flag(heroes_ts: Path, hero_id: str, value: bool) -> None:
    text = heroes_ts.read_text(encoding="utf-8")
    pattern = re.compile(rf"(id: '{hero_id}',.*?portraitAiGenerated: )(true|false)(,[^\n]*)", re.S)
    new, count = pattern.subn(lambda m: f"{m.group(1)}{'true' if value else 'false'},", text, count=1)
    if count != 1:
        raise ValueError(f"không tìm thấy portraitAiGenerated của {hero_id} trong heroes.ts")
    heroes_ts.write_text(new, encoding="utf-8")


def promote(candidate: Path, reviewer: str, *, heroes_dir: Path = HEROES_DIR, heroes_ts: Path = HEROES_TS,
            rig_builder: RigBuilder = default_rig_builder, dry_run: bool = False, today: str | None = None) -> str:
    reviewer = " ".join(reviewer.split())
    if not reviewer:
        raise ValueError("thiếu --reviewer: ảnh chưa có người duyệt thì không được thay lên web")
    meta_path = candidate.with_suffix(".json")
    if not candidate.is_file() or not meta_path.is_file():
        raise ValueError(f"thiếu ứng viên hoặc file thông tin {meta_path.name}")
    info = json.loads(meta_path.read_text(encoding="utf-8"))
    hero_id = info.get("character_id")
    if hero_id not in HERO_IDS:
        raise ValueError(f"character_id không hợp lệ: {hero_id!r}")
    with Image.open(candidate) as raw:
        layer = raw.convert("RGBA")
    alpha = layer.getchannel("A")
    w, h = layer.size
    if any(alpha.getpixel(p) > 8 for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))):
        raise ValueError("ứng viên chưa tách nền trong suốt (góc ảnh còn nền)")
    if dry_run:
        return f"SẼ THAY {hero_id} ← {candidate.name} · duyệt: {reviewer}"
    png = heroes_dir / f"{hero_id}.png"
    layer.save(png, "PNG")
    rig = rig_builder(hero_id, layer)
    (heroes_dir / f"{hero_id}.rig.json").write_text(json.dumps(rig, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = {
        "character_id": hero_id,
        "source": f"huggingface:{info.get('space_id', '')}",
        "generation": {key: info.get(key) for key in ("space_id", "model", "seed", "prompt", "look", "verify", "score",
                                                     "cutout", "created_at")},
        "candidate_file": candidate.name,
        "reviewed_by": reviewer,
        "reviewed_at": today or dt.date.today().isoformat(),
        "review_status": "reviewed",
        "image_sha256": sha256_file(png),
        "size": [w, h],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (heroes_dir / f"{hero_id}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    set_ai_flag(heroes_ts, hero_id, True)
    return f"ĐÃ THAY {hero_id} ← {candidate.name} · duyệt: {reviewer}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(promote(args.candidate.resolve(), args.reviewer, dry_run=args.dry_run))
    except ValueError as exc:
        print(f"LỖI: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
