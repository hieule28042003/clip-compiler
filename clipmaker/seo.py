"""Nghiên cứu SEO + xu hướng YouTube, miễn phí, không cần AI hay API key.

- Gợi ý tìm kiếm của YouTube (ô search tự hoàn thành): người xem đang gõ gì, xếp theo độ phổ biến.
- Video nhiều lượt xem nhất THÁNG NÀY cho từ khoá: kiểu tiêu đề nào đang ăn khách ("Nobody Expected X…").
- Tên người nổi tiếng trong các video gốc + tag mà chủ kênh gốc đã gắn.
"""
import json
import math
import re
import time
import urllib.parse
import urllib.request
from collections import Counter

import yt_dlp

from . import youtube as yt

SUGGEST = "https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=en&q={}"
THIS_MONTH_MOST_VIEWED = "CAMSBAgEEAE%253D"      # bộ lọc của YouTube: tải lên tháng này, sắp theo lượt xem


def suggestions(query):
    try:
        with urllib.request.urlopen(SUGGEST.format(urllib.parse.quote(query)), timeout=8) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return [s for s in data[1] if isinstance(s, str)]
    except Exception:  # noqa: BLE001 - không có gợi ý thì vẫn viết được tiêu đề
        return []


def month_top(keyword, n=30):
    """Video tải lên THÁNG NÀY nhiều lượt xem nhất cho từ khoá: [{id, title, channel, views, duration}]."""
    url = (f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(keyword)}"
           f"&sp={THIS_MONTH_MOST_VIEWED}")
    try:
        with yt_dlp.YoutubeDL({**yt._base_opts(), "extract_flat": True, "playlistend": n}) as y:
            res = y.extract_info(url, download=False)
    except Exception:  # noqa: BLE001 - mạng lỗi: coi như không có dữ liệu
        return []
    return [{"id": e["id"], "title": e["title"], "channel": e.get("channel") or e.get("uploader") or "",
             "views": e.get("view_count") or 0, "duration": e.get("duration") or 0}
            for e in list(res.get("entries") or [])[:n] if e.get("id") and e.get("title")]


def trending_titles(keyword, n=30):
    """[(lượt xem, tiêu đề)] các video tháng này nhiều lượt xem nhất cho từ khoá."""
    return [(v["views"], v["title"]) for v in month_top(keyword, n)]


def research(keyword):
    phrases = []
    for q in (keyword, keyword + " "):
        for s in suggestions(q):
            if s not in phrases:
                phrases.append(s)
    return {"phrases": phrases[:15], "trend": trending_titles(keyword), "year": time.localtime().tm_year}


def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def main_phrase(keyword, phrases):
    """Cụm từ khoá chính: gợi ý phổ biến nhất có đủ các chữ của từ khoá (YouTube xếp gợi ý theo độ phổ biến)."""
    kw = set(_words(keyword))
    for s in phrases:
        w = set(_words(s))
        if kw <= w and len(w) <= len(kw) + 1:
            return s
    return keyword.strip().lower()


# ---------- tên người nổi tiếng ----------
COMMON = set("""
a an the and or of in on at to for with from by is are was were be been his her their my your our its this that these
those when what who how why where which best most funny funniest moments moment show shows talk late night tonight live
full episode episodes interview interviews compilation video videos official new top part season ep vs ft feat
hilarious savage try not laugh laughing laughs laughter epic fails fail reaction reacts react gets get got makes make
made goes go went forgets forget leaves leave loses lose losing it he she they we you i me us him them all every ever
never nobody everyone everybody someone just only still really very so too more less can't don't doesn't won't didn't
isn't wasn't joke jokes tears near crying cry prank pranks awkward uncomfortable extremely celebrity celebrities star
stars kids guest guests host hosts audience minutes minute hours seconds times time day days year years out up down off
over after before again about like being playing play plays has have had zero filter expected wild crazy insane
unexpected surprise surprises surprised challenge edition special christmas netflix news tv snl abc nbc bbc cnn fox
breaks break character can can't cannot stop forgot funnier greatest worst good great big little old first last one two
three ten minutes straight his hers plan were uncut uncensored behind scenes bloopers blooper outtakes highlights
royal affair content comedy comedian comedians gameshow game games show's hollywood music song songs movie movies
cast red carpet award awards oscars golden globes press tour premiere scene scenes wwe nba nfl news reporter reporters
anchor anchors morning daily weekly week tour back home house family friends real world famous ultimate
""".split())
NAME_TOKEN = re.compile(r"[A-Z][A-Za-z.\-]*(?:['’]s)?|[A-Z]{2,}")


