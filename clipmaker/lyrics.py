"""Video TikTok lời bài hát: lời có mốc thời gian (LRCLIB) -> chọn đoạn điệp khúc -> nền là các clip hay cắt dọc 9:16,
đổi cảnh đúng lúc sang câu mới -> chữ lời hiện từng câu khớp tiếng hát.

Lời của LRCLIB khớp với bản thu âm gốc, nên tiếng lấy từ video YouTube có độ dài bằng độ dài bài trên LRCLIB
(thường là "Official Audio"); MV thường có đoạn mở đầu riêng làm lời lệch.
"""
import json
import math
import re
import subprocess
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
                       "Thử ghi đúng tên bài + ca sĩ, hoặc chọn bài khác. Bài Trung / Hàn / Nhật: ghi tên gốc "
                       "(vd: 欧阳耀莹 - 戒不掉); video đăng lại đặt tên tiếng Việt thường không tìm được lời.")
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


# ---------- chữ: từng chữ bật ra theo tiếng hát ----------
WORD_FONT = compose.FONTS / "BeVietnamPro-ExtraBold.ttf"
WORD_SIZE = 62
BAND_Y, BAND_H = 960, 600       # vùng chữ: giữa-dưới màn (tránh tên bài phía trên, caption + nút bên phải của TikTok)
TEXT_W = 820
POP = 0.2                        # s: chữ mới phóng to rồi thu về trong ngần này
POP_SCALE = 0.22
FADE = 0.15                      # s: hết một trang chữ thì mờ đi
NOW = (255, 214, 64)             # chữ đang hát: vàng; chữ đã hát: trắng
_WF = {}


def _wfont(text, size):
    if music.CJK.search(text) or music.THAI.search(text):
        return music._font(text, size)
    if size not in _WF:
        _WF[size] = ImageFont.truetype(str(WORD_FONT), size)
    return _WF[size]


def _tokens(text):
    """Từ (có dấu cách) hoặc từng chữ (Trung / Nhật / Thái viết liền)."""
    return (text.split(), " ") if " " in text.strip() else (list(text.strip()), "")


def word_times(t0, t1, toks):
    """LRCLIB chỉ có mốc từng câu: rải các từ trong lúc hát câu (từ dài hát lâu hơn chút), chừa khoảng ngân cuối câu."""
    sing = min((t1 - t0) * 0.85, 0.42 * len(toks) + 0.3)
    w = [1 + 0.06 * len(x) for x in toks]
    out, acc = [], 0.0
    for x in w:
        out.append(t0 + sing * acc / sum(w))
        acc += x
    return out


def _sprite(text, f, color):
    """Một từ: chữ đậm + bóng mềm phía dưới (dễ đọc trên mọi nền, không viền đen cứng)."""
    l, _, r, _ = f.getbbox(text)
    asc, desc = f.getmetrics()
    pad = 24                                         # cao bằng nhau cho mọi từ: các từ cùng một đường chân chữ
    img = Image.new("RGBA", (r - l + 2 * pad, asc + desc + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((pad - l, pad + 5), text, font=f, fill=(0, 0, 0, 190), stroke_width=4,
                            stroke_fill=(0, 0, 0, 190))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(7)))
    ImageDraw.Draw(img).text((pad - l, pad), text, font=f, fill=(*color, 255), stroke_width=2,
                             stroke_fill=(0, 0, 0, 90))
    return img, pad


def _rows(ws, gap, soft=()):
    """Chia một nhóm từ (độ rộng ws) thành 1 hoặc 2 dòng dài gần bằng nhau, ưu tiên ngắt sau dấu phẩy
    (soft: vị trí ngắt đẹp) -> [(đầu, cuối)]."""
    width = lambda i, j: sum(ws[i:j]) + gap * (j - i - 1)
    n = len(ws)
    if width(0, n) <= TEXT_W or n < 2:
        return [(0, n)]
    fits = [k for k in range(1, n) if max(width(0, k), width(k, n)) <= TEXT_W]
    if fits:
        k = min(fits, key=lambda k: max(width(0, k), width(k, n)) - (TEXT_W * 0.25 if k in soft else 0))
        return [(0, k), (k, n)]
    out, i = [], 0                                    # từ quá dài: xếp lần lượt
    for j in range(1, n + 1):
        if j == n or width(i, j + 1) > TEXT_W:
            out.append((i, j))
            i = j
    return out


