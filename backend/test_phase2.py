"""Đại Việt Sử Ký — Phase 2 — Tự kiểm thử Visual & Animation Engine.

Chạy từ thư mục gốc dự án:
    python backend/test_phase2.py            # offline + live (Wikimedia, Hugging Face Spaces, Gemini 5 key)
    python backend/test_phase2.py --offline  # chỉ offline (ảnh tổng hợp, nguồn ảnh giả lập, không gọi mạng)

OFFLINE
  P1 DP Cache trên ổ cứng: O(1), bền vững qua lần chạy, tự loại mục mất file
  P2 DP nội suy khung hình: tối ưu (so vét cạn), đủ khung, vòng lặp liền, mượt C¹
  P3 Âm tiết tiếng Việt → viseme; số đọc thành chữ khớp số âm tiết của Phase 1
  P4 TextToVisemeDPSync: Viterbi tối ưu (so vét cạn), chống nháy, khớp chunk AI Nhân vật, ngậm miệng khi xong
  P5 Tách lớp nhân vật: chroma key nền xanh, loang màu nền phẳng, giữ alpha sẵn có
  P6 Rig 2.5D: tư thế nghỉ trùng từng pixel, nghịch đảo chính xác, bộ phận không liên quan không đổi
  P7 Cảnh nền động 60 FPS + ghi WebP: đúng số khung, đúng 60 FPS, mượt, liền vòng lặp, mặt nước gợn sóng
  P8 MouthAndActionController: FOREGROUND_TALKING ↔ miệng theo chữ; BACKGROUND_ACTION ↔ miệng tắt, thân diễn
  P9 Toàn bộ engine với nguồn giả lập: tạo 3 ảnh mẫu, cảnh động, DP Cache O(1)
LIVE
  TEST 1 AI tự tìm (Wikimedia Commons) + tự vẽ (Hugging Face ZeroGPU) đủ 3 ảnh mẫu vào frontend/public/assets/heroes/
  TEST 2 Cảnh quê hương & cảnh trận Bạch Đằng: giữ nguyên model anh hùng + ảnh động 60 FPS mượt
  TEST 3 Đứng trước màn hình: miệng cử động theo chữ, nói xong thì ngậm; đánh trận: không cử động miệng
"""

from __future__ import annotations

import argparse
import asyncio
import io
import itertools
import json
import math
import random
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageChops, ImageDraw  # noqa: E402

