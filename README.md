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
- 3 mẫu thumbnail 1280x720 từ khung hình 3 đoạn hay nhất: ghép 3 ảnh + chữ vàng, 1 ảnh to + mũi tên đỏ,
  2 ảnh + dải vàng; màu rực, chữ to viền đen, huy hiệu "TOP N". Chữ trên thumbnail đặt ở ô "Chữ to trên thumbnail".

## Bình luận của bạn

Mỗi đoạn có ô chú thích (chữ to trên đoạn) và nút **🎙 Thu bình luận**: giọng của bạn phát ngay sau đoạn đó trên khung hình
dừng ("MY TAKE"). Phần bình luận làm video mang tính biến đổi hơn — nhưng **không đảm bảo** tránh được Content ID /
khiếu nại bản quyền khi đăng YouTube. App không có và sẽ không có tính năng lách nhận diện bản quyền.

## Cấu trúc

- `clipmaker/youtube.py`: tìm video, đọc heatmap + bình luận, tải đúng đoạn (yt-dlp; `bin/ffmpeg` là ffmpeg của imageio).
- `clipmaker/moments.py`: chấm điểm từng giây, chọn đoạn.
- `clipmaker/compose.py`: đưa đoạn về 1920x1080/30fps, chuẩn âm lượng, số thứ tự, nguồn, chú thích, khung bình luận, nối.
- `server.py`, `static/`: web. Đoạn đã tải lưu ở `data/<dự án>/raw/` nên dựng lại không phải tải lại.
