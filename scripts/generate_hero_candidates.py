"""Sinh ứng viên chân dung cho cả 3 nhân vật bằng khung prompt thống nhất (chỉ Space FLUX.1-schnell).

    python scripts/generate_hero_candidates.py                   # 4 ứng viên / nhân vật
    python scripts/generate_hero_candidates.py --count 3 nguyen_trai

* Ghi vào ``scene_candidates/heroes/{id}/u{NN}_seed{seed}.*`` (thư mục gitignore): ``.green.png`` (ảnh gốc nền xanh),
  ``.png`` (đã tách nền RGBA), ``.json`` (prompt, seed, mô hình, kết quả tự kiểm, ``reviewed_by: ""``).
* KHÔNG ghi vào ``frontend/public/assets/heroes/`` — thay ảnh lên web phải qua ``scripts/promote_hero_portrait.py
  --reviewer``.
* ``HF_TOKEN`` (nếu có trong ``.env`` hoặc biến môi trường) được ``HuggingFaceImageSource`` truyền vào gradio_client để
  có hạn mức GPU cao hơn; script không in token. Không có token thì dùng hạn mức ZeroGPU ẩn danh (rất ít).
* Không failover sang mô hình khác để giữ cùng phong cách với ảnh hiện có.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.actor_agent import get_character  # noqa: E402
from backend.core.hero_portraits import HERO_IDS  # noqa: E402
from backend.core.key_pool import GeminiKeyPool, load_api_keys  # noqa: E402
from backend.engine.portrait_prompts import (  # noqa: E402
    HERO_PORTRAIT_SPECS, PORTRAIT_STYLE_VERSION, unified_portrait_prompt,
)
from backend.engine.visual_pipeline import (  # noqa: E402
    DEFAULT_IMAGE_SPACES, HERO_IMAGE_SIZE, DirectorVision, DiskDPCache, HuggingFaceImageSource, image_to_png_bytes,
    make_cutout, open_image, portrait_score, read_env_value,
)

OUT_DIR = PROJECT_ROOT / "scene_candidates" / "heroes"
BASE_SEED = 20261117


async def generate(hero_ids: list[str], count: int) -> int:
    print(f"HF_TOKEN: {'có (hạn mức GPU cao hơn)' if read_env_value('HF_TOKEN') else 'không có (hạn mức ZeroGPU ẩn danh)'}")
    cache = DiskDPCache()
    hf = HuggingFaceImageSource(cache, spaces=(DEFAULT_IMAGE_SPACES[0],))
    vision = DirectorVision(GeminiKeyPool(load_api_keys()), cache)
    width, height = HERO_IMAGE_SIZE
    ok = 0
    try:
        for hero_id in hero_ids:
            profile = get_character(hero_id)
            prompt = unified_portrait_prompt(hero_id)
            spec = HERO_PORTRAIT_SPECS[hero_id]
            look = f"{spec.person}; {spec.costume}; {spec.prop}"
            out_dir = OUT_DIR / hero_id
            out_dir.mkdir(parents=True, exist_ok=True)
            seeds = [BASE_SEED + 1009 * HERO_IDS.index(hero_id) + 7 * i for i in range(count)]
            for n, seed in enumerate(seeds, 1):
                stem = f"u{n:02d}_seed{seed}"
                # Gọi lần lượt: một lỗi hết hạn mức thì dừng nhân vật này, khỏi tốn thêm lượt.
                try:
                    result = await hf.generate_async(prompt, width=width, height=height, seed=seed, spill=False)
                except Exception as exc:  # noqa: BLE001 — ghi lại lỗi Space, tiếp tục nhân vật khác
                    message = str(exc)
                    (out_dir / f"{stem}.error.txt").write_text(message, encoding="utf-8")
                    print(f"{hero_id} {stem}: LỖI {message[:220]}")
                    if "quota" in message.lower() or "tạm nghỉ" in message:
                        print("Hết hạn mức GPU của Space FLUX.1-schnell — dừng, chạy lại khi hạn mức hồi hoặc khi có HF_TOKEN.")
                        return ok
                    continue
                cutout = make_cutout(open_image(result.data))
                try:
                    check = await vision.verify_portrait(profile, cutout.image, look)
                except Exception as exc:  # noqa: BLE001 — tự kiểm lỗi không chặn việc lưu ứng viên
                    check = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                score = portrait_score(check, cutout)
                (out_dir / f"{stem}.green.png").write_bytes(result.data)
                (out_dir / f"{stem}.png").write_bytes(image_to_png_bytes(cutout.image))
                meta = {
                    "character_id": hero_id, "space_id": result.space_id, "model": "FLUX.1-schnell", "seed": seed,
                    "look": "unified", "style_version": PORTRAIT_STYLE_VERSION, "prompt": prompt,
                    "elapsed_s": round(result.elapsed_s, 2), "from_cache": result.from_cache,
                    "cutout": cutout.method, "cutout_usable": cutout.usable, "verify": check, "score": round(score, 2),
                    "size": list(cutout.image.size), "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "reviewed_by": "",
                }
                (out_dir / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
                (out_dir / f"{stem}.error.txt").unlink(missing_ok=True)
                ok += 1
                print(f"{hero_id} {stem}: OK cutout={cutout.method} score={score:.1f} "
                      f"issues={str(check.get('issues', check.get('error', '')))[:160]}")
    finally:
        hf.close()
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("heroes", nargs="*", metavar="HERO_ID", help=", ".join(HERO_IDS) + " (mặc định: cả 3)")
    parser.add_argument("--count", type=int, default=4, choices=range(3, 6), metavar="3-5")
    args = parser.parse_args(argv)
    unknown = [h for h in args.heroes if h not in HERO_IDS]
    if unknown:
        parser.error(f"nhân vật không hợp lệ: {unknown}")
    made = asyncio.run(generate(args.heroes or list(HERO_IDS), args.count))
    print(f"Tạo được {made} ứng viên.")
    return 0 if made else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
