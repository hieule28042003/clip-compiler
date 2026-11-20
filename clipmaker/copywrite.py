"""Tiêu đề + mô tả + tag YouTube chuẩn SEO, theo xu hướng, không cần AI.

- Tiêu đề: tên người nổi tiếng + kiểu tiêu đề đang ăn khách tháng này + cụm từ người ta đang tìm (seo.py),
  chấm điểm SEO từng tiêu đề.
- Lời dẫn: câu thoại ngay trước đỉnh gây cười (phụ đề YouTube) / bình luận nhiều like nhất ở mốc đó.
Chọn ngẫu nhiên có trọng số nên mỗi lần bấm "Viết lại" ra một bộ khác.
"""
import math
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
# tiêu đề "giật tít": gây tò mò mạnh nhưng vẫn đúng nội dung (không hứa điều video không có)
# bình luận đầu tiên của kênh dưới video: câu hỏi dễ trả lời để người xem bình luận (tương tác thật)
ENGAGE = [
    "Which one made you laugh the hardest? Drop the number 👇😂",
    "Be honest: did #1 deserve the top spot? If not, which number should have won? 👇",
    "{ts} got me every single time 💀 What was your favorite moment? 👇",
    "Who should I make the next video about? The most liked suggestion gets picked 👇",
]
ENGAGE_PERSON = [
    "What's the funniest {X} moment that I missed? Tell me and it might make the next video 👇",
    "{X} at {ts} 💀 Which one was your favorite? Comment the number 👇",
]

BAIT_PERSON = [
    "{X} Did WHAT?! 😳 {N} Moments Nobody Expected",
    "Nobody Was Ready For What {X} Did Next 💀",
    "{Xs} Most UNHINGED Moments Caught On Camera",
    "The Moments {X} Went WAY Too Far 😂",
    "{X} Moments You Won't Believe Actually Happened",
    "{X} Was NOT Ready For This 😳",
    "I Can't Believe {X} ACTUALLY Did This 😭",
    "{X} Lost It… Then THIS Happened 💀",
    "Try Not To Laugh: {X} Edition (IMPOSSIBLE) 😂",
]
BAIT_TOPIC = [
    "{S} Moments That Went WAY Too Far 😂",
    "{S} Moments You Won't Believe Actually Happened",
    "Nobody Was Ready For These {S} Moments 💀",
    "These {S} Moments Should NOT Be This Funny 😭",
    "Try Not To Laugh: {S} Edition (IMPOSSIBLE) 😂",
]
# chữ trên thumbnail: 2-4 chữ, đọc được trong 1 giây ở kích thước nhỏ (không bịa sự việc video không có)
PUNCH_PERSON = ["{X} DID WHAT?!", "{X} WENT TOO FAR", "{X} LOST IT", "{X} WASN'T READY", "WAIT… WHAT?!",
                "NO WAY!!", "NOBODY WAS READY"]
PUNCH_TOPIC = ["WAIT… WHAT?!", "NOBODY WAS READY", "THIS IS REAL?!", "WAY TOO FAR", "NO WAY!!", "DON'T LAUGH"]

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
    # có tên người thì giật tít theo tên (người xem tìm theo tên), không thì theo chủ đề
    bait = [tpl.format(X=n, Xs=seo.possessive(n), N=N) for n in names[:1] for tpl in BAIT_PERSON] or \
        [tpl.format(S=S) for tpl in BAIT_TOPIC]
    for base in bait:
        core = [w for w in seo._words(phrase) if not FILLER.fullmatch(w)]   # "trump funny moments" -> trump
        has_kw = all(w in base.lower() for w in (core or seo._words(phrase)))
        add(base if has_kw else f"{base} | {P}", "bait:" + base)
    scored = []
    for text, key in cands:
        sc, checks = seo.score_title(text, phrase, names, hot)
        trend_bonus = next((x["score"] for x in pats if x["person"] == key), 0)
        scored.append((sc + min(trend_bonus, 30) / 30 + rnd.random() * 1.5, text, key, sc, checks))
    scored.sort(reverse=True)
    first = next((x for x in scored if x[2].startswith("bait:")), None)   # tiêu đề chính luôn là kiểu giật tít
    if first:
        scored.remove(first)
        scored.insert(0, first)
    out, used = [], set()
    for _, text, key, sc, checks in scored:            # mỗi kiểu tiêu đề một lần cho đa dạng
        kind = "bait" if key.startswith("bait:") else key
        if key in used or (kind == "bait" and any(k.startswith("bait:") for k in used)):
            continue
        used.add(key)
        out.append({"text": text, "score": sc, "checks": checks})
        if len(out) == 6:              # 1 tiêu đề giật tít + 5 kiểu theo xu hướng / trích câu
            break
    return phrase, out