from backend.agents.actor_agent import SUPPORTED_CHARACTER_IDS, ActorAgent, PhraseChunker  # noqa: E402
from backend.agents.director_agent import DirectorAgent, SceneDirective  # noqa: E402
from backend.core.dp_optimizer import (  # noqa: E402
    DEFAULT_MS_PER_SYLLABLE,
    PAUSE_MS,
    SCENE_BACKGROUND_ACTION,
    SCENE_FOREGROUND_TALKING,
    count_syllables,
    vietnamese_number_syllables,
)
from backend.core.key_pool import GeminiKeyPool, load_api_keys  # noqa: E402
from backend.engine.visual_pipeline import (  # noqa: E402
    HEROES_DIR,
    HERO_IMAGE_SIZE,
    PORTRAIT_MIN_SCORE,
    VISEME_SHAPES,
    VISEMES,
    BackgroundAnimator,
    ConsistentHeroAnimationEngine,
    DiskDPCache,
    DPFrameInterpolator,
    GeneratedImage,
    HeroModelInitializer,
    HeroRig,
    HeroRigRenderer,
    HeroStage,
    KeyPose,
    MouthAndActionController,
    RigPose,
    TextToVisemeDPSync,
    WikimediaCommonsSource,
    WikimediaImage,
    _affine_inverse,
    alpha_bbox,
    build_rig,
    corners_transparent,
    alpha_coverage,
    histogram_similarity,
    image_to_png_bytes,
    inspect_animation,
    layout_from_analysis,
    make_cutout,
    mean_abs_diff,
    syllable_visemes,
    text_syllables,
    vietnamese_number_words,
    write_webp_animation,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except AttributeError:
    pass

QUESTION_BIO = "Ông sinh năm bao nhiêu mất năm nhiêu?"
QUESTION_BATTLE = "Trận Bạch Đằng diễn ra thế nào?"
FAKE_KEYS = [f"AQ.FakeTestKey{index}{'x' * 32}" for index in range(5)]
SAMPLE_SPEECH = "Ta sinh vào khoảng năm 1228, tại hương Tức Mặc. Ta mất năm 1300, ở Vạn Kiếp."


# --------------------------------------------------------------------------------------
class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def check(self, condition: bool, label: str, detail: str = "") -> bool:
        print(f"  [{'PASS' if condition else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
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


# --------------------------------------------------------------------------------------
# Ảnh tổng hợp & nguồn giả lập (offline)
# --------------------------------------------------------------------------------------
GEOMETRY = {  # toạ độ tương đối của nhân vật tổng hợp (x, y theo tỉ lệ khung)
    "head_top": (0.5, 0.11), "chin": (0.5, 0.31), "neck": (0.5, 0.34), "left_shoulder": (0.335, 0.375),
    "right_shoulder": (0.665, 0.375), "left_elbow": (0.29, 0.58), "right_elbow": (0.71, 0.58),
    "left_wrist": (0.29, 0.77), "right_wrist": (0.71, 0.77), "pelvis": (0.5, 0.80), "upper_lip": (0.5, 0.255),
    "lower_lip": (0.5, 0.268), "mouth_left_corner": (0.462, 0.262), "mouth_right_corner": (0.538, 0.262),
}


def synthetic_hero(width: int, height: int, seed: int = 0, background: tuple[int, int, int] = (60, 200, 40)) -> Image.Image:
    rng = random.Random(seed)
    robe = (170 + rng.randint(0, 40), 30 + rng.randint(0, 30), 30 + rng.randint(0, 30))
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    W, H = width, height
    draw.polygon([(0.33 * W, 0.36 * H), (0.67 * W, 0.36 * H), (0.74 * W, H), (0.26 * W, H)], fill=robe)
    draw.rectangle((0.24 * W, 0.37 * H, 0.33 * W, 0.78 * H), fill=(robe[0] - 20, robe[1], robe[2]))
    draw.rectangle((0.67 * W, 0.37 * H, 0.76 * W, 0.78 * H), fill=(robe[0] - 20, robe[1], robe[2]))
    draw.ellipse((0.40 * W, 0.11 * H, 0.60 * W, 0.31 * H), fill=(225, 185, 150))
    draw.ellipse((0.44 * W, 0.17 * H, 0.47 * W, 0.19 * H), fill=(30, 20, 20))
    draw.ellipse((0.53 * W, 0.17 * H, 0.56 * W, 0.19 * H), fill=(30, 20, 20))
    draw.polygon([(0.44 * W, 0.28 * H), (0.56 * W, 0.28 * H), (0.53 * W, 0.45 * H), (0.47 * W, 0.45 * H)], fill=(30, 25, 25))
    draw.line((0.462 * W, 0.262 * H, 0.538 * W, 0.262 * H), fill=(120, 40, 40), width=max(2, W // 150))
    draw.rectangle((0.42 * W, 0.04 * H, 0.58 * W, 0.11 * H), fill=(200, 160, 40))
    return image


def synthetic_rig_analysis() -> dict[str, Any]:
    return {
        "face_box": [110, 400, 310, 600],
        "mouth_box": [245, 450, 280, 550],
        "keypoints": [{"name": name, "point": [round(y * 1000), round(x * 1000)]} for name, (x, y) in GEOMETRY.items()],
    }


def synthetic_landscape(width: int, height: int, seed: int = 0) -> Image.Image:
    rng = random.Random(seed)
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / height
        draw.line((0, y, width, y), fill=(int(70 + 90 * t), int(120 + 50 * math.sin(6 * t)), int(210 - 140 * t)))
    for x in range(0, width, 16):
        h = int(0.42 * height + 25 * math.sin(x / 70.0 + seed))
        draw.line((x, h, x, int(0.6 * height)), fill=(40, 70 + rng.randint(0, 30), 50), width=16)
    for y in range(int(0.62 * height), height, 6):
        draw.line((0, y, width, y), fill=(30, 80 + (y % 24) * 3, 130 + (y % 18) * 4), width=3)
    draw.ellipse((0.62 * width, 0.5 * height, 0.75 * width, 0.7 * height), fill=(250, 150, 40))
    return image


class FakeHFSource:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, *, width: int, height: int, seed: int, negative_prompt: str = "",
                 spill: bool = True) -> GeneratedImage:
        self.calls += 1
        image = synthetic_hero(width, height, seed) if height > width else synthetic_landscape(width, height, seed)
        return GeneratedImage(image_to_png_bytes(image), width, height, "fake/space", seed, prompt, 0.01, False)

    async def generate_async(self, prompt: str, **kwargs: Any) -> GeneratedImage:
        return self.generate(prompt, **kwargs)

    def close(self) -> None:
        pass


class FakeVision:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def rank_references(self, profile: Any, images: Sequence[bytes]) -> dict[str, Any]:
        self.calls.append("rank")
        return {"candidates": [{"index": 0, "depicts_hero": True, "is_portrait": True, "score": 8}],
                "best_index": 0, "iconography": "a dignified general with a long black beard and red armor"}

    async def verify_portrait(self, profile: Any, image: Image.Image, look: str) -> dict[str, Any]:
        self.calls.append("verify")
        return {"single_person": True, "frontal": True, "body_framing_ok": True, "hands_ok": True,
                "mouth_visible_closed": True, "plain_background": True, "historical_costume": True,
                "matches_description": 8, "overall": 8, "issues": ""}

    async def analyze_rig(self, image: Image.Image) -> dict[str, Any]:
        self.calls.append("rig")
        return synthetic_rig_analysis()

    async def analyze_scene(self, image: Image.Image, scene_mode: str) -> dict[str, Any]:
        self.calls.append("scene")
        regions = [{"label": "sky", "box_2d": [0, 0, 400, 1000]}, {"label": "water", "box_2d": [620, 0, 1000, 1000]},
                   {"label": "vegetation", "box_2d": [400, 0, 600, 1000]}]
        if scene_mode == SCENE_BACKGROUND_ACTION:
            regions.append({"label": "fire", "box_2d": [500, 620, 700, 750]})
        return {"horizon_y": 420, "focus_point": [550, 680], "hero_anchor": [930, 270], "regions": regions}

    async def judge_consistency(self, reference: Image.Image, frame: Image.Image) -> dict[str, Any]:
        return {"same_character": True, "face_match": 9, "hair_beard_match": 9, "costume_match": 9, "notes": ""}


class FakeWikimedia:
    def search(self, query: str, *, limit: int = 8, thumb_width: int = 640) -> list[WikimediaImage]:
        return [WikimediaImage(f"File:{query}.jpg", "https://example.invalid/page", f"https://example.invalid/{query}.jpg",
                               800, 1000, "image/jpeg", "Public domain", "Test")]

    def download(self, image: WikimediaImage) -> bytes:
        buffer = io.BytesIO()
        synthetic_hero(160, 200, 7, background=(230, 230, 230)).save(buffer, "JPEG")
        return buffer.getvalue()


def make_directive(character_id: str, mode: str, context_key: str, question: str) -> SceneDirective:
    talking = mode == SCENE_FOREGROUND_TALKING
    return SceneDirective(
        state_id="vs-test", character_id=character_id, question=question, scene_mode=mode,
        mouth_sync_enabled=talking, character_reference_path=f"/assets/heroes/{character_id}.png",
        background_type="HOMELAND" if talking else "BATTLEFIELD", context_key=context_key,
        background_prompt="A misty river delta village at dawn." if talking else "Bạch Đằng River battle at ebb tide.",
        character_action="speaking" if talking else "commanding", scene_prompt="test", negative_prompt="",
        image_system_prompt="", reason="test", decided_by="test", cache_hit=False, cache_match=None,
        cache_similarity=None, key_label=None, model=None, latency_ms=0.0,
    )


def synthetic_stage(mode: str, out_size: tuple[int, int] = (480, 270)) -> tuple[HeroStage, HeroRig, Image.Image]:
    layer = make_cutout(synthetic_hero(448, 576, 1)).image
    rig = build_rig("tran_hung_dao", synthetic_rig_analysis(), 448, 576, alpha_bbox(layer))
    if mode == SCENE_FOREGROUND_TALKING:
        return HeroStage.for_talking(layer, rig, out_size), rig, layer
    return HeroStage.for_action(layer, rig, out_size, out_size[0] * 0.3), rig, layer


# --------------------------------------------------------------------------------------
# OFFLINE
# --------------------------------------------------------------------------------------
def test_p1_disk_cache(c: Checker) -> None:
    header("P1 · DP Cache trên ổ cứng (memoization bền vững, tra cứu O(1))")
    with tempfile.TemporaryDirectory() as tmp:
        cache = DiskDPCache(Path(tmp) / "cache")
        key_json = DiskDPCache.make_key("demo", {"q": "Trận Bạch Đằng"})
        cache.put_json(key_json, {"scene": "BACKGROUND_ACTION"})
        key_blob = DiskDPCache.make_key("blob", {"n": 1})
        path = cache.put_blob(key_blob, b"\x89PNG-test", suffix=".png")
        reopened = DiskDPCache(Path(tmp) / "cache")
        c.check(reopened.get_json(key_json) == {"scene": "BACKGROUND_ACTION"} and reopened.get_blob(key_blob) == b"\x89PNG-test",
                "Giá trị JSON và file nhị phân còn nguyên sau khi mở lại cache (bền vững qua lần chạy)")
        path.unlink()
        c.check(reopened.get_blob(key_blob) is None and key_blob not in reopened,
                "Mục có file bị xoá → tự loại khỏi chỉ mục (không trả dữ liệu hỏng)")
        timings = {}
        for size in (10, 20000):
            big = DiskDPCache(Path(tmp) / f"cache{size}")
            for index in range(size):
                big._index[f"k{index}"] = {"kind": "json", "value": index, "files": {}, "meta": {}, "created_at": 0}
            started = time.perf_counter()
            for index in range(0, size, max(1, size // 1000)):
                big.get(f"k{index}")
            timings[size] = (time.perf_counter() - started) / len(range(0, size, max(1, size // 1000))) * 1e6
        c.check(timings[20000] < 50 and timings[20000] < 5 * timings[10] + 5,
                "Thời gian tra cứu không phụ thuộc kích thước cache (O(1))",
                f"10 mục: {timings[10]:.2f} µs, 20 000 mục: {timings[20000]:.2f} µs")


def test_p2_dp_interpolation(c: Checker) -> None:
    header("P2 · DPFrameInterpolator (DP phân bổ khung hình + nội suy Hermite C¹)")
    interpolator = DPFrameInterpolator(60, quantum_frames=3, min_move_s=0.05, max_move_s=10.0, max_hold_s=2.0,
                                       hold_weight=4.0)
    keys = [KeyPose((0.0, 0.0), 0.1), KeyPose((3.0, 1.0), 0.0), KeyPose((1.0, -2.0), 0.2)]
    schedule = interpolator.schedule(keys, 36, loop=True)
    segments = interpolator._segments(len(keys), True)
    budget = 12
    best = math.inf
    ranges = [range(1, budget + 1) if kind == "move" else range(0, budget + 1) for kind, _, _ in segments]
    for counts in itertools.product(*ranges):
        if sum(counts) != budget:
            continue
        cost = sum(interpolator.segment_cost(kind, keys[i], keys[j], n) for (kind, i, j), n in zip(segments, counts))
        best = min(best, cost)
    c.check(abs(best - schedule.cost) < 1e-9, "Chi phí DP = tối ưu tìm bằng vét cạn mọi cách phân bổ",
            f"DP={schedule.cost:.6f}, vét cạn={best:.6f}")
    c.check(sum(s.frames for s in schedule.segments) == 36, "Tổng số khung đúng bằng yêu cầu")
    frames = interpolator.run(keys, 4.0, loop=True)
    c.check(len(frames) == 240, "4 giây @ 60 FPS → đúng 240 khung", str(len(frames)))
    steps = [math.dist(a, b) for a, b in zip(frames, frames[1:])]
    seam = math.dist(frames[-1], frames[0])
    c.check(seam <= max(steps) + 1e-9, "Vòng lặp liền mạch: bước nối khung cuối → khung đầu không lớn hơn bước thường",
            f"nối={seam:.4f}, bước lớn nhất={max(steps):.4f}")
    accelerations = [math.dist((a[0] - 2 * b[0] + d[0], a[1] - 2 * b[1] + d[1]), (0, 0))
                     for a, b, d in zip(frames, frames[1:], frames[2:])]
    c.check(max(accelerations) < 0.02, "Chuyển động mượt C¹ (gia tốc mỗi khung rất nhỏ, không giật)",
            f"max |Δ²| = {max(accelerations):.5f}")
    open_frames = interpolator.run(keys, 1.0, loop=False)
    c.check(open_frames[-1] == keys[-1].values and open_frames[0] == keys[0].values,
            "Không lặp: bắt đầu đúng tư thế đầu, kết thúc đúng tư thế cuối")


def test_p3_vietnamese(c: Checker) -> None:
    header("P3 · Âm tiết tiếng Việt → viseme; đọc số thành chữ")
    expectations = {
        "Bạch": ["MBP", "A", "REST"], "Đằng": ["A"], "quân": ["U", "A"], "giặc": ["A", "REST"], "gì": ["E"],
        "Đạo": ["A", "O"], "phải": ["FV", "A", "E"], "mũ": ["MBP", "U"], "người": ["E"], "Ô": ["O"],
    }
    wrong = {word: [v for v, _ in syllable_visemes(word)] for word, expected in expectations.items()
             if [v for v, _ in syllable_visemes(word)] != expected}
    c.check(not wrong, "Phân tích phụ âm đầu / vần / phụ âm cuối đúng cho các âm tiết mẫu", str(wrong) if wrong else "")
    c.check(all(abs(sum(share for _, share in syllable_visemes(w)) - 1.0) < 1e-9 for w in expectations),
            "Tỉ lệ thời lượng các viseme trong một âm tiết cộng lại = 1")
    c.check(vietnamese_number_words("1288") == ["một", "nghìn", "hai", "trăm", "tám", "mươi", "tám"]
            and vietnamese_number_words("1005") == ["một", "nghìn", "không", "trăm", "linh", "năm"]
            and vietnamese_number_words("21") == ["hai", "mươi", "mốt"] and vietnamese_number_words("15") == ["mười", "lăm"],
            "Đọc số đúng: 1288, 1005, 21, 15")
    mismatches = [n for n in range(0, 20001) if len(vietnamese_number_words(str(n))) != vietnamese_number_syllables(str(n))]
    c.check(not mismatches, "0..20000: số chữ đọc ra = số âm tiết Phase 1 dùng cho đồng hồ nói", str(mismatches[:5]))
    answer = ("Ta sinh vào khoảng năm 1228, tức niên hiệu Kiến Trung thứ tư. Năm 1288, ta cho đóng cọc gỗ dưới lòng sông "
              "Bạch Đằng, bắt sống Ô Mã Nhi cùng hơn 400 chiến thuyền.")
    c.check(len(text_syllables(answer)) == count_syllables(answer), "Số âm tiết dùng cho khẩu hình = số âm tiết của Phase 1",
            f"{len(text_syllables(answer))} âm tiết")


def _brute_force_viterbi(sync: TextToVisemeDPSync, targets: Sequence[str]) -> float:
    hold = sync.min_hold_frames
    index = {name: i for i, name in enumerate(VISEMES)}
    best = math.inf
    for sequence in itertools.product(range(len(VISEMES)), repeat=len(targets)):
        if sequence[0] != index["REST"] or sequence[-1] != index["REST"]:
            continue
        runs: list[int] = []
        for position, value in enumerate(sequence):
            if position and value == sequence[position - 1]:
                runs[-1] += 1
            else:
                runs.append(1)
        if any(length < hold for length in runs[1:-1]):
            continue
        cost = sum(sync._emission[index[t]][v] for t, v in zip(targets, sequence))
        cost += sum(sync._switch[a][b] for a, b in zip(sequence, sequence[1:]) if a != b)
        best = min(best, cost)
    return best


def test_p4_viseme_sync(c: Checker) -> None:
    header("P4 · TextToVisemeDPSync (Viterbi DP đồng bộ khẩu hình 60 FPS)")
    sync = TextToVisemeDPSync(60, min_hold_frames=2)
    for targets in (["REST", "A", "A", "MBP", "A", "REST"], ["REST", "O", "E", "E", "REST", "REST"],
                    ["REST", "A", "REST", "A", "A", "REST"]):
        path, cost = sync.viterbi(targets)
        brute = _brute_force_viterbi(sync, targets)
        c.check(abs(cost - brute) < 1e-9, f"Viterbi tối ưu toàn cục cho {targets}", f"DP={cost:.4f}, vét cạn={brute:.4f}")
    sync = TextToVisemeDPSync(60)
    track = sync.sync(SAMPLE_SPEECH)
    frame_ms = 1000.0 / 60
    c.check(len(track.frames) == math.ceil(track.duration_ms / frame_ms - 1e-9), "Mỗi khung 60 FPS có một trạng thái miệng",
            f"{len(track.frames)} khung, {track.duration_ms:.0f} ms")
    chunker = PhraseChunker(6)
    phrases = chunker.feed(SAMPLE_SPEECH) + chunker.flush()
    pieces = [(p.text, count_syllables(p.text) * DEFAULT_MS_PER_SYLLABLE, PAUSE_MS.get(p.boundary, 0) if p.text.strip() else 0)
              for p in phrases]
    chunk_track = sync.sync(pieces)
    c.check(abs(chunk_track.speech_end_ms - track.speech_end_ms) < 1e-6 and chunk_track.openness() == track.openness(),
            "Khớp đúng đồng hồ nói của từng cụm chữ AI Nhân vật stream (speak_ms/pause_ms)")
    inner = [s for s in track.segments[1:-1]]
    c.check(all(round((s.end_ms - s.start_ms) / frame_ms) >= sync.min_hold_frames for s in inner),
            "Không nháy miệng: mọi hình miệng giữ ≥ 3 khung (50 ms)")
    c.check(track.dp_switches <= track.target_switches, "DP làm mượt: số lần đổi hình miệng ≤ chuỗi mục tiêu thô",
            f"{track.dp_switches} ≤ {track.target_switches}")
    speaking = [f.openness for f in track.frames if f.t_ms < track.speech_end_ms]
    after = [f.openness for f in track.frames if f.t_ms > track.speech_end_ms + 200]
    c.check(sum(speaking) / len(speaking) > 0.2 and max(speaking) > 0.6, "Đang nói: miệng mở theo nguyên âm",
            f"độ mở TB={sum(speaking) / len(speaking):.2f}, max={max(speaking):.2f}")
    c.check(after and all(value == 0.0 for value in after) and track.frames[-1].viseme == "REST",
            "Nói xong: miệng khép hẳn (độ mở = 0) và dừng ở REST")
    pause_windows: list[tuple[float, float]] = []
    clock = 0.0
    for _, speak, pause in pieces:
        if pause >= 300:  # quãng nghỉ cuối câu (dấu chấm) = 380 ms
            pause_windows.append((clock + speak, clock + speak + pause))
        clock += speak + pause
    lowest = [min(f.openness for f in track.frames if start <= f.t_ms < end) for start, end in pause_windows]
    c.check(len(pause_windows) == 2 and all(value < 0.15 for value in lowest),
            "Ngắt câu (dấu chấm) → miệng khép lại trong quãng nghỉ",
            f"độ mở nhỏ nhất trong {len(pause_windows)} quãng nghỉ: {[round(v, 2) for v in lowest]}")
    numbers = sync.sync("Năm 1288.")
    c.check(len(numbers.syllable_onsets_ms) == 1 + 7, "Số được đọc thành chữ khi tạo khẩu hình (1288 → 7 âm tiết)")


def test_p5_cutout(c: Checker) -> None:
    header("P5 · Tách lớp nhân vật (chroma key / loang màu / alpha sẵn có)")
    green = synthetic_hero(448, 576, 2)
    chroma = make_cutout(green)
    c.check(chroma.method == "chroma" and chroma.corners_transparent and 0.2 < chroma.coverage < 0.6,
            "Nền xanh → chroma key: góc trong suốt, phủ hợp lý", f"{chroma.method}, phủ {chroma.coverage:.3f}")
    robe_pixel = chroma.image.getpixel((224, 450))
    c.check(robe_pixel[3] == 255 and robe_pixel[:3] == green.getpixel((224, 450)), "Pixel nhân vật giữ nguyên màu & đục hoàn toàn",
            str(robe_pixel))
    white = synthetic_hero(448, 576, 3, background=(236, 236, 236))
    flood = make_cutout(white)
    c.check(flood.method == "flood" and flood.corners_transparent and 0.2 < flood.coverage < 0.6,
            "Nền phẳng màu khác (ảnh người dùng tự đưa) → loang màu từ mép ảnh", f"{flood.method}, phủ {flood.coverage:.3f}")
    again = make_cutout(chroma.image)
    c.check(again.method == "alpha" and ImageChops.difference(again.image, chroma.image).getbbox() is None,
            "Ảnh đã có alpha → giữ nguyên, không xử lý lại")


def test_p6_rig(c: Checker) -> None:
    header("P6 · Rig 2.5D trên chính ảnh gốc (giữ nguyên 100% nhân vật)")
    layer = make_cutout(synthetic_hero(448, 576, 4)).image
    rig = build_rig("tran_hung_dao", synthetic_rig_analysis(), 448, 576, alpha_bbox(layer))
    renderer = HeroRigRenderer(layer, rig)
    c.check(ImageChops.difference(renderer.render(RigPose()), layer).getbbox() is None,
            "Tư thế nghỉ → trùng TỪNG PIXEL với ảnh gốc")
    pose = RigPose(head_tilt_deg=5.0, upper_r_deg=22.0, lower_r_deg=12.0, lean_deg=-3.0, jaw_open=0.8)
    matrices = renderer.bone_matrices(pose)
    inverses = {name: _affine_inverse(m) for name, m in matrices.items()}
    worst = 0.0
    for x, y in [(224, 90), (224, 152), (300, 300), (318, 420), (200, 520), (140, 260), (224, 160)]:
        fx, fy = renderer.forward(pose, x, y, matrices)
        bx, by = renderer.inverse(pose, fx, fy, inverses, matrices)
        worst = max(worst, math.hypot(bx - x, by - y))
    c.check(worst < 1.0, "Nghịch đảo LBS chính xác (kể cả tay đã giơ, hàm đã mở)", f"sai số lớn nhất {worst:.3f} px")
    tilted = renderer.render(RigPose(head_tilt_deg=4.0))
    bottom = (0, int(0.6 * 576), 448, 576)
    c.check(ImageChops.difference(tilted.crop(bottom), layer.crop(bottom)).getbbox() is None,
            "Nghiêng đầu không làm thay đổi phần thân dưới")
    jaw = renderer.render(RigPose(jaw_open=1.0))
    changed = ImageChops.difference(jaw, layer).getbbox()
    mouth_y = rig.lip_y
    c.check(changed is not None and changed[1] >= mouth_y - 0.25 * rig.face_h and changed[3] <= rig.joints["chin"][1] + rig.face_h,
            "Mở miệng chỉ thay đổi vùng miệng–cằm", f"vùng đổi {changed}")
    action = renderer.render(RigPose(upper_r_deg=22.0, lower_r_deg=12.0, lean_deg=-3.0))
    similarity = min(histogram_similarity(layer, img) for img in (tilted, jaw, action))
    c.check(similarity >= 0.97, "Phân bố màu nhân vật gần như không đổi ở mọi tư thế (không vẽ lại)", f"{similarity:.4f}")
    torso_center = (224, 400)
    c.check(action.getpixel(torso_center)[3] == 255, "Giơ tay không làm thủng thân (lớp thân phía sau tay lấp chỗ trống)")
    mirrored = RigPose(upper_r_deg=20.0, lean_deg=-3.0, body_dx=0.02).mirrored()
    c.check(mirrored.upper_l_deg == -20.0 and mirrored.lean_deg == 3.0 and mirrored.body_dx == -0.02,
            "Đối xứng động tác khi nhân vật đứng bên phải cảnh")


def test_p7_background(c: Checker) -> None:
    header("P7 · Cảnh nền động 60 FPS + ghi WebP (render song song, bộ nhớ thấp)")
    background = synthetic_landscape(1280, 720, 5)
    layout = layout_from_analysis({"horizon_y": 420, "focus_point": [550, 680], "hero_anchor": [930, 270], "regions": [
        {"label": "sky", "box_2d": [0, 0, 400, 1000]}, {"label": "water", "box_2d": [620, 0, 1000, 1000]},
        {"label": "fire", "box_2d": [500, 620, 700, 750]}]}, 1280, 720, SCENE_BACKGROUND_ACTION)
    animator = BackgroundAnimator(background, layout, scene_mode=SCENE_BACKGROUND_ACTION, out_size=(480, 270),
                                  fps=60, loop_s=2.0, seed=1)
    c.check(animator.frame_count == 120, "2 giây @ 60 FPS → 120 khung")
    quarter = animator.frame_count // 4
    water_box, sky_box = (0, 200, 480, 268), (0, 0, 480, 60)
    first, later = animator.frame(0), animator.frame(quarter)
    water = mean_abs_diff(first.crop(water_box), later.crop(water_box))
    sky = mean_abs_diff(first.crop(sky_box), later.crop(sky_box))
    c.check(water > sky, "Mặt nước (gần) chuyển động nhiều hơn bầu trời (xa) — parallax + sóng nước",
            f"nước {water:.2f} > trời {sky:.2f}")
    c.check(ImageChops.difference(animator.frame(0), animator.frame(animator.frame_count)).getbbox() is None,
            "Khung N trùng khung 0 → vòng lặp liền mạch")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "loop.webp"
        started = time.perf_counter()
        write_webp_animation(out, animator.frame_count, animator.frame, fps=60)
        elapsed = time.perf_counter() - started
        info = inspect_animation(out)
        c.check(info.frame_count == 120 and abs(info.fps - 60.0) < 0.05 and sum(info.durations_ms) == 2000 and info.loop == 0,
                "WebP động: đúng 120 khung, 60 FPS, tổng 2000 ms, lặp vô hạn",
                f"{info.frame_count} khung, {info.fps:.2f} FPS, {elapsed:.1f} s, {out.stat().st_size // 1024} KB")
        c.check(info.max_step_diff < 6.0 and info.loop_seam_diff <= 2 * info.mean_step_diff + 1.0,
                "Mượt: không có bước nhảy giữa hai khung liên tiếp, kể cả chỗ nối vòng lặp",
                f"bước TB {info.mean_step_diff:.2f}, max {info.max_step_diff:.2f}, nối {info.loop_seam_diff:.2f}")


def test_p8_controller(c: Checker) -> None:
    header("P8 · MouthAndActionController (miệng theo chữ ↔ miệng tắt khi hành động)")
    controller = MouthAndActionController(fps=60)
    talk = make_directive("tran_hung_dao", SCENE_FOREGROUND_TALKING, "homeland", QUESTION_BIO)
    battle = make_directive("tran_hung_dao", SCENE_BACKGROUND_ACTION, "bach_dang_1288", QUESTION_BATTLE)
    plan = controller.plan(talk, SAMPLE_SPEECH)
    jaw = plan.jaw_series()
    tail = [value for pose, value in zip(plan.poses, jaw)][-int(0.25 * 60):]
    c.check(plan.mouth_sync_enabled and max(jaw) > 0.6 and all(value == 0.0 for value in tail),
            "FOREGROUND_TALKING: miệng mở theo lời thoại, cuối lời khép hẳn", f"max={max(jaw):.2f}")
    c.check(plan.frame_count == len(plan.mouth_track.frames), "Số khung biểu diễn = số khung khẩu hình")
    action_plan = controller.plan(battle, SAMPLE_SPEECH)
    c.check(not action_plan.mouth_sync_enabled and all(value == 0.0 for value in action_plan.jaw_series()),
            "BACKGROUND_ACTION: mouth_sync TẮT — độ mở hàm = 0 ở mọi khung, kể cả khi có lời thoại")
    muted = controller.plan(SceneDirective(**{**talk.__dict__, "mouth_sync_enabled": False}), SAMPLE_SPEECH)
    c.check(all(value == 0.0 for value in muted.jaw_series()), "mouth_sync_enabled=False luôn được tôn trọng")
    stage, _, _ = synthetic_stage(SCENE_FOREGROUND_TALKING)
    activity = controller.mouth_activity(stage, plan, step=3)
    speaking = [value for index, value in activity if index * 1000 / 60 < plan.speech_end_ms]
    ending = [value for index, value in activity if index * 1000 / 60 > plan.speech_end_ms + 200]
    c.check(sum(1 for v in speaking if v > 1.0) >= 0.4 * len(speaking) and all(v == 0.0 for v in ending),
            "Render: vùng miệng thay đổi trong lúc nói và đứng yên sau khi nói xong",
            f"{sum(1 for v in speaking if v > 1.0)}/{len(speaking)} khung có miệng cử động")
    action_stage, _, _ = synthetic_stage(SCENE_BACKGROUND_ACTION)
    action_activity = controller.mouth_activity(action_stage, action_plan, step=6)
    c.check(all(value == 0.0 for _, value in action_activity), "Render hành động: vùng miệng không có khẩu hình")
    motion = ConsistentHeroAnimationEngine._body_motion(action_stage, action_plan)
    c.check(motion > 0.5, "Render hành động: thân người chuyển động (chỉ huy, xông lên)", f"{motion:.2f}")


async def test_p9_engine_offline(c: Checker) -> None:
    header("P9 · Toàn bộ engine với nguồn giả lập (3 ảnh mẫu → cảnh động 60 FPS → DP Cache O(1))")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = DiskDPCache(root / "cache")
        fake_hf, vision, wikimedia = FakeHFSource(), FakeVision(), FakeWikimedia()
        pool = GeminiKeyPool(FAKE_KEYS)
        initializer = HeroModelInitializer(pool, cache=cache, heroes_dir=root / "heroes", hf_source=fake_hf,  # type: ignore[arg-type]
                                           wikimedia=wikimedia, vision=vision, image_size=(448, 576))  # type: ignore[arg-type]
        engine = ConsistentHeroAnimationEngine(pool, cache=cache, hf_source=fake_hf, wikimedia=wikimedia,  # type: ignore[arg-type]
                                               vision=vision, initializer=initializer, scenes_dir=root / "scenes",  # type: ignore[arg-type]
                                               renders_dir=root / "renders", out_size=(480, 270), loop_s=1.0)
        try:
            c.check(initializer.missing() == list(SUPPORTED_CHARACTER_IDS), "Ban đầu thiếu cả 3 ảnh mẫu")
            heroes = await engine.ensure_heroes()
            files_ok = all((root / "heroes" / f"{cid}{suffix}").is_file() for cid in SUPPORTED_CHARACTER_IDS
                           for suffix in (".png", ".rig.json", ".meta.json"))
            c.check(files_ok and initializer.missing() == [], "Tạo đủ {id}.png + {id}.rig.json + {id}.meta.json cho 3 vị")
            meta = json.loads((root / "heroes" / "tran_hung_dao.meta.json").read_text(encoding="utf-8"))
            c.check(meta["source"] == "huggingface:fake/space" and meta["wikimedia_reference"] is not None,
                    "Ghi nguồn gốc: ảnh chuẩn Wikimedia + Space đã vẽ")
            calls = fake_hf.calls
            await engine.ensure_heroes()
            c.check(fake_hf.calls == calls, "Ảnh mẫu đã có → không vẽ lại")
            talk = make_directive("tran_hung_dao", SCENE_FOREGROUND_TALKING, "homeland", QUESTION_BIO)
            battle = make_directive("tran_hung_dao", SCENE_BACKGROUND_ACTION, "bach_dang_1288", QUESTION_BATTLE)
            scene_talk = await engine.prepare_scene(talk)
            scene_battle = await engine.prepare_scene(battle)
            info_talk, info_battle = inspect_animation(scene_talk.animation_webp), inspect_animation(scene_battle.animation_webp)
            c.check(info_talk.frame_count == 60 and info_battle.frame_count == 60 and abs(info_battle.fps - 60) < 0.05,
                    "Cảnh nói & cảnh hành động xuất WebP 60 FPS",
                    f"{scene_talk.animation_webp.name}, {scene_battle.animation_webp.name}")
            consistency = scene_battle.manifest["consistency"]
            c.check(consistency["rest_pose_identical"] and consistency["min_histogram_similarity"] >= 0.97,
                    "Cảnh hành động giữ nguyên nhân vật", str(consistency))
            calls = fake_hf.calls
            reused = await engine.prepare_scene(dataclass_replace(talk, question="Quê quán của ông ở đâu?"))
            c.check(reused.from_cache and fake_hf.calls == calls and reused.animation_webp == scene_talk.animation_webp,
                    "Câu hỏi cùng bối cảnh → dùng lại cảnh từ DP Cache, không vẽ lại")
            fresh_engine = ConsistentHeroAnimationEngine(pool, cache=DiskDPCache(root / "cache"), hf_source=fake_hf,  # type: ignore[arg-type]
                                                         wikimedia=wikimedia, vision=vision, initializer=initializer,  # type: ignore[arg-type]
                                                         scenes_dir=root / "scenes", renders_dir=root / "renders",
                                                         out_size=(480, 270), loop_s=1.0)
            again = await fresh_engine.prepare_scene(battle)
            c.check(again.from_cache and fake_hf.calls == calls, "Khởi động lại tiến trình vẫn dùng lại cảnh (cache trên đĩa)")
            performance = await engine.render_performance(talk, SAMPLE_SPEECH)
            speaking = [v for i, v in performance.mouth_activity if i * 1000 / 60 < performance.plan.speech_end_ms]
            ending = [v for i, v in performance.mouth_activity if i * 1000 / 60 > performance.plan.speech_end_ms + 200]
            c.check(max(speaking) > 1.0 and all(v == 0.0 for v in ending), "Bản xem trước nói chuyện: miệng động theo chữ rồi dừng")
            action = await engine.render_performance(battle, SAMPLE_SPEECH)
            c.check(all(v == 0.0 for _, v in action.mouth_activity) and action.body_motion > 0.5,
                    "Bản xem trước hành động: miệng không động, thân người diễn")
        finally:
            engine.close()
            pool.close()


def dataclass_replace(directive: SceneDirective, **changes: Any) -> SceneDirective:
    return SceneDirective(**{**directive.__dict__, **changes})


# --------------------------------------------------------------------------------------
# LIVE
# --------------------------------------------------------------------------------------
async def live_tests(c: Checker) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    keys = load_api_keys()
    pool = GeminiKeyPool(keys)
    engine = ConsistentHeroAnimationEngine(pool)
    actor, director = ActorAgent(pool), DirectorAgent(pool)
    try:
        header("TEST 1 · AI tự tìm (Wikimedia) + tự vẽ (Hugging Face) đủ 3 ảnh mẫu vào frontend/public/assets/heroes/")
        missing = engine.initializer.missing()
        print(f"    Thiếu trước khi chạy: {missing or 'không'}")
        started = time.perf_counter()
        heroes = await engine.ensure_heroes()
        print(f"    Hoàn tất sau {time.perf_counter() - started:.1f} s")
        for line in engine.initializer.log:
            print(f"    · {line}")
        for cid in SUPPORTED_CHARACTER_IDS:
            asset = heroes[cid]
            path = HEROES_DIR / f"{cid}.png"
            with Image.open(path) as image:
                image.load()
                mode, size = image.mode, image.size
                alpha = image.convert("RGBA").getchannel("A")
            meta = json.loads(asset.meta_path.read_text(encoding="utf-8"))
            reference = meta.get("wikimedia_reference") or {}
            generation = meta.get("generation") or {}
            print(f"    {cid}: {path.relative_to(PROJECT_ROOT)} {size} {mode} · nguồn: {meta.get('source')} · "
                  f"điểm AI: {generation.get('score', '-')}")
            if reference:
                print(f"       ảnh chuẩn Wikimedia: {reference.get('title')} ({reference.get('license')}) — {reference.get('page_url')}")
            c.check(path.is_file() and mode == "RGBA", f"{cid}.png nằm đúng thư mục, định dạng PNG RGBA")
            c.check(corners_transparent(alpha) and 0.08 <= alpha_coverage(alpha) <= 0.85,
                    f"{cid}: đã tách lớp nhân vật (nền trong suốt)", f"phủ {alpha_coverage(alpha):.2f}")
            c.check(asset.rig_path.is_file() and asset.rig.width == size[0] and asset.rig.source == "gemini",
                    f"{cid}: AI Đạo diễn dò khung xương → {cid}.rig.json")
            produced_by_ai = str(meta.get("source", "")).startswith(("huggingface:", "wikimedia"))
            c.check(produced_by_ai or cid not in missing, f"{cid}: ảnh do AI tự vẽ / tự tìm (ghi trong {cid}.meta.json)",
                    str(meta.get("source")))
            if str(meta.get("source", "")).startswith("huggingface:"):
                c.check(float(generation.get("score", 0)) >= PORTRAIT_MIN_SCORE and size == HERO_IMAGE_SIZE,
                        f"{cid}: AI Đạo diễn kiểm định đạt", f"điểm {generation.get('score')}")
        outputs["heroes"] = heroes

        header("TEST 2 · Cảnh quê hương & cảnh trận Bạch Đằng: giữ nguyên model anh hùng + ảnh động 60 FPS mượt")
        bio = await director.direct("tran_hung_dao", QUESTION_BIO)
        battle = await director.direct("tran_hung_dao", QUESTION_BATTLE)
        print(f"    AI Đạo diễn: \"{QUESTION_BIO}\" → {bio.scene_mode} ({bio.decided_by}); "
              f"\"{QUESTION_BATTLE}\" → {battle.scene_mode}/{battle.context_key} ({battle.decided_by})")
        c.check(bio.scene_mode == SCENE_FOREGROUND_TALKING and battle.scene_mode == SCENE_BACKGROUND_ACTION,
                "Chỉ thị cảnh từ AI Đạo diễn Phase 1 đúng chế độ")
        for label, directive in (("quê hương", bio), ("trận Bạch Đằng", battle)):
            started = time.perf_counter()
            scene = await engine.prepare_scene(directive)
            elapsed = time.perf_counter() - started
            info = inspect_animation(scene.animation_webp)
            print(f"    Cảnh {label}: {scene.animation_webp.relative_to(PROJECT_ROOT)} · nền vẽ bởi {scene.background_source} · "
                  f"{info.frame_count} khung · {info.fps:.2f} FPS · {scene.animation_webp.stat().st_size / 1e6:.1f} MB · "
                  f"{elapsed:.1f} s{' (DP Cache)' if scene.from_cache else ''}")
            print(f"       vùng động AI khoanh: {', '.join(f'{k}×{len(v)}' for k, v in scene.layout.regions.items()) or 'không'}")
            c.check(info.frame_count == 240 and abs(info.fps - 60.0) < 0.05 and info.loop == 0,
                    f"Cảnh {label}: ảnh động WebP 60 FPS, vòng lặp 4 s (240 khung)")
            c.check(info.max_step_diff < 8.0 and info.loop_seam_diff <= 2 * info.mean_step_diff + 1.5,
                    f"Cảnh {label}: chuyển động mượt, không giật, nối vòng lặp liền",
                    f"bước TB {info.mean_step_diff:.2f}, max {info.max_step_diff:.2f}, nối {info.loop_seam_diff:.2f}")
            outputs[f"scene_{directive.scene_mode}"] = scene
        battle_scene = outputs[f"scene_{SCENE_BACKGROUND_ACTION}"]
        consistency = battle_scene.manifest["consistency"]
        c.check(consistency["rest_pose_identical"] and consistency["min_histogram_similarity"] >= 0.97,
                "Cảnh trận Bạch Đằng: nhân vật là chính ảnh gốc (tư thế nghỉ trùng từng pixel, màu không đổi)",
                str(consistency))
        with Image.open(battle_scene.animation_webp) as animation:
            animation.seek(80)
            frame = animation.convert("RGB")
        verdict = await engine.vision.judge_consistency(heroes["tran_hung_dao"].load_layer(), frame)
        print(f"    AI Đạo diễn so khung #80 cảnh trận với ảnh mẫu: {verdict}")
        c.check(verdict.get("same_character") is True and min(verdict.get("face_match", 0), verdict.get("costume_match", 0)) >= 7,
                "AI Đạo diễn xác nhận: nhân vật trong cảnh trận đánh CHÍNH LÀ model anh hùng cũ")

        header("TEST 3 · Đứng trước màn hình: miệng cử động theo chữ · Đánh trận: không cử động miệng")
        answer = await actor.answer("tran_hung_dao", QUESTION_BIO)
        print(f"    AI Nhân vật: {answer.text[:160]}…")
        started = time.perf_counter()
        talk = await engine.render_performance(bio, answer.chunks, max_duration_ms=7000)
        print(f"    Bản nói chuyện: {talk.output_webp.relative_to(PROJECT_ROOT)} · {talk.frame_count} khung @ {talk.fps} FPS · "
              f"{time.perf_counter() - started:.1f} s")
        plan = talk.plan
        speaking = [v for i, v in talk.mouth_activity if i * 1000 / 60 < plan.speech_end_ms]
        ending = [v for i, v in talk.mouth_activity if i * 1000 / 60 > plan.speech_end_ms + 200]
        c.check(plan.mouth_sync_enabled and plan.mouth_track is not None, "FOREGROUND_TALKING → bật mouth_sync, có khẩu hình DP")
        c.check(sum(1 for v in speaking if v > 1.0) >= 0.4 * len(speaking),
                "Miệng cử động trong lúc nói (khớp nhịp từng âm tiết)",
                f"{sum(1 for v in speaking if v > 1.0)}/{len(speaking)} khung mẫu có miệng động")
        c.check(bool(ending) and all(v == 0.0 for v in ending) and plan.jaw_series()[-1] == 0.0,
                "Trả lời xong → miệng dừng cử động", f"{len(ending)} khung cuối đứng yên")
        talk_info = inspect_animation(talk.output_webp)
        c.check(talk_info.frame_count == talk.frame_count and abs(talk_info.fps - 60) < 0.05,
                "Bản nói chuyện là ảnh động 60 FPS", f"{talk_info.frame_count} khung")
        c.check(talk.consistency["rest_pose_identical"] and talk.consistency["min_histogram_similarity"] >= 0.97,
                "Bản nói chuyện giữ nguyên nhân vật", str(talk.consistency))
        battle_answer = await actor.answer("tran_hung_dao", QUESTION_BATTLE)
        started = time.perf_counter()
        action = await engine.render_performance(battle, battle_answer.chunks, max_duration_ms=4000)
        print(f"    Bản hành động: {action.output_webp.relative_to(PROJECT_ROOT)} · {action.frame_count} khung · "
              f"{time.perf_counter() - started:.1f} s")
        c.check(not action.plan.mouth_sync_enabled and all(v == 0.0 for v in action.plan.jaw_series()),
                "BACKGROUND_ACTION → mouth_sync = False, độ mở hàm = 0 ở mọi khung")
        c.check(all(v == 0.0 for _, v in action.mouth_activity), "Vùng miệng không có khẩu hình trong cảnh đánh trận")
        c.check(action.body_motion > 0.5, "Nhân vật thực hiện animation hành động (chỉ huy, xông lên)",
                f"chuyển động thân {action.body_motion:.2f}")
        outputs["talk"], outputs["action"] = talk, action
    finally:
        engine.close()
        pool.close()
    return outputs


# --------------------------------------------------------------------------------------
async def main_async(run_live: bool) -> Checker:
    c = Checker()
    sections: list[tuple[str, Callable[[Checker], Any]]] = [
        ("P1", test_p1_disk_cache), ("P2", test_p2_dp_interpolation), ("P3", test_p3_vietnamese),
        ("P4", test_p4_viseme_sync), ("P5", test_p5_cutout), ("P6", test_p6_rig), ("P7", test_p7_background),
        ("P8", test_p8_controller), ("P9", test_p9_engine_offline),
    ]
    if run_live:
        sections.append(("LIVE", live_tests))
    for name, function in sections:
        try:
            outcome = function(c)
            if asyncio.iscoroutine(outcome):
                await outcome
        except Exception as exc:
            c.crash(name, exc)
    return c


def main() -> int:
    parser = argparse.ArgumentParser(description="Đại Việt Sử Ký — Phase 2 self-test")
    parser.add_argument("--offline", action="store_true", help="chỉ chạy kiểm thử offline")
    args = parser.parse_args()
    started = time.perf_counter()
    c = asyncio.run(main_async(run_live=not args.offline))
    header(f"KẾT QUẢ: {c.passed} PASS · {c.failed} FAIL · {time.perf_counter() - started:.1f} s")
    for failure in c.failures:
        print(f"  ✗ {failure}")
    return 0 if c.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
