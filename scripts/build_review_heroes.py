"""Dựng trang duyệt chân dung ``scene_candidates/review_heroes.html`` (thư mục gitignore) cho nhóm / giáo viên Sử.

    python scripts/build_review_heroes.py

* Mỗi nhân vật: ảnh ĐANG CHẠY trên web + mọi ứng viên trong ``scene_candidates/heroes/{id}/`` (đợt mới theo khung
  prompt thống nhất ``u*``, đợt cũ ``0*``), mỗi ảnh ở cỡ 100% trên 3 nền (ô caro / giấy dó / tối) + ảnh cắt phóng 200%
  vùng mặt–miệng và hai bàn tay, kèm prompt, seed, kết quả tự kiểm.
* Ô ghi người duyệt (``reviewed_by``), Duyệt / Loại, ghi chú — lưu tạm trong trình duyệt; nút "Xuất kết quả" cho ra
  JSON + lệnh ``scripts/promote_hero_portrait.py ... --reviewer`` tương ứng. Trang KHÔNG tự thay ảnh lên web.
"""

from __future__ import annotations

import html
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.hero_portraits import HERO_IDS, HEROES_DIR, portrait_status  # noqa: E402
from backend.engine.portrait_prompts import PORTRAIT_STYLE_VERSION, unified_portrait_prompt  # noqa: E402

ROOT = PROJECT_ROOT / "scene_candidates"
CANDIDATES = ROOT / "heroes"
ASSETS = ROOT / "review_heroes_assets"
OUT = ROOT / "review_heroes.html"
E = html.escape
NAMES = {"tran_hung_dao": "Trần Hưng Đạo — thời Trần", "nguyen_trai": "Nguyễn Trãi — thời Lê sơ",
         "quang_trung": "Quang Trung — thời Tây Sơn"}
CHECKLIST = {
    "tran_hung_dao": "Khăn/mũ, áo, giáp đúng kiểu vương hầu – võ tướng thời Trần? Không mũ tua đỏ chóp vàng kiểu quan "
                     "Thanh/Minh? Tay đủ ngón, miệng lộ rõ?",
    "nguyen_trai": "Dáng ĐỨNG? Mũ ô sa đúng kiểu Lê sơ, không chóp vương miện? Áo quan văn KHÔNG thêu rồng? Râu không "
                   "che miệng? Tay cầm cuộn giấy rõ từng ngón?",
    "quang_trung": "Mũ trụ, giáp đúng kiểu chiến tướng Tây Sơn — không giáp mặt thú, không mũ chóp xòe kiểu tuồng? "
                   "Không lộ giày hiện đại? Kiếm trong vỏ?",
}
# Ghi chú đã soi trên ảnh đang chạy (đợt duyệt 16/11/2026).
CURRENT_NOTES = {
    "tran_hung_dao": ["Ảnh chụp cuộn tranh trên Wikimedia Commons: tác giả, nguồn, người chụp đều không rõ. Nhóm đã "
                      "quyết không dùng tiếp."],
    "nguyen_trai": ["Tư thế ngồi (rig cần đứng); râu dài phủ gần hết miệng; mũ có chóp vàng giống vương miện; áo thêu "
                    "rồng/thú vàng; ngón cái tay cầm cuộn giấy bị dính."],
    "quang_trung": ["Giày da có quai kiểu hiện đại; giáp ngực mặt thú lớn và mũ trụ chóp xòe gần kiểu tuồng; kiếm "
                    "đã tuốt khỏi vỏ."],
}


def crop(src: Path, rel: tuple[float, float, float, float], name: str) -> str:
    with Image.open(src) as raw:
        im = raw.convert("RGBA")
    w, h = im.size
    box = (int(rel[0] * w), int(rel[1] * h), int(rel[2] * w), int(rel[3] * h))
    part = im.crop(box)
    part = part.resize((part.width * 2, part.height * 2), Image.LANCZOS)
    part.save(ASSETS / name)
    return f"review_heroes_assets/{name}"


def copy(src: Path, name: str) -> str:
    shutil.copyfile(src, ASSETS / name)
    return f"review_heroes_assets/{name}"


FACE = (0.32, 0.08, 0.68, 0.40)
HANDS_LEFT = (0.12, 0.50, 0.52, 0.86)
HANDS_RIGHT = (0.48, 0.50, 0.88, 0.86)


def figure_block(img: str) -> str:
    labels = {"checker": "ô caro (trong suốt)", "paper": "giấy dó", "dark": "tối như sân khấu"}
    return "".join(f'<figure class="full {bg}"><img src="{img}" alt="" loading="lazy"><figcaption>nền {lab} · '
                   f'cỡ 100%</figcaption></figure>' for bg, lab in labels.items())


