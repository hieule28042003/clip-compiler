"""Tìm đoạn hay trong một buổi stream dài (vài giờ) để cắt Shorts, không cần AI:

- Chat phát lại (live chat replay): chỗ chat dồn dập hơn hẳn mức bình thường, nhất là tin nhắn cười / hét
  ("KEKW", "=))", "vãi", "😂"…). Người xem gõ chậm hơn khoảnh khắc vài giây nên dời lùi lại.
- Độ to âm thanh: streamer hét, cười to, nhạc nổ… to hơn hẳn mức nền của đoạn xung quanh.
- "Most replayed" + bình luận có mốc giờ (nếu video có).
Mỗi tín hiệu được so với mức nền ±2,5 phút quanh nó (stream có lúc ồn lúc yên cả tiếng đồng hồ), cộng lại
theo trọng số, chọn các đỉnh cách nhau đủ xa rồi cắt đoạn quanh đỉnh, đầu / cuối đoạn rơi vào chỗ yên lặng.
"""
import json
import math
import re
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np
import yt_dlp

from . import copywrite
from . import youtube as yt

CHAT_LAG = 6.0               # s: chat phản ứng chậm hơn khoảnh khắc
SKIP_HEAD = 180              # bỏ 3 phút đầu: màn chờ, chào hỏi
BASE_WIN = 301               # s: cửa sổ tính mức nền
PEAK_AT = 0.6                # đỉnh nằm ở 60% đoạn: đủ phần dẫn trước + phản ứng sau
SNAP = 4                     # s: dời đầu / cuối đoạn tối đa chừng này để rơi vào chỗ yên lặng
WEIGHTS = {"chat": 2.0, "heat": 1.5, "loud": 1.0, "talk": 1.2}
AUDIO_SR = 8000
HYPE = re.compile(r"😂|🤣|💀|😭|😱|🔥|=\)\)+|:\)\)+|\b(k{3,}|h[ae]h[ae]\w*|hihi\w*|lo+l|lmf?ao+|kekw|lul|omegalul|"
                  r"pog\w*|w+t+f|omg|clip\s*(it|that|lại)?|vãi|vl|vcl|đỉnh|ghê|trời ơi|ối|ủa|gg|ez|no+o+|ya+y)\b",
                  re.I)
# biến thể viết lách của từ miệt thị chủng tộc hay gặp trong chat (bộ lọc chung chỉ bắt cách viết chuẩn)
SLUR_CHAT = re.compile(r"\bn+[i1e]+g+[aeu]*[hrz]?s?\b|\bnga\b|\bn[i1]g\b", re.I)
# chửi thề / tục tiếng Việt hay gặp trong chat: không đưa lên hook, thumbnail
VN_SWEAR = re.compile(r"(?<!\w)(đm|dm|đcm|dcm|đkm|vcl|vkl|vl|vcđ|đéo|déo|địt|đụ|lồn|l\*n|cặc|buồi|bìu|đĩ|"
                      r"chó đẻ|óc chó|ngu như)(?!\w)", re.I)
URL_ID = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([\w-]{11})|^([\w-]{11})$")


def video_id(url):
    """Mã video YouTube từ link (watch?v=, youtu.be, /live/…) hoặc chính mã 11 ký tự."""
    m = URL_ID.search((url or "").strip())
    if not m:
        raise ValueError("Link YouTube không hợp lệ (cần dạng youtube.com/watch?v=… hoặc youtu.be/…)")
    return m.group(1) or m.group(2)


def fetch_chat(vid, work):
    """Tải chat phát lại -> [(giây, chữ, trọng số)]; stream tắt chat phát lại thì []."""
    out = Path(work) / f"chat_{vid}"
    opts = {**yt._base_opts(), "skip_download": True, "writesubtitles": True, "subtitleslangs": ["live_chat"],
            "outtmpl": str(out), "noprogress": True}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={vid}"])
    path = Path(f"{out}.live_chat.json")
    return parse_chat(path) if path.exists() else []


def parse_chat(path):
    msgs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            act = d.get("replayChatItemAction") or {}
            t = int(act.get("videoOffsetTimeMsec") or 0) / 1000
            for a in act.get("actions") or []:
                item = (a.get("addChatItemAction") or {}).get("item") or {}
                r, w = item.get("liveChatTextMessageRenderer"), 1.0
                if not r:
                    r, w = item.get("liveChatPaidMessageRenderer"), 3.0         # Super Chat: nặng ký hơn
                if not r:
                    continue
                text = "".join(x.get("text") or _emoji(x.get("emoji") or {})
                               for x in (r.get("message") or {}).get("runs") or [])
                msgs.append((t, text.strip(), w + (1.0 if HYPE.search(text) else 0)))
    return msgs


