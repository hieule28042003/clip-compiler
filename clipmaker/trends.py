"""Nghiên cứu xu hướng: từ khoá nào đang được tìm, chủ đề / người nào đang hot tháng này.

1. Mở rộng từ khoá: gợi ý tìm kiếm YouTube cho "lĩnh vực" và "lĩnh vực + a…z" (người xem đang gõ gì).
2. Đo độ hot từng từ khoá: video tải lên tháng này nhiều lượt xem nhất (tổng view top 5, số video ≥ 1 triệu,
   bao nhiêu video là dạng tổng hợp — dạng video app làm ra).
3. Chủ đề hot: tên người / chương trình xuất hiện ở nhiều video hot, cộng dồn lượt xem.
"""
import math
import re
import string
import time
from concurrent.futures import ThreadPoolExecutor

from . import seo

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


def measure(keyword):
    """Độ hot của một từ khoá dựa trên video tháng này (bỏ Shorts)."""
    vids = [v for v in seo.month_top(keyword, 20) if v["duration"] >= MIN_LEN]
    vids.sort(key=lambda v: -v["views"])
    views = [v["views"] for v in vids]
    return {"keyword": keyword, "videos": vids[:10], "top5": sum(views[:5]),
            "million": sum(1 for x in views[:10] if x >= 1_000_000),
            "compilations": sum(1 for v in vids[:10] if COMPILATION.search(v["title"])),
            "count": len(vids)}


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


def research(seed, depth=10, job=None):
    job = job if job is not None else {}
    job["stage"] = "Đang lấy từ khoá người xem đang tìm"
    keywords = expand(seed, depth)
    if not keywords:
        raise RuntimeError("Không lấy được gợi ý tìm kiếm từ YouTube (kiểm tra mạng)")
    measured = []
    job["stage"] = f"Đang đo độ hot {len(keywords)} từ khoá"
    with ThreadPoolExecutor(3) as ex:                  # vừa phải để YouTube không chặn
        for i, m in enumerate(ex.map(measure, keywords)):
            if job.get("stop"):
                raise InterruptedError
            measured.append(m)
            job["progress"] = 0.1 + 0.85 * (i + 1) / len(keywords)
    measured = [m for m in measured if m["count"]]
    if not measured:
        raise RuntimeError("YouTube không trả về video nào (kiểm tra mạng hoặc thử lĩnh vực khác)")
    top = max(_heat(m) for m in measured)
    for m in measured:
        m["heat"] = round(100 * _heat(m) / top)
    measured.sort(key=lambda m: -m["heat"])
    titles = [(v["views"], v["title"]) for m in measured for v in m["videos"]]
    patterns = sorted((p for p in seo.trend_scores(titles) if p["hits"]), key=lambda p: -p["score"])
    return {"seed": seed, "at": time.time(), "keywords": measured, "topics": topics(measured, seed),
            "patterns": [{"text": re.sub(r"\{\w+\}", "…", p["person"]), "hits": p["hits"]} for p in patterns]}
