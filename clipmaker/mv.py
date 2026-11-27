"""MV lời bài hát ảnh AI: ca sĩ / sân khấu / khán giả / cảnh minh hoạ lời bài do AI vẽ (Pollinations),
ảnh chuyển động nhẹ (zoom, lia), đổi cảnh ở đầu câu hát, lời gốc chạy chữ theo tiếng hát + lời dịch tiếng Việt bên dưới.
Ra bản ngang 16:9 (YouTube) và bản dọc 9:16 (TikTok / Reels) từ cùng một bộ ảnh.

Người và khán giả trong ảnh là người do AI vẽ, không phải người thật: video luôn ghi "Hình ảnh minh hoạ bằng AI".
"""
import io
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import caption, compose, lyrics, music

FPS = 30
SIZES = {"h": (1920, 1080), "v": (1080, 1920)}
IMG_W, IMG_H = 1344, 768          # ảnh AI vẽ khung ngang; bản dọc đặt ảnh giữa nền mờ của chính ảnh đó
SCENE_MIN, SCENE_MAX = 5.0, 9.0   # s: mỗi cảnh dài khoảng này, đổi cảnh ở đầu câu hát
MAX_IMAGES = 24                   # bài dài: các cảnh dùng lại ảnh (đổi hướng chuyển động)
CLASSIC = "https://image.pollinations.ai/prompt/"     # không cần key: chậm (~1 ảnh / 45 giây), ảnh nhỏ, có logo
GEN = "https://gen.pollinations.ai/image/"            # có API key: model flux, ảnh to, không logo
FG_Y = 470                        # bản dọc: mép trên ảnh (dưới thanh trên cùng của TikTok)
AI_NOTE = "Hình ảnh minh hoạ bằng AI"

SINGER = {
    "f": "a beautiful young Asian female singer with long black hair in a loose low bun, thin round glasses, "
         "small earrings, elegant black halter dress",
    "m": "a handsome young Asian male singer with short black hair, black suit jacket over a white shirt",
}
KINDS = {
    "singer": "close-up portrait of {S} singing emotionally into a microphone, eyes closed, concert stage, "
              "deep blue spotlights, bokeh lights",
    "stage": "wide shot of {S} standing alone on a grand concert stage, beams of blue light, sparkling lights, "
             "light haze",
    "audience": "audience in a dark concert hall listening to a sad song, moved, some with teary eyes, "
                "blue stage light glow on faces",
    "story": "{L}, emotional cinematic scene, young Asian couple, nostalgic mood, soft natural light",
}
STORY_FALLBACK = "a nostalgic memory of a young couple in an autumn park, falling leaves"
STYLE = "cinematic film still, photorealistic, dramatic lighting, shallow depth of field, high detail, no text"
# thứ tự cảnh: xen ca sĩ hát với cảnh minh hoạ lời, thỉnh thoảng toàn cảnh sân khấu / khán giả
PATTERN = ["singer", "story", "stage", "story", "singer", "audience", "story", "singer", "story", "stage"]
LANG_TAG = {"zh": "#nhactrung", "ko": "#nhachan", "ja": "#nhacnhat", "th": "#nhacthai", "en": "#nhacaumy"}


class DrawError(RuntimeError):
    pass


def lang_of(lines):
    text = "".join(lines)
    if re.search(r"[぀-ヿ]", text):
        return "ja"
    if re.search(r"[가-힯]", text):
        return "ko"
    if re.search(r"[一-鿿]", text):
        return "zh"
    if re.search(r"[฀-๿]", text):
        return "th"
    return "vi" if any(ch in caption.VI_CHARS for ch in text.lower()) else "en"


# ---------- kế hoạch cảnh ----------
def cuts(tl, total):
    """Độ dài từng cảnh: đổi cảnh ở đầu câu hát, mỗi cảnh SCENE_MIN–SCENE_MAX giây -> [(bắt đầu, độ dài)]."""
    marks = [0.0]
    for t0, _, _ in tl:
        if t0 - marks[-1] >= SCENE_MIN and total - t0 >= SCENE_MIN:
            marks.append(t0)
    out = []
    for x, y in zip(marks, marks[1:] + [total]):
        n = max(1, round((y - x) / SCENE_MAX + 0.49))
        out += [(x + (y - x) * k / n, (y - x) / n) for k in range(n)]
    return out


