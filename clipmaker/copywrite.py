"""Tiêu đề + mô tả YouTube "câu view", không cần AI.

Lời dẫn lấy từ chính video: câu thoại ngay trước đỉnh gây cười (phụ đề YouTube) và bình luận nhiều like nhất
ở mốc đó. Ghép vào các mẫu câu có sẵn, chọn ngẫu nhiên nên mỗi lần bấm "Viết lại" ra một bộ khác.
"""
import random
import re

from . import youtube as yt
from .moments import LAUGH

FILLER = re.compile(r"\b(funny|funniest|moments?|compilation|best|top|videos?|clips?|of|the|most|hilarious|lol)\b", re.I)
JUNK = re.compile(r"\[[^\]]*\]|\([^)]*\)|>>")
EMPTY_LINE = re.compile(r"^(uh+|um+|oh+|yeah|okay|so|and|but|i|you)$", re.I)


def topic(keyword):
    """'celebrity funny moments' -> 'Celebrity' (bỏ chữ thừa để tiêu đề không lặp 'Funny Moments Funny Moments')."""
    t = re.sub(r"\s+", " ", FILLER.sub(" ", keyword)).strip()
    return t.title() if t else ""


def _short(text, n):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:-") + "…"


def clean_comment(text):
    """Bỏ mốc giờ, @tên, link; giữ emoji. Trả về '' nếu không còn gì đáng trích."""
    text = yt.TS.sub("", text or "")
    text = re.sub(r"https?://\S+|@\S+", "", text)
    text = re.sub(r"^[\s\-–—:,.!]+", "", re.sub(r"\s+", " ", text)).strip()
    return _short(text, 90) if len(re.sub(r"[^\w]", "", text)) >= 6 else ""


def spoken_line(words, clip):
    """Câu thoại hay nhất trong đoạn: kết thúc ngay trước/đúng lúc đỉnh gây cười, đủ 3+ chữ.

    Phụ đề tự động không có dấu câu nên một cụm dài thường là câu cụt giữa chừng: chỉ nhận cụm có dấu câu
    hoặc ngắn (≤ 7 chữ, tức là người nói ngừng lại) — không có thì trả về '' để lời dẫn dùng bình luận.
    """
    peak = clip.get("peak", (clip["start"] + clip["end"]) / 2) - clip["start"]
    best, best_score = "", -1e9
    for t0, t1, text in yt.chunks(words, clip["start"], clip["end"], max_words=9, max_len=3.5):
        text = JUNK.sub("", text).strip(" -")
        n = len(text.split())
        if n < 3 or all(EMPTY_LINE.match(w) for w in text.split()) or (n > 7 and text[-1:] not in ".?!"):
            continue
        score = -abs(t1 - peak) - (0.5 if t1 > peak + 1 else 0) + (1.0 if text[-1:] in "?!" else 0) + min(n, 7) * 0.1
        if score > best_score:
            best, best_score = text, score
    if not best:
        return ""
    best = _short(best, 70)
    return best[0].upper() + best[1:]


# {T} chủ đề · {N} số đoạn · {Q} câu thoại/bình luận ngắn · {V} số video gốc
TITLES = [
    "{N} {T} Moments That Had Everyone Crying Laughing 😂",
    "Try Not To Laugh: {T} Edition (#1 Is Unreal)",
    "{T} Moments You Can't Unsee 😭 | Top {N}",
    "Nobody Was Ready For #1 💀 | Top {N} {T} Moments",
    "The Internet Can't Stop Replaying These {T} Moments",
    "{T} Moments That Went Completely Off Script",
    "{N} Times {T} Made The Whole Room Lose It",
]
QUOTE_TITLES = [
    "\"{Q}\" 💀 | Top {N} {T} Moments",
    "\"{Q}\" — {T} Moments That Broke Everyone",
    "When They Said \"{Q}\"… 😭 | {T} Funny Moments",
]
OPENERS = [
    "It starts like any normal clip… then someone says “{L}” and nobody in the room can hold it together 😂",
    "“{L}” — that's the exact second it all falls apart 💀",
    "One sentence. “{L}” That's all it took to ruin the whole thing 😭",
    "You can see it coming the moment they say “{L}”… and it's still funnier than you expect.",
]
COMMENT_LINES = [
    "And the comments agree: “{C}”",
    "One viewer said it best: “{C}”",
    "Top comment on that moment: “{C}” — yeah, same.",
]
TEASERS = [
    "But wait until #1 at {ts}. People rewound it so many times it's the most replayed part of the video.",
    "Stick around for #1 at {ts} — it's the part everyone keeps replaying.",
    "And #1 ({ts})? Let's just say the original video's replay graph spikes right there.",
]
COMMENT_OPENERS = [
    "“{C}” — that's how viewers reacted to #{n}, and honestly they're right 😂",
    "Someone watched #{n} and wrote “{C}”. After seeing it, we get it 💀",
    "“{C}” — the comments under #{n} say it all.",
]
SAME_TEASERS = [
    "It's #1 for a reason — jump to {ts} if you can't wait (but the rest are worth it).",
    "That's #1 at {ts}, and the original video's replay graph spikes right there.",
]
FALLBACK_OPENERS = [
    "We dug through the most replayed parts of {V} videos so you only get the moments people can't stop rewatching 😂",
    "Every clip here is a spot viewers literally rewound over and over. Try to make it to #1 without laughing.",
]
CTAS = [
    "👇 Which one got you? Comment the number!",
    "👇 Be honest — which number made you laugh first?",
    "👇 Drop the number that broke you, and tell me which moment I missed.",
]


