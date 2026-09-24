"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let BACKENDS = [];
let selectedBackend = "valkey";
let lastBench = null;
let lastGemini = null;

/* ---------------------------------------------------------------- tabs */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
  };
});

/* ------------------------------------------------------------- startup */
async function init() {
  try {
    const health = await (await fetch("/healthz")).json();
    $("health").innerHTML = Object.entries(health.stores)
      .map(([k, v]) => `${k}: <span class="${v === "ok" ? "ok" : "bad"}">${esc(v)}</span>`)
      .join("<br>");
  } catch {
    $("health").innerHTML = '<span class="bad">backend unreachable</span>';
  }

  const info = await (await fetch("/api/backends")).json();
  BACKENDS = info.backends;
  $("llm_mode").value = info.defaults.llm_mode;

  const toggle = $("backend-toggle");
  toggle.innerHTML = "";
  BACKENDS.filter((b) => b.id !== "postgres_sync_off").forEach((b) => {
    const btn = document.createElement("button");
    btn.textContent = b.label;
    btn.className = b.id === selectedBackend ? "on" : "";
    btn.onclick = () => {
      selectedBackend = b.id;
      [...toggle.children].forEach((c) => c.classList.remove("on"));
      btn.classList.add("on");
      $("backend-notes").textContent = b.notes;
    };
    toggle.appendChild(btn);
  });
  $("backend-notes").textContent =
    (BACKENDS.find((b) => b.id === selectedBackend) || {}).notes || "";

  renderMethodology(info.defaults);
}

function readBrief() {
  return {
    destination: $("destination").value,
    origin: $("origin").value,
    start_date: $("start_date").value,
    end_date: $("end_date").value,
    travelers: Number($("travelers").value),
    budget_total: Number($("budget_total").value),
    nuance: $("nuance").value,
    backend: selectedBackend,
    llm_mode: $("llm_mode").value,
    writes_per_step: Number($("writes_per_step").value),
  };
}

