"""Ghép clip từ kênh TikTok / Douyin của chính mình thành video dài, dịch phụ đề và lồng tiếng Việt.

Luồng: link kênh hoặc danh sách link -> tải từng clip (yt-dlp) -> nhận giọng nói (faster-whisper chạy trên máy)
-> dịch sang tiếng Việt (Google Dịch, miễn phí) -> đọc bằng edge-tts, mỗi câu đặt đúng lúc nhân vật nói
-> giảm tiếng gốc khi có lời lồng (vẫn giữ nhạc nền, tiếng động) -> phụ đề tiếng Việt -> MP4.
"""
import asyncio
import gc
import hashlib
import json
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import yt_dlp

from . import youtube as yt
from .youtube import FFMPEG

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "fonts"
VOICES = {"female": "vi-VN-HoaiMyNeural", "male": "vi-VN-NamMinhNeural"}
MODEL = "small"                     # ~500MB, ~1.3GB RAM khi chạy; đủ tốt cho tiếng Trung / Anh
SR = 24000                          # tần số mẫu của edge-tts
MAX_SPEED = 1.5                     # câu dịch dài hơn câu gốc: đọc nhanh tối đa 1.5 lần cho vừa chỗ
MAX_CLIPS = 60
LANG_GT = {"zh": "zh-CN", "jw": "jv", "he": "iw"}      # mã whisper -> mã Google Dịch


class DubError(Exception):
    pass


# ---------- lấy danh sách clip ----------
def parse_links(text):
    return list(dict.fromkeys(re.findall(r"https?://[^\s,;\"'<>]+", text or "")))


def _clip(e):
    url = e.get("url") or e.get("webpage_url") or ""
    if not url.startswith("http"):
        url = e.get("webpage_url") or url
    return {"key": re.sub(r"\W", "", str(e.get("id") or ""))[:40] or str(abs(hash(url)))[:12], "url": url,
            "title": (e.get("title") or e.get("description") or "")[:120], "duration": e.get("duration") or 0,
            "ts": e.get("timestamp") or 0, "on": True}


def list_channel(url, limit=MAX_CLIPS, tries=4):
    """Link kênh -> các clip, cũ nhất trước (phim chia tập thường đăng theo thứ tự)."""
    if "douyin.com/user" in url:
        raise DubError("Douyin chưa hỗ trợ lấy cả kênh. Hãy dán link từng video (mỗi dòng một link) "
                       "hoặc tải video về máy rồi bấm “Tải file lên”.")
    err = None
    for i in range(tries):
        try:
            with yt_dlp.YoutubeDL({**yt._base_opts(), "extract_flat": True, "skip_download": True, "ignore_no_formats_error": True,
                                   "playlistend": limit}) as y:
                info = y.extract_info(url, download=False)
            if not info:
                raise DubError("Không đọc được kênh")
            entries = [e for e in info.get("entries") or [] if e]
            if not entries:
                raise DubError("Kênh không có video công khai nào")
            clips = sorted((_clip(e) for e in entries), key=lambda c: c["ts"] or 0)
            return info.get("uploader") or info.get("channel") or info.get("title") or "", clips
        except DubError:
            raise
        except Exception as e:      # TikTok hay chặn tạm thời: thử lại sau vài giây
            err = e
            time.sleep(3 + 4 * i)
    raise DubError("TikTok đang chặn lấy danh sách kênh. Thử lại sau ít phút, hoặc dán link từng video "
                   "(mỗi dòng một link), hoặc tải video về máy rồi bấm “Tải file lên”.") from err


DOUYIN_COOKIES = ROOT / "data" / "douyin_cookies.txt"     # cookie douyin.com người dùng tự xuất từ trình duyệt


def canon(url):
    """Mọi dạng link một video Douyin (trang Đề xuất ?modal_id=, /note/, link chia sẻ iesdouyin, link rút gọn
    v.douyin.com) -> https://www.douyin.com/video/<id>, dạng duy nhất yt-dlp nhận. Link khác giữ nguyên."""
    if re.search(r"//v\.douyin\.com/", url):
        try:      # link rút gọn trong nút "Chia sẻ" của app: mở ra để lấy link thật
            req = urllib.request.Request(url, headers=_UA, method="HEAD")
            url = urllib.request.urlopen(req, timeout=20).geturl()
        except Exception:
            return url
    if "douyin.com" not in url:
        return url
    m = re.search(r"[?&]modal_id=(\d+)|/(?:video|note|share/video)/(\d+)", url)
    return f"https://www.douyin.com/video/{m[1] or m[2]}" if m else url


