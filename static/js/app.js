/* ============================================================
   Async Media Lab — client logic
   Reads the NDJSON event stream from POST /api/run and renders:
   live console, delivered media, metrics, comparison, history.
   ============================================================ */

(function () {
  "use strict";

  // ---------- helpers ----------
  const $ = (id) => document.getElementById(id);

  const consoleEl = $("console");
  const galleryEl = $("gallery");
  const galleryEmpty = $("gallery-empty");
  const metricsCards = $("metrics-cards");
  const comparisonBody = $("comparison-body");
  const historyBody = $("history-body");
  const runBtn = $("run-btn");
  const stopBtn = $("stop-btn");
  const progressBar = $("progress-bar");
  const progressEl = $("progress");
  const progressLabel = $("progress-label");
  const selCount = $("sel-count");
  const selEstimate = $("sel-estimate");
  const timeoutRow = $("timeout-row");
  const timeoutRange = $("timeout-range");
  const timeoutValue = $("timeout-value");
  const galleryHint = $("gallery-hint");

  const MODE_LABELS = {
    sync: "Sequential",
    gather: "Gather",
    stream: "Streaming",
    timeout: "Timeout",
  };

  const state = {
    running: false,
    controller: null,
    totalTasks: 0,
    settledTasks: 0,
    mode: "gather",
    bufferingGather: false,
    gatherBuffer: [],
    sessionRuns: [],
  };

  function fmtS(ms) {
    return (ms / 1000).toFixed(2) + "s";
  }

  // ---------- console ----------
  function logLine(text, cls, t) {
    if (consoleEl.querySelector(".console-hint")) {
      consoleEl.innerHTML = "";
    }
    const line = document.createElement("div");
    line.className = "console-line" + (cls ? " " + cls : "");
    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = t != null ? "[" + (t / 1000).toFixed(2) + "s]" : "[----]";
    line.appendChild(ts);
    line.appendChild(document.createTextNode(text));
    consoleEl.appendChild(line);
    while (consoleEl.children.length > 400) {
      consoleEl.removeChild(consoleEl.firstChild);
    }
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }

  $("console-clear").addEventListener("click", () => {
    consoleEl.innerHTML = "";
    logLine("console cleared", "console-hint");
  });

  // ---------- catalog selection ----------
  const cards = Array.from(document.querySelectorAll(".media-card"));
  const checks = cards.map((c) => c.querySelector(".media-check"));

  checks.forEach((chk) => chk.addEventListener("change", updateSelectionSummary));

  function selectedKeys() {
    return checks.filter((c) => c.checked).map((c) => c.value);
  }

  function updateSelectionSummary() {
    const sel = checks.filter((c) => c.checked);
    selCount.textContent = String(sel.length);
    const totalDelay = sel.reduce((sum, c) => {
      const card = c.closest(".media-card");
      return sum + Number(card.dataset.delay);
    }, 0);
    selEstimate.textContent = (totalDelay / 1000).toFixed(1) + "s";
  }

  function selectByKind(kind) {
    checks.forEach((c) => {
      const card = c.closest(".media-card");
      c.checked = kind ? card.dataset.kind === kind : true;
    });
    updateSelectionSummary();
  }

  $("select-gifs").addEventListener("click", () => selectByKind("gif"));
  $("select-videos").addEventListener("click", () => selectByKind("video"));
  $("select-all").addEventListener("click", () => selectByKind(null));
  $("select-none").addEventListener("click", () => {
    checks.forEach((c) => (c.checked = false));
    updateSelectionSummary();
  });

  updateSelectionSummary();

  // ---------- mode + timeout ----------
  const modeRadios = Array.from(document.querySelectorAll('input[name="mode"]'));

  modeRadios.forEach((r) =>
    r.addEventListener("change", () => {
      state.mode = r.value;
      timeoutRow.hidden = state.mode !== "timeout";
    })
  );

  timeoutRange.addEventListener("input", () => {
    timeoutValue.textContent = (Number(timeoutRange.value) / 1000).toFixed(1) + "s";
  });

  function currentMode() {
    const checked = modeRadios.find((r) => r.checked);
    return checked ? checked.value : "gather";
  }

  // ---------- progress ----------
  function setProgress(done, total, label) {
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    progressBar.style.width = pct + "%";
    progressEl.setAttribute("aria-valuenow", String(pct));
    progressLabel.textContent = label != null ? label : pct + "%";
  }

  function setRunningUI(running) {
    state.running = running;
    runBtn.disabled = running;
    stopBtn.disabled = !running;
  }

  // ---------- gallery ----------
  function clearGalleryIfFirst() {
    if (galleryEmpty && galleryEmpty.parentNode) {
      galleryEmpty.parentNode.removeChild(galleryEmpty);
    }
  }

  function mediaNode(ev) {
    if (ev.kind === "gif") {
      const img = document.createElement("img");
      img.src = ev.url;
      img.alt = ev.name + " (animated GIF)";
      img.loading = "lazy";
      return img;
    }
    const video = document.createElement("video");
    video.src = ev.url;
    video.controls = true;
    video.preload = "metadata";
    video.muted = true;
    video.setAttribute("aria-label", ev.name + " (video)");
    return video;
  }

  function addResultCard(ev, burstIndex) {
    clearGalleryIfFirst();
    const card = document.createElement("article");
    card.className = "result-card" + (burstIndex != null ? " gather-burst" : "");
    if (burstIndex != null) card.style.setProperty("--i", String(burstIndex));

    const media = document.createElement("div");
    media.className = "result-media";
    media.appendChild(mediaNode(ev));

    const info = document.createElement("div");
    info.className = "result-info";

    const name = document.createElement("span");
    name.className = "result-name";
    name.textContent = ev.name;

    const meta = document.createElement("span");
    meta.className = "result-meta";
    const wait = document.createElement("span");
    wait.textContent = "wait " + fmtS(ev.delayMs);
    const actual = document.createElement("span");
    actual.className = "ok";
    actual.textContent = "done " + fmtS(ev.t - ev.startedAt);
    const size = document.createElement("span");
    size.textContent = Number(ev.sizeMb).toFixed(1) + " MB";
    meta.appendChild(wait);
    meta.appendChild(actual);
    meta.appendChild(size);

    info.appendChild(name);
    info.appendChild(meta);
    card.appendChild(media);
    card.appendChild(info);
    galleryEl.appendChild(card);
  }

  function addCancelledCard(ev) {
    clearGalleryIfFirst();
    const card = document.createElement("article");
    card.className = "result-card is-cancelled";

    const media = document.createElement("div");
    media.className = "result-media";
    media.textContent = "✕ cancelled at " + fmtS(ev.t);

    const info = document.createElement("div");
    info.className = "result-info";
    const name = document.createElement("span");
    name.className = "result-name";
    name.textContent = ev.name;
    const meta = document.createElement("span");
    meta.className = "result-meta";
    const cancel = document.createElement("span");
    cancel.className = "cancel";
    cancel.textContent = "aborted";
    const wait = document.createElement("span");
    wait.textContent = "needed " + fmtS(ev.delayMs);
    meta.appendChild(cancel);
    meta.appendChild(wait);
    info.appendChild(name);
    info.appendChild(meta);
    card.appendChild(media);
    card.appendChild(info);
    galleryEl.appendChild(card);
  }

  function flushGatherBuffer() {
    state.gatherBuffer.forEach((ev, i) => addResultCard(ev, i));
    state.gatherBuffer = [];
    state.bufferingGather = false;
  }

  // ---------- metrics + comparison ----------
  function renderMetrics(m) {
    const savedCls = m.savedMs > 200 ? "good" : m.savedMs >= -200 ? "warn" : "bad";
    const cards = [
      { label: "runtime", value: fmtS(m.elapsedMs), cls: "neutral" },
      { label: "completed", value: m.completed + "/" + m.totalTasks, cls: "good" },
      { label: "cancelled", value: String(m.cancelled), cls: m.cancelled > 0 ? "bad" : "neutral" },
      {
        label: "saved vs sequential",
        value: (m.savedMs <= 0 ? "" : "+") + fmtS(m.savedMs),
        cls: savedCls,
      },
      { label: "throughput", value: m.throughput.toFixed(2) + "/s", cls: "good" },
      { label: "sequential est.", value: fmtS(m.sequentialMs), cls: "warn" },
    ];
    metricsCards.innerHTML = "";
    cards.forEach((c) => {
      const el = document.createElement("div");
      el.className = "metric-card";
      const l = document.createElement("div");
      l.className = "metric-label";
      l.textContent = c.label;
      const v = document.createElement("div");
      v.className = "metric-value " + c.cls;
      v.textContent = c.value;
      el.appendChild(l);
      el.appendChild(v);
      metricsCards.appendChild(el);
    });
  }

  function addComparisonRow(m) {
    state.sessionRuns.push(Object.assign({ mode: state.mode }, m));
    comparisonBody.innerHTML = "";
    state.sessionRuns
      .slice()
      .reverse()
      .forEach((r) => {
        const tr = document.createElement("tr");
        const saved = r.savedMs;
        tr.innerHTML =
          "<td><span class='mode-tag " +
          r.mode +
          "'>" +
          (MODE_LABELS[r.mode] || r.mode) +
          "</span></td>" +
          "<td>" +
          r.completed +
          "/" +
          r.totalTasks +
          (r.cancelled ? " <span class='saved-neg'>(-" + r.cancelled + ")</span>" : "") +
          "</td>" +
          "<td>" +
          fmtS(r.elapsedMs) +
          "</td>" +
          "<td>" +
          fmtS(r.sequentialMs) +
          "</td>" +
          "<td class='" +
          (saved > 0 ? "saved-pos" : "saved-neg") +
          "'>" +
          (saved > 0 ? "+" : "") +
          fmtS(saved) +
          "</td>" +
          "<td>" +
          r.throughput.toFixed(2) +
          " tasks/s</td>";
        comparisonBody.appendChild(tr);
      });
  }

  // ---------- history ----------
  function esc(s) {
    return String(s).replace(/[&<>"]/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
    })[ch]);
  }

  async function refreshHistory() {
    try {
      const res = await fetch("/api/history");
      const data = await res.json();
      const runs = data.runs || [];
      historyBody.innerHTML = "";
      if (!runs.length) {
        historyBody.innerHTML =
          "<tr class='empty-row'><td colspan='7'>No runs stored yet.</td></tr>";
        return;
      }
      runs.forEach((r) => {
        const saved = r.sequential_ms - r.elapsed_ms;
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" +
          r.id +
          "</td>" +
          "<td>" +
          esc(r.created_at) +
          "</td>" +
          "<td><span class='mode-tag " +
          esc(r.mode) +
          "'>" +
          (MODE_LABELS[r.mode] || esc(r.mode)) +
          "</span></td>" +
          "<td>" +
          r.completed +
          "/" +
          r.total_tasks +
          (r.cancelled ? " <span class='saved-neg'>(-" + r.cancelled + ")</span>" : "") +
          "</td>" +
          "<td>" +
          fmtS(r.elapsed_ms) +
          "</td>" +
          "<td class='" +
          (saved > 0 ? "saved-pos" : "saved-neg") +
          "'>" +
          (saved > 0 ? "+" : "") +
          fmtS(saved) +
          "</td>" +
          "<td>" +
          Number(r.throughput).toFixed(2) +
          " tasks/s</td>";
        historyBody.appendChild(tr);
      });
    } catch (err) {
      historyBody.innerHTML =
        "<tr class='empty-row'><td colspan='7'>Failed to load history.</td></tr>";
    }
  }

  $("history-refresh").addEventListener("click", refreshHistory);

  // ---------- event dispatch ----------
  function handleEvent(ev) {
    switch (ev.type) {
      case "run-start":
        state.totalTasks = ev.totalTasks;
        state.settledTasks = 0;
        state.mode = ev.mode;
        state.bufferingGather = ev.mode === "gather";
        state.gatherBuffer = [];
        galleryHint.textContent =
          ev.mode === "gather"
            ? "gather mode: everything is rendered at once, at the end"
            : "results appear as the server finishes them";
        setProgress(0, ev.totalTasks, "0 of " + ev.totalTasks + " tasks");
        logLine(ev.title, "console-header", ev.t);
        break;

      case "task-start":
        logLine("Start " + ev.name + " | planned wait " + fmtS(ev.delayMs), "console-start", ev.t);
        break;

      case "task-done": {
        logLine(
          "Done " +
            ev.name +
            " | planned wait " +
            fmtS(ev.delayMs) +
            " | actual wait " +
            fmtS(ev.t - ev.startedAt),
          "console-done",
          ev.t
        );
        if (state.bufferingGather) {
          state.gatherBuffer.push(ev);
        } else {
          addResultCard(ev, null);
        }
        state.settledTasks += 1;
        setProgress(
          state.settledTasks,
          state.totalTasks,
          state.settledTasks + " of " + state.totalTasks + " tasks"
        );
        break;
      }

      case "task-cancel":
        logLine("Cancelled " + ev.name + " (timeout limit reached)", "console-cancel", ev.t);
        addCancelledCard(ev);
        state.settledTasks += 1;
        setProgress(
          state.settledTasks,
          state.totalTasks,
          state.settledTasks + " of " + state.totalTasks + " tasks"
        );
        break;

      case "log":
        logLine(ev.message, null, ev.t);
        break;

      case "metrics":
        logLine("Metrics", "console-header", ev.t);
        logLine(
          "| completed tasks | " + ev.completed + " of " + ev.totalTasks + " |",
          "console-metric",
          ev.t
        );
        logLine("| total runtime | " + fmtS(ev.elapsedMs) + " |", "console-metric", ev.t);
        logLine(
          "| sequential wait estimate | " + fmtS(ev.sequentialMs) + " |",
          "console-metric",
          ev.t
        );
        logLine(
          "| time saved versus sequential | " +
            (ev.savedMs > 0 ? "+" : "") +
            fmtS(ev.savedMs) +
            " |",
          "console-metric",
          ev.t
        );
        logLine("| throughput | " + ev.throughput.toFixed(2) + " tasks per second |", "console-metric", ev.t);
        renderMetrics(ev);
        addComparisonRow(ev);
        if (state.bufferingGather) flushGatherBuffer();
        break;

      case "end":
        setProgress(state.totalTasks, state.totalTasks, "finished");
        break;

      case "error":
        logLine("ERROR: " + ev.message, "console-error", ev.t);
        break;

      default:
        break;
    }
  }

  // ---------- run ----------
  async function runDemo() {
    if (state.running) return;
    const keys = selectedKeys();
    if (!keys.length) {
      logLine("select at least one media item first", "console-error");
      return;
    }

    const mode = currentMode();
    const body = { mode, media: keys };
    if (mode === "timeout") body.timeoutMs = Number(timeoutRange.value);

    galleryEl.innerHTML = "";
    metricsCards.innerHTML = "<p class='metrics-empty'>Collecting metrics…</p>";
    setRunningUI(true);
    setProgress(0, keys.length, "starting…");
    logLine("$ POST /api/run → mode=" + mode + ", tasks=" + keys.length, "console-hint");

    state.controller = new AbortController();

    try {
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: state.controller.signal,
      });

      if (!res.ok) {
        let msg = "HTTP " + res.status;
        try {
          const err = await res.json();
          if (err && err.error) msg = err.error;
        } catch (e) {
          /* keep default */
        }
        throw new Error(msg);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            handleEvent(JSON.parse(trimmed));
          } catch (e) {
            logLine("bad event: " + trimmed.slice(0, 120), "console-error");
          }
        }
      }
    } catch (err) {
      if (err && err.name === "AbortError") {
        logLine("— run aborted by user, server cancelled pending tasks —", "console-cancel");
        setProgress(state.settledTasks, Math.max(state.totalTasks, 1), "aborted");
      } else {
        logLine("request failed: " + (err && err.message ? err.message : String(err)), "console-error");
      }
    } finally {
      state.controller = null;
      state.bufferingGather = false;
      setRunningUI(false);
      refreshHistory();
    }
  }

  runBtn.addEventListener("click", runDemo);

  stopBtn.addEventListener("click", () => {
    if (state.controller) state.controller.abort();
  });

  // ---------- health ----------
  fetch("/api/health")
    .then((r) => r.json())
    .then((d) => {
      if (d && d.status === "ok") {
        $("health-badge").textContent = "API ready";
      } else {
        $("health-badge").textContent = "API degraded";
      }
    })
    .catch(() => {
      $("health-badge").textContent = "API offline";
    });

  // ---------- init ----------
  refreshHistory();
})();