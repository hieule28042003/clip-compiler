"""Ghép nhạc thành video dài: tìm bài đang hot theo quốc gia + thể loại -> tải phần tiếng -> nối crossfade ->
video 1920x1080 có danh sách bài (bài đang phát được tô sáng), ảnh bìa, sóng nhạc, thanh tiến độ.
"""
import math
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import yt_dlp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import compose, seo
from . import youtube as yt

W, H = compose.W, compose.H
FPS = 30
# quốc gia: (tên hiện trên web, các câu tìm ({g} = chữ của thể loại), tên tiếng Anh cho tiêu đề)
COUNTRIES = {
    "vn": ("🇻🇳 Việt Nam", ["{g} việt official mv", "{g} việt mới nhất official music video", "vpop {g} official mv"],
           "Vietnamese"),
    "kr": ("🇰🇷 Hàn Quốc", ["kpop {g} official mv", "kpop {g} m/v", "korean {g} song official audio"], "K-Pop"),
    "us": ("🇺🇸 Âu Mỹ (US-UK)", ["{g} official music video", "{g} official audio", "new {g} song"], "US-UK"),
    "jp": ("🇯🇵 Nhật Bản", ["jpop {g} official music video", "japanese {g} song mv"], "J-Pop"),
    "cn": ("🇨🇳 Trung Quốc", ["chinese {g} song official mv", "抖音 {g} 歌曲", "c-pop {g} mv"], "Chinese"),
    "th": ("🇹🇭 Thái Lan", ["thai {g} song official mv", "เพลง {g} official mv"], "Thai"),
    "latin": ("🌎 Latin", ["latin {g} video oficial", "reggaeton {g} video oficial"], "Latin"),
    "in": ("🇮🇳 Ấn Độ", ["hindi {g} song official video", "bollywood {g} song"], "Bollywood"),
}
# thể loại: (tên hiện trên web, chữ thêm vào câu tìm, chữ cho câu tìm tiếng Việt, tên tiếng Anh)
GENRES = {
    "all": ("Tất cả thể loại", "", "", ""),
    "pop": ("Pop / nhạc trẻ", "pop", "nhạc trẻ", "Pop"),
    "ballad": ("Ballad / nhạc buồn", "ballad", "ballad", "Ballad"),
    "rap": ("Rap / Hip-hop", "rap hip hop", "rap", "Rap"),
    "edm": ("EDM / Dance", "edm dance", "vinahouse edm", "EDM"),
    "rnb": ("R&B / Soul", "r&b", "r&b", "R&B"),
    "rock": ("Rock / Indie", "rock indie", "rock indie", "Rock"),
}
# bài hot nhất mọi thời điểm: video nhiều view nhất từ trước tới nay cho các câu tìm này ("_" = tất cả / pop)
ALLTIME = {
    "vn": {"_": ["vpop official music video", "nhạc trẻ việt official mv"], "ballad": ["ballad việt official mv",
           "nhạc buồn việt official mv"], "rap": ["rap việt official mv"], "edm": ["vinahouse việt official mv",
           "edm việt official mv"], "rnb": ["r&b việt official mv"], "rock": ["rock việt official mv"]},
    "kr": {"_": ["kpop official mv", "kpop m/v"], "ballad": ["kpop ballad official mv"],
           "rap": ["korean hip hop official mv"], "edm": ["kpop edm official mv"], "rnb": ["korean r&b official mv"],
           "rock": ["korean rock band official mv"]},
    "us": {"_": ["vevo official music video"], "ballad": ["ballad vevo official video"],
           "rap": ["hip hop vevo official video"], "edm": ["edm vevo official video", "dance vevo official video"],
           "rnb": ["r&b vevo official video"], "rock": ["rock vevo official video"]},
    "jp": {"_": ["jpop official mv", "japanese song official music video"]},
    "cn": {"_": ["華語 official mv", "中文歌 official music video"]},
    "th": {"_": ["เพลงไทย official mv", "thai song official mv"]},
    "latin": {"_": ["reggaeton video oficial", "latin pop video oficial"]},
    "in": {"_": ["hindi song official video", "bollywood song official video"]},
}
# phiên bản của bài: (tên trên web, chữ thêm vào câu tìm, dấu hiệu trong tiêu đề, chữ thêm sau tên bài)
VERSIONS = {
    "original": ("🎤 Bản gốc", "", "", ""),
    "remix": ("🔥 Remix", "remix", r"remix|vinahouse|bootleg|\bedm\b|house", "Remix"),
    "chill": ("🌙 Chill / Lofi", "lofi", r"lo-?fi|chill", "Lofi"),
    "acoustic": ("🎸 Acoustic", "acoustic", r"acoustic|guitar|mộc", "Acoustic"),
    "piano": ("🎹 Piano", "piano", r"piano", "Piano"),
}
# dấu hiệu bản phối lại: bản gốc thì không được có
ANY_VERSION = re.compile(r"remix|lo-?fi|chill|acoustic|piano|instrumental|không lời|karaoke|\bbeat\b|cover|slowed|"
                         r"sped ?up|speed ?up|nightcore|\b8d\b|vinahouse|bootleg|\bver\b|version|guitar", re.I)
# bỏ các video không phải một bài hát: tổng hợp, live, reaction, karaoke, cover...
NOT_SONG = re.compile(r"\b(mix|playlist|nonstop|non-stop|compilation|full album|album|tuyển tập|liên khúc|lk|live|"
                      r"concert|reaction|react|karaoke|tutorial|cover|teaser|trailer|behind|making|dance practice|"
                      r"fancam|hours?|giờ|top \d+|top [kv]-?pop|nhiều lượt xem|bxh|chart|ep\.? ?\d+|episode|shorts?|podcast|interview|"
                      r"color coded|han_rom|lời việt|song ngữ|tuổi hát|parody|nhại|speed ?up|sped ?up|nightcore|8d)\b", re.I)