def check_douyin_cookies(text):
    """Nội dung cookies.txt -> số cookie của douyin.com; lỗi nếu sai định dạng hoặc không có cookie Douyin."""
    n = 0
    for line in text.splitlines():
        line = line.removeprefix("#HttpOnly_")
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            raise ValueError("File không đúng định dạng cookies.txt (Netscape). Xuất bằng tiện ích “Get cookies.txt LOCALLY”.")
        n += parts[0].lstrip(".").endswith("douyin.com")
    if not n:
        raise ValueError("File không có cookie của douyin.com: mở douyin.com trong trình duyệt rồi mới xuất cookie.")
    return n


def download(url, out_dir, key):
    """Tải một clip -> (file, thông tin). Đã tải rồi thì dùng lại."""
    for f in out_dir.glob(f"{key}.*"):
        if f.suffix in (".mp4", ".webm", ".mkv", ".mov"):
            return f, None
    url = canon(url)
    opts = {**yt._base_opts(), "outtmpl": str(out_dir / f"{key}.%(ext)s"), "noplaylist": True,
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b", "merge_output_format": "mp4"}
    douyin = "douyin.com" in url
    if douyin:
        opts.pop("cookiesfrombrowser", None)
        opts.pop("cookiefile", None)
        if DOUYIN_COOKIES.exists():
            opts["cookiefile"] = str(DOUYIN_COOKIES)
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
    except Exception as e:
        msg = str(e)
        if douyin and "cookie" in msg.lower():
            raise DubError(("Cookie Douyin đã hết hạn: xuất lại" if DOUYIN_COOKIES.exists() else "Douyin đòi cookie: thêm")
                           + " cookie Douyin ở ô “🍪 Cookie Douyin” phía trên (không cần đăng nhập), "
                           "hoặc tải video về máy rồi bấm “Tải file lên”.") from e
        raise DubError(f"Không tải được {url}: {msg[-200:]}") from e
    files = [f for f in out_dir.glob(f"{key}.*") if f.suffix in (".mp4", ".webm", ".mkv", ".mov")]
    if not files:
        raise DubError(f"Không tải được {url}")
    return files[0], info


def probe(path):
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", err)
    v = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", err)
    rot = re.search(r"rotation of (-?\d+)|rotate\s*:\s*(-?\d+)", err)
    w, h = (int(v[1]), int(v[2])) if v else (0, 0)
    if rot and abs(int(rot[1] or rot[2])) % 180 == 90:
        w, h = h, w
    return {"duration": int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0,
            "audio": "Audio:" in err, "w": w, "h": h}


# ---------- nhận giọng nói ----------
_MODEL, _MODEL_LOCK = None, threading.Lock()


def _model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel

        _MODEL = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=6)
    return _MODEL


def unload():
    """Trả RAM (~1.3GB) khi xong việc."""
    global _MODEL
    with _MODEL_LOCK:
        _MODEL = None
    gc.collect()


def audio16k(path):
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.float32)


def transcribe(path, lang=None):
    """-> (ngôn ngữ, [{s, e, src}]). Bỏ đoạn chỉ có nhạc (whisper hay bịa chữ lặp lại ở đó)."""
    a = audio16k(path)
    if len(a) < 16000 * 0.5:
        return lang or "", []
    with _MODEL_LOCK:
        segs, info = _model().transcribe(a, language=lang, vad_filter=True, condition_on_previous_text=False,
                                         beam_size=5)
        segs = list(segs)
    out = []
    for s in segs:
        text = s.text.strip()
        if not text or s.no_speech_prob > 0.6 or s.compression_ratio > 2.4 or s.avg_logprob < -1.2:
            continue
        out.append({"s": round(s.start, 2), "e": round(s.end, 2), "src": text})
    return info.language, out