def _not_name(word):
    w = word.lower()
    return w in COMMON or (len(w) > 4 and w.endswith(("ing", "ed", "ly"))) or (word.isupper() and len(word) > 4)


def names(titles, tags=(), weights=None, limit=4, need=2.5):
    """Tên người (2–3 chữ viết hoa liền nhau, không phải chữ thông dụng) trong tiêu đề video gốc.

    Chỉ nhận khi đủ bằng chứng (điểm >= need): có trong tag của video gốc (+2) hoặc lặp lại ở nhiều tiêu đề.
    Không lấy tên chỉ có trong tag: tag mô tả cả video, đoạn được cắt có thể không có người đó.
    """
    weights = weights or [1] * len(titles)
    tagset = {t.lower() for t in tags}
    score = Counter()
    for title, w in zip(titles, weights):
        run = []
        for tok in re.findall(r"[\w.’'\-]+", title) + [""]:
            clean = re.sub(r"['’]s$", "", tok)
            if NAME_TOKEN.fullmatch(tok or "x") is None or _not_name(clean) or not clean[:1].isupper():
                if 2 <= len(run) <= 3:
                    score[" ".join(run)] += w
                run = []
                continue
            run.append(clean)
            if clean != tok:                   # "Lee Mack's" kết thúc tên
                if 2 <= len(run) <= 3:
                    score[" ".join(run)] += w + 1
                run = []
    for n in list(score):
        if n.lower() in tagset:
            score[n] += 2
    good = [(n, s) for n, s in score.most_common() if s >= need]
    return [n for n, _ in good[:limit]]


# ---------- tiêu đề theo xu hướng ----------
# (nhận diện trong tiêu đề đang hot, mẫu khi chủ thể là TÊN NGƯỜI, mẫu khi chủ thể là CHỦ ĐỀ hoặc None)
# {X} chủ thể, {Xs} dạng sở hữu, {N} số đoạn
PATTERNS = [
    (r"nobody expected|didn'?t expect|never expected", "Nobody Expected {X} To Be This Funny",
     "Nobody Expected These {X} Moments To Be This Funny"),
    (r"savage", "{Xs} Most SAVAGE Moments", "Most SAVAGE {X} Moments Ever"),
    (r"is hilarious|hilarious when", "{X} Is HILARIOUS When Things Go Off Script", None),
    (r"not in the plan|off script|unscripted|wasn'?t planned", "{X} Moments That Were NOT In The Plan",
     "{X} Moments That Were NOT In The Plan"),
    (r"uncomfortable|awkward|cringe", "{X} Making Everyone EXTREMELY Uncomfortable",
     "{X} Moments That Got EXTREMELY Awkward"),
    (r"zero filter|no filter", "{X} Has ZERO Filter And It's HILARIOUS", None),
    (r"loses? it|losing it|can'?t stop laughing|breaks? character|cracks? up", "{X} Can't Stop LOSING IT 😂",
     "{X} Moments Where Everyone LOSES IT 😂"),
    (r"funniest", "{Xs} FUNNIEST Moments Ever", "FUNNIEST {X} Moments Ever"),
    (r"try not to laugh", "Try Not To Laugh: {X} Edition 😂", "Try Not To Laugh: {X} Edition 😂"),
    (r"best moments|top \d+", "{Xs} Best Moments (Top {N})", "Top {N} {X} Moments That Broke The Internet"),
]


