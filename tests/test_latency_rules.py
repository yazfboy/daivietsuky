"""Đại Việt Sử Ký — Test CỐ ĐỊNH về độ trễ & khẩu hình (không gọi Gemini thật, không đọc .env).

Chạy từ thư mục gốc:  python tests/test_latency_rules.py
* Key pool: mỗi lần thử bị cắt theo hạn chót còn lại → cả request không vượt EXTERNAL_TIMEOUT_S dù mọi key đều treo;
  chờ byte đầu tách khỏi timeout đọc; 503 "high demand" xoay ngay sang model kế tiếp.
* Máy chủ: lời thoại (cả Gemini đổ dồn lẫn câu trả lời dự phòng) được phát theo nhịp nói, cụm nào cũng có viseme,
  chữ đầu không bị trễ và sự kiện cảnh không bị chặn sau lời thoại.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from backend.agents.actor_agent import ActorAgent  # noqa: E402
from backend.agents.director_agent import DirectorAgent  # noqa: E402
from backend.core.key_pool import (  # noqa: E402
    ErrorKind,
    GeminiAPIError,
    GeminiKeyPool,
    KeyPoolError,
    StreamDelta,
    classify_http_error,
)
from backend.core.scene_library import SceneLibrary  # noqa: E402
from backend.engine.visual_pipeline import BackdropResolver, TextToVisemeDPSync  # noqa: E402
from backend import main as server  # noqa: E402

KEYS = [f"AQ.test-key-{i}-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" for i in range(5)]
RESULTS: list[bool] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    return bool(ok)


class HangingTransport:
    """Mọi key đều treo tới hết timeout rồi báo TIMEOUT (mô phỏng Gemini quá tải không trả lời)."""

    def __init__(self) -> None:
        self.first_byte: list[float] = []
        self.read: list[float | None] = []
        self.generate_timeouts: list[float] = []

    def generate_content(self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float) -> dict[str, Any]:
        self.generate_timeouts.append(timeout_s)
        time.sleep(timeout_s)
        raise GeminiAPIError(ErrorKind.TIMEOUT, "hết thời gian chờ", model=model)

    def stream_generate_content(self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float,
                                cancel_event: threading.Event, *, read_timeout_s: float | None = None) -> Iterator[dict[str, Any]]:
        self.first_byte.append(timeout_s)
        self.read.append(read_timeout_s)
        time.sleep(timeout_s)
        raise GeminiAPIError(ErrorKind.TIMEOUT, "hết thời gian chờ byte đầu", model=model)


class BusyThenOkTransport:
    """Model đầu trả 503 "high demand" ngay; model sau trả lời bình thường."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_content(self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float) -> dict[str, Any]:
        raise NotImplementedError

    def stream_generate_content(self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float,
                                cancel_event: threading.Event) -> Iterator[dict[str, Any]]:
        self.calls.append(model)
        if model.endswith("busy-model"):
            raise classify_http_error(503, '{"error": {"code": 503, "message": "This model is currently experiencing '
                                           'high demand.", "status": "UNAVAILABLE"}}', None, model=model)
        yield {"candidates": [{"content": {"parts": [{"text": "Ta là Trần Hưng Đạo."}]}, "finishReason": "STOP"}]}


async def test_pool_deadline() -> None:
    print("\n1. Key pool: hạn chót 8 s là trần cứng, chờ byte đầu ngắn, 503 xoay ngay")
    transport = HangingTransport()
    pool = GeminiKeyPool(KEYS, transport=transport, request_timeout_s=7.0, total_deadline_s=2.0,
                         first_byte_timeout_s=0.8)
    try:
        started = time.perf_counter()
        try:
            async for _ in pool.stream(("model-a", "model-b"), {"contents": []}):
                pass
            failed = False
        except KeyPoolError:
            failed = True
        elapsed = time.perf_counter() - started
        check(failed and elapsed <= 2.4, "Stream: mọi key treo → bỏ cuộc trong hạn chót (2 s), không cộng dồn 7 s/lần",
              f"{elapsed:.2f}s, {len(transport.first_byte)} lần thử")
        check(bool(transport.first_byte) and max(transport.first_byte) <= 0.8 + 1e-6,
              "Mỗi lần thử chờ byte đầu ≤ first_byte_timeout_s", ", ".join(f"{t:.2f}" for t in transport.first_byte))
        check(all(r == 7.0 for r in transport.read), "Sau byte đầu: timeout đọc giữa hai cụm = request_timeout_s")
        check(len(transport.first_byte) >= 2, "Treo → xoay sang key/model khác thay vì chờ một chỗ")

        started = time.perf_counter()
        try:
            await pool.generate(("model-a",), {"contents": []}, deadline_s=1.5)
            failed = False
        except KeyPoolError:
            failed = True
        elapsed = time.perf_counter() - started
        check(failed and elapsed <= 1.9 and max(transport.generate_timeouts) <= 1.5 + 1e-6,
              "generate: timeout mỗi lần thử bị cắt theo phần hạn chót còn lại", f"{elapsed:.2f}s")
    finally:
        pool.close()

    busy = BusyThenOkTransport()
    pool = GeminiKeyPool(KEYS, transport=busy, total_deadline_s=8.0, first_byte_timeout_s=3.0)
    try:
        started = time.perf_counter()
        deltas = [d async for d in pool.stream(("busy-model", "fast-model"), {"contents": []})]
        elapsed = time.perf_counter() - started
        check(deltas and deltas[0].model == "fast-model" and busy.calls[:2] == ["busy-model", "fast-model"]
              and elapsed < 0.5, "503 high demand → xoay NGAY sang model kế tiếp", f"{elapsed * 1000:.0f} ms")
        check("busy-model" in pool.degraded_models(), "Model 503 bị hạ ưu tiên cho các request sau")
    finally:
        pool.close()


