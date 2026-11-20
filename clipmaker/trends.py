"""Nghiên cứu xu hướng: từ khoá nào đang được tìm, chủ đề / người nào đang hot tuần / tháng này.

1. Mở rộng từ khoá: gợi ý tìm kiếm YouTube cho "lĩnh vực" và "lĩnh vực + a…z" (người xem đang gõ gì).
2. Đo độ hot từng từ khoá: video tải lên tháng này nhiều lượt xem nhất (tổng view top 5, số video ≥ 1 triệu,
   bao nhiêu video là dạng tổng hợp — dạng video app làm ra).
3. Chủ đề hot: tên người / chương trình xuất hiện ở nhiều video hot, cộng dồn lượt xem.
4. Phân tích: định dạng (Shorts / video dài), độ dài, công thức tiêu đề, ngày đăng, kênh nổi bật đang ăn khách,
   và các video đang lên nhanh (view / ngày, gấp bao nhiêu lần số sub kênh) để làm video tương tự.
"""
import math
import re
import statistics
import string
import time
from concurrent.futures import ThreadPoolExecutor

import yt_dlp

from . import seo
from . import youtube as yt

COMPILATION = re.compile(r"compilation|moments|top \d+|funniest|best of|try not to laugh|bloopers|fails", re.I)
SKIP = re.compile(r"\b(shorts?|lyrics?|song|status|whatsapp|ringtone|live stream)\b", re.I)
MIN_LEN = 60                     # bỏ Shorts: app làm video dài


def expand(seed, limit=16):
    """Từ khoá con của lĩnh vực, phổ biến trước: gợi ý cho chính lĩnh vực, rồi gợi ý đầu của 'lĩnh vực + chữ cái'."""
    seed = seed.strip().lower()
    with ThreadPoolExecutor(8) as ex:
        base = ex.submit(seo.suggestions, seed)
        letters = list(ex.map(lambda ch: seo.suggestions(f"{seed} {ch}"), string.ascii_lowercase))
    ordered = [seed, *base.result()]
    for rank in range(3):                      # gợi ý số 1 của mọi chữ cái, rồi số 2…
        ordered += [lst[rank] for lst in letters if len(lst) > rank]
    out = []
    for k in ordered:
        k = re.sub(r"\s+", " ", k).strip()
        if k and k not in out and not SKIP.search(k):
            out.append(k)
    return out[:limit]


def measure(keyword, period="month"):
    """Độ hot của một từ khoá dựa trên video tuần / tháng này (bỏ Shorts); "all" giữ cả Shorts để phân tích."""
    found = seo.top_videos(keyword, 20, period)
    for v in found:
        v["keyword"] = keyword
    vids = [v for v in found if v["duration"] >= MIN_LEN]
    vids.sort(key=lambda v: -v["views"])
    views = [v["views"] for v in vids]
    return {"keyword": keyword, "videos": vids[:10], "top5": sum(views[:5]),
            "million": sum(1 for x in views[:10] if x >= 1_000_000),
            "compilations": sum(1 for v in vids[:10] if COMPILATION.search(v["title"])),
            "count": len(vids), "all": found}


def _heat(m):
    return math.log10(m["top5"] + 10) + 0.15 * m["million"] + 0.1 * m["compilations"]


def _casing(word, titles):
    """Cách viết hay gặp nhất trong tiêu đề ('ishowspeed' -> 'IShowSpeed'), không có thì viết hoa chữ đầu."""
    seen = {}
    for t in titles:
        for m in re.finditer(r"\b" + re.escape(word) + r"\b", t, re.I):
            seen[m.group(0)] = seen.get(m.group(0), 0) + 1
    seen = {k: v for k, v in seen.items() if not k.islower()}
    return max(seen, key=seen.get) if seen else word.title()