SONG_MIN, SONG_MAX = 100, 480                  # giây: bỏ đoạn nhạc ngắn / video dài không phải một bài
PER_ARTIST = 2
# phần đuôi tiêu đề không thuộc tên bài: (Official MV), [Lyrics], | Official Audio, ft. ...
TAIL = re.compile(r"\s*[\(\[【「](?:[^\)\]】」]*?(?:official|mv|m/v|video|audio|lyric|visuali[sz]er|music|prod|"
                  r"vietsub|engsub|color coded|4k|hd|ost)[^\)\]】」]*)[\)\]】」]", re.I)
BAR = re.compile(r"\s*[|｜/]{1,2}\s*(?:official|mv|m/v|lyric|audio|music video|nhạc|ost|\S+ entertainment).*$", re.I)


def _clean(title):
    t = TAIL.sub("", title)
    t = BAR.sub("", t)
    t = re.sub(r"[\s!]*(?:official\s+(?:music\s+)?(?:video|audio|mv)|m/v|\bmv\b|lyrics?(?: video)?)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+!$", " !", t)
    t = re.sub(r"#\S+", "", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -–|·")


def split_title(title, channel=""):
    """"SƠN TÙNG M-TP | CHÚNG TA CỦA TƯƠNG LAI | OFFICIAL MV" -> (nghệ sĩ, tên bài)."""
    t = _clean(title)
    m = re.match(r"^(.+?)\s*['‘’\"“](.+?)['‘’\"”]\s*$", t)        # kiểu K-pop: NGHỆ SĨ 'Tên bài'
    if m:
        return re.sub(r"\s*\(.*?\)$", "", m[1]).strip(" -–"), m[2].strip()
    for sep in (" - ", " – ", " — ", " | ", "｜", " || "):
        if sep in t:
            a, s = t.split(sep, 1)
            s = re.split(r"\s+[|｜]\s+", s.split(sep)[0])[0]           # "Tên bài | G5R", "Tên bài | ft. A x B"
            a, s = _clean(a), _clean(s)
            ck = _key(channel)
            plain = lambda x: re.sub(r"[\(\[]?\b(?:feat|ft)\b.*$", "", x, flags=re.I).lower()
            # "TÊN BÀI - Ca sĩ": phần sau trùng tên kênh / có " x " (hợp tác) mới là nghệ sĩ
            if (ck and _key(s)[:6] and _key(s)[:6] in ck and _key(a)[:6] not in ck) or \
                    (" x " in plain(s) and " x " not in plain(a)):
                a, s = s, a
            if a and s:
                return a, s
    artist = re.sub(r"\s*(?:- topic|official|vevo|channel|music|entertainment)\s*$", "", channel or "", flags=re.I)
    return artist.strip(), t


def _key(text):
    t = re.sub(r"\(.*?\)|\[.*?\]", "", _clean(text).lower())
    t = re.sub(r"\b(?:ft|feat|featuring|x)\b.*$", "", t)
    return "".join(ch for ch in t if ch.isalnum())


def _norm(text):
    return "".join(ch for ch in text.lower() if ch.isalnum())


def queries(country, genre, free=False):
    g = GENRES[genre][1]
    if free:            # nhạc không bản quyền (NCS…): quốc gia ít ý nghĩa
        return [f"no copyright {g} music", f"{g} music copyright free", f"ncs {g}"]
    if country == "vn":
        g = GENRES[genre][2]
    return [re.sub(r"\s+", " ", q.format(g=g)).strip() for q in COUNTRIES[country][1]]


def alltime_queries(country, genre):
    t = ALLTIME[country]
    return t.get(genre) or ([f"{GENRES[genre][1]} {q}" for q in t["_"]] if genre not in ("all", "pop") else t["_"])


# Bảng xếp hạng Top Songs của iTunes theo nước (miễn phí, cập nhật hằng ngày): đúng "đang hot ở nước đó" hơn tìm
# trên YouTube (kết quả YouTube bị K-pop / nhạc Ấn nhiều view lấn át). Hàn Quốc không có iTunes: dùng mục K-Pop của Mỹ.
CHART = "https://itunes.apple.com/{store}/rss/topsongs/limit={n}{genre}/json"
STORES = {"vn": "vn", "kr": "us", "us": "us", "jp": "jp", "cn": "tw", "th": "th", "latin": "mx", "in": "in"}
POP_ID = {"kr": 51, "jp": 27, "latin": 12, "in": 1263}         # "pop" của các nước này = thể loại riêng của iTunes
GENRE_ID = {"pop": 14, "rap": 18, "edm": 7, "rnb": 15, "rock": 21}  # ballad không có trên iTunes: tìm YouTube
FOREIGN = {"K-Pop", "韓國流行樂", "J-Pop"}
VIET = re.compile(r"[ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ]", re.I)
MIN_VIEWS = 20000                        # bài lấy từ tìm YouTube phải có ít nhất chừng này view (bỏ nhạc AI / rác)
# bài hot mọi thời điểm phải có chữ của nước đó (tìm "thai official mv" vẫn ra BTS, Bruno Mars…)
NEED = {"vn": VIET, "th": re.compile(r"[\u0e00-\u0e7f]"), "jp": re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]"),
        "cn": re.compile(r"[\u4e00-\u9fff]")}
SCRIPTS = {"vn": re.compile(r"[\u0e00-\u0e7f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]"),
           "us": re.compile(r"[^\x00-\u024f\u2000-\u206f\s]"), "latin": re.compile(r"[^\x00-\u024f\u2000-\u206f\s]"),
           "cn": re.compile(r"[\u3040-\u30ff\uac00-\ud7af]")}


def chart_url(country, genre, n=100):
    store = STORES.get(country)
    if not store or (genre not in GENRE_ID and genre != "all") or (country == "kr" and genre not in ("pop", "all")):
        return None
    gid = POP_ID.get(country, 14 if genre == "pop" else None) if genre in ("pop", "all") else GENRE_ID[genre]
    return CHART.format(store=store, n=n, genre=f"/genre={gid}" if gid else "")