/* ----------------------------------------------------------- run swarm */
$("plan").onclick = async () => {
  const btn = $("plan");
  btn.disabled = true;
  btn.textContent = "Planning\u2026";
  $("steps").innerHTML = "";
  $("itinerary").innerHTML = '<span class="muted">Working\u2026</span>';
  $("run-headline").classList.add("hidden");

  const open = {};
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readBrief()),
    });

    // Parse the SSE stream manually: EventSource cannot issue a POST.
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop();

      for (const chunk of chunks) {
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const msg = JSON.parse(line.slice(6));

        if (msg.type === "agent_step" && msg.phase === "start") {
          const li = document.createElement("li");
          li.className = "running";
          li.innerHTML = `${esc(msg.label)} <span class="ms">running\u2026</span>`;
          open[msg.agent] = li;
          $("steps").appendChild(li);
          $("steps").scrollTop = $("steps").scrollHeight;
        } else if (msg.type === "agent_step" && msg.phase === "end") {
          const li = open[msg.agent];
          if (li) {
            li.className = "done";
            li.innerHTML = `${esc(msg.label)} <span class="ms">${msg.duration_ms} ms</span>`;
          }
        } else if (msg.type === "complete") {
          renderResult(msg.result);
        } else if (msg.type === "error") {
          $("itinerary").innerHTML = `<span class="bad">${esc(msg.message)}</span>`;
        }
      }
    }
  } catch (err) {
    $("itinerary").innerHTML = `<span class="bad">${esc(err.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Plan my trip";
  }
};

function renderResult(r) {
  $("headline-ms").textContent = r.e2e_latency_ms.toFixed(0);
  $("headline-backend").textContent = r.backend;
  $("run-headline").classList.remove("hidden");

  const it = r.itinerary;
  if (!it) {
    $("itinerary").innerHTML = '<span class="muted">No itinerary produced.</span>';
  } else {
    const money = (v) => "$" + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
    $("itinerary").innerHTML = `
      <div class="kv">
        <div><div class="k">Destination</div>${esc(it.destination)}</div>
        <div><div class="k">Flight</div>${it.flight ? esc(it.flight.carrier) + " &middot; " + money(it.flight.price_usd) : "&ndash;"}</div>
        <div><div class="k">Stay</div>${it.stay ? esc(it.stay.name) + " &middot; " + money(it.stay.nightly_usd) + "/night" : "&ndash;"}</div>
        <div><div class="k">Total</div>${money(it.total_estimate_usd)}</div>
        <div><div class="k">Budget</div>${it.within_budget
          ? '<span style="color:var(--accent-2)">within</span>'
          : '<span style="color:var(--danger)">over</span>'}</div>
      </div>
      <p class="notes">${esc(it.summary)}</p>
      ${(it.days || []).map((d) => `
        <div class="day">
          <h4>Day ${d.day}</h4>
          <p><strong>Morning</strong> &middot; ${esc(d.morning)}</p>
          <p><strong>Afternoon</strong> &middot; ${esc(d.afternoon)}</p>
          <p><strong>Evening</strong> &middot; ${esc(d.evening)}</p>
        </div>`).join("")}
      ${r.final_text ? `<p class="notes">${esc(r.final_text)}</p>` : ""}`;
  }

  renderTelemetry(r);
  $("scratchpad").textContent = JSON.stringify(r.scratchpad || {}, null, 2);
  $("scratchpad").classList.remove("muted");
  checkEviction(r.evicted_keys, r.backend);
}

function renderTelemetry(r) {
  const ol = r.op_latency || {};
  const byType = Object.entries(r.ops_by_type || {});
  const max = Math.max(...byType.map(([, v]) => v.p99_ms), 0.001);

  $("telemetry").classList.remove("muted");
  $("telemetry").innerHTML = `
    <div class="kv">
      <div><div class="k">End-to-end</div>${r.e2e_latency_ms.toFixed(1)} ms</div>
      <div><div class="k">State ops</div>${r.ops_total}</div>
      <div><div class="k">Round trips</div>${r.round_trips_total}</div>
      <div><div class="k">Errors</div>${r.errors}</div>
      <div><div class="k">Op p50 / p99</div>${ol.p50_ms} / ${ol.p99_ms} ms <span class="muted">(n=${ol.n})</span></div>
    </div>
    <p class="notes">
      Diagnostic &mdash; I/O busy time (union of overlapping intervals, so concurrent
      agents are not double-counted): <strong>${r.io_busy_ms} ms</strong>
      = ${r.io_overhead_pct}% of wall clock.
      ${r.io_overhead_is_high ? '<span style="color:var(--warn)"> Above the 10% diagnostic threshold.</span>' : ""}
    </p>
    ${r.cache ? `<p class="notes">Cache hit rate: <strong>${r.cache.hit_rate_pct}%</strong>
      (n=${r.cache.n}) &mdash; hit p50 ${r.cache.hit_p50_ms ?? "\u2013"} ms vs miss p50 ${r.cache.miss_p50_ms ?? "\u2013"} ms</p>` : ""}
    <h3>Latency by operation (p99)</h3>
    ${byType.map(([k, v]) => `
      <div class="bar-row">
        <div class="bar-label">${esc(k)} <span class="muted">(n=${v.n})</span></div>
        <div class="bar-track"><div class="bar-fill" style="width:${(v.p99_ms / max * 100).toFixed(1)}%"></div></div>
        <div class="bar-val">${v.p99_ms} ms</div>
      </div>`).join("")}
    <h3>Ops by agent role</h3>
    ${Object.entries(r.ops_by_agent || {}).map(([k, v]) =>
      `<div class="bar-row"><div class="bar-label">${esc(k)}</div>
       <div class="bar-val">${v.n} ops &middot; p50 ${v.p50_ms} ms</div></div>`).join("")}`;
}

function checkEviction(evicted, backend) {
  const el = $("eviction-banner");
  if (evicted > 0) {
    el.classList.remove("hidden");
    el.innerHTML = `<strong>Valkey evicted ${evicted} keys</strong> on <code>${esc(backend)}</code>.
      Results across arms are no longer directly comparable, and eviction under memory
      pressure is itself an operational risk of the in-memory approach: scratchpad
      entries can vanish mid-run.`;
  } else {
    el.classList.add("hidden");
  }
}

/* ----------------------------------------------------------- benchmark */
$("run-bench").onclick = async () => {
  const btn = $("run-bench");
  btn.disabled = true;
  const reps = Number($("bench_repeats").value);
  $("bench-progress").textContent =
    `Running ${reps} repeats + ${$("bench_warmups").value} warm-ups across 3 arms, interleaved\u2026`;

  try {
    const res = await fetch("/api/benchmark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        brief: readBrief(),
        arms: ["valkey", "postgres", "postgres_cached"],
        repeats: reps,
        warmups: Number($("bench_warmups").value),
        writes_per_step: Number($("bench_writes").value),
        baseline: "postgres",
      }),
    });
    lastBench = await res.json();
    renderBench(lastBench);
    renderContextChart();
    $("bench-progress").textContent =
      `Done in ${lastBench.manifest.duration_s}s. Raw op log: ${lastBench.oplog_path}`;
  } catch (err) {
    $("bench-progress").textContent = "Failed: " + err.message;
  } finally {
    btn.disabled = false;
  }
};

$("run-sweep").onclick = async () => {
  const btn = $("run-sweep");
  btn.disabled = true;
  $("bench-progress").textContent =
    "Running write-frequency sweep (1 / 10 / 100 writes per agent step)\u2026 this takes a few minutes.";
  try {
    const res = await fetch("/api/sweep/write-frequency", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        brief: readBrief(),
        arms: ["valkey", "postgres", "postgres_cached"],
        repeats: Number($("bench_repeats").value),
        warmups: Number($("bench_warmups").value),
        frequencies: [1, 10, 100],
        baseline: "postgres",
      }),
    });
    renderSweep(await res.json());
    $("bench-progress").textContent = "Sweep complete.";
  } catch (err) {
    $("bench-progress").textContent = "Failed: " + err.message;
  } finally {
    btn.disabled = false;
  }
};

$("probe-gemini").onclick = async () => {
  const btn = $("probe-gemini");
  btn.disabled = true;
  $("bench-progress").textContent = "Sampling live Gemini latency\u2026";
  try {
    lastGemini = await (await fetch("/api/gemini-latency?samples=30")).json();
    $("bench-progress").textContent = lastGemini.available
      ? `Gemini ${lastGemini.model}: p50 ${lastGemini.p50_ms}ms, p95 ${lastGemini.p95_ms}ms (n=${lastGemini.n})`
      : "Probe unavailable: " + lastGemini.reason;
    renderContextChart();
  } finally {
    btn.disabled = false;
  }
};

function armTable(arms) {
  return `<table>
    <tr><th>Arm</th><th class="num">e2e p50</th><th class="num">p95</th><th class="num">p99</th>
        <th class="num">n</th><th class="num">ops/run</th><th class="num">round trips</th><th class="num">errors</th></tr>
    ${Object.entries(arms).map(([id, a]) => `
      <tr><td>${esc(a.label)}</td>
        <td class="num">${a.e2e.p50_ms}</td>
        <td class="num">${a.e2e.p95_ms}</td>
        <td class="num">${a.e2e.p99_ms}</td>
        <td class="num">${a.e2e.n}</td>
        <td class="num">${a.ops_per_run}</td>
        <td class="num">${a.round_trips_per_run}</td>
        <td class="num">${a.errors}</td></tr>`).join("")}
  </table>`;
}

function renderBench(b) {
  // Single-tier arms must issue identical round trips for identical work; if they
  // don't, we're measuring app-code chattiness rather than the datastores.
  // The cache arm is excluded by design: write-through inherently costs a second
  // round trip, and that cost is part of what the arm is being evaluated on.
  const singleTier = Object.entries(b.arms).filter(([id]) => id !== "postgres_cached");
  const rt = singleTier.map(([, a]) => a.round_trips_per_run);
  const parity = rt.every((v) => Math.abs(v - rt[0]) < 0.51);
  const cachedRt = (b.arms.postgres_cached || {}).round_trips_per_run;

  $("bench-results").classList.remove("muted");
  $("bench-results").innerHTML = `
    ${armTable(b.arms)}
    <p class="notes">Round-trip parity across single-tier arms:
      <strong>${parity ? "yes" : "NO \u2014 results suspect"}</strong>
      (${rt.join(" / ")} per run).
      ${cachedRt ? `The cache arm issues ${cachedRt} by design: write-through pays a
      second round trip, which is a real cost of that architecture rather than a
      measurement artifact.` : ""}</p>
    <h3>Deltas vs baseline</h3>
    ${b.comparisons.map((c) => `
      <div class="verdict ${c.significant ? "sig" : "null"}">${esc(c.verdict)}</div>`).join("")}
    ${b.cache_hit_rate_pct !== null ? `<p class="notes">Cache arm hit rate: <strong>${b.cache_hit_rate_pct}%</strong></p>` : ""}`;
}

function renderSweep(s) {
  const rows = s.frequencies.map((f) => {
    const cell = s.cells[String(f)];
    return `<tr><td>${f} writes/step</td>${Object.entries(cell.arms)
      .map(([, a]) => `<td class="num">${a.e2e.p50_ms} <span class="muted">(n=${a.e2e.n})</span></td>`)
      .join("")}</tr>`;
  }).join("");

  const first = s.cells[String(s.frequencies[0])];
  const headers = Object.values(first.arms).map((a) => `<th class="num">${esc(a.label)}</th>`).join("");

  $("bench-results").classList.remove("muted");
  $("bench-results").innerHTML = `
    <h3>Write-frequency sweep &mdash; median end-to-end (ms)</h3>
    <table><tr><th>Workload</th>${headers}</tr>${rows}</table>
    <p class="notes">
      This is the axis where an in-memory tier can justify itself: not by winning at one
      write per step, but by whether the curves diverge as the agent design gets chattier.
    </p>
    ${s.frequencies.map((f) => `
      <h3>${f} writes/step</h3>
      ${s.cells[String(f)].comparisons.map((c) =>
        `<div class="verdict ${c.significant ? "sig" : "null"}">${esc(c.verdict)}</div>`).join("")}`).join("")}`;
}

function renderContextChart() {
  if (!lastBench) return;
  const el = $("context-chart");

  const deltas = lastBench.comparisons.map((c) => Math.abs(c.delta));
  const maxDelta = Math.max(...deltas, 0.001);

  if (!lastGemini || !lastGemini.available) {
    el.classList.remove("muted");
    el.innerHTML = `<p class="notes">Storage deltas measured. Run the Gemini probe to
      overlay real model latency and see whether these deltas are perceptible.</p>
      ${lastBench.comparisons.map((c) => `
        <div class="bar-row">
          <div class="bar-label">${esc(c.variant)} vs ${esc(c.baseline)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${(Math.abs(c.delta) / maxDelta * 100).toFixed(1)}%"></div></div>
          <div class="bar-val">${c.delta.toFixed(1)} ms</div>
        </div>`).join("")}`;
    return;
  }

  const scale = Math.max(lastGemini.p99_ms, maxDelta);
  const bar = (label, val, alt) => `
    <div class="bar-row">
      <div class="bar-label">${esc(label)}</div>
      <div class="bar-track"><div class="bar-fill ${alt ? "alt" : ""}" style="width:${(val / scale * 100).toFixed(1)}%"></div></div>
      <div class="bar-val">${val.toFixed(1)} ms</div>
    </div>`;

  const spread = lastGemini.p95_ms - lastGemini.p50_ms;
  const biggest = Math.max(...deltas);
  const invisible = biggest < spread;

  el.classList.remove("muted");
  el.innerHTML =
    lastBench.comparisons.map((c) => bar(`storage: ${c.variant} vs ${c.baseline}`, Math.abs(c.delta), true)).join("") +
    bar(`Gemini ${lastGemini.model} p50`, lastGemini.p50_ms) +
    bar("Gemini p95", lastGemini.p95_ms) +
    bar("Gemini p99", lastGemini.p99_ms) +
    `<div class="verdict ${invisible ? "null" : "sig"}">
      Largest storage delta is <strong>${biggest.toFixed(1)} ms</strong> against a
      Gemini p50\u2192p95 spread of <strong>${spread.toFixed(0)} ms</strong>.
      ${invisible
        ? "The storage difference is smaller than the model's own run-to-run variance, so it is not perceptible to the user in this configuration."
        : "The storage difference is large enough to be visible above model variance."}
    </div>`;
}

function renderMethodology(defaults) {
  $("methodology").innerHTML = `
    <table>
      <tr><th>Setting</th><th>Value</th></tr>
      <tr><td>Container caps</td><td>${esc(defaults.container_caps)}</td></tr>
      <tr><td>Benchmark LLM mode</td><td>fake (deterministic, ~0ms model latency)</td></tr>
      <tr><td>Demo LLM mode</td><td>${esc(defaults.gemini_model)} ${defaults.has_api_key ? "(key present)" : "(no key)"}</td></tr>
      <tr><td>Ordering</td><td>interleaved round-robin across arms</td></tr>
      <tr><td>Warm-ups</td><td>discarded (recorded in the op log, excluded from stats)</td></tr>
      <tr><td>Interval</td><td>paired percentile bootstrap, 10,000 resamples, 95%</td></tr>
      <tr><td>Provider latency</td><td>0 ms during benchmarks; 40 ms &plusmn; jitter in demo mode</td></tr>
    </table>
    <h3>Arm configurations</h3>
    <table>
      <tr><th>Arm</th><th>Configuration</th></tr>
      ${BACKENDS.map((b) => `<tr><td>${esc(b.label)}</td><td>${esc(b.notes)}</td></tr>`).join("")}
    </table>
    <h3>Caveats</h3>
    <ul class="notes">
      <li>Single host, single process. This is not a production capacity model.</li>
      <li>Stores run stock configs: Postgres keeps <code>synchronous_commit=on</code>.
          Durability is being priced, not normalised away.</li>
      <li>Mock travel providers, seeded from the brief so every arm plans identical data.</li>
      <li>Errored ops are excluded from latency percentiles but counted in the error column.</li>
      <li>Any non-zero Valkey eviction invalidates cross-arm comparison and is banner-flagged.</li>
    </ul>`;
}

/* ------------------------------------------------- demo / capture hooks */
// ?tab=hood            open the observability tab on load
// ?backend=postgres    preselect an arm
// ?autorun=1           immediately plan a trip
// Handy for live demos and for capturing populated screenshots.
async function applyUrlHooks() {
  const params = new URLSearchParams(location.search);

  const backend = params.get("backend");
  if (backend) {
    const btn = [...$("backend-toggle").children].find((b) =>
      (BACKENDS.find((x) => x.id === backend) || {}).label === b.textContent);
    if (btn) btn.click();
  }

  const tab = params.get("tab");
  if (tab) {
    const btn = document.querySelector(`.tab[data-tab="${tab}"]`);
    if (btn) btn.click();
  }

  if (params.get("autorun") === "1") {
    await $("plan").onclick();
  }
}

init().then(applyUrlHooks);
