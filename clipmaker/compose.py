"""Ghép video: mỗi đoạn được đưa về cùng khung 1920x1080 / 30 fps / âm lượng chuẩn, gắn số thứ tự, nguồn, chú thích;
sau đoạn có thể chèn phần bình luận bằng giọng của người làm video (khung hình dừng + giọng thu âm).
Mọi phần cùng thông số mã hoá nên nối bằng concat không cần mã hoá lại.
"""
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .youtube import FFMPEG

FONTS = Path(__file__).resolve().parent.parent / "fonts"
W, H, FPS, SR = 1920, 1080, 30, 48000
ACCENT = (255, 176, 46)
VCODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
          "-video_track_timescale", "15360"]
ACODEC = ["-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2"]


def font(size, weight="Bold"):
    return ImageFont.truetype(str(FONTS / f"Inter-{weight}.ttf"), size)


def run(args):
    p = subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError("ffmpeg: " + p.stderr.strip()[-400:])


def probe(path):
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    import re

    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    return {"duration": dur, "audio": "Audio:" in err, "video": "Video:" in err}


def _wrap(d, text, f, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if d.textlength(test, font=f) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def overlay(number=None, channel="", title="", caption=""):
    """Lớp phủ trong suốt: số thứ tự (góc trên trái), nguồn (góc dưới trái), chú thích kiểu meme (trên cùng)."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if number is not None:
        f = font(54)
        label = f"#{number}"
        w = d.textlength(label, font=f) + 44
        d.rounded_rectangle([36, 32, 36 + w, 112], radius=18, fill=ACCENT + (235,))
        d.text((36 + w / 2, 72), label, font=f, fill=(25, 18, 8), anchor="mm")
    if channel or title:
        f1, f2 = font(28, "SemiBold"), font(22, "Regular")
        t1 = f"Nguồn: {channel}" if channel else "Nguồn"
        t2 = textwrap.shorten(title, 70, placeholder="…") if title else ""
        w = max(d.textlength(t1, font=f1), d.textlength(t2, font=f2)) + 40
        h = 86 if t2 else 56
        d.rounded_rectangle([30, H - 30 - h, 30 + w, H - 30], radius=14, fill=(0, 0, 0, 150))
        d.text((50, H - 30 - h + 14), t1, font=f1, fill=(255, 255, 255))
        if t2:
            d.text((50, H - 30 - h + 52), t2, font=f2, fill=(220, 220, 220))
    if caption:
        f = font(64)
        lines = _wrap(d, caption.upper(), f, W * 0.86)[:2]
        y = 140 if number is not None else 50
        for l in lines:
            d.text((W / 2, y), l, font=f, fill=(255, 255, 255), anchor="mt", stroke_width=6, stroke_fill=(0, 0, 0))
            y += 78
    return img


SUB_BOTTOM = 150            # px: phụ đề nằm trên hộp "Nguồn" ở góc dưới


def subtitle_png(text, path):
    """Một câu phụ đề: chữ trắng viền đen, nền tối mờ, tối đa 2 dòng (ảnh vừa khít, đặt giữa đáy khung)."""
    f = font(52)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = _wrap(tmp, text, f, W * 0.72)[:2]
    lw = max(tmp.textlength(l, font=f) for l in lines)
    img = Image.new("RGBA", (int(lw + 60), 72 * len(lines) + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=16, fill=(0, 0, 0, 120))
    for i, l in enumerate(lines):
        d.text((img.width / 2, 12 + i * 72), l, font=f, fill=(255, 255, 255), anchor="mt",
               stroke_width=4, stroke_fill=(0, 0, 0))
    img.save(path)
    return path, img.size


def clip_segment(src, out, start_in_file, length, ov_png, work, subs=()):
    """Đoạn nguồn -> khung chuẩn: hình không bị méo (nền mờ lấp chỗ trống), âm lượng chuẩn, mờ vào/ra ngắn.
    subs: [(t0, t1, text)] phụ đề theo giờ của đoạn, in thẳng vào hình."""
    info = probe(src)
    length = min(length, max(0.5, info["duration"] - start_in_file)) if info["duration"] else length
    fo = max(0.0, length - 0.25)
    v = (f"[0:v]fps={FPS},split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
         f"boxblur=24:2,eq=brightness=-0.1[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
         f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v0];[v0][1:v]overlay=0:0[vs0]")
    sub_inputs, last = [], "vs0"
    for i, (t0, t1, text) in enumerate(s for s in subs if s[0] < length):
        png, (sw, sh) = subtitle_png(text, Path(work) / f"{Path(out).stem}_sub{i}.png")
        sub_inputs += ["-loop", "1", "-t", f"{length:.3f}", "-i", str(png)]
        idx = 2 + i
        v += (f";[{last}][{idx}:v]overlay=(W-w)/2:H-h-{SUB_BOTTOM}:"
              f"enable='between(t,{t0:.2f},{min(t1, length):.2f})'[vs{i + 1}]")
        last = f"vs{i + 1}"
    v += f";[{last}]fade=t=in:st=0:d=0.2,fade=t=out:st={fo:.2f}:d=0.25,format=yuv420p,setsar=1[v]"
    if info["audio"]:
        a = (f";[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample={SR},aformat=channel_layouts=stereo,"
             f"afade=t=in:d=0.15,afade=t=out:st={fo:.2f}:d=0.25[a]")
        amap, extra = "[a]", []
    else:
        a, amap = "", f"{2 + len(sub_inputs) // 6}:a"
        extra = ["-f", "lavfi", "-t", f"{length:.3f}", "-i", f"anullsrc=r={SR}:cl=stereo"]
    run(["-ss", f"{start_in_file:.3f}", "-i", str(src), "-i", str(ov_png), *sub_inputs, *extra,
         "-filter_complex", v + a, "-map", "[v]", "-map", amap, "-t", f"{length:.3f}", *VCODEC, *ACODEC, str(out)])
    return out


def still_segment(image, out, length, audio=None):
    """Ảnh tĩnh (thẻ tiêu đề / khung dừng bình luận) + tiếng (giọng thu âm hoặc im lặng)."""
    args = ["-loop", "1", "-framerate", str(FPS), "-t", f"{length:.3f}", "-i", str(image)]
    if audio:
        args += ["-i", str(audio)]
        af = (f"[1:a]loudnorm=I=-15:TP=-1.5,aresample={SR},aformat=channel_layouts=stereo,"
              f"apad,atrim=0:{length:.3f}[a]")
    else:
        args += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", f"anullsrc=r={SR}:cl=stereo"]
        af = "[1:a]anull[a]"
    vf = f"[0:v]scale={W}:{H},format=yuv420p,setsar=1,fade=t=in:d=0.2,fade=t=out:st={max(0, length - 0.25):.2f}:d=0.25[v]"
    run([*args, "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}",
         *VCODEC, *ACODEC, str(out)])
    return out


def last_frame(video, png):
    run(["-sseof", "-0.4", "-i", str(video), "-frames:v", "1", "-update", "1", str(png)])
    return png


def commentary_card(frame_png, text, out_png):
    """Khung dừng cho phần bình luận: hình cuối của đoạn, làm tối, kèm chữ + biểu tượng micro."""
    from PIL import ImageFilter

    img = Image.open(frame_png).convert("RGB").resize((W, H)).filter(ImageFilter.GaussianBlur(6))
    img = Image.blend(img, Image.new("RGB", (W, H), (10, 10, 20)), 0.45)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([W / 2 - 150, 120, W / 2 + 150, 190], radius=35, fill=ACCENT)
    d.text((W / 2, 155), "MY TAKE", font=font(36), fill=(25, 18, 8), anchor="mm")
    if text:
        f = font(70)
        lines = _wrap(d, text, f, W * 0.8)[:4]
        y = H / 2 - len(lines) * 45
        for l in lines:
            d.text((W / 2, y), l, font=f, fill=(255, 255, 255), anchor="mt", stroke_width=5, stroke_fill=(0, 0, 0))
            y += 90
    img.save(out_png)
    return out_png


def title_card(bg_png, title, subtitle, out_png):
    from PIL import ImageFilter

    img = (Image.open(bg_png).convert("RGB").resize((W, H)).filter(ImageFilter.GaussianBlur(14))
           if bg_png and Path(bg_png).exists() else Image.new("RGB", (W, H), (20, 20, 30)))
    img = Image.blend(img, Image.new("RGB", (W, H), (10, 10, 20)), 0.5)
    d = ImageDraw.Draw(img)
    f = font(110)
    lines = _wrap(d, title, f, W * 0.85)[:3]
    y = H / 2 - len(lines) * 62 - 30
    for l in lines:
        d.text((W / 2, y), l, font=f, fill=(255, 255, 255), anchor="mt", stroke_width=6, stroke_fill=(0, 0, 0))
        y += 126
    if subtitle:
        d.text((W / 2, y + 30), subtitle, font=font(48, "SemiBold"), fill=ACCENT, anchor="mt")
    img.save(out_png)
    return out_png


def concat(parts, out, work):
    lst = Path(work) / "concat.txt"
    lst.write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
    return out


# ---------- thumbnail ----------
YELLOW, RED = (255, 225, 60), (230, 30, 40)


def _pop(img):
    """Màu rực + tương phản cao kiểu thumbnail YouTube."""
    from PIL import ImageEnhance

    img = ImageEnhance.Color(img).enhance(1.35)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    return ImageEnhance.Sharpness(img).enhance(1.3)


def _cover(img, w, h, zoom=1.0):
    k = max(w / img.width, h / img.height) * zoom
    img = img.resize((int(img.width * k) + 1, int(img.height * k) + 1), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 3
    return img.crop((x, y, x + w, y + h))


def _big_text(d, text, box, fill, max_size=150, lines_max=2):
    x0, y0, x1, y1 = box
    size = max_size
    while size > 40:
        f = font(size)
        lines = _wrap(d, text, f, x1 - x0)
        if len(lines) <= lines_max and len(lines) * size * 1.05 <= y1 - y0:
            break
        size -= 6
    f = font(size)
    lines = _balance(d, _wrap(d, text, f, x1 - x0)[:lines_max], f)
    y = y1 - len(lines) * size * 1.05
    for l in lines:
        d.text((x0, y), l, font=f, fill=fill, stroke_width=max(6, size // 11), stroke_fill=(0, 0, 0))
        y += size * 1.05


def _balance(d, lines, f):
    """2 dòng: chia lại chữ cho hai dòng dài gần bằng nhau (tránh dòng thứ hai chỉ có 1 chữ)."""
    if len(lines) != 2:
        return lines
    words = " ".join(lines).split()
    best = min(range(1, len(words)), key=lambda i: max(d.textlength(" ".join(words[:i]), font=f),
                                                       d.textlength(" ".join(words[i:]), font=f)))
    return [" ".join(words[:best]), " ".join(words[best:])]


def _badge(d, text, cx, cy, r=92):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=RED, outline=(255, 255, 255), width=8)
    f = font(int(r * 0.62) if len(text) <= 3 else int(r * 0.45))
    d.text((cx, cy), text, font=f, fill=(255, 255, 255), anchor="mm")


def _arrow(d, x, y, s=1.0, angle_left=True):
    k = -1 if angle_left else 1
    pts = [(x, y), (x + k * 110 * s, y - 70 * s), (x + k * 80 * s, y - 20 * s), (x + k * 190 * s, y + 40 * s),
           (x + k * 160 * s, y + 80 * s), (x + k * 50 * s, y + 20 * s), (x + k * 30 * s, y + 70 * s)]
    d.polygon(pts, fill=RED, outline=(255, 255, 255), width=6)


def thumbnails(frames, text, top_n, out_dir):
    """3 mẫu thumbnail 1280x720 từ khung hình các đoạn hay nhất: ghép 3 ảnh, 1 ảnh to + mũi tên, dải chữ vàng."""
    out_dir = Path(out_dir)
    imgs = [_pop(Image.open(f).convert("RGB")) for f in frames if Path(f).exists()]
    if not imgs:
        imgs = [Image.new("RGB", (1280, 720), (30, 30, 40))]
    while len(imgs) < 3:
        imgs.append(imgs[0])
    text = (text or "").upper()
    badge = f"TOP {top_n}" if top_n else ""
    paths = []
    # 1. ghép 3 ảnh
    t = Image.new("RGB", (1280, 720), (255, 255, 255))
    t.paste(_cover(imgs[0], 800, 720), (0, 0))
    t.paste(_cover(imgs[1], 472, 356), (808, 0))
    t.paste(_cover(imgs[2], 472, 356), (808, 364))
    shade = Image.new("L", (1280, 720), 0)
    ImageDraw.Draw(shade).rectangle([0, 400, 1280, 720], fill=150)
    from PIL import ImageFilter

    t = Image.composite(Image.new("RGB", t.size, (0, 0, 0)), t, shade.filter(ImageFilter.GaussianBlur(60)))
    d = ImageDraw.Draw(t)
    _big_text(d, text, (40, 380, 1180, 690), YELLOW)
    if badge:
        _badge(d, badge, 1170, 110, 92)
    paths.append(out_dir / "thumb_1.jpg")
    t.save(paths[-1], quality=92)
    # 2. một ảnh to, chữ trắng phía trên, mũi tên đỏ chỉ vào giữa
    t = _cover(imgs[0], 1280, 720, zoom=1.12)
    shade = Image.new("L", (1280, 720), 0)
    ImageDraw.Draw(shade).rectangle([0, 0, 1280, 250], fill=170)
    t = Image.composite(Image.new("RGB", t.size, (0, 0, 0)), t, shade.filter(ImageFilter.GaussianBlur(50)))
    d = ImageDraw.Draw(t)
    _big_text(d, text, (40, 20, 1240, 250), (255, 255, 255), max_size=130)
    _arrow(d, 760, 470, 1.3)
    if badge:
        _badge(d, badge, 140, 600, 86)
    paths.append(out_dir / "thumb_2.jpg")
    t.save(paths[-1], quality=92)
    # 3. hai ảnh cạnh nhau + dải vàng chữ đen
    t = Image.new("RGB", (1280, 720))
    t.paste(_cover(imgs[1], 636, 720), (0, 0))
    t.paste(_cover(imgs[0], 636, 720), (644, 0))
    d = ImageDraw.Draw(t)
    d.rectangle([636, 0, 644, 720], fill=(255, 255, 255))
    d.polygon([(0, 520), (1280, 470), (1280, 720), (0, 720)], fill=YELLOW)
    f = font(100)
    lines = _wrap(d, text, f, 1180)[:2]
    size = 100 if len(lines) == 1 else 76
    f = font(size)
    lines = _wrap(d, text, f, 1180)[:2]
    y = 720 - 30 - len(lines) * size * 1.05
    for l in lines:
        d.text((640, y), l, font=f, fill=(15, 15, 15), anchor="mt")
        y += size * 1.05
    if badge:
        _badge(d, badge, 1170, 110, 86)
    paths.append(out_dir / "thumb_3.jpg")
    t.save(paths[-1], quality=92)
    return paths