def chart(country, genre, n=100):
    """[(nghệ sĩ, tên bài)] theo thứ hạng; bỏ bài nước ngoài lọt vào bảng (K-pop ở bảng Việt Nam…)."""
    import json
    import urllib.request

    url = chart_url(country, genre, n)
    if not url:
        return []
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            es = json.loads(r.read().decode("utf-8", "replace"))["feed"].get("entry") or []
    except Exception:  # noqa: BLE001 - mất mạng / iTunes đổi: quay về tìm YouTube
        return []
    out, bad = [], SCRIPTS.get(country)
    for e in es if isinstance(es, list) else [es]:
        artist, song = e["im:artist"]["label"], e["im:name"]["label"]
        cat = e.get("category", {}).get("attributes", {}).get("label", "")
        if (cat in FOREIGN and country not in ("kr", "jp")) or (bad and bad.search(artist + song)):
            continue
        if country == "vn" and not VIET.search(artist + song):     # bài Âu Mỹ / K-pop lọt bảng Việt Nam
            continue
        song = re.sub(r"\s*[\(\[](?:feat|ft|with|from|nhạc phim|theme|ost|sped up|remix|version|piano|[^\)\]]*soundtrack|[^\)\]]*\bver\b|[^\)\]]*show)[^\)\]]*[\)\]]", "", song,
                      flags=re.I).strip() or song
        out.append((artist, song))
    return out


def _views_ok(v, allow=()):
    bad = NOT_SONG.search(v["title"])
    return SONG_MIN - 20 <= (v["duration"] or 0) <= SONG_MAX and not (bad and bad[1].lower() not in allow)


def _main_artist(artist):
    return re.split(r"\s*(?:,|&| x | feat\.?| ft\.?)\s*", artist, flags=re.I)[0]


def match(artist, song, version="original"):
    """Video YouTube của bài ở phiên bản cần lấy. Bản gốc: kết quả có tên bài, không phải remix / cover…, ưu tiên nhiều
    view + bản audio / lyric (MV hay có đoạn diễn). Remix / lofi / acoustic / piano: tiêu đề phải có tên bài + dấu hiệu
    của phiên bản đó, nhiều view nhất."""
    main = _main_artist(artist)
    vq, vmark = VERSIONS[version][1], VERSIONS[version][2]
    try:
        with yt_dlp.YoutubeDL({**yt._base_opts(), "extract_flat": True, "skip_download": True}) as y:
            res = y.extract_info(f"ytsearch8:{song} {main} {vq}".strip(), download=False)
    except Exception:  # noqa: BLE001
        return None
    sk, ak = _key(song), _key(main)
    allow = ("cover",) if version in ("acoustic", "piano") else ()
    best, best_s = None, 0.0
    for e in res.get("entries") or []:
        v = {"id": e.get("id"), "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or "",
             "views": e.get("view_count") or 0, "duration": e.get("duration") or 0}
        if not v["id"] or not _views_ok(v, allow):
            continue
        tk = _norm(v["title"] + " " + v["channel"])
        if not sk or sk[:12] not in tk:
            continue
        if vmark and not re.search(vmark, v["title"], re.I):
            continue
        # bản phối khác lẫn vào (bản lofi trên kênh "…House" khi tìm remix)
        if vmark and any(re.search(o[2], v["title"], re.I) for k_, o in VERSIONS.items() if o[2] and k_ != version):
            continue
        if not vmark and ANY_VERSION.search(v["title"]):
            continue
        s = math.log10(v["views"] + 10) + (1.5 if ak and ak[:10] in tk else 0)
        if not vmark:
            s += 0.8 if re.search(r"audio|lyric|- topic", v["title"] + " " + v["channel"], re.I) else 0
        if s > best_s:
            best, best_s = v, s
    if not best or best["views"] < (3000 if not vmark else 1000):   # bài rác (nhạc chuông, bản AI…)
        return None
    tag = VERSIONS[version][3]
    return {**best, "artist": artist, "song": f"{song} ({tag})" if tag else song, "base": song, "version": version}


def _top(qs, period, country, job, lo, hi):
    """Video bài hát nhiều view nhất (mọi thời điểm / tuần / tháng này) cho các câu tìm."""
    found = {}
    need = NEED.get(country) if period == "all" else None
    for i, q in enumerate(qs):
        if job is not None:
            if job.get("stop"):
                raise InterruptedError
            job["stage"] = f"Đang tìm bài hot trên YouTube: “{q}”"
            job["progress"] = lo + (hi - lo) * i / len(qs)
        vids = seo.top_videos(q, 80 if period == "all" else 40, period)
        if len(vids) < 8 and period == "week":       # tuần này ít bài: lấy thêm của tháng
            vids += seo.top_videos(q, 40, "month")
        for v in vids:
            if need and not need.search(v["title"] + " " + v["channel"]):
                continue
            if _views_ok(v) and v["views"] >= MIN_VIEWS and found.get(v["id"], {}).get("views", -1) < v["views"]:
                found[v["id"]] = v
    out = []
    for v in sorted(found.values(), key=lambda v: -v["views"]):
        artist, song = split_title(v["title"], v["channel"])
        out.append({**v, "artist": artist, "song": song})
    return out


def _trending(country, genre, period, free, job, lo, hi):
    return _top(queries(country, genre, free), period, country, job, lo, hi)


def song_key(song):
    """Khoá của một bài (không tính phiên bản): dùng để không lặp bài giữa các bản mix."""
    return _key(re.sub(r"\((?:remix|lofi|acoustic|piano)\)$", "", song, flags=re.I))


