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
- Tiêu đề chính luôn là kiểu giật tít nhưng đúng nội dung ("Donald Trump Did WHAT?! 😳 …"), kèm 5 gợi ý theo xu hướng.

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
  **tài khoản Google phụ**) hoặc lấy thẳng từ trình duyệt (Firefox). Bấm **Kiểm tra cookie** để thử. File lưu ở
  `data/cookies.txt` (không đưa lên git).

## Cấu trúc

- `clipmaker/youtube.py`: tìm video, đọc heatmap + bình luận, tải đúng đoạn (yt-dlp; `bin/ffmpeg` là ffmpeg của imageio).
- `clipmaker/moments.py`: chấm điểm từng giây, chọn đoạn.
- `clipmaker/compose.py`: đưa đoạn về 1920x1080/30fps, chuẩn âm lượng, số thứ tự, nguồn, chú thích, khung bình luận, nối.
- `server.py`, `static/`: web. Đoạn đã tải lưu ở `data/<dự án>/raw/` nên dựng lại không phải tải lại.
