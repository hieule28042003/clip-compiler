"""Web ghép video "khoảnh khắc hay" từ YouTube: từ khoá -> tìm video -> tìm đoạn gây cười -> duyệt + bình luận -> MP4.

Chạy: .venv/bin/uvicorn server:app --port 8790
"""
import hashlib
import io
import json
import math
import random
import re
import shutil
import threading
import time
from datetime import date, datetime, timedelta
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from clipmaker import compose, moments
from clipmaker import caption, copywrite, dub, lyrics, music, mv, seo, social, stream, trends, voice, ytapi
from clipmaker import youtube as yt

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
INTRO_LEN, OUTRO_LEN = 3.5, 3.0
CLIP_MIN, CLIP_MAX = 3.0, 30.0
SHORTS, SHORTS_MAX = 3, 10        # số Shorts mặc định (cắt kèm video dài) / tối đa một dự án
STREAM_MIN, STREAM_MAX = 15.0, 90.0   # Short cắt từ stream dài hơn: cần phần dẫn + phản ứng sau khoảnh khắc
STREAM_SHORTS = 20                    # số Shorts tối đa từ một buổi stream
STREAM_LINKS = 10                     # số buổi stream tối đa một dự án
WORKERS = 3                     # tải / phân tích song song vừa phải để YouTube không chặn
# Mỗi lần quét đổi cách tìm một chút để ra video khác nhau dù cùng từ khoá.
VARIANTS = ("", "funny", "funniest", "best moments", "compilation", "try not to laugh", "hilarious", "awkward",
            "bloopers", "viral")
USED_PENALTY = 0.35             # đoạn đã đăng ở video trước: hạ điểm mạnh

app = FastAPI(title="Clip Compiler")


@app.middleware("http")
async def _no_stale_ui(request: Request, call_next):
    """Giao diện (html / js / css) luôn hỏi lại server (ETag, không đổi thì 304): cập nhật app xong không phải
    xoá cache trình duyệt, tránh trang mới chạy cùng app.js cũ."""
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp
JOBS, LOCK = {}, threading.Lock()
HIST_LOCK = threading.Lock()      # nhiều luồng dựng cùng lúc cùng ghi history.json


def folder(pid):
    f = DATA / pid
    if not pid.isalnum() or not (f / "project.json").exists():
        raise HTTPException(404, "Không tìm thấy dự án")
    return f


def load(pid):
    return json.loads((folder(pid) / "project.json").read_text(encoding="utf-8"))


def save(p):
    path = DATA / p["id"] / "project.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _history():
    path = DATA / "history.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"clips": {}, "videos": {}}


def _hkey(vid, start):
    return f"{vid}:{int(start // 15)}"


def _remember(plan):
    """Ghi lại các đoạn đã dựng để lần sau (kể cả cùng từ khoá) ưu tiên đoạn mới."""
    with HIST_LOCK:
        h = _history()
        for c, _ in plan:
            k = _hkey(c["vid"], c["start"])
            h["clips"][k] = h["clips"].get(k, 0) + 1
            h["videos"][c["vid"]] = h["videos"].get(c["vid"], 0) + 1
        tmp = DATA / "history.tmp"
        tmp.write_text(json.dumps(h), encoding="utf-8")
        tmp.replace(DATA / "history.json")


def _clip(p, cid):
    c = next((c for c in p["clips"] if c["id"] == cid), None)
    if not c:
        raise HTTPException(404, "Không có đoạn này")
    return c


def _plan(p):
    """Các đoạn sẽ dựng theo thứ tự, kèm số hiển thị (đếm ngược: đoạn cuối là #1)."""
    by_id = {c["id"]: c for c in p["clips"]}
    chosen = [by_id[i] for i in p["order"] if i in by_id]
    n = len(chosen)
    return [(c, (n - k) if p.get("countdown", True) else k + 1) for k, c in enumerate(chosen)]


def _voice_len(f, c):
    return c.get("voice_len", 0.0) + 0.6 if c.get("voice") and (f / c["voice"]).exists() else 0.0


def _stamp(p):
    keys = ("vid", "start", "end", "caption", "voice", "voice_at")
    return hashlib.md5(json.dumps([p.get("title"), p.get("countdown", True), p.get("credits", True),
                                   p.get("subtitles", True), p.get("thumb_text"),
                                   [[c.get(k) for k in keys] for c, _ in _plan(p)]]).encode()).hexdigest()


def _done(p, f):
    if p.get("kind") == "shorts":
        return any((f / x["file"]).exists() for x in p.get("shorts_out", []))
    return (f / "output.mp4").exists()


def view(p):
    f = DATA / p["id"]
    job = JOBS.get(p["id"])
    plan = _plan(p)
    total = sum(c["end"] - c["start"] + _voice_len(f, c) for c, _ in plan) + (INTRO_LEN + OUTRO_LEN if plan else 0)
    pool = sum(c["end"] - c["start"] for c in p["clips"]) + INTRO_LEN + OUTRO_LEN
    return {"project": p, "videos": p.get("videos", {}), "pool": round(pool, 1),
            "plan": [{"id": c["id"], "number": n} for c, n in plan],
            "total": round(total, 1),
            "output": _done(p, f) and p.get("output_stamp") == _stamp(p),
            "job": {k: v for k, v in job.items() if k != "stop"} if job else None}


def _new_job(pid, kind):
    with LOCK:
        cur = JOBS.get(pid)
        if cur and not cur["done"]:
            raise HTTPException(409, "Đang có việc khác chạy cho dự án này")
        job = {"kind": kind, "progress": 0.0, "stage": "", "error": None, "done": False, "stop": False,
               "result": None}
        JOBS[pid] = job
    return job


def _start_job(pid, kind, work):
    job = _new_job(pid, kind)

    def runner():
        try:
            job["result"] = work(job)
        except InterruptedError:
            job["error"] = "Đã huỷ"
        except Exception as e:      # báo lỗi lên giao diện
            job["error"] = str(e) or e.__class__.__name__
        finally:
            job["done"] = True

    threading.Thread(target=runner, daemon=True).start()
    return {k: v for k, v in job.items() if k != "stop"}


class _Part:
    """Một bước của job nhiều bước: progress 0–1 của bước này quy về khoảng [lo, hi] của job chính."""

    def __init__(self, job, lo, hi):
        self.job, self.lo, self.hi = job, lo, hi

    def __getitem__(self, k):
        return self.job[k]

    def get(self, k, default=None):
        return self.job.get(k, default)

    def __setitem__(self, k, v):
        self.job[k] = self.lo + (self.hi - self.lo) * v if k == "progress" else v


# ---------- quét: tìm video + tìm khoảnh khắc ----------
class CreateReq(BaseModel):
    keyword: str
    videos: int = 30
    length: float = 10.0
    minutes: int = 15
    comments: bool = True
    seed_videos: list[str] = []      # "Tạo video tương tự" từ trang Xu hướng: quét các video đang hot này trước
    ref: str = ""                    # ghi chú nguồn ý tưởng (chủ đề / từ khoá hot)
    auto: bool = False               # ⚡ làm hết: quét -> chọn -> giọng đọc -> dựng -> Shorts, không cần duyệt
    voiceover: bool = False          # lời bình bằng giọng đọc tự động cho các đoạn top
    voice: str = voice.DEFAULT_VOICE
    takes: int = 3                   # số đoạn có lời bình
    shorts: bool = True              # cắt thêm Shorts dọc từ các đoạn top khi dựng
    kind: str = "long"               # "shorts": chỉ làm Shorts (không dựng video dài)
    count: int = SHORTS              # số Shorts
    reply: bool = False              # Shorts "trả lời bình luận": khung bình luận hài + đúng đoạn được nhắc + câu đáp
    stream: str = ""                 # link một buổi stream dài: cắt Shorts từ các đoạn hay của đúng video này
    streams: list[str] = []          # nhiều link stream: video dài / Shorts ghép từ các đoạn hay của các buổi stream


@app.post("/api/projects")
def create(req: CreateReq):
    if req.stream.strip() or any(x.strip() for x in req.streams):
        p = _new_stream(req)
        pid = p["id"]
        if req.auto:
            _start_job(pid, "auto", lambda job: _auto_stream(pid, job))
        else:
            _start_job(pid, "scan", lambda job: _scan_stream(pid, job))
        return view(p)
    p = _new_project(req)
    pid, count = p["id"], min(80, max(5, req.videos))
    if req.auto:
        _start_job(pid, "auto", lambda job: _auto(pid, count, job))
    else:
        _start_job(pid, "scan", lambda job: _scan(pid, count, job, fresh=True))
    return view(p)


def _new_project(req: CreateReq):
    kw = req.keyword.strip()
    if not kw:
        raise HTTPException(400, "Nhập từ khoá cần tìm")
    if not CLIP_MIN <= req.length <= CLIP_MAX:
        raise HTTPException(400, "Độ dài mỗi đoạn phải từ 3 đến 30 giây")
    only = req.kind == "shorts"
    pid = uuid.uuid4().hex[:12]
    (DATA / pid).mkdir(parents=True)
    p = {"id": pid, "created": time.time(), "keyword": kw, "title": kw.title(), "length": req.length,
         "kind": "shorts" if only else "long", "count": min(SHORTS_MAX, max(1, req.count)),
         "minutes": min(30, max(3, req.minutes)), "countdown": True, "credits": True, "comments": req.comments,
         "subtitles": True, "thumb_text": "", "ref": req.ref[:200], "shorts": req.shorts,
         "voiceover": req.voiceover, "voice": req.voice if req.voice in voice.VOICES else voice.DEFAULT_VOICE,
         "takes": min(10, max(1, req.takes)), "auto": req.auto,
         "seed_videos": [v for v in req.seed_videos if re.fullmatch(r"[\w-]{11}", v)][:15],
         "videos": {}, "clips": [], "order": []}
    if only:                             # Shorts: đoạn hay nhất trước, không đếm ngược, không lời bình
        p.update(countdown=False, shorts=True, voiceover=False)
        if req.reply:                    # cần bình luận có mốc giờ của video gốc
            p.update(reply=True, comments=True, title="💬 " + p["title"])
    save(p)
    return p


def _new_stream(req: CreateReq):
    vids = []
    for url in [req.stream, *req.streams]:
        if not url.strip():
            continue
        try:
            vid = stream.video_id(url)
        except ValueError:
            raise HTTPException(400, f"Link không hợp lệ: {url.strip()[:80]}") from None
        if vid not in vids:
            vids.append(vid)
    if len(vids) > STREAM_LINKS:
        raise HTTPException(400, f"Tối đa {STREAM_LINKS} buổi stream một lần")
    if not STREAM_MIN <= req.length <= STREAM_MAX:
        raise HTTPException(400, "Mỗi đoạn cắt từ stream phải dài từ 15 đến 90 giây")
    only = req.kind == "shorts"
    pid = uuid.uuid4().hex[:12]
    (DATA / pid).mkdir(parents=True)
    p = {"id": pid, "created": time.time(), "keyword": "stream", "title": f"📺 Stream {vids[0]}", "length": req.length,
         "kind": "shorts" if only else "long", "stream": vids[0], "streams": vids,
         "count": min(STREAM_SHORTS if only else SHORTS_MAX, max(1, req.count)), "minutes": min(30, max(3, req.minutes)),
         "countdown": False, "credits": True, "comments": True, "subtitles": True, "thumb_text": "", "ref": "",
         "shorts": True if only else req.shorts, "voiceover": False, "voice": voice.DEFAULT_VOICE, "takes": 1,
         "auto": req.auto, "seed_videos": [], "videos": {}, "clips": [], "order": []}
    save(p)
    return p


def _stream_one(vid, f, n, length, job):
    """Một buổi stream: chat phát lại + độ to âm thanh + "Most replayed" + bình luận có mốc giờ -> các đoạn hay."""
    work = _fresh_dir(f / "stream")
    job["stage"] = "Đang đọc thông tin stream"
    info = yt.analyze(vid, 150)
    if info["duration"] < 10 * 60:
        raise RuntimeError("Video ngắn hơn 10 phút — chế độ này dành cho stream / video dài")
    notes = []
    job["progress"] = 0.08
    job["stage"] = "Đang tải chat phát lại"
    try:
        chat = stream.fetch_chat(vid, work)
    except Exception:  # noqa: BLE001 - chat là tín hiệu phụ: lỗi thì chấm bằng tín hiệu khác
        chat = []
    if not chat:
        notes.append("không có chat phát lại")
    if job["stop"]:
        raise InterruptedError
    job["progress"] = 0.3
    job["stage"] = f"Đang tải tiếng & đo độ to ({info['duration'] // 3600:.0f} giờ {info['duration'] % 3600 // 60:.0f} phút stream, khoảng 1–3 phút)"
    try:
        db = stream.fetch_loudness(vid, work, lambda: job["stop"])
    except InterruptedError:
        raise
    except Exception:  # noqa: BLE001
        db = None
        notes.append("không đo được âm thanh")
    shutil.rmtree(work, ignore_errors=True)
    if not chat and db is None and not info["heatmap"] and not info["marks"]:
        raise RuntimeError("Không lấy được dữ liệu nào của stream (chat, âm thanh, Most replayed, bình luận)")
    job["progress"] = 0.9
    job["stage"] = "Đang chấm điểm từng giây"
    score, parts = stream.curve(info["duration"], chat, db, info["heatmap"], info["marks"])
    found = stream.highlights(info["duration"], score, parts, db, chat, info["marks"], length, n)
    v = {k: info.get(k) for k in ("id", "title", "channel", "duration", "views", "tags")}
    v.update(caption_url=info.get("caption_native") or info.get("caption_url"), heat=bool(info["heatmap"]))
    sig = {"chat": len(chat), "loud": db is not None, "heat": bool(info["heatmap"]), "marks": len(info["marks"]),
           "notes": notes}
    return v, found, sig


def _scan_stream(pid, job):
    """Các buổi stream của dự án -> các đoạn hay (đủ cho số Shorts / thời lượng video dài)."""
    p = load(pid)
    vids, f = p.get("streams") or [p["stream"]], folder(pid)
    if p["kind"] == "shorts":
        need = p["count"]
    else:                                # video dài: đủ thời lượng, dư 60% để còn chọn / bỏ
        need = math.ceil((p["minutes"] * 60 - INTRO_LEN - OUTRO_LEN) / p["length"] * 1.6)
    n = max(8, math.ceil(max(12, need * 2 if p["kind"] == "shorts" else need) / len(vids)) + 2)
    videos, clips, sigs, errors = {}, [], [], []
    for i, vid in enumerate(vids):
        part = _Part(job, i / len(vids), (i + 1) / len(vids))
        try:
            v, found, sig = _stream_one(vid, f, n, p["length"], part)
        except InterruptedError:
            raise
        except Exception as e:  # noqa: BLE001 - một buổi lỗi (bị xoá, riêng tư…): vẫn dùng các buổi khác
            if len(vids) == 1:
                raise
            errors.append(f"{vid}: {str(e)[:120]}")
            continue
        if len(vids) > 1:
            job["stage"] = f"Xong stream {i + 1}/{len(vids)}"
        videos[vid] = v
        if len(vids) > 1:
            sig["notes"] = [f"buổi {i + 1}: {n_}" for n_ in sig["notes"]]
        sigs.append(sig)
        clips += [{"id": f"{vid}_{h['peak']}", "vid": vid, **h, "caption": "", "include": False} for h in found]
    if not clips:
        raise RuntimeError("Không tìm được đoạn nào nổi bật trong stream" + (f" ({'; '.join(errors)})" if errors else ""))
    p = load(pid)
    p["videos"] = videos
    p["clips"] = sorted(clips, key=lambda c: -c["score"])
    chans = list(dict.fromkeys(v["channel"] for v in videos.values() if v.get("channel")))
    first = next(iter(videos.values()))
    p["keyword"] = ", ".join(chans[:3]) or first["title"]
    if p["kind"] == "shorts":
        p["title"] = "📺 " + first["title"][:90]
    else:                                # video dài: tên kênh + "stream highlights" (tiêu đề YouTube viết riêng sau)
        p["title"] = (f"{chans[0]} – Best Stream Moments" if chans else first["title"][:90]) if len(videos) > 1 \
            else first["title"][:90]
    p["signals"] = {"chat": sum(x["chat"] for x in sigs), "loud": any(x["loud"] for x in sigs),
                    "heat": any(x["heat"] for x in sigs), "marks": sum(x["marks"] for x in sigs),
                    "notes": list(dict.fromkeys(n_ for x in sigs for n_ in x["notes"])) + errors}
    _autoselect(p, p["minutes"])
    save(p)
    job["progress"] = 1.0
    return {"videos": len(videos), "clips": len(clips), "failed": len(errors), "chat": p["signals"]["chat"]}


def _auto_stream(pid, job):
    _scan_stream(pid, _Part(job, 0.0, 0.4))
    return _build(pid, _Part(job, 0.4, 1.0))


def _planned(p):
    return sum(c["end"] - c["start"] for c, _ in _plan(p)) + INTRO_LEN + OUTRO_LEN


def _enough(p):
    if p.get("kind") == "shorts":
        return len(p["order"]) >= p.get("count", SHORTS)
    return _planned(p) >= p["minutes"] * 60 * 0.85


def _auto(pid, count, job):
    """⚡ Làm tự động: quét, chọn đủ thời lượng (quét thêm tối đa 2 lần), giọng đọc, dựng + Shorts."""
    _scan(pid, count, _Part(job, 0.0, 0.3), fresh=True)
    for k in range(2):
        p = load(pid)
        if _enough(p):
            break
        try:
            _scan(pid, 20, _Part(job, 0.3 + 0.05 * k, 0.35 + 0.05 * k), fresh=False)
        except RuntimeError:            # hết video mới: dựng với những gì đang có
            break
        p = load(pid)
        _autoselect(p, p["minutes"])
        save(p)
    note = ""
    if load(pid).get("voiceover") and load(pid).get("kind") != "shorts":
        try:
            _tts(pid, _Part(job, 0.4, 0.45))
        except Exception as e:          # mất mạng / dịch vụ giọng đọc lỗi: vẫn dựng, không có lời bình
            note = f"Không tạo được giọng đọc ({e}), video không có lời bình."
    res = _build(pid, _Part(job, 0.45, 1.0))
    return {**res, "note": note}


class MoreReq(BaseModel):
    videos: int = 20


@app.post("/api/projects/{pid}/more")
def scan_more(pid: str, req: MoreReq):
    """Quét thêm video mới (bỏ qua video đã quét) khi chưa đủ đoạn cho thời lượng mong muốn."""
    if load(pid).get("stream"):
        raise HTTPException(400, "Dự án cắt từ stream chỉ dùng một video")
    return _start_job(pid, "scan", lambda job: _scan(pid, min(80, max(5, req.videos)), job, fresh=False))


def _scan(pid, count, job, fresh):
    p = load(pid)
    keyword, length, comments = p["keyword"], p["length"], p.get("comments", True)
    job["stage"] = "Đang tìm video trên YouTube"
    known = set(p["videos"]) | set(p.get("skipped", []))
    hist = _history()
    rnd = random.Random()
    pool, seen = [], set()
    for extra in ["", *rnd.sample(VARIANTS[1:], 2)]:         # từ khoá gốc + 2 biến thể ngẫu nhiên
        q = f"{keyword} {extra}".strip()
        for v in yt.search(q, len(known) + count):
            if v["id"] not in known and v["id"] not in seen:
                seen.add(v["id"])
                pool.append(v)
    # video chưa từng dùng trước, rồi trộn ngẫu nhiên (vẫn ưu tiên nhiều lượt xem)
    pool.sort(key=lambda v: (hist["videos"].get(v["id"], 0), -rnd.random() * (1 + v["views"] / 1e6)))
    seeds = [{"id": v} for v in p.get("seed_videos", []) if fresh and v not in known]
    found = seeds + [v for v in pool if v["id"] not in {x["id"] for x in seeds}][:max(0, count - len(seeds))]
    if not found:
        raise RuntimeError("Không tìm thấy video mới nào cho từ khoá này" if known else
                           "Không tìm thấy video nào cho từ khoá này")
    videos, clips, failed, skipped = {}, [], 0, []
    job["stage"] = f"Đang tìm khoảnh khắc hay trong {len(found)} video"

    def one(v):
        if job["stop"]:
            raise InterruptedError
        return yt.analyze(v["id"], 120 if comments else 0)

    with ThreadPoolExecutor(WORKERS) as ex:
        for i, (v, fut) in enumerate([(v, ex.submit(one, v)) for v in found]):
            try:
                info = fut.result()
            except InterruptedError:
                raise
            except Exception:
                failed += 1
                skipped.append(v["id"])
                continue
            finally:
                job["progress"] = (i + 1) / len(found)
            videos[v["id"]] = {k: info.get(k) for k in ("id", "title", "channel", "duration", "views", "caption_url", "tags")}
            videos[v["id"]]["heat"] = bool(info["heatmap"])
            found_here = moments.find(info, length, per_video=4)
            if not found_here:
                skipped.append(v["id"])
            for m in found_here:
                clips.append({"id": f"{v['id']}_{m['peak']}", "vid": v["id"], **m, "caption": "", "include": False})
    if not clips and fresh:
        raise RuntimeError("Không tìm được khoảnh khắc nào (các video không có dữ liệu \"Most replayed\" / bình luận)")
    # điểm so được giữa các video: cộng nhẹ theo lượt xem
    for c in clips:
        views = videos[c["vid"]]["views"]
        c["score"] = round(c["score"] + 0.15 * min(1.0, views / 5_000_000), 3)
        if hist["clips"].get(_hkey(c["vid"], c["start"])):
            c["score"] = round(c["score"] * USED_PENALTY, 3)
            c["reasons"].append("♻ Đã dùng ở video trước")
    p = load(pid)
    p["videos"].update(videos)
    p["skipped"] = p.get("skipped", []) + skipped
    have = {c["id"] for c in p["clips"]}
    p["clips"] = sorted(p["clips"] + [c for c in clips if c["id"] not in have], key=lambda c: -c["score"])
    if fresh:
        _autoselect(p, p["minutes"])
    save(p)
    return {"videos": len(videos), "clips": len(clips), "failed": failed}


