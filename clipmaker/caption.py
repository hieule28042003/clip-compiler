"""Caption + hashtag cho TikTok / Facebook, không cần AI.

Lấy từ nội dung thật của video (câu thoại / câu hát gây tò mò nhất, số tập, tên bài) ghép với mẫu câu mồi,
nên giật tít được mà không bịa chuyện. Hashtag chọn theo chủ đề nhận ra từ lời thoại / lời hát.
Mỗi seed ra một bản khác (nút "Viết lại").
"""
import random
import re
import unicodedata

VI_CHARS = "ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"

# ---------- phim lồng tiếng ----------
LANG_TAG = {"zh": "#phimtrung", "ko": "#phimhan", "ja": "#phimnhat", "th": "#phimthai", "ar": "#phimarap",
            "en": "#phimngoai", "es": "#phimngoai", "hi": "#phimando", "id": "#phimindo", "tr": "#phimthonhiky"}
# chủ đề -> (từ khoá trong lời thoại đã dịch, hashtag)
THEMES = [
    (r"\b(mẹ|bố|cha|con trai|con gái|gia đình|vợ|chồng|ông bà|anh trai|em gái|chị gái|mẹ chồng|con dâu)\b",
     ["#phimgiadinh", "#giadinh"]),
    (r"\b(yêu|người yêu|hẹn hò|cưới|kết hôn|chia tay|bạn trai|bạn gái|trái tim)\b", ["#phimtinhcam", "#ngontinh"]),
    (r"\b(trẻ mồ côi|mồ côi|nhận nuôi|đứa trẻ|bọn trẻ|em bé)\b", ["#camdong", "#phimcamdong"]),
    (r"\b(công ty|giám đốc|tổng tài|giàu có|nghèo|thừa kế|tỷ phú)\b", ["#tongtai", "#phimngan"]),
    (r"\b(ma|quỷ|hồn ma|nguyền rủa|sợ hãi|xác chết)\b", ["#phimkinhdi", "#kinhdi"]),
    (r"\b(cảnh sát|giết|kẻ trộm|bắt quả tang|nhà tù|điều tra|hung thủ|tội phạm)\b", ["#phimhinhsu", "#trinhtham"]),
    (r"\b(sư phụ|kiếm|võ công|môn phái|giang hồ|hoàng thượng|hoàng hậu|công chúa|vương gia)\b",
     ["#phimcotrang", "#cotrang"]),
    (r"\b(trường học|lớp học|cô giáo|thầy giáo|học sinh|bài kiểm tra)\b", ["#hocduong", "#phimhocduong"]),
    (r"\b(trả thù|phản bội|lừa dối|ngoại tình|tiểu tam)\b", ["#drama", "#ngoaitinh"]),
]
DUB_BASE = ["#longtieng", "#phimhay", "#phimngan"]
GENERIC_TITLES = {"phim ghép", "phim hay"}          # tiêu đề mặc định: không làm hashtag
DUB_REACH = ["#xuhuong", "#fyp", "#viral", "#tiktokgiaitri", "#phimhaymoingay"]
DUB_HEADS = [
    "{T} 🎬 {E}(Lồng tiếng Việt)",
    "🔥 {T} {E}– bản lồng tiếng Việt",
    "{T} {E}| Thuyết minh tiếng Việt 🎬",
    "📺 {T} {E}lồng tiếng Việt cực cuốn",
]
DUB_HOOKS = ["“{Q}” 😳", "Câu nói khiến cả phim đổi chiều: “{Q}” 👀", "“{Q}”… 😶", "Ai cũng lặng người khi nghe: “{Q}” 😢",
             "“{Q}” 💔"]
DUB_TEASE = ["Xem hết để biết chuyện gì xảy ra tiếp theo 👀", "Đừng bỏ lỡ đoạn cuối nhé 👀",
             "Xem tới cuối mới hiểu vì sao 😮", "Cốt truyện cuốn từ đầu tới cuối 🍿"]
