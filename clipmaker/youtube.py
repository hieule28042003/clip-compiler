"""Tìm video, đọc dữ liệu "Most replayed" + bình luận có mốc giờ, tải đúng đoạn cần dùng (yt-dlp)."""
import os
import re
import subprocess
import threading
import urllib.request
from pathlib import Path

import yt_dlp
from yt_dlp.utils import download_range_func

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
# yt-dlp chỉ tìm ffmpeg trong PATH khi tải từng đoạn (bỏ qua ffmpeg_location) -> đưa bản ffmpeg của imageio vào PATH.
# Windows: tạo symlink cần quyền Admin và PATH chỉ nhận tên .exe -> chép file thành bin/ffmpeg.exe.
_FF = BIN / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
if not _FF.exists():
    import shutil

    from imageio_ffmpeg import get_ffmpeg_exe

    BIN.mkdir(exist_ok=True)
    _FF.unlink(missing_ok=True)            # symlink hỏng (venv tạo lại) vẫn chiếm tên
    try:
        _FF.symlink_to(get_ffmpeg_exe())
    except OSError:
        shutil.copy2(get_ffmpeg_exe(), _FF)
os.environ["PATH"] = f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}"
FFMPEG = str(_FF)
MIN_LEN, MAX_LEN = 60, 3 * 3600          # bỏ Shorts (thường không có "Most replayed") và livestream dài


def _base_opts():
    opts = {"quiet": True, "no_warnings": True, "ffmpeg_location": str(BIN), "socket_timeout": 30}
    # Python tự dùng proxy của hệ thống (Windows: cài đặt Proxy/VPN), còn ffmpeg thì không -> truyền rõ ràng,
    # nếu không ffmpeg treo rồi báo "exited with code 4294967158" (= -138, hết thời gian chờ).
    proxy = urllib.request.getproxies().get("https") or urllib.request.getproxies().get("http")
    if proxy:
        opts["proxy"] = proxy
    try:
        import deno      # YouTube cần bộ chạy JavaScript để lấy đủ định dạng video

        opts["js_runtimes"] = {"deno": {"path": deno.find_deno_bin()}}
    except ImportError:
        pass
    return opts


def search(keyword, count=30):
    """[{id, title, channel, duration, views}] — video thường (không Shorts), nhiều lượt xem trước."""
    with yt_dlp.YoutubeDL({**_base_opts(), "extract_flat": True, "skip_download": True}) as y:
        res = y.extract_info(f"ytsearch{int(count * 1.5)}:{keyword}", download=False)
    out, seen = [], set()
    for e in res.get("entries") or []:
        vid, dur = e.get("id"), e.get("duration") or 0
        if not vid or vid in seen or not MIN_LEN <= dur <= MAX_LEN or "/shorts/" in (e.get("url") or ""):
            continue
        seen.add(vid)
        out.append({"id": vid, "title": e.get("title") or vid, "channel": e.get("channel") or e.get("uploader") or "",
                    "duration": dur, "views": e.get("view_count") or 0})
    return out[:count]


TS = re.compile(r"(?<![\d:.])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")


def timestamps(text):
    """Các mốc giờ (giây) người xem ghi trong bình luận: "2:15", "1:02:30"."""
    out = []
    for h, m, s in TS.findall(text):
        if int(s) < 60:
            out.append(int(h or 0) * 3600 + int(m) * 60 + int(s))
    return out


def analyze(video_id, comments=120):
    """Thông tin dùng để tìm khoảnh khắc: heatmap, chương, bình luận có mốc giờ."""
    opts = {**_base_opts(), "skip_download": True}
    if comments:
        opts.update(getcomments=True, extractor_args={
            "youtube": {"max_comments": [str(comments), "all", "0", "0"], "comment_sort": ["top"]}})
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    marks = []
    for c in info.get("comments") or []:
        text = c.get("text") or ""
        for t in timestamps(text):
            marks.append({"t": t, "likes": c.get("like_count") or 0, "text": text[:140]})
    return {
        "id": video_id, "title": info.get("title") or video_id,
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": info.get("duration") or 0, "views": info.get("view_count") or 0,
        "heatmap": [[h["start_time"], h["end_time"], h["value"]] for h in info.get("heatmap") or []],
        "chapters": [{"start": c["start_time"], "end": c["end_time"], "title": c.get("title") or ""}
                     for c in info.get("chapters") or []],
        "marks": marks, "width": info.get("width"), "height": info.get("height"),
        "caption_url": _caption_url(info),
    }


def _caption_url(info):
    """Phụ đề tiếng Anh (người làm > tự động) dạng json3 có thời gian từng chữ."""
    subs, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    for tracks in (subs.get("en"), subs.get("en-US"), subs.get("en-GB"), auto.get("en-orig"), auto.get("en")):
        for t in tracks or []:
            if t.get("ext") == "json3":
                return t["url"]
    return None


NOISE = re.compile(r"\[[^\]]*\]|>>|♪")


