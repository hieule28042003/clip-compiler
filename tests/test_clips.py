import subprocess
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

import server
from clipmaker import compose, copywrite, moments, seo, trends, voice
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
    for _ in range(1500):
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

    def download(vid, start, end, out, progress=None):
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
    monkeypatch.setattr(seo, "month_top", lambda kw, n=30: hot.get(kw, [_hot(9, "Funny Moments Compilation", 1000)]))
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
    monkeypatch.setattr(trends, "research", lambda seed, depth=10, job=None: topics)
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

    def flaky(vid, start, end, out, progress=None):
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
