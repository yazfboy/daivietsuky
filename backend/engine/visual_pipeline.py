"""Đại Việt Sử Ký — Phase 2 — Visual & Animation Engine.

Ảnh mẫu 3 anh hùng · cảnh động 60 FPS giữ nguyên nhân vật · đồng bộ cử động miệng — HOÀN TOÀN MIỄN PHÍ.

Nguồn ảnh (không bật billing):
* **Wikimedia Commons** (MediaWiki API): tự tìm tranh/tượng chuẩn của từng vị anh hùng (chỉ giấy phép tự do).
  AI Đạo diễn (Gemini, 5 key miễn phí qua ``MultiThreadKeyPool``) chấm điểm, chọn ảnh chuẩn và mô tả đặc điểm
  nhận dạng truyền thống (râu, mũ, trang phục…).
* **Hugging Face ZeroGPU Spaces** qua ``gradio_client``, đa luồng, tự failover giữa các Space:
  FLUX.1-schnell → Stable Diffusion 3.5 Large Turbo → Z-Image-Turbo → SDXL-Lightning. Dùng để vẽ ảnh mẫu
  tối ưu cho rigging (đứng chính diện, nền xanh để tách lớp) theo đặc điểm lấy từ ảnh chuẩn, và vẽ cảnh nền.

Giữ nguyên 100% nhân vật cũ: nhân vật KHÔNG được vẽ lại trong từng cảnh. Ảnh mẫu gốc được tách nền, AI Đạo diễn
dò khung xương (đầu, cổ, vai, khuỷu, cổ tay, hông, môi) → rig 2.5D: Linear Blend Skinning + phép nghịch đảo điểm
bất động + lưới MESH của Pillow. Mọi chuyển động (nói, cử chỉ, chỉ huy) chỉ là biến dạng hình học trên CHÍNH các
pixel gốc; ở tư thế nghỉ, kết quả trùng khớp từng pixel với ảnh gốc.

Quy hoạch động:
* ``DPFrameInterpolator`` — DP phân bổ khung hình giữa các tư thế khoá (tối thiểu năng lượng Σ d²/T + độ lệch thời
  gian giữ) rồi nội suy Hermite C¹ → chuyển động 60 FPS mượt, vòng lặp liền mạch (dùng cho camera và cho rig).
* ``TextToVisemeDPSync`` — Viterbi DP trên từng khung 60 FPS: khớp khẩu hình với nhịp âm tiết tiếng Việt (cùng đồng
  hồ nói ``speak_ms``/``pause_ms`` của AI Nhân vật), chống nháy, giữ tối thiểu, ngậm miệng khi nói xong.
* ``DiskDPCache`` — bảng nhớ DP trên ổ cứng: ảnh tải/gen, phân tích của AI, cảnh đã render được tra lại O(1) theo
  khoá nội dung hoặc khoá ngữ cảnh (nhân vật, chế độ cảnh, bối cảnh) của Phase 1.

Đầu ra (thư mục ``frontend/public/assets``):
* ``heroes/{id}.png`` (RGBA đã tách nền), ``heroes/{id}.rig.json`` (khung xương), ``heroes/{id}.meta.json`` (nguồn gốc).
* ``scenes/<cảnh>/background_60fps.webp`` (quê hương, chế độ nói) hoặc ``scene_60fps.webp`` (trận đánh có nhân vật
  hành động), ``background.png``, ``layout.json``, ``manifest.json``.
* ``renders/<id>.webp`` — bản xem trước nhân vật nói chuyện với miệng khớp lời thoại.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import functools
import hashlib
import io
import json
import math
import os
import random
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

from ..agents.actor_agent import SUPPORTED_CHARACTER_IDS, CharacterProfile, PhraseChunker, SpeechChunk, get_character
from ..agents.director_agent import SceneDirective, character_reference_path
from ..core.dp_optimizer import (
    DEFAULT_MS_PER_SYLLABLE,
    PAUSE_MS,
    SCENE_BACKGROUND_ACTION,
    SCENE_FOREGROUND_TALKING,
    count_syllables,
)
from ..core.key_pool import DEFAULT_ENV_PATH, KeyPoolError, MultiThreadKeyPool

__all__ = [
    "ASSETS_DIR",
    "BACKGROUND_IMAGE_SIZE",
    "CACHE_DIR",
    "DEFAULT_OUTPUT_SIZE",
    "HEROES_DIR",
    "HERO_IMAGE_SIZE",
    "RENDERS_DIR",
    "SCENES_DIR",
    "TARGET_FPS",
    "VISEMES",
    "AnimationInfo",
    "BackgroundAnimator",
    "ConsistentHeroAnimationEngine",
    "CutoutResult",
    "DPFrameInterpolator",
    "DirectorVision",
    "DiskDPCache",
    "FrameSchedule",
    "GeneratedImage",
    "HeroAsset",
    "HeroModelInitializer",
    "HeroRig",
    "HeroRigRenderer",
    "HeroStage",
    "HuggingFaceImageSource",
    "KeyPose",
    "MouthAndActionController",
    "MouthTrack",
    "PerformancePlan",
    "PerformanceRender",
    "RigPose",
    "SceneAnimation",
    "SceneLayout",
    "SpaceAdapter",
    "TextToVisemeDPSync",
    "WikimediaCommonsSource",
    "WikimediaImage",
    "build_rig",
    "frame_durations_ms",
    "histogram_similarity",
    "inspect_animation",
    "make_cutout",
    "syllable_visemes",
    "text_syllables",
    "vietnamese_number_words",
    "write_webp_animation",
]

# ======================================================================================
# 1. Hằng số & tiện ích
# ======================================================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "frontend" / "public" / "assets"
HEROES_DIR = ASSETS_DIR / "heroes"
SCENES_DIR = ASSETS_DIR / "scenes"
RENDERS_DIR = ASSETS_DIR / "renders"
CACHE_DIR = PROJECT_ROOT / "backend" / ".cache" / "visual"

TARGET_FPS = 60
DEFAULT_OUTPUT_SIZE: tuple[int, int] = (960, 540)
HERO_IMAGE_SIZE: tuple[int, int] = (896, 1152)
BACKGROUND_IMAGE_SIZE: tuple[int, int] = (1280, 720)
RENDER_VERSION = 1
GENERIC_USER_AGENT = "HeroPortraitFetcher/1.0 (non-commercial educational use; python-urllib)"
_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class AssetSourceError(RuntimeError):
    """Không tải / tạo được tài nguyên hình ảnh."""


class ImageGenerationError(AssetSourceError):
    """Không Space Hugging Face nào tạo được ảnh."""


class HeroInitializationError(RuntimeError):
    """Không tạo / tìm được ảnh mẫu đạt yêu cầu cho một vị anh hùng."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_hash(payload: Any, length: int = 24) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:length]


def stable_seed(*parts: Any) -> int:
    return int(stable_hash(list(parts), 12), 16) % 2_147_483_647


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def read_env_value(name: str, env_path: str | os.PathLike[str] = DEFAULT_ENV_PATH) -> str | None:
    """Đọc biến tuỳ chọn (ví dụ ``HF_TOKEN``) từ môi trường hoặc dòng ``NAME=value`` trong ``./.env``."""
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    path = Path(env_path)
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, rest = line.partition("=")
        if separator and key.strip() == name:
            rest = rest.strip()
            if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "'\"":
                rest = rest[1:-1]
            return rest.strip() or None
    return None


def open_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def flatten_on(image: Image.Image, color: tuple[int, int, int] = (128, 128, 128)) -> Image.Image:
    if image.mode in ("RGBA", "LA", "RGBa") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (*color, 255))
        base.alpha_composite(rgba)
        return base.convert("RGB")
    return image.convert("RGB")


def image_to_jpeg_bytes(image: Image.Image, *, max_side: int = 768, quality: int = 88) -> bytes:
    rgb = flatten_on(image)
    if max(rgb.size) > max_side:
        rgb = rgb.copy()
        rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    rgb.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def default_render_workers() -> int:
    return max(2, min(8, os.cpu_count() or 4))


def frame_durations_ms(count: int, fps: int = TARGET_FPS) -> list[int]:
    """Thời lượng từng khung (ms, số nguyên) sao cho tổng khớp đúng ``count / fps`` giây (60 FPS: 17,17,16…)."""
    return [round((index + 1) * 1000 / fps) - round(index * 1000 / fps) for index in range(count)]


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _smooth01(value: float) -> float:
    value = _clamp(value, 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def _falloff(distance: float, inner: float, outer: float) -> float:
    if distance <= inner:
        return 1.0
    if distance >= outer:
        return 0.0
    return 1.0 - _smooth01((distance - inner) / (outer - inner))


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("\n") + 1 :] if "\n" in raw else raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("AI không trả về JSON hợp lệ.") from None
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("AI phải trả về một JSON object.")
    return data


# ======================================================================================
# 2. DP Cache trên ổ cứng — tra cứu O(1)
# ======================================================================================
@dataclass(frozen=True)
class CacheEntry:
    key: str
    kind: str
    value: Any
    files: Mapping[str, Path]
    meta: Mapping[str, Any]
    created_at: float


class DiskDPCache:
    """Bảng nhớ quy hoạch động lưu xuống ổ cứng (memoization): khoá → giá trị JSON / file.

    Mỗi bài toán con đắt chỉ giải MỘT lần; lần sau tra bảng băm (``dict``) theo khoá rồi đọc file — O(1) theo số
    mục trong cache. Chỉ mục ``dp_index.json`` được ghi nguyên tử (file tạm + ``os.replace``) để không hỏng khi tắt
    ngang. Mục nào mất file sẽ tự bị loại khi tra cứu.
    """

    INDEX_FILE = "dp_index.json"

    def __init__(self, root: str | os.PathLike[str] = CACHE_DIR) -> None:
        self.root = Path(root)
        self.objects_dir = self.root / "objects"
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / self.INDEX_FILE
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] = self._load_index()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(namespace: str, payload: Any) -> str:
        return f"{namespace}:{stable_hash(payload)}"

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self._index_path.is_file():
            return {}
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            try:
                os.replace(self._index_path, self._index_path.with_suffix(".corrupt.json"))
            except OSError:
                pass
            return {}
        return data if isinstance(data, dict) else {}

    def _persist_locked(self) -> None:
        atomic_write_text(self._index_path, json.dumps(self._index, ensure_ascii=False, indent=1))

    @staticmethod
    def _store_path(path: Path) -> str:
        resolved = Path(path).resolve()
        try:
            return resolved.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            return str(resolved)

    @staticmethod
    def _resolve(stored: str) -> Path:
        path = Path(stored)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            raw = self._index.get(key)
            if raw is None:
                self.misses += 1
                return None
            files = {name: self._resolve(stored) for name, stored in (raw.get("files") or {}).items()}
            if any(not path.is_file() for path in files.values()):
                del self._index[key]
                self._persist_locked()
                self.misses += 1
                return None
            self.hits += 1
            return CacheEntry(
                key=key,
                kind=str(raw.get("kind", "json")),
                value=raw.get("value"),
                files=files,
                meta=raw.get("meta") or {},
                created_at=float(raw.get("created_at", 0.0)),
            )

    def put(
        self,
        key: str,
        *,
        kind: str,
        value: Any = None,
        files: Mapping[str, Path] | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        record = {
            "kind": kind,
            "value": value,
            "files": {name: self._store_path(Path(path)) for name, path in (files or {}).items()},
            "meta": dict(meta or {}),
            "created_at": time.time(),
        }
        with self._lock:
            self._index[key] = record
            self._persist_locked()

    def get_json(self, key: str) -> Any | None:
        entry = self.get(key)
        return None if entry is None else entry.value

    def put_json(self, key: str, value: Any, meta: Mapping[str, Any] | None = None) -> None:
        self.put(key, kind="json", value=value, meta=meta)

    def get_blob(self, key: str) -> bytes | None:
        entry = self.get(key)
        if entry is None or "blob" not in entry.files:
            return None
        return entry.files["blob"].read_bytes()

    def put_blob(self, key: str, data: bytes, *, suffix: str, meta: Mapping[str, Any] | None = None) -> Path:
        path = self.objects_dir / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:32] + suffix)
        atomic_write_bytes(path, data)
        self.put(key, kind="blob", files={"blob": path}, meta=meta)
        return path

    def put_files(
        self,
        key: str,
        files: Mapping[str, Path],
        *,
        value: Any = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        self.put(key, kind="files", value=value, files=files, meta=meta)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._index

    def __len__(self) -> int:
        with self._lock:
            return len(self._index)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._index), "hits": self.hits, "misses": self.misses}


# ======================================================================================
# 3. Nguồn ảnh miễn phí: Wikimedia Commons & Hugging Face Spaces
# ======================================================================================
@dataclass(frozen=True)
class WikimediaImage:
    title: str
    page_url: str
    thumb_url: str
    width: int
    height: int
    mime: str
    license: str
    artist: str

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _strip_html(text: str) -> str:
    return " ".join(_HTML_TAG_RE.sub(" ", text or "").split())


