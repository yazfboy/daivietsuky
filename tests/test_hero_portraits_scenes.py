"""Đại Việt Sử Ký — Test cố định cho nhánh feat/hero-portraits-and-scenes (không gọi Gemini / Wikimedia / mạng).

Chạy từ thư mục gốc:  python tests/test_hero_portraits_scenes.py

* #P1 Nhãn "Chân dung minh họa do AI tạo": khớp nguồn ảnh trong {id}.meta.json; hiện ở thẻ chọn nhân vật (Sidebar) và
  cạnh tên nhân vật trên sân khấu cho đúng các nhân vật dùng ảnh AI.
* #P2 bach_dang_1288 = ảnh "Bãi cọc Cao Quỳ ngày nay" (foreground_only): FOREGROUND_TALKING hiện ảnh + caption + ghi
  nguồn; BACKGROUND_ACTION (Trần Hưng Đạo) vẫn là nền giấy dó chờ clip; không gọi Wikimedia.
* #P3 rach_gam_xoai_mut_1785 giữ neutral_only (ảnh rạch có công trình hiện đại).
* #P4 Ảnh nhân vật chưa bị thay: 3 PNG nguyên byte, không có .edges_clean, ứng viên chỉ nằm trong scene_candidates/.
* #P5 Cổng duyệt chân dung: ảnh mới thiếu reviewed_by / sai mã băm / sai cờ nhãn AI → chặn build;
  scripts/promote_hero_portrait.py bắt buộc --reviewer.
* #P6 scan_secrets.py đưa HF_TOKEN (nếu có trong .env) vào danh sách quét, kể cả mọi đoạn con ≥ 12 ký tự.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from backend.agents.director_agent import SceneDirective  # noqa: E402
from backend.agents.history_rules import action_allowed, scene_mode_for  # noqa: E402
from backend.core.scene_library import SceneLibrary  # noqa: E402
from backend.engine.visual_pipeline import BackdropResolver  # noqa: E402

FRONTEND = ROOT / "frontend"
HEROES_DIR = FRONTEND / "public" / "assets" / "heroes"
HERO_SHA256 = {
    "tran_hung_dao.png": "5118d8a800536ab6613f9cc38470906ceee808cf230fe21aa0aab1b6cbfe89e5",
    "nguyen_trai.png": "c59eba83ccc695a26e71540b5f2716ad187bf6641385d3512b344b634231f0ad",
    "quang_trung.png": "eea5e1de7fd3e42c41055c3da2a7d7e9f1d2f43a87bb708045a53b7f119c975c",
}
CAO_QUY_SHA16 = "d80bdeecf5a5b8af"
AI_PORTRAIT_LABEL = "Chân dung minh họa do AI tạo"
RESULTS: list[bool] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    return bool(ok)


# ─────────────────────────── #P1 ───────────────────────────
NODE_SCRIPT = r"""
const path = require('path');
const [frontend] = process.argv.slice(2);
const esbuild = require(path.join(frontend, 'node_modules', 'esbuild'));
const entry = `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { HEROES, HERO_LIST, AI_PORTRAIT_LABEL, buildDefaultSceneData } from './data/heroes';
import { HeroSidebar } from './components/HeroSidebar';
import { GlassChatBox } from './components/GlassChatBox';
import { effectiveBackdrop } from './utils/sceneRules';

export function run() {
  const out = { label: AI_PORTRAIT_LABEL, flags: {}, sidebar: '', header: {}, eff: {} };
  for (const hero of Object.values(HEROES)) out.flags[hero.id] = hero.portraitAiGenerated;
  out.sidebar = renderToStaticMarkup(React.createElement(HeroSidebar, { selectedHeroId: 'tran_hung_dao',
    onSelectHero: () => {}, onResetChat: () => {}, isOpen: true, onToggleSidebar: () => {}, preloadProgress: null,
    sceneMode: 'FOREGROUND_TALKING', mouthSyncEnabled: true }));
  out.order = HERO_LIST.map((h) => h.id);
  for (const hero of Object.values(HEROES)) {
    out.header[hero.id] = renderToStaticMarkup(React.createElement(GlassChatBox, { hero, messages: [],
      isStreaming: false, sceneMode: 'FOREGROUND_TALKING', mouthSyncEnabled: true, sceneData: buildDefaultSceneData(hero),
      isSidebarOpen: false, onToggleSidebar: () => {}, onSendMessage: () => {}, onSelectPrompt: () => {},
      onToggleSceneMode: () => {}, onResetChat: () => {} }));
  }
  const photo = { source: 'library', contextTopicKey: 'bach_dang_1288', title: 'Trận Bạch Đằng', subtitle: '1288',
    imageUrl: '/assets/scenes/bach_dang_1288_cao_quy.jpg', caption: 'Bãi cọc Cao Quỳ ngày nay', foregroundOnly: true,
    actionBackdrop: { source: 'neutral', contextTopicKey: 'bach_dang_1288', title: 'Trận Bạch Đằng', subtitle: '1288' } };
  out.eff.talk = effectiveBackdrop(photo, 'FOREGROUND_TALKING').imageUrl;
  out.eff.action = effectiveBackdrop(photo, 'BACKGROUND_ACTION');
  return out;
}
`;
esbuild.build({ stdin: { contents: entry, resolveDir: path.join(frontend, 'src'), loader: 'tsx' }, bundle: true,
  platform: 'node', format: 'cjs', write: false, logLevel: 'error', jsx: 'automatic',
  define: { 'import.meta.env.BASE_URL': '"/"', 'import.meta.env.VITE_API_BASE_URL': '""', 'process.env.NODE_ENV': '"production"' } })
  .then((out) => {
    const mod = { exports: {} };
    new Function('module', 'exports', 'require', out.outputFiles[0].text)(mod, mod.exports, require);
    process.stdout.write(JSON.stringify(mod.exports.run()));
  })
  .catch((err) => { console.error(err); process.exit(1); });
