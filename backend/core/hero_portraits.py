"""Đại Việt Sử Ký — Luật duyệt ảnh chân dung nhân vật (``frontend/public/assets/heroes/{id}.png`` + ``.meta.json``).

Giống thư viện video: một chân dung MỚI chỉ được lên web khi có người duyệt.

* ``{id}.meta.json`` có thêm ``reviewed_by`` (người/giáo viên Sử đã duyệt cổ phục), ``reviewed_at`` và
  ``image_sha256`` (mã băm đúng file PNG đã được duyệt — sửa ảnh sau khi duyệt sẽ bị phát hiện).
* 3 ảnh đang chạy trên web từ trước khi có quy định này (16/11/2026) được giữ nguyên tới khi nhóm chọn ảnh thay:
  chúng nằm trong ``LEGACY_PORTRAITS`` (khớp đúng mã băm), ``review_status: "legacy"``.
* ``scripts/check_hero_portraits.py`` chạy trong deploy: chân dung không phải ảnh cũ mà thiếu ``reviewed_by`` hoặc sai
  mã băm → build DỪNG (không thể "bỏ qua" một ảnh nhân vật như với video).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["AI_PORTRAIT_LABEL", "HERO_IDS", "LEGACY_PORTRAITS", "PortraitStatus", "portrait_status", "sha256_file"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HEROES_DIR = PROJECT_ROOT / "frontend" / "public" / "assets" / "heroes"
HEROES_TS = PROJECT_ROOT / "frontend" / "src" / "data" / "heroes.ts"
HERO_IDS = ("tran_hung_dao", "nguyen_trai", "quang_trung")
AI_PORTRAIT_LABEL = "Chân dung minh họa do AI tạo"
# Ảnh đang chạy trên web trước quy định reviewed_by — chỉ đúng các file này (theo mã băm) được miễn duyệt.
LEGACY_PORTRAITS: dict[str, str] = {
    "tran_hung_dao": "5118d8a800536ab6613f9cc38470906ceee808cf230fe21aa0aab1b6cbfe89e5",
    "nguyen_trai": "c59eba83ccc695a26e71540b5f2716ad187bf6641385d3512b344b634231f0ad",
    "quang_trung": "eea5e1de7fd3e42c41055c3da2a7d7e9f1d2f43a87bb708045a53b7f119c975c",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_ai_source(source: Any) -> bool:
    return isinstance(source, str) and source.startswith("huggingface:")


def frontend_ai_flags(heroes_ts: Path = HEROES_TS) -> dict[str, bool]:
    """Đọc ``portraitAiGenerated`` của từng nhân vật trong heroes.ts (để đối chiếu với nguồn ảnh)."""
    text = heroes_ts.read_text(encoding="utf-8")
    flags: dict[str, bool] = {}
    for hero_id in HERO_IDS:
        match = re.search(rf"id: '{hero_id}',.*?portraitAiGenerated: (true|false)", text, re.S)
        if match:
            flags[hero_id] = match.group(1) == "true"
    return flags


@dataclass(frozen=True)
class PortraitStatus:
    hero_id: str
    status: str  # "legacy" | "reviewed" | "blocked"
    problems: tuple[str, ...]
    reviewed_by: str
    source: str


def portrait_status(hero_id: str, heroes_dir: Path = HEROES_DIR, heroes_ts: Path | None = HEROES_TS) -> PortraitStatus:
    png = heroes_dir / f"{hero_id}.png"
    meta_path = heroes_dir / f"{hero_id}.meta.json"
    problems: list[str] = []
    if not png.is_file():
        return PortraitStatus(hero_id, "blocked", ("thiếu file PNG",), "", "")
    if not (heroes_dir / f"{hero_id}.rig.json").is_file():
        problems.append("thiếu file rig.json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return PortraitStatus(hero_id, "blocked", (f"meta.json lỗi: {exc}",), "", "")
    source = str(meta.get("source") or "")
    reviewed_by = " ".join(str(meta.get("reviewed_by") or "").split())
    digest = sha256_file(png)
    if heroes_ts is not None:
        flag = frontend_ai_flags(heroes_ts).get(hero_id)
        if flag is not None and flag != is_ai_source(source):
            problems.append("portraitAiGenerated trong heroes.ts không khớp nguồn ảnh (nhãn 'Chân dung minh họa do AI tạo')")
    if LEGACY_PORTRAITS.get(hero_id) == digest:
        return PortraitStatus(hero_id, "blocked" if problems else "legacy", tuple(problems), reviewed_by, source)
    if not reviewed_by:
        problems.append("ảnh mới thiếu reviewed_by (chưa có người/giáo viên Sử duyệt) — chưa được thay lên web")
    if meta.get("image_sha256") != digest:
        problems.append("image_sha256 trong meta.json không khớp file PNG (ảnh bị đổi sau khi duyệt?)")
    if not source:
        problems.append("meta.json thiếu source (nguồn ảnh)")
    return PortraitStatus(hero_id, "blocked" if problems else "reviewed", tuple(problems), reviewed_by, source)
