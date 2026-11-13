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


def subtitle_png(text, path, max_w=W * 0.72):
    """Một câu phụ đề: chữ trắng viền đen, nền tối mờ, tối đa 2 dòng (ảnh vừa khít, đặt giữa đáy khung)."""
    f = font(52)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = _wrap(tmp, text, f, max_w)[:2]
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


# ---------- Shorts (dọc 1080x1920) ----------
SW, SH = 1080, 1920
SHORT_VIDEO_Y = 640          # video 16:9 rộng 1080 (cao ~608) đặt giữa; chữ hook phía trên, phụ đề phía dưới


def short_overlay(hook, bottom, number=None):
    img = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if hook:
        size = 96
        while size > 50:
            f = font(size)
            lines = _wrap(d, hook.upper(), f, SW * 0.88)
            if len(lines) <= 3:
                break
            size -= 6
        lines = lines[:3]
        y = SHORT_VIDEO_Y - 50 - len(lines) * size * 1.08
        for l in lines:
            d.text((SW / 2, y), l, font=f, fill=(255, 255, 255), anchor="mt", stroke_width=8, stroke_fill=(0, 0, 0))
            y += size * 1.08
    if number is not None:
        f = font(50)
        label = f"#{number}"
        w = d.textlength(label, font=f) + 40
        d.rounded_rectangle([SW / 2 - w / 2, 150, SW / 2 + w / 2, 226], radius=18, fill=ACCENT + (240,))
        d.text((SW / 2, 188), label, font=f, fill=(25, 18, 8), anchor="mm")
    if bottom:
        f = font(46, "SemiBold")
        w = d.textlength(bottom, font=f) + 60
        d.rounded_rectangle([SW / 2 - w / 2, SH - 430, SW / 2 + w / 2, SH - 350], radius=40, fill=(230, 30, 40, 235))
        d.text((SW / 2, SH - 390), bottom, font=f, fill=(255, 255, 255), anchor="mm")
    return img


def short_segment(src, out, length, ov_png, work, subs=()):
    """Một đoạn -> video dọc cho Shorts: nền mờ phủ kín, video giữa, hook phía trên, phụ đề ngay dưới video."""
    info = probe(src)
    length = min(length, info["duration"]) if info["duration"] else length
    v = (f"[0:v]fps={FPS},split[a][b];[a]scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
         f"boxblur=30:2,eq=brightness=-0.18[bg];[b]scale={SW}:-2[fg];"
         f"[bg][fg]overlay=0:{SHORT_VIDEO_Y}[v0];[v0][1:v]overlay=0:0[vs0]")
    sub_inputs, last = [], "vs0"
    sub_y = SHORT_VIDEO_Y + SW * 9 // 16 + 40
    for i, (t0, t1, text) in enumerate(s for s in subs if s[0] < length):
        png, _ = subtitle_png(text, Path(work) / f"{Path(out).stem}_sub{i}.png", max_w=SW * 0.8)
        sub_inputs += ["-loop", "1", "-t", f"{length:.3f}", "-i", str(png)]
        v += (f";[{last}][{2 + i}:v]overlay=(W-w)/2:{sub_y}:"
              f"enable='between(t,{t0:.2f},{min(t1, length):.2f})'[vs{i + 1}]")
        last = f"vs{i + 1}"
    v += f";[{last}]format=yuv420p,setsar=1[v]"
    if info["audio"]:
        a, amap, extra = f";[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample={SR},aformat=channel_layouts=stereo[a]", "[a]", []
    else:
        a, amap = "", f"{2 + len(sub_inputs) // 6}:a"
        extra = ["-f", "lavfi", "-t", f"{length:.3f}", "-i", f"anullsrc=r={SR}:cl=stereo"]
    run(["-i", str(src), "-i", str(ov_png), *sub_inputs, *extra, "-filter_complex", v + a, "-map", "[v]",
         "-map", amap, "-t", f"{length:.3f}", *VCODEC, *ACODEC, "-movflags", "+faststart", str(out)])
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


MODELS = Path(__file__).resolve().parent.parent / "models"
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
FACE_MODELS = {"det": "face_detection_yunet/face_detection_yunet_2023mar.onnx",
               "rec": "face_recognition_sface/face_recognition_sface_2021dec.onnx",
               "seg": "human_segmentation_pphumanseg/human_segmentation_pphumanseg_2023mar.onnx"}