def card(hero_id: str, key: str, title: str, png: Path, notes: list[str], info: dict | None, reviewable: bool) -> str:
    slug = key.replace("/", "_").replace(".", "_")
    img = copy(png, f"{slug}.png")
    crops = [("Mặt + miệng", crop(png, FACE, f"{slug}_face.png")),
             ("Tay bên trái ảnh", crop(png, HANDS_LEFT, f"{slug}_hl.png")),
             ("Tay bên phải ảnh", crop(png, HANDS_RIGHT, f"{slug}_hr.png"))]
    parts = [f'<section class="card"><h3>{E(title)}</h3><div class="row">{figure_block(img)}</div><div class="crops">']
    parts += [f'<figure class="checker"><img src="{p}" alt=""><figcaption>{E(l)} (phóng 200%)</figcaption></figure>'
              for l, p in crops]
    parts.append("</div><ul>" + "".join(f"<li>{n}</li>" for n in notes) + "</ul>")
    if info:
        parts.append(f"<details><summary>Prompt / thông tin tạo ảnh</summary><pre>{E(json.dumps(info, ensure_ascii=False, indent=1))}</pre></details>")
    if reviewable:
        parts.append(
            f'<div class="decide" data-key="{E(key)}" data-hero="{hero_id}">'
            '<label>Người duyệt (reviewed_by): <input class="who" placeholder="Họ tên giáo viên Sử / thành viên nhóm"></label>'
            '<label><input type="radio" name="d_' + slug + '" value="duyet"> Duyệt</label>'
            '<label><input type="radio" name="d_' + slug + '" value="loai"> Loại</label>'
            '<label class="note">Ghi chú cổ phục: <input class="why" placeholder="vd. mũ chưa đúng kiểu Lê sơ"></label></div>')
    parts.append("</section>")
    return "".join(parts)


def candidate_notes(info: dict) -> list[str]:
    verify = info.get("verify") or {}
    batch = "Đợt mới — khung prompt thống nhất" if info.get("look") == "unified" else "Đợt cũ — prompt riêng (trước khi thống nhất)"
    return [f"<b>{batch}</b> · {E(str(info.get('model', '')))} · seed {info.get('seed')} · tách nền: {info.get('cutout')} · "
            f"điểm tự kiểm {info.get('score')}/10 · tạo {E(str(info.get('created_at', '')))}",
            f"Tự kiểm (Gemini) ghi: <i>{E(str(verify.get('issues', verify.get('error', ''))))}</i>"]


SCRIPT = """
<script>
(function () {
  var KEY = 'dvsk-review-heroes';
  var store = {};
  try { store = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { store = {}; }
  var boxes = document.querySelectorAll('.decide');
  boxes.forEach(function (box) {
    var k = box.dataset.key, saved = store[k] || {};
    box.querySelector('.who').value = saved.reviewed_by || '';
    box.querySelector('.why').value = saved.note || '';
    box.querySelectorAll('input[type=radio]').forEach(function (r) { r.checked = r.value === saved.decision; });
    box.addEventListener('input', function () {
      var picked = box.querySelector('input[type=radio]:checked');
      store[k] = { hero: box.dataset.hero, reviewed_by: box.querySelector('.who').value.trim(),
                   decision: picked ? picked.value : '', note: box.querySelector('.why').value.trim() };
      try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) {}
    });
  });
  document.getElementById('export').addEventListener('click', function () {
    var lines = [], out = [];
    Object.keys(store).forEach(function (k) {
      var v = store[k]; out.push(Object.assign({ candidate: k }, v));
      if (v.decision === 'duyet' && v.reviewed_by)
        lines.push('python scripts/promote_hero_portrait.py scene_candidates/heroes/' + k + ' --reviewer "' + v.reviewed_by.replace(/"/g, '') + '" --dry-run');
    });
    document.getElementById('result').value = JSON.stringify(out, null, 1) + (lines.length ? '\\n\\n# Lệnh thay ảnh (bỏ --dry-run khi chắc chắn):\\n' + lines.join('\\n') : '');
  });
})();
</script>
"""

