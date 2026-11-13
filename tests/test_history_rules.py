"""Đại Việt Sử Ký — Test CỐ ĐỊNH (không gọi Gemini / Wikimedia thật, không đọc .env).

Chạy từ thư mục gốc:  python tests/test_history_rules.py
Phủ: bảng HERO_EVENTS (2a/2b/2c) chốt bằng code, chuẩn hóa context_topic_key, giữ/đổi previous_context_topic_key,
luật nhận thức thời đại (nước Nga), bộ lọc Wikimedia trên dữ liệu mẫu, lọc HTML tác giả, dự phòng phông trung tính
khi Wikimedia lỗi, thư viện cảnh, CORS / WebSocket origin, GET /health.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from backend.agents.actor_agent import ActorAgent  # noqa: E402
from backend.agents.director_agent import DirectorAgent, SceneDirective  # noqa: E402
from backend.agents.history_rules import (  # noqa: E402
    HERO_EVENTS,
    clean_topic_key,
    detect_era_boundary,
    scene_mode_for,
)
from backend.core.dp_optimizer import TOPIC_KEY_RE, normalize_topic_key  # noqa: E402
from backend.core.key_pool import KeyPoolError  # noqa: E402
from backend.core.scene_library import LibraryEntry, SceneLibrary  # noqa: E402
from backend.engine.visual_pipeline import (  # noqa: E402
    AssetSourceError,
    BackdropResolver,
    WikimediaBackdropFinder,
    html_to_text,
    wikimedia_candidates,
)

FIXTURE = ROOT / "tests" / "fixtures" / "wikimedia_sample.json"
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    return bool(ok)


def section(title: str) -> None:
    print(f"\n{title}")


class FakePool:
    """Thay GeminiKeyPool: trả JSON soạn sẵn theo câu hỏi (mô phỏng AI Đạo diễn, kể cả khi AI trả SAI scene_mode)."""

    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.calls: list[str] = []

    async def generate(self, models: Any, payload: Mapping[str, Any], **_: Any) -> SimpleNamespace:
        text = payload["contents"][0]["parts"][0]["text"]
        self.calls.append(text)
        return SimpleNamespace(text=json.dumps(self.responder(text), ensure_ascii=False), key_label="fake", model="fake")


class FailingPool:
    async def generate(self, *_: Any, **__: Any) -> Any:
        raise KeyPoolError("mô phỏng 5 key đều lỗi")


def llm(key: str, *, wrong_mode: str = "", kind: str = "historical_topic", keep: bool = False, query: str = "",
        title: str = "") -> dict[str, Any]:
    data = {"reason": "mô phỏng", "question_kind": kind, "keep_previous_topic": keep, "context_topic_key": key,
            "topic_title_vi": title, "wikimedia_search_query": query, "beyond_era": False,
            "background_prompt": "", "character_action": "", "scene_prompt": ""}
    if wrong_mode:
        data["scene_mode"] = wrong_mode  # AI cố tình chọn sai — code phải ghi đè
    return data


def question_of(prompt: str) -> str:
    return prompt.split("CÂU HỎI NGƯỜI DÙNG")[-1]


# --------------------------------------------------------------------------------------
def test_hero_events_table() -> None:
    section("1. Bảng HERO_EVENTS cố định (liệt kê đích danh)")
    check(HERO_EVENTS["tran_hung_dao"] == {"bach_dang_1288", "khang_chien_mong_nguyen_1285",
                                           "khang_chien_mong_nguyen_1287_1288"}, "Trần Hưng Đạo: đúng 3 key")
    check(HERO_EVENTS["quang_trung"] == {"rach_gam_xoai_mut_1785", "ngoc_hoi_dong_da_1789"}, "Quang Trung: đúng 2 key")
    check(HERO_EVENTS["nguyen_trai"] == frozenset(), "Nguyễn Trãi: rỗng → luôn FOREGROUND_TALKING")
    check(scene_mode_for("tran_hung_dao", "chuong_duong_ham_tu_1285") == "FOREGROUND_TALKING",
          "Chương Dương – Hàm Tử (tướng khác chỉ huy) KHÔNG thuộc Trần Hưng Đạo")
    check(scene_mode_for("tran_hung_dao", "van_don_1288") == "FOREGROUND_TALKING", "Vân Đồn (Trần Khánh Dư) → FOREGROUND")
    library = SceneLibrary.load()
    check(all(key in library for keys in HERO_EVENTS.values() for key in keys), "Mọi key HERO_EVENTS có trong scene_library.json")
    check(all(TOPIC_KEY_RE.fullmatch(key) for key in library), "Mọi key thư viện khớp ^[a-z0-9_]+$", f"{len(library)} key")
    from backend.engine.visual_pipeline import contains_term  # noqa: PLC0415
    homeland = [e for e in library.entries() if e.is_homeland]
    check(len(homeland) == 3 and not any(
        contains_term(f"{e.title} {e.wikimedia_search_query} {' '.join(a.replace('_', ' ') for a in e.aliases)}",
                      ("con son", "kiep bac")) for e in homeland),
          "Key quê hương không tìm ảnh / đặt tên theo Côn Sơn – Kiếp Bạc",
          "; ".join(f"{e.key}: {e.wikimedia_search_query}" for e in homeland))


async def test_scene_mode_by_code() -> None:
    section("2. Test #2 — scene_mode / mouth_sync do code quyết theo HERO_EVENTS (AI trả sai vẫn bị ghi đè)")

    def responder(prompt: str) -> dict[str, Any]:
        q = question_of(prompt)
        if "Bạch Đằng" in q and "TRẦN HƯNG ĐẠO" in q:
            return llm("bach_dang" if "1288" not in q else "bach_dang_1288", wrong_mode="FOREGROUND_TALKING")
        if "Bạch Đằng" in q:
            return llm("bach_dang_1288", wrong_mode="BACKGROUND_ACTION")  # Nguyễn Trãi: AI bảo cầm gươm — sai
        if "Ngọc Hồi" in q:
            return llm("Ngọc Hồi – Đống Đa 1789", wrong_mode="FOREGROUND_TALKING")
        if "Rạch Gầm" in q:
            return llm("rach_gam_xoai_mut_1785")
        if "Chương Dương" in q:
            return llm("chuong_duong_ham_tu_1285", wrong_mode="BACKGROUND_ACTION")
        return llm("chu_de_chung", kind="off_topic")

    director = DirectorAgent(FakePool(responder))
    cases = [
        ("2a", "tran_hung_dao", "Trận Bạch Đằng năm 1288 diễn ra thế nào?", "bach_dang_1288", "BACKGROUND_ACTION"),
        ("2a'", "tran_hung_dao", "Ông đánh trận Bạch Đằng ra sao?", "bach_dang_1288", "BACKGROUND_ACTION"),
        ("2b", "nguyen_trai", "Ông nghĩ gì về trận Bạch Đằng?", "bach_dang_1288", "FOREGROUND_TALKING"),
        ("2c", "quang_trung", "Trận Ngọc Hồi – Đống Đa diễn ra thế nào?", "ngoc_hoi_dong_da_1789", "BACKGROUND_ACTION"),
        ("2c'", "quang_trung", "Kể về trận Rạch Gầm – Xoài Mút", "rach_gam_xoai_mut_1785", "BACKGROUND_ACTION"),
        ("+", "tran_hung_dao", "Trận Chương Dương ra sao?", "chuong_duong_ham_tu_1285", "FOREGROUND_TALKING"),
    ]
    for tag, hero, question, key, mode in cases:
        d = await director.direct(hero, question)
        check(d.context_topic_key == key and d.scene_mode == mode
              and d.mouth_sync_enabled is (mode == "FOREGROUND_TALKING"),
              f"{tag}: {hero} · \"{question}\" → {key}, {mode}, mouth_sync={mode == 'FOREGROUND_TALKING'}",
              f"{d.context_topic_key}, {d.scene_mode}, {d.mouth_sync_enabled}, {d.decided_by}")
    nt = await director.direct("nguyen_trai", "Bạch Đằng có ý nghĩa gì với ông?")
    check(nt.image_allowed and nt.wikimedia_search_query and nt.topic_kind == "battle"
          and "facing the viewer" in nt.character_action,
          "2b: Nguyễn Trãi đứng kể, cảnh Bạch Đằng chỉ là nền minh họa (vẫn tìm ảnh)", nt.character_action[:60])

    section("2'. Cùng các trường hợp khi AI Đạo diễn lỗi (quy tắc dự phòng không gọi AI)")
    fallback = DirectorAgent(FailingPool())
    for tag, hero, question, key, mode in cases:
        d = await fallback.direct(hero, question)
        check(d.decided_by == "rule_fallback" and d.context_topic_key == key and d.scene_mode == mode,
              f"{tag} dự phòng → {key}, {mode}", f"{d.context_topic_key}, {d.scene_mode}")


async def test_topic_keys() -> None:
    section("3. Chuẩn hóa context_topic_key + giữ / đổi previous_context_topic_key (Test #3)")
    check(normalize_topic_key("Bạch Đằng 1288") == "bach_dang_1288", "\"Bạch Đằng 1288\" → bach_dang_1288")
    check(normalize_topic_key("Rạch Gầm – Xoài Mút 1785") == "rach_gam_xoai_mut_1785", "Bỏ dấu, gạch nối → snake_case")
    check(normalize_topic_key("  Moscow Red-Square!! ") == "moscow_red_square", "Ký tự lạ → gạch dưới, cắt hai đầu")
    check(clean_topic_key("!!!") == "" and clean_topic_key(None) == "", "Không còn ký tự hợp lệ → rỗng")
    check(clean_topic_key("../../etc/passwd") == "etc_passwd", "Mã từ frontend có ký tự nguy hiểm bị làm sạch")

    def responder(prompt: str) -> dict[str, Any]:
        q = question_of(prompt)
        if "nước Nga" in q:
            return llm("Mát-xcơ-va Quảng trường Đỏ", query="Red Square Moscow Kremlin", title="Mát-xcơ-va")
        if "cọc" in q:
            return llm("something_else", keep=True)  # AI bảo giữ chủ đề → code dùng ĐÚNG mã lượt trước
        if "lạ" in q:
            return llm("@@@###")  # mã rác
        if "xứ Nga" in q:
            return llm("chu_de_chung", kind="off_topic")  # AI xếp nhầm nước Nga là lạc đề
        return llm("bach_dang_1288")

    director = DirectorAgent(FakePool(responder))
    first = await director.direct("tran_hung_dao", "Kể cho ta nghe về trận chiến năm ấy")
    check(first.context_topic_key == "bach_dang_1288" and first.topic_changed and first.previous_context_topic_key is None,
          "Lượt đầu: mã mới, topic_changed=True")
    keep = await director.direct("tran_hung_dao", "Khi ấy cọc gỗ được bố trí ra sao?",
                                 previous_context_topic_key=first.context_topic_key)
    check(keep.context_topic_key == "bach_dang_1288" and not keep.topic_changed,
          "Giữ chủ đề → trả về CHÍNH XÁC previous_context_topic_key", keep.context_topic_key)
    check(director.topics.get("tran_hung_dao", "bach_dang_1288") is not None, "Bối cảnh lấy lại O(1) từ TopicKeyCache")
    change = await director.direct("tran_hung_dao", "Ông biết gì về nước Nga?", previous_context_topic_key="bach_dang_1288")
    check(change.topic_changed and TOPIC_KEY_RE.fullmatch(change.context_topic_key) is not None
          and change.wikimedia_search_query == "Red Square Moscow Kremlin" and change.scene_mode == "FOREGROUND_TALKING",
          "Đổi chủ đề (nước Nga) → mã mới chuẩn regex + từ khóa tìm ảnh tiếng Anh", change.context_topic_key)
    misfiled = await director.direct("nguyen_trai", "Ông có biết xứ Nga ở đâu không?")
    check(misfiled.context_topic_key == "moscow_red_square" and misfiled.image_allowed
          and misfiled.wikimedia_search_query == "Red Square Moscow Kremlin",
          "AI xếp nhầm nước Nga là off_topic → code đặt lại moscow_red_square (vẫn tìm ảnh)", misfiled.context_topic_key)
    junk = await director.direct("tran_hung_dao", "Một câu hỏi lạ", previous_context_topic_key="bach_dang_1288")
    check(junk.context_topic_key == "bach_dang_1288", "AI trả mã rác → dùng lại mã lượt trước", junk.context_topic_key)
    evil = director.preview("tran_hung_dao", "Rồi sao nữa?", "DROP TABLE; <script>")
    check(TOPIC_KEY_RE.fullmatch(evil.context_topic_key) is not None, "Mã lượt trước độc hại không lọt ra ngoài",
          evil.context_topic_key)

    from backend.main import clean_previous_key  # noqa: PLC0415
    check(clean_previous_key("x" * 65) is None and clean_previous_key(123) is None
          and clean_previous_key("bach_dang_1288") == "bach_dang_1288", "main.clean_previous_key: chỉ nhận chuỗi ≤ 64")


def test_era_rule() -> None:
    section("4. Luật nhận thức thời đại — \"Ông biết gì về nước Nga?\"")
    for hero in ("tran_hung_dao", "nguyen_trai", "quang_trung"):
        boundary = detect_era_boundary(hero, "Ông biết gì về nước Nga?")
        check(boundary is not None and boundary.topic_key == "moscow_red_square", f"{hero}: nước Nga ngoài thời đại")
    check(detect_era_boundary("quang_trung", "Ông biết người Pháp không?") is None,
          "Quang Trung (thế kỷ XVIII) đã biết người Pháp → không áp luật")
    check(detect_era_boundary("tran_hung_dao", "Ông biết người Pháp không?") is not None, "Trần Hưng Đạo chưa biết châu Âu")
    check(detect_era_boundary("tran_hung_dao", "Hôm ấy ông có ngã ngựa không?") is None, "\"ngã\" không bị nhầm là nước Nga")
    actor = ActorAgent(pool=None)  # type: ignore[arg-type]
    payload = actor.build_payload("tran_hung_dao", "Ông biết gì về nước Nga?")
    text = payload["contents"][0]["parts"][0]["text"]
    check("Thời ta chưa biết đến xứ ấy" in text, "AI Nhân vật bị buộc mở đầu \"Thời ta chưa biết đến xứ ấy...\"")
    plain = actor.build_payload("tran_hung_dao", "Trận Bạch Đằng diễn ra thế nào?")["contents"][0]["parts"][0]["text"]
    check(plain == "Trận Bạch Đằng diễn ra thế nào?", "Câu hỏi trong thời đại: không chèn ghi chú")
    check("GIỚI HẠN NHẬN THỨC THỜI ĐẠI" in actor.build_system_prompt("nguyen_trai"), "System prompt có luật thời đại")


def test_wikimedia_filter() -> None:
    section("5. Bộ lọc Wikimedia trên dữ liệu mẫu + lọc HTML tác giả")
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    found = wikimedia_candidates(data)
    check([image.title for image in found] == data["expected_titles"],
          "Chỉ giữ jpg/png khổ ngang ≥ 800px, tỷ lệ ≥ 1.2, giấy phép tự do, có tác giả; loại svg/gif/tif, bản đồ, "
          "quốc huy, cờ, locator, sơ đồ, ảnh dọc, ảnh nhỏ, từ nhạy cảm, host lạ", ", ".join(i.title for i in found))
    best = found[0]
    check(best.artist == "Alex & Co (photo)" and "<" not in best.artist, "Artist HTML → chữ thường (bỏ thẻ, script)",
          best.artist)
    check(best.thumb_url.endswith("1920px-Moscow_Red_Square_panorama.jpg") and "original" not in best.thumb_url,
          "Dùng thumburl (iiurlwidth=1920), không dùng file gốc")
    check(best.page_url.startswith("https://commons.wikimedia.org/wiki/File:") and best.license == "CC BY-SA 4.0"
          and best.license_url.startswith("https://creativecommons.org/"), "Đủ giấy phép + link trang ảnh gốc")
    check(best.width >= 800 and best.width > best.height and best.mime == "image/jpeg", "width ≥ 800, ngang, image/jpeg")
    url = WikimediaBackdropFinder.build_url("Red Square Moscow Kremlin")
    check("iiurlwidth=1920" in url and "iiprop=url%7Csize%7Cmime%7Cextmetadata" in url, "Tham số API đúng chuẩn")
    check(html_to_text('<b>Ivan</b> &lt;3 <i>Petrov</i>') == "Ivan <3 Petrov", "html_to_text giải mã thực thể")


def directive(key: str, *, allowed: bool = True, query: str = "Red Square Moscow Kremlin", kind: str = "place") -> SceneDirective:
    return SceneDirective(
        state_id="vs-test", character_id="tran_hung_dao", question="q", scene_mode="FOREGROUND_TALKING",
        mouth_sync_enabled=True, character_reference_path="/assets/heroes/tran_hung_dao.png",
        background_type="HISTORICAL_EVENT", context_key=key, background_prompt="", character_action="",
        scene_prompt="", negative_prompt="", image_system_prompt="", reason="", decided_by="test", cache_hit=False,
        cache_match=None, cache_similarity=None, key_label=None, model=None, latency_ms=0.0, context_topic_key=key,
        topic_title="Mát-xcơ-va (nước Nga)", topic_kind=kind, wikimedia_search_query=query if allowed else "",
        image_allowed=allowed,
    )


async def test_backdrop_fallback() -> None:
    section("6. Thứ tự ảnh nền: thư viện → Wikimedia → phông trung tính (dự phòng khi Wikimedia lỗi)")
    fixture = FIXTURE.read_bytes()
    calls: list[str] = []

    def ok_fetch(url: str) -> bytes:
        calls.append(url)
        return fixture

    def broken_fetch(url: str) -> bytes:
        calls.append(url)
        raise AssetSourceError("mô phỏng mất mạng")

    library = SceneLibrary.load()
    ok = BackdropResolver(library, WikimediaBackdropFinder(None, fetch=ok_fetch))
    result = await ok.resolve(directive("moscow_red_square"))
    check(result["source"] == "wikimedia" and result["author"] == "Alex & Co (photo)" and result["sourceUrl"]
          and result["license"] and result["width"] >= 800, "Thư viện chưa có ảnh → lấy Wikimedia, đủ ghi nguồn",
          result.get("fileTitle", ""))
    again = await ok.resolve(directive("moscow_red_square"))
    check(again["fromCache"] and len(calls) == 1, "Gọi lại cùng context_topic_key → cache, không gọi API lần 2")

    calls.clear()
    broken = BackdropResolver(library, WikimediaBackdropFinder(None, fetch=broken_fetch))
    neutral = await broken.resolve(directive("moscow_red_square"))
    check(neutral["source"] == "neutral" and "imageUrl" not in neutral and neutral["title"] == "Mát-xcơ-va (nước Nga)",
          "Wikimedia lỗi → phông trung tính có tên địa danh (không rơi về ảnh quê hương / cảnh vẽ cũ)")
    await broken.resolve(directive("moscow_red_square"))
    check(len(calls) == 2, "Lỗi mạng không bị cache (lần sau thử lại)")

    empty = BackdropResolver(library, WikimediaBackdropFinder(None, fetch=lambda url: b'{"query": {"pages": {}}}'))
    check((await empty.resolve(directive("moscow_red_square")))["source"] == "neutral", "Không có ảnh qua lọc → phông trung tính")

    calls.clear()
    off = await ok.resolve(directive("chu_de_chung", allowed=False))
    homeland = await ok.resolve(directive("que_huong_tuc_mac", allowed=False, kind="homeland"))
    check(off["source"] == "neutral" and homeland["source"] == "neutral" and not calls,
          "Câu lạc đề / quê hương ngoài câu hỏi tiểu sử → phông trung tính, KHÔNG tìm ảnh")
    sensitive = await ok.resolve(directive("x_topic", query="naked statue"))
    check(sensitive["source"] == "neutral" and not calls, "Từ khóa nhạy cảm trong query → chặn, không gọi API")

    with tempfile.TemporaryDirectory() as tmp:  # thư viện thử nghiệm riêng, không đụng thư viện thật
        public = Path(tmp)
        (public / "assets" / "scenes").mkdir(parents=True)
        (public / "assets" / "scenes" / "bach_dang_1288.jpg").write_bytes(b"\xff\xd8test")
        entry = LibraryEntry("bach_dang_1288", "Trận Bạch Đằng", "1288", "battle", None, (), "Bach Dang",
                             "/assets/scenes/bach_dang_1288.jpg", "Nhóm SCIC", "CC BY 4.0", "", "https://example.org/a")
        unfilled = LibraryEntry("moscow_red_square", "M", "", "place", None, (), "Red Square",
                                "/assets/scenes/moscow_red_square.jpg", "", "", "", "")
        lib = SceneLibrary({"bach_dang_1288": entry, "moscow_red_square": unfilled})
        calls.clear()
        resolver = BackdropResolver(lib, WikimediaBackdropFinder(None, fetch=ok_fetch), public_dir=public)
        hit = await resolver.resolve(directive("bach_dang_1288"))
        check(hit["source"] == "library" and hit["imageUrl"] == "/assets/scenes/bach_dang_1288.jpg" and not calls,
              "Thư viện có ảnh + tác giả + giấy phép → dùng thư viện trước, không gọi Wikimedia")
        miss = await resolver.resolve(directive("moscow_red_square"))
        check(miss["source"] == "wikimedia", "Mục thư viện thiếu tác giả/giấy phép → bỏ qua, sang Wikimedia")


def test_server_security() -> None:
    section("7. Máy chủ: CORS chỉ GitHub Pages (+ localhost khi dev), WebSocket origin, GET /health")
    probe = ("import sys; sys.path.insert(0, r'%s'); import backend.main as m; print('|'.join(m.ALLOWED_ORIGINS), "
             "m.origin_allowed('http://localhost:5174'))"
             % ROOT)
    env = {**os.environ, "RENDER": "true", "PYTHONIOENCODING": "utf-8"}
    prod = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, timeout=120)
    check(prod.stdout.strip() == "https://yazfboy.github.io False", "Trên Render: chỉ https://yazfboy.github.io (không localhost)",
          prod.stdout.strip())
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from backend.main import ALLOWED_ORIGINS, app  # noqa: PLC0415
    from backend.main import origin_allowed  # noqa: PLC0415
    check("http://localhost:5173" in ALLOWED_ORIGINS and origin_allowed("http://127.0.0.1:5174")
          and not origin_allowed("http://localhost.evil.com"), "Chạy ở máy: nhận localhost mọi cổng cho dev")
    client = TestClient(app)  # không chạy lifespan → không nạp key, không gọi Gemini
    health = client.get("/health")
    check(health.status_code == 200 and health.json() == {"status": "ok"}, "GET /health trả ngay, không cần Gemini")
    evil = client.options("/api/heroes", headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"})
    check(evil.headers.get("access-control-allow-origin") is None, "CORS từ chối origin lạ")
    pages = client.options("/api/heroes", headers={"Origin": "https://yazfboy.github.io", "Access-Control-Request-Method": "GET"})
    check(pages.headers.get("access-control-allow-origin") == "https://yazfboy.github.io", "CORS cho GitHub Pages")
    try:
        with client.websocket_connect("/ws/chat/tran_hung_dao", headers={"Origin": "https://evil.example.com"}) as ws:
            ws.receive_json()
        rejected = False
    except Exception:  # noqa: BLE001 — server đóng kết nối với mã 4403
        rejected = True
    check(rejected, "WebSocket từ origin lạ bị đóng (4403)")
    source = (ROOT / "frontend" / "src" / "utils" / "backendClient.ts").read_text(encoding="utf-8")
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    check("backend=" not in source and "localStorage" not in source and "backend=" not in app_source,
          "Frontend không nhận ?backend= / localStorage — chỉ VITE_API_BASE_URL lúc build")


async def main() -> int:
    test_hero_events_table()
    await test_scene_mode_by_code()
    await test_topic_keys()
    test_era_rule()
    test_wikimedia_filter()
    await test_backdrop_fallback()
    test_server_security()
    passed = sum(ok for ok, _ in RESULTS)
    print(f"\nKẾT QUẢ: {passed} PASS · {len(RESULTS) - passed} FAIL")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