def plan(tl, total, n_max=MAX_IMAGES):
    """-> (cảnh [{s, d, img}], ảnh [{kind, line}]). Cảnh đầu (nhạc dạo) là ca sĩ trên sân khấu;
    ảnh minh hoạ lấy câu hát đang vang lúc cảnh bắt đầu."""
    sc = cuts(tl, total)
    n = min(len(sc), n_max)
    images = []
    for i in range(n):
        s = sc[i][0]
        kind = "stage" if i == 0 else PATTERN[(i - 1) % len(PATTERN)]
        line = next((x for t0, t1, x in tl if t0 <= s + 1.0 < t1), "") or \
            next((x for t0, _, x in tl if t0 >= s), "")
        images.append({"kind": kind, "line": line})
    return [{"s": round(s, 3), "d": round(d, 3), "img": i % n} for i, (s, d) in enumerate(sc)], images


NOTES = {"do", "re", "mi", "fa", "so", "sol", "la", "si", "ti", "la-la", "oh", "ah", "yeah", "na"}


def _drawable(en):
    """Câu hát có nghĩa để vẽ (không phải câu hát nốt nhạc / "la la la" / quá ngắn)."""
    words = re.findall(r"[a-z'-]+", (en or "").lower())
    return len(words) >= 3 and sum(w in NOTES for w in words) <= len(words) / 2


def prompt(img, singer, en=""):
    if img["kind"] == "story":
        what = f"a scene illustrating the lyric \"{re.sub(r'[\"“”]', '', en).strip(' .,')}\"" if _drawable(en) \
            else STORY_FALLBACK
        body = KINDS["story"].format(L=what)
    else:
        body = KINDS[img["kind"]].format(S=SINGER.get(singer, SINGER["f"]))
    return f"{body}, {STYLE}"


# ---------- vẽ ảnh ----------
def _sleep(sec, stop):
    end = time.time() + sec
    while time.time() < end:
        if stop():
            raise InterruptedError
        time.sleep(min(1.0, end - time.time()))


def draw(text, seed, out, key="", stop=lambda: False, wait=lambda sec: None, tries=12):
    """Vẽ một ảnh IMG_W x IMG_H -> out (JPEG). Gói miễn phí bị giới hạn lượt: tự chờ rồi thử lại."""
    params = {"width": IMG_W, "height": IMG_H, "seed": seed, "nologo": "true"}
    q = urllib.parse.quote(text[:900], safe="")
    if key:
        url, headers = GEN + q + "?" + urllib.parse.urlencode({**params, "model": "flux"}), \
            {"Authorization": f"Bearer {key}"}
    else:
        url, headers = CLASSIC + q + "?" + urllib.parse.urlencode(params), {}
    headers["User-Agent"] = "clip-compiler (lyrics MV maker)"
    for _ in range(tries):
        if stop():
            raise InterruptedError
        delay = 15
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=180) as r:
                data = r.read()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            tmp = Path(out).with_suffix(".part")
            img.save(tmp, "JPEG", quality=92)
            tmp.replace(out)
            return out
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if key and e.code in (401, 403):
                raise DrawError("API key Pollinations không đúng hoặc đã bị thu hồi") from None
            if key and e.code == 402:
                raise DrawError("Tài khoản Pollinations hết lượt vẽ (pollen). Nạp thêm hoặc xoá key để dùng gói miễn phí") \
                    from None
            if e.code not in (402, 429, 500, 502, 503, 504):
                raise DrawError(f"Vẽ ảnh lỗi HTTP {e.code}: {body[:160]}") from None
            m = re.search(r"[Rr]etry after (\d+)", body)
            delay = int(m[1]) + 2 if m else 25
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
        wait(delay)
        _sleep(delay, stop)
    raise DrawError("Dịch vụ vẽ ảnh đang quá tải, thử lại sau ít phút")