CSS = """
body{font-family:system-ui,'Segoe UI',Arial,sans-serif;background:#15161a;color:#e9e4d8;margin:0;padding:24px;line-height:1.5}
h1{font-size:22px} h2{margin-top:40px;border-bottom:1px solid #444;padding-bottom:6px} h3{margin:0 0 10px}
.card{background:#1f2026;border:1px solid #333;border-radius:10px;padding:16px;margin:18px 0}
.row{display:flex;gap:12px;overflow-x:auto} .crops{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}
figure{margin:0;flex:0 0 auto} figure.full img{max-width:none;display:block} .crops img{display:block}
figcaption{font-size:12px;color:#333;padding:4px;background:#ffffffcc}
.checker{background:repeating-conic-gradient(#bbb 0% 25%,#eee 0% 50%) 50%/20px 20px}
.paper{background:linear-gradient(135deg,#e9dcc0,#c4ad7f)} .dark{background:#0b0c0e}
li{margin:4px 0} pre{white-space:pre-wrap;font-size:12px;color:#ccc}
.decide{background:#2a2b33;padding:10px;border-radius:6px;display:flex;flex-wrap:wrap;gap:12px;align-items:center}
.decide input.who,.decide input.why{background:#15161a;color:#eee;border:1px solid #555;border-radius:4px;padding:4px 6px;min-width:240px}
.warn{background:#3a2a10;border:1px solid #806020;padding:10px;border-radius:8px} .prompt{background:#22252c;padding:10px;border-radius:8px}
code{color:#f0c674} textarea{width:100%;min-height:160px;background:#0f1013;color:#ddd;border:1px solid #444}
button{background:#D4B982;color:#111;border:0;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer}
"""


def build() -> Path:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for old in ASSETS.iterdir():
        old.unlink()
    sections = []
    totals = {}
    for hero_id in HERO_IDS:
        status = portrait_status(hero_id)
        cards = [card(hero_id, f"current/{hero_id}", "Ảnh đang chạy trên web (giữ nguyên tới khi có ảnh được duyệt)",
                      HEROES_DIR / f"{hero_id}.png", CURRENT_NOTES[hero_id] + [f"Trạng thái kiểm: {status.status}"],
                      None, False)]
        found = sorted((CANDIDATES / hero_id).glob("*.png")) if (CANDIDATES / hero_id).is_dir() else []
        found = [p for p in found if not p.name.endswith(".green.png") and p.with_suffix(".json").is_file()]
        found.sort(key=lambda p: (not p.name.startswith("u"), p.name))
        totals[hero_id] = sum(1 for p in found if p.name.startswith("u"))
        for png in found:
            info = json.loads(png.with_suffix(".json").read_text(encoding="utf-8"))
            cards.append(card(hero_id, f"{hero_id}/{png.name}", f"Ứng viên {png.stem}", png, candidate_notes(info),
                              info, True))
        empty = "" if totals[hero_id] else (
            '<p class="warn">Chưa có ứng viên theo khung prompt thống nhất: Space FLUX.1-schnell báo hết hạn mức GPU '
            'miễn phí. Chạy lại <code>python scripts/generate_hero_candidates.py</code> khi hạn mức hồi, hoặc sau khi '
            'nhóm tự thêm <code>HF_TOKEN</code> vào <code>.env</code>, rồi chạy <code>python scripts/build_review_heroes.py</code>.</p>')
        sections.append(
            f"<h2>{E(NAMES[hero_id])}</h2><p><b>Cần soi:</b> {E(CHECKLIST[hero_id])}</p>"
            f'<div class="prompt"><b>Prompt thống nhất ({PORTRAIT_STYLE_VERSION})</b><pre>{E(unified_portrait_prompt(hero_id))}</pre></div>'
            f"{empty}{''.join(cards)}")
    page = f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Duyệt chân dung nhân vật</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>{CSS}</style></head><body>
<h1>Duyệt chân dung 3 nhân vật — Đại Việt Sử Ký</h1>
<p class="warn">Trang này KHÔNG thay ảnh lên web. Ảnh chỉ được thay bằng <code>scripts/promote_hero_portrait.py --reviewer "Tên"</code>,
và bản build sẽ dừng nếu ảnh mới thiếu <code>reviewed_by</code>. Ứng viên theo khung prompt thống nhất: Trần Hưng Đạo
{totals['tran_hung_dao']}, Nguyễn Trãi {totals['nguyen_trai']}, Quang Trung {totals['quang_trung']}. Nhận xét về cổ phục ở
đây chỉ là điểm cần soi, không phải kết luận sử học.</p>
{''.join(sections)}
<h2>Xuất kết quả duyệt</h2><p><button id="export" type="button">Xuất kết quả (JSON + lệnh thay ảnh)</button></p>
<textarea id="result" readonly placeholder="Bấm nút để xuất"></textarea>
{SCRIPT}</body></html>"""
    OUT.write_text(page, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    path = build()
    print(f"Đã dựng {path.relative_to(PROJECT_ROOT).as_posix()} ({len(list(ASSETS.iterdir()))} ảnh kèm theo)")