def _entities(vids, skip):
    """Tên riêng 1–3 chữ (viết hoa, không phải chữ thông dụng) lặp lại ở nhiều video hot."""
    stats = {}
    for v in vids:
        toks = re.findall(r"[A-Za-z][\w’'.&-]*", v["title"])
        grams = set()
        for n in (1, 2, 3):
            for i in range(len(toks) - n + 1):
                w = [re.sub(r"['’]s$", "", t).strip(".-") for t in toks[i:i + n]]
                if all(len(x) > 2 and x[:1].isupper() and not seo._not_name(x) and x.lower() not in skip for x in w):
                    grams.add(" ".join(w))
        for g in grams:
            st = stats.setdefault(g.lower(), {"name": g, "ids": set()})
            st["ids"].add(v["id"])
    out = {k: st for k, st in stats.items() if len(st["ids"]) >= 3}
    # ghép 2 tên đơn hay đi liền nhau và cùng xuất hiện ở các video đó ("Barack" + "Obama" -> "Barack Obama")
    singles = [k for k in out if " " not in k]
    for a in singles:
        for b in singles:
            if a in out and b in out and a != b:
                both = out[a]["ids"] & out[b]["ids"]
                if len(both) >= 0.6 * min(len(out[a]["ids"]), len(out[b]["ids"])) and \
                        any(re.search(rf"\b{re.escape(a)}\s+{re.escape(b)}\b", v["title"], re.I) for v in vids):
                    out[f"{a} {b}"] = {"name": f'{out[a]["name"]} {out[b]["name"]}', "ids": out[a]["ids"] | out[b]["ids"]}
                    del out[a], out[b]
    # bỏ tên ngắn nằm trong tên dài hơn có gần đủ các video đó ("Kapil" khi đã có "Kapil Sharma")
    for k in list(out):
        if any(k != o and k in o and len(out[o]["ids"]) >= 0.7 * len(out[k]["ids"]) for o in out):
            del out[k]
    return out


def topics(measured, seed, limit=12):
    """Chủ đề hot: chữ người xem gõ thêm vào lĩnh vực ("… roblox") + tên riêng lặp lại ở nhiều video hot."""
    pool = {}
    for m in measured:
        for v in m["videos"]:
            pool.setdefault(v["id"], v)
    vids = list(pool.values())
    titles = [v["title"] for v in vids]
    seed_words = set(re.findall(r"\w+", seed.lower()))
    found = {}
    small = {"in", "of", "the", "and", "on", "with", "for", "a", "at", "to"}

    def generic(w):          # chữ chung chung / biến thể của lĩnh vực ("celeb" của "celebrity") không phải chủ đề
        return w in seo.COMMON or any(len(w) >= 4 and (s.startswith(w) or w.startswith(s)) for s in seed_words)

    for m in measured:
        words = [w for w in re.findall(r"[\w'’.&-]+", m["keyword"].lower()) if w not in seed_words]
        while words and words[0] in small:
            words.pop(0)
        while words and words[-1] in small:
            words.pop()
        if words and m["videos"] and not all(generic(w) or w in small for w in words):
            name = " ".join(w if w in small else _casing(w, titles) for w in words)
            found[name.lower()] = {"name": name, "videos": m["videos"], "source": "search", "keyword": m["keyword"]}
    skip = seed_words | set(found)
    for k, st in _entities(vids, skip).items():
        if not any(k in f or f in k for f in found):
            mine = sorted((pool[i] for i in st["ids"]), key=lambda v: -v["views"])
            found[k] = {"name": st["name"], "videos": mine, "source": "titles",
                        "keyword": f"{st['name']} " + ("funny moments" if "funny" in seed_words else seed)}
    out = []
    for t in found.values():
        vs = sorted(t["videos"], key=lambda v: -v["views"])[:8]
        out.append({"name": t["name"], "source": t["source"], "videos": vs, "count": len(t["videos"]),
                    "views": sum(v["views"] for v in t["videos"]), "keyword": t["keyword"]})
    out.sort(key=lambda t: -t["views"])
    return [t for t in out if t["views"] >= 100_000][:limit]


