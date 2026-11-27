"""Đăng video lên Fanpage Facebook (Graph API) và gửi vào hộp nháp TikTok (Content Posting API, upload to inbox).

Facebook: app của chính bạn ở chế độ phát triển đăng được lên Trang bạn quản trị, không cần Meta duyệt.
Video dọc ≤ 90 giây đăng thành Reels, còn lại đăng thành video thường.
TikTok: app chưa duyệt không đăng công khai được; gửi vào hộp nháp thì bạn mở app TikTok, thêm caption rồi bấm Đăng.
Đăng nhập TikTok theo kiểu app Desktop (redirect http://localhost, bắt buộc PKCE).
"""
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

FB_VER = "v23.0"
GRAPH = f"https://graph.facebook.com/{FB_VER}"
GRAPH_VIDEO = f"https://graph-video.facebook.com/{FB_VER}"
FB_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts"
REEL_MAX = 90                       # Reels Facebook: video dọc tối đa 90 giây
TT_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TT_API = "https://open.tiktokapis.com/v2"
TT_SCOPES = "user.info.basic,video.upload"
TT_CHUNK = 10 * 1024 * 1024         # TikTok: mỗi phần 5–64MB, phần cuối được tới 128MB
TT_SINGLE = 64 * 1024 * 1024


class SocialError(Exception):
    pass


