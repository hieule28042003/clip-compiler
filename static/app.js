"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let V = null, pollTimer = null, playingId = null, recorder = null, recId = null;

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* không phải JSON */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}
const json = (method, o) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(o) });

function fmt(t) {
  t = Math.max(0, Math.round(t || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
}

async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
  const o = btn.textContent; btn.textContent = "Đã copy ✓"; setTimeout(() => { btn.textContent = o; }, 1500);
}

// ---------- khung trang + sidebar ----------
const PAGES = ["home", "shortsPage", "musicPage", "tiktokPage", "mixEditor", "projectsPage", "trends", "channels", "settings", "editor"];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const views = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : `${n || 0}`;
let SEED = null;              // ý tưởng mang từ trang Xu hướng sang: {keyword, videos, ref}

function showPage(id, nav, crumb = "") {
  PAGES.forEach((p) => $("#" + p).classList.toggle("hidden", p !== id));
  $$(".menu a").forEach((a) => a.classList.toggle("on", a.dataset.nav === nav));
  $("#crumb").textContent = crumb;
}

async function loadProjects() {
  const list = await api("/api/projects");
  const box = $("#projects");
  box.innerHTML = list.length ? "" : `<p class="hint">Chưa có dự án nào.</p>`;
  for (const p of list) {
    const a = document.createElement("a");
    a.className = "proj"; a.href = `#/p/${p.id}`;
    a.innerHTML = `<span><b>${esc(p.title)}</b><br><span class="hint">“${esc(p.keyword)}” · ${p.clips} đoạn · `
      + `${new Date(p.created * 1000).toLocaleDateString("vi-VN")}</span></span><span class="hint">${p.kind === "shorts" ? "✂ Shorts " : ""}${p.done ? "✓ đã dựng" : ""}</span>`;
    box.appendChild(a);
  }
  const cur = V?.project?.id;
  $("#recent").innerHTML = list.slice(0, 8).map((p) =>
    `<a href="#/p/${p.id}" class="${p.id === cur ? "on" : ""}" title="${esc(p.title)}">${p.done ? "✓ " : ""}${p.kind === "shorts" ? "✂ " : ""}${esc(p.title)}</a>`).join("")
    || `<p class="hint">Chưa có</p>`;
}

// ---------- tạo video ----------
async function showHome() {
  V = null;
  showPage("home", "home");
  renderSeed();
  loadProjects();
}

async function showProjects() {
  V = null;
  showPage("projectsPage", "projects", "Dự án");
  loadProjects();
}

function renderSeed() {
  const box = $("#seedNote");
  box.classList.toggle("hidden", !SEED);
  if (!SEED) return;
  box.innerHTML = `<div>💡 Ý tưởng từ xu hướng: <b>${esc(SEED.ref)}</b> — app sẽ quét trước ${SEED.videos.length} video đang hot:</div>`
    + `<div class="seedThumbs">${SEED.videos.map((v) => `<img src="https://i.ytimg.com/vi/${v.id}/mqdefault.jpg" title="${esc(v.title)}" alt="" onerror="this.remove()">`).join("")}</div>`
    + `<button class="mini" id="seedClear">Bỏ ý tưởng này</button>`;
  $("#seedClear").onclick = () => { SEED = null; renderSeed(); };
}

// tạo dự án theo thiết lập trên form; auto = làm hết không cần duyệt
async function createProject(auto, keyword = $("#kw").value, seed = SEED) {
  const fromStream = !seed && $("#src").value === "stream";
  const streams = fromStream ? $("#streams").value.split(/\s+/).filter(Boolean) : [];
  if (fromStream && !streams.length) throw new Error("Dán ít nhất một link stream");
  const res = await api("/api/projects", json("POST", {
    keyword: fromStream ? "stream" : keyword, streams, videos: +$("#nVideos").value, length: +$("#clipLen").value,
    minutes: +$("#minutes").value, comments: $("#useComments").checked, auto,
    voiceover: !fromStream && $("#voiceover").checked, voice: $("#voice").value, shorts: $("#shorts").checked,
    seed_videos: seed ? seed.videos.map((v) => v.id) : [], ref: seed ? seed.ref : "" }));
  SEED = null;
  location.hash = `#/p/${res.project.id}`;
}
$("#createBtn").onclick = () => { $("#createErr").textContent = ""; createProject(false).catch((e) => { $("#createErr").textContent = e.message; }); };
$("#autoCreateBtn").onclick = () => { $("#createErr").textContent = ""; createProject(true).catch((e) => { $("#createErr").textContent = e.message; }); };
$("#kw").onkeydown = (e) => { if (e.key === "Enter") $("#createBtn").click(); };
$("#src").onchange = () => {
  const isStream = $("#src").value === "stream";
  for (const id of ["#srcStreamHint", "#streamsBox", "#streamWarn"]) $(id).classList.toggle("hidden", !isStream);
  for (const id of ["#kwBox", "#nVideosBox", "#useCommentsBox", "#voiceoverBox", "#kwWarn"]) $(id).classList.toggle("hidden", isStream);
  document.querySelectorAll("#home .streamOpt").forEach((o) => { o.hidden = !isStream; });
  // stream: đoạn dài hơn (cần phần dẫn + phản ứng), không dùng đoạn 6–15 giây
  document.querySelectorAll("#clipLen option").forEach((o) => { if (+o.value < 30) o.hidden = isStream; });
  if (isStream && +$("#clipLen").value < 30) $("#clipLen").value = "45";
  if (!isStream && +$("#clipLen").value > 15) $("#clipLen").value = "10";
  if (!isStream && +$("#minutes").value > 20) $("#minutes").value = "15";
};

// ---------- chỉ làm Shorts ----------
async function showShorts() {
  V = null;
  showPage("shortsPage", "shorts", "Làm Shorts");
  loadProjects();
}

async function createShorts(auto, keyword = $("#sKw").value, seed = null) {
  $("#sErr").textContent = "";
  const fromStream = !seed && $("#sStyle").value === "stream";
  try {
    const res = await api("/api/projects", json("POST", {
      keyword: fromStream ? "stream" : keyword, stream: fromStream ? $("#sStream").value : "",
      kind: "shorts", count: +$("#sCount").value, length: +$("#sLen").value,
      videos: +$("#sVideos").value, minutes: 5, auto, voiceover: false, shorts: true,
      reply: !seed && $("#sStyle").value === "reply", voice: $("#voice").value,
      seed_videos: seed ? seed.videos.map((v) => v.id) : [], ref: seed ? seed.ref : "" }));
    location.hash = `#/p/${res.project.id}`;
  } catch (e) { if (seed) throw e; $("#sErr").textContent = e.message; }
}
$("#sStyle").onchange = () => {
  const st = $("#sStyle").value, isStream = st === "stream";
  $("#sReplyHint").classList.toggle("hidden", st !== "reply");
  for (const id of ["#sStreamHint", "#sStreamBox", "#sStreamWarn"]) $(id).classList.toggle("hidden", !isStream);
  for (const id of ["#sKwBox", "#sVideosBox", "#sWarn"]) $(id).classList.toggle("hidden", isStream);
  document.querySelectorAll("#shortsPage .streamOpt").forEach((o) => { o.hidden = !isStream; });
  // stream: Short dài hơn (cần phần dẫn + phản ứng), không có 10–20 giây
  document.querySelectorAll("#sLen option").forEach((o) => { if (+o.value < 30) o.hidden = isStream; });
  if (isStream && +$("#sLen").value < 30) $("#sLen").value = "45";
  if (!isStream && +$("#sLen").value > 30) $("#sLen").value = "20";
  if (!isStream && +$("#sCount").value > 10) $("#sCount").value = "5";
};
$("#sAutoBtn").onclick = () => createShorts(true);
$("#sCreateBtn").onclick = () => createShorts(false);
$("#sKw").onkeydown = (e) => { if (e.key === "Enter") $("#sAutoBtn").click(); };
$("#sStream").onkeydown = (e) => { if (e.key === "Enter") $("#sAutoBtn").click(); };

// ---------- xu hướng ----------
const NICHES = ["funny moments", "celebrity funny moments", "talk show funny moments", "interview funny moments",
  "sports funny moments", "kids funny moments", "animals funny", "fails compilation"];
$("#seedChips").innerHTML = NICHES.map((n) => `<button class="chip pick">${n}</button>`).join("");
$$("#seedChips .pick").forEach((b) => b.onclick = () => { $("#seed").value = b.textContent; });
let trendTimer = null;

async function showTrends() {
  V = null;
  showPage("trends", "trends", "Xu hướng");
  loadProjects();
  const r = await api("/api/trends");
  renderTrends(r.result);
  if (r.job && !r.job.done) pollTrends();
}

$("#trendBtn").onclick = async () => {
  $("#trendNote").className = "note"; $("#trendNote").textContent = "";
  try {
    await api("/api/trends", json("POST", { seed: $("#seed").value, depth: +$("#depth").value, period: $("#period").value }));
    pollTrends();
  } catch (e) { $("#trendNote").className = "note bad"; $("#trendNote").textContent = e.message; }
};
$("#seed").onkeydown = (e) => { if (e.key === "Enter") $("#trendBtn").click(); };

function pollTrends() {
  clearTimeout(trendTimer);
  const tick = async () => {
    if (location.hash !== "#/trends") return;
    const r = await api("/api/trends").catch(() => null);
    if (!r || !r.job) return;
    const busy = !r.job.done, pct = Math.round(r.job.progress * 100);
    $("#trendBtn").disabled = busy;
    $("#trendProg").classList.toggle("hidden", !busy);
    $("#trendProg div").style.width = `${pct}%`;
    const note = $("#trendNote");
    if (busy) {
      note.className = "note"; note.textContent = `${r.job.stage || "Đang chuẩn bị"}… ${pct}%`;
      trendTimer = setTimeout(tick, 1000);
      return;
    }
    if (r.job.error) { note.className = "note bad"; note.textContent = "Lỗi: " + r.job.error; return; }
    note.className = "note ok";
    note.textContent = `Xong! ${r.job.result.topics} chủ đề hot, ${r.job.result.keywords} từ khoá.`;
    renderTrends(r.result);
  };
  tick();
}

const idea = (keyword, videos, ref) => ({ keyword, ref, videos: videos.slice(0, 8).map((v) => ({ id: v.id, title: v.title })) });

function useIdea(keyword, videos, ref) {
  SEED = idea(keyword, videos, ref);
  $("#kw").value = keyword;
  location.hash = "#/";
}

async function autoIdea(keyword, videos, ref, btn) {
  if (!confirm(`Làm tự động video về “${ref}”?\n\nDùng thiết lập ở trang “Tạo video ghép” (độ dài ${$("#minutes").value} phút`
    + `${$("#voiceover").checked ? ", lời bình giọng đọc" : ""}${$("#shorts").checked ? ", 3 Shorts" : ""}).`)) return;
  btn.disabled = true;
  try { await createProject(true, keyword, idea(keyword, videos, ref)); }
  catch (e) { alert(e.message); btn.disabled = false; }
}

function renderTrends(res) {
  $("#trendOut").classList.toggle("hidden", !res);
  if (!res) return;
  $("#seed").value = res.seed;
  const span = res.period === "week" ? "7 ngày qua" : "30 ngày qua";
  $("#period").value = res.period || "month";
  $("#trendAt").textContent = `· “${res.seed}” · ${span} · ${new Date(res.at * 1000).toLocaleString("vi-VN")}`;
  renderAnalysis(res.analysis, span);
  const tb = $("#topics"); tb.innerHTML = "";
  if (!res.topics.length) tb.innerHTML = `<p class="hint">Chưa tìm ra chủ đề nổi bật — thử lĩnh vực cụ thể hơn hoặc độ sâu “Kỹ”.</p>`;
  res.topics.forEach((t, i) => {
    const d = document.createElement("div");
    d.className = "topic card";
    d.innerHTML = `<div class="tThumbs">${t.videos.slice(0, 3).map((v) =>
        `<a href="https://youtu.be/${v.id}" target="_blank" rel="noopener" title="${esc(v.title)}"><img src="https://i.ytimg.com/vi/${v.id}/mqdefault.jpg" alt=""></a>`).join("")}</div>
      <div class="tHead"><span class="rank">#${i + 1}</span><b>${esc(t.name)}</b></div>
      <div class="hint">${views(t.views)} view ${res.period === "week" ? "tuần này" : "tháng này"} · ${t.count} video hot ·
        ${t.source === "search" ? "🔎 người xem đang tìm" : "⭐ có mặt ở nhiều video hot"}</div>
      <ul>${t.videos.slice(0, 2).map((v) => `<li>${esc(v.title)} <span class="hint">· ${views(v.views)}</span></li>`).join("")}</ul>
      <div class="row"><button class="primary auto" title="Quét, chọn, lời bình, dựng + Shorts — không cần duyệt">⚡ Làm tự động</button>
        <button class="manual" title="Mở form tạo video để tự chỉnh và duyệt">🎬 Tự làm</button></div>`;
    d.querySelector(".manual").onclick = () => useIdea(t.keyword, t.videos, t.name);
    d.querySelector(".auto").onclick = (e) => autoIdea(t.keyword, t.videos, t.name, e.target);
    tb.appendChild(d);
  });
  const rows = res.keywords.map((m, i) => `<tr>
      <td><b>${esc(m.keyword)}</b></td>
      <td><div class="heat"><div style="width:${m.heat}%"></div></div><span class="hint">${m.heat}</span></td>
      <td>${views(m.top5)}</td><td>${m.million}</td>
      <td>${m.compilations}/${Math.min(10, m.count)}${m.compilations >= 5 ? ' <span class="good">hợp</span>' : ""}</td>
      <td><button class="mini" data-k="${i}">Tạo video</button></td></tr>`).join("");
  $("#kwTable").innerHTML = `<thead><tr><th>Từ khoá</th><th>Độ hot</th><th title="Tổng view 5 video top tháng này">View top 5</th>
    <th title="Số video ≥ 1 triệu view trong top 10">≥1M</th><th title="Bao nhiêu video top là dạng tổng hợp (giống video app làm)">Dạng tổng hợp</th><th></th></tr></thead><tbody>${rows}</tbody>`;
  $$("#kwTable [data-k]").forEach((b) => b.onclick = () => {
    const m = res.keywords[+b.dataset.k];
    useIdea(m.keyword, m.videos, m.keyword);
  });
  $("#patterns").innerHTML = res.patterns.map((p) => `<span class="chip">${esc(p.text)} <b>×${p.hits}</b></span>`).join("")
    || `<p class="hint">Không có kiểu nổi bật.</p>`;
}

// phân tích: cái gì đang ăn khách + video đang lên để làm tương tự
const bars = (rows, label, val, txt) => {
  const max = Math.max(1, ...rows.map(val));
  return rows.map((r, i) => `<div class="bar${i === 0 && rows.length > 1 ? " lead" : ""}"><span>${label(r)}</span><span class="hint">${txt(r)}</span>
    <div class="track"><div style="width:${Math.round(100 * val(r) / max)}%"></div></div></div>`).join("");
};
const dur = (s) => s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
  : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function renderAnalysis(a, span) {
  $("#anaBox").classList.toggle("hidden", !a);
  if (!a) return;
  $("#anaAt").textContent = `· ${a.count} video hot ${span}`;
  $("#anaTips").innerHTML = a.tips.map((t) => `<li>${esc(t)}</li>`).join("") || `<li class="hint">Chưa đủ dữ liệu để kết luận.</li>`;
  const f = [{ k: "🎬 Video dài", ...a.format.long }, { k: "✂ Shorts", ...a.format.shorts }].filter((x) => x.count)
    .sort((x, y) => y.median - x.median);
  $("#anaFormat").innerHTML = bars(f, (r) => r.k, (r) => r.median,
    (r) => `${r.count} video · trung vị ${views(r.median)}`) + `<p class="hint">Thanh = view trung vị mỗi video</p>`;
  const lens = [...a.lengths].sort((x, y) => y.median - x.median);
  $("#anaLen").innerHTML = lens.length ? bars(lens, (r) => r.label, (r) => r.median, (r) => `${r.count} video · ${views(r.median)}`)
    : `<p class="hint">Không có video dài.</p>`;
  $("#anaFeat").innerHTML = (a.features.length ? bars(a.features, (r) => r.label, (r) => r.lift,
    (r) => `×${r.lift} · ${r.share}% video top`) : `<p class="hint">Chưa đủ video để so sánh.</p>`)
    + (a.patterns.length ? `<div class="row mt">${a.patterns.map((p) => `<span class="chip">${esc(p.text)} <b>×${p.hits}</b></span>`).join("")}</div>` : "");
  $("#anaDays").innerHTML = (a.days.length ? bars(a.days, (r) => r.label, (r) => r.median, (r) => `${r.count} video · ${views(r.median)}`)
    : `<p class="hint">Chưa đủ video có ngày đăng.</p>`)
    + (a.hours.length ? bars(a.hours, (r) => r.label, (r) => r.median, (r) => `${r.count} video · ${views(r.median)}`) : "")
    + `<p class="hint">Chỉ tính các video hot nhất đã xem chi tiết; giờ theo máy bạn.</p>`;
  $("#anaCh").innerHTML = bars(a.channels, (r) => esc(r.name), (r) => r.views, (r) => `${r.count} video · ${views(r.views)}`);
  const box = $("#rising"); box.innerHTML = "";
  if (!a.rising.length) box.innerHTML = `<p class="hint">Không lấy được chi tiết video (YouTube chặn tạm thời?). Thử lại sau.</p>`;
  a.rising.forEach((r, i) => {
    const d = document.createElement("div");
    d.className = "topic card rise";
    const kw = r.keyword || $("#seed").value;
    d.innerHTML = `<a href="https://youtu.be/${r.id}" target="_blank" rel="noopener"><img src="https://i.ytimg.com/vi/${r.id}/mqdefault.jpg" alt=""></a>
      <div class="tHead"><span class="rank">#${i + 1}</span> <span class="badge">${r.short ? "✂ Shorts" : "🎬 " + dur(r.duration)}</span></div>
      <div class="ttl">${esc(r.title)}</div>
      <div class="hint">${esc(r.channel)}${r.subs ? ` · ${views(r.subs)} sub` : ""} · ${views(r.views)} view${r.age ? ` · ${r.age < 1 ? "<1" : Math.round(r.age)} ngày` : ""}</div>
      <ul class="why">${r.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
      <div class="hint">Làm tương tự với từ khoá: <b>${esc(kw)}</b>${r.short ? " · dạng Shorts" : ""}</div>
      <div class="row"><button class="primary auto" title="Quét, chọn, dựng không cần duyệt">⚡ Làm tương tự</button>
        <button class="manual" title="Mở dự án để tự duyệt">🎬 Tự làm</button></div>`;
    const seed = { keyword: kw, ref: r.topic || r.title.slice(0, 80),
      videos: [{ id: r.id, title: r.title }, ...r.similar.map((id) => ({ id, title: "" }))] };
    d.querySelector(".auto").onclick = async (e) => {
      if (!confirm(`Làm tự động ${r.short ? "Shorts" : "video"} tương tự “${r.title}”?\n\nTừ khoá: ${kw}\n`
        + (r.short ? `Dùng thiết lập ở trang ✂ Làm Shorts (${$("#sCount").value} Shorts).` : `Dùng thiết lập ở trang Tạo video ghép (${$("#minutes").value} phút).`))) return;
      e.target.disabled = true;
      try { r.short ? await createShorts(true, kw, seed) : await createProject(true, kw, seed); }
      catch (err) { alert(err.message); e.target.disabled = false; }
    };
    d.querySelector(".manual").onclick = async (e) => {
      if (!r.short) return useIdea(kw, seed.videos, seed.ref);
      e.target.disabled = true;
      try { await createShorts(false, kw, seed); } catch (err) { alert(err.message); e.target.disabled = false; }
    };
    box.appendChild(d);
  });
}

// ---------- kênh YouTube ----------
let CH = null, chTimer = null;
const PRIV = { private: "🔒 Riêng tư", unlisted: "🔗 Không công khai", public: "🌍 Công khai" };
const ST = { queued: "Đang chờ", running: "Đang chạy", done: "Đã đăng", error: "Lỗi", cancelled: "Đã huỷ" };
const PHASE = { topic: "Chọn chủ đề", build: "Làm video", upload: "Tải lên" };

async function showChannels() {
  V = null;
  showPage("channels", "channels", "Kênh YouTube");
  loadProjects();
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const note = $("#chNote");
  if (q.get("ok")) { note.className = "note ok"; note.textContent = `Đã kết nối kênh “${q.get("ok")}”. Chỉnh chủ đề / loại video bên dưới rồi bấm ▶ Chạy.`; }
  else if (q.get("err")) { note.className = "note bad"; note.textContent = "Kết nối không được: " + q.get("err"); }
  else note.textContent = "";
  if (q.toString()) history.replaceState(null, "", "#/channels");
  renderChannels(await api("/api/channels"), true);
  pollChannels();
}

function pollChannels() {
  clearTimeout(chTimer);
  chTimer = setTimeout(async () => {
    if (!location.hash.startsWith("#/channels")) return;
    const r = await api("/api/channels").catch(() => null);
    if (r) renderChannels(r, false);
    pollChannels();
  }, 2000);
}

function renderChannels(r, full) {
  CH = r;
  $("#clientState").textContent = r.client.id && r.client.has_secret ? "✓ đã lưu" : "chưa nhập";
  if (full) {
    $("#clientId").value = r.client.id;
    $("#clientSecret").placeholder = r.client.has_secret ? "•••••• (để trống = giữ nguyên)" : "GOCSPX-…";
    $("#workers").value = String(r.workers);
    $("#quotaLimit").value = r.quota.limit;
    $("#guide").open = !(r.client.id && r.client.has_secret);
  }
  const q = r.quota, pct = Math.min(100, Math.round(q.used * 100 / q.limit));
  $("#quotaBar").style.width = `${pct}%`;
  $("#quotaText").textContent = `Hạn mức YouTube API hôm nay: ${q.used.toLocaleString("vi-VN")} / ${q.limit.toLocaleString("vi-VN")}`
    + ` · còn đăng được khoảng ${Math.floor((q.limit - q.used) / 1650)} video`;
  // danh sách kênh chỉ vẽ lại khi số kênh đổi / lần đầu (không làm mất chữ đang gõ)
  const sig = r.accounts.map((a) => a.id).join();
  if (full || $("#accList").dataset.sig !== sig) { $("#accList").dataset.sig = sig; renderAccounts(r); }
  renderTasks(r.tasks);
}

const DAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];
const fmtAt = (ts) => new Date(ts * 1000).toLocaleString("vi-VN", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

function scheduleBox(a) {
  const up = (a.upcoming || []).map((b) => `<li><b>${fmtAt(b.at)}</b> ${b.kind === "long" ? "🎬" : "✂"} `
    + (b.url ? `<a href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.title || "video")}</a>` : `<span class="hint">đã giữ chỗ (đang làm)</span>`) + `</li>`).join("");
  return `<div class="sched">
    <div class="row"><b>📅 Lịch đăng</b><select data-f="schedule" style="width:auto">
      <option value="now">Đăng ngay khi làm xong (theo “Chế độ đăng”)</option>
      <option value="slots">⏰ Hẹn giờ lên sóng theo khung giờ</option></select></div>
    ${a.schedule === "slots" ? `<div class="accGrid">
      <div><span class="hint">Giờ lên sóng video dài</span><input data-f="slots_long" placeholder="19:00"></div>
      <div><span class="hint">Giờ lên sóng Shorts</span><input data-f="slots_short" placeholder="12:00, 17:00, 21:00"></div>
    </div>
    <div class="row days">${DAYS.map((n, i) => `<label class="check"><input type="checkbox" data-day="${i}" ${(a.days || []).includes(i) ? "checked" : ""}> ${n}</label>`).join("")}</div>
    <p class="hint">Video được dựng + tải lên trước ở chế độ Riêng tư, tới giờ YouTube tự chuyển sang <b>Công khai</b>
      (bỏ qua “Chế độ đăng”). Mỗi video lấy mốc trống kế tiếp, không trùng nhau; Shorts lên sóng sau video dài.
      Giờ theo đồng hồ máy này (${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}).</p>` : ""}
    ${up ? `<p class="hint">Sắp lên sóng:</p><ul class="upcoming">${up}</ul>` : ""}
  </div>`;
}

const STATS_OPEN = {};
const num = (x) => x == null ? "–" : x.toLocaleString("vi-VN");
const C_STATE = { pending: "⏳ chờ lên sóng", done: "✓ đã bình luận", off: "" };

function growBox(a) {
  return `<div class="sched">
    <div class="row"><b>📈 Tương tác</b>
      <label class="check"><input type="checkbox" data-f="comment"> 💬 Tự bình luận câu hỏi dưới video khi lên sóng</label></div>
    <div class="row"><span class="hint">Thumbnail đăng lên</span><select data-f="thumb_style" style="width:auto">
      <option value="rotate">🔁 Xoay vòng 3 mẫu (để so mẫu nào nhiều view)</option>
      <option value="1">Sticker</option><option value="2">Reaction</option><option value="3">Split</option></select>
      <button class="mini" data-act="stats">📊 Thống kê (${a.uploads || 0} video)</button></div>
    <p class="hint">Bình luận do chính kênh đăng (câu hỏi kiểu “số mấy làm bạn cười nhất? 👇”; Shorts thì dẫn link video
      dài). YouTube API không cho ghim: muốn ghim thì vào YouTube Studio → Bình luận → ⋮ → Ghim.</p>
    <div class="statsBox" ${STATS_OPEN[a.id] ? "" : "hidden"}></div>
  </div>`;
}

function renderStats(box, st) {
  const ins = st.insights.map((g) => `<div class="ins"><b>${g.kind} · theo ${esc(g.by.toLowerCase())}</b>
    ${g.sure ? "" : `<span class="hint"> (ít video, mới là gợi ý)</span>`}
    <div class="tblWrap"><table><tr><th></th><th>Video</th><th>View TB</th><th>% xem hết</th><th>Sub</th></tr>
    ${g.rows.map((r, i) => `<tr${i === 0 ? ' class="best"' : ""}><td>${i === 0 ? "🏆 " : ""}${esc(r.key)}</td><td>${r.n}</td>
      <td>${num(r.views)}</td><td>${r.avg_pct == null ? "–" : r.avg_pct + "%"}</td><td>${num(r.subs)}</td></tr>`).join("")}</table></div></div>`).join("");
  const vids = st.uploads.slice(0, 40).map((u) => {
    const x = u.stats || {}, url = u.kind === "long" ? `https://youtu.be/${u.id}` : `https://youtube.com/shorts/${u.id}`;
    const cs = u.c_state?.startsWith("error") ? `<span class="bad" title="${esc(u.c_state)}">✗ lỗi bình luận</span>` : C_STATE[u.c_state] || "";
    return `<tr><td>${u.kind === "long" ? "🎬" : "✂"} <a href="${url}" target="_blank" rel="noopener">${esc(u.title)}</a>
      <div class="hint">${fmtAt(u.at)}${u.thumb ? ` · ${esc(st.styles[u.thumb])}` : ""} ${cs}</div></td>
      <td>${num(x.views)}</td><td>${num(x.likes)}</td><td>${num(x.comments)}</td><td>${x.avg_pct == null ? "–" : x.avg_pct + "%"}</td><td>${num(x.subs)}</td></tr>`;
  }).join("");
  box.innerHTML = `<div class="row"><button class="mini primary" data-act="statsRefresh">↻ Lấy số liệu mới</button>
      <span class="hint">${st.updated ? `Cập nhật ${fmtAt(st.updated)}` : "Chưa lấy số liệu"} · số liệu YouTube trễ vài giờ tới 2–3 ngày</span></div>
    ${st.note ? `<p class="hint">⚠ ${esc(st.note)}</p>` : ""}
    ${ins || (st.uploads.length ? `<p class="hint">Cần thêm video (khác mẫu thumbnail / giờ / chủ đề) để so sánh.</p>` : "")}
    ${vids ? `<div class="tblWrap"><table class="vids"><tr><th>Video</th><th>View</th><th>Like</th><th>BL</th><th>% xem</th><th>Sub</th></tr>${vids}</table></div>`
      : `<p class="hint">Chưa có video nào do app đăng.</p>`}`;
}

async function loadStats(a, box, refresh) {
  if (!refresh) box.innerHTML = `<p class="hint">Đang tải…</p>`;
  try { renderStats(box, await api(`/api/channels/${a.id}/stats`, refresh ? { method: "POST" } : undefined)); }
  catch (e) { if (refresh) alert(e.message); else box.innerHTML = `<p class="bad">${esc(e.message)}</p>`; return; }
  const b = box.querySelector('[data-act="statsRefresh"]');
  b.onclick = async () => { b.disabled = true; b.textContent = "Đang lấy…"; await loadStats(a, box, true); };
}

function renderAccounts(r) {
  const box = $("#accList");
  box.innerHTML = r.accounts.length ? "" : `<p class="hint">Chưa có kênh nào. Nhập Client ID ở bước 1 rồi bấm “+ Kết nối kênh”.</p>`;
  const voices = Object.entries(r.voices).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  for (const a of r.accounts) {
    const d = document.createElement("div");
    d.className = "acc" + (a.enabled ? "" : " off");
    d.innerHTML = `<div class="accHead">${a.thumb ? `<img src="${esc(a.thumb)}" alt="">` : ""}<b>${esc(a.title)}</b>
        <label class="check"><input type="checkbox" data-f="enabled"> Bật</label>
        <div class="row"><select data-run style="width:auto"><option value="1">1 video</option><option value="2">2 video</option><option value="3">3 video</option></select>
          <span class="hint">lên sóng lúc</span><input type="datetime-local" data-at title="Hẹn giờ lên sóng cụ thể cho video này (để trống = theo lịch đăng)" style="width:auto">
          <button class="primary mini" data-act="run">▶ Chạy ngay</button>
          <button class="mini" data-act="reconnect" title="Đăng nhập lại Google cho kênh này">Kết nối lại</button>
          <button class="mini danger" data-act="del">Xoá</button></div></div>
      <div><span class="hint">Chủ đề / lĩnh vực (mỗi dòng một cái)</span><textarea data-f="seeds" rows="2"></textarea></div>
      <div class="accGrid">
        <div><span class="hint">Cách chọn chủ đề</span><select data-f="mode">
          <option value="hot">🔥 Lấy chủ đề đang hot trong lĩnh vực</option><option value="keywords">🔁 Dùng đúng từ khoá, xoay vòng</option></select></div>
        <div><span class="hint">Video dài</span><select data-f="minutes"><option value="5">5 phút</option><option value="10">10 phút</option><option value="15">15 phút</option><option value="20">20 phút</option></select></div>
        <div><span class="hint">Chế độ đăng</span><select data-f="privacy">${Object.entries(PRIV).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
        <div><span class="hint">Giọng đọc</span><select data-f="voice">${voices}</select></div>
        <div><span class="hint">Mỗi lần chạy</span><select data-f="per_run"><option value="1">1 video</option><option value="2">2 video</option><option value="3">3 video</option></select></div>
        <div><span class="hint">Tự chạy mỗi ngày lúc</span><input type="time" data-f="daily"></div>
      </div>
      <div class="row">
        <label class="check"><input type="checkbox" data-f="long"> 🎬 Đăng video dài</label>
        <label class="check"><input type="checkbox" data-f="shorts"> ✂ Đăng
          <select data-f="shorts_n" class="inline" style="width:auto">${[1, 2, 3, 5, 8, 10].map((n) => `<option value="${n}">${n}</option>`).join("")}</select> Shorts</label>
        <label class="check"><input type="checkbox" data-f="voiceover"> 🤖 Lời bình giọng đọc</label>
      </div>
      ${scheduleBox(a)}
      ${growBox(a)}
      ${a.long ? "" : `<p class="hint">✂ Kênh <b>chỉ làm Shorts</b> (không dựng video dài, nhanh hơn). Mỗi Short tải lên tốn 1.600 đơn vị hạn mức API (khoảng 6 Shorts/ngày cho mỗi Client ID).</p>`}
      <p class="hint">${a.daily ? `⏰ Tự làm video lúc ${a.daily} mỗi ngày.` : ""} ${a.used?.length ? `Chủ đề gần đây: ${esc(a.used.slice(-3).map((u) => u.split("|").pop()).join(", "))}` : ""}</p>`;
    for (const el of d.querySelectorAll("[data-f]")) {
      const f = el.dataset.f, v = a[f];
      if (el.type === "checkbox") el.checked = !!v;
      else el.value = f === "seeds" ? (v || []).join("\n") : f.startsWith("slots_") ? (v || []).join(", ") : String(v ?? "");
      el.onchange = async () => {
        const val = el.type === "checkbox" ? el.checked : f === "seeds" ? el.value.split("\n")
          : f.startsWith("slots_") ? el.value.split(/[,;\s]+/).filter(Boolean)
          : ["minutes", "per_run", "shorts_n"].includes(f) ? +el.value : el.value;
        try { renderChannels(await api(`/api/channels/${a.id}`, json("PUT", { [f]: val })), true); }
        catch (e) { alert(e.message); renderAccounts(CH); }
      };
    }
    for (const el of d.querySelectorAll("[data-day]")) {
      el.onchange = async () => {
        const days = [...d.querySelectorAll("[data-day]")].filter((x) => x.checked).map((x) => +x.dataset.day);
        try { renderChannels(await api(`/api/channels/${a.id}`, json("PUT", { days })), true); }
        catch (e) { alert(e.message); renderAccounts(CH); }
      };
    }
    d.querySelector('[data-act="run"]').onclick = async () => {
      const at = d.querySelector("[data-at]").value;
      try {
        renderChannels(await api(`/api/channels/${a.id}/run`, json("POST", { count: +d.querySelector("[data-run]").value, publish_at: at || null })), true);
      } catch (e) { alert(e.message); }
    };
    d.querySelector('[data-act="reconnect"]').onclick = connect;
    const sb = d.querySelector(".statsBox");
    d.querySelector('[data-act="stats"]').onclick = () => {
      STATS_OPEN[a.id] = sb.hidden;
      sb.hidden = !sb.hidden;
      if (!sb.hidden) loadStats(a, sb, false);
    };
    if (STATS_OPEN[a.id]) loadStats(a, sb, false);
    d.querySelector('[data-act="del"]').onclick = async () => {
      if (!confirm(`Xoá kênh “${a.title}” khỏi app? (Không ảnh hưởng gì tới kênh trên YouTube)`)) return;
      renderChannels(await api(`/api/channels/${a.id}`, { method: "DELETE" }), true);
    };
    box.appendChild(d);
  }
}

function renderTasks(tasks) {
  $("#taskList").innerHTML = tasks.length ? tasks.map((t) => {
    const pct = Math.round((t.progress || 0) * 100);
    return `<div class="task"><div><span class="st ${t.status}">${ST[t.status]}</span>
        <b>${esc(t.channel)}</b>${t.keyword ? ` · ${esc(t.keyword)}` : ""}
        <div class="hint">${t.status === "running" ? `${PHASE[t.phase] || ""}: ${esc(t.stage)} ${pct}%` : ""}${t.error ? `<span class="bad">${esc(t.error)}</span>` : ""}${t.warn ? ` ⚠ ${esc(t.warn)}` : ""}</div></div>
      <div>${["queued", "running"].includes(t.status) ? `<button class="mini" data-cancel="${t.id}">Huỷ</button>` : ""}</div>
      ${t.status === "running" ? `<div class="progress"><div style="width:${pct}%"></div></div>` : ""}
      ${(t.plan || []).length ? `<div class="hint">⏰ Lên sóng: ${t.plan.map((x) => `${x.kind === "long" ? "🎬" : "✂"} ${fmtAt(x.at)}`).join(" · ")}</div>`
        : t.publish_at ? `<div class="hint">⏰ Hẹn lên sóng ${fmtAt(t.publish_at)}</div>` : ""}
      <div class="links">${t.project ? `<a href="#/p/${t.project}">📁 Dự án</a>` : ""}${t.video ? `<a href="${t.video}" target="_blank" rel="noopener">▶ Video</a>` : ""}${(t.shorts || []).map((u, i) => `<a href="${u}" target="_blank" rel="noopener">✂ Short ${i + 1}</a>`).join("")}</div></div>`;
  }).join("") : `<p class="hint">Chưa có việc nào. Bấm “▶ Chạy ngay” ở một kênh.</p>`;
  $$("#taskList [data-cancel]").forEach((b) => b.onclick = async () => renderChannels(await api(`/api/tasks/${b.dataset.cancel}/cancel`, { method: "POST" }), false));
}

async function connect() {
  try { location.href = (await api("/api/channels/connect")).url; }
  catch (e) { $("#chNote").className = "note bad"; $("#chNote").textContent = e.message; }
}
$("#connectBtn").onclick = connect;
$("#clientSave").onclick = async () => {
  try {
    renderChannels(await api("/api/channels/settings", json("PUT", { client_id: $("#clientId").value, client_secret: $("#clientSecret").value })), true);
    $("#clientSecret").value = "";
    $("#chNote").className = "note ok"; $("#chNote").textContent = "Đã lưu. Bấm “+ Kết nối kênh” để đăng nhập kênh YouTube.";
  } catch (e) { alert(e.message); }
};
$("#workers").onchange = async (e) => renderChannels(await api("/api/channels/settings", json("PUT", { workers: +e.target.value })), false);
$("#runAll").onclick = async () => {
  try { renderChannels(await api("/api/channels-run-all", { method: "POST" }), false); } catch (e) { alert(e.message); }
};
$("#quotaLimit").onchange = async (e) => renderChannels(await api("/api/channels/settings", json("PUT", { quota_limit: +e.target.value })), true);
$("#clearTasks").onclick = async () => renderChannels(await api("/api/tasks", { method: "DELETE" }), false);

// ---------- trang dự án ----------
async function showEditor(id) {
  showPage("editor", "");
  apply(await api(`/api/projects/${id}`), true);
  loadProjects();
  if (V.job && !V.job.done) poll();
}

function apply(res, full) {
  V = res;
  const P = V.project, only = P.kind === "shorts";
  $("#editor").classList.toggle("shortsMode", only);
  $("#buildBtn").textContent = only ? "✂ Cắt Shorts" : "🎬 Dựng video";
  const sig = P.signals;
  $("#crumb").textContent = P.stream
    ? (only ? `📺 Cắt Shorts từ stream của ${P.keyword}` : `📺 Video tổng hợp ${(P.streams || [1]).length} buổi stream của ${P.keyword}`) + (sig ? ` · chấm điểm bằng: ${[sig.chat && `${views(sig.chat)} tin chat`,
      sig.loud && "độ to âm thanh", sig.heat && "Most replayed", sig.marks && `${sig.marks} bình luận có mốc giờ`].filter(Boolean).join(", ")}`
      + (sig.notes.length ? ` (${sig.notes.join(", ")})` : "") : "")
    : (only ? "✂ Shorts · " : "") + `“${P.keyword}”` + (P.ref ? ` · 💡 ý tưởng từ xu hướng: ${P.ref}` : "");
  $("#moreBtn").classList.toggle("hidden", !!P.stream);
  document.querySelectorAll("#editor .streamOpt").forEach((o) => { o.hidden = !P.stream; });
  if (full) {
    $("#title").value = P.title;
    $("#countdown").checked = P.countdown !== false;
    $("#credits").checked = P.credits !== false;
    $("#subtitles").checked = P.subtitles !== false;
    $("#thumbText").value = P.thumb_text || "";
    $("#shortsOpt").checked = P.shorts !== false;
    if (P.voice) $("#ttsVoice").value = P.voice;
    if (P.takes) $("#ttsTakes").value = String([3, 5, 8].includes(P.takes) ? P.takes : 3);
    $("#autoMin").value = String([10, 15, 20, ...(P.stream ? [30] : [])].includes(P.minutes) ? P.minutes : 15);
    $("#autoCount").value = String([3, 5, 8, 10, ...(P.stream ? [15, 20] : [])].includes(P.count) ? P.count : 5);
  }
  renderClips();
  showOutput();
}

function renderClips() {
  const P = V.project, byId = Object.fromEntries(P.clips.map((c) => [c.id, c]));
  const plan = $("#plan"), rest = $("#rest");
  plan.innerHTML = ""; rest.innerHTML = "";
  V.plan.forEach((x, i) => plan.appendChild(clipCard(byId[x.id], x.number, i)));
  const others = P.clips.filter((c) => !c.include);
  for (const c of others.slice(0, 80)) rest.appendChild(clipCard(c, null, -1));
  if (P.kind === "shorts") {
    const want = P.count || 5, busy = V.job && !V.job.done;
    $("#planInfo").textContent = `· ${V.plan.length} Shorts`;
    $("#restInfo").textContent = others.length ? `· ${others.length} đoạn (điểm cao trước)` : "";
    const few = V.plan.length < want && !busy && P.clips.length;
    $("#shortWarn").classList.toggle("hidden", !few);
    if (few) $("#shortWarn").textContent = P.stream ? `Stream này chỉ có ${V.plan.length} đoạn nổi bật đủ mạnh.` : `Mới có ${V.plan.length} / ${want} đoạn hay (mỗi video gốc lấy 1 đoạn). Bấm "Quét thêm 20 video" rồi "Tự chọn lại".`;
    if (!P.clips.length && !busy) plan.innerHTML = `<p class="hint">Chưa có đoạn nào.</p>`;
    return;
  }
  $("#planInfo").textContent = `· ${V.plan.length} đoạn · video dài khoảng ${fmt(V.total)}`;
  $("#restInfo").textContent = others.length ? `· ${others.length} đoạn (điểm cao trước)` : "";
  const target = (P.minutes || 15) * 60;
  const short = V.total < target * 0.85 && !(V.job && !V.job.done);
  $("#shortWarn").classList.toggle("hidden", !short || !P.clips.length);
  if (short) {
    $("#shortWarn").textContent = V.pool < target
      ? (P.stream ? `Mới đủ ${fmt(V.total)} / ${fmt(target)}: các buổi stream chỉ có ${fmt(V.pool)} đoạn nổi bật. Thêm buổi stream khác (tạo dự án mới) hoặc chọn thời lượng ngắn hơn.`
        : `Mới đủ ${fmt(V.total)} / ${fmt(target)}: các video đã quét chỉ có ${fmt(V.pool)} đoạn hay. Bấm "Quét thêm 20 video" (có thể bấm nhiều lần) rồi "Tự chọn lại".`)
      : `Mới chọn ${fmt(V.total)} / ${fmt(target)}. Thêm đoạn từ "Ứng viên khác" hoặc bấm "Tự chọn lại".`;
  }
  if (!P.clips.length && !(V.job && !V.job.done)) plan.innerHTML = `<p class="hint">Chưa có đoạn nào.</p>`;
}

function clipCard(c, number, idx) {
  const v = V.videos[c.vid] || { title: c.vid, channel: "" };
  const el = document.createElement("div");
  el.className = "clip" + (playingId === c.id ? " playing" : "");
  el.innerHTML = `<div class="num"></div><img loading="lazy" alt=""><div>
    <b></b><div class="meta"></div><div class="why"></div><div class="ctl"></div></div>`;
  el.querySelector(".num").textContent = number != null ? `#${number}` : "";
  el.querySelector("img").src = `https://i.ytimg.com/vi/${c.vid}/mqdefault.jpg`;
  el.querySelector("b").textContent = v.title;
  el.querySelector(".meta").textContent = `${v.channel} · đoạn ${fmt(c.start)}–${fmt(c.end)} (${Math.round(c.end - c.start)} giây) · điểm ${c.score}`;
  if (V.project.reply) {        // Short trả lời bình luận: bình luận sẽ hiện trong khung
    const d = document.createElement("div");
    d.className = c.comment ? "good" : "hint";
    d.textContent = c.comment ? `💬 Trả lời ${c.author || "người xem"} (${views(c.c_likes || 0)} like): “${c.comment}”`
      : "Đoạn này không có bình luận: Short sẽ dùng câu hook thường";
    el.querySelector(".why").appendChild(d);
  }
  if (V.project.stream && c.comment) {     // hook to phía trên Short lấy từ câu chat này
    const d = document.createElement("div");
    d.className = "good";
    d.textContent = `💬 Chat hay gõ: “${c.comment}”`;
    el.querySelector(".why").appendChild(d);
  }
  for (const r of c.reasons || []) { const d = document.createElement("div"); d.textContent = r; el.querySelector(".why").appendChild(d); }
  const ctl = el.querySelector(".ctl");
  const btn = (label, title, fn, cls = "mini") => {
    const b = document.createElement("button"); b.className = cls; b.textContent = label; b.title = title;
    b.onclick = fn; ctl.appendChild(b); return b;
  };
  btn("▶", "Xem đoạn này", () => preview(c));
  if (number != null) {
    btn("↑", "Lên trên", () => move(idx, -1)).disabled = idx === 0;
    btn("↓", "Xuống dưới", () => move(idx, 1)).disabled = idx === V.plan.length - 1;
    btn("✕ Bỏ", "Bỏ khỏi danh sách dựng", () => edit(c, { include: false }));
  } else {
    btn("＋ Thêm", "Thêm vào cuối danh sách dựng", () => edit(c, { include: true }), "mini primary");
  }
  btn("◀ đầu −1s", "Bắt đầu sớm hơn 1 giây", () => edit(c, { start: c.start - 1 }));
  btn("đầu +1s ▶", "Bắt đầu muộn hơn 1 giây", () => edit(c, { start: c.start + 1 }));
  btn("◀ cuối −1s", "Kết thúc sớm hơn 1 giây", () => edit(c, { end: c.end - 1 }));
  btn("cuối +1s ▶", "Kết thúc muộn hơn 1 giây", () => edit(c, { end: c.end + 1 }));
  if (number != null && V.project.kind !== "shorts") {      // Shorts không dùng chú thích / bình luận thu
    const cap = document.createElement("input");
    cap.placeholder = "Chú thích hiện trên đoạn (tuỳ chọn)"; cap.value = c.caption || ""; cap.maxLength = 120;
    cap.onchange = () => edit(c, { caption: cap.value });
    ctl.appendChild(cap);
    const rec = btn(recId === c.id ? "⏹ Dừng thu" : "🎙 Thu bình luận", "Thu giọng bình luận của bạn (phát sau đoạn này)",
      () => toggleRecord(c), "mini rec" + (recId === c.id ? " on" : ""));
    rec.disabled = recId && recId !== c.id;
    if (c.voice) {
      const s = document.createElement("span");
      s.className = "has" + (c.voice_auto ? " auto" : "");
      s.textContent = `${c.voice_auto ? "🤖" : "✓"} ${c.voice_len.toFixed(1)} giây`;
      if (c.voice_text) s.title = c.voice_text;
      ctl.appendChild(s);
      btn("🔊", "Nghe bản thu", () => { const a = $("#voicePlay"); a.src = `/api/projects/${V.project.id}/clips/${c.id}/voice.wav?v=${c.voice_at}`; a.play(); });
      btn("🗑", "Xoá bản thu", async () => apply(await api(`/api/projects/${V.project.id}/clips/${c.id}/voice`, { method: "DELETE" })));
    }
  }
  return el;
}

function preview(c) {
  playingId = c.id;
  const v = V.videos[c.vid] || {};
  $("#yt").src = `https://www.youtube-nocookie.com/embed/${c.vid}?start=${Math.floor(c.start)}&end=${Math.ceil(c.end)}&autoplay=1&rel=0`;
  $("#playerHint").classList.add("hidden");
  $("#nowPlaying").textContent = `${v.title} · ${fmt(c.start)}–${fmt(c.end)}`;
  $$(".clip").forEach((x) => x.classList.remove("playing"));
  renderClips();
}

async function edit(c, change) {
  try { apply(await api(`/api/projects/${V.project.id}/clips/${c.id}`, json("PUT", change))); }
  catch (e) { alert(e.message); }
  if (("start" in change || "end" in change) && playingId === c.id) preview(V.project.clips.find((x) => x.id === c.id));
}

async function move(i, d) {
  const order = V.plan.map((x) => x.id);
  [order[i], order[i + d]] = [order[i + d], order[i]];
  apply(await api(`/api/projects/${V.project.id}`, json("PUT", { order })));
}

async function toggleRecord(c) {
  if (recorder && recId === c.id) { recorder.stop(); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { alert("Không mở được micro. Hãy cho phép trình duyệt dùng micro."); return; }
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recId = c.id;
  recorder.ondataavailable = (e) => chunks.push(e.data);
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
    recorder = null; recId = null;
    const fd = new FormData();
    fd.append("file", blob, "voice.webm");
    try { apply(await api(`/api/projects/${V.project.id}/clips/${c.id}/voice`, { method: "POST", body: fd })); }
    catch (e) { alert(e.message); renderClips(); }
  };
  recorder.start();
  renderClips();
}

// ---------- thiết lập + dựng ----------
const saveSettings = async (o) => apply(await api(`/api/projects/${V.project.id}`, json("PUT", o)));
$("#title").onchange = (e) => saveSettings({ title: e.target.value });
$("#countdown").onchange = (e) => saveSettings({ countdown: e.target.checked });
$("#credits").onchange = (e) => saveSettings({ credits: e.target.checked });
$("#subtitles").onchange = (e) => saveSettings({ subtitles: e.target.checked });
$("#thumbText").onchange = (e) => saveSettings({ thumb_text: e.target.value });
$("#shortsOpt").onchange = (e) => saveSettings({ shorts: e.target.checked });
$("#ttsBtn").onclick = async () => {
  try { await api(`/api/projects/${V.project.id}/tts`, json("POST", { voice: $("#ttsVoice").value, takes: +$("#ttsTakes").value })); poll(); }
  catch (e) { $("#buildNote").className = "note bad"; $("#buildNote").textContent = e.message; }
};
$("#autoBtn").onclick = async () => {
  if (V.plan.length && !confirm("Chọn lại từ đầu theo điểm? Thứ tự và các đoạn bạn tự thêm sẽ bị thay.")) return;
  apply(await api(`/api/projects/${V.project.id}/autoselect`, json("POST", { minutes: +$("#autoMin").value, count: +$("#autoCount").value })));
};
$("#moreBtn").onclick = async () => {
  try { await api(`/api/projects/${V.project.id}/more`, json("POST", { videos: 20 })); poll(); }
  catch (e) { alert(e.message); }
};
$("#buildBtn").onclick = async () => {
  try { await api(`/api/projects/${V.project.id}/build`, { method: "POST" }); poll(); }
  catch (e) { $("#buildNote").className = "note bad"; $("#buildNote").textContent = e.message; }
};
$("#cancelBtn").onclick = $("#scanCancel").onclick = () => api(`/api/projects/${V.project.id}/cancel`, { method: "POST" });
$("#delBtn").onclick = async () => {
  if (!confirm("Xoá dự án này cùng mọi file của nó?")) return;
  await api(`/api/projects/${V.project.id}`, { method: "DELETE" });
  location.hash = "";
};
$$("[data-copy]").forEach((b) => b.onclick = () => copyText($("#" + b.dataset.copy).value, b));

function showOutput() {
  const ok = V.output, P = V.project;
  $("#outBox").classList.toggle("hidden", !ok);
  if (!ok) return;
  const base = `/api/projects/${P.id}/files/`;
  if (P.kind === "shorts") {
    $("#ytTags").value = P.youtube?.tags || "";
    showShortList(P, base);
    return;
  }
  if (!$("#outVideo").src.includes(P.output_stamp)) $("#outVideo").src = `${base}output.mp4?v=${P.output_stamp}`;
  $("#dlVideo").href = `${base}output.mp4?download=1`;
  const th = $("#thumbs"); th.innerHTML = "";
  for (const i of [1, 2, 3]) {
    const a = document.createElement("a");
    a.href = `${base}thumb_${i}.jpg?download=1`; a.title = `Tải mẫu ${i}`;
    a.innerHTML = `<img alt="Thumbnail ${i}">`;
    a.querySelector("img").src = `${base}thumb_${i}.jpg?v=${P.output_stamp}`;
    th.appendChild(a);
  }
  if (P.youtube) showCopy(P.youtube);
  showShortList(P, base);
}

function showShortList(P, base) {
  const sh = P.shorts_out || [];
  $("#shortsBox").classList.toggle("hidden", !sh.length);
  $("#shortsList").innerHTML = sh.map((x, i) => `<div class="short">
      <video controls playsinline preload="metadata" src="${base}${x.file}?v=${P.output_stamp}"></video>
      <b>${esc(x.title)}</b>
      <div class="row"><a class="link primary" href="${base}${x.file}?download=1">⬇ Tải</a>
        <button class="mini" data-st="${i}">Copy tiêu đề</button><button class="mini" data-sd="${i}">Copy mô tả</button></div></div>`).join("");
  $$("#shortsList [data-st]").forEach((b) => b.onclick = () => copyText(sh[+b.dataset.st].title, b));
  $$("#shortsList [data-sd]").forEach((b) => b.onclick = () => copyText(sh[+b.dataset.sd].description, b));
}

function titleLen() {
  const n = $("#ytTitle").value.length;
  $("#titleLen").textContent = `${n} ký tự` + (n > 70 ? " — dài quá, điện thoại sẽ cắt bớt" : n < 40 ? " — hơi ngắn" : " ✓");
}
$("#ytTitle").oninput = titleLen;

function showCopy(y) {
  $("#ytTitle").value = y.title; $("#ytDesc").value = y.description; $("#ytTags").value = y.tags || "";
  titleLen();
  const box = $("#titleOpts"); box.innerHTML = "";
  for (const t of y.titles || []) {
    const o = typeof t === "string" ? { text: t } : t;
    const b = document.createElement("button");
    b.className = "opt" + (o.text === y.title ? " on" : "");
    b.innerHTML = (o.score != null ? `<span class="seo s${o.score >= 8 ? "hi" : o.score >= 6 ? "mid" : "lo"}">SEO ${o.score}/10</span>` : "")
      + `<span>${esc(o.text)}</span>`;
    if (o.checks) b.title = o.checks.map(([l, ok]) => `${ok ? "✓" : "✗"} ${l}`).join("\n");
    b.onclick = () => { $("#ytTitle").value = o.text; titleLen(); $$("#titleOpts .opt").forEach((x) => x.classList.toggle("on", x === b)); };
    box.appendChild(b);
  }
  const parts = [];
  if (y.searches?.length) parts.push(`<b>🔎 Người xem đang tìm:</b> ${y.searches.map((s) => `<span class="chip">${esc(s)}</span>`).join(" ")}`);
  if (y.hot?.length) parts.push(`<b>📈 Kiểu tiêu đề hot tháng này:</b> ${y.hot.map((s) => `<span class="chip">${esc(s)}</span>`).join(" ")}`);
  if (y.names?.length) parts.push(`<b>⭐ Người nổi tiếng trong video:</b> ${y.names.map((s) => `<span class="chip">${esc(s)}</span>`).join(" ")}`);
  $("#seoBox").innerHTML = parts.map((p) => `<div>${p}</div>`).join("");
}
$("#rewriteBtn").onclick = async () => {
  try { V = await api(`/api/projects/${V.project.id}/copy`, { method: "POST" }); showCopy(V.project.youtube); }
  catch (e) { alert(e.message); }
};

function poll() {
  clearTimeout(pollTimer);
  const id = V.project.id;
  let stage = "";
  const tick = async () => {
    if (!V || V.project.id !== id) return;
    const job = await api(`/api/projects/${id}/job`).catch(() => null);
    if (!job) return;
    const busy = !job.done, scan = job.kind === "scan", pct = Math.round(job.progress * 100);
    $("#scanBox").classList.toggle("hidden", !(scan && (busy || job.error)));
    $("#scanProg div").style.width = $("#prog div").style.width = `${pct}%`;
    $("#prog").classList.toggle("hidden", !(busy && !scan));
    $("#cancelBtn").classList.toggle("hidden", !(busy && !scan));
    $("#buildBtn").disabled = $("#moreBtn").disabled = $("#autoBtn").disabled = $("#ttsBtn").disabled = busy;
    const note = scan ? $("#scanNote") : $("#buildNote");
    if (busy) {
      note.className = "note";
      note.textContent = (job.kind === "auto" ? "⚡ Làm tự động: " : "") + `${job.stage || "Đang chuẩn bị"}… ${pct}%`
        + (scan || job.kind === "tts" ? "" : V.project.kind === "shorts" ? " (khoảng 3–8 phút)" : " (video 15 phút mất khoảng 10–25 phút)");
      // làm tự động: hiện các đoạn đã chọn ngay khi quét xong, không phải chờ dựng xong
      if (job.kind === "auto" && job.stage !== stage) { stage = job.stage; apply(await api(`/api/projects/${id}`), true); }
      pollTimer = setTimeout(tick, 1000);
      return;
    }
    if (job.error) { note.className = "note bad"; note.textContent = "Lỗi: " + job.error; return; }
    apply(await api(`/api/projects/${id}`), true);
    const r = job.result || {};
    if (job.kind === "tts") {
      note.className = "note ok";
      note.textContent = `Đã tạo ${r.takes} lời bình giọng đọc (🤖). Bấm “Dựng video” để ghép vào.`;
    } else if (!scan) {
      note.className = r.note ? "note bad" : "note ok";
      note.textContent = (V.project.kind === "shorts" ? `Xong! ${r.shorts} Shorts.` : `Xong! ${r.clips} đoạn, dài ${fmt(r.duration)}` + (r.shorts ? ` + ${r.shorts} Shorts.` : "."))
        + (r.failed ? ` ${r.failed} đoạn không tải được nên đã bỏ.` : "") + (r.note ? " " + r.note : "");
    }
  };
  tick();
}

// ---------- cài đặt: cookie YouTube ----------
const BROWSER_NAMES = { firefox: "Firefox", chrome: "Chrome", edge: "Edge", brave: "Brave", chromium: "Chromium", opera: "Opera" };

function renderCookies(c, note = "", ok = true) {
  const state = c.mode === "file" ? `✓ Đang dùng file cookies.txt${c.count ? ` (${c.count} cookie YouTube)` : ""}`
    : c.mode === "browser" ? `✓ Đang dùng cookie của ${BROWSER_NAMES[c.browser] || c.browser}` : "Chưa bật cookie";
  $("#ckState").textContent = state + (c.mode !== "none" ? (c.tested ? " · đã kiểm tra OK" : " · chưa kiểm tra") : "");
  $("#ckState").className = "hint " + (c.mode !== "none" ? "okText" : "");
  $("#ckFileInfo").textContent = c.file ? `Đã có file (cập nhật ${new Date(c.updated * 1000).toLocaleString("vi-VN")})` : "";
  $("#ckBrowser").innerHTML = c.browsers.map((b) => `<option value="${b}" ${b === c.browser ? "selected" : ""}>${BROWSER_NAMES[b] || b}</option>`).join("");
  $("#ckOff").disabled = c.mode === "none";
  $("#ckDelete").disabled = !c.file;
  $("#ckTest").disabled = c.mode === "none";
  $("#ckNote").textContent = note;
  $("#ckNote").className = "note " + (note ? (ok ? "ok" : "bad") : "");
}

async function cookieAction(fn, okMsg) {
  try { renderCookies(await fn(), okMsg); }
  catch (e) { renderCookies(await api("/api/settings/cookies"), e.message, false); }
}

async function showSettings() {
  V = null;
  showPage("settings", "settings", "Cài đặt");
  renderCookies(await api("/api/settings/cookies"));
}

$("#ckFile").onchange = () => {
  const f = $("#ckFile").files[0];
  if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  $("#ckFile").value = "";
  cookieAction(() => api("/api/settings/cookies/file", { method: "POST", body: fd }),
    "Đã lưu file cookie. Bấm “Kiểm tra cookie” để thử.");
};
$("#ckUseBrowser").onclick = () => cookieAction(() => api("/api/settings/cookies", json("PUT", { mode: "browser", browser: $("#ckBrowser").value })),
  "Đã chọn trình duyệt. Bấm “Kiểm tra cookie” để thử.");
$("#ckOff").onclick = () => cookieAction(() => api("/api/settings/cookies", json("PUT", { mode: "none" })), "Đã tắt cookie.");
$("#ckDelete").onclick = () => confirm("Xoá file cookies.txt khỏi máy?")
  && cookieAction(() => api("/api/settings/cookies", { method: "DELETE" }), "Đã xoá file cookie.");
$("#ckTest").onclick = async () => {
  $("#ckTest").disabled = true;
  $("#ckNote").className = "note"; $("#ckNote").textContent = "Đang thử đọc một video bằng cookie…";
  await cookieAction(() => api("/api/settings/cookies/test", { method: "POST" }), "✓ Cookie dùng được — app sẽ tải video bằng cookie này.");
};

// ---------- 🎵 ghép nhạc ----------
let M = null, mPoll = null, MOPT = null;

async function showMusic() {
  V = null; M = null;
  showPage("musicPage", "music", "Ghép nhạc");
  if (!MOPT) {
    MOPT = await api("/api/music/options");
    $("#mCountry").innerHTML = Object.entries(MOPT.countries).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("#mGenre").innerHTML = Object.entries(MOPT.genres).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("#mVersion").innerHTML = Object.entries(MOPT.versions).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("#mVersion").onchange = showUsed;
  }
  MOPT.used = (await api("/api/music/options")).used;
  showUsed();
  const list = await api("/api/mixes");
  $("#mixes").innerHTML = list.length ? list.map((m) => `<a class="proj" href="#/m/${m.id}"><span><b>${esc(m.title)}</b><br>
    <span class="hint">${m.songs} bài · ${new Date(m.created * 1000).toLocaleDateString("vi-VN")}</span></span>
    <span class="hint">${m.done ? "✓ đã dựng" : ""}</span></a>`).join("") : `<p class="hint">Chưa có bản mix nào.</p>`;
}

const COUNT_LABEL = {
  playlist: { 10: "10 bài (~35 phút)", 15: "15 bài (~50 phút)", 20: "20 bài (~1 giờ 10)", 30: "30 bài (~1 giờ 45)" },
  mashup: { 10: "10 bài (~2,5 phút)", 15: "15 bài (~4 phút)", 20: "20 bài (~5 phút)", 30: "30 bài (~7,5 phút)" },
};
$("#mStyle").onchange = () => {
  const st = $("#mStyle").value;
  $("#mMashHint").classList.toggle("hidden", st !== "mashup");
  $("#mFadeBox").classList.toggle("hidden", st === "mashup");
  [...$("#mCount").options].forEach((o) => { o.textContent = COUNT_LABEL[st][o.value]; });
  if (st === "mashup" && +$("#mCount").value < 20) $("#mCount").value = "20";
};

function showUsed() {
  const n = MOPT?.used?.[$("#mVersion").value] || 0;
  $("#mUsed").textContent = n ? `(đã dùng ${n} bài)` : "";
}

async function createMix(auto) {
  $("#mErr").textContent = "";
  try {
    const r = await api("/api/mixes", json("POST", {
      country: $("#mCountry").value, genre: $("#mGenre").value, version: $("#mVersion").value, count: +$("#mCount").value,
      style: $("#mStyle").value,
      fade: +$("#mFade").value, period: $("#mPeriod").value, free: $("#mFree").checked, fresh: $("#mFresh").checked, auto }));
    location.hash = `#/m/${r.mix.id}`;
  } catch (e) { $("#mErr").textContent = e.message; }
}
$("#mAutoBtn").onclick = () => createMix(true);
$("#mFindBtn").onclick = () => createMix(false);

async function showMix(id) {
  V = null;
  showPage("mixEditor", "music", "Ghép nhạc");
  $("#mYt").src = ""; $("#mPlayerHint").classList.remove("hidden");
  $("#mAddErr").textContent = ""; $("#mNote").textContent = "";
  renderMix(await api(`/api/mixes/${id}`));
  pollMix();
}

function renderMix(r) {
  M = r;
  const m = r.mix, busy = r.job && !r.job.done;
  $("#mInfo").textContent = `· ${r.tracks.length} bài · dài khoảng ${fmt(r.total)}`;
  $("#mFadeEdit").value = m.fade;
  const mash = m.style === "mashup";
  $$(".mFadeEditBox").forEach((x) => x.classList.toggle("hidden", mash));
  $("#mStyleInfo").textContent = mash ? `🔥 Mashup: điệp khúc 8 nhịp mỗi bài, nối khớp nhịp${m.bpm_groups?.length ? ` · nhịp ${m.bpm_groups.join(" → ")} BPM` : ""}. App xếp các bài theo nhóm nhịp từ chậm đến nhanh khi dựng.` : "";
  $("#mBgDel").classList.toggle("hidden", !m.bg);
  $("#mBgPrev").classList.toggle("hidden", !m.bg);
  if (m.bg) $("#mBgPrev").src = `/api/mixes/${m.id}/files/${m.bg.replace("?", "?v=")}`;
  const box = $("#mTracks");
  box.innerHTML = r.tracks.length ? "" : `<p class="hint">${busy ? "Đang tìm bài…" : "Chưa có bài nào."}</p>`;
  r.tracks.forEach((t, i) => {
    const d = document.createElement("div");
    d.className = "track"; d.dataset.id = t.id;
    d.innerHTML = `<span class="num">${i + 1}</span>
      <img loading="lazy" src="/api/music/cover/${t.id}" alt="">
      <div class="name"><span class="song" contenteditable="plaintext-only" spellcheck="false" title="Bấm để sửa tên bài">${esc(t.song)}</span>
        <span class="artist" contenteditable="plaintext-only" spellcheck="false" title="Bấm để sửa tên ca sĩ">${esc(t.artist) || "—"}</span>
        <span class="meta">${t.rank ? `#${t.rank} hot · ` : ""}${views(t.views)} view · ${fmt(t.duration)} · ${esc(t.channel)}</span></div>
      <div class="ctl"><button class="mini" data-a="play" title="Nghe thử">▶</button><button class="mini" data-a="up" title="Lên">↑</button>
        <button class="mini" data-a="down" title="Xuống">↓</button><button class="mini danger" data-a="del" title="Bỏ bài">✕</button></div>`;
    d.querySelector(".song").onblur = (e) => e.target.textContent.trim() !== t.song && mixEdit({ track: t.id, song: e.target.textContent });
    d.querySelector(".artist").onblur = (e) => {
      const v = e.target.textContent.trim(); if (v !== (t.artist || "—")) mixEdit({ track: t.id, artist: v === "—" ? "" : v });
    };
    d.querySelectorAll("[contenteditable]").forEach((el) => el.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); el.blur(); } });
    d.querySelectorAll("[data-a]").forEach((b) => b.onclick = () => trackAction(b.dataset.a, i, t));
    box.appendChild(d);
  });
  showMixOut();
}

async function mixEdit(o) {
  try { renderMix(await api(`/api/mixes/${M.mix.id}`, json("PUT", o))); }
  catch (e) { alert(e.message); }
}

function trackAction(a, i, t) {
  const order = M.tracks.map((x) => x.id);
  if (a === "play") {
    $("#mYt").src = `https://www.youtube.com/embed/${t.id}?autoplay=1`;
    $("#mPlayerHint").classList.add("hidden");
    $$(".track").forEach((x) => x.classList.toggle("playing", x.dataset.id === t.id));
    return;
  }
  if (a === "del") order.splice(i, 1);
  else {
    const j = a === "up" ? i - 1 : i + 1;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
  }
  mixEdit({ order });
}

$("#mFadeEdit").onchange = () => mixEdit({ fade: +$("#mFadeEdit").value });
$("#mAddBtn").onclick = async () => {
  const url = $("#mAddUrl").value.trim();
  if (!url) return;
  $("#mAddBtn").disabled = true; $("#mAddErr").textContent = "";
  try { renderMix(await api(`/api/mixes/${M.mix.id}/tracks`, json("POST", { url }))); $("#mAddUrl").value = ""; }
  catch (e) { $("#mAddErr").textContent = e.message; }
  $("#mAddBtn").disabled = false;
};
$("#mBg").onchange = async () => {
  const f = $("#mBg").files[0];
  if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  $("#mBg").value = "";
  try { renderMix(await api(`/api/mixes/${M.mix.id}/bg`, { method: "POST", body: fd })); }
  catch (e) { alert(e.message); }
};
$("#mBgDel").onclick = async () => renderMix(await api(`/api/mixes/${M.mix.id}/bg`, { method: "DELETE" }));
$("#mMoreBtn").onclick = async () => {
  try { await api(`/api/mixes/${M.mix.id}/more`, { method: "POST" }); pollMix(); } catch (e) { alert(e.message); }
};
$("#mTitle").onchange = () => mixEdit({ title: $("#mTitle").value });
$("#mBuildBtn").onclick = async () => {
  try {
    await api(`/api/mixes/${M.mix.id}/build`, { method: "POST" });
    pollMix();
  } catch (e) { alert(e.message); }
};
$("#mCancelBtn").onclick = () => api(`/api/mixes/${M.mix.id}/cancel`, { method: "POST" });
$("#mDelBtn").onclick = async () => {
  if (!confirm("Xoá bản mix này cùng video đã dựng?")) return;
  await api(`/api/mixes/${M.mix.id}`, { method: "DELETE" });
  location.hash = "#/music";
};

function showMixOut() {
  const m = M.mix, ok = M.output, base = `/api/mixes/${m.id}/files/`;
  $("#mOut").classList.toggle("hidden", !ok);
  $("#mBuildBtn").textContent = m.output_stamp ? "🎬 Dựng lại" : "🎬 Dựng video";
  if (!ok) return;
  if (!$("#mVideo").src.includes(m.output_stamp)) $("#mVideo").src = `${base}output.mp4?v=${m.output_stamp}`;
  $("#mThumb").src = `${base}thumbnail.jpg?v=${m.output_stamp}`;
  $("#mThumbA").href = `${base}thumbnail.jpg?download=1`;
  $("#mDl").href = `${base}output.mp4?download=1`;
  const y = m.youtube || {};
  if (document.activeElement !== $("#mTitle")) $("#mTitle").value = y.title || "";
  $("#mDesc").value = y.description || ""; $("#mTags").value = y.tags || "";
}

function pollMix() {
  clearTimeout(mPoll);
  const id = M.mix.id;
  let stage = "";
  const tick = async () => {
    if (!M || M.mix.id !== id || !location.hash.startsWith("#/m/")) return;
    const r = await api(`/api/mixes/${id}`).catch(() => null);
    if (!r) return;
    const job = r.job, busy = job && !job.done, find = job?.kind === "find", pct = Math.round((job?.progress || 0) * 100);
    $("#mScanBox").classList.toggle("hidden", !(find && (busy || job.error)));
    $("#mScanProg div").style.width = $("#mProg div").style.width = `${pct}%`;
    $("#mProg").classList.toggle("hidden", !(busy && !find));
    $("#mCancelBtn").classList.toggle("hidden", !busy);
    $("#mBuildBtn").disabled = $("#mMoreBtn").disabled = $("#mAddBtn").disabled = !!busy;
    const note = find ? $("#mScanNote") : $("#mNote");
    if (busy) {
      note.className = "note";
      note.textContent = (job.kind === "auto" ? "⚡ " : "") + `${job.stage || "Đang chuẩn bị"}… ${pct}%`;
      if (job.stage !== stage) { stage = job.stage; renderMix(r); }
      mPoll = setTimeout(tick, 1500);
      return;
    }
    renderMix(r);
    if (!job) return;
    if (job.error) { note.className = "note bad"; note.textContent = "Lỗi: " + job.error; return; }
    const x = job.result || {};
    if (find) { $("#mScanBox").classList.add("hidden"); return; }
    note.className = "note ok";
    note.textContent = `Xong! ${x.songs} bài, dài ${fmt(x.duration)}.` + (x.failed ? ` ${x.failed} bài không tải được nên đã bỏ.` : "");
  };
  tick();
}

// ---------- 🎤 TikTok lời bài hát ----------
let tPoll = null;

async function showTikTok() {
  V = null; M = null;
  showPage("tiktokPage", "tiktok", "TikTok lời bài hát");
  await loadTikTok();
}

async function loadTikTok() {
  clearTimeout(tPoll);
  if (!location.hash.startsWith("#/tiktok")) return;
  const list = await api("/api/tiktok").catch(() => null);
  if (!list) return;
  const box = $("#tList");
  if (!list.length) box.innerHTML = `<p class="hint">Chưa có video nào.</p>`;
  const ids = list.map((t) => t.id);
  [...box.querySelectorAll(".tt")].forEach((el) => { if (!ids.includes(el.dataset.id)) el.remove(); });
  if (list.length) box.querySelector("p.hint")?.remove();
  list.forEach((t, i) => {
    let el = box.querySelector(`.tt[data-id="${t.id}"]`);
    const key = JSON.stringify([t.stamp, t.job?.done, t.job?.error, t.job?.stage, Math.round((t.job?.progress || 0) * 100), t.warn, t.title]);
    if (el && el.dataset.key === key) return;
    const fresh = !el;
    if (fresh) { el = document.createElement("div"); el.className = "tt"; el.dataset.id = t.id; }
    el.dataset.key = key;
    renderTT(el, t);
    if (fresh) box.insertBefore(el, box.children[i] || null);
  });
  if (list.some((t) => t.job && !t.job.done)) tPoll = setTimeout(loadTikTok, 1500);
}

function renderTT(el, t) {
  const busy = t.job && !t.job.done, pct = Math.round((t.job?.progress || 0) * 100), base = `/api/tiktok/${t.id}/files/`;
  const len = t.length ? `${t.length} giây` : "cả bài";
  const status = busy ? `<p class="note">${esc(t.job.stage || "Đang chuẩn bị")}… ${pct}%</p><div class="progress"><div style="width:${pct}%"></div></div>`
    : t.job?.error ? `<p class="note bad">Lỗi: ${esc(t.job.error)}</p>` : "";
  el.innerHTML = `${t.output && !busy ? `<video controls playsinline preload="metadata" src="${base}output.mp4?v=${t.stamp}"></video>`
      : `<div class="ph">${busy ? "Đang làm…" : "Chưa có video"}</div>`}
    <div class="info">
      <b>${esc(t.title)}</b>
      <span class="hint">${len}${t.duration ? ` (${fmt(t.duration)})` : ""} · nền “${esc(t.bg)}” · ${new Date(t.created * 1000).toLocaleString("vi-VN")}</span>
      ${status}
      ${t.warn ? `<p class="note">⚠ ${esc(t.warn)}</p>` : ""}
      ${t.output && !busy ? `<label class="lbl">Caption TikTok <button class="mini" data-a="copy">Copy</button></label>
      <textarea data-f="caption">${esc(t.caption || "")}</textarea>
      <div class="offset"><span class="hint">Lệch lời (giây, + = chữ hiện muộn hơn):</span>
        <input type="number" step="0.1" min="-10" max="10" value="${t.offset || 0}" data-f="offset">
        <button class="mini" data-a="offset">Áp dụng</button></div>` : ""}
      <div class="row">
        ${t.output && !busy ? `<a class="link primary" href="${base}output.mp4?download=1">⬇ Tải video</a>
          <a class="link" href="https://www.tiktok.com/tiktokstudio/upload" target="_blank" rel="noopener">TikTok Studio ↗</a>` : ""}
        ${busy ? `<button class="mini" data-a="cancel">Huỷ</button>` : `<button class="mini" data-a="build">${t.output ? "🔁 Dựng lại" : "🎬 Dựng"}</button>
          <button class="mini danger" data-a="del">Xoá</button>`}
      </div>
    </div>`;
  const act = {
    copy: (b) => copyText(el.querySelector("[data-f=caption]").value, b),
    offset: () => api(`/api/tiktok/${t.id}`, json("PUT", { offset: +el.querySelector("[data-f=offset]").value })),
    build: () => api(`/api/tiktok/${t.id}/build`, { method: "POST" }),
    cancel: () => api(`/api/tiktok/${t.id}/cancel`, { method: "POST" }),
    del: () => confirm("Xoá video này?") && api(`/api/tiktok/${t.id}`, { method: "DELETE" }),
  };
  el.querySelectorAll("[data-a]").forEach((b) => b.onclick = async () => {
    try { await act[b.dataset.a](b); if (b.dataset.a !== "copy") loadTikTok(); } catch (e) { alert(e.message); }
  });
  const cap = el.querySelector("[data-f=caption]");
  if (cap) cap.onchange = () => api(`/api/tiktok/${t.id}`, json("PUT", { caption: cap.value })).catch((e) => alert(e.message));
}

$("#tMake").onclick = async () => {
  $("#tErr").textContent = "";
  const song = $("#tSong").value.trim();
  if (!song) { $("#tErr").textContent = "Nhập tên bài hát hoặc link YouTube"; return; }
  $("#tMake").disabled = true;
  try {
    await api("/api/tiktok", json("POST", { song, length: +$("#tLen").value, bg: $("#tBg").value }));
    $("#tSong").value = "";
    await loadTikTok();
  } catch (e) { $("#tErr").textContent = e.message; }
  $("#tMake").disabled = false;
};

function route() {
  const h = location.hash, m = h.match(/^#\/p\/(\w+)/), mx = h.match(/^#\/m\/(\w+)/);
  if (m) showEditor(m[1]).catch((e) => { alert(e.message); location.hash = ""; });
  else if (mx) showMix(mx[1]).catch((e) => { alert(e.message); location.hash = "#/music"; });
  else if (h.startsWith("#/music")) showMusic();
  else if (h.startsWith("#/tiktok")) showTikTok();
  else if (h.startsWith("#/trends")) showTrends();
  else if (h.startsWith("#/shorts")) showShorts();
  else if (h.startsWith("#/channels")) showChannels();
  else if (h.startsWith("#/settings")) showSettings();
  else if (h.startsWith("#/projects")) showProjects();
  else showHome();
}
addEventListener("hashchange", route);
route();
