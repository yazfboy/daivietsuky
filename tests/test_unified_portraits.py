"""Đại Việt Sử Ký — Test cố định cho nhánh feat/unified-hero-portraits (không gọi FLUX / Gemini).

Chạy từ thư mục gốc:  python tests/test_unified_portraits.py

* #U1 Khung prompt thống nhất: cùng khung hình / tư thế đứng / nền / ánh sáng cho cả 3 vị; sửa đúng các lỗi của prompt
  cũ (Nguyễn Trãi ngồi + thêu rồng + mũ chóp vương miện, Quang Trung giày hiện đại + giáp mặt thú + mũ chóp xòe, Trần Hưng
  Đạo mũ tua đỏ chóp vàng); đủ ngắn để FLUX.1-schnell đọc hết.
* #U2 Script sinh ứng viên chỉ ghi vào scene_candidates/ (kèm reviewed_by rỗng), chỉ dùng Space FLUX.1-schnell, HF_TOKEN
  đi qua HuggingFaceImageSource (không in ra).
* #U3 Trang duyệt có ô reviewed_by cho từng ứng viên; ảnh trên web không đổi.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from backend.core.hero_portraits import HERO_IDS, LEGACY_PORTRAITS, portrait_status  # noqa: E402
from backend.engine.portrait_prompts import HERO_PORTRAIT_SPECS, unified_portrait_prompt  # noqa: E402

RESULTS: list[bool] = []
MAX_WORDS = 170  # FLUX.1-schnell chỉ đọc ~256 token T5 đầu tiên


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    return bool(ok)


def test_prompts() -> None:
    print("\n#U1 — Khung prompt thống nhất cho 3 vị")
    prompts = {h: unified_portrait_prompt(h) for h in HERO_IDS}
    check(set(HERO_PORTRAIT_SPECS) == set(HERO_IDS), "Có đặc tả cho đúng 3 nhân vật")
    shared = ["Standing upright, facing the viewer, centered", "framed from head to just above the knees",
              "feet not visible", "both hands visible with five separate fingers", "lips closed and clearly visible",
              "Plain flat bright green background (#00FF00)", "Soft frontal studio light, detailed historical oil painting"]
    for hero_id, prompt in prompts.items():
        words = len(prompt.split())
        check(all(s in prompt for s in shared) and words <= MAX_WORDS,
              f"{hero_id}: cùng khung hình / tư thế đứng / nền xanh / ánh sáng / phong cách, {words} từ ≤ {MAX_WORDS}")
    # Phần chung đứng cùng vị trí tương đối → cùng cấu trúc câu cho cả 3.
    tails = {h: p[p.index("Standing upright"):p.index("Plain flat")] for h, p in prompts.items()}
    check(len(set(tails.values())) == 1, "Đoạn khung hình + biểu cảm giống hệt nhau ở cả 3 prompt")

    nt = prompts["nguyen_trai"]
    check("Seated formally" not in nt and "hidden within his wide sleeves" not in nt and "Not seated" in nt,
          "Nguyễn Trãi: bỏ 'Seated formally… hands hidden', bắt buộc đứng")
    check("ô sa" in nt and "no crown or ornament on the hat" in nt and "no dragons or gold embroidery" in nt
          and "dragon" not in nt.replace("no dragons", ""),
          "Nguyễn Trãi: mũ ô sa không chóp vương miện, áo quan văn KHÔNG thêu rồng")
    check("scroll" in nt and "sparse" in nt, "Nguyễn Trãi: cầm cuộn giấy, râu thưa vừa phải")
    qt = prompts["quang_trung"].lower()
    check("feet not visible" in qt and "no monster face on the armor" in qt and "no plumes or fan crest" in qt
          and "sheathed sword" in qt and "no drawn sword" in qt,
          "Quang Trung: không lộ giày, không giáp mặt thú, không mũ chóp xòe, kiếm trong vỏ")
    thd = prompts["tran_hung_dao"].lower()
    check("head wrap" in thd and "no hat with tassels" in thd and "no qing or ming official hat" in thd,
          "Trần Hưng Đạo: khăn chít gọn, tránh mũ tua đỏ chóp vàng kiểu Thanh/Minh")
    check(all("(#00FF00)" in p and "no text" in p for p in prompts.values()), "Nền xanh phẳng để tách nền, không chữ")


def test_generator() -> None:
    print("\n#U2 — scripts/generate_hero_candidates.py")
    src = (ROOT / "scripts/generate_hero_candidates.py").read_text(encoding="utf-8")
    check('OUT_DIR = PROJECT_ROOT / "scene_candidates" / "heroes"' in src and "assets/heroes" not in src.replace(
        "frontend/public/assets/heroes/`` — thay", ""), "Chỉ ghi vào scene_candidates/heroes/, không ghi vào assets/heroes")
    check('"reviewed_by": ""' in src, "Mỗi ứng viên có trường reviewed_by rỗng (chờ duyệt)")
    check("spaces=(DEFAULT_IMAGE_SPACES[0],)" in src and "spill=False" in src,
          "Chỉ dùng Space FLUX.1-schnell, không failover sang mô hình khác")
    pipeline = (ROOT / "backend/engine/visual_pipeline.py").read_text(encoding="utf-8")
    check('read_env_value("HF_TOKEN")' in pipeline and 'options["token"] = self.hf_token' in pipeline
          and not re.search(r"print\([^)]*hf_token", src + pipeline),
          "HF_TOKEN (nếu có) đi vào gradio_client qua HuggingFaceImageSource, không bị in ra")
    check("choices=range(3, 6)" in src, "Số ứng viên mỗi vị giới hạn 3–5")
    check('"quota" in message.lower()' in src, "Hết hạn mức GPU → dừng ngay, không gọi tiếp vô ích")
    env = {**__import__("os").environ, "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, str(ROOT / "scripts/generate_hero_candidates.py"), "khong_ton_tai"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)
    check(done.returncode != 0 and "không hợp lệ" in done.stderr, "Tên nhân vật sai → báo lỗi, không gọi Space")


def test_review_page() -> None:
    print("\n#U3 — Trang duyệt + ảnh trên web")
    check(all(portrait_status(h).status == "legacy" for h in HERO_IDS)
          and all(hashlib.sha256((ROOT / f"frontend/public/assets/heroes/{h}.png").read_bytes()).hexdigest() == d
                  for h, d in LEGACY_PORTRAITS.items()),
          "Chưa thay ảnh mới lên web: 3 ảnh hiện tại giữ nguyên")
    src = (ROOT / "scripts/build_review_heroes.py").read_text(encoding="utf-8")
    check('class="who"' in src and "reviewed_by" in src and "promote_hero_portrait.py" in src and "--dry-run" in src,
          "build_review_heroes.py có ô reviewed_by và xuất lệnh thay ảnh (mặc định --dry-run)")
    page = ROOT / "scene_candidates" / "review_heroes.html"
    if page.is_file():
        html = page.read_text(encoding="utf-8")
        ignored = subprocess.run(["git", "check-ignore", "-q", str(page)], cwd=ROOT).returncode == 0
        check(ignored and all(unified_portrait_prompt(h).split(":")[0].replace("(", "") in html.replace("(", "")
                              for h in HERO_IDS) and html.count('class="decide"') >= 1,
              "review_heroes.html (gitignore) có prompt thống nhất của cả 3 vị + ô duyệt")
    else:
        print("  [INFO] Không có scene_candidates/ trên máy này (gitignore) — bỏ qua kiểm trang duyệt")


def main() -> int:
    test_prompts()
    test_generator()
    test_review_page()
    passed = sum(RESULTS)
    print(f"\nKẾT QUẢ: {passed} PASS · {len(RESULTS) - passed} FAIL")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