def find(country, genre, n=15, period="all", free=False, job=None, exclude=(), version="original", used=()):
    """Bài hot theo quốc gia + thể loại, ở phiên bản cần lấy (gốc / remix / lofi / acoustic / piano).
    - "all": video bài hát nhiều view nhất từ trước tới nay, rồi đến bảng xếp hạng iTunes hiện tại.
    - "week" / "month": bảng xếp hạng iTunes hiện tại, thiếu thì bổ sung bài nhiều view nhất tuần / tháng này.
    Danh sách bài hot chỉ để biết bài nào; video lấy là phiên bản cần lấy của bài đó (bản gốc: chính video đó).
    Bỏ bài trong `used` (đã dùng ở bản mix trước), mỗi nghệ sĩ tối đa 2 bài.
    -> [{id, title, channel, views, duration, artist, song, base, version, rank?}]"""
    from concurrent.futures import ThreadPoolExecutor

    out, keys, ids, per = [], set(used), set(exclude), {}

    def take(v):
        k, a = song_key(v.get("base") or v["song"]), _key(_main_artist(v["artist"])) or _key(v["channel"])
        if not k or k in keys or v["id"] in ids or per.get(a, 0) >= PER_ARTIST:
            return
        keys.add(k)
        ids.add(v["id"])
        per[a] = per.get(a, 0) + 1
        out.append(v)

    # 1. danh sách bài hot (theo thứ tự): video sẵn có (tìm YouTube) hoặc (nghệ sĩ, tên bài) từ bảng xếp hạng
    pool = []
    if free:
        pool += [("video", v) for v in _trending(country, genre, "all" if period == "all" else period, True, job, 0, 0.3)]
    elif period == "all":
        pool += [("video", v) for v in _top(alltime_queries(country, genre), "all", country, job, 0, 0.3)]
        pool += [("chart", a, s_) for a, s_ in chart(country, genre)]
    else:
        pool += [("chart", a, s_) for a, s_ in chart(country, genre)]
        pool += [("video", v) for v in _trending(country, genre, period, False, job, 0, 0.3)]
    seen, todo = set(keys), []
    for item in pool:
        artist, song = (item[1]["artist"], item[1]["song"]) if item[0] == "video" else item[1:]
        k = song_key(song)
        if k and k not in seen:
            seen.add(k)
            todo.append(item)

    # 2. lấy video đúng phiên bản của từng bài, theo lô để dừng sớm khi đủ
    def resolve(item):
        if item[0] == "video" and version == "original" and not ANY_VERSION.search(item[1]["title"]):
            return {**item[1], "base": item[1]["song"], "version": "original"}
        artist, song = (item[1]["artist"], item[1]["song"]) if item[0] == "video" else item[1:]
        return match(artist, song, version)

    limit = min(len(todo), n * 4)
    if job is not None:
        job["stage"] = (f"Đang tìm bản {VERSIONS[version][0].split(' ', 1)[1]} của các bài hot"
                        if version != "original" else "Đang tìm video của các bài hot")
    with ThreadPoolExecutor(4) as ex:
        for start in range(0, limit, 8):
            if len(out) >= n:
                break
            batch = todo[start:start + 8]
            for j, (item, v) in enumerate(zip(batch, ex.map(resolve, batch))):
                if job is not None:
                    if job.get("stop"):
                        raise InterruptedError
                    job["progress"] = 0.3 + 0.65 * min(1.0, max(len(out) / n, (start + j + 1) / limit))
                if v:
                    take({**v, "rank": start + j + 1})
    return out[:n]


def info(url_or_id):
    """Thêm bài bằng link YouTube."""
    m = re.search(r"(?:v=|youtu\.be/|shorts/|^)([\w-]{11})(?:\b|$)", url_or_id.strip())
    if not m:
        raise ValueError("Link YouTube không hợp lệ")
    with yt_dlp.YoutubeDL({**yt._base_opts(), "skip_download": True}) as y:
        e = y.extract_info(f"https://www.youtube.com/watch?v={m[1]}", download=False)
    v = {"id": e["id"], "title": e.get("title") or e["id"], "channel": e.get("channel") or e.get("uploader") or "",
         "views": e.get("view_count") or 0, "duration": e.get("duration") or 0}
    artist, song = split_title(v["title"], v["channel"])
    return {**v, "artist": artist, "song": song}


# ---------- tải + chuẩn hoá tiếng ----------
def download_audio(vid, out):
    """Tải phần tiếng của một bài -> FLAC 48 kHz stereo, cắt khoảng lặng đầu / cuối, âm lượng chuẩn (-14 LUFS)."""
    out = Path(out)
    if out.exists():
        return out
    tmp_dir = out.parent / f"tmp_{vid}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        opts = {**yt._base_opts(), "format": "bestaudio/best", "noplaylist": True, "noprogress": True,
                "outtmpl": str(tmp_dir / "src.%(ext)s")}
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([f"https://www.youtube.com/watch?v={vid}"])
        except yt_dlp.utils.DownloadError as e:
            if yt._blocked(str(e)):
                raise yt.Blocked(str(e)) from e
            raise
        src = next(tmp_dir.glob("src.*"))
        trim = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.1"
        part = out.with_suffix(".part.flac")
        compose.run(["-i", str(src), "-vn", "-af",
                     f"{trim},areverse,{trim},areverse,loudnorm=I=-14:TP=-1.5:LRA=11,aresample={compose.SR}",
                     "-ac", "2", "-c:a", "flac", str(part)])
        part.replace(out)
        return out
    finally:
        for f in tmp_dir.glob("*"):
            f.unlink(missing_ok=True)
        tmp_dir.rmdir()


def _fades(fade, n):
    return list(fade) if isinstance(fade, (list, tuple)) else [fade] * max(0, n - 1)


def timeline(durations, fade):
    """Thời điểm bắt đầu của từng bài khi nối chồng crossfade `fade` giây (một số, hoặc danh sách cho từng chỗ nối);
    tổng độ dài."""
    fades = _fades(fade, len(durations))
    starts, t = [], 0.0
    for i, d in enumerate(durations):
        starts.append(t)
        t += d - (fades[i] if i < len(durations) - 1 else 0)
    return starts, t


