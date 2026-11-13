"""Web ghép video "khoảnh khắc hay" từ YouTube: từ khoá -> tìm video -> tìm đoạn gây cười -> duyệt + bình luận -> MP4.

Chạy: .venv/bin/uvicorn server:app --port 8790
"""
import hashlib
import json
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

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from clipmaker import compose, moments
from clipmaker import copywrite, seo, trends, voice, ytapi
from clipmaker import youtube as yt

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
INTRO_LEN, OUTRO_LEN = 3.5, 3.0
CLIP_MIN, CLIP_MAX = 3.0, 30.0
WORKERS = 3                     # tải / phân tích song song vừa phải để YouTube không chặn
# Mỗi lần quét đổi cách tìm một chút để ra video khác nhau dù cùng từ khoá.
VARIANTS = ("", "funny", "funniest", "best moments", "compilation", "try not to laugh", "hilarious", "awkward",
            "bloopers", "viral")
USED_PENALTY = 0.35             # đoạn đã đăng ở video trước: hạ điểm mạnh

app = FastAPI(title="Clip Compiler")
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


def view(p):
    f = DATA / p["id"]
    job = JOBS.get(p["id"])
    plan = _plan(p)
    total = sum(c["end"] - c["start"] + _voice_len(f, c) for c, _ in plan) + (INTRO_LEN + OUTRO_LEN if plan else 0)
    pool = sum(c["end"] - c["start"] for c in p["clips"]) + INTRO_LEN + OUTRO_LEN
    return {"project": p, "videos": p.get("videos", {}), "pool": round(pool, 1),
            "plan": [{"id": c["id"], "number": n} for c, n in plan],
            "total": round(total, 1),
            "output": (f / "output.mp4").exists() and p.get("output_stamp") == _stamp(p),
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


@app.post("/api/projects")
def create(req: CreateReq):
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
    pid = uuid.uuid4().hex[:12]
    (DATA / pid).mkdir(parents=True)
    p = {"id": pid, "created": time.time(), "keyword": kw, "title": kw.title(), "length": req.length,
         "minutes": min(30, max(3, req.minutes)), "countdown": True, "credits": True, "comments": req.comments,
         "subtitles": True, "thumb_text": "", "ref": req.ref[:200], "shorts": req.shorts,
         "voiceover": req.voiceover, "voice": req.voice if req.voice in voice.VOICES else voice.DEFAULT_VOICE,
         "takes": min(10, max(1, req.takes)), "auto": req.auto,
         "seed_videos": [v for v in req.seed_videos if re.fullmatch(r"[\w-]{11}", v)][:15],
         "videos": {}, "clips": [], "order": []}
    save(p)
    return p


def _planned(p):
    return sum(c["end"] - c["start"] for c, _ in _plan(p)) + INTRO_LEN + OUTRO_LEN


def _auto(pid, count, job):
    """⚡ Làm tự động: quét, chọn đủ thời lượng (quét thêm tối đa 2 lần), giọng đọc, dựng + Shorts."""
    _scan(pid, count, _Part(job, 0.0, 0.3), fresh=True)
    for k in range(2):
        p = load(pid)
        if _planned(p) >= p["minutes"] * 60 * 0.85:
            break
        try:
            _scan(pid, 20, _Part(job, 0.3 + 0.05 * k, 0.35 + 0.05 * k), fresh=False)
        except RuntimeError:            # hết video mới: dựng với những gì đang có
            break
        p = load(pid)
        _autoselect(p, p["minutes"])
        save(p)
    note = ""
    if load(pid).get("voiceover"):
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
    load(pid)
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
    """Chọn đoạn điểm cao nhất tới đủ thời lượng; đoạn hay nhất để cuối (#1), tối đa 3 đoạn mỗi video."""
    budget = minutes * 60 - INTRO_LEN - OUTRO_LEN
    picked, per_video, used = [], {}, 0.0
    rnd = random.Random()            # mỗi lần chọn lại hơi khác: điểm nhân hệ số ngẫu nhiên
    for c in sorted(p["clips"], key=lambda c: -c["score"] * rnd.uniform(0.7, 1.3)):
        if used >= budget:
            break
        if per_video.get(c["vid"], 0) >= 3:
            continue
        picked.append(c)
        per_video[c["vid"]] = per_video.get(c["vid"], 0) + 1
        used += c["end"] - c["start"]
    # đếm ngược: điểm thấp -> cao, nhưng mở đầu bằng một đoạn mạnh để giữ người xem
    asc = sorted(picked, key=lambda c: c["score"])
    if len(asc) > 3:
        asc.insert(0, asc.pop(-2))
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
                    "clips": len(p["order"]), "done": (pj.parent / "output.mp4").exists()})
    return out


# ---------- nghiên cứu xu hướng ----------
TRENDS = "_trends"                # khoá job (không phải dự án)


class TrendReq(BaseModel):
    seed: str = "funny moments"
    depth: int = 10