class WikimediaCommonsSource:
    """Tìm & tải ảnh có giấy phép tự do trên Wikimedia Commons (MediaWiki API, không cần key)."""

    API_URL = "https://commons.wikimedia.org/w/api.php"
    FREE_LICENSE_MARKERS = ("public domain", "cc0", "cc by", "cc-by", "pd-", "pd ")

    def __init__(
        self,
        cache: DiskDPCache,
        *,
        timeout_s: float = 30.0,
        retries: int = 2,
        user_agent: str = GENERIC_USER_AGENT,
    ) -> None:
        self.cache = cache
        self.timeout_s = timeout_s
        self.retries = retries
        self.user_agent = user_agent

    def _fetch(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    return response.read()
            except (urllib.error.URLError, OSError) as exc:
                last_error = exc
                time.sleep(0.8 * (attempt + 1))
        raise AssetSourceError(f"Không tải được {url}: {last_error}")

    @classmethod
    def is_free_license(cls, license_name: str) -> bool:
        lowered = (license_name or "").lower()
        return any(marker in lowered for marker in cls.FREE_LICENSE_MARKERS)

    def search(self, query: str, *, limit: int = 8, thumb_width: int = 640) -> list[WikimediaImage]:
        key = DiskDPCache.make_key("wikimedia:search", {"q": query, "limit": limit, "w": thumb_width})
        cached = self.cache.get_json(key)
        if cached is None:
            params = {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrnamespace": "6",
                "gsrsearch": query,
                "gsrlimit": str(limit),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": str(thumb_width),
            }
            payload = json.loads(self._fetch(f"{self.API_URL}?{urllib.parse.urlencode(params)}").decode("utf-8"))
            pages = sorted(((payload.get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 0))
            cached = []
            for page in pages:
                info = (page.get("imageinfo") or [{}])[0]
                metadata = info.get("extmetadata") or {}
                cached.append(
                    {
                        "title": str(page.get("title", "")),
                        "page_url": str(info.get("descriptionurl", "")),
                        "thumb_url": str(info.get("thumburl") or info.get("url") or ""),
                        "width": int(info.get("width") or 0),
                        "height": int(info.get("height") or 0),
                        "mime": str(info.get("mime", "")),
                        "license": _strip_html(str((metadata.get("LicenseShortName") or {}).get("value", ""))),
                        "artist": _strip_html(str((metadata.get("Artist") or {}).get("value", "")))[:200],
                    }
                )
            self.cache.put_json(key, cached, meta={"query": query})
        return [WikimediaImage(**item) for item in cached]

    def download(self, image: WikimediaImage) -> bytes:
        key = DiskDPCache.make_key("wikimedia:image", {"url": image.thumb_url})
        data = self.cache.get_blob(key)
        if data is None:
            data = self._fetch(image.thumb_url)
            suffix = ".png" if "png" in image.mime else ".jpg"
            self.cache.put_blob(key, data, suffix=suffix, meta={"title": image.title, "license": image.license})
        return data


@dataclass(frozen=True)
class SpaceAdapter:
    space_id: str
    kind: str  # "flux_schnell" | "sd35_turbo" | "z_image_turbo" | "sdxl_lightning"
    max_concurrency: int = 2


DEFAULT_IMAGE_SPACES: tuple[SpaceAdapter, ...] = (
    SpaceAdapter("black-forest-labs/FLUX.1-schnell", "flux_schnell", 3),
    SpaceAdapter("stabilityai/stable-diffusion-3.5-large-turbo", "sd35_turbo", 2),
    SpaceAdapter("Tongyi-MAI/Z-Image-Turbo", "z_image_turbo", 2),
    SpaceAdapter("ByteDance/SDXL-Lightning", "sdxl_lightning", 2),
)

_Z_IMAGE_RESOLUTIONS: tuple[tuple[int, int, str], ...] = (
    (1024, 1024, "1024x1024 ( 1:1 )"),
    (896, 1152, "896x1152 ( 7:9 )"),
    (864, 1152, "864x1152 ( 3:4 )"),
    (1152, 896, "1152x896 ( 9:7 )"),
    (1152, 864, "1152x864 ( 4:3 )"),
    (1280, 720, "1280x720 ( 16:9 )"),
    (720, 1280, "720x1280 ( 9:16 )"),
    (1248, 832, "1248x832 ( 3:2 )"),
    (832, 1248, "832x1248 ( 2:3 )"),
)
_QUOTA_MARKERS = ("quota", "zerogpu", "exceeded", "rate limit", "too many requests", "429")


def _z_image_resolution(width: int, height: int) -> str:
    target = math.log(width / height)
    return min(
        _Z_IMAGE_RESOLUTIONS,
        key=lambda item: (abs(math.log(item[0] / item[1]) - target), abs(item[0] * item[1] - width * height)),
    )[2]


def _gradio_file_path(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, Path):
        return str(result)
    if isinstance(result, Mapping):
        for name in ("path", "name"):
            value = result.get(name)
            if isinstance(value, str) and value:
                return value
        if "image" in result:
            return _gradio_file_path(result["image"])
    if isinstance(result, (list, tuple)) and result:
        return _gradio_file_path(result[0])
    raise AssetSourceError(f"Không đọc được đường dẫn ảnh từ kết quả Space ({type(result).__name__}).")


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    width: int
    height: int
    space_id: str
    seed: int
    prompt: str
    elapsed_s: float
    from_cache: bool


class HuggingFaceImageSource:
    """Vẽ ảnh bằng Hugging Face ZeroGPU Spaces miễn phí qua ``gradio_client`` — đa luồng, failover giữa các Space.

    * Mỗi Space có số "slot" song song; ``spill=True`` cho phép tràn sang Space kế tiếp khi Space ưu tiên đang bận
      (phân tải nhiều luồng lên nhiều Space), ``spill=False`` giữ phong cách thống nhất (xếp hàng ở Space ưu tiên).
    * Space lỗi (quá tải, hết hạn mức GPU) bị tạm nghỉ (cooldown), request chuyển ngay sang Space kế tiếp.
    * Mọi ảnh đã tạo được lưu ``DiskDPCache`` theo (prompt, kích thước, seed) → lần sau lấy lại O(1).
    * ``HF_TOKEN`` (tuỳ chọn, trong môi trường hoặc ``./.env``) giúp tăng hạn mức GPU miễn phí.
    """

    def __init__(
        self,
        cache: DiskDPCache,
        *,
        spaces: Sequence[SpaceAdapter] = DEFAULT_IMAGE_SPACES,
        hf_token: str | None = None,
        max_workers: int = 6,
        timeout_s: float = 240.0,
        failure_cooldown_s: float = 120.0,
        quota_cooldown_s: float = 1800.0,
        download_dir: Path | None = None,
    ) -> None:
        if not spaces:
            raise ValueError("Cần ít nhất một Space tạo ảnh.")
        self.cache = cache
        self.spaces = tuple(spaces)
        self.hf_token = hf_token if hf_token is not None else read_env_value("HF_TOKEN")
        self.timeout_s = timeout_s
        self.failure_cooldown_s = failure_cooldown_s
        self.quota_cooldown_s = quota_cooldown_s
        self.download_dir = Path(download_dir) if download_dir else CACHE_DIR / "hf_downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._clients: dict[str, Any] = {}
        self._client_locks = {space.space_id: threading.Lock() for space in self.spaces}
        self._slots = {space.space_id: threading.BoundedSemaphore(space.max_concurrency) for space in self.spaces}
        self._cooldown_until: dict[str, float] = {}
        self._state_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="hf-space")
        self.attempts: list[dict[str, Any]] = []

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _client(self, adapter: SpaceAdapter) -> Any:
        with self._client_locks[adapter.space_id]:
            client = self._clients.get(adapter.space_id)
            if client is None:
                from gradio_client import Client  # nạp chậm: chỉ cần khi thật sự gọi Space

                options: dict[str, Any] = {"verbose": False, "download_files": str(self.download_dir),
                                           "httpx_kwargs": {"timeout": self.timeout_s}, "analytics_enabled": False}
                if self.hf_token:
                    options["token"] = self.hf_token
                client = Client(adapter.space_id, **options)
                self._clients[adapter.space_id] = client
            return client

    def _cooling(self, space_id: str) -> bool:
        with self._state_lock:
            return self._cooldown_until.get(space_id, 0.0) > time.monotonic()

    def _record(self, adapter: SpaceAdapter, ok: bool, elapsed_s: float, error: str | None) -> None:
        with self._state_lock:
            self.attempts.append(
                {"space": adapter.space_id, "ok": ok, "elapsed_s": round(elapsed_s, 2), "error": error,
                 "thread": threading.current_thread().name}
            )
            if ok:
                self._cooldown_until.pop(adapter.space_id, None)
            else:
                quota = any(marker in (error or "").lower() for marker in _QUOTA_MARKERS)
                delay = self.quota_cooldown_s if quota else self.failure_cooldown_s
                self._cooldown_until[adapter.space_id] = time.monotonic() + delay

    def _acquire_space(self, spill: bool, tried: set[str]) -> SpaceAdapter | None:
        candidates = [a for a in self.spaces if a.space_id not in tried and not self._cooling(a.space_id)]
        if not candidates:
            return None
        if spill:
            for adapter in candidates:
                if self._slots[adapter.space_id].acquire(blocking=False):
                    return adapter
        adapter = candidates[0]
        self._slots[adapter.space_id].acquire()
        return adapter

    def _invoke(self, adapter: SpaceAdapter, prompt: str, width: int, height: int, seed: int, negative: str) -> str:
        started = time.monotonic()
        client = self._client(adapter)
        # Trần thời gian tính cho CẢ lượt gọi (kết nối Space + chờ kết quả), không chỉ phần chờ kết quả.
        budget = max(0.5, self.timeout_s - (time.monotonic() - started))
        if adapter.kind == "flux_schnell":
            job = client.submit(prompt=prompt, seed=seed, randomize_seed=False, width=width, height=height,
                                num_inference_steps=4, api_name="/infer")
            return _gradio_file_path(job.result(timeout=budget)[0])
        if adapter.kind == "sd35_turbo":
            # Space giới hạn mỗi cạnh ≤ 1024 (bội 64): thu nhỏ giữ tỉ lệ; ảnh trả về được fit_cover lại đúng kích thước.
            factor = min(1.0, 1024 / max(width, height))
            sd_w = max(256, int(width * factor) // 64 * 64)
            sd_h = max(256, int(height * factor) // 64 * 64)
            job = client.submit(prompt=prompt, negative_prompt=negative, seed=seed, randomize_seed=False, width=sd_w,
                                height=sd_h, guidance_scale=0.0, num_inference_steps=4, api_name="/infer")
            return _gradio_file_path(job.result(timeout=budget)[0])
        if adapter.kind == "z_image_turbo":
            job = client.submit(prompt=prompt, resolution=_z_image_resolution(width, height), seed=seed, steps=8,
                                shift=3.0, random_seed=False, gallery_images=[], api_name="/generate")
            return _gradio_file_path(job.result(timeout=budget)[0])
        if adapter.kind == "sdxl_lightning":
            job = client.submit(prompt=prompt, ckpt="4-Step", api_name="/generate_image")
            return _gradio_file_path(job.result(timeout=budget))
        raise ValueError(f"Loại Space không hỗ trợ: {adapter.kind}")

    def generate(
        self,
        prompt: str,
        *,
        width: int,
        height: int,
        seed: int,
        negative_prompt: str = "",
        spill: bool = True,
    ) -> GeneratedImage:
        """Tạo ảnh (chặn luồng). Trả ảnh PNG đúng ``width × height`` (cắt phủ nếu Space trả kích thước khác)."""
        key = DiskDPCache.make_key("hf:image", {"p": prompt, "w": width, "h": height, "s": seed, "n": negative_prompt})
        entry = self.cache.get(key)
        if entry is not None and "blob" in entry.files:
            return GeneratedImage(entry.files["blob"].read_bytes(), width, height,
                                  str(entry.meta.get("space_id", "cache")), seed, prompt, 0.0, True)
        errors: list[str] = []
        tried: set[str] = set()
        while True:
            adapter = self._acquire_space(spill, tried)
            if adapter is None:
                break
            tried.add(adapter.space_id)
            started = time.perf_counter()
            try:
                path = self._invoke(adapter, prompt, width, height, seed, negative_prompt)
                with Image.open(path) as raw:
                    raw.load()
                    image = fit_cover(raw.convert("RGB"), (width, height))
                data = image_to_png_bytes(image)
            except Exception as exc:  # gradio_client có thể ném nhiều kiểu lỗi (AppError, timeout, httpx…)
                message = f"{type(exc).__name__}: {str(exc)[:200]}"
                self._record(adapter, False, time.perf_counter() - started, message)
                errors.append(f"{adapter.space_id}: {message}")
                continue
            finally:
                self._slots[adapter.space_id].release()
            elapsed = time.perf_counter() - started
            self._record(adapter, True, elapsed, None)
            self.cache.put_blob(key, data, suffix=".png", meta={"space_id": adapter.space_id, "seed": seed,
                                                                "prompt": prompt, "elapsed_s": round(elapsed, 2)})
            return GeneratedImage(data, width, height, adapter.space_id, seed, prompt, elapsed, False)
        detail = " | ".join(errors) if errors else "mọi Space đang tạm nghỉ sau lỗi trước đó"
        raise ImageGenerationError(f"Không Space nào tạo được ảnh: {detail}")

    async def generate_async(
        self,
        prompt: str,
        *,
        width: int,
        height: int,
        seed: int,
        negative_prompt: str = "",
        spill: bool = True,
    ) -> GeneratedImage:
        loop = asyncio.get_running_loop()
        call = functools.partial(self.generate, prompt, width=width, height=height, seed=seed,
                                 negative_prompt=negative_prompt, spill=spill)
        return await loop.run_in_executor(self._executor, call)


# ======================================================================================
# 4. AI Đạo diễn — thị giác (Gemini đa phương thức, 5 key miễn phí)
# ======================================================================================
def _box_schema() -> dict[str, Any]:
    return {"type": "ARRAY", "items": {"type": "INTEGER"}}


class DirectorVision:
    """Các nhiệm vụ "nhìn ảnh" của AI Đạo diễn, chạy trên ``MultiThreadKeyPool`` (Gemini, gói miễn phí).

    Ảnh được gửi dưới dạng JPEG ≤ 768 px; kết quả JSON (theo schema) được lưu ``DiskDPCache`` theo
    (nhiệm vụ, băm ảnh, prompt) → hỏi lại cùng câu trả về O(1), không tốn request.
    """

    DEFAULT_MODELS: tuple[str, ...] = ("gemini-3-flash-preview", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite")

    def __init__(
        self,
        pool: MultiThreadKeyPool,
        cache: DiskDPCache,
        *,
        models: Sequence[str] = DEFAULT_MODELS,
        request_timeout_s: float = 45.0,
    ) -> None:
        self.pool = pool
        self.cache = cache
        self.models = tuple(models)
        self.request_timeout_s = request_timeout_s

    async def ask_json(
        self,
        task: str,
        prompt: str,
        images: Sequence[Image.Image | bytes],
        schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        jpegs = [image_to_jpeg_bytes(open_image(item) if isinstance(item, bytes) else item) for item in images]
        key = DiskDPCache.make_key(
            f"vision:{task}",
            {"prompt": prompt, "images": [sha256_hex(data) for data in jpegs], "schema": schema, "models": self.models},
        )
        cached = self.cache.get_json(key)
        if isinstance(cached, dict):
            return cached
        parts: list[dict[str, Any]] = [{"text": prompt}]
        parts += [{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(data).decode("ascii")}}
                  for data in jpegs]
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "thinkingConfig": {"thinkingLevel": "minimal"},
            },
        }
        result = await self.pool.generate(self.models, payload, tag=f"director:vision:{task}",
                                          request_timeout_s=self.request_timeout_s)
        data = _parse_json_object(result.text)
        self.cache.put_json(key, data, meta={"task": task, "model": result.model, "key": result.key_label})
        return data

    async def rank_references(self, profile: CharacterProfile, images: Sequence[bytes]) -> dict[str, Any]:
        prompt = (
            f"The {len(images)} images (index 0..{len(images) - 1}, in order) are candidate reference depictions found "
            f"on Wikimedia Commons for the Vietnamese historical figure {profile.display_name} ({profile.full_name}), "
            f"{profile.era_en}. For every image decide depicts_hero (a traditional painting, illustration or statue that "
            "represents THIS figure — not a road, sign, coin, building, map or another person), is_portrait (one person, "
            "face visible) and a score 0-10 for usefulness as a reference of his traditional iconography. Then give "
            "best_index (-1 if none is acceptable) and, for that image, describe the figure as a living person in "
            "35-70 English words: apparent age, face, beard/mustache, hair and headwear, costume type and colors, "
            "armor, props. If the best image is a statue, translate bronze/stone into natural skin, hair and "
            "historically plausible costume colors."
        )
        schema = {
            "type": "OBJECT",
            "properties": {
                "candidates": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "index": {"type": "INTEGER"},
                            "depicts_hero": {"type": "BOOLEAN"},
                            "is_portrait": {"type": "BOOLEAN"},
                            "score": {"type": "INTEGER"},
                        },
                        "required": ["index", "depicts_hero", "is_portrait", "score"],
                    },
                },
                "best_index": {"type": "INTEGER"},
                "iconography": {"type": "STRING"},
            },
            "required": ["candidates", "best_index", "iconography"],
        }
        return await self.ask_json("rank_references", prompt, images, schema)

    async def verify_portrait(self, profile: CharacterProfile, image: Image.Image, look: str) -> dict[str, Any]:
        prompt = (
            f"This is a generated reference portrait for 2.5D animation of {profile.display_name}, {profile.era_en}. "
            f"Expected look: {look}. Evaluate it strictly and return: single_person, frontal (facing the viewer), "
            "body_framing_ok (whole head and torso visible, roughly knee-length or waist-up), hands_ok (no deformed or "
            "extra hands/fingers), mouth_visible_closed, plain_background (flat uniform background, no scenery), "
            "historical_costume (pre-modern Vietnamese/East-Asian costume, no anachronism), matches_description (0-10), "
            "overall (0-10, quality as an animation reference) and issues (short text)."
        )
        schema = {
            "type": "OBJECT",
            "properties": {
                "single_person": {"type": "BOOLEAN"},
                "frontal": {"type": "BOOLEAN"},
                "body_framing_ok": {"type": "BOOLEAN"},
                "hands_ok": {"type": "BOOLEAN"},
                "mouth_visible_closed": {"type": "BOOLEAN"},
                "plain_background": {"type": "BOOLEAN"},
                "historical_costume": {"type": "BOOLEAN"},
                "matches_description": {"type": "INTEGER"},
                "overall": {"type": "INTEGER"},
                "issues": {"type": "STRING"},
            },
            "required": ["single_person", "frontal", "body_framing_ok", "hands_ok", "mouth_visible_closed",
                         "plain_background", "historical_costume", "matches_description", "overall", "issues"],
        }
        return await self.ask_json("verify_portrait", prompt, [image], schema)

    async def analyze_rig(self, image: Image.Image) -> dict[str, Any]:
        prompt = (
            "Analyse this standing character portrait for 2D skeletal rigging. Return face_box and mouth_box as "
            "[ymin, xmin, ymax, xmax] normalized 0-1000 (mouth_box tightly covers the lips, including the mustache "
            "directly above them). Return keypoints as [y, x] normalized 0-1000 for: head_top, chin, neck, "
            "left_shoulder, right_shoulder, left_elbow, right_elbow, left_wrist, right_wrist, pelvis, upper_lip, "
            "lower_lip, mouth_left_corner, mouth_right_corner."
        )
        schema = {
            "type": "OBJECT",
            "properties": {
                "face_box": _box_schema(),
                "mouth_box": _box_schema(),
                "keypoints": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {"name": {"type": "STRING"}, "point": _box_schema()},
                        "required": ["name", "point"],
                    },
                },
            },
            "required": ["face_box", "mouth_box", "keypoints"],
        }
        return await self.ask_json("analyze_rig", prompt, [image], schema)

    async def analyze_scene(self, image: Image.Image, scene_mode: str) -> dict[str, Any]:
        purpose = (
            "a talking character will stand in the centre foreground"
            if scene_mode == SCENE_FOREGROUND_TALKING
            else "a commanding character will be composited standing in the foreground"
        )
        prompt = (
            f"Analyse this background painting for 2.5D parallax animation; {purpose}. Return horizon_y (0-1000), "
            "focus_point [y, x] (the most interesting area), hero_anchor [y, x] (foreground spot where the standing "
            "character should be placed without covering the focus) and regions: boxes [ymin, xmin, ymax, xmax] "
            "normalized 0-1000 labelled sky, water, fire, smoke or vegetation (fields, grass, trees, bamboo). "
            "Return every clearly visible region; an empty list is allowed."
        )
        schema = {
            "type": "OBJECT",
            "properties": {
                "horizon_y": {"type": "INTEGER"},
                "focus_point": _box_schema(),
                "hero_anchor": _box_schema(),
                "regions": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "label": {"type": "STRING", "enum": ["sky", "water", "fire", "smoke", "vegetation"]},
                            "box_2d": _box_schema(),
                        },
                        "required": ["label", "box_2d"],
                    },
                },
            },
            "required": ["horizon_y", "focus_point", "hero_anchor", "regions"],
        }
        return await self.ask_json("analyze_scene", prompt, [image], schema)

    async def judge_consistency(self, reference: Image.Image, frame: Image.Image) -> dict[str, Any]:
        prompt = (
            "Image 1 is the reference character model. Image 2 is a frame from an animation. Is the main character in "
            "image 2 the SAME character as image 1 (same face, beard/hair, headwear, costume and colors)? Return "
            "same_character, face_match, hair_beard_match, costume_match (each 0-10) and short notes."
        )
        schema = {
            "type": "OBJECT",
            "properties": {
                "same_character": {"type": "BOOLEAN"},
                "face_match": {"type": "INTEGER"},
                "hair_beard_match": {"type": "INTEGER"},
                "costume_match": {"type": "INTEGER"},
                "notes": {"type": "STRING"},
            },
            "required": ["same_character", "face_match", "hair_beard_match", "costume_match", "notes"],
        }
        return await self.ask_json("judge_consistency", prompt, [reference, frame], schema)


# ======================================================================================
# 5. Tách lớp nhân vật & đo đạc ảnh
# ======================================================================================
@dataclass(frozen=True)
class CutoutResult:
    image: Image.Image
    method: str  # "alpha" | "chroma" | "flood"
    coverage: float
    corners_transparent: bool

    @property
    def usable(self) -> bool:
        return self.corners_transparent and 0.06 <= self.coverage <= 0.85


def alpha_coverage(alpha: Image.Image) -> float:
    histogram = alpha.histogram()
    total = sum(histogram) or 1
    return sum(histogram[128:]) / total


def corners_transparent(alpha: Image.Image, patch: int = 6) -> bool:
    width, height = alpha.size
    for x0, y0 in ((0, 0), (width - patch, 0), (0, height - patch), (width - patch, height - patch)):
        if alpha.crop((x0, y0, x0 + patch, y0 + patch)).getextrema()[1] > 40:
            return False
    return True


