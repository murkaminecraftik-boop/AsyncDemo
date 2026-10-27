// async-media-fetcher — client script.
// Talks to /api/stream (SSE), /api/fetch and /api/compare (JSON).
// No dependencies. Rows are built with createElement (never innerHTML for
// server data) so a weird title from a CDN can't inject markup.

"use strict";

const $ = (sel) => document.querySelector(sel);

/* ---------- formatting ---------- */

const fmtSecs = (v) => `${Number(v).toFixed(2)} s`;
const fmtMB = (b) => `${(b / 1e6).toFixed(1)} MB`;
const clock = () => new Date().toTimeString().slice(0, 8);

function safeParse(e) {
  try { return JSON.parse(e.data); } catch { return null; }
}

/* ---------- dom refs ---------- */

const els = {
  status: $("#statusline"),
  rows: $("#rows"),
  runNote: $("#run-note"),
  console: $("#console"),
  run: $("#run"),
  stop: $("#stop"),
  modehint: $("#modehint"),
  timeoutPair: $("#timeout-pair"),
  timeoutSel: $("#timeout"),
  metricsBox: $("#metrics-box"),
  metricsNote: $("#metrics-note"),
  metricsBody: $("#metrics-body"),
  metricsBars: $("#metrics-bars"),
  compareBox: $("#compare-box"),
  compareBody: $("#compare-body"),
  compareVerdict: $("#compare-verdict"),
  timeoutNote: $("#timeout-note"),
  form: $("#form"),
};

const MODE_HINTS = {
  stream: "Results arrive over SSE the moment each task finishes (asyncio.as_completed).",
  gather: "Waits for the whole batch, then returns a single JSON response (asyncio.gather). No per-item progress — that is the point of gather().",
  timeout: "Tasks that take longer than the timeout get cancelled (asyncio.wait + cancel). Try 3 s with videos in sim mode.",
  sync: "Baseline: one blocking download after another, no concurrency.",
  compare: "Downloads the same batch twice — blocking first, then concurrent — and reports both timings. Capped at 4 items.",
};

/* ---------- state ---------- */

const state = {
  running: false,
  es: null,          // EventSource, when in an SSE mode
  abort: null,       // AbortController, when in a JSON mode
  t0: 0,
  total: 0,
  done: 0,
  bytes: new Map(),  // item_id -> downloaded bytes, for the status line
  timer: null,
};

/* ---------- console ---------- */

function log(kind, msg) {
  const box = els.console;
  const line = document.createElement("div");
  if (kind) line.className = kind;
  const ts = document.createElement("span");
  ts.className = "ts";
  ts.textContent = clock();
  line.append(ts, document.createTextNode(msg));
  // stick to the bottom unless the user scrolled up to read something
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
  box.appendChild(line);
  while (box.childElementCount > 500) box.firstChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
}

$("#clear").addEventListener("click", () => els.console.replaceChildren());

/* ---------- status line ---------- */

function setStatus(text, busy) {
  els.status.textContent = "> " + text;
  els.status.classList.toggle("busy", !!busy);
}

function byteSum() {
  let n = 0;
  state.bytes.forEach((v) => { n += v; });
  return n;
}

function startTicker() {
  stopTicker();
  state.timer = setInterval(() => {
    const secs = (performance.now() - state.t0) / 1000;
    const parts = ["running"];
    if (state.total) parts.push(`${state.done}/${state.total} done`);
    parts.push(fmtMB(byteSum()), `${secs.toFixed(1)} s`);
    setStatus(parts.join(" · "), true);
  }, 100);
}

function stopTicker() {
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
}

function setRunning(v) {
  state.running = v;
  els.run.disabled = v;
  els.stop.disabled = !v;
  els.run.textContent = v ? "Running…" : "Start";
}

function finish(msg) {
  stopTicker();
  const secs = (performance.now() - state.t0) / 1000;
  const parts = ["finished"];
  if (state.total) parts.push(`${state.done}/${state.total}`);
  parts.push(fmtMB(byteSum()), `${secs.toFixed(1)} s`);
  setStatus(msg || parts.join(" · "), false);
  setRunning(false);
}

/* ---------- result rows ---------- */