# ---------- dịch ----------
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def _gt(batch, sl, tl):
    q = [("client", "dict-chrome-ex"), ("sl", sl), ("tl", tl)] + [("q", t) for t in batch]
    url = "https://clients5.google.com/translate_a/t?" + urllib.parse.urlencode(q)
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30))
    out = [x[0] if isinstance(x, list) else x for x in d]
    if len(out) != len(batch):
        raise ValueError("số câu dịch không khớp")
    return out


def _mymemory(text, sl, tl):
    url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": text, "langpair": f"{sl}|{tl}"})
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30))
    if d.get("responseStatus") != 200:
        raise ValueError(d.get("responseDetails") or "MyMemory lỗi")
    return d["responseData"]["translatedText"]


def translate(texts, lang, tl="vi"):
    if not texts:
        return []
    sl = LANG_GT.get(lang, lang or "auto")
    if sl == tl:
        return list(texts)
    out, batch, size = [], [], 0
    batches = []
    for t in texts:
        n = len(urllib.parse.quote(t))
        if batch and size + n > 1600:
            batches.append(batch)
            batch, size = [], 0
        batch.append(t)
        size += n + 3
    batches.append(batch)
    for b in batches:
        for i in range(3):
            try:
                out += _gt(b, sl, tl)
                break
            except Exception:
                time.sleep(2 + 3 * i)
        else:
            try:
                out += [_mymemory(t, "zh-CN" if sl == "auto" else sl, tl) for t in b]
            except Exception as e:
                raise DubError(f"Dịch không được (Google Dịch và MyMemory đều lỗi): {e}") from e
    return [re.sub(r"\s+", " ", t).strip() for t in out]


# ---------- dựng ----------
def size_for(dims):
    """Hướng khung theo đa số clip: dọc 1080x1920 hoặc ngang 1920x1080."""
    tall = sum(1 for w, h in dims if h > w)
    return (1080, 1920) if tall * 2 >= len(dims) else (1920, 1080)


def normalize(src, out, W, H, has_audio=True):
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
          "setsar=1,fps=30,format=yuv420p")
    args = [FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(src)]
    if not has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    args += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a", "-vf", vf, "-c:v", "libx264", "-preset",
             "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", "-shortest",
             str(out)]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError("ffmpeg: " + p.stderr.strip()[-400:])
    return out


TTS_TIMEOUT = 40          # giây cho một lần đọc một câu


def _tts_all(items, voice, report=lambda done, n: None, stop=lambda: False):
    """items: [(text, out_mp3)] -> đọc song song (tối đa 4 câu một lúc), báo tiến độ sau mỗi câu."""
    import edge_tts
    done = 0

    async def one(sem, text, out):
        # dịch vụ thỉnh thoảng trả "No audio was received" cho câu bình thường, hoặc treo hẳn không trả lời:
        # mỗi lần chỉ chờ TTS_TIMEOUT giây rồi thử lại. Vẫn lỗi thì bỏ câu đó (phụ đề vẫn có).
        nonlocal done
        async with sem:
            for i in range(6):
                if stop():
                    break
                tmp = out.with_suffix(".part")
                try:
                    await asyncio.wait_for(edge_tts.Communicate(text, voice, rate="+10%").save(str(tmp)), TTS_TIMEOUT)
                    tmp.replace(out)
                    break
                except Exception:       # gồm cả hết giờ chờ
                    tmp.unlink(missing_ok=True)
                    await asyncio.sleep(1.5 + i)
        done += 1
        report(done, len(items))

    async def run():
        sem = asyncio.Semaphore(4)
        await asyncio.gather(*(one(sem, t, o) for t, o in items))
    asyncio.run(run())
    if stop():
        raise InterruptedError


def _pcm(mp3, speed=1.0):
    af = ["-af", f"atempo={speed:.3f}"] if speed > 1.01 else []
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(mp3), *af, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.float32)


