"""Đại Việt Sử Ký — Test cố định cho nhánh fix/scene-library-history (không gọi Gemini / Wikimedia thật).

Chạy từ thư mục gốc:  python tests/test_scene_library_fix.py
* Test #1 — Mục 0: ảnh sai sử / chưa xác minh đã gỡ, ``neutral_only`` chặn gọi Wikimedia, ``caption`` cho ảnh công
  trình hậu thế (không ghi "điện Kính Thiên" cho Diên Hồng), ảnh anh hùng gốc không bị ghi đè.
* Test #3 — không nhân vật nào vào được BACKGROUND_ACTION với key ngoài HERO_EVENTS của mình, kể cả khi bấm nút
  "Hành động" bằng tay: backend (scene_mode + cờ action_allowed) và logic frontend (bundle TypeScript bằng esbuild).
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
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from backend.agents.actor_agent import SUPPORTED_CHARACTER_IDS  # noqa: E402
from backend.agents.director_agent import DirectorAgent, SceneDirective  # noqa: E402
from backend.agents.history_rules import HERO_EVENTS, action_allowed  # noqa: E402
from backend.core.scene_library import SceneLibrary  # noqa: E402
from backend.engine.visual_pipeline import BackdropResolver, WikimediaBackdropFinder  # noqa: E402

FRONTEND = ROOT / "frontend"
SCENES = FRONTEND / "public" / "assets" / "scenes"
HERO_SHA256 = {
    "tran_hung_dao.png": "5118d8a800536ab6613f9cc38470906ceee808cf230fe21aa0aab1b6cbfe89e5",
    "nguyen_trai.png": "c59eba83ccc695a26e71540b5f2716ad187bf6641385d3512b344b634231f0ad",
    "quang_trung.png": "eea5e1de7fd3e42c41055c3da2a7d7e9f1d2f43a87bb708045a53b7f119c975c",
}
# bach_dang_1288: nhóm duyệt ảnh Bãi cọc Cao Quỳ (foreground_only, 16/11/2026) — kiểm ở tests/test_hero_portraits_scenes.py
REMOVED = ("khang_chien_mong_nguyen_1285", "hich_tuong_si", "ngoc_hoi_dong_da_1789",
           "khang_chien_mong_nguyen_1287_1288", "rach_gam_xoai_mut_1785", "len_ngoi_nui_ban_1788")
EMPTY = ("tot_dong_chuc_dong_1426", "chuong_duong_ham_tu_1285", "le_chi_vien_1442", "que_huong_nhi_khe")
RESULTS: list[bool] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    return bool(ok)


def directive(key: str, hero: str = "tran_hung_dao", query: str = "Some Place Vietnam") -> SceneDirective:
    return SceneDirective(
        state_id="vs-t", character_id=hero, question="q", scene_mode="FOREGROUND_TALKING", mouth_sync_enabled=True,
        character_reference_path=f"/assets/heroes/{hero}.png", background_type="HISTORICAL_EVENT", context_key=key,
        background_prompt="", character_action="", scene_prompt="", negative_prompt="", image_system_prompt="",
        reason="", decided_by="test", cache_hit=False, cache_match=None, cache_similarity=None, key_label=None,
        model=None, latency_ms=0.0, context_topic_key=key, topic_title=key, wikimedia_search_query=query,
    )


async def test_library() -> None:
    print("\nTest #1 — Mục 0: thư viện cảnh sau khi sửa lỗi sử")
    library = SceneLibrary.load()
    raw = json.loads((ROOT / "backend/data/scene_library.json").read_text(encoding="utf-8"))["scenes"]
    for key in REMOVED:
        e = library.get(key)
        check(e is not None and e.neutral_only and not e.image and not (SCENES / f"{key}.jpg").exists()
              and bool(raw[key].get("review_note")), f"{key}: đã gỡ ảnh, neutral_only, có ghi chú lý do",
              raw[key].get("review_note", "")[:70])
    for key in EMPTY:
        e = library.get(key)
        check(e is not None and e.neutral_only and not e.image, f"{key}: key trống → neutral_only")
    with_image = [e for e in library.entries() if e.image]
    check(all(e.image_file() is not None and e.caption and e.author and e.license for e in with_image),
          "Mọi ảnh còn lại có file, tác giả, giấy phép và caption", f"{len(with_image)} ảnh")
    check(not any(e.image and e.neutral_only for e in library.entries()), "Không mục nào vừa có ảnh vừa neutral_only")
    dien_hong = library.get("dien_hong_1284")
    check(dien_hong.caption == "Hoàng thành Thăng Long ngày nay" and "Kính Thiên" not in dien_hong.caption
          and "Kính Thiên" not in dien_hong.title, "dien_hong_1284: caption 'Hoàng thành Thăng Long ngày nay', "
          "không ghi điện Kính Thiên (dựng 1428)")
    check("ngày nay" in library.get("lam_son_khoi_nghia").caption, "lam_son_khoi_nghia: caption ghi rõ công trình ngày nay",
          library.get("lam_son_khoi_nghia").caption)
    referenced = {Path(e.image).name for e in library.entries() if e.image}
    orphans = sorted(p.name for p in SCENES.glob("*.jpg") if p.name not in referenced)
    check(not orphans, "Không còn file ảnh mồ côi trong assets/scenes", ", ".join(orphans))

    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return b'{"query": {"pages": {}}}'

    resolver = BackdropResolver(library, WikimediaBackdropFinder(None, fetch=fetch))
    neutral_keys = [e.key for e in library.entries() if e.neutral_only]
    results = [await resolver.resolve(directive(k, query="Bach Dang River")) for k in neutral_keys]
    check(all(r["source"] == "neutral" and "imageUrl" not in r for r in results) and not calls,
          "neutral_only → phông trung tính, KHÔNG gọi Wikimedia dù có từ khóa", f"{len(neutral_keys)} key, {len(calls)} lời gọi API")
    other = await resolver.resolve(directive("dia_danh_ngoai_thu_vien"))
    check(other["source"] == "neutral" and len(calls) == 1, "Key ngoài thư viện vẫn được tìm Wikimedia (đối chứng)")
    lib_hit = await resolver.resolve(directive("dien_hong_1284"))
    check(lib_hit["source"] == "library" and lib_hit.get("caption") == "Hoàng thành Thăng Long ngày nay",
          "Ảnh thư viện trả kèm caption xuống frontend", lib_hit.get("caption", ""))

    class Pool:
        async def generate(self, models: Any, payload: Any, **_: Any) -> SimpleNamespace:
            return SimpleNamespace(text=json.dumps({
                "reason": "x", "question_kind": "historical_topic", "keep_previous_topic": False,
                "context_topic_key": "ngoc_hoi_dong_da_1789", "topic_title_vi": "", "wikimedia_search_query": "Dong Da",
                "beyond_era": False, "background_prompt": "", "character_action": "", "scene_prompt": ""}),
                key_label="fake", model="fake")

    d = await DirectorAgent(Pool(), library=library).direct("nguyen_trai", "Trận Đống Đa?")
    check(d.context_topic_key == "ngoc_hoi_dong_da_1789" and d.wikimedia_search_query == "",
          "AI Đạo diễn không phát từ khóa tìm ảnh cho key neutral_only (dù AI đề xuất)")

    for name, digest in HERO_SHA256.items():
        path = FRONTEND / "public/assets/heroes" / name
        check(hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"Ảnh anh hùng gốc {name} không bị ghi đè")
    candidate = ROOT / "scene_candidates/hero_edges/tran_hung_dao.edges_clean.png"
    check(not (FRONTEND / "public/assets/heroes/tran_hung_dao.edges_clean.png").exists(),
          "Bản đề xuất bỏ viền chỉ nằm ở scene_candidates (chưa thay vào app)",
          "có bản đề xuất" if candidate.exists() else "chưa tạo bản đề xuất")


async def test_backend_action_lock() -> None:
    print("\nTest #3 (backend) — scene_mode / action_allowed chỉ theo HERO_EVENTS")
    library = SceneLibrary.load()
    keys = list(library) + ["chu_de_chung", "moscow_red_square", "dia_danh_moi"]

    class Pool:
        def __init__(self) -> None:
            self.key = ""

        async def generate(self, models: Any, payload: Any, **_: Any) -> SimpleNamespace:
            return SimpleNamespace(text=json.dumps({
                "reason": "x", "scene_mode": "BACKGROUND_ACTION", "question_kind": "historical_topic",
                "keep_previous_topic": False, "context_topic_key": self.key, "topic_title_vi": "",
                "wikimedia_search_query": "", "beyond_era": False, "background_prompt": "", "character_action": "",
                "scene_prompt": ""}), key_label="fake", model="fake")

    from backend.main import scene_payload  # noqa: PLC0415
    services = SimpleNamespace(heroes={})
    bad: list[str] = []
    total = 0
    for hero in SUPPORTED_CHARACTER_IDS:
        pool = Pool()
        director = DirectorAgent(pool, library=library)
        for key in keys:
            pool.key = key
            d = await director.direct(hero, f"Câu hỏi thử {key} {total}")
            payload = scene_payload(services, d, "final")  # type: ignore[arg-type]
            allowed = key in HERO_EVENTS[hero]
            total += 1
            if (d.scene_mode == "BACKGROUND_ACTION") != allowed or d.action_allowed != allowed \
                    or payload["actionAllowed"] != allowed or d.mouth_sync_enabled == allowed:
                bad.append(f"{hero}/{key}")
    check(not bad, "AI đề xuất BACKGROUND_ACTION cho mọi key → chỉ key thuộc HERO_EVENTS[hero] được vào; cờ "
          "actionAllowed khớp", f"{total} cặp, sai: {bad[:4]}")
    check(all(action_allowed(h, k) == (k in HERO_EVENTS[h]) for h in SUPPORTED_CHARACTER_IDS for k in keys),
          "history_rules.action_allowed khớp bảng HERO_EVENTS")


NODE_SCRIPT = r"""
const path = require('path');
const [frontend] = process.argv.slice(2);
const esbuild = require(path.join(frontend, 'node_modules', 'esbuild'));
const entry = `
import { applyManualSceneMode, canEnterAction, toLocalSafeScene } from './utils/sceneRules';
import { HEROES, buildDefaultSceneData, inferSceneAndReply } from './data/heroes';
export function run() {
  const out = { defaults: [], manualLocked: [], localScenes: [], allowedFlow: null };
  for (const hero of Object.values(HEROES)) {
    const d = buildDefaultSceneData(hero);
    out.defaults.push([hero.id, d.sceneMode, d.mouthSyncEnabled, canEnterAction(d)]);
    out.manualLocked.push([hero.id, applyManualSceneMode(d, 'BACKGROUND_ACTION').sceneMode]);
    const questions = hero.suggestedPrompts.map((p) => p.question).concat(['Trận đánh ác liệt nhất?', 'Hãy kể về chiến dịch xuất quân']);
    for (const q of questions) {
      const local = toLocalSafeScene(inferSceneAndReply(hero, q).sceneData);
      out.localScenes.push([hero.id, q, local.sceneMode, local.mouthSyncEnabled, applyManualSceneMode(local, 'BACKGROUND_ACTION').sceneMode]);
    }
  }
  const base = { ...buildDefaultSceneData(HEROES.tran_hung_dao), actionAllowed: true, sceneMode: 'BACKGROUND_ACTION', mouthSyncEnabled: false };
  const talk = applyManualSceneMode(base, 'FOREGROUND_TALKING');
  const back = applyManualSceneMode(talk, 'BACKGROUND_ACTION');
  out.allowedFlow = [talk.sceneMode, talk.mouthSyncEnabled, back.sceneMode, back.mouthSyncEnabled];
  return out;
}
`;
esbuild.build({ stdin: { contents: entry, resolveDir: path.join(frontend, 'src'), loader: 'ts' }, bundle: true,
  platform: 'node', format: 'cjs', write: false, logLevel: 'error', define: { 'import.meta.env.BASE_URL': '"/"' } })
  .then((out) => {
    const mod = { exports: {} };
    new Function('module', 'exports', 'require', out.outputFiles[0].text)(mod, mod.exports, require);
    process.stdout.write(JSON.stringify(mod.exports.run()));
  })
  .catch((err) => { console.error(err); process.exit(1); });
