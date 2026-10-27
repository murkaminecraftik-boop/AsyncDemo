"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = { es: null, abort: null, running: false };

const fmtSecs = (v) => `${Number(v).toFixed(2)}s`;
const fmtMB = (b) => `${(b / 1e6).toFixed(1)} MB`;

function log(kind, msg) {
  const box = $("#console");
  const line = document.createElement("div");
  line.className = "log-line " + (kind || "");
  line.textContent = msg;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function params() {
  return new URLSearchParams({
    kind: $('input[name="kind"]:checked').value,
    count: $("#count").value,
    concurrency: $("#concurrency").value,
    timeout: $("#timeout").value,
  });
}

function setRunning(v) {
  state.running = v;
  $("#run").disabled = v;
  $("#stop").disabled = !v;
}

function resetUI() {
  $("#console").innerHTML = "";
  $("#cards").innerHTML = "";
  $("#metrics-wrap").hidden = true;
  $("#compare-wrap").hidden = true;
  $("#timeout-banner").hidden = true;
}

function skeletons(items) {
  $("#cards").innerHTML = items.map((it) => `
    <article class="card pending" id="card-${it.item_id}">
      <div class="card-media"><div class="spinner"></div></div>
      <div class="card-body">
        <h3>${it.title}</h3>
        <div class="meta">
          <span class="chip ${it.kind}">${it.kind}</span>
          ${it.size_hint_mb ? `<span>~${Number(it.size_hint_mb).toFixed(1)} MB</span>` : ""}
          ${it.sim_delay ? `<span>${Number(it.sim_delay).toFixed(1)}s</span>` : ""}
        </div>
        <div class="status">pending</div>
      </div>
    </article>`).join("");
}

function fillCard(r) {
  const card = document.getElementById("card-" + r.item_id);
  if (!card) return;
  card.classList.remove("pending");
  const box = card.querySelector(".card-media");
  const status = card.querySelector(".status");
  if (r.ok && r.media_url) {
    const ctype = r.content_type || "";
    if (ctype.startsWith("image/")) {
      box.innerHTML = `<img src="${r.media_url}" alt="${r.title}">`;
    } else if (ctype.startsWith("video/")) {
      box.innerHTML = `<video controls preload="metadata" src="${r.media_url}"></video>`;
    } else {
      box.innerHTML = `<div class="no-preview">no preview</div>`;
    }
    card.classList.add("done");
    status.innerHTML =
      `${fmtSecs(r.elapsed)} · ${fmtMB(r.size_bytes)}` +
      ` · <a href="${r.media_url}?download=1">download</a>`;
  } else {
    card.classList.add("fail");
    box.innerHTML = `<div class="no-preview">error</div>`;
    status.textContent = r.error || "failed";
  }
}

function markCancelled(d) {
  const card = document.getElementById("card-" + d.item_id);
  if (!card) return;
  card.classList.remove("pending");
  card.classList.add("cancelled");
  card.querySelector(".card-media").innerHTML = `<div class="no-preview">cancelled</div>`;
  card.querySelector(".status").textContent = "timeout";
}

function markError(d) {
  const card = document.getElementById("card-" + d.item_id);
  if (!card) return;
  card.classList.remove("pending");
  card.classList.add("fail");
  card.querySelector(".card-media").innerHTML = `<div class="no-preview">error</div>`;
  card.querySelector(".status").textContent = d.message || "error";
}

function renderMetrics(m) {
  $("#metrics-wrap").hidden = false;
  $("#metrics-body").innerHTML = `
    <tr><td>Mode</td><td>${m.mode}${m.simulated ? " · sim" : ""}</td></tr>
    <tr><td>Done</td><td>${m.completed}/${m.total}${m.cancelled ? ` · cancelled ${m.cancelled}` : ""}</td></tr>
    <tr><td>Elapsed</td><td>${fmtSecs(m.elapsed)}</td></tr>
    <tr><td>Sequential estimate</td><td>${fmtSecs(m.sequential_estimate)}</td></tr>
    <tr><td>Saved vs sequential</td><td class="good">${fmtSecs(m.time_saved)}</td></tr>
    <tr><td>Throughput</td><td>${m.throughput.toFixed(2)} /s</td></tr>
    <tr><td>Bytes</td><td>${fmtMB(m.bytes_received)}</td></tr>`;
  log("info", `metrics ${m.mode} ${m.completed}/${m.total} ${m.elapsed.toFixed(2)}s saved ${m.time_saved.toFixed(2)}s`);
}

function renderCompare(d) {
  $("#compare-wrap").hidden = false;
  const a = d.async.metrics, s = d.sync.metrics;
  const speedup = s.elapsed > 0 ? (s.elapsed / Math.max(a.elapsed, 1e-6)).toFixed(1) : "—";
  const rows = [
    ["Mode", s.mode, a.mode, ""],
    ["Done", `${s.completed}/${s.total}`, `${a.completed}/${a.total}`, ""],
    ["Elapsed", fmtSecs(s.elapsed), fmtSecs(a.elapsed), `×${speedup}`],
    ["Sequential estimate", fmtSecs(s.sequential_estimate), fmtSecs(a.sequential_estimate), ""],
    ["Throughput", s.throughput.toFixed(2), a.throughput.toFixed(2), "/s"],
    ["Bytes", fmtMB(s.bytes_received), fmtMB(a.bytes_received), ""],
  ];
  $("#compare-body").innerHTML = rows.map((r) =>
    `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td><td class="good">${r[3]}</td></tr>`).join("");
  skeletons(d.async.items.map((r) => ({
    item_id: r.item_id, title: r.title, kind: r.kind, size_hint_mb: 0, sim_delay: 0,
  })));
  d.async.items.forEach(fillCard);
}

function runStream(mode) {
  const p = params();
  p.set("mode", mode);
  const es = new EventSource("/api/stream?" + p.toString());
  state.es = es;

  es.addEventListener("start", (e) => {
    const d = JSON.parse(e.data);
    log("info", `${d.mode} · ${d.simulated ? "sim" : "real"} · ${d.total} tasks`);
    skeletons(d.items);
  });
  es.addEventListener("log", (e) => log("", JSON.parse(e.data).msg));
  es.addEventListener("item", (e) => {
    const d = JSON.parse(e.data);
    fillCard(d);
    log("ok", `done ${d.title} ${Number(d.elapsed).toFixed(2)}s ${fmtMB(d.size_bytes)}`);
  });
  es.addEventListener("fail", (e) => {
    const d = JSON.parse(e.data);
    markError(d);
    log("err", `fail ${d.title}: ${d.message}`);
  });
  es.addEventListener("cancelled", (e) => {
    const d = JSON.parse(e.data);
    markCancelled(d);
    log("warn", `cancelled ${d.title}`);
  });
  es.addEventListener("timeout_info", (e) => {
    const d = JSON.parse(e.data);
    const b = $("#timeout-banner");
    b.hidden = false;
    b.textContent = `Timeout ${d.timeout}s · done ${d.completed} · cancelled ${d.cancelled}`;
  });
  es.addEventListener("metrics", (e) => {
    renderMetrics(JSON.parse(e.data));
    log("ok", "done");
    es.close();
    setRunning(false);
  });
  es.onerror = () => {
    if (!state.running) return;
    log("err", "connection closed");
    es.close();
    setRunning(false);
  };
  setRunning(true);
}

async function runGather() {
  setRunning(true);
  const ctl = new AbortController();
  state.abort = ctl;
  try {
    log("info", "gather…");
    const resp = await fetch("/api/fetch?" + params().toString(), { signal: ctl.signal });
    const d = await resp.json();
    if (d.fallback_simulated) log("warn", "fallback to sim");
    skeletons(d.items.map((r) => ({
      item_id: r.item_id, title: r.title, kind: r.kind, size_hint_mb: 0, sim_delay: 0,
    })));
    d.items.forEach(fillCard);
    renderMetrics(d.metrics);
    log("ok", `done ${d.metrics.completed}/${d.metrics.total} ${fmtSecs(d.metrics.elapsed)}`);
  } catch (err) {
    if (err.name !== "AbortError") log("err", String(err));
  }
  setRunning(false);
}

async function runCompare() {
  setRunning(true);
  const ctl = new AbortController();
  state.abort = ctl;
  try {
    log("info", "compare sync vs async…");
    const resp = await fetch("/api/compare?" + params().toString(), { signal: ctl.signal });
    const d = await resp.json();
    log("ok", `sync ${d.sync.metrics.elapsed.toFixed(2)}s · async ${d.async.metrics.elapsed.toFixed(2)}s`);
    renderCompare(d);
  } catch (err) {
    if (err.name !== "AbortError") log("err", String(err));
  }
  setRunning(false);
}

$("#run").addEventListener("click", () => {
  const mode = $('input[name="mode"]:checked').value;
  resetUI();
  if (mode === "gather") runGather();
  else if (mode === "compare") runCompare();
  else runStream(mode);
});

$("#stop").addEventListener("click", () => {
  if (state.es) state.es.close();
  if (state.abort) state.abort.abort();
  setRunning(false);
  log("warn", "stopped");
});

$$('input[name="mode"]').forEach((r) => r.addEventListener("change", () => {
  $("#timeout-row").hidden = $('input[name="mode"]:checked').value !== "timeout";
}));

["count", "concurrency", "timeout"].forEach((id) => {
  const input = $("#" + id);
  const out = $("#" + id + "-val");
  out.textContent = input.value;
  input.addEventListener("input", () => { out.textContent = input.value; });
});
