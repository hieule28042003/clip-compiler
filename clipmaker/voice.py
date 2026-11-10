"""Lời bình bằng giọng đọc tự động: câu bình ngắn cho các đoạn hay nhất + đọc bằng edge-tts (miễn phí, cần mạng).

Câu bình dựa vào dữ liệu thật của đoạn: bình luận nhiều like nhất ở mốc đó, câu thoại ngay trước đỉnh gây cười,
nên mỗi video có phần bình riêng (video mang tính biến đổi hơn so với chỉ ghép đoạn).
"""
import asyncio
import random
import re

VOICES = {
    "en-US-AndrewNeural": "Andrew (nam, Mỹ)",
    "en-US-AvaNeural": "Ava (nữ, Mỹ)",
    "en-US-BrianNeural": "Brian (nam, Mỹ, trẻ)",
    "en-GB-RyanNeural": "Ryan (nam, Anh)",
}
DEFAULT_VOICE = "en-US-AndrewNeural"

WITH_COMMENT = [
    "Someone in the comments said: {C}. And honestly? Same.",
    "The top comment on this one: {C}. I can't argue with that.",
    "One viewer wrote: {C}. Yeah, that's exactly how I felt.",
]
WITH_LINE = [
    "The second they said \"{L}\", it was over.",
    "\"{L}\". Nobody in that room was ready for that.",
    "Just listen to the timing on \"{L}\". Perfect.",
]
GENERIC = [
    "Okay, I was not ready for that one.",
    "Rewind that. Seriously, watch it one more time.",
    "How did nobody in that room lose it right there?",
    "The timing on that is just perfect.",
    "I've watched this way too many times, and it still gets me.",
]
TOP = [
    "And that is exactly why this one is number one.",
    "Number one, and it's not even close.",
]


def _speakable(text, n=90):
    """Bỏ emoji / ký tự lạ (giọng đọc sẽ đọc sai); chỉ lấy câu trọn vẹn, dài quá n ký tự thì bỏ (không đọc câu cụt)."""
    cut = (text or "").rstrip().endswith("…")            # clean_comment đã cắt bớt: câu cuối không trọn
    text = re.sub(r"[^\w\s'’!?,.\-]", " ", (text or "").rstrip("…"))
    text = re.sub(r"\s+", " ", text).strip(" ,-")
    sentences = re.findall(r"[^.!?]+[.!?]*", text)
    if cut and len(sentences) > 1:
        sentences = sentences[:-1]
    elif cut:
        return ""
    out = ""
    for sent in sentences:
        if len(out) + len(sent) > n:
            break
        out += sent
    out = out.strip(" ,.")
    return out if len(re.sub(r"\W", "", out)) >= 6 else ""


def lines(items, seed=None):
    """items: [{id, num, comment, line}] các đoạn sẽ có lời bình -> {id: câu bình}. Không lặp mẫu câu."""
    rnd = random.Random(seed)
    pools = {k: rnd.sample(v, len(v)) for k, v in
             (("c", WITH_COMMENT), ("l", WITH_LINE), ("g", GENERIC), ("t", TOP))}
    out = {}
    for x in items:
        c, ln = _speakable(x.get("comment")), _speakable(x.get("line"), 50)
        if c and pools["c"]:
            text = pools["c"].pop().format(C=c)
        elif ln and ln[-1:] in "?!" and pools["l"]:
            text = pools["l"].pop().format(L=ln.rstrip("?!"))
        else:
            text = pools["g"].pop() if pools["g"] else rnd.choice(GENERIC)
        if x.get("num") == 1:
            text += " " + pools["t"][0]
        out[x["id"]] = text
    return out


def speak(text, out_mp3, voice=DEFAULT_VOICE):
    import edge_tts

    async def run():
        await edge_tts.Communicate(text, voice if voice in VOICES else DEFAULT_VOICE, rate="+8%").save(str(out_mp3))
    asyncio.run(run())
    return out_mp3