# ---------- dựng ----------
def _motion(k, n):
    """Chuyển động cảnh thứ k (n khung): xen kẽ zoom vào / zoom ra, lia trái / phải / đứng giữa."""
    z = f"1.0+0.12*on/{n}" if k % 2 == 0 else f"1.12-0.12*on/{n}"
    x = [f"iw/2-(iw/zoom/2)", f"(iw-iw/zoom)*on/{n}", f"(iw-iw/zoom)*(1-on/{n})"][k % 3]
    return f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={n}"


def scene(img, out, dur, fmt, k):
    """Một cảnh: ảnh tĩnh chuyển động nhẹ. Ngang: ảnh kín khung. Dọc: ảnh 16:9 giữa nền mờ của chính ảnh."""
    W, H = SIZES[fmt]
    n = max(1, round(dur * FPS))
    if fmt == "h":
        vf = (f"scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,{_motion(k, n)}:s={W}x{H}:fps={FPS},"
              "vignette=PI/6,format=yuv420p")
        args = ["-i", str(img), "-vf", vf]
    else:
        fh = round(W * 9 / 16 / 2) * 2
        fc = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40,"
              f"eq=brightness=-0.22:saturation=1.1,loop=loop=-1:size=1,fps={FPS}[bg];"
              f"[b]scale=2160:{fh * 2}:force_original_aspect_ratio=increase,crop=2160:{fh * 2},"
              f"{_motion(k, n)}:s={W}x{fh}:fps={FPS}[fg];[bg][fg]overlay=0:{FG_Y},format=yuv420p")
        args = ["-i", str(img), "-filter_complex", fc]
    compose.run([*args, "-frames:v", str(n), *compose.VCODEC, "-an", str(out)])
    return out


def _ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _esc(s):
    return re.sub(r"[{}\\]", "", s or "").replace("\n", " ").strip()


CJK_FONT = "Noto Sans CJK SC"


def ass(tl, vi, out, fmt):
    """Phụ đề: lời gốc chạy màu theo tiếng hát (karaoke), lời Việt vàng nhạt bên dưới.
    vi: {câu gốc: câu dịch} (bỏ trống / bài tiếng Việt thì chỉ có lời gốc)."""
    W, H = SIZES[fmt]
    tall = fmt == "v"
    o_size, v_size = (76, 56) if tall else (70, 52)
    # bản dọc: chữ ngay dưới ảnh; bản ngang: chữ đáy khung
    align, margin = (8, FG_Y + round(W * 9 / 16) + 60) if tall else (2, 70)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Orig,Be Vietnam Pro ExtraBold,{o_size},&H0040D6FF,&H00FFFFFF,&H00301010,&H80000000,0,0,0,0,100,100,1,0,1,4,3,{align},{int(W * 0.07)},{int(W * 0.07)},{margin},1