"""


def card_html(sidebar: str, hero_id: str) -> str:
    """Đoạn HTML thẻ của một nhân vật trong Sidebar (từ #id của nhân vật tới thẻ kế tiếp)."""
    start = sidebar.find(f"#{hero_id}")
    end = min([i for i in (sidebar.find("#", start + 1 + len(hero_id)),) if i > 0] or [len(sidebar)])
    return sidebar[start:end]


def test_portrait_labels() -> dict:
    print("\n#P1 — Nhãn 'Chân dung minh họa do AI tạo'")
    expected = {}
    for hero_id in HERO_SHA256:
        stem = hero_id.removesuffix(".png")
        meta = json.loads((HEROES_DIR / f"{stem}.meta.json").read_text(encoding="utf-8"))
        expected[stem] = str(meta.get("source", "")).startswith("huggingface:")
    node = shutil.which("node")
    if not check(node is not None, "Có node để chạy TypeScript của frontend"):
        return {}
    with tempfile.TemporaryDirectory(prefix="dvsk_p1_") as tmp:
        script = Path(tmp) / "p1.cjs"
        script.write_text(NODE_SCRIPT, encoding="utf-8")
        done = subprocess.run([node, str(script), str(FRONTEND)], capture_output=True, text=True, encoding="utf-8",
                              timeout=120)
    if not check(done.returncode == 0, "Bundle + render tĩnh HeroSidebar / GlassChatBox", done.stderr[-400:]):
        return {}
    r = json.loads(done.stdout)
    check(r["label"] == AI_PORTRAIT_LABEL, "Nhãn đúng chữ 'Chân dung minh họa do AI tạo'")
    check(r["flags"] == expected, "portraitAiGenerated khớp nguồn ảnh trong meta.json (huggingface → AI, wikimedia → không)",
          f"{r['flags']}")
    sidebar = r["sidebar"]
    count = sidebar.count('data-testid="sidebar-portrait-ai-label"')
    check(count == sum(expected.values()), "Sidebar: số nhãn AI đúng bằng số nhân vật dùng ảnh AI", f"{count}")
    for hero_id, is_ai in expected.items():
        card = card_html(sidebar, hero_id)
        check((AI_PORTRAIT_LABEL in card) == is_ai,
              f"Sidebar · {hero_id}: {'có' if is_ai else 'không có'} nhãn AI trên thẻ chọn nhân vật")
        header = r["header"][hero_id]
        has = 'data-testid="hero-portrait-ai-label"' in header and AI_PORTRAIT_LABEL in header
        name_pos = header.find("<h1")
        label_pos = header.find('data-testid="hero-portrait-ai-label"')
        check(has == is_ai and (not is_ai or 0 < name_pos < label_pos < name_pos + 1500),
              f"Sân khấu · {hero_id}: {'có' if is_ai else 'không có'} dòng nhãn AI ngay cạnh tên nhân vật")
    return r


# ─────────────────────────── #P2, #P3 ───────────────────────────
class RecordingFinder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def find(self, key: str, query: str):
        self.calls.append(key)
        return None


def directive(key: str, hero: str) -> SceneDirective:
    mode = scene_mode_for(hero, key)
    return SceneDirective(
        state_id="vs-t", character_id=hero, question="q", scene_mode=mode,
        mouth_sync_enabled=mode == "FOREGROUND_TALKING", character_reference_path=f"/assets/heroes/{hero}.png",
        background_type="HISTORICAL_EVENT", context_key=key, background_prompt="", character_action="",
        scene_prompt="", negative_prompt="", image_system_prompt="", reason="", decided_by="test", cache_hit=False,
        cache_match=None, cache_similarity=None, key_label=None, model=None, latency_ms=0.0, context_topic_key=key,
        topic_title="Trận Bạch Đằng", topic_year="1288", wikimedia_search_query="Bach Dang River Quang Ninh",
        action_allowed=action_allowed(hero, key),
    )


async def test_scenes(r: dict) -> None:
    print("\n#P2 — bach_dang_1288: ảnh 'Bãi cọc Cao Quỳ ngày nay' (foreground_only)")
    library = SceneLibrary.load()
    raw = json.loads((ROOT / "backend/data/scene_library.json").read_text(encoding="utf-8"))["scenes"]
    e = library.get("bach_dang_1288")
    path = e.image_file() if e else None
    check(e is not None and e.foreground_only and not e.neutral_only and e.caption == "Bãi cọc Cao Quỳ ngày nay"
          and e.image == "/assets/scenes/bach_dang_1288_cao_quy.jpg", "Mục thư viện: foreground_only, caption, đường dẫn ảnh")
    check(path is not None and hashlib.sha256(path.read_bytes()).hexdigest()[:16] == CAO_QUY_SHA16,
          "File ảnh đúng là ảnh Bãi cọc Cao Quỳ trong scene_candidates (không chỉnh sửa)")
    check(bool(e.author and e.license and e.source_url.startswith("https://commons.wikimedia.org/wiki/File:")),
          "Có tác giả, giấy phép và link trang gốc Commons", f"{e.author} · {e.license}")
    check(not (FRONTEND / "public/assets/scenes/bach_dang_1288.jpg").exists(),
          "Tên /assets/scenes/bach_dang_1288.jpg vẫn để dành cho khung hình clip AI (không bị ảnh chụp chiếm)")
    finder = RecordingFinder()
    resolver = BackdropResolver(library, finder)
    for hero in ("nguyen_trai", "quang_trung"):
        d = directive("bach_dang_1288", hero)
        res = await resolver.resolve(d)
        check(d.scene_mode == "FOREGROUND_TALKING" and res["source"] == "library"
              and res["caption"] == "Bãi cọc Cao Quỳ ngày nay" and res["foregroundOnly"] is True
              and res["author"] and res["license"],
              f"{hero} kể về Bạch Đằng → FOREGROUND_TALKING, nền là ảnh di tích thật + caption + ghi nguồn")
    d = directive("bach_dang_1288", "tran_hung_dao")
    res = await resolver.resolve(d)
    check(d.scene_mode == "BACKGROUND_ACTION" and res["actionBackdrop"]["source"] == "neutral"
          and "imageUrl" not in res["actionBackdrop"],
          "Trần Hưng Đạo → BACKGROUND_ACTION: nền hành động là phông giấy dó (chờ clip), không dùng ảnh di tích")
    if r:
        check(r["eff"]["talk"] == "/assets/scenes/bach_dang_1288_cao_quy.jpg" and r["eff"]["action"]["source"] == "neutral"
              and r["eff"]["action"]["title"] == "Trận Bạch Đằng",
              "Frontend effectiveBackdrop: ảnh khi trò chuyện, phông giấy dó (giữ tên trận) khi hành động")
    check(not finder.calls, "Không gọi Wikimedia cho bach_dang_1288", f"{finder.calls}")
    check(raw["khang_chien_mong_nguyen_1287_1288"].get("neutral_only") is True,
          "khang_chien_mong_nguyen_1287_1288 không bị tự đổi (nhóm chỉ duyệt cho bach_dang_1288)")

    print("\n#P3 — rach_gam_xoai_mut_1785 giữ neutral_only")
    rg = library.get("rach_gam_xoai_mut_1785")
    check(rg.neutral_only and not rg.image and "hiện đại" in raw["rach_gam_xoai_mut_1785"].get("review_note", ""),
          "Ảnh rạch có công trình hiện đại → giữ neutral_only, ghi lý do", raw["rach_gam_xoai_mut_1785"]["review_note"][-90:])
    res = await resolver.resolve(directive("rach_gam_xoai_mut_1785", "quang_trung"))
    check(res["source"] == "neutral" and not finder.calls, "Rạch Gầm → phông trung tính, không gọi Wikimedia")


# ─────────────────────────── #P4 ───────────────────────────
def test_heroes_untouched() -> None:
    print("\n#P4 — Ảnh nhân vật chưa bị thay (chờ nhóm duyệt)")
    check(all(hashlib.sha256((HEROES_DIR / n).read_bytes()).hexdigest() == h for n, h in HERO_SHA256.items()),
          "3 ảnh nhân vật giữ nguyên từng byte")
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    check(not any("edges_clean" in p for p in tracked) and not list(ROOT.rglob("*edges_clean*")),
          "Không còn file tran_hung_dao.edges_clean.png ở đâu cả")
    check(sorted(p.name for p in HEROES_DIR.glob("*.png")) == sorted(HERO_SHA256),
          "Thư mục assets/heroes chỉ có 3 PNG gốc (ứng viên không bị chép vào)")
    check(not any(p.startswith("scene_candidates/") for p in tracked), "scene_candidates/ không bị commit")
    review = ROOT / "scene_candidates" / "review_heroes.html"
    if review.is_file():
        ignored = subprocess.run(["git", "check-ignore", "-q", str(review)], cwd=ROOT).returncode == 0
        page = review.read_text(encoding="utf-8")
        cands = sorted((ROOT / "scene_candidates/heroes/tran_hung_dao").glob("0*_seed*[0-9].png"))
        check(ignored and 3 <= len(cands) <= 5 and all(f"tran_hung_dao/{c.name}" in page for c in cands)
              and "Nguyễn Trãi" in page and "Quang Trung" in page,
              "review_heroes.html (gitignore) có đủ ứng viên Trần Hưng Đạo + ảnh Nguyễn Trãi, Quang Trung",
              f"{len(cands)} ứng viên")
    else:
        print("  [INFO] Không có scene_candidates/ trên máy này (gitignore) — bỏ qua kiểm trang duyệt")


# ─────────────────────────── #P5 ───────────────────────────
def test_review_gate() -> None:
    print("\n#P5 — Cổng duyệt chân dung (reviewed_by)")
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_hero_portraits  # noqa: F401  (kiểm import được)
    import promote_hero_portrait as promote
    from PIL import Image

    from backend.core.hero_portraits import HERO_IDS, LEGACY_PORTRAITS, portrait_status, sha256_file

    real = {h: portrait_status(h) for h in HERO_IDS}
    check(all(st.status == "legacy" for st in real.values()), "3 ảnh trên web hiện tại: ảnh cũ được giữ (legacy)",
          f"{ {h: st.status for h, st in real.items()} }")
    metas = {h: json.loads((HEROES_DIR / f"{h}.meta.json").read_text(encoding="utf-8")) for h in HERO_IDS}
    check(all("reviewed_by" in m and m.get("review_status") == "legacy" for m in metas.values()),
          "meta.json của 3 ảnh hiện tại có trường reviewed_by (rỗng) + review_status 'legacy'")
    check(set(LEGACY_PORTRAITS.values()) == set(HERO_SHA256.values()), "Danh sách ảnh cũ đúng 3 mã băm hiện tại")
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    check(0 < workflow.find("check_hero_portraits.py") < workflow.find("npm run build"),
          "deploy.yml chạy check_hero_portraits.py TRƯỚC npm run build")

    with tempfile.TemporaryDirectory(prefix="dvsk_p5_") as tmp:
        tmp = Path(tmp)
        heroes = tmp / "heroes"
        shutil.copytree(HEROES_DIR, heroes)
        ts = tmp / "heroes.ts"
        shutil.copyfile(FRONTEND / "src/data/heroes.ts", ts)
        cand_dir = tmp / "cand"
        cand_dir.mkdir()
        cand = cand_dir / "01_test_seed1.png"
        img = Image.new("RGBA", (64, 96), (0, 0, 0, 0))
        img.paste((150, 30, 30, 255), (16, 10, 48, 90))
        img.save(cand)
        (cand_dir / "01_test_seed1.json").write_text(json.dumps({
            "character_id": "tran_hung_dao", "space_id": "black-forest-labs/FLUX.1-schnell", "model": "FLUX.1-schnell",
            "seed": 1, "prompt": "p", "reviewed_by": ""}), encoding="utf-8")
        rig_calls = []

        def fake_rig(hero_id, layer):
            rig_calls.append(hero_id)
            return {"character_id": hero_id, "joints": {}}

        before = sha256_file(heroes / "tran_hung_dao.png")
        try:
            promote.promote(cand, "   ", heroes_dir=heroes, heroes_ts=ts, rig_builder=fake_rig)
            refused = False
        except ValueError:
            refused = True
        check(refused and sha256_file(heroes / "tran_hung_dao.png") == before and not rig_calls,
              "Thay ảnh khi chưa có người duyệt (--reviewer rỗng) → từ chối, ảnh trên web giữ nguyên")
        msg = promote.promote(cand, "Cô giáo Sử A", heroes_dir=heroes, heroes_ts=ts, rig_builder=fake_rig,
                              today="2026-11-17")
        st = portrait_status("tran_hung_dao", heroes, ts)
        meta = json.loads((heroes / "tran_hung_dao.meta.json").read_text(encoding="utf-8"))
        check(st.status == "reviewed" and meta["reviewed_by"] == "Cô giáo Sử A" and meta["reviewed_at"] == "2026-11-17"
              and meta["image_sha256"] == sha256_file(heroes / "tran_hung_dao.png") and rig_calls == ["tran_hung_dao"],
              "Có --reviewer → thay ảnh, dò lại rig, meta.json ghi reviewed_by / reviewed_at / image_sha256", msg)
        check(promote.HEROES_TS != ts and "portraitAiGenerated: true," in ts.read_text(encoding="utf-8").split(
            "id: 'tran_hung_dao'")[1].split("id: 'nguyen_trai'")[0],
              "Thay bằng ảnh AI → bật portraitAiGenerated (nhãn 'Chân dung minh họa do AI tạo') cho nhân vật đó")
        Image.new("RGBA", (64, 96), (9, 9, 9, 0)).save(heroes / "tran_hung_dao.png")
        check(portrait_status("tran_hung_dao", heroes, ts).status == "blocked",
              "Ảnh bị đổi sau khi duyệt (sai image_sha256) → chặn build")
        meta.pop("reviewed_by")
        meta["image_sha256"] = sha256_file(heroes / "tran_hung_dao.png")
        (heroes / "tran_hung_dao.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        blocked = portrait_status("tran_hung_dao", heroes, ts)
        check(blocked.status == "blocked" and any("reviewed_by" in p for p in blocked.problems),
              "Ảnh mới thiếu reviewed_by → chặn build (chưa được thay lên web)")
        ts.write_text(ts.read_text(encoding="utf-8").replace("portraitAiGenerated: true,", "portraitAiGenerated: false,", 1),
                      encoding="utf-8")
        check(portrait_status("nguyen_trai", heroes, ts).status == "legacy"
              and portrait_status("tran_hung_dao", heroes, ts).status == "blocked",
              "Cờ nhãn AI trong heroes.ts lệch nguồn ảnh → chặn")


# ─────────────────────────── #P6 ───────────────────────────
def test_hf_token_scan() -> None:
    print("\n#P6 — scan_secrets.py quét cả HF_TOKEN")
    import secrets as pysecrets
    import string

    sys.path.insert(0, str(ROOT / "tests"))
    import scan_secrets

    alphabet = string.ascii_letters + string.digits
    fake = "".join(pysecrets.choice(alphabet) for _ in range(30))  # sinh lúc chạy, không có chuỗi token trong repo
    with tempfile.TemporaryDirectory(prefix="dvsk_p6_") as tmp:
        env = Path(tmp) / ".env"
        env.write_text(f"# test\nexport HF_TOKEN={fake}\nOTHER=short\n", encoding="utf-8")
        found = scan_secrets.read_secrets(env)
    names = [n for n, _ in found]
    check(names == ["HF_TOKEN"] and found[0][1] == fake, "Dòng 'export HF_TOKEN=…' trong .env được đưa vào danh sách quét")
    pattern = scan_secrets.fragment_pattern(fake)
    check(bool(pattern.search(f"const x = '{fake[7:19]}';".encode())) and bool(pattern.search(fake.encode()))
          and not pattern.search(b"khong co token nao o day 1234567890"),
          "Bắt được cả token đầy đủ lẫn đoạn con 12 ký tự; không báo nhầm")
    check("HF_TOKEN" in scan_secrets.HF_TOKEN_NAMES, "Danh sách tên token Hugging Face gồm HF_TOKEN")


async def main() -> int:
    r = test_portrait_labels()
    await test_scenes(r)
    test_heroes_untouched()
    test_review_gate()
    test_hf_token_scan()
    passed = sum(RESULTS)
    print(f"\nKẾT QUẢ: {passed} PASS · {len(RESULTS) - passed} FAIL")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
