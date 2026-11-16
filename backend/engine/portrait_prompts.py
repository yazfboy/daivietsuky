"""Đại Việt Sử Ký — Khung prompt chân dung THỐNG NHẤT cho 3 nhân vật (FLUX.1-schnell).

Cả 3 vị dùng chung: cùng khung hình (đỉnh đầu → ngay trên đầu gối, KHÔNG lộ bàn chân / giày), cùng tư thế đứng chính
diện, cùng ánh sáng studio, cùng nền xanh phẳng để tách nền, cùng phong cách tranh sơn dầu lịch sử. Chỉ khác phần mô
tả người + trang phục + đạo cụ + những điều cần tránh của từng thời (Trần, Lê sơ, Tây Sơn).

FLUX.1-schnell không nhận negative prompt nên các điều cần tránh được viết thẳng vào prompt ("no …").

Những lỗi của prompt cũ đã sửa:
* Nguyễn Trãi: bỏ "Seated formally… hands hidden" (mâu thuẫn với dáng đứng); áo quan văn KHÔNG thêu rồng, mũ ô sa
  không có chóp vương miện; râu vừa phải không che miệng; tay cầm cuộn giấy rõ từng ngón.
* Quang Trung: khung cắt trên đầu gối nên không lộ giày; tránh giáp ngực mặt thú lớn và mũ trụ chóp xòe kiểu tuồng;
  kiếm tra trong vỏ bên hông.
* Trần Hưng Đạo: khăn chít đầu gọn thời Trần, tránh mũ tua đỏ chóp vàng kiểu quan nhà Thanh/Minh; tay rõ từng ngón.

Mô tả trang phục là đề xuất để giáo viên Sử duyệt trên ảnh (review_heroes.html), không phải khẳng định sử học.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["HERO_PORTRAIT_SPECS", "PORTRAIT_STYLE_VERSION", "HeroPortraitSpec", "unified_portrait_prompt"]

PORTRAIT_STYLE_VERSION = "unified-2026-11-16"

# FLUX.1-schnell chỉ đọc ~256 token đầu → prompt ngắn (~160 từ), phần quan trọng đứng trước.
FRAMING = (
    "Standing upright, facing the viewer, centered, framed from head to just above the knees, feet not visible, "
    "both hands visible with five separate fingers"
)
FACE = "Calm expression, lips closed and clearly visible"
BACKGROUND = "Plain flat bright green background (#00FF00), no shadows, no scenery, no text"
STYLE = "Soft frontal studio light, detailed historical oil painting"


@dataclass(frozen=True)
class HeroPortraitSpec:
    hero_id: str
    subject: str
    person: str
    costume: str
    prop: str
    avoid: str


HERO_PORTRAIT_SPECS: dict[str, HeroPortraitSpec] = {
    "tran_hung_dao": HeroPortraitSpec(
        hero_id="tran_hung_dao",
        subject="Trần Hưng Đạo, supreme commander of the 13th-century Trần dynasty, Đại Việt (Vietnam)",
        person="a Vietnamese prince-general about 55, stern dignified face, short black beard and mustache",
        costume=("a neat dark cloth head wrap; deep red cross-collared robe under dark brown lamellar armor, "
                 "wide black sash"),
        prop="left hand on the hilt of a sheathed sword at his hip",
        avoid="no hat with tassels, no crown, no Qing or Ming official hat, no queue, no dragons",
    ),
    "nguyen_trai": HeroPortraitSpec(
        hero_id="nguyen_trai",
        subject="Nguyễn Trãi, Confucian scholar-official of the early Lê dynasty, 15th-century Đại Việt (Vietnam)",
        person="a Vietnamese scholar about 55, thin wise face, sparse medium-length grey-black beard",
        costume=("a black ô sa official hat with two short rounded side wings and a plain top; plain dark blue "
                 "cross-collared official robe with wide sleeves, black belt"),
        prop="left hand holding a rolled paper scroll",
        avoid="not seated, no dragons or gold embroidery on the robe, no crown or ornament on the hat",
    ),
    "quang_trung": HeroPortraitSpec(
        hero_id="quang_trung",
        subject="Emperor Quang Trung (Nguyễn Huệ), Tây Sơn dynasty, late 18th-century Đại Việt (Vietnam)",
        person="a Vietnamese commander about 36, strong build, determined eyes, short black mustache, small beard",
        costume=("a rounded dark iron helmet with a short neck guard; dark brown and red lamellar armor over a red "
                 "robe, plain round chest plate, dark red cape"),
        prop="left hand on the hilt of a sheathed sword at his hip",
        avoid="no monster face on the armor, no plumes or fan crest on the helmet, no opera costume, no drawn sword",
    ),
}


def unified_portrait_prompt(hero_id: str) -> str:
    spec = HERO_PORTRAIT_SPECS[hero_id]
    return (
        f"Portrait of {spec.subject}: {spec.person}. Wearing {spec.costume}. {spec.prop[0].upper()}{spec.prop[1:]}. "
        f"{FRAMING}. {FACE}. {BACKGROUND}. {spec.avoid[0].upper()}{spec.avoid[1:]}. {STYLE}."
    )