def _emoji(e):
    """Emoji chuẩn giữ nguyên ký tự (😂), emoji riêng của kênh dùng tên tắt (:hype:)."""
    eid = e.get("emojiId") or ""
    return eid if len(eid) <= 8 else ((e.get("shortcuts") or [""])[0])


def fetch_loudness(vid, work, stop=lambda: False):
    """Tải riêng tiếng (bản nhẹ nhất, ~20 MB/giờ) rồi đo độ to từng giây (dB)."""
    out = Path(work) / f"audio_{vid}"
    opts = {**yt._base_opts(), "format": "249/250/worstaudio", "outtmpl": str(out) + ".%(ext)s",
            "noprogress": True, "retries": 5, "fragment_retries": 5}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={vid}"])
    got = next((p for p in Path(work).glob(f"audio_{vid}.*") if p.suffix != ".part"), None)
    if not got:
        raise RuntimeError("Không tải được tiếng của stream")
    try:
        return loudness(got, stop)
    finally:
        got.unlink(missing_ok=True)


def loudness(path, stop=lambda: False):
    """dB từng giây của file tiếng/video, đọc từng khúc nên stream vài giờ cũng chỉ tốn vài chục MB RAM."""
    proc = subprocess.Popen([yt.FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(AUDIO_SR),
                             "-f", "s16le", "-"], stdout=subprocess.PIPE)
    db, chunk = [], AUDIO_SR * 2 * 60                       # mỗi lần đọc 60 giây
    try:
        while True:
            if stop():
                raise InterruptedError
            buf = proc.stdout.read(chunk)
            if not buf:
                break
            x = np.frombuffer(buf[: len(buf) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
            n = len(x) // AUDIO_SR
            if n:
                rms = np.sqrt(np.mean(x[: n * AUDIO_SR].reshape(n, AUDIO_SR) ** 2, axis=1))
                db.extend(20 * np.log10(rms + 1e-5))
    finally:
        proc.kill()
        proc.wait()
    return np.array(db, dtype=np.float32)


def _baseline(x, win=BASE_WIN):
    """Mức nền: trung vị trong cửa sổ trượt (lấy mẫu mỗi 10 giây cho nhanh rồi nội suy)."""
    if len(x) < 3:
        return np.zeros_like(x)
    step, half = 10, win // 2
    idx = np.arange(0, len(x), step)
    med = [np.median(x[max(0, i - half): i + half + 1]) for i in idx]
    return np.interp(np.arange(len(x)), idx, med).astype(np.float32)


def _spike(x):
    """Phần vượt mức nền, chia cho mức vượt cao (phân vị 99,5) -> 0..~1."""
    s = np.clip(x - _baseline(x), 0, None)
    top = float(np.percentile(s, 99.5)) if s.any() else 0.0
    return np.clip(s / top, 0, 1.5) if top > 1e-6 else np.zeros_like(s)


def _chat_spike(rate):
    """Chat dồn dập: số tin trong 10 giây vượt mức nền bao nhiêu lần độ dao động tự nhiên (phân phối Poisson),
    nên stream ít chat vài tin trùng lúc không bị tính là bùng nổ; vượt 3 lần mới bắt đầu có điểm."""
    n10 = np.convolve(rate, np.ones(10), mode="same")
    base = _baseline(n10)
    z = (n10 - base) / np.sqrt(base + 1)
    return np.clip((z - 3) / 15, 0, 1.5)


def _loud_spike(db):
    """To hơn mức nền xung quanh: 3 dB trở xuống coi như bình thường, +13 dB (hét, cười phá lên) = 1 điểm."""
    d = np.convolve(db, np.ones(3) / 3, mode="same")
    return np.clip((d - _baseline(d) - 3) / 10, 0, 1.5)


def curve(duration, chat=(), db=None, heatmap=(), marks=()):
    """Điểm từng giây + từng tín hiệu đã chuẩn hoá (để ghi lý do chọn đoạn)."""
    n = int(duration) + 1
    parts = {}
    if chat:
        rate = np.zeros(n, dtype=np.float32)
        for t, _, w in chat:
            k = int(t - CHAT_LAG)
            if 0 <= k < n:
                rate[k] += w
        parts["chat"] = _chat_spike(rate)
    if db is not None and len(db):
        d = np.full(n, float(np.median(db)), dtype=np.float32)
        d[: min(n, len(db))] = db[:n]
        parts["loud"] = _loud_spike(d)
    if heatmap:
        heat = np.zeros(n, dtype=np.float32)
        for a, b, v in heatmap:
            heat[int(a): int(math.ceil(b)) + 1] = v
        parts["heat"] = _spike(heat)
    if marks:
        talk = np.zeros(n, dtype=np.float32)
        for m in marks:
            t = m["t"] + 1.5
            if t < n:
                x = np.arange(max(0, int(t - 6)), min(n, int(t + 7)))
                talk[x] += (1 + math.log1p(m["likes"]) / 3) * np.exp(-((x - t) ** 2) / 12.5)
        parts["talk"] = np.clip(talk / max(3.0, float(talk.max())), 0, 1)
    score = np.zeros(n, dtype=np.float32)
    for k, v in parts.items():
        if k != "heat":
            score += WEIGHTS[k] * v[:n]
    if "heat" in parts:
        if duration / len(heatmap) > 20:     # stream dài: mỗi ô heatmap vài phút, chỉ khoanh vùng được
            score = score * (1 + WEIGHTS["heat"] * parts["heat"]) + 0.3 * parts["heat"]
        else:
            score += WEIGHTS["heat"] * parts["heat"]
    score[: min(n, SKIP_HEAD)] = 0
    score[max(0, n - 30):] = 0                       # màn kết thúc stream
    return score, parts


def _quiet(db, t, lo, hi):
    """Giây yên nhất trong [t+lo, t+hi] (cắt vào giữa câu nghe rất cụt)."""
    a, b = max(0, int(t + lo)), min(len(db), int(t + hi) + 1)
    if b - a < 2:
        return t
    return a + int(np.argmin(db[a:b]))


def _chat_line(chat, a, b):
    """Câu chat tiêu biểu trong đoạn: nhiều người cùng gõ, 2–8 chữ, không link / @tên."""
    cnt = Counter()
    for t, text, w in chat:
        if (a <= t - CHAT_LAG <= b and 2 <= len(text.split()) <= 8 and not re.search(r"https?://|@|:\w+:", text)
                and not copywrite.SWEAR.search(text) and not copywrite.SLUR.search(text)
                and not SLUR_CHAT.search(text) and not VN_SWEAR.search(text)):
            cnt[re.sub(r"\s+", " ", text.lower()).strip(" \\/|-_~")] += w
    return max(cnt, key=cnt.get) if cnt else ""


def highlights(duration, score, parts, db=None, chat=(), marks=(), length=30.0, n=10):
    """Các đoạn hay nhất [{start, end, peak, score, reasons, comment}], tốt nhất trước."""
    smooth = np.convolve(score, np.ones(5) / 5, mode="same")
    gap = max(60.0, length * 1.5)
    floor = max(0.3, float(np.percentile(smooth, 90)))
    picks = []
    for t in np.argsort(-smooth):
        if smooth[t] < floor or len(picks) >= n:
            break
        if all(abs(int(t) - p) >= gap for p in picks):
            picks.append(int(t))
    out = []
    for t in picks:
        start = max(0.0, t - length * PEAK_AT)
        end = min(duration, start + length)
        if db is not None and len(db):
            start = float(_quiet(db, start, -SNAP, SNAP / 2))
            end = float(min(duration, _quiet(db, start + length, -SNAP / 2, SNAP)))
        reasons = []
        if "chat" in parts and parts["chat"][t] > 0.05:
            near = [m for m in chat if t - 10 <= m[0] - CHAT_LAG <= t + 10]
            reasons.append(f"💬 Chat dồn dập: {len(near)} tin trong 20 giây")
        if "loud" in parts and parts["loud"][t] > 0.25:
            reasons.append("🔊 Tiếng to hơn hẳn xung quanh (hét / cười / nổ)")
        if "heat" in parts and parts["heat"][t] > 0.25:
            reasons.append("🔥 Người xem tua lại nhiều")
        near = [m for m in marks if abs(m["t"] - t) <= 10]
        if near:
            best = max(near, key=lambda m: m["likes"])
            reasons.append(f"🕒 {len(near)} bình luận nhắc mốc giờ: “{best['text'][:80]}”")
        out.append({"start": round(start, 1), "end": round(end, 1), "peak": t, "score": round(float(smooth[t]), 3),
                    "reasons": reasons, "comment": _chat_line(chat, start, end)})
    return out