function addRows(items) {
  els.rows.replaceChildren();
  items.forEach((it, i) => {
    const row = document.createElement("div");
    row.className = "row";
    row.id = "row-" + it.item_id;

    const head = document.createElement("div");
    head.className = "row-head";

    const idx = document.createElement("span");
    idx.className = "idx";
    idx.textContent = String(i + 1).padStart(2, "0");

    const title = document.createElement("span");
    title.className = "title";
    title.textContent = it.title;

    const kind = document.createElement("span");
    kind.className = "kindtag";
    kind.textContent = it.kind;

    const stat = document.createElement("span");
    stat.className = "stat";
    stat.textContent = "queued";

    head.append(idx, title, kind, stat);

    const track = document.createElement("div");
    track.className = "track";
    const fill = document.createElement("div");
    fill.className = "fill";
    track.appendChild(fill);

    const media = document.createElement("div");
    media.className = "row-media";

    row.append(head, track, media);
    els.rows.appendChild(row);
  });
}

function rowOf(itemId) { return document.getElementById("row-" + itemId); }

function setProgress(itemId, downloaded, total) {
  const row = rowOf(itemId);
  if (!row) return;
  state.bytes.set(itemId, downloaded);
  const pct = total > 0 ? Math.min(100, (downloaded / total) * 100) : 0;
  row.querySelector(".fill").style.width = pct.toFixed(1) + "%";
  row.querySelector(".stat").textContent = total > 0
    ? `${pct.toFixed(0)}% · ${fmtMB(downloaded)} / ${fmtMB(total)}`
    : fmtMB(downloaded);
}

function completeRow(r) {
  state.done += 1;
  state.bytes.set(r.item_id, r.size_bytes);
  const row = rowOf(r.item_id);
  if (!row) return;
  const stat = row.querySelector(".stat");
  row.querySelector(".fill").style.width = "100%";
  if (r.ok) {
    row.classList.add("done");
    stat.textContent = `done in ${fmtSecs(r.elapsed)} · ${fmtMB(r.size_bytes)}`;
    const save = document.createElement("a");
    save.href = r.media_url + "?download=1";
    save.textContent = "save";
    stat.appendChild(save);
    const media = row.querySelector(".row-media");
    const ctype = r.content_type || "";
    if (ctype.startsWith("image/")) {
      const img = document.createElement("img");
      img.src = r.media_url;
      img.alt = r.title;
      img.loading = "lazy";
      media.appendChild(img);
    } else if (ctype.startsWith("video/")) {
      const v = document.createElement("video");
      v.src = r.media_url;
      v.controls = true;
      v.preload = "metadata";
      media.appendChild(v);
    } else {
      media.textContent = `no inline preview (${ctype || "unknown type"}) — use the save link`;
    }
  } else {
    row.classList.add("fail");
    stat.textContent = `failed — ${r.error || "unknown error"}`;
  }
}

function cancelRow(d) {
  state.done += 1;
  const row = rowOf(d.item_id);
  if (!row) return;
  row.classList.add("cancelled");
  row.querySelector(".fill").style.width = "100%";
  row.querySelector(".stat").textContent = "cancelled (timeout)";
}

function failRow(d) {
  state.done += 1;
  const row = rowOf(d.item_id);
  if (!row) return;
  row.classList.add("fail");
  row.querySelector(".fill").style.width = "100%";
  row.querySelector(".stat").textContent = `failed — ${d.message || "error"}`;
}

/* ---------- metrics / compare ---------- */

function hbar(label, secs, max, seq) {
  const wrap = document.createElement("div");
  wrap.className = "hrow" + (seq ? " seq" : "");
  const lab = document.createElement("div");
  lab.className = "lab";
  const l = document.createElement("span");
  l.textContent = label;
  const v = document.createElement("span");
  v.textContent = fmtSecs(secs);
  lab.append(l, v);
  const tr = document.createElement("div");
  tr.className = "tr";
  const fl = document.createElement("div");
  fl.className = "fl";
  fl.style.width = (max > 0 ? Math.max(1.5, (secs / max) * 100) : 0) + "%";
  tr.appendChild(fl);
  wrap.append(lab, tr);
  return wrap;
}