# video tổng hợp các buổi livestream: kể theo thời gian, không xếp hạng
STREAM_TITLES = ["{C} Best Stream Moments 😂 ({N} Highlights)", "{C} Funniest Stream Moments You Missed 💀",
                 "{C} Stream Highlights: The Moments Chat Went Crazy 😂", "Best Of {C} Livestreams ({N} Moments) 🔥",
                 "{C} Lost It On Stream… Best Moments 😭"]
STREAM_PUNCH = ["BEST MOMENTS", "CHAT WENT CRAZY", "STREAM HIGHLIGHTS", "HE LOST IT", "NO WAY!!"]
STREAM_ENGAGE = ["Which moment was your favorite? Drop the timestamp 👇", "What did I miss from this stream? 👇",
                 "Which stream should I make highlights of next? 👇"]


def _stream_copy(rnd, keyword, items, video_count, chapters_text, sources, research, names, source_tags, channels):
    C = (channels or [keyword])[0]
    N = len(items)
    quote = ""
    for x in items:
        q = re.sub(r"[^\w\s'’!?,.-]", "", x.get("comment", "")).strip(" ,.-")
        if 2 <= len(q.split()) <= 5 and len(q) <= 30:
            quote = q
            break
    titles = []
    for tpl in rnd.sample(STREAM_TITLES, len(STREAM_TITLES)):
        text = tpl.format(C=C, N=N)
        sc, checks = seo.score_title(text, f"{C} stream highlights", names, [])
        titles.append({"text": text, "score": sc, "checks": checks})
    hook = (f"The best moments from {video_count} {C} streams, in the order they happened." if video_count > 1
            else f"The best moments from {C}'s stream, in the order they happened.")
    if quote:
        hook += f"\nChat when it happened: “{quote}”"
    desc = (f"{C} stream highlights 😂 — {N} moments where chat went crazy." + "\n\n" + hook
            + "\n\nPicked from the live chat replay, the loudest reactions and the most replayed parts of the stream."
            + "\n\n" + rnd.choice(STREAM_ENGAGE)
            + "\n\n⏱ Timestamps:\n" + chapters_text
            + "\n\n🎬 Full streams (all credit to the streamer):\n" + sources
            + "\n\n" + " ".join(dict.fromkeys([_hashtag(C), "#StreamHighlights", "#Livestream"])))
    phrase = f"{C} stream highlights".lower()
    tag_list = seo.tags(phrase, keyword, research.get("phrases", []), names, source_tags, channels)
    punch = f'"{quote}"' if quote and len(quote.split()) <= 4 and rnd.random() < 0.4 else rnd.choice(STREAM_PUNCH)
    return {"title": titles[0]["text"], "titles": titles, "thumb": punch.upper(), "hook": hook, "description": desc,
            "pin": rnd.choice(STREAM_ENGAGE), "tags": ", ".join(tag_list), "phrase": phrase,
            "searches": research.get("phrases", [])[:8], "hot": [], "names": list(names)}


