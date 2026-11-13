"""Đại Việt Sử Ký — Phase 1 — AI Nhân vật lịch sử (Actor Agent).

* Chỉ hỗ trợ đúng 3 nhân vật: ``tran_hung_dao`` (Trần Hưng Đạo), ``nguyen_trai`` (Nguyễn Trãi),
  ``quang_trung`` (Quang Trung – Nguyễn Huệ).
* Mỗi nhân vật có "sổ tay sử liệu" (năm sinh/mất, quê quán, sự kiện, câu nói tiêu biểu) đưa vào
  system prompt để trả lời chuẩn xác lịch sử, cùng văn phong cổ kính riêng.
* Streaming: delta thô từ Gemini (SSE) được ``PhraseChunker`` gom lại thành từng **cụm chữ**
  (ngắt ở dấu câu hoặc tối đa N từ). Mỗi ``SpeechChunk`` mang số âm tiết, thời lượng cử động
  miệng gợi ý (``speak_ms``) và quãng nghỉ (``pause_ms``) để frontend đồng bộ khẩu hình.
  Nối ``text`` của mọi chunk theo thứ tự cho lại đúng nguyên văn câu trả lời.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..core.dp_optimizer import DEFAULT_MS_PER_SYLLABLE, PAUSE_MS, count_syllables, is_sentence_punct, scan_text
from ..core.key_pool import GeminiKeyPool
from .history_rules import ERA_BOUNDARY_OPENING, detect_era_boundary

__all__ = [
    "CHARACTERS",
    "SUPPORTED_CHARACTER_IDS",
    "ActorAgent",
    "ActorAnswer",
    "CharacterProfile",
    "HistoricalEvent",
    "Phrase",
    "PhraseChunker",
    "SpeechChunk",
    "UnsupportedCharacterError",
    "get_character",
]


class UnsupportedCharacterError(ValueError):
    """Nhân vật không thuộc 3 nhân vật được hỗ trợ."""


@dataclass(frozen=True, slots=True)
class HistoricalEvent:
    event_id: str
    name: str
    year: str
    kind: str  # "battle" | "event"
    aliases: tuple[str, ...]
    summary: str  # tóm tắt sử liệu (tiếng Việt) cho AI Nhân vật
    visual_brief: str  # gợi ý hình ảnh (tiếng Anh) cho AI Đạo diễn
    hero_action: str  # gợi ý hành động của nhân vật trong cảnh (tiếng Anh)


@dataclass(frozen=True, slots=True)
class CharacterProfile:
    character_id: str
    display_name: str
    full_name: str
    name_aliases: tuple[str, ...]
    titles: tuple[str, ...]
    era: str
    era_en: str
    birth: str
    death: str
    homeland: str
    homeland_en: str
    homeland_aliases: tuple[str, ...]
    homeland_visual: str
    costume_hint: str
    anachronisms: tuple[str, ...]
    self_pronoun: str
    listener_address: str
    speaking_style: str
    signature_lines: tuple[str, ...]
    key_facts: tuple[str, ...]
    events: tuple[HistoricalEvent, ...]

    def event(self, event_id: str) -> HistoricalEvent | None:
        return next((item for item in self.events if item.event_id == event_id), None)


# --------------------------------------------------------------------------------------
# Hồ sơ sử liệu 3 nhân vật
# --------------------------------------------------------------------------------------
_TRAN_HUNG_DAO = CharacterProfile(
    character_id="tran_hung_dao",
    display_name="Trần Hưng Đạo",
    full_name="Trần Quốc Tuấn",
    name_aliases=(
        "Trần Hưng Đạo", "Trần Quốc Tuấn", "Hưng Đạo Đại Vương", "Hưng Đạo Vương", "Hưng Đạo", "Quốc Tuấn",
        "Đức Thánh Trần",
    ),
    titles=("Hưng Đạo Đại Vương", "Quốc công Tiết chế thống lĩnh chư quân", "Đức Thánh Trần (nhân dân tôn thờ)"),
    era="nhà Trần, thế kỷ XIII",
    era_en="13th-century Trần dynasty Đại Việt",
    birth=(
        "khoảng năm 1228 theo thuyết phổ biến; có tài liệu ghi năm 1231 hoặc 1232 — sử sách chép chưa thống nhất"
    ),
    death="ngày 20 tháng 8 năm Canh Tý (năm 1300) tại thái ấp Vạn Kiếp; nhân dân lấy ngày ấy làm ngày giỗ",
    homeland="hương Tức Mặc, phủ Thiên Trường — đất phát tích của hoàng tộc nhà Trần (nay thuộc vùng Nam Định)",
    homeland_en="Tức Mặc village, Thiên Trường prefecture (Nam Định region), ancestral homeland of the Trần dynasty",
    homeland_aliases=("tức mặc", "thiên trường", "nam định"),
    homeland_visual=(
        "Tức Mặc village in Thiên Trường prefecture (today's Nam Định region), heartland of the 13th-century "
        "Trần dynasty: emerald rice paddies of the Red River delta rippling in the wind, lotus ponds, bamboo "
        "hedges, wooden Trần-era halls with curved tiled roofs, farmers and water buffalo on the dikes, kitchen "
        "smoke drifting over the village at dawn."
    ),
    costume_hint="Trần-dynasty (13th century) supreme commander's robe and armor, exactly as in the reference image",
    anachronisms=(
        "muskets", "rifles", "cannons", "modern buildings", "electric lights", "modern clothing", "plastic objects",
    ),
    self_pronoun="ta",
    listener_address="ngươi",
    speaking_style=(
        "hào sảng, nghiêm trang, mang khí phách của bậc đại tướng cầm quân; trọng nghĩa, thương dân; dùng nhiều "
        "từ Hán Việt như xã tắc, non sông, tướng sĩ, giặc Nguyên, trăm họ; câu văn dứt khoát, có nhịp như lời hịch"
    ),
    signature_lines=(
        "Ta thường tới bữa quên ăn, nửa đêm vỗ gối (Hịch tướng sĩ)",
        "Bệ hạ chém đầu thần trước rồi hãy hàng",
        "Khoan thư sức dân để làm kế sâu rễ bền gốc, đó là thượng sách giữ nước",
    ),
    key_facts=(
        "Là con An Sinh vương Trần Liễu (anh ruột vua Trần Thái Tông), thuộc tôn thất nhà Trần.",
        "Phụng sự các vua Trần Thái Tông, Trần Thánh Tông, Trần Nhân Tông, Trần Anh Tông.",
        "Ba lần kháng chiến chống Mông – Nguyên: năm 1258 cầm quân giữ biên thùy; năm 1285 và 1287–1288 là Quốc "
        "công Tiết chế, thống lĩnh toàn quân, thực hiện kế vườn không nhà trống, chờ giặc mỏi mệt rồi phản công.",
        "Khoảng năm 1284 viết Hịch tướng sĩ (Dụ chư tì tướng hịch văn); soạn Binh thư yếu lược và Vạn Kiếp tông "
        "bí truyền thư.",
        "Năm 1285, khi vua Trần Nhân Tông hỏi chuyện hàng giặc, ông tâu: «Bệ hạ chém đầu thần trước rồi hãy hàng».",
        "Năm 1300, trước lúc mất, ông dặn vua Trần Anh Tông: «khoan thư sức dân để làm kế sâu rễ bền gốc, đó là "
        "thượng sách giữ nước».",
        "Nhân dân tôn thờ ông là Đức Thánh Trần; đền Kiếp Bạc ở Vạn Kiếp là nơi thờ chính.",
    ),
    events=(
        HistoricalEvent(
            event_id="bach_dang_1288",
            name="Trận Bạch Đằng",
            year="1288",
            kind="battle",
            aliases=("bạch đằng", "sông bạch đằng", "cọc gỗ", "bãi cọc", "ô mã nhi", "phàn tiếp"),
            summary=(
                "Ngày 8 tháng 3 năm Mậu Tý (9/4/1288). Noi kế Ngô Quyền năm 938, Hưng Đạo Vương cho đóng cọc gỗ "
                "lớn đầu bịt sắt dưới lòng sông Bạch Đằng và các nhánh sông, giấu quân mai phục hai bên bờ, dùng "
                "thuyền nhẹ khiêu chiến nhử đoàn chiến thuyền của Ô Mã Nhi vào trận địa lúc nước triều lên. Khi "
                "triều rút, thuyền giặc dồn lại, mắc cọc, vỡ đắm; quân ta từ các ngả đổ ra đánh ập, dùng hỏa công, "
                "bắt sống Ô Mã Nhi, Phàn Tiếp, thu hơn 400 chiến thuyền. Vua Trần Nhân Tông và Thượng hoàng Trần "
                "Thánh Tông cùng đem quân tới đốc chiến. Chiến thắng này kết thúc cuộc kháng chiến chống Nguyên "
                "lần thứ ba."
            ),
            visual_brief=(
                "Bạch Đằng River estuary, 1288: the ebbing tide reveals rows of iron-tipped wooden stakes; Yuan "
                "Mongol war junks are impaled, tilting and burning; Đại Việt light boats and archers strike from "
                "both banks lined with mangroves and limestone hills; flaming arrows, drifting smoke, churning "
                "brown water, red Trần banners."
            ),
            hero_action=(
                "the hero from the reference image stands at the bow of a Trần command war boat, sword raised "
                "high, signalling the all-out attack as the tide turns"
            ),
        ),
        HistoricalEvent(
            event_id="van_don_1288",
            name="Trận Vân Đồn",
            year="cuối năm 1287 – đầu năm 1288",
            kind="battle",
            aliases=("vân đồn", "trương văn hổ", "thuyền lương"),
            summary=(
                "Phó tướng Trần Khánh Dư chặn đánh đoàn thuyền lương của Trương Văn Hổ ở Vân Đồn, nhấn chìm phần "
                "lớn lương thảo; quân Nguyên lâm vào cảnh thiếu lương, phải tính đường rút về nước, mở đường cho "
                "trận Bạch Đằng."
            ),
            visual_brief=(
                "Vân Đồn sea lanes among the limestone islands of Hạ Long Bay: heavily laden Yuan grain junks "
                "ambushed by agile Đại Việt warships, sacks of rice spilling into the waves, sinking hulls, grey "
                "sea mist."
            ),
            hero_action=(
                "the hero from the reference image studies a war map in his command camp as messengers report the "
                "destruction of the Yuan grain fleet, then clenches his fist in resolve"
            ),
        ),
        HistoricalEvent(
            event_id="chuong_duong_ham_tu_1285",
            name="Chiến thắng Chương Dương – Hàm Tử",
            year="1285",
            kind="battle",
            aliases=("chương dương", "hàm tử", "tây kết", "toa đô", "thoát hoan", "ống đồng", "sát thát"),
            summary=(
                "Mùa hè năm 1285 quân ta phản công: Trần Nhật Duật thắng ở Hàm Tử, Trần Quang Khải thắng ở Chương "
                "Dương rồi giải phóng Thăng Long; Toa Đô tử trận ở Tây Kết; Thoát Hoan phải chui vào ống đồng cho "
                "quân khiêng chạy về nước. Cuộc kháng chiến lần thứ hai toàn thắng."
            ),
            visual_brief=(
                "Red River crossings at Chương Dương and Hàm Tử, 1285: Đại Việt war boats storming Yuan positions on "
                "the riverbanks, soldiers with 'Sát Thát' tattoos charging with spears, burning Yuan camps, the walls "
                "of Thăng Long in the distance."
            ),
            hero_action=(
                "the hero from the reference image on horseback on the riverbank, pointing his sword toward Thăng "
                "Long and ordering the counter-offensive"
            ),
        ),
        HistoricalEvent(
            event_id="hich_tuong_si",
            name="Viết Hịch tướng sĩ",
            year="khoảng 1284",
            kind="event",
            aliases=("hịch tướng sĩ", "dụ chư tì tướng", "viết hịch", "bài hịch", "binh thư yếu lược"),
            summary=(
                "Trước cuộc kháng chiến lần thứ hai, Hưng Đạo Vương viết Hịch tướng sĩ khích lệ tướng sĩ: nêu gương "
                "trung nghĩa, vạch tội ác của giặc, phê phán thói ăn chơi hưởng lạc, kêu gọi tập luyện Binh thư yếu "
                "lược để giữ nước."
            ),
            visual_brief=(
                "Inside a Trần-era wooden command hall at Vạn Kiếp by lamplight: generals in armor kneel in rows as "
                "the proclamation is read aloud, silk banners, weapon racks, candle smoke curling, fervent faces."
            ),
            hero_action=(
                "the hero from the reference image writes on a long scroll with a brush, then raises the finished "
                "proclamation before his kneeling officers"
            ),
        ),
        HistoricalEvent(
            event_id="dien_hong_1284",
            name="Hội nghị Diên Hồng",
            year="1284",
            kind="event",
            aliases=("diên hồng", "hội nghị diên hồng", "bô lão"),
            summary=(
                "Cuối năm 1284, Thượng hoàng Trần Thánh Tông mời các bô lão về điện Diên Hồng hỏi kế nên đánh hay "
                "nên hòa; muôn người như một đồng thanh hô «Đánh!», thể hiện ý chí toàn dân kháng chiến."
            ),
            visual_brief=(
                "The Diên Hồng hall of the Trần court, 1284: white-haired village elders in brown robes raising their "
                "fists and shouting 'Fight!', the retired emperor on the dais, red lacquered pillars, banners, dust "
                "motes in shafts of light."
            ),
            hero_action=(
                "the hero from the reference image stands beside the throne, gripping his sword hilt as the elders "
                "roar their decision"
            ),
        ),
        HistoricalEvent(
            event_id="khang_chien_mong_nguyen",
            name="Ba lần kháng chiến chống Mông – Nguyên",
            year="1258, 1285, 1287–1288",
            kind="battle",
            aliases=(
                "mông cổ", "mông nguyên", "nguyên mông", "quân nguyên", "giặc nguyên", "quân mông",
                "kháng chiến chống nguyên", "kháng chiến chống mông nguyên",
            ),
            summary=(
                "Ba lần Đại Việt đánh bại quân Mông – Nguyên: năm 1258 dưới thời vua Trần Thái Tông; năm 1285 và "
                "1287–1288 do Hưng Đạo Vương làm Quốc công Tiết chế thống lĩnh, với kế sách vườn không nhà trống, "
                "tránh thế mạnh ban đầu của giặc, chờ giặc mỏi mệt, thiếu lương rồi phản công."
            ),
            visual_brief=(
                "Sweeping panorama of Đại Việt resisting the Mongol-Yuan invasions: evacuated villages, Trần troops "
                "moving through misty mountains and rivers, war elephants, red banners, Yuan cavalry bogged down "
                "in flooded paddies."
            ),
            hero_action=(
                "the hero from the reference image on a hilltop with his generals, pointing across the river valley "
                "while issuing orders"
            ),
        ),
    ),
)

_NGUYEN_TRAI = CharacterProfile(
    character_id="nguyen_trai",
    display_name="Nguyễn Trãi",
    full_name="Nguyễn Trãi, hiệu Ức Trai",
    name_aliases=("Nguyễn Trãi", "Ức Trai", "Lê Trãi"),
    titles=("Ức Trai", "Quan phục hầu", "quân sư của nghĩa quân Lam Sơn"),
    era="cuối đời Trần – Hồ đến đầu thời Lê sơ, thế kỷ XIV–XV",
    era_en="early 15th-century Đại Việt (Lam Sơn uprising and early Lê dynasty)",
    birth="năm 1380 (năm Canh Thân)",
    death=(
        "năm 1442 (năm Nhâm Tuất), bị khép oan trong vụ án Lệ Chi Viên và chịu án tru di tam tộc; năm 1464 vua "
        "Lê Thánh Tông minh oan"
    ),
    homeland=(
        "quê gốc làng Chi Ngại (vùng Chí Linh ngày nay, dưới chân núi Côn Sơn), sau dời về làng Nhị Khê, huyện "
        "Thượng Phúc (nay thuộc vùng Thường Tín, Hà Nội)"
    ),
    homeland_en="Chi Ngại village beneath the Côn Sơn hills (Chí Linh), his ancestral land, and Nhị Khê village",
    homeland_aliases=("chi ngại", "nhị khê", "chí linh", "côn sơn", "thượng phúc", "thường tín"),
    homeland_visual=(
        "Chi Ngại village at the foot of the Côn Sơn hills (Chí Linh), the ancestral land of Nguyễn Trãi: pine "
        "forests swaying on the slopes, a clear stream tumbling over rocks, bamboo groves and a simple thatched "
        "scholar's house with an ink table, mist drifting across the early-15th-century Đại Việt countryside."
    ),
    costume_hint=(
        "early Lê-dynasty (15th century) scholar-official's robe and headwear, exactly as in the reference image"
    ),
    anachronisms=("modern rifles", "modern buildings", "electric lights", "modern clothing", "plastic objects"),
    self_pronoun="ta",
    listener_address="người",
    speaking_style=(
        "điềm đạm, thâm trầm, uyên bác của bậc đại nho và nhà chiến lược; trọng nhân nghĩa, lấy dân làm gốc; lời "
        "lẽ giàu hình ảnh thiên nhiên (trúc, mai, suối Côn Sơn), hay dùng điển cố và từ Hán Việt nhưng vẫn mềm mại, "
        "khiêm cung"
    ),
    signature_lines=(
        "Việc nhân nghĩa cốt ở yên dân, quân điếu phạt trước lo trừ bạo (Bình Ngô đại cáo)",
        "Lấy đại nghĩa để thắng hung tàn, lấy chí nhân để thay cường bạo (Bình Ngô đại cáo)",
    ),
    key_facts=(
        "Cha là Nguyễn Phi Khanh, mẹ là bà Trần Thị Thái, con gái Tư đồ Trần Nguyên Đán.",
        "Năm 1400 đỗ Thái học sinh dưới triều Hồ, làm quan Ngự sử đài chánh chưởng.",
        "Năm 1407 giặc Minh xâm lược, cha bị bắt giải sang Trung Quốc; tương truyền ở ải Nam Quan, cha dặn ông "
        "quay về lo trả thù nhà, đền nợ nước.",
        "Ông tìm vào Lam Sơn theo Lê Lợi, dâng Bình Ngô sách, làm quân sư; soạn thư từ, hiểu dụ trong Quân trung "
        "từ mệnh tập, chủ trương «mưu phạt tâm công» — đánh vào lòng người.",
        "Đầu năm 1428 thay Lê Lợi soạn Bình Ngô đại cáo; được ban quốc tính họ Lê và tước Quan phục hầu.",
        "Tác phẩm: Bình Ngô đại cáo, Quân trung từ mệnh tập, Ức Trai thi tập, Quốc âm thi tập (thơ chữ Nôm), Dư "
        "địa chí.",
        "Về sau bị gièm pha, ông lui về ở ẩn tại Côn Sơn; năm 1980 UNESCO kỷ niệm 600 năm ngày sinh của ông như "
        "một danh nhân văn hóa.",
    ),
    events=(
        HistoricalEvent(
            event_id="chi_lang_xuong_giang_1427",
            name="Chiến thắng Chi Lăng – Xương Giang",
            year="1427",
            kind="battle",
            aliases=("chi lăng", "xương giang", "liễu thăng", "mã yên", "ải chi lăng", "mộc thạnh", "viện binh"),
            summary=(
                "Cuối năm 1427, nhà Minh sai Liễu Thăng và Mộc Thạnh chia hai đường đem khoảng 15 vạn viện binh "
                "sang cứu thành Đông Quan. Nghĩa quân Lam Sơn bày trận mai phục ở ải Chi Lăng, Liễu Thăng trúng "
                "phục binh, tử trận ở gò Mã Yên; quân ta tiếp tục chặn đánh ở Cần Trạm, Phố Cát rồi vây diệt giặc ở "
                "thành Xương Giang. Mộc Thạnh nghe tin cũng tháo chạy. Nguyễn Trãi bày mưu và soạn thư dụ hàng các "
                "tướng giặc."
            ),
            visual_brief=(
                "The narrow Chi Lăng pass in Lạng Sơn, autumn 1427: Ming cavalry strung out on a muddy road between "
                "jagged limestone peaks, Lam Sơn soldiers springing from ambush in the forest, spears and crossbows, "
                "Liễu Thăng's banner falling, mist and rain."
            ),
            hero_action=(
                "the hero from the reference image in a strategist's tent on the heights above the pass, pointing "
                "at a war map and dispatching messengers while the ambush unfolds below"
            ),
        ),
        HistoricalEvent(
            event_id="tot_dong_chuc_dong_1426",
            name="Chiến thắng Tốt Động – Chúc Động",
            year="1426",
            kind="battle",
            aliases=("tốt động", "chúc động", "vương thông"),
            summary=(
                "Cuối năm 1426, nghĩa quân Lam Sơn mai phục đánh tan quân Minh của Vương Thông ở Tốt Động – Chúc "
                "Động (vùng Chương Mỹ, Hà Nội ngày nay), diệt hàng vạn quân, buộc Vương Thông rút vào cố thủ thành "
                "Đông Quan."
            ),
            visual_brief=(
                "Waterlogged fields and marshes of Tốt Động – Chúc Động, 1426: Lam Sơn soldiers rising from the reeds "
                "to ambush Ming columns crossing narrow dikes, broken bridges, soldiers floundering in mud, "
                "rain-heavy skies."
            ),
            hero_action=(
                "the hero from the reference image on a small rise beside the Lam Sơn commanders, studying the "
                "battlefield and directing the ambush with a raised hand"
            ),
        ),
        HistoricalEvent(
            event_id="dong_quan_hoi_the_1427",
            name="Hội thề Đông Quan",
            year="1427",
            kind="event",
            aliases=("đông quan", "hội thề đông quan", "hội thề"),
            summary=(
                "Tháng 12 năm 1427, sau khi viện binh tan vỡ, Vương Thông xin hòa; hai bên hội thề ở phía nam thành "
                "Đông Quan. Nghĩa quân cấp thuyền, lương, ngựa cho quân Minh rút về nước, thể hiện tư tưởng nhân "
                "nghĩa «mở đường hiếu sinh»."
            ),
            visual_brief=(
                "On the riverbank south of Đông Quan citadel (Thăng Long), winter 1427: an altar with incense and "
                "banners, Ming generals in dark armor bowing before the Lam Sơn leaders, boats and horses prepared "
                "for the Ming withdrawal, calm grey river."
            ),
            hero_action=(
                "the hero from the reference image reads the oath text aloud at the altar, scroll in hand, with "
                "dignified composure"
            ),
        ),
        HistoricalEvent(
            event_id="binh_ngo_dai_cao_1428",
            name="Soạn Bình Ngô đại cáo",
            year="1428",
            kind="event",
            aliases=("bình ngô đại cáo", "đại cáo", "viết đại cáo", "soạn đại cáo", "tuyên ngôn độc lập"),
            summary=(
                "Đầu năm 1428, Nguyễn Trãi thay Lê Lợi soạn Bình Ngô đại cáo, bố cáo thiên hạ việc dẹp yên giặc "
                "Minh: khẳng định nền văn hiến, cương vực, phong tục riêng của nước Đại Việt, tổng kết cuộc kháng "
                "chiến mười năm và tư tưởng «việc nhân nghĩa cốt ở yên dân»; hậu thế coi là áng thiên cổ hùng văn."
            ),
            visual_brief=(
                "A Lê-dynasty pavilion in Thăng Long, 1428: the proclamation brushed in bold Hán characters on a long "
                "scroll, an inkstone and brushes on a low lacquered table, morning light through wooden lattices, "
                "victory banners outside."
            ),
            hero_action=(
                "the hero from the reference image kneels at a low desk, writing the proclamation with a large brush "
                "in bold, decisive strokes"
            ),
        ),
        HistoricalEvent(
            event_id="le_chi_vien_1442",
            name="Vụ án Lệ Chi Viên",
            year="1442",
            kind="event",
            aliases=("lệ chi viên", "trại vải", "tru di tam tộc", "nguyễn thị lộ", "vụ án"),
            summary=(
                "Năm 1442, vua Lê Thái Tông đi duyệt quân ở Chí Linh, ghé Côn Sơn, trên đường về đột ngột băng hà ở "
                "Lệ Chi Viên (Trại Vải). Triều đình khép Nguyễn Trãi và bà Nguyễn Thị Lộ tội giết vua, xử tru di "
                "tam tộc. Năm 1464 vua Lê Thánh Tông minh oan, khen «Ức Trai tâm thượng quang Khuê tảo»."
            ),
            visual_brief=(
                "A lychee garden (Lệ Chi Viên) at night in 1442: lanterns glowing among dark lychee trees, royal "
                "guards with torches, a tense and tragic atmosphere, drifting mist."
            ),
            hero_action=(
                "the hero from the reference image stands calm and upright as royal guards arrive with the edict, "
                "his face showing quiet sorrow but no fear"
            ),
        ),
        HistoricalEvent(
            event_id="lam_son_khoi_nghia",
            name="Khởi nghĩa Lam Sơn",
            year="1418–1427",
            kind="battle",
            aliases=(
                "lam sơn", "khởi nghĩa lam sơn", "nghĩa quân lam sơn", "lê lợi", "bình ngô sách", "giặc minh",
                "quân minh",
            ),
            summary=(
                "Năm 1418 Lê Lợi dựng cờ khởi nghĩa ở Lam Sơn (Thanh Hóa). Nguyễn Trãi tìm vào Lam Sơn dâng Bình "
                "Ngô sách, trở thành quân sư; ông soạn thư dụ hàng các thành, chủ trương «mưu phạt tâm công», góp "
                "phần đưa cuộc khởi nghĩa mười năm đến toàn thắng năm 1427."
            ),
            visual_brief=(
                "The forested hills of Lam Sơn in Thanh Hóa: an insurgent camp of bamboo huts and campfires, Lam Sơn "
                "banners, soldiers training with spears, war elephants by a stream, morning mist."
            ),
            hero_action=(
                "the hero from the reference image presents a strategy scroll to the Lam Sơn leader by a campfire, "
                "then drafts letters by lamplight"
            ),
        ),
    ),
)

_QUANG_TRUNG = CharacterProfile(
    character_id="quang_trung",
    display_name="Quang Trung – Nguyễn Huệ",
    full_name="Nguyễn Huệ (còn có tên Nguyễn Quang Bình), niên hiệu Quang Trung",
    name_aliases=("Quang Trung", "Nguyễn Huệ", "Nguyễn Quang Bình", "vua Quang Trung", "hoàng đế Quang Trung"),
    titles=("Hoàng đế Quang Trung", "Bắc Bình Vương", "anh hùng áo vải đất Tây Sơn"),
    era="thời Tây Sơn, cuối thế kỷ XVIII",
    era_en="late 18th-century Tây Sơn era Đại Việt",
    birth="năm 1753 (năm Quý Dậu)",
    death=(
        "ngày 29 tháng 7 năm Nhâm Tý (16 tháng 9 năm 1792) tại kinh đô Phú Xuân, hưởng dương 40 tuổi theo cách "
        "tính tuổi ta"
    ),
    homeland=(
        "ấp Kiên Mỹ, huyện Tây Sơn, phủ Quy Nhơn (vùng Tây Sơn – Bình Định xưa); tổ tiên vốn họ Hồ ở làng Thái "
        "Lão, huyện Hưng Nguyên, Nghệ An"
    ),
    homeland_en="Kiên Mỹ hamlet, Tây Sơn district, Quy Nhơn prefecture (old Bình Định)",
    homeland_aliases=("kiên mỹ", "tây sơn", "quy nhơn", "bình định", "sông côn", "thái lão", "nghệ an"),
    homeland_visual=(
        "Kiên Mỹ hamlet in Tây Sơn (old Bình Định), birthplace of the Tây Sơn brothers in the 18th century: an "
        "ancient tamarind tree and a stone well before a wooden house, rice fields and sugarcane along the Côn "
        "River, the misty mountains of the Tây Sơn upper route on the horizon, palm leaves rustling in the wind."
    ),
    costume_hint="Tây Sơn (late 18th century) emperor-general's attire, exactly as in the reference image",
    anachronisms=("modern rifles", "modern buildings", "electric lights", "modern clothing", "plastic objects"),
    self_pronoun="ta",
    listener_address="ngươi",
    speaking_style=(
        "mạnh mẽ, quyết đoán, thẳng thắn của vị hoàng đế áo vải xuất thân từ chiến trận; giọng hiệu lệnh dứt khoát "
        "nhưng gần gũi với dân, với lính; khi nói việc nước thì hùng hồn, khi nói việc dân thì ân cần"
    ),
    signature_lines=(
        "Đánh cho để dài tóc, đánh cho để đen răng, đánh cho nó chích luân bất phản, đánh cho nó phiến giáp bất "
        "hoàn, đánh cho sử tri Nam quốc anh hùng chi hữu chủ (lời dụ tướng sĩ, tương truyền)",
    ),
    key_facts=(
        "Cha là Hồ Phi Phúc, mẹ là Nguyễn Thị Đồng; ba anh em Nguyễn Nhạc, Nguyễn Huệ, Nguyễn Lữ được gọi là "
        "anh em Tây Sơn.",
        "Năm 1771 cùng anh em dựng cờ khởi nghĩa ở Tây Sơn thượng đạo (vùng An Khê), lấy của nhà giàu chia cho "
        "dân nghèo.",
        "Năm 1785 đánh tan khoảng 5 vạn quân Xiêm ở Rạch Gầm – Xoài Mút trên sông Tiền.",
        "Năm 1786 tiến quân ra Bắc với danh nghĩa «phù Lê diệt Trịnh», lật đổ chúa Trịnh; vua Lê Hiển Tông gả "
        "công chúa Lê Ngọc Hân cho ông.",
        "Ngày 22 tháng 12 năm 1788 (25 tháng 11 năm Mậu Thân) lên ngôi hoàng đế ở núi Bân (Phú Xuân), lấy niên "
        "hiệu Quang Trung, rồi thần tốc tiến quân ra Bắc đánh 29 vạn quân Thanh do Tôn Sĩ Nghị chỉ huy.",
        "Tết Kỷ Dậu năm 1789 đại phá quân Thanh ở Ngọc Hồi – Đống Đa, trưa mùng 5 Tết tiến vào Thăng Long.",
        "Sau chiến thắng, ban Chiếu khuyến nông, Chiếu lập học, đề cao chữ Nôm, lập Sùng chính viện do La Sơn "
        "Phu tử Nguyễn Thiếp đứng đầu, đóng đô ở Phú Xuân.",
    ),
    events=(
        HistoricalEvent(
            event_id="ngoc_hoi_dong_da_1789",
            name="Đại phá quân Thanh – Ngọc Hồi, Đống Đa",
            year="1789",
            kind="battle",
            aliases=(
                "ngọc hồi", "đống đa", "hà hồi", "khương thượng", "tôn sĩ nghị", "sầm nghi đống", "quân thanh",
                "giặc thanh", "kỷ dậu", "tết kỷ dậu", "tam điệp", "gián khẩu", "đại phá quân thanh",
            ),
            summary=(
                "Cuối năm Mậu Thân, ta cho quân ăn Tết trước ở Tam Điệp, hẹn mùng 7 Tết vào Thăng Long. Đêm 30 Tết "
                "vượt sông Gián Khẩu; đêm mùng 3 vây đồn Hà Hồi, quân Thanh xin hàng; mờ sáng mùng 5, ta cưỡi voi "
                "đốc chiến đánh đồn Ngọc Hồi, quân sĩ khiêng những tấm ván ghép bọc rơm ướt che tên đạn mà xông "
                "lên, cùng voi chiến và hỏa hổ; cùng lúc Đô đốc Long đánh đồn Khương Thượng (Đống Đa), Sầm Nghi "
                "Đống thắt cổ tự tử. Tôn Sĩ Nghị hoảng sợ bỏ chạy qua sông Hồng, cầu phao gãy, quân Thanh chết đuối "
                "rất nhiều. Trưa mùng 5 Tết Kỷ Dậu (30/1/1789) ta vào Thăng Long, sớm hơn hẹn hai ngày."
            ),
            visual_brief=(
                "Ngọc Hồi south of Thăng Long at dawn on the fifth day of Tết Kỷ Dậu (1789): Tây Sơn soldiers advancing "
                "behind huge wooden shields wrapped in wet straw, war elephants charging the Qing fortifications, "
                "fire-lances (hỏa hổ) spitting flame, cannon smoke, peach blossoms, the Qing camp in flames."
            ),
            hero_action=(
                "the hero from the reference image rides a war elephant at the head of his army, sword raised, "
                "commanding the charge on the Ngọc Hồi fortress"
            ),
        ),
        HistoricalEvent(
            event_id="rach_gam_xoai_mut_1785",
            name="Trận Rạch Gầm – Xoài Mút",
            year="1785",
            kind="battle",
            aliases=("rạch gầm", "xoài mút", "quân xiêm", "giặc xiêm", "sông tiền", "mỹ tho"),
            summary=(
                "Tháng 1 năm 1785, khoảng 5 vạn quân Xiêm cùng 300 chiến thuyền theo lời cầu viện của Nguyễn Ánh "
                "tiến vào Gia Định. Ta chọn khúc sông Tiền từ Rạch Gầm đến Xoài Mút (Mỹ Tho) làm trận địa mai phục, "
                "giấu thuyền chiến trong các rạch và cù lao, bố trí quân trên bờ; sáng 20 tháng 1 năm 1785 nhử địch "
                "vào trận địa rồi đánh ập từ nhiều phía, quân Xiêm gần như bị tiêu diệt, chỉ vài nghìn tàn quân "
                "chạy thoát về nước."
            ),
            visual_brief=(
                "The Tiền River near Mỹ Tho at dawn, January 1785: a fleet of Siamese war boats sailing into the stretch "
                "between Rạch Gầm and Xoài Mút, Tây Sơn boats bursting out of hidden canals and islet groves, guns "
                "firing from the banks, coconut palms, smoke over brown water."
            ),
            hero_action=(
                "the hero from the reference image stands on a Tây Sơn war boat, signalling the ambush with a war drum "
                "and a raised sword"
            ),
        ),
        HistoricalEvent(
            event_id="len_ngoi_nui_ban_1788",
            name="Lên ngôi hoàng đế ở núi Bân",
            year="1788",
            kind="event",
            aliases=("núi bân", "lên ngôi", "đăng quang", "lên ngôi hoàng đế", "niên hiệu quang trung"),
            summary=(
                "Ngày 25 tháng 11 năm Mậu Thân (22/12/1788), khi nghe tin 29 vạn quân Thanh chiếm Thăng Long, Nguyễn "
                "Huệ lập đàn ở núi Bân (phía nam Phú Xuân), tế cáo trời đất, lên ngôi hoàng đế, đặt niên hiệu Quang "
                "Trung, rồi lập tức thống lĩnh đại quân tiến ra Bắc."
            ),
            visual_brief=(
                "An open-air altar on Bân hill south of Phú Xuân (Huế), December 1788: yellow banners, incense smoke, "
                "ranks of Tây Sơn troops, drums and gongs, the Perfume River glinting in the distance."
            ),
            hero_action=(
                "the hero from the reference image ascends the altar steps and raises a ceremonial sword to proclaim "
                "the Quang Trung era before his kneeling army"
            ),
        ),
        HistoricalEvent(
            event_id="phu_le_diet_trinh_1786",
            name="Tiến ra Bắc «phù Lê diệt Trịnh»",
            year="1786",
            kind="event",
            aliases=("phù lê diệt trịnh", "diệt trịnh", "chúa trịnh", "ngọc hân"),
            summary=(
                "Năm 1786, Nguyễn Huệ tiến quân ra Bắc dưới danh nghĩa «phù Lê diệt Trịnh», lật đổ chúa Trịnh; vua "
                "Lê Hiển Tông gả công chúa Lê Ngọc Hân cho ông."
            ),
            visual_brief=(
                "Thăng Long in summer 1786: Tây Sơn troops entering the citadel gates, the Trịnh lords' palace in "
                "disarray, Lê court officials receiving the army, lotus ponds and royal pavilions."
            ),
            hero_action=(
                "the hero from the reference image rides into the Thăng Long citadel at the head of his army, "
                "saluting the Lê court"
            ),
        ),
        HistoricalEvent(
            event_id="khoi_nghia_tay_son_1771",
            name="Khởi nghĩa Tây Sơn",
            year="1771",
            kind="event",
            aliases=("khởi nghĩa tây sơn", "dựng cờ khởi nghĩa", "tây sơn thượng đạo", "an khê"),
            summary=(
                "Năm 1771, ba anh em Nguyễn Nhạc, Nguyễn Huệ, Nguyễn Lữ dựng cờ khởi nghĩa ở Tây Sơn thượng đạo "
                "(vùng An Khê), lấy của nhà giàu chia cho dân nghèo, được nhân dân khắp nơi hưởng ứng."
            ),
            visual_brief=(
                "The highland of the Tây Sơn upper route (An Khê) in 1771: farmers and highland villagers gathering "
                "with bamboo spears and banners, a rebel camp on the hills, rice being shared with the poor, rugged "
                "green mountains."
            ),
            hero_action=(
                "the hero from the reference image raises the uprising banner on a hilltop and rallies the farmers "
                "around him"
            ),
        ),
    ),
)

CHARACTERS: Mapping[str, CharacterProfile] = MappingProxyType(
    {profile.character_id: profile for profile in (_TRAN_HUNG_DAO, _NGUYEN_TRAI, _QUANG_TRUNG)}
)
SUPPORTED_CHARACTER_IDS: tuple[str, ...] = tuple(CHARACTERS)


def get_character(character_id: str) -> CharacterProfile:
    """Trả về hồ sơ nhân vật; chỉ chấp nhận tran_hung_dao, nguyen_trai, quang_trung."""
    key = str(character_id or "").strip().lower()
    profile = CHARACTERS.get(key)
    if profile is None:
        raise UnsupportedCharacterError(
            f"Nhân vật '{character_id}' không được hỗ trợ. Chỉ có: {', '.join(SUPPORTED_CHARACTER_IDS)}."
        )
    return profile


# --------------------------------------------------------------------------------------
# Gom delta thành cụm chữ (phục vụ đồng bộ cử động miệng)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Phrase:
    text: str
    char_start: int
    char_end: int
    boundary: str  # "sentence" | "paragraph" | "clause" | "soft" | "end"


class PhraseChunker:
    """Gom văn bản stream thành cụm chữ: ngắt sau dấu câu + khoảng trắng, hoặc sau tối đa ``max_words`` từ.

    Không làm mất ký tự: nối ``text`` của mọi cụm theo thứ tự = toàn bộ văn bản đã ``feed``.
    Dấu câu ở cuối bộ đệm được giữ lại chờ ký tự kế tiếp (để không cắt nhầm số như ``1.288``).
    """

    def __init__(self, max_words: int = 6) -> None:
        if max_words < 1:
            raise ValueError("max_words phải >= 1")
        self.max_words = max_words
        self._buffer = ""
        self._offset = 0

    @property
    def consumed(self) -> int:
        """Số ký tự đã phát thành cụm chữ."""
        return self._offset

    def feed(self, text: str) -> list[Phrase]:
        if text:
            self._buffer += text
        return self._drain(final=False)

    def flush(self) -> list[Phrase]:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[Phrase]:
        phrases: list[Phrase] = []
        while self._buffer:
            cut = self._find_cut(self._buffer, final=final)
            if cut is None:
                break
            end, boundary = cut
            piece = self._buffer[:end]
            phrases.append(Phrase(piece, self._offset, self._offset + end, boundary))
            self._offset += end
            self._buffer = self._buffer[end:]
        return phrases

    def _find_cut(self, buffer: str, *, final: bool) -> tuple[int, str] | None:
        tokens = list(scan_text(buffer))
        words = 0
        for position, token in enumerate(tokens):
            if token.kind == "word":
                # "1.288" = word + punct + word: phần sau dấu chấm/phẩy liền kề vẫn là cùng một từ.
                glued = position > 0 and tokens[position - 1].kind == "punct" and words > 0
                if not glued:
                    words += 1
            elif token.kind == "nl":
                if words:
                    return token.end, "paragraph"
            elif token.kind == "punct":
                boundary = "sentence" if is_sentence_punct(token.text) else "clause"
                following = tokens[position + 1] if position + 1 < len(tokens) else None
                if following is None:
                    if final:
                        return token.end, boundary
                    return None
                if following.kind == "ws":
                    return following.end, boundary
                if following.kind == "nl":
                    return following.end, "paragraph"
            elif token.kind == "ws" and words >= self.max_words:
                return token.end, "soft"
        if final:
            return len(buffer), "end"
        return None


@dataclass(frozen=True, slots=True)
class SpeechChunk:
    """Một cụm lời thoại phát cho frontend để hiện phụ đề và cử động miệng."""

    seq: int
    text: str
    char_start: int
    char_end: int
    syllables: int
    speak_ms: int  # thời lượng cử động miệng gợi ý
    pause_ms: int  # nghỉ sau cụm (dấu phẩy / dấu chấm)
    boundary: str
    is_final: bool
    character_id: str
    key_index: int
    key_label: str
    model: str
    elapsed_ms: float  # thời điểm phát, tính từ lúc gửi câu hỏi


@dataclass(frozen=True, slots=True)
class ActorAnswer:
    character_id: str
    question: str
    text: str
    chunks: tuple[SpeechChunk, ...]
    key_index: int
    key_label: str
    model: str
    first_chunk_ms: float | None
    total_ms: float
    total_speech_ms: int


# --------------------------------------------------------------------------------------
# Actor Agent
# --------------------------------------------------------------------------------------
class ActorAgent:
    """AI nhập vai nhân vật lịch sử, trả lời streaming theo từng cụm chữ."""

    # Chuỗi model ưu tiên: model sau là dự phòng khi model trước quá tải (503) / timeout / ngừng cung cấp.
    DEFAULT_MODELS: tuple[str, ...] = ("gemini-3.5-flash", "gemini-3-flash-preview", "gemini-3.5-flash-lite")

    def __init__(
        self,
        pool: GeminiKeyPool,
        *,
        models: Sequence[str] = DEFAULT_MODELS,
        temperature: float = 0.55,
        max_output_tokens: int = 900,
        thinking_level: str | None = "minimal",
        max_words_per_chunk: int = 6,
        ms_per_syllable: int = DEFAULT_MS_PER_SYLLABLE,
        request_timeout_s: float = 12.0,
    ) -> None:
        if not models:
            raise ValueError("Cần ít nhất một model cho AI Nhân vật.")
        if max_words_per_chunk < 1 or ms_per_syllable <= 0 or request_timeout_s <= 0:
            raise ValueError("max_words_per_chunk, ms_per_syllable và request_timeout_s phải dương.")
        self._pool = pool
        self.models = tuple(models)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.thinking_level = thinking_level
        self.max_words_per_chunk = max_words_per_chunk
        self.ms_per_syllable = ms_per_syllable
        # Thời gian chờ tối đa cho byte đầu tiên / giữa hai cụm stream trước khi failover sang key/model khác.
        self.request_timeout_s = request_timeout_s

    # ------------------------------------------------------------------ prompt
    def build_system_prompt(self, character_id: str) -> str:
        profile = get_character(character_id)
        facts = "\n".join(f"- {fact}" for fact in profile.key_facts)
        events = "\n".join(f"- {event.name} ({event.year}): {event.summary}" for event in profile.events)
        lines = "\n".join(f"- «{line}»" for line in profile.signature_lines)
        return (
            f"Ngươi là anh linh của {profile.display_name} ({profile.full_name}), {profile.era}, đang trò chuyện "
            "với hậu thế trong ứng dụng giáo dục lịch sử \"Đại Việt Sử Ký\".\n\n"
            "NHẬP VAI\n"
            f"- Luôn nói ở ngôi thứ nhất, xưng \"{profile.self_pronoun}\", gọi người hỏi là "
            f"\"{profile.listener_address}\".\n"
            f"- Văn phong: {profile.speaking_style}.\n"
            "- Lời lẽ cổ kính, trang trọng của người xưa nhưng học sinh ngày nay vẫn hiểu được; không dùng tiếng "
            "lóng, từ ngữ hiện đại hay tiếng nước ngoài.\n"
            "- Ngươi biết rõ cuộc đời mình từ lúc sinh tới lúc mất và biết hậu thế ghi nhớ mình ra sao. Việc xảy ra "
            "sau khi mất chỉ nói là \"nghe hậu thế kể lại\"; không bàn chuyện chính trị đương thời.\n\n"
            "CHUẨN XÁC LỊCH SỬ (bắt buộc)\n"
            "- Chỉ khẳng định những điều khớp với chính sử (Đại Việt sử ký toàn thư, Khâm định Việt sử thông giám "
            "cương mục...) và SỔ TAY SỬ LIỆU bên dưới.\n"
            "- Năm sinh, năm mất, quê quán, tên người, tên đất, niên hiệu phải đúng như sổ tay. Điều gì sử sách còn "
            "nhiều thuyết thì nêu thuyết phổ biến trước rồi nói rõ còn thuyết khác.\n"
            "- Không bịa số liệu, ngày tháng, lời nói hay nhân vật. Điều gì sử không chép rõ thì nói thẳng là sử "
            "sách chép chưa tường.\n"
            "- Câu hỏi gán nhầm sự kiện cho ngươi thì ôn tồn đính chính. Trận đánh ngươi không trực tiếp cầm quân "
            "thì kể như người đời sau nhìn lại chiến tích, không nhận là mình đã đánh.\n\n"
            "GIỚI HẠN NHẬN THỨC THỜI ĐẠI (bắt buộc)\n"
            "- Khi câu hỏi nhắc tới quốc gia, địa danh, sự kiện, công trình hay khái niệm mà sinh thời ngươi chưa "
            "thể biết (ví dụ nước Nga, châu Mỹ, máy bay...), câu ĐẦU TIÊN phải nói rõ: \"Thời ta chưa biết đến xứ "
            "ấy...\" (hoặc lời cổ phong tương đương khẳng định sinh thời ngươi chưa biết tới điều đó).\n"
            "- Sau câu đó mới kể tiếp theo góc nhìn hậu thế (\"nghe hậu thế kể lại...\"), ngắn gọn, đúng sự thật, "
            "rồi có thể liên hệ với điều ngươi từng trải.\n\n"
            f"SỔ TAY SỬ LIỆU — {profile.display_name.upper()}\n"
            f"- Tên: {profile.full_name}. Tước hiệu, danh xưng: {', '.join(profile.titles)}.\n"
            f"- Sinh: {profile.birth}.\n"
            f"- Mất: {profile.death}.\n"
            f"- Quê quán: {profile.homeland}.\n"
            f"{facts}\n"
            "Sự kiện tiêu biểu:\n"
            f"{events}\n"
            "Câu nói tiêu biểu (chỉ trích khi hợp ý và trích đúng nguyên văn):\n"
            f"{lines}\n\n"
            "CÁCH TRẢ LỜI (lời thoại sẽ hiện thành phụ đề và đồng bộ cử động miệng)\n"
            "- Chỉ viết lời nói thuần văn bản bằng tiếng Việt: không markdown, không gạch đầu dòng, không tiêu đề, "
            "không emoji, không mô tả hành động trong ngoặc.\n"
            "- Câu ngắn hoặc vừa, ngắt nhịp tự nhiên bằng dấu phẩy và dấu chấm.\n"
            "- Câu hỏi về tiểu sử (năm sinh, năm mất, quê quán, gia đình...): 3 đến 6 câu, trả lời thẳng vào điều "
            "được hỏi trước rồi mới kể thêm.\n"
            "- Câu hỏi về trận đánh hay sự kiện: 6 đến 10 câu, kể theo trình tự trước – trong – sau, nêu rõ thời "
            "gian, địa danh, kế sách và kết quả.\n"
            "- Ghi năm bằng chữ số (ví dụ 1288), có thể kèm năm âm lịch (ví dụ năm Mậu Tý)."
        )

    def build_payload(self, character_id: str, question: str) -> dict[str, Any]:
        profile = get_character(character_id)
        cleaned = " ".join(str(question or "").split())
        if not cleaned:
            raise ValueError("Câu hỏi không được để trống.")
        generation_config: dict[str, Any] = {
            "temperature": self.temperature,
            "topP": 0.9,
            "maxOutputTokens": self.max_output_tokens,
        }
        if self.thinking_level:
            generation_config["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        user_text = cleaned
        # Luật 2 chốt bằng code: câu hỏi về xứ sở / sự vật ngoài thời đại → nhắc bắt buộc mở đầu xác định ranh giới.
        boundary = detect_era_boundary(profile.character_id, cleaned)
        if boundary is not None:
            user_text = (
                f"{cleaned}\n\n[Ghi chú cho người nhập vai: câu hỏi nhắc tới {boundary.label} — điều sinh thời "
                f"{profile.display_name} chưa biết tới. Câu đầu tiên BẮT BUỘC mở đầu bằng \"{ERA_BOUNDARY_OPENING}\" "
                "rồi mới kể theo lời hậu thế kể lại. Không nhắc tới ghi chú này.]"
            )
        return {
            "systemInstruction": {"parts": [{"text": self.build_system_prompt(profile.character_id)}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": generation_config,
        }

    # ------------------------------------------------------------------ streaming
    def _make_chunk(
        self,
        seq: int,
        phrase: Phrase,
        *,
        is_final: bool,
        character_id: str,
        key_index: int,
        key_label: str,
        model: str,
        started_at: float,
    ) -> SpeechChunk:
        syllables = count_syllables(phrase.text)
        return SpeechChunk(
            seq=seq,
            text=phrase.text,
            char_start=phrase.char_start,
            char_end=phrase.char_end,
            syllables=syllables,
            speak_ms=syllables * self.ms_per_syllable,
            pause_ms=PAUSE_MS.get(phrase.boundary, 0) if phrase.text.strip() else 0,
            boundary=phrase.boundary,
            is_final=is_final,
            character_id=character_id,
            key_index=key_index,
            key_label=key_label,
            model=model,
            elapsed_ms=(time.perf_counter() - started_at) * 1000.0,
        )

    async def stream_answer(
        self,
        character_id: str,
        question: str,
        *,
        avoid_keys: Iterable[int] = (),
    ) -> AsyncIterator[SpeechChunk]:
        """Trả lời streaming từng cụm chữ; chunk cuối cùng có ``is_final=True``."""
        profile = get_character(character_id)
        payload = self.build_payload(profile.character_id, question)
        started_at = time.perf_counter()
        chunker = PhraseChunker(self.max_words_per_chunk)
        seq = 0
        key_index, key_label, model = -1, "", self.models[0]
        async for delta in self._pool.stream(
            self.models,
            payload,
            tag=f"actor:{profile.character_id}",
            avoid_keys=avoid_keys,
            request_timeout_s=self.request_timeout_s,
        ):
            key_index, key_label, model = delta.key_index, delta.key_label, delta.model
            for phrase in chunker.feed(delta.text):
                yield self._make_chunk(
                    seq,
                    phrase,
                    is_final=False,
                    character_id=profile.character_id,
                    key_index=key_index,
                    key_label=key_label,
                    model=model,
                    started_at=started_at,
                )
                seq += 1
        remaining = chunker.flush()
        if not remaining:
            end = chunker.consumed
            remaining = [Phrase("", end, end, "end")]
        for position, phrase in enumerate(remaining):
            yield self._make_chunk(
                seq,
                phrase,
                is_final=position == len(remaining) - 1,
                character_id=profile.character_id,
                key_index=key_index,
                key_label=key_label,
                model=model,
                started_at=started_at,
            )
            seq += 1

    def chunk_text(self, character_id: str, text: str) -> list[SpeechChunk]:
        """Chia một văn bản có sẵn thành các ``SpeechChunk`` cùng định dạng với ``stream_answer`` (không gọi AI)."""
        profile = get_character(character_id)
        started_at = time.perf_counter()
        chunker = PhraseChunker(self.max_words_per_chunk)
        phrases = chunker.feed(text) + chunker.flush()
        if not phrases:
            phrases = [Phrase("", 0, 0, "end")]
        return [
            self._make_chunk(
                seq,
                phrase,
                is_final=seq == len(phrases) - 1,
                character_id=profile.character_id,
                key_index=-1,
                key_label="offline",
                model="profile",
                started_at=started_at,
            )
            for seq, phrase in enumerate(phrases)
        ]

    async def answer(self, character_id: str, question: str, *, avoid_keys: Iterable[int] = ()) -> ActorAnswer:
        """Gom toàn bộ stream thành một câu trả lời hoàn chỉnh."""
        profile = get_character(character_id)
        started_at = time.perf_counter()
        chunks: list[SpeechChunk] = []
        async for chunk in self.stream_answer(profile.character_id, question, avoid_keys=avoid_keys):
            chunks.append(chunk)
        last = chunks[-1]
        return ActorAnswer(
            character_id=profile.character_id,
            question=question,
            text="".join(chunk.text for chunk in chunks),
            chunks=tuple(chunks),
            key_index=last.key_index,
            key_label=last.key_label,
            model=last.model,
            first_chunk_ms=chunks[0].elapsed_ms if chunks[0].text else None,
            total_ms=(time.perf_counter() - started_at) * 1000.0,
            total_speech_ms=sum(chunk.speak_ms + chunk.pause_ms for chunk in chunks),
        )
