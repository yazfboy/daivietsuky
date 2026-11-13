import { HeroCharacter, HeroId, SceneData, SceneMode, SuggestedPrompt } from '../types/hero';

/**
 * Dựng URL tài nguyên tĩnh theo BASE_URL của Vite: '/' ở local, '/daivietsuky/' trên GitHub Pages.
 * Nhờ vậy ảnh anh hùng, rig, meta không bị lỗi 404 khi chạy dưới https://yazfboy.github.io/daivietsuky/.
 */
const asset = (path: string): string => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`;

export const HERO_IDS: readonly HeroId[] = [
  'tran_hung_dao',
  'nguyen_trai',
  'quang_trung',
] as const;

export const HEROES: Record<HeroId, HeroCharacter> = {
  tran_hung_dao: {
    id: 'tran_hung_dao',
    name: 'Trần Hưng Đạo',
    fullName: 'Hưng Đạo Đại Vương Trần Quốc Tuấn',
    title: 'Quốc công Tiết chế · Hưng Đạo Đại Vương',
    era: 'Nhà Trần · Thế kỷ XIII',
    years: '1228 – 1300',
    homeland: 'Hương Tức Mặc, phủ Thiên Trường (Nam Định)',
    homelandVisual:
      'Tức Mặc village in Thiên Trường prefecture, 13th-century Trần dynasty: emerald rice paddies rippling in the wind, lotus ponds, wooden Trần-era halls with curved tiled roofs at dawn.',
    selfPronoun: 'Ta',
    listenerAddress: 'ngươi',
    heroImageUrl: asset('/assets/heroes/tran_hung_dao.png'),
    rigUrl: asset('/assets/heroes/tran_hung_dao.rig.json'),
    metaUrl: asset('/assets/heroes/tran_hung_dao.meta.json'),
    accentColor: '#C89D54',
    ambientGlow: 'rgba(200, 157, 84, 0.16)',
    signatureQuote:
      '“Khoan thư sức dân để làm kế sâu rễ bền gốc, đó là thượng sách giữ nước.”',
    shortBio:
      'Bậc danh tướng kiệt xuất nhà Trần, ba lần thống lĩnh quân dân Đại Việt đánh tan đế chế Mông – Nguyên, tác giả Hịch tướng sĩ và Binh thư yếu lược.',
    openingGreeting:
      'Ta là Trần Quốc Tuấn — Quốc công Tiết chế nhà Trần. Non sông Đại Việt trải ba phen lửa khói trước vó ngựa Mông – Nguyên vẫn vững vàng bờ cõi. Ngươi muốn cùng ta bàn việc binh cơ, hỏi chuyện quê hương Tức Mặc, hay ôn lại đại thắng trên sông Bạch Đằng năm Mậu Tý?',
    suggestedPrompts: [
      {
        id: 'thd_homeland',
        label: 'Thượng sách giữ nước & quê hương Tức Mặc',
        question: 'Thưa Quốc công, quê hương Tức Mặc và kế sách khoan thư sức dân có ý nghĩa thế nào với ngài?',
        expectedSceneMode: 'FOREGROUND_TALKING',
        contextKey: 'homeland',
        contextTitle: 'Hương Tức Mặc — Thiên Trường',
        contextYear: 'Thế kỷ XIII',
        backgroundType: 'HOMELAND',
        backgroundPrompt:
          'Tức Mặc village in Thiên Trường prefecture, 13th-century Trần dynasty: emerald rice paddies of the Red River delta rippling in the wind, lotus ponds, wooden Trần-era halls with curved tiled roofs at dawn.',
        characterAction:
          'the hero from the reference image stands in the foreground, waist-up, facing the viewer and speaking with calm dignity, subtle hand gestures, mouth clearly visible and unobstructed for lip-sync',
        scenePrompt:
          'Medium shot at eye level, slow push-in. The hero from the reference image /assets/heroes/tran_hung_dao.png stands in the foreground speaking calmly in front of Tức Mặc homeland.',
        reason: 'Câu hỏi về quê hương và tư tưởng trị quốc nên nhân vật đứng tiền cảnh trò chuyện (FOREGROUND_TALKING).',
        sampleAnswer:
          'Quê ta ở hương Tức Mặc, phủ Thiên Trường — vùng đất đồng bằng trù phú bên dòng Nhị Hà, nơi phát tích của cơ nghiệp nhà Trần. Trải qua ba cuộc binh đao, ta càng thấm thía một lẽ: thành cao hào sâu chẳng bằng lòng dân quy phục. Lúc lâm chung năm Canh Tý (1300), khi vua Anh Tông hỏi kế giữ nước, ta chỉ dặn một điều: Vua tôi đồng lòng, anh em hòa mục, cả nước góp sức; và trên hết phải khoan thư sức dân để làm kế sâu rễ bền gốc — ấy chính là thượng sách giữ gìn xã tắc muôn đời.',
      },
      {
        id: 'thd_bach_dang',
        label: 'Đại thắng Bạch Đằng Giang (1288)',
        question: 'Xin ngài tái hiện lại kế cắm cọc gỗ và trận quyết chiến trên sông Bạch Đằng năm 1288!',
        expectedSceneMode: 'BACKGROUND_ACTION',
        contextKey: 'bach_dang_1288',
        contextTitle: 'Trận Bạch Đằng Giang',
        contextYear: '1288',
        backgroundType: 'BATTLEFIELD',
        backgroundPrompt:
          'Bạch Đằng River estuary, 1288: the ebbing tide reveals rows of iron-tipped wooden stakes; Yuan Mongol war junks are impaled and burning; Đại Việt war boats strike from both banks under red Trần banners.',
        characterAction:
          'the hero from the reference image stands at the bow of a Trần command war boat, sword raised high, signalling the all-out attack as the tide turns',
        scenePrompt:
          'Wide cinematic shot, dynamic camera. The hero from the reference image /assets/heroes/tran_hung_dao.png stands at the bow of a Trần command war boat on Bạch Đằng River in 1288.',
        reason: 'Câu hỏi yêu cầu tái hiện trận đánh Bạch Đằng nên chuyển sang chế độ hành động toàn cảnh (BACKGROUND_ACTION).',
        sampleAnswer:
          'Ngày mùng 8 tháng 3 năm Mậu Tý (9/4/1288), khi đoàn thuyền lương của Trương Văn Hổ đã bị Trần Khánh Dư đánh đắm ở Vân Đồn, Ô Mã Nhi và Phàn Tiếp buộc phải rút quân theo đường sông Bạch Đằng. Noi gương Ngô Vương Quyền thuở trước, ta cho đóng hàng ngàn cọc gỗ lim đầu bịt sắt dưới lòng sông hiểm yếu. Lúc nước triều dâng cao che khuất bãi cọc, thuyền nhẹ của ta ra khiêu chiến rồi giả thua nhử giặc vào trận địa. Đợi đúng khắc nước triều rút mạnh, đại quân nhà Trần từ hai bờ đồng loạt đổ ra đánh ập, tên lửa bắn rợp trời. Hơn bốn trăm chiến thuyền giặc va vào cọc ngầm vỡ đắm, Ô Mã Nhi bị bắt sống, khép lại mộng xâm lăng của quân Nguyên!',
      },
      {
        id: 'thd_hich_tuong_si',
        label: 'Hào khí Hịch tướng sĩ (1284)',
        question: 'Vì sao ngài viết Hịch tướng sĩ trước cuộc kháng chiến chống Nguyên lần thứ hai?',
        expectedSceneMode: 'BACKGROUND_ACTION',
        contextKey: 'hich_tuong_si',
        contextTitle: 'Viết Hịch Tướng Sĩ tại Vạn Kiếp',
        contextYear: '1284',
        backgroundType: 'HISTORICAL_EVENT',
        backgroundPrompt:
          'Inside a Trần-era wooden command hall at Vạn Kiếp by lamplight: generals in armor kneel in rows as the proclamation is read aloud, silk banners, weapon racks, candle smoke curling.',
        characterAction:
          'the hero from the reference image writes on a long scroll with a brush, then raises the finished proclamation before his kneeling officers',
        scenePrompt:
          'Cinematic historical scene inside Vạn Kiếp command hall. The hero from the reference image /assets/heroes/tran_hung_dao.png raises the Hịch tướng sĩ scroll before his officers.',
        reason: 'Câu hỏi gắn với sự kiện lịch sử viết Hịch tướng sĩ tại Vạn Kiếp nên tái hiện sự kiện (BACKGROUND_ACTION).',
        sampleAnswer:
          'Trước họa 50 vạn quân Nguyên rập rình bờ cõi năm 1284, ta thấy trong hàng ngũ vẫn còn kẻ ham thú vui chọi gà, đánh bạc, quên việc binh thư. Đêm nơi doanh trại Vạn Kiếp, ta viết Dụ chư tì tướng hịch văn: “Ta thường tới bữa quên ăn, nửa đêm vỗ gối, ruột đau như cắt, nước mắt đầm đìa...”. Từng lời hịch là máu huyết của ta nhằm thức tỉnh nghĩa khí, truyền cho ba quân tập luyện Binh thư yếu lược, quyết khắc hai chữ “Sát Thát” lên cánh tay để thề sống chết cùng non sông!',
      },
    ],
  },
  nguyen_trai: {
    id: 'nguyen_trai',
    name: 'Nguyễn Trãi',
    fullName: 'Nguyễn Trãi · Hiệu Ức Trai',
    title: 'Quan phục hầu · Quân sư nghĩa quân Lam Sơn',
    era: 'Thời Lê sơ · Thế kỷ XIV–XV',
    years: '1380 – 1442',
    homeland: 'Chi Ngại (Côn Sơn, Chí Linh) & Nhị Khê (Thường Tín)',
    homelandVisual:
      'Chi Ngại village at the foot of the Côn Sơn hills: pine forests swaying on the slopes, a clear stream tumbling over rocks, bamboo groves and a simple thatched scholar house with an ink table in morning mist.',
    selfPronoun: 'Ta',
    listenerAddress: 'người',
    heroImageUrl: asset('/assets/heroes/nguyen_trai.png'),
    rigUrl: asset('/assets/heroes/nguyen_trai.rig.json'),
    metaUrl: asset('/assets/heroes/nguyen_trai.meta.json'),
    accentColor: '#7BA7BC',
    ambientGlow: 'rgba(123, 167, 188, 0.16)',
    signatureQuote:
      '“Việc nhân nghĩa cốt ở yên dân, quân điếu phạt trước lo trừ bạo.”',
    shortBio:
      'Bậc đại nho, nhà tư tưởng và quân sư lỗi lạc của khởi nghĩa Lam Sơn; dùng “mưu phạt tâm công” mở đường thái bình và chắp bút Bình Ngô đại cáo.',
    openingGreeting:
      'Ta là Nguyễn Trãi, hiệu Ức Trai. Cả đời ta gửi tâm huyết nơi câu chữ và vận nước, lấy đại nghĩa thắng hung tàn, lấy chí nhân thay cường bạo. Người muốn cùng ta đàm đạo về tư tưởng nhân nghĩa nơi rừng thông Côn Sơn, hay điểm lại mưu lược Bình Ngô đại cáo và trận Chi Lăng – Xương Giang?',
    suggestedPrompts: [
      {
        id: 'nt_homeland',
        label: 'Tư tưởng nhân nghĩa & Côn Sơn',
        question: 'Thưa cụ Ức Trai, tư tưởng “Việc nhân nghĩa cốt ở yên dân” và cảnh trí Côn Sơn gắn bó với cụ ra sao?',
        expectedSceneMode: 'FOREGROUND_TALKING',
        contextKey: 'homeland',
        contextTitle: 'Rừng thông & suối Côn Sơn',
        contextYear: 'Thế kỷ XV',
        backgroundType: 'HOMELAND',
        backgroundPrompt:
          'Chi Ngại village at the foot of the Côn Sơn hills: pine forests swaying on the slopes, a clear stream tumbling over rocks, bamboo groves and a scholar house with an ink table.',
        characterAction:
          'the hero from the reference image stands in the foreground, waist-up, facing the viewer and speaking with calm dignity, subtle hand gestures, mouth clearly visible and unobstructed for lip-sync',
        scenePrompt:
          'Medium shot at eye level. The hero from the reference image /assets/heroes/nguyen_trai.png stands in the foreground speaking serenely before the pine hills and stream of Côn Sơn.',
        reason: 'Câu hỏi về tư tưởng triết học và quê hương Côn Sơn nên nhân vật đứng tiền cảnh đàm đạo (FOREGROUND_TALKING).',
        sampleAnswer:
          'Suối Côn Sơn chảy rì rầm như tiếng đàn cầm, đá rêu phơi như chiếu êm, thông trúc bốn mùa xanh ngắt — đó là nơi tâm hồn ta tìm về sự tĩnh tại. Nhưng bậc kẻ sĩ đọc sách thánh hiền không thể chỉ vui thú lâm tuyền khi trăm họ còn lầm than. Với ta, nhân nghĩa không phải lời sáo rỗng trên trang giấy, mà cốt ở hai chữ “yên dân”: làm cho khắp thôn cùng xóm vắng không còn tiếng oán than, để người dân cày ruộng, dệt vải trong cảnh thái bình.',
      },
      {
        id: 'nt_binh_ngo',
        label: 'Khởi thảo Bình Ngô đại cáo (1428)',
        question: 'Khoảnh khắc cụ chấp bút soạn Bình Ngô đại cáo mùa xuân năm 1428 diễn ra như thế nào?',
        expectedSceneMode: 'BACKGROUND_ACTION',
        contextKey: 'binh_ngo_dai_cao_1428',
        contextTitle: 'Soạn Bình Ngô Đại Cáo tại Đông Kinh',
        contextYear: '1428',
        backgroundType: 'HISTORICAL_EVENT',
        backgroundPrompt:
          'A Lê-dynasty pavilion in Thăng Long, 1428: the proclamation brushed in bold characters on a long silk scroll, inkstone and brushes on a low lacquered table, morning light through wooden lattices.',
        characterAction:
          'the hero from the reference image kneels at a low desk, writing the proclamation with a large brush in bold, decisive strokes',
        scenePrompt:
          'Cinematic historical scene in Thăng Long pavilion, 1428. The hero from the reference image /assets/heroes/nguyen_trai.png writes Bình Ngô đại cáo on a long scroll.',
        reason: 'Câu hỏi yêu cầu tái hiện sự kiện chấp bút Bình Ngô đại cáo năm 1428 (BACKGROUND_ACTION).',
        sampleAnswer:
          'Mùa xuân năm Mậu Thân (1428), sau khi quân Minh đã rút về nước theo Hội thề Đông Quan, vâng mệnh đức Bình Định Vương Lê Lợi, ta trải cuộn lụa dài dưới ánh nắng ban mai nơi thành Đông Kinh. Bao nhiêu máu xương của mười năm “nếm mật nằm gai” nơi núi rừng Lam Sơn dồn cả vào ngọn bút: “Như nước Đại Việt ta từ trước, vốn xưng nền văn hiến đã lâu; núi sông bờ cõi đã chia, phong tục Bắc Nam cũng khác...”. Đó là lời tuyên cáo khẳng định chủ quyền độc lập bất khả xâm phạm của quốc gia Đại Việt!',
      },
      {
        id: 'nt_chi_lang',
        label: 'Chiến thắng Chi Lăng – Xương Giang (1427)',
        question: 'Nghĩa quân Lam Sơn đã kết hợp mai phục quân sự và tâm công ở Chi Lăng – Xương Giang năm 1427 ra sao?',
        expectedSceneMode: 'BACKGROUND_ACTION',
        contextKey: 'chi_lang_xuong_giang_1427',
        contextTitle: 'Ải Chi Lăng – Xương Giang',
        contextYear: '1427',
        backgroundType: 'BATTLEFIELD',
        backgroundPrompt:
          'The narrow Chi Lăng pass in Lạng Sơn, autumn 1427: Ming cavalry strung out between jagged limestone peaks, Lam Sơn soldiers springing from ambush, mist and banners.',
        characterAction:
          'the hero from the reference image in a strategist tent on the heights above the pass, pointing at a war map and dispatching messengers while the ambush unfolds below',
        scenePrompt:
          'Wide cinematic battle scene at Chi Lăng pass, 1427. The hero from the reference image /assets/heroes/nguyen_trai.png directs the strategy above the pass.',
        reason: 'Câu hỏi về diễn biến chiến dịch Chi Lăng – Xương Giang nên chuyển sang chế độ hành động (BACKGROUND_ACTION).',
        sampleAnswer:
          'Mùa thu năm Đinh Mùi (1427), nhà Minh sai Liễu Thăng và Mộc Thạnh chia hai đạo quân đem 15 vạn viện binh sang cứu thành Đông Quan. Nắm rõ tính kiêu ngạo của Liễu Thăng, nghĩa quân Lam Sơn nhử địch lọt sâu vào ải Chi Lăng hiểm trở, chém Liễu Thăng tại núi Mã Yên, rồi vây diệt toàn bộ viện binh ở cánh đồng Xương Giang. Song hành cùng lưỡi gươm trận mạc, ta liên tiếp gửi thư vào thành Đông Quan phân tích thế thời, đánh tan ý chí cố thủ của Vương Thông — dùng “mưu phạt tâm công” để kết thúc chiến tranh mà đỡ hao tổn xương máu hai dân tộc.',
      },
    ],
  },
  quang_trung: {
    id: 'quang_trung',
    name: 'Quang Trung - Nguyễn Huệ',
    fullName: 'Hoàng đế Quang Trung · Nguyễn Huệ',
    title: 'Hoàng đế triều Tây Sơn · Bắc Bình Vương',
    era: 'Triều Tây Sơn · Cuối thế kỷ XVIII',
    years: '1753 – 1792',
    homeland: 'Ấp Kiên Mỹ, huyện Tây Sơn, phủ Quy Nhơn (Bình Định)',
    homelandVisual:
      'Kiên Mỹ hamlet in Tây Sơn (Bình Định) in the 18th century: ancient tamarind tree and stone well before a wooden house, fields along the Côn River, misty mountains of Tây Sơn upper route.',
    selfPronoun: 'Ta',
    listenerAddress: 'ngươi',
    heroImageUrl: asset('/assets/heroes/quang_trung.png'),
    rigUrl: asset('/assets/heroes/quang_trung.rig.json'),
    metaUrl: asset('/assets/heroes/quang_trung.meta.json'),
    accentColor: '#D67A4B',
    ambientGlow: 'rgba(214, 122, 75, 0.16)',
    signatureQuote:
      '“Đánh cho để dài tóc, đánh cho để đen răng, đánh cho sử tri Nam quốc anh hùng chi hữu chủ!”',
    shortBio:
      'Vị hoàng đế áo vải bách chiến bách thắng từ đất Tây Sơn, đại phá quân Xiêm ở Rạch Gầm – Xoài Mút và thần tốc quét sạch 29 vạn quân Thanh mùa xuân Kỷ Dậu 1789.',
    openingGreeting:
      'Ta là Nguyễn Huệ — Hoàng đế Quang Trung xuất thân từ đất võ Tây Sơn! Cờ đào phất lên từ ấp Kiên Mỹ, nam bình quân Xiêm trên sông Tiền, bắc phá 29 vạn quân Thanh giữa mùa xuân Kỷ Dậu. Ngươi muốn hỏi ta về cội nguồn Tây Sơn, hay muốn chứng kiến cuộc hành quân thần tốc tiến vào Ngọc Hồi – Đống Đa?',
    suggestedPrompts: [
      {
        id: 'qt_homeland',
        label: 'Đất võ Tây Sơn & chí lớn dựng nghiệp',
        question: 'Thưa bệ hạ, quê hương Kiên Mỹ – Tây Sơn và khát vọng chấn hưng đất nước của ngài bắt nguồn từ đâu?',
        expectedSceneMode: 'FOREGROUND_TALKING',
        contextKey: 'homeland',
        contextTitle: 'Ấp Kiên Mỹ — Đất võ Tây Sơn',
        contextYear: 'Thế kỷ XVIII',
        backgroundType: 'HOMELAND',
        backgroundPrompt:
          'Kiên Mỹ hamlet in Tây Sơn (old Bình Định) in the 18th century: ancient tamarind tree, wooden house, rice fields along the Côn River, misty mountains of the Tây Sơn upper route.',
        characterAction:
          'the hero from the reference image stands in the foreground, waist-up, facing the viewer and speaking with calm dignity, subtle hand gestures, mouth clearly visible and unobstructed for lip-sync',
        scenePrompt:
          'Medium shot at eye level. The hero from the reference image /assets/heroes/quang_trung.png stands in the foreground speaking with resolute presence before Kiên Mỹ homeland.',
        reason: 'Câu hỏi về quê hương Tây Sơn và chí hướng dựng nước nên nhân vật đứng tiền cảnh trò chuyện (FOREGROUND_TALKING).',
        sampleAnswer:
          'Ta sinh ra nơi ấp Kiên Mỹ, bên dòng sông Côn hiền hòa mà hào kiệt, dưới bóng núi Tây Sơn thượng đạo trùng điệp. Thuở ấy trăm họ lầm than vì quyền thần chia cắt Đàng Trong – Đàng Ngoài, ba anh em ta dựng cờ nghĩa năm Tân Mão (1771) chỉ mong lấy của nhà giàu chia cho dân nghèo, dẹp yên loạn lạc. Khi giặc ngoại xâm đã quét sạch, điều ta trăn trở nhất là ban Chiếu khuyến nông cho ruộng đồng xanh tốt, mở Chiếu lập học, đưa chữ Nôm thành quốc âm chính thống để dân tộc tự cường!',
      },
      {
        id: 'qt_ngoc_hoi',
        label: 'Đại phá Ngọc Hồi – Đống Đa (Tết Kỷ Dậu 1789)',
        question: 'Xin bệ hạ kể lại cuộc hành quân thần tốc và trận đại phá đồn Ngọc Hồi – Đống Đa mùng 5 Tết Kỷ Dậu 1789!',
        expectedSceneMode: 'BACKGROUND_ACTION',
        contextKey: 'ngoc_hoi_dong_da_1789',
        contextTitle: 'Đại phá Ngọc Hồi – Đống Đa',
        contextYear: 'Tết Kỷ Dậu 1789',
        backgroundType: 'BATTLEFIELD',
        backgroundPrompt:
          'Ngọc Hồi south of Thăng Long at dawn on the fifth day of Tết Kỷ Dậu (1789): Tây Sơn soldiers advancing behind wooden shields wrapped in wet straw, war elephants charging Qing fortifications, fire-lances and peach blossoms.',
        characterAction:
          'the hero from the reference image rides a war elephant at the head of his army, sword raised, commanding the charge on the Ngọc Hồi fortress',
        scenePrompt:
          'Epic wide battle scene at Ngọc Hồi dawn 1789. The hero from the reference image /assets/heroes/quang_trung.png commands from a war elephant amid cannon smoke and peach blossoms.',
        reason: 'Câu hỏi về trận đánh Ngọc Hồi – Đống Đa nên tái hiện toàn cảnh chiến trường (BACKGROUND_ACTION).',
        sampleAnswer:
          'Cuối năm Mậu Thân (1788), nghe cấp báo 29 vạn quân Thanh của Tôn Sĩ Nghị giày xéo Thăng Long, ta lập đàn tế trời đất trên núi Bân lên ngôi Hoàng đế, lấy niên hiệu Quang Trung rồi lập tức xuất quân! Tại Tam Điệp, ta cho ba quân ăn Tết trước và hẹn đúng mùng 7 Tết sẽ vào ăn mừng ở Thăng Long. Đêm 30 Tết vượt sông Gián Khẩu, đêm mùng 3 hạ gọn đồn Hà Hồi. Mờ sáng mùng 5 Tết Kỷ Dậu (1789), ta truyền lấy ván gỗ ghép lại quấn rơm ướt làm lá chắn cản đạn, 100 voi chiến cùng hỏa hổ ầm ầm xông thẳng vào đồn Ngọc Hồi; cùng lúc Đô đốc Long đánh tan đồn Khương Thượng – Đống Đa. Chưa tới giờ Ngọ mùng 5 Tết, chiếc áo bào sạm khói thuốc súng của ta đã tung bay giữa thành Thăng Long!',
      },
      {
        id: 'qt_rach_gam',
        label: 'Trận thủy chiến Rạch Gầm – Xoài Mút (1785)',
        question: 'Bệ hạ đã bày thế trận mai phục đánh tan 5 vạn quân Xiêm trên sông Tiền năm 1785 như thế nào?',
        expectedSceneMode: 'BACKGROUND_ACTION',
        contextKey: 'rach_gam_xoai_mut_1785',
        contextTitle: 'Thủy chiến Rạch Gầm – Xoài Mút',
        contextYear: '1785',
        backgroundType: 'BATTLEFIELD',
        backgroundPrompt:
          'The Tiền River near Mỹ Tho at dawn, January 1785: Siamese war fleet sailing into the stretch between Rạch Gầm and Xoài Mút, Tây Sơn boats bursting out of hidden canals and islet groves.',
        characterAction:
          'the hero from the reference image stands on a Tây Sơn war boat, signalling the ambush with a war drum and a raised sword',
        scenePrompt:
          'Wide cinematic river battle at Rạch Gầm – Xoài Mút, 1785. The hero from the reference image /assets/heroes/quang_trung.png commands the Tây Sơn naval ambush.',
        reason: 'Câu hỏi về trận thủy chiến Rạch Gầm – Xoài Mút nên chuyển sang chế độ hành động (BACKGROUND_ACTION).',
        sampleAnswer:
          'Đầu năm Ất Tỵ (1785), 5 vạn quân Xiêm cùng 300 chiến thuyền cậy thế hùng hổ tiến vào Gia Định. Ta đích thân khảo sát địa thế sông Tiền, chọn khúc sông từ Rạch Gầm đến Xoài Mút dài chừng bảy dặm, có cù lao Thới Sơn che chắn, làm tử địa khóa chặt quân giặc. Rạng sáng ngày 19 tháng Chạp (19/1/1785), lợi dụng con nước thủy triều và gió chướng, ta nhử toàn bộ đoàn thuyền Xiêm lọt vào giữa hai đầu Rạch Gầm – Xoài Mút rồi đồng loạt phất cờ. Đại bác trên hai bờ và cù lao Thới Sơn gầm vang, chiến thuyền Tây Sơn từ các nhánh rạch lao ra khóa đầu chặn đuôi, chỉ trong một buổi sáng đánh tan tành 300 chiến thuyền giặc!',
      },
    ],
  },
};

export const HERO_LIST: HeroCharacter[] = HERO_IDS.map((id) => HEROES[id]);

const ACTION_KEYWORDS = [
  'trận',
  'đánh',
  'chiến',
  'bạch đằng',
  'vân đồn',
  'chương dương',
  'hàm tử',
  'hịch tướng sĩ',
  'diên hồng',
  'chi lăng',
  'xương giang',
  'tốt động',
  'chúc động',
  'đông quan',
  'bình ngô đại cáo',
  'soạn',
  'viết',
  'lam sơn',
  'lệ chi viên',
  'ngọc hồi',
  'đống đa',
  'rạch gầm',
  'xoài mút',
  'núi bân',
  'lên ngôi',
  'hành quân',
  'cọc gỗ',
  'voi chiến',
  'tái hiện',
  'diễn biến',
];

export function buildDefaultSceneData(hero: HeroCharacter): SceneData {
  const defaultPrompt = hero.suggestedPrompts[0];
  return {
    stateId: `init_${hero.id}_homeland`,
    characterId: hero.id,
    sceneMode: 'FOREGROUND_TALKING',
    mouthSyncEnabled: true,
    characterReferencePath: hero.heroImageUrl,
    backgroundType: 'HOMELAND',
    contextKey: 'homeland',
    contextTitle: defaultPrompt.contextTitle,
    contextYear: defaultPrompt.contextYear,
    backgroundPrompt: hero.homelandVisual,
    characterAction:
      'the hero from the reference image stands in the foreground, waist-up, facing the viewer and speaking with calm dignity, subtle hand gestures, mouth clearly visible and unobstructed for lip-sync',
    scenePrompt: defaultPrompt.scenePrompt,
    reason: 'Khởi tạo sân khấu mặc định: Nhân vật đứng tiền cảnh trò chuyện trước khung cảnh quê hương.',
  };
}

export function inferSceneAndReply(
  hero: HeroCharacter,
  userQuestion: string
): { sceneData: SceneData; replyText: string; matchedPrompt?: SuggestedPrompt } {
  const normalized = userQuestion.trim().toLowerCase();

  // Check if matches any curated suggested prompt closely
  for (const prompt of hero.suggestedPrompts) {
    if (
      normalized === prompt.question.toLowerCase() ||
      normalized.includes(prompt.contextTitle.toLowerCase()) ||
      normalized.includes(prompt.contextKey.replace(/_/g, ' '))
    ) {
      const isTalking = prompt.expectedSceneMode === 'FOREGROUND_TALKING';
      return {
        matchedPrompt: prompt,
        replyText: prompt.sampleAnswer,
        sceneData: {
          stateId: `state_${hero.id}_${prompt.contextKey}_${Date.now()}`,
          characterId: hero.id,
          sceneMode: prompt.expectedSceneMode,
          mouthSyncEnabled: isTalking,
          characterReferencePath: hero.heroImageUrl,
          backgroundType: prompt.backgroundType,
          contextKey: prompt.contextKey,
          contextTitle: prompt.contextTitle,
          contextYear: prompt.contextYear,
          backgroundPrompt: prompt.backgroundPrompt,
          characterAction: prompt.characterAction,
          scenePrompt: prompt.scenePrompt,
          reason: prompt.reason,
        },
      };
    }
  }

  const isActionQuery = ACTION_KEYWORDS.some((kw) => normalized.includes(kw));
  const fallbackPrompt = isActionQuery
    ? hero.suggestedPrompts.find((p) => p.expectedSceneMode === 'BACKGROUND_ACTION') ||
      hero.suggestedPrompts[1]
    : hero.suggestedPrompts[0];

  const sceneMode: SceneMode = isActionQuery ? 'BACKGROUND_ACTION' : 'FOREGROUND_TALKING';
  const mouthSyncEnabled = sceneMode === 'FOREGROUND_TALKING';

  const replyText = isActionQuery
    ? `${fallbackPrompt.sampleAnswer}`
    : `${hero.selfPronoun} lắng nghe câu hỏi của ${hero.listenerAddress}. ${fallbackPrompt.sampleAnswer}`;

  return {
    replyText,
    sceneData: {
      stateId: `state_${hero.id}_${sceneMode.toLowerCase()}_${Date.now()}`,
      characterId: hero.id,
      sceneMode,
      mouthSyncEnabled,
      characterReferencePath: hero.heroImageUrl,
      backgroundType: isActionQuery ? fallbackPrompt.backgroundType : 'HOMELAND',
      contextKey: isActionQuery ? fallbackPrompt.contextKey : 'homeland',
      contextTitle: isActionQuery ? fallbackPrompt.contextTitle : `${hero.homeland}`,
      contextYear: isActionQuery ? fallbackPrompt.contextYear : hero.era,
      backgroundPrompt: isActionQuery ? fallbackPrompt.backgroundPrompt : hero.homelandVisual,
      characterAction: isActionQuery
        ? fallbackPrompt.characterAction
        : 'the hero from the reference image stands in the foreground, waist-up, facing the viewer and speaking with calm dignity, mouth clearly visible for lip-sync',
      scenePrompt: fallbackPrompt.scenePrompt,
      reason: isActionQuery
        ? 'Phát hiện từ khóa trận đánh / sự kiện lịch sử → chuyển sang BACKGROUND_ACTION (tắt đồng bộ khẩu hình, tái hiện bối cảnh lịch sử).'
        : 'Câu hỏi đàm đạo / thông tin sử liệu → giữ chế độ FOREGROUND_TALKING (bật đồng bộ khẩu hình, hậu cảnh quê hương).',
    },
  };
}
