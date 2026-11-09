import time

import pytest
from fastapi.testclient import TestClient

import server
from clipmaker import compose, copywrite, moments
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
    assert len(yt_copy["titles"]) >= 3 and yt_copy["title"] in yt_copy["titles"]
    assert desc.startswith(yt_copy["hook"]) and "#1" in yt_copy["hook"]                 # lời dẫn nhử tới #1
    again = c.post(f"/api/projects/{pid}/copy").json()["project"]["youtube"]
    assert "#3 Funny video" in again["description"]
    for i in (1, 2, 3):
        assert c.get(f"/api/projects/{pid}/files/thumb_{i}.jpg").headers["content-type"] == "image/jpeg"
    assert (tmp_path / "data" / pid / "subs").exists()

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
    assert any("You're not my dad?" in t for t in out["titles"]) and all(len(t) <= 100 for t in out["titles"])
    assert "Moments Moments" not in " ".join(out["titles"]) and "#celebrity" in out["description"]
    assert copywrite.make("celebrity funny moments", items, 2, "", "", seed=2) != out     # viết lại ra bản khác
