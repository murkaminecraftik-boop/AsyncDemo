/* Async Media Fetcher — клиентская логика: SSE-поток, gather, сравнение */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = { es: null, abort: null, running: false };

/* ---------- утилиты ---------- */

const fmtSecs = (v) => `${Number(v).toFixed(2)} с`;
const fmtMB = (b) => `${(b / 1e6).toFixed(1)} МБ`;

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

/* ---------- карточки ---------- */

function skeletons(items) {
  $("#cards").innerHTML = items.map((it) => `
    <article class="card pending" id="card-${it.item_id}">
      <div class="card-media"><div class="spinner"></div></div>
      <div class="card-body">
        <h3>${it.title}</h3>
        <div class="meta">
          <span class="chip ${it.kind}">${it.kind === "gif" ? "гифка" : "видео"}</span>
          ${it.size_hint_mb ? `<span>~${Number(it.size_hint_mb).toFixed(1)} МБ</span>` : ""}
          ${it.sim_delay ? `<span>planned ${Number(it.sim_delay).toFixed(1)}s</span>` : ""}
        </div>
        <div class="status">ожидание…</div>
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
      box.innerHTML = `<div class="no-preview">нет превью</div>`;
    }
    card.classList.add("done");
    status.innerHTML =
      `готово за ${fmtSecs(r.elapsed)} · ${fmtMB(r.size_bytes)}` +
      ` · <a href="${r.media_url}?download=1">скачать</a>`;
  } else {
    card.classList.add("fail");
    box.innerHTML = `<div class="no-preview">ошибка</div>`;
    status.textContent = r.error || "ошибка загрузки";
  }
}

function markCancelled(d) {
  const card = document.getElementById("card-" + d.item_id);
  if (!card) return;
  card.classList.remove("pending");
  card.classList.add("cancelled");
  card.querySelector(".card-media").innerHTML = `<div class="no-preview">⏱ отменено</div>`;
  card.querySelector(".status").textContent = "отменено по таймауту";
}

function markError(d) {
  const card = document.getElementById("card-" + d.item_id);
  if (!card) return;
  card.classList.remove("pending");
  card.classList.add("fail");
  card.querySelector(".card-media").innerHTML = `<div class="no-preview">ошибка</div>`;
  card.querySelector(".status").textContent = d.message || "ошибка";
}

/* ---------- метрики ---------- */

function renderMetrics(m) {
  $("#metrics-wrap").hidden = false;
  $("#metrics-body").innerHTML = `
    <tr><td>Режим</td><td>${m.mode}${m.simulated ? " · демо-данные" : ""}</td></tr>
    <tr><td>Выполнено</td><td>${m.completed} из ${m.total}${m.cancelled ? ` · отменено ${m.cancelled}` : ""}</td></tr>
    <tr><td>Общее время</td><td>${fmtSecs(m.elapsed)}</td></tr>
    <tr><td>Последовательная оценка</td><td>${fmtSecs(m.sequential_estimate)}</td></tr>
    <tr><td>Сэкономлено versus последовательный</td><td class="good">${fmtSecs(m.time_saved)}</td></tr>
    <tr><td>Пропускная способность</td><td>${m.throughput.toFixed(2)} задач/с</td></tr>
    <tr><td>Получено байт</td><td>${fmtMB(m.bytes_received)}</td></tr>`;
  log("info", `Metrics: ${m.mode} | ${m.completed}/${m.total} | ` +
      `elapsed ${m.elapsed.toFixed(2)}s | saved ${m.time_saved.toFixed(2)}s | ` +
      `throughput ${m.throughput.toFixed(2)}`);
}

function renderCompare(d) {
  const wrap = $("#compare-wrap");
  wrap.hidden = false;
  const a = d.async.metrics, s = d.sync.metrics;
  const speedup = s.elapsed > 0 ? (s.elapsed / Math.max(a.elapsed, 1e-6)).toFixed(1) : "—";
  const rows = [
    ["Режим", s.mode, a.mode, ""],
    ["Выполнено", `${s.completed}/${s.total}`, `${a.completed}/${a.total}`, ""],
    ["Общее время", fmtSecs(s.elapsed), fmtSecs(a.elapsed), `×${speedup} быстрее`],
    ["Последовательная оценка", fmtSecs(s.sequential_estimate), fmtSecs(a.sequential_estimate), ""],
    ["Пропускная способность", `${s.throughput.toFixed(2)}`, `${a.throughput.toFixed(2)}`, "задач/с"],
    ["Получено байт", fmtMB(s.bytes_received), fmtMB(a.bytes_received), ""],
  ];
  $("#compare-body").innerHTML = rows.map((r) =>
    `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td><td class="good">${r[3]}</td></tr>`).join("");
  skeletons(d.async.items.map((r) => ({
    item_id: r.item_id, title: r.title, kind: r.kind, size_hint_mb: 0, sim_delay: 0,
  })));
  d.async.items.forEach(fillCard);
}

/* ---------- запуск режимов ---------- */

function runStream(mode) {
  const p = params();
  p.set("mode", mode);
  const es = new EventSource("/api/stream?" + p.toString());
  state.es = es;

  es.addEventListener("start", (e) => {
    const d = JSON.parse(e.data);
    log("info", `режим: ${d.mode} · ${d.simulated ? "демо-данные" : "реальные загрузки"} · всего ${d.total}`);
    skeletons(d.items);
  });
  es.addEventListener("log", (e) => log("", JSON.parse(e.data).msg));
  es.addEventListener("item", (e) => {
    const d = JSON.parse(e.data);
    fillCard(d);
    log("ok", `Done ${d.title} | actual wait ${Number(d.elapsed).toFixed(2)}s | ${fmtMB(d.size_bytes)}`);
  });
  es.addEventListener("fail", (e) => {
    const d = JSON.parse(e.data);
    markError(d);
    log("err", `Ошибка: ${d.title} — ${d.message}`);
  });
  es.addEventListener("cancelled", (e) => {
    const d = JSON.parse(e.data);
    markCancelled(d);
    log("warn", `Cancelled ${d.title}`);
  });
  es.addEventListener("timeout_info", (e) => {
    const d = JSON.parse(e.data);
    const b = $("#timeout-banner");
    b.hidden = false;
    b.textContent = `Таймаут ${d.timeout} с: выполнено до дедлайна ${d.completed}, ` +
      `отменено после ${d.cancelled}`;
  });
  es.addEventListener("metrics", (e) => {
    renderMetrics(JSON.parse(e.data));
    log("ok", "Готово.");
    es.close();
    setRunning(false);
  });
  es.onerror = () => {
    if (!state.running) return;
    log("err", "Соединение закрыто сервером");
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
    log("info", "await asyncio.gather(…) — ждём ВСЕ задачи, потом один ответ…");
    const resp = await fetch("/api/fetch?" + params().toString(), { signal: ctl.signal });
    const d = await resp.json();
    if (d.fallback_simulated) log("warn", "Сеть недоступна — автоматически перешли в демо-режим");
    skeletons(d.items.map((r) => ({
      item_id: r.item_id, title: r.title, kind: r.kind, size_hint_mb: 0, sim_delay: 0,
    })));
    d.items.forEach(fillCard);
    renderMetrics(d.metrics);
    log("ok", `Готово: ${d.metrics.completed}/${d.metrics.total} за ${fmtSecs(d.metrics.elapsed)}`);
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
    log("info", "Sync vs Async: сначала качаем по очереди, затем параллельно (это долго)…");
    const resp = await fetch("/api/compare?" + params().toString(), { signal: ctl.signal });
    const d = await resp.json();
    log("ok", `Sync: ${d.sync.metrics.elapsed.toFixed(2)}s | Async: ${d.async.metrics.elapsed.toFixed(2)}s`);
    renderCompare(d);
  } catch (err) {
    if (err.name !== "AbortError") log("err", String(err));
  }
  setRunning(false);
}

/* ---------- события контролов ---------- */

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
  log("warn", "Остановлено пользователем");
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