def trend_scores(trend):
    """Điểm từng mẫu = tổng log(lượt xem) các video tháng này có kiểu tiêu đề đó."""
    out = []
    for rx, person, topic in PATTERNS:
        hits = [v for v, t in trend if re.search(rx, t, re.I)]
        out.append({"person": person, "topic": topic, "score": round(sum(math.log10(v + 10) for v in hits), 2),
                    "hits": len(hits)})
    return out


def possessive(x):
    return x + ("'" if x.endswith("s") else "'s")


def title_case(text):
    small = {"a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "vs"}
    ws = text.split()
    return " ".join(w if w.isupper() and len(w) > 1 else (w if i and w in small else w[:1].upper() + w[1:])
                    for i, w in enumerate(ws))


def score_title(title, phrase, name_list, trending_tpls):
    """Chấm SEO thang 10, kèm danh sách tiêu chí (đạt / chưa đạt) để hiện trên giao diện."""
    low = title.lower()
    pw = _words(phrase)
    has_phrase = all(w in _words(title) for w in pw)
    first = low.find(pw[0]) if pw else -1
    checks = [
        ("Có cụm từ người ta đang tìm “" + phrase + "”", has_phrase, 3),
        ("Từ khoá nằm ở 50 ký tự đầu", has_phrase and 0 <= first < 50, 1),
        ("Có tên người nổi tiếng", any(n.lower() in low for n in name_list), 2 if name_list else 0),
        ("Dài 40–70 ký tự (không bị cắt trên điện thoại)", 40 <= len(title) <= 70, 2),
        ("Kiểu tiêu đề đang hot tháng này", any(_tpl_match(t, title) for t in trending_tpls), 1),
        ("Có 1 từ VIẾT HOA nhấn mạnh", bool(re.search(r"\b[A-Z]{4,}\b", title)), 1),
    ]
    total = sum(w for _, _, w in checks) or 1
    got = sum(w for _, ok, w in checks if ok)
    return round(got * 10 / total), [(label, ok) for label, ok, w in checks if w]


def _tpl_match(tpl, title):
    parts = [re.escape(p.strip()) for p in re.split(r"\{\w+\}", tpl) if p.strip()]
    return bool(parts) and all(re.search(p, title, re.I) for p in parts)


def relevant(keyword, phrases, context):
    """Gợi ý tìm kiếm khớp với nội dung video: chữ thêm vào so với từ khoá phải là chữ thông dụng ("moments")
    hoặc có trong tiêu đề/tag video gốc (bỏ "… india", "… bollywood" khi video không liên quan)."""
    kw, ctx = set(_words(keyword)), set(_words(context))
    stem = {w.rstrip("s") for w in ctx}
    out = []
    for ph in phrases:
        extra = set(_words(ph)) - kw
        if kw & set(_words(ph)) and all(w in COMMON or w in ctx or w.rstrip("s") in stem for w in extra):
            out.append(ph)
    return out


def tags(phrase, keyword, phrases, name_list, source_tags, channels, limit=480):
    """Danh sách tag YouTube (tối đa ~500 ký tự): từ khoá chính, gợi ý tìm kiếm, tên người, tag của video gốc."""
    cand = [phrase, keyword.lower(), *phrases, *(n.lower() for n in name_list),
            *(f"{n.lower()} funny moments" for n in name_list[:2]),
            *(t for t, _ in Counter(t.lower() for t in source_tags).most_common(15)),
            *(c.lower() for c in channels), "funny moments", "try not to laugh", "compilation"]
    out, size = [], 0
    for t in cand:
        t = re.sub(r"[<>,#]", "", t).strip()
        if t and t not in out and size + len(t) + 1 <= limit:
            out.append(t)
            size += len(t) + 1
    return out