SAME_FACE = 0.363          # ngưỡng cosine của SFace: >= là cùng một người


def _model(key):
    """Mô hình ONNX của OpenCV Zoo, tải một lần vào models/ -> đường dẫn."""
    import urllib.request

    MODELS.mkdir(exist_ok=True)
    rel = FACE_MODELS[key]
    path = MODELS / Path(rel).name
    if not path.exists():
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(ZOO + rel, tmp)
        tmp.replace(path)
    return str(path)


def _face_models():
    """YuNet (tìm mặt) + SFace (so mặt), ~39 MB."""
    import cv2

    return (cv2.FaceDetectorYN.create(_model("det"), "", (320, 320), 0.8),
            cv2.FaceRecognizerSF.create(_model("rec"), ""))


def best_shots(cands, out_dir, n=3):
    """Chọn khung hình cho thumbnail: có MẶT NHÂN VẬT CHÍNH to, đang có hành động (chuyển động mạnh), nét.
    Nhân vật chính = gương mặt xuất hiện ở nhiều đoạn nhất (video tổng hợp Trump -> mặt Trump), nên không lấy nhầm
    người dẫn chương trình hay khung hoạt hình. cands: [(file video, giây đỉnh trong đoạn, độ liên quan 0..1)]
    -> [{img, face, act}] tối đa n ảnh; face / act: hộp (x0, y0, x1, y1) theo toạ độ ảnh gốc."""
    import cv2
    import numpy as np

    try:
        det, rec = _face_models()
    except Exception:                       # không tải được mô hình: chỉ dựa vào chuyển động + độ nét
        det = rec = None
    frames = []                             # (clip, ảnh, [(hộp, đặc trưng, diện tích)], chuyển động, hộp chuyển động, nét)
    for k, (src, peak, rel) in enumerate(cands):
        cap = cv2.VideoCapture(str(src))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
        for t in np.arange(max(0.3, peak - 2.5), min(total - 0.1, peak + 2.5), 0.5):
            cap.set(cv2.CAP_PROP_POS_MSEC, (t - 0.2) * 1000)
            ok0, prev = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if not (ok and ok0):
                continue
            h, w = fr.shape[:2]
            sc = 640 / w
            small = cv2.resize(fr, (640, int(h * sc)))
            faces = []
            if det is not None:
                det.setInputSize((small.shape[1], small.shape[0]))
                found = det.detect(small)[1]
                for f in (found if found is not None else []):
                    if f[2] * f[3] < 900:                 # mặt quá nhỏ (< 30x30) không dùng được
                        continue
                    feat = rec.feature(rec.alignCrop(small, f)).flatten()
                    box = tuple(int(v / sc) for v in (f[0], f[1], f[0] + f[2], f[1] + f[3]))
                    faces.append((box, feat / (np.linalg.norm(feat) + 1e-9), f[2] * f[3] / (small.shape[0] * 640)))
            g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            g0 = cv2.cvtColor(cv2.resize(prev, (640, small.shape[0])), cv2.COLOR_BGR2GRAY)
            diff = cv2.GaussianBlur(cv2.absdiff(g, g0), (9, 9), 0)
            motion = min(float(diff.mean()) / 12, 1.0)
            act = None
            ys, xs = np.nonzero(diff > max(25, np.percentile(diff, 98)))
            if motion > 0.15 and len(xs) > 30:
                act = tuple(int(v / sc) for v in (np.percentile(xs, 10), np.percentile(ys, 10),
                                                  np.percentile(xs, 90), np.percentile(ys, 90)))
            sharp = min(cv2.Laplacian(g, cv2.CV_64F).var() / 300, 1.0)
            frames.append((k, rel, fr, faces, motion, act, sharp))
        cap.release()
    # nhân vật chính: gom mặt theo người (so đặc trưng), người có mặt ở nhiều đoạn nhất (ưu tiên đoạn liên quan)
    people = []                             # [đặc trưng đại diện, {clip}]
    for k, rel, _, faces, *_ in frames:
        for _, feat, _ in faces:
            for p in people:
                if float(feat @ p[0]) >= SAME_FACE:
                    p[1].add(k)
                    break
            else:
                people.append([feat, {k}])
    weight = {k: 1 + rel for k, (_, _, rel) in enumerate(cands)}
    main = max(people, key=lambda p: sum(weight[k] for k in p[1]))[0] if people else None
    best = {}
    for k, rel, fr, faces, motion, act, sharp in frames:
        mine = [(b, a) for b, f, a in faces if main is not None and float(f @ main) >= SAME_FACE]
        face, size = max(mine, key=lambda x: x[1]) if mine else (None, 0)
        score = (3 if face else 0) + 2.5 * min(size, 0.08) / 0.08 + 1.5 * motion + 0.5 * sharp + rel
        if not face and faces:              # khung chỉ có người khác (MC, khán giả): hạ điểm
            score -= 1
        if k not in best or score > best[k][0]:
            best[k] = (score, fr, face, act)
    out = []
    for k, (score, fr, face, act) in sorted(best.items(), key=lambda x: -x[1][0])[:n]:
        path = Path(out_dir) / f"shot_{k}.png"
        cv2.imwrite(str(path), fr)
        out.append({"img": path, "face": face, "act": act})
    return out


