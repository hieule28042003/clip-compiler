"""YouTube Data API v3 tối giản (không cần thư viện Google): đăng nhập OAuth, thông tin kênh, tải video + thumbnail lên.

Cần một "OAuth client" loại Desktop app trong Google Cloud (bật YouTube Data API v3). Hạn mức mặc định của một dự án
Google Cloud là 10.000 đơn vị/ngày, tải 1 video tốn 1.600 -> khoảng 6 video/ngày cho TẤT CẢ kênh dùng chung client đó.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
# force-ssl: đăng bình luận; yt-analytics.readonly: thời gian xem, % xem hết, sub có thêm từ mỗi video
SCOPES = ("https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly "
          "https://www.googleapis.com/auth/youtube.force-ssl https://www.googleapis.com/auth/yt-analytics.readonly")
COST = {"upload": 1600, "thumbnail": 50, "comment": 50, "stats": 1}
DAILY_QUOTA = 10_000
CHUNK = 8 * 1024 * 1024
COMEDY = "23"                    # categoryId "Comedy"


class ApiError(RuntimeError):
    pass


def _call(req, timeout=60):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, r.headers, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        if e.code == 308:                  # tải tiếp (resumable upload): chưa xong, không phải lỗi
            return 308, e.headers, {}
        raw = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(raw)
            err = err.get("error_description") or err.get("error", {}).get("message") or err.get("error") or raw
        except ValueError:
            err = raw
        raise ApiError(f"YouTube API {e.code}: {str(err)[:300]}") from None


def _form(url, data):
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode(), method="POST")
    return _call(req)[2]


def auth_url(client_id, redirect, state):
    return AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state})


def exchange(client_id, secret, code, redirect):
    """Mã từ trang đồng ý -> {access_token, refresh_token, expires_in}."""
    return _form(TOKEN, {"code": code, "client_id": client_id, "client_secret": secret,
                         "redirect_uri": redirect, "grant_type": "authorization_code"})


def refresh(client_id, secret, refresh_token):
    return _form(TOKEN, {"client_id": client_id, "client_secret": secret, "refresh_token": refresh_token,
                         "grant_type": "refresh_token"})


def _auth(token, extra=None):
    return {"Authorization": f"Bearer {token}", **(extra or {})}


def channel(token):
    req = urllib.request.Request(f"{API}/channels?part=snippet&mine=true", headers=_auth(token))
    items = _call(req)[2].get("items") or []
    if not items:
        raise ApiError("Tài khoản Google này chưa có kênh YouTube")
    sn = items[0]["snippet"]
    return {"id": items[0]["id"], "title": sn["title"],
            "thumb": ((sn.get("thumbnails") or {}).get("default") or {}).get("url", "")}


def clean_meta(title, description, tags, privacy="private", publish_at=None):
    """Giới hạn của YouTube: tiêu đề <= 100 ký tự, mô tả <= 5000 byte, không có '<' '>', tag tổng <= 500 ký tự."""
    strip = lambda s: (s or "").replace("<", "‹").replace(">", "›")
    title = strip(title).strip()[:100] or "Funny moments"
    desc = strip(description).encode("utf-8")[:4900].decode("utf-8", "ignore")
    out, size = [], 0
    for t in tags:
        t = strip(t).replace(",", " ").strip()
        if t and size + len(t) + 3 <= 480:
            out.append(t)
            size += len(t) + 3
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if publish_at:                         # hẹn giờ: YouTube yêu cầu để private, tới giờ tự công khai
        status.update(privacyStatus="private", publishAt=publish_at)
    return {"snippet": {"title": title, "description": desc, "tags": out, "categoryId": COMEDY}, "status": status}


def upload(token, path, meta, progress=None, stop=None):
    """Tải lên kiểu resumable theo từng khúc 8 MB. Trả về id video."""
    size = os.path.getsize(path)
    req = urllib.request.Request(
        f"{UPLOAD}/videos?uploadType=resumable&part=snippet,status", json.dumps(meta).encode(), method="POST",
        headers=_auth(token, {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4",
                              "X-Upload-Content-Length": str(size)}))
    location = _call(req)[1]["Location"]
    sent = 0
    with open(path, "rb") as fh:
        while True:
            if stop and stop():
                raise InterruptedError
            chunk = fh.read(CHUNK)
            end = sent + len(chunk) - 1
            req = urllib.request.Request(location, chunk, method="PUT", headers=_auth(token, {
                "Content-Length": str(len(chunk)), "Content-Range": f"bytes {sent}-{end}/{size}"}))
            status, headers, body = _call(req, timeout=300)
            if status in (200, 201):
                return body["id"]
            rng = headers.get("Range")          # 308: server đã nhận tới byte nào
            sent = int(rng.split("-")[1]) + 1 if rng else 0
            fh.seek(sent)
            if progress:
                progress(sent / size)


def set_thumbnail(token, video_id, jpg):
    """Kênh phải xác minh số điện thoại mới đặt được thumbnail riêng."""
    data = open(jpg, "rb").read()
    req = urllib.request.Request(f"{UPLOAD}/thumbnails/set?videoId={video_id}", data, method="POST",
                                 headers=_auth(token, {"Content-Type": "image/jpeg"}))
    _call(req)


def comment(token, video_id, text):
    """Bình luận cấp 1 dưới video (YouTube API không cho ghim bình luận: ghim tay trong YouTube Studio)."""
    body = {"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text[:9000]}}}}
    req = urllib.request.Request(f"{API}/commentThreads?part=snippet", json.dumps(body).encode(), method="POST",
                                 headers=_auth(token, {"Content-Type": "application/json; charset=UTF-8"}))
    return _call(req)[2].get("id", "")


def video_stats(token, ids):
    """{id: {views, likes, comments}}; mỗi 50 video tốn 1 đơn vị hạn mức."""
    out = {}
    for i in range(0, len(ids), 50):
        q = urllib.parse.urlencode({"part": "statistics", "id": ",".join(ids[i:i + 50])})
        req = urllib.request.Request(f"{API}/videos?{q}", headers=_auth(token))
        for it in _call(req)[2].get("items") or []:
            st = it.get("statistics") or {}
            out[it["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                             "comments": int(st.get("commentCount", 0))}
    return out


def analytics(token, ids, start, end):
    """YouTube Analytics API (bật riêng trong Google Cloud, không tính vào hạn mức 10.000):
    {id: {minutes, avg_pct, subs}}. Số liệu thường trễ 1-3 ngày."""
    out = {}
    for i in range(0, len(ids), 200):
        q = urllib.parse.urlencode({"ids": "channel==MINE", "startDate": start, "endDate": end, "dimensions": "video",
                                    "metrics": "estimatedMinutesWatched,averageViewPercentage,subscribersGained",
                                    "filters": "video==" + ",".join(ids[i:i + 200]), "maxResults": 200})
        res = _call(urllib.request.Request(f"{ANALYTICS}?{q}", headers=_auth(token)))[2]
        cols = [c["name"] for c in res.get("columnHeaders") or []]
        for row in res.get("rows") or []:
            r = dict(zip(cols, row))
            out[r["video"]] = {"minutes": round(r.get("estimatedMinutesWatched", 0)),
                               "avg_pct": round(r.get("averageViewPercentage", 0), 1),
                               "subs": int(r.get("subscribersGained", 0))}
    return out
