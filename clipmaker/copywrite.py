"""Tiêu đề + mô tả + tag YouTube chuẩn SEO, theo xu hướng, không cần AI.

- Tiêu đề: tên người nổi tiếng + kiểu tiêu đề đang ăn khách tháng này + cụm từ người ta đang tìm (seo.py),
  chấm điểm SEO từng tiêu đề.
- Lời dẫn: câu thoại ngay trước đỉnh gây cười (phụ đề YouTube) / bình luận nhiều like nhất ở mốc đó.
Chọn ngẫu nhiên có trọng số nên mỗi lần bấm "Viết lại" ra một bộ khác.
"""
import random
import re

from . import seo
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


# tiêu đề trích câu nói: tăng tò mò (CTR), vẫn kèm cụm từ khoá {P}
QUOTE_TITLES = [
    "\"{Q}\" 💀 | {P}",
    "When They Said \"{Q}\"… 😭 | {P}",
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


def _join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _hashtag(text):
    return "#" + "".join(w[:1].upper() + w[1:] for w in re.findall(r"\w+", text))


def _titles(rnd, keyword, research, names, N, quote):
    phrase = seo.main_phrase(keyword, research.get("phrases", []))
    P = seo.title_case(phrase)
    S = topic(phrase) or P              # chủ thể khi không có tên người: "Celebrity" (tránh "Moments Moments")
    trend = research.get("trend", [])
    pats = seo.trend_scores(trend)
    hot = [x["person"] for x in pats if x["hits"]] + [x["topic"] for x in pats if x["hits"] and x["topic"]]
    year = research.get("year")
    use_year = year and sum(str(year) in t for _, t in trend) >= 2
    cands = []                      # (tiêu đề, mẫu gốc)

    def add(text, key):
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= 100:
            cands.append((text, key))

    for x in pats:
        forms = [(n, x["person"]) for n in names[:2]] + ([(S, x["topic"])] if x["topic"] else [])
        for subj, tpl in forms:
            base = tpl.format(X=subj, Xs=seo.possessive(subj), N=N)
            if all(w in base.lower() for w in seo._words(phrase)):
                add(base, x["person"])
            else:
                add(f"{base} | {P}", x["person"])
                add(base, x["person"])
            if use_year:
                add(f"{base} ({year})", x["person"])
    if quote:
        add(rnd.choice(QUOTE_TITLES).format(Q=quote, P=P), "quote")
    scored = []
    for text, key in cands:
        sc, checks = seo.score_title(text, phrase, names, hot)
        trend_bonus = next((x["score"] for x in pats if x["person"] == key), 0)
        scored.append((sc + min(trend_bonus, 30) / 30 + rnd.random() * 1.5, text, key, sc, checks))
    scored.sort(reverse=True)
    out, used = [], set()
    for _, text, key, sc, checks in scored:            # mỗi kiểu tiêu đề một lần cho đa dạng
        if key in used:
            continue
        used.add(key)
        out.append({"text": text, "score": sc, "checks": checks})
        if len(out) == 5:
            break
    return phrase, out


def make(keyword, items, video_count, chapters_text, sources, commentary=False, seed=None,
         research=None, names=(), source_tags=(), channels=(), context=""):
    """items: các đoạn theo thứ tự xuất hiện, mỗi phần tử {num, ts, line, comment}.
    research: kết quả seo.research(); names: tên người nổi tiếng trong các đoạn."""
    rnd = random.Random(seed)
    research = dict(research or {})
    names = list(names)
    if context:            # chỉ dùng các gợi ý tìm kiếm mà video thật sự khớp
        research["phrases"] = seo.relevant(keyword, research.get("phrases", []), context)
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
        if 2 <= len(q.split()) <= 7 and len(q) <= 36:
            quote = q
            break
    phrase, titles = _titles(rnd, keyword, research, names, N, quote)
    P = seo.title_case(phrase)

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

    # 2 dòng đầu mô tả hiện ngay trong kết quả tìm kiếm: phải có cụm từ khoá + tên người
    intro = f"{P} 😂" + (f" featuring {_join(names[:3])}" if names else "") + \
        f" — {N} moments viewers replayed the most."
    related = [x for x in research.get("phrases", []) if x != phrase][:3]
    hashtags = [_hashtag(phrase), *(_hashtag(n) for n in names[:1]), "#FunnyMoments"]
    desc = (intro + "\n\n" + hook_text
            + f"\n\n{N} moments the internet keeps replaying, picked from the “Most replayed” parts of {video_count} videos"
            + (" — with my commentary after the best ones." if commentary else ".")
            + (f" If you were searching for {_join(related)}, you're in the right place." if related else "")
            + "\n\n" + rnd.choice(CTAS)
            + "\n\n⏱ Timestamps:\n" + chapters_text
            + "\n\n🎬 Original videos (all credit to the creators):\n" + sources
            + "\n\n" + " ".join(dict.fromkeys(hashtags)))
    tag_list = seo.tags(phrase, keyword, research.get("phrases", []), names, source_tags, channels)
    hot = [x for x in seo.trend_scores(research.get("trend", [])) if x["hits"]]
    return {"title": titles[0]["text"] if titles else P, "titles": titles, "hook": hook_text, "description": desc,
            "tags": ", ".join(tag_list), "phrase": phrase, "searches": research.get("phrases", [])[:8],
            "hot": [re.sub(r"\{\w+\}", "…", x["person"]) for x in sorted(hot, key=lambda x: -x["score"])[:4]],
            "names": names}


# ---------- Shorts ----------
SHORT_HOOKS = ["Wait for it", "Nobody was ready for this", "This timing is unreal", "Watch their faces",
               "I can't stop replaying this", "The way this ended"]


def short_hook(item, rnd):
    """Chữ to phía trên video dọc: câu thoại / bình luận ngắn của chính đoạn đó, không có thì câu nhử chung."""
    # bình luận người xem trước: phụ đề tự động hay nghe nhầm chữ
    for cand in (item.get("comment", ""), item.get("line") if item.get("line", "")[-1:] in "?!" else ""):
        q = re.sub(r"[^\w\s'’!?,.-]", "", cand or "").strip(" ,.-")
        if 2 <= len(q.split()) <= 8 and len(q) <= 44:
            return f"\"{q}\""
    return rnd.choice(SHORT_HOOKS)


def shorts(items, main_title, names=()):
    """items: [{num, hook, title, channel, vid}] -> tiêu đề + mô tả cho từng Short (kéo người xem về video dài)."""
    out = []
    for x in items:
        tag = names[0] if names else ""
        title = f"{x['hook'].strip(chr(34))} 😂" + (f" | {tag}" if tag and tag.lower() not in x["hook"].lower() else "")
        desc = (f"#{x['num']} from my full video: {main_title}\n"
                f"👉 Full compilation on my channel!\n\n"
                f"🎬 Original: {x['title']} — {x['channel']} https://youtu.be/{x['vid']}\n\n"
                + " ".join(dict.fromkeys(["#shorts", "#funny", "#funnymoments", *(_hashtag(n) for n in names[:1])])))
        out.append({"title": title[:95] + " #shorts", "description": desc})
    return out