"""


def test_frontend_action_lock() -> None:
    print("\nTest #3 (frontend) — nút 'Hành động' chỉ đọc cờ actionAllowed")
    node = shutil.which("node")
    if not check(node is not None, "Có node để chạy logic TypeScript của frontend"):
        return
    with tempfile.TemporaryDirectory(prefix="dvsk_lock_") as tmp:
        script = Path(tmp) / "lock.cjs"
        script.write_text(NODE_SCRIPT, encoding="utf-8")
        done = subprocess.run([node, str(script), str(FRONTEND)], capture_output=True, text=True, encoding="utf-8",
                              timeout=60)
    if not check(done.returncode == 0, "Bundle sceneRules.ts + heroes.ts", done.stderr[-300:]):
        return
    r = json.loads(done.stdout)
    check(all(m == "FOREGROUND_TALKING" and mouth and not can for _, m, mouth, can in r["defaults"]),
          "Chưa gửi câu hỏi nào: cả 3 nhân vật ở FOREGROUND_TALKING, nút Hành động khóa")
    check(all(m == "FOREGROUND_TALKING" for _, m in r["manualLocked"]),
          "Bấm 'Hành động' bằng tay khi actionAllowed = false → vẫn FOREGROUND_TALKING (cả 3 nhân vật)")
    check(all(m == "FOREGROUND_TALKING" and mouth and forced == "FOREGROUND_TALKING" for _, _, m, mouth, forced in r["localScenes"]),
          "Cảnh dự phòng không có backend (gồm Hịch tướng sĩ, Chi Lăng, Bình Ngô) → luôn tiền cảnh, không vào được hành động",
          f"{len(r['localScenes'])} câu hỏi")
    check(r["allowedFlow"] == ["FOREGROUND_TALKING", True, "BACKGROUND_ACTION", False],
          "actionAllowed = true → chuyển qua lại Đàm đạo / Hành động được")
    sources = "\n".join(p.read_text(encoding="utf-8") for p in (FRONTEND / "src").rglob("*.ts*"))
    check("HERO_EVENTS" not in sources.replace("HERO_EVENTS[hero_id]", "").replace("HERO_EVENTS (bảng", "")
          or all(line.strip().startswith(("*", "//", "/**")) for line in sources.splitlines() if "HERO_EVENTS" in line),
          "Frontend không chép bảng HERO_EVENTS (chỉ nhắc trong chú thích)")


async def main() -> int:
    await test_library()
    await test_backend_action_lock()
    test_frontend_action_lock()
    passed = sum(RESULTS)
    print(f"\nKẾT QUẢ: {passed} PASS · {len(RESULTS) - passed} FAIL")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
