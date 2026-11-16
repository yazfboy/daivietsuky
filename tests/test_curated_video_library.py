"""Đại Việt Sử Ký — Test cố định cho nhánh feat/curated-video-library (không gọi Gemini / Wikimedia / mạng).

Chạy từ thư mục gốc:  python tests/test_curated_video_library.py

* #V1 ``backend/core/video_library.py``: đọc MP4 (rãnh hình / âm thanh / thời lượng) và luật loại mục.
* #V2 ``scripts/check_video_library.py``: chỉ mục hợp lệ vào approved.json; .mp4 không có mục duyệt → không có nút;
  ``--dist`` gỡ clip chưa duyệt khỏi bản build.
* #V3 ``scripts/extract_action_posters.py``: trích khung hình cho key ACTION ``neutral_only`` có clip hợp lệ, gỡ
  ``neutral_only``, ghi ``ai_still``; không ghi đè file; không đụng key thiếu duyệt / có mặt nhân vật.
* #V4 ``foreground_only`` + ``ai_still`` trong BackdropResolver (không gọi Wikimedia).
* #V5 Dữ liệu thật: giữ nguyên toàn bộ ``neutral_only``, không tự thêm ảnh/clip, ảnh anh hùng & scene_candidates nguyên vẹn.
* #V6 Frontend (bundle TypeScript bằng esbuild + render React tĩnh): ``effectiveBackdrop``, đọc approved.json, nút
  "Xem video", nhãn "Video/Ảnh minh họa do AI tạo", thẻ <video> tắt tiếng, nút "Hành động" vẫn theo actionAllowed.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image  # noqa: E402

import check_video_library  # noqa: E402
import extract_action_posters as posters  # noqa: E402
from backend.agents.director_agent import SceneDirective  # noqa: E402
from backend.agents.history_rules import HERO_EVENTS  # noqa: E402
from backend.core.scene_library import SceneLibrary  # noqa: E402
from backend.core.video_library import AI_IMAGE_LABEL, AI_VIDEO_LABEL, VideoLibrary, mp4_info  # noqa: E402
from backend.engine.visual_pipeline import BackdropResolver  # noqa: E402

FRONTEND = ROOT / "frontend"
HERO_SHA256 = {
    "tran_hung_dao.png": "5118d8a800536ab6613f9cc38470906ceee808cf230fe21aa0aab1b6cbfe89e5",
    "nguyen_trai.png": "c59eba83ccc695a26e71540b5f2716ad187bf6641385d3512b344b634231f0ad",
    "quang_trung.png": "eea5e1de7fd3e42c41055c3da2a7d7e9f1d2f43a87bb708045a53b7f119c975c",
}
NEUTRAL_ONLY = ("khang_chien_mong_nguyen_1285", "hich_tuong_si", "ngoc_hoi_dong_da_1789", "bach_dang_1288",
                "khang_chien_mong_nguyen_1287_1288", "rach_gam_xoai_mut_1785", "len_ngoi_nui_ban_1788",
                "tot_dong_chuc_dong_1426", "chuong_duong_ham_tu_1285", "le_chi_vien_1442", "que_huong_nhi_khe")
CANDIDATES = {  # scene_candidates/dot2 (gitignore — chỉ kiểm khi có trên máy)
    "bach_dang_1288/01_Bai_coc_Cao_Quy.jpg": "d80bdeecf5a5b8af",
    "khang_chien_mong_nguyen_1287_1288/01_Bai_coc_Cao_Quy.jpg": "d80bdeecf5a5b8af",
    "ngoc_hoi_dong_da_1789/01_Battle_at_the_River_Thi_cau.jpg": "1f3306fc84baf540",
    "ngoc_hoi_dong_da_1789/02_Battle_at_the_River_Phu_luong.jpg": "1c81ac63bf4c8b1c",
    "ngoc_hoi_dong_da_1789/03_Battle_at_the_River_Tho_xuong.jpg": "81dcd54bb5dce8f7",
    "ngoc_hoi_dong_da_1789/04_Battle_at_Tam_dy_and_Tru_huu.jpg": "83968475d3edaaec",
    "rach_gam_xoai_mut_1785/01_A_small_river_in_Ban_Long_commune_Chau_Thanh_district_Tien_G.jpg": "1bb47cf3b03d3419",
    "rach_gam_xoai_mut_1785/02_A_site_near_Xoai_Mut_river_Thanh_Phu_commune_Chau_Thanh_dist.jpg": "2dd815d6dea87bca",
}
RESULTS: list[bool] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    return bool(ok)


# ─────────────────────────── MP4 tổng hợp (chỉ cấu trúc box, đủ cho bộ đọc) ───────────────────────────
def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mvhd(duration_s: float, timescale: int = 1000, version: int = 0) -> bytes:
    if version == 1:
        head = bytes([1, 0, 0, 0]) + struct.pack(">QQIQ", 0, 0, timescale, round(duration_s * timescale))
    else:
        head = bytes([0, 0, 0, 0]) + struct.pack(">IIII", 0, 0, timescale, round(duration_s * timescale))
    return box(b"mvhd", head + b"\0" * 80)


def trak(handler: bytes) -> bytes:
    hdlr = box(b"hdlr", b"\0" * 8 + handler + b"\0" * 12 + b"track\0")
    return box(b"trak", box(b"tkhd", b"\0" * 84) + box(b"mdia", box(b"mdhd", b"\0" * 24) + hdlr + box(b"minf", b"")))


def make_mp4(duration_s: float = 8.0, tracks: tuple[bytes, ...] = (b"vide",), version: int = 0,
             large_mdat: bool = False) -> bytes:
    ftyp = box(b"ftyp", b"isom\0\0\0\0isomiso2mp41")
    moov = box(b"moov", mvhd(duration_s, version=version) + b"".join(trak(t) for t in tracks))
    body = b"\0" * 32
    mdat = struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 16 + len(body)) + body if large_mdat else box(b"mdat", body)
    return ftyp + mdat + moov


def video_item(key: str, **over: object) -> dict:
    item = {
        "video_path": f"/assets/videos/{key}.mp4",
        "poster_path": f"/assets/scenes/{key}.jpg",
        "duration_seconds": 8,
        "ai_video_label": AI_VIDEO_LABEL,
        "ai_image_label": AI_IMAGE_LABEL,
        "creator": "Nhóm Đại Việt Sử Ký",
        "tool": "Công cụ AI thử nghiệm",
        "created_at": "2026-11-16",
        "reviewed_by": "Người duyệt A",
        "has_hero_face": False,
    }
    item.update(over)
    return item


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fake_extractor(calls: list[tuple[Path, float]]):
    def extract(video: Path, out: Path, at_s: float) -> None:
        calls.append((video, at_s))
        Image.new("RGB", (64, 36), (120, 90, 60)).save(out, "JPEG")
    return extract


# ─────────────────────────── #V1 ───────────────────────────
def test_video_library_core() -> None:
    print("\n#V1 — video_library.py: đọc MP4 và luật loại mục")
    with tempfile.TemporaryDirectory(prefix="dvsk_v1_") as tmp:
        pub = Path(tmp) / "public"
        vids = pub / "assets" / "videos"
        vids.mkdir(parents=True)
        (vids / "silent.mp4").write_bytes(make_mp4())
        (vids / "audio.mp4").write_bytes(make_mp4(tracks=(b"vide", b"soun")))
        (vids / "v1.mp4").write_bytes(make_mp4(8.0, version=1, large_mdat=True))
        (vids / "broken.mp4").write_bytes(b"\0\0\0\x10ftypisom" + b"garbage")
        info = mp4_info(vids / "silent.mp4")
        check(info.has_video and not info.has_audio and abs(info.duration_s - 8.0) < 1e-9,
              "MP4 chỉ có rãnh hình: has_video, KHÔNG audio, 8.0 s", f"{info}")
        info = mp4_info(vids / "audio.mp4")
        check(info.has_audio and info.handlers == ("vide", "soun"), "MP4 có rãnh 'soun' → phát hiện còn âm thanh")
        info = mp4_info(vids / "v1.mp4")
        check(abs(info.duration_s - 8.0) < 1e-9, "mvhd version 1 (64-bit) + box mdat 64-bit đọc đúng thời lượng")
        try:
            mp4_info(vids / "broken.mp4")
            broken = False
        except ValueError:
            broken = True
        check(broken, "File .mp4 hỏng → ValueError (không coi là hợp lệ)")

        cases = {
            "hop_le": ({}, "hop_le", make_mp4()),
            "thieu_duyet": ({"reviewed_by": ""}, "thieu_duyet", make_mp4()),
            "co_mat_nhan_vat": ({"has_hero_face": True}, "co_mat_nhan_vat", make_mp4()),
            "khong_khai_bao_mat": ({"has_hero_face": None}, "khong_khai_bao_mat", make_mp4()),
            "thieu_file": ({}, None, None),
            "con_am_thanh": ({}, "con_am_thanh", make_mp4(tracks=(b"vide", b"soun"))),
            "sai_thoi_luong": ({}, "sai_thoi_luong", make_mp4(12.0)),
            "sai_duong_dan": ({"video_path": "/assets/videos/khac.mp4"}, "sai_duong_dan", make_mp4()),
            "sai_nhan": ({"ai_video_label": "Video minh họa"}, "sai_nhan", make_mp4()),
        }
        videos = {"_mau_bach_dang_1288": video_item("bach_dang_1288")}
        for key, (over, file_key, data) in cases.items():
            item = video_item(key, **over)
            if over.get("has_hero_face", 0) is None:
                item.pop("has_hero_face")
            videos[key] = item
            if file_key and data:
                (vids / f"{file_key}.mp4").write_bytes(data)
        lib_path = Path(tmp) / "video_library.json"
        write_json(lib_path, {"videos": videos})
        lib = VideoLibrary.load(lib_path)
        report = lib.report(pub)
        check("_mau_bach_dang_1288" not in report, "Mục mẫu bắt đầu bằng '_' bị bỏ qua")
        check(report["hop_le"] == [] and set(lib.approved(pub)) == {"hop_le"},
              "Chỉ mục đủ điều kiện được duyệt", f"approved={sorted(lib.approved(pub))}")
        expect = {
            "thieu_duyet": "reviewed_by",
            "co_mat_nhan_vat": "has_hero_face",
            "khong_khai_bao_mat": "has_hero_face",
            "thieu_file": "thiếu file .mp4",
            "con_am_thanh": "âm thanh",
            "sai_thoi_luong": "thời lượng",
            "sai_duong_dan": "video_path",
            "sai_nhan": "ai_video_label",
        }
        for key, needle in expect.items():
            check(any(needle in p for p in report[key]), f"Loại '{key}' với lý do chứa '{needle}'", "; ".join(report[key]))
        public = lib.get("hop_le").to_public()
        check(set(public) == {"video_path", "poster_path", "duration_seconds", "ai_video_label", "ai_image_label",
                              "creator", "tool", "created_at", "reviewed_by", "has_hero_face"}
              and public["has_hero_face"] is False and public["duration_seconds"] == 8,
              "approved.json chứa đủ trường yêu cầu (video_path, poster_path, 8 s, 2 nhãn AI, creator, tool, "
              "created_at, reviewed_by, has_hero_face=false)")


# ─────────────────────────── #V2 ───────────────────────────
def test_build_script() -> None:
    print("\n#V2 — scripts/check_video_library.py (chạy lúc build)")
    with tempfile.TemporaryDirectory(prefix="dvsk_v2_") as tmp:
        pub = Path(tmp) / "public"
        vids = pub / "assets" / "videos"
        vids.mkdir(parents=True)
        (vids / "bach_dang_1288.mp4").write_bytes(make_mp4())
        (vids / "ngoc_hoi_dong_da_1789.mp4").write_bytes(make_mp4())
        (vids / "rach_gam_xoai_mut_1785.mp4").write_bytes(make_mp4(tracks=(b"vide", b"soun")))
        (vids / "khang_chien_mong_nguyen_1285.mp4").write_bytes(make_mp4())
        (vids / "tha_vao_khong_khai_bao.mp4").write_bytes(make_mp4())
        lib_path = Path(tmp) / "video_library.json"
        write_json(lib_path, {"videos": {
            "bach_dang_1288": video_item("bach_dang_1288"),
            "ngoc_hoi_dong_da_1789": video_item("ngoc_hoi_dong_da_1789", reviewed_by=""),
            "rach_gam_xoai_mut_1785": video_item("rach_gam_xoai_mut_1785"),
            "khang_chien_mong_nguyen_1285": video_item("khang_chien_mong_nguyen_1285", has_hero_face=True),
        }})
        out = io.StringIO()
        code = check_video_library.run(lib_path, pub, None, out)
        text = out.getvalue()
        approved = json.loads((vids / "approved.json").read_text(encoding="utf-8"))["videos"]
        check(code == 0 and list(approved) == ["bach_dang_1288"],
              "approved.json chỉ chứa clip hợp lệ (mục bị loại không làm hỏng build)", f"{list(approved)}")
        for key in ("ngoc_hoi_dong_da_1789", "rach_gam_xoai_mut_1785", "khang_chien_mong_nguyen_1285"):
            check(f"LOẠI   {key}" in text, f"Báo cáo loại {key}")
        check("BỎ QUA tha_vao_khong_khai_bao.mp4" in text and "tha_vao_khong_khai_bao" not in approved,
              ".mp4 thả vào thư mục mà không có mục duyệt → báo bỏ qua, không vào approved.json")

        dist = Path(tmp) / "dist"
        shutil.copytree(pub, dist)
        code = check_video_library.run(lib_path, pub, dist, io.StringIO())
        left = sorted(p.name for p in (dist / "assets" / "videos").iterdir())
        check(code == 0 and left == ["approved.json", "bach_dang_1288.mp4"],
              "--dist: gỡ mọi .mp4 chưa duyệt khỏi bản build, giữ clip đã duyệt", f"{left}")
        check(sorted(p.name for p in vids.iterdir()) == sorted(
            ["approved.json", "bach_dang_1288.mp4", "ngoc_hoi_dong_da_1789.mp4", "rach_gam_xoai_mut_1785.mp4",
             "khang_chien_mong_nguyen_1285.mp4", "tha_vao_khong_khai_bao.mp4"]),
              "--dist không xóa gì trong frontend/public (file gốc của nhóm)")
        sl = Path(tmp) / "scene_library.json"
        (pub / "assets" / "scenes").mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8)).save(pub / "assets" / "scenes" / "bach_dang_1288.jpg", "JPEG")
        still = {"poster_path": "/assets/scenes/bach_dang_1288.jpg", "video_key": "bach_dang_1288", "label": AI_IMAGE_LABEL,
                 "tool": "Công cụ AI", "creator": "Nhóm", "reviewed_by": "Người duyệt A", "created_at": "2026-11-16"}
        write_json(sl, {"scenes": {"bach_dang_1288": {"title": "x", "ai_still": still},
                                   "ngoc_hoi_dong_da_1789": {"title": "y", "ai_still": dict(
                                       still, poster_path="/assets/scenes/bach_dang_1288.jpg",
                                       poster_reviewed_by="Người duyệt ảnh B")}}})
        out = io.StringIO()
        check_video_library.run(lib_path, pub, None, out, scene_library_path=sl)
        text = out.getvalue()
        check("ẢNH TĨNH AI KHÔNG DÙNG  bach_dang_1288: thiếu poster_reviewed_by" in text
              and "ẢNH TĨNH AI DÙNG  ngoc_hoi_dong_da_1789" in text,
              "Build báo ảnh tĩnh AI thiếu poster_reviewed_by là KHÔNG DÙNG; có người duyệt ảnh thì DÙNG")
        (Path(tmp) / "hong.json").write_text("{không phải json", encoding="utf-8")
        check(check_video_library.run(Path(tmp) / "hong.json", pub, None, io.StringIO()) == 1,
              "video_library.json hỏng → thoát mã 1 (build dừng)")


# ─────────────────────────── #V3 ───────────────────────────
def scene_fixture() -> dict:
    neutral = {"title": "x", "year": "", "kind": "battle", "aliases": [], "wikimedia_search_query": "",
               "image": "", "author": "", "license": "", "license_url": "", "source_url": "", "neutral_only": True,
               "review_note": "ghi chú của nhóm"}
    return {"_note": "test", "scenes": {
        "bach_dang_1288": dict(neutral, title="Trận Bạch Đằng"),
        "ngoc_hoi_dong_da_1789": dict(neutral, title="Ngọc Hồi – Đống Đa"),
        "rach_gam_xoai_mut_1785": dict(neutral, title="Rạch Gầm – Xoài Mút"),
        "khang_chien_mong_nguyen_1285": dict(neutral, title="1285"),
        "khang_chien_mong_nguyen_1287_1288": dict(neutral, title="1287"),
        "hich_tuong_si": dict(neutral, title="Hịch tướng sĩ"),
    }}


def test_poster_extraction() -> None:
    print("\n#V3 — scripts/extract_action_posters.py (trích khung hình cho key ACTION)")
    check(set(posters.ACTION_KEYS) == {k for keys in HERO_EVENTS.values() for k in keys},
          "Key ACTION lấy thẳng từ HERO_EVENTS (không chép bảng)", f"{posters.ACTION_KEYS}")
    with tempfile.TemporaryDirectory(prefix="dvsk_v3_") as tmp:
        pub = Path(tmp) / "public"
        vids = pub / "assets" / "videos"
        vids.mkdir(parents=True)
        for key in ("bach_dang_1288", "ngoc_hoi_dong_da_1789", "rach_gam_xoai_mut_1785", "khang_chien_mong_nguyen_1285",
                    "hich_tuong_si"):
            (vids / f"{key}.mp4").write_bytes(make_mp4())
        lib_path = Path(tmp) / "scene_library.json"
        write_json(lib_path, scene_fixture())
        vlib_path = Path(tmp) / "video_library.json"
        write_json(vlib_path, {"videos": {
            "bach_dang_1288": video_item("bach_dang_1288"),
            "ngoc_hoi_dong_da_1789": video_item("ngoc_hoi_dong_da_1789", reviewed_by=""),
            "rach_gam_xoai_mut_1785": video_item("rach_gam_xoai_mut_1785", has_hero_face=True),
            "khang_chien_mong_nguyen_1285": video_item("khang_chien_mong_nguyen_1285"),
            "hich_tuong_si": video_item("hich_tuong_si"),
        }})
        scenes_dir = pub / "assets" / "scenes"
        scenes_dir.mkdir(parents=True)
        (scenes_dir / "khang_chien_mong_nguyen_1285.jpg").write_bytes(b"anh-co-san")
        pending_dir = Path(tmp) / "scene_candidates" / "posters"
        kwargs = dict(library_path=lib_path, video_library_path=vlib_path, public_dir=pub)
        ext = dict(kwargs, pending_dir=pending_dir)

        calls: list[tuple[Path, float]] = []
        dry = posters.extract_pending_posters(**ext, extractor=fake_extractor(calls), dry_run=True)
        check(not calls and json.loads(lib_path.read_text(encoding="utf-8")) == scene_fixture()
              and {r.key: r.status for r in dry}["bach_dang_1288"] == "would_extract",
              "--dry-run: chỉ báo, không trích, không sửa scene_library.json")

        results = {r.key: r for r in posters.extract_pending_posters(**ext, extractor=fake_extractor(calls))}
        check(results["bach_dang_1288"].status == "pending_review" and (pending_dir / "bach_dang_1288.jpg").is_file()
              and len(calls) == 1 and calls[0][0].name == "bach_dang_1288.mp4" and calls[0][1] == 4.0,
              "Bước 1: clip hợp lệ → trích đúng 1 khung (giữa clip, 4.0 s) vào scene_candidates/posters/ CHỜ DUYỆT")
        check(json.loads(lib_path.read_text(encoding="utf-8")) == scene_fixture()
              and not (scenes_dir / "bach_dang_1288.jpg").exists(),
              "Bước 1 KHÔNG sửa scene_library.json, KHÔNG ghi vào frontend/public (chưa ai xem khung hình)")
        data = json.loads(lib_path.read_text(encoding="utf-8"))["scenes"]
        check(results["ngoc_hoi_dong_da_1789"].status == "skipped" and not (pending_dir / "ngoc_hoi_dong_da_1789.jpg").exists(),
              "Clip thiếu reviewed_by → không trích", results["ngoc_hoi_dong_da_1789"].detail)
        check(results["rach_gam_xoai_mut_1785"].status == "skipped" and not (pending_dir / "rach_gam_xoai_mut_1785.jpg").exists(),
              "Clip có mặt nhân vật (has_hero_face true) → không trích")
        check(results["khang_chien_mong_nguyen_1285"].status == "error"
              and (scenes_dir / "khang_chien_mong_nguyen_1285.jpg").read_bytes() == b"anh-co-san",
              "File poster đã tồn tại → báo lỗi, KHÔNG ghi đè")
        check("hich_tuong_si" not in results and not (pending_dir / "hich_tuong_si.jpg").exists(),
              "Key không thuộc HERO_EVENTS (hich_tuong_si) không bị đụng dù có clip hợp lệ")

        no_reviewer = posters.approve_poster("bach_dang_1288", "  ", **kwargs, pending_dir=pending_dir)
        check(no_reviewer.status == "error" and json.loads(lib_path.read_text(encoding="utf-8")) == scene_fixture(),
              "--approve không có tên người duyệt → từ chối, không sửa gì")
        bad_key = posters.approve_poster("ngoc_hoi_dong_da_1789", "Người duyệt ảnh B", **kwargs, pending_dir=pending_dir)
        check(bad_key.status == "error" and data["ngoc_hoi_dong_da_1789"].get("neutral_only"),
              "--approve key có clip chưa hợp lệ → từ chối", bad_key.detail)
        ok = posters.approve_poster("bach_dang_1288", "Người duyệt ảnh B", **kwargs, pending_dir=pending_dir,
                                    today="2026-11-17")
        data = json.loads(lib_path.read_text(encoding="utf-8"))["scenes"]
        bd = data["bach_dang_1288"]
        check(ok.status == "approved" and (scenes_dir / "bach_dang_1288.jpg").is_file()
              and not (pending_dir / "bach_dang_1288.jpg").exists(),
              "Bước 2 (--approve): chép khung hình đã xem sang /assets/scenes/bach_dang_1288.jpg")
        check("neutral_only" not in bd and bd["ai_still"] == {
            "poster_path": "/assets/scenes/bach_dang_1288.jpg", "video_key": "bach_dang_1288", "label": AI_IMAGE_LABEL,
            "tool": "Công cụ AI thử nghiệm", "creator": "Nhóm Đại Việt Sử Ký", "reviewed_by": "Người duyệt A",
            "created_at": "2026-11-16", "poster_reviewed_by": "Người duyệt ảnh B",
            "poster_reviewed_at": "2026-11-17"} and bd["review_note"] == "ghi chú của nhóm",
              "Bước 2 gỡ neutral_only + ghi ai_still kèm poster_reviewed_by / poster_reviewed_at; giữ ghi chú nhóm")
        check(all(data[k].get("neutral_only") for k in ("ngoc_hoi_dong_da_1789", "rach_gam_xoai_mut_1785",
                                                         "khang_chien_mong_nguyen_1285", "hich_tuong_si")),
              "Các key khác giữ nguyên neutral_only")
        again = {r.key: r for r in posters.extract_pending_posters(**ext, extractor=fake_extractor(calls))}
        check(again["bach_dang_1288"].status == "skipped" and len(calls) == 1, "Chạy lại → bỏ qua key đã có ảnh tĩnh AI")

        def broken(video: Path, out: Path, at_s: float) -> None:
            raise posters.FrameExtractionError("ffmpeg lỗi giả lập")
        write_json(lib_path, scene_fixture())
        (scenes_dir / "bach_dang_1288.jpg").unlink()
        failed = {r.key: r for r in posters.extract_pending_posters(**ext, extractor=broken)}
        check(failed["bach_dang_1288"].status == "error" and not (pending_dir / "bach_dang_1288.jpg").exists()
              and json.loads(lib_path.read_text(encoding="utf-8")) == scene_fixture(),
              "Trích lỗi → không để lại file, không sửa scene_library.json")

        def not_jpeg(video: Path, out: Path, at_s: float) -> None:
            out.write_bytes(b"khong phai anh")
        bad = {r.key: r for r in posters.extract_pending_posters(**ext, extractor=not_jpeg)}
        check(bad["bach_dang_1288"].status == "error" and not (pending_dir / "bach_dang_1288.jpg").exists(),
              "Khung hình không phải JPEG hợp lệ → loại")
        missing = posters.approve_poster("bach_dang_1288", "Người duyệt ảnh B", **kwargs, pending_dir=pending_dir)
        check(missing.status == "error" and "bước 1" in missing.detail, "--approve khi chưa có khung hình chờ duyệt → từ chối")
    if shutil.which("ffmpeg") is None:
        try:
            posters.ffmpeg_extractor(Path("x.mp4"), Path("x.jpg"), 1.0)
            message = ""
        except posters.FrameExtractionError as exc:
            message = str(exc)
        check("chưa cài ffmpeg" in message, "Máy chưa có ffmpeg → báo rõ 'chưa cài ffmpeg' (không tự tải)")


# ─────────────────────────── #V4 ───────────────────────────
class RecordingFinder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def find(self, key: str, query: str):
        self.calls.append(key)
        return None


def directive(key: str, mode: str = "FOREGROUND_TALKING") -> SceneDirective:
    return SceneDirective(
        state_id="vs-t", character_id="nguyen_trai", question="q", scene_mode=mode,
        mouth_sync_enabled=mode == "FOREGROUND_TALKING", character_reference_path="/assets/heroes/nguyen_trai.png",
        background_type="HISTORICAL_EVENT", context_key=key, background_prompt="", character_action="",
        scene_prompt="", negative_prompt="", image_system_prompt="", reason="", decided_by="test", cache_hit=False,
        cache_match=None, cache_similarity=None, key_label=None, model=None, latency_ms=0.0, context_topic_key=key,
        topic_title=key, topic_year="1288", wikimedia_search_query="Bach Dang river",
    )


async def test_foreground_only() -> None:
    print("\n#V4 — foreground_only + ai_still trong BackdropResolver")
    still = {"poster_path": "/assets/scenes/{key}.jpg", "video_key": "{key}", "label": AI_IMAGE_LABEL,
             "tool": "Công cụ AI", "creator": "Nhóm", "reviewed_by": "Người duyệt A", "created_at": "2026-11-16",
             "poster_reviewed_by": "Người duyệt ảnh B"}

    def ai(key: str, **over: str) -> dict:
        return {k: (v.format(key=key) if isinstance(v, str) else v) for k, v in {**still, **over}.items()}

    photo = {"image": "/assets/scenes/{key}_cao_quy.jpg", "author": "Tác giả ảnh", "license": "CC BY-SA 4.0",
             "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
             "source_url": "https://commons.wikimedia.org/wiki/File:X.jpg", "caption": "Bãi cọc Cao Quỳ ngày nay"}

    def ph(key: str) -> dict:
        return {k: v.format(key=key) for k, v in photo.items()}

    scenes = {
        "fg_photo_only": {"title": "a", "kind": "battle", **ph("fg_photo_only"), "foreground_only": True},
        "fg_photo_ai": {"title": "b", "kind": "battle", **ph("fg_photo_ai"), "foreground_only": True,
                        "ai_still": ai("fg_photo_ai")},
        "ai_only": {"title": "c", "kind": "battle", "ai_still": ai("ai_only")},
        "fg_missing_file": {"title": "d", "kind": "battle", **ph("fg_missing_file"), "foreground_only": True},
        "neutral_with_ai": {"title": "e", "kind": "battle", "neutral_only": True, "ai_still": ai("neutral_with_ai")},
        "ai_unreviewed": {"title": "f", "kind": "battle", "ai_still": ai("ai_unreviewed", reviewed_by="")},
        "poster_unreviewed": {"title": "h", "kind": "battle", "neutral_only": False,
                              "ai_still": ai("poster_unreviewed", poster_reviewed_by="")},
        "plain_photo": {"title": "g", "kind": "place", **ph("plain_photo")},
    }
    with tempfile.TemporaryDirectory(prefix="dvsk_v4_") as tmp:
        pub = Path(tmp) / "public"
        sdir = pub / "assets" / "scenes"
        sdir.mkdir(parents=True)
        for name in ("fg_photo_only_cao_quy", "fg_photo_ai_cao_quy", "fg_photo_ai", "ai_only", "neutral_with_ai",
                     "ai_unreviewed", "plain_photo_cao_quy", "poster_unreviewed"):
            Image.new("RGB", (8, 8)).save(sdir / f"{name}.jpg", "JPEG")
        lib_path = Path(tmp) / "scene_library.json"
        write_json(lib_path, {"scenes": scenes})
        library = SceneLibrary.load(lib_path)
        check(library.get("fg_photo_only").foreground_only and library.get("fg_photo_ai").ai_still is not None
              and not library.get("plain_photo").foreground_only, "SceneLibrary đọc cờ foreground_only và ai_still")

        finder = RecordingFinder()
        resolver = BackdropResolver(library, finder, public_dir=pub)
        for mode in ("FOREGROUND_TALKING", "BACKGROUND_ACTION"):
            r = await resolver.resolve(directive("fg_photo_only", mode))
            check(r["source"] == "library" and r["caption"] == "Bãi cọc Cao Quỳ ngày nay" and r["foregroundOnly"] is True
                  and r["actionBackdrop"]["source"] == "neutral" and "imageUrl" not in r["actionBackdrop"],
                  f"[{mode}] Ảnh foreground_only có caption + ghi nguồn; nền hành động = phông trung tính (chưa có clip)")
        r = await resolver.resolve(directive("fg_photo_ai"))
        alt = r["actionBackdrop"]
        check(r["foregroundOnly"] and alt["source"] == "ai_still" and alt["imageUrl"] == "/assets/scenes/fg_photo_ai.jpg"
              and alt["aiLabel"] == AI_IMAGE_LABEL and alt["tool"] == "Công cụ AI" and alt["creator"] == "Nhóm"
              and alt["reviewedBy"] == "Người duyệt A" and alt["posterReviewedBy"] == "Người duyệt ảnh B",
              "Có khung hình clip AI đã duyệt → nền hành động = ảnh tĩnh AI kèm nhãn, tool, creator, reviewed_by")
        for mode in ("FOREGROUND_TALKING", "BACKGROUND_ACTION"):
            r = await resolver.resolve(directive("ai_only", mode))
            check(r["source"] == "ai_still" and r["aiLabel"] == AI_IMAGE_LABEL and "foregroundOnly" not in r,
                  f"[{mode}] Key chỉ có ảnh tĩnh AI (đã gỡ neutral_only) → dùng ảnh tĩnh AI")
        r = await resolver.resolve(directive("fg_missing_file", "BACKGROUND_ACTION"))
        check(r["source"] == "neutral", "foreground_only nhưng thiếu file ảnh → phông trung tính")
        r = await resolver.resolve(directive("neutral_with_ai"))
        check(r["source"] == "neutral", "neutral_only vẫn thắng (ai_still bị bỏ qua tới khi gỡ cờ)")
        r = await resolver.resolve(directive("poster_unreviewed", "BACKGROUND_ACTION"))
        check(r["source"] != "ai_still" and "aiLabel" not in r,
              "Khung hình chưa có poster_reviewed_by (chưa ai xem) → KHÔNG dùng dù clip đã duyệt", r["source"])
        r = await resolver.resolve(directive("ai_unreviewed"))
        check(r["source"] == "neutral" and "aiLabel" not in r,
              "ai_still thiếu reviewed_by không được dùng (rơi về Wikimedia → không có ảnh → trung tính)", r["source"])
        r = await resolver.resolve(directive("plain_photo", "BACKGROUND_ACTION"))
        check(r["source"] == "library" and "foregroundOnly" not in r and "actionBackdrop" not in r,
              "Ảnh thường (không foreground_only) không đổi hành vi")
        check(finder.calls == ["poster_unreviewed", "ai_unreviewed"],
              "Không gọi Wikimedia cho key foreground_only / ai_still / neutral_only", f"gọi: {finder.calls}")


# ─────────────────────────── #V5 ───────────────────────────
def test_real_data() -> None:
    print("\n#V5 — Dữ liệu thật trên nhánh: không tự thêm ảnh/clip, giữ quyết định của nhóm")
    raw = json.loads((ROOT / "backend/data/scene_library.json").read_text(encoding="utf-8"))["scenes"]
    check(all(raw[k].get("neutral_only") is True for k in NEUTRAL_ONLY),
          "Giữ nguyên toàn bộ neutral_only: true (11 key)")
    check(not any("foreground_only" in v or "ai_still" in v for v in raw.values()),
          "Chưa có mục nào được tự gắn foreground_only / ai_still (chờ nhóm duyệt ảnh và clip)")
    vlib = VideoLibrary.load()
    check(len(vlib) == 0, "video_library.json chỉ có mục mẫu '_mau_…' — không tự thêm clip")
    vdir = FRONTEND / "public" / "assets" / "videos"
    files = sorted(p.name for p in vdir.iterdir()) if vdir.is_dir() else []
    check(all(name == "approved.json" for name in files), "Không có file video nào trong frontend/public/assets/videos",
          f"{files}")
    check(not (FRONTEND / "public/assets/scenes/bach_dang_1288.jpg").exists(),
          "Không có poster AI nào được tạo (bach_dang_1288.jpg vẫn không tồn tại)")
    tracked = subprocess.run(["git", "ls-files", "frontend/public/assets/videos"], cwd=ROOT, capture_output=True,
                             text=True).stdout.split()
    check(tracked == [], "approved.json là file sinh lúc build (gitignore), không commit", f"{tracked}")
    heroes = FRONTEND / "public" / "assets" / "heroes"
    check(all(hashlib.sha256((heroes / n).read_bytes()).hexdigest() == h for n, h in HERO_SHA256.items())
          and not list(heroes.glob("*edges_clean*")),
          "3 ảnh anh hùng giữ nguyên từng byte, không có file .edges_clean trong repo")
    cand = ROOT / "scene_candidates" / "dot2"
    if cand.is_dir():
        ok = all((cand / rel).is_file() and hashlib.sha256((cand / rel).read_bytes()).hexdigest()[:16] == h
                 for rel, h in CANDIDATES.items())
        check(ok, "scene_candidates/dot2 nguyên vẹn (4 tranh nhà Thanh, 2 ảnh Rạch Gầm, Bãi cọc Cao Quỳ)")
    else:
        print("  [INFO] Không có scene_candidates/ trên máy này (thư mục gitignore) — bỏ qua kiểm tra ứng viên")


# ─────────────────────────── #V6 ───────────────────────────
NODE_SCRIPT = r"""
const path = require('path');
const [frontend] = process.argv.slice(2);
const esbuild = require(path.join(frontend, 'node_modules', 'esbuild'));
const entry = `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { effectiveBackdrop, applyManualSceneMode } from './utils/sceneRules';
import { parseApprovedVideos, approvedVideoFor, loadApprovedVideos } from './utils/videoLibrary';
import { StageInfoBar } from './components/StageInfoBar';
import { GlassChatBox } from './components/GlassChatBox';
import { DynamicHeroStage } from './components/DynamicHeroStage';
import { HEROES, buildDefaultSceneData } from './data/heroes';

export async function run() {
  const out = {};
  const photo = { source: 'library', contextTopicKey: 'bach_dang_1288', title: 'Trận Bạch Đằng', subtitle: '1288',
    imageUrl: 'https://x/assets/scenes/cao_quy.jpg', author: 'Tác giả', license: 'CC BY-SA 4.0',
    caption: 'Bãi cọc Cao Quỳ ngày nay', foregroundOnly: true,
    actionBackdrop: { source: 'neutral', contextTopicKey: 'bach_dang_1288', title: 'Trận Bạch Đằng', subtitle: '1288' } };
  const aiStill = { source: 'ai_still', contextTopicKey: 'bach_dang_1288', title: 'Trận Bạch Đằng',
    imageUrl: 'https://x/assets/scenes/bach_dang_1288.jpg', aiLabel: 'Ảnh minh họa do AI tạo', tool: 'Công cụ AI',
    creator: 'Nhóm', reviewedBy: 'Người duyệt A', posterReviewedBy: 'Người duyệt ảnh B' };
  const photoAi = { ...photo, actionBackdrop: aiStill };
  out.eff = {
    talk: effectiveBackdrop(photo, 'FOREGROUND_TALKING') === photo,
    actionNeutral: effectiveBackdrop(photo, 'BACKGROUND_ACTION'),
    actionAi: effectiveBackdrop(photoAi, 'BACKGROUND_ACTION') === aiStill,
    noAlt: effectiveBackdrop({ ...photo, actionBackdrop: undefined }, 'BACKGROUND_ACTION'),
    plain: effectiveBackdrop({ ...photo, foregroundOnly: false }, 'BACKGROUND_ACTION').source,
  };
  const good = { video_path: '/assets/videos/bach_dang_1288.mp4', poster_path: '/assets/scenes/bach_dang_1288.jpg',
    duration_seconds: 8, ai_video_label: 'Video minh họa do AI tạo', ai_image_label: 'Ảnh minh họa do AI tạo',
    creator: 'Nhóm', tool: 'Công cụ AI', created_at: '2026-11-16', reviewed_by: 'Người duyệt A', has_hero_face: false };
  const parsed = parseApprovedVideos({ videos: {
    bach_dang_1288: good,
    ngoc_hoi_dong_da_1789: { ...good, video_path: '/assets/videos/ngoc_hoi_dong_da_1789.mp4', has_hero_face: true },
    rach_gam_xoai_mut_1785: { ...good, video_path: '/assets/videos/rach_gam_xoai_mut_1785.mp4', reviewed_by: '' },
    khang_chien_mong_nguyen_1285: { ...good },
    'Bad Key': { ...good },
  } }, '/daivietsuky/');
  out.parsed = Object.keys(parsed);
  out.videoUrl = parsed.bach_dang_1288 && parsed.bach_dang_1288.videoUrl;
  out.lookup = [approvedVideoFor(parsed, 'bach_dang_1288') !== null, approvedVideoFor(parsed, 'van_don_1288'),
    approvedVideoFor(parsed, '__proto__'), approvedVideoFor(parsed, undefined)];
  out.load404 = Object.keys(await loadApprovedVideos(async () => ({ ok: false, json: async () => ({}) }), '/'));
  out.loadBad = Object.keys(await loadApprovedVideos(async () => ({ ok: true, json: async () => { throw new Error('x'); } }), '/'));
  out.loadThrow = Object.keys(await loadApprovedVideos(async () => { throw new Error('offline'); }, '/'));

  const hero = HEROES.tran_hung_dao;
  const base = buildDefaultSceneData(hero);
  const video = parsed.bach_dang_1288;
  const bar = (props) => renderToStaticMarkup(React.createElement(StageInfoBar, { sceneMode: 'FOREGROUND_TALKING',
    mouthSyncEnabled: true, fallbackTitle: 'x', onToggleVideo: () => {}, ...props }));
  out.html = {
    noVideo: bar({ sceneData: { ...base, backdrop: photo }, video: null }),
    videoOff: bar({ sceneData: { ...base, backdrop: photo }, video, videoOn: false }),
    videoOn: bar({ sceneData: { ...base, backdrop: photo }, video, videoOn: true }),
    talkPhoto: bar({ sceneData: { ...base, backdrop: photo }, video: null }),
    actionPhoto: bar({ sceneData: { ...base, backdrop: photo }, sceneMode: 'BACKGROUND_ACTION', video: null }),
    actionPhotoAi: bar({ sceneData: { ...base, backdrop: photoAi }, sceneMode: 'BACKGROUND_ACTION', video: null }),
    aiStill: bar({ sceneData: { ...base, backdrop: aiStill }, video: null }),
  };
  const box = (actionAllowed) => renderToStaticMarkup(React.createElement(GlassChatBox, { hero, messages: [],
    isStreaming: false, sceneMode: 'FOREGROUND_TALKING', mouthSyncEnabled: true, sceneData: base, isSidebarOpen: true,
    onToggleSidebar: () => {}, onSendMessage: () => {}, onSelectPrompt: () => {}, onToggleSceneMode: () => {},
    onResetChat: () => {}, actionAllowed, video, videoOn: false, onToggleVideo: () => {} }));
  out.chatLocked = box(false);
  out.chatAllowed = box(true);
  out.stageVideo = renderToStaticMarkup(React.createElement(DynamicHeroStage, { characterId: 'tran_hung_dao',
    heroImageUrl: hero.heroImageUrl, sceneMode: 'BACKGROUND_ACTION', mouthSyncEnabled: false, isStreaming: false,
    sceneData: base, backdropVideoUrl: video.videoUrl }));
  out.stageNoVideo = renderToStaticMarkup(React.createElement(DynamicHeroStage, { characterId: 'tran_hung_dao',
    heroImageUrl: hero.heroImageUrl, sceneMode: 'BACKGROUND_ACTION', mouthSyncEnabled: false, isStreaming: false,
    sceneData: base, backdropVideoUrl: null }));
  const locked = applyManualSceneMode({ ...base, actionAllowed: false }, 'BACKGROUND_ACTION');
  out.manualLocked = locked.sceneMode;
  return out;
}
`;
esbuild.build({ stdin: { contents: entry, resolveDir: path.join(frontend, 'src'), loader: 'tsx' }, bundle: true,
  platform: 'node', format: 'cjs', write: false, logLevel: 'error', jsx: 'automatic',
  define: { 'import.meta.env.BASE_URL': '"/"', 'import.meta.env.VITE_API_BASE_URL': '""', 'process.env.NODE_ENV': '"production"' } })
  .then(async (out) => {
    const mod = { exports: {} };
    new Function('module', 'exports', 'require', out.outputFiles[0].text)(mod, mod.exports, require);
    process.stdout.write(JSON.stringify(await mod.exports.run()));
  })
  .catch((err) => { console.error(err); process.exit(1); });
