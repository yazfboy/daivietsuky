"""Đại Việt Sử Ký — Phase 1 — Bộ quản lý 5 API key Google AI Studio chạy đa luồng.

Kiến trúc
---------
* ``load_api_keys`` đọc file ``./.env`` ở thư mục gốc dự án. Hỗ trợ dòng key trần
  ``AQ.xxxxxxxx...`` (không cần dấu ``=``), dòng ``NAME=value`` / ``export NAME="value"``,
  bỏ qua dòng trống và dòng chú thích ``#``. Mặc định bắt buộc đúng 5 key.
* ``GeminiRestTransport`` gọi REST endpoint chính thức của Google AI Studio
  (``https://generativelanguage.googleapis.com/v1beta``), xác thực bằng header
  ``x-goog-api-key``: ``models/{model}:generateContent`` và
  ``models/{model}:streamGenerateContent?alt=sse`` (Server-Sent Events).
* ``GeminiKeyPool`` kết hợp **asyncio** (tầng điều phối, các agent chạy song song bằng
  ``asyncio.gather``/``create_task``) với **ThreadPoolExecutor** (tầng I/O chặn: mỗi request
  HTTP chạy trên một worker thread riêng).
* Chọn key = **Least-Connections + Token Bucket**: chỉ xét các key còn token trong xô
  (nạp lại theo RPM), không bị cooldown, chưa chạm trần request song song; trong số đó chọn
  key có ít kết nối đang chạy nhất, hoà thì chọn key còn nhiều token nhất rồi xoay vòng.
* **Failover**: khi gặp 429 (rate limit), hết quota, hết credit, key hỏng, 5xx hay lỗi mạng,
  key/model lỗi bị đặt cooldown và request được gửi lại *ngay trong worker thread* trên key
  khác — không quay về event loop, không ``sleep`` — nên thời gian chuyển key thường dưới
  1 ms (yêu cầu < 5 ms). Thời gian này được đo và lưu trong ``RequestTrace``.
* Mỗi request nhận một chuỗi model ưu tiên; model trả 503 "high demand" / timeout bị circuit
  breaker hạ ưu tiên trong ``model_degrade_s`` giây để các request sau dùng ngay model khoẻ.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import inspect
import itertools
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "DEFAULT_ENV_PATH",
    "EXPECTED_KEY_COUNT",
    "GEMINI_API_BASE_URL",
    "ErrorKind",
    "GeminiAPIError",
    "GeminiKeyPool",
    "GeminiRestTransport",
    "GeminiTransport",
    "GenerationResult",
    "KeyPoolClosedError",
    "KeyPoolConfigError",
    "KeyPoolError",
    "MultiThreadKeyPool",
    "NoAvailableKeyError",
    "RequestCancelledError",
    "RequestTrace",
    "StreamDelta",
    "TokenBucket",
    "classify_http_error",
    "extract_candidate_text",
    "key_fingerprint",
    "load_api_keys",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
EXPECTED_KEY_COUNT = 5
GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Key Google AI Studio: định dạng mới "AQ.<...>" và định dạng cũ "AIza<35 ký tự>".
_API_KEY_RE = re.compile(r"^(?:AQ\.[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{35})$")
_ENV_ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_.\-]*)\s*=\s*(.*)$")
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*s?$")
_CREDIT_MARKERS = (
    "credit",
    "prepay",
    "prepaid",
    "depleted",
    "insufficient funds",
    "billing account",
    "billing to be enabled",
    "billing is not enabled",
)
_BLOCKED_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}
)
_INFLIGHT_RECHECK_S = 0.05
_clock = time.perf_counter


# --------------------------------------------------------------------------------------
# Lỗi
# --------------------------------------------------------------------------------------
_MIN_ATTEMPT_TIMEOUT_S = 0.25


class KeyPoolError(Exception):
    """Lỗi gốc của key pool."""


class KeyPoolConfigError(KeyPoolError):
    """File .env hoặc tham số khởi tạo pool không hợp lệ."""


class KeyPoolClosedError(KeyPoolError):
    """Pool đã đóng, không nhận request mới."""


class RequestCancelledError(KeyPoolError):
    """Request đã bị phía gọi huỷ (ví dụ người dùng ngắt stream giữa chừng)."""


class ErrorKind(str, Enum):
    """Phân loại lỗi Gemini API để quyết định cooldown và failover."""

    RATE_LIMITED = "RATE_LIMITED"  # 429 vượt RPM/TPM
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"  # 429 hết quota theo ngày
    CREDIT_EXHAUSTED = "CREDIT_EXHAUSTED"  # hết credit / billing
    KEY_INVALID = "KEY_INVALID"  # key sai, hết hạn, bị thu hồi
    PERMISSION_DENIED = "PERMISSION_DENIED"  # 403
    SERVER_ERROR = "SERVER_ERROR"  # 5xx, model quá tải, phản hồi rỗng/hỏng
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    NOT_FOUND = "NOT_FOUND"  # model không tồn tại / ngừng cung cấp
    BAD_REQUEST = "BAD_REQUEST"  # payload sai — không thử lại
    BLOCKED = "BLOCKED"  # bị bộ lọc an toàn chặn — không thử lại
    UNKNOWN = "UNKNOWN"


KEY_LEVEL_ERROR_KINDS = frozenset(
    {
        ErrorKind.RATE_LIMITED,
        ErrorKind.QUOTA_EXHAUSTED,
        ErrorKind.CREDIT_EXHAUSTED,
        ErrorKind.KEY_INVALID,
        ErrorKind.PERMISSION_DENIED,
        ErrorKind.NETWORK,
    }
)
MODEL_LEVEL_ERROR_KINDS = frozenset({ErrorKind.SERVER_ERROR, ErrorKind.TIMEOUT, ErrorKind.NOT_FOUND})
RETRYABLE_ERROR_KINDS = KEY_LEVEL_ERROR_KINDS | MODEL_LEVEL_ERROR_KINDS


class GeminiAPIError(KeyPoolError):
    """Lỗi trả về từ Gemini API (đã phân loại)."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        http_status: int | None = None,
        retry_after_s: float | None = None,
        quota_id: str | None = None,
        model: str | None = None,
        key_label: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.http_status = http_status
        self.retry_after_s = retry_after_s
        self.quota_id = quota_id
        self.model = model
        self.key_label = key_label
        self.partial_output = False

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_ERROR_KINDS

    def __str__(self) -> str:
        parts = [self.kind.value]
        if self.http_status is not None:
            parts.append(f"HTTP {self.http_status}")
        if self.key_label:
            parts.append(self.key_label)
        if self.model:
            parts.append(self.model)
        return f"[{' | '.join(parts)}] {self.message}"