def place(lines, total):
    """lines: [(s, slot_end, pcm)] theo thời gian -> [(start, speed)]: mỗi câu bắt đầu đúng lúc nhân vật nói,
    dài quá chỗ trống thì đọc nhanh hơn (tối đa MAX_SPEED), không bao giờ đè lên câu trước."""
    out, cursor = [], 0.0
    for s, slot_end, pcm in lines:
        d = len(pcm) / SR
        start = max(s, cursor)
        avail = min(slot_end, total) - start
        speed = min(MAX_SPEED, max(1.0, d / avail)) if avail > 0.05 else MAX_SPEED
        out.append((start, speed))
        cursor = start + d / speed + 0.05
    return out


def dub_track(segs, total, voice, out_wav, cache, report=lambda done, n: None, stop=lambda: False):
    """segs: [{s, e, tr}] theo thời gian của cả phim -> file WAV chỉ có giọng lồng.
    Câu đã đọc lưu ở cache (theo giọng + chữ): dựng lại chỉ đọc câu mới sửa hoặc lần trước lỗi."""
    cache.mkdir(exist_ok=True)
    key = lambda t: hashlib.md5(f"{voice}|{t}".encode()).hexdigest()[:16]
    todo = [(g, cache / f"{key(g['tr'])}.mp3") for g in segs if g.get("tr")]
    _tts_all(list({o: (g["tr"], o) for g, o in todo if not o.exists()}.values()), voice, report, stop)
    todo = [(g, o) for g, o in todo if o.exists() and o.stat().st_size]
    lines, kept = [], [g for g, _ in todo]
    for i, (g, mp3) in enumerate(todo):
        nxt = kept[i + 1]["s"] if i + 1 < len(kept) else total
        lines.append((g["s"], max(nxt, g["e"]), _pcm(mp3)))
    buf = np.zeros(int(total * SR) + SR, np.float32)
    for (start, speed), (_, _, pcm), (_, mp3) in zip(place(lines, total), lines, todo):
        if speed > 1.01:
            pcm = _pcm(mp3, speed)
        i = int(start * SR)
        pcm = pcm[:max(0, len(buf) - i)]
        buf[i:i + len(pcm)] += pcm * 0.95
    buf = buf[:int(total * SR)]
    missed = sum(1 for g in segs if g.get("tr")) - len(todo)
    pcm16 = (np.clip(buf, -1, 1) * 32767).astype("<i2")
    import wave

    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return out_wav, missed


