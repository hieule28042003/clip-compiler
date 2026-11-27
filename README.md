# Ghép Khoảnh Khắc Hay

Nhập từ khoá → app tìm video trên YouTube → tìm các đoạn gây cười → bạn duyệt, viết chú thích, thu giọng bình luận →
ghép thành video 10–20 phút (1080p) có số thứ tự đếm ngược, ghi nguồn, mô tả YouTube có mốc giờ + link video gốc.
Miễn phí, không dùng AI trả phí.

## Chạy

```bash
git clone <link repo> clip-compiler && cd clip-compiler
./setup.sh          # lần đầu: tạo .venv và cài thư viện (ffmpeg đi kèm, không cần cài riêng)
./run.sh            # chạy rồi mở http://localhost:8790  (./run.sh 9000 để đổi cổng)
./run.sh --update   # cập nhật yt-dlp khi YouTube đổi, tìm / tải video bị lỗi
```

Windows (PowerShell / CMD, không cần quyền Admin):

```bat
setup.bat           & rem lần đầu
run.bat             & rem chạy rồi mở http://127.0.0.1:8790  (run.bat 9000 để đổi cổng)
run.bat --update    & rem cập nhật yt-dlp
```

Chạy test: `.venv/bin/python -m pytest -q`

## Cách tìm đoạn gây cười (`clipmaker/moments.py`)