def _focus(shot, w, h, zoom=1.0, face_h=0.0):
    """Cắt ảnh về w x h, lấy tâm ở mặt người (hoặc vùng hành động) thay vì giữa khung. face_h > 0: phóng to tới khi
    mặt cao bằng face_h * h. Trả (ảnh, hàm đổi toạ độ ảnh gốc -> ảnh mới)."""
    img = shot["img"] if isinstance(shot["img"], Image.Image) else Image.open(shot["img"]).convert("RGB")
    face = shot.get("face")
    box = face or shot.get("act")
    k = max(w / img.width, h / img.height) * zoom
    if face and face_h:
        k = max(k, min(face_h * h / (face[3] - face[1]), k * 3))
    big = img.resize((int(img.width * k) + 1, int(img.height * k) + 1), Image.LANCZOS)
    if box:
        cx, cy = (box[0] + box[2]) / 2 * k, (box[1] + box[3]) / 2 * k
    else:
        cx, cy = big.width / 2, big.height / 2.6
    x = int(min(max(cx - w / 2, 0), big.width - w))
    y = int(min(max(cy - h * 0.4, 0), big.height - h))
    return _pop(big.crop((x, y, x + w, y + h))), lambda b: (b[0] * k - x, b[1] * k - y, b[2] * k - x, b[3] * k - y)


