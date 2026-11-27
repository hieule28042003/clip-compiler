import random
import subprocess
from pathlib import Path
import time
import urllib.parse as urllib_parse
from json import dumps as json_dumps

import numpy as np
import pytest
from fastapi.testclient import TestClient

import server
from clipmaker import compose, copywrite, moments, music, seo, stream, trends, voice
from clipmaker import youtube as yt


def test_timestamps_in_comments():
    assert yt.timestamps("2:15 lol and 1:02:30 too") == [135, 3750]
    assert yt.timestamps("score 12:345 or 3:75 or 10.5:20") == []


def _info(dur=600, heat_peak=300, marks=()):
    heat = [[i * dur / 100, (i + 1) * dur / 100, 0.2 + (0.8 if abs(i * dur / 100 - heat_peak) < 8 else 0)]
            for i in range(100)]
    heat[0][2] = 1.0                     # đầu video luôn cao: phải bị bỏ qua
    return {"duration": dur, "heatmap": heat, "chapters": [], "marks": list(marks)}


def test_moments_follow_replays_and_funny_comments():
    info = _info(marks=[{"t": 450, "likes": 900, "text": "4:30 I'm crying 😂😂"},
                        {"t": 451, "likes": 20, "text": "4:31 lmao"}])
    found = moments.find(info, length=10)
    peaks = sorted(c["peak"] for c in found)
    assert any(abs(p - 302) <= 6 for p in peaks) and any(abs(p - 452) <= 4 for p in peaks)
    assert all(c["peak"] > 30 for c in found)                                   # bỏ phần mở đầu
    com = next(c for c in found if abs(c["peak"] - 452) <= 4)
    assert com["end"] - com["start"] == 10 and com["start"] <= 450 + 1.5 <= com["end"]
    assert any("crying" in r for r in com["reasons"])


RESEARCH = {"phrases": ["celeb funny moments", "celeb funny moments india", "celeb funny moments reaction"],
            "trend": [(5_000_000, "Nobody Expected Michelle Obama To Be This Funny"),
                      (3_000_000, "Nobody Didn't Expect Adam Sandler To Be This Funny"),
                      (900_000, "Bill Hader's Most SAVAGE Moments")], "year": 2026}