# ---------- phân tích ----------
FEATURES = [            # (nhãn, kiểm tra tiêu đề)
    ("Có từ VIẾT HOA nhấn mạnh", lambda t: bool(re.search(r"\b[A-Z]{3,}\b", t))),
    ("Có emoji", lambda t: bool(re.search("[\U0001F300-\U0001FAFF\u2600-\u27BF]", t))),
    ("Có dấu ! hoặc ?", lambda t: bool(re.search(r"[!?]", t))),
    ("Có con số (Top 10, 5 lần…)", lambda t: bool(re.search(r"\d", t))),
    ("Dạng tổng hợp (moments, compilation…)", lambda t: bool(COMPILATION.search(t))),
    ("Tiêu đề ngắn ≤ 50 ký tự", lambda t: len(t) <= 50),
]
LENGTHS = [(0, 300, "dưới 5 phút"), (300, 600, "5–10 phút"), (600, 1200, "10–20 phút"), (1200, 10 ** 9, "trên 20 phút")]
DAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
LIVE = re.compile(r"🔴|\blive\b|\bstream", re.I)
DETAILS = 16                    # số video lấy thông tin chi tiết (ngày đăng, like, sub kênh)


def is_short(v):
    return 0 < v["duration"] <= 60 or ("#short" in v["title"].lower() and v["duration"] <= 180)


def _med(xs):
    return int(statistics.median(xs)) if xs else 0


def _details(vid):
    """Ngày giờ đăng, like, bình luận, số sub kênh của một video (không tải video)."""
    with yt_dlp.YoutubeDL({**yt._base_opts(), "skip_download": True}) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    return {"ts": info.get("timestamp") or info.get("release_timestamp"), "date": info.get("upload_date"),
            "likes": info.get("like_count") or 0, "comments": info.get("comment_count") or 0,
            "subs": info.get("channel_follower_count") or 0, "views": info.get("view_count") or 0,
            "live": info.get("live_status") in ("is_live", "was_live", "post_live", "is_upcoming")}


def _when(d):
    if d.get("ts"):
        return float(d["ts"])
    if d.get("date"):
        return time.mktime(time.strptime(d["date"], "%Y%m%d")) + 12 * 3600
    return None


def _group(vids):
    views = [v["views"] for v in vids]
    return {"count": len(vids), "median": _med(views), "total": sum(views)}