def make(keyword, items, video_count, chapters_text, sources, commentary=False, seed=None,
         research=None, names=(), source_tags=(), channels=(), context="", stream=False):
    """items: các đoạn theo thứ tự xuất hiện, mỗi phần tử {num, ts, line, comment}.
    research: kết quả seo.research(); names: tên người nổi tiếng trong các đoạn.
    stream: video tổng hợp các buổi livestream (kể theo thời gian, không xếp hạng)."""
    rnd = random.Random(seed)
    if stream:
        return _stream_copy(rnd, keyword, items, video_count, chapters_text, sources, dict(research or {}),
                            list(names), source_tags, channels)
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
    X = names[0].split()[-1] if names else ""
    punch = rnd.choice(PUNCH_PERSON).format(X=X) if X else rnd.choice(PUNCH_TOPIC)
    if 2 <= len(quote.split()) <= 4 and rnd.random() < 0.4:    # câu nói thật trong video: tò mò mà vẫn đúng
        punch = f'"{quote.rstrip(".,")}"'
    engage = [t for t in ENGAGE + (ENGAGE_PERSON if X else []) if top.get("ts") or "{ts}" not in t]
    pin = rnd.choice(engage).format(X=names[0] if names else "", ts=top.get("ts", ""))
    return {"title": titles[0]["text"] if titles else P, "titles": titles, "thumb": punch.upper(), "hook": hook_text, "description": desc,
            "pin": pin,
            "tags": ", ".join(tag_list), "phrase": phrase, "searches": research.get("phrases", [])[:8],
            "hot": [re.sub(r"\{\w+\}", "…", x["person"]) for x in sorted(hot, key=lambda x: -x["score"])[:4]],
            "names": names}


# ---------- Shorts ----------
SHORT_HOOKS = ["Wait for it…", "Nobody was ready for this", "This should NOT be this funny", "Watch their faces",
               "I can't stop replaying this", "The way this ended", "Watch till the end", "They really said that",
               "This went WAY too far", "Did that really just happen?!", "The face at the end", "Don't blink"]
# tiêu đề Short: {H} là câu hook (câu nói thật / câu nhử chung)
SHORT_TITLES = ["{H} 😳", "{H}… wait for it 💀", "{H} 😭 (watch till the end)", "{H} 💀 Nobody was ready"]
SHORT_QUOTE_TITLES = ["They Really Said {H} 💀", "{H} 😳 Nobody Was Ready", "When They Said {H}… 😭"]
# Short đứng riêng (không có video dài): bình luận đầu tiên kéo người xem trả lời + theo dõi kênh
SHORT_ENGAGE = ["Rate this from 1 to 10 😂👇", "Who do you want to see next? 👇", "Did you see that coming? 👇",
                "Tag someone who needs to see this 😂"]


def short_hook(item, rnd, used=()):
    """Chữ to phía trên video dọc: câu thoại / bình luận ngắn của chính đoạn đó, không có thì câu nhử chung
    (không lặp câu đã dùng cho Short khác)."""
    # bình luận người xem trước: phụ đề tự động hay nghe nhầm chữ
    for cand in (item.get("comment", ""), item.get("line") if item.get("line", "")[-1:] in "?!" else ""):
        q = re.sub(r"[^\w\s'’!?,.-]", "", cand or "").strip(" ,.-")
        if 2 <= len(q.split()) <= 8 and len(q) <= 44:
            if f"\"{q}\"" not in used:
                return f"\"{q}\""
    return rnd.choice([h for h in SHORT_HOOKS if h not in used] or SHORT_HOOKS)


