"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const SYS = {
  GPS: { c: "#4caf50", l: "G" }, GLONASS: { c: "#e94f7a", l: "R" }, Galileo: { c: "#3fa7ff", l: "E" },
  BeiDou: { c: "#ffa726", l: "C" }, QZSS: { c: "#b47cff", l: "J" }, NavIC: { c: "#26c6da", l: "I" },
  SBAS: { c: "#9e9e9e", l: "S" }, GNSS: { c: "#cccccc", l: "?" },
};
const sysOf = s => SYS[s] || SYS.GNSS;
const fmt = (v, d = 1, unit = "") => (v === null || v === undefined || Number.isNaN(v)) ? "--" : (typeof v === "number" ? v.toFixed(d) : v) + unit;
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opt = {}) {
  const o = { method: opt.method || (opt.body ? "POST" : "GET"), headers: {} };
  if (opt.body) { o.body = JSON.stringify(opt.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, err = false) {
  const d = document.createElement("div");
  d.textContent = msg; if (err) d.className = "err";
  $("#toast").appendChild(d); setTimeout(() => d.remove(), 4000);
}

// ------------------------------------------------------------------ tabs
let activeTab = "live";
$$(".tab").forEach(b => b.onclick = () => {
  $$(".tab").forEach(x => x.classList.toggle("active", x === b));
  activeTab = b.dataset.tab;
  $$(".tabpane").forEach(p => p.classList.toggle("active", p.id === "tab-" + activeTab));
  if (activeTab === "test") loadResults();
  if (activeTab === "config") { loadConfig(); loadLogs(); loadPorts(); }
});

// ----------------------------------------------------------- live status
let lastStatus = null, restartAt = null;
async function pollStatus() {
  try {
    const s = await api("/api/status");
    lastStatus = s; renderStatus(s);
  } catch (e) { setPill("#pillPort", "服务断开", "bad"); }
  setTimeout(pollStatus, 1000);
}
function setPill(sel, text, cls) { const p = $(sel); p.textContent = text; p.className = "pill " + (cls || ""); }

function renderStatus(s) {
  const alive = s.connected && s.data_age !== null && s.data_age < 3;
  setPill("#pillPort", (s.port || "").replace("/dev/", "") + (s.connected ? (alive ? " 数据正常" : " 无数据") : " 未连接"), alive ? "ok" : "bad");
  setPill("#pillRate", s.line_rate.toFixed(1) + " 行/s" + (s.bad_cs ? ` · 校验错 ${s.bad_cs}` : ""), s.bad_cs ? "warn" : "");
  setPill("#pillEngine", alive ? "引擎 运行" : "引擎 停止/无输出", alive ? "ok" : "warn");
  const ft = s.fix_valid ? (s.fix_type === 3 ? "3D 定位" : s.fix_type === 2 ? "2D 定位" : "已定位") : "无定位";
  setPill("#pillFix", ft, s.fix_valid ? "ok" : "bad");
  const rec = $("#pillRec"); rec.hidden = !s.recording; if (s.recording) rec.className = "pill bad";
  const br = $("#btnRec"); br.textContent = s.recording ? "停止录制 (" + s.recording + ")" : "开始录制 NMEA"; br.classList.toggle("on", !!s.recording);

  $("#vFix").textContent = ft; $("#vFix").style.color = s.fix_valid ? "var(--ok)" : "var(--bad)";
  $("#vQual").textContent = "质量: " + s.quality_text + (s.test_state && !["idle", "done", "aborted", "error"].includes(s.test_state) ? " · 测试中" : "");
  if (s.lat !== null) {
    $("#vLatLon").innerHTML = `${s.lat.toFixed(7)}°<br>${s.lon.toFixed(7)}°`;
    $("#mapLink").href = `https://www.openstreetmap.org/?mlat=${s.lat}&mlon=${s.lon}#map=18/${s.lat}/${s.lon}`;
  } else $("#vLatLon").textContent = "--";
  $("#vLatLon").style.opacity = s.fix_valid ? 1 : .45;

  const sv = s.survey || {};
  if (sv.n && sv.lat !== null) {
    $("#vSvN").textContent = `(${sv.n}s)`;
    $("#vSvLatLon").innerHTML = `${sv.lat.toFixed(7)}°<br>${sv.lon.toFixed(7)}°`;
    $("#vSvLatLon").style.color = "var(--ok)";
    $("#vSvStd").textContent = `收敛不确定度 ±${sv.std_m !== null ? (sv.std_m * 100).toFixed(1) + " cm" : "--"}`;
  } else {
    $("#vSvN").textContent = "";
    $("#vSvLatLon").textContent = "--";
    $("#vSvStd").textContent = "等待静态收敛...";
  }

  $("#vAlt").textContent = `${fmt(s.alt, 1)} / ${fmt(sv.alt, 2, " m")}`;
  $("#vGeoid").textContent = "大地水准面差距: " + fmt(s.geoid, 1, " m");
  $("#vSats").textContent = `${s.sats_used || 0} / ${s.sats_view}`;
  $("#vPerSys").textContent = Object.entries(s.per_sys).map(([k, v]) => `${sysOf(k).l}:${v.used}/${v.view}`).join("  ") || "--";
  $("#vSnr").textContent = `${fmt(s.snr_top4, 1)} / ${fmt(s.snr_max, 0)}`;
  $("#vDop").textContent = `${fmt(s.pdop, 2)} / ${fmt(s.hdop, 2)} / ${fmt(s.vdop, 2)}`;
  $("#vSpeed").textContent = `速度 ${fmt(s.speed_kmh, 1, " km/h")} · 航向 ${fmt(s.course, 1, "°")}`;
  const t = s.utc_time ? s.utc_time.replace(/^(\d\d)(\d\d)(\d\d)(\.\d+)?/, "$1:$2:$3") : "--";
  const d = s.utc_date ? s.utc_date.replace(/^(\d\d)(\d\d)(\d\d)$/, "20$3-$2-$1") : "";
  $("#vUtc").innerHTML = `${d}<br>${t}`;
  $("#vAge").textContent = s.last_fix_age !== null ? `上次定位 ${fmt(s.last_fix_age, 0)} s 前` : "从未定位";

  if (restartAt) {
    const el = (Date.now() / 1000 - restartAt);
    if (s.fix_valid && s.last_fix_age !== null && s.server_time - s.last_fix_age > restartAt) {
      $("#restartTimer").textContent = `✔ 定位用时 ${(s.server_time - s.last_fix_age - restartAt).toFixed(1)} s`;
      restartAt = null;
    } else $("#restartTimer").textContent = `重启后 ${el.toFixed(0)} s, 等待定位...`;
  }
  if (activeTab === "live") {
    drawSky(s.satellites); drawSnr(s.satellites); renderSatTable(s.satellites);
  }
}

function renderSatTable(sats) {
  $("#satTable tbody").innerHTML = sats.map(x => `<tr class="${x.used ? "used" : ""}">
    <td><span style="color:${sysOf(x.sys).c}">●</span> ${x.sys}</td><td>${sysOf(x.sys).l}${String(x.prn).padStart(2, "0")}</td>
    <td>${fmt(x.el, 0, "°")}</td><td>${fmt(x.az, 0, "°")}</td><td>${fmt(x.snr, 0)}</td><td>${x.used ? "✔" : ""}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted">无卫星数据 (请检查天线 / 是否在室外)</td></tr>`;
}

$("#legend").innerHTML = ["GPS", "GLONASS", "Galileo", "BeiDou", "QZSS", "SBAS"].map(k => `<span><i style="background:${SYS[k].c}"></i>${k} (${SYS[k].l})</span>`).join("");

// ---------------------------------------------------------------- canvas
function drawSky(sats) {
  const c = $("#sky"), g = c.getContext("2d"), W = c.width, cx = W / 2, cy = W / 2, R = W / 2 - 26;
  g.clearRect(0, 0, W, W);
  g.strokeStyle = "#2a3a48"; g.fillStyle = "#7f93a3"; g.lineWidth = 1; g.font = "12px sans-serif";
  for (const el of [0, 30, 60]) { g.beginPath(); g.arc(cx, cy, R * (90 - el) / 90, 0, 7); g.stroke(); g.fillText(el + "°", cx + 3, cy - R * (90 - el) / 90 + 12); }
  for (let a = 0; a < 360; a += 30) {
    const r = a * Math.PI / 180; g.beginPath(); g.moveTo(cx, cy); g.lineTo(cx + R * Math.sin(r), cy - R * Math.cos(r)); g.stroke();
  }
  g.font = "bold 14px sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
  [["N", 0], ["E", 90], ["S", 180], ["W", 270]].forEach(([t, a]) => {
    const r = a * Math.PI / 180; g.fillText(t, cx + (R + 14) * Math.sin(r), cy - (R + 14) * Math.cos(r));
  });
  g.font = "bold 10px sans-serif";
  for (const s of sats) {
    if (s.el === null || s.az === null) continue;
    const r = R * (90 - Math.max(0, s.el)) / 90, a = s.az * Math.PI / 180;
    const x = cx + r * Math.sin(a), y = cy - r * Math.cos(a), col = sysOf(s.sys).c;
    g.beginPath(); g.arc(x, y, 12, 0, 7);
    g.globalAlpha = s.used ? 1 : (s.snr ? .35 : .12); g.fillStyle = col; g.fill(); g.globalAlpha = 1;
    g.strokeStyle = col; g.lineWidth = 2; g.stroke();
    g.fillStyle = s.used ? "#000" : "#fff"; g.fillText(sysOf(s.sys).l + s.prn, x, y);
  }
  g.textAlign = "left"; g.textBaseline = "alphabetic"; g.lineWidth = 1;
}

function drawSnr(sats) {
  const c = $("#snr"), g = c.getContext("2d"), W = c.width, H = c.height, L = 34, B = 36, T = 14, MAX = 60;
  g.clearRect(0, 0, W, H);
  const y = v => H - B - (H - B - T) * Math.min(v, MAX) / MAX;
  g.font = "11px sans-serif"; g.fillStyle = "#7f93a3"; g.strokeStyle = "#2a3a48";
  for (let v = 0; v <= MAX; v += 10) { g.beginPath(); g.moveTo(L, y(v)); g.lineTo(W - 4, y(v)); g.stroke(); g.fillText(v, 6, y(v) + 4); }
  const list = sats.filter(s => s.snr !== null || s.used);
  if (!list.length) { g.fillText("无卫星信号", W / 2 - 30, H / 2); return; }
  const slot = (W - L - 8) / list.length, bw = Math.min(26, slot - 3);
  g.textAlign = "center";
  list.forEach((s, i) => {
    const x = L + 4 + i * slot + (slot - bw) / 2, v = s.snr || 0, col = sysOf(s.sys).c;
    g.fillStyle = col; g.globalAlpha = s.used ? 1 : .3; g.fillRect(x, y(v), bw, y(0) - y(v)); g.globalAlpha = 1;
    g.strokeStyle = col; g.strokeRect(x + .5, y(v) + .5, bw - 1, y(0) - y(v));
    g.fillStyle = "#d8e2ea"; if (v) g.fillText(v, x + bw / 2, y(v) - 3);
    g.fillStyle = col; g.save(); g.translate(x + bw / 2, H - B + 6);
    if (slot < 26) { g.rotate(-Math.PI / 2); g.textAlign = "right"; g.fillText(sysOf(s.sys).l + s.prn, 0, 4); }
    else g.fillText(sysOf(s.sys).l + s.prn, 0, 10);
    g.restore(); g.textAlign = "center";
  });
  g.textAlign = "left"; g.strokeStyle = "#ffb020"; g.setLineDash([5, 4]); g.beginPath(); g.moveTo(L, y(35)); g.lineTo(W - 4, y(35)); g.stroke(); g.setLineDash([]);
}

async function pollTrack() {
  if (activeTab === "live") {
    try { drawScatter(await api("/api/track")); } catch (e) { }
  }
  setTimeout(pollTrack, 2000);
}
function niceStep(range) { const r = range / 4, p = Math.pow(10, Math.floor(Math.log10(r))); return [1, 2, 5, 10].map(m => m * p).find(s => s >= r) || p * 10; }
function drawScatter(d) {
  const c = $("#scatter"), g = c.getContext("2d"), W = c.width, cx = W / 2, cy = W / 2;
  g.clearRect(0, 0, W, W);
  const st = d.stats, fst = d.flt_stats;
  $("#trackInfo").textContent = st
    ? `N=${st.n} · 原始 CEP50 ${st.cep50.toFixed(2)}m / 滤波后 ${fst ? fst.cep50.toFixed(2) + "m" : "--"} · 原始 CEP95 ${st.cep95.toFixed(2)}m / 滤波后 ${fst ? fst.cep95.toFixed(2) + "m" : "--"}`
    : "(无定位数据)";
  let ext = 1.5; d.points.forEach(([e, n]) => ext = Math.max(ext, Math.abs(e), Math.abs(n)));
  if (st) ext = Math.max(ext, st.cep95);
  ext *= 1.15; const k = (W / 2 - 20) / ext, step = niceStep(ext);
  g.strokeStyle = "#22313d"; g.fillStyle = "#7f93a3"; g.font = "11px sans-serif";
  for (let v = step; v <= ext; v += step) {
    for (const sg of [1, -1]) {
      g.beginPath(); g.moveTo(cx + sg * v * k, 0); g.lineTo(cx + sg * v * k, W); g.stroke();
      g.beginPath(); g.moveTo(0, cy + sg * v * k); g.lineTo(W, cy + sg * v * k); g.stroke();
    }
    g.fillText(v + "m", cx + v * k + 2, cy - 3);
  }
  g.strokeStyle = "#3b5060"; g.beginPath(); g.moveTo(cx, 0); g.lineTo(cx, W); g.moveTo(0, cy); g.lineTo(W, cy); g.stroke();
  g.fillText("N", cx + 4, 12); g.fillText("E", W - 12, cy - 4);
  if (st) {
    [[st.cep50, "rgba(63,167,255,.55)", "原始CEP50"], [fst ? fst.cep50 : 0, "#31c46b", "滤波CEP50"], [st.cep95, "#ffb020", "CEP95"]].forEach(([r, col, t]) => {
      if (!r) return;
      g.strokeStyle = col; g.lineWidth = 1.5; g.beginPath(); g.arc(cx, cy, r * k, 0, 7); g.stroke();
      g.fillStyle = col; g.fillText(t, cx + r * k * .72 + 3, cy + r * k * .72 + 12);
    });
    g.lineWidth = 1;
  }
  // Raw points (blue)
  g.fillStyle = "rgba(63,167,255,.42)";
  d.points.forEach(([e, n]) => g.fillRect(cx + e * k - 1.5, cy - n * k - 1.5, 3, 3));
  // Filtered points (green)
  g.fillStyle = "rgba(49,196,107,.85)";
  (d.flt_points || []).forEach(([e, n]) => g.fillRect(cx + e * k - 1.5, cy - n * k - 1.5, 3, 3));
  // Survey-In center (0,0) gold crosshair
  g.strokeStyle = "#ffb020"; g.lineWidth = 2;
  g.beginPath(); g.moveTo(cx - 6, cy); g.lineTo(cx + 6, cy); g.moveTo(cx, cy - 6); g.lineTo(cx, cy + 6); g.stroke(); g.lineWidth = 1;
  // Latest raw (red) & filtered (bright green) dots
  const last = d.points[d.points.length - 1];
  if (last) { g.fillStyle = "#ff5468"; g.beginPath(); g.arc(cx + last[0] * k, cy - last[1] * k, 4, 0, 7); g.fill(); }
  const flast = (d.flt_points || [])[(d.flt_points || []).length - 1];
  if (flast) { g.fillStyle = "#00ff88"; g.strokeStyle = "#000"; g.beginPath(); g.arc(cx + flast[0] * k, cy - flast[1] * k, 4.5, 0, 7); g.fill(); g.stroke(); }
}

// --------------------------------------------------------------- controls
$$("[data-gnss]").forEach(b => b.onclick = async () => {
  const action = b.dataset.gnss;
  if (action === "cold" && !confirm("冷启动将清除星历和历书数据, 确定?")) return;
  b.disabled = true;
  try {
    const r = await api("/api/gnss", { body: { action } });
    toast(r.results.map(x => `${x.cmd} → ${x.resp.replace(/\n/g, " ")}`).join("\n"), !r.ok);
    if (["cold", "warm", "hot", "on"].includes(action)) restartAt = Date.now() / 1000;
    else { restartAt = null; $("#restartTimer").textContent = ""; }
  } catch (e) { toast(e.message, true); }
  b.disabled = false;
});
$("#btnRec").onclick = async () => {
  try { const r = await api("/api/record", { body: { on: !(lastStatus && lastStatus.recording) } }); toast(r.recording ? "开始录制: " + r.file : "录制已保存: " + (r.file || "")); }
  catch (e) { toast(e.message, true); }
};
$("#btnTrackReset").onclick = () => api("/api/track/reset", { body: {} });

// ------------------------------------------------------------------ NMEA
let nmeaSeq = 0, nmeaPaused = false;
async function pollNmea() {
  if (activeTab === "console" && !nmeaPaused) {
    try {
      const r = await api("/api/nmea?since=" + nmeaSeq);
      if (nmeaSeq === 0 || r.seq < nmeaSeq) $("#nmea").innerHTML = "";
      nmeaSeq = r.seq;
      const f = $("#nmeaFilter").value.trim().toUpperCase(), box = $("#nmea");
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      const html = r.lines.filter(l => !f || l[2].toUpperCase().includes(f)).map(([, t, l]) =>
        `<span class="t">${new Date(t * 1000).toTimeString().slice(0, 8)}</span> ${esc(l)}`).join("\n");
      if (html) box.insertAdjacentHTML("beforeend", (box.innerHTML ? "\n" : "") + html);
      if (box.childNodes.length > 3000) box.innerHTML = box.innerHTML.split("\n").slice(-800).join("\n");
      if (atBottom) box.scrollTop = box.scrollHeight;
    } catch (e) { }
  }
  setTimeout(pollNmea, 1000);
}
$("#btnNmeaPause").onclick = e => { nmeaPaused = !nmeaPaused; e.target.textContent = nmeaPaused ? "继续" : "暂停"; };
$("#btnNmeaClear").onclick = () => $("#nmea").innerHTML = "";

// -------------------------------------------------------------------- AT
const atHist = []; let atIdx = 0;
async function sendAt(cmd) {
  const log = $("#atLog");
  log.insertAdjacentHTML("beforeend", `<span class="c">&gt; ${esc(cmd)}</span>\n`);
  log.scrollTop = log.scrollHeight;
  try {
    const r = await api("/api/at", { body: { cmd } });
    log.insertAdjacentHTML("beforeend", `<span class="${r.ok ? "o" : "e"}">${esc(r.resp)}</span> <span class="t">(${r.ms} ms)</span>\n`);
  } catch (e) { log.insertAdjacentHTML("beforeend", `<span class="e">${esc(e.message)}</span>\n`); }
  log.scrollTop = log.scrollHeight;
}
$("#atForm").onsubmit = e => {
  e.preventDefault(); const v = $("#atInput").value.trim(); if (!v) return;
  atHist.push(v); atIdx = atHist.length; $("#atInput").value = ""; sendAt(v);
};
$("#atInput").onkeydown = e => {
  if (e.key === "ArrowUp" && atIdx > 0) { atIdx--; e.target.value = atHist[atIdx]; e.preventDefault(); }
  if (e.key === "ArrowDown") { atIdx = Math.min(atHist.length, atIdx + 1); e.target.value = atHist[atIdx] || ""; e.preventDefault(); }
};
$$("[data-at]").forEach(b => b.onclick = () => sendAt(b.dataset.at));

// ------------------------------------------------------------------ test
const STATE_TXT = { idle: "空闲", preparing: "准备中...", acquiring: "搜星定位中", observing: "定位观测中", done: "测试完成", aborted: "已中止", error: "错误" };
$("#testForm").onsubmit = async e => {
  e.preventDefault();
  const fd = Object.fromEntries(new FormData(e.target).entries());
  if (fd.mode === "cold" && !confirm("将执行冷启动测试, 确定?")) return;
  try { await api("/api/test/start", { body: fd }); toast("测试已开始"); lastTestLog = 0; } catch (err) { toast(err.message, true); }
};
$("#btnTestStop").onclick = () => api("/api/test/stop", { body: {} });
$("#btnClearResults").onclick = async () => { if (confirm("清空所有历史结果?")) { await api("/api/results", { method: "DELETE" }); loadResults(); } };

let lastTestFinished = null, lastTestLog = 0;
async function pollTest() {
  try {
    const t = await api("/api/test");
    renderTest(t);
    if (t.finished && t.finished !== lastTestFinished) { lastTestFinished = t.finished; if (activeTab === "test") loadResults(); }
  } catch (e) { }
  setTimeout(pollTest, 1000);
}
function renderTest(t) {
  const st = t.state || "idle", p = t.params || {};
  $("#btnTestStart").disabled = t.running; $("#btnTestStop").disabled = !t.running;
  let txt = STATE_TXT[st] || st, pct = 0, cls = t.running ? "run" : "idle";
  if (st === "acquiring") { txt += ` ${fmt(t.elapsed, 0)} s / ${p.ttff_timeout} s`; pct = 100 * (t.elapsed || 0) / p.ttff_timeout; }
  if (st === "observing") { txt += ` ${fmt(t.obs_elapsed, 0)} / ${p.observe} s`; pct = 100 * (t.obs_elapsed || 0) / p.observe; }
  if (["done", "aborted", "error"].includes(st) && t.result) { cls = t.result.pass ? "pass" : "fail"; pct = 100; }
  $("#testState").textContent = txt; $("#testState").className = "teststate " + cls;
  $("#testBar").style.width = Math.min(100, pct) + "%";
  const lv = t.live;
  $("#testLive").textContent = t.running && lv ? `定位:${lv.fix_valid ? "是" : "否"}  卫星 ${lv.sats_used}/${lv.sats_view}  Top4 C/N0 ${fmt(lv.snr_top4, 1)}  HDOP ${fmt(lv.hdop, 2)}` : "";
  const log = t.log || [];
  if (log.length !== lastTestLog) {
    lastTestLog = log.length;
    $("#testLog").textContent = log.map(([ts, m]) => `${new Date(ts * 1000).toTimeString().slice(0, 8)}  ${m}`).join("\n");
    $("#testLog").scrollTop = 1e9;
  }
  $("#testResult").innerHTML = (!t.running && t.result) ? resultHtml(t.result) : "";
}
function resultHtml(r) {
  const rows = (r.checks || []).map(c => `<tr><td>${c.name}</td><td>${typeof c.value === "number" ? c.value.toFixed(2) : (c.value ?? "--")} ${c.unit}</td><td>${c.limit} ${c.unit}</td>
    <td><span class="tag ${c.pass ? "ok" : "bad"}">${c.pass ? "PASS" : "FAIL"}</span></td></tr>`).join("");
  const ps = r.per_sys ? Object.entries(r.per_sys).map(([k, v]) => `${k} ${v.used}/${v.view}`).join(" · ") : "";
  return `<div class="big-result ${r.pass ? "ok" : "bad"}">${r.pass ? "PASS" : "FAIL"}</div>
    <table class="checks"><thead><tr><th>项目</th><th>实测</th><th>标准</th><th>结果</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="muted">首次捕获信号 ${fmt(r.first_signal, 1, " s")} · 最大可见 ${r.sats_view_max ?? "--"} · 最大 C/N0 ${fmt(r.snr_max, 0)} ·
      HDOP ${fmt(r.hdop_avg, 2)} · 3D率 ${fmt(r.fix3d_rate, 0, "%")} · CEP95 ${fmt(r.cep95, 2, " m")}<br>${ps}
      ${r.lat ? `<br>平均位置 ${r.lat.toFixed(7)}, ${r.lon.toFixed(7)}, ${fmt(r.alt, 1, " m")} (σh ${fmt(r.alt_std, 2)} m)` : ""}
      ${r.error ? `<br><span style="color:var(--bad)">${esc(r.error)}</span>` : ""}</p>`;
}
const MODE_TXT = { cold: "冷", warm: "温", hot: "热", none: "无" };
async function loadResults() {
  const rs = await api("/api/results");
  $("#resTable tbody").innerHTML = rs.map(r => `<tr><td>${r.time}</td><td>${esc(r.label || "")}</td><td>${MODE_TXT[r.mode] || r.mode}</td>
    <td><span class="tag ${r.pass ? "ok" : "bad"}">${r.aborted ? "中止" : r.pass ? "PASS" : "FAIL"}</span></td>
    <td>${fmt(r.ttff, 1)}</td><td>${fmt(r.sats_used_avg, 1)}</td><td>${r.sats_view_max ?? "--"}</td><td>${fmt(r.snr_top4_avg, 1)}</td>
    <td>${fmt(r.snr_max, 0)}</td><td>${fmt(r.hdop_avg, 2)}</td><td>${fmt(r.cep50, 2)}</td><td>${fmt(r.cep95, 2)}</td><td>${fmt(r.fix_rate, 0, "%")}</td></tr>`).join("")
    || `<tr><td colspan="13" class="muted">暂无记录</td></tr>`;
}

// ---------------------------------------------------------------- config
let settings = null;
async function loadSettings() {
  settings = await api("/api/settings");
  const f = $("#testForm");
  for (const [k, v] of Object.entries(settings.test || {})) if (f.elements[k]) f.elements[k].value = v;
  $("#atPortLbl").textContent = settings.at_port;
}
async function loadPorts() {
  const ports = await api("/api/ports"), pf = $("#portForm");
  for (const name of ["nmea_port", "at_port"]) {
    const sel = pf.elements[name], cur = settings[name];
    sel.innerHTML = ports.map(p => `<option value="${p.dev}">${p.dev}${p.desc ? " (" + p.desc.replace(/^usb-/, "").slice(0, 34) + ")" : ""}</option>`).join("");
    if (!ports.find(p => p.dev === cur)) sel.insertAdjacentHTML("afterbegin", `<option>${cur}</option>`);
    sel.value = cur;
  }
  pf.elements.baud.value = settings.baud; pf.elements.auto_on.checked = !!settings.auto_on;
}
$("#portForm").onsubmit = async e => {
  e.preventDefault(); const f = e.target;
  try {
    const r = await api("/api/settings", { body: { nmea_port: f.elements.nmea_port.value, at_port: f.elements.at_port.value, baud: f.elements.baud.value, auto_on: f.elements.auto_on.checked } });
    settings = r.settings; $("#atPortLbl").textContent = settings.at_port; toast("已保存");
  } catch (err) { toast(err.message, true); }
};
const CFG_DESC = {
  outport: "NMEA 输出端口", nmeasrc: "AT+QGPSGNMEA 获取 NMEA", gpsnmeatype: "GPS NMEA 类型 (0-31 位掩码)",
  glonassnmeatype: "GLONASS NMEA 类型 (0-7)", galileonmeatype: "Galileo NMEA 类型 (0-1)", beidounmeatype: "BeiDou NMEA 类型 (0-3)",
  gsvextnmeatype: "GSV 扩展 (0-1)", gnssconfig: "星座组合 (4=全星座常开)", dpoenable: "动态省电 DPO (0=关闭)", fixfreq: "定位频率 Hz (1/2/5/10)", autogps: "开机自动启动 GNSS", "1pps": "1PPS 输出",
};
async function loadConfig() {
  $("#cfgForm").innerHTML = '<p class="muted">读取中...</p>';
  try {
    const r = await api("/api/config"), m = r.module;
    $("#moduleName").textContent = m.module || "";
    $("#cfgForm").innerHTML = Object.keys(CFG_DESC).map(k => `<label>${k} <small class="muted">${CFG_DESC[k]}</small>
      <span><input data-k="${k}" value="${esc(m[k] ?? "")}" size="8" style="width:90px"> <button type="button" class="btn xs" data-set="${k}">写入</button></span></label>`).join("")
      + `<p class="muted">GNSS 引擎: ${m.qgps ? "开启" : "关闭"}</p>`;
    $$("[data-set]").forEach(b => b.onclick = () => setCfg({ [b.dataset.set]: $(`[data-k="${b.dataset.set}"]`).value }));
  } catch (e) { $("#cfgForm").innerHTML = `<p class="e">${esc(e.message)}</p>`; }
}
async function setCfg(obj) {
  try {
    const r = await api("/api/config", { body: obj });
    $("#cfgLog").textContent += r.results.map(x => `${x.cmd}\n  → ${x.resp.replace(/\n/g, " ")}`).join("\n") + "\n";
    toast(r.ok ? "写入成功" : "部分写入失败", !r.ok);
  } catch (e) { toast(e.message, true); }
}
$("#btnCfgReload").onclick = loadConfig;
$("#btnAllNmea").onclick = async () => {
  try {
    const r = await api("/api/gnss", { body: { action: "multi" } });
    $("#cfgLog").textContent += r.results.map(x => `${x.cmd}\n  → ${x.resp.replace(/\n/g, " ")}`).join("\n") + "\n";
    toast(r.ok ? "已开启全星座并重启 GNSS 引擎" : "部分命令失败", !r.ok);
    loadConfig();
  } catch (e) { toast(e.message, true); }
};

async function loadLogs() {
  const fs = await api("/api/logs");
  $("#logTable tbody").innerHTML = fs.map(f => `<tr><td><a href="/logs/${encodeURIComponent(f.name)}">${esc(f.name)}</a></td>
    <td>${(f.size / 1024).toFixed(1)} KB</td><td>${new Date(f.mtime * 1000).toLocaleString()}</td>
    <td><button class="btn xs warn" data-del="${esc(f.name)}">删除</button></td></tr>`).join("") || `<tr><td colspan="4" class="muted">暂无录制文件</td></tr>`;
  $$("[data-del]").forEach(b => b.onclick = async () => {
    try { await api("/api/logs/" + encodeURIComponent(b.dataset.del), { method: "DELETE" }); loadLogs(); } catch (e) { toast(e.message, true); }
  });
}

// ------------------------------------------------------------------ boot
loadSettings().catch(() => { });
api("/api/at", { body: { cmd: "ATI" } }).then(r => { $("#moduleName").textContent = r.resp.split("\n").filter(l => l && l !== "OK").join(" "); }).catch(() => { });
pollStatus(); pollTrack(); pollNmea(); pollTest();