@app.post("/api/trends")
def start_trends(req: TrendReq):
    seed = req.seed.strip()
    if not seed:
        raise HTTPException(400, "Nhập lĩnh vực cần nghiên cứu")

    def work(job):
        res = trends.research(seed, min(20, max(4, req.depth)), job)
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
    if not CLIP_MIN <= end - start <= CLIP_MAX:
        raise HTTPException(400, "Mỗi đoạn phải dài từ 3 đến 30 giây")
    c["start"], c["end"] = start, end
    if e.caption is not None:
        c["caption"] = e.caption.strip()[:120]
    if e.include is not None and e.include != c["include"]:
        c["include"] = e.include
        p["order"] = [i for i in p["order"] if i != cid] + ([cid] if e.include else [])
    save(p)
    return view(p)


class AutoReq(BaseModel):
    minutes: int


@app.post("/api/projects/{pid}/autoselect")
def autoselect(pid: str, req: AutoReq):
    p = load(pid)
    p["minutes"] = min(30, max(1, req.minutes))
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
    raw_dir, seg_dir = f / "raw", f / "seg"
    raw_dir.mkdir(exist_ok=True)
    if seg_dir.exists():
        shutil.rmtree(seg_dir)
    seg_dir.mkdir()
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
                yt.download(c["vid"], c["start"], c["end"], tmp)
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
    if failed and p.get("countdown", True):     # đánh số lại cho liền mạch
        plan = [(c, len(plan) - k) for k, (c, _) in enumerate(plan)]
    # phụ đề: luôn lấy vì lời dẫn mô tả + hook của Shorts trích câu thoại từ đây
    job["stage"] = "Đang lấy phụ đề"
    words = _words(p, f, {c["vid"] for c, _ in plan})
    # 2. đưa từng đoạn về khung chuẩn + bình luận
    job["stage"] = "Đang dựng từng đoạn"
    parts, chapters, copy_items, t = [], [], [], 0.0
    first_frame = seg_dir / "first.png"
    compose.run(["-ss", "1", "-i", str(raws[plan[0][0]["id"]]), "-frames:v", "1", "-update", "1", str(first_frame)])
    intro = compose.title_card(first_frame, p["title"], f"Top {len(plan)}", seg_dir / "intro.png")
    parts.append(compose.still_segment(intro, seg_dir / "000_intro.mp4", INTRO_LEN))
    t += INTRO_LEN
    chapters.append((0.0, "Intro"))
    for k, (c, num) in enumerate(plan):
        if job["stop"]:
            raise InterruptedError
        v = p["videos"][c["vid"]]
        ov = compose.overlay(num, v["channel"] if p.get("credits", True) else "",
                             v["title"] if p.get("credits", True) else "", c.get("caption", ""))
        ov_png = seg_dir / f"ov_{k}.png"
        ov.save(ov_png)
        subs = yt.chunks(words.get(c["vid"], []), c["start"], c["end"]) if p.get("subtitles", True) else []
        seg = compose.clip_segment(raws[c["id"]], seg_dir / f"{k + 1:03d}_clip.mp4", 0.0, c["end"] - c["start"],
                                   ov_png, seg_dir, subs)
        parts.append(seg)
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
                "context": " ".join([v["title"] for v in used] + used_tags)}
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


SHORTS = 3


def _shorts(p, f, plan, raws, words, copy_items, seg_dir, job):
    """Cắt các đoạn top thành video dọc 1080x1920 cho YouTube Shorts (kèm hook + phụ đề, kéo về video dài)."""
    for old in f.glob("short_*.mp4"):
        old.unlink()
    by_num = {x["num"]: x for x in copy_items}
    top = sorted(plan, key=lambda x: x[1] if p.get("countdown", True) else -x[0]["score"])[:SHORTS]
    rnd = random.Random(p["id"])
    out = []
    for i, (c, num) in enumerate(top, 1):
        if job["stop"]:
            raise InterruptedError
        job["stage"] = f"Đang cắt Shorts {i}/{len(top)}"
        v = p["videos"][c["vid"]]
        hook = copywrite.short_hook(by_num.get(num, {}), rnd)
        ov = seg_dir / f"short_ov_{i}.png"
        compose.short_overlay(hook, "FULL VIDEO ON MY CHANNEL", num).save(ov)
        subs = yt.chunks(words.get(c["vid"], []), c["start"], c["end"])
        try:
            compose.short_segment(raws[c["id"]], f / f"short_{i}.mp4", c["end"] - c["start"], ov, seg_dir, subs)
        except RuntimeError:
            continue
        out.append({"file": f"short_{i}.mp4", "num": num, "hook": hook, "title": v["title"],
                    "channel": v["channel"], "vid": c["vid"]})
        job["progress"] = 0.85 + 0.1 * i / len(top)
    return out


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
                    *(f"short_{i}.mp4" for i in range(1, SHORTS + 1))):
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
                "comment": True, "thumb_style": "rotate", "uploads": []}
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
        res = trends.research(seed, 8)
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
    p = _new_project(CreateReq(keyword=keyword, videos=40, length=10, minutes=a.get("minutes", 10), auto=True,
                               voiceover=a.get("voiceover", True), voice=a.get("voice", voice.DEFAULT_VOICE),
                               shorts=a.get("shorts", True), seed_videos=seed_videos,
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
                    f"🎬 Full video: {t['video']}\nWhich moment should be the next Short? 👇" if t.get("video") else None)
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


app.mount("/fonts", StaticFiles(directory=ROOT / "fonts"), name="fonts")
app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
