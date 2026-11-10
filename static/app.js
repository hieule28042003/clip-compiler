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
const PAGES = ["home", "projectsPage", "trends", "editor"];
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
      + `${new Date(p.created * 1000).toLocaleDateString("vi-VN")}</span></span><span class="hint">${p.done ? "✓ đã dựng" : ""}</span>`;
    box.appendChild(a);
  }
  const cur = V?.project?.id;
  $("#recent").innerHTML = list.slice(0, 8).map((p) =>
    `<a href="#/p/${p.id}" class="${p.id === cur ? "on" : ""}" title="${esc(p.title)}">${p.done ? "✓ " : ""}${esc(p.title)}</a>`).join("")
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
  const res = await api("/api/projects", json("POST", {
    keyword, videos: +$("#nVideos").value, length: +$("#clipLen").value,
    minutes: +$("#minutes").value, comments: $("#useComments").checked, auto,
    voiceover: $("#voiceover").checked, voice: $("#voice").value, shorts: $("#shorts").checked,
    seed_videos: seed ? seed.videos.map((v) => v.id) : [], ref: seed ? seed.ref : "" }));
  SEED = null;
  location.hash = `#/p/${res.project.id}`;
}
$("#createBtn").onclick = () => { $("#createErr").textContent = ""; createProject(false).catch((e) => { $("#createErr").textContent = e.message; }); };
$("#autoCreateBtn").onclick = () => { $("#createErr").textContent = ""; createProject(true).catch((e) => { $("#createErr").textContent = e.message; }); };
$("#kw").onkeydown = (e) => { if (e.key === "Enter") $("#createBtn").click(); };

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
    await api("/api/trends", json("POST", { seed: $("#seed").value, depth: +$("#depth").value }));
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
  $("#trendAt").textContent = `· “${res.seed}” · ${new Date(res.at * 1000).toLocaleString("vi-VN")}`;
  const tb = $("#topics"); tb.innerHTML = "";
  if (!res.topics.length) tb.innerHTML = `<p class="hint">Chưa tìm ra chủ đề nổi bật — thử lĩnh vực cụ thể hơn hoặc độ sâu “Kỹ”.</p>`;
  res.topics.forEach((t, i) => {
    const d = document.createElement("div");
    d.className = "topic card";
    d.innerHTML = `<div class="tThumbs">${t.videos.slice(0, 3).map((v) =>
        `<a href="https://youtu.be/${v.id}" target="_blank" rel="noopener" title="${esc(v.title)}"><img src="https://i.ytimg.com/vi/${v.id}/mqdefault.jpg" alt=""></a>`).join("")}</div>
      <div class="tHead"><span class="rank">#${i + 1}</span><b>${esc(t.name)}</b></div>
      <div class="hint">${views(t.views)} view tháng này · ${t.count} video hot ·
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

// ---------- trang dự án ----------
async function showEditor(id) {
  showPage("editor", "");
  apply(await api(`/api/projects/${id}`), true);
  loadProjects();
  if (V.job && !V.job.done) poll();
}

function apply(res, full) {
  V = res;
  const P = V.project;
  $("#crumb").textContent = `“${P.keyword}”` + (P.ref ? ` · 💡 ý tưởng từ xu hướng: ${P.ref}` : "");
  if (full) {
    $("#title").value = P.title;
    $("#countdown").checked = P.countdown !== false;
    $("#credits").checked = P.credits !== false;
    $("#subtitles").checked = P.subtitles !== false;
    $("#thumbText").value = P.thumb_text || "";
    $("#shortsOpt").checked = P.shorts !== false;
    if (P.voice) $("#ttsVoice").value = P.voice;
    if (P.takes) $("#ttsTakes").value = String([3, 5, 8].includes(P.takes) ? P.takes : 3);
    $("#autoMin").value = String([10, 15, 20].includes(P.minutes) ? P.minutes : 15);
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
  $("#planInfo").textContent = `· ${V.plan.length} đoạn · video dài khoảng ${fmt(V.total)}`;
  $("#restInfo").textContent = others.length ? `· ${others.length} đoạn (điểm cao trước)` : "";
  const target = (P.minutes || 15) * 60;
  const short = V.total < target * 0.85 && !(V.job && !V.job.done);
  $("#shortWarn").classList.toggle("hidden", !short || !P.clips.length);
  if (short) {
    $("#shortWarn").textContent = V.pool < target
      ? `Mới đủ ${fmt(V.total)} / ${fmt(target)}: các video đã quét chỉ có ${fmt(V.pool)} đoạn hay. Bấm "Quét thêm 20 video" (có thể bấm nhiều lần) rồi "Tự chọn lại".`
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
  if (number != null) {
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
  apply(await api(`/api/projects/${V.project.id}/autoselect`, json("POST", { minutes: +$("#autoMin").value })));
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
        + (scan || job.kind === "tts" ? "" : " (video 15 phút mất khoảng 10–25 phút)");
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
      note.textContent = `Xong! ${r.clips} đoạn, dài ${fmt(r.duration)}` + (r.shorts ? ` + ${r.shorts} Shorts.` : ".")
        + (r.failed ? ` ${r.failed} đoạn không tải được nên đã bỏ.` : "") + (r.note ? " " + r.note : "");
    }
  };
  tick();
}

function route() {
  const h = location.hash, m = h.match(/^#\/p\/(\w+)/);
  if (m) showEditor(m[1]).catch((e) => { alert(e.message); location.hash = ""; });
  else if (h.startsWith("#/trends")) showTrends();
  else if (h.startsWith("#/projects")) showProjects();
  else showHome();
}
addEventListener("hashchange", route);
route();
