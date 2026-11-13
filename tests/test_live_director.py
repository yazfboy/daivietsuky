"""Đại Việt Sử Ký — Test GỌI THẬT Gemini + Wikimedia (chạy riêng: python tests/test_live_director.py).

Chấm lỏng:
* Test #1 — "Ông biết gì về nước Nga?" (Trần Hưng Đạo, Nguyễn Trãi): câu trả lời mở đầu bằng ý xác định ranh giới thời
  đại rồi mới kể theo góc nhìn hậu thế; FOREGROUND_TALKING + mouth_sync; context_topic_key hợp lệ; ảnh nền Wikimedia
  đạt chuẩn (jpg/png, ngang, ≥ 800px, đủ tác giả / giấy phép / link gốc) HOẶC phông trung tính dự phòng.
* Test #2 — 2a / 2b / 2c qua AI Đạo diễn thật.
* Test #3 — giữ / đổi previous_context_topic_key qua AI Đạo diễn thật.
Key đọc từ ./.env (hoặc biến môi trường) lúc chạy; không in key.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from backend.agents.actor_agent import ActorAgent  # noqa: E402
from backend.agents.director_agent import DirectorAgent  # noqa: E402
from backend.core.dp_optimizer import TOPIC_KEY_RE, normalize_text, strip_diacritics  # noqa: E402
from backend.core.key_pool import GeminiKeyPool  # noqa: E402
from backend.core.scene_library import SceneLibrary  # noqa: E402
from backend.engine.visual_pipeline import BackdropResolver, DiskDPCache, WikimediaBackdropFinder  # noqa: E402
from backend.main import ACTOR_MODELS, EXTERNAL_TIMEOUT_S, REQUEST_TIMEOUT_S, load_api_keys_any  # noqa: E402

RESULTS: list[bool] = []
BOUNDARY_MARKERS = ("chua biet", "chua tung biet", "chua he biet", "chua nghe", "chua tung nghe", "khong biet",
                    "chua tung hay", "chua hay biet", "chua tung den", "chua he nghe", "chua tung thay")


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    return bool(ok)


def opening(text: str, sentences: int = 2) -> str:
    parts = [p for p in __import__("re").split(r"(?<=[.!?…])\s+", text.strip()) if p]
    return " ".join(parts[:sentences])


def backdrop_ok(info: dict) -> tuple[bool, str]:
    if info.get("source") == "neutral":
        return True, f"phông trung tính dự phòng ({info.get('detail', '')})"
    if info.get("source") == "library":
        from PIL import Image  # noqa: PLC0415
        path = ROOT / "frontend" / "public" / str(info.get("imageUrl", "")).lstrip("/")
        with Image.open(path) as im:
            width, height = im.size
            fmt = im.format
        good = (fmt == "JPEG" and width >= 800 and width / max(1, height) >= 1.2 and bool(info.get("author"))
                and bool(info.get("license")) and str(info.get("sourceUrl", "")).startswith("https://"))
        return good, (f"thư viện {info.get('imageUrl')} {width}×{height} · {info.get('author', '')[:40]} · "
                      f"{info.get('license')} · {info.get('sourceUrl')}")
    good = (info.get("source") == "wikimedia" and info.get("mime") in ("image/jpeg", "image/png")
            and str(info.get("fileTitle", "")).lower().endswith((".jpg", ".jpeg", ".png"))
            and info.get("width", 0) >= 800 and info.get("width", 0) > info.get("height", 0)
            and info.get("width", 0) / max(1, info.get("height", 1)) >= 1.2
            and bool(info.get("author")) and bool(info.get("license")) and str(info.get("sourceUrl", "")).startswith("https://"))
    return good, (f"{info.get('fileTitle')} {info.get('width')}×{info.get('height')} · {info.get('author', '')[:40]} · "
                  f"{info.get('license')} · {info.get('sourceUrl')}")


async def direct_llm(director: DirectorAgent, hero: str, question: str, previous: str | None = None):
    """Gọi AI Đạo diễn thật; Gemini quá tải tạm thời (503 → rule_fallback) thì thử lại tối đa 3 lần."""
    for attempt in range(3):
        d = await director.direct(hero, question, previous_context_topic_key=previous)
        if d.decided_by in ("llm", "cache"):
            return d
        print(f"    (lần {attempt + 1}: AI Đạo diễn quá tải tạm thời → thử lại)")
        await asyncio.sleep(5)
    return d


async def ask_russia(director: DirectorAgent, actor: ActorAgent, backdrops: BackdropResolver, hero: str) -> None:
    print(f"\nTest #1 — {hero}: \"Ông biết gì về nước Nga?\"")
    answer, directive = "", None
    for attempt in range(3):  # Gemini đôi khi trả 503 "high demand" tạm thời → thử lại
        answer, errors = "", []
        async for event in director.run_scene(actor, hero, "Ông biết gì về nước Nga?"):
            if event.kind == "speech":
                answer += event.data.text
            elif event.kind == "scene":
                directive = event.data
            elif event.kind == "error":
                errors.append(str(event.data)[:120])
        if answer.strip():
            break
        print(f"    (lần {attempt + 1}: AI Nhân vật lỗi tạm thời — {errors[:1]}; thử lại)")
        await asyncio.sleep(3)
    head = opening(answer)
    print(f"    mở đầu: {head[:220]}")
    check(any(m in strip_diacritics(normalize_text(head)) for m in BOUNDARY_MARKERS),
          "Mở đầu xác định sinh thời chưa biết đến xứ ấy")
    check(any(m in strip_diacritics(normalize_text(answer)) for m in ("hau the", "doi sau", "nguoi sau", "ke lai")),
          "Sau đó kể theo góc nhìn hậu thế")
    if not check(directive is not None, "AI Đạo diễn chốt cảnh"):
        return
    check(directive.scene_mode == "FOREGROUND_TALKING" and directive.mouth_sync_enabled is True,
          "FOREGROUND_TALKING + mouth_sync_enabled", f"{directive.decided_by}")
    check(TOPIC_KEY_RE.fullmatch(directive.context_topic_key) is not None and directive.beyond_era,
          "context_topic_key chuẩn hóa + beyond_era", f"{directive.context_topic_key} · q={directive.wikimedia_search_query!r}")
    info = await backdrops.resolve(directive)
    ok, detail = backdrop_ok(info)
    check(ok, "Ảnh Mát-xcơ-va đạt chuẩn (hoặc phông trung tính dự phòng)", detail)


async def main() -> int:
    keys = load_api_keys_any()
    pool = GeminiKeyPool(keys, request_timeout_s=REQUEST_TIMEOUT_S, total_deadline_s=EXTERNAL_TIMEOUT_S)
    library = SceneLibrary.load()
    director = DirectorAgent(pool, request_timeout_s=REQUEST_TIMEOUT_S, library=library)
    actor = ActorAgent(pool, models=ACTOR_MODELS, request_timeout_s=REQUEST_TIMEOUT_S)
    backdrops = BackdropResolver(library, WikimediaBackdropFinder(DiskDPCache(), timeout_s=EXTERNAL_TIMEOUT_S))
    try:
        for hero in ("tran_hung_dao", "nguyen_trai"):
            await ask_russia(director, actor, backdrops, hero)

        print("\nTest #2 — phân biệt nhân vật với trận đánh (AI Đạo diễn thật)")
        for tag, hero, question, mode in (
            ("2a", "tran_hung_dao", "Trận Bạch Đằng năm 1288", "BACKGROUND_ACTION"),
            ("2b", "nguyen_trai", "Trận Bạch Đằng", "FOREGROUND_TALKING"),
            ("2c", "quang_trung", "Trận Ngọc Hồi – Đống Đa", "BACKGROUND_ACTION"),
            ("2c'", "quang_trung", "Rạch Gầm – Xoài Mút", "BACKGROUND_ACTION"),
        ):
            d = await direct_llm(director, hero, f"Hãy kể về {question}.")
            check(d.scene_mode == mode and d.mouth_sync_enabled is (mode == "FOREGROUND_TALKING"),
                  f"{tag}: {hero} · {question} → {mode}", f"{d.context_topic_key} ({d.decided_by})")

        print("\nTest #3 — giữ / đổi previous_context_topic_key (AI Đạo diễn thật)")
        first = await direct_llm(director, "tran_hung_dao", "Kể cho ta nghe về trận thủy chiến trên sông năm 1288.")
        check(first.context_topic_key == "bach_dang_1288", "Lượt 1 → bach_dang_1288", f"{first.context_topic_key} ({first.decided_by})")
        follow = await direct_llm(director, "tran_hung_dao", "Rồi sau trận ấy, tướng giặc Ô Mã Nhi ra sao?",
                                  first.context_topic_key)
        check(follow.context_topic_key == first.context_topic_key and not follow.topic_changed,
              "Hỏi tiếp cùng chủ đề → giữ nguyên mã", f"{follow.context_topic_key} ({follow.decided_by})")
        vague = await direct_llm(director, "tran_hung_dao", "Lúc ấy trời đất và con nước ra sao?", first.context_topic_key)
        check(vague.decided_by == "llm" and vague.context_topic_key == first.context_topic_key and not vague.topic_changed,
              "Câu hỏi tiếp không nêu địa danh → AI giữ ĐÚNG mã lượt trước", f"{vague.context_topic_key} ({vague.decided_by})")
        change = await direct_llm(director, "tran_hung_dao", "Ông biết gì về nước Nga?", first.context_topic_key)
        check(change.topic_changed and change.context_topic_key != first.context_topic_key
              and TOPIC_KEY_RE.fullmatch(change.context_topic_key) is not None,
              "Đổi sang nước Nga → mã mới", f"{change.context_topic_key} ({change.decided_by})")
    finally:
        pool.close()
    passed = sum(RESULTS)
    print(f"\nKẾT QUẢ LIVE: {passed} PASS · {len(RESULTS) - passed} FAIL")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