def border_color(rgb: Image.Image) -> tuple[int, int, int]:
    width, height = rgb.size
    samples: list[tuple[int, int, int]] = []
    step = max(1, min(width, height) // 64)
    for x in range(0, width, step):
        samples.append(rgb.getpixel((x, 0)))
        samples.append(rgb.getpixel((x, height - 1)))
    for y in range(0, height, step):
        samples.append(rgb.getpixel((0, y)))
        samples.append(rgb.getpixel((width - 1, y)))
    channels = list(zip(*samples))
    return tuple(sorted(channel)[len(channel) // 2] for channel in channels)  # type: ignore[return-value]


def chroma_key_cutout(image: Image.Image) -> CutoutResult | None:
    """Tách nền xanh (chroma key) bằng phép toán kênh màu của Pillow (G − max(R,B)), có khử viền xanh."""
    rgb = image.convert("RGB")
    br, bg, bb = border_color(rgb)
    border_excess = bg - max(br, bb)
    if border_excess < 40:
        return None
    red, green, blue = rgb.split()
    max_rb = ImageChops.lighter(red, blue)
    excess = ImageChops.subtract(green, max_rb)
    high = max(30, int(border_excess * 0.72))
    low = max(12, int(border_excess * 0.30))
    table = [255 if v <= low else 0 if v >= high else int(round(255 * (high - v) / (high - low))) for v in range(256)]
    alpha = excess.point(table).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    despilled = ImageChops.darker(green, ImageChops.add(max_rb, Image.new("L", rgb.size, 10)))
    rgba = Image.merge("RGBA", (red, despilled, blue, alpha))
    return CutoutResult(rgba, "chroma", alpha_coverage(alpha), corners_transparent(alpha))


def flood_fill_cutout(image: Image.Image, tolerance: int = 26) -> CutoutResult:
    """Tách nền phẳng bất kỳ màu bằng loang màu từ mép ảnh (dùng cho ảnh mẫu do người dùng cung cấp)."""
    rgb = image.convert("RGB")
    work = rgb.copy()
    marker = (1, 254, 3)
    width, height = rgb.size
    seeds = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1),
             (width // 2, 0), (0, height // 2), (width - 1, height // 2)]
    for seed in seeds:
        if work.getpixel(seed) != marker:
            ImageDraw.floodfill(work, seed, marker, thresh=tolerance)
    difference = ImageChops.difference(work, Image.new("RGB", rgb.size, marker)).convert("L")
    alpha = difference.point(lambda v: 0 if v == 0 else 255)
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.0))
    rgba = rgb.convert("RGBA")
    rgba.putalpha(alpha)
    return CutoutResult(rgba, "flood", alpha_coverage(alpha), corners_transparent(alpha))


def make_cutout(image: Image.Image) -> CutoutResult:
    """Lớp nhân vật RGBA: giữ alpha sẵn có → chroma key nền xanh → loang màu nền phẳng."""
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        if alpha.getextrema()[0] < 250:
            return CutoutResult(rgba, "alpha", alpha_coverage(alpha), corners_transparent(alpha))
    chroma = chroma_key_cutout(image)
    if chroma is not None and chroma.usable:
        return chroma
    flood = flood_fill_cutout(image)
    if flood.usable or chroma is None:
        return flood
    return chroma


def alpha_bbox(rgba: Image.Image, threshold: int = 24) -> tuple[int, int, int, int]:
    box = rgba.getchannel("A").point(lambda v: 255 if v > threshold else 0).getbbox()
    return box if box else (0, 0, rgba.width, rgba.height)


def mean_abs_diff(first: Image.Image, second: Image.Image) -> float:
    if first.mode != second.mode:
        second = second.convert(first.mode)
    stat = ImageStat.Stat(ImageChops.difference(first, second))
    return sum(stat.mean) / len(stat.mean)


def histogram_similarity(first: Image.Image, second: Image.Image, bins: int = 16) -> float:
    """Hệ số Bhattacharyya giữa phân bố màu của phần nhân vật (alpha > 50%) — 1.0 là trùng khớp."""

    def distribution(image: Image.Image) -> list[list[float]]:
        rgba = image.convert("RGBA")
        mask = rgba.getchannel("A").point(lambda v: 255 if v > 128 else 0)
        raw = rgba.convert("RGB").histogram(mask=mask)
        result = []
        for channel in range(3):
            values = raw[channel * 256 : (channel + 1) * 256]
            grouped = [sum(values[i : i + 256 // bins]) for i in range(0, 256, 256 // bins)]
            total = sum(grouped) or 1
            result.append([value / total for value in grouped])
        return result

    first_dist, second_dist = distribution(first), distribution(second)
    scores = [sum(math.sqrt(p * q) for p, q in zip(a, b)) for a, b in zip(first_dist, second_dist)]
    return sum(scores) / len(scores)


# ======================================================================================
# 6. Rig 2.5D trên chính ảnh gốc của nhân vật
# ======================================================================================
JOINT_NAMES: tuple[str, ...] = (
    "head_top", "chin", "neck", "shoulder_l", "shoulder_r", "elbow_l", "elbow_r", "wrist_l", "wrist_r",
    "pelvis", "upper_lip", "lower_lip", "mouth_l", "mouth_r",
)


@dataclass(frozen=True)
class HeroRig:
    """Khung xương 2D (toạ độ pixel; ``_l``/``_r`` là bên TRÁI/PHẢI của khung hình, không phải của nhân vật)."""

    character_id: str
    width: int
    height: int
    joints: Mapping[str, tuple[float, float]]
    face_box: tuple[float, float, float, float]
    mouth_box: tuple[float, float, float, float]
    source: str = "gemini"

    @property
    def face_w(self) -> float:
        return max(1.0, self.face_box[2] - self.face_box[0])

    @property
    def face_h(self) -> float:
        return max(1.0, self.face_box[3] - self.face_box[1])

    @property
    def lip_y(self) -> float:
        return (self.joints["upper_lip"][1] + self.joints["lower_lip"][1]) / 2.0

    @property
    def mouth_center(self) -> tuple[float, float]:
        return ((self.joints["mouth_l"][0] + self.joints["mouth_r"][0]) / 2.0, self.lip_y)

    @property
    def mouth_half_width(self) -> float:
        corners = (self.joints["mouth_r"][0] - self.joints["mouth_l"][0]) / 2.0
        return max(3.0, corners, (self.mouth_box[2] - self.mouth_box[0]) * 0.22)

    def transformed(self, *, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0,
                    width: int | None = None, height: int | None = None) -> "HeroRig":
        def move(point: tuple[float, float]) -> tuple[float, float]:
            return ((point[0] + dx) * scale, (point[1] + dy) * scale)

        def move_box(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
            (x0, y0), (x1, y1) = move((box[0], box[1])), move((box[2], box[3]))
            return (x0, y0, x1, y1)

        return HeroRig(
            character_id=self.character_id,
            width=width if width is not None else round(self.width * scale),
            height=height if height is not None else round(self.height * scale),
            joints={name: move(point) for name, point in self.joints.items()},
            face_box=move_box(self.face_box),
            mouth_box=move_box(self.mouth_box),
            source=self.source,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "width": self.width,
            "height": self.height,
            "joints": {name: [round(x, 2), round(y, 2)] for name, (x, y) in self.joints.items()},
            "face_box": [round(v, 2) for v in self.face_box],
            "mouth_box": [round(v, 2) for v in self.mouth_box],
            "source": self.source,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "HeroRig":
        return cls(
            character_id=str(data["character_id"]),
            width=int(data["width"]),
            height=int(data["height"]),
            joints={name: (float(p[0]), float(p[1])) for name, p in data["joints"].items()},
            face_box=tuple(float(v) for v in data["face_box"]),  # type: ignore[arg-type]
            mouth_box=tuple(float(v) for v in data["mouth_box"]),  # type: ignore[arg-type]
            source=str(data.get("source", "gemini")),
        )


def _norm_box(value: Any, width: int, height: int) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    box = (xmin / 1000 * width, ymin / 1000 * height, xmax / 1000 * width, ymax / 1000 * height)
    if box[2] - box[0] < 2 or box[3] - box[1] < 2:
        return None
    return (_clamp(box[0], 0, width), _clamp(box[1], 0, height), _clamp(box[2], 0, width), _clamp(box[3], 0, height))


def build_rig(
    character_id: str,
    analysis: Mapping[str, Any],
    width: int,
    height: int,
    alpha_box: tuple[int, int, int, int] | None = None,
) -> HeroRig:
    """Chuẩn hoá kết quả dò khung xương của AI (toạ độ 0-1000) thành ``HeroRig`` hợp lệ, có giá trị dự phòng."""
    figure = alpha_box or (0, 0, width, height)
    points: dict[str, list[tuple[float, float]]] = {}
    for item in analysis.get("keypoints") or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "")).strip().lower()
        point = item.get("point")
        if not (isinstance(point, (list, tuple)) and len(point) == 2):
            continue
        try:
            y, x = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            continue
        position = (_clamp(x / 1000 * width, 0, width - 1), _clamp(y / 1000 * height, 0, height - 1))
        for family in ("shoulder", "elbow", "wrist"):
            if family in name:
                name = family
        if "mouth" in name and "corner" in name:
            name = "mouth_corner"
        points.setdefault(name, []).append(position)

    def single(name: str) -> tuple[float, float] | None:
        found = points.get(name)
        return found[0] if found else None

    face = _norm_box(analysis.get("face_box"), width, height)
    if face is not None:
        face_w, face_h = face[2] - face[0], face[3] - face[1]
        if not (0.03 * width <= face_w <= 0.6 * width and 0.03 * height <= face_h <= 0.5 * height):
            face = None
    if face is None:
        figure_h = figure[3] - figure[1]
        face_h = 0.12 * figure_h
        face_w = 0.72 * face_h
        cx = (figure[0] + figure[2]) / 2
        top = figure[1] + 0.05 * figure_h
        face = (cx - face_w / 2, top, cx + face_w / 2, top + face_h)
    fx0, fy0, fx1, fy1 = face
    fw, fh = fx1 - fx0, fy1 - fy0
    fcx = (fx0 + fx1) / 2

    mouth = _norm_box(analysis.get("mouth_box"), width, height)
    if mouth is not None:
        mcx, mcy = (mouth[0] + mouth[2]) / 2, (mouth[1] + mouth[3]) / 2
        if not (fx0 - 0.15 * fw <= mcx <= fx1 + 0.15 * fw and fy0 + 0.45 * fh <= mcy <= fy1 + 0.2 * fh
                and mouth[2] - mouth[0] <= 1.1 * fw):
            mouth = None
    if mouth is None:
        mouth = (fcx - 0.22 * fw, fy0 + 0.70 * fh, fcx + 0.22 * fw, fy0 + 0.86 * fh)
    mx0, my0, mx1, my1 = mouth
    mw, mh = mx1 - mx0, my1 - my0
    mcx = (mx0 + mx1) / 2

    head_top = single("head_top")
    if head_top is None or head_top[1] > fy0 + 0.15 * fh:
        head_top = (fcx, max(0.0, fy0 - 0.15 * fh))
    chin = single("chin")
    if chin is None or not (fy0 + 0.6 * fh <= chin[1] <= fy1 + 0.4 * fh):
        chin = (fcx, fy1)
    neck = single("neck")
    if neck is None or not (chin[1] < neck[1] <= chin[1] + 1.3 * fh):
        neck = (fcx, chin[1] + 0.35 * fh)

    def pair(family: str, default_l: tuple[float, float], default_r: tuple[float, float],
             valid: Callable[[tuple[float, float], int], bool]) -> tuple[tuple[float, float], tuple[float, float]]:
        found = sorted(points.get(family, []), key=lambda p: p[0])
        left = found[0] if len(found) >= 2 else None
        right = found[-1] if len(found) >= 2 else None
        if left is None or not valid(left, -1):
            left = default_l
        if right is None or not valid(right, 1):
            right = default_r
        return left, right

    shoulder_y = neck[1] + 0.45 * fh
    shoulder_l, shoulder_r = pair(
        "shoulder",
        (fcx - 1.3 * fw, shoulder_y),
        (fcx + 1.3 * fw, shoulder_y),
        lambda p, side: neck[1] - 0.3 * fh <= p[1] <= neck[1] + 1.6 * fh and (p[0] - fcx) * side >= 0.45 * fw,
    )
    elbow_l, elbow_r = pair(
        "elbow",
        (shoulder_l[0] - 0.2 * fw, shoulder_l[1] + 1.9 * fh),
        (shoulder_r[0] + 0.2 * fw, shoulder_r[1] + 1.9 * fh),
        lambda p, side: p[1] >= (shoulder_l if side < 0 else shoulder_r)[1] + 0.5 * fh,
    )
    wrist_l, wrist_r = pair(
        "wrist",
        (elbow_l[0] + 0.1 * fw, elbow_l[1] + 1.6 * fh),
        (elbow_r[0] - 0.1 * fw, elbow_r[1] + 1.6 * fh),
        lambda p, side: p[1] >= (elbow_l if side < 0 else elbow_r)[1] - 0.4 * fh,
    )
    pelvis = single("pelvis")
    top_shoulder = min(shoulder_l[1], shoulder_r[1])
    if pelvis is None or pelvis[1] < top_shoulder + 1.5 * fh:
        pelvis = (fcx, min(height - 1.0, top_shoulder + 3.0 * fh))

    upper_lip = single("upper_lip")
    lower_lip = single("lower_lip")
    lip_ok = (lambda p: p is not None and mx0 - 0.3 * mw <= p[0] <= mx1 + 0.3 * mw
              and my0 - 0.6 * mh <= p[1] <= my1 + 0.6 * mh)
    if not lip_ok(upper_lip):
        upper_lip = (mcx, my0 + 0.42 * mh)
    if not lip_ok(lower_lip):
        lower_lip = (mcx, my0 + 0.62 * mh)
    if lower_lip[1] <= upper_lip[1] + 1.0:  # type: ignore[index]
        middle = (upper_lip[1] + lower_lip[1]) / 2  # type: ignore[index]
        upper_lip, lower_lip = (upper_lip[0], middle - 0.08 * mh), (lower_lip[0], middle + 0.08 * mh)  # type: ignore[index]
    lip_y = (upper_lip[1] + lower_lip[1]) / 2  # type: ignore[index]
    corners = sorted(points.get("mouth_corner", []), key=lambda p: p[0])
    if len(corners) >= 2 and mx0 - 0.4 * mw <= corners[0][0] < corners[-1][0] <= mx1 + 0.4 * mw:
        mouth_l, mouth_r = (corners[0][0], lip_y), (corners[-1][0], lip_y)
    else:
        mouth_l, mouth_r = (mx0 + 0.22 * mw, lip_y), (mx1 - 0.22 * mw, lip_y)

    joints = {
        "head_top": head_top, "chin": chin, "neck": neck, "shoulder_l": shoulder_l, "shoulder_r": shoulder_r,
        "elbow_l": elbow_l, "elbow_r": elbow_r, "wrist_l": wrist_l, "wrist_r": wrist_r, "pelvis": pelvis,
        "upper_lip": upper_lip, "lower_lip": lower_lip, "mouth_l": mouth_l, "mouth_r": mouth_r,
    }
    joints = {name: (_clamp(p[0], 0, width - 1), _clamp(p[1], 0, height - 1)) for name, p in joints.items()}  # type: ignore[index]
    source = "gemini" if analysis.get("keypoints") else "heuristic"
    return HeroRig(character_id, width, height, joints, face, mouth, source)


POSE_FIELDS: tuple[str, ...] = (
    "body_dx", "body_dy", "lean_deg", "breath", "head_tilt_deg", "head_nod",
    "upper_l_deg", "lower_l_deg", "upper_r_deg", "lower_r_deg", "jaw_open", "mouth_width",
)
# Trọng số khoảng cách giữa các tư thế (đổi đơn vị độ / tỉ lệ về cùng thang) cho DP nội suy khung hình.
POSE_DISTANCE_WEIGHTS: tuple[float, ...] = (25.0, 25.0, 0.25, 100.0, 0.3, 40.0, 0.05, 0.05, 0.05, 0.05, 0.0, 0.0)


@dataclass(frozen=True)
class RigPose:
    """Tư thế rig. Góc theo độ, dương = ngược chiều kim đồng hồ trên màn hình; dịch chuyển theo đơn vị chiều cao mặt."""

    body_dx: float = 0.0
    body_dy: float = 0.0
    lean_deg: float = 0.0
    breath: float = 0.0
    head_tilt_deg: float = 0.0
    head_nod: float = 0.0
    upper_l_deg: float = 0.0
    lower_l_deg: float = 0.0
    upper_r_deg: float = 0.0
    lower_r_deg: float = 0.0
    jaw_open: float = 0.0
    mouth_width: float = 1.0

    def vector(self) -> tuple[float, ...]:
        return tuple(getattr(self, name) for name in POSE_FIELDS)

    @classmethod
    def from_vector(cls, values: Sequence[float]) -> "RigPose":
        return cls(**{name: float(value) for name, value in zip(POSE_FIELDS, values)})

    def with_mouth(self, jaw_open: float, mouth_width: float) -> "RigPose":
        return dataclasses.replace(self, jaw_open=jaw_open, mouth_width=mouth_width)

    def mirrored(self) -> "RigPose":
        """Đối xứng trái ↔ phải của khung hình (dùng khi nhân vật đứng bên phải cảnh)."""
        return RigPose(
            body_dx=-self.body_dx, body_dy=self.body_dy, lean_deg=-self.lean_deg, breath=self.breath,
            head_tilt_deg=-self.head_tilt_deg, head_nod=self.head_nod,
            upper_l_deg=-self.upper_r_deg, lower_l_deg=-self.lower_r_deg,
            upper_r_deg=-self.upper_l_deg, lower_r_deg=-self.lower_l_deg,
            jaw_open=self.jaw_open, mouth_width=self.mouth_width,
        )

    def is_rest(self, epsilon: float = 1e-6) -> bool:
        rest = RigPose().vector()
        return all(abs(a - b) <= epsilon for a, b in zip(self.vector(), rest))


Affine = tuple[float, float, float, float, float, float]  # x' = a·x + b·y + c ; y' = d·x + e·y + f


def _affine_mul(first: Affine, second: Affine) -> Affine:
    """first ∘ second (áp ``second`` trước)."""
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (a1 * a2 + b1 * d2, a1 * b2 + b1 * e2, a1 * c2 + b1 * f2 + c1,
            d1 * a2 + e1 * d2, d1 * b2 + e1 * e2, d1 * c2 + e1 * f2 + f1)


def _affine_translate(tx: float, ty: float) -> Affine:
    return (1.0, 0.0, tx, 0.0, 1.0, ty)


def _affine_rotate(degrees: float) -> Affine:
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    return (cosine, sine, 0.0, -sine, cosine, 0.0)  # ngược chiều kim đồng hồ trên ảnh (trục y hướng xuống)


def _affine_scale(factor: float) -> Affine:
    return (factor, 0.0, 0.0, 0.0, factor, 0.0)


def _affine_about(pivot: tuple[float, float], transform: Affine) -> Affine:
    return _affine_mul(_affine_translate(pivot[0], pivot[1]),
                       _affine_mul(transform, _affine_translate(-pivot[0], -pivot[1])))


def _affine_apply(transform: Affine, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    return (a * x + b * y + c, d * x + e * y + f)


def _affine_inverse(transform: Affine) -> Affine:
    a, b, c, d, e, f = transform
    det = a * e - b * d
    if abs(det) < 1e-12:
        raise ValueError("Ma trận biến đổi suy biến.")
    return (e / det, -b / det, (b * f - c * e) / det, -d / det, a / det, (c * d - a * f) / det)


def _segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    length2 = vx * vx + vy * vy
    t = 0.0 if length2 <= 1e-9 else _clamp((wx * vx + wy * vy) / length2, 0.0, 1.0)
    return math.hypot(wx - t * vx, wy - t * vy)


_BONES: tuple[str, ...] = ("body", "head", "upper_l", "lower_l", "upper_r", "lower_r")


class HeroRigRenderer:
    """Biến dạng lưới trên chính ảnh gốc (Linear Blend Skinning + nghịch đảo điểm bất động + Pillow MESH).

    * Mỗi đỉnh lưới ĐÍCH được ánh xạ ngược về ảnh nguồn: p ← Σ w_b(p)·M_b⁻¹·q − Δ_hàm(p), lặp điểm bất động.
    * Hàm dưới (jaw) là trường dịch chuyển mượt quanh miệng; khoang miệng được tô tối giữa hai môi khi mở.
    * Ảnh được biến đổi ở dạng alpha nhân trước (RGBa) để không lem viền.
    * Tư thế nghỉ → trả về bản sao CHÍNH XÁC ảnh gốc (không lấy mẫu lại).
    """

    def __init__(self, image: Image.Image, rig: HeroRig, *, grid_cell: int = 28, jaw_drop_ratio: float = 0.11,
                 iterations: int = 8) -> None:
        self.image = image.convert("RGBA")
        if self.image.size != (rig.width, rig.height):
            raise ValueError("Kích thước ảnh và rig không khớp.")
        self.rig = rig
        self.iterations = max(1, iterations)
        self._premultiplied = self.image.convert("RGBa")
        self._alpha = self.image.getchannel("A")
        j = rig.joints
        self.fw, self.fh = rig.face_w, rig.face_h
        fx0, fy0, fx1, fy1 = rig.face_box
        self.chin_y = j["chin"][1]
        self.neck = j["neck"]
        self.neck_y = self.neck[1]
        self.pelvis = j["pelvis"]
        self.head_center = ((fx0 + fx1) / 2.0, (j["head_top"][1] + self.chin_y) / 2.0)
        self.head_rx = 0.62 * self.fw
        self.head_ry = max(0.55 * self.fh, (self.chin_y - j["head_top"][1]) / 2.0 + 0.05 * self.fh)
        self.shoulders = (j["shoulder_l"], j["shoulder_r"])
        self.elbows = (j["elbow_l"], j["elbow_r"])
        hands = []
        for elbow, wrist in ((j["elbow_l"], j["wrist_l"]), (j["elbow_r"], j["wrist_r"])):
            hands.append((wrist[0] + 0.35 * (wrist[0] - elbow[0]), wrist[1] + 0.35 * (wrist[1] - elbow[1])))
        self.hands = tuple(hands)
        self.lip_y = rig.lip_y
        self.mouth_center = rig.mouth_center
        self.mouth_half_w = rig.mouth_half_width
        self.upper_lip = j["upper_lip"]
        self.lower_lip = j["lower_lip"]
        self.rest_lip_gap = max(1.0, self.lower_lip[1] - self.upper_lip[1])
        self.jaw_drop_px = jaw_drop_ratio * self.fh
        width, height = self.image.size
        columns = max(2, math.ceil(width / grid_cell))
        rows = max(2, math.ceil(height / grid_cell))
        self._grid_shape = (columns + 1, rows + 1)
        xs = [round(i * width / columns) for i in range(columns + 1)]
        ys = [round(k * height / rows) for k in range(rows + 1)]
        self._vertices = [(float(x), float(y)) for y in ys for x in xs]
        self._plate: Image.Image | None = None
        self._plate_lock = threading.Lock()
        stride = columns + 1
        self._cells = []
        for k in range(rows):
            for i in range(columns):
                ul, ur = k * stride + i, k * stride + i + 1
                ll, lr = (k + 1) * stride + i, (k + 1) * stride + i + 1
                self._cells.append(((xs[i], ys[k], xs[i + 1], ys[k + 1]), ul, ll, lr, ur))

    # ---------------------------------------------------------------- trọng số & biến đổi
    def influences(self, x: float, y: float) -> tuple[float, float, float, float, float, float]:
        """Trọng số (body, head, upper_l, lower_l, upper_r, lower_r) tại điểm nghỉ (x, y); tổng = 1."""
        fw, fh = self.fw, self.fh
        hx, hy = self.head_center
        ellipse = math.hypot((x - hx) / self.head_rx, (y - hy) / self.head_ry)
        head = _falloff(ellipse, 1.0, 1.35)
        if head > 0.0:
            gate_span = max(1.0, self.neck_y + 0.25 * fh - self.chin_y)
            head *= 1.0 - _smooth01((y - self.chin_y) / gate_span)
        arms: list[float] = []
        for side, shoulder, elbow, hand in ((-1, self.shoulders[0], self.elbows[0], self.hands[0]),
                                            (1, self.shoulders[1], self.elbows[1], self.hands[1])):
            upper = _falloff(_segment_distance(x, y, *shoulder, *elbow), 0.30 * fw, 0.58 * fw)
            if upper > 0.0:
                upper *= _smooth01((y - (shoulder[1] - 0.6 * fh)) / (0.3 * fh))
                upper *= _smooth01(((x - shoulder[0]) * side + 0.9 * fw) / (0.5 * fw))
            lower = _falloff(_segment_distance(x, y, *elbow, *hand), 0.24 * fw, 0.44 * fw)
            arms.append(upper * (1.0 - lower))
            arms.append(lower)
        total_arms = sum(arms)
        if total_arms > 1.0:
            arms = [value / total_arms for value in arms]
            total_arms = 1.0
        remaining = 1.0 - total_arms
        head_weight = head * remaining
        return (remaining - head_weight, head_weight, arms[0], arms[1], arms[2], arms[3])

    def jaw_offset(self, pose: RigPose, x: float, y: float) -> tuple[float, float]:
        if pose.jaw_open <= 0.0 and abs(pose.mouth_width - 1.0) < 1e-4:
            return 0.0, 0.0
        cx = self.mouth_center[0]
        horizontal = _falloff(abs(x - cx), 0.55 * self.mouth_half_w, 1.6 * self.mouth_half_w)
        if horizontal <= 0.0:
            return 0.0, 0.0
        fh = self.fh
        vertical = 0.0
        if y > self.lip_y:
            rise = _smooth01((y - self.lip_y) / (0.10 * fh))
            fall = 1.0 - _smooth01((y - (self.chin_y + 0.05 * fh)) / (0.55 * fh))
            vertical = rise * fall
        dy = pose.jaw_open * self.jaw_drop_px * horizontal * vertical
        lip_band = _falloff(abs(y - self.lip_y), 0.04 * fh, 0.18 * fh)
        dx = (pose.mouth_width - 1.0) * (x - cx) * horizontal * lip_band
        return dx, dy

    def bone_matrices(self, pose: RigPose) -> dict[str, Affine]:
        fh = self.fh
        body_local = _affine_mul(_affine_rotate(pose.lean_deg), _affine_scale(1.0 + pose.breath))
        body = _affine_mul(_affine_translate(pose.body_dx * fh, pose.body_dy * fh), _affine_about(self.pelvis, body_local))
        head = _affine_mul(body, _affine_about(self.neck, _affine_mul(_affine_translate(0.0, pose.head_nod * fh),
                                                                        _affine_rotate(pose.head_tilt_deg))))
        upper_l = _affine_mul(body, _affine_about(self.shoulders[0], _affine_rotate(pose.upper_l_deg)))
        lower_l = _affine_mul(upper_l, _affine_about(self.elbows[0], _affine_rotate(pose.lower_l_deg)))
        upper_r = _affine_mul(body, _affine_about(self.shoulders[1], _affine_rotate(pose.upper_r_deg)))
        lower_r = _affine_mul(upper_r, _affine_about(self.elbows[1], _affine_rotate(pose.lower_r_deg)))
        return {"body": body, "head": head, "upper_l": upper_l, "lower_l": lower_l, "upper_r": upper_r,
                "lower_r": lower_r}

    def forward(self, pose: RigPose, x: float, y: float, matrices: Mapping[str, Affine] | None = None) -> tuple[float, float]:
        """Vị trí trên khung hình của điểm nghỉ (x, y) ở tư thế ``pose`` (LBS thuận)."""
        matrices = matrices or self.bone_matrices(pose)
        jx, jy = self.jaw_offset(pose, x, y)
        px, py = x + jx, y + jy
        weights = self.influences(x, y)
        out_x = out_y = 0.0
        for weight, name in zip(weights, _BONES):
            if weight:
                tx, ty = _affine_apply(matrices[name], px, py)
                out_x += weight * tx
                out_y += weight * ty
        return out_x, out_y

    def _undo_jaw(self, pose: RigPose, x: float, y: float) -> tuple[float, float]:
        """Giải p + Δ_hàm(p) = (x, y) theo trục dọc bằng chia đôi (ánh xạ y ↦ y + Δ(y) đơn điệu tăng)."""
        if pose.jaw_open <= 0.0 and abs(pose.mouth_width - 1.0) < 1e-4:
            return x, y
        low, high = y - self.jaw_drop_px * max(1.0, pose.jaw_open) - 2.0, y + 2.0
        for _ in range(26):
            middle = (low + high) / 2.0
            if middle + self.jaw_offset(pose, x, middle)[1] < y:
                low = middle
            else:
                high = middle
        py = (low + high) / 2.0
        return x - self.jaw_offset(pose, x, py)[0], py

    def _support_boxes(self, pose: RigPose, matrices: Mapping[str, Affine]) -> list[tuple[str, tuple[float, float, float, float]]]:
        """Hộp bao (trên khung hình ĐÍCH) của vùng chịu ảnh hưởng của từng xương đang chuyển động."""
        fw, fh = self.fw, self.fh
        hx, hy = self.head_center
        supports: dict[str, tuple[float, float, float, float]] = {}
        if pose.head_tilt_deg or pose.head_nod:
            supports["head"] = (hx - 1.4 * self.head_rx, hy - 1.4 * self.head_ry, hx + 1.4 * self.head_rx,
                                self.neck_y + 0.3 * fh)
        arms = (("upper_l", self.shoulders[0], self.elbows[0], 0.6 * fw, pose.upper_l_deg or pose.lower_l_deg),
                ("lower_l", self.elbows[0], self.hands[0], 0.52 * fw, pose.upper_l_deg or pose.lower_l_deg),
                ("upper_r", self.shoulders[1], self.elbows[1], 0.6 * fw, pose.upper_r_deg or pose.lower_r_deg),
                ("lower_r", self.elbows[1], self.hands[1], 0.52 * fw, pose.upper_r_deg or pose.lower_r_deg))
        for name, start, end, radius, moving in arms:
            if moving:
                supports[name] = (min(start[0], end[0]) - radius, min(start[1], end[1]) - radius,
                                  max(start[0], end[0]) + radius, max(start[1], end[1]) + radius)
        if pose.jaw_open > 0.0 or abs(pose.mouth_width - 1.0) >= 1e-4:
            cx = self.mouth_center[0]
            supports["jaw"] = (cx - 1.7 * self.mouth_half_w, self.lip_y - 0.2 * fh, cx + 1.7 * self.mouth_half_w,
                               self.chin_y + 0.7 * fh + self.jaw_drop_px)
        boxes = []
        for name, (x0, y0, x1, y1) in supports.items():
            matrix = matrices["head" if name == "jaw" else name]
            corners = [_affine_apply(matrix, cx, cy) for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
            xs = [c[0] for c in corners] + [x0, x1]
            ys = [c[1] for c in corners] + [y0, y1]
            boxes.append((name, (min(xs) - 2.0, min(ys) - 2.0, max(xs) + 2.0, max(ys) + 2.0 + self.jaw_drop_px)))
        return boxes

    def _alpha_at(self, x: float, y: float) -> int:
        width, height = self.image.size
        if 0 <= x < width and 0 <= y < height:
            return int(self._alpha.getpixel((int(x), int(y))))
        return 0

    def inverse_point(self, pose: RigPose, x: float, y: float, inverses: Mapping[str, Affine],
                      matrices: Mapping[str, Affine] | None = None,
                      boxes: Sequence[tuple[str, tuple[float, float, float, float]]] | None = None,
                      tolerance: float = 1.5) -> tuple[float, float, bool]:
        """Điểm nguồn (ảnh gốc) cho điểm đích (x, y) + cờ "có tiền ảnh chính xác".

        Mỗi xương b cho ứng viên p_b = M_b⁻¹·q (có gỡ dịch chuyển hàm), tinh chỉnh bằng Newton rồi kiểm chứng bằng
        phép thuận ‖forward(p_b) − q‖ ≤ ``tolerance``. Nhiều ứng viên đúng (tay giơ lên che nền) → chọn điểm ảnh
        không trong suốt (bộ phận nằm trên). Không ứng viên nào đúng → đây là vùng bộ phận vừa rời đi (cờ False).
        """
        matrices = matrices or self.bone_matrices(pose)
        boxes = self._support_boxes(pose, matrices) if boxes is None else boxes
        active = {name for name, (x0, y0, x1, y1) in boxes if x0 <= x <= x1 and y0 <= y <= y1}
        body_point = _affine_apply(inverses["body"], x, y)
        if not active:
            return body_point[0], body_point[1], True
        candidates = ["body"] + [name for name in _BONES[1:] if name in active or ("jaw" in active and name == "head")]
        best_point, best_key, best_exact = body_point, (1, 1e18), False
        for name in candidates:
            linear = inverses[name]
            ux, uy = _affine_apply(linear, x, y)
            px, py = self._undo_jaw(pose, ux, uy) if "jaw" in active else (ux, uy)
            error = 1e18
            for _ in range(self.iterations):
                fx, fy = self.forward(pose, px, py, matrices)
                ex, ey = fx - x, fy - y
                error = math.hypot(ex, ey)
                if error < 0.25:
                    break
                px -= linear[0] * ex + linear[1] * ey
                py -= linear[3] * ex + linear[4] * ey
            exact = error <= tolerance
            key = (0 if exact else 1, -self._alpha_at(px, py) if exact else error)
            if key < best_key:
                best_point, best_key, best_exact = (px, py), key, exact
        return best_point[0], best_point[1], best_exact

    def inverse(self, pose: RigPose, x: float, y: float, inverses: Mapping[str, Affine],
                matrices: Mapping[str, Affine] | None = None,
                boxes: Sequence[tuple[str, tuple[float, float, float, float]]] | None = None) -> tuple[float, float]:
        px, py, _ = self.inverse_point(pose, x, y, inverses, matrices, boxes)
        return px, py

    def body_plate(self) -> Image.Image:
        """Lớp thân "phía sau tay": vùng tay được lấp bằng tích chập chuẩn hoá (normalized convolution).

        Điểm ảnh ngoài vùng tay — cả thân (đục) lẫn nền (trong suốt) — đều là dữ liệu "đã biết":
        màu lấp = Σ G·c·a·k / Σ G·a·k, độ đục lấp = Σ G·a·k / Σ G·k (G: nhân Gauss, k: ngoài vùng tay).
        Độ đục được chốt dứt khoát quanh 50% → phía sau tay là thân (kéo dài vạt áo) hoặc là nền.
        Chỉ hiện ra ở nơi cánh tay vừa rời đi, thay vì để lại lỗ hổng hay bóng ma của tay cũ.
        """
        with self._plate_lock:
            if self._plate is not None:
                return self._plate
            width, height = self.image.size
            step = 4
            coarse = Image.new("L", (math.ceil(width / step), math.ceil(height / step)), 0)
            pixels = coarse.load()
            for j in range(coarse.height):
                for i in range(coarse.width):
                    weights = self.influences((i + 0.5) * step, (j + 0.5) * step)
                    pixels[i, j] = int(255 * _clamp((weights[2] + weights[3] + weights[4] + weights[5]) * 1.6, 0.0, 1.0))
            arm_mask = coarse.resize((width, height), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(2))
            known = ImageOps.invert(arm_mask)
            red, green, blue, alpha = self.image.split()
            colour_weight = ImageChops.multiply(alpha, known)
            blur = ImageFilter.GaussianBlur(max(3.0, 0.45 * self.fw))
            premultiplied = [ImageChops.multiply(channel, colour_weight).filter(blur) for channel in (red, green, blue)]
            colour = Image.merge("RGBa", (*premultiplied, colour_weight.filter(blur))).convert("RGBA")
            alpha_known = ImageChops.multiply(alpha, known).filter(blur)
            alpha_fill = Image.merge("RGBa", (alpha_known, alpha_known, alpha_known, known.filter(blur))).convert("RGBA")
            decisive = alpha_fill.getchannel("R").point(
                lambda v: 0 if v < 100 else 255 if v > 155 else int((v - 100) * 255 / 55))
            colour.putalpha(decisive)
            self._plate = Image.composite(colour, self.image, arm_mask).convert("RGBa")
            return self._plate

    # ---------------------------------------------------------------- render
    def render(self, pose: RigPose) -> Image.Image:
        if pose.is_rest():
            return self.image.copy()
        matrices = self.bone_matrices(pose)
        inverses = {name: _affine_inverse(matrix) for name, matrix in matrices.items()}
        boxes = self._support_boxes(pose, matrices)
        resolved = [self.inverse_point(pose, x, y, inverses, matrices, boxes) for x, y in self._vertices]
        sources = [(px, py) for px, py, _ in resolved]
        mesh = [(box, (*sources[ul], *sources[ll], *sources[lr], *sources[ur])) for box, ul, ll, lr, ur in self._cells]
        warped = self._premultiplied.transform(self.image.size, Image.Transform.MESH, mesh,
                                               Image.Resampling.BILINEAR).convert("RGBA")
        if not all(exact for _, _, exact in resolved):
            vacated = Image.new("L", self._grid_shape, 0)
            vacated.putdata([0 if exact else 255 for _, _, exact in resolved])
            mask = vacated.resize(self.image.size, Image.Resampling.BILINEAR)
            warped.putalpha(ImageChops.multiply(warped.getchannel("A"), ImageOps.invert(mask)))
            plate = self.body_plate().transform(self.image.size, Image.Transform.AFFINE, inverses["body"],
                                                Image.Resampling.BILINEAR).convert("RGBA")
            plate.putalpha(ImageChops.multiply(plate.getchannel("A"), mask))  # chỉ hiện ở vùng bộ phận vừa rời đi
            plate.alpha_composite(warped)
            warped = plate
        if pose.jaw_open > 0.02:
            self._draw_mouth_cavity(warped, pose, matrices)
        return warped

    def mouth_box(self, pose: RigPose, matrices: Mapping[str, Affine] | None = None) -> tuple[int, int, int, int]:
        cx, cy = self.forward(pose, *self.mouth_center, matrices)
        half_w = self.mouth_half_w * 1.6
        top = cy - 0.25 * self.fh
        bottom = cy + 0.25 * self.fh + self.jaw_drop_px
        width, height = self.image.size
        return (int(_clamp(cx - half_w, 0, width - 1)), int(_clamp(top, 0, height - 1)),
                int(_clamp(cx + half_w, 1, width)), int(_clamp(bottom, 1, height)))

    def _draw_mouth_cavity(self, canvas: Image.Image, pose: RigPose, matrices: Mapping[str, Affine]) -> None:
        upper = self.forward(pose, *self.upper_lip, matrices)
        lower = self.forward(pose, *self.lower_lip, matrices)
        gap = (lower[1] - upper[1]) - self.rest_lip_gap
        if gap < 1.0:
            return
        cx = self.forward(pose, *self.mouth_center, matrices)[0]
        half_w = self.mouth_half_w * (0.55 + 0.35 * pose.jaw_open) * pose.mouth_width
        top = upper[1] + 0.25 * self.rest_lip_gap
        bottom = top + gap * 0.92
        x0, y0 = int(math.floor(cx - half_w)), int(math.floor(top))
        x1, y1 = int(math.ceil(cx + half_w)), int(math.ceil(bottom))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        size = (x1 - x0, y1 - y0)
        supersample = 4
        mask = Image.new("L", (size[0] * supersample, size[1] * supersample), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
        mask = mask.resize(size, Image.Resampling.LANCZOS)
        strength = min(1.0, pose.jaw_open * 1.6) * 0.92
        mask = mask.point(lambda v: int(v * strength))
        cavity = Image.new("RGBA", size, (40, 15, 17, 0))
        cavity.putalpha(mask)
        if pose.jaw_open > 0.45 and size[1] >= 6:
            teeth_h = max(2, int(size[1] * 0.18))
            teeth_mask = Image.new("L", size, 0)
            ImageDraw.Draw(teeth_mask).ellipse((size[0] * 0.12, -teeth_h, size[0] * 0.88, teeth_h), fill=int(110 * strength))
            teeth = Image.new("RGBA", size, (206, 196, 182, 0))
            teeth.putalpha(ImageChops.multiply(teeth_mask, mask))
            cavity.alpha_composite(teeth)
        canvas.alpha_composite(cavity, dest=(max(0, x0), max(0, y0)),
                               source=(max(0, -x0), max(0, -y0)))


# ======================================================================================
# 7. Quy hoạch động nội suy khung hình (DP Frame Interpolation)
# ======================================================================================
@dataclass(frozen=True)
class KeyPose:
    values: tuple[float, ...]
    hold_s: float = 0.0
    label: str = ""


@dataclass(frozen=True)
class ScheduleSegment:
    kind: str  # "hold" | "move"
    key_from: int
    key_to: int
    start_frame: int
    frames: int


@dataclass(frozen=True)
class FrameSchedule:
    fps: int
    total_frames: int
    loop: bool
    segments: tuple[ScheduleSegment, ...]
    cost: float


class DPFrameInterpolator:
    """DP phân bổ khung hình cho các đoạn (giữ tư thế / chuyển tư thế) rồi nội suy Hermite C¹.

    Bài toán: chia ``Q`` lượng tử khung hình (mỗi lượng tử ``quantum_frames`` khung) cho các đoạn s = 1..S:
        DP[s][b] = min_n DP[s−1][b−n] + cost_s(n)
        cost(chuyển i→j, n) = d(P_i, P_j)² / T(n)          (năng lượng chuyển động — tốc độ đều, không giật)
        cost(giữ i, n)       = λ·(T(n) − hold_i)²            (giữ tư thế đúng nhịp mong muốn)
    với ràng buộc T_min ≤ T(chuyển) ≤ T_max. O(S·Q·R) thời gian. Sau đó nội suy Hermite bậc 3 (tiếp tuyến
    Catmull–Rom, bằng 0 tại tư thế có giữ) → chuyển động liên tục C¹; với vòng lặp, khung cuối nối liền khung đầu.
    """

    def __init__(
        self,
        fps: int = TARGET_FPS,
        *,
        quantum_frames: int = 3,
        min_move_s: float = 0.2,
        max_move_s: float = 10.0,
        max_hold_s: float = 6.0,
        hold_weight: float = 3.0,
        weights: Sequence[float] | None = None,
    ) -> None:
        if fps <= 0 or quantum_frames < 1:
            raise ValueError("fps và quantum_frames phải dương.")
        self.fps = fps
        self.quantum = quantum_frames
        self.min_move_s = min_move_s
        self.max_move_s = max_move_s
        self.max_hold_s = max_hold_s
        self.hold_weight = hold_weight
        self.weights = tuple(weights) if weights is not None else None

    def distance(self, first: Sequence[float], second: Sequence[float]) -> float:
        weights = self.weights or (1.0,) * len(first)
        return math.sqrt(sum((w * (a - b)) ** 2 for w, a, b in zip(weights, first, second)))

    @staticmethod
    def _segments(count: int, loop: bool) -> list[tuple[str, int, int]]:
        segments: list[tuple[str, int, int]] = []
        for index in range(count):
            segments.append(("hold", index, index))
            if loop or index < count - 1:
                segments.append(("move", index, (index + 1) % count))
        return segments

    def segment_cost(self, kind: str, key_from: KeyPose, key_to: KeyPose, quanta: int) -> float:
        seconds = quanta * self.quantum / self.fps
        if kind == "hold":
            return self.hold_weight * (seconds - key_from.hold_s) ** 2
        if quanta <= 0:
            return math.inf
        return self.distance(key_from.values, key_to.values) ** 2 / seconds

    def schedule(self, keys: Sequence[KeyPose], total_frames: int, *, loop: bool = True) -> FrameSchedule:
        if not keys:
            raise ValueError("Cần ít nhất một tư thế khoá.")
        if total_frames < 1:
            raise ValueError("total_frames phải >= 1.")
        if len(keys) == 1:
            return FrameSchedule(self.fps, total_frames, loop, (ScheduleSegment("hold", 0, 0, 0, total_frames),), 0.0)
        quantum = self.quantum
        budget = total_frames // quantum
        remainder = total_frames - budget * quantum
        segments = self._segments(len(keys), loop)
        min_move = max(1, round(self.min_move_s * self.fps / quantum))
        max_move = max(min_move, round(self.max_move_s * self.fps / quantum))
        max_hold = max(0, round(self.max_hold_s * self.fps / quantum))
        moves = sum(1 for kind, _, _ in segments if kind == "move")
        if moves * min_move > budget:
            min_move = 1
            if moves > budget:
                raise ValueError("Quá ít khung hình cho số tư thế khoá.")
        ranges = [(min_move, max_move) if kind == "move" else (0, max_hold) for kind, _, _ in segments]
        inf = math.inf
        table = [[inf] * (budget + 1) for _ in range(len(segments) + 1)]
        choice = [[-1] * (budget + 1) for _ in range(len(segments) + 1)]
        table[0][0] = 0.0
        for s, (kind, i, j) in enumerate(segments):
            low, high = ranges[s]
            costs = {n: self.segment_cost(kind, keys[i], keys[j], n) for n in range(low, min(high, budget) + 1)}
            current, following = table[s], table[s + 1]
            for used in range(budget + 1):
                base = current[used]
                if base == inf:
                    continue
                for n, cost in costs.items():
                    total_used = used + n
                    if total_used > budget:
                        break
                    candidate = base + cost
                    if candidate < following[total_used]:
                        following[total_used] = candidate
                        choice[s + 1][total_used] = n
        if table[len(segments)][budget] == inf:
            raise ValueError("Không có cách phân bổ khung hình hợp lệ.")
        counts = [0] * len(segments)
        used = budget
        for s in range(len(segments), 0, -1):
            n = choice[s][used]
            counts[s - 1] = n
            used -= n
        frames = [n * quantum for n in counts]
        last_move = max(index for index, (kind, _, _) in enumerate(segments) if kind == "move")
        frames[last_move] += remainder
        result: list[ScheduleSegment] = []
        start = 0
        for (kind, i, j), count in zip(segments, frames):
            result.append(ScheduleSegment(kind, i, j, start, count))
            start += count
        return FrameSchedule(self.fps, total_frames, loop, tuple(result), table[len(segments)][budget])

    def interpolate(self, keys: Sequence[KeyPose], schedule: FrameSchedule) -> list[tuple[float, ...]]:
        if len(keys) == 1:
            return [tuple(keys[0].values)] * schedule.total_frames
        count = len(keys)
        holds = {segment.key_from: segment.frames for segment in schedule.segments if segment.kind == "hold"}
        arrival: dict[int, int] = {}
        for segment in schedule.segments:
            if segment.kind == "hold":
                arrival[segment.key_from] = segment.start_frame
        dims = len(keys[0].values)

        def tangent(index: int) -> tuple[float, ...]:
            if holds.get(index, 0) > 0:
                return (0.0,) * dims
            if not schedule.loop and (index == 0 or index == count - 1):
                return (0.0,) * dims
            previous, following = (index - 1) % count, (index + 1) % count
            t_prev, t_next = arrival[previous], arrival[following]
            t_here = arrival[index]
            if t_prev > t_here:
                t_prev -= schedule.total_frames
            if t_next < t_here:
                t_next += schedule.total_frames
            span = max(1, t_next - t_prev)
            return tuple((b - a) / span for a, b in zip(keys[previous].values, keys[following].values))

        tangents = [tangent(index) for index in range(count)]
        frames: list[tuple[float, ...]] = []
        for segment in schedule.segments:
            start_pose = keys[segment.key_from].values
            if segment.kind == "hold":
                frames.extend([tuple(start_pose)] * segment.frames)
                continue
            end_pose = keys[segment.key_to].values
            n = segment.frames
            m0, m1 = tangents[segment.key_from], tangents[segment.key_to]
            for f in range(n):
                s = f / n
                h00 = 2 * s ** 3 - 3 * s ** 2 + 1
                h10 = s ** 3 - 2 * s ** 2 + s
                h01 = -2 * s ** 3 + 3 * s ** 2
                h11 = s ** 3 - s ** 2
                frames.append(tuple(h00 * a + h10 * n * ta + h01 * b + h11 * n * tb
                                    for a, b, ta, tb in zip(start_pose, end_pose, m0, m1)))
        if not schedule.loop and frames:
            frames[-1] = tuple(keys[-1].values)
        return frames[: schedule.total_frames]

    def run(self, keys: Sequence[KeyPose], duration_s: float, *, loop: bool = True) -> list[tuple[float, ...]]:
        total = max(1, round(duration_s * self.fps))
        return self.interpolate(keys, self.schedule(keys, total, loop=loop))


# ======================================================================================
# 8. Đồng bộ khẩu hình bằng quy hoạch động (TextToVisemeDPSync)
# ======================================================================================
VISEMES: tuple[str, ...] = ("REST", "MBP", "FV", "U", "O", "E", "A")
VISEME_SHAPES: dict[str, tuple[float, float]] = {  # (độ mở hàm 0..1, độ rộng môi)
    "REST": (0.0, 1.0),
    "MBP": (0.0, 0.94),
    "FV": (0.16, 1.02),
    "U": (0.32, 0.74),
    "O": (0.58, 0.82),
    "E": (0.42, 1.10),
    "A": (0.86, 1.0),
}
_TONE_MARKS = frozenset("̣̀́̃̉")
_ONSETS = ("ngh", "ng", "nh", "ch", "tr", "th", "ph", "kh", "gh", "gi", "qu",
           "b", "c", "d", "đ", "g", "h", "k", "l", "m", "n", "p", "r", "s", "t", "v", "x")
_CODAS = ("ng", "nh", "ch", "c", "m", "n", "p", "t")
_VOWEL_VISEME = {"a": "A", "ă": "A", "â": "A", "e": "E", "ê": "E", "i": "E", "y": "E",
                 "o": "O", "ô": "O", "ơ": "E", "u": "U", "ư": "E"}
_ONSET_VISEME = {"b": "MBP", "m": "MBP", "p": "MBP", "ph": "FV", "v": "FV", "qu": "U"}
_CODA_VISEME = {"m": "MBP", "p": "MBP", "t": "REST", "c": "REST", "ch": "REST"}
_DIGIT_WORDS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_SCALE_WORDS = ("", "nghìn", "triệu", "tỷ")


def strip_tone_marks(text: str) -> str:
    """Bỏ dấu thanh (huyền, sắc, hỏi, ngã, nặng) nhưng giữ dấu nguyên âm (â, ă, ê, ô, ơ, ư)."""
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(ch for ch in decomposed if ch not in _TONE_MARKS))


def _group_words(group: int, full: bool) -> list[str]:
    hundreds, tens, units = group // 100, (group // 10) % 10, group % 10
    words: list[str] = []
    if hundreds or full:
        words += [_DIGIT_WORDS[hundreds], "trăm"]
    if tens == 0:
        if units:
            words += ["linh", _DIGIT_WORDS[units]] if (hundreds or full) else [_DIGIT_WORDS[units]]
    elif tens == 1:
        words.append("mười")
        if units:
            words.append("lăm" if units == 5 else _DIGIT_WORDS[units])
    else:
        words += [_DIGIT_WORDS[tens], "mươi"]
        if units:
            words.append({1: "mốt", 4: "tư", 5: "lăm"}.get(units, _DIGIT_WORDS[units]))
    return words


def vietnamese_number_words(token: str) -> list[str]:
    """Đọc số thành chữ (1288 → một nghìn hai trăm tám mươi tám) — cùng số âm tiết với Phase 1."""
    digits = re.sub(r"\D", "", token)
    if not digits:
        return []
    if len(digits) > 12:
        return [_DIGIT_WORDS[int(d)] for d in digits]
    value = int(digits)
    if value == 0:
        return ["không"]
    groups: list[int] = []
    while value:
        groups.append(value % 1000)
        value //= 1000
    words: list[str] = []
    for position in range(len(groups) - 1, -1, -1):
        group = groups[position]
        if group == 0:
            continue
        words += _group_words(group, full=position != len(groups) - 1)
        if position > 0:
            words.append(_SCALE_WORDS[position])
    return words


def text_syllables(text: str) -> list[str]:
    syllables: list[str] = []
    for token in _TOKEN_RE.findall(unicodedata.normalize("NFC", text or "")):
        if token[0].isdigit():
            syllables.extend(vietnamese_number_words(token))
        else:
            syllables.append(token)
    return syllables


def syllable_visemes(syllable: str) -> list[tuple[str, float]]:
    """Một âm tiết tiếng Việt → chuỗi (viseme, tỉ lệ thời lượng): phụ âm đầu · vần · phụ âm cuối."""
    text = "".join(ch for ch in strip_tone_marks(syllable.lower()) if ch.isalpha())
    if not text:
        return []
    onset = ""
    for candidate in _ONSETS:
        if text.startswith(candidate):
            rest = text[len(candidate) :]
            if candidate in ("gi", "qu") and not any(ch in _VOWEL_VISEME for ch in rest):
                continue
            onset = candidate
            break
    rest = text[len(onset) :]
    coda = ""
    for candidate in _CODAS:
        if rest.endswith(candidate) and any(ch in _VOWEL_VISEME for ch in rest[: -len(candidate)]):
            coda = candidate
            break
    nucleus = rest[: len(rest) - len(coda)] if coda else rest
    vowels: list[str] = []
    for ch in nucleus:
        viseme = _VOWEL_VISEME.get(ch)
        if viseme and (not vowels or vowels[-1] != viseme):
            vowels.append(viseme)
    if not vowels:
        vowels = ["E"]
    onset_viseme, coda_viseme = _ONSET_VISEME.get(onset), _CODA_VISEME.get(coda)
    onset_share = 0.22 if onset_viseme else 0.0
    coda_share = 0.18 if coda_viseme else 0.0
    main = max(range(len(vowels)), key=lambda index: VISEME_SHAPES[vowels[index]][0])
    raw = [2.0 if index == main else 1.0 for index in range(len(vowels))]
    scale = (1.0 - onset_share - coda_share) / sum(raw)
    units: list[tuple[str, float]] = []
    if onset_viseme:
        units.append((onset_viseme, onset_share))
    units += [(viseme, share * scale) for viseme, share in zip(vowels, raw)]
    if coda_viseme:
        units.append((coda_viseme, coda_share))
    return units


@dataclass(frozen=True)
class VisemeFrame:
    index: int
    t_ms: float
    viseme: str
    openness: float
    width: float


@dataclass(frozen=True)
class VisemeSegment:
    viseme: str
    start_ms: float
    end_ms: float


@dataclass(frozen=True)
class MouthTrack:
    fps: int
    frames: tuple[VisemeFrame, ...]
    segments: tuple[VisemeSegment, ...]
    syllable_onsets_ms: tuple[float, ...]
    speech_end_ms: float
    duration_ms: float
    dp_cost: float
    target_switches: int
    dp_switches: int

    def openness(self) -> tuple[float, ...]:
        return tuple(frame.openness for frame in self.frames)

    def to_json(self) -> dict[str, Any]:
        return {
            "fps": self.fps,
            "speech_end_ms": round(self.speech_end_ms, 1),
            "duration_ms": round(self.duration_ms, 1),
            "segments": [{"viseme": s.viseme, "start_ms": round(s.start_ms, 1), "end_ms": round(s.end_ms, 1)}
                         for s in self.segments],
            "openness": [round(frame.openness, 3) for frame in self.frames],
            "width": [round(frame.width, 3) for frame in self.frames],
        }


@dataclass(frozen=True)
class _SpeechPiece:
    text: str
    speak_ms: float
    pause_ms: float


class TextToVisemeDPSync:
    """Đồng bộ khẩu hình với nhịp chữ bằng Viterbi DP trên từng khung hình (mặc định 60 FPS).

    1. Lời thoại → cụm chữ (``PhraseChunker`` của AI Nhân vật) → âm tiết (số đọc thành chữ) → chuỗi viseme mục tiêu
       theo đúng đồng hồ nói ``speak_ms``/``pause_ms`` (cùng hằng số Phase 1 nên khớp phụ đề đang stream).
    2. Viterbi trên trạng thái (viseme, số khung đã giữ): chi phí phát xạ = độ lệch hình miệng so với mục tiêu,
       chi phí chuyển = độ đổi hình miệng; mỗi viseme phải giữ ≥ ``min_hold_frames`` khung → hết nháy miệng.
       Trạng thái cuối bắt buộc là REST → hết lời là ngậm miệng.
    3. Làm mượt độ mở hàm (tấn công/nhả) rồi chốt đúng 0 sau khi nói xong.
    """

    def __init__(
        self,
        fps: int = TARGET_FPS,
        *,
        ms_per_syllable: int = DEFAULT_MS_PER_SYLLABLE,
        min_hold_frames: int = 3,
        switch_weight: float = 0.6,
        tail_ms: float = 500.0,
        attack_ms: float = 30.0,
        release_ms: float = 45.0,
        max_words_per_chunk: int = 6,
    ) -> None:
        if fps <= 0 or min_hold_frames < 1:
            raise ValueError("fps và min_hold_frames phải dương.")
        self.fps = fps
        self.ms_per_syllable = ms_per_syllable
        self.min_hold_frames = min_hold_frames
        self.switch_weight = switch_weight
        self.tail_ms = tail_ms
        self.attack = 1.0 - math.exp(-(1000.0 / fps) / attack_ms)
        self.release = 1.0 - math.exp(-(1000.0 / fps) / release_ms)
        self.max_words_per_chunk = max_words_per_chunk
        self._index = {name: position for position, name in enumerate(VISEMES)}
        self._emission = [[self._emission_cost(target, candidate) for candidate in VISEMES] for target in VISEMES]
        self._switch = [[0.0 if a == b else self.switch_weight * (0.5 + abs(VISEME_SHAPES[a][0] - VISEME_SHAPES[b][0]))
                         for b in VISEMES] for a in VISEMES]

    @staticmethod
    def _emission_cost(target: str, candidate: str) -> float:
        if target == candidate:
            return 0.0
        (to, tw), (co, cw) = VISEME_SHAPES[target], VISEME_SHAPES[candidate]
        cost = 0.25 + 1.2 * abs(to - co) + 0.8 * abs(tw - cw)
        if target == "REST" and co > 0.05:
            cost += 0.5  # không mở miệng khi đang nghỉ
        if target != "REST" and candidate == "REST":
            cost += 0.3  # đang nói thì không nên ngậm hẳn
        return cost

    def speech_pieces(self, speech: str | Sequence[SpeechChunk] | Sequence[tuple[str, float, float]]) -> list[_SpeechPiece]:
        if isinstance(speech, str):
            chunker = PhraseChunker(self.max_words_per_chunk)
            phrases = chunker.feed(speech) + chunker.flush()
            pieces = []
            for phrase in phrases:
                speak = count_syllables(phrase.text) * self.ms_per_syllable
                pause = PAUSE_MS.get(phrase.boundary, 0) if phrase.text.strip() else 0
                pieces.append(_SpeechPiece(phrase.text, float(speak), float(pause)))
            return pieces
        pieces = []
        for item in speech:
            if isinstance(item, SpeechChunk):
                pieces.append(_SpeechPiece(item.text, float(item.speak_ms), float(item.pause_ms)))
            else:
                text, speak, pause = item
                pieces.append(_SpeechPiece(str(text), float(speak), float(pause)))
        return pieces

    def timeline(self, pieces: Sequence[_SpeechPiece]) -> tuple[list[tuple[float, float, str]], list[float], float]:
        intervals: list[tuple[float, float, str]] = []
        onsets: list[float] = []
        clock = 0.0
        for piece in pieces:
            syllables = text_syllables(piece.text)
            if syllables and piece.speak_ms > 0:
                per_syllable = piece.speak_ms / len(syllables)
                for syllable in syllables:
                    onsets.append(clock)
                    units = syllable_visemes(syllable) or [("E", 1.0)]
                    cursor = clock
                    for viseme, share in units:
                        end = cursor + share * per_syllable
                        intervals.append((cursor, end, viseme))
                        cursor = end
                    clock += per_syllable
            clock += piece.pause_ms
        speech_end = intervals[-1][1] if intervals else 0.0
        return intervals, onsets, speech_end

    def targets(self, intervals: Sequence[tuple[float, float, str]], total_frames: int) -> list[str]:
        result = ["REST"] * total_frames
        frame_ms = 1000.0 / self.fps
        cursor = 0
        for frame in range(total_frames):
            t = (frame + 0.5) * frame_ms
            while cursor < len(intervals) and intervals[cursor][1] <= t:
                cursor += 1
            if cursor < len(intervals) and intervals[cursor][0] <= t:
                result[frame] = intervals[cursor][2]
        return result

    def viterbi(self, targets: Sequence[str]) -> tuple[list[str], float]:
        visemes = len(VISEMES)
        hold = self.min_hold_frames
        width = hold + 1
        inf = math.inf
        rest = self._index["REST"]
        emission_rows = [self._emission[self._index[target]] for target in targets]
        current = [[inf] * width for _ in range(visemes)]
        current[rest][hold] = emission_rows[0][rest]
        backpointers: list[list[int]] = []
        for t in range(1, len(targets)):
            emission = emission_rows[t]
            following = [[inf] * width for _ in range(visemes)]
            pointer = [-1] * (visemes * width)
            ready = [current[u][hold] for u in range(visemes)]
            for v in range(visemes):
                cost_here = emission[v]
                base = v * width
                for k in range(1, width):
                    previous = current[v][k]
                    if previous == inf:
                        continue
                    next_k = k + 1 if k < hold else hold
                    candidate = previous + cost_here
                    if candidate < following[v][next_k]:
                        following[v][next_k] = candidate
                        pointer[base + next_k] = base + k
                best, best_source = inf, -1
                for u in range(visemes):
                    if u != v and ready[u] < inf:
                        candidate = ready[u] + self._switch[u][v]
                        if candidate < best:
                            best, best_source = candidate, u * width + hold
                if best < inf and best + cost_here < following[v][1]:
                    following[v][1] = best + cost_here
                    pointer[base + 1] = best_source
            backpointers.append(pointer)
            current = following
        end_k = min(range(1, width), key=lambda k: current[rest][k])
        state, cost = rest * width + end_k, current[rest][end_k]
        if cost == inf:
            flat = [(current[v][k], v * width + k) for v in range(visemes) for k in range(1, width)]
            cost, state = min(flat)
        path = [0] * len(targets)
        for t in range(len(targets) - 1, 0, -1):
            path[t] = state // width
            state = backpointers[t - 1][state]
        path[0] = state // width
        return [VISEMES[index] for index in path], cost

    def sync(self, speech: str | Sequence[SpeechChunk] | Sequence[tuple[str, float, float]], *,
             start_ms: float = 0.0) -> MouthTrack:
        pieces = self.speech_pieces(speech)
        intervals, onsets, speech_end = self.timeline(pieces)
        content_end = sum(piece.speak_ms + piece.pause_ms for piece in pieces)
        duration = max(speech_end, content_end) + self.tail_ms
        total_frames = max(2, math.ceil(duration * self.fps / 1000.0))
        targets = self.targets(intervals, total_frames)
        path, cost = self.viterbi(targets)
        frame_ms = 1000.0 / self.fps
        frames: list[VisemeFrame] = []
        openness, width = 0.0, 1.0
        for index, viseme in enumerate(path):
            t = index * frame_ms
            target_open, target_width = VISEME_SHAPES[viseme]
            rate = self.attack if target_open > openness else self.release
            openness += (target_open - openness) * rate
            width += (target_width - width) * rate
            if viseme == "REST" and t > speech_end + 150.0:
                openness, width = 0.0, 1.0
            frames.append(VisemeFrame(index, start_ms + t, viseme, openness, width))
        last = frames[-1]
        frames[-1] = VisemeFrame(last.index, last.t_ms, "REST", 0.0, 1.0)
        segments: list[VisemeSegment] = []
        for frame in frames:
            if segments and segments[-1].viseme == frame.viseme:
                segments[-1] = VisemeSegment(frame.viseme, segments[-1].start_ms, frame.t_ms + frame_ms)
            else:
                segments.append(VisemeSegment(frame.viseme, frame.t_ms, frame.t_ms + frame_ms))
        target_switches = sum(1 for a, b in zip(targets, targets[1:]) if a != b)
        dp_switches = sum(1 for a, b in zip(path, path[1:]) if a != b)
        return MouthTrack(self.fps, tuple(frames), tuple(segments), tuple(start_ms + t for t in onsets),
                          start_ms + speech_end, total_frames * frame_ms, cost, target_switches, dp_switches)


# ======================================================================================
# 9. Bố cục cảnh & cảnh nền động 60 FPS
# ======================================================================================
REGION_LABELS: tuple[str, ...] = ("sky", "water", "fire", "smoke", "vegetation")


@dataclass(frozen=True)
class SceneLayout:
    width: int
    height: int
    horizon_y: float
    focus: tuple[float, float]
    hero_anchor: tuple[float, float]
    regions: Mapping[str, tuple[tuple[float, float, float, float], ...]]
    source: str

    def to_json(self) -> dict[str, Any]:
        return {
            "width": self.width, "height": self.height, "horizon_y": round(self.horizon_y, 1),
            "focus": [round(v, 1) for v in self.focus], "hero_anchor": [round(v, 1) for v in self.hero_anchor],
            "regions": {label: [[round(v, 1) for v in box] for box in boxes] for label, boxes in self.regions.items()},
            "source": self.source,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "SceneLayout":
        return cls(
            width=int(data["width"]), height=int(data["height"]), horizon_y=float(data["horizon_y"]),
            focus=(float(data["focus"][0]), float(data["focus"][1])),
            hero_anchor=(float(data["hero_anchor"][0]), float(data["hero_anchor"][1])),
            regions={label: tuple(tuple(float(v) for v in box) for box in boxes)  # type: ignore[misc]
                     for label, boxes in (data.get("regions") or {}).items()},
            source=str(data.get("source", "gemini")),
        )


def heuristic_layout(width: int, height: int, scene_mode: str) -> SceneLayout:
    horizon = 0.42 * height
    regions: dict[str, tuple[tuple[float, float, float, float], ...]] = {"sky": ((0.0, 0.0, float(width), horizon),)}
    anchor_x = 0.5 * width if scene_mode == SCENE_FOREGROUND_TALKING else 0.27 * width
    return SceneLayout(width, height, horizon, (0.6 * width, 0.5 * height), (anchor_x, 0.92 * height), regions,
                       "heuristic")


def layout_from_analysis(analysis: Mapping[str, Any], width: int, height: int, scene_mode: str) -> SceneLayout:
    fallback = heuristic_layout(width, height, scene_mode)

    def point(value: Any, default: tuple[float, float]) -> tuple[float, float]:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                return (_clamp(float(value[1]) / 1000 * width, 0, width), _clamp(float(value[0]) / 1000 * height, 0, height))
            except (TypeError, ValueError):
                return default
        return default

    try:
        horizon = _clamp(float(analysis.get("horizon_y", 420)) / 1000 * height, 0.1 * height, 0.85 * height)
    except (TypeError, ValueError):
        horizon = fallback.horizon_y
    regions: dict[str, list[tuple[float, float, float, float]]] = {}
    for item in analysis.get("regions") or []:
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label", "")).lower()
        box = _norm_box(item.get("box_2d"), width, height)
        if label in REGION_LABELS and box is not None:
            regions.setdefault(label, []).append(box)
    focus = point(analysis.get("focus_point"), fallback.focus)
    anchor = point(analysis.get("hero_anchor"), fallback.hero_anchor)
    if scene_mode == SCENE_FOREGROUND_TALKING:
        anchor = (0.5 * width, 0.92 * height)
    return SceneLayout(width, height, horizon, focus, anchor, {k: tuple(v) for k, v in regions.items()}, "gemini")


CAMERA_WEIGHTS = (1.0, 1.0, 20.0)


class BackgroundAnimator:
    """Ảnh nền tĩnh → cảnh động 2.5D, vòng lặp liền mạch ở 60 FPS.

    * Parallax theo độ sâu (xa ít, gần nhiều) + zoom chậm; quỹ đạo camera do ``DPFrameInterpolator`` lập lịch.
    * Hiệu ứng theo vùng do AI Đạo diễn khoanh: mặt nước gợn sóng, cây/lúa đung đưa, khói trôi, lửa bập bùng + tàn lửa.
    * Mọi dao động có số chu kỳ nguyên trong một vòng lặp → khung cuối nối liền khung đầu.
    """

    def __init__(
        self,
        background: Image.Image,
        layout: SceneLayout,
        *,
        scene_mode: str,
        out_size: tuple[int, int] = DEFAULT_OUTPUT_SIZE,
        fps: int = TARGET_FPS,
        loop_s: float = 4.0,
        seed: int = 0,
        grid_cell: int = 30,
        overscan: float = 0.07,
        interpolator: DPFrameInterpolator | None = None,
    ) -> None:
        self.out_w, self.out_h = out_size
        self.fps = fps
        self.frame_count = max(2, round(loop_s * fps))
        self.scene_mode = scene_mode
        self.canvas_w = round(self.out_w * (1 + 2 * overscan))
        self.canvas_h = round(self.out_h * (1 + 2 * overscan))
        self.canvas = fit_cover(background.convert("RGB"), (self.canvas_w, self.canvas_h))
        scale = max(self.canvas_w / layout.width, self.canvas_h / layout.height)
        offset_x = (layout.width * scale - self.canvas_w) / 2
        offset_y = (layout.height * scale - self.canvas_h) / 2
        view_x = (self.canvas_w - self.out_w) / 2
        view_y = (self.canvas_h - self.out_h) / 2

        def to_canvas(x: float, y: float) -> tuple[float, float]:
            return (x * scale - offset_x, y * scale - offset_y)

        def canvas_box(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
            (x0, y0), (x1, y1) = to_canvas(box[0], box[1]), to_canvas(box[2], box[3])
            return (x0, y0, x1, y1)

        self.regions = {label: [canvas_box(box) for box in boxes] for label, boxes in layout.regions.items()}
        self.horizon = to_canvas(0, layout.horizon_y)[1]
        anchor_canvas = to_canvas(*layout.hero_anchor)
        self.hero_anchor_out = (anchor_canvas[0] - view_x, anchor_canvas[1] - view_y)
        self.max_shift = 0.018 * self.out_w
        interpolator = interpolator or DPFrameInterpolator(fps, weights=CAMERA_WEIGHTS)
        if scene_mode == SCENE_FOREGROUND_TALKING:
            keys = [KeyPose((0.0, 0.0, 1.0), 0.2), KeyPose((1.0, -0.3, 1.02), 0.1),
                    KeyPose((0.1, 0.25, 1.035), 0.2), KeyPose((-1.0, -0.2, 1.02), 0.1)]
        else:
            keys = [KeyPose((0.0, 0.0, 1.0), 0.0), KeyPose((0.6, 0.2, 1.03), 0.1),
                    KeyPose((-0.3, 0.45, 1.05), 0.1), KeyPose((-0.65, 0.0, 1.025), 0.0)]
        self.camera = interpolator.interpolate(keys, interpolator.schedule(keys, self.frame_count, loop=True))
        columns = max(2, math.ceil(self.out_w / grid_cell))
        rows = max(2, math.ceil(self.out_h / grid_cell))
        xs = [round(i * self.out_w / columns) for i in range(columns + 1)]
        ys = [round(k * self.out_h / rows) for k in range(rows + 1)]
        self._vertex_data: list[tuple[float, float, float, float, float, float]] = []
        for y in ys:
            for x in xs:
                cx, cy = x + view_x, y + view_y
                self._vertex_data.append((cx, cy, self._depth(cx, cy), self._region_weight("water", cx, cy),
                                          self._region_weight("vegetation", cx, cy), self._region_weight("smoke", cx, cy)))
        stride = columns + 1
        self._cells = []
        for k in range(rows):
            for i in range(columns):
                self._cells.append(((xs[i], ys[k], xs[i + 1], ys[k + 1]), k * stride + i, (k + 1) * stride + i,
                                    (k + 1) * stride + i + 1, k * stride + i + 1))
        self._fires = self._prepare_fires(view_x, view_y)
        rng = random.Random(seed)
        self._embers = self._prepare_embers(rng)

    def _region_weight(self, label: str, x: float, y: float) -> float:
        best = 0.0
        for x0, y0, x1, y1 in self.regions.get(label, []):
            margin_x = max(4.0, 0.12 * (x1 - x0))
            margin_y = max(4.0, 0.12 * (y1 - y0))
            wx = _smooth01((x - x0) / margin_x) * _smooth01((x1 - x) / margin_x)
            wy = _smooth01((y - y0) / margin_y) * _smooth01((y1 - y) / margin_y)
            best = max(best, wx * wy)
        return best

    def _depth(self, x: float, y: float) -> float:
        """0 = gần (tiền cảnh), 1 = xa (bầu trời)."""
        if self._region_weight("sky", x, y) > 0.5:
            return 1.0
        if y <= self.horizon:
            return 0.85 + 0.15 * (1.0 - y / max(1.0, self.horizon))
        return 0.85 * (1.0 - (y - self.horizon) / max(1.0, self.canvas_h - self.horizon))

    def _prepare_fires(self, view_x: float, view_y: float) -> list[tuple[tuple[int, int, int, int], Image.Image, float]]:
        fires = []
        for index, (x0, y0, x1, y1) in enumerate(self.regions.get("fire", [])):
            box = (int(_clamp(x0 - view_x, 0, self.out_w - 1)), int(_clamp(y0 - view_y, 0, self.out_h - 1)),
                   int(_clamp(x1 - view_x, 1, self.out_w)), int(_clamp(y1 - view_y, 1, self.out_h)))
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                continue
            mask = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(max(2.0, min(mask.size) * 0.12)))
            fires.append((box, mask, index * 0.37))
        return fires

    def _prepare_embers(self, rng: random.Random) -> list[tuple[float, float, float, float, float, int, float]]:
        embers = []
        for box, _, _ in self._fires:
            x0, y0, x1, y1 = box
            for _ in range(min(26, max(8, (x1 - x0) // 12))):
                embers.append((rng.uniform(x0, x1), rng.uniform((y0 + y1) / 2, y1), rng.uniform(0.25, 0.6) * self.out_h,
                               rng.random(), rng.uniform(4.0, 14.0), rng.choice((1, 2, 3)), rng.uniform(1.4, 3.2)))
        return embers

    def camera_shift(self, index: int) -> tuple[float, float, float]:
        ox, oy, zoom = self.camera[index % self.frame_count][:3]
        return ox * self.max_shift, oy * self.max_shift, zoom

    def near_shift(self, index: int) -> tuple[float, float]:
        ox, oy, _ = self.camera_shift(index)
        return ox, oy

    def frame(self, index: int) -> Image.Image:
        index %= self.frame_count
        phase = index / self.frame_count
        ox, oy, zoom = self.camera_shift(index)
        center_x, center_y = self.canvas_w / 2, self.canvas_h / 2
        amplitude = self.canvas_w / 1000.0
        two_pi = 2.0 * math.pi
        sources = []
        for cx, cy, depth, water, vegetation, smoke in self._vertex_data:
            x = center_x + (cx - center_x) / zoom
            y = center_y + (cy - center_y) / zoom
            parallax = 0.25 + 0.75 * (1.0 - depth)
            x -= ox * parallax
            y -= oy * parallax
            if water:
                x += 3.0 * amplitude * water * math.sin(two_pi * (3 * phase + cy / 38.0))
                y += 1.0 * amplitude * water * math.sin(two_pi * (2 * phase + cx / 55.0))
            if vegetation:
                x += 2.2 * amplitude * vegetation * math.sin(two_pi * (2 * phase + cx / 160.0 + cy / 240.0))
            if smoke:
                y -= 3.0 * amplitude * smoke * (0.5 + 0.5 * math.sin(two_pi * (phase + cx / 120.0)))
                x += 1.5 * amplitude * smoke * math.sin(two_pi * (2 * phase + cy / 90.0))
            sources.append((x, y))
        mesh = [(box, (*sources[ul], *sources[ll], *sources[lr], *sources[ur])) for box, ul, ll, lr, ur in self._cells]
        frame = self.canvas.transform((self.out_w, self.out_h), Image.Transform.MESH, mesh, Image.Resampling.BILINEAR)
        for box, mask, offset in self._fires:
            factor = 1.0 + 0.10 * math.sin(two_pi * (5 * phase + offset)) + 0.05 * math.sin(two_pi * (11 * phase + 2 * offset))
            region = frame.crop(box)
            frame.paste(ImageEnhance.Brightness(region).enhance(factor), box[:2], mask)
        if self._embers:
            overlay = Image.new("RGBA", (self.out_w, self.out_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            for x0, y0, rise, offset, drift, cycles, size in self._embers:
                u = (phase + offset) % 1.0
                x = x0 + drift * math.sin(two_pi * (cycles * phase + offset))
                y = y0 - rise * u
                alpha = int(230 * math.sin(math.pi * u))
                if alpha <= 0:
                    continue
                draw.ellipse((x - size * 1.8, y - size * 1.8, x + size * 1.8, y + size * 1.8), fill=(255, 120, 30, alpha // 4))
                draw.ellipse((x - size, y - size, x + size, y + size), fill=(255, 190, 90, alpha))
            base = frame.convert("RGBA")
            base.alpha_composite(overlay)
            frame = base.convert("RGB")
        return frame


# ======================================================================================
# 10. Sân khấu nhân vật & biên đạo động tác
# ======================================================================================
class HeroStage:
    """Đặt lớp nhân vật (ảnh gốc + rig) lên khung hình đầu ra; render tư thế bằng ``HeroRigRenderer``."""

    def __init__(self, layer: Image.Image, rig: HeroRig, *, crop_box: tuple[int, int, int, int], scale: float,
                 position: tuple[int, int], out_size: tuple[int, int], grid_cell: int = 24) -> None:
        crop = layer.crop(crop_box)
        size = (max(2, round(crop.width * scale)), max(2, round(crop.height * scale)))
        self.scaled = crop.resize(size, Image.Resampling.LANCZOS)
        self.rig = rig.transformed(scale=scale, dx=-crop_box[0], dy=-crop_box[1], width=size[0], height=size[1])
        self.renderer = HeroRigRenderer(self.scaled, self.rig, grid_cell=grid_cell)
        self.position = position
        self.out_size = out_size
        self.scale = scale
        self.crop_box = crop_box

    @classmethod
    def for_talking(cls, layer: Image.Image, rig: HeroRig, out_size: tuple[int, int]) -> "HeroStage":
        """Nhân vật đứng TRƯỚC màn hình, khung từ đầu tới thắt lưng, căn giữa, chạm đáy khung hình."""
        width, height = layer.size
        top = int(max(0.0, rig.joints["head_top"][1] - 0.35 * rig.face_h))
        bottom = int(min(float(height), rig.joints["pelvis"][1] + 0.15 * rig.face_h))
        if bottom - top < 0.3 * height:
            bottom = height
        scale = out_size[1] / (bottom - top)
        scaled_w = width * scale
        position = (round(out_size[0] / 2 - scaled_w / 2), out_size[1] - round((bottom - top) * scale))
        return cls(layer, rig, crop_box=(0, top, width, bottom), scale=scale, position=position, out_size=out_size)

    @classmethod
    def for_action(cls, layer: Image.Image, rig: HeroRig, out_size: tuple[int, int], anchor_x: float,
                   height_ratio: float = 0.9) -> "HeroStage":
        """Nhân vật đứng TRONG cảnh (gần như cả người), đặt tại điểm neo AI Đạo diễn chọn."""
        width, height = layer.size
        box = alpha_bbox(layer)
        top = int(max(0.0, box[1] - 0.05 * rig.face_h))
        scale = out_size[1] * height_ratio / (height - top)
        scaled_w = width * scale
        neck_x = rig.joints["neck"][0] * scale
        x = anchor_x - neck_x
        x = _clamp(x, -0.25 * scaled_w, out_size[0] - 0.75 * scaled_w)
        y = out_size[1] - round((height - top) * scale) + round(0.03 * out_size[1])
        return cls(layer, rig, crop_box=(0, top, width, height), scale=scale, position=(round(x), y), out_size=out_size)

    def layer(self, pose: RigPose) -> Image.Image:
        return self.renderer.render(pose)

    def composite(self, frame: Image.Image, pose: RigPose, shift: tuple[float, float] = (0.0, 0.0)) -> Image.Image:
        layer = self.layer(pose)
        frame = frame.convert("RGB")
        frame.paste(layer, (self.position[0] + round(shift[0]), self.position[1] + round(shift[1])), layer)
        return frame


# Biên độ được chọn để biến dạng chỉ là "uốn" nhẹ trên ảnh gốc (giữ nguyên nhân vật), cảm giác hành động đến từ
# chuyển động toàn thân (rướn người, nhún bước, nghiêng đầu) cộng với camera, lửa, khói của cảnh nền.
TALK_IDLE_KEYS: tuple[KeyPose, ...] = (
    KeyPose(RigPose().vector(), 0.4, "nghỉ"),
    KeyPose(RigPose(breath=0.006, head_tilt_deg=-1.6, head_nod=0.008, upper_r_deg=1.5, lower_r_deg=3.0).vector(),
            0.3, "nghiêng đầu, đưa tay phải"),
    KeyPose(RigPose(breath=0.003, head_tilt_deg=1.2, head_nod=-0.004, upper_l_deg=-1.5, lower_l_deg=-3.0).vector(),
            0.3, "nghiêng đầu, đưa tay trái"),
    KeyPose(RigPose(breath=0.007, head_tilt_deg=-0.5, head_nod=0.004).vector(), 0.2, "hít sâu"),
)
ACTION_KEYS_RIGHT: tuple[KeyPose, ...] = (
    KeyPose(RigPose(lean_deg=-1.0, breath=0.004, upper_r_deg=6.0, lower_r_deg=4.0).vector(), 0.15, "thủ thế"),
    KeyPose(RigPose(lean_deg=-3.5, body_dy=-0.02, head_tilt_deg=-2.5, upper_r_deg=22.0, lower_r_deg=12.0,
                    breath=0.009).vector(), 0.35, "rướn người, vung tay ra lệnh"),
    KeyPose(RigPose(lean_deg=-6.0, body_dx=0.05, body_dy=0.005, head_tilt_deg=2.0, upper_r_deg=16.0, lower_r_deg=-4.0,
                    breath=0.006).vector(), 0.3, "xông lên, chỉ thẳng về phía quân giặc"),
    KeyPose(RigPose(lean_deg=-2.0, body_dx=0.015, upper_r_deg=9.0, lower_r_deg=6.0, breath=0.003).vector(), 0.1,
            "thu thế"),
)


class Choreographer:
    """Biên đạo: dựng chuỗi tư thế từng khung (60 FPS) từ tư thế khoá bằng ``DPFrameInterpolator``."""

    def __init__(self, fps: int = TARGET_FPS) -> None:
        # hold_weight lớn: động tác chỉ huy cần dừng đúng nhịp ở tư thế khoá (giơ tay, chỉ thẳng) cho rõ ràng.
        self.interpolator = DPFrameInterpolator(fps, weights=POSE_DISTANCE_WEIGHTS, hold_weight=12.0)
        self.fps = fps

    def _cycle(self, keys: Sequence[KeyPose], loop_s: float, frames: int) -> list[RigPose]:
        loop_frames = max(2, round(loop_s * self.fps))
        cycle = [RigPose.from_vector(v) for v in self.interpolator.interpolate(
            keys, self.interpolator.schedule(keys, loop_frames, loop=True))]
        return [cycle[index % loop_frames] for index in range(frames)]

    def talking(self, frames: int, loop_s: float = 6.0) -> list[RigPose]:
        return self._cycle(TALK_IDLE_KEYS, loop_s, frames)

    def action(self, frames: int, loop_s: float = 4.0, side: str = "right") -> list[RigPose]:
        poses = self._cycle(ACTION_KEYS_RIGHT, loop_s, frames)
        return poses if side == "right" else [pose.mirrored() for pose in poses]


# ======================================================================================
# 11. Ghi ảnh động WebP 60 FPS (render song song, bộ nhớ thấp) & kiểm tra
# ======================================================================================
class _LazyFrameSequence(Image.Image):
    """Chuỗi khung "ảo" cho bộ mã hoá WebP động của Pillow: khung được render khi cần, nhiều luồng tiền nạp."""

    def __init__(self, count: int, render: Callable[[int], Image.Image], *, workers: int) -> None:
        super().__init__()
        if count < 1:
            raise ValueError("Cần ít nhất một khung hình.")
        self._count = count
        self._render = render
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="frame-render")
        self._futures: dict[int, Future[Image.Image]] = {}
        self._prefetch = max(2, workers * 2)
        self._next_submit = 0
        self._current = -1
        self._first: Image.Image | None = None
        self._activate(0)

    @property
    def n_frames(self) -> int:
        return self._count

    @property
    def is_animated(self) -> bool:
        return self._count > 1

    def tell(self) -> int:
        return self._current

    def seek(self, frame: int) -> None:
        if frame == self._current:
            return
        if not 0 <= frame < self._count:
            raise EOFError("Khung hình ngoài phạm vi.")
        self._activate(frame)

    def _submit_until(self, last: int) -> None:
        while self._next_submit <= min(last, self._count - 1):
            index = self._next_submit
            self._futures[index] = self._executor.submit(self._render, index)
            self._next_submit += 1

    def _activate(self, index: int) -> None:
        if index == 0 and self._first is not None:
            image = self._first
        elif index in self._futures or index >= self._next_submit:
            self._submit_until(index + self._prefetch)
            image = self._futures.pop(index).result()
        else:
            image = self._render(index)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        if index == 0:
            self._first = image
        self._im = image.im
        self._mode = image.mode
        self._size = image.size
        self._current = index

    def shutdown(self) -> None:
        for future in self._futures.values():
            future.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)


def write_webp_animation(path: Path, frame_count: int, render: Callable[[int], Image.Image], *,
                         fps: int = TARGET_FPS, quality: int = 72, method: int = 0, workers: int | None = None) -> Path:
    """Ghi ảnh động WebP lặp vô hạn ở ``fps`` khung/giây; khung render song song, chỉ giữ vài khung trong RAM."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{os.getpid()}.{threading.get_ident()}.webp")
    sequence = _LazyFrameSequence(frame_count, render, workers=workers or default_render_workers())
    try:
        sequence.save(temporary, "WEBP", save_all=True, duration=frame_durations_ms(frame_count, fps), loop=0,
                      quality=quality, method=method, minimize_size=False)
    finally:
        sequence.shutdown()
    os.replace(temporary, path)
    return path


@dataclass(frozen=True)
class AnimationInfo:
    path: Path
    frame_count: int
    size: tuple[int, int]
    durations_ms: tuple[int, ...]
    fps: float
    loop: int
    mean_step_diff: float
    max_step_diff: float
    loop_seam_diff: float


def inspect_animation(path: Path, *, sample_size: tuple[int, int] = (96, 54)) -> AnimationInfo:
    """Đọc lại ảnh động: số khung, thời lượng từng khung, FPS, độ mượt (chênh lệch giữa các khung liên tiếp)."""
    with Image.open(path) as animation:
        durations: list[int] = []
        thumbs: list[Image.Image] = []
        for index in range(getattr(animation, "n_frames", 1)):
            animation.seek(index)
            animation.load()  # Pillow chỉ điền info["duration"] của khung WebP sau khi giải mã khung đó
            durations.append(int(animation.info.get("duration", 0) or 0))
            thumbs.append(animation.convert("L").resize(sample_size, Image.Resampling.BILINEAR))
        size = animation.size
        loop = int(animation.info.get("loop", 0) or 0)
    steps = [mean_abs_diff(a, b) for a, b in zip(thumbs, thumbs[1:])]
    total_ms = sum(durations)
    return AnimationInfo(
        path=Path(path), frame_count=len(thumbs), size=size, durations_ms=tuple(durations),
        fps=(len(thumbs) * 1000.0 / total_ms) if total_ms else 0.0, loop=loop,
        mean_step_diff=sum(steps) / len(steps) if steps else 0.0, max_step_diff=max(steps) if steps else 0.0,
        loop_seam_diff=mean_abs_diff(thumbs[-1], thumbs[0]) if len(thumbs) > 1 else 0.0,
    )


# ======================================================================================
# 12. Khởi tạo ảnh mẫu 3 vị anh hùng
# ======================================================================================
HERO_SEARCH_QUERIES: dict[str, tuple[str, ...]] = {
    "tran_hung_dao": ("Trần Hưng Đạo painting", "Trần Hưng Đạo statue", "Tran Hung Dao"),
    "nguyen_trai": ("Nguyễn Trãi portrait", "Nguyen Trai", "Nguyễn Trãi statue"),
    "quang_trung": ("Quang Trung statue", "Quang Trung Nguyễn Huệ", "Nguyễn Huệ Quang Trung painting"),
}
# Bức "Annam King Ruan Guangping" (1790) theo nhiều nhà sử học vẽ người thế thân (Phạm Công Trị), không phải
# Quang Trung → loại khỏi ứng viên ảnh chuẩn.
HERO_EXCLUDED_TITLE_MARKERS: dict[str, tuple[str, ...]] = {
    "tran_hung_dao": ("road", "street", "coin", "lư hương"),
    "nguyen_trai": ("road", "street", "coin"),
    "quang_trung": ("ruan guangping", "annam king", "phạm công trị", "coin", "thông bảo", "dai bao", "museum"),
}
HERO_NAME_EN: dict[str, str] = {
    "tran_hung_dao": "Trần Hưng Đạo (Trần Quốc Tuấn), Vietnamese grand general of the Trần dynasty",
    "nguyen_trai": "Nguyễn Trãi, Vietnamese Confucian scholar-strategist of the early Lê dynasty",
    "quang_trung": "Emperor Quang Trung (Nguyễn Huệ) of the Tây Sơn dynasty, Vietnam",
}
HERO_LOOK: dict[str, str] = {
    "tran_hung_dao": ("a Vietnamese grand general about 55 years old, dignified stern face, long black beard and long "
                      "mustache, black hair in a topknot with a golden crown ornament, red and gold lamellar armor with "
                      "golden shoulder guards over a crimson robe"),
    "nguyen_trai": ("a Vietnamese Confucian scholar-official about 50 years old, thin wise face, long thin grey-black "
                    "beard and mustache, black winged official hat, dark blue silk official robe with an embroidered "
                    "square insignia on the chest"),
    "quang_trung": ("a Vietnamese emperor-general about 35 years old, strong build, determined fiery eyes, short black "
                    "mustache and small beard, yellow silk turban, golden-yellow robe embroidered with dragons over "
                    "red armor"),
}
HERO_PROP: dict[str, str] = {
    "tran_hung_dao": "his left hand resting on the hilt of a sheathed sword at his waist",
    "nguyen_trai": "holding a rolled paper scroll in his left hand",
    "quang_trung": "his left hand resting on the hilt of a sheathed sword at his waist",
}
PORTRAIT_ACCEPT_SCORE = 6.0
PORTRAIT_MIN_SCORE = 4.0


def build_hero_prompt(profile: CharacterProfile, look: str) -> str:
    return (
        f"Portrait of {HERO_NAME_EN[profile.character_id]}, {profile.era_en}. {look}. Knee-length three-quarter body "
        "portrait, standing upright and facing the viewer frontally, centered, symmetrical stance, both arms relaxed "
        f"along the sides with both hands fully visible inside the frame, {HERO_PROP[profile.character_id]}. Calm "
        "dignified expression, lips gently closed, mouth clearly visible. Isolated on a plain flat solid bright green "
        "background (#00FF00), no shadows, no scenery, no other people, no text. Even soft studio lighting, highly "
        "detailed historical oil painting."
    )


def portrait_score(check: Mapping[str, Any], cutout: CutoutResult) -> float:
    """Điểm 0-10 tổng hợp đánh giá của AI Đạo diễn và chất lượng tách nền."""
    try:
        score = float(check.get("overall", 0))
        matches = float(check.get("matches_description", 5))
    except (TypeError, ValueError):
        return 0.0
    penalties = {"single_person": 3.0, "frontal": 2.0, "mouth_visible_closed": 2.0, "plain_background": 1.5,
                 "historical_costume": 1.5, "body_framing_ok": 1.0, "hands_ok": 1.0}
    for name, penalty in penalties.items():
        if check.get(name) is not True:
            score -= penalty
    if not cutout.usable:
        score -= 2.0
    return _clamp(score + 0.2 * (matches - 5.0), 0.0, 10.0)


@dataclass(frozen=True)
class HeroAsset:
    character_id: str
    image_path: Path
    rig_path: Path
    meta_path: Path
    rig: HeroRig
    source: str
    width: int
    height: int
    reference_url: str

    def load_layer(self) -> Image.Image:
        with Image.open(self.image_path) as image:
            image.load()
            return make_cutout(image).image


class HeroModelInitializer:
    """Đảm bảo ``frontend/public/assets/heroes/`` có đủ 3 ảnh mẫu gốc (+ rig + nguồn gốc); thiếu thì AI tự làm.

    Quy trình cho một vị anh hùng còn thiếu ảnh:
    1. Tìm ảnh chuẩn trên Wikimedia Commons (giấy phép tự do), tải song song nhiều luồng.
    2. AI Đạo diễn chấm điểm, chọn ảnh chuẩn nhất và mô tả đặc điểm nhận dạng truyền thống.
    3. Hugging Face Spaces vẽ chân dung đứng chính diện trên nền xanh theo mô tả đó (song song, nhiều seed).
    4. AI Đạo diễn kiểm định từng ứng viên; tách nền; chọn ảnh tốt nhất (thiếu thì vẽ thêm một vòng).
    5. AI Đạo diễn dò khung xương → ``{id}.rig.json``; ghi ``{id}.png`` (RGBA) và ``{id}.meta.json``.
    Không vẽ được thì dùng thẳng ảnh chuẩn Wikimedia (đã tách nền) làm ảnh mẫu.
    """

    def __init__(
        self,
        pool: MultiThreadKeyPool,
        *,
        cache: DiskDPCache | None = None,
        heroes_dir: Path = HEROES_DIR,
        hf_source: HuggingFaceImageSource | None = None,
        wikimedia: WikimediaCommonsSource | None = None,
        vision: DirectorVision | None = None,
        candidates_per_round: int = 2,
        max_rounds: int = 2,
        wikimedia_candidates: int = 6,
        image_size: tuple[int, int] = HERO_IMAGE_SIZE,
    ) -> None:
        self.cache = cache or DiskDPCache()
        self.heroes_dir = Path(heroes_dir)
        self.vision = vision or DirectorVision(pool, self.cache)
        self.hf = hf_source or HuggingFaceImageSource(self.cache)
        self.wikimedia = wikimedia or WikimediaCommonsSource(self.cache)
        self.candidates_per_round = candidates_per_round
        self.max_rounds = max_rounds
        self.wikimedia_candidates = wikimedia_candidates
        self.image_size = image_size
        self._io = ThreadPoolExecutor(max_workers=6, thread_name_prefix="wikimedia")
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}
        self.log: list[str] = []

    def close(self) -> None:
        self._io.shutdown(wait=False, cancel_futures=True)

    def paths(self, character_id: str) -> tuple[Path, Path, Path]:
        base = self.heroes_dir / character_id
        return base.with_suffix(".png"), base.with_suffix(".rig.json"), base.with_suffix(".meta.json")

    def missing(self) -> list[str]:
        return [cid for cid in SUPPORTED_CHARACTER_IDS if not self.paths(cid)[0].is_file()]

    def _lock(self, character_id: str) -> asyncio.Lock:
        key = (id(asyncio.get_running_loop()), character_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def ensure_all(self, *, force: bool = False) -> dict[str, HeroAsset]:
        assets = await asyncio.gather(*(self.ensure(cid, force=force) for cid in SUPPORTED_CHARACTER_IDS))
        return {asset.character_id: asset for asset in assets}

    async def ensure(self, character_id: str, *, force: bool = False) -> HeroAsset:
        profile = get_character(character_id)
        async with self._lock(profile.character_id):
            image_path = self.paths(profile.character_id)[0]
            if image_path.is_file() and not force:
                return await self._load_existing(profile)
            return await self._create(profile)

    async def _find_reference(self, profile: CharacterProfile) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        searches = await asyncio.gather(
            *(loop.run_in_executor(self._io, self.wikimedia.search, query) for query in HERO_SEARCH_QUERIES[profile.character_id]),
            return_exceptions=True,
        )
        excluded = HERO_EXCLUDED_TITLE_MARKERS.get(profile.character_id, ())
        seen: set[str] = set()
        candidates: list[WikimediaImage] = []
        for result in searches:
            if isinstance(result, BaseException):
                self.log.append(f"{profile.character_id}: tìm Wikimedia lỗi: {result}")
                continue
            for item in result:
                title = item.title.lower()
                if (item.title in seen or not item.thumb_url or not item.mime.startswith("image/")
                        or not WikimediaCommonsSource.is_free_license(item.license)
                        or min(item.width, item.height) < 300 or any(marker in title for marker in excluded)):
                    continue
                seen.add(item.title)
                candidates.append(item)
        candidates = candidates[: self.wikimedia_candidates]
        downloads = await asyncio.gather(
            *(loop.run_in_executor(self._io, self.wikimedia.download, item) for item in candidates), return_exceptions=True
        )
        usable = [(item, data) for item, data in zip(candidates, downloads) if isinstance(data, bytes)]
        empty = {"iconography": None, "image": None, "image_bytes": None, "score": 0, "candidates": len(candidates)}
        if not usable:
            return empty
        try:
            ranking = await self.vision.rank_references(profile, [data for _, data in usable])
        except (KeyPoolError, ValueError) as exc:
            self.log.append(f"{profile.character_id}: AI chấm ảnh chuẩn lỗi: {exc}")
            return empty
        best_index = ranking.get("best_index", -1)
        if not isinstance(best_index, int) or not 0 <= best_index < len(usable):
            return empty
        scores = {item.get("index"): item for item in ranking.get("candidates") or [] if isinstance(item, Mapping)}
        best_meta = scores.get(best_index, {})
        if best_meta and best_meta.get("depicts_hero") is False:
            return empty
        iconography = str(ranking.get("iconography") or "").strip()
        image, data = usable[best_index]
        return {"iconography": iconography or None, "image": image, "image_bytes": data,
                "score": int(best_meta.get("score", 0) or 0), "candidates": len(candidates)}

    async def _create(self, profile: CharacterProfile) -> HeroAsset:
        cid = profile.character_id
        reference = await self._find_reference(profile)
        look = reference["iconography"] or HERO_LOOK[cid]
        prompt = build_hero_prompt(profile, look)
        width, height = self.image_size
        best: tuple[float, GeneratedImage, CutoutResult, Mapping[str, Any]] | None = None
        errors: list[str] = []
        for round_index in range(self.max_rounds):
            seeds = [stable_seed(cid, "portrait", round_index, index) for index in range(self.candidates_per_round)]
            results = await asyncio.gather(
                *(self.hf.generate_async(prompt, width=width, height=height, seed=seed, spill=False) for seed in seeds),
                return_exceptions=True,
            )
            evaluated = []
            for result in results:
                if isinstance(result, BaseException):
                    errors.append(str(result))
                    continue
                cutout = make_cutout(open_image(result.data))
                evaluated.append((result, cutout))
            checks = await asyncio.gather(
                *(self.vision.verify_portrait(profile, cutout.image, look) for _, cutout in evaluated),
                return_exceptions=True,
            )
            for (result, cutout), check in zip(evaluated, checks):
                if isinstance(check, BaseException):
                    errors.append(f"kiểm định lỗi: {check}")
                    check = {}
                score = portrait_score(check, cutout)
                if best is None or score > best[0]:
                    best = (score, result, cutout, check)
            if best is not None and best[0] >= PORTRAIT_ACCEPT_SCORE:
                break
        if best is not None and best[0] >= PORTRAIT_MIN_SCORE:
            score, generated, cutout, check = best
            layer = cutout.image
            source = f"huggingface:{generated.space_id}"
            generation = {"space_id": generated.space_id, "seed": generated.seed, "prompt": prompt,
                          "elapsed_s": round(generated.elapsed_s, 2), "verify": dict(check), "score": round(score, 2),
                          "cutout": cutout.method}
        elif reference["image_bytes"] is not None:
            cutout = make_cutout(open_image(reference["image_bytes"]))
            layer = cutout.image
            source = "wikimedia"
            generation = {"fallback_reason": " | ".join(errors)[-600:] or "điểm kiểm định thấp", "cutout": cutout.method}
        else:
            raise HeroInitializationError(f"{cid}: không tạo / tìm được ảnh mẫu đạt yêu cầu. {' | '.join(errors)[-500:]}")
        rig = await self._analyze_rig(cid, layer)
        image_path, rig_path, meta_path = self.paths(cid)
        atomic_write_bytes(image_path, image_to_png_bytes(layer))
        atomic_write_text(rig_path, json.dumps(rig.to_json(), ensure_ascii=False, indent=1))
        reference_image: WikimediaImage | None = reference["image"]
        meta = {
            "character_id": cid,
            "source": source,
            "generation": generation,
            "iconography": look,
            "wikimedia_reference": reference_image.to_json() if reference_image else None,
            "wikimedia_reference_score": reference["score"],
            "size": list(layer.size),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=1))
        return HeroAsset(cid, image_path, rig_path, meta_path, rig, source, layer.width, layer.height,
                         character_reference_path(cid))

    async def _analyze_rig(self, character_id: str, layer: Image.Image) -> HeroRig:
        try:
            analysis = await self.vision.analyze_rig(layer)
        except (KeyPoolError, ValueError) as exc:
            self.log.append(f"{character_id}: dò khung xương lỗi, dùng tỉ lệ cơ thể mặc định: {exc}")
            analysis = {}
        return build_rig(character_id, analysis, layer.width, layer.height, alpha_bbox(layer))

    async def _load_existing(self, profile: CharacterProfile) -> HeroAsset:
        cid = profile.character_id
        image_path, rig_path, meta_path = self.paths(cid)
        with Image.open(image_path) as image:
            image.load()
            layer = make_cutout(image).image
        rig: HeroRig | None = None
        if rig_path.is_file():
            try:
                rig = HeroRig.from_json(json.loads(rig_path.read_text(encoding="utf-8")))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                rig = None
        if rig is None or (rig.width, rig.height) != layer.size:
            rig = await self._analyze_rig(cid, layer)
            atomic_write_text(rig_path, json.dumps(rig.to_json(), ensure_ascii=False, indent=1))
        source = "existing"
        if meta_path.is_file():
            try:
                source = str(json.loads(meta_path.read_text(encoding="utf-8")).get("source", "existing"))
            except (OSError, json.JSONDecodeError):
                source = "existing"
        else:
            atomic_write_text(meta_path, json.dumps({"character_id": cid, "source": "existing"}, ensure_ascii=False))
        return HeroAsset(cid, image_path, rig_path, meta_path, rig, source, layer.width, layer.height,
                         character_reference_path(cid))


# ======================================================================================
# 13. Điều khiển cử động miệng vs hành động
# ======================================================================================
@dataclass(frozen=True)
class PerformancePlan:
    character_id: str
    scene_mode: str
    mouth_sync_enabled: bool
    fps: int
    frame_count: int
    duration_ms: float
    speech_end_ms: float
    poses: tuple[RigPose, ...]
    mouth_track: MouthTrack | None
    action_side: str

    def jaw_series(self) -> tuple[float, ...]:
        return tuple(pose.jaw_open for pose in self.poses)


class MouthAndActionController:
    """Quyết định & lập kế hoạch biểu diễn theo ``scene_mode`` của AI Đạo diễn.

    * FOREGROUND_TALKING: nhân vật đứng trước màn hình; miệng chạy theo ``TextToVisemeDPSync`` khớp nhịp chữ, kèm
      cử chỉ nhẹ; hết lời → miệng về REST và đứng yên miệng.
    * BACKGROUND_ACTION: nhân vật hành động (chỉ huy, xông trận); ``mouth_sync`` TẮT — độ mở hàm = 0 ở MỌI khung.
    """

    def __init__(self, *, fps: int = TARGET_FPS, viseme_sync: TextToVisemeDPSync | None = None,
                 choreographer: Choreographer | None = None) -> None:
        self.fps = fps
        self.sync = viseme_sync or TextToVisemeDPSync(fps)
        self.choreographer = choreographer or Choreographer(fps)

    def _trim(self, speech: str | Sequence[SpeechChunk], max_duration_ms: float) -> list[_SpeechPiece]:
        pieces = self.sync.speech_pieces(speech)
        kept: list[_SpeechPiece] = []
        clock = 0.0
        for piece in pieces:
            if kept and clock + piece.speak_ms + piece.pause_ms + self.sync.tail_ms > max_duration_ms:
                break
            kept.append(piece)
            clock += piece.speak_ms + piece.pause_ms
        return kept

    def plan(
        self,
        directive: SceneDirective,
        speech: str | Sequence[SpeechChunk] | None = None,
        *,
        max_duration_ms: float | None = None,
        action_loop_s: float = 4.0,
        action_side: str = "right",
    ) -> PerformancePlan:
        talking = directive.scene_mode == SCENE_FOREGROUND_TALKING and directive.mouth_sync_enabled
        track: MouthTrack | None = None
        speech_end = 0.0
        if speech:
            pieces = self._trim(speech, max_duration_ms) if max_duration_ms else self.sync.speech_pieces(speech)
            if talking:
                track = self.sync.sync([(p.text, p.speak_ms, p.pause_ms) for p in pieces])
                duration = track.duration_ms
                speech_end = track.speech_end_ms
            else:
                duration = sum(p.speak_ms + p.pause_ms for p in pieces) + self.sync.tail_ms
        else:
            duration = action_loop_s * 1000.0
        if max_duration_ms:
            duration = min(duration, max_duration_ms)
        frames = max(2, round(duration * self.fps / 1000.0))
        if talking:
            body = self.choreographer.talking(frames)
        else:
            body = self.choreographer.action(frames, loop_s=action_loop_s, side=action_side)
        poses = []
        for index, pose in enumerate(body):
            if track is not None and index < len(track.frames):
                mouth = track.frames[index]
                poses.append(pose.with_mouth(mouth.openness, mouth.width))
            else:
                poses.append(pose.with_mouth(0.0, 1.0))  # không nói / tắt mouth_sync → miệng khép
        return PerformancePlan(directive.character_id, directive.scene_mode, talking, self.fps, frames,
                               frames * 1000.0 / self.fps, speech_end, tuple(poses), track, action_side)

    @staticmethod
    def mouth_activity(stage: HeroStage, plan: PerformancePlan, step: int = 4) -> list[tuple[int, float]]:
        """Độ thay đổi vùng miệng do khẩu hình gây ra: so khung có miệng với cùng tư thế nhưng miệng khép."""
        series = []
        for index in range(0, plan.frame_count, step):
            pose = plan.poses[index]
            closed = pose.with_mouth(0.0, 1.0)
            matrices = stage.renderer.bone_matrices(closed)
            box = stage.renderer.mouth_box(closed, matrices)
            if pose.jaw_open == 0.0 and pose.mouth_width == 1.0:
                series.append((index, 0.0))
                continue
            speaking = stage.renderer.render(pose).crop(box)
            silent = stage.renderer.render(closed).crop(box)
            series.append((index, mean_abs_diff(speaking, silent)))
        return series


# ======================================================================================
# 14. Engine: cảnh động giữ nguyên nhân vật
# ======================================================================================
@dataclass(frozen=True)
class SceneAnimation:
    scene_key: str
    character_id: str
    scene_mode: str
    context_key: str
    folder: Path
    background_png: Path
    animation_webp: Path
    layout: SceneLayout
    fps: int
    frame_count: int
    size: tuple[int, int]
    background_source: str
    from_cache: bool
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class PerformanceRender:
    output_webp: Path
    plan: PerformancePlan
    scene: SceneAnimation
    frame_count: int
    fps: int
    mouth_activity: tuple[tuple[int, float], ...]
    body_motion: float
    consistency: Mapping[str, Any]
    from_cache: bool


class ConsistentHeroAnimationEngine:
    """Tạo cảnh động 60 FPS theo kịch bản của AI Đạo diễn, giữ nguyên 100% model anh hùng.

    * Cảnh nền: Hugging Face Space vẽ theo ``background_prompt`` của AI Đạo diễn; AI Đạo diễn khoanh vùng (trời, nước,
      lửa, khói, cây) + điểm đặt nhân vật; ``BackgroundAnimator`` làm động 60 FPS.
    * Nhân vật: lớp ảnh gốc + rig 2.5D. FOREGROUND_TALKING → nhân vật đứng trước, nền quê hương động phía sau.
      BACKGROUND_ACTION → nhân vật đứng trong cảnh, diễn động tác chỉ huy/chiến đấu, miệng khép.
    * DP Cache trên đĩa theo khoá ngữ cảnh (nhân vật, chế độ, bối cảnh) → câu hỏi cùng bối cảnh dùng lại cảnh O(1).
    """

    def __init__(
        self,
        pool: MultiThreadKeyPool,
        *,
        cache: DiskDPCache | None = None,
        hf_source: HuggingFaceImageSource | None = None,
        wikimedia: WikimediaCommonsSource | None = None,
        vision: DirectorVision | None = None,
        initializer: HeroModelInitializer | None = None,
        controller: MouthAndActionController | None = None,
        heroes_dir: Path = HEROES_DIR,
        scenes_dir: Path = SCENES_DIR,
        renders_dir: Path = RENDERS_DIR,
        out_size: tuple[int, int] = DEFAULT_OUTPUT_SIZE,
        fps: int = TARGET_FPS,
        loop_s: float = 4.0,
        render_workers: int | None = None,
    ) -> None:
        self.cache = cache or DiskDPCache()
        self.vision = vision or DirectorVision(pool, self.cache)
        self.hf = hf_source or HuggingFaceImageSource(self.cache)
        self.wikimedia = wikimedia or WikimediaCommonsSource(self.cache)
        self.initializer = initializer or HeroModelInitializer(pool, cache=self.cache, heroes_dir=heroes_dir,
                                                               hf_source=self.hf, wikimedia=self.wikimedia,
                                                               vision=self.vision)
        self.controller = controller or MouthAndActionController(fps=fps)
        self.scenes_dir = Path(scenes_dir)
        self.renders_dir = Path(renders_dir)
        self.out_size = out_size
        self.fps = fps
        self.loop_s = loop_s
        self.render_workers = render_workers or default_render_workers()
        self.log: list[str] = []

    def close(self) -> None:
        self.hf.close()
        self.initializer.close()

    async def __aenter__(self) -> "ConsistentHeroAnimationEngine":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.close()

    async def ensure_heroes(self, *, force: bool = False) -> dict[str, HeroAsset]:
        return await self.initializer.ensure_all(force=force)

    # ------------------------------------------------------------------ khoá & prompt
    def scene_key(self, directive: SceneDirective) -> str:
        payload: dict[str, Any] = {"v": RENDER_VERSION, "c": directive.character_id, "m": directive.scene_mode,
                                   "ctx": directive.context_key, "size": list(self.out_size), "fps": self.fps,
                                   "loop": self.loop_s}
        if directive.context_key in ("other_event", ""):
            payload["prompt"] = stable_hash(directive.background_prompt)
        return DiskDPCache.make_key("scene", payload)

    @staticmethod
    def background_prompt(directive: SceneDirective) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", directive.background_prompt.strip())
        kept = [s for s in sentences if s and "hero" not in s.lower() and "reference image" not in s.lower()]
        words = " ".join(kept or sentences).split()
        base = " ".join(words[:90])
        if directive.scene_mode == SCENE_FOREGROUND_TALKING:
            suffix = ("Wide cinematic establishing landscape, empty foreground without people, no text, no watermark, "
                      "soft natural light, rich detail, historical painting style.")
        else:
            suffix = ("Epic wide battle scene seen from an elevated vantage point, the lower-left foreground is an empty "
                      "ledge with no figure, no text, no watermark, dramatic cinematic light, historical painting style.")
        return f"{base} {suffix}"

    # ------------------------------------------------------------------ cảnh
    async def _layout(self, background: Image.Image, scene_mode: str) -> SceneLayout:
        try:
            analysis = await self.vision.analyze_scene(background, scene_mode)
            return layout_from_analysis(analysis, background.width, background.height, scene_mode)
        except (KeyPoolError, ValueError) as exc:
            self.log.append(f"Khoanh vùng cảnh lỗi, dùng bố cục mặc định: {exc}")
            return heuristic_layout(background.width, background.height, scene_mode)

    def _animator(self, background: Image.Image, layout: SceneLayout, scene_mode: str, seed: int) -> BackgroundAnimator:
        return BackgroundAnimator(background, layout, scene_mode=scene_mode, out_size=self.out_size, fps=self.fps,
                                  loop_s=self.loop_s, seed=seed)

    def _stage(self, hero: HeroAsset, layer: Image.Image, scene_mode: str, animator: BackgroundAnimator) -> HeroStage:
        if scene_mode == SCENE_FOREGROUND_TALKING:
            return HeroStage.for_talking(layer, hero.rig, self.out_size)
        return HeroStage.for_action(layer, hero.rig, self.out_size, animator.hero_anchor_out[0])

    @staticmethod
    def action_side(animator: BackgroundAnimator, out_width: int) -> str:
        return "right" if animator.hero_anchor_out[0] <= out_width / 2 else "left"

    async def prepare_scene(self, directive: SceneDirective) -> SceneAnimation:
        """Tạo (hoặc lấy lại O(1) từ DP Cache) cảnh động 60 FPS cho chỉ thị của AI Đạo diễn."""
        profile = get_character(directive.character_id)
        hero = await self.initializer.ensure(profile.character_id)
        key = self.scene_key(directive)
        entry = self.cache.get(key)
        if entry is not None and isinstance(entry.value, Mapping):
            return self._scene_from_manifest(key, entry.value, from_cache=True)
        prompt = self.background_prompt(directive)
        seed = stable_seed(key)
        width, height = BACKGROUND_IMAGE_SIZE
        generated = await self.hf.generate_async(prompt, width=width, height=height, seed=seed, spill=True)
        background = open_image(generated.data).convert("RGB")
        layout = await self._layout(background, directive.scene_mode)
        short_mode = "talk" if directive.scene_mode == SCENE_FOREGROUND_TALKING else "action"
        folder = self.scenes_dir / f"{profile.character_id}__{short_mode}__{directive.context_key or 'scene'}__{key[-8:]}"
        folder.mkdir(parents=True, exist_ok=True)
        background_png = folder / "background.png"
        atomic_write_bytes(background_png, generated.data)
        atomic_write_text(folder / "layout.json", json.dumps(layout.to_json(), ensure_ascii=False, indent=1))
        animator = self._animator(background, layout, directive.scene_mode, seed)
        loop = asyncio.get_running_loop()
        consistency: dict[str, Any] = {}
        if directive.scene_mode == SCENE_FOREGROUND_TALKING:
            animation = folder / "background_60fps.webp"
            await loop.run_in_executor(None, functools.partial(
                write_webp_animation, animation, animator.frame_count, animator.frame, fps=self.fps,
                workers=self.render_workers))
        else:
            layer = hero.load_layer()
            stage = self._stage(hero, layer, directive.scene_mode, animator)
            side = self.action_side(animator, self.out_size[0])
            poses = self.controller.choreographer.action(animator.frame_count, loop_s=self.loop_s, side=side)

            def render_action(index: int) -> Image.Image:
                return stage.composite(animator.frame(index), poses[index], animator.near_shift(index))

            animation = folder / "scene_60fps.webp"
            await loop.run_in_executor(None, functools.partial(
                write_webp_animation, animation, animator.frame_count, render_action, fps=self.fps,
                workers=self.render_workers))
            consistency = self.consistency_report(stage, poses)
        manifest = {
            "scene_key": key,
            "character_id": profile.character_id,
            "scene_mode": directive.scene_mode,
            "mouth_sync_enabled": directive.mouth_sync_enabled,
            "context_key": directive.context_key,
            "state_id": directive.state_id,
            "character_reference_path": directive.character_reference_path,
            "folder": DiskDPCache._store_path(folder),
            "background_png": DiskDPCache._store_path(background_png),
            "animation_webp": DiskDPCache._store_path(animation),
            "background_prompt": prompt,
            "background_source": generated.space_id,
            "layout": layout.to_json(),
            "fps": self.fps,
            "frame_count": animator.frame_count,
            "size": list(self.out_size),
            "action_side": self.action_side(animator, self.out_size[0]),
            "consistency": consistency,
            "public_url": "/" + DiskDPCache._store_path(animation).split("/public/", 1)[-1],
        }
        atomic_write_text(folder / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        self.cache.put_files(key, {"background": background_png, "animation": animation}, value=manifest,
                             meta={"character_id": profile.character_id, "scene_mode": directive.scene_mode})
        return self._scene_from_manifest(key, manifest, from_cache=False)

    def _scene_from_manifest(self, key: str, manifest: Mapping[str, Any], *, from_cache: bool) -> SceneAnimation:
        resolve = DiskDPCache._resolve
        return SceneAnimation(
            scene_key=key,
            character_id=str(manifest["character_id"]),
            scene_mode=str(manifest["scene_mode"]),
            context_key=str(manifest["context_key"]),
            folder=resolve(str(manifest["folder"])),
            background_png=resolve(str(manifest["background_png"])),
            animation_webp=resolve(str(manifest["animation_webp"])),
            layout=SceneLayout.from_json(manifest["layout"]),
            fps=int(manifest["fps"]),
            frame_count=int(manifest["frame_count"]),
            size=(int(manifest["size"][0]), int(manifest["size"][1])),
            background_source=str(manifest["background_source"]),
            from_cache=from_cache,
            manifest=manifest,
        )

    # ------------------------------------------------------------------ biểu diễn
    async def render_performance(
        self,
        directive: SceneDirective,
        speech: str | Sequence[SpeechChunk] | None = None,
        *,
        max_duration_ms: float | None = None,
    ) -> PerformanceRender:
        """Ảnh động 60 FPS của nhân vật trả lời: nói (miệng khớp chữ) hoặc hành động (miệng tắt)."""
        scene = await self.prepare_scene(directive)
        hero = await self.initializer.ensure(directive.character_id)
        with Image.open(scene.background_png) as image:
            image.load()
            background = image.convert("RGB")
        animator = self._animator(background, scene.layout, directive.scene_mode, stable_seed(scene.scene_key))
        side = self.action_side(animator, self.out_size[0])
        plan = self.controller.plan(directive, speech, max_duration_ms=max_duration_ms, action_loop_s=self.loop_s,
                                    action_side=side)
        speech_signature = stable_hash([[p.text, p.speak_ms, p.pause_ms] for p in self.controller.sync.speech_pieces(speech)]) if speech else ""
        key = DiskDPCache.make_key("render", {"scene": scene.scene_key, "speech": speech_signature,
                                              "frames": plan.frame_count, "mode": plan.scene_mode,
                                              "mouth": plan.mouth_sync_enabled})
        layer = hero.load_layer()
        stage = self._stage(hero, layer, directive.scene_mode, animator)
        entry = self.cache.get(key)
        if entry is not None and isinstance(entry.value, Mapping):
            value = entry.value
            return PerformanceRender(entry.files["animation"], plan, scene, plan.frame_count, self.fps,
                                     tuple((int(i), float(v)) for i, v in value.get("mouth_activity", [])),
                                     float(value.get("body_motion", 0.0)), value.get("consistency", {}), True)

        def render(index: int) -> Image.Image:
            return stage.composite(animator.frame(index), plan.poses[index], animator.near_shift(index))

        output = self.renders_dir / f"{directive.character_id}-{'talk' if plan.mouth_sync_enabled else 'action'}-{key[-12:]}.webp"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, functools.partial(write_webp_animation, output, plan.frame_count, render,
                                                           fps=self.fps, workers=self.render_workers))
        activity = await loop.run_in_executor(None, self.controller.mouth_activity, stage, plan)
        body_motion = await loop.run_in_executor(None, self._body_motion, stage, plan)
        consistency = await loop.run_in_executor(None, self.consistency_report, stage, list(plan.poses))
        value = {"mouth_activity": activity, "body_motion": body_motion, "consistency": consistency,
                 "public_url": "/" + DiskDPCache._store_path(output).split("/public/", 1)[-1],
                 "mouth_track": plan.mouth_track.to_json() if plan.mouth_track else None}
        self.cache.put_files(key, {"animation": output}, value=value, meta={"scene_key": scene.scene_key})
        return PerformanceRender(output, plan, scene, plan.frame_count, self.fps, tuple(activity), body_motion,
                                 consistency, False)

    @staticmethod
    def _body_motion(stage: HeroStage, plan: PerformancePlan, samples: int = 8) -> float:
        """Mức chuyển động thân người (không tính miệng) giữa các khung mẫu — > 0 nghĩa là nhân vật đang diễn."""
        step = max(1, plan.frame_count // samples)
        layers = [stage.layer(plan.poses[index].with_mouth(0.0, 1.0)) for index in range(0, plan.frame_count, step)]
        diffs = [mean_abs_diff(a, b) for a, b in zip(layers, layers[1:])]
        return max(diffs) if diffs else 0.0

    @staticmethod
    def consistency_report(stage: HeroStage, poses: Sequence[RigPose], samples: int = 6) -> dict[str, Any]:
        """Kiểm chứng giữ nguyên nhân vật: tư thế nghỉ trùng từng pixel; phân bố màu ở các khung mẫu không đổi."""
        rest = stage.renderer.render(RigPose())
        identical = ImageChops.difference(rest, stage.scaled).getbbox() is None
        step = max(1, len(poses) // samples)
        similarities = [histogram_similarity(stage.scaled, stage.renderer.render(poses[index]))
                        for index in range(0, len(poses), step)]
        return {"rest_pose_identical": identical, "min_histogram_similarity": round(min(similarities), 4),
                "sampled_frames": len(similarities)}

    async def judge_frame(self, hero: HeroAsset, render: PerformanceRender, frame_index: int = 0) -> dict[str, Any]:
        """AI Đạo diễn so một khung hình với ảnh mẫu gốc (cùng nhân vật hay không)."""
        with Image.open(render.output_webp) as animation:
            animation.seek(min(frame_index, getattr(animation, "n_frames", 1) - 1))
            frame = animation.convert("RGB")
        return await self.vision.judge_consistency(hero.load_layer(), frame)