function renderMetrics(m) {
  els.metricsBox.hidden = false;
  els.metricsNote.textContent = m.mode + (m.simulated ? " · sim data" : "");
  const rows = [
    ["Completed", `${m.completed} of ${m.total}` + (m.cancelled ? ` (${m.cancelled} cancelled)` : "")],
    ["Total time", fmtSecs(m.elapsed)],
    ["Sequential estimate", fmtSecs(m.sequential_estimate)],
    ["Time saved", fmtSecs(m.time_saved)],
    ["Throughput", `${m.throughput.toFixed(2)} items/s`],
    ["Downloaded", fmtMB(m.bytes_received)],
  ];
  els.metricsBody.replaceChildren(...rows.map(([k, v]) => {
    const tr = document.createElement("tr");
    const td1 = document.createElement("td");
    td1.textContent = k;
    const td2 = document.createElement("td");
    td2.className = "num";
    td2.textContent = v;
    tr.append(td1, td2);
    return tr;
  }));
  const max = Math.max(m.elapsed, m.sequential_estimate, 0.01);
  els.metricsBars.replaceChildren(
    hbar("concurrent (actual)", m.elapsed, max, false),
    hbar("if downloaded one by one", m.sequential_estimate, max, true),
  );
  log("", `metrics: ${m.mode} · ${m.completed}/${m.total} · elapsed ${m.elapsed.toFixed(2)}s · saved ${m.time_saved.toFixed(2)}s · ${m.throughput.toFixed(2)} items/s`);
}

function renderCompare(d) {
  const a = d.async.metrics, s = d.sync.metrics;
  els.compareBox.hidden = false;
  const speedup = a.elapsed > 0 ? s.elapsed / a.elapsed : 0;

  const mk = (cells) => {
    const tr = document.createElement("tr");
    cells.forEach((c, i) => {
      const td = document.createElement("td");
      td.className = i > 0 ? "num" : "";
      td.textContent = typeof c === "object" ? c.v : c;
      if (typeof c === "object" && c.cls) td.classList.add(...c.cls.split(" "));
      tr.appendChild(td);
    });
    return tr;
  };

  els.compareBody.replaceChildren(
    mk(["Total time", fmtSecs(s.elapsed), fmtSecs(a.elapsed)]),
    mk(["Throughput", `${s.throughput.toFixed(2)} items/s`, `${a.throughput.toFixed(2)} items/s`]),
    mk(["Downloaded", fmtMB(s.bytes_received), fmtMB(a.bytes_received)]),
    mk(["Time saved vs sequential", fmtSecs(s.time_saved), { v: fmtSecs(a.time_saved), cls: "pos" }]),
  );

  els.compareVerdict.textContent = speedup > 1
    ? `The concurrent pass finished ${speedup.toFixed(1)}× faster on the same files.`
    : "No speedup this time — with tiny files or concurrency 1 the difference can vanish.";
  log("ok", `sync ${s.elapsed.toFixed(2)}s vs async ${a.elapsed.toFixed(2)}s (${speedup.toFixed(1)}x)`);
}

/* ---------- run modes ---------- */

function resetRun(note) {
  state.done = 0;
  state.total = 0;
  state.bytes = new Map();
  state.t0 = performance.now();
  els.runNote.textContent = note;
  els.metricsBox.hidden = true;
  els.compareBox.hidden = true;
  els.timeoutNote.hidden = true;
}

// SSE. One thing to remember: EventSource reconnects on its own unless we
// close it — so every exit path (metrics, error, Stop) must call close().
function openStream(mode) {
  const p = new URLSearchParams(formParams());
  p.set("mode", mode);
  const es = new EventSource("/api/stream?" + p.toString());
  state.es = es;
  let closed = false;
  const close = () => { if (!closed) { closed = true; es.close(); } };

  es.addEventListener("start", (e) => {
    const d = safeParse(e);
    if (!d) return;
    state.total = d.total;
    addRows(d.items);
    log("", `--- ${d.title} | ${d.total} items | ${d.simulated ? "sim" : "real"} mode ---`);
  });
  es.addEventListener("log", (e) => {
    const d = safeParse(e);
    if (d) log("", d.msg);
  });
  es.addEventListener("progress", (e) => {
    const d = safeParse(e);
    if (d) setProgress(d.item_id, d.downloaded, d.total);
  });
  es.addEventListener("item", (e) => {
    const d = safeParse(e);
    if (!d) return;
    completeRow(d);
    if (d.ok) log("ok", `Done ${d.title} | ${fmtSecs(d.elapsed)} | ${fmtMB(d.size_bytes)}`);
    else log("err", `Failed ${d.title} | ${d.error}`);
  });
  es.addEventListener("fail", (e) => {
    const d = safeParse(e);
    if (!d) return;
    failRow(d);
    log("err", `Failed ${d.title} | ${d.message}`);
  });
  es.addEventListener("cancelled", (e) => {
    const d = safeParse(e);
    if (!d) return;
    cancelRow(d);
    log("warn", `Cancelled ${d.title}`);
  });
  es.addEventListener("timeout_info", (e) => {
    const d = safeParse(e);
    if (!d) return;
    els.timeoutNote.hidden = false;
    els.timeoutNote.textContent =
      `Timeout of ${d.timeout} s hit: ${d.completed} item(s) finished before the deadline, ${d.cancelled} cancelled.`;
    log("warn", `timeout ${d.timeout}s: completed ${d.completed}, cancelled ${d.cancelled}`);
  });
  es.addEventListener("metrics", (e) => {
    const d = safeParse(e);
    close();
    if (d) renderMetrics(d);
    log("ok", "batch finished");
    finish();
  });
  es.onerror = () => {
    if (closed) return;
    close();
    log("err", "connection lost — stream ended early");
    finish("error · connection lost");
  };
}

