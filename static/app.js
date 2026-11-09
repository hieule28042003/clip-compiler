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

// ---------- trang chủ ----------
async function showHome() {
  V = null;
  $("#home").classList.remove("hidden"); $("#editor").classList.add("hidden");
  $("#crumb").textContent = "";
  const list = await api("/api/projects");
  const box = $("#projects");
  box.innerHTML = list.length ? "" : `<p class="hint">Chưa có dự án nào.</p>`;
  for (const p of list) {
    const a = document.createElement("a");
    a.className = "proj"; a.href = `#/p/${p.id}`;
    a.innerHTML = `<span><b></b><br><span class="hint"></span></span><span class="hint"></span>`;
    a.querySelector("b").textContent = p.title;
    a.querySelector("span .hint").textContent = `“${p.keyword}” · ${p.clips} đoạn · ${new Date(p.created * 1000).toLocaleDateString("vi-VN")}`;
    a.lastElementChild.textContent = p.done ? "✓ đã dựng" : "";
    box.appendChild(a);
  }
}

$("#createBtn").onclick = async () => {
  $("#createErr").textContent = "";
  try {
    const res = await api("/api/projects", json("POST", {
      keyword: $("#kw").value, videos: +$("#nVideos").value, length: +$("#clipLen").value,
      minutes: +$("#minutes").value, comments: $("#useComments").checked }));
    location.hash = `#/p/${res.project.id}`;
  } catch (e) { $("#createErr").textContent = e.message; }
};
$("#kw").onkeydown = (e) => { if (e.key === "Enter") $("#createBtn").click(); };

// ---------- trang dự án ----------
async function showEditor(id) {
  $("#home").classList.add("hidden"); $("#editor").classList.remove("hidden");
  apply(await api(`/api/projects/${id}`), true);
  if (V.job && !V.job.done) poll();
}

function apply(res, full) {
  V = res;
  const P = V.project;
  $("#crumb").textContent = `“${P.keyword}”`;
  if (full) {
    $("#title").value = P.title;
    $("#countdown").checked = P.countdown !== false;
    $("#credits").checked = P.credits !== false;
    $("#subtitles").checked = P.subtitles !== false;
    $("#thumbText").value = P.thumb_text || "";
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
      const s = document.createElement("span"); s.className = "has"; s.textContent = `✓ ${c.voice_len.toFixed(1)} giây`; ctl.appendChild(s);
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
}

function showCopy(y) {
  $("#ytTitle").value = y.title; $("#ytDesc").value = y.description;
  const box = $("#titleOpts"); box.innerHTML = "";
  for (const t of y.titles || []) {
    const b = document.createElement("button");
    b.className = "opt" + (t === y.title ? " on" : ""); b.textContent = t; b.title = `${t.length} ký tự`;
    b.onclick = () => { $("#ytTitle").value = t; $$("#titleOpts .opt").forEach((x) => x.classList.toggle("on", x === b)); };
    box.appendChild(b);
  }
}
$("#rewriteBtn").onclick = async () => {
  try { V = await api(`/api/projects/${V.project.id}/copy`, { method: "POST" }); showCopy(V.project.youtube); }
  catch (e) { alert(e.message); }
};

function poll() {
  clearTimeout(pollTimer);
  const id = V.project.id;
  const tick = async () => {
    if (!V || V.project.id !== id) return;
    const job = await api(`/api/projects/${id}/job`).catch(() => null);
    if (!job) return;
    const busy = !job.done, scan = job.kind === "scan", pct = Math.round(job.progress * 100);
    $("#scanBox").classList.toggle("hidden", !(scan && (busy || job.error)));
    $("#scanProg div").style.width = $("#prog div").style.width = `${pct}%`;
    $("#prog").classList.toggle("hidden", !(busy && !scan));
    $("#cancelBtn").classList.toggle("hidden", !(busy && !scan));
    $("#buildBtn").disabled = $("#moreBtn").disabled = $("#autoBtn").disabled = busy;
    const note = scan ? $("#scanNote") : $("#buildNote");
    if (busy) {
      note.className = "note";
      note.textContent = `${job.stage || "Đang chuẩn bị"}… ${pct}%` + (scan ? "" : " (video 15 phút mất khoảng 10–20 phút)");
      pollTimer = setTimeout(tick, 1000);
      return;
    }
    if (job.error) { note.className = "note bad"; note.textContent = "Lỗi: " + job.error; return; }
    apply(await api(`/api/projects/${id}`), true);
    if (!scan) {
      const r = job.result || {};
      note.className = "note ok";
      note.textContent = `Xong! ${r.clips} đoạn, dài ${fmt(r.duration)}.` + (r.failed ? ` ${r.failed} đoạn không tải được nên đã bỏ.` : "");
    }
  };
  tick();
}

function route() {
  const m = location.hash.match(/^#\/p\/(\w+)/);
  if (m) showEditor(m[1]).catch((e) => { alert(e.message); location.hash = ""; });
  else showHome();
}
addEventListener("hashchange", route);
route();