class ScriptedPool:
    """Thay GeminiKeyPool cho cả lượt: AI Đạo diễn trả JSON, AI Nhân vật đổ dồn cả câu trả lời hoặc lỗi."""

    def __init__(self, answer: str | None) -> None:
        self.answer = answer

    async def generate(self, models: Any, payload: Mapping[str, Any], **_: Any) -> SimpleNamespace:
        text = payload["contents"][0]["parts"][0]["text"]
        if "PHÂN CẢNH" in text:
            return SimpleNamespace(text='{"beats": []}', key_label="fake", model="fake")
        return SimpleNamespace(
            text='{"reason": "x", "question_kind": "biography", "keep_previous_topic": false, '
                 '"context_topic_key": "que_huong_tuc_mac", "topic_title_vi": "", "wikimedia_search_query": "", '
                 '"beyond_era": false, "background_prompt": "", "character_action": "", "scene_prompt": ""}',
            key_label="fake", model="fake")

    async def stream(self, models: Any, payload: Mapping[str, Any], **_: Any):
        if self.answer is None:
            raise KeyPoolError("mô phỏng: 5 key đều 503 / quá hạn")
        for index, word in enumerate(self.answer.split(" ")):  # đổ dồn: không chờ giữa các delta
            yield StreamDelta(text=("" if index == 0 else " ") + word, finish_reason=None, usage=None, model="fake",
                              model_version=None, key_index=0, key_label="key#1", request_id=1, elapsed_ms=0.0)

    def degraded_models(self) -> dict[str, float]:
        return {}

    def close(self) -> None:
        return None


def make_services(answer: str | None) -> server.Services:
    pool = ScriptedPool(answer)
    library = SceneLibrary.load()
    return server.Services(
        pool=pool,  # type: ignore[arg-type]
        actor=ActorAgent(pool),  # type: ignore[arg-type]
        director=DirectorAgent(pool, library=library),  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        viseme_sync=TextToVisemeDPSync(),
        backdrops=BackdropResolver(library, None),
        key_fingerprints=(),
    )


async def run(answer: str | None) -> list[dict[str, Any]]:
    return [event async for event in server.run_turn(make_services(answer), "tran_hung_dao", "Ông sinh năm nào?")]


async def test_speech_pacing() -> None:
    print("\n2. Lời thoại phát theo nhịp nói — cả khi Gemini đổ dồn và khi dùng câu trả lời dự phòng")
    answer = ("Ta sinh vào khoảng năm 1228, theo thuyết phổ biến. Ta tạ thế năm 1300 tại thái ấp Vạn Kiếp. "
              "Quê ta ở hương Tức Mặc, phủ Thiên Trường, đất phát tích của hoàng tộc nhà Trần.")
    for label, text in (("Gemini đổ dồn cả câu trả lời", answer), ("AI Nhân vật lỗi → câu trả lời dự phòng", None)):
        events = await run(text)
        speech = [e for e in events if e["type"] == "speech"]
        spoken = [e for e in speech if e["chunk"]["text"].strip()]
        times = [e["t_ms"] for e in speech]
        gaps = [b - a for a, b in zip(times, times[1:])]
        types = [e["type"] for e in events]
        final_scene = next((e for e in events if e["type"] == "scene" and e["sceneData"]["phase"] == "final"), None)
        check(len(spoken) >= 4, f"{label}: có lời thoại", f"{len(spoken)} cụm")
        check(bool(gaps) and min(gaps) >= server.SPEECH_GAP_MIN_S * 1000 * 0.8,
              f"{label}: các cụm được giãn nhịp (không ra cùng một thời điểm)",
              f"khoảng cách {min(gaps):.0f}–{max(gaps):.0f} ms, trải {times[-1] - times[0]:.0f} ms")
        check(all(e["chunk"]["mouthSyncEnabled"] and e["chunk"]["visemes"] for e in spoken),
              f"{label}: mọi cụm có dữ liệu khẩu hình (viseme)")
        check(speech[-1]["chunk"]["is_final"] and sum(e["chunk"]["is_final"] for e in speech) == 1,
              f"{label}: đúng một cụm cuối is_final")
        check(types.index("done") > max(i for i, t in enumerate(types) if t == "speech") and types[-1] == "turn_end",
              f"{label}: done chỉ đến sau khi phát hết lời thoại")
        check(final_scene is not None and final_scene["t_ms"] <= times[-1],
              f"{label}: sự kiện cảnh không bị xếp sau lời thoại")
        if text is not None:
            check(times[0] < 200, f"{label}: chữ đầu tiên không bị trễ", f"{times[0]:.0f} ms")
    check(abs(server.speech_gap_s(1380, 90) - 0.2205) < 1e-6 and server.speech_gap_s(0, 0) == server.SPEECH_GAP_MIN_S
          and server.speech_gap_s(99_999, 0) == server.SPEECH_GAP_MAX_S, "speech_gap_s kẹp đúng [MIN, MAX]")
    check(server.FIRST_BYTE_TIMEOUT_S <= 3.0 and server.EXTERNAL_TIMEOUT_S <= 8.0
          and "gemini-3.5-flash" not in server.ACTOR_MODELS, "Cấu hình: byte đầu ≤ 3 s, hạn chót ≤ 8 s, bỏ model chậm 6–8 s")


async def main() -> int:
    await test_pool_deadline()
    await test_speech_pacing()
    passed = sum(RESULTS)
    print(f"\nKẾT QUẢ: {passed} PASS · {len(RESULTS) - passed} FAIL")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
