"""Web ghép video "khoảnh khắc hay" từ YouTube: từ khoá -> tìm video -> tìm đoạn gây cười -> duyệt + bình luận -> MP4.

Chạy: .venv/bin/uvicorn server:app --port 8790
"""
import hashlib
import json
import random
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from clipmaker import compose, moments
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


def _start_job(pid, kind, work):
    with LOCK:
        cur = JOBS.get(pid)
        if cur and not cur["done"]:
            raise HTTPException(409, "Đang có việc khác chạy cho dự án này")
        job = {"kind": kind, "progress": 0.0, "stage": "", "error": None, "done": False, "stop": False,
               "result": None}
        JOBS[pid] = job

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


# ---------- quét: tìm video + tìm khoảnh khắc ----------
class CreateReq(BaseModel):
    keyword: str
    videos: int = 30
    length: float = 10.0
    minutes: int = 15
    comments: bool = True


@app.post("/api/projects")
def create(req: CreateReq):
    kw = req.keyword.strip()
    if not kw:
        raise HTTPException(400, "Nhập từ khoá cần tìm")
    if not CLIP_MIN <= req.length <= CLIP_MAX:
        raise HTTPException(400, "Độ dài mỗi đoạn phải từ 3 đến 30 giây")
    pid = uuid.uuid4().hex[:12]
    (DATA / pid).mkdir(parents=True)
    p = {"id": pid, "created": time.time(), "keyword": kw, "title": kw.title(), "length": req.length,
         "minutes": min(30, max(3, req.minutes)), "countdown": True, "credits": True, "comments": req.comments,
         "subtitles": True, "thumb_text": "",
         "videos": {}, "clips": [], "order": []}
    save(p)
    _start_job(pid, "scan", lambda job: _scan(pid, min(80, max(5, req.videos)), job, fresh=True))
    return view(p)


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
    found = pool[:count]
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
            videos[v["id"]] = {k: info[k] for k in ("id", "title", "channel", "duration", "views", "caption_url")}
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