def _req(url, data=None, headers=None, method=None, timeout=120):
    """-> JSON. data: dict (gửi dạng form), bytes (gửi thô) hoặc None."""
    headers = dict(headers or {})
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            j = json.loads(body)
        except ValueError:
            raise SocialError(f"HTTP {e.code}: {body[:200].decode('utf-8', 'replace')}") from None
        raise SocialError(_msg(j) or f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise SocialError(f"Không kết nối được: {e.reason}") from None
    return json.loads(body) if body.strip() else {}


def _msg(j):
    err = j.get("error") if isinstance(j, dict) else None
    if isinstance(err, dict):
        if err.get("code") in (None, "ok", 0):
            return ""
        return err.get("error_user_msg") or err.get("message") or str(err.get("code"))
    return j.get("error_description") or (err if isinstance(err, str) else "")


def _json(url, payload, token):
    j = _req(url, json.dumps(payload).encode(), {"Authorization": f"Bearer {token}",
                                                  "Content-Type": "application/json; charset=UTF-8"})
    if _msg(j):
        raise SocialError(_msg(j))
    return j


# ---------- Facebook ----------
def fb_auth_url(app_id, redirect, state):
    return "https://www.facebook.com/" + FB_VER + "/dialog/oauth?" + urllib.parse.urlencode(
        {"client_id": app_id, "redirect_uri": redirect, "state": state, "scope": FB_SCOPES, "response_type": "code"})


def fb_exchange(app_id, secret, code, redirect):
    """Mã đăng nhập -> các Trang bạn quản trị [{id, name, token}]. Token Trang lấy từ token người dùng dài hạn
    nên không hết hạn (trừ khi bạn đổi mật khẩu / gỡ quyền app)."""
    short = _req(f"{GRAPH}/oauth/access_token?" + urllib.parse.urlencode(
        {"client_id": app_id, "client_secret": secret, "redirect_uri": redirect, "code": code}))["access_token"]
    long = _req(f"{GRAPH}/oauth/access_token?" + urllib.parse.urlencode(
        {"grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": secret,
         "fb_exchange_token": short}))["access_token"]
    pages = _req(f"{GRAPH}/me/accounts?" + urllib.parse.urlencode(
        {"fields": "id,name,access_token,tasks", "limit": 100, "access_token": long})).get("data", [])
    pages = [{"id": p["id"], "name": p["name"], "token": p["access_token"]} for p in pages
             if "CREATE_CONTENT" in (p.get("tasks") or ["CREATE_CONTENT"])]
    if not pages:
        raise SocialError("Tài khoản không quản trị Trang nào (hoặc chưa tích chọn Trang khi đồng ý quyền)")
    return pages


def _fb_reel(page_id, token, path, description, size):
    start = _req(f"{GRAPH}/{page_id}/video_reels", {"upload_phase": "start", "access_token": token})
    vid = start["video_id"]
    with open(path, "rb") as f:
        up = _req(start.get("upload_url") or f"https://rupload.facebook.com/video-upload/{FB_VER}/{vid}", f.read(),
                  {"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)}, timeout=1800)
    if not up.get("success", True):
        raise SocialError("Tải video lên Facebook không thành công")
    _req(f"{GRAPH}/{page_id}/video_reels", {"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                                            "description": description, "access_token": token})
    return vid, f"https://www.facebook.com/reel/{vid}"


def _multipart(fields, file_field, filename, chunk):
    b = uuid.uuid4().hex
    out = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                   for k, v in fields.items())
    out += (f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode() + chunk + f"\r\n--{b}--\r\n".encode()
    return out, f"multipart/form-data; boundary={b}"


def _fb_video(page_id, token, path, title, description, size):
    """Video thường: tải lên từng phần (resumable), không phải đọc cả file vào RAM."""
    s = _req(f"{GRAPH_VIDEO}/{page_id}/videos", {"upload_phase": "start", "file_size": size, "access_token": token})
    sid, a, b = s["upload_session_id"], int(s["start_offset"]), int(s["end_offset"])
    with open(path, "rb") as f:
        while a < b:
            f.seek(a)
            body, ctype = _multipart({"upload_phase": "transfer", "upload_session_id": sid, "start_offset": a,
                                      "access_token": token}, "video_file_chunk", "chunk.mp4", f.read(b - a))
            r = _req(f"{GRAPH_VIDEO}/{page_id}/videos", body, {"Content-Type": ctype}, timeout=900)
            a, b = int(r["start_offset"]), int(r["end_offset"])
    _req(f"{GRAPH_VIDEO}/{page_id}/videos", {"upload_phase": "finish", "upload_session_id": sid, "title": title[:250],
                                             "description": description, "access_token": token})
    return s["video_id"], f"https://www.facebook.com/{page_id}/videos/{s['video_id']}"


def fb_post(page_id, token, path, title, description, duration, vertical):
    """-> (id video, link)."""
    size = path.stat().st_size
    if vertical and 3 <= duration <= REEL_MAX:
        return _fb_reel(page_id, token, path, description, size)
    return _fb_video(page_id, token, path, title, description, size)


# ---------- TikTok ----------
def tt_pkce():
    """-> (code_verifier, code_challenge). TikTok Desktop: challenge là SHA256 dạng hex (không phải base64url)."""
    v = secrets.token_urlsafe(48)[:64]
    return v, hashlib.sha256(v.encode()).hexdigest()


def tt_auth_url(client_key, redirect, state, challenge):
    return TT_AUTH + "?" + urllib.parse.urlencode(
        {"client_key": client_key, "scope": TT_SCOPES, "response_type": "code", "redirect_uri": redirect,
         "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def _tt_token(form):
    j = _req(f"{TT_API}/oauth/token/", form, {"Cache-Control": "no-cache"})
    if not j.get("access_token"):
        raise SocialError(_msg(j) or "TikTok không trả token")
    now = time.time()
    return {"open_id": j.get("open_id", ""), "access": j["access_token"], "refresh": j.get("refresh_token", ""),
            "exp": now + int(j.get("expires_in", 86400)) - 120,
            "rexp": now + int(j.get("refresh_expires_in", 31536000)) - 3600, "scope": j.get("scope", "")}


def tt_exchange(client_key, secret, code, redirect, verifier):
    t = _tt_token({"client_key": client_key, "client_secret": secret, "code": code, "grant_type": "authorization_code",
                   "redirect_uri": redirect, "code_verifier": verifier})
    if "video.upload" not in t["scope"]:
        raise SocialError("Chưa cấp quyền video.upload: bật Content Posting API cho app rồi đăng nhập lại")
    return t


def tt_refresh(client_key, secret, refresh):
    return _tt_token({"client_key": client_key, "client_secret": secret, "grant_type": "refresh_token",
                      "refresh_token": refresh})


def tt_user(access):
    j = _req(f"{TT_API}/user/info/?fields=open_id,display_name", headers={"Authorization": f"Bearer {access}"})
    if _msg(j):
        raise SocialError(_msg(j))
    return (j.get("data") or {}).get("user", {}).get("display_name", "")


def tt_chunks(size):
    """-> (chunk_size, số phần) theo luật TikTok: file ≤ 64MB gửi một lần; lớn hơn chia phần 10MB,
    phần cuối gộp phần dư (≤ 128MB)."""
    if size <= TT_SINGLE:
        return size, 1
    return TT_CHUNK, size // TT_CHUNK


def tt_inbox(access, path):
    """Gửi video vào hộp nháp TikTok của bạn -> publish_id."""
    size = path.stat().st_size
    chunk, n = tt_chunks(size)
    j = _json(f"{TT_API}/post/publish/inbox/video/init/", {"source_info": {
        "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": n}}, access)
    pid, url = j["data"]["publish_id"], j["data"]["upload_url"]
    with open(path, "rb") as f:
        for i in range(n):
            a = i * chunk
            b = size - 1 if i == n - 1 else a + chunk - 1
            f.seek(a)
            _req(url, f.read(b - a + 1), {"Content-Type": "video/mp4", "Content-Range": f"bytes {a}-{b}/{size}"},
                 method="PUT", timeout=1800)
    return pid


def tt_status(access, publish_id):
    j = _json(f"{TT_API}/post/publish/status/fetch/", {"publish_id": publish_id}, access)
    d = j.get("data") or {}
    return d.get("status", ""), d.get("fail_reason", "")


def tt_wait(access, publish_id, tries=20, gap=6):
    """Chờ TikTok xử lý xong -> "SEND_TO_USER_INBOX" (đã vào hộp nháp)."""
    for _ in range(tries):
        st, why = tt_status(access, publish_id)
        if st == "FAILED":
            raise SocialError(f"TikTok báo lỗi: {why or 'không rõ lý do'}")
        if st in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return st
        time.sleep(gap)
    return "PROCESSING_UPLOAD"
