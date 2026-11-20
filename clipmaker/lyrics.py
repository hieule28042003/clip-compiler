"""Video TikTok lời bài hát: lời có mốc thời gian (LRCLIB) -> chọn đoạn điệp khúc -> nền là các clip hay cắt dọc 9:16,
đổi cảnh đúng lúc sang câu mới -> chữ lời hiện từng câu khớp tiếng hát.

Lời của LRCLIB khớp với bản thu âm gốc, nên tiếng lấy từ video YouTube có độ dài bằng độ dài bài trên LRCLIB
(thường là "Official Audio"); MV thường có đoạn mở đầu riêng làm lời lệch.
"""
import json
import re
import subprocess
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import compose, music

LRCLIB = "https://lrclib.net/api"
TW, TH, FPS = 1080, 1920, 30
DUR_OK = 2.5              # s: video YouTube lệch độ dài quá mức này so với bản LRCLIB thì lời dễ lệch
LINE_MAX = 7.0            # s: một câu hiện tối đa ngần này (sau đó là đoạn nhạc dạo, hiện ♪)
CHUNK_MIN, CHUNK_MAX = 3.5, 8.0      # s: mỗi cảnh nền dài khoảng này, đổi cảnh ở đầu câu hát
MAX_LEN = 600             # TikTok nhận video tối đa 10 phút
TS = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
EXTRA = re.compile(r"\b(remix|lofi|lo-fi|speed ?up|sped ?up|slowed|reverb|cover|live|karaoke|instrumental|beat|"
                   r"acoustic|piano|paused|nightcore|8d|version|ver\.?)\b", re.I)


class NoLyrics(RuntimeError):
    pass