def _split(ws, gap, n, soft=()):
    """Chia các từ thành n nhóm liền nhau, độ dài gần bằng nhau (lệch 1 từ để ngắt ở dấu phẩy) -> [(đầu, cuối)]."""
    out, i = [], 0
    for g in range(n, 0, -1):
        rest = sum(ws[i:]) + gap * (len(ws) - i - 1)
        j, w = i, 0.0
        while j < len(ws) - (g - 1) and (j == i or w + gap + ws[j] / 2 <= rest / g):
            w += ws[j] + (gap if j > i else 0)
            j += 1
        if g > 1 and j not in soft:
            j = next((x for x in (j - 1, j + 1) if x in soft and i < x <= len(ws) - (g - 1)), j)
        out.append((i, j if g > 1 else len(ws)))
        i = out[-1][1]
    return out


def _groups(ws, gap, soft=()):
    """Câu dài -> ít trang nhất mà mỗi trang vừa 2 dòng, các trang dài gần bằng nhau
    (không để trang cuối chỉ còn vài từ)."""
    total = sum(ws) + gap * (len(ws) - 1)
    for n in range(max(1, math.ceil(total / (TEXT_W * 2))), len(ws) + 1):
        for gs in (_split(ws, gap, n, soft), _split(ws, gap, n)):
            if all(len(_rows(ws[i:j], gap, {k - i for k in soft})) <= 2 for i, j in gs):
                return gs
    return [(i, i + 1) for i in range(len(ws))]


def pages(tl):
    """Câu -> các trang chữ (tối đa 2 dòng) -> [{t0, t1, words: [(t, ảnh trắng, ảnh vàng, x, y, pad)]}]."""
    d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    out = []
    for t0, t1, text in tl:
        toks, sep = _tokens(text)
        if not toks:
            continue
        times = word_times(t0, t1, toks)
        f = _wfont(text, WORD_SIZE)
        gap = d.textlength(" ", font=f) if sep else 2
        lh = int(f.size * 1.3)
        ws = [d.textlength(x, font=f) for x in toks]
        soft = {k + 1 for k, x in enumerate(toks[:-1]) if x[-1] in ",.;:!?…，、。"}     # ngắt đẹp: sau dấu câu
        cur = []
        for gi, gj in _groups(ws, gap, soft):
            rows = [(gi + i, gi + j) for i, j in _rows(ws[gi:gj], gap, {k - gi for k in soft})]
            words, top = [], (BAND_H - lh * len(rows)) / 2
            for k, (i, j) in enumerate(rows):
                x = (TW - (sum(ws[i:j]) + gap * (j - i - 1))) / 2
                for m in range(i, j):
                    white, pad = _sprite(toks[m], f, (255, 255, 255))
                    words.append((times[m], white, _sprite(toks[m], f, NOW)[0], x, top + k * lh, pad))
                    x += ws[m] + gap
            cur.append({"t0": words[0][0], "words": words})
        for k, p in enumerate(cur):
            p["t1"] = cur[k + 1]["t0"] if k + 1 < len(cur) else t1
        out += cur
    return out


def _ease(x):
    return 1 - (1 - x) ** 3


def still(pg, t):
    """Số chữ đang hiện nếu khung lúc t đứng yên (không chữ nào đang bật, không mờ dần), ngược lại None:
    hai khung đứng yên cùng số chữ thì giống hệt nhau, khỏi vẽ lại."""
    shown = sum(w[0] <= t for w in pg["words"])
    moving = any(0 <= t - w[0] < POP for w in pg["words"])
    return None if moving or pg["t1"] - t < FADE else shown