def _autoselect(p, minutes):
    """Chọn đoạn điểm cao nhất tới đủ thời lượng; đoạn hay nhất để cuối (#1), tối đa 3 đoạn mỗi video.
    Dự án Shorts: `count` đoạn hay nhất, mỗi video gốc 1 đoạn, hay nhất trước."""
    if p.get("kind") == "shorts":
        rnd = random.Random()
        picked, vids = [], set()
        if p.get("reply"):               # trả lời bình luận: bình luận hài / nhiều like nhất, đoạn phải có bình luận
            key = lambda c: -copywrite.reply_score(c.get("comment", ""), c.get("c_likes", 0)) * rnd.uniform(0.9, 1.1)
            pool = [c for c in p["clips"] if copywrite.reply_score(c.get("comment", ""), c.get("c_likes", 0)) >= 0]
        else:
            key = lambda c: -c["score"] * rnd.uniform(0.85, 1.15)
            pool = p["clips"]
        if p.get("stream"):              # cắt từ một stream: nhiều đoạn cùng video, hay nhất trước
            key = lambda c: -c["score"]
        for c in sorted(pool, key=key):
            if (p.get("stream") or c["vid"] not in vids) and len(picked) < p.get("count", SHORTS):
                picked.append(c)
                vids.add(c["vid"])
        for c in p["clips"]:
            c["include"] = c in picked
        p["order"] = [c["id"] for c in picked]
        return
    budget = minutes * 60 - INTRO_LEN - OUTRO_LEN
    picked, per_video, used = [], {}, 0.0
    rnd = random.Random()            # mỗi lần chọn lại hơi khác: điểm nhân hệ số ngẫu nhiên
    pool = sorted(p["clips"], key=lambda c: -c["score"] * rnd.uniform(0.7, 1.3))
    if p.get("stream"):              # nhiều buổi stream: lần lượt đoạn hay nhất của từng buổi (buổi có chat điểm cao
        rank, seen = {}, {}          # hơn hẳn buổi không có, so thẳng thì một buổi chiếm hết)
        for c in pool:
            rank[c["id"]] = seen[c["vid"]] = seen.get(c["vid"], -1) + 1
        pool.sort(key=lambda c: rank[c["id"]])
    for c in pool:
        if used >= budget:
            break
        if per_video.get(c["vid"], 0) >= 3 and not p.get("stream"):
            continue
        picked.append(c)
        per_video[c["vid"]] = per_video.get(c["vid"], 0) + 1
        used += c["end"] - c["start"]
    # đếm ngược: điểm thấp -> cao, nhưng mở đầu bằng một đoạn mạnh để giữ người xem
    asc = sorted(picked, key=lambda c: c["score"])
    if len(asc) > 3:
        asc.insert(0, asc.pop(-2))
    if p.get("stream") and not p.get("countdown", True):     # tổng hợp stream: kể lại theo thứ tự thời gian
        streams = p.get("streams") or [p["stream"]]
        asc = sorted(picked, key=lambda c: (streams.index(c["vid"]) if c["vid"] in streams else 0, c["start"]))
    for c in p["clips"]:
        c["include"] = False
    for c in asc:
        c["include"] = True
    p["order"] = [c["id"] for c in asc]