- Biểu đồ **"Most replayed"** của YouTube: chỗ người xem tua lại nhiều nhất (bỏ phần mở đầu video vì luôn cao).
- **Bình luận có mốc giờ** ("12:50 got me in tears 😂"): nhiều like / có 😂 🤣 💀 lol lmao… thì điểm cao hơn.
- **Tên chương** có funny / fail / laugh…
- Mỗi video tối đa 4 ứng viên; tự chọn tối đa 3 đoạn mỗi video, đoạn hay nhất để cuối (#1).

Video ít lượt xem không có "Most replayed" thì chỉ dựa vào bình luận. Chưa đủ thời lượng thì bấm **Quét thêm 20 video**.

## Mỗi lần ra video khác nhau

- Mỗi lần quét tìm bằng từ khoá gốc + 2 biến thể ngẫu nhiên (funny, bloopers, try not to laugh…) và trộn thứ tự video.
- Đoạn đã dựng thành video được ghi vào `data/history.json`; lần sau bị hạ điểm mạnh (ghi "♻ Đã dùng ở video trước").
- "Tự chọn lại" chọn có yếu tố ngẫu nhiên nên mỗi lần bấm ra danh sách khác.

## Phụ đề và thumbnail

- Phụ đề tiếng Anh lấy từ phụ đề YouTube (người làm hoặc tự động), chia câu ngắn, in thẳng vào video. Tắt được.
- Thumbnail 1280x720 kiểu kênh lớn: tìm **nhân vật chính** (gương mặt xuất hiện ở nhiều đoạn nhất, YuNet + SFace của
  OpenCV), chọn khung có mặt người đó to + đang có hành động quanh đỉnh gây cười, **tách người khỏi nền** (PP-HumanSeg).
  3 mẫu: *Sticker* (người viền trắng to tràn khung trên nền tia sáng, mũi tên), *Reaction* (khung toàn cảnh + bong bóng
  mặt cận cảnh), *Split* (chia đôi chéo). Chữ ngắn 2–4 chữ font Anton, xen trắng/vàng, chữ cuối trong ô đỏ.
  Các mô hình (~45 MB) tự tải vào `models/` lần đầu. Chữ tự đặt được ở ô "Chữ to trên thumbnail".
- Tiêu đề chính luôn là kiểu giật tít nhưng đúng nội dung ("Donald Trump Lost It… Then THIS Happened 💀", "Try Not To
  Laugh: … Edition (IMPOSSIBLE)"), kèm 5 gợi ý theo xu hướng. Chữ thumbnail đôi khi là câu nói thật trong video
  ("\"I AM DONE\""); mẫu Reaction có vòng đỏ + mũi tên chỉ chỗ có hành động. Không bịa sự việc video không có
  (YouTube phạt tiêu đề / thumbnail gây hiểu lầm).

## Bình luận của bạn

Mỗi đoạn có ô chú thích (chữ to trên đoạn) và nút **🎙 Thu bình luận**: giọng của bạn phát ngay sau đoạn đó trên khung hình
dừng ("MY TAKE"). Phần bình luận làm video mang tính biến đổi hơn — nhưng **không đảm bảo** tránh được Content ID /
khiếu nại bản quyền khi đăng YouTube. App không có và sẽ không có tính năng lách nhận diện bản quyền.

## ⚡ Làm tự động, giọng đọc, Shorts
- **⚡ Làm tự động** (form tạo video hoặc thẻ chủ đề ở trang Xu hướng): quét → chọn đủ thời lượng (tự quét thêm tối đa
  2 lần) → lời bình giọng đọc → dựng → thumbnail, tiêu đề, mô tả, tag → Shorts. Vẫn mở dự án ra sửa và dựng lại được.
- **🤖 Lời bình giọng đọc** (`clipmaker/voice.py`): câu bình ngắn cho các đoạn top, dựa trên bình luận nhiều like nhất /
  câu thoại của chính đoạn đó, đọc bằng edge-tts (miễn phí, cần mạng). Bản thu tay của bạn luôn được giữ nguyên.
- **✂ Shorts**: 3 đoạn top thành video dọc 1080x1920 có hook, phụ đề, nút “Full video on my channel”, kèm tiêu đề + mô tả.

## ✂ Làm Shorts (không dựng video dài)
- Trang **✂ Làm Shorts**: nhập từ khoá, chọn số Shorts (3–10) và độ dài mỗi Short → app quét, lấy các đoạn hay nhất
  (mỗi video gốc 1 đoạn) và chỉ cắt video dọc 1080x1920: hook to phía trên (không lặp giữa các Short), phụ đề, nút
  “FOLLOW FOR MORE”. Mỗi Short có tiêu đề `#shorts`, mô tả ghi nguồn, tag chung. Mở dự án ra chỉnh ±1 giây, chọn lại,
  quét thêm rồi bấm **✂ Cắt Shorts**.
- Kiểu **💬 Trả lời bình luận hài**: lấy bình luận nhiều like, có 😂💀 / lol… và nhắc mốc giờ trong video gốc
  (bỏ bình luận chỉ có mốc giờ, bình luận miệt thị; che chữ chửi thề "s**t"), cắt đúng đoạn được nhắc. Khung trắng
  "Reply to @tên's comment" + nội dung bình luận phía trên (bỏ emoji vì font không vẽ được), câu đáp chữ to
  ("Here's the exact moment", "You asked, so here it is"…) chọn theo kiểu bình luận và đọc bằng giọng edge-tts, tiếng
  gốc nhỏ lại lúc đọc. Tiêu đề / mô tả ghi "Replying to @tên's comment". App không tự hiểu câu nào hài: nên tự duyệt.
- Kênh chỉ đăng Shorts: ở thẻ kênh bỏ tick **🎬 Đăng video dài** và chọn số Shorts mỗi lần (1–10). Bình luận tự đăng
  là câu hỏi tương tác thay cho link video dài.

## 📺 Cắt Shorts từ một buổi stream dài (`clipmaker/stream.py`)

Trang ✂ Làm Shorts → Kiểu Short "📺 Cắt từ một buổi stream dài" → dán link stream (bản phát lại, dài vài giờ cũng được).
Mỗi giây của stream được chấm điểm theo:
- **Chat phát lại**: số tin trong 10 giây vượt mức nền ±2,5 phút bao nhiêu lần độ dao động tự nhiên (stream ít chat
  không bị tính nhầm); tin có "=))", "vãi", "KEKW", 😂… và Super Chat nặng ký hơn; dời lùi 6 giây vì chat gõ chậm.
- **Độ to âm thanh**: tải riêng tiếng bản nhẹ (~20 MB/giờ), đo dB từng giây; to hơn nền ≥ 3 dB mới có điểm.
- **"Most replayed"** (stream dài thì mỗi ô vài phút nên chỉ dùng để khoanh vùng) + **bình luận có mốc giờ**.
Chọn các đỉnh cách nhau ≥ 1 phút, đỉnh nằm ở 60% đoạn, đầu/cuối đoạn dời ±4 giây vào chỗ yên lặng. Hook to phía trên lấy
câu chat nhiều người gõ nhất (lọc chửi thề / miệt thị), phụ đề theo ngôn ngữ của stream. Không tải cả video: chỉ tải đúng đoạn.
Chỉ dùng cho stream của chính bạn hoặc streamer cho phép làm clip.

**Video dài từ các buổi livestream**: trang Tạo video ghép → "Lấy đoạn từ" = 📺 Các buổi livestream → dán 1–10 link (mỗi dòng
một link), chọn đoạn 30/45/60 giây và độ dài 10–30 phút. Mỗi buổi được chấm điểm như trên; các đoạn chia đều cho các buổi
(buổi có chat điểm cao hơn hẳn buổi không có), ghép theo thứ tự thời gian, không đánh số. Chương trong mô tả ghi câu chat +
mốc giờ trong stream; tiêu đề / mô tả kiểu "stream highlights" (`copywrite.make(stream=True)`). Buổi nào lỗi (riêng tư, bị
xoá) thì bỏ qua và ghi chú, vẫn dựng từ các buổi còn lại.

## 🎵 Ghép nhạc thành video dài (`clipmaker/music.py`)

Menu **🎵 Ghép nhạc** → chọn quốc gia, thể loại, số bài, kiểu chuyển bài → **⚡ Tìm bài & dựng luôn** hoặc **🔎 Tìm bài & tự duyệt**.

- **Bài hot**: chọn quốc gia + thể loại (hoặc Tất cả) và cách lấy bài:
  - 🏆 **Hot nhất mọi thời điểm** (mặc định): video bài hát nhiều view nhất từ trước tới nay trên YouTube
    (vd Việt Nam: Bống Bống Bang Bang, Sóng Gió, Nơi Này Có Anh…), sau đó đến bảng xếp hạng Top Songs của iTunes hiện tại.
  - 📈 **Đang hot 7 / 30 ngày qua**: bảng xếp hạng iTunes ở nước đó (Hàn Quốc dùng mục K-Pop của iTunes Mỹ),
    thiếu thì lấy bài nhiều view nhất trong khoảng đó trên YouTube.
  Bỏ video tổng hợp, live, reaction, karaoke, parody, speed up; mỗi nghệ sĩ tối đa 2 bài.
- **Phiên bản**: 🎤 bản gốc, 🔥 remix, 🌙 chill / lofi, 🎸 acoustic, 🎹 piano. Danh sách bài hot chỉ để biết bài nào;
  app tìm bản phiên bản đó nhiều view nhất của từng bài (tiêu đề phải có tên bài + chữ remix / lofi…, không lẫn phiên bản khác).
- **Không lặp bài**: bài đã dùng trong bản mix đã dựng được ghi ở `data/music_history.json` (theo từng phiên bản),
  lần sau app lấy các bài hot tiếp theo. Bỏ tick “Không lặp bài” nếu muốn dùng lại.
- **Kiểu video**: 🎧 Playlist (cả bài, crossfade) hoặc 🔥 **Mashup / Megamix**:
  - Mỗi bài lấy đoạn điệp khúc 32 phách (8 nhịp), quanh đỉnh “Most replayed” của YouTube, chọn đoạn to và dày nhất.
  - App tự đo nhịp: BPM bằng tự tương quan của spectral flux, tinh chỉnh BPM và pha cho khớp lưới phách,
    sai lệch dưới 10 ms khi thử với tiếng gõ nhịp chuẩn.
  - Các bài được chia thành nhóm nhịp như DJ: trong nhóm, mỗi bài đổi nhịp không quá 12% bằng rubberband
    (giữ nguyên tông). Các nhóm xếp từ chậm đến nhanh, nối chồng đúng 2 phách.
  - Ví dụ thật: 12 hit V-Pop trong 3:12, ba nhóm nhịp 92 → 115 → 130 BPM.
- **Tiếng**: tải phần tiếng, bỏ khoảng lặng đầu và cuối, chỉnh âm lượng đều nhau (-14 LUFS), nối bằng crossfade.
  Tiếng đã tải lưu ở `data/music_cache/` nên dùng lại được cho bản mix sau.
- **Hình** 1920x1080: ảnh bìa và tên bài đang phát, danh sách tất cả các bài kèm mốc giờ (bài đang phát được tô sáng),
  sóng nhạc chạy theo nhạc, thanh tiến độ. Nền ghép từ ảnh bìa các bài, hoặc ảnh bạn tải lên.
  Video 1 giờ dựng mất khoảng 10–15 phút, ffmpeg dùng khoảng 0,6–1,6 GB RAM.
- Kèm thumbnail ghép ảnh bìa + chữ to, tiêu đề, mô tả có mốc giờ từng bài + link video gốc, tag.
- ⚠ Bài hát thương mại gần như chắc chắn bị Content ID nhận ra: doanh thu thuộc về chủ bài hát, có thể bị chặn
  hoặc nhận gậy bản quyền. Có tuỳ chọn chỉ lấy nhạc không bản quyền.

## 🎤 TikTok lời bài hát (`clipmaker/lyrics.py`)

Nhập "Ca sĩ - Tên bài" hoặc link YouTube của bài, chọn độ dài (15 / 30 / 45 giây, 1 phút hoặc cả bài) và từ khoá cho clip nền.
App làm video dọc 1080x1920:

- **Lời**: lấy từ [LRCLIB](https://lrclib.net) (miễn phí, có mốc thời gian từng câu). App ưu tiên bản gốc, bỏ bản remix / sped up / paused nếu bạn không ghi.
- **Tiếng**: lấy từ video YouTube **dài đúng bằng** bản có lời (thường là "Official Audio"). MV hay có đoạn mở đầu riêng làm lời lệch.
  App giữ nguyên khoảng lặng đầu bài để mốc thời gian khớp. Không tìm được bản khớp thì app vẫn làm và báo "lời có thể lệch".
- **Đoạn**: video ngắn bắt đầu ở câu hát gần đoạn điệp khúc (đoạn được tua lại nhiều nhất + to nhất), kết thúc ngay trước một câu mới.
- **Nền**: các khoảnh khắc hay của video tìm theo từ khoá nền, cắt kín khung dọc, bỏ viền đen, tối nhẹ cho chữ dễ đọc.
  Cảnh đổi ở đầu câu hát (mỗi cảnh 3,5–8 giây), cảnh liền nhau lấy từ video khác nhau.
- **Chữ**: câu đang hát to giữa màn, câu sau nhỏ mờ bên dưới, tên bài phía trên; đoạn nhạc dạo hiện ♪.
  Chữ Hàn / Nhật / Trung / Thái dùng font Noto.
- Lời lệch thì nhập số giây ở ô **Lệch lời** rồi bấm Áp dụng: app dựng lại ngay, không tải lại gì.
- App viết sẵn caption TikTok (tên bài, một câu hát, hashtag). Tải video về rồi đăng ở TikTok Studio.

## 🎶 MV lời bài hát ảnh AI (`clipmaker/mv.py`)

Nhập “Ca sĩ - Tên bài” (hoặc link YouTube), chọn cả bài / 1 phút / 30 giây, khung ngang + dọc, chủ đề ảnh
(chuyện tình theo lời bài, phong cảnh không người, anime, cổ trang, thành phố đêm mưa, ca sĩ trên sân khấu) hoặc
**tự viết kịch bản** (mỗi dòng một cảnh, viết tiếng Việt được: app dịch sang tiếng Anh cho AI vẽ, cảnh hiện theo thứ tự):

1. Lời có mốc thời gian + bản thu âm khớp lời: như TikTok lời bài hát.
2. Bài nước ngoài: dịch từng câu sang tiếng Việt (Google Dịch), sửa lại cho hay ở “🇻🇳 Lời dịch” rồi Dựng lại.
3. Lên cảnh: đổi cảnh ở đầu câu hát (5–9 giây/cảnh). Mỗi ảnh minh hoạ một câu hát khác nhau (điệp khúc lặp lại thì
   lấy câu chưa vẽ), mỗi ảnh một góc máy (toàn cảnh, cận, ngược sáng, từ trên cao…); câu hát dịch sang tiếng Anh làm mô tả.
   Đổi chủ đề / kịch bản cho MV đã làm ở “🎨 Đổi chủ đề ảnh”: lên cảnh và vẽ lại cả bộ. Chọn 8 / 12 / 16 / 24 ảnh: ít ảnh vẽ nhanh hơn, các cảnh dùng lại ảnh với
   chuyển động khác.
4. Vẽ ảnh bằng Pollinations: không key là gói miễn phí (khoảng 45 giây một ảnh, ảnh nhỏ, có logo pollinations.ai ở góc);
   có API key (`sk_…`, lưu ở `data/ai_image.json`) thì dùng model Flux. Ảnh nào chưa ưng: 🎨 Vẽ lại, ✏ Tả cảnh (tự viết mô tả),
   hoặc ⬆ thay bằng ảnh của bạn.
5. Dựng: ảnh zoom / lia chậm (phóng ảnh gấp 3 trước khi zoom cho mịn, tốc độ êm ở hai đầu), hoà sang cảnh mới
   trong 0.8 giây (xen vài kiểu trượt nhẹ) sao cho cảnh mới hiện rõ đúng lúc vào câu hát, tên bài lớn ở đầu, lời gốc chạy màu theo tiếng hát (karaoke), lời Việt bên dưới, chữ
   “Hình ảnh minh hoạ bằng AI”. Bản dọc đặt ảnh 16:9 giữa nền mờ. Kèm ảnh bìa YouTube, tiêu đề + mô tả (có lời và lời dịch),
   caption + hashtag (#vietsub, #nhactrung…). Nút 📤 Đăng dùng bản dọc.

Người trong ảnh là người do AI vẽ: đừng đặt tiêu đề như thể đó là buổi diễn có thật. Bài hát có bản quyền (Content ID).

## 🎬 Ghép phim + lồng tiếng Việt (`clipmaker/dub.py`)

Chỉ dùng cho **kênh của bạn hoặc kênh đã cho phép bạn dùng** (app bắt tích ô xác nhận).

1. Dán link kênh TikTok (`https://www.tiktok.com/@ten_kenh`) hoặc link từng video TikTok / Douyin (mỗi dòng một link),
   hoặc tải file video từ máy lên. TikTok hay chặn lấy cả kênh: khi đó dán link từng video. Douyin chỉ lấy được từng
   video; video đòi đăng nhập thì tải về máy rồi tải file lên.
2. Bỏ tích video không dùng, xếp thứ tự bằng ↑ ↓ (mặc định cũ nhất trước).
3. Bấm “Ghép & lồng tiếng”: tải → nhận giọng nói bằng faster-whisper (model `small`, chạy trên máy, khoảng 1.3GB RAM,
   lần đầu tải model khoảng 500MB) → dịch sang tiếng Việt (Google Dịch miễn phí, dự phòng MyMemory) → đọc bằng edge-tts
   (giọng nữ Hoài My / nam Nam Minh), mỗi câu bắt đầu đúng lúc nhân vật nói, dài hơn chỗ trống thì đọc nhanh tối đa 1.5 lần
   → tiếng gốc tự nhỏ lại khi có lời lồng (sidechain, vẫn giữ nhạc nền) hoặc tắt hẳn → phụ đề tiếng Việt → MP4.
   Khung hình theo đa số clip: dọc 1080x1920 hoặc ngang 1920x1080.
4. Sửa bản dịch từng câu trong “Sửa bản dịch” rồi Dựng lại: không phải nhận giọng lại.

Hạn chế: một giọng cho cả phim (không phân biệt nhân vật), phụ đề chữ Trung có sẵn trong video gốc không bị xoá.

## 📤 Đăng TikTok & Facebook (`clipmaker/social.py`)

Ở ⚙ Cài đặt → “Đăng TikTok & Facebook”: nhập key của app bạn tự tạo, bấm Kết nối (một lần). Sau đó video TikTok lời bài hát,
MV ảnh AI và phim lồng tiếng có nút **📤 Đăng**: đăng ngay hoặc hẹn giờ (máy phải đang bật lúc tới giờ). Tiến độ, lỗi, nút Thử lại ở
“Lịch đăng”. Key, secret, token lưu ở `data/social.json` (không đưa lên git).

- **Facebook Fanpage** (Graph API): app ở chế độ phát triển, trường hợp sử dụng “Quản lý mọi thứ trên Trang”, không cần Meta duyệt
  vì chỉ đăng lên Trang của chính bạn. Video dọc ≤ 90 giây đăng thành Reels, còn lại video thường (tải lên từng phần).
  Token Trang lấy từ token người dùng dài hạn nên không hết hạn.
- **TikTok** (Content Posting API, upload to inbox): app loại Desktop (redirect `http://localhost:8790/api/social/tt/callback`,
  PKCE), dùng sandbox với tài khoản của bạn là target user. Video vào hộp thư / nháp trong app TikTok, bạn dán caption rồi bấm Đăng
  (TikTok chỉ cho app đã qua duyệt đăng công khai thẳng; giới hạn khoảng 5 video chờ trong 24 giờ). Token 24 giờ tự làm mới.

## 🔥 Xu hướng & 📊 phân tích (`clipmaker/trends.py`)
- Chọn lĩnh vực + **7 ngày qua / 30 ngày qua**: app lấy các từ khoá người xem đang gõ, các video đăng trong khoảng đó
  nhiều view nhất (cả Shorts), rồi phân tích:
  - *Định dạng*: Shorts hay video dài đang có view trung vị cao hơn; *độ dài* video dài ăn khách nhất.
  - *Công thức tiêu đề*: view trung vị khi tiêu đề có / không có VIẾT HOA, emoji, ! ?, con số, dạng tổng hợp, tiêu đề
    ngắn; các kiểu tiêu đề đang lặp lại nhiều.
  - *Ngày giờ đăng* và *kênh nổi bật* (16 video hot nhất được xem chi tiết: ngày đăng, like, số sub kênh).
  - **🚀 Video đang lên**: xếp theo view mỗi ngày và độ "vượt sức" kênh (view gấp nhiều lần số sub), kèm lý do; bỏ
    livestream, mỗi kênh tối đa 2 video. **⚡ Làm tương tự** tạo video tổng hợp (hoặc Shorts nếu video gốc là Shorts)
    cùng từ khoá, quét chính video đó + các video hot cùng từ khoá trước. App chỉ ghép khoảnh khắc, không dựng lại
    nội dung gốc.
  - Mục *Nên làm gì* tóm tắt các kết luận trên.

## 📺 Kênh YouTube & tự đăng
- Tab **Kênh YouTube**: nhập Client ID/secret (OAuth client loại *Desktop app* của Google Cloud, bật YouTube Data API v3),
  bấm **+ Kết nối kênh** để đăng nhập từng kênh. Thông tin đăng nhập lưu ở `data/accounts.json` (không đưa lên git).
- Mỗi kênh: lĩnh vực/chủ đề, cách chọn chủ đề (hot trong lĩnh vực hoặc xoay vòng từ khoá), độ dài, giọng đọc,
  đăng video dài / 3 Shorts, chế độ đăng (riêng tư / không công khai / công khai), giờ tự chạy mỗi ngày.
- **📅 Lịch đăng** từng kênh: *đăng ngay* hoặc *hẹn giờ lên sóng theo khung giờ* (giờ video dài, giờ Shorts, ngày
  trong tuần). Video được dựng + tải lên trước ở chế độ riêng tư kèm `publishAt`, tới giờ YouTube tự công khai; mỗi
  video lấy mốc trống kế tiếp (luồng song song không trùng mốc), Shorts lên sóng sau video dài. Nút **▶ Chạy ngay** có
  ô chọn ngày giờ để hẹn riêng cho một video. Danh sách "Sắp lên sóng" hiện ngay trong thẻ kênh.
- **📈 Tương tác** từng kênh (tăng view / sub thật, không dùng view ảo):
  - *💬 Tự bình luận*: khi video lên sóng, kênh tự bình luận một câu hỏi dễ trả lời ("số mấy làm bạn cười nhất? 👇");
    dưới Shorts là link video dài. Video riêng tư thì không bình luận. API không cho ghim: ghim tay trong Studio.
  - *Thumbnail xoay vòng* 3 mẫu Sticker / Reaction / Split (hoặc cố định một mẫu).
  - *📊 Thống kê*: view, like, bình luận (Data API) + thời gian xem, % xem hết, sub có thêm (cần bật **YouTube
    Analytics API** và bấm Kết nối lại) của từng video app đã đăng; so sánh trung bình view theo mẫu thumbnail,
    chủ đề, giờ và ngày lên sóng (🏆 nhóm tốt nhất; dưới 3 video mỗi nhóm thì mới là gợi ý).
- Hàng chờ chạy song song 1–4 luồng; mỗi việc: chọn chủ đề chưa dùng → ⚡ làm tự động → tải video + thumbnail + Shorts lên.
- Giới hạn của Google: 10.000 đơn vị/ngày cho mỗi Client ID, mỗi lần tải video tốn 1.600 (≈ 6 video/ngày, tính cả Shorts).
  Dự án Google Cloud chưa qua kiểm duyệt thì video tải lên qua API bị khoá ở chế độ riêng tư.

## 🍪 Cookie YouTube (ít bị chặn khi tải nhiều)
- YouTube tạm chặn máy tải quá nhiều ("Sign in to confirm you're not a bot"). Khi đó app dừng tải và báo lỗi (không ra
  video thiếu đoạn); đợi 30–60 phút rồi bấm **Dựng lại** — đoạn đã tải được giữ lại.
- Trang **⚙ Cài đặt**: tải lên `cookies.txt` (xuất bằng tiện ích *Get cookies.txt LOCALLY*, từ cửa sổ ẩn danh đăng nhập
  **tài khoản Google phụ**) hoặc lấy thẳng từ trình duyệt (Firefox). Bấm **Kiểm tra cookie** để thử. Nếu cookie làm YouTube không trả định dạng nào
  ("Requested format is not available"), app tự tải không cookie trong 1 giờ. File lưu ở
  `data/cookies.txt` (không đưa lên git).

## Cấu trúc

- `clipmaker/youtube.py`: tìm video, đọc heatmap + bình luận, tải đúng đoạn (yt-dlp; `bin/ffmpeg` là ffmpeg của imageio).
- `clipmaker/moments.py`: chấm điểm từng giây, chọn đoạn.
- `clipmaker/compose.py`: đưa đoạn về 1920x1080/30fps, chuẩn âm lượng, số thứ tự, nguồn, chú thích, khung bình luận, nối.
- `clipmaker/stream.py`: chấm điểm từng giây của stream (chat phát lại, độ to, Most replayed, bình luận) → đoạn hay.
- `clipmaker/social.py`: đăng Fanpage Facebook (Reels / video) và gửi vào hộp nháp TikTok.
- `clipmaker/mv.py`: MV lời bài hát ảnh AI (vẽ ảnh Pollinations, ảnh chuyển động, karaoke + lời Việt, bản ngang / dọc).
- `clipmaker/caption.py`: caption + hashtag từ lời thoại / lời hát thật.
- `clipmaker/dub.py`: lấy clip từ kênh / link, nhận giọng nói, dịch, lồng tiếng, phụ đề, ghép phim dài.
- `clipmaker/lyrics.py`: lời có mốc thời gian (LRCLIB), chọn bản thu âm khớp lời, chọn đoạn, vẽ chữ, dựng video TikTok lời bài hát.
- `clipmaker/music.py`: tìm bài hot, tải tiếng, nối crossfade, vẽ khung danh sách bài + sóng nhạc, dựng video ghép nhạc.
- `server.py`, `static/`: web. Đoạn đã tải lưu ở `data/<dự án>/raw/` nên dựng lại không phải tải lại.