DUB_ASK = ["Nếu là bạn, bạn sẽ làm gì? Bình luận nhé 👇", "Bạn đứng về phía ai? 👇", "Bạn chấm phim này mấy điểm? 👇",
           "Bạn đoán được cái kết không? 👇"]
DUB_MORE = ["Follow để xem thêm phim hay mỗi ngày 🔔", "Lưu lại để xem lại nhé 📌"]
DUB_FOLLOW = ["Follow để xem tập tiếp theo 🔔", "Theo dõi để không bỏ lỡ tập mới 🔔", "Lưu lại để xem tiếp nhé 📌"]

# ---------- TikTok lời bài hát ----------
MOODS = [
    (r"\b(buồn|nước mắt|khóc|đau|chia tay|cô đơn|lẻ loi|xa rồi|quên)\b", ["#nhacbuon", "#tamtrang"]),
    (r"\b(nhớ|thương|yêu|em ơi|anh ơi|bên nhau|trái tim)\b", ["#nhacyeu", "#nhacchill"]),
    (r"\b(sad|cry|tears|alone|goodbye|broken)\b", ["#sadsong", "#sadvibes"]),
    (r"\b(love|baby|heart|kiss|forever)\b", ["#lovesong", "#chill"]),
]
LYRIC_HEADS_VI = ["{S} – {A} 🎶", "🎧 {S} | {A}", "{S} – {A} 💫 nghe là nghiện"]
LYRIC_HEADS_EN = ["{S} – {A} 🎶", "🎧 {S} | {A}", "{S} – {A} ✨"]
LYRIC_HOOKS_VI = ["“{Q}”", "Câu hát chạm tim nhất: “{Q}” 💔", "“{Q}” 🥺", "Nghe tới câu này là nổi da gà: “{Q}”"]
LYRIC_HOOKS_EN = ["“{Q}”", "“{Q}” 🥺", "That line: “{Q}” 💔"]
LYRIC_ASK_VI = ["Bạn nghe bài này lần đầu khi nào? 👇", "Tag người bạn muốn gửi bài này 💌", "Bài này gợi bạn nhớ ai? 👇",
                "Lưu lại để nghe tiếp nhé 📌"]
LYRIC_ASK_EN = ["Tag someone who needs to hear this 💌", "Who does this song remind you of? 👇", "Save this for later 📌"]
LYRIC_BASE_VI = ["#lyrics", "#nhachay", "#lyricsvideo"]
LYRIC_BASE_EN = ["#lyrics", "#lyricsvideo", "#songlyrics"]
LYRIC_REACH = ["#xuhuong", "#fyp", "#tiktokmusic", "#viral"]