async function runGather() {
  state.abort = new AbortController();
  try {
    const resp = await fetch("/api/fetch?" + new URLSearchParams(formParams()).toString(),
                             { signal: state.abort.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const d = await resp.json();
    state.total = d.items.length;
    if (d.fallback_simulated) log("warn", "network unreachable — server fell back to sim mode");
    // gather gives no progress: rows go straight from "queued" to done
    addRows(d.items.map((r) => ({ item_id: r.item_id, title: r.title, kind: r.kind })));
    d.items.forEach(completeRow);
    renderMetrics(d.metrics);
    finish();
  } catch (err) {
    if (err.name !== "AbortError") {
      log("err", String(err));
      finish("error · " + (err.message || "request failed"));
    }
  }
}

async function runCompare() {
  state.abort = new AbortController();
  try {
    setStatus("running · sequential pass first, then concurrent (this takes a while)", true);
    const resp = await fetch("/api/compare?" + new URLSearchParams(formParams()).toString(),
                             { signal: state.abort.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const d = await resp.json();
    state.total = d.async.items.length;
    addRows(d.async.items.map((r) => ({ item_id: r.item_id, title: r.title, kind: r.kind })));
    d.async.items.forEach(completeRow);
    renderCompare(d);
    finish();
  } catch (err) {
    if (err.name !== "AbortError") {
      log("err", String(err));
      finish("error · " + (err.message || "request failed"));
    }
  }
}

/* ---------- wiring ---------- */

function formParams() {
  return {
    kind: document.querySelector('input[name="kind"]:checked').value,
    count: $("#count").value,
    concurrency: $("#conc").value,
    timeout: $("#timeout").value,
  };
}

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.running) return;
  const mode = document.querySelector('input[name="mode"]:checked').value;
  const p = formParams();
  resetRun(`${p.kind} · ${p.count} items · concurrency ${p.concurrency}` +
           (mode === "timeout" ? ` · timeout ${p.timeout} s` : ""));
  setRunning(true);
  startTicker();
  if (mode === "gather") {
    log("info", "await asyncio.gather(…) — one response when everything is done");
    runGather();
  } else if (mode === "compare") {
    log("info", "compare: same batch, blocking first, then concurrent");
    runCompare();
  } else {
    openStream(mode);
  }
});

els.stop.addEventListener("click", () => {
  if (state.es) state.es.close();
  if (state.abort) state.abort.abort();
  log("warn", "stopped by user (the server may still finish the batch)");
  finish("stopped by user");
});

(function fillSelects() {
  const count = $("#count"), conc = $("#conc"), to = $("#timeout");
  for (let i = 2; i <= 8; i++) count.add(new Option(String(i), String(i)));
  count.value = "4";
  for (let i = 1; i <= 8; i++) conc.add(new Option(String(i), String(i)));
  conc.value = "4";
  [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 15].forEach((v) => to.add(new Option(v.toFixed(1), String(v))));
  to.value = "5";
})();

function syncModeHint() {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  els.modehint.textContent = MODE_HINTS[mode] || "";
  const needTimeout = mode === "timeout";
  els.timeoutSel.disabled = !needTimeout;
  els.timeoutPair.classList.toggle("off", !needTimeout);
}
document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", syncModeHint));
syncModeHint();

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !state.running) els.form.requestSubmit();
  if (e.key === "Escape" && state.running) els.stop.click();
});

setStatus("idle — configure a batch and press Start", false);
log("info", "ready");