"""


def test_frontend() -> None:
    print("\n#V6 — Frontend: effectiveBackdrop, approved.json, nút 'Xem video', nhãn AI, <video> tắt tiếng")
    node = shutil.which("node")
    if not check(node is not None, "Có node để chạy TypeScript của frontend"):
        return
    with tempfile.TemporaryDirectory(prefix="dvsk_v6_") as tmp:
        script = Path(tmp) / "video.cjs"
        script.write_text(NODE_SCRIPT, encoding="utf-8")
        done = subprocess.run([node, str(script), str(FRONTEND)], capture_output=True, text=True, encoding="utf-8",
                              timeout=120)
    if not check(done.returncode == 0, "Bundle + render tĩnh StageInfoBar / GlassChatBox / DynamicHeroStage",
                 done.stderr[-400:]):
        return
    r = json.loads(done.stdout)
    eff = r["eff"]
    check(eff["talk"], "FOREGROUND_TALKING: ảnh foreground_only được dùng nguyên vẹn")
    check(eff["actionNeutral"]["source"] == "neutral" and "imageUrl" not in eff["actionNeutral"]
          and eff["actionAi"] and eff["noAlt"]["source"] == "neutral" and eff["noAlt"]["title"] == "Trận Bạch Đằng"
          and eff["plain"] == "library",
          "BACKGROUND_ACTION: bỏ ảnh foreground_only → ảnh tĩnh AI nếu có, không thì phông trung tính (giữ tên trận)")
    check(r["parsed"] == ["bach_dang_1288"], "Frontend kiểm lại approved.json: bỏ mục có mặt nhân vật / thiếu duyệt / "
          "sai đường dẫn / sai key", f"{r['parsed']}")
    check(r["videoUrl"] == "/daivietsuky/assets/videos/bach_dang_1288.mp4", "Đường dẫn video theo BASE_URL của Pages")
    check(r["lookup"] == [True, None, None, None], "approvedVideoFor: chỉ key đã duyệt, không dính __proto__")
    check(r["load404"] == [] and r["loadBad"] == [] and r["loadThrow"] == [],
          "approved.json 404 / hỏng / mất mạng → không có video nào")
    h = r["html"]
    check('data-testid="video-toggle"' not in h["noVideo"], "Key không có clip duyệt → KHÔNG có nút 'Xem video'")
    check('data-testid="video-toggle"' in h["videoOff"] and "Xem video" in h["videoOff"]
          and 'aria-pressed="false"' in h["videoOff"] and "ai-video-label" not in h["videoOff"],
          "Key có clip duyệt → hiện nút 'Xem video' (chưa bật thì chưa có nhãn video)")
    on = h["videoOn"]
    check("Tắt video" in on and 'data-testid="ai-video-label"' in on and "Video minh họa do AI tạo" in on
          and all(s in on for s in ("Công cụ: Công cụ AI", "Thực hiện: Nhóm", "Ngày tạo: 2026-11-16",
                                    "Duyệt: Người duyệt A")) and "backdrop-attribution" not in on,
          "Bật video → nhãn 'Video minh họa do AI tạo' + công cụ, người tạo, ngày tạo, người duyệt (ẩn ghi nguồn ảnh)")
    check("Bãi cọc Cao Quỳ ngày nay" in h["talkPhoto"] and "backdrop-attribution" in h["talkPhoto"]
          and "CC BY-SA 4.0" in h["talkPhoto"], "FOREGROUND_TALKING: ảnh foreground_only hiện caption + dòng ghi nguồn")
    check("Bãi cọc Cao Quỳ" not in h["actionPhoto"] and "backdrop-attribution" not in h["actionPhoto"],
          "BACKGROUND_ACTION: không hiện ảnh / caption foreground_only (phông trung tính)")
    ai_html = h["actionPhotoAi"]
    check("Bãi cọc Cao Quỳ" not in ai_html and 'data-testid="ai-image-label"' in ai_html
          and "Ảnh minh họa do AI tạo" in ai_html
          and all(s in ai_html for s in ("Công cụ: Công cụ AI", "Thực hiện: Nhóm", "Duyệt clip: Người duyệt A",
                                          "Duyệt ảnh: Người duyệt ảnh B")),
          "BACKGROUND_ACTION + khung hình AI → nhãn 'Ảnh minh họa do AI tạo' + tool, creator, reviewed_by")
    check('data-testid="ai-image-label"' in h["aiStill"] and "backdrop-attribution" not in h["aiStill"],
          "Ảnh tĩnh AI ở chế độ ảnh tĩnh: hiện nhãn AI thay dòng ghi nguồn ảnh chụp")

    def action_disabled(html: str) -> bool:
        start = html.find('data-testid="scene-mode-action"')
        tag = html[html.rfind("<button", 0, start):html.find(">", start)]
        return ' disabled=""' in tag  # thuộc tính disabled thật, không tính class Tailwind "disabled:..."
    check(action_disabled(r["chatLocked"]) and 'data-testid="video-toggle"' in r["chatLocked"],
          "actionAllowed = false: nút 'Hành động' vẫn khóa dù key có video (video không mở khóa hành động)")
    check(not action_disabled(r["chatAllowed"]), "actionAllowed = true: nút 'Hành động' bấm được")
    check(r["manualLocked"] == "FOREGROUND_TALKING", "Bấm 'Hành động' khi actionAllowed = false → vẫn FOREGROUND_TALKING")
    st = r["stageVideo"]
    tag = st[st.find("<video"):st.find(">", st.find("<video")) + 1]
    check(all(a in tag for a in ('autoplay=""', 'loop=""', 'playsinline=""', 'muted=""'))
          and 'src="/daivietsuky/assets/videos/bach_dang_1288.mp4"' in tag,
          "<video> có autoPlay, loop, playsInline, muted", tag[:200])
    check(st.find("<video") > st.find('data-testid="stage-hero-canvas"') and "<video" not in r["stageNoVideo"],
          "Video chỉ là nguồn khung hình ẩn của lớp nền (không có thẻ <video> khi chưa bật)")
    src = (FRONTEND / "src/components/DynamicHeroStage.tsx").read_text(encoding="utf-8")
    effect = src[src.find("useLayoutEffect(() => {"):]
    check(0 < effect.find("video.muted = true") < effect.find("video.play()"),
          "Ref đặt video.muted = true TRƯỚC khi gọi play()")
    backdrop = (FRONTEND / "src/engine/sceneBackdrop.ts").read_text(encoding="utf-8")
    frame = src[src.find("if (backdrop) {\n        backdrop.resize"):]
    check("backdrop.draw(ctx, t, camera)" in frame and frame.find("backdrop.draw(") < frame.find("drawHero(")
          and "'videoWidth' in src" in backdrop,
          "Video vẽ ở lớp nền (backdrop.draw) TRƯỚC model anh hùng → PNG nhân vật luôn nằm trên")


async def main() -> int:
    test_video_library_core()
    test_build_script()
    test_poster_extraction()
    await test_foreground_only()
    test_real_data()
    test_frontend()
    passed = sum(RESULTS)
    print(f"\nKẾT QUẢ: {passed} PASS · {len(RESULTS) - passed} FAIL")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