def hashtag(text):
    """'Sơn Tùng M-TP' -> '#sontungmtp' (bỏ dấu, ký tự lạ)."""
    t = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    t = re.sub(r"[^a-z0-9]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))
    return "#" + t if 2 < len(t) <= 30 else ""


def _clean(t):
    return re.sub(r"\s+", " ", (t or "")).strip(" ,.;:-–")


def _hook_score(t):
    """Câu gây tò mò: câu hỏi / cảm thán, độ dài vừa đọc, có từ cảm xúc."""
    n = len(t)
    if n < 12 or n > 90:
        return -1
    s = 3 if t.endswith("?") else 2 if t.endswith("!") or "..." in t else 0
    s += 2 * bool(re.search(r"\b(không thể|tại sao|sao|giá như|đừng|xin|chết|yêu|ghét|phản bội|bí mật|sự thật|"
                            r"cuối cùng|tha thứ|mãi mãi|why|never|love|sorry|please)\b", t.lower()))
    return s + (2 if 25 <= n <= 70 else 0)


def pick_hook(lines, rnd):
    scored = sorted(((_hook_score(_clean(t)), _clean(t)) for t in lines if t), reverse=True)
    good = [t for s, t in scored if s >= 0][:4]
    return rnd.choice(good) if good else ""


def themes(text, table):
    """Chủ đề nhận ra trong lời, chủ đề nhắc nhiều nhất trước -> [[hashtag...], ...]."""
    low = (text or "").lower()
    hits = sorted(((len(re.findall(pat, low)), i) for i, (pat, _) in enumerate(table)), reverse=True)
    return [table[i][1] for n, i in hits if n]


NET_QUOTA = {"tiktok": (2, 2, 2, 2), "facebook": (2, 1, 1, 1)}     # riêng video, chủ đề, chung, tăng tiếp cận


def _tags(core, topic, base, reach, net, rnd):
    """Chia suất: tag riêng video + chủ đề (mỗi chủ đề một tag) + tag chung + tag tăng tiếp cận, không trùng."""
    q_core, q_topic, q_base, q_reach = NET_QUOTA.get(net, NET_QUOTA["tiktok"])
    out = [t for t in core if t][:q_core] + [tags[0] for tags in topic[:q_topic]]
    out += base[:q_base] + rnd.sample(reach, len(reach))[:q_reach]
    return " ".join(dict.fromkeys(out))


def episodes(eps):
    eps = sorted({e for e in eps if e})
    if not eps:
        return ""
    if len(eps) == 1:
        return f"Tập {eps[0]} "
    return f"Tập {eps[0]}–{eps[-1]} " if eps[-1] - eps[0] + 1 == len(eps) else f"{len(eps)} tập "


def for_dub(title, lines, lang="", eps=(), net="tiktok", seed=None):
    """Caption cho phim lồng tiếng. lines: lời thoại đã dịch (theo thứ tự), eps: số tập của các clip đã ghép."""
    rnd = random.Random(seed)
    T = _clean(title) or "Phim hay"
    body = [re.sub(r"\s{2,}", " ", rnd.choice(DUB_HEADS).format(T=T, E=episodes(eps)))]
    q = pick_hook(lines, rnd)
    if q:
        body.append(rnd.choice(DUB_HOOKS).format(Q=q.rstrip(".")))
    body.append(rnd.choice(DUB_TEASE))
    body.append(rnd.choice(DUB_ASK))
    if len({e for e in eps if e}) > 1:
        body.append(rnd.choice(DUB_FOLLOW))
    elif rnd.random() < 0.5:
        body.append(rnd.choice(DUB_MORE))
    topic = themes(" ".join(lines), THEMES)
    core = [hashtag(T) if T.lower() not in GENERIC_TITLES else "", LANG_TAG.get(lang, "")]
    tags = _tags(core, topic, DUB_BASE, DUB_REACH, net, rnd)
    return ("\n".join(body) + "\n\n" + tags)[:2200]


def for_lyrics(artist, title, lines, net="tiktok", seed=None, extra=()):
    """Caption cho video lời bài hát. lines: các câu hát có trong video; extra: tag chung thêm (#vietsub...)."""
    rnd = random.Random(seed)
    text = " ".join(lines) + " " + title
    vi = any(ch in VI_CHARS for ch in text.lower())
    S, A = _clean(title), _clean(artist)
    head = rnd.choice(LYRIC_HEADS_VI if vi else LYRIC_HEADS_EN).format(S=S, A=A) if A else f"{S} 🎶"
    q = pick_hook(lines, rnd) or max(lines, key=len, default="")
    body = [head]
    if q:
        body.append(rnd.choice(LYRIC_HOOKS_VI if vi else LYRIC_HOOKS_EN).format(Q=_clean(q)))
    body.append(rnd.choice(LYRIC_ASK_VI if vi else LYRIC_ASK_EN))
    topic = themes(text, MOODS)
    core = [hashtag(re.sub(r"\([^)]*\)|\[[^\]]*\]", "", S)), hashtag(A)]
    base = list(extra) + (LYRIC_BASE_VI if vi or extra else LYRIC_BASE_EN)
    tags = _tags(core, topic, base, LYRIC_REACH, net, rnd)
    return ("\n".join(body) + "\n\n" + tags)[:2200]