def _ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def ass(segs, out, W, H):
    tall = H > W
    size, margin = (62, 330) if tall else (56, 70)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Be Vietnam Pro ExtraBold,{size},&H00FFFFFF,&H00FFFFFF,&H90000000,&H90000000,0,0,0,0,100,100,0,0,3,10,0,2,{int(W * 0.08)},{int(W * 0.08)},{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for i, g in enumerate(segs):
        text = re.sub(r"[{}\\]", "", g.get("tr") or "").strip()
        if not text:
            continue
        end = g["e"] + 0.3
        if i + 1 < len(segs):
            end = min(end, segs[i + 1]["s"])
        rows.append(f"Dialogue: 0,{_ts(g['s'])},{_ts(max(end, g['s'] + 0.6))},Sub,,0,0,0,,{text}")
    out.write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    return out


def render(parts, segs, out, work, W, H, dub=True, subs=True, orig="duck", voice="female", tts_cache=None,
           report=lambda stage, frac: None, stop=lambda: False):
    """parts: các clip đã chuẩn hoá (cùng khung, cùng định dạng); segs: câu thoại theo thời gian cả phim.
    -> (thời lượng, số câu không đọc được)."""
    missed = 0
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    joined = work / "joined.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(joined)], check=True, capture_output=True)
    total = probe(joined)["duration"]
    args = [FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(joined)]
    fc = []
    has_dub = dub and any(g.get("tr") for g in segs)
    if has_dub:
        wav, missed = dub_track(segs, total, VOICES.get(voice, VOICES["female"]), work / "dub.wav",
                                tts_cache or work / "tts",
                                lambda done, n: report(f"Đang đọc lồng tiếng {done}/{n} câu", done / max(1, n)), stop)
        report("Đang xuất phim", 1.0)
        args += ["-i", str(wav)]
        fc.append("[1:a]aresample=44100,aformat=channel_layouts=stereo,asplit=2[sc][dv]")
        if orig == "mute":
            fc.append("[dv]apad[a]")
        else:       # tiếng gốc tự nhỏ lại khi có lời lồng, hết câu thì to lại (giữ nhạc nền, tiếng động)
            fc.append("[0:a][sc]sidechaincompress=threshold=0.01:ratio=20:attack=10:release=400[bg];"
                      "[bg][dv]amix=inputs=2:duration=first:normalize=0[a]")
    if subs and any(g.get("tr") for g in segs):
        sub = ass(segs, work / "subs.ass", W, H)
        fc.append(f"[0:v]subtitles=filename={sub.name}:fontsdir={FONTS}[v]")
    vmap = ["-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"] if subs and "[v]" in ";".join(fc) \
        else ["-map", "0:v", "-c:v", "copy"]
    amap = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"] if has_dub else ["-map", "0:a", "-c:a", "copy"]
    if fc:
        args += ["-filter_complex", ";".join(fc)]
    args += [*vmap, *amap, "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=work)
    if p.returncode:
        raise RuntimeError("ffmpeg: " + p.stderr.strip()[-500:])
    return total, missed


# ---------- xem nội dung trước khi ghép ----------
_CN_NUM = {c: i for i, c in enumerate("零一二三四五六七八九十")}
_EP = [r"第\s*([0-9零一二三四五六七八九十百]+)\s*[集话話部回]", r"(?:ep|episode|part|pt|tập|phần)\s*\.?\s*(\d{1,3})\b",
       r"\b(\d{1,3})\s*/\s*\d{1,3}\b", r"[（(\[【]\s*(\d{1,3})\s*[)）\]】]", r"#\s*(\d{1,3})\b"]


def _cn(s):
    if s.isdigit():
        return int(s)
    n, cur = 0, 0
    for ch in s:
        if ch == "十":
            n += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            n += (cur or 1) * 100
            cur = 0
        else:
            cur = _CN_NUM.get(ch, 0)
    return n + cur


def episode(title):
    """Số tập trong tiêu đề / mô tả: 第3集, 第十二集, EP 3, Part 3, Tập 3, 3/10, (3), #3."""
    for pat in _EP:
        m = re.search(pat, title or "", re.I)
        if m:
            n = _cn(m[1])
            if 0 < n < 1000:
                return n
    return None


def _norm_text(items):
    return re.sub(r"[\W_]+", "", "".join(g["src"] for g in items)).lower()


def review(clips, segs):
    """Xem nội dung các clip -> {key: {ep, flags, preview}} và thứ tự đề xuất.
    flags: "dup:<key>" trùng nội dung clip khác, "silent" không có thoại, "short" quá ngắn."""
    out, seen = {}, {}
    for c in clips:
        items = segs.get(c["key"], {}).get("items", [])
        flags = []
        text = _norm_text(items)
        if len(text) >= 12:
            for k, t in seen.items():           # cùng lời thoại (đăng lại / bản cắt khác) -> trùng
                a, b = (text, t) if len(text) <= len(t) else (t, text)
                if a in b or (len(a) > 30 and a[:30] in b and a[-30:] in b):
                    flags.append(f"dup:{k}")
                    break
            else:
                seen[c["key"]] = text
        if not items:
            flags.append("silent")
        if c.get("duration") and c["duration"] < 3:
            flags.append("short")
        preview = " ".join(g.get("tr") or g["src"] for g in items[:3])[:200]
        out[c["key"]] = {"ep": episode(c.get("title")), "flags": flags, "preview": preview,
                         "lines": len(items)}
    with_ep = [c for c in clips if out[c["key"]]["ep"]]
    if len(with_ep) * 2 >= len(clips):      # đa số có số tập: xếp theo tập, không có số thì theo ngày đăng
        order = sorted(clips, key=lambda c: (out[c["key"]]["ep"] or 10 ** 6, c.get("ts") or 0))
    else:
        order = sorted(clips, key=lambda c: c.get("ts") or 0) if any(c.get("ts") for c in clips) else list(clips)
    return out, [c["key"] for c in order]


def thumb(src, out, at=1.0):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1",
                    "-vf", "scale=320:-2", "-q:v", "4", str(out)], capture_output=True)
    return out if out.exists() else None