class NoAvailableKeyError(KeyPoolError):
    """Không còn API key nào khả dụng trong thời hạn chờ."""

    def __init__(self, message: str, last_error: GeminiAPIError | None = None) -> None:
        super().__init__(message if last_error is None else f"{message} Lỗi gần nhất: {last_error}")
        self.last_error = last_error


# --------------------------------------------------------------------------------------
# Đọc .env
# --------------------------------------------------------------------------------------
def key_fingerprint(api_key: str) -> str:
    """Dấu vân tay an toàn để log (SHA-256 rút gọn) — không lộ nội dung key."""
    return "sha256:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:10]


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def _extract_key_candidate(line: str) -> tuple[str | None, bool]:
    """Trả về (giá trị ứng viên, có_phải_dòng_key_trần)."""
    assignment = _ENV_ASSIGNMENT_RE.match(line)
    if assignment:
        raw_value = assignment.group(2).strip()
        if raw_value[:1] in ("'", '"'):
            quote = raw_value[0]
            closing = raw_value.find(quote, 1)
            value = raw_value[1:closing] if closing > 0 else raw_value[1:]
        else:
            value = raw_value.split(" #", 1)[0].split("\t#", 1)[0]
        value = value.strip()
        return (value or None), False
    token = line.split()[0] if line.split() else ""
    token = _strip_quotes(token)
    return (token or None), True


def load_api_keys(
    env_path: str | os.PathLike[str] | None = None,
    *,
    expected_count: int | None = EXPECTED_KEY_COUNT,
) -> list[str]:
    """Đọc API key Google AI Studio từ ``./.env`` (mặc định ở thư mục gốc dự án).

    * Dòng key trần: ``AQ.xxxxxxxx...`` (không cần ``=``), cho phép chú thích phía sau.
    * Dòng gán: ``GEMINI_KEY_1=AQ...``, ``export KEY="AQ..."``; biến không phải key bị bỏ qua.
    * Key trùng bị loại; thứ tự xuất hiện được giữ nguyên.
    * ``expected_count`` (mặc định 5): số key bắt buộc; ``None`` để không kiểm tra.
    """
    path = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    if not path.is_file():
        raise KeyPoolConfigError(f"Không tìm thấy file .env tại: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KeyPoolConfigError(f"File {path.name} không phải UTF-8 hợp lệ: {exc}") from None

    keys: list[str] = []
    seen: set[str] = set()
    rejected_lines: list[int] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        candidate, bare_line = _extract_key_candidate(line)
        if candidate is None:
            continue
        if not _API_KEY_RE.match(candidate):
            if bare_line:
                rejected_lines.append(line_no)
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        keys.append(candidate)

    if expected_count is not None and len(keys) != expected_count:
        detail = f" Các dòng không đúng định dạng key: {rejected_lines}." if rejected_lines else ""
        raise KeyPoolConfigError(
            f"File {path.name} phải chứa đúng {expected_count} API key Google AI Studio "
            f"(dạng AQ.... hoặc AIza...), nhưng đọc được {len(keys)} key hợp lệ.{detail}"
        )
    if not keys:
        raise KeyPoolConfigError(f"File {path.name} không chứa API key hợp lệ nào.")
    return keys


# --------------------------------------------------------------------------------------
# Phân loại lỗi & phân tích phản hồi
# --------------------------------------------------------------------------------------
def _parse_duration_s(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    match = _DURATION_RE.match(str(value).strip().lower())
    return float(match.group(1)) if match else None


def classify_http_error(
    status: int | None,
    body: str,
    headers: Mapping[str, str] | None = None,
    *,
    model: str | None = None,
) -> GeminiAPIError:
    """Chuyển phản hồi lỗi của Google (JSON ``{"error": {...}}``) thành ``GeminiAPIError``."""
    status = int(status or 0)
    message = (body or "").strip()
    api_status = ""
    reasons: set[str] = set()
    quota_ids: list[str] = []
    retry_after: float | None = None

    try:
        data: Any = json.loads(body) if body else None
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, list) and data and isinstance(data[0], Mapping):
        data = data[0]
    if isinstance(data, Mapping) and isinstance(data.get("error"), Mapping):
        error = data["error"]
        message = str(error.get("message") or message)
        api_status = str(error.get("status") or "")
        if not status and isinstance(error.get("code"), int):
            status = int(error["code"])
        for detail in error.get("details") or []:
            if not isinstance(detail, Mapping):
                continue
            detail_type = str(detail.get("@type", ""))
            if detail_type.endswith("google.rpc.ErrorInfo") and detail.get("reason"):
                reasons.add(str(detail["reason"]))
            elif detail_type.endswith("google.rpc.QuotaFailure"):
                for violation in detail.get("violations") or []:
                    if isinstance(violation, Mapping) and violation.get("quotaId"):
                        quota_ids.append(str(violation["quotaId"]))
            elif detail_type.endswith("google.rpc.RetryInfo"):
                retry_after = _parse_duration_s(detail.get("retryDelay"))
    if retry_after is None and headers is not None:
        try:
            retry_after = _parse_duration_s(headers.get("Retry-After"))
        except AttributeError:
            retry_after = None

    lowered = message.lower()
    if (
        status == 401
        or reasons & {"API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_SERVICE_BLOCKED"}
        or "api key not valid" in lowered
        or "api key expired" in lowered
        or "invalid api key" in lowered
    ):
        kind = ErrorKind.KEY_INVALID
    elif status in (402, 403, 429) and any(marker in lowered for marker in _CREDIT_MARKERS):
        kind = ErrorKind.CREDIT_EXHAUSTED
    elif status == 429 or api_status == "RESOURCE_EXHAUSTED":
        per_day = any("perday" in quota.lower() for quota in quota_ids)
        kind = ErrorKind.QUOTA_EXHAUSTED if per_day else ErrorKind.RATE_LIMITED
    elif status == 403 or api_status == "PERMISSION_DENIED":
        kind = ErrorKind.PERMISSION_DENIED
    elif status == 404 or api_status == "NOT_FOUND":
        kind = ErrorKind.NOT_FOUND
    elif status == 408 or api_status == "DEADLINE_EXCEEDED":
        kind = ErrorKind.TIMEOUT
    elif status >= 500 or api_status in ("UNAVAILABLE", "INTERNAL"):
        kind = ErrorKind.SERVER_ERROR
    elif status == 400 or api_status in ("INVALID_ARGUMENT", "FAILED_PRECONDITION"):
        kind = ErrorKind.BAD_REQUEST
    else:
        kind = ErrorKind.UNKNOWN
    return GeminiAPIError(
        kind,
        message[:600] or f"HTTP {status}",
        http_status=status or None,
        retry_after_s=retry_after,
        quota_id=quota_ids[0] if quota_ids else None,
        model=model,
    )


def _network_error(exc: BaseException, model: str | None) -> GeminiAPIError:
    reason: Any = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return GeminiAPIError(ErrorKind.TIMEOUT, f"Hết thời gian chờ phản hồi: {reason}", model=model)
    return GeminiAPIError(ErrorKind.NETWORK, f"Lỗi kết nối mạng: {reason}", model=model)


def extract_candidate_text(response: Mapping[str, Any]) -> tuple[str, str | None, str | None]:
    """Trả về ``(text, finish_reason, block_reason)`` của candidate đầu tiên (bỏ part 'thought')."""
    feedback = response.get("promptFeedback")
    block_reason = feedback.get("blockReason") if isinstance(feedback, Mapping) else None
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], Mapping):
        return "", None, block_reason
    first = candidates[0]
    content = first.get("content")
    parts = content.get("parts") if isinstance(content, Mapping) else None
    pieces: list[str] = []
    for part in parts or []:
        if isinstance(part, Mapping) and not part.get("thought") and isinstance(part.get("text"), str):
            pieces.append(part["text"])
    finish_reason = first.get("finishReason")
    return "".join(pieces), (str(finish_reason) if finish_reason else None), block_reason