def mix_audio(files, out, fade):
    """Nối các bài: crossfade `fade` giây (0 = nối liền; danh sách = độ dài riêng cho từng chỗ nối)."""
    args = []
    for f in files:
        args += ["-i", str(f)]
    n = len(files)
    fades = _fades(fade, n)
    if n == 1:
        flt = "[0:a]anull[a]"
    elif all(x > 0 for x in fades):
        parts, last = [], "[0:a]"
        for i in range(1, n):
            lab = "[a]" if i == n - 1 else f"[x{i}]"
            parts.append(f"{last}[{i}:a]acrossfade=d={fades[i - 1]:.4f}:c1=tri:c2=tri{lab}")
            last = lab
        flt = ";".join(parts)
    else:
        flt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
    compose.run([*args, "-filter_complex", flt, "-map", "[a]", "-c:a", "flac", str(out)])
    return out


# ---------- hình ----------
_FONT_CACHE = {}
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]")
THAI = re.compile(r"[\u0e00-\u0e7f]")
CJK_FONTS = ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "C:/Windows/Fonts/malgunbd.ttf",
             "C:/Windows/Fonts/msyhbd.ttc", "/System/Library/Fonts/AppleSDGothicNeo.ttc")
THAI_FONTS = ("/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf", "C:/Windows/Fonts/leelawdb.ttf",
              "/System/Library/Fonts/Thonburi.ttc")


def _font(text, size, weight="Bold"):
    """Font Inter; tên bài tiếng Hàn / Nhật / Trung / Thái dùng font Noto (Inter không có các chữ này)."""
    paths = CJK_FONTS if CJK.search(text) else THAI_FONTS if THAI.search(text) else ()
    key = (next((p for p in paths if Path(p).exists()), None), size, weight)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(key[0], size) if key[0] else compose.font(size, weight)
    return _FONT_CACHE[key]


def _fit(d, text, size, max_w, weight="Bold", min_size=26):
    while size > min_size and d.textlength(text, font=_font(text, size, weight)) > max_w:
        size -= 2
    f = _font(text, size, weight)
    while text and d.textlength(text, font=f) > max_w:
        text = text[:-2].rstrip() + "…"
    return text, f


def thumb_image(path):
    try:
        return Image.open(path).convert("RGB")
    except Exception:  # noqa: BLE001 - không tải được ảnh bìa: dùng ô màu
        return Image.new("RGB", (480, 270), (40, 40, 55))


def fetch_cover(vid, out):
    """Ảnh bìa (thumbnail YouTube) của bài."""
    import urllib.request

    out = Path(out)
    if not out.exists():
        for name in ("maxresdefault", "hqdefault"):
            try:
                with urllib.request.urlopen(f"https://i.ytimg.com/vi/{vid}/{name}.jpg", timeout=15) as r:
                    out.write_bytes(r.read())
                break
            except Exception:  # noqa: BLE001
                continue
    return out