# ---------- dựng video ----------
def _ts(t):
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@app.post("/api/projects/{pid}/build")
def build(pid: str):
    p = load(pid)
    if not p["order"]:
        raise HTTPException(400, "Chưa chọn đoạn nào")
    f = folder(pid)

    def work(job):
        plan = _plan(p)
        raw_dir, seg_dir = f / "raw", f / "seg"
        raw_dir.mkdir(exist_ok=True)
        if seg_dir.exists():
            shutil.rmtree(seg_dir)
        seg_dir.mkdir()
        # 1. tải các đoạn (song song, có lưu đệm: dựng lại không phải tải lại)
        job["stage"] = "Đang tải các đoạn video"
        raws = {}

        def fetch(c):
            if job["stop"]:
                raise InterruptedError
            path = raw_dir / f"{c['vid']}_{c['start']:.1f}_{c['end']:.1f}.mp4"
            if not path.exists():
                tmp = raw_dir / ("tmp_" + path.name)
                yt.download(c["vid"], c["start"], c["end"], tmp)
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
        plan = [(c, n) for c, n in plan if c["id"] in raws]
        if not plan:
            raise RuntimeError("Không tải được đoạn nào (YouTube chặn hoặc mất mạng)")
        if failed and p.get("countdown", True):     # đánh số lại cho liền mạch
            plan = [(c, len(plan) - k) for k, (c, _) in enumerate(plan)]
        # phụ đề: lấy một lần mỗi video (lưu đệm)
        words = {}
        if p.get("subtitles", True):
            job["stage"] = "Đang lấy phụ đề"
            sub_dir = f / "subs"
            sub_dir.mkdir(exist_ok=True)
            for vid in {c["vid"] for c, _ in plan}:
                cache = sub_dir / f"{vid}.json"
                url = p["videos"][vid].get("caption_url")
                try:
                    if not cache.exists() and url:
                        cache.write_text(json.dumps(yt.captions(url)))
                    words[vid] = json.loads(cache.read_text()) if cache.exists() else []
                except Exception:
                    words[vid] = []          # thiếu phụ đề một video không làm hỏng cả bản dựng
        # 2. đưa từng đoạn về khung chuẩn + bình luận
        job["stage"] = "Đang dựng từng đoạn"
        parts, chapters, t = [], [], 0.0
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
            subs = yt.chunks(words.get(c["vid"], []), c["start"], c["end"])
            seg = compose.clip_segment(raws[c["id"]], seg_dir / f"{k + 1:03d}_clip.mp4", 0.0, c["end"] - c["start"],
                                       ov_png, seg_dir, subs)
            parts.append(seg)
            chapters.append((t, f"#{num} {v['title'][:60]}", c, v))
            t += compose.probe(seg)["duration"]
            if c.get("voice") and (f / c["voice"]).exists():
                frame = compose.last_frame(seg, seg_dir / f"last_{k}.png")
                card = compose.commentary_card(frame, c.get("caption", ""), seg_dir / f"take_{k}.png")
                vseg = compose.still_segment(card, seg_dir / f"{k + 1:03d}_take.mp4", c["voice_len"] + 0.6,
                                             f / c["voice"])
                parts.append(vseg)
                t += compose.probe(vseg)["duration"]
            job["progress"] = 0.45 + 0.5 * (k + 1) / len(plan)
        outro = compose.title_card(first_frame, "Thanks for watching!", "Like & subscribe for more", seg_dir / "outro.png")
        parts.append(compose.still_segment(outro, seg_dir / "999_outro.mp4", OUTRO_LEN))
        # 3. nối
        job["stage"] = "Đang ghép video"
        tmp = f / "output.tmp.mp4"
        compose.concat(parts, tmp, seg_dir)
        tmp.replace(f / "output.mp4")
        # thumbnail: khung hình giữa 3 đoạn điểm cao nhất
        job["stage"] = "Đang làm thumbnail"
        frames = []
        for i, (c, _) in enumerate(sorted(plan, key=lambda x: -x[0]["score"])[:3]):
            fr = f / f"thumbframe_{i}.png"
            mid = max(0.0, (c["end"] - c["start"]) * 0.5)
            try:
                compose.run(["-ss", f"{mid:.2f}", "-i", str(raws[c["id"]]), "-frames:v", "1", "-update", "1", str(fr)])
                frames.append(fr)
            except RuntimeError:
                pass
        text = p.get("thumb_text") or p["title"]
        thumbs = compose.thumbnails(frames, text, len(plan), f)
        shutil.copyfile(thumbs[0], f / "thumbnail.jpg")
        for fr in frames:
            fr.unlink(missing_ok=True)
        _remember(plan)
        shutil.rmtree(seg_dir, ignore_errors=True)
        lines = [f"{_ts(x[0])} {x[1]}" for x in chapters]
        sources = sorted({(x[3]["title"], x[3]["channel"], x[2]["vid"]) for x in chapters[1:]})
        takes = sum(1 for c, _ in plan if c.get("voice"))
        desc = (f"{p['title']} — {len(plan)} moments" + (" with my commentary" if takes else "") + ".\n\nTimestamps:\n"
                + "\n".join(lines)
                + "\n\nOriginal videos (all credit to the creators):\n"
                + "\n".join(f"- {t_} — {ch} https://youtu.be/{vid}" for t_, ch, vid in sources)
                + "\n\n#funnymoments #compilation")
        cur = load(pid)
        cur.update(output_stamp=_stamp(p), youtube={"title": f"{p['title']} | Top {len(plan)}"[:100],
                                                    "description": desc}, failed=failed)
        save(cur)
        job["progress"] = 1.0
        return {"clips": len(plan), "failed": len(failed), "duration": round(t + OUTRO_LEN, 1)}

    return _start_job(pid, "build", work)


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
    if name not in ("output.mp4", "thumbnail.jpg", "thumb_1.jpg", "thumb_2.jpg", "thumb_3.jpg"):
        raise HTTPException(404)
    path = folder(pid) / name
    if not path.exists():
        raise HTTPException(404)
    if download:
        title = load(pid)["title"]
        nice = "".join(ch for ch in title if ch.isalnum() or ch in " -_").strip() or "compilation"
        suffix = "" if name == "output.mp4" else f" - {path.stem}"
        return FileResponse(path, filename=f"{nice}{suffix}{path.suffix}")
    return FileResponse(path)


@app.delete("/api/projects/{pid}")
def delete(pid: str):
    shutil.rmtree(folder(pid))
    JOBS.pop(pid, None)
    return {"ok": True}


app.mount("/fonts", StaticFiles(directory=ROOT / "fonts"), name="fonts")
app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