def _get(path, params):
    req = urllib.request.Request(f"{LRCLIB}/{path}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": "clip-compiler (lyrics video maker)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower().replace("đ", "d"))
    return re.sub(r"[^\w]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def parse_lrc(text):
    """LRC -> [(giây, câu)] theo thời gian; câu rỗng giữ lại (đánh dấu hết câu trước). Hỗ trợ [offset:±ms]."""
    m = re.search(r"\[offset:\s*([+-]?\d+)\]", text or "")
    shift = -int(m[1]) / 1000 if m else 0.0          # chuẩn LRC: offset dương = lời hiện sớm hơn
    out = []
    for raw in (text or "").splitlines():
        stamps = TS.findall(raw)
        if not stamps:
            continue
        words = re.sub(r"<\d+:\d+(?:\.\d+)?>", "", TS.sub("", raw)).strip()    # bỏ mốc từng chữ (LRC nâng cao)
        for mm, ss in stamps:
            out.append((max(0.0, int(mm) * 60 + float(ss.replace(":", ".")) + shift), words))
    return sorted(out, key=lambda x: x[0])


def lines(lrc, duration):
    """[(bắt đầu, kết thúc, câu)]: câu kéo tới câu sau, dài tối đa LINE_MAX giây; bỏ câu rỗng."""
    out = []
    for i, (t, text) in enumerate(lrc):
        if not text:
            continue
        nxt = lrc[i + 1][0] if i + 1 < len(lrc) else duration
        out.append((t, min(nxt, t + LINE_MAX, duration), text))
    return [x for x in out if x[1] > x[0]]


def _score(rec, artist, song, duration):
    s = 0.0
    want, got = norm(song), norm(rec.get("trackName"))
    if want and (want == got or want in got):
        s += 2
    if artist and norm(artist).split()[:1] == norm(rec.get("artistName")).split()[:1]:
        s += 1
    if EXTRA.search(rec.get("trackName") or "") and not EXTRA.search(song or ""):
        s -= 2                                   # bản remix / paused / sped up... khi người dùng không ghi
    if duration:
        s -= min(3.0, abs((rec.get("duration") or 0) - duration) / 2)
    return s


def find(artist="", song="", query="", duration=None):
    """Bản lời có mốc thời gian khớp nhất -> {artist, song, duration, lrc}. Không có thì NoLyrics."""
    params = {"track_name": song, **({"artist_name": artist} if artist else {})} if song else {"q": query}
    recs = [r for r in _get("search", params) if r.get("syncedLyrics") and not r.get("instrumental")]
    if not recs and song and artist:                 # tên ca sĩ trên YouTube hay khác LRCLIB (feat., viết tắt...)
        recs = [r for r in _get("search", {"q": f"{artist} {song}"}) if r.get("syncedLyrics")]
    if not recs:
        raise NoLyrics(f"Không tìm thấy lời có mốc thời gian cho “{(artist + ' - ' + song).strip(' -') or query}”. "
                       "Thử ghi đúng tên bài + ca sĩ, hoặc chọn bài khác.")
    best = max(recs, key=lambda r: _score(r, artist, song or query, duration))
    return {"artist": best.get("artistName") or artist, "song": best.get("trackName") or song,
            "duration": float(best.get("duration") or 0), "lrc": best["syncedLyrics"]}


def split_query(text):
    """"Ca sĩ - Tên bài" -> (ca sĩ, tên bài); không có dấu gạch thì (“”, cả câu)."""
    parts = re.split(r"\s+[-–—|]\s+", text.strip(), maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", text.strip())


def pick_audio(cands, song, duration):
    """Video YouTube có độ dài khớp bản LRCLIB nhất (cùng bản thu âm) -> video hoặc None."""
    key = norm(song)
    ok = [v for v in cands if v.get("duration") and abs(v["duration"] - duration) <= DUR_OK
          and (not key or key in norm(v.get("title")))]
    ok = [v for v in ok if not EXTRA.search(v.get("title") or "") or EXTRA.search(song or "")] or ok
    return max(ok, key=lambda v: (-abs(v["duration"] - duration) // 1, v.get("views") or 0), default=None)


def window(ls, anchor, length, duration):
    """Đoạn [a, b] dài khoảng `length` giây: bắt đầu ở câu gần mốc điệp khúc `anchor`, kết thúc ngay trước một câu mới
    (không cắt ngang câu). length = 0: cả bài."""
    if not ls:
        raise NoLyrics("Bài này không có lời")
    if not length:
        return 0.0, min(duration, ls[-1][1] + 3.0, MAX_LEN)
    near = [x for x in ls if abs(x[0] - anchor) <= 5 and x[0] + length * 0.6 <= duration] or \
        [x for x in ls if x[0] + length * 0.6 <= duration] or ls[:1]
    a = max(0.0, min(near, key=lambda x: abs(x[0] - anchor))[0] - 0.3)
    ends = [x[0] - 0.1 for x in ls if a + length * 0.8 <= x[0] - 0.1 <= a + length * 1.25]
    b = min(ends, key=lambda e: abs(e - a - length)) if ends else min(duration, a + length)
    return a, b


def timeline(ls, a, b, offset=0.0):
    """Các câu trong đoạn [a, b], tính lại thời gian từ 0 (offset: + = lời hiện muộn hơn) -> [(t0, t1, câu)]."""
    out = []
    for t0, t1, text in ls:
        t0, t1 = t0 + offset - a, t1 + offset - a
        if t1 > 0.8 and t0 < b - a - 0.4:              # câu chỉ kịp loé lên ở mép đoạn thì bỏ
            out.append((max(0.0, t0), min(b - a, t1), text))
    return out


def chunks(tl, total):
    """Mốc đổi cảnh nền: ở đầu câu hát, mỗi cảnh CHUNK_MIN–CHUNK_MAX giây -> [độ dài từng cảnh]."""
    cuts = [0.0]
    for t0, _, _ in tl:
        if t0 - cuts[-1] >= CHUNK_MIN and total - t0 >= CHUNK_MIN:
            cuts.append(t0)
    out = []
    for x, y in zip(cuts, cuts[1:] + [total]):
        n = max(1, round((y - x) / CHUNK_MAX + 0.49))     # câu / nhạc dạo quá dài: chia đều thành nhiều cảnh
        out += [(y - x) / n] * n
    return out


# ---------- hình ----------
def _wrap(d, text, size, max_w, max_lines):
    toks = text.split() if " " in text.strip() else list(text)
    sep = " " if " " in text.strip() else ""
    while True:
        f = music._font(text, size)
        rows, cur = [], ""
        for t in toks:
            nxt = (cur + sep + t) if cur else t
            if d.textlength(nxt, font=f) <= max_w or not cur:
                cur = nxt
            else:
                rows.append(cur)
                cur = t
        rows.append(cur)
        if len(rows) <= max_lines or size <= 40:
            return rows[:max_lines], f
        size -= 4


def card(cur, nxt, head, out):
    """Khung chữ 1080x1920 trong suốt: tên bài phía trên, câu đang hát to giữa màn, câu sau nhỏ mờ bên dưới."""
    img = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # tên bài: viên thuốc mờ phía trên (chừa chỗ cho thanh trên cùng của TikTok)
    text, f = music._fit(d, "♪ " + head, 40, TW - 200, "SemiBold")
    w = d.textlength(text, font=f)
    x0, y0 = (TW - w) / 2 - 30, 250
    d.rounded_rectangle((x0, y0, x0 + w + 60, y0 + 76), 38, fill=(0, 0, 0, 120))
    d.text((TW / 2, y0 + 38), text, font=f, fill=(255, 255, 255, 235), anchor="mm")
    glow = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    if cur:
        rows, f = _wrap(d, cur, 96, TW - 140, 3)
        lh = f.size * 1.22
        y = TH * 0.52 - lh * len(rows) / 2
        for r in rows:
            g.text((TW / 2, y + lh / 2), r, font=f, fill=(0, 0, 0, 200), anchor="mm", stroke_width=14,
                   stroke_fill=(0, 0, 0, 200))
            d.text((TW / 2, y + lh / 2), r, font=f, fill=(255, 255, 255, 255), anchor="mm", stroke_width=5,
                   stroke_fill=(0, 0, 0, 230))
            y += lh
        if nxt:
            rows2, f2 = _wrap(d, nxt, 54, TW - 200, 2)
            y += 36
            for r in rows2:
                d.text((TW / 2, y + f2.size * 0.6), r, font=f2, fill=(255, 255, 255, 150), anchor="mm",
                       stroke_width=3, stroke_fill=(0, 0, 0, 150))
                y += f2.size * 1.2
    else:                                            # nhạc dạo
        d.text((TW / 2, TH * 0.52), "♪  ♪  ♪", font=music._font("♪", 90), fill=(255, 255, 255, 210), anchor="mm",
               stroke_width=4, stroke_fill=(0, 0, 0, 200))
    out_img = Image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)), img)
    out_img.save(out)
    return out


def cards(tl, total, head, work):
    """Ảnh chữ phủ kín [0, total] (khoảng trống giữa các câu = ảnh ♪) -> [(ảnh, độ dài)]."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    spans, t = [], 0.0
    for i, (t0, t1, text) in enumerate(tl):
        if t0 - t > 0.4:
            spans.append((t, t0, "", text))
        elif spans and t0 > t:                       # khe nhỏ: kéo câu trước tới câu này
            spans[-1] = (spans[-1][0], t0, *spans[-1][2:])
        spans.append((max(t, t0), t1, text, tl[i + 1][2] if i + 1 < len(tl) else ""))
        t = t1
    if total - t > 0.05:
        spans.append((t, total, "", ""))
    out = []
    for k, (x, y, cur, nxt) in enumerate(spans):
        if y - x > 0.02:
            out.append((card(cur, nxt, head, work / f"card_{k:03d}.png"), y - x))
    return out


def _bars(src):
    """Viền đen sẵn trong clip (video 4:3 / phim có letterbox) -> "crop=w:h:x:y," để cắt bỏ, không có thì ""."""
    p = subprocess.run([compose.FFMPEG, "-hide_banner", "-t", "3", "-i", str(src), "-vf", "cropdetect=24:2:0",
                        "-an", "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", p.stderr)
    m = re.search(r"\b(\d{2,5})x(\d{2,5})\b", re.search(r"Video:.*", p.stderr)[0]) if "Video:" in p.stderr else None
    if not found or not m:
        return ""
    w, h, x, y = map(int, found[-1])
    if w < int(m[1]) * 0.6 or h < int(m[2]) * 0.6 or (w >= int(m[1]) - 8 and h >= int(m[2]) - 8):   # cảnh tối cả mảng: không phải viền
        return ""
    return f"crop={w}:{h}:{x}:{y},"


def scene(src, out, dur):
    """Một cảnh nền: bỏ viền đen, cắt kín khung dọc 9:16, tối nhẹ cho chữ dễ đọc, không tiếng; clip ngắn quá thì lặp lại."""
    compose.run(["-stream_loop", "-1", "-i", str(src), "-t", f"{dur:.3f}", "-an", "-vf",
                 f"{_bars(src)}scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH},fps={FPS},"
                 "eq=brightness=-0.10:saturation=1.08,setsar=1",
                 *compose.VCODEC, str(out)])
    return out


def render(scenes, card_list, audio, a, b, out, work):
    """Ghép cảnh nền + lớp chữ + tiếng bài hát đoạn [a, b] (fade đầu / cuối) -> MP4 1080x1920."""
    work = Path(work)
    total = b - a
    lst = work / "scenes.txt"
    lst.write_text("".join(f"file '{Path(s).resolve().as_posix()}'\n" for s in scenes), encoding="utf-8")
    bg = work / "bg.mp4"
    compose.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(bg)])
    ov = work / "cards.txt"
    body = "".join(f"file '{Path(p).resolve().as_posix()}'\nduration {d:.3f}\n" for p, d in card_list)
    ov.write_text("ffconcat version 1.0\n" + body + f"file '{Path(card_list[-1][0]).resolve().as_posix()}'\n",
                  encoding="utf-8")
    fade = min(1.5, total / 6)
    compose.run(["-i", str(bg), "-f", "concat", "-safe", "0", "-i", str(ov), "-ss", f"{a:.3f}", "-t", f"{total:.3f}",
                 "-i", str(audio), "-filter_complex",
                 f"[1:v]fps={FPS},format=rgba[c];[0:v][c]overlay=0:0:eof_action=repeat,format=yuv420p[v];"
                 f"[2:a]afade=t=in:d=0.25,afade=t=out:st={total - fade:.3f}:d={fade:.3f}[a]",
                 "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}", *compose.VCODEC, *compose.ACODEC,
                 "-movflags", "+faststart", str(out)])
    return out


TAGS_VI = ["#lyrics", "#nhachay", "#lyricsvideo", "#xuhuong", "#fyp"]
TAGS_EN = ["#lyrics", "#lyricsvideo", "#songlyrics", "#music", "#fyp"]


def caption(artist, song, tl):
    """Caption TikTok: tên bài + một câu hát làm mồi + hashtag (dưới 2200 ký tự)."""
    hook = max((x[2] for x in tl), key=len, default="")
    vi = any(ch in "ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ" for ch in
             (song + " " + " ".join(x[2] for x in tl)).lower())
    tag = "#" + re.sub(r"\W", "", norm(artist).replace(" ", "")) if artist else ""
    tags = (TAGS_VI if vi else TAGS_EN) + ([tag] if len(tag) > 2 else [])
    return (f"{song} – {artist} 🎶\n“{hook}”\n\n" if artist else f"{song} 🎶\n“{hook}”\n\n") + " ".join(tags)