def _fake_video(path, seconds, size="640x360"):
    compose.run(["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}",
                 "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)])
    return path


def test_segments_share_format_and_concat(tmp_path):
    src = _fake_video(tmp_path / "src.mp4", 4, "480x640")              # video dọc -> nền mờ hai bên
    ov = tmp_path / "ov.png"
    compose.overlay(3, "Some Channel", "Some title", "when he said that").save(ov)
    a = compose.clip_segment(src, tmp_path / "a.mp4", 0, 3, ov, tmp_path)
    compose.run(["-f", "lavfi", "-i", "sine=frequency=300:duration=1.5", str(tmp_path / "v.wav")])
    card = compose.commentary_card(compose.last_frame(a, tmp_path / "l.png"), "This was wild", tmp_path / "c.png")
    b = compose.still_segment(card, tmp_path / "b.mp4", 2.0, tmp_path / "v.wav")
    out = compose.concat([a, b], tmp_path / "out.mp4", tmp_path)
    info = compose.probe(out)
    assert abs(info["duration"] - 5.0) < 0.3 and info["audio"]


def _wait(c, pid):
    for _ in range(3000):                 # tối đa 5 phút (máy bận dựng video khác thì chậm hơn)
        job = c.get(f"/api/projects/{pid}/job").json()
        if job["done"]:
            return job
        time.sleep(0.1)
    raise AssertionError("job chưa xong")


@pytest.fixture
def fake_youtube(monkeypatch, tmp_path):
    vids = [{"id": f"vid{i}", "title": f"Funny video {i}", "channel": f"Chan {i}", "duration": 600,
             "views": 1000 * i} for i in range(4)]
    monkeypatch.setattr(yt, "search", lambda kw, n: vids[:n])

    def analyze(vid, comments=120):
        i = int(vid[-1])
        return {**vids[i], "heatmap": _info(heat_peak=100 + 80 * i)["heatmap"], "chapters": [],
                "marks": [{"t": 400 + i, "likes": 50, "text": "lol 😂"}], "width": 640, "height": 360,
                "caption_url": f"https://captions/{vid}"}

    downloads = []

    def download(vid, start, end, out, progress=None, full=True):
        downloads.append((vid, start, end))
        return _fake_video(out, end - start)

    monkeypatch.setattr(yt, "analyze", analyze)
    monkeypatch.setattr(yt, "captions", lambda url: [(float(t), w) for t in range(0, 600) for w in ["ha"]])
    monkeypatch.setattr(yt, "download", download)
    monkeypatch.setattr(seo, "research", lambda kw: RESEARCH)
    monkeypatch.setattr(server, "DATA", tmp_path / "data")
    (tmp_path / "data").mkdir()
    return downloads


def test_server_end_to_end(fake_youtube, tmp_path):
    c = TestClient(server.app)
    assert c.post("/api/projects", json={"keyword": "  "}).status_code == 400
    pid = c.post("/api/projects", json={"keyword": "celeb funny", "videos": 4, "length": 4, "minutes": 1}).json()["project"]["id"]
    assert _wait(c, pid)["error"] is None
    v = c.get(f"/api/projects/{pid}").json()
    clips = v["project"]["clips"]
    assert len(clips) >= 8 and v["plan"] and v["plan"][-1]["number"] == 1
    plan_ids = [x["id"] for x in v["plan"]]
    per_video = {}
    for i in plan_ids:
        per_video[i.split("_")[0]] = per_video.get(i.split("_")[0], 0) + 1
    assert max(per_video.values()) <= 3
    c.post(f"/api/projects/{pid}/more", json={"videos": 5})                  # hết video mới -> báo lỗi rõ
    assert "video mới" in _wait(c, pid)["error"]

    # chỉ dựng 3 đoạn cho nhanh
    keep = plan_ids[:3]
    v = c.put(f"/api/projects/{pid}", json={"order": keep}).json()
    assert [x["id"] for x in v["plan"]] == keep and [x["number"] for x in v["plan"]] == [3, 2, 1]
    assert c.put(f"/api/projects/{pid}", json={"order": ["nope"]}).status_code == 400
    cid = keep[1]
    assert c.put(f"/api/projects/{pid}/clips/{cid}", json={"end": 9999}).status_code == 400   # quá 30 s
    v = c.put(f"/api/projects/{pid}/clips/{cid}", json={"caption": "Look at his face"}).json()
    compose.run(["-f", "lavfi", "-i", "sine=frequency=500:duration=2", str(tmp_path / "rec.wav")])
    with open(tmp_path / "rec.wav", "rb") as fh:
        v = c.post(f"/api/projects/{pid}/clips/{cid}/voice", files={"file": ("voice.webm", fh, "audio/webm")}).json()
    clip = next(x for x in v["project"]["clips"] if x["id"] == cid)
    assert abs(clip["voice_len"] - 2.0) < 0.1
    assert c.get(f"/api/projects/{pid}/clips/{cid}/voice.wav").status_code == 200

    c.put(f"/api/projects/{pid}", json={"thumb_text": "Try not to laugh"})
    c.post(f"/api/projects/{pid}/build")
    job = _wait(c, pid)
    assert job["error"] is None and job["result"]["clips"] == 3
    v = c.get(f"/api/projects/{pid}").json()
    assert v["output"]
    out = compose.probe(tmp_path / "data" / pid / "output.mp4")
    assert abs(out["duration"] - (server.INTRO_LEN + 12 + 2.6 + server.OUTRO_LEN)) < 0.8
    desc = v["project"]["youtube"]["description"]
    assert "https://youtu.be/" in desc and "#3 Funny video" in desc and "0:00 Intro" in desc
    yt_copy = v["project"]["youtube"]
    texts = [t["text"] for t in yt_copy["titles"]]
    assert len(texts) >= 3 and yt_copy["title"] in texts and "celeb funny" in yt_copy["tags"]
    assert desc.startswith("Celeb Funny Moments") and yt_copy["hook"] in desc and "#1" in yt_copy["hook"]  # từ khoá trước, rồi lời dẫn
    again = c.post(f"/api/projects/{pid}/copy").json()["project"]["youtube"]
    assert "#3 Funny video" in again["description"]
    for i in (1, 2, 3):
        assert c.get(f"/api/projects/{pid}/files/thumb_{i}.jpg").headers["content-type"] == "image/jpeg"
    assert (tmp_path / "data" / pid / "subs").exists()
    shorts = v["project"]["shorts_out"]                               # Shorts dọc từ các đoạn top
    assert job["result"]["shorts"] == 3 and [x["num"] for x in shorts] == [1, 2, 3]
    assert all(x["title"].endswith("#shorts") and "youtu.be/" in x["description"] for x in shorts)
    sh = c.get(f"/api/projects/{pid}/files/short_1.mp4")
    assert sh.status_code == 200
    (tmp_path / "s1.mp4").write_bytes(sh.content)
    assert "1080x1920" in subprocess.run([compose.FFMPEG, "-i", str(tmp_path / "s1.mp4")], capture_output=True, text=True).stderr

    # dựng lại: không tải lại các đoạn đã có
    n = len(fake_youtube)
    c.put(f"/api/projects/{pid}", json={"title": "New title"})
    assert not c.get(f"/api/projects/{pid}").json()["output"]
    c.post(f"/api/projects/{pid}/build")
    assert _wait(c, pid)["error"] is None and len(fake_youtube) == n


def test_same_keyword_gives_different_clips_next_time(fake_youtube):
    c = TestClient(server.app)
    first = c.post("/api/projects", json={"keyword": "celeb", "videos": 4, "length": 4, "minutes": 1}).json()["project"]["id"]
    _wait(c, first)
    plan1 = [x["id"] for x in c.get(f"/api/projects/{first}").json()["plan"]]
    server._remember([(cl, 0) for cl in c.get(f"/api/projects/{first}").json()["project"]["clips"]
                      if cl["id"] in plan1])
    second = c.post("/api/projects", json={"keyword": "celeb", "videos": 4, "length": 4, "minutes": 1}).json()["project"]["id"]
    _wait(c, second)
    v = c.get(f"/api/projects/{second}").json()
    used = [cl for cl in v["project"]["clips"] if cl["id"] in plan1]
    assert used and all("♻ Đã dùng ở video trước" in cl["reasons"] for cl in used)
    assert [x["id"] for x in v["plan"]] != plan1


def test_copy_quotes_the_moment():
    words = [(100.0, "so"), (100.4, "I"), (100.8, "told"), (101.2, "him"), (101.6, "you're"), (102.0, "not"),
             (102.4, "my"), (102.8, "dad?"), (106.0, "[Laughter]")]
    clip = {"start": 98.0, "end": 108.0, "peak": 103}
    assert copywrite.spoken_line(words, clip) == "So I told him you're not my dad?"
    assert copywrite.clean_comment("3:15 @bob he really said that 😂😂") == "he really said that 😂😂"
    assert copywrite.clean_comment("2:10 😂") == ""
    assert copywrite.topic("celebrity funny moments") == "Celebrity"
    items = [{"num": 2, "ts": "0:04", "line": "", "comment": "the face he made 😭"},
             {"num": 1, "ts": "0:20", "line": "You're not my dad?", "comment": ""}]
    out = copywrite.make("celebrity funny moments", items, 2, "0:00 Intro", "- a", seed=1)
    assert "You're not my dad" in out["hook"] and "0:20" in out["hook"] and "the face he made" in out["hook"]
    texts = [t["text"] for t in out["titles"]]
    assert any("You're not my dad?" in t for t in texts) and all(len(t) <= 100 for t in texts)
    assert "Moments Moments" not in " ".join(texts) and "#CelebrityFunnyMoments" in out["description"]
    assert copywrite.make("celebrity funny moments", items, 2, "", "", seed=2) != out     # viết lại ra bản khác


def test_ffmpeg_copied_when_symlink_not_allowed(tmp_path, monkeypatch):
    """Windows không có quyền Admin: symlink báo WinError 1314 -> phải chép file ffmpeg thay thế."""
    import importlib
    import pathlib

    def deny(*a, **k):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr(pathlib.Path, "symlink_to", deny)
    src = pathlib.Path(yt.__spec__.origin).read_text(encoding="utf-8")
    ns = {"__file__": str(tmp_path / "clipmaker" / "youtube.py"), "__name__": "yt_copy"}
    exec(compile(src, "youtube.py", "exec"), ns)
    ff = pathlib.Path(ns["FFMPEG"])
    assert ff.parent == tmp_path / "bin" and ff.is_file() and not ff.is_symlink()
    assert compose.subprocess.run([str(ff), "-version"], capture_output=True).returncode == 0


def test_download_falls_back_when_ffmpeg_cannot_connect(tmp_path, monkeypatch):
    """ffmpeg không qua được proxy (Windows: 'exited with code 4294967158') -> tải cả video bằng Python rồi cắt."""
    tries = []

    def section_fails(*a):
        tries.append(1)
        raise RuntimeError("ERROR: ffmpeg exited with code 4294967158")

    fetched = []

    def fetch_full(vid, full, hook):
        fetched.append(vid)
        _fake_video(full, 20)
        return True

    monkeypatch.setattr(yt, "_download_section", section_fails)
    monkeypatch.setattr(yt, "_fetch_full", fetch_full)
    out = yt.download("abc", 5, 9, tmp_path / "a.mp4")
    assert len(tries) == 2 and abs(compose.probe(out)["duration"] - 4) < 0.2
    monkeypatch.setattr(yt, "_fetch_full", lambda vid, full, hook: full.exists())   # đoạn thứ 2 dùng lại file
    assert abs(compose.probe(yt.download("abc", 10, 13, tmp_path / "b.mp4"))["duration"] - 3) < 0.2


def test_seo_names_need_evidence():
    titles = ["Celebrity Kids Saying WAY Too Much...", "Lee Mack's Joke Leaves John Cleese In Near Tears | The Graham Norton Show",
              "Royal Affair Of Laughter - Saif Ali Khan", "Nobody Expected Michelle Obama To Be This Funny"]
    found = seo.names(titles, tags=["john cleese", "michelle obama"], weights=[1.5] * 4)
    assert "John Cleese" in found and "Michelle Obama" in found
    assert not any(n in found for n in ("Saying WAY", "Saif Ali Khan", "Royal Affair", "Graham Norton Show"))


def test_seo_titles_follow_trend_and_search():
    assert seo.main_phrase("celebrity funny", ["celebrity funny", "celebrity funny moments"]) == "celebrity funny"
    assert seo.relevant("celeb funny moments", RESEARCH["phrases"], "funniest celeb audience reactions ever") == \
        ["celeb funny moments", "celeb funny moments reaction"]                        # bỏ "india"
    items = [{"num": 1, "ts": "0:20", "line": "", "comment": ""}]
    out = copywrite.make("celeb funny moments", items, 3, "", "", seed=4, research=RESEARCH, names=["Michelle Obama"],
                         context="Michelle Obama reaction")
    best = out["titles"][0]
    assert "Michelle Obama" in best["text"] and best["score"] >= 8
    assert any(t["text"].startswith("Nobody Expected Michelle Obama") for t in out["titles"])  # mẫu hot nhất tháng
    assert out["description"].startswith("Celeb Funny Moments 😂 featuring Michelle Obama")
    assert "india" not in out["description"] + out["tags"] and "michelle obama" in out["tags"]
    assert len(out["tags"]) <= 500
    ok = dict(seo.score_title("x" * 80, "celeb funny moments", [], [])[1])
    assert not ok["Dài 40–70 ký tự (không bị cắt trên điện thoại)"]


def _hot(i, title, views):
    return {"id": f"hot{i:08d}", "title": title, "channel": "C", "views": views, "duration": 600}


def test_trends_find_hot_topics_and_seed_project(fake_youtube, monkeypatch):
    from clipmaker import trends

    sugg = {"funny moments": ["funny moments", "funny moments roblox", "funny moments compilation"],
            "funny moments i": ["funny moments ishowspeed"]}
    monkeypatch.setattr(seo, "suggestions", lambda q: sugg.get(q, []))
    hot = {
        "funny moments roblox": [_hot(1, "Roblox Funny Moments 😂", 9_000_000), _hot(2, "Roblox Fails", 6_000_000)],
        "funny moments ishowspeed": [_hot(3, "IShowSpeed Loses It With Michelle Obama", 5_000_000),
                                     _hot(4, "Nobody Expected Michelle Obama To Be This Funny", 4_000_000),
                                     _hot(5, "Michelle Obama Savage Moments", 3_000_000)],
    }
    periods = []

    def top_videos(kw, n=30, period="month"):
        periods.append(period)
        return [dict(v) for v in hot.get(kw, [_hot(9, "Funny Moments Compilation", 1000)])]

    monkeypatch.setattr(seo, "top_videos", top_videos)
    monkeypatch.setattr(trends, "_details", lambda vid: {"ts": time.time() - 2 * 86400, "date": None, "likes": 1000,
                                                         "comments": 10, "subs": 50_000, "views": 0})
    assert trends.expand("funny moments")[:3] == ["funny moments", "funny moments roblox", "funny moments compilation"]
    c = TestClient(server.app)
    assert c.post("/api/trends", json={"seed": " "}).status_code == 400
    c.post("/api/trends", json={"seed": "funny moments", "depth": 6})
    for _ in range(100):
        r = c.get("/api/trends").json()
        if r["job"]["done"]:
            break
        time.sleep(0.05)
    assert r["job"]["error"] is None
    res = r["result"]
    names = [t["name"] for t in res["topics"]]
    assert names[0] == "Roblox" and "IShowSpeed" in names and "Michelle Obama" in names
    assert "Compilation" not in names                                        # chữ chung chung không phải chủ đề
    obama = next(t for t in res["topics"] if t["name"] == "Michelle Obama")
    assert obama["keyword"] == "Michelle Obama funny moments" and obama["count"] == 3
    roblox = next(t for t in res["topics"] if t["name"] == "Roblox")
    assert roblox["keyword"] == "funny moments roblox"                      # đúng cụm người xem gõ
    assert res["keywords"][0]["heat"] == 100
    assert set(periods) == {"month"} and res["period"] == "month" and "all" not in res["keywords"][0]
    ana = res["analysis"]
    assert ana["rising"] and ana["rising"][0]["id"] == "hot00000001" and ana["rising"][0]["keyword"] == "funny moments roblox"
    assert ana["rising"][0]["ratio"] == 180 and any("gấp 180 lần" in w for w in ana["rising"][0]["why"])
    c.post("/api/trends", json={"seed": "funny moments", "depth": 6, "period": "week"})
    for _ in range(100):
        r = c.get("/api/trends").json()
        if r["job"]["done"]:
            break
        time.sleep(0.05)
    assert r["result"]["period"] == "week" and periods[-1] == "week"

    # "Tạo video tương tự": quét các video đang hot trước
    seen = []
    real = yt.analyze

    def analyze(vid, comments=120):
        seen.append(vid)
        return real(f"vid{len(seen) % 4}", comments) | {"id": vid}

    monkeypatch.setattr(yt, "analyze", analyze)
    seeds = [v["id"] for v in obama["videos"]] + ["bad id!"]
    pid = c.post("/api/projects", json={"keyword": obama["keyword"], "videos": 4, "length": 4, "minutes": 1,
                                         "seed_videos": seeds, "ref": "Michelle Obama"}).json()["project"]["id"]
    _wait(c, pid)
    p = c.get(f"/api/projects/{pid}").json()["project"]
    assert seen[:3] == seeds[:3] and p["ref"] == "Michelle Obama" and "bad id!" not in p["seed_videos"]


def test_trend_analysis_formats_titles_and_rising(monkeypatch):
    from clipmaker import trends

    now = time.time()
    vids = []
    for i in range(6):                     # Shorts nhiều view hơn, tiêu đề VIẾT HOA nhiều view hơn
        vids.append({"id": f"s{i:010d}", "title": f"He said WHAT {i} 😂", "channel": "Big", "views": 3_000_000 + i,
                     "duration": 30, "keyword": "funny shorts"})
        vids.append({"id": f"l{i:010d}", "title": f"funny moments part {i}", "channel": f"Small{i}", "views": 200_000 + i,
                     "duration": 700 + i, "keyword": "funny moments"})
    det = {v["id"]: {"ts": now - (1 if v["id"].startswith("l") else 10) * 86400, "date": None, "likes": 10,
                     "comments": 0, "subs": 10_000 if v["id"].startswith("l") else 5_000_000, "views": 0} for v in vids}
    det["l0000000005"] = None                             # video lỗi: bỏ qua
    monkeypatch.setattr(trends, "_details", lambda vid: det[vid] or (_ for _ in ()).throw(RuntimeError("x")))
    a = trends.analyze(vids, [], now=now)
    assert a["count"] == 12 and a["format"]["shorts"]["count"] == 6 and a["format"]["long"]["median"] < 1_000_000
    assert a["features"][0]["label"] == "Có từ VIẾT HOA nhấn mạnh" and a["features"][0]["lift"] > 10
    assert a["tips"][0].startswith("Shorts đang ăn khách hơn")
    assert a["lengths"][0]["label"] == "10–20 phút"
    assert "l0000000005" not in [r["id"] for r in a["rising"]]
    small = next(r for r in a["rising"] if r["id"] == "l0000000000")
    assert small["ratio"] == 20 and small["per_day"] == 200_000 and small["similar"] and "l0000000000" not in small["similar"]
    assert a["channels"][0]["name"] == "Big" and trends.analyze([], []) is None
    assert sum(r["channel"] == "Big" for r in a["rising"]) == 2          # mỗi kênh tối đa 2 video đang lên
    vids[0]["title"] = "🔴 LIVE funny stream"
    assert "s0000000000" not in [r["id"] for r in trends.analyze(vids, [], now=now)["rising"]]   # bỏ livestream


def test_auto_makes_everything_with_voiceover(fake_youtube, monkeypatch):
    spoken = []

    def speak(text, out, v=None):
        spoken.append(text)
        compose.run(["-f", "lavfi", "-i", "sine=frequency=600:duration=1.5", str(out)])
        return out

    monkeypatch.setattr(voice, "speak", speak)
    c = TestClient(server.app)
    pid = c.post("/api/projects", json={"keyword": "celeb funny", "videos": 4, "length": 4, "minutes": 1, "auto": True,
                                        "voiceover": True, "takes": 2}).json()["project"]["id"]
    job = _wait(c, pid)
    assert job["kind"] == "auto" and job["error"] is None and job["result"]["shorts"] == 3, job["result"]
    assert not job["result"]["note"], job["result"]["note"]
    v = c.get(f"/api/projects/{pid}").json()
    assert v["output"] and len(spoken) == 2 and len(set(spoken)) == 2
    takes = {x["id"]: x for x in v["project"]["clips"] if x.get("voice_auto")}
    nums = {x["id"]: x["number"] for x in v["plan"]}
    assert sorted(nums[i] for i in takes) == [1, 2]
    assert any("number one" in t["voice_text"].lower() for t in takes.values())
    # tạo lại lời bình cho 1 đoạn: lời bình tự động của đoạn không còn trong top bị bỏ
    c.post(f"/api/projects/{pid}/tts", json={"takes": 1})
    assert _wait(c, pid)["result"]["takes"] == 1
    clips = c.get(f"/api/projects/{pid}").json()["project"]["clips"]
    assert [nums[x["id"]] for x in clips if x.get("voice_auto")] == [1]


def test_voice_lines_use_comments_and_never_repeat():
    items = [{"id": str(i), "num": i, "comment": "this guy is unbelievable 😂😂" if i == 2 else "", "line": ""}
             for i in (3, 2, 1)]
    out = voice.lines(items, seed=1)
    assert "this guy is unbelievable" in out["2"] and "😂" not in out["2"]
    assert len(set(out.values())) == 3 and ("number one" in out["1"].lower() or "not even close" in out["1"])


def test_channels_queue_builds_and_uploads(fake_youtube, monkeypatch):
    from clipmaker import ytapi
    uploads, thumbs = [], []
    monkeypatch.setattr(ytapi, "exchange", lambda cid, sec, code, redirect: {"access_token": "A", "refresh_token": "R"})
    monkeypatch.setattr(ytapi, "channel", lambda tok: {"id": "UC1", "title": "My Funny Channel", "thumb": ""})
    monkeypatch.setattr(ytapi, "refresh", lambda cid, sec, rt: {"access_token": "A2"})

    def upload(tok, path, meta, progress=None, stop=None):
        assert tok == "A2" and Path(path).exists() and len(meta["snippet"]["title"]) <= 100
        uploads.append(meta)
        return f"v{len(uploads)}"

    monkeypatch.setattr(ytapi, "upload", upload)
    monkeypatch.setattr(ytapi, "set_thumbnail", lambda tok, vid, jpg: thumbs.append(vid))
    monkeypatch.setattr(voice, "speak", lambda text, out, v=None: compose.run(
        ["-f", "lavfi", "-i", "sine=duration=1", str(out)]))
    topics = {"seed": "celeb", "keywords": [], "patterns": [], "at": 0,
              "topics": [{"name": "Kevin Hart", "keyword": "kevin hart funny moments", "videos": [{"id": "vid1"}]},
                         {"name": "Roblox", "keyword": "funny moments roblox", "videos": [{"id": "vid2"}]}]}
    monkeypatch.setattr(trends, "research", lambda seed, depth=10, job=None, **kw: topics)
    c = TestClient(server.app)
    assert c.get("/api/channels/connect").status_code == 400          # chưa có client id
    c.put("/api/channels/settings", json={"client_id": "cid", "client_secret": "sec", "workers": 2,
                                          "quota_limit": 12_000})
    url = c.get("/api/channels/connect").json()["url"]
    state = url.split("state=")[1].split("&")[0]
    r = c.get(f"/?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307) and "ok=" in r.headers["location"]
    v = c.get("/api/channels").json()
    assert [a["title"] for a in v["accounts"]] == ["My Funny Channel"] and "refresh" not in v["accounts"][0]
    assert c.put("/api/channels/UC1", json={"long": False, "shorts": False}).status_code == 400
    assert c.put("/api/channels/UC1", json={"daily": "25:00"}).status_code == 400
    c.put("/api/channels/UC1", json={"seeds": ["celeb"], "minutes": 3, "privacy": "public"})
    c.post("/api/channels/UC1/run", json={"count": 2})               # 2 video chạy song song
    for _ in range(3000):
        tasks = c.get("/api/channels").json()["tasks"]
        if all(t["status"] in ("done", "error", "cancelled") for t in tasks):
            break
        time.sleep(0.1)
    assert [t["status"] for t in tasks] == ["done", "done"], [t["error"] for t in tasks]
    assert {t["keyword"] for t in tasks} == {"Kevin Hart", "Roblox"}           # hai luồng không trùng chủ đề
    # hạn mức 12.000: đủ 2 video dài + 2 thumbnail + 5 Shorts; Short thứ 6 bỏ qua kèm cảnh báo, không báo lỗi
    assert len(uploads) == 7 and len(thumbs) == 2
    assert sum("Shorts chưa đăng" in t["warn"] for t in tasks) == 1
    shorts = [m for m in uploads if "#shorts" in m["snippet"]["title"]]
    assert len(shorts) == 5 and all("Full video: https://youtu.be/" in m["snippet"]["description"] for m in shorts)
    assert sum("publishAt" in m["status"] for m in shorts) >= 3                 # Shorts sau được hẹn giờ
    assert c.get("/api/channels").json()["quota"]["used"] == 7 * 1600 + 2 * 50
    c.post("/api/channels/UC1/run", json={})                                   # hết hạn mức: báo ngay, không dựng
    for _ in range(100):
        t = c.get("/api/channels").json()["tasks"][0]
        if t["status"] == "error":
            break
        time.sleep(0.1)
    assert "hạn mức" in t["error"] and not t["project"]
    assert c.get("/").status_code == 200 and "<html" in c.get("/").text


def test_clickbait_title_and_short_thumb_text():
    from clipmaker import copywrite

    items = [{"num": n, "ts": f"0:{n}0", "line": "", "comment": ""} for n in (3, 2, 1)]
    y = copywrite.make("trump funny moments", items, 3, "", "", seed=1, names=["Donald Trump"])
    bait = [t.format(X="Donald Trump", Xs="Donald Trump's", N=3) for t in copywrite.BAIT_PERSON]
    assert y["title"] in bait                              # tiêu đề chính luôn là kiểu giật tít, không lặp "| Trump …"
    assert 2 <= len(y["thumb"].split()) <= 4 and y["thumb"].isupper()
    assert len({t["text"] for t in y["titles"]}) == len(y["titles"])


def test_thumbnail_prefers_main_subject_face(tmp_path):
    from PIL import Image

    from clipmaker import compose

    shot = tmp_path / "s.png"
    Image.new("RGB", (1920, 1080), (90, 120, 160)).save(shot)
    paths = compose.thumbnails([{"img": shot, "face": (900, 200, 1100, 450), "act": None}], "TRUMP DID WHAT?!", 0,
                               tmp_path)
    assert [Image.open(p).size for p in paths] == [(1280, 720)] * 3


def test_bot_block_is_not_retried(monkeypatch, tmp_path):
    tries = []

    def section_blocked(*a):
        tries.append(1)
        raise RuntimeError("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies-from-browser")

    monkeypatch.setattr(yt, "_download_section", section_blocked)
    with pytest.raises(yt.Blocked):
        yt.download("abc", 5, 9, tmp_path / "a.mp4")
    assert len(tries) == 1                                  # không thử lại khi đang bị chặn


def test_bot_block_stops_build_instead_of_short_video(fake_youtube, monkeypatch):
    """YouTube chặn ("not a bot") giữa chừng: dừng tải ngay, báo rõ, không ra video cụt; Dựng lại chỉ tải phần thiếu."""
    c = TestClient(server.app)
    pid = c.post("/api/projects", json={"keyword": "celeb funny", "videos": 4, "length": 4, "minutes": 1}).json()["project"]["id"]
    assert _wait(c, pid)["error"] is None
    ok_download = yt.download
    calls = []

    def flaky(vid, start, end, out, progress=None, full=True):
        calls.append(vid)
        if len(calls) > 2:
            raise yt.Blocked("Sign in to confirm you're not a bot")
        return ok_download(vid, start, end, out)

    monkeypatch.setattr(yt, "download", flaky)
    c.post(f"/api/projects/{pid}/build")
    err = _wait(c, pid)["error"]
    assert "tạm chặn" in err and "Dựng lại" in err
    assert not (server.DATA / pid / "output.mp4").exists()
    monkeypatch.setattr(yt, "download", ok_download)        # hết chặn: dựng lại thành công
    c.post(f"/api/projects/{pid}/build")
    assert _wait(c, pid)["error"] is None and (server.DATA / pid / "output.mp4").exists()


def test_cookie_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "COOKIE_FILE", tmp_path / "cookies.txt")
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(server, "DATA", tmp_path)
    c = TestClient(server.app)
    assert c.get("/api/settings/cookies").json()["mode"] == "none"
    assert c.post("/api/settings/cookies/test").status_code == 400
    bad = c.post("/api/settings/cookies/file", files={"file": ("c.txt", b"hello", "text/plain")})
    assert bad.status_code == 400 and "định dạng" in bad.json()["detail"]
    logged_out = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1999999999\tVISITOR_INFO1_LIVE\tx\n"
    r = c.post("/api/settings/cookies/file", files={"file": ("c.txt", logged_out.encode(), "text/plain")})
    assert r.status_code == 400 and "chưa đăng nhập" in r.json()["detail"]
    good = logged_out + "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tabc\n"
    v = c.post("/api/settings/cookies/file", files={"file": ("c.txt", good.encode(), "text/plain")}).json()
    assert v["mode"] == "file" and v["count"] == 2 and "cookiefile" in yt._base_opts()
    copy = Path(yt._base_opts()["cookiefile"])
    assert copy != tmp_path / "cookies.txt" and copy.read_text() == good       # mỗi luồng một bản sao
    monkeypatch.setattr(yt, "check_access", lambda vid: True)
    assert c.post("/api/settings/cookies/test").json()["tested"]
    assert c.put("/api/settings/cookies", json={"mode": "browser", "browser": "netscape"}).status_code == 400
    assert c.put("/api/settings/cookies", json={"mode": "browser", "browser": "firefox"}).json()["mode"] == "browser"
    assert yt._base_opts()["cookiesfrombrowser"] == ("firefox",)
    v = c.delete("/api/settings/cookies").json()
    assert v["mode"] == "none" and not v["file"] and "cookiefile" not in yt._base_opts()


def test_free_slots_follow_days_and_skip_taken():
    from datetime import datetime

    mon = datetime(2026, 11, 16, 8, 0).timestamp()                    # thứ Hai 16/11/2026 08:00
    a = {"slots_long": ["19:00"], "slots_short": ["21:00", "12:00"], "days": [0, 2],     # T2, T4
         "booked": [{"at": datetime(2026, 11, 16, 19, 0).timestamp()}]}
    got = [datetime.fromtimestamp(x) for x in server._free_slots(a, "long", 2, mon)]
    assert got == [datetime(2026, 11, 18, 19, 0), datetime(2026, 11, 23, 19, 0)]   # T2 19:00 đã có video
    got = [datetime.fromtimestamp(x) for x in server._free_slots(a, "short", 3, mon)]
    assert got == [datetime(2026, 11, 16, 12, 0), datetime(2026, 11, 16, 21, 0), datetime(2026, 11, 18, 12, 0)]


def test_channel_schedule_publishes_at_slots(fake_youtube, monkeypatch):
    from clipmaker import ytapi
    uploads = []
    monkeypatch.setattr(ytapi, "exchange", lambda cid, sec, code, redirect: {"access_token": "A", "refresh_token": "R"})
    monkeypatch.setattr(ytapi, "channel", lambda tok: {"id": "UC1", "title": "Ch", "thumb": ""})
    monkeypatch.setattr(ytapi, "refresh", lambda cid, sec, rt: {"access_token": "A2"})
    monkeypatch.setattr(ytapi, "upload", lambda tok, path, meta, progress=None, stop=None:
                        uploads.append(meta) or f"v{len(uploads)}")
    monkeypatch.setattr(ytapi, "set_thumbnail", lambda tok, vid, jpg: None)
    monkeypatch.setattr(voice, "speak", lambda text, out, v=None: compose.run(
        ["-f", "lavfi", "-i", "sine=duration=1", str(out)]))
    c = TestClient(server.app)
    c.put("/api/channels/settings", json={"client_id": "cid", "client_secret": "sec"})
    state = c.get("/api/channels/connect").json()["url"].split("state=")[1].split("&")[0]
    c.get(f"/?code=abc&state={state}", follow_redirects=False)
    assert c.put("/api/channels/UC1", json={"slots_long": ["7pm"]}).status_code == 400
    assert c.put("/api/channels/UC1", json={"days": []}).status_code == 400
    assert c.post("/api/channels/UC1/run", json={"publish_at": "2020-01-01T10:00"}).status_code == 400   # quá khứ
    v = c.put("/api/channels/UC1", json={"seeds": ["celeb"], "mode": "keywords", "minutes": 3, "privacy": "public",
                                         "schedule": "slots", "slots_long": ["9:30", "20:00"],
                                         "slots_short": ["21:00", "12:00", "17:00"]}).json()
    assert v["accounts"][0]["slots_long"] == ["09:30", "20:00"]
    c.post("/api/channels/UC1/run", json={"count": 1})
    for _ in range(3000):
        t = c.get("/api/channels").json()["tasks"][0]
        if t["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert t["status"] == "done", t["error"]
    ats = [m["status"].get("publishAt") for m in uploads]
    assert len(uploads) == 4 and all(ats) and all(m["status"]["privacyStatus"] == "private" for m in uploads)
    ts = [datetime_utc(x) for x in ats]
    assert ts[0] > time.time() + 15 * 60 and ts[1:] == sorted(ts[1:]) and ts[1] > ts[0]   # Shorts sau video dài
    assert time.strftime("%H:%M", time.localtime(ts[0])) in ("09:30", "20:00")
    assert {time.strftime("%H:%M", time.localtime(x)) for x in ts[1:]} <= {"12:00", "17:00", "21:00"}
    acc = c.get("/api/channels").json()["accounts"][0]
    assert [b["at"] for b in acc["upcoming"]] == ts and all(b["url"] for b in acc["upcoming"])
    assert [x["at"] for x in t["plan"]] == ts


def datetime_utc(s):
    import calendar

    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))


def test_channel_comments_rotating_thumbs_and_stats(fake_youtube, monkeypatch):
    from clipmaker import ytapi
    uploads, thumbs, comments = [], [], []
    monkeypatch.setattr(ytapi, "exchange", lambda cid, sec, code, redirect: {"access_token": "A", "refresh_token": "R"})
    monkeypatch.setattr(ytapi, "channel", lambda tok: {"id": "UC1", "title": "Ch", "thumb": ""})
    monkeypatch.setattr(ytapi, "refresh", lambda cid, sec, rt: {"access_token": "A2"})
    monkeypatch.setattr(ytapi, "upload", lambda tok, path, meta, progress=None, stop=None:
                        uploads.append(meta) or f"v{len(uploads)}")
    monkeypatch.setattr(ytapi, "set_thumbnail", lambda tok, vid, jpg: thumbs.append((vid, jpg.name)))
    monkeypatch.setattr(ytapi, "comment", lambda tok, vid, text: comments.append((vid, text)) or "c1")
    monkeypatch.setattr(voice, "speak", lambda text, out, v=None: compose.run(
        ["-f", "lavfi", "-i", "sine=duration=1", str(out)]))
    c = TestClient(server.app)
    c.put("/api/channels/settings", json={"client_id": "cid", "client_secret": "sec"})
    state = c.get("/api/channels/connect").json()["url"].split("state=")[1].split("&")[0]
    c.get(f"/?code=abc&state={state}", follow_redirects=False)
    assert c.put("/api/channels/UC1", json={"thumb_style": "9"}).status_code == 400
    c.put("/api/channels/settings", json={"quota_limit": 100_000})
    assert c.post("/api/channels/UC1/stats").status_code == 400                # chưa có video
    c.put("/api/channels/UC1", json={"seeds": ["celeb"], "mode": "keywords", "minutes": 3, "privacy": "public"})
    for _ in range(2):
        c.post("/api/channels/UC1/run", json={"count": 1})
        for _ in range(3000):
            t = c.get("/api/channels").json()["tasks"][0]
            if t["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert t["status"] == "done", t["error"]
    assert [x[1] for x in thumbs] == ["thumb_1.jpg", "thumb_2.jpg"]            # xoay vòng mẫu thumbnail
    l1, l2 = [x[0] for x in thumbs]
    acc = c.get("/api/channels").json()["accounts"][0]
    assert acc["uploads"] == len(uploads) > 2 and acc["comment"] is True
    with server.ACC_LOCK:                                                      # giả như đã tới giờ lên sóng
        d = server._accounts()
        ups = d["accounts"][0]["uploads"]
        assert all(u["c_state"] == "pending" and u["comment"] for u in ups)
        for u in ups:
            u["c_due"] = 0
        server._save_accounts(d)
    server._post_comments()
    assert len(comments) == len(uploads) and "👇" in comments[0][1]
    assert any("Full video: https://youtu.be/v1" in text for vid, text in comments if vid == "v2")
    assert {vid for vid, _ in comments} == {f"v{i + 1}" for i in range(len(uploads))}
    ups = c.get("/api/channels/UC1/stats").json()["uploads"]
    assert all(u["c_state"] == "done" for u in ups)

    longs = {l1: 900, l2: 300}
    monkeypatch.setattr(ytapi, "video_stats", lambda tok, ids: {
        i: {"views": longs.get(i, 50), "likes": 1, "comments": 2} for i in ids})

    def no_analytics(tok, ids, start, end):
        raise ytapi.ApiError("YouTube API 403: YouTube Analytics API has not been used")
    monkeypatch.setattr(ytapi, "analytics", no_analytics)
    r = c.post("/api/channels/UC1/stats")
    assert r.status_code == 200, r.text
    st = r.json()
    assert "YouTube Analytics API" in st["note"]
    assert {u["id"]: u["stats"]["views"] for u in st["uploads"]}["v1"] == 900
    by_thumb = next(g for g in st["insights"] if g["by"] == "Mẫu thumbnail")
    assert [r["key"] for r in by_thumb["rows"]] == ["Sticker", "Reaction"] and not by_thumb["sure"]

    monkeypatch.setattr(ytapi, "analytics", lambda tok, ids, start, end: {"v1": {"minutes": 10, "avg_pct": 55.0, "subs": 3}})
    st = c.post("/api/channels/UC1/stats").json()
    assert st["note"] == "" and next(u for u in st["uploads"] if u["id"] == "v1")["stats"]["subs"] == 3


def test_engage_comment_text():
    out = copywrite.make("trump funny moments", [{"num": 1, "ts": "3:20", "line": "", "comment": ""}], 3, "", "",
                         names=["Donald Trump"], seed=1)
    assert out["pin"] and "{" not in out["pin"] and "👇" in out["pin"]
    out = copywrite.make("funny cats", [{"num": 1, "ts": "", "line": "", "comment": ""}], 3, "", "", seed=2)
    assert "{" not in out["pin"] and "  " not in out["pin"]


def test_shorts_only_project(fake_youtube):
    c = TestClient(server.app)
    r = c.post("/api/projects", json={"keyword": "celeb funny", "kind": "shorts", "count": 3, "length": 6,
                                      "videos": 4, "auto": True, "voiceover": True})
    pid = r.json()["project"]["id"]
    job = _wait(c, pid)
    assert job["error"] is None and job["result"]["shorts"] == 3
    v = c.get(f"/api/projects/{pid}").json()
    p = v["project"]
    assert p["kind"] == "shorts" and v["output"] and not (server.DATA / pid / "output.mp4").exists()
    assert len(p["order"]) == 3 and len({c_["vid"] for c_ in p["clips"] if c_["include"]}) == 3   # mỗi video 1 đoạn
    assert not any(c_.get("voice") for c_ in p["clips"])                                           # không lời bình
    sh = p["shorts_out"]
    assert [x["file"] for x in sh] == ["short_1.mp4", "short_2.mp4", "short_3.mp4"]
    assert len({x["hook"] for x in sh}) == 3                                                      # không lặp hook
    assert all(x["title"].endswith("#shorts") and "Follow" in x["description"] and "full video" not in
               x["description"].lower() and x["pin"] for x in sh)
    err = subprocess.run([compose.FFMPEG, "-i", str(server.DATA / pid / "short_1.mp4")], capture_output=True,
                         text=True).stderr
    assert "1080x1920" in err
    assert c.get(f"/api/projects/{pid}/files/short_3.mp4").status_code == 200
    assert p["youtube"]["tags"] and next(x for x in c.get("/api/projects").json() if x["id"] == pid)["kind"] == "shorts"
    v = c.post(f"/api/projects/{pid}/autoselect", json={"count": 2}).json()
    assert len(v["plan"]) == 2 and not v["output"]


def test_reply_to_comment_shorts(fake_youtube, monkeypatch):
    from PIL import Image

    texts = {0: "4:00 lol", 1: "4:01 the way he looked at the camera 💀 I can't breathe",
             2: "4:02 why did nobody talk about this part?", 3: "4:03 this is so true honestly my dad does this"}
    real = yt.analyze

    def analyze(vid, comments=120):
        i = int(vid[-1])
        info = real(vid, comments)
        info["marks"] = [{"t": 400 + i, "likes": 10 ** (i + 1), "text": texts[i], "author": f"@fan{i}"}]
        return info

    monkeypatch.setattr(yt, "analyze", analyze)
    spoken = []

    def speak(text, out, v=None):
        spoken.append((text, v))
        subprocess.run([compose.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", str(out)], check=True)
        return out

    monkeypatch.setattr(voice, "speak", speak)
    c = TestClient(server.app)
    r = c.post("/api/projects", json={"keyword": "celeb funny", "kind": "shorts", "reply": True, "count": 2,
                                      "length": 6, "videos": 4, "auto": True, "voice": "en-US-AndrewNeural"})
    pid = r.json()["project"]["id"]
    job = _wait(c, pid)
    assert job["error"] is None and job["result"]["shorts"] == 2
    p = c.get(f"/api/projects/{pid}").json()["project"]
    assert p["reply"] and p["comments"] and p["title"].startswith("💬")
    picked = [c_ for c_ in p["clips"] if c_["include"]]
    assert "@fan0" not in [c_["author"] for c_ in picked]                  # "4:00 lol": không đủ ý để trả lời
    sh = p["shorts_out"]
    assert {x["author"] for x in sh} <= {"@fan1", "@fan2", "@fan3"} and all(x["reply"] and x["comment"] for x in sh)
    assert not any(x["comment"].startswith("4:0") for x in sh)             # bỏ mốc giờ của video gốc
    assert all(x["title"].endswith("#shorts") and "Replying to @fan" in x["description"] for x in sh)
    assert len(spoken) == 2 and all(v == "en-US-AndrewNeural" for _, v in spoken)
    err = subprocess.run([compose.FFMPEG, "-i", str(server.DATA / pid / "short_1.mp4")], capture_output=True,
                         text=True).stderr
    assert "1080x1920" in err and "Audio" in err
    assert copywrite.reply_line("why is he like this?", random.Random(1)) in copywrite.REPLY_ASK
    assert copywrite.reply_text("0:12 losing his shit 😂") == "losing his s**t 😂"
    assert copywrite.reply_score("you are a retard lol so funny", 10_000) < 0          # bình luận miệt thị: bỏ
    ov = compose.reply_overlay("@a_very_long_user_name_that_goes_on_and_on_forever", "x " * 200, "Glad you asked", "")
    assert isinstance(ov, Image.Image) and ov.size == (1080, 1920)


def test_channel_shorts_only(fake_youtube, monkeypatch):
    from clipmaker import ytapi
    uploads, comments = [], []
    monkeypatch.setattr(ytapi, "exchange", lambda cid, sec, code, redirect: {"access_token": "A", "refresh_token": "R"})
    monkeypatch.setattr(ytapi, "channel", lambda tok: {"id": "UC1", "title": "Ch", "thumb": ""})
    monkeypatch.setattr(ytapi, "refresh", lambda cid, sec, rt: {"access_token": "A2"})
    monkeypatch.setattr(ytapi, "upload", lambda tok, path, meta, progress=None, stop=None:
                        uploads.append((path.name, meta)) or f"v{len(uploads)}")
    monkeypatch.setattr(ytapi, "set_thumbnail", lambda tok, vid, jpg: pytest.fail("Shorts không đặt thumbnail"))
    monkeypatch.setattr(ytapi, "comment", lambda tok, vid, text: comments.append(text) or "c1")
    c = TestClient(server.app)
    c.put("/api/channels/settings", json={"client_id": "cid", "client_secret": "sec"})
    state = c.get("/api/channels/connect").json()["url"].split("state=")[1].split("&")[0]
    c.get(f"/?code=abc&state={state}", follow_redirects=False)
    assert c.put("/api/channels/UC1", json={"long": False, "shorts": False}).status_code == 400
    acc = c.put("/api/channels/UC1", json={"seeds": ["celeb"], "mode": "keywords", "long": False, "shorts_n": 2,
                                           "privacy": "unlisted"}).json()["accounts"][0]
    assert acc["shorts_n"] == 2 and acc["long"] is False
    c.post("/api/channels/UC1/run", json={"count": 1})
    for _ in range(3000):
        t = c.get("/api/channels").json()["tasks"][0]
        if t["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert t["status"] == "done", t["error"]
    assert t["video"] is None and len(t["shorts"]) == 2
    assert [name for name, _ in uploads] == ["short_1.mp4", "short_2.mp4"]
    assert all("Full video" not in m["snippet"]["description"] and m["snippet"]["title"].endswith("#shorts")
               for _, m in uploads)
    assert server.load(t["project"])["kind"] == "shorts"
    with server.ACC_LOCK:
        d = server._accounts()
        for u in d["accounts"][0]["uploads"]:
            u["c_due"] = 0
        server._save_accounts(d)
    server._post_comments()
    assert len(comments) == 2 and all("Full video" not in x for x in comments)


def test_music_titles_and_timeline():
    assert music.split_title("SƠN TÙNG M-TP | CHÚNG TA CỦA TƯƠNG LAI | OFFICIAL MV") == ("SƠN TÙNG M-TP", "CHÚNG TA CỦA TƯƠNG LAI")
    assert music.split_title("ATEEZ(에이티즈) - 'UP' Official MV") == ("ATEEZ(에이티즈)", "UP")
    assert music.split_title("ILLIT (아일릿) 'Magnetic' Official MV") == ("ILLIT", "Magnetic")
    # "TÊN BÀI - Ca sĩ x Ca sĩ": đảo lại cho đúng
    assert music.split_title("Em Sẽ Đi Thật Lâu – Nguyệt Chan x Thanh Hưng (Official Music Video)") == \
        ("Nguyệt Chan x Thanh Hưng", "Em Sẽ Đi Thật Lâu")
    assert music.NOT_SONG.search("Top 20 V-Pop nonstop mix 2026") and not music.NOT_SONG.search("Đen - Lối Nhỏ (M/V)")
    starts, total = music.timeline([200, 180, 150], 3)
    assert starts == [0, 197, 374] and total == 524
    assert music.chart_url("vn", "rap").endswith("/vn/rss/topsongs/limit=100/genre=18/json")
    assert "genre=51" in music.chart_url("kr", "pop") and music.chart_url("kr", "rap") is None
    assert music.chart_url("vn", "lofi") is None                 # không có bảng xếp hạng: tìm YouTube


def test_music_find_prefers_chart_then_fills(monkeypatch):
    monkeypatch.setattr(music, "chart", lambda c, g, n=100: [("Đen", "Lối Nhỏ"), ("Đen", "Mười Năm"), ("Đen", "Bài Này Chill Phết"),
                                                              ("Junk", "Ringtone")])

    def match(artist, song, version="original"):
        if artist == "Junk":
            return None
        return {"id": f"id{abs(hash(song)) % 10**9:011d}"[:11], "title": f"{artist} - {song}", "channel": artist,
                "views": 1_000_000, "duration": 200, "artist": artist, "song": song, "base": song, "version": version}

    monkeypatch.setattr(music, "match", match)
    monkeypatch.setattr(seo, "top_videos", lambda q, n=30, period="month": [
        {"id": "yt000000001", "title": "Mỹ Tâm - Hoa Nắng (Official MV)", "channel": "My Tam", "views": 900_000, "duration": 250},
        {"id": "yt000000002", "title": "TOP 50 nhạc trẻ nonstop", "channel": "x", "views": 5_000_000, "duration": 3600},
        {"id": "yt000000003", "title": "bài AI", "channel": "x", "views": 50, "duration": 200}])
    got = music.find("vn", "pop", 5, "month")
    assert [g["song"] for g in got] == ["Lối Nhỏ", "Mười Năm", "Hoa Nắng"]       # tối đa 2 bài / nghệ sĩ, bỏ rác
    assert got[0]["rank"] == 1
    # bài đã dùng ở bản mix trước: bỏ
    got = music.find("vn", "pop", 5, "month", used={music.song_key("Lối Nhỏ")})
    assert [g["song"] for g in got] == ["Mười Năm", "Bài Này Chill Phết", "Hoa Nắng"]


def test_music_versions_of_alltime_hits(monkeypatch):
    hits = [{"id": "hit00000001", "title": "SÓNG GIÓ | ICM x JACK | OFFICIAL MUSIC VIDEO", "channel": "ICM",
             "views": 480_000_000, "duration": 300},
            {"id": "hit00000002", "title": "NƠI NÀY CÓ ANH | OFFICIAL MUSIC VIDEO | SƠN TÙNG M-TP", "channel": "Sơn Tùng M-TP",
             "views": 460_000_000, "duration": 280}]
    periods = []
    monkeypatch.setattr(seo, "top_videos", lambda q, n=30, period="month": periods.append(period) or hits)
    monkeypatch.setattr(music, "chart", lambda c, g, n=100: [])
    got = music.find("vn", "all", 5)                                    # mặc định: hot nhất mọi thời điểm, bản gốc
    assert set(periods) == {"all"} and [g["id"] for g in got] == ["hit00000001", "hit00000002"]
    assert got[1]["artist"] == "Sơn Tùng M-TP" and got[1]["song"] == "NƠI NÀY CÓ ANH"

    searched = []

    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def extract_info(self, q, download=False):
            searched.append(q)
            return {"entries": [
                {"id": "lofi0000001", "title": "Sóng Gió (Lofi Ver.) - K-ICM x Jack | DH's House", "channel": "x",
                 "view_count": 5_000_000, "duration": 240},
                {"id": "remx0000001", "title": "Sóng Gió (Masew Remix) - K-ICM x Jack", "channel": "x",
                 "view_count": 2_000_000, "duration": 230},
                {"id": "remx0000002", "title": "Nơi Này Có Anh Remix - Sơn Tùng", "channel": "y",
                 "view_count": 900_000, "duration": 220}]}

    monkeypatch.setattr(music.yt_dlp, "YoutubeDL", FakeYDL)
    got = music.find("vn", "all", 5, version="remix")
    assert "remix" in searched[0].lower()
    assert [g["id"] for g in got] == ["remx0000001", "remx0000002"]      # bản lofi trên kênh "House" không tính
    assert got[0]["song"] == "SÓNG GIÓ (Remix)" and got[0]["base"] == "SÓNG GIÓ"
    assert music.song_key(got[0]["song"]) == music.song_key("SÓNG GIÓ")
    assert music.name("vn", "all", version="remix") == "V-Pop Remix"


def test_music_mix_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MIXES", tmp_path / "mixes")
    monkeypatch.setattr(server, "MUSIC_CACHE", tmp_path / "cache")
    songs = [{"id": f"song{i:07d}", "title": f"Artist {i} - Song {i}", "channel": f"Artist {i}", "views": 10**6,
              "duration": 8, "artist": f"Artist {i}", "song": f"Song {i}", "rank": i + 1} for i in range(3)]
    monkeypatch.setattr(music, "find", lambda *a, **kw: songs)

    def audio(vid, out):
        compose.run(["-f", "lavfi", "-i", f"sine=frequency={300 + int(vid[-1]) * 200}:duration=8", "-ac", "2",
                     "-ar", "48000", "-c:a", "flac", str(out)])
        return out

    def cover(vid, out):
        from PIL import Image
        Image.new("RGB", (480, 270), (40 * int(vid[-1]), 90, 160)).save(out)
        return out

    monkeypatch.setattr(music, "download_audio", audio)
    monkeypatch.setattr(music, "fetch_cover", cover)
    c = TestClient(server.app)
    assert c.post("/api/mixes", json={"country": "xx"}).status_code == 400
    monkeypatch.setattr(server, "_music_hist_file", lambda: tmp_path / "music_history.json")
    mid = c.post("/api/mixes", json={"country": "vn", "genre": "pop", "count": 3, "fade": 2, "period": "month"}).json()["mix"]["id"]

    def wait():
        for _ in range(1500):
            job = c.get(f"/api/mixes/{mid}").json()["job"]
            if job and job["done"]:
                return job
            time.sleep(0.1)
        raise AssertionError("job chưa xong")

    assert wait()["error"] is None
    v = c.get(f"/api/mixes/{mid}").json()
    assert [t["song"] for t in v["tracks"]] == ["Song 0", "Song 1", "Song 2"] and v["total"] == 20
    order = [t["id"] for t in v["tracks"]][::-1]
    v = c.put(f"/api/mixes/{mid}", json={"order": order, "track": order[0], "artist": "Nghệ Sĩ Mới"}).json()
    assert v["tracks"][0]["artist"] == "Nghệ Sĩ Mới"
    c.post(f"/api/mixes/{mid}/build")
    job = wait()
    assert job["error"] is None and job["result"]["songs"] == 3
    v = c.get(f"/api/mixes/{mid}").json()
    assert v["output"]
    out = tmp_path / "mixes" / mid / "output.mp4"
    info = compose.probe(out)
    assert info["video"] and info["audio"] and abs(info["duration"] - 20) < 0.6       # 3 x 8 s - 2 x 2 s crossfade
    y = v["mix"]["youtube"]
    assert y["title"].startswith("Top 3 Hot V-Pop Songs")
    assert "0:00 Nghệ Sĩ Mới - Song 2\n0:06 Artist 1 - Song 1\n0:12 Artist 0 - Song 0" in y["description"]
    assert "https://youtu.be/song0000000" in y["description"]
    assert c.get(f"/api/mixes/{mid}/files/thumbnail.jpg").status_code == 200
    v = c.put(f"/api/mixes/{mid}", json={"title": "My Playlist 🎧"}).json()       # đổi tiêu đề: không phải dựng lại
    assert v["mix"]["youtube"]["title"] == "My Playlist 🎧" and v["output"]
    assert c.get("/api/mixes").json()[0]["id"] == mid
    assert c.get("/api/music/options").json()["used"] == {"original": 3}      # lần sau không lặp 3 bài này
    assert c.delete(f"/api/mixes/{mid}").json()["ok"] and c.get(f"/api/mixes/{mid}").status_code == 404


def test_music_mashup_beat_matched(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MIXES", tmp_path / "mixes")
    monkeypatch.setattr(server, "MUSIC_CACHE", tmp_path / "cache")
    monkeypatch.setattr(server, "_music_hist_file", lambda: tmp_path / "music_history.json")
    bpms = {"song0000000": 120, "song0000001": 124, "song0000002": 116}
    songs = [{"id": k, "title": f"A{i} - S{i}", "channel": f"A{i}", "views": 10**6, "duration": 60,
              "artist": f"A{i}", "song": f"S{i}"} for i, k in enumerate(bpms)]
    monkeypatch.setattr(music, "find", lambda *a, **kw: songs)

    def audio(vid, out):                       # tiếng gõ nhịp đúng BPM, 60 giây
        b = bpms[vid]
        compose.run(["-f", "lavfi", "-i", f"aevalsrc='0.8*sin(2*PI*60*t)*exp(-25*mod(t\\,60/{b}))':s=44100:d=60",
                     "-ac", "2", "-ar", "48000", "-c:a", "flac", str(out)])
        return out

    def cover(vid, out):
        from PIL import Image
        Image.new("RGB", (480, 270), (90, 90, 160)).save(out)
        return out

    monkeypatch.setattr(music, "download_audio", audio)
    monkeypatch.setattr(music, "fetch_cover", cover)
    monkeypatch.setattr(yt, "analyze", lambda vid, comments=0: {"duration": 60, "heatmap": [[30, 33, 1.0], [0, 3, 0.2]]})
    c = TestClient(server.app)
    mid = c.post("/api/mixes", json={"country": "vn", "genre": "pop", "count": 3, "style": "mashup", "auto": True,
                                     "period": "month"}).json()["mix"]["id"]
    for _ in range(1500):
        job = c.get(f"/api/mixes/{mid}").json()["job"]
        if job and job["done"]:
            break
        time.sleep(0.1)
    assert job["error"] is None, job["error"]
    v = c.get(f"/api/mixes/{mid}").json()
    bpm = v["mix"]["bpm"]
    assert abs(bpm - 120) < 1.5                                     # nhịp chung = trung vị
    beat = 60 / bpm
    want = 3 * music.MASH_BEATS * beat - 2 * music.MASH_XFADE * beat
    assert abs(compose.probe(tmp_path / "mixes" / mid / "output.mp4")["duration"] - want) < 0.6
    y = v["mix"]["youtube"]
    assert "Mashup" in y["title"] and "3 Hit Songs" in y["title"] and "mashup" in y["tags"]
    assert v["mix"]["tracks"]["song0000000"]["peak"] == 31.5        # đỉnh "Most replayed" được lưu lại


def test_music_bpm_and_beats(tmp_path):
    for b in (92, 128, 150):
        f = tmp_path / f"c{b}.flac"
        compose.run(["-f", "lavfi", "-i", f"aevalsrc='0.8*sin(2*PI*60*t)*exp(-25*mod(t\\,60/{b}))':s=44100:d=40",
                     "-c:a", "flac", str(f)])
        start, length, bpm = music.chorus(f)
        assert abs(bpm - b) < 0.5
        p = 60 / b
        off = (start + p / 2) % p - p / 2
        assert abs(off) < 0.02                                       # bắt đầu đúng phách (sai < 20 ms)
        assert abs(length - music.MASH_BEATS * 60 / bpm) < 0.01
    assert music.common_bpm([60, 124, 240, 118]) in (120, 124)
    assert music.stretch_ratio(64, 128) == 1.0 and music.stretch_ratio(100, 128) is None
    # nhóm nhịp như DJ: bài chậm một nhóm, bài nhanh một nhóm, từ chậm đến nhanh, mỗi bài đổi nhịp <= 12%
    bpms = [115, 95, 92, 120, 90, 128, 140, 85, 140, 132, 134, 130]
    groups = music.tempo_groups(bpms)
    assert sorted(i for _, g in groups for i in g) == list(range(len(bpms)))
    assert [round(g[0]) for g in groups] == sorted(round(g[0]) for g in groups) and len(groups) <= 3
    assert all(music.stretch_ratio(bpms[i], tgt) for tgt, g in groups for i in g)


def _chat_line(t, text, paid=False):
    import json
    key = "liveChatPaidMessageRenderer" if paid else "liveChatTextMessageRenderer"
    return json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(int(t * 1000)), "actions": [
        {"addChatItemAction": {"item": {key: {"message": {"runs": [{"text": text}, {"emoji": {"emojiId": "😂"}}]}}}}}]}})


def test_stream_link_and_chat_parsing(tmp_path):
    for url in ("https://www.youtube.com/watch?v=dez_J8xsFKg&t=10s", "https://youtu.be/dez_J8xsFKg",
                "https://www.youtube.com/live/dez_J8xsFKg?si=x", "dez_J8xsFKg"):
        assert stream.video_id(url) == "dez_J8xsFKg"
    with pytest.raises(ValueError):
        stream.video_id("https://example.com/video")
    f = tmp_path / "c.live_chat.json"
    f.write_text("\n".join([_chat_line(12.5, "hello chat"), _chat_line(20, "big love", paid=True), "rác"]))
    chat = stream.parse_chat(f)
    assert chat == [(12.5, "hello chat😂", 2.0), (20.0, "big love😂", 4.0)]        # 😂 -> cộng điểm, Super Chat x3


def test_stream_highlights_follow_chat_and_shouting():
    dur = 3 * 3600
    rnd = random.Random(1)
    chat = [(rnd.uniform(0, dur), "ok", 1.0) for _ in range(dur // 20)]          # chat đều đều suốt stream
    chat += [(5000 + rnd.uniform(0, 12), "no way bro =))", 2.0) for _ in range(120)]  # bùng nổ sau khoảnh khắc 5000s
    chat += [(60 + rnd.uniform(0, 10), "hi everyone", 1.0) for _ in range(200)]  # chào đầu stream: phải bỏ qua
    db = np.full(dur, -32.0, dtype=np.float32) + np.array([rnd.uniform(-2, 2) for _ in range(dur)], dtype=np.float32)
    db[8000:8004] = -12                                                            # streamer hét lúc 8000s
    score, parts = stream.curve(dur, chat, db)
    hs = stream.highlights(dur, score, parts, db, chat, (), length=45, n=5)
    assert all(h["start"] > stream.SKIP_HEAD for h in hs)
    first = hs[0]
    assert abs(first["peak"] - (5000 + 6 - stream.CHAT_LAG)) <= 8 and "Chat" in first["reasons"][0]
    a = int(first["peak"] - 45 * stream.PEAK_AT)                                  # đầu đoạn rơi vào chỗ yên nhất
    assert first["start"] == a - stream.SNAP + int(np.argmin(db[a - stream.SNAP: a + stream.SNAP // 2 + 1]))
    assert 45 - 2 * stream.SNAP <= first["end"] - first["start"] <= 45 + 2 * stream.SNAP
    assert first["comment"] == "no way bro =))"
    loud = next(h for h in hs if abs(h["peak"] - 8002) <= 4)
    assert loud["start"] < 8000 < loud["end"] and any("Tiếng to" in r for r in loud["reasons"])
    assert all(abs(a["peak"] - b["peak"]) >= 60 for a in hs for b in hs if a is not b)


def test_stream_loudness_reads_audio(tmp_path):
    f = tmp_path / "a.wav"
    compose.run(["-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-f", "lavfi", "-i", "sine=f=440:r=8000",
                 "-filter_complex", "[0]atrim=0:3[a];[1]atrim=0:2,volume=0.5[b];[a][b]concat=n=2:v=0:a=1",
                 str(f)])
    db = stream.loudness(f)
    assert len(db) == 5 and db[:3].max() < -80 and -29 < db[3] < -25          # sine 1/8 x 0.5 -> RMS -27 dB


def test_stream_shorts_project(fake_youtube, monkeypatch):
    dur = 2 * 3600
    rnd = random.Random(2)
    chat = [(rnd.uniform(0, dur), "ok", 1.0) for _ in range(300)]
    for t in (1500, 3000, 4500, 6000):
        chat += [(t + rnd.uniform(0, 10), "bro what was that =))", 2.0) for _ in range(60)]
    monkeypatch.setattr(yt, "analyze", lambda vid, comments=120: {
        "id": vid, "title": "Full Stream 20/11", "channel": "My Channel", "duration": dur, "views": 10,
        "heatmap": [], "chapters": [], "marks": [], "tags": [], "caption_url": None,
        "caption_native": "https://captions/vi"})
    monkeypatch.setattr(stream, "fetch_chat", lambda vid, work: chat)
    monkeypatch.setattr(stream, "fetch_loudness", lambda vid, work, stop: np.full(dur, -30.0, dtype=np.float32))
    c = TestClient(server.app)
    assert c.post("/api/projects", json={"keyword": "x", "stream": "https://example.com/v", "length": 45}).status_code == 400
    assert c.post("/api/projects", json={"keyword": "x", "stream": "abcdefghijk", "length": 10}).status_code == 400
    r = c.post("/api/projects", json={"keyword": "stream", "stream": "https://youtu.be/abcdefghijk", "kind": "shorts",
                                      "count": 3, "length": 30, "auto": True})
    pid = r.json()["project"]["id"]
    job = _wait(c, pid)
    assert job["error"] is None and job["result"]["shorts"] == 3
    v = c.get(f"/api/projects/{pid}").json()
    p = v["project"]
    assert p["stream"] == "abcdefghijk" and p["keyword"] == "My Channel" and p["title"] == "📺 Full Stream 20/11"
    assert p["signals"]["chat"] == len(chat) and p["signals"]["loud"]
    assert len(p["order"]) == 3 and {c_["vid"] for c_ in p["clips"]} == {"abcdefghijk"}   # nhiều đoạn cùng video
    assert p["videos"]["abcdefghijk"]["caption_url"] == "https://captions/vi"
    assert all(25 <= e - s <= 35 for _, s, e in fake_youtube)
    sh = p["shorts_out"]
    assert len(sh) == 3 and all("youtu.be/abcdefghijk" in x["description"] for x in sh)
    assert sh[0]["hook"] == '"bro what was that"'                 # câu chat nhiều người gõ, bỏ ký tự lạ
    cid = p["order"][0]
    clip = next(x for x in p["clips"] if x["id"] == cid)
    assert c.put(f"/api/projects/{pid}/clips/{cid}", json={"end": clip["start"] + 60}).status_code == 200
    assert c.put(f"/api/projects/{pid}/clips/{cid}", json={"end": clip["start"] + 120}).status_code == 400
    assert c.post(f"/api/projects/{pid}/more", json={"videos": 20}).status_code == 400
    assert len(c.post(f"/api/projects/{pid}/autoselect", json={"count": 4}).json()["plan"]) == 4


def test_long_video_from_several_streams(fake_youtube, monkeypatch):
    dur = 3600
    rnd = random.Random(3)

    def analyze(vid, comments=120):
        if vid == "deadstream1":
            raise RuntimeError("Video unavailable")
        return {"id": vid, "title": f"Stream {vid[-1]}", "channel": "My Channel", "duration": dur, "views": 10,
                "heatmap": [], "chapters": [], "marks": [], "tags": [], "caption_url": None, "caption_native": None}

    def chat(vid, work):                 # buổi 2 ít chat hơn hẳn: vẫn phải được chọn ngang buổi 1
        big = vid.endswith("1")
        out = [(rnd.uniform(0, dur), "ok", 1.0) for _ in range(200)]
        for t in range(400, dur - 200, 300):
            out += [(t + rnd.uniform(0, 10), "bro what was that =))" if big else "vl đỉnh quá", 2.0)
                    for _ in range(40 if big else 12)]
        return out

    monkeypatch.setattr(yt, "analyze", analyze)
    monkeypatch.setattr(stream, "fetch_chat", chat)
    monkeypatch.setattr(stream, "fetch_loudness", lambda vid, work, stop: np.full(dur, -30.0, dtype=np.float32))
    c = TestClient(server.app)
    links = ["https://youtu.be/aaaaaaaaaa1", "https://www.youtube.com/live/aaaaaaaaaa2", "deadstream1"]
    r = c.post("/api/projects", json={"keyword": "stream", "streams": links, "length": 30, "minutes": 4,
                                      "shorts": False})
    pid = r.json()["project"]["id"]
    job = _wait(c, pid)
    assert job["error"] is None and job["result"]["videos"] == 2 and job["result"]["failed"] == 1
    p = c.get(f"/api/projects/{pid}").json()["project"]
    assert p["kind"] == "long" and p["streams"] == ["aaaaaaaaaa1", "aaaaaaaaaa2", "deadstream1"]
    assert p["title"] == "My Channel – Best Stream Moments" and any("deadstream1" in n for n in p["signals"]["notes"])
    chosen = [x for i in p["order"] for x in p["clips"] if x["id"] == i]
    assert len(chosen) > 3 and sum(x["end"] - x["start"] for x in chosen) >= 4 * 60 * 0.7
    per = [sum(x["vid"] == v for x in chosen) for v in ("aaaaaaaaaa1", "aaaaaaaaaa2")]
    assert abs(per[0] - per[1]) <= 1                                     # chia đều cho các buổi
    assert not any("vl" in x["comment"] for x in p["clips"])             # câu chat tục không làm hook
    key = [(p["streams"].index(x["vid"]), x["start"]) for x in chosen]
    assert key == sorted(key)                                            # kể theo thứ tự thời gian
    c.post(f"/api/projects/{pid}/build")
    job = _wait(c, pid)
    assert job["error"] is None and (server.DATA / pid / "output.mp4").exists()
    p = c.get(f"/api/projects/{pid}").json()["project"]
    desc = p["youtube"]["description"]
    assert "youtu.be/aaaaaaaaaa1" in desc and "youtu.be/aaaaaaaaaa2" in desc and "#1 " not in desc
    assert "bro what was that" in desc                                  # chương ghi câu chat + mốc giờ
    many = ["https://youtu.be/aaaaaaaaa%02d" % i for i in range(11)]
    assert c.post("/api/projects", json={"keyword": "s", "streams": many, "length": 30}).status_code == 400


def test_lyrics_parse_window_and_audio_pick():
    from clipmaker import lyrics
    lrc = "[offset:+500]\n[00:10.00]Line one\n[00:14.00][01:00.00]Chorus here\n[00:18.00]\n[00:30.00]Line <00:31.00>three"
    p = lyrics.parse_lrc(lrc)
    assert p[0] == (9.5, "Line one") and (59.5, "Chorus here") in p and (29.5, "Line three") in p
    ls = lyrics.lines(p, 80)
    assert (13.5, 17.5, "Chorus here") in ls and all(t1 - t0 <= lyrics.LINE_MAX for t0, t1, _ in ls)
    a, b = lyrics.window(ls, 13, 15, 80)
    assert a == 13.2 and 25 <= b <= 30
    assert lyrics.timeline(ls, a, b, 0)[0][2] == "Chorus here"
    assert lyrics.split_query("Sơn Tùng M-TP - Chúng Ta Của Hiện Tại") == ("Sơn Tùng M-TP", "Chúng Ta Của Hiện Tại")
    cands = [{"id": "mv", "title": "Song X (Official MV)", "duration": 260, "views": 9e8},
             {"id": "rmx", "title": "Song X Remix", "duration": 231, "views": 5e7},
             {"id": "aud", "title": "Song X | Official Audio", "duration": 230, "views": 1e6}]
    assert lyrics.pick_audio(cands, "Song X", 229.5)["id"] == "aud"
    assert lyrics.pick_audio(cands, "Song X", 100) is None
    assert sum(lyrics.chunks([(0, 3, "a"), (4, 9, "b"), (9.5, 30, "c")], 30)) == pytest.approx(30)


def test_tiktok_lyrics_video(fake_youtube, monkeypatch):
    from clipmaker import lyrics
    lrc = "".join(f"[00:{s:02d}.00]Câu hát số {s}\n" for s in range(2, 58, 4))
    recs = [{"trackName": "Bài Test (Remix)", "artistName": "Ca Sĩ", "duration": 60, "syncedLyrics": lrc},
            {"trackName": "Bài Test", "artistName": "Ca Sĩ", "duration": 60, "syncedLyrics": lrc}]
    monkeypatch.setattr(lyrics, "_get", lambda path, params: recs)
    songs = [{"id": "mvmvmvmvmv1", "title": "Ca Sĩ - Bài Test (Official MV)", "duration": 75, "views": 1e8},
             {"id": "audaudaudau", "title": "Bài Test - Official Audio", "duration": 60, "views": 1e6}]
    real_search = yt.search
    monkeypatch.setattr(yt, "search", lambda kw, n: songs if "Bài Test" in kw else real_search(kw, n))
    got = []

    def audio(vid, out, trim=True):
        got.append((vid, trim))
        compose.run(["-f", "lavfi", "-i", "sine=frequency=300:duration=60", "-ac", "2", str(out)])
        return out
    monkeypatch.setattr(music, "download_audio", audio)
    c = TestClient(server.app)
    t = c.post("/api/tiktok", json={"song": "Ca Sĩ - Bài Test", "length": 15, "bg": "city night"}).json()
    for _ in range(3000):
        t = next(x for x in c.get("/api/tiktok").json() if x["id"] == t["id"])
        if t["job"]["done"]:
            break
        time.sleep(0.1)
    assert t["job"]["error"] is None, t["job"]["error"]
    assert got == [("audaudaudau", False)]                  # bản thu âm dài đúng bằng bản có lời, giữ khoảng lặng đầu
    assert t["title"] == "Ca Sĩ - Bài Test" and t["output"] and not t["warn"]
    assert 12 <= t["duration"] <= 19 and "#lyrics" in t["caption"] and "Câu hát số" in t["caption"]
    out = server.DATA / "tiktok" / t["id"] / "output.mp4"
    info = compose.probe(out)
    assert info["audio"] and abs(info["duration"] - t["duration"]) < 0.3
    # chỉnh lệch lời: dựng lại, không tải lại clip nền
    n = len(fake_youtube)
    c.put(f"/api/tiktok/{t['id']}", json={"offset": 0.5})
    for _ in range(3000):
        t = next(x for x in c.get("/api/tiktok").json() if x["id"] == t["id"])
        if t["job"]["done"]:
            break
        time.sleep(0.1)
    assert t["job"]["error"] is None and t["offset"] == 0.5 and len(fake_youtube) == n
    assert c.delete(f"/api/tiktok/{t['id']}").json()["ok"] and not out.exists()


def test_dub_place_never_overlaps():
    from clipmaker import dub
    sr = dub.SR
    lines = [(1.0, 2.0, np.zeros(int(2.0 * sr), np.float32)),       # dài gấp đôi chỗ trống -> đọc nhanh tối đa
             (2.0, 5.0, np.zeros(int(1.0 * sr), np.float32)),
             (2.1, 9.0, np.zeros(int(1.0 * sr), np.float32))]
    (s1, v1), (s2, v2), (s3, v3) = dub.place(lines, 10)
    assert s1 == 1.0 and v1 == dub.MAX_SPEED
    assert s2 >= s1 + 2.0 / v1 and v2 == 1.0               # không đè lên câu trước
    assert s3 >= s2 + 1.0 and v3 == 1.0


def test_dub_upload_dub_and_edit(fake_youtube, monkeypatch, tmp_path):
    from clipmaker import dub
    monkeypatch.setattr(dub, "transcribe", lambda src, lang=None: ("zh", [{"s": 0.5, "e": 1.8, "src": "你好"},
                                                                          {"s": 2.0, "e": 3.2, "src": "再见"}]))
    monkeypatch.setattr(dub, "translate", lambda texts, lang, tl="vi": [f"câu {t}" for t in texts])
    said = []

    def tts(items, voice, report=None, stop=None):
        for text, out in items:
            said.append(text)
            compose.run(["-f", "lavfi", "-i", "sine=frequency=500:duration=0.8", str(out)])
    monkeypatch.setattr(dub, "_tts_all", tts)
    c = TestClient(server.app)
    assert c.post("/api/dub", json={"source": "", "owner": False}).status_code == 400
    d = c.post("/api/dub", json={"source": "", "owner": True}).json()
    files = []
    for i, (w, h) in enumerate(((360, 640), (360, 640), (640, 360))):
        f = tmp_path / f"c{i}.mp4"
        compose.run(["-f", "lavfi", "-i", f"color=c=blue:s={w}x{h}:d=4", "-f", "lavfi", "-i", "sine=d=4",
                     "-shortest", "-c:v", "libx264", "-c:a", "aac", str(f)])
        files.append(("files", (f.name, f.read_bytes(), "video/mp4")))
    d = c.post(f"/api/dub/{d['id']}/upload", files=files).json()
    keys = [x["key"] for x in d["clips"]]
    assert len(keys) == 3 and all(x["duration"] for x in d["clips"])
    # bỏ clip giữa, đảo thứ tự
    c.put(f"/api/dub/{d['id']}", json={"order": [keys[2], keys[1], keys[0]], "on": {keys[1]: False}})
    assert c.post(f"/api/dub/{d['id']}/build").status_code == 200

    def wait():
        for _ in range(3000):
            x = next(y for y in c.get("/api/dub").json() if y["id"] == d["id"])
            if x["job"]["done"]:
                return x
            time.sleep(0.1)
    d = wait()
    assert d["job"]["error"] is None, d["job"]["error"]
    assert d["size"] == [1080, 1920] and abs(d["duration"] - 8) < 0.4 and not d["warn"]
    assert [x["key"] for x in d["clips"]] == [keys[2], keys[1], keys[0]] and keys[1] not in d["segs"]
    assert sorted(said) == ["câu 你好", "câu 再见"]                # câu trùng chữ chỉ đọc một lần
    info = dub.probe(server.DATA / "dub" / d["id"] / "output.mp4")
    assert info["audio"] and (info["w"], info["h"]) == (1080, 1920)
    # sửa bản dịch: dựng lại, đọc câu mới, không nhận giọng lại
    monkeypatch.setattr(dub, "transcribe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nhận giọng lại")))
    c.put(f"/api/dub/{d['id']}", json={"segs": {keys[0]: ["Chào sư phụ", "Tạm biệt"]}})
    said.clear()
    c.post(f"/api/dub/{d['id']}/build")
    d = wait()
    assert d["job"]["error"] is None, d["job"]["error"]
    assert d["segs"][keys[0]]["items"][0]["tr"] == "Chào sư phụ" and "Chào sư phụ" in said
    assert c.delete(f"/api/dub/{d['id']}").json()["ok"]


def test_dub_scan_orders_episodes_and_flags_duplicates(fake_youtube, monkeypatch, tmp_path):
    from clipmaker import dub
    lines = {"a": "师父你为什么不让我下山我已经练了十年", "b": "因为山下的世界很危险你还没有准备好"}
    c = TestClient(server.app)
    d = c.post("/api/dub", json={"source": "", "owner": True}).json()
    f = tmp_path / "x.mp4"
    compose.run(["-f", "lavfi", "-i", "color=c=red:s=360x640:d=3.5", "-f", "lavfi", "-i", "sine=d=3.5", "-shortest",
                 "-c:v", "libx264", "-c:a", "aac", str(f)])
    names = ["Phim hay 第2集", "Phim hay 第1集", "Phim hay 第2集 (đăng lại)", "Hậu trường"]
    d = c.post(f"/api/dub/{d['id']}/upload",
               files=[("files", (n + ".mp4", f.read_bytes(), "video/mp4")) for n in names]).json()
    by_title = {x["title"]: x["key"] for x in d["clips"]}
    said = {by_title[names[0]]: lines["b"], by_title[names[1]]: lines["a"], by_title[names[2]]: lines["b"],
            by_title[names[3]]: ""}
    order_seen = []

    def transcribe(src, lang=None):
        key = Path(src).stem
        order_seen.append(key)
        return "zh", ([{"s": 0.3, "e": 2.0, "src": said[key]}] if said[key] else [])
    monkeypatch.setattr(dub, "transcribe", transcribe)
    monkeypatch.setattr(dub, "translate", lambda texts, lang, tl="vi": [f"vi {t}" for t in texts])
    c.post(f"/api/dub/{d['id']}/scan")
    for _ in range(3000):
        d = next(y for y in c.get("/api/dub").json() if y["id"] == d["id"])
        if d["job"]["done"]:
            break
        time.sleep(0.1)
    assert d["job"]["error"] is None, d["job"]["error"]
    assert d["scanned"] and len(order_seen) == 4
    titles = [x["title"] for x in d["clips"]]
    assert titles.index(names[1]) < titles.index(names[0])           # tập 1 trước tập 2
    rv, on = d["review"], {x["title"]: x["on"] for x in d["clips"]}
    assert rv[by_title[names[1]]]["ep"] == 1 and rv[by_title[names[0]]]["ep"] == 2
    dup = [k for k in (by_title[names[0]], by_title[names[2]]) if any(x.startswith("dup:") for x in rv[k]["flags"])]
    assert len(dup) == 1 and sum(on[n] for n in (names[0], names[2])) == 1     # chỉ giữ một bản
    assert "silent" in rv[by_title[names[3]]]["flags"] and on[names[3]]
    assert c.get(f"/api/dub/{d['id']}/thumb/{by_title[names[1]]}.jpg").status_code == 200
    assert c.get(f"/api/dub/{d['id']}/src/{by_title[names[1]]}").status_code == 200


def test_douyin_links_and_cookie_file(fake_youtube, monkeypatch, tmp_path):
    from clipmaker import dub
    vid = "https://www.douyin.com/video/7710584006282817146"
    for u in ("https://www.douyin.com/jingxuan?modal_id=7710584006282817146",
              "https://www.douyin.com/user/MS4wLjABAAAA?from_tab_name=main&modal_id=7710584006282817146",
              "https://www.douyin.com/note/7710584006282817146",
              "https://www.iesdouyin.com/share/video/7710584006282817146/?region=CN", vid):
        assert dub.canon(u) == vid
    assert dub.canon("https://www.tiktok.com/@a/video/1") == "https://www.tiktok.com/@a/video/1"
    monkeypatch.setattr(dub, "DOUYIN_COOKIES", tmp_path / "dy.txt")

    def no_net(*a):
        raise dub.DubError("không tải trong test")
    monkeypatch.setattr(dub, "download", no_net)
    c = TestClient(server.app)
    # link video mở từ trang người dùng (có modal_id) là một video, không phải cả kênh
    d = c.post("/api/dub", json={"source": "https://www.douyin.com/user/MS4w?modal_id=7710584006282817146",
                                 "owner": True}).json()
    assert [x["key"] for x in d["clips"]] == ["7710584006282817146"]
    c.post(f"/api/dub/{d['id']}/cancel")
    bad = c.post("/api/dub-cookies", files={"file": ("c.txt", b".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\n")})
    assert bad.status_code == 400 and "douyin" in bad.json()["detail"]
    ok = c.post("/api/dub-cookies", files={"file": ("c.txt", b".douyin.com\tTRUE\t/\tTRUE\t0\tttwid\tx\n")}).json()
    assert ok["has"] and (tmp_path / "dy.txt").exists()
    assert not c.delete("/api/dub-cookies").json()["has"]


def test_dub_tts_hang_does_not_block(monkeypatch, tmp_path):
    """Dịch vụ giọng đọc treo hẳn ở một câu: không được đứng mãi, câu đó bỏ tiếng, các câu khác vẫn đọc."""
    import asyncio
    import edge_tts
    from clipmaker import dub

    class Fake:
        def __init__(self, text, voice, rate=""):
            self.text = text

        async def save(self, path):
            if self.text == "treo":
                await asyncio.Event().wait()            # không bao giờ trả lời
            compose.run(["-f", "lavfi", "-i", "sine=duration=0.5", "-f", "mp3", path])
    monkeypatch.setattr(edge_tts, "Communicate", Fake)
    monkeypatch.setattr(dub, "TTS_TIMEOUT", 0.3)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))         # không chờ giữa các lần thử
    seen = []
    t0 = time.time()
    segs = [{"s": 0.5, "e": 1.5, "tr": "một"}, {"s": 2.0, "e": 3.0, "tr": "treo"}, {"s": 3.5, "e": 4.5, "tr": "ba"}]
    wav, missed = dub.dub_track(segs, 6.0, "vi-VN-HoaiMyNeural", tmp_path / "d.wav", tmp_path / "tts",
                                lambda done, n: seen.append((done, n)))
    assert time.time() - t0 < 30 and missed == 1 and wav.exists()
    assert seen[-1] == (3, 3)
    # dựng lại: câu đã đọc lấy từ bộ đệm, chỉ thử lại câu lỗi
    seen.clear()
    dub.dub_track(segs, 6.0, "vi-VN-HoaiMyNeural", tmp_path / "d.wav", tmp_path / "tts", lambda d, n: seen.append(n))
    assert seen == [1]


def _wait_posts(c, done=lambda p: all(v["state"] in ("ok", "err") for v in p["status"].values())):
    for _ in range(300):
        posts = c.get("/api/social").json()["posts"]
        if posts and all(done(p) for p in posts):
            return posts
        time.sleep(0.1)
    raise AssertionError("chưa đăng xong")


def test_social_connect_and_post(fake_youtube, monkeypatch, tmp_path):
    from clipmaker import social
    monkeypatch.setattr(server, "SOCIAL_FILE", tmp_path / "social.json")
    calls = []
    monkeypatch.setattr(social, "fb_exchange", lambda app, sec, code, red: (
        calls.append(("fbx", code, red)) or [{"id": "p1", "name": "Trang Phim", "token": "PT"}]))
    monkeypatch.setattr(social, "tt_exchange", lambda key, sec, code, red, ver: (
        calls.append(("ttx", code, len(ver))) or {"open_id": "o", "access": "A1", "refresh": "R", "exp": 0,
                                                  "rexp": time.time() + 1e6, "scope": "video.upload"}))
    monkeypatch.setattr(social, "tt_user", lambda a: "kenh_cua_toi")
    monkeypatch.setattr(social, "tt_refresh", lambda key, sec, r: (
        calls.append(("refresh", r)) or {"access": "A2", "refresh": "R2", "exp": time.time() + 3600,
                                         "rexp": time.time() + 1e6}))
    monkeypatch.setattr(social, "fb_post", lambda page, tok, path, title, desc, dur, vert: (
        calls.append(("fb", page, tok, desc, vert)) or ("v9", "https://facebook.com/reel/v9")))
    monkeypatch.setattr(social, "tt_inbox", lambda access, path: calls.append(("tt", access)) or "pub1")
    monkeypatch.setattr(social, "tt_wait", lambda access, pid: "SEND_TO_USER_INBOX")
    c = TestClient(server.app, follow_redirects=False)
    assert c.get("/api/social/fb/connect").status_code == 400          # chưa nhập App ID
    v = c.put("/api/social", json={"fb_app_id": "123", "fb_secret": "FBSECRET", "tt_key": "k", "tt_secret": "TTSECRET"}).json()
    assert v["fb"]["has_secret"] and "FBSECRET" not in json_dumps(v) and "TTSECRET" not in json_dumps(v)
    for net in ("fb", "tt"):
        url = c.get(f"/api/social/{net}/connect").json()["url"]
        state = urllib_parse.parse_qs(urllib_parse.urlparse(url).query)["state"][0]
        if net == "tt":
            assert "code_challenge" in url and "video.upload" in urllib_parse.unquote(url)
        r = c.get(f"/api/social/{net}/callback", params={"code": "C", "state": state})
        assert r.status_code in (302, 307) and "L%E1%BB%97i" not in r.headers["location"]
    # state lạ: không nhận
    assert "L%E1%BB%97i" in c.get("/api/social/fb/callback", params={"code": "C", "state": "x"}).headers["location"]
    v = c.get("/api/social").json()
    assert v["fb"]["page"] == "p1" and v["tt"]["connected"] and v["tt"]["name"] == "kenh_cua_toi"
    assert '"PT"' not in json_dumps(v) and "A1" not in json_dumps(v)              # không lộ token ra giao diện
    # video dọc có sẵn: đăng ngay lên cả hai
    d = c.post("/api/dub", json={"source": "", "owner": True}).json()
    out = server.DATA / "dub" / d["id"] / "output.mp4"
    assert c.post("/api/social/posts", json={"kind": "dub", "ref": d["id"], "targets": ["tiktok"]}).status_code == 400
    _fake_video(out, 3, size="360x640")
    c.post("/api/social/posts", json={"kind": "dub", "ref": d["id"], "targets": ["tiktok", "facebook"], "caption": "Tập 1 #phim"})
    p = _wait_posts(c)[0]
    assert p["status"]["facebook"]["state"] == "ok" and p["status"]["facebook"]["link"].endswith("v9")
    assert p["status"]["tiktok"]["state"] == "ok" and "hộp thư" in p["status"]["tiktok"]["msg"]
    assert ("fb", "p1", "PT", "Tập 1 #phim", True) in calls
    assert ("refresh", "R") in calls and ("tt", "A2") in calls           # token TikTok hết hạn -> tự làm mới
    # hẹn giờ: chưa tới giờ thì không đăng
    n = len(calls)
    c.post("/api/social/posts", json={"kind": "dub", "ref": d["id"], "targets": ["facebook"], "at": time.time() + 3600})
    server._social_tick()
    later = [p for p in c.get("/api/social").json()["posts"] if p["at"] > time.time() + 60][0]
    assert later["status"]["facebook"]["state"] == "wait" and len(calls) == n
    # lỗi một bài: báo lỗi, thử lại được
    monkeypatch.setattr(social, "fb_post", lambda *a: (_ for _ in ()).throw(social.SocialError("hết quyền")))
    c.post("/api/social/posts", json={"kind": "dub", "ref": d["id"], "targets": ["facebook"]})
    posts = _wait_posts(c, lambda p: p["at"] > time.time() + 60 or p["status"]["facebook"]["state"] in ("ok", "err"))
    bad = next(p for p in posts if p["status"]["facebook"]["state"] == "err")
    assert bad["status"]["facebook"]["msg"] == "hết quyền"
    monkeypatch.setattr(social, "fb_post", lambda *a: ("v10", "https://facebook.com/x"))
    c.post(f"/api/social/posts/{bad['id']}/retry")
    posts = _wait_posts(c, lambda p: p["at"] > time.time() + 60 or p["status"]["facebook"]["state"] in ("ok", "err"))
    assert next(p for p in posts if p["id"] == bad["id"])["status"]["facebook"]["state"] == "ok"
    assert c.delete(f"/api/social/posts/{later['id']}").status_code == 200


def test_social_tiktok_chunks_and_pkce():
    from clipmaker import social
    assert social.tt_chunks(30 << 20) == (30 << 20, 1)
    size = 200 * 1024 * 1024 + 123
    chunk, n = social.tt_chunks(size)
    assert chunk == 10 << 20 and n == 20 and size - chunk * (n - 1) <= 128 << 20    # phần cuối gộp phần dư
    v, ch = social.tt_pkce()
    import hashlib as _h
    assert 43 <= len(v) <= 128 and ch == _h.sha256(v.encode()).hexdigest()


def test_captions_from_real_content():
    from clipmaker import caption
    lines = ["Con đi đâu vậy?", "Mẹ ơi, tại sao mẹ lại bỏ con ở trại trẻ mồ côi?", "ừ", "Đi thôi"]
    tt = caption.for_dub("Mẹ Kế", lines, "zh", [1, 2, 3], "tiktok", seed=1)
    fb = caption.for_dub("Mẹ Kế", lines, "zh", [1, 2, 3], "facebook", seed=1)
    assert "Tập 1–3" in tt and "#mek" in tt and "#phimtrung" in tt and "#longtieng" in tt
    assert any(q in tt for q in lines[:2])                        # câu mồi lấy đúng lời thoại thật
    assert "#phimgiadinh" in tt and "#camdong" in tt
    n = lambda c: len([w for w in c.split() if w.startswith("#")])
    assert n(tt) == 8 and n(fb) == 5 and len(tt) <= 2200
    assert "Follow để xem tập tiếp theo" in tt or "Theo dõi" in tt or "Lưu lại" in tt
    assert caption.for_dub("Mẹ Kế", lines, seed=1) != caption.for_dub("Mẹ Kế", lines, seed=2)   # Viết lại ra bản khác
    assert "#phimghep" not in caption.for_dub("Phim ghép", [], seed=1)
    one = caption.for_dub("X Y", ["Anh ấy là ai vậy?"], eps=[], seed=3)
    assert "tập tiếp theo" not in one                                 # không có nhiều tập thì không hứa tập sau
    ly = caption.for_lyrics("Sơn Tùng M-TP", "Chúng Ta Của Hiện Tại (Remake)", ["Còn ai thương ai, mong ai?", "Em ơi"],
                            seed=1)
    assert "#chungtacuahientai" in ly and "#sontungmtp" in ly and "#nhacyeu" in ly and "“" in ly
    assert caption.hashtag("Đen Vâu") == "#denvau"


def test_mv_plan_subtitles_and_texts(tmp_path):
    from clipmaker import mv
    tl = [(2.0, 6.0, "故事的小黄花"), (6.0, 10.5, "从出生那年就飘着"), (10.5, 15.0, "童年的荡秋千"),
          (15.0, 40.0, "随记忆一直晃到现在")]
    assert mv.lang_of([x[2] for x in tl]) == "zh" and mv.lang_of(["Anh còn nợ em"]) == "vi"
    scenes, images = mv.plan(tl, 42.0, n_max=4)
    assert abs(sum(s["d"] for s in scenes) - 42.0) < 0.01
    assert all(mv.SCENE_MIN - 0.01 <= s["d"] <= mv.SCENE_MAX + 0.01 for s in scenes[:-1])
    assert len(images) == 4 and images[0]["kind"] == "stage" and {s["img"] for s in scenes} == {0, 1, 2, 3}
    assert all(any(abs(s["s"] - t0) < 0.01 for t0, _, _ in tl) or s["s"] == 0 or s["d"] <= mv.SCENE_MAX
               for s in scenes)
    p = mv.prompt({"kind": "story", "line": "x"}, "f", "Little yellow flower in the story")
    assert "Little yellow flower" in p and "glasses" not in p
    assert "glasses" in mv.prompt({"kind": "singer", "line": ""}, "f") and "suit" in mv.prompt({"kind": "stage"}, "m")
    vi = {"故事的小黄花": "Đóa hoa vàng nhỏ trong câu chuyện"}
    for fmt in "hv":
        a = mv.ass(tl, vi, tmp_path / f"{fmt}.ass", fmt).read_text(encoding="utf-8")
        assert "\\kf" in a and "\\N{\\rVi}Đóa hoa vàng nhỏ" in a and mv.CJK_FONT in a
        assert a.count("Dialogue:") == 4 and a.count("\\rVi") == 1      # câu chưa dịch: chỉ lời gốc
    title, desc = mv.youtube_text("晴天", "周杰伦", "zh", tl + tl, vi)
    assert title == "晴天 – 周杰伦 | Vietsub" and desc.count("故事的小黄花") == 1 and mv.AI_NOTE in desc


def test_youtube_drops_cookies_when_no_formats(monkeypatch):
    monkeypatch.setitem(yt.COOKIES, "browser", "chrome")
    monkeypatch.setattr(yt, "_NO_COOKIE_UNTIL", [0.0])
    assert yt._cookie_opts() == {"cookiesfrombrowser": ("chrome",)}
    assert yt.no_formats("ERROR: [youtube] x: Requested format is not available. Use --list-formats")
    assert yt.drop_cookies() and yt._cookie_opts() == {} and not yt.drop_cookies()


def test_mv_build_edit_and_redraw(fake_youtube, monkeypatch):
    from clipmaker import dub, lyrics, mv
    words = ["故事的小黄花", "从出生那年就飘着", "童年的荡秋千", "随记忆一直晃到现在", "吹着前奏望着天空", "我想起花瓣试着掉落"]
    lrc = "".join(f"[00:{s:02d}.00]{words[k % len(words)]}\n" for k, s in enumerate(range(2, 58, 4)))
    monkeypatch.setattr(lyrics, "_get", lambda path, params: [
        {"trackName": "晴天", "artistName": "周杰伦", "duration": 60, "syncedLyrics": lrc}])
    songs = [{"id": "audaudaudau", "title": "周杰伦 晴天 Official Audio", "duration": 60, "views": 1e6}]
    real_search = yt.search
    monkeypatch.setattr(yt, "search", lambda kw, n: songs if "晴天" in kw else real_search(kw, n))

    def audio(vid, out, trim=True):
        compose.run(["-f", "lavfi", "-i", "sine=frequency=300:duration=60", "-ac", "2", str(out)])
        return out
    monkeypatch.setattr(music, "download_audio", audio)
    monkeypatch.setattr(dub, "translate", lambda texts, lang, tl="vi": [f"{tl}: memory of {t} in the rain" for t in texts])
    drawn = []

    def draw(text, seed, out, key="", stop=lambda: False, wait=lambda s: None):
        drawn.append(text)
        compose.run(["-f", "lavfi", "-i", f"color=c=0x{seed % 0xFFFFFF:06x}:s=1344x768", "-frames:v", "1", str(out)])
        return out
    monkeypatch.setattr(mv, "draw", draw)
    c = TestClient(server.app)

    def wait(mid):
        for _ in range(3000):
            t = next(x for x in c.get("/api/mv").json() if x["id"] == mid)
            if t["job"]["done"]:
                return t
            time.sleep(0.1)
    t = c.post("/api/mv", json={"song": "周杰伦 - 晴天", "length": 30, "formats": "hv", "singer": "f"}).json()
    t = wait(t["id"])
    assert t["job"]["error"] is None, t["job"]["error"]
    assert t["outputs"] == ["h", "v"] and t["lang"] == "zh" and t["title"] == "周杰伦 - 晴天"
    assert len(drawn) == len(t["images"]) == len(t["drawn"]) and any("en:" in d for d in drawn)  # cảnh minh hoạ theo lời
    assert all(t["vi"][x] == f"vi: memory of {x} in the rain" for x in t["lines"])
    f = server.DATA / "mv" / t["id"]
    for fmt, (w, h) in mv.SIZES.items():
        info = dub.probe(f / f"{fmt}.mp4")
        assert (info["w"], info["h"]) == (w, h) and info["audio"] and abs(info["duration"] - t["duration"]) < 0.3
    assert (f / "thumb.jpg").exists() and "Vietsub" in t["yt_title"]
    assert "#vietsub" in t["caption"] and "#nhactrung" in t["caption"] and "vi:" in t["caption"]
    assert server._social_video("mv", f).name == "v.mp4"
    # sửa lời dịch, vẽ lại một ảnh: đánh dấu cần dựng lại, không vẽ lại cả bộ
    first = t["lines"][0]
    t = c.put(f"/api/mv/{t['id']}", json={"vi": {first: "Đóa hoa vàng nhỏ", "lạ": "x"}}).json()
    assert t["dirty"] and t["vi"][first] == "Đóa hoa vàng nhỏ" and "lạ" not in t["vi"]
    n = len(drawn)
    c.post(f"/api/mv/{t['id']}/images/1/redraw", json={"prompt": "a girl in the rain"})
    t = wait(t["id"])
    assert t["job"]["error"] is None and len(drawn) == n + 1 and drawn[-1] == "a girl in the rain"
    assert t["images"][1]["custom"] and "1" in t["img_v"]
    c.post(f"/api/mv/{t['id']}/build")
    t = wait(t["id"])
    assert t["job"]["error"] is None and not t["dirty"] and len(drawn) == n + 1
    assert "Đóa hoa vàng nhỏ" in (f / "mv.json").read_text(encoding="utf-8")
    assert c.get(f"/api/mv/{t['id']}/files/../mv.json").status_code == 404
    assert c.delete(f"/api/mv/{t['id']}").json()["ok"] and not f.exists()


def test_mv_story_prompt_skips_note_singing():
    from clipmaker import mv
    assert mv.STORY_FALLBACK in mv.prompt({"kind": "story"}, "f", "Re So So Si Do Si La")
    assert mv.STORY_FALLBACK in mv.prompt({"kind": "story"}, "f", "la la")
    assert "petals falling" in mv.prompt({"kind": "story"}, "f", "I remember the petals falling")


def test_ui_files_are_revalidated():
    c = TestClient(server.app)
    for path in ("/", "/app.js", "/style.css"):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert "showMV" in c.get("/app.js").text and 'href="#/mv"' in c.get("/").text


def test_info_only_calls_ignore_missing_formats(monkeypatch):
    """Cookie đăng nhập có lúc làm YouTube không trả định dạng nào: đọc thông tin (tên, độ dài, heatmap) vẫn phải chạy."""
    import yt_dlp
    seen = []

    class FakeYDL:
        def __init__(self, opts):
            seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            if not seen[-1].get("ignore_no_formats_error"):
                raise yt_dlp.utils.DownloadError("ERROR: [youtube] x: Requested format is not available")
            return {"id": "K2sZVlw5bts", "title": "周杰伦 - 晴天", "channel": "x", "duration": 270, "entries": []}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    assert music.info("https://www.youtube.com/watch?v=K2sZVlw5bts")["duration"] == 270
    yt.analyze("K2sZVlw5bts", 0)
    assert all(o.get("ignore_no_formats_error") for o in seen)