def _ring(d, box, pad=30):
    x0, y0, x1, y1 = box
    d.ellipse([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=(255, 255, 255), width=18)
    d.ellipse([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=RED, width=11)


def _clip_box(b, w, h, min_side=170):
    """Hộp trong khung, không quá nhỏ (vòng đỏ quá bé thì không thấy)."""
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    bw, bh = max(b[2] - b[0], min_side), max(b[3] - b[1], min_side)
    bw, bh = min(bw, w * 0.55), min(bh, h * 0.6)
    return (max(40, cx - bw / 2), max(40, cy - bh / 2), min(w - 40, cx + bw / 2), min(h - 40, cy + bh / 2))


def _person_mask(img, face=None):
    """Tách người khỏi nền (PP-HumanSeg, ~6 MB) -> ảnh L cùng cỡ (255 = người), chỉ giữ người chứa mặt nhân vật chính
    (hoặc mảng lớn nhất). None nếu không tách được."""
    import cv2
    import numpy as np

    try:
        net = cv2.dnn.readNet(_model("seg"))
    except Exception:
        return None
    arr = np.asarray(img.convert("RGB"))
    net.setInput(cv2.dnn.blobFromImage(arr, 1 / 127.5, (192, 192), (127.5, 127.5, 127.5)))
    out = net.forward()[0]
    prob = np.exp(out[1]) / (np.exp(out[0]) + np.exp(out[1]))
    prob = cv2.resize(prob.astype(np.float32), (img.width, img.height), interpolation=cv2.INTER_CUBIC)
    hard = (prob > 0.5).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(hard)
    if n <= 1:
        return None
    keep = None
    if face:
        cx, cy = (face[0] + face[2]) // 2, (face[1] + face[3]) // 2
        lab = labels[min(cy, img.height - 1), min(cx, img.width - 1)]
        keep = lab if lab else None
    if keep is None:
        keep = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[keep, cv2.CC_STAT_AREA] < 0.03 * img.width * img.height:
        return None
    m = np.where(labels == keep, prob, 0)
    m = cv2.GaussianBlur((m * 255).astype(np.uint8), (0, 0), max(1.5, img.width / 900))
    return Image.fromarray(m)


def _cutout(shot):
    """Nhân vật chính tách nền -> ảnh RGBA đã cắt sát người, hoặc None."""
    img = shot["img"] if isinstance(shot["img"], Image.Image) else Image.open(shot["img"]).convert("RGB")
    mask = _person_mask(img, shot.get("face"))
    if mask is None:
        return None
    rgba = _pop(img).convert("RGBA")
    rgba.putalpha(mask)
    box = mask.point(lambda v: 255 if v > 40 else 0).getbbox()
    return rgba.crop(box) if box else None


def _sticker(rgba, stroke=12):
    """Viền trắng dày + bóng đen mờ quanh người (kiểu dán sticker)."""
    from PIL import ImageFilter

    pad = stroke * 3
    a = Image.new("L", (rgba.width + 2 * pad, rgba.height + 2 * pad), 0)
    a.paste(rgba.getchannel("A"), (pad, pad))
    hard = a.point(lambda v: 255 if v > 90 else 0)
    ring = hard.filter(ImageFilter.MaxFilter(stroke * 2 + 1)).filter(ImageFilter.GaussianBlur(1.2))
    shadow = hard.filter(ImageFilter.MaxFilter(stroke * 2 + 1)).filter(ImageFilter.GaussianBlur(stroke * 1.6))
    out = Image.new("RGBA", a.size, (0, 0, 0, 0))
    out.paste((0, 0, 0, 255), (0, 0), shadow.point(lambda v: int(v * 0.85)))
    out.paste((255, 255, 255, 255), (0, 0), ring)
    out.alpha_composite(rgba, (pad, pad))
    return out


def _burst(w, h, center, c1, c2, rays=22, glow_a=170):
    """Nền tia sáng toả ra từ center, hai màu xen kẽ + sáng dần ở giữa."""
    import math

    img = Image.new("RGB", (w, h), c1)
    d = ImageDraw.Draw(img)
    cx, cy = center
    r = math.hypot(w, h)
    for i in range(rays):
        if i % 2:
            a0, a1 = 2 * math.pi * i / rays, 2 * math.pi * (i + 1) / rays
            d.polygon([(cx, cy), (cx + r * math.cos(a0), cy + r * math.sin(a0)),
                       (cx + r * math.cos(a1), cy + r * math.sin(a1))], fill=c2)
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse([cx - 380, cy - 380, cx + 380, cy + 380], fill=glow_a)
    from PIL import ImageFilter

    return Image.composite(Image.new("RGB", (w, h), (255, 245, 200)), img, glow.filter(ImageFilter.GaussianBlur(160)))


def _vignette(img, strength=170):
    from PIL import ImageFilter

    m = Image.new("L", img.size, strength)
    ImageDraw.Draw(m).ellipse([-img.width * 0.1, -img.height * 0.15, img.width * 1.1, img.height * 1.15], fill=0)
    return Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, m.filter(ImageFilter.GaussianBlur(120)))


def _impact(size):
    path = FONTS / "Anton-Regular.ttf"
    return ImageFont.truetype(str(path), size) if path.exists() else font(size)


def _headline(base, text, box, angle=-4, colors=((255, 255, 255), YELLOW), max_size=170, lines_max=3):
    """Chữ tiêu đề kiểu thumbnail: font đậm khổ hẹp, viền đen dày + bóng, dòng xen màu trắng / vàng,
    chữ cuối nằm trong ô đỏ (điểm nhấn), xoay nhẹ cho có lực. Vẽ lên base (RGB) trong vùng box."""
    from PIL import ImageFilter

    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    words = text.upper().split()
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    size = max_size
    while size > 40:
        f = _impact(size)
        lines, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if probe.textlength(t, font=f) <= bw * 0.92 or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= lines_max and len(lines) * size * 1.08 <= bh * 0.92:
            break
        size -= 6
    f = _impact(size)
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    total = len(lines) * size * 1.08
    y = (bh - total) / 2
    sw = max(5, size // 13)
    for li, line in enumerate(lines):
        x = 10
        last = li == len(lines) - 1
        head, tail = (line.rsplit(" ", 1) if last and " " in line else (("", line) if last else (line, "")))
        if head:
            d.text((x, y), head, font=f, fill=colors[li % 2], stroke_width=sw, stroke_fill=(0, 0, 0))
            x += d.textlength(head + " ", font=f)
        if tail:                                   # chữ cuối: chữ trắng trên ô đỏ
            l_, t_, r_, b_ = d.textbbox((x, y), tail, font=f)
            pad = sw + size // 9
            d.rounded_rectangle([l_ - pad, t_ - pad, r_ + pad, b_ + pad], radius=16, fill=RED, outline=(0, 0, 0),
                                width=max(4, sw // 2))
            d.text((x, y), tail, font=f, fill=(255, 255, 255), stroke_width=max(3, sw // 2), stroke_fill=(0, 0, 0))
        y += size * 1.08
    layer = layer.rotate(angle, resample=Image.BICUBIC, expand=False)
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.putalpha(layer.getchannel("A").filter(ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.8)))
    out = base.convert("RGBA")
    out.alpha_composite(shadow, (x0 + 8, y0 + 10))
    out.alpha_composite(layer, (x0, y0))
    return out.convert("RGB")


def _bubble(face_img, d_px):
    """Mặt cận cảnh trong vòng tròn viền trắng + đỏ (bong bóng biểu cảm)."""
    from PIL import ImageFilter

    face_img = face_img.resize((d_px, d_px), Image.LANCZOS)
    m = Image.new("L", (d_px, d_px), 0)
    ImageDraw.Draw(m).ellipse([0, 0, d_px - 1, d_px - 1], fill=255)
    out = Image.new("RGBA", (d_px + 60, d_px + 60), (0, 0, 0, 0))
    sh = Image.new("L", out.size, 0)
    ImageDraw.Draw(sh).ellipse([22, 30, d_px + 38, d_px + 46], fill=200)
    out.paste((0, 0, 0, 255), (0, 0), sh.filter(ImageFilter.GaussianBlur(14)))
    dr = ImageDraw.Draw(out)
    dr.ellipse([14, 14, d_px + 46, d_px + 46], fill=RED)
    dr.ellipse([22, 22, d_px + 38, d_px + 38], fill=(255, 255, 255))
    out.paste(face_img, (30, 30), m)
    return out


def _fit(rgba, max_w, max_h):
    k = min(max_w / rgba.width, max_h / rgba.height)
    return rgba.resize((max(1, int(rgba.width * k)), max(1, int(rgba.height * k))), Image.LANCZOS)


def thumbnails(shots, text, top_n, out_dir):
    """3 mẫu thumbnail 1280x720 kiểu kênh lớn:
    1. "Sticker": nhân vật chính tách nền, viền trắng, to tràn khung trên nền tia sáng + ảnh mờ, chữ to xoay nhẹ, mũi tên.
    2. "Reaction": khung hành động rực màu + bong bóng mặt cận cảnh + vòng đỏ chỗ hành động, chữ có ô đỏ nhấn.
    3. "Split": nửa trái nhân vật tách nền trên nền đỏ, nửa phải khung hành động, vạch vàng chéo, chữ dưới.
    Không tách được người (không có người / thiếu mô hình) thì dùng ảnh cắt quanh mặt."""
    from PIL import ImageFilter

    out_dir = Path(out_dir)
    shots = [s if isinstance(s, dict) else {"img": s, "face": None, "act": None} for s in shots]
    shots = [s for s in shots if isinstance(s["img"], Image.Image) or Path(s["img"]).exists()]
    if not shots:
        shots = [{"img": Image.new("RGB", (1280, 720), (30, 30, 40)), "face": None, "act": None}]
    while len(shots) < 3:
        shots.append(shots[0])
    text = (text or "").upper()
    badge = f"TOP {top_n}" if top_n else ""
    hero, wide = shots[0], shots[1]
    cut = _cutout(hero)
    paths = []

    def save(img, name):
        paths.append(out_dir / name)
        img.save(paths[-1], quality=93)

    # 1. sticker
    bg_frame = _focus(hero, 1280, 720, zoom=1.3)[0].filter(ImageFilter.GaussianBlur(14))
    burst = _burst(1280, 720, (900, 330), (200, 20, 30), (255, 120, 0))
    t = Image.blend(burst, bg_frame, 0.35)
    t = _vignette(t, 120)
    if cut:
        st = _sticker(_fit(cut, 820, 860))
        x = max(560, 1280 - st.width + 30)
        y = 720 - st.height + 60                   # tràn mép dưới cho cảm giác "gần"
        t = t.convert("RGBA")
        t.alpha_composite(st, (x, y))
        t = t.convert("RGB")
        t = _headline(t, text, (20, 50, x + 60, 680), angle=-5)
        if hero.get("face"):                       # mũi tên chỉ vào mặt, đặt sát mép trái người
            _arrow(ImageDraw.Draw(t), x + 30, y + 200, 0.9, angle_left=True)
    else:
        f_img, to = _focus(hero, 1280, 720, zoom=1.1, face_h=0.5)
        t = _vignette(f_img, 150)
        t = _headline(t, text, (30, 330, 900, 710), angle=-3)
    if badge:
        _badge(ImageDraw.Draw(t), badge, 1170, 100, 80)
    save(t, "thumb_1.jpg")

    # 2. reaction: chữ + bong bóng nằm phía không có mặt người trong khung toàn cảnh
    t, to = _focus(wide, 1280, 720, zoom=1.05)
    t = _vignette(t, 150)
    face_left = bool(wide.get("face")) and (to(wide["face"])[0] + to(wide["face"])[2]) / 2 < 640
    face_shot = next((x for x in shots if x.get("face") and x is not wide), hero)
    f_img, _ = _focus(face_shot, 400, 400, face_h=0.62)
    bub = _bubble(f_img, 360)
    t = t.convert("RGBA")
    t.alpha_composite(bub, (1280 - bub.width - 10 if face_left else 10, 720 - bub.height - 10))
    box = (440, 10, 1270, 300) if face_left else (20, 10, 850, 300)
    t = _headline(t.convert("RGB"), text, box, angle=-3, max_size=150, lines_max=2)
    save(t, "thumb_2.jpg")

    # 3. split
    left = _burst(700, 720, (330, 300), (150, 0, 20), (230, 40, 40), glow_a=90)
    if cut:
        st = _sticker(_fit(cut, 660, 760), stroke=10)
        left = left.convert("RGBA")
        left.alpha_composite(st, ((640 - st.width) // 2, 720 - st.height + 60))
        left = left.convert("RGB")
    else:
        left = _focus(hero, 700, 720, face_h=0.45)[0]
    right = _focus(wide, 760, 720, zoom=1.1)[0]
    t = Image.new("RGB", (1280, 720))
    t.paste(right, (520, 0))
    m = Image.new("L", (1280, 720), 0)
    ImageDraw.Draw(m).polygon([(0, 0), (700, 0), (560, 720), (0, 720)], fill=255)
    big_left = Image.new("RGB", (1280, 720))
    big_left.paste(left, (0, 0))
    t = Image.composite(big_left, t, m)
    d = ImageDraw.Draw(t)
    d.line([(700, 0), (560, 720)], fill=(0, 0, 0), width=26)
    d.line([(700, 0), (560, 720)], fill=YELLOW, width=14)
    shade = Image.new("L", (1280, 720), 0)
    ImageDraw.Draw(shade).rectangle([0, 470, 1280, 720], fill=170)
    t = Image.composite(Image.new("RGB", t.size, (0, 0, 0)), t, shade.filter(ImageFilter.GaussianBlur(50)))
    t = _headline(t, text, (60, 470, 1240, 715), angle=0, max_size=130, lines_max=2)
    if badge:
        _badge(ImageDraw.Draw(t), badge, 1170, 100, 80)
    save(t, "thumb_3.jpg")
    return paths