def captions(url):
    """[(giây, chữ)] từ phụ đề json3 của YouTube."""
    import json

    with yt_dlp.YoutubeDL(_base_opts()) as y:
        data = json.loads(y.urlopen(url).read())
    words = []
    for ev in data.get("events") or []:
        t0 = ev.get("tStartMs", 0)
        for seg in ev.get("segs") or []:
            text = NOISE.sub("", seg.get("utf8", "")).strip()
            if text:
                for w in text.split():
                    words.append(((t0 + seg.get("tOffsetMs", 0)) / 1000.0, w))
    words.sort(key=lambda x: x[0])
    out, last = [], None
    for t, w in words:              # phụ đề tự động hay lặp lại chữ ở dòng cuốn -> bỏ trùng
        if last and w == last[1] and t - last[0] < 1.0:
            continue
        out.append((t, w))
        last = (t, w)
    return out


def chunks(words, start, end, max_words=7, max_len=2.6):
    """Chia chữ trong đoạn [start, end] thành từng câu phụ đề ngắn [(t0, t1, text)] theo giờ của đoạn (0 = start)."""
    inside = [(t - start, w) for t, w in words if start <= t < end]
    out, cur = [], []
    for i, (t, w) in enumerate(inside):
        cur.append((t, w))
        nxt = inside[i + 1][0] if i + 1 < len(inside) else end - start
        if len(cur) >= max_words or nxt - cur[0][0] > max_len or w[-1:] in ".?!" or nxt - t > 1.2:
            out.append((cur[0][0], min(nxt, cur[-1][0] + 1.6), " ".join(x[1] for x in cur)))
            cur = []
    return [(round(a, 2), round(b, 2), txt) for a, b, txt in out if b - a > 0.2]


def _newest(out_path):
    got = sorted(out_path.parent.glob(out_path.stem + ".*"), key=lambda p: p.stat().st_mtime)
    got = [p for p in got if p.suffix in (".mp4", ".mkv", ".webm") and p.stat().st_size > 0]
    return got[-1] if got else None


def _download_section(video_id, start, end, out_path, hook):
    """Cách nhanh: ffmpeg tự lấy đúng đoạn từ máy chủ YouTube (không tải cả video)."""
    opts = {**_base_opts(), "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[height<=1080]/b",
            "download_ranges": download_range_func(None, [(float(start), float(end))]),
            "force_keyframes_at_cuts": True, "outtmpl": str(out_path.with_suffix("")) + ".%(ext)s",
            "merge_output_format": "mp4", "progress_hooks": [hook], "overwrites": True}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={video_id}"])
    return _newest(out_path)


def _fetch_full(video_id, full, hook):
    if full.exists():
        return True
    # hình + tiếng tải riêng bằng Python rồi ghép trên máy (ffmpeg chỉ đọc file, không cần mạng)
    opts = {**_base_opts(), "format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
            "outtmpl": str(full.with_suffix("")) + ".part.%(ext)s", "merge_output_format": "mp4",
            "progress_hooks": [hook], "overwrites": True, "retries": 5, "fragment_retries": 5, "noprogress": True}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={video_id}"])
    done = full.with_suffix(".part.mp4")
    if not done.exists():
        return False
    done.replace(full)
    return True


_full_locks, _full_guard = {}, threading.Lock()


def _download_full_then_cut(video_id, start, end, out_path, hook):
    """Dự phòng khi ffmpeg không kết nối được (proxy, mạng chặn): Python tải cả video 720p một lần
    (giữ lại cho các đoạn khác cùng video) rồi cắt đoạn trên máy."""
    full = out_path.parent / f"full_{video_id}.mp4"
    with _full_guard:
        lock = _full_locks.setdefault(video_id, threading.Lock())
    with lock:                      # 2 đoạn cùng video chạy song song: chỉ tải cả video một lần
        if not _fetch_full(video_id, full, hook):
            return None
    p = subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(full),
                        "-t", f"{end - start:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-c:a", "aac", "-b:a", "192k", str(out_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg cắt đoạn lỗi: {p.stderr[-300:]}")
    return out_path


def download(video_id, start, end, out_path, progress=lambda f: None):
    """Tải riêng đoạn [start, end] ra MP4: thử cách nhanh 2 lần, hỏng thì tải cả video rồi cắt."""
    out_path = Path(out_path)

    def hook(d):
        if d.get("status") == "downloading" and d.get("total_bytes"):
            progress(d.get("downloaded_bytes", 0) / d["total_bytes"])

    errors = []
    for way in (_download_section, _download_section, _download_full_then_cut):
        try:
            got = way(video_id, start, end, out_path, hook)
        except Exception as e:  # noqa: BLE001 - lỗi mạng/yt-dlp đủ loại, thử cách sau
            errors.append(str(e)[-200:])
            continue
        if got:
            if got != out_path:
                got.replace(out_path)
            return out_path
    raise RuntimeError(f"Không tải được đoạn video {video_id}: {errors[-1] if errors else ''}")
