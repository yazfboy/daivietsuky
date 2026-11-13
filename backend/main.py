"""Đại Việt Sử Ký — Phase 4: máy chủ FastAPI ghép nối toàn bộ backend với frontend (Vite + React).

Luồng một lượt hỏi–đáp (WebSocket ``/ws/chat/{character_id}`` hoặc SSE ``POST /api/chat/stream``):

1. ``scene`` (phase ``preview``)  — DirectorAgent.preview(): DP cache O(1) / quy tắc từ khoá → sân khấu đổi cảnh NGAY.
2. Song song trên 5 API key (``GeminiKeyPool`` — ThreadPoolExecutor + asyncio, Least-Connections + Token Bucket):
   * ``speech``  — ActorAgent stream từng cụm chữ xuống GlassChatBox; kèm khẩu hình ``visemes`` (TextToVisemeDPSync —
     Viterbi DP 60 FPS) khi ``mouthSyncEnabled``; cụm cuối ``is_final = True`` → miệng dừng ngay.
   * ``scene`` (phase ``final``) — DirectorAgent.direct(): AI Đạo diễn chốt ``sceneMode`` / ``mouthSyncEnabled`` / hậu cảnh.
3. ``backdrop`` — ảnh nền theo ``context_topic_key``: thư viện dựng sẵn → Wikimedia Commons (lọc nghiêm ngặt, ghi
   tác giả / giấy phép / link gốc) → phông trung tính. Bản này không dùng GPU tạo ảnh live.
4. ``storyboard`` — DP chia lời thoại thành phân cảnh (dp_scene_segmentation) + AI Đạo diễn viết beat cho từng cảnh.
5. ``done`` → ``turn_end``.

Backend KHÔNG giữ trạng thái phiên: frontend gửi kèm ``previousContextTopicKey`` (mã bối cảnh lượt trước) mỗi request.

Mọi lệnh gọi ra ngoài (Gemini, Hugging Face, Wikimedia) có timeout cứng ``EXTERNAL_TIMEOUT_S`` ≤ 8 giây.
Chạy: ``python -m uvicorn backend.main:app --port 8000`` (từ thư mục gốc dự án) hoặc ``python run_project.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agents.actor_agent import (
    CHARACTERS,
    SUPPORTED_CHARACTER_IDS,
    ActorAgent,
    CharacterProfile,
    SpeechChunk,
    UnsupportedCharacterError,
    get_character,
)
from backend.agents.director_agent import DirectorAgent, SceneDirective, Storyboard, TurnSummary
from backend.agents.history_rules import ERA_BOUNDARY_OPENING, clean_topic_key, detect_era_boundary
from backend.core.dp_optimizer import normalize_text
from backend.core.key_pool import DEFAULT_ENV_PATH, GeminiKeyPool, key_fingerprint, load_api_keys
from backend.core.scene_library import SceneLibrary
from backend.engine.visual_pipeline import (
    BackdropResolver,
    ConsistentHeroAnimationEngine,
    DiskDPCache,
    DirectorVision,
    HeroAsset,
    HeroModelInitializer,
    HuggingFaceImageSource,
    MouthAndActionController,
    TextToVisemeDPSync,
    WikimediaBackdropFinder,
    WikimediaCommonsSource,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "public"
ASSETS_DIR = PUBLIC_DIR / "assets"
HEROES_DIR = ASSETS_DIR / "heroes"

EXTERNAL_TIMEOUT_S = 8.0  # trần cứng cho mọi lệnh gọi ra ngoài
REQUEST_TIMEOUT_S = 7.0  # generateContent: chờ cả phản hồi; stream: chờ giữa hai cụm SSE sau byte đầu
# Stream: chờ byte đầu tối đa 3 s (model khoẻ trả sau ~1–1,5 s) rồi xoay ngay sang key/model khác. Mọi lần thử đều bị
# cắt theo phần còn lại của hạn chót EXTERNAL_TIMEOUT_S, nên cả lượt failover không bao giờ vượt 8 s.
FIRST_BYTE_TIMEOUT_S = 3.0
BACKDROP_WAIT_S = EXTERNAL_TIMEOUT_S  # chờ ảnh nền (thư viện / Wikimedia) tối đa trong một lượt
# Thứ tự model của AI Nhân vật cho máy chủ: chỉ các model đo được ~1–1,5 s tới chữ đầu. gemini-3.5-flash bị bỏ khỏi
# chuỗi vì đo được 6–8 s tới byte đầu và hay 503 "high demand" (không kịp trong hạn 8 s).
ACTOR_MODELS: tuple[str, ...] = ("gemini-3-flash-preview", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
# Nhịp phát lời thoại xuống frontend: Gemini thường đổ cả câu trả lời trong ~1–2 s (hoặc câu trả lời dự phòng ra cùng
# lúc), khiến miệng nhân vật không kịp cử động trước cụm cuối. Mỗi cụm cách cụm trước một khoảng tỷ lệ với thời lượng
# nói của nó (kẹp trong [MIN, MAX]) — chữ đầu tiên không bị trễ, sự kiện cảnh / ảnh nền không bị chặn.
SPEECH_PACE_RATIO = 0.15
SPEECH_GAP_MIN_S = 0.06
SPEECH_GAP_MAX_S = 0.25
PRODUCTION_ORIGINS = ("https://yazfboy.github.io",)
DEV_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)
# Render tự đặt biến RENDER=true → production chỉ nhận GitHub Pages; chạy ở máy thì nhận thêm localhost.
IS_PRODUCTION = os.environ.get("RENDER", "").strip().lower() in ("1", "true", "yes")
ALLOWED_ORIGINS = PRODUCTION_ORIGINS if IS_PRODUCTION else PRODUCTION_ORIGINS + DEV_ORIGINS
# Khi dev ở máy: nhận mọi cổng localhost / 127.0.0.1 (Vite có thể tự đổi cổng); production thì không.
DEV_ORIGIN_REGEX = None if IS_PRODUCTION else r"^http://(localhost|127\.0\.0\.1)(:\d{1,5})?$"


def origin_allowed(origin: str) -> bool:
    return origin in ALLOWED_ORIGINS or (DEV_ORIGIN_REGEX is not None and re.fullmatch(DEV_ORIGIN_REGEX, origin) is not None)

log = logging.getLogger("dai_viet_su_ky")


# ======================================================================================
# Dịch vụ dùng chung (khởi tạo một lần theo vòng đời ứng dụng)
# ======================================================================================
def load_api_keys_any() -> list[str]:
    """Nạp 5 API key: ưu tiên biến môi trường (khi deploy server — không có file .env), nếu không thì đọc ./.env.

    Biến môi trường (đặt bí mật trên Render / Hugging Face Space, KHÔNG commit):
      * ``GEMINI_API_KEYS`` = 5 key, phân tách bằng dấu phẩy / xuống dòng / khoảng trắng, hoặc
      * ``GEMINI_KEY_1`` … ``GEMINI_KEY_5``.
    """
    raw = os.environ.get("GEMINI_API_KEYS", "").strip()
    found: list[str] = []
    if raw:
        found = [part.strip() for part in re.split(r"[\s,]+", raw) if part.strip()]
    else:
        for index in range(1, 11):
            value = os.environ.get(f"GEMINI_KEY_{index}", "").strip()
            if value:
                found.append(value)
    if found:
        seen: set[str] = set()
        unique = [key for key in found if not (key in seen or seen.add(key))]
        log.info("Nạp %d API key từ biến môi trường.", len(unique))
        return unique
    return load_api_keys(DEFAULT_ENV_PATH)  # local: bắt buộc đúng 5 key trong ./.env


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def public_url(path: Path) -> str:
    return "/" + path.resolve().relative_to(PUBLIC_DIR.resolve()).as_posix()


@dataclass
class Services:
    pool: GeminiKeyPool
    actor: ActorAgent
    director: DirectorAgent
    engine: ConsistentHeroAnimationEngine
    viseme_sync: TextToVisemeDPSync
    backdrops: BackdropResolver
    key_fingerprints: tuple[str, ...]
    heroes: dict[str, HeroAsset] = field(default_factory=dict)
    hero_hashes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls) -> "Services":
        keys = load_api_keys_any()  # biến môi trường (server) hoặc ./.env (local) — 5 key Google AI Studio
        pool = GeminiKeyPool(keys, request_timeout_s=REQUEST_TIMEOUT_S, total_deadline_s=EXTERNAL_TIMEOUT_S,
                             first_byte_timeout_s=FIRST_BYTE_TIMEOUT_S)
        cache = DiskDPCache()
        hf = HuggingFaceImageSource(cache, timeout_s=EXTERNAL_TIMEOUT_S)
        wikimedia = WikimediaCommonsSource(cache, timeout_s=EXTERNAL_TIMEOUT_S, retries=0)
        vision = DirectorVision(pool, cache, request_timeout_s=REQUEST_TIMEOUT_S)
        initializer = HeroModelInitializer(pool, cache=cache, heroes_dir=HEROES_DIR, hf_source=hf,
                                           wikimedia=wikimedia, vision=vision)
        controller = MouthAndActionController()
        engine = ConsistentHeroAnimationEngine(pool, cache=cache, hf_source=hf, wikimedia=wikimedia, vision=vision,
                                               initializer=initializer, controller=controller)
        library = SceneLibrary.load()
        return cls(
            pool=pool,
            actor=ActorAgent(pool, models=ACTOR_MODELS, request_timeout_s=REQUEST_TIMEOUT_S),
            director=DirectorAgent(pool, request_timeout_s=REQUEST_TIMEOUT_S, library=library),
            engine=engine,
            viseme_sync=controller.sync,
            backdrops=BackdropResolver(library, WikimediaBackdropFinder(cache, timeout_s=EXTERNAL_TIMEOUT_S),
                                       timeout_s=EXTERNAL_TIMEOUT_S),
            key_fingerprints=tuple(key_fingerprint(key) for key in keys),
        )

    async def start(self) -> None:
        # Ảnh mẫu đã có sẵn → HeroModelInitializer chỉ ĐỌC (ảnh + rig), không vẽ lại, không ghi đè ảnh gốc.
        for character_id in SUPPORTED_CHARACTER_IDS:
            image_path = HEROES_DIR / f"{character_id}.png"
            if not image_path.is_file():
                raise RuntimeError(f"Thiếu ảnh mẫu anh hùng gốc: {image_path}")
            self.hero_hashes[character_id] = file_sha256(image_path)
        self.heroes = await self.engine.ensure_heroes()

    async def close(self) -> None:
        self.engine.close()
        self.pool.close()


# ======================================================================================
# Chuyển đổi dữ liệu sang hợp đồng frontend (types/hero.ts)
# ======================================================================================
def hero_summary(services: Services, profile: CharacterProfile) -> dict[str, Any]:
    cid = profile.character_id
    return {
        "id": cid,
        "name": profile.display_name,
        "fullName": profile.full_name,
        "titles": list(profile.titles),
        "era": profile.era,
        "birth": profile.birth,
        "death": profile.death,
        "homeland": profile.homeland,
        "heroImageUrl": f"/assets/heroes/{cid}.png",
        "rigUrl": f"/assets/heroes/{cid}.rig.json",
        "metaUrl": f"/assets/heroes/{cid}.meta.json",
        "imageSha256": services.hero_hashes.get(cid),
        "events": [
            {"id": event.event_id, "name": event.name, "year": event.year, "kind": event.kind}
            for event in profile.events
        ],
    }


def scene_payload(services: Services, directive: SceneDirective, phase: str) -> dict[str, Any]:
    profile = get_character(directive.character_id)
    asset = services.heroes.get(profile.character_id)
    rig_path = asset.rig_path if asset else HEROES_DIR / f"{profile.character_id}.rig.json"
    meta_path = asset.meta_path if asset else HEROES_DIR / f"{profile.character_id}.meta.json"
    return {
        "phase": phase,
        "stateId": directive.state_id,
        "characterId": profile.character_id,
        "sceneMode": directive.scene_mode,
        "mouthSyncEnabled": directive.mouth_sync_enabled,
        "characterReferencePath": directive.character_reference_path,
        "backgroundType": directive.background_type,
        "contextKey": directive.context_key,
        "contextTopicKey": directive.context_topic_key,
        "previousContextTopicKey": directive.previous_context_topic_key,
        "topicChanged": directive.topic_changed,
        "topicKind": directive.topic_kind,
        "questionKind": directive.question_kind,
        "beyondEra": directive.beyond_era,
        "contextTitle": directive.topic_title,
        "contextYear": directive.topic_year,
        "backgroundPrompt": directive.background_prompt,
        "characterAction": directive.character_action,
        "scenePrompt": directive.scene_prompt,
        "reason": directive.reason,
        "decidedBy": directive.decided_by,
        "rigData": read_json(rig_path),
        "metaData": read_json(meta_path),
    }


def chunk_payload(services: Services, chunk: SpeechChunk, mouth_sync: bool, source: str) -> dict[str, Any]:
    visemes: list[dict[str, Any]] = []
    if mouth_sync and chunk.text.strip():
        track = services.viseme_sync.sync([(chunk.text, float(chunk.speak_ms), float(chunk.pause_ms))])
        visemes = [
            {"viseme": seg.viseme, "start_ms": round(seg.start_ms, 1), "end_ms": round(min(seg.end_ms, track.speech_end_ms), 1)}
            for seg in track.segments
            if seg.start_ms < track.speech_end_ms
        ]
    return {
        "seq": chunk.seq,
        "text": chunk.text,
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
        "syllables": chunk.syllables,
        "speak_ms": chunk.speak_ms,
        "pause_ms": chunk.pause_ms,
        "boundary": chunk.boundary,
        "is_final": chunk.is_final,
        "character_id": chunk.character_id,
        "mouthSyncEnabled": mouth_sync,
        "visemes": visemes,
        "source": source,
        "model": chunk.model,
        "keyLabel": chunk.key_label,
    }


def storyboard_payload(storyboard: Storyboard) -> dict[str, Any]:
    return {
        "stateId": storyboard.state_id,
        "sceneMode": storyboard.scene_mode,
        "decidedBy": storyboard.decided_by,
        "segmentationCost": round(storyboard.segmentation_cost, 4),
        "beats": [beat.to_dict() for beat in storyboard.beats],
    }


def fallback_answer(director: DirectorAgent, profile: CharacterProfile, question: str) -> str:
    """Câu trả lời từ hồ sơ sử liệu khi AI Nhân vật không phản hồi trong thời hạn (5 key quá tải / mất mạng)."""
    fingerprint = director.analyze(profile.character_id, question)
    event = next((profile.event(eid) for eid in fingerprint.entities if profile.event(eid) is not None), None)
    pronoun = profile.self_pronoun.capitalize()
    boundary = detect_era_boundary(profile.character_id, question)
    if boundary is not None:  # Luật 2 vẫn áp dụng cả khi AI Nhân vật không phản hồi
        return (
            f"{ERA_BOUNDARY_OPENING} Sinh thời {profile.self_pronoun} chưa từng nghe tới {boundary.label}; "
            f"điều ấy {profile.self_pronoun} chỉ nghe hậu thế kể lại, nên không dám nói điều chưa tường. "
            f"{pronoun} xin kể với {profile.listener_address} những việc {profile.self_pronoun} đã trải qua. "
            f"{profile.key_facts[0]}"
        )
    if event is not None:
        return f"{event.name} ({event.year}). {event.summary}"
    text = normalize_text(question)
    parts = [f"{pronoun} là {profile.display_name}, tên thật {profile.full_name}, người {profile.era}."]
    if any(word in text for word in ("sinh", "mất", "qua đời", "năm nào", "bao nhiêu tuổi", "tạ thế")):
        parts.append(f"Về năm sinh: {profile.birth}.")
        parts.append(f"Về năm mất: {profile.death}.")
    else:
        parts.extend(fact.rstrip(".") + "." for fact in profile.key_facts[:3])
    return " ".join(parts)


# ======================================================================================
# Một lượt hỏi–đáp
# ======================================================================================
def speech_gap_s(speak_ms: float, pause_ms: float) -> float:
    """Khoảng cách tối thiểu (giây) trước khi phát cụm lời thoại kế tiếp."""
    return min(SPEECH_GAP_MAX_S, max(SPEECH_GAP_MIN_S, SPEECH_PACE_RATIO * (speak_ms + pause_ms) / 1000.0))


def clean_previous_key(value: Any) -> str | None:
    """Mã lượt trước do frontend gửi lên là dữ liệu không tin cậy: chỉ nhận đúng chuẩn ``^[a-z0-9_]+$``."""
    key = clean_topic_key(value) if isinstance(value, str) and len(value) <= 64 else ""
    return key or None


async def run_turn(
    services: Services,
    character_id: str,
    question: str,
    previous_context_topic_key: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Sinh chuỗi sự kiện JSON cho một lượt hỏi–đáp (dùng chung cho WebSocket và SSE)."""
    profile = get_character(character_id)
    previous = clean_previous_key(previous_context_topic_key)
    cleaned = " ".join(str(question or "").split())
    if not cleaned:
        yield {"type": "error", "agent": "server", "error": "Câu hỏi không được để trống."}
        yield {"type": "turn_end"}
        return
    started = time.perf_counter()

    def now_ms() -> float:
        return round((time.perf_counter() - started) * 1000.0, 1)

    preview = services.director.preview(profile.character_id, cleaned, previous)
    current: dict[str, SceneDirective] = {"directive": preview}
    yield {"type": "turn_start", "characterId": profile.character_id, "question": cleaned, "t_ms": now_ms()}
    yield {"type": "scene", "t_ms": now_ms(), "sceneData": scene_payload(services, preview, "preview")}

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    spoken: list[str] = []
    state = {"final_sent": False}
    backdrop: dict[str, asyncio.Task[dict[str, Any]]] = {}
    backdrop_deadline: list[float] = []
    backdrop_sent: list[bool] = []

    async def send_backdrop(directive: SceneDirective) -> None:
        try:
            result = await services.backdrops.resolve(directive)
        except Exception as exc:  # không bao giờ để lượt treo vì ảnh nền
            result = services.backdrops.neutral(directive, f"{type(exc).__name__}")
        backdrop_sent.append(True)
        await queue.put({"type": "backdrop", "t_ms": now_ms(), "stateId": directive.state_id, "backdrop": result})

    speech_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def speech_pacer() -> None:
        """Chuyển lời thoại sang hàng đợi chính theo nhịp nói (xem SPEECH_PACE_RATIO)."""
        loop = asyncio.get_running_loop()
        next_at = 0.0
        while True:
            item = await speech_queue.get()
            if item is None:
                return
            wait = next_at - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            item["t_ms"] = now_ms()
            await queue.put(item)
            chunk = item["chunk"]
            next_at = loop.time() + speech_gap_s(chunk["speak_ms"], chunk["pause_ms"])

    pacer_task = asyncio.create_task(speech_pacer(), name=f"speech:{profile.character_id}")

    async def flush_speech() -> None:
        """Chờ mọi cụm lời thoại đã xếp hàng được phát xong (trước ``done``)."""
        if not pacer_task.done():
            await speech_queue.put(None)
            await asyncio.gather(pacer_task, return_exceptions=True)

    async def emit_chunk(chunk: SpeechChunk, source: str) -> None:
        spoken.append(chunk.text)
        if chunk.is_final:
            state["final_sent"] = True
        mouth = current["directive"].mouth_sync_enabled
        await speech_queue.put({"type": "speech", "t_ms": now_ms(), "chunk": chunk_payload(services, chunk, mouth, source)})

    async def emit_fallback() -> None:
        text = fallback_answer(services.director, profile, cleaned)
        if spoken and "".join(spoken).strip():
            text = ""  # đã có một phần câu trả lời: chỉ cần chốt cụm cuối để miệng dừng
        for chunk in services.actor.chunk_text(profile.character_id, text):
            await emit_chunk(chunk, "offline")

    async def pump() -> None:
        try:
            async for event in services.director.run_scene(
                services.actor, profile.character_id, cleaned, previous_context_topic_key=previous
            ):
                if event.kind == "scene":
                    directive: SceneDirective = event.data
                    current["directive"] = directive
                    await queue.put({"type": "scene", "t_ms": now_ms(),
                                     "sceneData": scene_payload(services, directive, "final")})
                    if "task" not in backdrop:
                        backdrop["task"] = asyncio.create_task(send_backdrop(directive), name="backdrop")
                        backdrop_deadline.append(asyncio.get_running_loop().time() + BACKDROP_WAIT_S)
                elif event.kind == "speech":
                    await emit_chunk(event.data, "gemini")
                elif event.kind == "storyboard":
                    await queue.put({"type": "storyboard", "t_ms": now_ms(), "storyboard": storyboard_payload(event.data)})
                elif event.kind == "error":
                    data = event.data if isinstance(event.data, dict) else {"error": str(event.data)}
                    await queue.put({"type": "error", "t_ms": now_ms(), **data})
                    if data.get("agent") == "actor" and not state["final_sent"]:
                        await emit_fallback()
                elif event.kind == "done":
                    summary: TurnSummary = event.data
                    if not state["final_sent"]:
                        await emit_fallback()
                    await flush_speech()
                    directive = summary.directive or current["directive"]
                    await queue.put({
                        "type": "done",
                        "t_ms": now_ms(),
                        "answer": "".join(spoken),
                        "sceneMode": directive.scene_mode,
                        "mouthSyncEnabled": directive.mouth_sync_enabled,
                        "contextTopicKey": directive.context_topic_key,
                        "directorDecidedBy": directive.decided_by,
                        "actorModel": summary.actor_model,
                        "actorKey": summary.actor_key_label,
                        "firstSpeechMs": summary.first_speech_ms,
                        "sceneReadyMs": summary.scene_ready_ms,
                        "totalMs": summary.total_ms,
                        "errors": list(summary.errors),
                    })
        except Exception as exc:  # lỗi ngoài dự kiến: vẫn chốt lượt để giao diện không treo
            await queue.put({"type": "error", "t_ms": now_ms(), "agent": "server", "error": f"{type(exc).__name__}: {exc}"})
            if not state["final_sent"]:
                await emit_fallback()
        finally:
            await flush_speech()
            task = backdrop.get("task")
            if task is None:  # AI Đạo diễn không chốt được cảnh → ảnh nền theo bản xem trước
                task = asyncio.create_task(send_backdrop(current["directive"]), name="backdrop")
                backdrop_deadline.append(asyncio.get_running_loop().time() + BACKDROP_WAIT_S)
            if task is not None:
                remaining = max(0.0, backdrop_deadline[0] - asyncio.get_running_loop().time())
                await asyncio.wait({task}, timeout=remaining)
                if not backdrop_sent:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await queue.put({"type": "backdrop", "t_ms": now_ms(), "stateId": current["directive"].state_id,
                                     "backdrop": services.backdrops.neutral(current["directive"], "timeout")})
            await queue.put(None)

    pump_task = asyncio.create_task(pump(), name=f"turn:{profile.character_id}")
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        yield {"type": "turn_end", "t_ms": now_ms()}
    finally:
        for task in (pump_task, pacer_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(pump_task, pacer_task, return_exceptions=True)


# ======================================================================================
# Ứng dụng FastAPI
# ======================================================================================
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    services = Services.create()
    await services.start()
    app.state.services = services
    log.info("Đại Việt Sử Ký sẵn sàng: %d API key, %d anh hùng.", len(services.key_fingerprints), len(services.heroes))
    try:
        yield
    finally:
        await services.close()


app = FastAPI(title="Đại Việt Sử Ký API", version="4.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_origin_regex=DEV_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
# Ảnh mẫu anh hùng, rig, cảnh động do visual_pipeline sinh ra (frontend/public/assets/...).
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


def services_of(app_or_ws: FastAPI | WebSocket) -> Services:
    application = app_or_ws.app if isinstance(app_or_ws, WebSocket) else app_or_ws
    return application.state.services  # type: ignore[no-any-return]


class ChatRequest(BaseModel):
    character_id: str = Field(..., alias="characterId")
    question: str = Field(..., min_length=1, max_length=2000)
    previous_context_topic_key: str | None = Field(default=None, alias="previousContextTopicKey", max_length=64)

    model_config = {"populate_by_name": True}


@app.get("/health")
async def wake() -> dict[str, str]:
    """Trả ngay, không gọi Gemini — dùng để đánh thức server (Render free) trước buổi demo."""
    return {"status": "ok"}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    services = services_of(app)
    return {
        "status": "ok",
        "keys": len(services.key_fingerprints),
        "keyFingerprints": list(services.key_fingerprints),
        "heroes": sorted(services.heroes),
        "externalTimeoutS": EXTERNAL_TIMEOUT_S,
        "degradedModels": services.pool.degraded_models(),
    }


@app.get("/api/heroes")
async def list_heroes() -> dict[str, Any]:
    services = services_of(app)
    return {"heroes": [hero_summary(services, CHARACTERS[cid]) for cid in SUPPORTED_CHARACTER_IDS]}


@app.get("/api/heroes/{character_id}")
async def get_hero(character_id: str) -> dict[str, Any]:
    try:
        profile = get_character(character_id)
    except UnsupportedCharacterError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return hero_summary(services_of(app), profile)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """SSE: mỗi sự kiện là một dòng ``data: {json}``; kết thúc bằng sự kiện ``turn_end``."""
    try:
        profile = get_character(request.character_id)
    except UnsupportedCharacterError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    services = services_of(app)

    async def body() -> AsyncIterator[bytes]:
        async for event in run_turn(services, profile.character_id, request.question,
                                    request.previous_context_topic_key):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.websocket("/ws/chat/{character_id}")
async def chat_socket(websocket: WebSocket, character_id: str) -> None:
    """WebSocket: client gửi ``{"question": "..."}``; server đẩy các sự kiện của lượt rồi ``turn_end``."""
    origin = websocket.headers.get("origin")
    if origin is not None and not origin_allowed(origin):  # CORSMiddleware không áp cho WebSocket
        await websocket.close(code=4403)
        return
    try:
        profile = get_character(character_id)
    except UnsupportedCharacterError:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    services = services_of(websocket)
    try:
        while True:
            message = await websocket.receive_json()
            question = str(message.get("question", "")) if isinstance(message, dict) else ""
            previous = message.get("previousContextTopicKey") if isinstance(message, dict) else None
            async for event in run_turn(services, profile.character_id, question[:2000], previous):
                await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    except (RuntimeError, ValueError) as exc:  # socket đã đóng giữa chừng / JSON hỏng
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason=str(exc)[:120])


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