def _normalize_model_name(model: str) -> str:
    name = str(model).strip()
    if name.startswith("models/"):
        name = name[len("models/") :]
    if not name or "/" in name or ":" in name or any(ch.isspace() for ch in name):
        raise ValueError(f"Tên model không hợp lệ: {model!r}")
    return name


def _normalize_models(models: str | Sequence[str]) -> list[str]:
    items = [models] if isinstance(models, str) else list(models)
    normalized = list(dict.fromkeys(_normalize_model_name(m) for m in items))
    if not normalized:
        raise ValueError("Cần ít nhất một model.")
    return normalized


def _decode_json_body(raw: bytes, model: str) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GeminiAPIError(ErrorKind.SERVER_ERROR, f"Phản hồi không phải JSON hợp lệ: {exc}", model=model) from None
    if isinstance(data, Mapping) and isinstance(data.get("error"), Mapping):
        code = data["error"].get("code")
        raise classify_http_error(code if isinstance(code, int) else 0, json.dumps(data), None, model=model)
    if not isinstance(data, dict):
        raise GeminiAPIError(ErrorKind.SERVER_ERROR, "Phản hồi JSON không phải object.", model=model)
    return data


# --------------------------------------------------------------------------------------
# Transport REST chính thức của Google AI Studio
# --------------------------------------------------------------------------------------
class GeminiTransport(Protocol):
    """Giao diện tầng HTTP (cho phép thay bằng transport giả lập khi kiểm thử)."""

    def generate_content(
        self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float
    ) -> dict[str, Any]: ...

    def stream_generate_content(
        self,
        api_key: str,
        model: str,
        payload: Mapping[str, Any],
        timeout_s: float,
        cancel_event: threading.Event,
    ) -> Iterator[dict[str, Any]]: ...


def _set_socket_timeout(response: Any, seconds: float) -> None:
    """Đổi timeout đọc của socket sau khi đã nhận header (http.client → BufferedReader → SocketIO → socket)."""
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if sock is not None:
        try:
            sock.settimeout(seconds)
        except OSError:
            pass


