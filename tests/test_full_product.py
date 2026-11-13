"""Kiểm thử toàn sản phẩm Đại Việt Sử Ký (Phase 4) — chạy THẬT từ đầu đến cuối.

    python tests/test_full_product.py            (từ thư mục gốc dự án)

1. Nạp đủ 5 API key từ ./.env; 3 ảnh anh hùng gốc nguyên vẹn (SHA-256 cố định) trước & sau kiểm thử.
2. Máy chủ FastAPI (backend/main.py, chạy trong tiến trình qua TestClient): /api/health, /api/heroes, /assets tĩnh.
3. Kịch bản "Ông sinh năm bao nhiêu, mất năm nào?" (WebSocket /ws/chat/tran_hung_dao) → FOREGROUND_TALKING,
   mouthSyncEnabled = True, hậu cảnh quê hương động, lời thoại stream kèm khẩu hình; phát lại luồng chữ thật qua
   động cơ khẩu hình của frontend (StreamingMouthSync) → miệng cử động khi stream và khép NGAY khi xong.
4. Kịch bản "Trận Bạch Đằng diễn ra thế nào?" (SSE /api/chat/stream) → BACKGROUND_ACTION, mouthSyncEnabled = False,
   hậu cảnh trận Bạch Đằng động (theme 'naval' của sân khấu), nhân vật hành động, không khẩu hình; giữ model gốc.
5. Mọi lệnh gọi ngoài có timeout cứng ≤ 8 giây; `npm run build` trong frontend/ PASS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from backend.core.key_pool import key_fingerprint, load_api_keys  # noqa: E402
from backend.main import EXTERNAL_TIMEOUT_S, REQUEST_TIMEOUT_S, app  # noqa: E402

FRONTEND = ROOT / "frontend"
HEROES_DIR = FRONTEND / "public" / "assets" / "heroes"
HERO_SHA256 = {
    "tran_hung_dao.png": "5118d8a800536ab6613f9cc38470906ceee808cf230fe21aa0aab1b6cbfe89e5",
    "nguyen_trai.png": "c59eba83ccc695a26e71540b5f2716ad187bf6641385d3512b344b634231f0ad",
    "quang_trung.png": "eea5e1de7fd3e42c41055c3da2a7d7e9f1d2f43a87bb708045a53b7f119c975c",
}
TALK_QUESTION = "Ông sinh năm bao nhiêu, mất năm nào?"
ACTION_QUESTION = "Trận Bạch Đằng diễn ra thế nào?"
HERO = "tran_hung_dao"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    return bool(condition)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset_hashes() -> dict[str, str]:
    return {path.name: sha256(path) for path in sorted(HEROES_DIR.iterdir()) if path.is_file()}


# --------------------------------------------------------------------------------------
# Thu sự kiện của một lượt
# --------------------------------------------------------------------------------------
def collect_ws(client: TestClient, character_id: str, question: str) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    events: list[dict[str, Any]] = []
    with client.websocket_connect(f"/ws/chat/{character_id}") as socket:
        socket.send_json({"question": question})
        while True:
            event = socket.receive_json()
            events.append(event)
            if event["type"] == "turn_end":
                break
    return events, time.perf_counter() - started


def collect_sse(client: TestClient, character_id: str, question: str) -> tuple[list[dict[str, Any]], float, str]:
    started = time.perf_counter()
    events: list[dict[str, Any]] = []
    with client.stream("POST", "/api/chat/stream", json={"characterId": character_id, "question": question}) as response:
        content_type = response.headers.get("content-type", "")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events, time.perf_counter() - started, content_type


def scenes(events: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    return [e["sceneData"] for e in events if e["type"] == "scene" and e["sceneData"]["phase"] == phase]


def speech(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(e["chunk"], t_ms=e["t_ms"]) for e in events if e["type"] == "speech"]


def first(events: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((e for e in events if e["type"] == kind), None)


def describe_turn(events: list[dict[str, Any]], seconds: float) -> None:
    done = first(events, "done") or {}
    visual = first(events, "visual") or {}
    chunks = speech(events)
    answer = done.get("answer", "")
    print(f"    · {len(events)} sự kiện, {len(chunks)} cụm chữ, {seconds:.1f}s; AI Đạo diễn: {done.get('directorDecidedBy')}; "
          f"AI Nhân vật: {done.get('actorModel')} ({done.get('actorKey')}); cảnh động: {visual.get('status')}")
    if done.get("errors"):
        print(f"    · lỗi được xử lý: {done['errors']}")
    print(f"    · trả lời: {answer[:260].replace(chr(10), ' ')}{'…' if len(answer) > 260 else ''}")


# --------------------------------------------------------------------------------------
# Phát lại luồng chữ thật qua động cơ khẩu hình & cảnh nền của frontend (TypeScript, bundle bằng esbuild)
# --------------------------------------------------------------------------------------
NODE_SCRIPT = r"""
const path = require('path');
const fs = require('fs');
const [frontend, dataPath] = process.argv.slice(2);
const esbuild = require(path.join(frontend, 'node_modules', 'esbuild'));
const entry = `
import { StreamingMouthSync, textSyllables } from './engine/visemeSync';
import { resolveBackdropTheme } from './engine/sceneBackdrop';
export function simulate(chunks, mouthSyncEnabled) {
  // Mô phỏng đúng logic DynamicHeroStage: activeChunk.text = toàn bộ chữ đã stream; chỉ nạp phần mới;
  // dùng khẩu hình DP của backend khi khớp số âm tiết; is_final → isStreaming = false → stop() ngay.
  const mouth = new StreamingMouthSync();
  const FRAME = 1000 / 60;
  const t0 = chunks.length ? chunks[0].t_ms : 0;
  const tEnd = chunks.length ? chunks[chunks.length - 1].t_ms : 0;
  let full = '', consumed = '', next = 0, hinted = 0, stopped = false;
  const opens = [], visemes = new Set();
  let openAfterStop = null;
  for (let now = t0; now <= tEnd + 4 * FRAME; now += FRAME) {
    while (next < chunks.length && chunks[next].t_ms <= now) {
      const c = chunks[next++];
      full += c.text;
      if (mouthSyncEnabled && !stopped) {
        const fresh = full.slice(consumed.length);
        consumed = full;
        if (fresh.trim()) {
          const useHint = c.visemes && c.visemes.length && textSyllables(fresh).length === c.syllables;
          if (useHint) hinted += 1;
          mouth.push(fresh, now, useHint ? c.visemes : undefined);
        }
      }
      if (c.is_final) { mouth.stop(); stopped = true; }
    }
    const s = mouth.sample(now);
    if (!stopped) { opens.push(s.open); visemes.add(s.viseme); }
    else if (openAfterStop === null) openAfterStop = s.open;
  }
  const active = opens.filter((o) => o > 0.05).length;
  return {
    frames: opens.length,
    maxOpen: opens.length ? Math.max(...opens) : 0,
    activeFraction: opens.length ? active / opens.length : 0,
    distinctVisemes: [...visemes].filter((v) => v !== 'REST').length,
    hintedChunks: hinted,
    openAfterStop,
    activeAfterStop: mouth.active,
  };
}
export function themes(list) { return list.map((s) => resolveBackdropTheme(s)); }
`;
esbuild.build({ stdin: { contents: entry, resolveDir: path.join(frontend, 'src'), loader: 'ts' }, bundle: true,
  platform: 'node', format: 'cjs', write: false, logLevel: 'error' })
  .then((out) => {
    const mod = { exports: {} };
    new Function('module', 'exports', 'require', out.outputFiles[0].text)(mod, mod.exports, require);
    const data = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
    const result = {
      talk: mod.exports.simulate(data.talk.chunks, data.talk.mouth),
      action: mod.exports.simulate(data.action.chunks, data.action.mouth),
      themes: mod.exports.themes(data.themes),
    };
    process.stdout.write(JSON.stringify(result));
  })
  .catch((err) => { console.error(err); process.exit(1); });