Style: Vi,Be Vietnam Pro ExtraBold,{v_size},&H00B4F0FF,&H00B4F0FF,&H00301010,&H80000000,0,0,0,0,100,100,0,0,1,3,2,{align},{int(W * 0.07)},{int(W * 0.07)},{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for t0, t1, text in tl:
        toks, sep = lyrics._tokens(_esc(text))
        if not toks:
            continue
        times = lyrics.word_times(t0, t1, toks) + [None]
        sing_end = t0 + min((t1 - t0) * 0.85, 0.42 * len(toks) + 0.3)
        kara = ""
        for i, w in enumerate(toks):
            nxt = times[i + 1] if times[i + 1] is not None else sing_end
            kara += f"{{\\kf{max(1, round((nxt - times[i]) * 100))}}}{w}{sep if i < len(toks) - 1 else ''}"
        fn = f"{{\\fn{CJK_FONT}\\b1}}" if music.CJK.search(text) else ""
        line = fn + kara
        tr = _esc((vi or {}).get(text, ""))
        if tr and tr != _esc(text):
            line += "\\N{\\rVi}" + tr
        rows.append(f"Dialogue: 0,{_ts(t0)},{_ts(t1)},Orig,,0,0,0,,{line}")
    Path(out).write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    return Path(out)


def _font(text, size):
    if music.CJK.search(text) or music.THAI.search(text):
        return music._font(text, size)
    return ImageFont.truetype(str(compose.FONTS / "BeVietnamPro-ExtraBold.ttf"), size)


def _text(d, xy, text, f, fill, anchor="mm", stroke=6):
    d.text(xy, text, font=f, fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=(10, 10, 20, 255))


def _fit(d, text, size, max_w, min_size=28, font=None):
    font = font or _font
    f = font(text, size)
    while d.textlength(text, font=f) > max_w and size > min_size:
        size -= 4
        f = font(text, size)
    return f


def overlay_png(song, artist, fmt, out):
    """Lớp chữ cố định: tên bài nhỏ ở góc + ghi chú ảnh AI."""
    W, H = SIZES[fmt]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    head = f"♪ {song}" + (f" · {artist}" if artist else "")
    if fmt == "h":
        f = _fit(d, head, 34, W * 0.45, font=music._font)
        d.text((48, 40), head, font=f, fill=(255, 255, 255, 220), stroke_width=3, stroke_fill=(0, 0, 0, 160))
        d.text((40, H - 26), AI_NOTE, font=_font(AI_NOTE, 22), fill=(255, 255, 255, 150), anchor="ls")
    else:
        f = _fit(d, head, 38, W - 200, font=music._font)
        w = d.textlength(head, font=f)
        x0, y0 = (W - w) / 2 - 28, 300
        d.rounded_rectangle((x0, y0, x0 + w + 56, y0 + 70), 35, fill=(0, 0, 0, 120))
        d.text((W / 2, y0 + 35), head, font=f, fill=(255, 255, 255, 235), anchor="mm")
        d.text((30, FG_Y + round(W * 9 / 16) - 14), AI_NOTE, font=_font(AI_NOTE, 22),
               fill=(255, 255, 255, 160), anchor="ls")
    img.save(out)
    return out


def _title_lines(song):
    """Tên bài dài -> 2 dòng (dòng dưới to hơn, giống ảnh bìa MV)."""
    words = song.split()
    if len(words) < 3 or music.CJK.search(song):
        return "", song
    k = (len(words) + 1) // 2
    return " ".join(words[:k]), " ".join(words[k:])


def title_png(song, artist, fmt, out, sub="Vietsub"):
    """Tên bài lớn hiện ở đầu video (vài giây rồi mờ dần)."""
    W, H = SIZES[fmt]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    top, big = _title_lines(song)
    cx, cy = (W * 0.68, H * 0.42) if fmt == "h" else (W / 2, H - 440)      # dọc: dưới vùng lời hát
    max_w = W * 0.56 if fmt == "h" else W - 100
    if top:
        _text(d, (cx, cy - 70), top, _fit(d, top, 92, max_w), (255, 226, 40, 255))
        _text(d, (cx, cy + 50), big.upper(), _fit(d, big.upper(), 130, max_w), (255, 255, 255, 255), stroke=8)
    else:
        _text(d, (cx, cy), big, _fit(d, big, 120, max_w), (255, 255, 255, 255), stroke=8)
    if artist:
        _text(d, (cx, cy + 170), artist, _fit(d, artist, 44, max_w), (120, 255, 170, 255), stroke=4)
    if sub:
        _text(d, (cx, cy + 235), sub, _font(sub, 38), (255, 120, 120, 255), stroke=4)
    img.save(out)
    return out


def render(scenes, images, tl, vi, song, artist, audio, a, b, fmt, out, work, stop=lambda: False,
           report=lambda frac: None, sub="Vietsub"):
    """Dựng một bản (fmt "h" ngang / "v" dọc) -> out MP4."""
    work = Path(work)
    total = b - a
    parts = []
    for k, sc in enumerate(scenes):
        if stop():
            raise InterruptedError
        parts.append(scene(images[sc["img"]], work / f"{fmt}_scene_{k:03d}.mp4", sc["d"], fmt, k))
        report(0.85 * (k + 1) / len(scenes))
    lst = work / f"{fmt}_scenes.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    bg = work / f"{fmt}_bg.mp4"
    compose.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(bg)])
    over = overlay_png(song, artist, fmt, work / f"{fmt}_over.png")
    title = title_png(song, artist, fmt, work / f"{fmt}_title.png", sub)
    subs = ass(tl, vi, work / f"{fmt}_subs.ass", fmt)
    fade = min(2.5, total / 8)
    t_end = min(7.0, max(3.0, (tl[0][0] if tl else 7.0) - 0.3))      # tên bài mờ đi trước câu hát đầu
    fc = (f"[0:v][1:v]overlay=0:0[o];"
          f"[2:v]format=rgba,fade=t=in:st=0.3:d=0.7:alpha=1,fade=t=out:st={t_end - 0.8:.2f}:d=0.8:alpha=1[t];"
          f"[o][t]overlay=0:0:eof_action=pass[o2];"
          f"[o2]subtitles=filename={subs.name}:fontsdir={compose.FONTS},"
          f"fade=t=out:st={total - fade:.3f}:d={fade:.3f},format=yuv420p[v];"
          f"[3:a]afade=t=in:d=0.4,afade=t=out:st={total - fade:.3f}:d={fade:.3f}[a]")
    args = [compose.FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(bg.resolve()), "-loop", "1", "-i", over.name,
            "-loop", "1", "-t", f"{t_end:.2f}", "-i", title.name, "-ss", f"{a:.3f}", "-t", f"{total:.3f}",
            "-i", str(Path(audio).resolve()), "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            *compose.VCODEC, *compose.ACODEC, "-movflags", "+faststart", str(Path(out).resolve())]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=work)
    if p.returncode:
        raise RuntimeError("ffmpeg: " + p.stderr.strip()[-500:])
    report(1.0)
    return out


def thumbnail(img, song, artist, out, sub="Vietsub"):
    """Ảnh bìa YouTube 1280x720: ảnh ca sĩ + tên bài chữ to (ảnh lấy từ chính video)."""
    base = Image.open(img).convert("RGB")
    base = base.resize((1280, round(1280 * base.height / base.width)), Image.LANCZOS)
    top = (base.height - 720) // 2
    base = base.crop((0, max(0, top), 1280, max(0, top) + 720)).resize((1280, 720)).convert("RGBA")
    shade = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle((620, 0, 1280, 720), fill=(0, 0, 20, 110))
    base = Image.alpha_composite(base, shade.filter(ImageFilter.GaussianBlur(60)))
    d = ImageDraw.Draw(base)
    t, big = _title_lines(song)
    cx = 955
    if t:
        _text(d, (cx, 250), t, _fit(d, t, 96, 600), (255, 226, 40, 255))
        _text(d, (cx, 380), big.upper(), _fit(d, big.upper(), 140, 600), (255, 255, 255, 255), stroke=9)
    else:
        _text(d, (cx, 320), big, _fit(d, big, 130, 600), (255, 255, 255, 255), stroke=9)
    if artist:
        _text(d, (cx, 500), artist, _fit(d, artist, 46, 560), (120, 255, 170, 255), stroke=5)
    if sub:
        _text(d, (cx, 575), sub.upper(), _font(sub, 50), (255, 90, 90, 255), stroke=6)
    base.convert("RGB").save(out, "JPEG", quality=92)
    return out


def youtube_text(song, artist, lang, tl, vi):
    """Tiêu đề + mô tả gợi ý cho YouTube: tên bài, ca sĩ, lời gốc kèm lời Việt, ghi chú ảnh AI."""
    tag = " | Vietsub" if lang != "vi" else " | Lyrics"
    title = (f"{song} – {artist}" if artist else song) + tag
    seen, rows = set(), []
    for _, _, x in tl:
        if x in seen:
            continue
        seen.add(x)
        tr = (vi or {}).get(x, "")
        rows.append(x + (f"\n{tr}" if tr and tr != x else ""))
    desc = (f"🎧 {song}" + (f" – {artist}" if artist else "") + "\n"
            f"🖼 {AI_NOTE}; nhân vật trong video không phải người thật.\n\n"
            "Lời bài hát" + (" & lời dịch" if lang != "vi" else "") + ":\n" + "\n\n".join(rows))
    return title[:100], desc[:4900]