@app.get("/api/projects")
def list_projects():
    out = []
    for pj in sorted(DATA.glob("*/project.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        p = json.loads(pj.read_text(encoding="utf-8"))
        out.append({"id": p["id"], "title": p["title"], "keyword": p["keyword"], "created": p["created"],
                    "clips": len(p["order"]), "done": _done(p, pj.parent), "kind": p.get("kind", "long")})
    return out


# ---------- nghiên cứu xu hướng ----------
TRENDS = "_trends"                # khoá job (không phải dự án)


class TrendReq(BaseModel):
    seed: str = "funny moments"
    depth: int = 10
    period: str = "month"            # "week": video đăng 7 ngày qua, "month": 30 ngày qua


@app.post("/api/trends")
def start_trends(req: TrendReq):
    seed = req.seed.strip()
    if not seed:
        raise HTTPException(400, "Nhập lĩnh vực cần nghiên cứu")

    def work(job):
        res = trends.research(seed, min(20, max(4, req.depth)), job, "week" if req.period == "week" else "month")
        tmp = DATA / "trends.tmp.json"
        tmp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        tmp.replace(DATA / "trends.json")
        job["progress"] = 1.0
        return {"keywords": len(res["keywords"]), "topics": len(res["topics"])}

    return _start_job(TRENDS, "trends", work)


@app.post("/api/trends/cancel")
def cancel_trends():
    return cancel(TRENDS)


@app.get("/api/trends")
def get_trends():
    f = DATA / "trends.json"
    job = JOBS.get(TRENDS)
    return {"result": json.loads(f.read_text(encoding="utf-8")) if f.exists() else None,
            "job": {k: v for k, v in job.items() if k != "stop"} if job else None}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return view(load(pid))


class Settings(BaseModel):
    title: str | None = None
    countdown: bool | None = None
    credits: bool | None = None
    subtitles: bool | None = None
    thumb_text: str | None = None
    order: list[str] | None = None
    shorts: bool | None = None


@app.put("/api/projects/{pid}")
def update(pid: str, s: Settings):
    p = load(pid)
    if s.title is not None:
        p["title"] = s.title.strip()[:100] or p["keyword"].title()
    if s.countdown is not None:
        p["countdown"] = s.countdown
    if s.credits is not None:
        p["credits"] = s.credits
    if s.subtitles is not None:
        p["subtitles"] = s.subtitles
    if s.thumb_text is not None:
        p["thumb_text"] = s.thumb_text.strip()[:60]
    if s.shorts is not None:
        p["shorts"] = s.shorts
    if s.order is not None:
        ids = {c["id"] for c in p["clips"]}
        if set(s.order) - ids or len(set(s.order)) != len(s.order):
            raise HTTPException(400, "Thứ tự không hợp lệ")
        p["order"] = s.order
        for c in p["clips"]:
            c["include"] = c["id"] in s.order
    save(p)
    return view(p)


class ClipEdit(BaseModel):
    include: bool | None = None
    start: float | None = None
    end: float | None = None
    caption: str | None = None


@app.put("/api/projects/{pid}/clips/{cid}")
def edit_clip(pid: str, cid: str, e: ClipEdit):
    p = load(pid)
    c = _clip(p, cid)
    dur = p["videos"][c["vid"]]["duration"]
    start = c["start"] if e.start is None else round(max(0.0, e.start), 1)
    end = c["end"] if e.end is None else round(min(dur, e.end), 1)
    lo, hi = (STREAM_MIN, STREAM_MAX) if p.get("stream") else (CLIP_MIN, CLIP_MAX)
    if not lo <= end - start <= hi:
        raise HTTPException(400, f"Mỗi đoạn phải dài từ {lo:.0f} đến {hi:.0f} giây")
    c["start"], c["end"] = start, end
    if e.caption is not None:
        c["caption"] = e.caption.strip()[:120]
    if e.include is not None and e.include != c["include"]:
        c["include"] = e.include
        p["order"] = [i for i in p["order"] if i != cid] + ([cid] if e.include else [])
    save(p)
    return view(p)


class AutoReq(BaseModel):
    minutes: int = 15
    count: int | None = None                # dự án Shorts: số Shorts


@app.post("/api/projects/{pid}/autoselect")
def autoselect(pid: str, req: AutoReq):
    p = load(pid)
    p["minutes"] = min(30, max(1, req.minutes))
    if req.count:
        p["count"] = min(STREAM_SHORTS if p.get("stream") else SHORTS_MAX, max(1, req.count))
    _autoselect(p, p["minutes"])
    save(p)
    return view(p)


# ---------- bình luận bằng giọng ----------
@app.post("/api/projects/{pid}/clips/{cid}/voice")
def upload_voice(pid: str, cid: str, file: UploadFile = File(...)):
    p = load(pid)
    c = _clip(p, cid)
    f = folder(pid)
    (f / "voice").mkdir(exist_ok=True)
    raw = f / "voice" / f"{cid}.upload"
    with open(raw, "wb") as out:
        shutil.copyfileobj(file.file, out)
    wav = f / "voice" / f"{cid}.wav"
    try:
        compose.run(["-i", str(raw), "-ac", "1", "-ar", "48000", str(wav)])
    finally:
        raw.unlink(missing_ok=True)
    length = compose.probe(wav)["duration"]
    if length < 0.5:
        wav.unlink(missing_ok=True)
        raise HTTPException(400, "Bản thu quá ngắn")
    if length > 60:
        wav.unlink(missing_ok=True)
        raise HTTPException(400, "Bản thu tối đa 60 giây")
    c.update(voice=f"voice/{cid}.wav", voice_len=round(length, 2), voice_at=time.time())
    save(p)
    return view(p)


@app.delete("/api/projects/{pid}/clips/{cid}/voice")
def delete_voice(pid: str, cid: str):
    p = load(pid)
    c = _clip(p, cid)
    if c.get("voice"):
        (folder(pid) / c["voice"]).unlink(missing_ok=True)
    for k in ("voice", "voice_len", "voice_at"):
        c.pop(k, None)
    save(p)
    return view(p)


@app.get("/api/projects/{pid}/clips/{cid}/voice.wav")
def get_voice(pid: str, cid: str):
    c = _clip(load(pid), cid)
    path = folder(pid) / (c.get("voice") or "none")
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


class TtsReq(BaseModel):
    voice: str = voice.DEFAULT_VOICE
    takes: int = 3


@app.post("/api/projects/{pid}/tts")
def make_tts(pid: str, req: TtsReq):
    """Tạo lại lời bình bằng giọng đọc cho các đoạn top (bản thu tay của bạn được giữ nguyên)."""
    p = load(pid)
    if not p["order"]:
        raise HTTPException(400, "Chưa chọn đoạn nào")
    p.update(voice=req.voice if req.voice in voice.VOICES else voice.DEFAULT_VOICE,
             takes=min(10, max(1, req.takes)), voiceover=True)
    save(p)
    return _start_job(pid, "tts", lambda job: _tts(pid, job))


def _words(p, f, vids):
    """Phụ đề từng chữ của các video (lưu đệm); thiếu phụ đề một video thì bỏ qua video đó."""
    words = {}
    sub_dir = f / "subs"
    sub_dir.mkdir(exist_ok=True)
    for vid in vids:
        cache = sub_dir / f"{vid}.json"
        url = p["videos"][vid].get("caption_url")
        try:
            if not cache.exists() and url:
                cache.write_text(json.dumps(yt.captions(url)))
            words[vid] = json.loads(cache.read_text()) if cache.exists() else []
        except Exception:
            words[vid] = []
    return words


def _tts(pid, job):
    p = load(pid)
    f = folder(pid)
    plan = _plan(p)
    # đoạn top: số nhỏ nhất khi đếm ngược (#1, #2, #3), không thì điểm cao nhất
    top = sorted(plan, key=lambda x: x[1] if p.get("countdown", True) else -x[0]["score"])[:p.get("takes", 3)]
    job["stage"] = "Đang viết lời bình"
    words = _words(p, f, {c["vid"] for c, _ in top})
    items = [{"id": c["id"], "num": n, "comment": copywrite.clean_comment(c.get("comment", "")),
              "line": copywrite.spoken_line(words.get(c["vid"], []), c)} for c, n in top]
    texts = voice.lines(items)
    (f / "voice").mkdir(exist_ok=True)
    keep = {c["id"] for c, _ in top}
    for c in p["clips"]:                     # lời bình tự động cũ của đoạn không còn trong top: bỏ
        if c.get("voice_auto") and c["id"] not in keep:
            (f / c["voice"]).unlink(missing_ok=True)
            for k in ("voice", "voice_len", "voice_at", "voice_auto", "voice_text"):
                c.pop(k, None)
    made = 0
    for i, (c, _) in enumerate(top):
        if job["stop"]:
            raise InterruptedError
        job["stage"] = f"Đang đọc lời bình {i + 1}/{len(top)}"
        if c.get("voice") and not c.get("voice_auto"):
            continue                          # bản thu tay của bạn: giữ nguyên
        mp3, wav = f / "voice" / f"{c['id']}.mp3", f / "voice" / f"{c['id']}.wav"
        voice.speak(texts[c["id"]], mp3, p.get("voice", voice.DEFAULT_VOICE))
        try:
            compose.run(["-i", str(mp3), "-ac", "1", "-ar", "48000", str(wav)])
        finally:
            mp3.unlink(missing_ok=True)
        c.update(voice=f"voice/{c['id']}.wav", voice_len=round(compose.probe(wav)["duration"], 2),
                 voice_at=time.time(), voice_auto=True, voice_text=texts[c["id"]])
        made += 1
        job["progress"] = (i + 1) / len(top)
    save(p)
    return {"takes": made}


# ---------- dựng video ----------
def _ts(t):
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fresh_dir(d):
    if d.exists():
        shutil.rmtree(d)
    d.mkdir()
    return d


def _fetch(f, plan, job, full=True):
    """Tải các đoạn của plan (lưu đệm ở raw/) -> (plan chỉ còn đoạn tải được, {id: file}, [id lỗi])."""
    raw_dir = f / "raw"
    raw_dir.mkdir(exist_ok=True)
    # 1. tải các đoạn (song song, có lưu đệm: dựng lại không phải tải lại)
    job["stage"] = "Đang tải các đoạn video"
    raws = {}

    blocked = threading.Event()

    def fetch(c):
        if job["stop"]:
            raise InterruptedError
        path = raw_dir / f"{c['vid']}_{c['start']:.1f}_{c['end']:.1f}.mp4"
        if not path.exists():
            if blocked.is_set():            # YouTube đang chặn: không gửi thêm yêu cầu (chặn sẽ lâu hơn)
                raise yt.Blocked("skip")
            tmp = raw_dir / ("tmp_" + path.name)
            try:
                yt.download(c["vid"], c["start"], c["end"], tmp, full=full)
            except yt.Blocked:
                blocked.set()
                raise
            tmp.replace(path)
        return path

    failed = []
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = [(c, ex.submit(fetch, c)) for c, _ in plan]
        for i, (c, fut) in enumerate(futs):
            try:
                raws[c["id"]] = fut.result()
            except InterruptedError:
                raise
            except Exception:
                failed.append(c["id"])
            job["progress"] = 0.45 * (i + 1) / len(futs)
    total = len(plan)
    plan = [(c, n) for c, n in plan if c["id"] in raws]
    # thiếu nhiều đoạn thì dừng, không ra video cụt (đoạn đã tải vẫn giữ: bấm Dựng lại chỉ tải phần còn thiếu)
    if blocked.is_set():
        raise RuntimeError(f"YouTube đang tạm chặn tải (\"Sign in to confirm you're not a bot\") do tải quá nhiều. "
                           f"Đã tải {len(plan)}/{total} đoạn. Đợi 30–60 phút rồi bấm Dựng lại (chỉ tải phần còn thiếu)"
                           + ("." if yt.COOKIES["file"] or yt.COOKIES["browser"]
                              else ", hoặc thêm cookie YouTube ở trang ⚙ Cài đặt để ít bị chặn hơn."))
    if not plan:
        raise RuntimeError("Không tải được đoạn nào (YouTube chặn hoặc mất mạng)")
    if len(failed) > total * 0.3:
        raise RuntimeError(f"Chỉ tải được {len(plan)}/{total} đoạn (mạng lỗi hoặc video bị xoá / chặn). "
                           "Bấm Dựng lại để tải tiếp phần còn thiếu, hoặc bỏ bớt đoạn lỗi.")
    return plan, raws, failed


@app.post("/api/projects/{pid}/build")
def build(pid: str):
    if not load(pid)["order"]:
        raise HTTPException(400, "Chưa chọn đoạn nào")
    return _start_job(pid, "build", lambda job: _build(pid, job))


def _build(pid, job):
    p = load(pid)
    if not p["order"]:
        raise RuntimeError("Không chọn được đoạn nào")
    f = folder(pid)
    plan = _plan(p)
    if p.get("kind") == "shorts":
        return _build_shorts(pid, p, f, plan, job)
    seg_dir = _fresh_dir(f / "seg")
    plan, raws, failed = _fetch(f, plan, job, full=not p.get("stream"))
    if failed and p.get("countdown", True):     # đánh số lại cho liền mạch
        plan = [(c, len(plan) - k) for k, (c, _) in enumerate(plan)]
    # phụ đề: luôn lấy vì lời dẫn mô tả + hook của Shorts trích câu thoại từ đây
    job["stage"] = "Đang lấy phụ đề"
    words = _words(p, f, {c["vid"] for c, _ in plan})
    # tổng hợp stream theo thời gian: không đánh số, chương ghi câu chat / mốc giờ trong stream
    stream_mix = bool(p.get("stream")) and not p.get("countdown", True)
    # 2. đưa từng đoạn về khung chuẩn + bình luận
    job["stage"] = "Đang dựng từng đoạn"
    parts, chapters, copy_items, t = [], [], [], 0.0
    first_frame = seg_dir / "first.png"
    compose.run(["-ss", "1", "-i", str(raws[plan[0][0]["id"]]), "-frames:v", "1", "-update", "1", str(first_frame)])
    intro = compose.title_card(first_frame, p["title"], f"{len(plan)} best moments" if stream_mix else f"Top {len(plan)}",
                               seg_dir / "intro.png")
    parts.append(compose.still_segment(intro, seg_dir / "000_intro.mp4", INTRO_LEN))
    t += INTRO_LEN
    chapters.append((0.0, "Intro"))
    for k, (c, num) in enumerate(plan):
        if job["stop"]:
            raise InterruptedError
        v = p["videos"][c["vid"]]
        ov = compose.overlay(None if stream_mix else num, v["channel"] if p.get("credits", True) else "",
                             v["title"] if p.get("credits", True) else "", c.get("caption", ""))
        ov_png = seg_dir / f"ov_{k}.png"
        ov.save(ov_png)
        subs = yt.chunks(words.get(c["vid"], []), c["start"], c["end"]) if p.get("subtitles", True) else []
        seg = compose.clip_segment(raws[c["id"]], seg_dir / f"{k + 1:03d}_clip.mp4", 0.0, c["end"] - c["start"],
                                   ov_png, seg_dir, subs)
        parts.append(seg)
        if stream_mix:
            # tên chương: câu chat tiêu biểu, không có thì câu streamer nói lúc đó, cuối cùng mới tới tên stream
            line = (copywrite.clean_comment(c.get("comment", "")) or copywrite.spoken_line(words.get(c["vid"], []), c)
                    or v["title"][:50])
            chapters.append((t, f"{copywrite._short(line, 60)} ({_ts(c['start'])})", c, v))
        else:
            chapters.append((t, f"#{num} {v['title'][:60]}", c, v))
        copy_items.append({"num": num, "ts": _ts(t), "line": copywrite.spoken_line(words.get(c["vid"], []), c),
                           "comment": copywrite.clean_comment(c.get("comment", ""))})
        t += compose.probe(seg)["duration"]
        if c.get("voice") and (f / c["voice"]).exists():
            frame = compose.last_frame(seg, seg_dir / f"last_{k}.png")
            card = compose.commentary_card(frame, c.get("caption") or c.get("voice_text", ""),
                                           seg_dir / f"take_{k}.png")
            vseg = compose.still_segment(card, seg_dir / f"{k + 1:03d}_take.mp4", c["voice_len"] + 0.6,
                                         f / c["voice"])
            parts.append(vseg)
            t += compose.probe(vseg)["duration"]
        job["progress"] = 0.45 + 0.4 * (k + 1) / len(plan)
    outro = compose.title_card(first_frame, "Thanks for watching!", "Like & subscribe for more", seg_dir / "outro.png")
    parts.append(compose.still_segment(outro, seg_dir / "999_outro.mp4", OUTRO_LEN))
    # 3. nối
    job["stage"] = "Đang ghép video"
    tmp = f / "output.tmp.mp4"
    compose.concat(parts, tmp, seg_dir)
    tmp.replace(f / "output.mp4")
    short_items = _shorts(p, f, plan, raws, words, copy_items, seg_dir, job) if p.get("shorts", True) else []
    _remember(plan)
    shutil.rmtree(seg_dir, ignore_errors=True)
    lines = [f"{_ts(x[0])} {x[1]}" for x in chapters]
    sources = sorted({(x[3]["title"], x[3]["channel"], x[2]["vid"]) for x in chapters[1:]})
    takes = sum(1 for c, _ in plan if c.get("voice"))
    job["stage"] = "Đang nghiên cứu từ khoá & xu hướng"
    used = [p["videos"][v] for v in dict.fromkeys(c["vid"] for c, _ in plan)]
    others = [v for k, v in p["videos"].items() if k not in {u["id"] for u in used}]
    used_tags = [t for v in used for t in v.get("tags") or []]
    found = seo.names([v["title"] for v in used] + [v["title"] for v in others],
                      tags=used_tags, weights=[2] * len(used) + [1] * len(others))
    in_plan = " ".join([v["title"] for v in used] + used_tags).lower()
    copy_src = {"keyword": p["keyword"], "items": copy_items, "video_count": len(sources),
                "chapters_text": "\n".join(lines), "commentary": bool(takes),
                "sources": "\n".join(f"- {t_} — {ch} https://youtu.be/{vid}" for t_, ch, vid in sources),
                "research": seo.research(p["keyword"]), "names": [n for n in found if n.lower() in in_plan],
                "source_tags": used_tags, "channels": sorted({v["channel"] for v in used if v.get("channel")}),
                "context": " ".join([v["title"] for v in used] + used_tags), "stream": stream_mix}
    youtube = copywrite.make(**copy_src)
    # thumbnail: khung hình "hành động" (mặt người to + chuyển động mạnh) quanh đỉnh của các đoạn điểm cao,
    # ưu tiên đoạn từ video có tên người / chủ đề chính; chữ ngắn kiểu giật tít
    job["stage"] = "Đang làm thumbnail"
    subject = [w for w in re.findall(r"\w+", " ".join(copy_src["names"][:1]) or p.get("ref") or p["keyword"])
               if len(w) > 2 and w.lower() not in ("funny", "moments", "moment", "best")]
    top = sorted(plan, key=lambda x: -x[0]["score"])[:8]
    cands = []
    for c, _ in top:
        title = p["videos"].get(c["vid"], {}).get("title", "").lower()
        rel = 1.0 if subject and any(w.lower() in title for w in subject) else 0.0
        cands.append((raws[c["id"]], max(0.0, (c.get("peak") or c["start"]) - c["start"]), rel))
    try:
        shots = compose.best_shots(cands, f)
    except Exception:                      # thiếu OpenCV / video lỗi: quay về khung giữa đoạn
        shots = []
        for i, (src, peak, _) in enumerate(cands[:3]):
            fr = f / f"shot_{i}.png"
            try:
                compose.run(["-ss", f"{peak:.2f}", "-i", str(src), "-frames:v", "1", "-update", "1", str(fr)])
                shots.append(fr)
            except RuntimeError:
                pass
    text = p.get("thumb_text") or youtube.get("thumb") or p["title"]
    thumbs = compose.thumbnails(shots, text, len(plan) if p.get("countdown", True) else 0, f)
    shutil.copyfile(thumbs[0], f / "thumbnail.jpg")
    for fr in f.glob("shot_*.png"):
        fr.unlink(missing_ok=True)
    cur = load(pid)
    cur.update(output_stamp=_stamp(p), copy_src=copy_src, youtube=youtube, failed=failed)
    cur["shorts_out"] = [{**x, **meta} for x, meta in zip(short_items, copywrite.shorts(
        short_items, cur["youtube"]["title"], copy_src["names"]))]
    save(cur)
    job["progress"] = 1.0
    return {"clips": len(plan), "failed": len(failed), "duration": round(t + OUTRO_LEN, 1),
            "shorts": len(short_items)}


def _shorts(p, f, plan, raws, words, copy_items, seg_dir, job, lo=0.85, hi=0.95):
    """Cắt các đoạn top thành video dọc 1080x1920 cho YouTube Shorts (kèm hook + phụ đề).
    Có video dài: nút "FULL VIDEO ON MY CHANNEL" + số thứ tự; dự án chỉ Shorts: "FOLLOW FOR MORE"."""
    for old in f.glob("short_*.mp4"):
        old.unlink()
    only = p.get("kind") == "shorts"
    by_num = {x["num"]: x for x in copy_items}
    top = plan if only else sorted(plan, key=lambda x: x[1] if p.get("countdown", True) else -x[0]["score"])
    top = top[:p.get("count", SHORTS)]
    rnd = random.Random(p["id"])
    out, used = [], set()
    for i, (c, num) in enumerate(top, 1):
        if job["stop"]:
            raise InterruptedError
        job["stage"] = f"Đang cắt Shorts {i}/{len(top)}"
        v = p["videos"][c["vid"]]
        ov = seg_dir / f"short_ov_{i}.png"
        extra, voice_mp3 = {}, None
        if p.get("reply") and copywrite.reply_text(c.get("comment", "")):
            text = copywrite.reply_text(c["comment"])
            hook = copywrite.reply_line(c["comment"], rnd, used)
            compose.reply_overlay(c.get("author", ""), text, hook, "FOLLOW FOR MORE").save(ov)
            try:                                       # câu đáp đọc bằng giọng (cần mạng); lỗi thì chỉ hiện chữ
                voice_mp3 = voice.speak(voice._speakable(hook) or hook, seg_dir / f"reply_{i}.mp3", p.get("voice"))
            except Exception:  # noqa: BLE001
                voice_mp3 = None
            extra = {"reply": hook, "comment": text, "author": c.get("author", "")}
        else:
            hook = copywrite.short_hook(by_num.get(num, {}), rnd, used)
            compose.short_overlay(hook, "FOLLOW FOR MORE" if only else "FULL VIDEO ON MY CHANNEL",
                                  None if only else num).save(ov)
        used.add(hook)
        subs = yt.chunks(words.get(c["vid"], []), c["start"], c["end"])
        try:
            compose.short_segment(raws[c["id"]], f / f"short_{i}.mp4", c["end"] - c["start"], ov, seg_dir, subs,
                                  voice_mp3=voice_mp3)
        except RuntimeError:
            continue
        out.append({"file": f"short_{i}.mp4", "num": num, "hook": hook, "title": v["title"],
                    "channel": v["channel"], "vid": c["vid"], **extra})
        job["progress"] = lo + (hi - lo) * i / len(top)
    return out


def _build_shorts(pid, p, f, plan, job):
    """Dự án chỉ Shorts: tải đoạn -> phụ đề -> cắt dọc -> tiêu đề, mô tả, tag cho từng Short."""
    seg_dir = _fresh_dir(f / "seg")
    plan, raws, failed = _fetch(f, plan, job, full=not p.get("stream"))   # stream vài giờ: không tải cả video
    job["stage"] = "Đang lấy phụ đề"
    words = _words(p, f, {c["vid"] for c, _ in plan})
    items = [{"num": n, "line": copywrite.spoken_line(words.get(c["vid"], []), c),
              "comment": copywrite.clean_comment(c.get("comment", ""))} for c, n in plan]
    out = _shorts(p, f, plan, raws, words, items, seg_dir, job, 0.5, 0.95)
    shutil.rmtree(seg_dir, ignore_errors=True)
    if not out:
        raise RuntimeError("Không cắt được Short nào")
    _remember(plan)
    job["stage"] = "Đang viết tiêu đề & tag"
    used = [p["videos"][v] for v in dict.fromkeys(c["vid"] for c, _ in plan)]
    used_tags = [t for v in used for t in v.get("tags") or []]
    names = seo.names([v["title"] for v in used], tags=used_tags, weights=[2] * len(used))
    research = seo.research(p["keyword"])
    tags = seo.tags(p["keyword"].lower(), p["keyword"], research.get("phrases", []), names, used_tags,
                    sorted({v["channel"] for v in used if v.get("channel")}))
    metas = copywrite.shorts(out, None, names, seed=p["id"])
    cur = load(pid)
    cur.update(output_stamp=_stamp(p), failed=failed, shorts_out=[{**x, **m} for x, m in zip(out, metas)],
               youtube={"title": metas[0]["title"], "tags": ", ".join(tags), "names": names})
    save(cur)
    job["progress"] = 1.0
    return {"clips": len(plan), "failed": len(failed), "duration": 0, "shorts": len(out)}


@app.post("/api/projects/{pid}/copy")
def rewrite_copy(pid: str):
    """Viết lại tiêu đề + mô tả (mẫu câu khác, cùng dữ liệu của bản dựng gần nhất)."""
    p = load(pid)
    if not p.get("copy_src"):
        raise HTTPException(400, "Dựng video trước rồi mới viết tiêu đề / mô tả")
    if "research" not in p["copy_src"]:              # bản dựng cũ (trước khi có SEO): nghiên cứu bổ sung
        vid_of = {c["id"]: c["vid"] for c in p["clips"]}       # id video YouTube có thể chứa "_"
        used = [p["videos"][v] for v in dict.fromkeys(vid_of[c] for c in p["order"] if c in vid_of)]
        p["copy_src"].update(research=seo.research(p["keyword"]),
                             names=seo.names([v["title"] for v in used], weights=[2] * len(used)),
                             channels=sorted({v["channel"] for v in used if v.get("channel")}))
    p["youtube"] = copywrite.make(**p["copy_src"])
    save(p)
    return view(p)


@app.post("/api/projects/{pid}/cancel")
def cancel(pid: str):
    job = JOBS.get(pid)
    if job and not job["done"]:
        job["stop"] = True
    return {"ok": True}


@app.get("/api/projects/{pid}/job")
def job_status(pid: str):
    job = JOBS.get(pid)
    return {k: v for k, v in job.items() if k != "stop"} if job else None


@app.get("/api/projects/{pid}/files/{name}")
def files(pid: str, name: str, download: bool = False):
    if name not in ("output.mp4", "thumbnail.jpg", "thumb_1.jpg", "thumb_2.jpg", "thumb_3.jpg",
                    *(f"short_{i}.mp4" for i in range(1, STREAM_SHORTS + 1))):
        raise HTTPException(404)
    path = folder(pid) / name
    if not path.exists():
        raise HTTPException(404)
    if download:
        title = load(pid)["title"]
        nice = "".join(ch for ch in title if ch.isalnum() or ch in " -_").strip() or "compilation"
        suffix = "" if name == "output.mp4" else f" - {path.stem}"
        if name.startswith("short_"):
            nice = next((x["title"].removesuffix(" #shorts") for x in load(pid).get("shorts_out", [])
                         if x["file"] == name), nice)
            nice = "".join(ch for ch in nice if ch.isalnum() or ch in " -_").strip() or "short"
        return FileResponse(path, filename=f"{nice}{suffix}{path.suffix}")
    return FileResponse(path)


@app.delete("/api/projects/{pid}")
def delete(pid: str):
    shutil.rmtree(folder(pid))
    JOBS.pop(pid, None)
    return {"ok": True}


# ---------- 🎵 ghép nhạc: bài hot theo quốc gia + thể loại -> một video dài ----------
MIXES = DATA / "mixes"
MUSIC_CACHE = DATA / "music_cache"          # tiếng đã tải + chuẩn hoá, ảnh bìa: dùng lại cho các bản mix sau
MIX_FADE = (0, 2, 3, 5, 8)
MIX_PERIODS = ("all", "month", "week")
MUSIC_LOCK = threading.Lock()


def _music_hist_file():
    return DATA / "music_history.json"


def _music_used():
    """Các bài (theo phiên bản) đã dùng trong bản mix đã dựng: {"remix": [khoá bài…], …}."""
    try:
        return json.loads(_music_hist_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _music_remember(version, tracks):
    with MUSIC_LOCK:
        h = _music_used()
        keys = h.setdefault(version, [])
        keys += [k for k in (music.song_key(t.get("base") or t["song"]) for t in tracks) if k and k not in keys]
        tmp = _music_hist_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_music_hist_file())


def _mix_dir(mid):
    f = MIXES / mid
    if not mid.isalnum() or not (f / "mix.json").exists():
        raise HTTPException(404, "Không tìm thấy bản mix")
    return f


def _mix_load(mid):
    return json.loads((_mix_dir(mid) / "mix.json").read_text(encoding="utf-8"))


def _mix_save(m):
    path = MIXES / m["id"] / "mix.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _mix_job(mid):
    return "mix_" + mid


def _mix_stamp(m):
    return hashlib.md5(json.dumps([m["order"], m["fade"], m.get("bg"), m.get("style"),
                                   [m["tracks"][i].get("artist") for i in m["order"] if i in m["tracks"]],
                                   [m["tracks"][i].get("song") for i in m["order"] if i in m["tracks"]]])
                       .encode()).hexdigest()


def _mix_view(m):
    job = JOBS.get(_mix_job(m["id"]))
    tracks = [m["tracks"][i] for i in m["order"] if i in m["tracks"]]
    if m.get("style") == "mashup":      # ~32 phách mỗi bài ở ~120 BPM
        total = len(tracks) * (music.MASH_BEATS - music.MASH_XFADE) * 0.5
    else:
        total = sum(t["duration"] for t in tracks) - m["fade"] * max(0, len(tracks) - 1)
    return {"mix": m, "tracks": tracks, "total": round(max(0, total)),
            "output": (MIXES / m["id"] / "output.mp4").exists() and m.get("output_stamp") == _mix_stamp(m),
            "job": {k: v for k, v in job.items() if k != "stop"} if job else None}


@app.get("/api/music/options")
def music_options():
    return {"countries": {k: v[0] for k, v in music.COUNTRIES.items()},
            "genres": {k: v[0] for k, v in music.GENRES.items()},
            "versions": {k: v[0] for k, v in music.VERSIONS.items()}, "fades": MIX_FADE,
            "used": {k: len(v) for k, v in _music_used().items()}}


class MixReq(BaseModel):
    country: str = "vn"
    genre: str = "all"
    version: str = "original"       # bản gốc / remix / chill (lofi) / acoustic / piano của các bài hot
    count: int = 15
    period: str = "all"             # "all": hot nhất mọi thời điểm; "month" / "week": đang hot
    free: bool = False              # chỉ nhạc không bản quyền (NCS…)
    fresh: bool = True              # bỏ bài đã dùng ở các bản mix trước (cùng phiên bản)
    fade: int = 3
    style: str = "playlist"         # "playlist": cả bài; "mashup": điệp khúc từng bài nối khớp nhịp
    auto: bool = False              # tìm xong dựng luôn


@app.post("/api/mixes")
def mix_create(req: MixReq):
    if req.country not in music.COUNTRIES or req.genre not in music.GENRES or req.version not in music.VERSIONS:
        raise HTTPException(400, "Quốc gia / thể loại / phiên bản không hợp lệ")
    mid = uuid.uuid4().hex[:12]
    (MIXES / mid).mkdir(parents=True)
    m = {"id": mid, "created": time.time(), "country": req.country, "genre": req.genre,
         "count": min(40, max(3, req.count)), "period": req.period if req.period in MIX_PERIODS else "all",
         "version": req.version, "fresh": req.fresh, "style": "mashup" if req.style == "mashup" else "playlist",
         "free": req.free, "fade": req.fade if req.fade in MIX_FADE else 3, "auto": req.auto,
         "title": music.name(req.country, req.genre, req.free, req.version), "tracks": {}, "order": []}
    _mix_save(m)

    def work(job):
        _mix_find(mid, job, 0.0, 0.15 if req.auto else 1.0)
        if req.auto:
            return _mix_build(mid, _Part(job, 0.15, 1.0))
        job["progress"] = 1.0
        return {"found": len(_mix_load(mid)["order"])}

    _start_job(_mix_job(mid), "auto" if req.auto else "find", work)
    return _mix_view(m)


def _mix_find(mid, job, lo, hi, more=False):
    m = _mix_load(mid)
    want = max(5, m["count"] // 2) if more else m["count"]
    version = m.get("version", "original")
    used = set(_music_used().get(version, [])) if m.get("fresh", True) else set()
    if more:                            # tìm thêm: không lấy lại bài đang có trong bản mix
        used |= {music.song_key(t.get("base") or t["song"]) for t in m["tracks"].values()}
    found = music.find(m["country"], m["genre"], max(1, want), m.get("period", "all"), m.get("free"),
                       _Part(job, lo, hi), exclude=set(m["tracks"]), version=version, used=used)
    if not found:
        raise RuntimeError("Không tìm được bài nào" + (" mới — các bài hot đã dùng hết ở bản mix trước. Bỏ tick "
                           "“Không lặp bài”, đổi thể loại / phiên bản, hoặc chọn “Đang hot 30 ngày qua”."
                           if used else " (mạng lỗi hoặc YouTube chặn). Thử lại sau ít phút."))
    cur = _mix_load(mid)
    for v in found:
        cur["tracks"][v["id"]] = v
        if v["id"] not in cur["order"]:
            cur["order"].append(v["id"])
    _mix_save(cur)
    return len(found)


@app.get("/api/mixes")
def mix_list():
    out = []
    for mj in sorted(MIXES.glob("*/mix.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        m = json.loads(mj.read_text(encoding="utf-8"))
        out.append({"id": m["id"], "title": m.get("youtube", {}).get("title") or m["title"], "created": m["created"],
                    "songs": len(m["order"]), "done": (mj.parent / "output.mp4").exists()})
    return out


@app.get("/api/mixes/{mid}")
def mix_get(mid: str):
    return _mix_view(_mix_load(mid))


class MixEdit(BaseModel):
    order: list[str] | None = None
    fade: int | None = None
    title: str | None = None            # tiêu đề YouTube tự đặt ("" = để app tự viết)
    track: str | None = None            # sửa tên hiển thị của một bài
    artist: str | None = None
    song: str | None = None


@app.put("/api/mixes/{mid}")
def mix_update(mid: str, e: MixEdit):
    m = _mix_load(mid)
    if e.order is not None:
        m["order"] = [i for i in dict.fromkeys(e.order) if i in m["tracks"]]
    if e.fade is not None:
        if e.fade not in MIX_FADE:
            raise HTTPException(400, "Độ dài crossfade không hợp lệ")
        m["fade"] = e.fade
    if e.title is not None:             # đổi tiêu đề: viết lại mô tả ngay, không phải dựng lại video
        m["custom_title"] = e.title.strip()[:100]
        if m.get("timeline"):
            done = [(m["tracks"][x["id"]], x["start"]) for x in m["timeline"] if x["id"] in m["tracks"]]
            m["youtube"] = music.copy(m, [t for t, _ in done], [s_ for _, s_ in done])
    if e.track:
        t = m["tracks"].get(e.track)
        if not t:
            raise HTTPException(404, "Không có bài này")
        if e.artist is not None:
            t["artist"] = e.artist.strip()[:80]
        if e.song is not None:
            t["song"] = e.song.strip()[:100] or t["song"]
    _mix_save(m)
    return _mix_view(m)


class MixAdd(BaseModel):
    url: str


@app.post("/api/mixes/{mid}/tracks")
def mix_add(mid: str, req: MixAdd):
    _mix_dir(mid)
    try:
        v = music.info(req.url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:  # noqa: BLE001 - video riêng tư / bị xoá / mạng lỗi
        raise HTTPException(400, f"Không đọc được video: {str(e)[-200:]}") from None
    if not 30 <= v["duration"] <= 15 * 60:
        raise HTTPException(400, "Chỉ thêm được bài dài 30 giây – 15 phút")
    m = _mix_load(mid)
    m["tracks"][v["id"]] = v
    if v["id"] not in m["order"]:
        m["order"].append(v["id"])
    _mix_save(m)
    return _mix_view(m)


@app.post("/api/mixes/{mid}/more")
def mix_more(mid: str):
    _mix_dir(mid)

    def work(job):
        n = _mix_find(mid, job, 0.0, 1.0, more=True)
        job["progress"] = 1.0
        return {"found": n}

    return _start_job(_mix_job(mid), "find", work)


@app.post("/api/mixes/{mid}/bg")
async def mix_bg(mid: str, file: UploadFile = File(...)):
    f = _mix_dir(mid)
    raw = await file.read()
    if len(raw) > 15_000_000:
        raise HTTPException(400, "Ảnh quá lớn (tối đa 15 MB)")
    try:
        from PIL import Image
        import io

        Image.open(io.BytesIO(raw)).convert("RGB").save(f / "bg.jpg", quality=92)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "File không phải ảnh") from None
    m = _mix_load(mid)
    m["bg"] = f"bg.jpg?{int(time.time())}"
    _mix_save(m)
    return _mix_view(m)


@app.delete("/api/mixes/{mid}/bg")
def mix_bg_delete(mid: str):
    f = _mix_dir(mid)
    (f / "bg.jpg").unlink(missing_ok=True)
    m = _mix_load(mid)
    m.pop("bg", None)
    _mix_save(m)
    return _mix_view(m)


@app.post("/api/mixes/{mid}/build")
def mix_build(mid: str):
    if not _mix_load(mid)["order"]:
        raise HTTPException(400, "Chưa có bài nào")
    return _start_job(_mix_job(mid), "build", lambda job: _mix_build(mid, job))


def _mix_build(mid, job):
    m = _mix_load(mid)
    f = _mix_dir(mid)
    MUSIC_CACHE.mkdir(exist_ok=True)
    tracks = [m["tracks"][i] for i in m["order"] if i in m["tracks"]]
    if not tracks:
        raise RuntimeError("Chưa có bài nào")
    mash = m.get("style") == "mashup"
    # 1. tải tiếng (song song vừa phải, có lưu đệm) + ảnh bìa
    job["stage"] = "Đang tải nhạc"
    blocked = threading.Event()

    def fetch(t):
        if job["stop"]:
            raise InterruptedError
        if blocked.is_set():
            raise yt.Blocked("skip")
        try:
            music.fetch_cover(t["id"], MUSIC_CACHE / f"{t['id']}.jpg")
            if mash and "peak" not in t:        # mashup: đoạn "Most replayed" thường là điệp khúc
                try:
                    a = yt.analyze(t["id"], comments=0)
                    t["peak"] = music.heat_peak(a["heatmap"], a["duration"])
                except Exception:  # noqa: BLE001 - không có heatmap: tự tìm đoạn to nhất
                    t["peak"] = None
            return music.download_audio(t["id"], MUSIC_CACHE / f"{t['id']}.flac")
        except yt.Blocked:
            blocked.set()
            raise

    files, failed = {}, []
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = [(t, ex.submit(fetch, t)) for t in tracks]
        for i, (t, fut) in enumerate(futs):
            try:
                files[t["id"]] = fut.result()
            except InterruptedError:
                raise
            except Exception:  # noqa: BLE001 - bài lỗi thì bỏ
                failed.append(t["id"])
            job["progress"] = 0.3 * (i + 1) / len(futs)
    if blocked.is_set():
        raise RuntimeError(f"YouTube đang tạm chặn tải (\"not a bot\"). Đã tải {len(files)}/{len(tracks)} bài. "
                           "Đợi 30–60 phút rồi bấm Dựng lại (bài đã tải được giữ lại)"
                           + ("." if yt.COOKIES["file"] or yt.COOKIES["browser"] else
                              ", hoặc thêm cookie YouTube ở trang ⚙ Cài đặt."))
    tracks = [t for t in tracks if t["id"] in files]
    if len(tracks) < 2:
        raise RuntimeError("Tải được quá ít bài (YouTube chặn hoặc video bị xoá)")
    work = _fresh_dir(f / "work")
    bpm = None
    if mash:
        # 2a. mashup: điệp khúc mỗi bài, đổi về cùng nhịp, nối chồng đúng 2 phách
        job["stage"] = "Đang tìm điệp khúc + đo nhịp từng bài"
        cuts = []
        for i, t in enumerate(tracks):
            if job["stop"]:
                raise InterruptedError
            cuts.append(music.chorus(files[t["id"]], t.get("peak")))
            job["progress"] = 0.3 + 0.04 * (i + 1) / len(tracks)
        # chia nhóm nhịp (chậm -> nhanh), mỗi nhóm một nhịp chung; trong nhóm giữ thứ tự hot
        groups = music.tempo_groups([c[2] for c in cuts])
        order = [(i, tgt) for tgt, members in groups for i in members]
        tracks, cuts = [tracks[i] for i, _ in order], [cuts[i] for i, _ in order]
        targets = [tgt for _, tgt in order]
        bpm = music.common_bpm(targets)
        job["stage"] = "Đang đưa các bài về nhịp chung: " + ", ".join(f"{g[0]:.0f} BPM" for g in groups)
        play = []
        for i, (t, (start, length, b), tgt) in enumerate(zip(tracks, cuts, targets)):
            play.append(music.mash_segment(files[t["id"]], start, length, music.stretch_ratio(b, tgt),
                                           work / f"seg_{i:03d}.flac"))
        fade = [music.MASH_XFADE * 60 / tgt for tgt in targets[1:]]      # chồng 2 phách của bài vào
    else:
        play = [files[t["id"]] for t in tracks]
        fade = m["fade"]
    # 2. nối crossfade
    job["stage"] = "Đang nối nhạc (crossfade)"
    durs = [compose.probe(x)["duration"] for x in play]
    fade = [min(x, min(durs) / 3) for x in fade] if isinstance(fade, list) else min(fade, min(durs) / 3)
    starts, total = music.timeline(durs, fade)
    music.mix_audio(play, work / "mix.flac", fade)
    job["progress"] = 0.38
    # 3. khung hình cho từng bài (đổi khung ở giữa đoạn crossfade)
    job["stage"] = "Đang vẽ khung hình"
    covers = [MUSIC_CACHE / f"{t['id']}.jpg" for t in tracks]
    bg = f / "bg.jpg" if m.get("bg") and (f / "bg.jpg").exists() else None
    base = music.background(covers, bg)
    frames = [music.frame(base, tracks, starts, k, covers[k], work / f"frame_{k:03d}.png") for k in range(len(tracks))]
    fades = fade if isinstance(fade, list) else [fade] * (len(tracks) - 1)
    switch = [0.0] + [s + x / 2 for s, x in zip(starts[1:], fades)]
    # 4. dựng video
    job["stage"] = f"Đang dựng video {_ts(total)} (khoảng {max(1, round(total / 60 / 4))} phút)"
    tmp = f / "output.tmp.mp4"

    def prog(x):
        job["progress"] = 0.4 + 0.57 * x

    music.render(frames, switch, total, work / "mix.flac", tmp, work, prog, lambda: job["stop"])
    tmp.replace(f / "output.mp4")
    # 5. thumbnail + tiêu đề / mô tả
    job["stage"] = "Đang làm thumbnail, tiêu đề, mô tả"
    cur = _mix_load(mid)
    y = music.copy(cur, tracks, starts, total)
    music.thumbnail(covers, y["thumb"], y["thumb_sub"], f / "thumbnail.jpg")
    shutil.rmtree(work, ignore_errors=True)
    _music_remember(m.get("version", "original"), tracks)
    for t in tracks:                    # đỉnh "Most replayed": lưu lại, dựng lại không phải đọc lại
        if "peak" in t and t["id"] in cur["tracks"]:
            cur["tracks"][t["id"]]["peak"] = t["peak"]
    if mash:                            # mashup xếp lại theo nhóm nhịp: danh sách trên web theo đúng thứ tự trong video
        cur["order"] = [t["id"] for t in tracks] + [i for i in cur["order"] if i not in {t["id"] for t in tracks}]
        m = {**m, "order": cur["order"]}
    cur.update(youtube=y, output_stamp=_mix_stamp(m), failed=failed, built_total=round(total), bpm=bpm,
               bpm_groups=[round(g[0]) for g in groups] if mash else None,
               timeline=[{"id": t["id"], "start": round(s, 1)} for t, s in zip(tracks, starts)])
    _mix_save(cur)
    job["progress"] = 1.0
    return {"songs": len(tracks), "failed": len(failed), "duration": round(total)}


@app.post("/api/mixes/{mid}/cancel")
def mix_cancel(mid: str):
    _mix_dir(mid)
    return cancel(_mix_job(mid))


@app.get("/api/mixes/{mid}/files/{name}")
def mix_files(mid: str, name: str, download: bool = False):
    if name not in ("output.mp4", "thumbnail.jpg", "bg.jpg"):
        raise HTTPException(404)
    path = _mix_dir(mid) / name
    if not path.exists():
        raise HTTPException(404)
    if download:
        m = _mix_load(mid)
        title = m.get("youtube", {}).get("title") or m["title"]
        nice = "".join(ch for ch in title if ch.isalnum() or ch in " -_").strip() or "playlist"
        return FileResponse(path, filename=f"{nice}{path.suffix}")
    return FileResponse(path)


@app.get("/api/music/cover/{vid}")
def music_cover(vid: str):
    if not re.fullmatch(r"[\w-]{11}", vid):
        raise HTTPException(404)
    MUSIC_CACHE.mkdir(exist_ok=True)
    path = music.fetch_cover(vid, MUSIC_CACHE / f"{vid}.jpg")
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.delete("/api/mixes/{mid}")
def mix_delete(mid: str):
    shutil.rmtree(_mix_dir(mid))
    JOBS.pop(_mix_job(mid), None)
    return {"ok": True}


# ---------- 🎤 video TikTok lời bài hát ----------
TT_LENGTHS = (0, 15, 30, 45, 60)          # 0 = cả bài
TT_BG = "aesthetic scenery"


def _tt_dir(tid):
    f = DATA / "tiktok" / tid
    if not tid.isalnum() or not (f / "tt.json").exists():
        raise HTTPException(404, "Không tìm thấy video TikTok này")
    return f


def _tt_load(tid):
    return json.loads((_tt_dir(tid) / "tt.json").read_text(encoding="utf-8"))


def _tt_save(t):
    path = DATA / "tiktok" / t["id"] / "tt.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _tt_view(t):
    job = JOBS.get("tt_" + t["id"])
    return {**{k: v for k, v in t.items() if k not in ("lrc",)},
            "output": (DATA / "tiktok" / t["id"] / "output.mp4").exists(),
            "job": {k: v for k, v in job.items() if k != "stop"} if job else None}


class TikTokReq(BaseModel):
    song: str
    length: int = 30
    bg: str = ""


@app.post("/api/tiktok")
def tiktok_create(req: TikTokReq):
    song = req.song.strip()
    if not song:
        raise HTTPException(400, "Nhập tên bài hát (Ca sĩ - Tên bài) hoặc link YouTube của bài")
    if req.length not in TT_LENGTHS:
        raise HTTPException(400, "Độ dài không hợp lệ")
    tid = uuid.uuid4().hex[:12]
    (DATA / "tiktok" / tid).mkdir(parents=True)
    t = {"id": tid, "created": time.time(), "input": song[:200], "length": req.length,
         "bg": req.bg.strip()[:80] or TT_BG, "offset": 0.0, "title": song[:80], "warn": "", "stamp": ""}
    _tt_save(t)
    _start_job("tt_" + tid, "build", lambda job: _tt_build(tid, job))
    return _tt_view(t)


@app.get("/api/tiktok")
def tiktok_list():
    root = DATA / "tiktok"
    items = []
    for f in root.glob("*/tt.json") if root.exists() else []:
        try:
            items.append(_tt_view(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return sorted(items, key=lambda t: -t["created"])


class TikTokEdit(BaseModel):
    offset: float | None = None
    caption: str | None = None


@app.put("/api/tiktok/{tid}")
def tiktok_update(tid: str, e: TikTokEdit):
    t = _tt_load(tid)
    if e.caption is not None:
        t["caption"] = e.caption[:2200]
    rebuild = e.offset is not None and round(e.offset, 2) != t.get("offset", 0)
    if rebuild:
        t["offset"] = max(-10.0, min(10.0, round(e.offset, 2)))
    _tt_save(t)
    if rebuild:
        _start_job("tt_" + tid, "build", lambda job: _tt_build(tid, job))
    return _tt_view(t)


@app.post("/api/tiktok/{tid}/build")
def tiktok_build(tid: str):
    _tt_dir(tid)
    _start_job("tt_" + tid, "build", lambda job: _tt_build(tid, job))
    return _tt_view(_tt_load(tid))


@app.post("/api/tiktok/{tid}/cancel")
def tiktok_cancel(tid: str):
    _tt_dir(tid)
    return cancel("tt_" + tid)


@app.get("/api/tiktok/{tid}/files/{name}")
def tiktok_files(tid: str, name: str, download: bool = False):
    if name != "output.mp4":
        raise HTTPException(404)
    path = _tt_dir(tid) / name
    if not path.exists():
        raise HTTPException(404)
    if download:
        nice = "".join(ch for ch in _tt_load(tid)["title"] if ch.isalnum() or ch in " -_").strip() or "tiktok"
        return FileResponse(path, filename=f"{nice} lyrics.mp4")
    return FileResponse(path)


@app.delete("/api/tiktok/{tid}")
def tiktok_delete(tid: str):
    job = JOBS.get("tt_" + tid)
    if job and not job["done"]:
        raise HTTPException(409, "Video đang dựng, bấm Huỷ trước")
    shutil.rmtree(_tt_dir(tid))
    JOBS.pop("tt_" + tid, None)
    return {"ok": True}


def _tt_resolve(t, job):
    """Tên bài -> lời có mốc thời gian + video YouTube có tiếng khớp đúng bản thu âm đó."""
    job["stage"] = "Đang tìm lời bài hát"
    src = None
    if re.search(r"youtu\.?be|^[\w-]{11}$", t["input"]):
        src = music.info(t["input"])
        lyr = lyrics.find(src["artist"], src["song"], duration=src["duration"])
    else:
        artist, song = lyrics.split_query(t["input"])
        lyr = lyrics.find(artist, song, "" if song and artist else t["input"])
    warn = ""
    if src and abs(src["duration"] - lyr["duration"]) <= lyrics.DUR_OK:
        audio = src
    else:
        job["stage"] = "Đang tìm bản thu âm khớp lời"
        cands = []
        for q in (f"{lyr['artist']} {lyr['song']} official audio", f"{lyr['artist']} {lyr['song']} lyrics",
                  f"{lyr['artist']} {lyr['song']}"):
            cands += [v for v in yt.search(q, 10) if v["id"] not in {c["id"] for c in cands}]
            audio = lyrics.pick_audio(cands, lyr["song"], lyr["duration"])
            if audio:
                break
        if not audio:
            if not src and not cands:
                raise RuntimeError("Không tìm thấy bài này trên YouTube")
            audio = src or min(cands, key=lambda v: abs((v.get("duration") or 0) - lyr["duration"]))
            warn = ("Không tìm được bản thu âm dài đúng bằng bản có lời, nên lời có thể lệch vài giây. "
                    "Xem thử rồi chỉnh ô “Lệch lời”.")
    t.update(artist=lyr["artist"], song=lyr["song"], title=f"{lyr['artist']} - {lyr['song']}".strip(" -"),
             lrc=lyr["lrc"], lrc_dur=lyr["duration"], audio_vid=audio["id"],
             audio_dur=audio.get("duration") or lyr["duration"], warn=warn)


def _tt_backgrounds(t, f, n, seg, job):
    """n clip nền dài ~seg giây: các khoảnh khắc hay (được tua lại nhiều) của video tìm theo từ khoá nền."""
    bg_dir = f / "bg"
    have = sorted(bg_dir.glob("bg_*.mp4")) if bg_dir.exists() else []
    if len(have) >= n:
        return have
    bg_dir.mkdir(exist_ok=True)
    job["stage"] = f"Đang tìm clip nền “{t['bg']}”"
    vids = [v for v in yt.search(t["bg"], 12) if 30 < (v.get("duration") or 0) < 4 * 3600][:8]
    if not vids:
        raise RuntimeError(f"Không tìm thấy video nào cho từ khoá nền “{t['bg']}”")
    per = {}

    def one(v):
        if job["stop"]:
            raise InterruptedError
        try:
            info = yt.analyze(v["id"], 0)
        except yt.Blocked:
            raise
        except Exception:
            info = {"duration": v["duration"], "heatmap": [], "chapters": [], "marks": []}
        got = moments.find(info, seg, per_video=4) if info.get("heatmap") else []
        if not got:                                  # không có "Most replayed": lấy rải đều khúc giữa video
            d = info.get("duration") or v["duration"]
            got = [{"start": d * k, "end": d * k + seg, "score": 0} for k in (0.3, 0.45, 0.6, 0.75)]
        return v["id"], got

    with ThreadPoolExecutor(WORKERS) as ex:
        for vid, got in ex.map(one, vids):
            per[vid] = sorted(got, key=lambda m: -m["score"])
    picks, k = [], 0                                  # lần lượt từng video: cảnh liền nhau không cùng một video
    while len(picks) < n and any(len(v) > k for v in per.values()):
        picks += [(vid, ms[k]) for vid, ms in per.items() if len(ms) > k]
        k += 1
    picks = picks[:n]
    random.shuffle(picks)
    job["stage"] = "Đang tải clip nền"
    out, done = [], [0]

    def fetch(i_pick):
        i, (vid, m) = i_pick
        if job["stop"]:
            raise InterruptedError
        path = bg_dir / f"bg_{i:02d}.mp4"
        tmp = bg_dir / f"tmp_{i:02d}.mp4"
        yt.download(vid, m["start"], m["start"] + seg + 0.5, tmp, full=False)
        tmp.replace(path)
        done[0] += 1
        job["progress"] = 0.35 + 0.4 * done[0] / len(picks)
        return path

    with ThreadPoolExecutor(WORKERS) as ex:
        for fut in [ex.submit(fetch, x) for x in enumerate(picks)]:
            try:
                out.append(fut.result())
            except InterruptedError:
                raise
            except Exception:
                continue
    if not out:
        raise RuntimeError("Không tải được clip nền nào (YouTube chặn hoặc mất mạng). Thử lại sau ít phút.")
    return out


def _lyric_window(t, audio, ls, dur, job):
    """Đoạn [a, b] dài t["length"] giây quanh điệp khúc (đoạn được tua lại nhiều nhất); length 0: cả bài."""
    job["stage"], job["progress"] = "Đang tìm đoạn điệp khúc", 0.25
    anchor = ls[len(ls) // 3][0] if ls else 0.0
    if t["length"]:
        try:
            peak = music.heat_peak(yt.analyze(t["audio_vid"], 0).get("heatmap"), dur)
        except Exception:
            peak = None
        try:
            anchor = music.chorus(audio, peak)[0]
        except Exception:
            anchor = peak or anchor
    return lyrics.window(ls, anchor, t["length"], dur)


def _tt_build(tid, job):
    f = _tt_dir(tid)
    t = _tt_load(tid)
    if not t.get("lrc"):
        _tt_resolve(t, job)
        _tt_save(t)
    if job["stop"]:
        raise InterruptedError
    job["stage"], job["progress"] = "Đang tải bài hát", 0.1
    cache = DATA / "music_cache"
    cache.mkdir(exist_ok=True)
    audio = music.download_audio(t["audio_vid"], cache / f"{t['audio_vid']}_raw.flac", trim=False)
    dur = compose.probe(audio)["duration"] or t["audio_dur"]
    ls = lyrics.lines(lyrics.parse_lrc(t["lrc"]), dur)
    if "a" not in t:
        t["a"], t["b"] = _lyric_window(t, audio, ls, dur, job)
        _tt_save(t)
    a, b = t["a"], t["b"]
    tl = lyrics.timeline(ls, a, b, t.get("offset", 0.0))
    scenes_len = lyrics.chunks(lyrics.timeline(ls, a, b), b - a)      # đổi cảnh theo câu (không theo lệch lời)
    clips = _tt_backgrounds(t, f, min(len(scenes_len), 14), lyrics.CHUNK_MAX, job)
    if job["stop"]:
        raise InterruptedError
    job["stage"], job["progress"] = "Đang dựng video", 0.8
    work = f / "work"
    _fresh_dir(work)
    scenes = [lyrics.scene(clips[i % len(clips)], work / f"scene_{i:03d}.mp4", d) for i, d in enumerate(scenes_len)]
    if job["stop"]:
        raise InterruptedError
    head = f"{t['song']} · {t['artist']}" if t.get("artist") else t["song"]
    tmp = f / "output.tmp.mp4"
    lyrics.render(scenes, tl, head, audio, a, b, tmp, work)
    tmp.replace(f / "output.mp4")
    shutil.rmtree(work, ignore_errors=True)
    t = _tt_load(tid)
    t["stamp"] = uuid.uuid4().hex[:8]
    t["duration"] = round(b - a, 1)
    if not t.get("caption"):
        t["caption"] = caption.for_lyrics(t.get("artist", ""), t["song"], [x[2] for x in tl])
    _tt_save(t)
    return {"duration": t["duration"]}


# ---------- 🎶 MV lời bài hát ảnh AI ----------
MV_FORMATS = ("h", "v", "hv")
MV_IMAGES = (8, 12, 16, 24)         # số ảnh AI: ít ảnh vẽ nhanh hơn, các cảnh dùng lại ảnh với chuyển động khác
MV_KEY_FILE = DATA / "ai_image.json"


def _mv_key():
    try:
        return json.loads(MV_KEY_FILE.read_text(encoding="utf-8")).get("key", "")
    except (OSError, ValueError):
        return ""


def _mv_dir(mid):
    f = DATA / "mv" / mid
    if not mid.isalnum() or not (f / "mv.json").exists():
        raise HTTPException(404, "Không tìm thấy MV này")
    return f


def _mv_load(mid):
    return json.loads((_mv_dir(mid) / "mv.json").read_text(encoding="utf-8"))


def _mv_save(t):
    path = DATA / "mv" / t["id"] / "mv.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _mv_view(t):
    f = DATA / "mv" / t["id"]
    job = JOBS.get("mv_" + t["id"])
    return {**{k: v for k, v in t.items() if k != "lrc"},
            "outputs": [x for x in "hv" if (f / f"{x}.mp4").exists()],
            "drawn": [i for i in range(len(t.get("images") or [])) if (f / f"img_{i:02d}.jpg").exists()],
            "job": {k: v for k, v in job.items() if k != "stop"} if job else None}


class MvReq(BaseModel):
    song: str
    length: int = 0
    formats: str = "hv"
    singer: str = "f"
    images: int = 12
    theme: str = "love"
    script: str = ""


def _mv_script(text):
    """Kịch bản tự viết: mỗi dòng một cảnh (bỏ dòng trống, số thứ tự / gạch đầu dòng)."""
    rows = [re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", x).strip()[:300] for x in (text or "").splitlines()]
    return [x for x in rows if x][:mv.MAX_SCRIPT]


@app.get("/api/mv-key")
def mv_key_get():
    return {"has": bool(_mv_key())}


class MvKey(BaseModel):
    key: str


@app.put("/api/mv-key")
def mv_key_put(k: MvKey):
    key = k.key.strip()
    if not re.fullmatch(r"(sk|pk)_[\w-]{8,200}", key):
        raise HTTPException(400, "Key Pollinations có dạng sk_… (lấy ở enter.pollinations.ai)")
    MV_KEY_FILE.write_text(json.dumps({"key": key}), encoding="utf-8")
    return {"has": True}


@app.delete("/api/mv-key")
def mv_key_delete():
    MV_KEY_FILE.unlink(missing_ok=True)
    return {"has": False}


@app.post("/api/mv")
def mv_create(req: MvReq):
    song = req.song.strip()
    if not song:
        raise HTTPException(400, "Nhập tên bài hát (Ca sĩ - Tên bài) hoặc link YouTube của bài")
    if req.length not in TT_LENGTHS or req.formats not in MV_FORMATS or req.singer not in mv.SINGER \
            or req.images not in MV_IMAGES or req.theme not in mv.THEMES:
        raise HTTPException(400, "Tuỳ chọn không hợp lệ")
    script = _mv_script(req.script)
    if req.theme == "script" and not script:
        raise HTTPException(400, "Nhập kịch bản: mỗi dòng một cảnh muốn vẽ")
    mid = uuid.uuid4().hex[:12]
    (DATA / "mv" / mid).mkdir(parents=True)
    t = {"id": mid, "created": time.time(), "input": song[:200], "length": req.length, "formats": req.formats,
         "singer": req.singer, "n_img": req.images, "theme": req.theme, "script": script, "offset": 0.0, "title": song[:80], "warn": "", "stamp": "", "vi": {}, "img_v": {}}
    _mv_save(t)
    _mv_start(mid)
    return _mv_view(t)


@app.get("/api/mv")
def mv_list():
    root = DATA / "mv"
    items = []
    for f in root.glob("*/mv.json") if root.exists() else []:
        try:
            items.append(_mv_view(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return sorted(items, key=lambda t: -t["created"])


class MvEdit(BaseModel):
    vi: dict[str, str] | None = None
    caption: str | None = None
    offset: float | None = None
    singer: str | None = None
    theme: str | None = None
    script: str | None = None


@app.put("/api/mv/{mid}")
def mv_update(mid: str, e: MvEdit):
    t = _mv_load(mid)
    if e.caption is not None:
        t["caption"] = e.caption[:2200]
    if e.vi is not None:
        new = {k: v.strip()[:300] for k, v in e.vi.items() if k in t.get("vi", {})}
        if any(t["vi"].get(k) != v for k, v in new.items()):
            t["vi"].update(new)
            t["dirty"] = True
    if e.offset is not None and round(e.offset, 2) != t.get("offset", 0):
        t["offset"] = max(-10.0, min(10.0, round(e.offset, 2)))
        t["dirty"] = True
    if e.singer in mv.SINGER and e.singer != t["singer"]:
        t["singer"] = e.singer           # chỉ ảnh vẽ lại sau đó mới theo ca sĩ mới
    if e.theme is not None:
        if e.theme not in mv.THEMES:
            raise HTTPException(400, "Chủ đề không hợp lệ")
        script = _mv_script(e.script) if e.script is not None else t.get("script", [])
        if e.theme == "script" and not script:
            raise HTTPException(400, "Nhập kịch bản: mỗi dòng một cảnh muốn vẽ")
        if e.theme != t.get("theme", "stage") or script != t.get("script", []):
            job = JOBS.get("mv_" + mid)
            if job and not job["done"]:
                raise HTTPException(409, "MV đang dựng, chờ xong hoặc bấm Huỷ")
            t.update(theme=e.theme, script=script, img_v={}, dirty=True)   # lên cảnh + vẽ lại cả bộ ảnh
            t.pop("scenes", None)
            t.pop("images", None)
            for img in _mv_dir(mid).glob("img_*.jpg"):
                img.unlink()
    _mv_save(t)
    return _mv_view(t)


@app.post("/api/mv/{mid}/build")
def mv_build(mid: str):
    _mv_dir(mid)
    _mv_start(mid)
    return _mv_view(_mv_load(mid))


@app.post("/api/mv/{mid}/cancel")
def mv_cancel(mid: str):
    _mv_dir(mid)
    return cancel("mv_" + mid)


class MvRedraw(BaseModel):
    prompt: str = ""


def _mv_image_ok(t, i):
    if not 0 <= i < len(t.get("images") or []):
        raise HTTPException(404, "Không có ảnh này")


@app.post("/api/mv/{mid}/images/{i}/redraw")
def mv_redraw(mid: str, i: int, r: MvRedraw):
    t = _mv_load(mid)
    _mv_image_ok(t, i)

    def work(job):
        job["stage"] = f"Đang vẽ lại ảnh {i + 1}"
        cur = _mv_load(mid)
        im = cur["images"][i]
        im["seed"] = random.randint(1, 10 ** 9)
        if r.prompt.strip():
            im["prompt"] = r.prompt.strip()[:800]
        elif not im.get("custom"):
            im["prompt"] = mv.prompt(im, cur["singer"], im.get("en", ""), cur.get("theme", "stage"))
        im["custom"] = bool(r.prompt.strip()) or im.get("custom", False)
        mv.draw(im["prompt"], im["seed"], _mv_dir(mid) / f"img_{i:02d}.jpg", _mv_key(), lambda: job["stop"],
                lambda sec: job.update(stage=f"Đang chờ lượt vẽ miễn phí (~{sec} giây)"))
        cur = _mv_load(mid)                 # đọc lại: người dùng có thể vừa sửa lời dịch
        cur["images"][i] = im
        cur["img_v"][str(i)] = uuid.uuid4().hex[:6]
        cur["dirty"] = True
        _mv_save(cur)
        job["progress"] = 1.0

    _start_job("mv_" + mid, "draw", work)
    return _mv_view(t)


@app.post("/api/mv/{mid}/images/{i}")
async def mv_upload_image(mid: str, i: int, file: UploadFile = File(...)):
    t = _mv_load(mid)
    _mv_image_ok(t, i)
    job = JOBS.get("mv_" + mid)
    if job and not job["done"]:
        raise HTTPException(409, "MV đang dựng, chờ xong hoặc bấm Huỷ")
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise HTTPException(400, "File không phải ảnh") from None
    img.save(_mv_dir(mid) / f"img_{i:02d}.jpg", "JPEG", quality=92)
    t["img_v"][str(i)] = uuid.uuid4().hex[:6]
    t["images"][i]["custom"] = True
    t["dirty"] = True
    _mv_save(t)
    return _mv_view(t)


@app.get("/api/mv/{mid}/files/{name}")
def mv_files(mid: str, name: str, download: bool = False):
    if not re.fullmatch(r"[hv]\.mp4|thumb\.jpg|img_\d{2}\.jpg", name):
        raise HTTPException(404)
    path = _mv_dir(mid) / name
    if not path.exists():
        raise HTTPException(404)
    if download:
        nice = "".join(ch for ch in _mv_load(mid)["title"] if ch.isalnum() or ch in " -_").strip() or "mv"
        tag = {"h.mp4": " 16x9", "v.mp4": " 9x16", "thumb.jpg": " thumbnail"}.get(name, "")
        return FileResponse(path, filename=f"{nice}{tag}{path.suffix}")
    return FileResponse(path)


@app.delete("/api/mv/{mid}")
def mv_delete(mid: str):
    job = JOBS.get("mv_" + mid)
    if job and not job["done"]:
        raise HTTPException(409, "MV đang dựng, bấm Huỷ trước")
    shutil.rmtree(_mv_dir(mid))
    JOBS.pop("mv_" + mid, None)
    return {"ok": True}


def _mv_mark(mid, on):
    """Đánh dấu MV đang dựng trong mv.json: server tắt giữa chừng thì lần chạy sau tự dựng tiếp."""
    t = _mv_load(mid)
    if t.get("building") != on:
        t["building"] = on
        _mv_save(t)


def _mv_start(mid):
    def work(job):
        _mv_mark(mid, True)
        try:
            return _mv_build(mid, job)
        finally:
            _mv_mark(mid, False)
    _start_job("mv_" + mid, "build", work)


def _mv_resume():
    """Khi server chạy lại: dựng tiếp các MV đang dựng dở (ảnh đã vẽ được giữ, không vẽ lại)."""
    for f in (DATA / "mv").glob("*/mv.json") if (DATA / "mv").exists() else []:
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("building"):
                _mv_start(f.parent.name)
        except (OSError, ValueError, HTTPException):
            continue


def _mv_draw_all(t, f, job):
    """Vẽ các ảnh còn thiếu (lần lượt: gói miễn phí chỉ cho 1 ảnh mỗi lúc)."""
    key = _mv_key()
    todo = [i for i in range(len(t["images"])) if not (f / f"img_{i:02d}.jpg").exists()]
    for n, i in enumerate(todo):
        im = t["images"][i]
        label = f"Đang vẽ ảnh {n + 1}/{len(todo)}" + ("" if key else " (gói miễn phí ~45 giây/ảnh)")
        job["stage"] = label
        mv.draw(im["prompt"], im["seed"], f / f"img_{i:02d}.jpg", key, lambda: job["stop"],
                lambda sec, label=label: job.update(stage=f"{label}: chờ lượt vẽ {sec} giây"))
        job["progress"] = 0.15 + 0.5 * (n + 1) / len(todo)


def _mv_build(mid, job):
    f = _mv_dir(mid)
    t = _mv_load(mid)
    if not t.get("lrc"):
        _tt_resolve(t, job)
        _mv_save(t)
    if job["stop"]:
        raise InterruptedError
    job["stage"], job["progress"] = "Đang tải bài hát", 0.08
    cache = DATA / "music_cache"
    cache.mkdir(exist_ok=True)
    audio = music.download_audio(t["audio_vid"], cache / f"{t['audio_vid']}_raw.flac", trim=False)
    dur = compose.probe(audio)["duration"] or t["audio_dur"]
    ls = lyrics.lines(lyrics.parse_lrc(t["lrc"]), dur)
    if "a" not in t:
        t["a"], t["b"] = _lyric_window(t, audio, ls, dur, job)
    a, b = t["a"], t["b"]
    plain = lyrics.timeline(ls, a, b)                  # cảnh đổi theo câu (không theo lệch lời)
    t["lines"] = list(dict.fromkeys(x for _, _, x in plain))
    t["lang"] = t.get("lang") or mv.lang_of(t["lines"])
    if t["lang"] != "vi":
        missing = [x for x in t["lines"] if x not in t["vi"]]
        if missing:
            job["stage"], job["progress"] = "Đang dịch lời sang tiếng Việt", 0.12
            t["vi"].update(zip(missing, dub.translate(missing, t["lang"])))
    if not t.get("scenes"):
        job["stage"] = "Đang lên cảnh cho MV"
        theme = t.get("theme", "stage")          # MV làm trước khi có chủ đề: kiểu sân khấu
        scenes, images = mv.plan(plain, b - a, t.get("n_img", mv.MAX_IMAGES), theme, t.get("script") or [])
        en = {}
        for kind, lang in (("story", t["lang"]), ("script", "auto")):
            src = list(dict.fromkeys(im["line"] for im in images if im["kind"] == kind and im["line"]))
            try:
                en.update(zip(src, dub.translate(src, lang, "en")) if lang != "en" else {x: x for x in src})
            except Exception:
                pass
        for im in images:
            im.update(en=en.get(im["line"], ""), seed=random.randint(1, 10 ** 9))
            im["prompt"] = mv.prompt(im, t["singer"], im["en"], theme)
        t["scenes"], t["images"] = scenes, images
    _mv_save(t)
    _mv_draw_all(t, f, job)
    if job["stop"]:
        raise InterruptedError
    tl = lyrics.timeline(ls, a, b, t.get("offset", 0.0))
    imgs = [f / f"img_{i:02d}.jpg" for i in range(len(t["images"]))]
    sub = "Vietsub" if t["lang"] != "vi" else "Lyrics"
    work = f / "work"
    _fresh_dir(work)
    fmts = list(t["formats"])
    for k, fmt in enumerate(fmts):
        name = "bản ngang 16:9" if fmt == "h" else "bản dọc 9:16"
        job["stage"] = f"Đang dựng {name}"
        lo, span = 0.65 + 0.33 * k / len(fmts), 0.33 / len(fmts)
        tmp = f / f"{fmt}.tmp.mp4"
        mv.render(t["scenes"], imgs, tl, t["vi"] if t["lang"] != "vi" else {}, t["song"], t.get("artist", ""),
                  audio, a, b, fmt, tmp, work, lambda: job["stop"],
                  lambda x, lo=lo, span=span: job.update(progress=lo + span * x), sub)
        tmp.replace(f / f"{fmt}.mp4")
    for x in "hv":
        if x not in fmts:
            (f / f"{x}.mp4").unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    cover = next((imgs[i] for i, im in enumerate(t["images"]) if im["kind"] == "singer"), imgs[0])
    mv.thumbnail(cover, t["song"], t.get("artist", ""), f / "thumb.jpg", sub)
    cur = _mv_load(mid)
    for k in ("lrc", "lrc_dur", "audio_vid", "audio_dur", "artist", "song", "title", "warn", "a", "b", "lines",
              "lang", "scenes", "images"):
        if k in t:
            cur[k] = t[k]
    cur["vi"] = {**t["vi"], **cur.get("vi", {})}
    cur["stamp"], cur["duration"], cur["dirty"] = uuid.uuid4().hex[:8], round(b - a, 1), False
    cur["yt_title"], cur["yt_desc"] = mv.youtube_text(cur["song"], cur.get("artist", ""), cur["lang"], tl, cur["vi"])
    if not cur.get("caption"):
        cur["caption"] = _caption_text("mv", cur, "tiktok", None)
    _mv_save(cur)
    job["progress"] = 1.0
    return {"duration": cur["duration"]}


# ---------- 🎬 ghép clip kênh của mình + dịch, lồng tiếng Việt ----------
DUB_ORIG = ("duck", "mute")
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv")


def _dub_dir(did):
    f = DATA / "dub" / did
    if not did.isalnum() or not (f / "dub.json").exists():
        raise HTTPException(404, "Không tìm thấy phim này")
    return f


def _dub_load(did):
    return json.loads((_dub_dir(did) / "dub.json").read_text(encoding="utf-8"))


def _dub_save(d):
    path = DATA / "dub" / d["id"] / "dub.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _dub_view(d):
    job = JOBS.get("dub_" + d["id"])
    return {**d, "output": (DATA / "dub" / d["id"] / "output.mp4").exists(),
            "job": {k: v for k, v in job.items() if k != "stop"} if job else None}


def _dub_key(url):
    m = re.search(r"/video/(\d+)|modal_id=(\d+)|/note/(\d+)", url)
    return (m and next(g for g in m.groups() if g)) or hashlib.md5(url.encode()).hexdigest()[:12]


class DubReq(BaseModel):
    source: str = ""
    owner: bool = False


def _douyin_cookie_view():
    f = dub.DOUYIN_COOKIES
    return {"has": f.exists(), "updated": f.stat().st_mtime if f.exists() else None}


@app.get("/api/dub-cookies")
def douyin_cookies_get():
    return _douyin_cookie_view()


@app.post("/api/dub-cookies")
async def douyin_cookies_upload(file: UploadFile):
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(400, "File quá lớn: chỉ cần cookie của douyin.com")
    text = raw.decode("utf-8", "replace")
    try:
        dub.check_douyin_cookies(text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    dub.DOUYIN_COOKIES.parent.mkdir(exist_ok=True)
    dub.DOUYIN_COOKIES.write_text(text, encoding="utf-8")
    return _douyin_cookie_view()


@app.delete("/api/dub-cookies")
def douyin_cookies_delete():
    dub.DOUYIN_COOKIES.unlink(missing_ok=True)
    return _douyin_cookie_view()


@app.post("/api/dub")
def dub_create(req: DubReq):
    if not req.owner:
        raise HTTPException(400, "Chỉ dùng cho kênh của bạn hoặc kênh đã cho phép bạn dùng: hãy tích ô xác nhận")
    links = dub.parse_links(req.source)
    channel = len(links) == 1 and bool(re.search(r"tiktok\.com/@[\w.-]+/?(\?|$)", links[0]) or (
        "douyin.com/user/" in links[0] and "modal_id=" not in links[0]))
    if req.source.strip() and not links:
        raise HTTPException(400, "Không thấy link nào. Dán link kênh hoặc link từng video (mỗi dòng một link)")
    if len(links) > dub.MAX_CLIPS:
        raise HTTPException(400, f"Tối đa {dub.MAX_CLIPS} video một phim")
    did = uuid.uuid4().hex[:12]
    (DATA / "dub" / did).mkdir(parents=True)
    d = {"id": did, "created": time.time(), "input": req.source.strip()[:2000], "title": "Phim ghép",
         "clips": [] if channel else [{"key": _dub_key(u), "url": u, "title": "", "duration": 0, "ts": 0, "on": True}
                                      for u in links],
         "opts": {"dub": True, "subs": True, "orig": "duck", "voice": "female"}, "segs": {}, "stamp": "", "warn": ""}
    _dub_save(d)
    if channel:
        _start_job("dub_" + did, "list", lambda job: _dub_list(did, links[0], job))
    elif links:
        _start_job("dub_" + did, "scan", lambda job: _dub_scan(did, job))
    return _dub_view(d)


def _dub_list(did, url, job):
    job["stage"] = "Đang lấy danh sách video của kênh"
    name, clips = dub.list_channel(url)
    d = _dub_load(did)
    d["clips"], d["title"] = clips, name or d["title"]
    _dub_save(d)
    return _dub_scan(did, job, 0.05)


@app.get("/api/dub")
def dub_list():
    root = DATA / "dub"
    items = []
    for f in root.glob("*/dub.json") if root.exists() else []:
        try:
            items.append(_dub_view(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return sorted(items, key=lambda d: -d["created"])


@app.post("/api/dub/{did}/upload")
def dub_upload(did: str, files: list[UploadFile] = File(...)):
    f = _dub_dir(did)
    d = _dub_load(did)
    (f / "src").mkdir(exist_ok=True)
    for up in files:
        ext = Path(up.filename or "").suffix.lower()
        if ext not in VIDEO_EXT:
            raise HTTPException(400, f"{up.filename}: chỉ nhận file video {', '.join(VIDEO_EXT)}")
        if len(d["clips"]) >= dub.MAX_CLIPS:
            raise HTTPException(400, f"Tối đa {dub.MAX_CLIPS} video một phim")
        key = "up" + uuid.uuid4().hex[:10]
        with open(f / "src" / f"{key}{ext}", "wb") as out:
            shutil.copyfileobj(up.file, out)
        info = dub.probe(f / "src" / f"{key}{ext}")
        if not info["duration"]:
            (f / "src" / f"{key}{ext}").unlink()
            raise HTTPException(400, f"{up.filename}: không đọc được video")
        d["clips"].append({"key": key, "url": "", "title": Path(up.filename).stem[:120],
                           "duration": round(info["duration"], 1), "ts": 0, "on": True})
    _dub_save(d)
    return _dub_view(d)


class DubEdit(BaseModel):
    title: str | None = None
    order: list[str] | None = None          # thứ tự clip (theo key)
    on: dict[str, bool] | None = None
    opts: dict | None = None
    segs: dict[str, list[str]] | None = None    # key -> bản dịch đã sửa (theo thứ tự câu)


@app.put("/api/dub/{did}")
def dub_update(did: str, e: DubEdit):
    job = JOBS.get("dub_" + did)
    if job and not job["done"]:
        raise HTTPException(409, "Đang chạy, chờ xong hoặc bấm Huỷ")
    d = _dub_load(did)
    if e.title is not None:
        d["title"] = e.title.strip()[:100] or d["title"]
    if e.order is not None:
        pos = {k: i for i, k in enumerate(e.order)}
        d["clips"].sort(key=lambda c: pos.get(c["key"], len(pos)))
    for c in d["clips"]:
        if e.on and c["key"] in e.on:
            c["on"] = bool(e.on[c["key"]])
    if e.opts:
        o = d["opts"]
        o["dub"] = bool(e.opts.get("dub", o["dub"]))
        o["subs"] = bool(e.opts.get("subs", o["subs"]))
        o["orig"] = e.opts.get("orig") if e.opts.get("orig") in DUB_ORIG else o["orig"]
        o["voice"] = e.opts.get("voice") if e.opts.get("voice") in dub.VOICES else o["voice"]
    for key, texts in (e.segs or {}).items():
        items = d["segs"].get(key, {}).get("items", [])
        for g, t in zip(items, texts):
            g["tr"] = t.strip()[:500]
    _dub_save(d)
    return _dub_view(d)


@app.post("/api/dub/{did}/build")
def dub_build(did: str):
    d = _dub_load(did)
    if not any(c["on"] for c in d["clips"]):
        raise HTTPException(400, "Chưa chọn video nào")
    _start_job("dub_" + did, "build", lambda job: _dub_build(did, job))
    return _dub_view(d)


@app.post("/api/dub/{did}/cancel")
def dub_cancel(did: str):
    _dub_dir(did)
    return cancel("dub_" + did)


@app.get("/api/dub/{did}/files/output.mp4")
def dub_file(did: str, download: bool = False):
    path = _dub_dir(did) / "output.mp4"
    if not path.exists():
        raise HTTPException(404)
    if download:
        nice = "".join(ch for ch in _dub_load(did)["title"] if ch.isalnum() or ch in " -_").strip() or "phim"
        return FileResponse(path, filename=f"{nice} long tieng.mp4")
    return FileResponse(path)


@app.delete("/api/dub/{did}")
def dub_delete(did: str):
    job = JOBS.get("dub_" + did)
    if job and not job["done"]:
        raise HTTPException(409, "Đang chạy, bấm Huỷ trước")
    shutil.rmtree(_dub_dir(did))
    JOBS.pop("dub_" + did, None)
    return {"ok": True}


def _dub_src(f, key):
    return next((p for p in (f / "src").glob(f"{key}.*") if p.suffix in VIDEO_EXT), None)


def _dub_prepare(d, f, clips, job, lo, hi, transcribe=True):
    """Tải + nhận giọng + dịch các clip (lưu lại, lần sau không làm lại; bản dịch đã sửa được giữ).
    -> ([(clip, file, thông tin)], [cảnh báo]). Tiến độ quy về khoảng [lo, hi]."""
    (f / "src").mkdir(exist_ok=True)
    ok, warns, n = [], [], max(1, len(clips))
    mid = lo + (hi - lo) * (0.4 if transcribe else 1.0)
    for i, c in enumerate(clips):
        if job["stop"]:
            raise InterruptedError
        job["stage"], job["progress"] = f"Đang tải video {i + 1}/{len(clips)}", lo + (mid - lo) * i / n
        src = _dub_src(f, c["key"])
        if not src:
            if not c["url"]:
                warns.append(f"Thiếu file của “{c['title']}”")
                c["error"] = "Thiếu file"
                continue
            try:
                src, info = dub.download(c["url"], f / "src", c["key"])
            except dub.DubError as e:
                warns.append(str(e))
                c["error"] = str(e)[:200]
                continue
            if info:
                c["title"] = c["title"] or (info.get("title") or info.get("description") or "")[:120]
                c["ts"] = c.get("ts") or info.get("timestamp") or 0
                if d["title"] == "Phim ghép" and info.get("uploader"):
                    d["title"] = info["uploader"]
        c.pop("error", None)
        meta = dub.probe(src)
        c["duration"] = round(meta["duration"], 1)
        ok.append((c, src, meta))
    _dub_save(d)
    if not transcribe:
        return ok, warns
    try:
        for i, (c, src, meta) in enumerate(ok):
            if job["stop"]:
                raise InterruptedError
            if c["key"] in d["segs"]:
                continue
            job["progress"] = mid + (hi - mid) * i / len(ok)
            job["stage"] = (f"Đang nghe thoại video {i + 1}/{len(ok)}"
                            + (" (lần đầu tải model khoảng 500MB)" if dub._MODEL is None else ""))
            lang, items = dub.transcribe(src) if meta["audio"] else ("", [])
            job["stage"] = f"Đang dịch video {i + 1}/{len(ok)}"
            for g, tr in zip(items, dub.translate([g["src"] for g in items], lang)):
                g["tr"] = tr
            d["segs"][c["key"]] = {"lang": lang, "items": items}
            _dub_save(d)
    finally:
        dub.unload()
    return ok, warns


@app.post("/api/dub/{did}/scan")
def dub_scan(did: str):
    d = _dub_load(did)
    if not d["clips"]:
        raise HTTPException(400, "Chưa có video nào")
    _start_job("dub_" + did, "scan", lambda job: _dub_scan(did, job))
    return _dub_view(d)


def _dub_scan(did, job, lo=0.0):
    """Xem nội dung tất cả clip trước khi ghép: tải, nghe thoại, dịch, ảnh xem trước; nhận số tập, clip trùng,
    clip không thoại; xếp theo tập (hoặc ngày đăng) và bỏ tích clip trùng / tải lỗi. Người dùng xem lại rồi mới ghép."""
    f = _dub_dir(did)
    d = _dub_load(did)
    ok, warns = _dub_prepare(d, f, d["clips"], job, lo, 0.95)
    job["stage"], job["progress"] = "Đang tạo ảnh xem trước", 0.96
    (f / "thumb").mkdir(exist_ok=True)
    for c, src, meta in ok:
        if not (f / "thumb" / f"{c['key']}.jpg").exists():
            dub.thumb(src, f / "thumb" / f"{c['key']}.jpg", min(1.0, meta["duration"] / 3))
    got = {c["key"] for c, _, _ in ok}
    rev, order = dub.review([c for c in d["clips"] if c["key"] in got], d["segs"])
    pos = {k: i for i, k in enumerate(order)}
    d["clips"].sort(key=lambda c: pos.get(c["key"], len(pos)))
    for c in d["clips"]:
        r = rev.get(c["key"])
        if not r or any(x.startswith("dup:") for x in r["flags"]):
            c["on"] = False
    d["review"], d["scanned"] = rev, True
    d["warn"] = " · ".join(warns[:5])
    _dub_save(d)
    return {"clips": len(ok)}


@app.get("/api/dub/{did}/thumb/{key}.jpg")
def dub_thumb(did: str, key: str):
    path = _dub_dir(did) / "thumb" / f"{key}.jpg"
    if not key.isalnum() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/dub/{did}/src/{key}")
def dub_src(did: str, key: str):
    path = _dub_src(_dub_dir(did), key) if key.isalnum() else None
    if not path:
        raise HTTPException(404)
    return FileResponse(path)


def _dub_build(did, job):
    f = _dub_dir(did)
    d = _dub_load(did)
    o = d["opts"]
    ok, warns = _dub_prepare(d, f, [c for c in d["clips"] if c["on"]], job, 0.0, 0.7, o["dub"] or o["subs"])
    if not ok:
        raise RuntimeError("Không tải được video nào. " + " · ".join(warns[:3]))

    # chuẩn hoá cùng khung hình
    W, H = dub.size_for([(m["w"], m["h"]) for _, _, m in ok])
    (f / "norm").mkdir(exist_ok=True)
    parts, timeline, at = [], [], 0.0
    for i, (c, src, meta) in enumerate(ok):
        if job["stop"]:
            raise InterruptedError
        job["stage"], job["progress"] = f"Đang chuẩn hoá video {i + 1}/{len(ok)}", 0.7 + 0.15 * i / len(ok)
        out = f / "norm" / f"{c['key']}_{W}x{H}.mp4"
        if not out.exists():
            dub.normalize(src, out.with_suffix(".tmp.mp4"), W, H, meta["audio"]).replace(out)
        length = dub.probe(out)["duration"]
        for g in d["segs"].get(c["key"], {}).get("items", []):
            if g["s"] < length:
                timeline.append({"s": at + g["s"], "e": at + min(g["e"], length), "tr": g.get("tr", "")})
        parts.append(out)
        at += length
    if job["stop"]:
        raise InterruptedError

    # dựng
    job["stage"], job["progress"] = "Đang lồng tiếng và dựng phim" if o["dub"] else "Đang dựng phim", 0.86
    work = f / "work"
    _fresh_dir(work)
    tmp = f / "output.tmp.mp4"
    def report(stage, frac):
        job["stage"], job["progress"] = stage, 0.86 + 0.1 * frac
    total, missed = dub.render(parts, timeline, tmp, work, W, H, dub=o["dub"], subs=o["subs"], orig=o["orig"],
                               voice=o["voice"], tts_cache=f / "tts", report=report, stop=lambda: job["stop"])
    tmp.replace(f / "output.mp4")
    shutil.rmtree(work, ignore_errors=True)
    d = _dub_load(did) | {"clips": d["clips"], "segs": d["segs"], "title": d["title"]}
    d["stamp"], d["duration"] = uuid.uuid4().hex[:8], round(total, 1)
    if missed:
        warns.append(f"{missed} câu không đọc được (dịch vụ giọng đọc lỗi), vẫn có phụ đề. Bấm Dựng lại để thử lại")
    d["warn"] = " · ".join(warns[:5])
    d["size"] = [W, H]
    d["caption"] = _caption_text("dub", d, "tiktok", None)
    _dub_save(d)
    return {"duration": d["duration"]}


# ---------- kênh YouTube: cấu hình, hàng chờ nhiều luồng, tự đăng ----------
ACC_LOCK = threading.Lock()
PICK_LOCK = threading.Lock()
TASKS, TASK_LOCK = [], threading.Lock()
PRIVACY = ("private", "unlisted", "public")
SHORT_GAP_H = 3                    # Shorts hẹn giờ công khai cách nhau 3 giờ (khi chọn đăng công khai)
_OAUTH_STATES = {}
_TREND_CACHE = {}                  # lĩnh vực -> (lúc nghiên cứu, kết quả): dùng chung cho các kênh trong 6 giờ


def _acc_file():
    return DATA / "accounts.json"


def _accounts():
    try:
        d = json.loads(_acc_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("client", {"id": "", "secret": ""})
    d.setdefault("workers", 2)
    d.setdefault("quota", {"date": "", "used": 0})
    d.setdefault("quota_limit", ytapi.DAILY_QUOTA)
    d.setdefault("accounts", [])
    if d["quota"]["date"] != time.strftime("%Y-%m-%d"):
        d["quota"] = {"date": time.strftime("%Y-%m-%d"), "used": 0}
    return d


def _save_accounts(d):
    tmp = DATA / "accounts.tmp"
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_acc_file())


def _account(d, cid):
    a = next((a for a in d["accounts"] if a["id"] == cid), None)
    if not a:
        raise HTTPException(404, "Không có kênh này")
    return a


ACC_DEFAULTS = {"enabled": True, "seeds": ["funny moments"], "mode": "hot", "minutes": 10, "voiceover": True,
                "voice": voice.DEFAULT_VOICE, "long": True, "shorts": True, "privacy": "private", "per_run": 1,
                "daily": "", "last_daily": "", "used": [],
                # lịch đăng: "now" = đăng ngay theo chế độ đăng; "slots" = hẹn giờ lên sóng theo khung giờ (giờ máy)
                "schedule": "now", "slots_long": ["19:00"], "slots_short": ["12:00", "17:00", "21:00"],
                "days": [0, 1, 2, 3, 4, 5, 6], "booked": [],
                # tăng tương tác thật: tự bình luận câu hỏi dưới video khi video công khai; xoay vòng mẫu thumbnail
                # để bảng thống kê so được mẫu nào nhiều view hơn
                "comment": True, "thumb_style": "rotate", "uploads": [],
                "shorts_n": SHORTS}               # số Shorts mỗi video (bỏ tick video dài = kênh chỉ làm Shorts)
THUMB_STYLES = {"1": "Sticker", "2": "Reaction", "3": "Split"}
HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
MIN_LEAD = 20 * 60                 # mốc hẹn giờ phải sau lúc tải lên ít nhất 20 phút (YouTube cần thời gian xử lý)


def _public_acc(a):
    now = time.time()
    out = {**ACC_DEFAULTS, **{k: v for k, v in a.items() if k not in ("refresh", "uploads")}}
    out["uploads"] = len(a.get("uploads", []))
    out["upcoming"] = sorted((b for b in a.get("booked", []) if b["at"] > now), key=lambda b: b["at"])[:12]
    return out


def _free_slots(a, kind, n, after):
    """n mốc lên sóng kế tiếp (timestamp, giờ máy) theo khung giờ + ngày trong tuần của kênh, sau `after`,
    bỏ các mốc đã có video khác đặt."""
    hm = sorted(a.get("slots_long" if kind == "long" else "slots_short") or [])
    days = set(a.get("days") or range(7))
    taken = {b["at"] for b in a.get("booked", [])}
    out, day = [], datetime.fromtimestamp(after).date()
    for k in range(400):
        d_ = day + timedelta(days=k)
        if d_.weekday() not in days:
            continue
        for x in hm:
            ts = datetime(d_.year, d_.month, d_.day, int(x[:2]), int(x[3:])).timestamp()
            if ts >= after and ts not in taken:
                out.append(ts)
                if len(out) == n:
                    return out
    return out


def _book(t, a, kind, n, after, fixed=()):
    """Giữ chỗ n mốc đăng cho task (dưới ACC_LOCK: luồng song song không lấy trùng mốc). fixed: mốc định sẵn."""
    with ACC_LOCK:
        d = _accounts()
        acc = _account(d, a["id"])
        fixed = list(fixed)[:n]
        got = fixed + (_free_slots(acc, kind, n - len(fixed), after) if n > len(fixed) else [])
        now = time.time()
        acc["booked"] = [b for b in acc.get("booked", []) if b["at"] > now - 7 * 86400][-150:] + \
            [{"at": ts, "kind": kind, "task": t["id"], "title": "", "url": ""} for ts in got]
        _save_accounts(d)
    return got


def _booked_update(a, t, at, **kw):
    with ACC_LOCK:
        d = _accounts()
        for b in _account(d, a["id"]).get("booked", []):
            if b["task"] == t["id"] and b["at"] == at:
                b.update(kw)
        _save_accounts(d)


def _unbook(a, t):
    """Task lỗi / huỷ: trả lại các mốc chưa dùng (chưa có video)."""
    with ACC_LOCK:
        d = _accounts()
        acc = _account(d, a["id"])
        acc["booked"] = [b for b in acc.get("booked", []) if b["task"] != t["id"] or b["url"]]
        _save_accounts(d)


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _tasks_view():
    with TASK_LOCK:
        out = []
        for t in TASKS[-60:][::-1]:
            job = JOBS.get(t.get("project")) if t["status"] == "running" and t.get("project") else None
            out.append({**{k: v for k, v in t.items() if k != "stop"},
                        **({"stage": job["stage"], "progress": job["progress"]}
                           if job and t["phase"] == "build" else {})})
        return out


@app.get("/api/channels")
def channels(request: Request):
    d = _accounts()
    return {"client": {"id": d["client"]["id"], "has_secret": bool(d["client"]["secret"])},
            "workers": d["workers"], "quota": {**d["quota"], "limit": d["quota_limit"]},
            "redirect": str(request.base_url), "accounts": [_public_acc(a) for a in d["accounts"]],
            "tasks": _tasks_view(), "voices": voice.VOICES}


class ChannelSettings(BaseModel):
    client_id: str | None = None
    client_secret: str | None = None
    workers: int | None = None
    quota_limit: int | None = None             # đã xin Google tăng hạn mức thì nhập số mới


@app.put("/api/channels/settings")
def channel_settings(s: ChannelSettings, request: Request):
    with ACC_LOCK:
        d = _accounts()
        if s.client_id is not None:
            d["client"]["id"] = s.client_id.strip()
        if s.client_secret:                        # để trống = giữ mật khẩu cũ
            d["client"]["secret"] = s.client_secret.strip()
        if s.workers is not None:
            d["workers"] = min(4, max(1, s.workers))
        if s.quota_limit is not None:
            d["quota_limit"] = max(1600, s.quota_limit)
        _save_accounts(d)
    _pump()
    return channels(request)


@app.get("/api/channels/connect")
def channel_connect(request: Request):
    d = _accounts()
    if not d["client"]["id"] or not d["client"]["secret"]:
        raise HTTPException(400, "Nhập Client ID và Client secret của Google trước")
    state = uuid.uuid4().hex
    redirect = str(request.base_url)
    _OAUTH_STATES[state] = redirect
    return {"url": ytapi.auth_url(d["client"]["id"], redirect, state)}


@app.get("/")
def root(request: Request, code: str = "", state: str = "", error: str = ""):
    """Trang chính; cũng là địa chỉ Google gửi mã về sau khi bạn đồng ý cho app đăng video lên kênh."""
    if not (code or error):
        return FileResponse(ROOT / "static" / "index.html")
    redirect = _OAUTH_STATES.pop(state, None)
    if error or not redirect:
        return RedirectResponse("/#/channels?err=" + urllib.parse.quote(error or "Phiên đăng nhập đã hết hạn, thử lại"))
    try:
        with ACC_LOCK:
            d = _accounts()
            tok = ytapi.exchange(d["client"]["id"], d["client"]["secret"], code, redirect)
            ch = ytapi.channel(tok["access_token"])
            a = next((a for a in d["accounts"] if a["id"] == ch["id"]), None)
            if a is None:
                a = {**ACC_DEFAULTS, "seeds": list(ACC_DEFAULTS["seeds"]), "used": []}
                d["accounts"].append(a)
            a.update(ch)
            if tok.get("refresh_token"):
                a["refresh"] = tok["refresh_token"]
            _save_accounts(d)
    except ytapi.ApiError as e:
        return RedirectResponse("/#/channels?err=" + urllib.parse.quote(str(e)))
    return RedirectResponse("/#/channels?ok=" + urllib.parse.quote(ch["title"]))


class AccountCfg(BaseModel):
    enabled: bool | None = None
    seeds: list[str] | None = None
    mode: str | None = None
    minutes: int | None = None
    voiceover: bool | None = None
    voice: str | None = None
    long: bool | None = None
    shorts: bool | None = None
    privacy: str | None = None
    per_run: int | None = None
    daily: str | None = None
    schedule: str | None = None
    slots_long: list[str] | None = None
    slots_short: list[str] | None = None
    days: list[int] | None = None
    comment: bool | None = None
    thumb_style: str | None = None
    shorts_n: int | None = None


def _slots(v, what):
    out = sorted({x.strip().zfill(5) for x in v if x.strip()})
    if any(not HHMM.fullmatch(x) for x in out):
        raise HTTPException(400, f"Giờ đăng {what} dạng HH:MM, cách nhau bằng dấu phẩy (vd 12:00, 19:30)")
    if not out:
        raise HTTPException(400, f"Nhập ít nhất một giờ đăng {what}")
    return out[:12]


@app.put("/api/channels/{cid}")
def channel_update(cid: str, cfg: AccountCfg, request: Request):
    with ACC_LOCK:
        d = _accounts()
        a = _account(d, cid)
        c = cfg.model_dump(exclude_none=True)
        if "seeds" in c:
            c["seeds"] = [x.strip()[:80] for x in c["seeds"] if x.strip()][:20]
            if not c["seeds"]:
                raise HTTPException(400, "Nhập ít nhất một chủ đề")
        if c.get("mode", "hot") not in ("hot", "keywords"):
            raise HTTPException(400, "Cách chọn chủ đề không hợp lệ")
        if c.get("privacy", "private") not in PRIVACY:
            raise HTTPException(400, "Chế độ đăng không hợp lệ")
        if "daily" in c and c["daily"] and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", c["daily"]):
            raise HTTPException(400, "Giờ chạy hằng ngày dạng HH:MM")
        if c.get("schedule", "now") not in ("now", "slots"):
            raise HTTPException(400, "Lịch đăng không hợp lệ")
        if "slots_long" in c:
            c["slots_long"] = _slots(c["slots_long"], "video dài")
        if "slots_short" in c:
            c["slots_short"] = _slots(c["slots_short"], "Shorts")
        if "days" in c:
            c["days"] = sorted({x for x in c["days"] if 0 <= x <= 6})
            if not c["days"]:
                raise HTTPException(400, "Chọn ít nhất một ngày đăng trong tuần")
        if c.get("thumb_style", "rotate") not in ("rotate", *THUMB_STYLES):
            raise HTTPException(400, "Mẫu thumbnail không hợp lệ")
        if "voice" in c and c["voice"] not in voice.VOICES:
            c["voice"] = voice.DEFAULT_VOICE
        if "minutes" in c:
            c["minutes"] = min(30, max(3, c["minutes"]))
        if "shorts_n" in c:
            c["shorts_n"] = min(SHORTS_MAX, max(1, c["shorts_n"]))
        if "per_run" in c:
            c["per_run"] = min(5, max(1, c["per_run"]))
        if not c.get("long", a.get("long", True)) and not c.get("shorts", a.get("shorts", True)):
            raise HTTPException(400, "Chọn ít nhất một loại video để đăng")
        a.update(c)
        _save_accounts(d)
    return channels(request)


@app.delete("/api/channels/{cid}")
def channel_delete(cid: str, request: Request):
    with ACC_LOCK:
        d = _accounts()
        _account(d, cid)
        d["accounts"] = [a for a in d["accounts"] if a["id"] != cid]
        _save_accounts(d)
    return channels(request)


WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def _insights(ups):
    """So sánh trung bình lượt xem theo nhóm (mẫu thumbnail, chủ đề, giờ / ngày lên sóng), tách video dài và Shorts."""
    now = time.time()
    out = []
    for kind, label in (("long", "🎬 Video dài"), ("short", "✂ Shorts")):
        vids = [u for u in ups if u["kind"] == kind and u["at"] <= now and "views" in u.get("stats", {})]
        if not vids:
            continue
        keys = [("Mẫu thumbnail", lambda u: THUMB_STYLES.get(u.get("thumb"), ""))] if kind == "long" else []
        keys += [("Chủ đề", lambda u: u.get("topic", "")),
                 ("Giờ lên sóng", lambda u: time.strftime("%H:00", time.localtime(u["at"]))),
                 ("Ngày lên sóng", lambda u: WEEKDAYS[time.localtime(u["at"]).tm_wday])]
        for name, key in keys:
            groups = {}
            for u in vids:
                k = key(u)
                if k:
                    groups.setdefault(k, []).append(u["stats"])
            if len(groups) < 2:
                continue
            rows = []
            for k, ss in groups.items():
                pct = [x["avg_pct"] for x in ss if "avg_pct" in x]
                rows.append({"key": k, "n": len(ss), "views": round(sum(x["views"] for x in ss) / len(ss)),
                             "avg_pct": round(sum(pct) / len(pct), 1) if pct else None,
                             "subs": sum(x.get("subs", 0) for x in ss)})
            rows.sort(key=lambda r: -r["views"])
            out.append({"kind": label, "by": name, "rows": rows[:8],
                        "sure": rows[0]["n"] >= 3 and all(r["n"] >= 2 for r in rows[:2])})
    return out


def _stats_view(a):
    ups = sorted(a.get("uploads", []), key=lambda u: -u["at"])
    return {"uploads": ups, "insights": _insights(ups), "updated": a.get("stats_at", 0),
            "note": a.get("stats_note", ""), "styles": THUMB_STYLES}


@app.get("/api/channels/{cid}/stats")
def channel_stats(cid: str):
    return _stats_view(_account(_accounts(), cid))


@app.post("/api/channels/{cid}/stats")
def channel_stats_refresh(cid: str):
    """Lấy lượt xem / like / bình luận (Data API, ~1 đơn vị mỗi 50 video) + thời gian xem, % xem hết, sub có thêm
    (YouTube Analytics API, nếu đã bật) cho các video app đã đăng."""
    a = _account(_accounts(), cid)
    ids = [u["id"] for u in a.get("uploads", [])]
    if not ids:
        raise HTTPException(400, "Kênh chưa có video nào do app đăng")
    try:
        token = _token(cid)
        _spend(ytapi.COST["stats"] * (len(ids) // 50 + 1))
        basic = ytapi.video_stats(token, ids)
    except (RuntimeError, ytapi.ApiError) as e:
        raise HTTPException(400, str(e)) from None
    note = ""
    first = min(u["at"] for u in a["uploads"])
    try:
        extra = ytapi.analytics(token, ids, time.strftime("%Y-%m-%d", time.localtime(first - 86400)),
                                time.strftime("%Y-%m-%d"))
    except ytapi.ApiError as e:
        extra = {}
        note = ("Chưa lấy được thời gian xem / % xem hết / sub: bật “YouTube Analytics API” trong Google Cloud "
                "rồi bấm “Kết nối lại” kênh này" + (f" ({e})" if "403" not in str(e) else "") + ".")
    with ACC_LOCK:
        d = _accounts()
        acc = _account(d, cid)
        for u in acc.get("uploads", []):
            if u["id"] in basic:
                u["stats"] = {**basic[u["id"]], **extra.get(u["id"], {})}
        acc["stats_at"], acc["stats_note"] = time.time(), note
        _save_accounts(d)
    return _stats_view(acc)


class RunReq(BaseModel):
    count: int | None = None
    publish_at: str | None = None               # "YYYY-MM-DDTHH:MM" giờ máy: hẹn giờ cụ thể cho video này


@app.post("/api/channels/{cid}/run")
def channel_run(cid: str, req: RunReq, request: Request):
    a = _account(_accounts(), cid)
    at = None
    if req.publish_at:
        try:
            at = datetime.strptime(req.publish_at[:16], "%Y-%m-%dT%H:%M").timestamp()
        except ValueError:
            raise HTTPException(400, "Giờ hẹn đăng không hợp lệ") from None
        if at < time.time() + 30 * 60:
            raise HTTPException(400, "Giờ hẹn đăng phải sau bây giờ ít nhất 30 phút (cần thời gian dựng + tải lên)")
        if at > time.time() + 365 * 86400:
            raise HTTPException(400, "Chỉ hẹn được trong vòng 1 năm")
    _enqueue(a, 1 if at else (req.count or a.get("per_run", 1)), at)
    return channels(request)


@app.post("/api/channels-run-all")
def channel_run_all(request: Request):
    accs = [a for a in _accounts()["accounts"] if a.get("enabled", True)]
    if not accs:
        raise HTTPException(400, "Chưa có kênh nào đang bật")
    for a in accs:
        _enqueue(a, a.get("per_run", 1))
    return channels(request)


@app.post("/api/tasks/{tid}/cancel")
def task_cancel(tid: str, request: Request):
    with TASK_LOCK:
        t = next((t for t in TASKS if t["id"] == tid), None)
        if not t:
            raise HTTPException(404)
        if t["status"] == "queued":
            t["status"], t["error"] = "cancelled", "Đã huỷ"
        elif t["status"] == "running":
            t["stop"] = True
            if t.get("project") and t["project"] in JOBS:
                JOBS[t["project"]]["stop"] = True
    return channels(request)


@app.delete("/api/tasks")
def tasks_clear(request: Request):
    with TASK_LOCK:
        TASKS[:] = [t for t in TASKS if t["status"] in ("queued", "running")]
    return channels(request)


def _enqueue(a, count, publish_at=None):
    with TASK_LOCK:
        for _ in range(min(5, max(1, count))):
            TASKS.append({"id": uuid.uuid4().hex[:10], "account": a["id"], "channel": a["title"], "status": "queued",
                          "publish_at": publish_at, "plan": [],
                          "phase": "", "stage": "Đang chờ", "progress": 0.0, "keyword": "", "project": None,
                          "video": None, "shorts": [], "error": None, "warn": "", "created": time.time(),
                          "stop": False})
    _pump()


def _pump():
    """Khởi động thêm task đang chờ tới khi đủ số luồng song song."""
    workers = _accounts()["workers"]
    with TASK_LOCK:
        running = sum(t["status"] == "running" for t in TASKS)
        for t in TASKS:
            if running >= workers:
                break
            if t["status"] == "queued":
                t["status"], running = "running", running + 1
                threading.Thread(target=_run_task, args=(t,), daemon=True).start()


def _run_task(t):
    try:
        _task_work(t)
        t["status"] = "done"
    except InterruptedError:
        t["status"], t["error"] = "cancelled", "Đã huỷ"
    except Exception as e:          # lỗi của một task không làm dừng các task khác
        t["status"], t["error"] = "error", str(e) or e.__class__.__name__
    finally:
        if t["status"] != "done" and t.get("plan"):
            try:
                _unbook({"id": t["account"]}, t)        # trả lại mốc giờ chưa đăng được
            except Exception:
                pass
        t["progress"] = 1.0 if t["status"] == "done" else t["progress"]
        _pump()


def _pick_topic(a):
    """Chủ đề cho video tiếp theo của kênh -> (từ khoá, video hot quét trước, tên chủ đề, khoá ghi nhận đã dùng).

    hot: nghiên cứu xu hướng từng lĩnh vực, lấy chủ đề hot nhất chưa dùng gần đây; keywords: xoay vòng các từ khoá đã nhập.
    """
    used = a.get("used", [])
    last = {u: i for i, u in enumerate(used)}
    seeds = a.get("seeds") or ["funny moments"]
    if a.get("mode", "hot") == "keywords":
        kw = min(seeds, key=lambda s: last.get(s, -1))           # lâu nhất chưa dùng
        return kw, [], kw, kw
    seed = min(seeds, key=lambda s: sum(u.startswith(s + "|") for u in used))
    at, res = _TREND_CACHE.get(seed, (0, None))
    if not res or time.time() - at > 6 * 3600:
        res = trends.research(seed, 8, deep=False)
        _TREND_CACHE[seed] = (time.time(), res)
    recent = set(used[-30:])
    for topic in res["topics"]:
        key = f"{seed}|{topic['keyword']}"
        if key not in recent:
            return topic["keyword"], [v["id"] for v in topic["videos"][:8]], topic["name"], key
    best = res["keywords"][0] if res["keywords"] else {"keyword": seed, "videos": []}
    return best["keyword"], [v["id"] for v in best["videos"][:8]], best["keyword"], f"{seed}|{best['keyword']}"


class QuotaError(RuntimeError):
    pass


def _quota_left():
    d = _accounts()
    return d["quota_limit"] - d["quota"]["used"]


def _spend(units):
    with ACC_LOCK:
        d = _accounts()
        if d["quota"]["used"] + units > d["quota_limit"]:
            raise QuotaError(f"Hết hạn mức YouTube API hôm nay ({d['quota']['used']}/{d['quota_limit']}). "
                             "Hạn mức tính chung cho mọi kênh dùng cùng Client ID, mai chạy lại.")
        d["quota"]["used"] += units
        _save_accounts(d)


def _token(cid):
    d = _accounts()
    a = _account(d, cid)
    if not a.get("refresh"):
        raise RuntimeError("Kênh chưa đăng nhập Google (bấm Kết nối lại)")
    try:
        return ytapi.refresh(d["client"]["id"], d["client"]["secret"], a["refresh"])["access_token"]
    except ytapi.ApiError as e:
        raise RuntimeError(f"Đăng nhập Google của kênh đã hết hạn, bấm “Kết nối lại” ({e})") from None


def _task_work(t):
    a = _account(_accounts(), t["account"])
    if _quota_left() < ytapi.COST["upload"]:        # báo trước, không mất công dựng video rồi mới biết
        raise QuotaError(f"Hết hạn mức YouTube API hôm nay (còn {_quota_left()}, mỗi video cần "
                         f"{ytapi.COST['upload']}). Mai chạy lại, hoặc xin Google tăng hạn mức.")
    t["phase"], t["stage"] = "topic", "Đang kiểm tra đăng nhập kênh"
    _token(a["id"])                                  # đăng nhập hỏng thì báo ngay, không dựng video vô ích
    t["stage"] = "Đang chọn chủ đề hot"
    with PICK_LOCK:                          # chọn + ghi nhận liền một mạch: luồng song song không trùng chủ đề
        keyword, seed_videos, ref, used_key = _pick_topic(_account(_accounts(), a["id"]))
        with ACC_LOCK:
            d = _accounts()
            acc = _account(d, a["id"])
            acc["used"] = (acc.get("used", []) + [used_key])[-60:]
            _save_accounts(d)
    t["keyword"] = ref
    if t["stop"]:
        raise InterruptedError
    long_ = a.get("long", True)
    p = _new_project(CreateReq(keyword=keyword, videos=40, length=10 if long_ else 20, minutes=a.get("minutes", 10),
                               auto=True,
                               voiceover=a.get("voiceover", True), voice=a.get("voice", voice.DEFAULT_VOICE),
                               shorts=a.get("shorts", True), seed_videos=seed_videos,
                               kind="long" if long_ else "shorts", count=a.get("shorts_n", SHORTS),
                               ref=f"{ref} · kênh {a['title']}"))
    pid = t["project"] = p["id"]
    t["phase"] = "build"
    job = _new_job(pid, "auto")
    try:
        job["result"] = res = _auto(pid, 40, job)
    except InterruptedError:
        job["error"] = "Đã huỷ"
        raise
    except Exception as e:
        job["error"] = str(e) or e.__class__.__name__
        raise
    finally:
        job["done"] = True
    if res.get("note"):
        t["warn"] = res["note"]
    # tải lên
    t["phase"], t["progress"] = "upload", 0.0
    p = load(pid)
    f = folder(pid)
    y = p["youtube"]
    privacy = a.get("privacy", "private")
    stop = lambda: t["stop"]
    token = _token(a["id"])                          # lấy lại: mã đăng nhập chỉ dùng được 1 giờ, dựng có thể lâu hơn
    slots = a.get("schedule", "now") == "slots"
    fixed = t.get("publish_at")
    scheduled = slots or bool(fixed)                 # hẹn giờ: tải lên ở chế độ riêng tư, tới giờ YouTube tự công khai
    after = time.time() + MIN_LEAD
    long_at = None
    if a.get("long", True):
        _spend(ytapi.COST["upload"])
        if scheduled:
            long_at = (_book(t, a, "long", 1, after, [fixed] if fixed else []) or [None])[0]
            if long_at:
                t["plan"].append({"kind": "long", "at": long_at})
        t["stage"] = "Đang tải video lên YouTube"
        meta = ytapi.clean_meta(y["title"], y["description"], y["tags"].split(", "), privacy,
                                _iso(long_at) if long_at else None)

        def prog(x):
            t["progress"] = 0.8 * x
        vid = ytapi.upload(token, f / "output.mp4", meta, prog, stop)
        t["video"] = f"https://youtu.be/{vid}"
        if long_at:
            _booked_update(a, t, long_at, title=y["title"], url=t["video"])
        style = _thumb_style(a)
        thumb = f / f"thumb_{style}.jpg"
        try:
            _spend(ytapi.COST["thumbnail"])
            ytapi.set_thumbnail(token, vid, thumb if thumb.exists() else f / "thumbnail.jpg")
        except (ytapi.ApiError, QuotaError) as e:
            style = ""
            t["warn"] = (t["warn"] + " " if t["warn"] else "") + f"Không đặt được thumbnail ({e})."
        _record(a, t, vid, "long", y["title"], long_at, privacy, y.get("pin"), thumb=style)
    if a.get("shorts", True):
        items = p.get("shorts_out", [])
        short_at = [None] * len(items)
        if scheduled and items:
            base = max(after, (long_at or 0) + 60)   # Shorts lên sóng sau video dài (link "Full video" đã xem được)
            if slots:
                got = _book(t, a, "short", len(items), base)
            else:                                    # chỉ hẹn giờ cụ thể: Shorts cách nhau SHORT_GAP_H giờ sau đó
                start = long_at + SHORT_GAP_H * 3600 if long_at else fixed
                got = _book(t, a, "short", len(items), base,
                            [start + SHORT_GAP_H * 3600 * i for i in range(len(items))])
            short_at = got + [None] * (len(items) - len(got))
            t["plan"] += [{"kind": "short", "at": x} for x in got]
        for i, s in enumerate(items):
            if t["stop"]:
                raise InterruptedError
            t["stage"] = f"Đang tải Shorts {i + 1}/{len(items)} lên"
            try:
                _spend(ytapi.COST["upload"])
            except QuotaError:
                t["warn"] = (t["warn"] + " " if t["warn"] else "") + \
                    f"Hết hạn mức API: {len(items) - i} Shorts chưa đăng (tải về ở trang dự án hoặc mai đăng)."
                break
            desc = s["description"] + (f"\n\n▶ Full video: {t['video']}" if t.get("video") else "")
            at = short_at[i]
            if not scheduled and privacy == "public" and i:   # không hẹn giờ: rải Shorts, cái đầu đăng ngay
                at = time.time() + SHORT_GAP_H * 3600 * i
            meta = ytapi.clean_meta(s["title"], desc, ["shorts", *y["tags"].split(", ")[:10]], privacy,
                                    _iso(at) if at else None)
            sid = ytapi.upload(token, f / s["file"], meta, None, stop)
            t["shorts"].append(f"https://youtube.com/shorts/{sid}")
            _record(a, t, sid, "short", s["title"], at, privacy,
                    f"🎬 Full video: {t['video']}\nWhich moment should be the next Short? 👇" if t.get("video")
                    else s.get("pin"))
            if short_at[i]:
                _booked_update(a, t, short_at[i], title=s["title"], url=t["shorts"][-1])
            t["progress"] = 0.8 + 0.2 * (i + 1) / len(items)
    if t["plan"]:
        _unbook(a, t)                                # mốc của Shorts không đăng được (hết hạn mức) trả lại
    t["stage"] = "Đã đăng xong"


def _thumb_style(a):
    st = a.get("thumb_style", "rotate")
    if st in THUMB_STYLES:
        return st
    n = sum(u["kind"] == "long" for u in _account(_accounts(), a["id"]).get("uploads", []))
    return str(n % 3 + 1)


def _record(a, t, vid, kind, title, at, privacy, text, thumb=""):
    """Ghi video đã đăng (cho bảng thống kê) + xếp hàng bình luận: chỉ đăng khi video đã công khai / không công khai
    (video riêng tư thì người xem không thấy). Hẹn giờ thì đợi tới giờ lên sóng."""
    now = time.time()
    visible = bool(at) or privacy != "private"
    state = "pending" if text and a.get("comment", True) and visible else "off"
    with ACC_LOCK:
        d = _accounts()
        acc = _account(d, a["id"])
        acc["uploads"] = (acc.get("uploads", []) + [{
            "id": vid, "kind": kind, "title": title, "topic": t.get("keyword", ""), "thumb": thumb,
            "uploaded": now, "at": at or now, "comment": text or "", "c_state": state, "c_due": (at or now) + 120,
            "c_tries": 0, "stats": {}}])[-300:]
        _save_accounts(d)


def _post_comments():
    """Đăng các bình luận tới hạn (gọi mỗi phút từ bộ hẹn giờ)."""
    now = time.time()
    with ACC_LOCK:
        d = _accounts()
        due = [(a["id"], u["id"], u["comment"]) for a in d["accounts"] for u in a.get("uploads", [])
               if u.get("c_state") == "pending" and u.get("c_due", 0) <= now]
    for cid, vid, text in due[:20]:
        try:
            _spend(ytapi.COST["comment"])
            ytapi.comment(_token(cid), vid, text)
            res = "done"
        except QuotaError:
            return                                  # mai thử lại
        except Exception as e:
            res = f"error: {e}"
        with ACC_LOCK:
            d = _accounts()
            for u in _account(d, cid).get("uploads", []):
                if u["id"] == vid:
                    u["c_tries"] = u.get("c_tries", 0) + 1
                    if res == "done" or u["c_tries"] >= 3:
                        u["c_state"] = res
                    else:                           # video có thể chưa xử lý xong / chưa công khai: 15 phút sau thử lại
                        u["c_due"] = now + 900
            _save_accounts(d)


def _scheduler():
    """Mỗi phút: kênh nào tới giờ chạy hằng ngày mà hôm nay chưa chạy thì xếp hàng (server phải đang mở)."""
    while True:
        try:
            today, now = time.strftime("%Y-%m-%d"), time.strftime("%H:%M")
            with ACC_LOCK:
                d = _accounts()
                due = [a for a in d["accounts"] if a.get("enabled", True) and a.get("daily")
                       and now >= a["daily"] and a.get("last_daily") != today]
                for a in due:
                    a["last_daily"] = today
                if due:
                    _save_accounts(d)
            for a in due:
                _enqueue(a, a.get("per_run", 1))
            _post_comments()
        except Exception:               # không để lỗi đọc file làm chết bộ hẹn giờ
            pass
        time.sleep(60)


# ---------- 📤 đăng TikTok (hộp nháp) + Fanpage Facebook, hẹn giờ ----------
SOCIAL_FILE = DATA / "social.json"
SOCIAL_LOCK = threading.Lock()
POST_LOCK = threading.Lock()          # đăng lần lượt từng video
_SOCIAL_STATES = {}                   # state OAuth -> (nền tảng, redirect, code_verifier)
SOCIAL_KINDS = {"dub": ("dub", "dub.json"), "tt": ("tiktok", "tt.json"), "mv": ("mv", "mv.json")}


def _social_video(kind, f):
    """File video để đăng: MV ưu tiên bản dọc (TikTok / Reels), không có thì bản ngang."""
    if kind == "mv":
        return next((f / x for x in ("v.mp4", "h.mp4") if (f / x).exists()), f / "v.mp4")
    return f / "output.mp4"
TARGETS = ("tiktok", "facebook")


def _social():
    try:
        s = json.loads(SOCIAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    s.setdefault("fb", {"app_id": "", "secret": "", "pages": [], "page": ""})
    s.setdefault("tt", {"key": "", "secret": ""})
    s.setdefault("posts", [])
    return s


def _social_save(s):
    tmp = SOCIAL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SOCIAL_FILE)


def _social_view(request=None):
    s = _social()
    fb, tt = s["fb"], s["tt"]
    base = str(request.base_url).rstrip("/") if request else "http://localhost:8790"
    page = next((p for p in fb["pages"] if p["id"] == fb.get("page")), None)
    return {"fb": {"app_id": fb["app_id"], "has_secret": bool(fb["secret"]),
                   "pages": [{"id": p["id"], "name": p["name"]} for p in fb["pages"]], "page": page and page["id"],
                   "redirect": base + "/api/social/fb/callback"},
            "tt": {"key": tt["key"], "has_secret": bool(tt["secret"]), "name": tt.get("name", ""),
                   "connected": bool(tt.get("refresh")) and tt.get("rexp", 0) > time.time(),
                   "redirect": base + "/api/social/tt/callback"},
            "posts": sorted(s["posts"], key=lambda p: (p["at"], p["created"]))[-100:]}


class SocialCfg(BaseModel):
    fb_app_id: str | None = None
    fb_secret: str | None = None
    fb_page: str | None = None
    tt_key: str | None = None
    tt_secret: str | None = None


@app.get("/api/social")
def social_get(request: Request):
    return _social_view(request)


@app.put("/api/social")
def social_set(c: SocialCfg, request: Request):
    with SOCIAL_LOCK:
        s = _social()
        if c.fb_app_id is not None:
            if c.fb_app_id.strip() != s["fb"]["app_id"]:
                s["fb"].update(pages=[], page="")
            s["fb"]["app_id"] = c.fb_app_id.strip()
        if c.fb_secret:
            s["fb"]["secret"] = c.fb_secret.strip()
        if c.fb_page is not None:
            if c.fb_page and not any(p["id"] == c.fb_page for p in s["fb"]["pages"]):
                raise HTTPException(400, "Không có Trang này")
            s["fb"]["page"] = c.fb_page
        if c.tt_key is not None:
            if c.tt_key.strip() != s["tt"]["key"]:
                s["tt"] = {"key": "", "secret": s["tt"]["secret"]}
            s["tt"]["key"] = c.tt_key.strip()
        if c.tt_secret:
            s["tt"]["secret"] = c.tt_secret.strip()
        _social_save(s)
    return _social_view(request)


@app.get("/api/social/{net}/connect")
def social_connect(net: str, request: Request):
    s = _social()
    redirect = str(request.base_url).rstrip("/") + f"/api/social/{net}/callback"
    state = uuid.uuid4().hex
    if net == "fb":
        if not s["fb"]["app_id"] or not s["fb"]["secret"]:
            raise HTTPException(400, "Nhập App ID và App secret của Facebook trước")
        _SOCIAL_STATES[state] = (net, redirect, "")
        return {"url": social.fb_auth_url(s["fb"]["app_id"], redirect, state)}
    if net == "tt":
        if not s["tt"]["key"] or not s["tt"]["secret"]:
            raise HTTPException(400, "Nhập Client key và Client secret của TikTok trước")
        verifier, challenge = social.tt_pkce()
        _SOCIAL_STATES[state] = (net, redirect, verifier)
        return {"url": social.tt_auth_url(s["tt"]["key"], redirect, state, challenge)}
    raise HTTPException(404)


@app.get("/api/social/{net}/callback")
def social_callback(net: str, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    saved = _SOCIAL_STATES.pop(state, None)
    back = "/#/settings?social="
    if error or not saved or saved[0] != net:
        return RedirectResponse(back + urllib.parse.quote("Lỗi: " + (error_description or error
                                                                     or "phiên đăng nhập đã hết hạn, thử lại")))
    _, redirect, verifier = saved
    try:
        with SOCIAL_LOCK:
            s = _social()
            if net == "fb":
                pages = social.fb_exchange(s["fb"]["app_id"], s["fb"]["secret"], code, redirect)
                s["fb"]["pages"] = pages
                if not any(p["id"] == s["fb"].get("page") for p in pages):
                    s["fb"]["page"] = pages[0]["id"]
                msg = "Đã kết nối Facebook: " + ", ".join(p["name"] for p in pages)
            else:
                t = social.tt_exchange(s["tt"]["key"], s["tt"]["secret"], code, redirect, verifier)
                try:
                    t["name"] = social.tt_user(t["access"])
                except social.SocialError:
                    t["name"] = ""
                s["tt"].update(t)
                msg = "Đã kết nối TikTok" + (f": {t['name']}" if t["name"] else "")
            _social_save(s)
    except (social.SocialError, KeyError) as e:
        return RedirectResponse(back + urllib.parse.quote(f"Lỗi: {e}"))
    return RedirectResponse(back + urllib.parse.quote(msg))


@app.post("/api/social/{net}/disconnect")
def social_disconnect(net: str, request: Request):
    with SOCIAL_LOCK:
        s = _social()
        if net == "fb":
            s["fb"].update(pages=[], page="")
        elif net == "tt":
            s["tt"] = {"key": s["tt"]["key"], "secret": s["tt"]["secret"]}
        else:
            raise HTTPException(404)
        _social_save(s)
    return _social_view(request)


def _social_item(kind, ref):
    if kind not in SOCIAL_KINDS or not ref.isalnum():
        raise HTTPException(400, "Video không hợp lệ")
    sub, name = SOCIAL_KINDS[kind]
    f = DATA / sub / ref
    if not (f / name).exists():
        raise HTTPException(404, "Không tìm thấy video")
    return f, json.loads((f / name).read_text(encoding="utf-8"))


def _caption_text(kind, item, net, seed):
    """Caption + hashtag từ nội dung thật của video (xem clipmaker/caption.py)."""
    if kind == "dub":
        on = [c for c in item["clips"] if c["on"]]
        segs = [item["segs"].get(c["key"], {}) for c in on]
        lines = [g.get("tr", "") for sg in segs for g in sg.get("items", [])]
        langs = [sg.get("lang") for sg in segs if sg.get("items")]
        lang = max(set(langs), key=langs.count) if langs else ""
        eps = [(item.get("review") or {}).get(c["key"], {}).get("ep") for c in on]
        return caption.for_dub(item["title"], lines, lang, eps, net, seed)
    if kind == "mv":
        lang = item.get("lang", "")
        vi = item.get("vi") or {}
        lines = [vi.get(x) or x for x in item.get("lines", [])]
        extra = ["#vietsub", mv.LANG_TAG[lang]] if lang in mv.LANG_TAG else []
        return caption.for_lyrics(item.get("artist", ""), item.get("song") or item.get("title", ""), lines, net, seed,
                                  extra)
    lines = []
    if item.get("lrc") and "a" in item:
        ls = lyrics.lines(lyrics.parse_lrc(item["lrc"]), item.get("audio_dur") or item["b"] + 30)
        lines = [x for _, x in sorted((t0, x) for t0, t1, x in ls if t1 > item["a"] and t0 < item["b"])]
    return caption.for_lyrics(item.get("artist", ""), item.get("song") or item.get("title", ""), lines, net, seed)


@app.get("/api/caption")
def caption_get(kind: str, ref: str, net: str = "tiktok", seed: int | None = None):
    _, item = _social_item(kind, ref)
    return {"caption": _caption_text(kind, item, net if net in TARGETS else "tiktok", seed)}


class PostReq(BaseModel):
    kind: str
    ref: str
    targets: list[str]
    caption: str = ""
    at: float = 0          # giờ đăng (epoch giây); 0 = ngay


@app.post("/api/social/posts")
def social_post(r: PostReq, request: Request):
    f, item = _social_item(r.kind, r.ref)
    if not _social_video(r.kind, f).exists():
        raise HTTPException(400, "Video chưa dựng xong")
    targets = [t for t in dict.fromkeys(r.targets) if t in TARGETS]
    if not targets:
        raise HTTPException(400, "Chọn TikTok hoặc Facebook")
    s = _social()
    if "facebook" in targets and not s["fb"].get("page"):
        raise HTTPException(400, "Chưa kết nối Fanpage Facebook (⚙ Cài đặt)")
    if "tiktok" in targets and not s["tt"].get("refresh"):
        raise HTTPException(400, "Chưa kết nối TikTok (⚙ Cài đặt)")
    if r.at and r.at < time.time() - 60:
        raise HTTPException(400, "Giờ hẹn đã qua")
    post = {"id": uuid.uuid4().hex[:10], "created": time.time(), "at": r.at or time.time(), "kind": r.kind,
            "ref": r.ref, "title": item.get("title", "")[:120], "caption": r.caption[:2200],
            "status": {t: {"state": "wait", "msg": "", "link": ""} for t in targets}}
    with SOCIAL_LOCK:
        s = _social()
        s["posts"].append(post)
        _social_save(s)
    threading.Thread(target=_social_tick, daemon=True).start()
    return _social_view(request)


@app.delete("/api/social/posts/{pid}")
def social_post_delete(pid: str, request: Request):
    with SOCIAL_LOCK:
        s = _social()
        p = next((p for p in s["posts"] if p["id"] == pid), None)
        if not p:
            raise HTTPException(404)
        if any(v["state"] == "run" for v in p["status"].values()):
            raise HTTPException(409, "Đang đăng, chờ xong")
        s["posts"].remove(p)
        _social_save(s)
    return _social_view(request)


@app.post("/api/social/posts/{pid}/retry")
def social_post_retry(pid: str, request: Request):
    with SOCIAL_LOCK:
        s = _social()
        p = next((p for p in s["posts"] if p["id"] == pid), None)
        if not p:
            raise HTTPException(404)
        for v in p["status"].values():
            if v["state"] == "err":
                v.update(state="wait", msg="")
        p["at"] = min(p["at"], time.time())
        _social_save(s)
    threading.Thread(target=_social_tick, daemon=True).start()
    return _social_view(request)


def _tt_access(s):
    """Token TikTok hết hạn sau 24 giờ: làm mới bằng refresh token (dùng được 1 năm)."""
    tt = s["tt"]
    if tt.get("exp", 0) > time.time():
        return tt["access"]
    if tt.get("rexp", 0) < time.time():
        raise social.SocialError("Phiên TikTok đã hết hạn: vào ⚙ Cài đặt kết nối lại")
    tt.update(social.tt_refresh(tt["key"], tt["secret"], tt["refresh"]))
    return tt["access"]


def _social_set(pid, target, **kw):
    with SOCIAL_LOCK:
        s = _social()
        p = next((p for p in s["posts"] if p["id"] == pid), None)
        if p:
            p["status"][target].update(kw)
            _social_save(s)


def _social_tick():
    """Đăng các bài tới giờ (lần lượt từng bài)."""
    if not POST_LOCK.acquire(blocking=False):
        return
    try:
        while True:
            with SOCIAL_LOCK:
                s = _social()
                due = next(((p, t) for p in sorted(s["posts"], key=lambda p: p["at"]) if p["at"] <= time.time()
                            for t, v in p["status"].items() if v["state"] == "wait"), None)
                if not due:
                    return
                p, target = due
                p["status"][target]["state"] = "run"
                _social_save(s)
            try:
                link, msg = _social_publish(p, target)
                _social_set(p["id"], target, state="ok", msg=msg, link=link, done=time.time())
            except Exception as e:      # lỗi một bài không chặn các bài sau
                _social_set(p["id"], target, state="err", msg=str(e)[:300] or e.__class__.__name__)
    finally:
        POST_LOCK.release()


def _social_publish(p, target):
    f, item = _social_item(p["kind"], p["ref"])
    video = _social_video(p["kind"], f)
    if not video.exists():
        raise social.SocialError("Không còn file video (đã xoá hoặc đang dựng lại)")
    if target == "facebook":
        s = _social()
        page = next((x for x in s["fb"]["pages"] if x["id"] == s["fb"].get("page")), None)
        if not page:
            raise social.SocialError("Chưa chọn Fanpage")
        meta = dub.probe(video)
        _, link = social.fb_post(page["id"], page["token"], video, p["title"] or "Video", p["caption"],
                                 meta["duration"], meta["h"] > meta["w"])
        return link, f"Đã đăng lên {page['name']}"
    with SOCIAL_LOCK:
        s = _social()
        access = _tt_access(s)
        _social_save(s)
    pid = social.tt_inbox(access, video)
    st = social.tt_wait(access, pid)
    return "", ("Đã vào hộp thư TikTok: mở app TikTok → Hộp thư → thông báo video, dán caption rồi bấm Đăng"
                if st != "PROCESSING_UPLOAD" else "TikTok đang xử lý, vài phút nữa video sẽ vào hộp thư")


def _social_loop():
    with SOCIAL_LOCK:
        s = _social()
        for p in s["posts"]:
            for v in p["status"].values():
                if v["state"] == "run":
                    v.update(state="err", msg="Server tắt giữa lúc đăng, bấm Thử lại")
        _social_save(s)
    while True:
        try:
            _social_tick()
        except Exception:
            pass
        time.sleep(20)


# ---------- cookie YouTube (giảm bị chặn "Sign in to confirm you're not a bot") ----------
COOKIE_FILE = DATA / "cookies.txt"
SETTINGS_FILE = DATA / "settings.json"
TEST_VIDEO = "jNQXAC9IVRw"          # "Me at the zoo": video công khai lâu đời, dùng để thử cookie


def _settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _apply_cookies():
    c = _settings().get("cookies") or {}
    if c.get("mode") == "browser":
        yt.set_cookies(browser=c.get("browser"))
    elif c.get("mode") == "file" and COOKIE_FILE.exists():
        yt.set_cookies(file=COOKIE_FILE)
    else:
        yt.set_cookies()


def _cookie_view():
    c = _settings().get("cookies") or {}
    mode = c.get("mode") if c.get("mode") in ("file", "browser") else "none"
    if mode == "file" and not COOKIE_FILE.exists():
        mode = "none"
    return {"mode": mode, "browser": c.get("browser") or "firefox", "browsers": list(yt.BROWSERS),
            "file": COOKIE_FILE.exists(), "updated": COOKIE_FILE.stat().st_mtime if COOKIE_FILE.exists() else None,
            "count": c.get("count"), "tested": c.get("tested")}


def _save_cookie_cfg(**kw):
    s = _settings()
    s["cookies"] = {**(s.get("cookies") or {}), **kw}
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    _apply_cookies()


@app.get("/api/settings/cookies")
def cookies_get():
    return _cookie_view()


@app.post("/api/settings/cookies/file")
async def cookies_upload(file: UploadFile):
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(400, "File quá lớn — chỉ cần cookie của youtube.com")
    text = raw.decode("utf-8", "replace")
    try:
        count = yt.check_cookie_file(text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    COOKIE_FILE.write_text(text, encoding="utf-8")
    for old in DATA.glob(".cookies_*.txt"):            # bản sao cũ của từng luồng
        old.unlink(missing_ok=True)
    _save_cookie_cfg(mode="file", count=count, tested=None)
    return _cookie_view()


class CookieCfg(BaseModel):
    mode: str
    browser: str | None = None


@app.put("/api/settings/cookies")
def cookies_set(c: CookieCfg):
    if c.mode not in ("none", "file", "browser"):
        raise HTTPException(400, "Chế độ không hợp lệ")
    if c.mode == "file" and not COOKIE_FILE.exists():
        raise HTTPException(400, "Chưa có file cookies.txt — tải file lên trước")
    if c.mode == "browser" and c.browser not in yt.BROWSERS:
        raise HTTPException(400, "Trình duyệt không hỗ trợ")
    _save_cookie_cfg(mode=c.mode, browser=c.browser, tested=None)
    return _cookie_view()


@app.delete("/api/settings/cookies")
def cookies_delete():
    COOKIE_FILE.unlink(missing_ok=True)
    for old in DATA.glob(".cookies_*.txt"):
        old.unlink(missing_ok=True)
    _save_cookie_cfg(mode="none", count=None, tested=None)
    return _cookie_view()


@app.post("/api/settings/cookies/test")
def cookies_test():
    """Thử đọc một video bằng cookie hiện tại: báo lỗi cụ thể (cookie hết hạn, không đọc được trình duyệt…)."""
    if _cookie_view()["mode"] == "none":
        raise HTTPException(400, "Chưa bật cookie")
    try:
        yt.check_access(TEST_VIDEO)
    except Exception as e:  # noqa: BLE001 - báo nguyên văn lỗi yt-dlp cho người dùng
        msg = str(e)
        if "could not find" in msg.lower() or "decrypt" in msg.lower() or "keyring" in msg.lower():
            msg += " — Không đọc được cookie của trình duyệt này (Chrome/Edge mới mã hoá cookie). Hãy dùng Firefox hoặc cách tải file cookies.txt."
        raise HTTPException(400, msg[-500:]) from None
    _save_cookie_cfg(tested=time.time())
    return _cookie_view()


_apply_cookies()


@app.on_event("startup")
def _start_scheduler():
    threading.Thread(target=_scheduler, daemon=True).start()
    threading.Thread(target=_social_loop, daemon=True).start()
    _mv_resume()


app.mount("/fonts", StaticFiles(directory=ROOT / "fonts"), name="fonts")
app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
