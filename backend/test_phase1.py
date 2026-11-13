"""Đại Việt Sử Ký — Phase 1 — Tự kiểm thử (Self-Verification).

Chạy từ thư mục gốc dự án:
    python backend/test_phase1.py            # kiểm thử offline + kiểm thử live với Gemini API thật
    python backend/test_phase1.py --offline  # chỉ kiểm thử offline (không gọi API)

Phần OFFLINE (transport giả lập, không tốn quota):
    O1 đọc .env · O2 token bucket · O3 least-connections đa luồng · O4 failover < 5 ms (429 / hết credit /
    key hỏng / 503 / hết sạch key) · O5 cầu nối stream thread → asyncio · O6 DP phân cảnh (so với vét cạn) ·
    O7 DP cache trạng thái cảnh O(1) · O8 cụm chữ & đếm âm tiết · O9 bất biến của AI Đạo diễn.
Phần LIVE (Gemini API thật, 5 key trong ./.env):
    TEST 1 nạp đúng 5 key + chạy đa luồng song song AI Nhân vật ↔ AI Đạo diễn
    TEST 2 "Ông sinh năm bao nhiêu mất năm nhiêu?" → FOREGROUND_TALKING, mouth_sync = True, nền quê hương
    TEST 3 "Trận Bạch Đằng diễn ra thế nào?" → BACKGROUND_ACTION, mouth_sync = False, cảnh trận Bạch Đằng
    TEST 4 failover thật: giả lập 429 ở key đầu tiên, request thật phải chuyển key trong < 5 ms
    TEST 5 tái sử dụng trạng thái cảnh O(1) cho câu hỏi cùng bối cảnh (không tốn request)
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import random
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.actor_agent import (  # noqa: E402
    CHARACTERS,
    SUPPORTED_CHARACTER_IDS,
    ActorAgent,
    PhraseChunker,
    SpeechChunk,
    UnsupportedCharacterError,
    get_character,
)
from backend.agents.director_agent import (  # noqa: E402
    BACKGROUND_BATTLEFIELD,
    BACKGROUND_HOMELAND,
    DirectorAgent,
    SceneDirective,
    Storyboard,
    TurnSummary,
)
from backend.core.dp_optimizer import (  # noqa: E402
    SCENE_BACKGROUND_ACTION,
    SCENE_FOREGROUND_TALKING,
    SegmentationConfig,
    VisualStateCache,
    count_syllables,
    dp_scene_segmentation,
    dp_weighted_edit_distance,
    normalize_text,
    segmentation_cost,
    split_text_units,
    strip_diacritics,
    vietnamese_number_syllables,
)
from backend.core.key_pool import (  # noqa: E402
    DEFAULT_ENV_PATH,
    ErrorKind,
    GeminiAPIError,
    GeminiKeyPool,
    GeminiRestTransport,
    KeyPoolConfigError,
    NoAvailableKeyError,
    TokenBucket,
    classify_http_error,
    key_fingerprint,
    load_api_keys,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except AttributeError:
    pass

QUESTION_BIO = "Ông sinh năm bao nhiêu mất năm nhiêu?"
QUESTION_BATTLE = "Trận Bạch Đằng diễn ra thế nào?"
LIVE_TINY_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
FAILOVER_BUDGET_MS = 5.0

FAKE_KEYS = [f"AQ.FakeTestKey{index}{'x' * 32}" for index in range(5)]

GOOGLE_429_BODY = json.dumps(
    {
        "error": {
            "code": 429,
            "message": "You exceeded your current quota, please check your plan and billing details. For more "
            "information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits.",
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {
                            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                            "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                            "quotaDimensions": {"location": "global", "model": "gemini-3.5-flash-lite"},
                            "quotaValue": "15",
                        }
                    ],
                },
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"},
            ],
        }
    }
)
GOOGLE_DAILY_429_BODY = GOOGLE_429_BODY.replace("PerMinute", "PerDay").replace('"7s"', '"38s"')
GOOGLE_CREDIT_BODY = json.dumps(
    {
        "error": {
            "code": 429,
            "message": "Your prepayment credits are depleted. Please go to AI Studio to manage your project and billing.",
            "status": "RESOURCE_EXHAUSTED",
        }
    }
)
GOOGLE_KEY_INVALID_BODY = json.dumps(
    {
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "status": "INVALID_ARGUMENT",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID", "domain": "googleapis.com"}
            ],
        }
    }
)
GOOGLE_503_BODY = json.dumps(
    {
        "error": {
            "code": 503,
            "message": "This model is currently experiencing high demand. Spikes in demand are usually temporary.",
            "status": "UNAVAILABLE",
        }
    }
)
GOOGLE_400_BODY = json.dumps(
    {"error": {"code": 400, "message": "Invalid JSON payload received.", "status": "INVALID_ARGUMENT"}}
)

SAMPLE_BATTLE_ANSWER = (
    "Năm Mậu Tý 1288, quân Nguyên lại kéo sang lần thứ ba. Ta đã tính kỹ từ trước, cho quân đóng cọc gỗ đầu bịt sắt "
    "xuống lòng sông Bạch Đằng. Khi nước triều lên, ta sai thuyền nhẹ ra khiêu chiến, nhử đoàn chiến thuyền của Ô Mã "
    "Nhi vào trận địa. Đến lúc triều rút, thuyền giặc mắc cọc, vỡ đắm la liệt. Quân ta từ hai bên bờ đổ ra đánh ập, "
    "dùng hỏa công thiêu cháy thuyền giặc. Ô Mã Nhi bị bắt sống, hơn 400 chiến thuyền về tay quân ta. Từ đó giặc "
    "Nguyên không dám nhòm ngó nước Nam nữa. Ấy là nhờ lòng dân đồng sức, tướng sĩ một lòng."
)
SAMPLE_BIO_ANSWER = (
    "Ta sinh vào khoảng năm 1228, có sách chép năm 1231, tại đất Tức Mặc thuộc phủ Thiên Trường. Ngày 20 tháng 8 năm "
    "Canh Tý, tức năm 1300, ta trút hơi thở cuối cùng ở Vạn Kiếp. Ngươi hãy nhớ, khoan thư sức dân là thượng sách giữ "
    "nước."
)


# --------------------------------------------------------------------------------------
# Bộ ghi kết quả
# --------------------------------------------------------------------------------------
class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def check(self, condition: bool, label: str, detail: str = "") -> bool:
        status = "PASS" if condition else "FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"  [{status}] {label}{suffix}")
        if condition:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append(label)
        return condition

    def crash(self, section: str, exc: BaseException) -> None:
        self.failed += 1
        self.failures.append(f"{section}: {type(exc).__name__}: {exc}")
        print(f"  [FAIL] {section} ném lỗi ngoài dự kiến: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def header(title: str) -> None:
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)


def mentions(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    haystack = strip_diacritics(normalize_text(text))
    return any(strip_diacritics(normalize_text(phrase)) in haystack for phrase in phrases)


def short(text: str, limit: int = 220) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# --------------------------------------------------------------------------------------
# Transport giả lập (offline)
# --------------------------------------------------------------------------------------
def fake_response(text: str, model: str) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5},
        "modelVersion": model,
    }


class FakeTransport:
    """Không gọi mạng. ``failures``: danh sách lỗi lần lượt ném ra cho các lần gọi đầu tiên."""

    def __init__(
        self,
        *,
        delay_s: float = 0.0,
        failures: list[GeminiAPIError] | None = None,
        responder: Callable[[str, str, Mapping[str, Any]], str] | None = None,
        stream_pieces: list[str] | None = None,
        stream_fail_after: int | None = None,
        stream_delay_s: float = 0.0,
    ) -> None:
        self.delay_s = delay_s
        self.failures = list(failures or [])
        self.responder = responder
        self.stream_pieces = list(stream_pieces or [])
        self.stream_fail_after = stream_fail_after
        self.stream_delay_s = stream_delay_s
        self.lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.inflight: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.peak_total = 0
        self._total = 0

    def _begin(self, api_key: str, model: str, kind: str) -> GeminiAPIError | None:
        with self.lock:
            self.calls.append(
                {"key": key_fingerprint(api_key), "model": model, "kind": kind, "thread": threading.current_thread().name}
            )
            self.inflight[api_key] = self.inflight.get(api_key, 0) + 1
            self.peak[api_key] = max(self.peak.get(api_key, 0), self.inflight[api_key])
            self._total += 1
            self.peak_total = max(self.peak_total, self._total)
            return self.failures.pop(0) if self.failures else None

    def _end(self, api_key: str) -> None:
        with self.lock:
            self.inflight[api_key] -= 1
            self._total -= 1

    def generate_content(self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float) -> dict[str, Any]:
        failure = self._begin(api_key, model, "generate")
        try:
            if failure is not None:
                raise failure
            if self.delay_s:
                time.sleep(self.delay_s)
            text = self.responder(api_key, model, payload) if self.responder else f"OK từ {model}"
            return fake_response(text, model)
        finally:
            self._end(api_key)

    def stream_generate_content(
        self,
        api_key: str,
        model: str,
        payload: Mapping[str, Any],
        timeout_s: float,
        cancel_event: threading.Event,
    ) -> Iterator[dict[str, Any]]:
        failure = self._begin(api_key, model, "stream")
        try:
            if failure is not None:
                raise failure
            for index, piece in enumerate(self.stream_pieces):
                if cancel_event.is_set():
                    return
                if self.stream_fail_after is not None and index == self.stream_fail_after:
                    raise GeminiAPIError(ErrorKind.NETWORK, "Mất kết nối giữa dòng (giả lập).", model=model)
                if self.stream_delay_s:
                    time.sleep(self.stream_delay_s)
                last = index == len(self.stream_pieces) - 1
                event: dict[str, Any] = {
                    "candidates": [
                        {"content": {"role": "model", "parts": [{"text": piece}]}, **({"finishReason": "STOP"} if last else {})}
                    ],
                    "modelVersion": model,
                }
                if last:
                    event["usageMetadata"] = {"totalTokenCount": 42}
                yield event
        finally:
            self._end(api_key)


class FaultInjectingTransport:
    """Bọc transport thật: ném trước các lỗi giả lập (không gọi mạng), sau đó gọi Gemini thật."""

    def __init__(self, inner: GeminiRestTransport, failures: list[GeminiAPIError]) -> None:
        self.inner = inner
        self.failures = list(failures)
        self.lock = threading.Lock()
        self.injected_on: list[str] = []

    def _maybe_fail(self, api_key: str) -> None:
        with self.lock:
            failure = self.failures.pop(0) if self.failures else None
            if failure is not None:
                self.injected_on.append(key_fingerprint(api_key))
        if failure is not None:
            raise failure

    def generate_content(self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float) -> dict[str, Any]:
        self._maybe_fail(api_key)
        return self.inner.generate_content(api_key, model, payload, timeout_s)

    def stream_generate_content(
        self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float, cancel_event: threading.Event
    ) -> Iterator[dict[str, Any]]:
        self._maybe_fail(api_key)
        return self.inner.stream_generate_content(api_key, model, payload, timeout_s, cancel_event)


def http_error(status: int, body: str, model: str = "fake-model") -> GeminiAPIError:
    return classify_http_error(status, body, None, model=model)


# --------------------------------------------------------------------------------------
# OFFLINE
# --------------------------------------------------------------------------------------
def test_o1_env_loader(c: Checker) -> None:
    header("O1 · Đọc API key từ .env (dòng key trần AQ.xxxxxxxx... không cần dấu '=')")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bare = tmp_path / "bare.env"
        bare.write_bytes(
            ("﻿" + "\r\n".join(["# 5 key Google AI Studio", FAKE_KEYS[0], "", FAKE_KEYS[1] + "   # key 2",
                                     FAKE_KEYS[2], FAKE_KEYS[2], f'"{FAKE_KEYS[3]}"', FAKE_KEYS[4], ""])).encode("utf-8")
        )
        keys = load_api_keys(bare)
        c.check(keys == FAKE_KEYS, "Dòng key trần + BOM + CRLF + chú thích + key trùng → đúng 5 key theo thứ tự")

        mixed = tmp_path / "mixed.env"
        mixed.write_text(
            "\n".join([f"GEMINI_KEY_1={FAKE_KEYS[0]}", f'export GEMINI_KEY_2="{FAKE_KEYS[1]}"',
                       f"GEMINI_KEY_3='{FAKE_KEYS[2]}' # chú thích", "PORT=8000", FAKE_KEYS[3], FAKE_KEYS[4]]),
            encoding="utf-8",
        )
        c.check(load_api_keys(mixed) == FAKE_KEYS, "Trộn NAME=value, export, nháy, biến không phải key → đúng 5 key")

        four = tmp_path / "four.env"
        four.write_text("\n".join(FAKE_KEYS[:4]), encoding="utf-8")
        try:
            load_api_keys(four)
            c.check(False, "Thiếu key (4/5) phải báo lỗi KeyPoolConfigError")
        except KeyPoolConfigError as exc:
            c.check("đúng 5" in str(exc), "Thiếu key (4/5) → KeyPoolConfigError", short(str(exc), 120))

        broken = tmp_path / "broken.env"
        broken.write_text("\n".join([*FAKE_KEYS[:4], "AQ.bad key"]), encoding="utf-8")
        try:
            load_api_keys(broken)
            c.check(False, "Dòng key sai định dạng phải bị báo lỗi kèm số dòng")
        except KeyPoolConfigError as exc:
            c.check("[5]" in str(exc), "Dòng key sai định dạng → báo đúng số dòng", short(str(exc), 120))

        try:
            load_api_keys(tmp_path / "missing.env")
            c.check(False, "Thiếu file .env phải báo lỗi")
        except KeyPoolConfigError:
            c.check(True, "Thiếu file .env → KeyPoolConfigError")

    if DEFAULT_ENV_PATH.is_file():
        real = load_api_keys()
        prints = [key_fingerprint(key) for key in real]
        c.check(
            len(real) == 5 and len(set(real)) == 5 and all(key.startswith("AQ.") for key in real),
            "./.env thật: đúng 5 key AQ.… khác nhau",
            ", ".join(prints),
        )


def test_o2_token_bucket(c: Checker) -> None:
    header("O2 · Token Bucket")
    bucket = TokenBucket(capacity=2, refill_per_s=10.0, now=0.0)
    first, second, third = bucket.try_consume(0.0), bucket.try_consume(0.0), bucket.try_consume(0.0)
    c.check(first and second and not third, "Xô 2 token: 2 lần lấy đầu thành công, lần 3 bị chặn")
    wait = bucket.seconds_until(0.0)
    c.check(abs(wait - 0.1) < 1e-9, "Thời gian chờ token kế tiếp = 1/rate = 0.1 s", f"{wait:.3f}s")
    c.check(bucket.try_consume(0.1), "Sau 0.1 s nạp lại đủ 1 token")
    c.check(abs(bucket.available(100.0) - 2.0) < 1e-9, "Không nạp quá dung lượng")


async def test_o3_least_connections(c: Checker) -> None:
    header("O3 · Least-Connections + ThreadPoolExecutor + asyncio (20 request song song, 5 key)")
    transport = FakeTransport(delay_s=0.25)
    pool = GeminiKeyPool(FAKE_KEYS, requests_per_minute_per_key=6000, burst_per_key=10, max_inflight_per_key=4,
                         transport=transport)
    try:
        started = time.perf_counter()
        results = await asyncio.gather(*(pool.generate("fake-model", {"contents": []}, tag=f"lc:{i}") for i in range(20)))
        elapsed = time.perf_counter() - started
        per_key = {label: sum(1 for r in results if r.key_label == label) for label in pool.key_labels}
        c.check(all(count == 4 for count in per_key.values()), "Phân tải đều: mỗi key nhận đúng 4 request", str(per_key))
        c.check(max(transport.peak.values()) <= 4, "Không key nào vượt trần 4 request song song",
                f"peak/key={max(transport.peak.values())}")
        c.check(transport.peak_total == 20, "20 request chạy đồng thời trên worker thread", f"peak={transport.peak_total}")
        c.check(elapsed < 1.0, "Thời gian ≈ 1 lượt (song song thật, không tuần tự 5 s)", f"{elapsed * 1000:.0f} ms")
        threads = {r.thread_name for r in results}
        c.check(len(threads) >= 5 and threading.current_thread().name not in threads,
                "Request chạy trên các worker thread riêng (không phải MainThread)", f"{len(threads)} thread")
    finally:
        pool.close()

    bucket_transport = FakeTransport(delay_s=0.0)
    limited = GeminiKeyPool(FAKE_KEYS[:1], requests_per_minute_per_key=600, burst_per_key=2, transport=bucket_transport)
    try:
        started = time.perf_counter()
        for _ in range(3):
            await limited.generate("fake-model", {"contents": []})
        elapsed = time.perf_counter() - started
        c.check(elapsed >= 0.08, "Token bucket giới hạn tốc độ: request thứ 3 phải chờ token (~0.1 s)", f"{elapsed * 1000:.0f} ms")
    finally:
        limited.close()


async def test_o4_failover(c: Checker) -> None:
    header("O4 · Failover < 5 ms khi gặp 429 / hết quota / hết credit / key hỏng / 503")
    kinds = {
        "429 per-minute": (http_error(429, GOOGLE_429_BODY), ErrorKind.RATE_LIMITED),
        "429 per-day": (http_error(429, GOOGLE_DAILY_429_BODY), ErrorKind.QUOTA_EXHAUSTED),
        "hết credit": (http_error(429, GOOGLE_CREDIT_BODY), ErrorKind.CREDIT_EXHAUSTED),
        "key không hợp lệ": (http_error(400, GOOGLE_KEY_INVALID_BODY), ErrorKind.KEY_INVALID),
        "503 quá tải": (http_error(503, GOOGLE_503_BODY), ErrorKind.SERVER_ERROR),
        "400 payload sai": (http_error(400, GOOGLE_400_BODY), ErrorKind.BAD_REQUEST),
    }
    for label, (error, expected) in kinds.items():
        c.check(error.kind is expected, f"Phân loại lỗi '{label}' → {expected.value}", f"retry_after={error.retry_after_s}")

    transport = FakeTransport(failures=[http_error(429, GOOGLE_429_BODY)])
    pool = GeminiKeyPool(FAKE_KEYS, transport=transport)
    try:
        result = await pool.generate("fake-model", {"contents": []}, tag="failover:429")
        failed_key = transport.calls[0]["key"]
        c.check(result.attempts == 2 and result.failovers == 1, "429 → tự chuyển sang key khác và thành công",
                f"attempts={result.attempts}")
        c.check(key_fingerprint(FAKE_KEYS[result.key_index]) != failed_key, "Key phục vụ khác key bị 429")
        latency = result.failover_latencies_ms[0]
        c.check(latency < FAILOVER_BUDGET_MS, "Thời gian chuyển key < 5 ms", f"{latency:.4f} ms")
        snapshot = {row["fingerprint"]: row for row in pool.snapshot()}
        cooldown = snapshot[failed_key]["model_cooldowns_s"].get("fake-model", 0.0)
        c.check(6.0 < cooldown <= 7.0, "Key bị 429 được cooldown theo RetryInfo (7 s)", f"{cooldown:.2f}s")
        follow_ups = await asyncio.gather(*(pool.generate("fake-model", {"contents": []}) for _ in range(8)))
        c.check(all(key_fingerprint(FAKE_KEYS[r.key_index]) != failed_key for r in follow_ups),
                "Trong thời gian cooldown, key bị 429 không được chọn lại")
    finally:
        pool.close()

    credit_transport = FakeTransport(failures=[http_error(429, GOOGLE_CREDIT_BODY)])
    pool = GeminiKeyPool(FAKE_KEYS, transport=credit_transport)
    try:
        result = await pool.generate("fake-model", {"contents": []})
        snapshot = {row["fingerprint"]: row for row in pool.snapshot()}
        failed = snapshot[credit_transport.calls[0]["key"]]
        c.check(result.failovers == 1 and result.failover_latencies_ms[0] < FAILOVER_BUDGET_MS,
                "Hết credit → chuyển key < 5 ms", f"{result.failover_latencies_ms[0]:.4f} ms")
        c.check(failed["key_cooldown_s"] > 3500, "Key hết credit bị khoá cả key ~1 giờ", f"{failed['key_cooldown_s']:.0f}s")
    finally:
        pool.close()

    invalid_transport = FakeTransport(failures=[http_error(400, GOOGLE_KEY_INVALID_BODY)])
    pool = GeminiKeyPool(FAKE_KEYS, transport=invalid_transport)
    try:
        result = await pool.generate("fake-model", {"contents": []})
        snapshot = {row["fingerprint"]: row for row in pool.snapshot()}
        c.check(result.failovers == 1 and snapshot[invalid_transport.calls[0]["key"]]["disabled"] is not None,
                "Key không hợp lệ → bị vô hiệu hoá, request chuyển key khác")
    finally:
        pool.close()

    overload_transport = FakeTransport(failures=[http_error(503, GOOGLE_503_BODY, "model-a")])
    pool = GeminiKeyPool(FAKE_KEYS, transport=overload_transport)
    try:
        result = await pool.generate(("model-a", "model-b"), {"contents": []})
        overloaded_key = overload_transport.calls[0]["key"]
        c.check(result.model == "model-b" and key_fingerprint(FAKE_KEYS[result.key_index]) != overloaded_key
                and result.failover_latencies_ms[0] < FAILOVER_BUDGET_MS,
                "503 model quá tải → chuyển sang model dự phòng trên key khác < 5 ms",
                f"model={result.model}, {result.key_label}, {result.failover_latencies_ms[0]:.4f} ms")
        degraded = pool.degraded_models()
        follow_up = await pool.generate(("model-a", "model-b"), {"contents": []})
        c.check("model-a" in degraded and follow_up.model == "model-b" and follow_up.attempts == 1,
                "Circuit breaker: request kế tiếp đi thẳng vào model khoẻ, không phải chờ lại model quá tải",
                f"degraded={degraded}")
    finally:
        pool.close()

    bad_transport = FakeTransport(failures=[http_error(400, GOOGLE_400_BODY)])
    pool = GeminiKeyPool(FAKE_KEYS, transport=bad_transport)
    try:
        await pool.generate("fake-model", {"contents": []})
        c.check(False, "400 payload sai không được thử lại")
    except GeminiAPIError as exc:
        c.check(exc.kind is ErrorKind.BAD_REQUEST and len(bad_transport.calls) == 1,
                "400 payload sai → báo lỗi ngay, không đốt thêm key", f"calls={len(bad_transport.calls)}")
    finally:
        pool.close()

    exhausted = FakeTransport(failures=[http_error(429, GOOGLE_429_BODY.replace('"7s"', '"30s"')) for _ in range(5)])
    pool = GeminiKeyPool(FAKE_KEYS, transport=exhausted, total_deadline_s=0.6)
    try:
        started = time.perf_counter()
        await pool.generate("fake-model", {"contents": []})
        c.check(False, "Cả 5 key cùng 429 phải báo NoAvailableKeyError")
    except NoAvailableKeyError as exc:
        elapsed = time.perf_counter() - started
        latencies = pool.failover_latencies_ms()
        c.check(len(exhausted.calls) == 5 and len({call["key"] for call in exhausted.calls}) == 5,
                "Cả 5 key đều bị 429 → đã thử lần lượt đủ 5 key")
        c.check(len(latencies) == 4 and max(latencies) < FAILOVER_BUDGET_MS,
                "4 lần chuyển key liên tiếp đều < 5 ms", f"max={max(latencies):.4f} ms")
        c.check(elapsed < 1.5 and exc.last_error is not None, "Hết sạch key → dừng đúng hạn chót, kèm lỗi gần nhất",
                f"{elapsed * 1000:.0f} ms")
    finally:
        pool.close()

    # 10 vòng × 4 request; mỗi request: lần thử đầu bị 429 (cooldown 30 s), lần sau thành công trên key khác.
    # Mỗi vòng dùng pool mới nên lúc failover luôn còn key khả dụng → mọi lần chuyển key phải < 5 ms.
    latencies: list[float] = []
    for _ in range(10):
        script: list[GeminiAPIError | None] = []
        for _ in range(4):
            script += [http_error(429, GOOGLE_429_BODY.replace('"7s"', '"30s"')), None]
        round_pool = GeminiKeyPool(FAKE_KEYS, transport=FakeTransport(failures=script))  # type: ignore[arg-type]
        try:
            for _ in range(4):
                result = await round_pool.generate("fake-model", {"contents": []})
                latencies.extend(result.failover_latencies_ms)
        finally:
            round_pool.close()
    c.check(len(latencies) == 40 and max(latencies) < FAILOVER_BUDGET_MS,
            "40 lần failover (mỗi request gặp một 429): tất cả < 5 ms",
            f"max={max(latencies):.4f} ms, tb={sum(latencies) / len(latencies):.4f} ms")


async def test_o5_stream_bridge(c: Checker) -> None:
    header("O5 · Stream SSE: worker thread → asyncio.Queue → async iterator")
    pieces = ["Ta sinh", " vào khoảng năm 1228,", " tại hương Tức Mặc.", " Ta mất năm 1300."]
    transport = FakeTransport(failures=[http_error(429, GOOGLE_429_BODY)], stream_pieces=pieces, stream_delay_s=0.02)
    pool = GeminiKeyPool(FAKE_KEYS, transport=transport)
    try:
        deltas = [delta async for delta in pool.stream("fake-model", {"contents": []}, tag="actor:offline")]
        c.check("".join(d.text for d in deltas) == "".join(pieces), "Nhận đủ delta theo đúng thứ tự")
        trace = pool.traces("actor:offline")[-1]
        c.check(trace.attempts == 2 and trace.failover_latencies_ms[0] < FAILOVER_BUDGET_MS,
                "429 trước byte đầu tiên → failover stream < 5 ms", f"{trace.failover_latencies_ms[0]:.4f} ms")
        c.check(deltas[-1].finish_reason == "STOP" and deltas[-1].usage is not None, "Delta cuối có finishReason + usage")
        c.check(trace.thread_name != threading.current_thread().name, "Stream đọc trên worker thread", trace.thread_name)
    finally:
        pool.close()

    broken = FakeTransport(stream_pieces=pieces, stream_fail_after=2)
    pool = GeminiKeyPool(FAKE_KEYS, transport=broken)
    received: list[str] = []
    try:
        async for delta in pool.stream("fake-model", {"contents": []}):
            received.append(delta.text)
        c.check(False, "Lỗi giữa dòng phải được ném ra")
    except GeminiAPIError as exc:
        c.check(exc.partial_output and len(broken.calls) == 1 and len(received) == 2,
                "Lỗi giữa dòng → báo lỗi (partial_output), không phát lặp nội dung trên key khác")
    finally:
        pool.close()

    actor_transport = FakeTransport(stream_pieces=pieces)
    pool = GeminiKeyPool(FAKE_KEYS, transport=actor_transport)
    try:
        actor = ActorAgent(pool, models=("fake-model",))
        chunks = [chunk async for chunk in actor.stream_answer("tran_hung_dao", QUESTION_BIO)]
        c.check("".join(chunk.text for chunk in chunks) == "".join(pieces), "AI Nhân vật: nối các cụm chữ = nguyên văn")
        c.check(chunks[-1].is_final and not any(chunk.is_final for chunk in chunks[:-1]), "Chỉ cụm cuối có is_final=True")
        c.check(all(chunk.speak_ms == chunk.syllables * actor.ms_per_syllable for chunk in chunks),
                "Mỗi cụm có thời lượng cử động miệng = số âm tiết × ms/âm tiết")
        try:
            async for _ in actor.stream_answer("le_loi", "Ông là ai?"):
                pass
            c.check(False, "Nhân vật ngoài danh sách phải bị từ chối")
        except UnsupportedCharacterError:
            c.check(True, "Nhân vật ngoài 3 nhân vật được hỗ trợ → UnsupportedCharacterError")
    finally:
        pool.close()


def _brute_force_min_cost(units: tuple, config: SegmentationConfig) -> float:
    count = len(units)
    best = float("inf")
    for mask in range(1 << (count - 1)):
        cuts = [i + 1 for i in range(count - 1) if mask >> i & 1]
        bounds = list(zip([0, *cuts], [*cuts, count]))
        best = min(best, segmentation_cost(units, bounds, config))
    return best


def test_o6_dp_segmentation(c: Checker) -> None:
    header("O6 · Thuật toán 1 — dp_scene_segmentation (DP[i] = min DP[j] + cost(j, i))")
    config = SegmentationConfig()
    result = dp_scene_segmentation(SAMPLE_BATTLE_ANSWER, config)
    for segment in result.segments:
        print(f"    cảnh {segment.index}: {segment.start_ms / 1000:5.1f}s +{segment.duration_ms / 1000:4.1f}s "
              f"[{segment.dominant_visual or '-'}] {short(segment.text, 90)}")
    c.check("".join(s.text for s in result.segments) == SAMPLE_BATTLE_ANSWER, "Các phân cảnh phủ kín và đúng thứ tự văn bản")
    contiguous = all(a.char_end == b.char_start for a, b in zip(result.segments, result.segments[1:]))
    c.check(contiguous and result.segments[0].char_start == 0, "Biên ký tự liên tiếp, không chồng lấn")
    c.check(all(s.duration_ms <= config.max_ms or s.unit_end - s.unit_start == 1 for s in result.segments),
            "Mọi cảnh nhiều đơn vị đều ≤ T_max")
    c.check(2 <= len(result.segments) <= 6, "Câu trả lời ~30 s được chia thành 2–6 cảnh", f"{len(result.segments)} cảnh")
    brute = _brute_force_min_cost(result.units, config)
    c.check(abs(brute - result.total_cost) < 1e-9, "Chi phí DP = chi phí tối ưu tìm bằng vét cạn 2^(n−1) cách chia",
            f"DP={result.total_cost:.6f}, vét cạn={brute:.6f}, n={len(result.units)}")

    rng = random.Random(20261112)
    sentences = [
        "Quân giặc kéo đến đông như kiến cỏ.", "Ta truyền lệnh cho tướng sĩ giữ vững thành.", "Đêm ấy trời tối như mực.",
        "Thuyền giặc mắc cọc giữa dòng sông, vỡ tan tành.", "Dân làng mang cơm nước ra khao quân.",
        "Ta cưỡi ngựa đi tuần quanh đồn, lòng nóng như lửa đốt.", "Gió thổi mạnh, cờ bay phần phật trên mặt thành.",
        "Sáng hôm sau, ta xuất quân đánh úp doanh trại giặc, phá tan chúng.", "Trăm họ được yên ổn làm ăn.",
        "Ấy là nhờ lòng dân đồng sức.",
    ]
    optimal = True
    worst_gap = 0.0
    for _ in range(25):
        text = " ".join(rng.sample(sentences, rng.randint(3, 9)))
        trial = dp_scene_segmentation(text, config)
        if len(trial.units) > 12:
            continue
        gap = abs(_brute_force_min_cost(trial.units, config) - trial.total_cost)
        worst_gap = max(worst_gap, gap)
        optimal &= gap < 1e-9 and "".join(s.text for s in trial.segments) == text
    c.check(optimal, "25 văn bản ngẫu nhiên: DP luôn đạt tối ưu toàn cục và phủ kín văn bản", f"sai lệch lớn nhất={worst_gap:.2e}")

    run_on = "ta " * 160
    long_result = dp_scene_segmentation(run_on.strip(), config)
    c.check(len(long_result.segments) >= 3 and all(s.duration_ms <= config.max_ms for s in long_result.segments),
            "Văn bản dài không dấu câu vẫn được chia theo cửa sổ từ, mỗi cảnh ≤ T_max", f"{len(long_result.segments)} cảnh")
    c.check(dp_scene_segmentation("   ").segments == (), "Văn bản rỗng → không có cảnh")
    single = dp_scene_segmentation("Ta là Trần Quốc Tuấn.")
    c.check(len(single.segments) == 1, "Câu ngắn → đúng 1 cảnh")
    units = split_text_units(SAMPLE_BIO_ANSWER)
    c.check("".join(u.text for u in units) == SAMPLE_BIO_ANSWER, "Tách đơn vị không mất ký tự")


def test_o7_visual_cache(c: Checker) -> None:
    header("O7 · Thuật toán 2 — dp_visual_state_cache (edit distance DP + tra băm O(1))")
    identical = dp_weighted_edit_distance(("trận", "bạch", "đằng"), (1.5, 3.0, 3.0), ("trận", "bạch", "đằng"), (1.5, 3.0, 3.0))
    c.check(identical.similarity == 1.0 and identical.distance == 0.0, "Hai chuỗi giống hệt → sim = 1")
    plain = dp_weighted_edit_distance(("bach", "dang"), (3.0, 3.0), ("bạch", "đằng"), (3.0, 3.0))
    c.check(plain.similarity == 1.0, "Gõ không dấu 'bach dang' khớp 'bạch đằng'")
    one_edit = dp_weighted_edit_distance(("sinh", "năm"), (1.0, 1.0), ("mất", "năm"), (1.0, 1.0))
    c.check(abs(one_edit.distance - 1.0) < 1e-9 and abs(one_edit.similarity - 0.5) < 1e-9, "Một phép thay thế → d = 1, sim = 0.5")

    pool = GeminiKeyPool(FAKE_KEYS, transport=FakeTransport())
    director = DirectorAgent(pool)
    cache: VisualStateCache[str] = VisualStateCache()
    bio = director.analyze("tran_hung_dao", QUESTION_BIO)
    battle = director.analyze("tran_hung_dao", QUESTION_BATTLE)
    c.check(bio.intent == "TALK" and bio.signature == ("tran_hung_dao", SCENE_FOREGROUND_TALKING, "homeland"),
            "Phân tích câu hỏi tiểu sử → ý định TALK, chữ ký (nhân vật, FOREGROUND_TALKING, homeland)")
    c.check(battle.intent == "ACTION" and battle.entities[0] == "bach_dang_1288",
            "Phân tích câu hỏi trận Bạch Đằng → ý định ACTION, thực thể bach_dang_1288")
    c.check(cache.lookup(bio).state is None, "Cache rỗng → miss")
    cache.store(bio, "STATE_HOMELAND", state_id="vs-a", scene_mode=SCENE_FOREGROUND_TALKING, context_key="homeland")
    cache.store(battle, "STATE_BACH_DANG", state_id="vs-b", scene_mode=SCENE_BACKGROUND_ACTION, context_key="bach_dang_1288")
    for index in range(120):
        filler = director.analyze("nguyen_trai", f"Câu hỏi thứ {index} về thơ văn Ức Trai?")
        cache.store(filler, f"FILLER_{index}", state_id=f"vs-f{index}", scene_mode=SCENE_FOREGROUND_TALKING,
                    context_key=f"filler_{index}")
    hometown_question = director.analyze("tran_hung_dao", "Quê quán của ông ở đâu?")
    started = time.perf_counter()
    hometown = cache.lookup(hometown_question)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    c.check(hometown.state == "STATE_HOMELAND" and hometown.decision.match_type == "signature" and hometown.decision.compared == 0,
            "Cache 122 trạng thái: câu hỏi cùng bối cảnh quê hương → tái sử dụng bằng tra băm O(1), không cần so khớp",
            f"{elapsed_ms:.3f} ms")
    follow = cache.lookup(director.analyze("tran_hung_dao", "Quân Nguyên đã thua ở sông Bạch Đằng ra sao?"))
    c.check(follow.state == "STATE_BACH_DANG", "Câu hỏi tiếp về Bạch Đằng → tái sử dụng cảnh Bạch Đằng")
    other_battle = cache.lookup(director.analyze("tran_hung_dao", "Trận Chi Lăng diễn ra thế nào?"))
    c.check(other_battle.state is None, "Trận khác (Chi Lăng) → không tái sử dụng nhầm cảnh Bạch Đằng",
            f"sim={other_battle.decision.similarity:.3f}")
    cross = cache.lookup(director.analyze("nguyen_trai", "Quê quán của ông ở đâu?"))
    c.check(cross.state is None, "Nhân vật khác → không dùng chung trạng thái")
    general_a = director.analyze("tran_hung_dao", "Ông nghĩ gì về lòng yêu nước?")
    cache.store(general_a, "STATE_GENERAL", state_id="vs-c", scene_mode=SCENE_FOREGROUND_TALKING, context_key="homeland")
    general_b = cache.lookup(director.analyze("tran_hung_dao", "Ông nghĩ gì về lòng yêu nước của thanh niên?"))
    c.check(general_b.state == "STATE_GENERAL" and general_b.decision.match_type == "alignment",
            "Câu hỏi diễn đạt gần giống (không có chữ ký chắc chắn) → khớp bằng DP alignment",
            f"{general_b.decision.match_type}, sim={general_b.decision.similarity:.3f}")
    small: VisualStateCache[str] = VisualStateCache(capacity=3)
    small.store(bio, "STATE_HOMELAND", state_id="vs-a", scene_mode=SCENE_FOREGROUND_TALKING, context_key="homeland")
    small.store(battle, "STATE_BACH_DANG", state_id="vs-b", scene_mode=SCENE_BACKGROUND_ACTION, context_key="bach_dang_1288")
    for index in range(2):
        filler = director.analyze("nguyen_trai", f"Câu hỏi thứ {index} về thơ văn Ức Trai?")
        small.store(filler, f"FILLER_{index}", state_id=f"vs-f{index}", scene_mode=SCENE_FOREGROUND_TALKING,
                    context_key=f"filler_{index}")
    c.check(len(small) == 3 and small.lookup(hometown_question).state is None
            and small.lookup(director.analyze("tran_hung_dao", QUESTION_BATTLE)).state == "STATE_BACH_DANG",
            "LRU: vượt dung lượng thì đẩy trạng thái cũ nhất ra, xoá cả chữ ký và anchor của nó")
    pool.close()


def test_o8_chunker(c: Checker) -> None:
    header("O8 · Cụm chữ cho đồng bộ cử động miệng & đếm âm tiết tiếng Việt")
    c.check(vietnamese_number_syllables("1288") == 7, "1288 → 'một nghìn hai trăm tám mươi tám' = 7 âm tiết")
    c.check(vietnamese_number_syllables("1300") == 4 and vietnamese_number_syllables("1005") == 6,
            "1300 → 4 âm tiết, 1005 → 'một nghìn không trăm linh năm' = 6")
    c.check(count_syllables("Ta sinh năm 1228.") == 3 + 7, "Đếm âm tiết câu có số")
    rng = random.Random(7)
    lossless = True
    max_words_ok = True
    for _ in range(200):
        chunker = PhraseChunker(max_words=6)
        cursor = 0
        phrases = []
        while cursor < len(SAMPLE_BATTLE_ANSWER):
            step = rng.randint(1, 25)
            phrases += chunker.feed(SAMPLE_BATTLE_ANSWER[cursor : cursor + step])
            cursor += step
        phrases += chunker.flush()
        lossless &= "".join(p.text for p in phrases) == SAMPLE_BATTLE_ANSWER
        lossless &= all(a.char_end == b.char_start for a, b in zip(phrases, phrases[1:]))
        max_words_ok &= all(len(p.text.split()) <= 7 for p in phrases)
    c.check(lossless, "200 cách cắt delta ngẫu nhiên: nối cụm chữ luôn = nguyên văn, biên liên tiếp")
    c.check(max_words_ok, "Mỗi cụm chữ ngắn (≤ 6 từ + dấu câu) để khẩu hình mượt")
    chunker = PhraseChunker(max_words=6)
    phrases = chunker.feed("Năm 1.288 quân ta") + chunker.feed(" thắng lớn.") + chunker.flush()
    c.check([p.text for p in phrases] == ["Năm 1.288 quân ta thắng lớn."], "Không cắt nhầm số có dấu chấm (1.288)",
            str([p.text for p in phrases]))


async def test_o9_director_invariants(c: Checker) -> None:
    header("O9 · Bất biến của AI Đạo diễn (ép đúng dù AI trả lời lệch)")

    def responder(api_key: str, model: str, payload: Mapping[str, Any]) -> str:
        prompt = payload["contents"][0]["parts"][0]["text"]
        if "PHÂN CẢNH" in prompt:
            return json.dumps({"beats": [{"segment_index": 0, "character_action": "pointing at the river",
                                          "scene_prompt": "Beat zero."}]})
        # AI cố tình đề xuất scene_mode SAI → code phải ghi đè theo HERO_EVENTS.
        if "Bạch Đằng" in prompt.split("CÂU HỎI NGƯỜI DÙNG")[-1]:
            return json.dumps({"reason": "Hỏi về trận đánh.", "scene_mode": "FOREGROUND_TALKING",
                               "question_kind": "historical_topic", "keep_previous_topic": False,
                               "context_topic_key": "Bach Dang 1288", "background_prompt": "A river at dawn.",
                               "character_action": "commanding the fleet", "scene_prompt": "A battle on a river."})
        return json.dumps({"reason": "Hỏi tiểu sử.", "scene_mode": "BACKGROUND_ACTION",
                           "question_kind": "biography", "keep_previous_topic": False,
                           "context_topic_key": "que_huong_tuc_mac", "background_prompt": "A generic village.",
                           "character_action": "", "scene_prompt": "A portrait."})

    pool = GeminiKeyPool(FAKE_KEYS, transport=FakeTransport(responder=responder))
    try:
        director = DirectorAgent(pool, models=("fake-model",))
        bio = await director.direct("tran_hung_dao", QUESTION_BIO)
        c.check(bio.scene_mode == SCENE_FOREGROUND_TALKING and bio.mouth_sync_enabled is True,
                "FOREGROUND_TALKING ⇒ mouth_sync_enabled = True")
        c.check(bio.background_type == BACKGROUND_HOMELAND and bio.context_key == "que_huong_tuc_mac"
                and mentions(bio.background_prompt, get_character("tran_hung_dao").homeland_aliases),
                "Nền bị AI viết lệch → ép về ảnh động quê hương (Tức Mặc / Thiên Trường)")
        c.check(bio.character_reference_path == "/assets/heroes/tran_hung_dao.png"
                and bio.character_reference_path in bio.scene_prompt, "Ảnh mẫu cố định /assets/heroes/tran_hung_dao.png")
        battle = await director.direct("tran_hung_dao", QUESTION_BATTLE)
        c.check(battle.scene_mode == SCENE_BACKGROUND_ACTION and battle.mouth_sync_enabled is False
                and battle.background_type == BACKGROUND_BATTLEFIELD, "BACKGROUND_ACTION ⇒ mouth_sync = False, nền BATTLEFIELD")
        c.check(mentions(battle.background_prompt + " " + battle.scene_prompt, ["bạch đằng"]),
                "Prompt thiếu địa danh → tự bổ sung bối cảnh sông Bạch Đằng")
        board = await director.storyboard(battle, SAMPLE_BATTLE_ANSWER)
        c.check(len(board.beats) >= 2 and all(b.mouth_sync_enabled is False for b in board.beats)
                and all(b.character_reference_path in b.scene_prompt for b in board.beats),
                "Storyboard: mọi beat giữ mode, mouth_sync và ảnh mẫu; beat thiếu được bù từ cảnh gốc",
                f"{len(board.beats)} beat")
        for character_id in SUPPORTED_CHARACTER_IDS:
            prompt = director.build_image_system_prompt(character_id, SCENE_FOREGROUND_TALKING)
            c.check(f"/assets/heroes/{character_id}.png" in prompt and "lip-sync" in prompt,
                    f"System prompt tạo ảnh của {character_id} khoá ảnh mẫu + yêu cầu khẩu hình")
    finally:
        pool.close()

    failing = FakeTransport(failures=[http_error(400, GOOGLE_400_BODY)])
    pool = GeminiKeyPool(FAKE_KEYS, transport=failing)
    try:
        director = DirectorAgent(pool, models=("fake-model",))
        fallback = await director.direct("quang_trung", "Trận Ngọc Hồi – Đống Đa diễn ra thế nào?")
        c.check(fallback.decided_by == "rule_fallback" and fallback.scene_mode == SCENE_BACKGROUND_ACTION
                and fallback.context_key == "ngoc_hoi_dong_da_1789",
                "AI lỗi → phương án dự phòng theo quy tắc vẫn cho cảnh đúng (Ngọc Hồi – Đống Đa)")
        c.check(len(director.cache) == 0, "Kết quả dự phòng không được lưu cache")
    finally:
        pool.close()


# --------------------------------------------------------------------------------------
# LIVE
# --------------------------------------------------------------------------------------
async def collect_turn(director: DirectorAgent, actor: ActorAgent, character_id: str, question: str) -> dict[str, Any]:
    speech: list[SpeechChunk] = []
    directive: SceneDirective | None = None
    storyboard: Storyboard | None = None
    summary: TurnSummary | None = None
    errors: list[Any] = []
    print(f"\n  ❯ Câu hỏi gửi {get_character(character_id).display_name}: \"{question}\"")
    print("  ❯ Lời thoại stream: ", end="", flush=True)
    async for event in director.run_scene(actor, character_id, question):
        if event.kind == "speech":
            speech.append(event.data)
            print(event.data.text, end="", flush=True)
        elif event.kind == "scene":
            directive = event.data
            print(f"\n    ⟨scene sẵn sàng sau {event.t_ms:.0f} ms: {directive.scene_mode}⟩\n    … ", end="", flush=True)
        elif event.kind == "storyboard":
            storyboard = event.data
        elif event.kind == "error":
            errors.append(event.data)
        elif event.kind == "done":
            summary = event.data
    print()
    return {"speech": speech, "directive": directive, "storyboard": storyboard, "summary": summary, "errors": errors}


def print_directive(directive: SceneDirective) -> None:
    print(f"    scene_mode               = {directive.scene_mode}")
    print(f"    mouth_sync_enabled       = {directive.mouth_sync_enabled}")
    print(f"    character_reference_path = {directive.character_reference_path}")
    print(f"    background_type          = {directive.background_type} | context_key = {directive.context_key}")
    print(f"    decided_by               = {directive.decided_by} ({directive.model}, {directive.key_label}, "
          f"{directive.latency_ms:.0f} ms)")
    print(f"    reason                   = {short(directive.reason, 160)}")
    print(f"    background_prompt        = {short(directive.background_prompt)}")
    print(f"    character_action         = {short(directive.character_action)}")
    print(f"    scene_prompt             = {short(directive.scene_prompt, 320)}")


def print_storyboard(board: Storyboard) -> None:
    print(f"    storyboard: {len(board.beats)} phân cảnh (DP cost={board.segmentation_cost:.3f}, {board.decided_by}, "
          f"{board.model}, {board.key_label})")
    for beat in board.beats:
        print(f"      [{beat.beat_index}] {beat.start_ms / 1000:5.1f}s +{beat.duration_ms / 1000:4.1f}s "
              f"mouth_sync={beat.mouth_sync_enabled} | {short(beat.character_action, 90)}")


async def live_tests(c: Checker) -> None:
    header("TEST 1 · Nạp đúng 5 API key từ ./.env và chạy đa luồng song song")
    keys = load_api_keys()
    c.check(len(keys) == 5 and len(set(keys)) == 5, "Nạp đúng 5 API key Google AI Studio khác nhau từ ./.env",
            ", ".join(key_fingerprint(key) for key in keys))
    pool = GeminiKeyPool(keys)
    actor = ActorAgent(pool)
    director = DirectorAgent(pool)
    try:
        tiny = {
            "contents": [{"role": "user", "parts": [{"text": "Chỉ trả lời đúng một từ: OK"}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 16, "thinkingConfig": {"thinkingLevel": "minimal"}},
        }
        started = time.perf_counter()
        warmups = await asyncio.gather(*(pool.generate(LIVE_TINY_MODELS, tiny, tag=f"warmup:{i}") for i in range(5)))
        elapsed = (time.perf_counter() - started) * 1000.0
        for result in warmups:
            print(f"    {result.key_label} · {result.thread_name} · {result.model} · {result.latency_ms:.0f} ms → "
                  f"{result.text.strip()!r}")
        traces = pool.traces("warmup:")
        overlap = max(t.started_at for t in traces) < min(t.finished_at for t in traces)
        c.check(overlap, "5 request khởi động chạy chồng lấn thời gian (song song thật)", f"tổng {elapsed:.0f} ms")
        no_failover = all(result.failovers == 0 for result in warmups)
        distinct = len({result.key_index for result in warmups})
        c.check(distinct == 5 or not no_failover, "Least-Connections rải 5 request lên 5 key khác nhau", f"{distinct} key")
        c.check(len({result.thread_name for result in warmups}) == 5, "5 request chạy trên 5 worker thread khác nhau")
        untested = [row["key"] for row in pool.snapshot() if row["successes"] == 0]
        transport = GeminiRestTransport()
        for label in untested:
            index = pool.key_labels.index(label)
            response = await asyncio.to_thread(transport.generate_content, keys[index], LIVE_TINY_MODELS[0], tiny, 45.0)
            c.check(bool(response.get("candidates")), f"{label} (kiểm tra riêng sau failover) hoạt động")
        c.check(all(row["disabled"] is None for row in pool.snapshot()), "Cả 5 key đều hoạt động (không key nào bị vô hiệu)")

        bio_turn = await collect_turn(director, actor, "tran_hung_dao", QUESTION_BIO)
        actor_trace = pool.traces("actor:tran_hung_dao")[-1]
        director_trace = pool.traces("director:scene:tran_hung_dao")[-1]
        print(f"    AI Nhân vật : {actor_trace.key_label} · {actor_trace.thread_name} · "
              f"{actor_trace.duration_ms:.0f} ms (byte đầu sau "
              f"{((actor_trace.first_byte_at or actor_trace.finished_at) - actor_trace.started_at) * 1000:.0f} ms)")
        print(f"    AI Đạo diễn : {director_trace.key_label} · {director_trace.thread_name} · {director_trace.duration_ms:.0f} ms")
        c.check(actor_trace.overlaps(director_trace), "AI Nhân vật và AI Đạo diễn chạy SONG SONG (khoảng thời gian chồng lấn)")
        c.check(actor_trace.key_index != director_trace.key_index, "AI Đạo diễn chạy trên key KHÁC AI Nhân vật",
                f"{actor_trace.key_label} ≠ {director_trace.key_label}")
        c.check(actor_trace.thread_name != director_trace.thread_name
                and threading.current_thread().name not in (actor_trace.thread_name, director_trace.thread_name),
                "Hai agent chạy trên hai worker thread khác nhau")

        header("TEST 2 · \"Ông sinh năm bao nhiêu mất năm nhiêu?\" → FOREGROUND_TALKING")
        directive = bio_turn["directive"]
        summary = bio_turn["summary"]
        answer = summary.answer_text if summary else ""
        c.check(directive is not None and not bio_turn["errors"], "Lượt hỏi–đáp hoàn tất không lỗi", str(bio_turn["errors"]))
        if directive is not None:
            print_directive(directive)
            profile = get_character("tran_hung_dao")
            c.check(directive.decided_by == "llm", "Quyết định do AI Đạo diễn (Gemini) đưa ra", directive.decided_by)
            c.check(directive.scene_mode == SCENE_FOREGROUND_TALKING, "scene_mode = FOREGROUND_TALKING", directive.scene_mode)
            c.check(directive.mouth_sync_enabled is True, "mouth_sync_enabled = True")
            c.check(directive.background_type == BACKGROUND_HOMELAND and directive.context_key == "que_huong_tuc_mac",
                    "Cảnh nền = ảnh động QUÊ HƯƠNG (HOMELAND)")
            c.check(mentions(directive.background_prompt, profile.homeland_aliases),
                    "Prompt nền nhắc đúng quê hương Tức Mặc / Thiên Trường / Nam Định")
            c.check(directive.character_reference_path == "/assets/heroes/tran_hung_dao.png",
                    "character_reference_path cố định /assets/heroes/tran_hung_dao.png")
            c.check(mentions(directive.character_action, ["speak", "talk", "facing the viewer", "foreground"]),
                    "Nhân vật đứng tiền cảnh nói chuyện")
        print(f"    Câu trả lời: {short(answer, 500)}")
        c.check("1300" in answer, "Trả lời đúng năm mất 1300")
        c.check(any(year in answer for year in ("1228", "1231", "1232")), "Trả lời đúng năm sinh (1228 / 1231 / 1232)")
        c.check(mentions(f" {answer} ", [" ta "]), "Văn phong nhập vai: xưng 'ta'")
        speech = bio_turn["speech"]
        c.check(bool(speech) and "".join(chunk.text for chunk in speech) == answer and speech[-1].is_final,
                "Streaming từng cụm chữ: nối lại = nguyên văn, cụm cuối is_final", f"{len(speech)} cụm")
        first_byte_ms = ((actor_trace.first_byte_at or actor_trace.finished_at) - actor_trace.started_at) * 1000.0
        streaming_ms = (actor_trace.finished_at - (actor_trace.first_byte_at or actor_trace.finished_at)) * 1000.0
        arrival_buckets = {round(chunk.elapsed_ms / 50.0) for chunk in speech}
        c.check(actor_trace.first_byte_at is not None and streaming_ms >= 200 and len(arrival_buckets) >= 3,
                "Stream thật: cụm chữ đầu phát ra khi model còn đang sinh tiếp (không đợi trọn câu trả lời)",
                f"byte đầu sau {first_byte_ms:.0f} ms, stream tiếp {streaming_ms:.0f} ms, {len(arrival_buckets)} đợt nhận")
        board = bio_turn["storyboard"]
        if board is not None:
            print_storyboard(board)
            c.check(all(b.mouth_sync_enabled and b.scene_mode == SCENE_FOREGROUND_TALKING for b in board.beats),
                    "Storyboard giữ FOREGROUND_TALKING + mouth_sync cho mọi phân cảnh")
        c.check(board is not None and board.beats and "".join(b.text for b in board.beats) == answer,
                "Storyboard DP phủ kín câu trả lời")

        header("TEST 3 · \"Trận Bạch Đằng diễn ra thế nào?\" → BACKGROUND_ACTION")
        battle_turn = await collect_turn(director, actor, "tran_hung_dao", QUESTION_BATTLE)
        directive = battle_turn["directive"]
        summary = battle_turn["summary"]
        answer = summary.answer_text if summary else ""
        c.check(directive is not None and not battle_turn["errors"], "Lượt hỏi–đáp hoàn tất không lỗi", str(battle_turn["errors"]))
        if directive is not None:
            print_directive(directive)
            c.check(directive.decided_by == "llm", "Quyết định do AI Đạo diễn (Gemini) đưa ra", directive.decided_by)
            c.check(directive.scene_mode == SCENE_BACKGROUND_ACTION, "scene_mode = BACKGROUND_ACTION", directive.scene_mode)
            c.check(directive.mouth_sync_enabled is False, "mouth_sync_enabled = False")
            c.check(directive.background_type == BACKGROUND_BATTLEFIELD and directive.context_key == "bach_dang_1288",
                    "Cảnh nền = chiến trường Bạch Đằng (BATTLEFIELD, bach_dang_1288)")
            c.check(mentions(directive.background_prompt, ["bạch đằng"]) and mentions(directive.scene_prompt, ["bạch đằng"]),
                    "Prompt nền & scene_prompt dựng cảnh sông Bạch Đằng")
            action_words = ["command", "fight", "battle", "attack", "sword", "charge", "lead", "direct", "signal",
                            "strike", "combat", "order", "rally", "wield", "war boat", "warship"]
            c.check(mentions(directive.character_action, action_words), "Nhân vật đang hành động đánh trận (không đứng nói)",
                    short(directive.character_action, 120))
            c.check(not mentions(directive.character_action, ["lip-sync", "talking to the camera", "facing the viewer and speaking"]),
                    "Không yêu cầu nhép miệng trong cảnh hành động")
        print(f"    Câu trả lời: {short(answer, 700)}")
        c.check("1288" in answer, "Trả lời đúng năm 1288")
        c.check(mentions(answer, ["cọc"]) and mentions(answer, ["triều"]), "Trả lời đúng kế cọc gỗ + thủy triều")
        speech = battle_turn["speech"]
        c.check(bool(speech) and "".join(chunk.text for chunk in speech) == answer, "Streaming từng cụm chữ không mất chữ",
                f"{len(speech)} cụm")
        board = battle_turn["storyboard"]
        if board is not None:
            print_storyboard(board)
            c.check(board.decided_by == "llm" and len(board.beats) >= 2, "AI Đạo diễn viết storyboard nhiều phân cảnh",
                    f"{len(board.beats)} beat")
            c.check(all(not b.mouth_sync_enabled and b.scene_mode == SCENE_BACKGROUND_ACTION for b in board.beats),
                    "Mọi phân cảnh giữ BACKGROUND_ACTION, mouth_sync = False")
            c.check("".join(b.text for b in board.beats) == answer, "Storyboard DP phủ kín câu trả lời")
            storyboard_trace = pool.traces("director:storyboard:tran_hung_dao")[-1]
            actor_key = battle_turn["speech"][-1].key_index if battle_turn["speech"] else -1
            c.check(storyboard_trace.key_index != actor_key, "Đạo diễn storyboard tránh key của AI Nhân vật",
                    f"{storyboard_trace.key_label} ≠ key#{actor_key + 1}")
        else:
            c.check(False, "Có storyboard cho câu trả lời về trận Bạch Đằng")

        header("TEST 5 · Tái sử dụng trạng thái cảnh O(1) khi câu hỏi tiếp theo cùng bối cảnh")
        requests_before = pool.total_requests()
        reuse_bio = await director.direct("tran_hung_dao", "Quê quán của ông ở đâu?")
        reuse_battle = await director.direct("tran_hung_dao", "Quân Nguyên đã thua ở sông Bạch Đằng ra sao?")
        requests_after = pool.total_requests()
        bio_state = bio_turn["directive"].state_id if bio_turn["directive"] else None
        battle_state = battle_turn["directive"].state_id if battle_turn["directive"] else None
        print(f"    'Quê quán của ông ở đâu?' → {reuse_bio.decided_by} ({reuse_bio.cache_match}) state={reuse_bio.state_id} "
              f"{reuse_bio.latency_ms:.3f} ms")
        print(f"    'Quân Nguyên đã thua ở sông Bạch Đằng ra sao?' → {reuse_battle.decided_by} ({reuse_battle.cache_match}) "
              f"state={reuse_battle.state_id} {reuse_battle.latency_ms:.3f} ms")
        c.check(reuse_bio.cache_hit and reuse_bio.state_id == bio_state and reuse_bio.scene_mode == SCENE_FOREGROUND_TALKING,
                "Hỏi quê quán sau khi hỏi năm sinh → dùng lại cảnh quê hương (cùng state_id)")
        c.check(reuse_battle.cache_hit and reuse_battle.state_id == battle_state,
                "Hỏi tiếp về Bạch Đằng → dùng lại cảnh trận Bạch Đằng (cùng state_id)")
        c.check(requests_after == requests_before, "Tái sử dụng không tốn request API nào",
                f"{requests_before} → {requests_after}")
        c.check(reuse_bio.latency_ms < 5.0 and reuse_battle.latency_ms < 5.0, "Tra cache tức thời (< 5 ms)")
    finally:
        pool.close()

    header("TEST 4 · Failover thật: 429 ở key đầu tiên → chuyển key < 5 ms, request thật vẫn thành công")
    injected = FaultInjectingTransport(GeminiRestTransport(), [http_error(429, GOOGLE_429_BODY, LIVE_TINY_MODELS[0])])
    pool = GeminiKeyPool(keys, transport=injected)
    try:
        tiny = {
            "contents": [{"role": "user", "parts": [{"text": "Thủ đô của nước Đại Việt thời Trần tên là gì? Trả lời 2 từ."}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 24, "thinkingConfig": {"thinkingLevel": "minimal"}},
        }
        result = await pool.generate(LIVE_TINY_MODELS, tiny, tag="live-failover")
        print(f"    429 giả lập trên {injected.injected_on[0]} → phục vụ bởi {result.key_label} "
              f"({key_fingerprint(keys[result.key_index])}) · {result.model} · {result.latency_ms:.0f} ms → {result.text.strip()!r}")
        c.check(result.failovers >= 1 and key_fingerprint(keys[result.key_index]) != injected.injected_on[0],
                "Request chuyển sang key khác và nhận câu trả lời thật từ Gemini")
        c.check(result.failover_latencies_ms[0] < FAILOVER_BUDGET_MS, "Thời gian chuyển key < 5 ms",
                f"{result.failover_latencies_ms[0]:.4f} ms")
        print("    Trạng thái pool:")
        for row in pool.snapshot():
            print(f"      {row['key']} {row['fingerprint']} requests={row['requests']} ok={row['successes']} "
                  f"fail={row['failures']} 429={row['rate_limited']} cooldown={row['model_cooldowns_s']}")
    finally:
        pool.close()


# --------------------------------------------------------------------------------------
async def main_async(run_live: bool) -> Checker:
    c = Checker()
    sections: list[tuple[str, Callable[[Checker], Any]]] = [
        ("O1", test_o1_env_loader),
        ("O2", test_o2_token_bucket),
        ("O3", test_o3_least_connections),
        ("O4", test_o4_failover),
        ("O5", test_o5_stream_bridge),
        ("O6", test_o6_dp_segmentation),
        ("O7", test_o7_visual_cache),
        ("O8", test_o8_chunker),
        ("O9", test_o9_director_invariants),
    ]
    if run_live:
        sections.append(("LIVE", live_tests))
    for name, function in sections:
        try:
            outcome = function(c)
            if asyncio.iscoroutine(outcome):
                await outcome
        except Exception as exc:  # một phần lỗi không chặn các phần còn lại
            c.crash(name, exc)
    return c


def main() -> int:
    parser = argparse.ArgumentParser(description="Đại Việt Sử Ký — Phase 1 self-test")
    parser.add_argument("--offline", action="store_true", help="chỉ chạy kiểm thử offline (không gọi Gemini API)")
    args = parser.parse_args()
    started = time.perf_counter()
    c = asyncio.run(main_async(run_live=not args.offline))
    header(f"KẾT QUẢ: {c.passed} PASS · {c.failed} FAIL · {time.perf_counter() - started:.1f} s")
    for failure in c.failures:
        print(f"  ✗ {failure}")
    return 0 if c.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