"""


def run_frontend_engine(talk: dict[str, Any], action: dict[str, Any], theme_inputs: list[dict[str, Any]]) -> dict[str, Any] | None:
    node = shutil.which("node")
    if not node:
        return None
    with tempfile.TemporaryDirectory(prefix="dvsk_") as tmp:
        script = Path(tmp) / "engine_check.cjs"
        data = Path(tmp) / "data.json"
        script.write_text(NODE_SCRIPT, encoding="utf-8")
        data.write_text(json.dumps({"talk": talk, "action": action, "themes": theme_inputs}, ensure_ascii=False),
                        encoding="utf-8")
        completed = subprocess.run([node, str(script), str(FRONTEND), str(data)], capture_output=True, text=True,
                                   encoding="utf-8", timeout=15)
        if completed.returncode != 0:
            print(completed.stderr[-800:])
            return None
        return json.loads(completed.stdout)


def chunk_input(chunks: list[dict[str, Any]], mouth: bool) -> dict[str, Any]:
    keep = ("text", "t_ms", "visemes", "syllables", "is_final")
    return {"mouth": mouth, "chunks": [{k: c[k] for k in keep} for c in chunks]}


# --------------------------------------------------------------------------------------
# Kiểm thử
# --------------------------------------------------------------------------------------
def test_environment() -> list[str]:
    section("1. Nạp 5 API key & bảo toàn 3 ảnh anh hùng gốc")
    keys = load_api_keys(ROOT / ".env")
    check("Nạp đủ 5 API key từ ./.env", len(keys) == 5, ", ".join(key_fingerprint(k) for k in keys))
    check("5 key khác nhau", len(set(keys)) == 5)
    for name, expected in HERO_SHA256.items():
        path = HEROES_DIR / name
        check(f"Ảnh gốc {name} nguyên vẹn (SHA-256)", path.is_file() and sha256(path) == expected, expected[:16] + "…")
    check("Timeout cứng mọi lệnh gọi ngoài ≤ 8s", EXTERNAL_TIMEOUT_S <= 8 and REQUEST_TIMEOUT_S <= 8,
          f"Gemini {REQUEST_TIMEOUT_S}s/lần, tổng {EXTERNAL_TIMEOUT_S}s; HF/Wikimedia {EXTERNAL_TIMEOUT_S}s")
    return keys


def test_server_basics(client: TestClient) -> None:
    section("2. Máy chủ FastAPI: health, danh sách anh hùng, tài nguyên tĩnh")
    health = client.get("/api/health").json()
    check("/api/health: 5 key trong pool đa luồng", health.get("status") == "ok" and health.get("keys") == 5)
    response = client.get("/api/heroes")
    heroes = response.json().get("heroes", [])
    ids = [hero["id"] for hero in heroes]
    check("/api/heroes trả đúng 3 vị anh hùng", response.status_code == 200 and ids == ["tran_hung_dao", "nguyen_trai", "quang_trung"],
          ", ".join(hero["name"] for hero in heroes))
    for hero in heroes:
        name = f"{hero['id']}.png"
        check(f"{hero['name']}: ảnh mẫu /assets/heroes/{name}", hero["heroImageUrl"] == f"/assets/heroes/{name}"
              and hero["imageSha256"] == HERO_SHA256[name])
        served = client.get(hero["heroImageUrl"])
        check(f"Phục vụ tĩnh {name} đúng từng byte", served.status_code == 200
              and hashlib.sha256(served.content).hexdigest() == HERO_SHA256[name])
    check("Nhân vật không tồn tại → 404", client.get("/api/heroes/khong_ton_tai").status_code == 404)
    cors = client.options("/api/heroes", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    check("CORS cho frontend Vite (localhost:5173)", cors.headers.get("access-control-allow-origin") == "http://localhost:5173")


def test_talking(client: TestClient) -> dict[str, Any]:
    section(f"3. Kịch bản FOREGROUND_TALKING — \"{TALK_QUESTION}\" (WebSocket)")
    events, seconds = collect_ws(client, HERO, TALK_QUESTION)
    describe_turn(events, seconds)
    types = [e["type"] for e in events]
    preview = scenes(events, "preview")
    final = scenes(events, "final")
    chunks = speech(events)
    done = first(events, "done") or {}
    check("Thứ tự sự kiện: turn_start → scene(preview) … done → turn_end",
          types[:2] == ["turn_start", "scene"] and "done" in types and types[-1] == "turn_end")
    check("Sân khấu đổi cảnh ngay (preview < 50 ms)", bool(preview) and events[1]["t_ms"] < 50, f"{events[1]['t_ms']} ms")
    check("AI Đạo diễn chốt cảnh (scene final)", len(final) == 1, final[0]["decidedBy"] if final else "")
    scene = final[0] if final else {}
    check("sceneMode = FOREGROUND_TALKING", scene.get("sceneMode") == "FOREGROUND_TALKING")
    check("mouthSyncEnabled = True", scene.get("mouthSyncEnabled") is True)
    check("Hậu cảnh: ảnh động quê hương (HOMELAND / homeland)",
          scene.get("backgroundType") == "HOMELAND" and scene.get("contextKey") == "homeland", scene.get("contextTitle", "")[:60])
    check("Nhân vật đứng tiền cảnh, đang nói (character_action)",
          any(w in scene.get("characterAction", "").lower() for w in ("speak", "talk", "nói", "foreground", "camera")),
          scene.get("characterAction", "")[:90])
    check("Giữ nguyên model gốc /assets/heroes/tran_hung_dao.png",
          scene.get("characterReferencePath") == "/assets/heroes/tran_hung_dao.png"
          and "reference image" in scene.get("scenePrompt", ""))
    rig = scene.get("rigData") or {}
    check("sceneData kèm rig 2.5D (14 khớp) cho DynamicHeroStage", len(rig.get("joints", {})) == 14)
    check("ActorAgent stream nhiều cụm chữ", len(chunks) >= 2 and sum(bool(c["text"].strip()) for c in chunks) >= 2,
          f"{len(chunks)} cụm")
    finals = [i for i, c in enumerate(chunks) if c["is_final"]]
    check("Đúng một cụm cuối is_final (dừng miệng khi xong)", finals == [len(chunks) - 1])
    spoken = [c for c in chunks if c["text"].strip()]
    check("Mọi cụm chữ bật mouthSyncEnabled", all(c["mouthSyncEnabled"] for c in chunks))
    with_visemes = [c for c in spoken if c["visemes"]]
    viseme_names = {seg["viseme"] for c in with_visemes for seg in c["visemes"]}
    check("Khẩu hình Viterbi DP gửi kèm từng cụm chữ", len(with_visemes) == len(spoken) and len(viseme_names - {"REST"}) >= 3,
          ", ".join(sorted(viseme_names)))
    ordered = all(seg["start_ms"] < seg["end_ms"] for c in with_visemes for seg in c["visemes"])
    check("Đoạn khẩu hình hợp lệ (start < end)", ordered)
    answer = done.get("answer", "")
    check("Câu trả lời = ghép các cụm chữ đã stream", answer == "".join(c["text"] for c in chunks))
    check("Nội dung lịch sử chuẩn: năm sinh ~1228 & năm mất 1300",
          "1228" in answer and ("1300" in answer or "Canh Tý" in answer))
    check("Ra chữ đầu tiên trong hạn (≤ 10 s)", bool(chunks) and chunks[0]["t_ms"] <= 10_000, f"{chunks[0]['t_ms'] if chunks else '-'} ms")
    visual = first(events, "visual")
    check("visual_pipeline xử lý cảnh động trong hạn 8 s (ready / rendering nền / dự phòng 60 FPS)",
          visual is not None and visual["status"] in ("ready", "rendering", "unavailable"), (visual or {}).get("status", ""))
    return {"chunks": chunks, "scene": scene}


def test_action(client: TestClient) -> dict[str, Any]:
    section(f"4. Kịch bản BACKGROUND_ACTION — \"{ACTION_QUESTION}\" (SSE /api/chat/stream)")
    events, seconds, content_type = collect_sse(client, HERO, ACTION_QUESTION)
    describe_turn(events, seconds)
    check("SSE text/event-stream", content_type.startswith("text/event-stream"))
    final = scenes(events, "final")
    preview = scenes(events, "preview")
    chunks = speech(events)
    done = first(events, "done") or {}
    scene = final[0] if final else {}
    check("Cảnh xem trước đã là BACKGROUND_ACTION (không nhép miệng từ cụm đầu)",
          bool(preview) and preview[0]["sceneMode"] == "BACKGROUND_ACTION")
    check("sceneMode = BACKGROUND_ACTION", scene.get("sceneMode") == "BACKGROUND_ACTION", scene.get("decidedBy", ""))
    check("mouthSyncEnabled = False", scene.get("mouthSyncEnabled") is False)
    check("Hậu cảnh: trận Bạch Đằng (bach_dang_1288, BATTLEFIELD)",
          scene.get("contextKey") == "bach_dang_1288" and scene.get("backgroundType") == "BATTLEFIELD",
          f"{scene.get('contextTitle')} · {scene.get('contextYear')}")
    check("Nhân vật thực hiện hành động trong trận (character_action)", len(scene.get("characterAction", "")) > 10,
          scene.get("characterAction", "")[:90])
    check("Giữ nguyên model gốc /assets/heroes/tran_hung_dao.png",
          scene.get("characterReferencePath") == "/assets/heroes/tran_hung_dao.png")
    check("Không khẩu hình: mọi cụm mouthSyncEnabled = False, không viseme",
          bool(chunks) and all(not c["mouthSyncEnabled"] and not c["visemes"] for c in chunks))
    check("Lời kể vẫn stream xuống GlassChatBox", sum(bool(c["text"].strip()) for c in chunks) >= 2, f"{len(chunks)} cụm")
    answer = done.get("answer", "")
    check("Nội dung lịch sử chuẩn: Bạch Đằng 1288", "Bạch Đằng" in answer and ("1288" in answer or "Mậu Tý" in answer))
    storyboard = first(events, "storyboard")
    if storyboard is not None:
        beats = storyboard["storyboard"]["beats"]
        check("Storyboard DP: mọi beat là BACKGROUND_ACTION, tắt khẩu hình",
              bool(beats) and all(b["scene_mode"] == "BACKGROUND_ACTION" and not b["mouth_sync_enabled"] for b in beats),
              f"{len(beats)} beat")
    return {"chunks": chunks, "scene": scene}


def test_stage_engine(talk: dict[str, Any], action: dict[str, Any]) -> None:
    section("5. Phát lại luồng thật qua động cơ sân khấu frontend (khẩu hình 60 FPS & cảnh nền động)")
    themes_in = [
        {"contextKey": s["scene"].get("contextKey", ""), "backgroundType": s["scene"].get("backgroundType", "HOMELAND"),
         "sceneMode": s["scene"].get("sceneMode", "FOREGROUND_TALKING")}
        for s in (talk, action)
    ]
    result = run_frontend_engine(chunk_input(talk["chunks"], True), chunk_input(action["chunks"], False), themes_in)
    if not check("Chạy được động cơ TypeScript của frontend (node + esbuild)", result is not None):
        return
    t, a = result["talk"], result["action"]
    check("Sân khấu: hậu cảnh quê hương động (theme homeland)", result["themes"][0] == "homeland")
    check("Sân khấu: hậu cảnh trận Bạch Đằng động (theme naval: sóng, cọc, chiến thuyền, khói lửa)", result["themes"][1] == "naval")
    check("Miệng cử động khi đang stream (FOREGROUND_TALKING)",
          t["maxOpen"] > 0.3 and t["activeFraction"] > 0.3 and t["distinctVisemes"] >= 3,
          f"mở tối đa {t['maxOpen']:.2f}, {t['activeFraction'] * 100:.0f}% khung mở, {t['distinctVisemes']} viseme, "
          f"{t['hintedChunks']} cụm dùng DP backend")
    check("Trả lời xong → miệng khép NGAY khung kế tiếp", t["openAfterStop"] == 0 and not t["activeAfterStop"])
    check("BACKGROUND_ACTION: miệng khép suốt lượt", a["maxOpen"] == 0)


def test_frontend_build() -> None:
    section("6. Frontend: kết nối backend & npm run build")
    vite = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
    check("vite.config.ts proxy /api và /ws (WebSocket) → backend :8000",
          "'/api'" in vite and "'/ws'" in vite and "ws: true" in vite and "8000" in vite)
    app_source = (FRONTEND / "src" / "App.tsx").read_text(encoding="utf-8")
    client_source = (FRONTEND / "src" / "utils" / "backendClient.ts").read_text(encoding="utf-8")
    check("GlassChatBox → App → WebSocket /ws/chat/{id} (openChatTurn)",
          "openChatTurn(" in app_source and "/ws/chat/" in client_source and "onSendMessage={handleSendMessage}" in app_source)
    check("DynamicHeroStage nhận sceneData/isStreaming/activeChunk từ backend",
          "activeChunk" in app_source and re.search(r"<DynamicHeroStage[\s\S]*sceneData=\{currentSceneData\}", app_source) is not None)
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if not check("Có npm", npm is not None):
        return
    started = time.perf_counter()
    completed = subprocess.run([npm, "run", "build"], cwd=FRONTEND, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=240)
    output = (completed.stdout + completed.stderr).strip()
    errors = re.findall(r"error TS\d+", output)
    check("npm run build (tsc -b && vite build) PASS, 0 lỗi TypeScript",
          completed.returncode == 0 and not errors and "built in" in output, f"{time.perf_counter() - started:.1f}s")
    if completed.returncode != 0:
        print(output[-2000:])


def main() -> int:
    print("ĐẠI VIỆT SỬ KÝ — KIỂM THỬ TOÀN SẢN PHẨM (Phase 4)")
    before = asset_hashes()
    test_environment()
    with TestClient(app) as client:
        test_server_basics(client)
        talk = test_talking(client)
        action = test_action(client)
    test_stage_engine(talk, action)
    test_frontend_build()
    section("7. Bảo toàn tài nguyên anh hùng sau toàn bộ kiểm thử")
    after = asset_hashes()
    check("3 ảnh gốc + rig/meta trong assets/heroes không đổi một byte", before == after and all(
        after.get(name) == digest for name, digest in HERO_SHA256.items()), f"{len(after)} tệp")
    passed = sum(ok for _, ok, _ in RESULTS)
    total = len(RESULTS)
    print(f"\nKẾT QUẢ: {passed}/{total} PASS ({100.0 * passed / max(1, total):.1f}%)")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  ✗ {name} {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