class GeminiRestTransport:
    """Gọi ``generativelanguage.googleapis.com`` bằng thư viện chuẩn (urllib), header ``x-goog-api-key``."""

    def __init__(self, base_url: str = GEMINI_API_BASE_URL, *, user_agent: str = "gemini-rest-client/1.0") -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent

    def _open(self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float, *, stream: bool) -> Any:
        model_id = _normalize_model_name(model)
        method = "streamGenerateContent" if stream else "generateContent"
        url = f"{self._base_url}/models/{urllib.parse.quote(model_id, safe='-._')}:{method}"
        if stream:
            url += "?alt=sse"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "text/event-stream" if stream else "application/json",
                "x-goog-api-key": api_key,
                "User-Agent": self._user_agent,
            },
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout_s)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read()
            except (http.client.HTTPException, OSError):
                raw = b""
            finally:
                exc.close()
            raise classify_http_error(exc.code, raw.decode("utf-8", "replace"), exc.headers, model=model_id) from None
        except urllib.error.URLError as exc:
            raise _network_error(exc, model_id) from None
        except (http.client.HTTPException, OSError) as exc:
            raise _network_error(exc, model_id) from None

    def generate_content(
        self, api_key: str, model: str, payload: Mapping[str, Any], timeout_s: float
    ) -> dict[str, Any]:
        response = self._open(api_key, model, payload, timeout_s, stream=False)
        try:
            raw = response.read()
        except (http.client.HTTPException, OSError) as exc:
            raise _network_error(exc, model) from None
        finally:
            response.close()
        return _decode_json_body(raw, model)

    def stream_generate_content(
        self,
        api_key: str,
        model: str,
        payload: Mapping[str, Any],
        timeout_s: float,
        cancel_event: threading.Event,
        *,
        read_timeout_s: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        """``timeout_s``: chờ byte đầu tiên (header về cùng lúc với cụm chữ đầu); ``read_timeout_s``: chờ giữa hai
        dòng SSE sau khi đã có byte đầu (mặc định = ``timeout_s``)."""
        response = self._open(api_key, model, payload, timeout_s, stream=True)
        if read_timeout_s is not None and read_timeout_s != timeout_s:
            _set_socket_timeout(response, read_timeout_s)
        try:
            data_lines: list[str] = []
            while not cancel_event.is_set():
                try:
                    raw_line = response.readline()
                except (http.client.HTTPException, OSError) as exc:
                    raise _network_error(exc, model) from None
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    if data_lines:
                        yield self._decode_event(data_lines, model)
                        data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                field_name, _, value = line.partition(":")
                if field_name == "data":
                    data_lines.append(value[1:] if value.startswith(" ") else value)
            if data_lines and not cancel_event.is_set():
                yield self._decode_event(data_lines, model)
        finally:
            response.close()

    @staticmethod
    def _decode_event(data_lines: list[str], model: str) -> dict[str, Any]:
        text = "\n".join(data_lines)
        try:
            event = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GeminiAPIError(ErrorKind.SERVER_ERROR, f"Sự kiện SSE không phải JSON: {exc}", model=model) from None
        if isinstance(event, Mapping) and isinstance(event.get("error"), Mapping):
            code = event["error"].get("code")
            raise classify_http_error(code if isinstance(code, int) else 0, text, None, model=model)
        if not isinstance(event, dict):
            raise GeminiAPIError(ErrorKind.SERVER_ERROR, "Sự kiện SSE không phải object JSON.", model=model)
        return event


# --------------------------------------------------------------------------------------
# Token Bucket
# --------------------------------------------------------------------------------------
class TokenBucket:
    """Xô token: dung lượng ``capacity``, nạp ``refill_per_s`` token/giây.

    Lớp này không tự khoá; ``GeminiKeyPool`` gọi nó khi đang giữ lock của pool.
    """

    __slots__ = ("capacity", "refill_per_s", "_tokens", "_updated_at")

    def __init__(self, capacity: float, refill_per_s: float, now: float | None = None) -> None:
        if capacity < 1:
            raise ValueError("capacity phải >= 1")
        if refill_per_s <= 0:
            raise ValueError("refill_per_s phải > 0")
        self.capacity = float(capacity)
        self.refill_per_s = float(refill_per_s)
        self._tokens = float(capacity)
        self._updated_at = _clock() if now is None else now

    def _refill(self, now: float) -> None:
        if now > self._updated_at:
            self._tokens = min(self.capacity, self._tokens + (now - self._updated_at) * self.refill_per_s)
            self._updated_at = now

    def available(self, now: float) -> float:
        self._refill(now)
        return self._tokens

    def try_consume(self, now: float, amount: float = 1.0) -> bool:
        self._refill(now)
        if self._tokens + 1e-9 >= amount:
            self._tokens -= amount
            return True
        return False

    def seconds_until(self, now: float, amount: float = 1.0) -> float:
        self._refill(now)
        missing = amount - self._tokens
        return 0.0 if missing <= 0 else missing / self.refill_per_s


# --------------------------------------------------------------------------------------
# Kết quả & dấu vết request
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    finish_reason: str | None
    usage: Mapping[str, Any]
    model: str
    model_version: str | None
    key_index: int
    key_label: str
    attempts: int
    failover_latencies_ms: tuple[float, ...]
    latency_ms: float
    thread_name: str
    request_id: int
    tag: str
    raw: Mapping[str, Any] = field(repr=False)

    @property
    def failovers(self) -> int:
        return len(self.failover_latencies_ms)


@dataclass(frozen=True, slots=True)
class StreamDelta:
    text: str
    finish_reason: str | None
    usage: Mapping[str, Any] | None
    model: str
    model_version: str | None
    key_index: int
    key_label: str
    request_id: int
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class RequestTrace:
    """Dấu vết một request: key nào, thread nào, lúc nào, bao nhiêu lần failover."""

    request_id: int
    tag: str
    kind: str
    model: str
    key_index: int
    key_label: str
    thread_name: str
    started_at: float
    first_byte_at: float | None
    finished_at: float
    attempts: int
    failover_latencies_ms: tuple[float, ...]
    ok: bool
    error: str | None

    @property
    def duration_ms(self) -> float:
        return (self.finished_at - self.started_at) * 1000.0

    def overlaps(self, other: "RequestTrace") -> bool:
        return self.started_at < other.finished_at and other.started_at < self.finished_at


# --------------------------------------------------------------------------------------
# Trạng thái nội bộ
# --------------------------------------------------------------------------------------
@dataclass(slots=True, eq=False)
class _KeyState:
    index: int
    label: str
    fingerprint: str
    secret: str = field(repr=False)
    bucket: TokenBucket = field(repr=False)
    max_inflight: int = 4
    active: int = 0
    total_requests: int = 0
    successes: int = 0
    failures: int = 0
    rate_limited: int = 0
    disabled_reason: str | None = None
    key_cooldown_until: float = 0.0
    model_cooldown_until: dict[str, float] = field(default_factory=dict)
    consecutive_rate_limits: dict[str, int] = field(default_factory=dict)
    last_error_kind: str | None = None
    ewma_latency_ms: float | None = None


@dataclass(frozen=True, slots=True)
class _Lease:
    state: _KeyState
    model: str


@dataclass(slots=True, eq=False)
class _Job:
    request_id: int
    kind: str
    tag: str
    models: list[str]
    payload: Mapping[str, Any]
    avoid_keys: frozenset[int]
    request_timeout_s: float
    deadline: float
    max_attempts: int
    first_byte_timeout_s: float = 0.0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    model_cursor: int = 0
    tried_pairs: set[tuple[int, str]] = field(default_factory=set)
    attempts: int = 0
    cycles: int = 0
    not_before: float = 0.0
    failover_latencies_ms: list[float] = field(default_factory=list)
    errors: list[GeminiAPIError] = field(default_factory=list)
    started_at: float = 0.0
    first_byte_at: float | None = None
    finished_at: float = 0.0
    served_by: _KeyState | None = None
    served_model: str | None = None
    thread_name: str = ""

    def model_order(self) -> list[str]:
        count = len(self.models)
        return [self.models[(self.model_cursor + offset) % count] for offset in range(count)]


def _min_optional(current: float | None, candidate: float) -> float:
    return candidate if current is None else min(current, candidate)


def _consume_future_exception(future: "asyncio.Future[Any]") -> None:
    if not future.cancelled():
        future.exception()


# --------------------------------------------------------------------------------------
# Key pool
# --------------------------------------------------------------------------------------
class GeminiKeyPool:
    """Pool API key Google AI Studio: asyncio + ThreadPoolExecutor, Least-Connections + Token Bucket."""

    def __init__(
        self,
        api_keys: Sequence[str],
        *,
        requests_per_minute_per_key: float = 12.0,
        burst_per_key: int = 4,
        max_inflight_per_key: int = 4,
        max_workers: int | None = None,
        request_timeout_s: float = 45.0,
        total_deadline_s: float = 120.0,
        max_attempts: int | None = None,
        model_degrade_s: float = 30.0,
        transport: GeminiTransport | None = None,
        trace_capacity: int = 512,
        first_byte_timeout_s: float | None = None,
    ) -> None:
        cleaned = [str(k).strip() for k in api_keys if k is not None and str(k).strip()]
        if not cleaned:
            raise KeyPoolConfigError("Cần ít nhất một API key để khởi tạo pool.")
        if len(set(cleaned)) != len(cleaned):
            raise KeyPoolConfigError("Danh sách API key có key bị trùng lặp.")
        if requests_per_minute_per_key <= 0:
            raise KeyPoolConfigError("requests_per_minute_per_key phải > 0.")
        if burst_per_key < 1 or max_inflight_per_key < 1:
            raise KeyPoolConfigError("burst_per_key và max_inflight_per_key phải >= 1.")
        if request_timeout_s <= 0 or total_deadline_s <= 0 or model_degrade_s < 0:
            raise KeyPoolConfigError("request_timeout_s, total_deadline_s phải > 0 và model_degrade_s >= 0.")
        if first_byte_timeout_s is not None and first_byte_timeout_s <= 0:
            raise KeyPoolConfigError("first_byte_timeout_s phải > 0.")

        now = _clock()
        self._states: tuple[_KeyState, ...] = tuple(
            _KeyState(
                index=index,
                label=f"key#{index + 1}",
                fingerprint=key_fingerprint(key),
                secret=key,
                bucket=TokenBucket(burst_per_key, requests_per_minute_per_key / 60.0, now),
                max_inflight=max_inflight_per_key,
            )
            for index, key in enumerate(cleaned)
        )
        self._cond = threading.Condition(threading.Lock())
        self._transport: GeminiTransport = transport if transport is not None else GeminiRestTransport()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers or max(8, len(cleaned) * max_inflight_per_key),
            thread_name_prefix="dvsk-key-pool",
        )
        self._request_timeout_s = float(request_timeout_s)
        self._total_deadline_s = float(total_deadline_s)
        # Stream: chờ byte đầu tối đa first_byte_timeout_s rồi xoay ngay sang key/model khác (model khoẻ ~1–1,5 s).
        self._first_byte_timeout_s = float(first_byte_timeout_s or request_timeout_s)
        try:
            self._stream_read_timeout_kw = "read_timeout_s" in inspect.signature(
                self._transport.stream_generate_content
            ).parameters
        except (TypeError, ValueError):
            self._stream_read_timeout_kw = False
        self._max_attempts = int(max_attempts) if max_attempts else max(6, 2 * len(cleaned))
        self._rr_cursor = 0
        self._ids = itertools.count(1)
        self._traces: deque[RequestTrace] = deque(maxlen=trace_capacity)
        self._failover_latencies_ms: deque[float] = deque(maxlen=trace_capacity)
        self._model_unavailable_until: dict[str, float] = {}
        # Circuit breaker theo model: model vừa trả 5xx / timeout bị xếp xuống cuối chuỗi ưu tiên trong
        # model_degrade_s giây, để các request kế tiếp đi thẳng vào model khoẻ thay vì cùng chờ model quá tải.
        self._model_degrade_s = float(model_degrade_s)
        self._model_degraded_until: dict[str, float] = {}
        self._closed = False

    # ------------------------------------------------------------------ khởi tạo nhanh
    @classmethod
    def from_env(
        cls,
        env_path: str | os.PathLike[str] | None = None,
        *,
        expected_count: int | None = EXPECTED_KEY_COUNT,
        **kwargs: Any,
    ) -> "GeminiKeyPool":
        """Tạo pool từ ``./.env`` (mặc định đúng 5 key)."""
        return cls(load_api_keys(env_path, expected_count=expected_count), **kwargs)

    # ------------------------------------------------------------------ thông tin
    @property
    def key_count(self) -> int:
        return len(self._states)

    @property
    def key_labels(self) -> tuple[str, ...]:
        return tuple(state.label for state in self._states)

    @property
    def closed(self) -> bool:
        return self._closed

    def snapshot(self) -> list[dict[str, Any]]:
        """Trạng thái từng key (không chứa key thật)."""
        with self._cond:
            now = _clock()
            rows: list[dict[str, Any]] = []
            for state in self._states:
                rows.append(
                    {
                        "key": state.label,
                        "fingerprint": state.fingerprint,
                        "active": state.active,
                        "tokens": round(state.bucket.available(now), 3),
                        "requests": state.total_requests,
                        "successes": state.successes,
                        "failures": state.failures,
                        "rate_limited": state.rate_limited,
                        "disabled": state.disabled_reason,
                        "key_cooldown_s": round(max(0.0, state.key_cooldown_until - now), 3),
                        "model_cooldowns_s": {
                            model: round(until - now, 3)
                            for model, until in state.model_cooldown_until.items()
                            if until > now
                        },
                        "ewma_latency_ms": None if state.ewma_latency_ms is None else round(state.ewma_latency_ms, 1),
                        "last_error": state.last_error_kind,
                    }
                )
            return rows

    def traces(self, tag_prefix: str | None = None) -> list[RequestTrace]:
        with self._cond:
            items = list(self._traces)
        if tag_prefix is None:
            return items
        return [trace for trace in items if trace.tag.startswith(tag_prefix)]

    def failover_latencies_ms(self) -> list[float]:
        with self._cond:
            return list(self._failover_latencies_ms)

    def degraded_models(self) -> dict[str, float]:
        """Các model đang bị circuit breaker hạ ưu tiên → số giây còn lại."""
        with self._cond:
            now = _clock()
            return {model: round(until - now, 3) for model, until in self._model_degraded_until.items() if until > now}

    def total_requests(self) -> int:
        with self._cond:
            return sum(state.total_requests for state in self._states)

    # ------------------------------------------------------------------ vòng đời
    def close(self) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._cond.notify_all()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> "GeminiKeyPool":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    async def __aenter__(self) -> "GeminiKeyPool":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"GeminiKeyPool(keys={len(self._states)}, closed={self._closed})"

    # ------------------------------------------------------------------ API bất đồng bộ
    async def generate(
        self,
        models: str | Sequence[str],
        payload: Mapping[str, Any],
        *,
        tag: str = "",
        avoid_keys: Iterable[int] = (),
        request_timeout_s: float | None = None,
        deadline_s: float | None = None,
        first_byte_timeout_s: float | None = None,
    ) -> GenerationResult:
        """Gọi ``generateContent`` trên worker thread; tự chọn key và failover.

        ``models``: một model hoặc chuỗi model ưu tiên (model sau là dự phòng khi model trước
        quá tải/ngừng cung cấp). ``avoid_keys``: chỉ số key nên tránh nếu còn key khác.
        """
        job = self._new_job("generate", models, payload, tag, avoid_keys, request_timeout_s, deadline_s,
                            first_byte_timeout_s)
        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(self._executor, self._generate_worker, job)
        except RuntimeError as exc:
            raise KeyPoolClosedError("Key pool đã đóng.") from exc
        try:
            return await future
        except asyncio.CancelledError:
            job.cancel_event.set()
            raise

    async def stream(
        self,
        models: str | Sequence[str],
        payload: Mapping[str, Any],
        *,
        tag: str = "",
        avoid_keys: Iterable[int] = (),
        request_timeout_s: float | None = None,
        deadline_s: float | None = None,
        first_byte_timeout_s: float | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Gọi ``streamGenerateContent`` (SSE) trên worker thread, đẩy từng delta về event loop.

        Failover chỉ áp dụng trước khi delta đầu tiên được phát; lỗi giữa dòng được ném ra
        với ``partial_output = True``.
        """
        job = self._new_job("stream", models, payload, tag, avoid_keys, request_timeout_s, deadline_s,
                            first_byte_timeout_s)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def emit(kind: str, item: Any) -> bool:
            if kind == "delta" and job.cancel_event.is_set():
                return False
            try:
                loop.call_soon_threadsafe(queue.put_nowait, (kind, item))
            except RuntimeError:
                job.cancel_event.set()
                return False
            return True

        try:
            future = loop.run_in_executor(self._executor, self._stream_worker, job, emit)
        except RuntimeError as exc:
            raise KeyPoolClosedError("Key pool đã đóng.") from exc
        future.add_done_callback(_consume_future_exception)
        try:
            while True:
                kind, item = await queue.get()
                if kind == "delta":
                    yield item
                elif kind == "error":
                    raise item
                else:
                    return
        finally:
            job.cancel_event.set()

    # ------------------------------------------------------------------ job & worker
    def _new_job(
        self,
        kind: str,
        models: str | Sequence[str],
        payload: Mapping[str, Any],
        tag: str,
        avoid_keys: Iterable[int],
        request_timeout_s: float | None,
        deadline_s: float | None,
        first_byte_timeout_s: float | None = None,
    ) -> _Job:
        if self._closed:
            raise KeyPoolClosedError("Key pool đã đóng.")
        if not isinstance(payload, Mapping):
            raise TypeError("payload phải là Mapping (JSON object).")
        avoid = frozenset(int(i) for i in avoid_keys if 0 <= int(i) < len(self._states))
        return _Job(
            request_id=next(self._ids),
            kind=kind,
            tag=tag,
            models=_normalize_models(models),
            payload=payload,
            avoid_keys=avoid,
            request_timeout_s=float(request_timeout_s or self._request_timeout_s),
            deadline=_clock() + float(deadline_s or self._total_deadline_s),
            max_attempts=self._max_attempts,
            first_byte_timeout_s=float(first_byte_timeout_s or self._first_byte_timeout_s),
        )

    @staticmethod
    def _attempt_timeout(job: _Job, limit: float) -> float:
        """Timeout của MỘT lần thử không bao giờ vượt phần còn lại của hạn chót toàn request (≤ deadline)."""
        return max(_MIN_ATTEMPT_TIMEOUT_S, min(limit, job.deadline - _clock()))

    def _generate_worker(self, job: _Job) -> GenerationResult:
        job.thread_name = threading.current_thread().name
        job.started_at = _clock()
        error: BaseException | None = None

        def attempt(lease: _Lease) -> tuple[dict[str, Any], str, str | None]:
            response = self._transport.generate_content(
                lease.state.secret, lease.model, job.payload, self._attempt_timeout(job, job.request_timeout_s)
            )
            job.first_byte_at = _clock()
            text, finish_reason, block_reason = extract_candidate_text(response)
            if block_reason or (not text and finish_reason in _BLOCKED_FINISH_REASONS):
                raise GeminiAPIError(
                    ErrorKind.BLOCKED,
                    f"Nội dung bị bộ lọc an toàn chặn ({block_reason or finish_reason}).",
                    model=lease.model,
                )
            if not text:
                raise GeminiAPIError(
                    ErrorKind.SERVER_ERROR, f"Phản hồi rỗng (finishReason={finish_reason}).", model=lease.model
                )
            return response, text, finish_reason

        try:
            (response, text, finish_reason), lease = self._run_with_failover(job, attempt)
            job.finished_at = _clock()
            usage = response.get("usageMetadata")
            return GenerationResult(
                text=text,
                finish_reason=finish_reason,
                usage=dict(usage) if isinstance(usage, Mapping) else {},
                model=lease.model,
                model_version=response.get("modelVersion"),
                key_index=lease.state.index,
                key_label=lease.state.label,
                attempts=job.attempts,
                failover_latencies_ms=tuple(job.failover_latencies_ms),
                latency_ms=(job.finished_at - job.started_at) * 1000.0,
                thread_name=job.thread_name,
                request_id=job.request_id,
                tag=job.tag,
                raw=response,
            )
        except BaseException as exc:
            error = exc
            raise
        finally:
            if not job.finished_at:
                job.finished_at = _clock()
            self._record_trace(job, error)

    def _stream_worker(self, job: _Job, emit: Callable[[str, Any], bool]) -> None:
        job.thread_name = threading.current_thread().name
        job.started_at = _clock()
        error: BaseException | None = None

        def attempt(lease: _Lease) -> int:
            delivered = 0
            try:
                first_byte = self._attempt_timeout(job, min(job.first_byte_timeout_s, job.request_timeout_s))
                if self._stream_read_timeout_kw:
                    events = self._transport.stream_generate_content(
                        lease.state.secret, lease.model, job.payload, first_byte, job.cancel_event,
                        read_timeout_s=job.request_timeout_s,
                    )
                else:
                    events = self._transport.stream_generate_content(
                        lease.state.secret, lease.model, job.payload, first_byte, job.cancel_event
                    )
                for event in events:
                    if job.cancel_event.is_set():
                        break
                    now = _clock()
                    if job.first_byte_at is None:
                        job.first_byte_at = now
                    text, finish_reason, block_reason = extract_candidate_text(event)
                    if block_reason or (not text and delivered == 0 and finish_reason in _BLOCKED_FINISH_REASONS):
                        raise GeminiAPIError(
                            ErrorKind.BLOCKED,
                            f"Nội dung bị bộ lọc an toàn chặn ({block_reason or finish_reason}).",
                            model=lease.model,
                        )
                    if not text and (delivered == 0 or not finish_reason):
                        continue
                    usage = event.get("usageMetadata") if finish_reason else None
                    delta = StreamDelta(
                        text=text,
                        finish_reason=finish_reason,
                        usage=dict(usage) if isinstance(usage, Mapping) else None,
                        model=lease.model,
                        model_version=event.get("modelVersion"),
                        key_index=lease.state.index,
                        key_label=lease.state.label,
                        request_id=job.request_id,
                        elapsed_ms=(now - job.started_at) * 1000.0,
                    )
                    if not emit("delta", delta):
                        job.cancel_event.set()
                        break
                    delivered += 1
                if delivered == 0 and not job.cancel_event.is_set():
                    raise GeminiAPIError(ErrorKind.SERVER_ERROR, "Stream kết thúc mà không có nội dung.", model=lease.model)
            except GeminiAPIError as err:
                if delivered:
                    err.partial_output = True
                raise
            return delivered

        outcome: tuple[str, Any] = ("end", None)
        try:
            self._run_with_failover(job, attempt)
        except BaseException as exc:
            error = exc
            outcome = ("error", exc if isinstance(exc, Exception) else KeyPoolError(repr(exc)))
        finally:
            job.finished_at = _clock()
            try:
                # Ghi trace TRƯỚC khi báo kết thúc để phía asyncio luôn thấy trace của stream vừa xong.
                self._record_trace(job, error)
            finally:
                emit(*outcome)

    def _run_with_failover(self, job: _Job, attempt: Callable[[_Lease], Any]) -> tuple[Any, _Lease]:
        failover_started: float | None = None
        while True:
            lease = self._acquire(job)
            if failover_started is not None:
                latency_ms = (_clock() - failover_started) * 1000.0
                job.failover_latencies_ms.append(latency_ms)
                with self._cond:
                    self._failover_latencies_ms.append(latency_ms)
                failover_started = None
            job.attempts += 1
            job.served_by = lease.state
            job.served_model = lease.model
            attempt_started = _clock()
            try:
                outcome = attempt(lease)
            except GeminiAPIError as err:
                failover_started = _clock()
                err.key_label = err.key_label or lease.state.label
                err.model = err.model or lease.model
                job.errors.append(err)
                self._release(lease, failover_started - attempt_started, err)
                if not self._should_failover(job, err):
                    raise
                self._advance_after_error(job, lease, err)
                continue
            except BaseException:
                self._release(lease, _clock() - attempt_started, None, aborted=True)
                raise
            self._release(lease, _clock() - attempt_started, None)
            return outcome, lease

    def _should_failover(self, job: _Job, err: GeminiAPIError) -> bool:
        if err.partial_output or job.cancel_event.is_set() or self._closed:
            return False
        if not err.retryable:
            return False
        if err.kind is ErrorKind.NOT_FOUND and len(job.models) <= 1:
            return False
        if job.attempts >= job.max_attempts:
            return False
        return _clock() < job.deadline

    @staticmethod
    def _advance_after_error(job: _Job, lease: _Lease, err: GeminiAPIError) -> None:
        job.tried_pairs.add((lease.state.index, lease.model))
        if lease.model not in job.models:
            return
        if err.kind is ErrorKind.NOT_FOUND and len(job.models) > 1:
            position = job.models.index(lease.model)
            job.models.remove(lease.model)
            job.model_cursor = position % len(job.models)
        elif err.kind in MODEL_LEVEL_ERROR_KINDS and len(job.models) > 1:
            job.model_cursor = (job.models.index(lease.model) + 1) % len(job.models)

    # ------------------------------------------------------------------ chọn key
    def _acquire(self, job: _Job) -> _Lease:
        with self._cond:
            while True:
                if self._closed:
                    raise KeyPoolClosedError("Key pool đã đóng.")
                if job.cancel_event.is_set():
                    raise RequestCancelledError("Request đã bị huỷ.")
                now = _clock()
                if now >= job.deadline:
                    raise NoAvailableKeyError(
                        f"Quá hạn chờ mà không có API key khả dụng cho model {job.models}.",
                        job.errors[-1] if job.errors else None,
                    )
                if now < job.not_before:
                    self._cond.wait(timeout=min(job.not_before, job.deadline) - now)
                    continue
                lease, wait_s = self._select_locked(job, now)
                if lease is not None:
                    return lease
                if wait_s is None:
                    if job.tried_pairs and self._has_usable_key_locked():
                        job.tried_pairs.clear()
                        job.cycles += 1
                        job.not_before = now + min(0.25 * (2 ** (job.cycles - 1)), 4.0)
                        continue
                    raise NoAvailableKeyError(
                        "Tất cả API key đều đã bị vô hiệu hoá.", job.errors[-1] if job.errors else None
                    )
                self._cond.wait(timeout=max(0.001, min(wait_s, job.deadline - now)))

    def _has_usable_key_locked(self) -> bool:
        return any(state.disabled_reason is None for state in self._states)

    def _select_locked(self, job: _Job, now: float) -> tuple[_Lease | None, float | None]:
        """Least-Connections + Token Bucket. Trả về (lease, None) hoặc (None, số giây nên chờ)."""
        wait_s: float | None = None
        key_count = len(self._states)
        ordered = job.model_order()
        healthy = [model for model in ordered if self._model_degraded_until.get(model, 0.0) <= now]
        for model in healthy + [model for model in ordered if model not in healthy]:
            unavailable_until = self._model_unavailable_until.get(model, 0.0)
            if unavailable_until > now:
                wait_s = _min_optional(wait_s, unavailable_until - now)
                continue
            best: _KeyState | None = None
            best_rank: tuple[int, int, float, int] | None = None
            for state in self._states:
                if state.disabled_reason is not None or (state.index, model) in job.tried_pairs:
                    continue
                blocked_until = max(state.key_cooldown_until, state.model_cooldown_until.get(model, 0.0))
                if blocked_until > now:
                    wait_s = _min_optional(wait_s, blocked_until - now)
                    continue
                if state.active >= state.max_inflight:
                    wait_s = _min_optional(wait_s, _INFLIGHT_RECHECK_S)
                    continue
                tokens = state.bucket.available(now)
                if tokens < 1.0 - 1e-9:
                    wait_s = _min_optional(wait_s, state.bucket.seconds_until(now))
                    continue
                rank = (
                    1 if state.index in job.avoid_keys else 0,
                    state.active,
                    -round(tokens, 3),
                    (state.index - self._rr_cursor) % key_count,
                )
                if best_rank is None or rank < best_rank:
                    best, best_rank = state, rank
            if best is not None:
                best.bucket.try_consume(now)
                best.active += 1
                best.total_requests += 1
                self._rr_cursor = (best.index + 1) % key_count
                return _Lease(best, model), None
        return None, wait_s

    def _release(self, lease: _Lease, elapsed_s: float, error: GeminiAPIError | None, *, aborted: bool = False) -> None:
        with self._cond:
            state = lease.state
            state.active = max(0, state.active - 1)
            if error is None and not aborted:
                state.successes += 1
                state.consecutive_rate_limits.pop(lease.model, None)
                state.last_error_kind = None
                self._model_degraded_until.pop(lease.model, None)
                latency_ms = elapsed_s * 1000.0
                state.ewma_latency_ms = (
                    latency_ms if state.ewma_latency_ms is None else 0.8 * state.ewma_latency_ms + 0.2 * latency_ms
                )
            elif error is not None:
                state.failures += 1
                state.last_error_kind = error.kind.value
                self._apply_error_policy_locked(state, lease.model, error, _clock())
            self._cond.notify_all()

    def _apply_error_policy_locked(self, state: _KeyState, model: str, error: GeminiAPIError, now: float) -> None:
        kind = error.kind
        if kind is ErrorKind.RATE_LIMITED:
            count = state.consecutive_rate_limits.get(model, 0) + 1
            state.consecutive_rate_limits[model] = count
            state.rate_limited += 1
            delay = error.retry_after_s if error.retry_after_s else min(60.0, 2.0 ** min(count, 6))
            state.model_cooldown_until[model] = max(state.model_cooldown_until.get(model, 0.0), now + max(1.0, delay))
        elif kind is ErrorKind.QUOTA_EXHAUSTED:
            state.rate_limited += 1
            delay = max(error.retry_after_s or 0.0, 1800.0)
            state.model_cooldown_until[model] = max(state.model_cooldown_until.get(model, 0.0), now + delay)
        elif kind is ErrorKind.CREDIT_EXHAUSTED:
            state.key_cooldown_until = max(state.key_cooldown_until, now + 3600.0)
        elif kind is ErrorKind.KEY_INVALID:
            state.disabled_reason = "API key không hợp lệ, hết hạn hoặc bị thu hồi."
        elif kind is ErrorKind.PERMISSION_DENIED:
            state.model_cooldown_until[model] = max(state.model_cooldown_until.get(model, 0.0), now + 600.0)
        elif kind is ErrorKind.SERVER_ERROR:
            state.model_cooldown_until[model] = max(state.model_cooldown_until.get(model, 0.0), now + 1.0)
            self._model_degraded_until[model] = max(self._model_degraded_until.get(model, 0.0), now + self._model_degrade_s)
        elif kind is ErrorKind.TIMEOUT:
            state.model_cooldown_until[model] = max(state.model_cooldown_until.get(model, 0.0), now + 3.0)
            self._model_degraded_until[model] = max(self._model_degraded_until.get(model, 0.0), now + self._model_degrade_s)
        elif kind is ErrorKind.NETWORK:
            state.key_cooldown_until = max(state.key_cooldown_until, now + 0.5)
        elif kind is ErrorKind.NOT_FOUND:
            self._model_unavailable_until[model] = max(self._model_unavailable_until.get(model, 0.0), now + 600.0)

    def _record_trace(self, job: _Job, error: BaseException | None) -> None:
        state = job.served_by
        trace = RequestTrace(
            request_id=job.request_id,
            tag=job.tag,
            kind=job.kind,
            model=job.served_model or (job.models[0] if job.models else ""),
            key_index=state.index if state is not None else -1,
            key_label=state.label if state is not None else "",
            thread_name=job.thread_name,
            started_at=job.started_at,
            first_byte_at=job.first_byte_at,
            finished_at=job.finished_at or _clock(),
            attempts=job.attempts,
            failover_latencies_ms=tuple(job.failover_latencies_ms),
            ok=error is None,
            error=None if error is None else str(error),
        )
        with self._cond:
            self._traces.append(trace)


# Tên gọi dùng trong đặc tả dự án ("MultiThreadKeyPool") — cùng một lớp, không phải bản sao.
MultiThreadKeyPool = GeminiKeyPool