def background(covers, bg=None):
    """Nền: ảnh bạn tải lên, hoặc ghép ảnh bìa của tất cả các bài; làm mờ + tối để chữ nổi."""
    if bg and Path(bg).exists():
        img = compose._cover(Image.open(bg).convert("RGB"), W, H)
        img = img.filter(ImageFilter.GaussianBlur(4))
        return Image.blend(img, Image.new("RGB", (W, H), (8, 8, 16)), 0.45)
    img = Image.new("RGB", (W, H), (14, 14, 24))
    if covers:
        cols = 4 if len(covers) <= 12 else 5
        rows = -(-len(covers) // cols)
        cw, ch = W // cols + 1, H // max(rows, 1) + 1
        for i, c in enumerate(covers):
            img.paste(compose._cover(thumb_image(c), cw, ch), ((i % cols) * cw, (i // cols) * ch))
        img = img.filter(ImageFilter.GaussianBlur(28))
    return Image.blend(img, Image.new("RGB", (W, H), (8, 8, 16)), 0.62)


def _rounded(img, r):
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=r, fill=255)
    out = img.convert("RGBA")
    out.putalpha(m)
    return out


PANEL = (980, 70, 1840, 830)            # khung danh sách bài
VIS_Y, VIS_H = 870, 150                  # sóng nhạc
BAR_Y = 1044                             # thanh tiến độ


def frame(base, tracks, starts, k, cover, out):
    """Khung hình khi đang phát bài k: ảnh bìa + tên bài bên trái, danh sách tất cả các bài bên phải."""
    img = base.convert("RGBA")
    d = ImageDraw.Draw(img)
    # ảnh bìa + đổ bóng
    cw, ch = 780, 439
    cx, cy = 100, 110
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([cx + 10, cy + 16, cx + cw + 10, cy + ch + 16], radius=28, fill=(0, 0, 0, 170))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))
    img.alpha_composite(_rounded(compose._cover(thumb_image(cover), cw, ch), 28), (cx, cy))
    d = ImageDraw.Draw(img)
    t = tracks[k]
    label = f"▶  NOW PLAYING  ·  {k + 1}/{len(tracks)}"
    d.text((cx, cy + ch + 46), label, font=compose.font(34), fill=compose.ACCENT)
    song, f = _fit(d, t["song"] or t["title"], 74, cw)
    d.text((cx, cy + ch + 96), song, font=f, fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
    artist, f = _fit(d, t["artist"], 44, cw, "SemiBold")
    d.text((cx, cy + ch + 190), artist, font=f, fill=(215, 215, 225))
    # danh sách bài
    x0, y0, x1, y1 = PANEL
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle(PANEL, radius=28, fill=(10, 10, 18, 165))
    img.alpha_composite(layer)
    d = ImageDraw.Draw(img)
    d.text((x0 + 36, y0 + 28), "TRACKLIST", font=compose.font(34), fill=compose.ACCENT)
    n = len(tracks)
    rows = 13 if n > 13 else n
    row_h = min(56, (y1 - y0 - 110) // max(rows, 1))
    first = 0 if n <= rows else min(max(0, k - rows // 2), n - rows)
    size = 30 if row_h >= 50 else 26
    y = y0 + 92
    for i in range(first, first + rows):
        tr, cur = tracks[i], i == k
        if cur:
            d.rounded_rectangle([x0 + 18, y - 6, x1 - 18, y + row_h - 8], radius=14, fill=compose.ACCENT)
        col = (20, 16, 8) if cur else ((150, 150, 160) if i < k else (235, 235, 240))
        stamp = _ts(starts[i])
        d.text((x0 + 36, y + 4), f"{i + 1:02d}", font=compose.font(size), fill=col)
        d.text((x0 + 100, y + 4), stamp, font=compose.font(size, "SemiBold"), fill=col)
        name = f"{tr['artist']} – {tr['song']}" if tr["artist"] else tr["song"]
        name, f = _fit(d, name, size, x1 - x0 - 250, "SemiBold" if not cur else "Bold", 20)
        d.text((x0 + 220, y + 4), name, font=f, fill=col)
        y += row_h
    if n > rows:
        d.text((x1 - 36, y1 - 24), f"{n} songs", font=compose.font(24, "Regular"),
               fill=(170, 170, 180), anchor="rb")
    img.convert("RGB").save(out)
    return out


def _ts(t):
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render(frames, switch_at, total, audio, out, work, progress=lambda f: None, stop=lambda: False):
    """Ghép khung hình theo thời điểm đổi bài + sóng nhạc + thanh tiến độ, tiếng là bản đã mix."""
    work = Path(work)
    lst = work / "frames.txt"
    # mỗi khung lặp lại từng giây: nếu một ảnh kéo dài cả bài, ffmpeg đọc trước tiếng của cả bài và giữ hết khung
    # sóng nhạc trong RAM chờ hình (bài 5 phút ~ vài GB -> bị tắt vì hết RAM)
    lines, prev = [], 0.0
    for i, fr in enumerate(frames):
        end = switch_at[i + 1] if i + 1 < len(frames) else total
        name = Path(fr).resolve().as_posix()
        while end - prev > 1e-3:
            step = min(1.0, end - prev)
            lines.append(f"file '{name}'\nduration {max(0.04, step):.3f}\n")
            prev += step
        prev = end
    lines.append(f"file '{Path(frames[-1]).resolve().as_posix()}'\n")
    lst.write_text("".join(lines), encoding="utf-8")
    vw = W - 200
    # phổ tần 0–8 kHz (phần nhạc có năng lượng), cột to, đối xứng hai bên từ giữa ra
    bars, bw = 48, 18                         # 48 cột mỗi bên, mỗi cột 18 px (13 px màu + 5 px khe)
    half = bars * bw
    # apad: nhánh sóng nhạc không được hết tiếng trước hình (chạm EOF ở cuối làm ffmpeg phình RAM rồi bị tắt)
    flt = (f"[0:v]fps={FPS},format=yuv420p,setsar=1[bg];"
           f"[1:a]apad,aformat=channel_layouts=mono,aresample=16000,showfreqs=s={bars}x{VIS_H}:mode=bar:ascale=cbrt:"
           f"fscale=log:win_size=1024:averaging=2:colors=0xFFB02E:rate={FPS},fps={FPS},"   # fps: đúng 30 khung/giây, không dồn khung chờ
           f"scale={half}:{VIS_H}:flags=neighbor,drawgrid=w={bw}:h={VIS_H * 4}:t=5:c=black,"   # khe giữa các cột
           f"format=rgba,colorkey=black:0.01:0,format=yuva420p,split[r][l0];[l0]hflip[l];[l][r]hstack[vis];"
           f"[bg][vis]overlay={(W - 2 * half) // 2}:{VIS_Y}:format=yuv420[v1];"
           f"color=c=0x50505a:s={vw}x8:r={FPS},format=yuv420p[track];"
           f"color=c=0xFFB02E:s={vw}x8:r={FPS},format=yuv420p[fill];"
           f"[track][fill]overlay=x='-{vw}+{vw}*t/{total:.3f}':y=0:shortest=1[bar];"
           f"[v1][bar]overlay=100:{BAR_Y}:shortest=1:format=yuv420[v]")
    args = [compose.FFMPEG, "-hide_banner", "-v", "error", "-y", "-progress", "pipe:1", "-nostats",
            "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio), "-filter_complex", flt,
            "-map", "[v]", "-map", "1:a", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-threads", "6",                           # ít luồng hơn: đỡ RAM (máy yếu), vẫn nhanh hơn thời gian thật
            "-c:a", "aac", "-b:a", "256k", "-ar", str(compose.SR), "-ac", "2", "-movflags", "+faststart", str(out)]
    # video dài cả tiếng: đọc tiến độ của ffmpeg để báo lên web, huỷ được giữa chừng
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="replace") as proc:
        try:
            for line in proc.stdout:
                if stop():
                    raise InterruptedError
                if line.startswith("out_time_us=") and line[12:].strip().isdigit():
                    progress(min(1.0, int(line[12:]) / 1e6 / total))
        except BaseException:
            proc.kill()                 # huỷ / lỗi: không để ffmpeg chạy tiếp cả tiếng
            raise
        err = proc.stderr.read()
    if proc.returncode:
        raise RuntimeError(f"ffmpeg (mã {proc.returncode}): " + err.strip()[-400:])
    return out


def thumbnail(covers, headline, sub, out):
    """Thumbnail 1280x720: ghép ảnh bìa các bài + chữ to."""
    tw, th = 1280, 720
    img = Image.new("RGB", (tw, th), (14, 14, 24))
    pick = covers[:9]
    cols = 3 if len(pick) > 4 else 2
    rows = -(-len(pick) // cols) or 1
    cw, ch = tw // cols + 1, th // rows + 1
    for i, c in enumerate(pick):
        img.paste(compose._cover(thumb_image(c), cw, ch), ((i % cols) * cw, (i // cols) * ch))
    img = compose._pop(img)
    shade = Image.new("L", (tw, th), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(th):
        sd.line([(0, y), (tw, y)], fill=int(40 + 170 * (y / th) ** 1.4))
    img = Image.composite(Image.new("RGB", (tw, th), (5, 5, 12)), img, shade)
    img = compose._headline(img, headline, (40, 300, tw - 40, 640), angle=-3, max_size=150, lines_max=2)
    d = ImageDraw.Draw(img)
    f = compose._impact(54)
    d.rounded_rectangle([40, 40, 80 + d.textlength(sub, font=f), 124], radius=16, fill=compose.YELLOW)
    d.text((60, 46), sub, font=f, fill=(15, 15, 15))
    img.save(out, quality=92)
    return out


# ---------- tiêu đề / mô tả ----------
def name(country, genre, free=False, version="original"):
    c, g = COUNTRIES[country][2], GENRES[genre][3]
    v = VERSIONS.get(version, VERSIONS["original"])[3]
    if free:
        return " ".join(x for x in ("No Copyright", g or "Music", v) if x)
    if g in ("Pop", "") and c in ("K-Pop", "J-Pop"):
        base = c
    elif g in ("Pop", "") and c == "Vietnamese":
        base = "V-Pop"
    else:
        base = f"{c} {g}".strip()
    return f"{base} {v}".strip()


def copy(mix, tracks, starts, total=None):
    """Tiêu đề, mô tả (tracklist có mốc giờ + ghi nguồn từng bài), tag."""
    label = name(mix["country"], mix["genre"], mix.get("free"), mix.get("version", "original"))
    when = datetime.fromtimestamp(mix.get("created", time.time())).strftime("%B %Y")
    n = len(tracks)
    mash = mix.get("style") == "mashup"
    if mash:
        mins = max(1, round((total or 0) / 60))
        period, what = f"in {mins} minutes", "biggest"
        title = mix.get("custom_title") or f"{label} Mashup {when[-4:]} 🔥 {n} Hit Songs in {mins} Minutes"
    elif mix.get("period", "all") == "all":
        period, what = "of All Time", "most popular"
        title = mix.get("custom_title") or f"Top {n} Most Popular {label} Songs of All Time 🎧 Best {label} Playlist"
    else:
        period = "This Week" if mix.get("period") == "week" else "This Month"
        what = "hottest"
        title = mix.get("custom_title") or f"Top {n} Hot {label} Songs {period} 🎧 {when} Playlist"
    lines = [f"{_ts(s)} {t['artist']} - {t['song']}" if t["artist"] else f"{_ts(s)} {t['song']}"
             for t, s in zip(tracks, starts)]
    credit = [f"- {t['title']} — {t['channel']} https://youtu.be/{t['id']}" for t in tracks]
    hashtag = re.sub(r"[^\w]", "", label.replace(" ", ""))
    desc = ((f"🔥 {n} {what} {label} hits {period} — every chorus beat-matched into one non-stop mashup. "
             f"Hit like & subscribe for a new mashup every week!\n\n") if mash else
            (f"🎧 {n} {what} {label} songs {period.lower()} in one playlist — perfect for studying, working, "
             f"driving or chilling. Hit like & subscribe for a new playlist every week!\n\n"))
    desc += ("🎵 Tracklist:\n" + "\n".join(lines) + "\n\n"
            "🎤 All songs belong to their artists & labels. Original videos:\n" + "\n".join(credit) + "\n\n"
            f"#{hashtag} #playlist #music")
    tags = [f"{label} playlist", f"{label} songs", f"hot {label}", f"{label} {when}", f"top {label} songs",
            f"{label} mix", *([f"{label} mashup", "mashup", "megamix", "nonstop"] if mash else []),
            "playlist", "music playlist", *[t["artist"] for t in tracks if t["artist"]]]
    out, size = [], 0
    for t_ in dict.fromkeys(x.strip() for x in tags if x.strip()):
        if size + len(t_) + 2 > 480:
            break
        out.append(t_)
        size += len(t_) + 2
    return {"title": title[:100], "description": desc[:4900], "tags": ", ".join(out),
            "thumb": (f"{n} HITS {label.upper()} MASHUP" if mash else
                      f"TOP {n} {'BEST' if mix.get('period', 'all') == 'all' else 'HOT'} {label.upper()}"),
            "thumb_sub": (f"NON-STOP {when[-4:]}" if mash else
                          "ALL TIME HITS" if mix.get("period", "all") == "all" else when.upper())}


# ---------- mashup / megamix: điệp khúc của từng bài, cùng nhịp, nối đúng phách ----------
AN_SR, AN_HOP, AN_WIN = 11025, 256, 1024        # phân tích nhịp: mono 11 kHz, mỗi khung ~23 ms
MASH_BEATS, MASH_XFADE = 32, 2                  # mỗi bài 32 phách (8 nhịp), chuyển bài chồng 2 phách
ONSET_LAG = 0.057                               # khung phân tích báo phách sớm ~57 ms (đo bằng tiếng gõ nhịp chuẩn)


def _pcm(path, sr=AN_SR):
    import numpy as np

    p = subprocess.run([compose.FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True)
    if p.returncode:
        raise RuntimeError("ffmpeg: " + p.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(p.stdout, dtype=np.float32)


def _onsets(x):
    """Đường "độ mạnh nhịp" (spectral flux): tăng vọt ở chỗ có tiếng trống / nốt mới."""
    import numpy as np

    n = 1 + max(0, len(x) - AN_WIN) // AN_HOP
    win = np.hanning(AN_WIN).astype(np.float32)
    env = np.zeros(n, dtype=np.float32)
    prev = None
    for s in range(0, n, 2048):                 # từng khối cho đỡ RAM
        idx = np.arange(s, min(n, s + 2048))[:, None] * AN_HOP + np.arange(AN_WIN)[None, :]
        idx = np.minimum(idx, len(x) - 1)
        mag = np.log1p(10 * np.abs(np.fft.rfft(x[idx] * win, axis=1)))
        first = mag[:1] if prev is None else prev
        flux = np.maximum(0, np.diff(np.vstack([first, mag]), axis=0)).sum(axis=1)
        env[s:s + len(flux)] = flux
        prev = mag[-1:]
    env -= np.convolve(env, np.ones(16) / 16, mode="same")          # bỏ phần nền, giữ các đỉnh
    return np.maximum(env, 0)


def tempo(env):
    """BPM (70–180, ưu tiên gần 120) từ tự tương quan của đường độ mạnh nhịp."""
    import numpy as np

    fps = AN_SR / AN_HOP
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lags = np.arange(int(fps * 60 / 180), int(fps * 60 / 70) + 1)
    bpms = 60 * fps / lags
    score = ac[lags] * np.exp(-0.5 * (np.log2(bpms / 120) / 0.9) ** 2)
    lag = lags[int(np.argmax(score))]
    # tinh chỉnh lag lẻ (nội suy parabol) cho BPM chính xác hơn
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        d = (a - c) / (2 * (a - 2 * b + c)) if (a - 2 * b + c) else 0
        return 60 * fps / (lag + float(np.clip(d, -0.5, 0.5)))
    return 60 * fps / lag


def beat_grid(env, bpm):
    """BPM chính xác + thời điểm các phách (giây): thử BPM quanh giá trị ước lượng (±1,5%) và độ lệch pha,
    chọn lưới đều trùng nhiều đỉnh nhịp nhất (lệch BPM 0,5% là sau 1 phút phách trôi cả trăm ms)."""
    import numpy as np

    fps = AN_SR / AN_HOP
    best = (-1.0, bpm, 0.0)
    for b in bpm * (1 + np.linspace(-0.015, 0.015, 31)):
        period = 60 * fps / b
        pos = np.arange(0, len(env) - 1 - period, period)
        for ph in np.arange(0, period, 0.5):
            score = env[np.round(pos + ph).astype(int)].sum()
            if score > best[0]:
                best = (score, b, ph)
    _, b, ph = best
    return float(b), np.arange(ph, len(env) - 1, 60 * fps / b) / fps + ONSET_LAG


def heat_peak(heatmap, duration):
    """Giữa đoạn được tua lại nhiều nhất ("Most replayed"), bỏ 10% đầu bài."""
    bins = [h for h in heatmap or [] if h[0] > duration * 0.1]
    if not bins:
        return None
    h = max(bins, key=lambda h: h[2])
    return (h[0] + h[1]) / 2


def chorus(path, peak=None, beats=MASH_BEATS):
    """Đoạn điệp khúc `beats` phách: quanh đỉnh "Most replayed" (nếu có), chọn đoạn to + dày nhất, bắt đầu đúng phách.
    -> (bắt đầu, độ dài giây, bpm)"""
    import numpy as np

    x = _pcm(path)
    dur = len(x) / AN_SR
    env = _onsets(x)
    bpm, grid = beat_grid(env, tempo(env))
    length = beats * 60 / bpm
    # độ to theo từng giây
    sec = AN_SR
    rms = np.sqrt((x[: len(x) // sec * sec].reshape(-1, sec).astype(np.float64) ** 2).mean(axis=1))
    lo, hi = (peak - 14, peak + 6) if peak else (dur * 0.2, dur * 0.8 - length)
    lo, hi = max(dur * 0.08, lo), min(dur - length - 1, hi)
    cands = [b for b in grid if lo <= b <= hi] or [b for b in grid if b <= dur - length - 1] or [0.0]
    loud = lambda b: rms[int(b): int(b + length)].mean() if int(b + length) <= len(rms) and int(b) < len(rms) else 0
    start = max(cands, key=loud)
    # bắt đầu ở phách mạnh nhất trong 4 phách quanh đó (thường là phách đầu nhịp)
    fps = AN_SR / AN_HOP
    near = [b for b in grid if abs(b - start) < 2.2 * 60 / bpm and b + length <= dur]
    if near:
        start = max(near, key=lambda b: env[max(0, int(b * fps) - 1): int(b * fps) + 2].sum())
    return float(start), float(length), float(bpm)


def _fold(b):
    while b < 85:
        b *= 2
    while b > 170:
        b /= 2
    return b


def common_bpm(bpms):
    """Nhịp chung: trung vị, sau khi đưa các BPM về cùng khoảng 85–170 (gấp đôi / chia đôi)."""
    fold = sorted(_fold(b) for b in bpms)
    return fold[len(fold) // 2] if fold else 120.0


def stretch_ratio(bpm, target):
    """Hệ số đổi nhịp (giữ nguyên tông) để bài khớp nhịp chung; chọn cách gần 1 nhất (kể cả nửa / gấp đôi nhịp).
    None nếu phải đổi quá 12% (nghe méo)."""
    best = min((target / (bpm * k) for k in (0.5, 1, 2)), key=lambda r: abs(math.log(r)))
    return best if 1 / MAX_STRETCH <= best <= MAX_STRETCH else None


MAX_STRETCH = 1.12


def tempo_groups(bpms):
    """Chia các bài thành nhóm nhịp như DJ: mỗi nhóm một nhịp chung mà mọi bài trong nhóm đổi nhịp không quá 12%;
    nhóm đông nhất trước khi chọn. -> [(nhịp chung, [chỉ số bài theo thứ tự cũ])] xếp từ chậm đến nhanh."""
    left = list(range(len(bpms)))
    groups = []
    while left:
        best = None
        for cand in sorted({round(_fold(bpms[i]), 1) for i in left}):
            # nhịp chung = trung vị các bài khớp với ứng viên, kiểm tra lại cho chắc
            near = [i for i in left if stretch_ratio(bpms[i], cand)]
            tgt = common_bpm([bpms[i] for i in near])
            members = [i for i in left if stretch_ratio(bpms[i], tgt)] or [left[0]]
            if not best or len(members) > len(best[1]):
                best = (tgt if len(members) > 1 or stretch_ratio(bpms[members[0]], tgt) else _fold(bpms[members[0]]),
                        members)
        groups.append(best)
        left = [i for i in left if i not in best[1]]
    return sorted(groups, key=lambda g: g[0])


def mash_segment(path, start, length, ratio, out):
    """Cắt đoạn điệp khúc, đổi nhịp về nhịp chung (rubberband: không đổi tông)."""
    af = f"rubberband=tempo={ratio:.5f}," if ratio and abs(ratio - 1) > 0.003 else ""
    compose.run(["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(path), "-af",
                 f"{af}aresample={compose.SR}", "-ac", "2", "-c:a", "flac", str(out)])
    return out