def _comment_score(x, top_num):
    c = x["comment"]
    return ((2 if LAUGH.search(c) else 0) + (1 if 12 <= len(c) <= 70 else 0) - (3 if c.endswith("…") else 0)
            + (1 if x["num"] == top_num else 0))


def _fill(tpl, **kw):
    return re.sub(r"\s+", " ", tpl.format(**kw)).strip()


def make(keyword, items, video_count, chapters_text, sources, commentary=False, seed=None):
    """items: các đoạn theo thứ tự xuất hiện, mỗi phần tử {num, ts, line, comment}. Trả về tiêu đề + mô tả."""
    rnd = random.Random(seed)
    T = topic(keyword) or "Funny"
    N = len(items)
    top = min(items, key=lambda x: x["num"]) if items else {}
    # câu thoại chỉ dùng khi là câu hỏi / cảm thán trọn vẹn; còn lại bình luận người xem hấp dẫn hơn
    lines = [x for x in items if x.get("line", "")[-1:] in ("?", "!")]
    comments = sorted((x for x in items if x.get("comment")), key=lambda x: -_comment_score(x, top.get("num")))
    comments = [x for x in comments if _comment_score(x, top.get("num")) >= 0]
    hero = top if top in lines else (rnd.choice(lines) if lines else None)
    quote = ""
    for cand in [hero["line"] if hero else "", *(x["comment"] for x in comments)]:
        q = re.sub(r"[^\w\s'’!?,.-]", "", cand).strip(" ,.-")
        if 2 <= len(q.split()) <= 7 and len(q) <= 42:
            quote = q
            break

    pool = [_fill(t, T=T, N=N) for t in TITLES]
    if video_count >= 8:
        pool.append(f"I Watched {video_count} Videos So You Don't Have To | {T} Funny Moments")
    rnd.shuffle(pool)
    if quote:
        pool = [_fill(rnd.choice(QUOTE_TITLES), Q=quote, T=T, N=N)] + pool
    titles = []
    for t in pool:
        t = re.sub(r"\b(\w+) Moments Moments\b", r"\1 Moments", t)[:100]
        if t not in titles:
            titles.append(t)
    titles = titles[:5]

    hook, about_top = [], False
    # bình luận hay nhất, thỉnh thoảng đổi sang cái tốt thứ hai để "Viết lại" có lời dẫn khác
    quoted = (comments[1] if len(comments) > 1 and rnd.random() < 0.4 else comments[0]) if comments else None
    if hero:
        hook.append(_fill(rnd.choice(OPENERS), L=hero["line"].rstrip(".")))
        about_top = hero is top
    elif quoted:
        about_top = quoted is top
        hook.append(_fill(rnd.choice(COMMENT_OPENERS), C=quoted["comment"], n=quoted["num"]))
        quoted = None
    else:
        hook.append(_fill(rnd.choice(FALLBACK_OPENERS), V=video_count))
    if quoted:
        hook.append(_fill(rnd.choice(COMMENT_LINES), C=quoted["comment"]))
    if top.get("ts"):
        hook.append(_fill(rnd.choice(SAME_TEASERS if about_top else TEASERS), ts=top["ts"]))
    hook_text = "\n".join(hook)

    tag = re.sub(r"[^\w]", "", T.lower())
    tags = [f"#{tag}" if tag and tag != "funny" else "", "#funnymoments", "#trynottolaugh", "#compilation"]
    desc = (hook_text
            + f"\n\n{N} moments the internet keeps replaying, picked from the “Most replayed” parts of {video_count} videos"
            + (" — with my commentary after the best ones." if commentary else ".")
            + "\n\n" + rnd.choice(CTAS)
            + "\n\n⏱ Timestamps:\n" + chapters_text
            + "\n\n🎬 Original videos (all credit to the creators):\n" + sources
            + "\n\n" + " ".join(t for t in tags if t))
    return {"title": titles[0], "titles": titles, "hook": hook_text, "description": desc}