def band(pg, t):
    """Ảnh RGBA vùng chữ lúc t (trong trang pg)."""
    shown = [w for w in pg["words"] if w[0] <= t]
    fade = max(0.0, min(1.0, (pg["t1"] - t) / FADE))
    img = Image.new("RGBA", (TW, BAND_H), (0, 0, 0, 0))
    for k, (wt, white, now, x, y, pad) in enumerate(shown):
        spr = now if k == len(shown) - 1 else white
        age = t - wt
        if age < POP:
            s = 1 + POP_SCALE * (1 - _ease(age / POP))
            big = spr.resize((int(spr.width * s), int(spr.height * s)), Image.BILINEAR)
            if age < 0.06:
                big.putalpha(big.getchannel("A").point(lambda v, a=age / 0.06: int(v * a)))
            cx, cy = x - pad + spr.width / 2, y - pad + spr.height / 2
            img.alpha_composite(big, (int(cx - big.width / 2), int(cy - big.height / 2)))
        else:
            img.alpha_composite(spr, (int(x - pad), int(y - pad)))
    if fade < 1:
        img.putalpha(img.getchannel("A").point(lambda v: int(v * fade)))
    return img


def head_png(head, out):
    """Tên bài: viên thuốc mờ phía trên (chừa thanh trên cùng của TikTok)."""
    img = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    text, f = music._fit(d, "♪ " + head, 36, TW - 240, "SemiBold")
    w = d.textlength(text, font=f)
    x0, y0 = (TW - w) / 2 - 28, 250
    d.rounded_rectangle((x0, y0, x0 + w + 56, y0 + 68), 34, fill=(0, 0, 0, 110))
    d.text((TW / 2, y0 + 34), text, font=f, fill=(255, 255, 255, 230), anchor="mm")
    img.save(out)
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


def render(scenes, tl, head, audio, a, b, out, work):
    """Ghép cảnh nền + tên bài + chữ bật từng từ (vẽ từng khung, đưa thẳng vào ffmpeg) + tiếng bài hát đoạn [a, b]
    (fade đầu / cuối) -> MP4 1080x1920."""
    work = Path(work)
    total = b - a
    lst = work / "scenes.txt"
    lst.write_text("".join(f"file '{Path(s).resolve().as_posix()}'\n" for s in scenes), encoding="utf-8")
    bg = work / "bg.mp4"
    compose.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(bg)])
    hp = head_png(head, work / "head.png")
    fade = min(1.5, total / 6)
    log = open(work / "ffmpeg.log", "w+", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [compose.FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(bg),
         "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{TW}x{BAND_H}", "-r", str(FPS), "-i", "-",
         "-loop", "1", "-i", str(hp), "-ss", f"{a:.3f}", "-t", f"{total:.3f}", "-i", str(audio), "-filter_complex",
         f"[0:v][2:v]overlay=0:0[h];[h][1:v]overlay=0:{BAND_Y}:eof_action=pass,format=yuv420p[v];"
         f"[3:a]afade=t=in:d=0.25,afade=t=out:st={total - fade:.3f}:d={fade:.3f}[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}", *compose.VCODEC, *compose.ACODEC,
         "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE, stderr=log)
    pg_list = pages(tl)
    empty = bytes(TW * BAND_H * 4)
    last_key, last = None, empty
    try:
        k = 0
        for i in range(int(total * FPS + 0.999)):
            t = i / FPS
            while k < len(pg_list) and t >= pg_list[k]["t1"]:
                k += 1
            if k < len(pg_list) and t >= pg_list[k]["t0"]:
                n = still(pg_list[k], t)
                key = None if n is None else (k, n)
                if key is None or key != last_key:
                    last = band(pg_list[k], t).tobytes()
                last_key = key
                frame = last
            else:
                frame, last_key = empty, None
            proc.stdin.write(frame)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    code = proc.wait()
    log.seek(0)
    if code:
        raise RuntimeError("ffmpeg: " + log.read().strip()[-400:])
    log.close()
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
