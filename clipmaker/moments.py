"""Tìm khoảnh khắc gây cười trong một video, không cần AI:

- "Most replayed" của YouTube: chỗ người xem tua lại nhiều nhất (đỉnh nổi lên trên mức nền của video).
- Bình luận có mốc giờ ("12:50 got me in tears 😂"): càng nhiều like / biểu tượng cười càng nặng ký.
- Tên chương có chữ funny / fail / laugh…
Điểm cộng dồn trên trục thời gian 1 giây, chọn các đỉnh cách nhau đủ xa rồi cắt đoạn ngắn quanh đỉnh.
"""
import math
import re

import numpy as np

LAUGH = re.compile(r"😂|🤣|💀|😭|\blo+l\b|\blmf?ao+\b|\bha(ha)+\b|\bdead\b|\bdying\b|\bcrying\b|\bin tears\b|"
                   r"\bhilarious\b|\bfunniest\b|\bfunny\b|\blaugh|\bwheeze|\bkill(s|ed|ing)? me\b|\bomg\b", re.I)
FUNNY_CHAPTER = re.compile(r"funny|fail|laugh|lol|prank|awkward|blooper|roast|savage|epic|best|moment", re.I)
COMMENT_LEAD = 1.5          # s: người xem thường ghi mốc ngay trước lúc buồn cười
SKIP_START = 0.04           # bỏ phần đầu video: heatmap luôn cao do ai cũng xem đoạn mở đầu


def score_curve(info):
    """Điểm hấp dẫn từng giây của video + lý do theo từng giây."""
    dur = int(info["duration"]) or 1
    s = np.zeros(dur + 1, dtype=np.float32)
    heat = np.zeros(dur + 1, dtype=np.float32)
    for a, b, v in info.get("heatmap") or []:
        heat[int(a): int(math.ceil(b)) + 1] = v
    if heat.any():
        base = float(np.median(heat))
        s += np.clip(heat - base, 0, None) / max(1e-6, float(heat.max()) - base) * 2.0
    talk = np.zeros(dur + 1, dtype=np.float32)
    for m in info.get("marks") or []:
        t = m["t"] + COMMENT_LEAD
        if t >= dur:
            continue
        w = 1.0 + math.log1p(m["likes"]) / 3 + (1.5 if LAUGH.search(m["text"]) else 0)
        lo, hi = max(0, int(t - 6)), min(dur, int(t + 6))
        x = np.arange(lo, hi + 1)
        talk[lo:hi + 1] += w * np.exp(-((x - t) ** 2) / (2 * 2.5 ** 2))
    if talk.any():
        s += talk / max(3.0, float(talk.max())) * 1.6
    for c in info.get("chapters") or []:
        if FUNNY_CHAPTER.search(c["title"]):
            a = int(c["start"])
            s[a: min(dur, a + 20)] += 0.4
    s[: max(10, int(dur * SKIP_START)) + 3] = 0
    return s, heat, talk


def _plateau_center(s, t):
    lo = hi = t
    while lo > 0 and s[lo - 1] >= s[t] - 1e-6:
        lo -= 1
    while hi < len(s) - 1 and s[hi + 1] >= s[t] - 1e-6:
        hi += 1
    return (lo + hi) // 2


def find(info, length=10.0, per_video=3, min_gap=25):
    """Các đoạn ứng viên [{start, end, score, reasons}] của một video, tốt nhất trước."""
    dur = info["duration"]
    if dur < length + 5:
        return []
    s, heat, talk = score_curve(info)
    smooth = np.convolve(s, np.ones(3) / 3, mode="same")
    order = np.argsort(-smooth)
    picks = []
    for t in order:
        if smooth[t] < 0.35 or len(picks) >= per_video:
            break
        t = _plateau_center(smooth, int(t))     # heatmap chia thành từng ô vài giây: lấy giữa ô
        if any(abs(t - p) < min_gap for p in picks):
            continue
        picks.append(t)
    out = []
    for t in picks:
        lead = 0.4 if talk[t] > heat[t] else 0.55      # mốc bình luận ~ đầu khoảnh khắc; đỉnh heatmap ~ giữa
        start = max(0.0, min(dur - length, t - length * lead))
        end = start + length
        reasons = []
        if heat[t] > 0:
            reasons.append(f"🔥 Xem lại nhiều ({round(float(heat[t]) * 100)}%)")
        near = [m for m in info.get("marks") or [] if abs(m["t"] + COMMENT_LEAD - t) <= 6]
        if near:
            best = max(near, key=lambda m: m["likes"])
            reasons.append(f"💬 {len(near)} bình luận: “{best['text'][:90]}”")
        for c in info.get("chapters") or []:
            if c["start"] <= t < c["end"] and FUNNY_CHAPTER.search(c["title"]):
                reasons.append(f"📑 Chương: {c['title'][:60]}")
        out.append({"start": round(start, 1), "end": round(end, 1), "peak": t,
                    "score": round(float(smooth[t]), 3), "reasons": reasons,
                    "comment": max(near, key=lambda m: m["likes"])["text"] if near else ""})
    return out