def shorts(items, main_title, names=(), seed=None):
    """items: [{num, hook, title, channel, vid}] -> tiêu đề + mô tả cho từng Short.
    main_title: tiêu đề video dài (kéo người xem về đó); None = Short đứng riêng (kêu gọi theo dõi kênh)."""
    rnd = random.Random(seed)
    out = []
    for x in items:
        tag = names[0] if names else ""
        if x.get("reply"):
            h = f"“{_short(x['comment'], 60)}”"
            title = rnd.choice(REPLY_TITLES).format(H=h)
            desc = (f"Replying to {x.get('author') or 'a viewer'}'s comment: “{x['comment']}”\n"
                    f"{x['reply']}\n🔔 Follow for more! Drop your funniest comment and I might reply with a video 👇\n\n"
                    f"🎬 Original: {x['title']} — {x['channel']} https://youtu.be/{x['vid']}\n\n"
                    + " ".join(dict.fromkeys(["#shorts", "#funny", "#comments", *(_hashtag(n) for n in names[:1])])))
            out.append({"title": title[:95] + " #shorts", "description": desc,
                        "pin": "Comment something funny and I might reply with a video 👇😂"})
            continue
        quoted = x["hook"].startswith('"')
        h = x["hook"] if quoted else x["hook"].rstrip("…")
        pool = [t for t in (SHORT_QUOTE_TITLES if quoted else SHORT_TITLES)       # tránh "Wait for it… wait for it"
                if not any(k in t.lower() and k in h.lower() for k in ("wait for it", "nobody was ready", "till the end"))]
        title = rnd.choice(pool or SHORT_TITLES[:1]).format(H=h)
        title += f" | {tag}" if tag and tag.lower() not in x["hook"].lower() else ""
        head = (f"#{x['num']} from my full video: {main_title}\n👉 Full compilation on my channel!" if main_title
                else f"{x['hook'].strip(chr(34))} 😂\n🔔 Follow for a new funny moment every day!")
        desc = (head + "\n\n"
                f"🎬 Original: {x['title']} — {x['channel']} https://youtu.be/{x['vid']}\n\n"
                + " ".join(dict.fromkeys(["#shorts", "#funny", "#funnymoments", *(_hashtag(n) for n in names[:1])])))
        out.append({"title": title[:95] + " #shorts", "description": desc, "pin": rnd.choice(SHORT_ENGAGE)})
    return out


# ---------- Short trả lời bình luận ----------
# câu đáp của kênh (hiện chữ + đọc giọng), chọn theo kiểu bình luận
REPLY_ASK = ["You asked, so here it is 😂", "Glad you asked 💀", "Say less. Here it is 👀"]
REPLY_MOMENT = ["Here's the exact moment 💀", "You weren't lying 😂", "Okay, let's watch it again 👀",
                "This part right here 😭"]
REPLY_LAUGH = ["Same. I can't breathe 😭", "This one broke all of us 💀", "Crying with you 😂"]
REPLY_GENERIC = ["Couldn't agree more 😂", "This comment is the best part 💀", "Facts. Watch this 👀"]
REPLY_TITLES = ["Replying to the funniest comment 😂 {H}", "{H} 💀 You asked, here it is",
                "This comment was RIGHT 😭 {H}", "{H} 😂 Comment section won again"]


SWEAR = re.compile(r"\b(f+u+c+k\w*|sh+i+t\w*|bitch\w*|asshole\w*|pussy\w*|bastard\w*|"
                   r"motherf\w*|wtf|stfu)\b", re.I)
SLUR = re.compile(r"n[i1]gg|f[a@]gg?[o0]t|retard", re.I)            # bình luận miệt thị: không dùng


def _mask(m):
    w = m.group(0)
    return w if len(w) <= 2 else w[0] + "*" * (len(w) - 2) + w[-1]


def reply_text(comment):
    """Bình luận để hiện trong khung: bỏ mốc giờ ở đầu / link, che chữ chửi thề (YouTube hạn chế quảng cáo),
    gọn khoảng trắng, tối đa 150 ký tự."""
    text = SWEAR.sub(_mask, re.sub(r"https?://\S+", "", comment or ""))
    text = re.sub(r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s*[-–—:,.]*\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _short(text, 150)


def reply_score(comment, likes):
    """Độ "hài, lầy" của bình luận để chọn làm Short trả lời: nhiều like, có biểu tượng cười, đủ ý (không chỉ là mốc giờ)."""
    text = reply_text(comment)
    words = len(re.findall(r"[A-Za-z]{2,}", text))
    if words < 3 or SLUR.search(comment or ""):
        return -1.0
    return (math.log1p(likes) + (2.0 if LAUGH.search(comment or "") else 0) + (1.0 if 4 <= words <= 25 else 0)
            + (0.5 if "?" in text else 0))


def reply_line(comment, rnd, used=()):
    text = comment or ""
    pool = (REPLY_ASK if "?" in text else REPLY_LAUGH if LAUGH.search(text) else
            REPLY_MOMENT if re.search(r"\d:\d\d|\bthe way\b|\bwhen\b|\bpart\b", text, re.I) else REPLY_GENERIC)
    return rnd.choice([x for x in pool if x not in used] or pool)