def _fmt(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e3 else str(int(n))


def analyze(vids, topics_, job=None, now=None):
    """vids: video hot (gồm cả Shorts) của mọi từ khoá -> định dạng, độ dài, tiêu đề, ngày đăng, kênh, video đang lên."""
    job = job if job is not None else {}
    now = now or time.time()
    vids = sorted({v["id"]: v for v in vids}.values(), key=lambda v: -v["views"])
    if not vids:
        return None
    shorts, longs = [v for v in vids if is_short(v)], [v for v in vids if not is_short(v)]
    fmt = {"shorts": _group(shorts), "long": _group(longs)}
    lengths = [{"label": lab, **_group([v for v in longs if lo <= v["duration"] < hi])} for lo, hi, lab in LENGTHS]
    lengths = [x for x in lengths if x["count"]]
    top = vids[:max(10, len(vids) // 3)]               # nhóm video nhiều view nhất
    feats = []
    for label, test in FEATURES:
        yes = [v["views"] for v in vids if test(v["title"])]
        no = [v["views"] for v in vids if not test(v["title"])]
        if len(yes) >= 3 and len(no) >= 3:
            feats.append({"label": label, "share": round(100 * sum(test(v["title"]) for v in top) / len(top)),
                          "lift": round(_med(yes) / max(_med(no), 1), 1), "count": len(yes)})
    feats.sort(key=lambda f: -f["lift"])
    patterns = sorted((x for x in seo.trend_scores([(v["views"], v["title"]) for v in vids]) if x["hits"]),
                      key=lambda x: -x["score"])
    chans = {}
    for v in vids:
        c = chans.setdefault(v["channel"] or "?", {"name": v["channel"] or "?", "views": 0, "count": 0, "top": v})
        c["views"] += v["views"]
        c["count"] += 1
    channels = sorted(chans.values(), key=lambda c: -c["views"])[:6]
    channels = [{"name": c["name"], "views": c["views"], "count": c["count"],
                 "top": {"id": c["top"]["id"], "title": c["top"]["title"]}} for c in channels]

    # chi tiết các video nhiều view nhất: ngày đăng -> view / ngày; sub kênh -> video "vượt sức" kênh
    job["stage"] = f"Đang xem chi tiết {min(DETAILS, len(vids))} video hot nhất"
    pick = vids[:DETAILS]
    with ThreadPoolExecutor(4) as ex:
        infos = list(ex.map(lambda v: _safe(_details, v["id"]), pick))
    topic_of = {}
    for t in topics_:   # chủ đề từ tên riêng trong tiêu đề chỉ dùng khi là tên 2 chữ trở lên ("Michelle Obama", không "Missions")
        if t["source"] == "search" or " " in t["name"]:
            for v in t["videos"]:
                topic_of.setdefault(v["id"], t)
    rising, days, hours = [], {}, {}
    for v, d in zip(pick, infos):
        if not d or d.get("live") or LIVE.search(v["title"]):      # livestream: không làm lại được
            continue
        at = _when(d)
        views = max(v["views"], d["views"])
        if at:
            lt = time.localtime(at)
            days.setdefault(lt.tm_wday, []).append(views)
            if d.get("ts"):
                hours.setdefault(lt.tm_hour, []).append(views)
        age = max(0.5, (now - at) / 86400) if at else None
        per_day = views / age if age else 0
        ratio = views / d["subs"] if d["subs"] >= 1000 else 0
        why = []
        if age:
            why.append(f"{_fmt(views)} view sau {age:.0f} ngày (~{_fmt(per_day)}/ngày)" if age >= 1
                       else f"{_fmt(views)} view chỉ trong chưa tới 1 ngày")
        if ratio >= 3:
            why.append(f"gấp {ratio:.0f} lần số sub của kênh: nội dung tự kéo view, kênh nhỏ cũng làm được")
        if d["likes"] and d["likes"] / max(views, 1) >= 0.04:
            why.append(f"tỉ lệ like cao ({100 * d['likes'] / views:.1f}%)")
        if d["comments"] and d["comments"] / max(views, 1) >= 0.003:
            why.append(f"nhiều bình luận ({_fmt(d['comments'])})")
        why += [f["label"].lower() for f in feats[:3] if f["lift"] > 1.2 and dict(FEATURES)[f["label"]](v["title"])]
        t = topic_of.get(v["id"])
        rising.append({**{k: v[k] for k in ("id", "title", "channel", "views", "duration")},
                       "short": is_short(v), "age": round(age, 1) if age else None, "per_day": int(per_day),
                       "ratio": round(ratio, 1), "subs": d["subs"], "likes": d["likes"], "why": why,
                       "keyword": t["keyword"] if t else v.get("keyword", ""), "topic": t["name"] if t else "",
                       "score": math.log10(per_day + 10) + 0.4 * math.log10(min(ratio, 100) + 1)})
    rising.sort(key=lambda r: -r["score"])
    per_ch = {}
    rising = [r for r in rising if per_ch.setdefault(r["channel"], []).append(r) or len(per_ch[r["channel"]]) <= 2]
    same = {}                                    # video cùng từ khoá: quét kèm khi "làm tương tự"
    for v in vids:
        same.setdefault(v.get("keyword", ""), []).append(v["id"])
    for r in rising:
        r["similar"] = [i for i in same.get(r["keyword"], []) if i != r["id"]][:6]

    def best(groups, names):
        ok = {k: _med(x) for k, x in groups.items() if len(x) >= 2}
        return [{"label": names(k), "median": m, "count": len(groups[k])}
                for k, m in sorted(ok.items(), key=lambda kv: -kv[1])][:3]

    res = {"count": len(vids), "format": fmt, "lengths": lengths, "features": feats,
           "patterns": [{"text": re.sub(r"\{\w+\}", "…", x["person"]), "hits": x["hits"]} for x in patterns[:6]],
           "channels": channels, "days": best(days, lambda k: DAYS[k]), "hours": best(hours, lambda k: f"{k}h–{k + 1}h"),
           "rising": rising[:10]}
    res["tips"] = _tips(res)
    return res


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001 - video riêng tư / bị chặn: bỏ qua
        return None


def _tips(a):
    """Kết luận ngắn: nên làm gì cho giống các video đang ăn khách."""
    tips = []
    s, l_ = a["format"]["shorts"], a["format"]["long"]
    if s["count"] >= 3 and l_["count"] >= 3:
        if s["median"] > 1.5 * l_["median"]:
            tips.append(f"Shorts đang ăn khách hơn: view trung vị {_fmt(s['median'])} so với {_fmt(l_['median'])} của "
                        "video dài → nên làm Shorts (trang ✂ Làm Shorts).")
        elif l_["median"] > 1.5 * s["median"]:
            tips.append(f"Video dài đang ăn khách hơn: view trung vị {_fmt(l_['median'])} so với {_fmt(s['median'])} "
                        "của Shorts → ưu tiên video tổng hợp dài.")
    lens = [x for x in a["lengths"] if x["count"] >= 3]
    if lens:
        b = max(lens, key=lambda x: x["median"])
        tips.append(f"Độ dài ăn khách nhất: {b['label']} (view trung vị {_fmt(b['median'])}, {b['count']} video).")
    for f in a["features"][:2]:
        if f["lift"] >= 1.3:
            tips.append(f"Tiêu đề “{f['label'].lower()}” có view trung vị gấp {f['lift']} lần ({f['share']}% video "
                        "top dùng kiểu này).")
    if a["patterns"]:
        tips.append(f"Kiểu tiêu đề đang lặp lại nhiều: “{a['patterns'][0]['text']}” (×{a['patterns'][0]['hits']}).")
    if a["days"]:
        tips.append(f"Video đăng {a['days'][0]['label']} đang nhiều view nhất"
                    + (f", khung {a['hours'][0]['label']} (giờ máy bạn)." if a["hours"] else "."))
    r = next((x for x in a["rising"] if x["ratio"] >= 3), None)
    if r:
        tips.append(f"Ví dụ nên làm theo: “{r['title']}”: {r['why'][0] if r['why'] else ''}.")
    return tips


def research(seed, depth=10, job=None, period="month", deep=True):
    """deep: phân tích thêm (xem chi tiết các video hot nhất); kênh tự đăng chỉ cần chủ đề nên tắt cho nhanh."""
    job = job if job is not None else {}
    job["stage"] = "Đang lấy từ khoá người xem đang tìm"
    keywords = expand(seed, depth)
    if not keywords:
        raise RuntimeError("Không lấy được gợi ý tìm kiếm từ YouTube (kiểm tra mạng)")
    measured = []
    job["stage"] = f"Đang đo độ hot {len(keywords)} từ khoá"
    with ThreadPoolExecutor(3) as ex:                  # vừa phải để YouTube không chặn
        for i, m in enumerate(ex.map(lambda k: measure(k, period), keywords)):
            if job.get("stop"):
                raise InterruptedError
            measured.append(m)
            job["progress"] = 0.05 + 0.65 * (i + 1) / len(keywords)
    everything = [v for m in measured for v in m.pop("all")]
    measured = [m for m in measured if m["count"]]
    if not measured:
        raise RuntimeError("YouTube không trả về video nào (kiểm tra mạng hoặc thử lĩnh vực khác)")
    top = max(_heat(m) for m in measured)
    for m in measured:
        m["heat"] = round(100 * _heat(m) / top)
    measured.sort(key=lambda m: -m["heat"])
    titles = [(v["views"], v["title"]) for m in measured for v in m["videos"]]
    patterns = sorted((p for p in seo.trend_scores(titles) if p["hits"]), key=lambda p: -p["score"])
    hot = topics(measured, seed)
    job["progress"] = 0.75
    return {"seed": seed, "period": period, "at": time.time(), "keywords": measured, "topics": hot,
            "patterns": [{"text": re.sub(r"\{\w+\}", "…", p["person"]), "hits": p["hits"]} for p in patterns],
            "analysis": analyze(everything, hot, job) if deep else None}
