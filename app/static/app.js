"use strict";

/*
 * Travel Concierge UI: plain DOM, no framework, no build step.
 *
 * One page serves two audiences from one event stream per run:
 *   Experience      the customer: the trip form, the team's progress in plain
 *                   language, and the finished itinerary.
 *   Under the Hood  developers: every session and scratchpad operation, both
 *                   stores as they were at any step, and the raw keys or rows
 *                   the run left behind.
 *
 * Security: the page is built with DOM APIs only. Server strings (model output,
 * stored values) are inserted as text nodes and never parsed as HTML, and the
 * CSP forbids inline scripts and styles.
 */

const $ = (id) => document.getElementById(id);

/* =================================================================== DOM */

/**
 * Create an element. `props` keys: "class", "dataset", "on" ({event: handler});
 * anything else becomes an attribute. Inline handlers and styles are refused.
 */
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    const lower = key.toLowerCase();
    if (lower === "style" || (lower !== "on" && lower.startsWith("on"))) {
      throw new Error(`h(): "${key}" is not allowed`);
    }
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "on") {
      for (const [type, fn] of Object.entries(value)) el.addEventListener(type, fn);
    } else {
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  return add(el, children);
}

/** Append children: nodes as-is, arrays flattened, anything else as text. */
function add(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) add(el, child);
    else el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

function fill(el, ...children) {
  el.replaceChildren();
  return add(el, children);
}

/** Scroll a positioned list so that `el` is visible, without moving the page. */
function keepVisible(list, el) {
  const top = el.offsetTop;
  const bottom = top + el.offsetHeight;
  if (top < list.scrollTop) list.scrollTop = Math.max(0, top - 4);
  else if (bottom > list.scrollTop + list.clientHeight) {
    list.scrollTop = bottom - list.clientHeight + 4;
  }
}

/* ============================================================ formatting */

const money = (v) => "$" + Math.round(Number(v) || 0).toLocaleString("en-US");
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
const capitalize = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
const size = (b) => (b >= 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b || 0} B`);
const offset = (tMs) => `+${((Number(tMs) || 0) / 1000).toFixed(2)} s`;

function duration(value) {
  const v = Number(value) || 0;
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`;
  if (v >= 10) return `${Math.round(v)} ms`;
  return `${v.toFixed(2)} ms`;
}

function pretty(value) {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function preview(value, max = 90) {
  let s;
  try {
    s = JSON.stringify(value);
  } catch {
    s = undefined;
  }
  if (s === undefined) s = String(value);
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

const jsonBlock = (value) => h("pre", { class: "json" }, pretty(value));

/** Model text arrives as Markdown; the page shows it as plain text. */
function stripMarkdown(text) {
  return String(text || "")
    .replace(/```[\w-]*\n?([\s\S]*?)```/g, "$1")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/\*\*([^*\n]+)\*\*/g, "$1")
    .replace(/__([^_\n]+)__/g, "$1")
    .replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, "")
    .replace(/^[ \t]*[-*+][ \t]+/gm, "• ")
    .replace(/\[([^\]\n]+)\]\([^)\n]*\)/g, "$1")
    .replace(/(^|[\s(])[*_]([^*_\n]+)[*_](?=[\s).,;:!?]|$)/gm, "$1$2")
    .replace(/[ \t]+$/gm, "")
    .trim();
}

function firstSentence(text, max = 160) {
  const flat = stripMarkdown(text).replace(/^• /gm, "").replace(/\s+/g, " ").trim();
  if (!flat) return "";
  const m = /^(.{12,}?[.!?])(?=\s|$)/.exec(flat);
  let s = m ? m[1] : flat;
  if (s.length > max) s = s.slice(0, max).replace(/\s+\S*$/, "") + "…";
  return s;
}

function paragraphs(text) {
  return stripMarkdown(text)
    .split(/\n{2,}/)
    .filter((p) => p.trim())
    .map((p) => h("p", {}, p.split("\n").map((line, i) => (i ? [h("br"), line] : line))));
}

function parseDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

function prettyDate(iso, opts = { weekday: "short", month: "short", day: "numeric" }) {
  const d = parseDay(iso);
  return d ? d.toLocaleDateString("en-US", opts) : iso || "";
}

function nightsBetween(a, b) {
  const x = parseDay(a);
  const y = parseDay(b);
  return x && y ? Math.round((y - x) / 86400000) : NaN;
}

function plusDays(iso, n) {
  const d = parseDay(iso);
  if (!d) return "";
  d.setDate(d.getDate() + n);
  const pad = (v) => String(v).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "2026-10-10 08:15" -> "Oct 10, 08:15" */
function dayTime(s) {
  const [day, time] = String(s || "").split(" ");
  return time ? `${prettyDate(day, { month: "short", day: "numeric" })}, ${time}` : String(s || "");
}

/** UI preferences only (the store for the next run). Never trip data. */
const prefs = {
  get(key) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* storage disabled: the choice just isn't remembered */
    }
  },
};

/* ================================================================= state */

const STORE_PREF = "concierge.store";
const REPLAY_MS = 160;

const FALLBACK_CONFIG = {
  stores: [
    { id: "valkey", label: "Valkey", detail: "" },
    { id: "postgres", label: "PostgreSQL", detail: "" },
  ],
  default_store: "valkey",
  model: "",
  has_api_key: true, // unknown: let the server decide
  max_nights: 21,
  max_rounds: 3,
  scratchpad_ttl_s: 900,
};

// Ideas for the destination box. Any place can be typed: the team researches it.
const DESTINATION_IDEAS = [
  "Lisbon", "Kyoto", "Bali", "Amalfi Coast", "Banff", "Hanoi", "Reykjavik", "Cape Town",
  "Marrakech", "Queenstown", "Oaxaca", "Scottish Highlands",
];

const app = {
  config: FALLBACK_CONFIG,
  store: "valkey", // store for the next run (the Under the Hood switch)
  running: false,
  run: null, // the run on screen; see newRun()
  hood: { filter: "all", live: true, replay: null, openKeys: new Set(), renderQueued: false },
};

/* ================================================================ agents */

// Mirrors app/agents/callbacks.py AGENT_ROLES.
const AGENT_ROLE = {
  supervisor_intake: "supervisor",
  destination_scout: "scout",
  transit_agent: "transit",
  stay_agent: "stay",
  budget_guardrail: "budget",
  itinerary_assembly: "itinerary",
  supervisor_final: "supervisor",
};
const ROLES = new Set([...Object.values(AGENT_ROLE), "runner", "user"]);
const roleClass = (who) => "role-" + (ROLES.has(who) ? who : "other");

// Where each agent sits in the ADK graph, for the step detail pane.
const AGENT_CONTEXT = {
  supervisor_intake:
    "SequentialAgent step 1. Turns the brief into trip_constraints for the specialists, with the destination and both airports identified by the model.",
  destination_scout:
    "ParallelAgent branch inside the budget LoopAgent. Researches places with Google Search, then posts destination_shortlist.",
  stay_agent:
    "ParallelAgent branch inside the budget LoopAgent. Researches places to stay with Google Search, waits for the scout's shortlist, then posts stay_plan.",
  transit_agent:
    "ParallelAgent branch inside the budget LoopAgent. Researches flights with Google Search, waits for stay_plan, then posts transit_plan.",
  budget_guardrail:
    "Closes each LoopAgent round. Prices the whole plan, then either ends the loop or posts a budget_directive with price caps for the next round.",
  itinerary_assembly:
    "SequentialAgent step 3. Reads the whole scratchpad and assembles the day-by-day plan.",
  supervisor_final: "SequentialAgent step 4. Writes the customer-facing summary.",
};

// Live research: what each specialist looks up, and where its findings land.
const RESEARCH_KINDS = [
  { kind: "places", field: "destination_shortlist", noun: "places to see", title: "Places to see" },
  { kind: "stays", field: "stay_plan", noun: "places to stay", title: "Places to stay" },
  { kind: "flights", field: "transit_plan", noun: "flights", title: "Flights" },
];
const KIND_NOUN = Object.fromEntries(RESEARCH_KINDS.map((k) => [k.kind, k.noun]));

// Experience tab wording: no store names, timings or jargon.
const TEAM = [
  { agent: "supervisor_intake", title: "Understanding your trip", who: "Supervisor" },
  {
    agent: "research",
    title: "Researching in parallel",
    who: "",
    members: [
      { agent: "destination_scout", title: "Neighborhoods & must-sees", who: "Scout" },
      { agent: "stay_agent", title: "Where to stay", who: "Stay" },
      { agent: "transit_agent", title: "Flights & airport transfer", who: "Transit" },
    ],
  },
  { agent: "budget_guardrail", title: "Checking your budget", who: "Budget" },
  { agent: "itinerary_assembly", title: "Building your day-by-day plan", who: "Itinerary" },
  { agent: "supervisor_final", title: "Final review", who: "Supervisor" },
];
const RESEARCH = ["destination_scout", "stay_agent", "transit_agent"];

// Which team row shows each scratchpad finding.
const FIELD_AGENT = {
  trip_constraints: "supervisor_intake",
  destination_shortlist: "destination_scout",
  stay_plan: "stay_agent",
  transit_plan: "transit_agent",
  budget_verdict: "budget_guardrail",
  budget_directive: "budget_guardrail",
  itinerary: "itinerary_assembly",
};

const HEADLINE = {
  supervisor_intake: "Reading your trip details…",
  destination_scout: "Researching your trip…",
  stay_agent: "Researching your trip…",
  transit_agent: "Researching your trip…",
  budget_guardrail: "Checking the plan against your budget…",
  itinerary_assembly: "Building your day-by-day plan…",
  supervisor_final: "Final review…",
};

const storeLabel = (id) => (app.config.stores.find((s) => s.id === id) || {}).label || id;

/* ================================================================== runs */

function newRun(brief, store) {
  return {
    id: null,
    store,
    brief,
    model: "",
    layout: null,
    maxRounds: app.config.max_rounds,
    status: "running",
    events: [], // every SSE event, in seq order
    result: null,
    error: null,
    team: buildTeam(),
    hood: {
      steps: [], // timeline rows: {i, n, msg, note, el}
      bySeq: new Map(),
      cursor: -1,
      selected: null,
      lastWrite: {}, // field -> step of its latest scratchpad write
      counts: { session: 0, scratchpad: 0, waits: 0, research: 0, errors: 0 },
      rawLoadedFor: null,
    },
  };
}

function readBrief() {
  return {
    destination: $("destination").value.trim(),
    origin: $("origin").value.trim(),
    start_date: $("start_date").value,
    end_date: $("end_date").value,
    travelers: Number($("travelers").value),
    budget_total: Number($("budget_total").value),
    nuance: $("nuance").value.trim(),
  };
}

function checkBrief(b) {
  const nights = nightsBetween(b.start_date, b.end_date);
  if (!(nights >= 1)) return "Your return date must be after your departure date.";
  if (nights > app.config.max_nights) {
    return `Trips can be at most ${app.config.max_nights} nights.`;
  }
  return "";
}

function showFormError(text) {
  const el = $("form-error");
  el.textContent = text;
  el.classList.toggle("hidden", !text);
}

function setRunning(on) {
  app.running = on;
  const blocked = on || !app.config.has_api_key;
  const plan = $("plan");
  plan.disabled = blocked;
  plan.textContent = on ? "Planning…" : "Plan my trip";
  $("run-again").disabled = blocked;
  $("brief-form").setAttribute("aria-busy", String(on));
}

// The server answers /api/run at once and, while a model call is in flight,
// sends a keep-alive every 15 s (app/main.py HEARTBEAT_S). The windows are
// generous because the demo is reached through the BeyondCorp proxy (GFE),
// which may batch small chunks: only a connection that is really gone should
// trip them, never a slow model.
const CONNECT_TIMEOUT_MS = 30000;
const STALL_TIMEOUT_MS = 90000; // six missed keep-alives

/** Aborts `controller` unless re-armed within the given time. */
function watchdog(controller) {
  let timer = 0;
  return {
    arm(ms) {
      clearTimeout(timer);
      timer = setTimeout(() => controller.abort(), ms);
    },
    stop() {
      clearTimeout(timer);
    },
  };
}

async function startRun() {
  if (app.running) return;
  const form = $("brief-form");
  showFormError("");
  if (!form.checkValidity()) {
    showTab("ux");
    form.reportValidity();
    return;
  }
  const brief = readBrief();
  const problem = checkBrief(brief);
  if (problem) {
    showTab("ux");
    showFormError(problem);
    return;
  }

  // From here on everything sits inside try/finally, so no failure can leave
  // the button stuck on "Planning…".
  setRunning(true);
  const status = $("team-status");
  const idleStatus = status.textContent;
  status.textContent = "Contacting your travel team…";
  const controller = new AbortController();
  const dog = watchdog(controller);
  let run = null;
  try {
    dog.arm(CONNECT_TIMEOUT_MS);
    let res = null;
    try {
      res = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ ...brief, backend: app.store }),
        signal: controller.signal,
      });
    } catch {
      res = null;
    }
    if (!res || !res.ok || !res.body) {
      let message = "We couldn't reach the concierge. Check your connection and try again.";
      if (res) message = await describeHttpError(res);
      else if (controller.signal.aborted) message = "The concierge didn't answer in time. Please try again.";
      status.textContent = idleStatus;
      showTab("ux");
      showFormError(message);
      return;
    }

    run = newRun(brief, app.store);
    app.run = run;
    resetExperience();
    resetHood(run);
    dog.arm(STALL_TIMEOUT_MS);
    await readEvents(res.body, (msg) => handleEvent(run, msg), () => dog.arm(STALL_TIMEOUT_MS));
    if (run.status === "running") {
      failRun(run, "The connection closed before your plan was finished. Please try again.");
    }
  } catch {
    if (run && run.status === "running") {
      failRun(run, "We lost the connection while planning. Please try again.");
    } else if (!run) {
      status.textContent = idleStatus;
      showTab("ux");
      showFormError("Something went wrong starting your plan. Please try again.");
    }
  } finally {
    dog.stop();
    controller.abort(); // no-op after a clean finish; else frees the stream
    setRunning(false);
    refreshHealth();
  }
}

/** Parse the SSE stream by hand: EventSource cannot send a POST.
 * `onChunk` runs for every chunk received, keep-alive comments included. */
async function readEvents(body, onEvent, onChunk = () => {}) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk();
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = chunk
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n");
      if (!data) continue; // e.g. ": keep-alive"
      let msg;
      try {
        msg = JSON.parse(data);
      } catch {
        continue;
      }
      if (msg && typeof msg === "object") onEvent(msg);
    }
  }
}

const FIELD_LABEL = {
  destination: "Destination",
  origin: "Departing from",
  start_date: "Leaving",
  end_date: "Returning",
  travelers: "Travelers",
  budget_total: "Budget",
  nuance: "Anything else",
};

async function describeHttpError(res) {
  if (res.status === 422) {
    let body = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    const items = body && Array.isArray(body.detail) ? body.detail : [];
    const lines = [...new Set(items.map(validationMessage).filter(Boolean))];
    return lines.length ? lines.join(" ") : "Please check your trip details and try again.";
  }
  if (res.status === 403 || res.status === 415) {
    return "This page couldn't start a plan. Reload the page and try again.";
  }
  return "Something went wrong starting your plan. Please try again.";
}

/** One friendly sentence per validation error. Uses only `loc` and `msg`:
 * FastAPI also echoes the submitted input, which is never shown. */
function validationMessage(item) {
  const loc = item && Array.isArray(item.loc) ? item.loc : [];
  const field = loc.length > 1 ? String(loc[loc.length - 1]) : "";
  const msg = String((item && item.msg) || "")
    .replace(/^Value error,\s*/i, "")
    .replace(/\.$/, "");
  if (/end_date must be after start_date/i.test(msg)) {
    return "Your return date must be after your departure date.";
  }
  const nights = /limited to (\d+) nights/i.exec(msg);
  if (nights) return `Trips can be at most ${nights[1]} nights.`;
  const label = FIELD_LABEL[field];
  if (!msg) return "";
  return label ? `${label}: ${msg.charAt(0).toLowerCase()}${msg.slice(1)}.` : `${capitalize(msg)}.`;
}

function handleEvent(run, msg) {
  if (app.run !== run) return;
  run.events.push(msg);
  if (msg.type === "started") {
    run.id = msg.run_id;
    run.model = msg.model || "";
    run.layout = msg.layout || null;
    run.maxRounds = msg.max_rounds || run.maxRounds;
  } else if (msg.type === "complete") {
    run.status = "complete";
    run.result = msg.result || {};
  } else if (msg.type === "error") {
    run.status = "error";
    run.error = msg.message || "Something went wrong while planning your trip. Please try again.";
  }
  experienceOnEvent(run, msg);
  hoodOnEvent(run, msg);
}

/** A failure the server could not report, e.g. the connection dropped. */
function failRun(run, message) {
  if (app.run !== run) return;
  run.status = "error";
  run.error = message;
  experienceOnEvent(run, { type: "error", message });
  hoodOnEvent(run, { type: "error", message });
}

/* ============================================================ Experience */

const STATUS_WORD = { waiting: "waiting", working: "working", done: "done", failed: "stopped" };

function buildTeam() {
  const rows = {};
  const row = (spec, sub) => {
    const state = h("span", { class: "visually-hidden" }, "waiting: ");
    const search = h("div", { class: "tm-search" });
    const finding = h("div", { class: "tm-finding" });
    const say = h("div", { class: "tm-say" });
    const li = h(
      "li",
      { class: "tm waiting" + (sub ? " tm-group" : "") },
      h(
        "div",
        { class: "tm-line" },
        h("span", { class: "tm-icon", "aria-hidden": "true" }),
        state,
        h("span", { class: "tm-title" }, spec.title),
        spec.who ? h("span", { class: "tm-who" }, spec.who) : null,
      ),
      search,
      finding,
      say,
      sub || null,
    );
    rows[spec.agent] = { li, state, search, finding, say, status: "waiting" };
    return li;
  };
  fill(
    $("team"),
    TEAM.map((spec) =>
      spec.members
        ? row(spec, h("ol", { class: "team-sub" }, spec.members.map((m) => row(m))))
        : row(spec),
    ),
  );
  return rows;
}

function setStatus(row, status) {
  row.status = status;
  row.li.classList.remove("waiting", "working", "done", "failed");
  row.li.classList.add(status);
  row.state.textContent = STATUS_WORD[status] + ": ";
}

function syncResearch(team) {
  const members = RESEARCH.map((a) => team[a]);
  const has = (s) => members.some((r) => r.status === s);
  let status = "waiting";
  if (has("working")) status = "working";
  else if (members.every((r) => r.status === "done")) status = "done";
  else if (has("failed")) status = "failed";
  else if (has("done")) status = "working";
  setStatus(team.research, status);
}

function resetExperience() {
  $("team-status").textContent = "Your team is getting started…";
  fill($("itinerary-meta"));
  fill($("itinerary"), h("p", { class: "empty" }, "Your team is putting your plan together…"));
}

function experienceOnEvent(run, msg) {
  const team = run.team;
  switch (msg.type) {
    case "agent_step": {
      const row = team[msg.agent];
      if (!row) return;
      setStatus(row, msg.phase === "start" ? "working" : "done");
      if (msg.phase === "start" && HEADLINE[msg.agent]) {
        $("team-status").textContent = HEADLINE[msg.agent];
      }
      syncResearch(team);
      break;
    }
    case "blackboard":
      if (msg.action === "wrote") showFinding(run, msg);
      else if (msg.action === "researching" || msg.action === "researched") showSearch(run, msg);
      break;
    case "state_op":
      if (msg.store === "session" && msg.op === "append" && msg.value) {
        showAgentLine(team, msg.value);
      }
      break;
    case "complete":
      for (const row of Object.values(team)) if (row.status === "working") setStatus(row, "done");
      syncResearch(team);
      renderItinerary(run);
      break;
    case "error":
      for (const row of Object.values(team)) {
        if (row.status === "working") setStatus(row, "failed");
        row.search.classList.remove("busy");
      }
      syncResearch(team);
      $("team-status").textContent = "Planning stopped.";
      fill($("itinerary"), h("p", { class: "notice bad", role: "alert" }, msg.message || run.error));
      // "We couldn't find that destination": say so where it can be fixed.
      if (msg.category === "unknown_destination" || msg.category === "unknown_origin") {
        showFormError(msg.message);
      }
      break;
    default:
      break;
  }
}

/** A specialist researching its part of the trip with Google Search. */
function showSearch(run, msg) {
  const row = run.team[FIELD_AGENT[msg.field]];
  if (!row) return;
  if (msg.action === "researching") {
    row.search.textContent = `${msg.detail || "Searching Google"}…`;
    row.search.classList.add("busy");
    $("team-status").textContent = "Researching your trip with Google Search…";
    return;
  }
  row.search.classList.remove("busy");
  const queries = Array.isArray(msg.queries) ? msg.queries.length : 0;
  const sources = Array.isArray(msg.sources) ? msg.sources.length : 0;
  row.search.textContent = msg.searched
    ? `Searched Google · ${plural(queries, "search", "searches")} · ${plural(sources, "source")}`
    : "No search needed";
}

/** Each agent's key finding, taken from what it posted to the scratchpad. */
function showFinding(run, msg) {
  const team = run.team;
  const row = team[FIELD_AGENT[msg.field]];
  if (!row) return;
  row.finding.classList.remove("ok", "bad", "stale");
  if (msg.field === "budget_directive") {
    const round = (Number(msg.round) || 0) + 1;
    const max = msg.max_rounds || run.maxRounds;
    row.finding.textContent =
      `Over by ${money(msg.overage_usd)}. Asking the team for cheaper options ` +
      `(round ${round} of ${max}).`;
    row.finding.classList.add("bad");
    team.research.finding.textContent = `Round ${round} of ${max}: looking for cheaper options`;
    for (const a of RESEARCH) team[a].finding.classList.add("stale");
    return;
  }
  row.finding.textContent = capitalize(msg.detail || "");
  if (msg.field === "budget_verdict") {
    row.finding.classList.add(msg.within_budget === false ? "bad" : "ok");
    team.research.finding.textContent = "";
  }
}

/**
 * The visible reply in an ADK event. Gemini may split one reply across several
 * contiguous text parts, so they are joined without a separator.
 */
function eventText(ev) {
  const parts = ev && ev.content && Array.isArray(ev.content.parts) ? ev.content.parts : [];
  return parts
    .filter((p) => p && typeof p.text === "string" && !p.thought)
    .map((p) => p.text)
    .join("");
}

/** On the live model, a one-line version of what the agent itself said. */
function showAgentLine(team, ev) {
  const row = team[ev.author];
  if (!row) return;
  const line = firstSentence(eventText(ev));
  if (line) row.say.textContent = `“${line}”`;
}

const ICON = {
  flight: "\u2708\uFE0F",
  lunch: "\u{1F37D}\uFE0F",
  sunrise: "\u{1F305}",
  sunset: "\u{1F307}",
};
const MODE_ICON = { walk: "\u{1F6B6}", metro: "\u{1F687}", taxi: "\u{1F695}", rail: "\u{1F686}" };
const TIME_ICON = {
  sunrise: "\u{1F305}",
  sunset: "\u{1F307}",
  evening: "\u{1F319}",
  morning: "\u2600\uFE0F",
  midday: "\u2600\uFE0F",
  afternoon: "\u26C5",
};
const BEST_AT = {
  sunrise: "Best at sunrise",
  sunset: "Best at sunset",
  evening: "Best in the evening",
  morning: "Best in the morning",
  midday: "Best around midday",
  afternoon: "Best in the afternoon",
  anytime: "Good any time",
};
const WHY_LABEL = {
  scout: "Where to base yourself",
  stay: "Where you'll stay",
  transit: "Getting there",
  budget: "Budget",
};

function renderItinerary(run) {
  const r = run.result || {};
  const it = r.itinerary;
  const b = run.brief;
  const nights = nightsBetween(b.start_date, b.end_date);
  fill(
    $("itinerary-meta"),
    [
      (it && it.destination) || b.destination,
      `${prettyDate(b.start_date)} – ${prettyDate(b.end_date)}`,
      plural(nights, "night"),
      plural(b.travelers, "traveler"),
    ].join(" · "),
  );
  if (!it) {
    $("team-status").textContent = "Planning finished without an itinerary.";
    fill(
      $("itinerary"),
      h("p", { class: "notice bad" }, "The team couldn't assemble an itinerary this time. Please try again."),
    );
    return;
  }

  const verdict = (r.scratchpad && r.scratchpad.budget_verdict) || {};
  const within = it.within_budget !== false;
  const overage =
    Number(verdict.overage_usd) || Math.max(0, (Number(it.total_estimate_usd) || 0) - b.budget_total);
  $("team-status").textContent = within
    ? "Your plan is ready."
    : "Your plan is ready, but it is over your budget. See the note below.";

  fill(
    $("itinerary"),
    r.final_text
      ? h("section", { class: "concierge" }, h("h3", {}, "From your concierge"), paragraphs(r.final_text))
      : null,
    factsGrid(it, r, within, overage),
    within
      ? null
      : h(
          "p",
          { class: "notice bad" },
          `This plan is ${money(overage)} over your ${money(b.budget_total)} budget, even with the ` +
            "cheapest options we found. A shorter trip or a higher budget would bring it within reach.",
        ),
    tailoredSection(it.tailored),
    it.summary ? h("p", { class: "season" }, it.summary) : null,
    whyThisPlan(it),
    h("div", { class: "days" }, (it.days || []).map(dayCard)),
    it.unscheduled && it.unscheduled.length
      ? h("p", { class: "unscheduled" }, "Didn't fit this time: " + it.unscheduled.join(", "))
      : null,
  );
}

function fact(label, value, sub, tone) {
  return h(
    "div",
    { class: "fact" },
    h("dt", {}, label),
    h("dd", { class: tone || null }, value, sub ? h("span", { class: "sub" }, sub) : null),
  );
}

/** "nonstop", "1 stop via TPE", "2 stops via DOH, LHR" */
function route(f) {
  if (!f.stops) return "nonstop";
  return plural(Number(f.stops), "stop") + (f.via ? ` via ${f.via}` : "");
}

function factsGrid(it, r, within, overage) {
  const b = it.breakdown || {};
  const f = it.flight;
  const s = it.stay;
  const overland = Boolean(f && f.no_flight);
  const rounds = Number(r.budget_rounds) || 1;
  let replans = "";
  if (rounds === 2) replans = "Re-planned once to fit";
  else if (rounds > 2) replans = `Re-planned ${rounds - 1} times to fit`;
  // Hotel nights start the night you land, so an overnight flight books one fewer.
  const nights = b.stay_nights ? ` (${plural(Number(b.stay_nights), "night")})` : "";
  return h(
    "dl",
    { class: "facts" },
    fact("Destination", it.destination, it.base_area ? `Base: ${it.base_area}` : ""),
    overland
      ? fact("Getting there", "No flight needed", "Close enough to travel overland")
      : fact(
          "Flight",
          f ? `${f.carrier} · ${money(f.price_usd)}` : "–",
          f
            ? `${dayTime(f.depart)} → ${dayTime(f.arrive)} · ${Number(f.duration_hours).toFixed(1)} h · ${route(f)}`
            : "",
        ),
    fact(
      "Stay",
      s ? s.name : "–",
      s
        ? [s.kind && s.kind !== "hotel" ? capitalize(s.kind) : "", s.neighborhood, `${money(s.nightly_usd)}/night`, `★ ${s.rating}`]
            .filter(Boolean)
            .join(" · ")
        : "",
    ),
    fact(
      "Total",
      money(it.total_estimate_usd),
      [overland ? "" : `flight ${money(b.flight_usd)}`, `stay ${money(b.stay_usd)}${nights}`, `on the ground ${money(b.ground_usd)}`]
        .filter(Boolean)
        .join(" · "),
    ),
    fact("Budget", within ? "Within budget" : `Over by ${money(overage)}`, replans, within ? "ok" : "bad"),
  );
}

/** A web page the research read: https only, in a new tab, no referrer. */
function externalLink(uri, text) {
  if (typeof uri !== "string" || !/^https:\/\//i.test(uri)) return null;
  return h("a", { href: uri, target: "_blank", rel: "noopener noreferrer" }, text || uri);
}

/**
 * Google's Search Suggestions for one research step. Google renders this HTML
 * and its terms ask for it to be shown with grounded results. It is its own
 * sandboxed document (main.py search_suggestions), so this page never parses it.
 * Shown in the Under the hood panel.
 */
function suggestionsFrame(runId, kind, title) {
  return h("iframe", {
    class: "suggestions",
    src: `/api/search-suggestions/${encodeURIComponent(runId)}/${encodeURIComponent(kind)}`,
    title: `Google Search suggestions for ${title.toLowerCase()}`,
    sandbox: "allow-popups allow-popups-to-escape-sandbox",
    referrerpolicy: "no-referrer",
    loading: "lazy",
  });
}

function whyThisPlan(it) {
  const c = it.collaboration || {};
  const items = Object.keys(WHY_LABEL)
    .filter((k) => c[k])
    .map((k) => h("li", {}, h("strong", {}, WHY_LABEL[k]), " ", c[k]));
  if (!items.length) return null;
  return h("details", { class: "why-plan", open: true }, h("summary", {}, "Why this plan"), h("ul", {}, items));
}

/** How the "Anything else we should know?" note shaped the plan. */
function tailoredSection(t) {
  if (!t || !t.note) return null;
  const list = (v) => (Array.isArray(v) ? v : []);

  const heard = [];
  if (t.pace_label && t.pace_label !== "balanced") heard.push(`${capitalize(t.pace_label)} pace`);
  if (list(t.interests).length) heard.push(`into ${list(t.interests).join(", ")}`);
  if (list(t.wishes).length) heard.push(`wishes: ${list(t.wishes).join(", ")}`);
  if (list(t.avoid).length) heard.push(`skip ${list(t.avoid).join(", ")}`);
  if (t.early_ok === false) heard.push("no early starts");

  // One line per wish or interest, listing the places that answer it.
  const groups = new Map();
  for (const x of list(t.for_you)) {
    if (!groups.has(x.reason)) groups.set(x.reason, []);
    groups.get(x.reason).push(x);
  }
  const answered = [...groups].map(([reason, places]) =>
    h(
      "li",
      {},
      h("strong", {}, capitalize(String(reason))),
      " → ",
      places.map((p, k) => [
        k ? ", " : "",
        p.name,
        h("span", { class: "day-ref" }, ` (day ${p.day})`),
        p.added ? h("span", { class: "added" }, "added for you") : null,
      ]),
    ),
  );
  const skipped = list(t.skipped);
  const unmet = list(t.unmet);

  return h(
    "section",
    { class: "tailored" },
    h("h3", {}, "Tailored to your note"),
    h("p", { class: "note" }, `“${t.note}”`),
    heard.length ? h("p", { class: "heard" }, "What we heard: " + heard.join(" · ")) : null,
    answered.length
      ? h("ul", {}, answered)
      : h("p", { class: "heard" }, "Nothing in this plan was picked specifically for your note."),
    skipped.length
      ? h("p", { class: "skipped" }, "Left out: " + skipped.map((s) => `${s.name} (${s.reason})`).join("; "))
      : null,
    unmet.length
      ? h("p", { class: "unmet" }, "Couldn't include: " + unmet.map((u) => `${u.what} (${u.reason})`).join("; "))
      : null,
  );
}

function legEl(leg, to) {
  if (!leg) return null;
  const bits = [leg.mode, `${Number(leg.distance_km || 0).toFixed(1)} km`, `${leg.minutes} min`];
  if (leg.cost_usd) bits.push(money(leg.cost_usd));
  if (to) bits.push(`to ${to}`);
  return h(
    "div",
    { class: "leg" },
    h("span", { class: "leg-icon", "aria-hidden": "true" }, MODE_ICON[leg.mode] || "→"),
    bits.join(" · "),
  );
}

function itemEls(i) {
  if (i.kind === "flight") {
    return h(
      "div",
      { class: "item flight" },
      h("div", { class: "when" }, i.start || ""),
      h(
        "div",
        { class: "what" },
        h("strong", {}, `${ICON.flight} ${i.name}`),
        i.note ? h("div", { class: "why" }, i.note) : null,
      ),
    );
  }
  if (i.kind === "arrival") {
    return [
      h(
        "div",
        { class: "item arrival" },
        h("div", { class: "when" }, i.start),
        h(
          "div",
          { class: "what" },
          h("strong", {}, `${ICON.flight} ${i.name}`),
          i.note ? h("div", { class: "why" }, i.note) : null,
        ),
      ),
      legEl(i.leg, i.leg && i.leg.to),
    ];
  }
  if (i.kind === "lunch") {
    return h(
      "div",
      { class: "item lunch" },
      h("div", { class: "when" }, i.start),
      h("div", { class: "what" }, `${ICON.lunch} ${i.name}`, i.note ? h("div", { class: "why" }, i.note) : null),
    );
  }
  const why = [BEST_AT[i.best_time] || BEST_AT.anytime, i.why_this_time].filter(Boolean).join(": ");
  return [
    legEl(i.leg),
    h(
      "div",
      { class: "item stop" },
      h("div", { class: "when" }, i.start, h("span", {}, i.end)),
      h(
        "div",
        { class: "what" },
        h(
          "div",
          { class: "what-head" },
          h("strong", {}, `${TIME_ICON[i.best_time] || ""} ${i.name}`.trim()),
          i.cost_usd ? h("span", { class: "cost" }, money(i.cost_usd)) : null,
        ),
        i.for_you
          ? h(
              "div",
              {},
              h(
                "span",
                { class: "for-you" },
                `${i.added_by === "scout" ? "Added for you" : "For you"} · ${i.for_you}`,
              ),
            )
          : null,
        h("div", { class: "why" }, why + (i.wait_min > 20 ? ` · ${i.wait_min} min free before` : "")),
        i.tip ? h("div", { class: "tip" }, i.tip) : null,
      ),
    ),
  ];
}

function dayCard(d) {
  const t = d.totals;
  const items = d.items || [];
  // A day spent in the air: just the flight, no sun times or ground totals.
  const travel = items.length > 0 && items.every((i) => i.kind === "flight");
  const sun = [];
  if (d.sunrise) sun.push(`${ICON.sunrise} ${d.sunrise}`);
  if (d.sunset) sun.push(`${ICON.sunset} ${d.sunset}`);
  if (d.golden_hour) sun.push(`golden hour ${d.golden_hour}`);
  if (!sun.length && d.daylight) sun.push(d.daylight);
  return h(
    "article",
    { class: travel ? "day travel" : "day" },
    h(
      "header",
      { class: "day-head" },
      h(
        "h3",
        {},
        `Day ${d.day}`,
        h("span", { class: "day-date" }, [d.weekday, prettyDate(d.date, { month: "short", day: "numeric" })].filter(Boolean).join(", ")),
      ),
      h("span", { class: "sun" }, sun.join(" · ")),
    ),
    d.title ? h("p", { class: "day-title" }, d.title) : null,
    h("div", { class: "items" }, items.map(itemEls)),
    d.return_leg
      ? [
          legEl(d.return_leg),
          h("div", { class: "item back" }, h("div", { class: "when" }), h("div", { class: "what" }, `Back to ${d.return_leg.to}`)),
        ]
      : null,
    t && !travel
      ? h(
          "footer",
          { class: "day-foot" },
          `${t.distance_km} km travelled (${t.walk_km} km on foot) · ${t.travel_min} min getting around · ` +
            `about ${money(d.est_cost_usd)} incl. food`,
        )
      : null,
  );
}

/* ======================================================== Under the Hood */

const WAIT_ACTIONS = new Set(["waited", "timed_out"]);
const RESEARCH_ACTIONS = new Set(["researching", "researched"]);

const isStep = (m) =>
  m.type === "state_op" ||
  m.type === "agent_step" ||
  (m.type === "blackboard" && (WAIT_ACTIONS.has(m.action) || RESEARCH_ACTIONS.has(m.action)));

function matchesFilter(step, filter) {
  const m = step.msg;
  switch (filter) {
    case "session":
      return m.type === "state_op" && m.store === "session";
    case "scratchpad":
      // Store ops, plus the waits that decide when a peer's post gets read.
      return (m.type === "state_op" && m.store === "scratchpad") || (m.type === "blackboard" && WAIT_ACTIONS.has(m.action));
    case "agents":
      return m.type !== "state_op";
    default:
      return true;
  }
}

/** Who performed a store op. Session appends are issued by the ADK Runner, so
 * the event's author says which agent produced it. */
function actorOf(m) {
  if (m.store === "session") {
    const author = m.op === "append" && m.value ? m.value.author : null;
    if (!author) return "runner";
    return author === "user" ? "user" : AGENT_ROLE[author] || author;
  }
  return m.agent || "runner";
}

const opName = (m) => `${m.store === "session" ? "session" : "scratch"}.${m.op}`;

function eventSummary(ev) {
  const bits = [];
  const parts = ev.content && Array.isArray(ev.content.parts) ? ev.content.parts : [];
  // Consecutive text parts are one reply split by the model; quote them once.
  let text = "";
  const flushText = () => {
    if (text.trim()) bits.push(`“${firstSentence(text, 80)}”`);
    text = "";
  };
  for (const p of parts) {
    if (!p || p.thought) continue;
    if (p.function_call) {
      flushText();
      const args = p.function_call.args && Object.keys(p.function_call.args).length ? "…" : "";
      bits.push(`→ ${p.function_call.name}(${args})`);
    } else if (p.function_response) {
      flushText();
      bits.push(`⇐ ${p.function_response.name} ${preview(p.function_response.response, 48)}`);
    } else if (typeof p.text === "string") {
      text += p.text;
    }
  }
  flushText();
  const actions = ev.actions || {};
  const delta = Object.keys(actions.state_delta || {});
  if (delta.length) bits.push(`state Δ ${delta.join(", ")}`);
  if (actions.escalate) bits.push("escalate");
  if (actions.transfer_to_agent) bits.push(`transfer → ${actions.transfer_to_agent}`);
  return bits.join(" · ") || "event";
}

function subjectOf(m) {
  const v = m.value;
  let s;
  if (m.store === "session") {
    if (m.op === "create") s = `state {${Object.keys(v || {}).join(", ")}}`;
    else if (m.op === "get") s = `${plural(Number(v && v.events_loaded) || 0, "event")} loaded`;
    else if (m.op === "append") s = eventSummary(v || {});
    else s = m.key || "";
  } else if (m.op === "read") {
    s = `${m.field} · ${m.bytes ? size(m.bytes) : "not posted yet"}`;
  } else if (m.op === "read_all") {
    s = `${plural(Array.isArray(v) ? v.length : 0, "field")} · ${size(m.bytes)}`;
  } else {
    s = `${m.field || "…"} · ${size(m.bytes)}`;
  }
  return m.error ? `${s} · ${m.error}` : s;
}

function setChip(el, text) {
  el.textContent = text || "";
  el.classList.toggle("hidden", !text);
}

function setHoodStatus(status) {
  const el = $("hood-status");
  el.className = "chip status " + status;
  el.textContent = { idle: "idle", running: "running", complete: "complete", error: "error" }[status];
}

function renderCounts(run) {
  const c = run.hood.counts;
  const parts = [plural(c.session, "session op"), plural(c.scratchpad, "scratchpad op")];
  if (c.research) parts.push(plural(c.research, "Google Search step"));
  if (c.waits) parts.push(plural(c.waits, "wait"));
  if (c.errors) parts.push(plural(c.errors, "failed op"));
  $("hood-counts").textContent = parts.join(" · ");
}

function renderStoreSwitch() {
  fill(
    $("store-switch"),
    app.config.stores.map((s) =>
      h(
        "button",
        {
          type: "button",
          class: s.id === app.store ? "on" : null,
          "aria-pressed": String(s.id === app.store),
          title: s.detail || null,
          on: {
            click: () => {
              app.store = s.id;
              prefs.set(STORE_PREF, s.id);
              renderStoreSwitch();
            },
          },
        },
        s.label,
      ),
    ),
  );
}

function resetHood(run) {
  stopReplay();
  setLive(true);
  fill($("timeline"));
  $("hood-run").textContent = "starting…";
  setChip($("hood-store"), storeLabel(run.store));
  setChip($("hood-model"), "");
  setHoodStatus("running");
  renderCounts(run);
  $("raw-sub").textContent = "what is physically in the store for this run";
  $("raw-status").textContent = "";
  fill($("raw-body"), h("p", { class: "empty" }, "Loads when the run finishes."));
  scheduleHoodRender();
}

function hoodOnEvent(run, msg) {
  const hood = run.hood;
  switch (msg.type) {
    case "started":
      $("hood-run").textContent = msg.run_id;
      setChip($("hood-store"), storeLabel(msg.backend));
      setChip($("hood-model"), msg.model);
      break;
    case "complete":
      setHoodStatus("complete");
      loadRaw(run);
      break;
    case "error":
      setHoodStatus("error");
      if (run.id) loadRaw(run);
      break;
    case "blackboard":
      if (msg.action === "wrote" && msg.detail) {
        // The scratchpad write carrying this finding streamed just before it.
        const step = hood.lastWrite[msg.field];
        if (step) {
          step.note = msg.detail;
          const sub = step.el.querySelector(".tl-sub");
          if (sub) sub.textContent = msg.detail;
        }
      }
      break;
    default:
      break;
  }
  if (isStep(msg)) {
    addStep(run, msg);
    if (app.hood.live) hood.cursor = hood.steps.length - 1;
  }
  renderCounts(run);
  scheduleHoodRender();
}

function addStep(run, msg) {
  const hood = run.hood;
  const step = { i: hood.steps.length, n: hood.steps.length + 1, msg, note: "", el: null };
  step.el = timelineRow(step);
  step.el.classList.toggle("hidden", !matchesFilter(step, app.hood.filter));
  hood.steps.push(step);
  hood.bySeq.set(msg.seq, step);
  if (msg.type === "state_op") {
    hood.counts[msg.store === "session" ? "session" : "scratchpad"] += 1;
    if (msg.error) hood.counts.errors += 1;
    if (msg.store === "scratchpad" && msg.op === "write" && msg.field) hood.lastWrite[msg.field] = step;
  } else if (msg.type === "blackboard") {
    if (WAIT_ACTIONS.has(msg.action)) hood.counts.waits += 1;
    else if (msg.action === "researched") hood.counts.research += 1;
  }
  $("timeline").append(step.el);
}

function timelineRow(step) {
  const m = step.msg;
  const li = h("li", {
    class: "tl-row",
    on: {
      click: () => {
        selectStep(step.i, true);
        $("timeline").focus({ preventScroll: true });
      },
    },
  });
  const cells = [h("span", { class: "tl-n" }, String(step.n)), h("span", { class: "tl-t" }, offset(m.t_ms))];
  if (m.type === "agent_step") {
    li.classList.add("tl-agent", m.phase === "start" ? "start" : "end");
    cells.push(
      h(
        "span",
        { class: "tl-main" },
        h("span", { class: "tl-mark", "aria-hidden": "true" }, m.phase === "start" ? "▶" : "■"),
        `${m.label || m.agent} ${m.phase === "start" ? "started" : "finished"}`,
      ),
    );
  } else if (m.type === "blackboard" && RESEARCH_ACTIONS.has(m.action)) {
    const noun = KIND_NOUN[m.kind] || m.field;
    li.classList.add("tl-research");
    cells.push(
      h(
        "span",
        { class: "tl-main" },
        h("span", { class: "tl-mark", "aria-hidden": "true" }, m.action === "researching" ? "⌕" : "✓"),
        m.action === "researching"
          ? `${m.agent} searching Google for ${noun}`
          : `${m.agent} researched ${noun} · ${m.detail || ""}`,
      ),
    );
  } else if (m.type === "blackboard") {
    li.classList.add("tl-wait");
    cells.push(
      h(
        "span",
        { class: "tl-main" },
        m.action === "waited"
          ? `${m.agent} waited ${Math.round(m.waited_ms || 0)} ms for ${m.field}`
          : `${m.agent} timed out waiting for ${m.field}`,
      ),
    );
  } else {
    const actor = actorOf(m);
    li.classList.add("tl-op", m.store === "session" ? "store-session" : "store-scratchpad");
    if (m.error) li.classList.add("err");
    cells.push(
      h("span", { class: "tl-actor " + roleClass(actor) }, actor),
      h("span", { class: "tl-verb" }, opName(m)),
      h(
        "span",
        { class: "tl-subj" },
        subjectOf(m),
        m.round ? h("span", { class: "round" }, `round ${m.round + 1}`) : null,
      ),
      h("span", { class: "tl-sub" }),
    );
  }
  return add(li, cells);
}

function selectStep(i, byUser) {
  const run = app.run;
  if (!run || i < 0 || i >= run.hood.steps.length) return;
  if (byUser) {
    stopReplay();
    setLive(false);
  }
  run.hood.cursor = i;
  scheduleHoodRender();
}

function selectSeq(seq) {
  const step = app.run && app.run.hood.bySeq.get(seq);
  if (step) selectStep(step.i, true);
}

/** Index of the next visible step in direction `dir`, or -1. */
function neighbour(dir) {
  const run = app.run;
  if (!run) return -1;
  const { steps, cursor } = run.hood;
  for (let i = cursor + dir; i >= 0 && i < steps.length; i += dir) {
    if (matchesFilter(steps[i], app.hood.filter)) return i;
  }
  return -1;
}

function edge(last) {
  const run = app.run;
  if (!run) return -1;
  const visible = run.hood.steps.filter((s) => matchesFilter(s, app.hood.filter));
  if (!visible.length) return -1;
  return (last ? visible[visible.length - 1] : visible[0]).i;
}

function setLive(on) {
  app.hood.live = on;
  const btn = $("tl-live");
  btn.classList.toggle("on", on);
  btn.setAttribute("aria-pressed", String(on));
  if (on && app.run) {
    app.run.hood.cursor = app.run.hood.steps.length - 1;
    scheduleHoodRender();
  }
}

function startReplay() {
  const run = app.run;
  if (!run || !run.hood.steps.length) return;
  stopReplay();
  setLive(false);
  run.hood.cursor = -1;
  const tick = () => {
    const next = app.run === run ? neighbour(1) : -1;
    if (next < 0) {
      stopReplay();
      return;
    }
    run.hood.cursor = next;
    scheduleHoodRender();
  };
  tick();
  app.hood.replay = window.setInterval(tick, REPLAY_MS);
  $("tl-replay").textContent = "Stop";
  $("tl-replay").setAttribute("aria-pressed", "true");
}

function stopReplay() {
  if (app.hood.replay !== null) {
    window.clearInterval(app.hood.replay);
    app.hood.replay = null;
  }
  $("tl-replay").textContent = "Replay";
  $("tl-replay").setAttribute("aria-pressed", "false");
}

function setFilter(filter) {
  app.hood.filter = filter;
  for (const btn of $("tl-filters").querySelectorAll("button")) {
    const on = btn.dataset.filter === filter;
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", String(on));
  }
  if (app.run) {
    for (const s of app.run.hood.steps) s.el.classList.toggle("hidden", !matchesFilter(s, filter));
  }
  scheduleHoodRender();
}

function scheduleHoodRender() {
  if (app.hood.renderQueued) return;
  app.hood.renderQueued = true;
  window.setTimeout(() => {
    app.hood.renderQueued = false;
    renderHood();
  }, 30);
}

function renderHood() {
  if (!$("tab-hood").classList.contains("active")) return; // drawn when the tab opens
  const run = app.run;
  if (!run) {
    renderEmptyHood();
    return;
  }
  const hood = run.hood;
  const step = hood.steps[hood.cursor] || null;
  if (hood.selected && hood.selected !== step) {
    hood.selected.el.classList.remove("sel");
    hood.selected.el.removeAttribute("aria-current");
  }
  if (step) {
    step.el.classList.add("sel");
    step.el.setAttribute("aria-current", "step");
    if (!step.el.classList.contains("hidden")) keepVisible($("timeline"), step.el);
  }
  hood.selected = step;
  $("tl-pos").textContent = step
    ? `Step ${step.n} of ${hood.steps.length}`
    : plural(hood.steps.length, "step");
  $("tl-prev").disabled = neighbour(-1) < 0;
  $("tl-next").disabled = neighbour(1) < 0;
  $("tl-replay").disabled = hood.steps.length === 0;

  const view = foldState(run, step ? step.msg.seq : 0);
  renderWhere(run);
  renderSession(run, view, step);
  renderScratch(run, view, step);
  renderDetail(run, view, step);
}

function renderEmptyHood() {
  fill($("session-where"));
  fill($("scratch-where"));
  fill(
    $("session-panel"),
    h("p", { class: "empty" }, "Plan a trip to watch the session store fill up, event by event."),
  );
  fill(
    $("scratch-panel"),
    h("p", { class: "empty" }, "Plan a trip to watch the agents post and read findings."),
  );
  $("detail-heading").textContent = "Step detail";
  fill(
    $("detail"),
    h(
      "p",
      { class: "empty" },
      "Every store operation of a run appears in the timeline. Select one to see the exact command, its cost and the value.",
    ),
  );
  $("tl-pos").textContent = "";
  for (const id of ["tl-prev", "tl-next", "tl-replay"]) $(id).disabled = true;
}

/**
 * Rebuild both stores as they were right after event `uptoSeq`, from the
 * streamed ops alone. Failed ops change nothing.
 */
function foldState(run, uptoSeq) {
  const session = { state: null, events: [] };
  const pad = new Map();
  const entry = (field, seq) => {
    let e = pad.get(field);
    if (!e) {
      e = {
        field,
        value: undefined,
        by: null,
        versions: 0,
        bytes: 0,
        round: 0,
        writeSeq: null,
        lastReadSeq: null,
        firstSeq: seq,
        readers: new Map(),
      };
      pad.set(field, e);
    }
    return e;
  };
  const noteRead = (e, who, miss, seq) => {
    const r = e.readers.get(who) || { n: 0, miss: 0 };
    r.n += 1;
    if (miss) r.miss += 1;
    e.readers.set(who, r);
    e.lastReadSeq = seq;
  };
  const noteWrite = (e, m, value, bytes) => {
    Object.assign(e, { value, by: m.agent, bytes, round: m.round || 0, writeSeq: m.seq });
    e.versions += 1;
  };

  for (const m of run.events) {
    if (m.seq > uptoSeq) break;
    if (m.type !== "state_op" || m.error) continue;
    if (m.store === "session") {
      if (m.op === "create") {
        session.state = { ...(m.value || {}) };
      } else if (m.op === "append" && m.value) {
        session.events.push(m);
        const delta = (m.value.actions && m.value.actions.state_delta) || {};
        session.state = session.state || {};
        for (const [k, v] of Object.entries(delta)) {
          if (!k.startsWith("temp:")) session.state[k] = v; // ADK never persists temp: keys
        }
      }
    } else if (m.op === "write" && m.field) {
      noteWrite(entry(m.field, m.seq), m, m.value, m.bytes);
    } else if (m.op === "write_many" && m.value && typeof m.value === "object") {
      for (const [f, v] of Object.entries(m.value)) noteWrite(entry(f, m.seq), m, v, null);
    } else if (m.op === "read" && m.field) {
      noteRead(entry(m.field, m.seq), m.agent, !m.bytes, m.seq);
    } else if (m.op === "read_all" && Array.isArray(m.value)) {
      for (const f of m.value) noteRead(entry(f, m.seq), m.agent, false, m.seq);
    }
  }
  return { session, pad };
}

function renderWhere(run) {
  const lay = run.layout;
  const chips = (sec) =>
    (sec && Array.isArray(sec.structures) ? sec.structures : []).map((s) =>
      h(
        "code",
        { class: "struct", title: s.holds || null },
        `${s.type} ${s.name}${s.rows ? ` · ${s.rows}` : ""}`,
      ),
    );
  fill($("session-where"), lay ? chips(lay.session) : null);
  fill(
    $("scratch-where"),
    lay ? [chips(lay.scratchpad), h("span", { class: "muted" }, lay.scratchpad.summary)] : null,
  );
}

function renderSession(run, view, step) {
  const panel = $("session-panel");
  const s = view.session;
  if (!s.state && !s.events.length) {
    fill(
      panel,
      h("p", { class: "empty" }, step ? "Nothing in the session store yet at this step." : "Waiting for the first step…"),
    );
    return;
  }
  const cur = step && step.msg.type === "state_op" && step.msg.store === "session" ? step.msg : null;
  let changed = [];
  if (cur && cur.op === "append") {
    changed = Object.keys((cur.value && cur.value.actions && cur.value.actions.state_delta) || {});
  } else if (cur && cur.op === "create") {
    changed = Object.keys(s.state || {});
  }
  const state = s.state || {};
  const list = h(
    "ol",
    { class: "ev-list" },
    s.events.map((m, idx) => eventRow(m, idx, cur)),
  );
  fill(
    panel,
    h("div", { class: "sp-label" }, "state", h("span", { class: "muted" }, ` · ${plural(Object.keys(state).length, "key")}`)),
    h("div", { class: "kv-list" }, Object.entries(state).map(([k, v]) => kvRow(k, v, changed.includes(k)))),
    h("div", { class: "sp-label" }, "events", h("span", { class: "muted" }, ` · ${s.events.length} as of this step`)),
    s.events.length ? list : h("p", { class: "empty" }, "No events yet."),
  );
  const fresh = list.querySelector(".ev.new");
  if (fresh) keepVisible(list, fresh);
}

function kvRow(key, value, isNew) {
  const d = h(
    "details",
    { class: "kv" + (isNew ? " new" : ""), open: app.hood.openKeys.has(key) },
    h(
      "summary",
      {},
      h("code", { class: "k" }, key),
      h("span", { class: "preview" }, preview(value, 80)),
      isNew ? h("span", { class: "tag" }, "changed") : null,
    ),
    jsonBlock(value),
  );
  d.addEventListener("toggle", () => {
    if (d.open) app.hood.openKeys.add(key);
    else app.hood.openKeys.delete(key);
  });
  return d;
}

function eventRow(m, idx, cur) {
  const ev = m.value || {};
  const author = ev.author || "?";
  const who = author === "user" ? "user" : AGENT_ROLE[author] || author;
  return h(
    "li",
    { class: "ev" + (cur && cur.seq === m.seq ? " new" : "") },
    h(
      "button",
      {
        type: "button",
        class: "ev-btn",
        title: "Jump to the step that appended this event",
        on: { click: () => selectSeq(m.seq) },
      },
      h("span", { class: "ev-n" }, String(idx + 1)),
      h("span", { class: "ev-author " + roleClass(who) }, author),
      h("span", { class: "ev-sum" }, eventSummary(ev)),
    ),
  );
}

function renderScratch(run, view, step) {
  const panel = $("scratch-panel");
  if (!view.pad.size) {
    fill(
      panel,
      h("p", { class: "empty" }, step ? "Nothing on the scratchpad yet at this step." : "Waiting for the first step…"),
    );
    return;
  }
  const cur = step && step.msg.type === "state_op" && step.msg.store === "scratchpad" ? step.msg : null;
  const rows = [...view.pad.values()].sort((a, b) => a.firstSeq - b.firstSeq);
  fill(
    panel,
    h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        { class: "pad" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            ["field", "by", "v", "size", "round", "read by"].map((c) => h("th", { scope: "col" }, c)),
          ),
        ),
        h("tbody", {}, rows.map((e) => padRow(e, cur))),
      ),
    ),
  );
}

function padRow(e, cur) {
  const touched =
    cur &&
    (cur.field === e.field ||
      (cur.op === "read_all" && Array.isArray(cur.value) && cur.value.includes(e.field)));
  const wroteNow = touched && cur.op === "write";
  const written = e.versions > 0;
  const classes = [written ? "" : "ghost", touched ? (wroteNow ? "new" : "cur") : ""].join(" ").trim();
  const target = written ? e.writeSeq : e.lastReadSeq;
  return h(
    "tr",
    { class: classes || null },
    h(
      "td",
      {},
      h(
        "button",
        {
          type: "button",
          class: "linkish",
          title: written ? "Jump to the step that wrote this value" : "Jump to the latest read",
          on: { click: () => selectSeq(target) },
        },
        e.field,
      ),
      wroteNow ? h("span", { class: "tag" }, "new") : null,
    ),
    h("td", { class: written ? roleClass(e.by) : "muted" }, written ? e.by : "—"),
    h("td", { class: "num" }, String(e.versions)),
    h("td", { class: "num" }, written && e.bytes !== null ? size(e.bytes) : "—"),
    h("td", { class: "num" }, written ? String(e.round + 1) : "—"),
    h("td", {}, readersEl(e)),
  );
}

function readersEl(e) {
  if (!e.readers.size) return h("span", { class: "muted" }, "—");
  return [...e.readers.entries()].map(([who, r], i) => {
    const allMissed = r.miss === r.n;
    let label = who;
    if (allMissed) label += " ✗";
    else if (r.n > 1) label += ` ×${r.n}`;
    return [
      i ? ", " : "",
      h(
        "span",
        {
          class: "reader " + roleClass(who) + (allMissed ? " miss" : ""),
          title: r.miss ? `${r.miss} of ${plural(r.n, "read")} found nothing` : plural(r.n, "read"),
        },
        label,
      ),
    ];
  });
}

/** The store's physical command for an op, with this run's keys filled in. */
function primitiveFor(run, m) {
  const lay = run.layout;
  const template = lay && lay.primitives ? lay.primitives[m.op_type] : "";
  if (!template) return m.op_type;
  const named = (sec, type) =>
    ((sec && Array.isArray(sec.structures) ? sec.structures : []).find((s) => s.type === type) || {}).name;
  const sess = named(lay.session, "HASH");
  const swaps = [
    ["sess:<app>:<user>:<sid>", sess],
    ["sess:<...>", sess],
    ["evt:<...>", named(lay.session, "ZSET")],
    ["sessidx:<app>:<user>", named(lay.session, "SET")],
    ["scratch:<run>", named(lay.scratchpad, "HASH")],
    ["<field>", m.field],
    ["<ttl>", lay.scratchpad && lay.scratchpad.ttl_s],
    ["<sid>", run.id],
  ];
  let text = template;
  for (const [from, to] of swaps) {
    if (to !== undefined && to !== null) text = text.split(from).join(String(to));
  }
  return text;
}

/** A specialist's Google Search research: how it works and what it found. */
function renderResearchDetail(run, step, head, el) {
  const m = step.msg;
  const noun = KIND_NOUN[m.kind] || m.field;
  const label = (text) => h("div", { class: "dt-label" }, text);
  const at = `round ${(Number(m.round) || 0) + 1} · at ${offset(m.t_ms)}`;
  if (m.action === "researching") {
    head.textContent = `Step ${step.n} · ${m.agent} started researching ${noun}`;
    fill(
      el,
      h(
        "p",
        {},
        `The ${m.agent} agent looks up ${noun} for this trip with Google Search. It starts before ` +
          "waiting on any peer, so the three specialists research in parallel. Nothing is stored " +
          "in the session or scratchpad until the findings are posted.",
      ),
      h("p", { class: "facts-line" }, at),
    );
    return;
  }
  head.textContent = `Step ${step.n} · ${m.agent} researched ${noun}`;
  if (!m.searched) {
    fill(
      el,
      h(
        "p",
        {},
        m.kind === "flights"
          ? "No search needed: the trip starts close enough to the destination to travel overland."
          : "The model answered without running a search, so there are no sources to show.",
      ),
      h("p", { class: "facts-line" }, at),
    );
    return;
  }
  const queries = Array.isArray(m.queries) ? m.queries : [];
  const sources = (Array.isArray(m.sources) ? m.sources : [])
    .map((s) => (s ? externalLink(s.uri, s.title) : null))
    .filter(Boolean);
  const facts = [];
  if (m.search_s !== undefined && m.search_s !== null) facts.push(`search ${Number(m.search_s).toFixed(1)} s`);
  if (m.structure_s !== undefined && m.structure_s !== null) facts.push(`structure ${Number(m.structure_s).toFixed(1)} s`);
  facts.push(at);
  fill(
    el,
    h(
      "p",
      {},
      "Two model calls. The first is grounded with Google Search and writes research notes; the " +
        "second turns the notes into a fixed JSON schema (search can't run in a call that has a " +
        "response schema). The server then checks every entry, such as coordinates near the " +
        "destination, prices within bounds and flight times that are physically possible, " +
        "before the agent posts to the scratchpad.",
    ),
    h(
      "p",
      {},
      "Findings are shared within this run only, so a budget re-plan reuses them without " +
        "searching again. Nothing is kept for later runs.",
    ),
    h("p", { class: "facts-line" }, facts.join(" · ")),
    queries.length ? [label("Searches Google ran"), h("ul", { class: "queries" }, queries.map((q) => h("li", {}, q)))] : null,
    sources.length ? [label("Sources read"), h("ul", { class: "src-list" }, sources.map((a) => h("li", {}, a)))] : null,
    m.suggestions && run.id
      ? [label("Google Search suggestions"), suggestionsFrame(run.id, m.kind, noun)]
      : null,
  );
}

function renderDetail(run, view, step) {
  const head = $("detail-heading");
  const el = $("detail");
  // A research step's detail never changes once drawn. Redrawing it on every
  // streamed event would reload its suggestions frame each time.
  const shown = el.dataset.shown;
  delete el.dataset.shown;
  if (!step) {
    head.textContent = "Step detail";
    fill(
      el,
      h("p", { class: "empty" }, run.hood.steps.length ? "Select a step in the timeline." : "Steps appear here as the run progresses."),
    );
    return;
  }
  const m = step.msg;
  if (m.type === "agent_step") {
    const started = m.phase === "start";
    head.textContent = `Step ${step.n} · ${m.label || m.agent} ${started ? "started" : "finished"}`;
    const facts = [`agent ${m.agent}`, `role ${m.role}`];
    if (!started && m.duration_ms !== undefined && m.duration_ms !== null) facts.push(`took ${duration(m.duration_ms)}`);
    facts.push(`at ${offset(m.t_ms)}`);
    fill(el, AGENT_CONTEXT[m.agent] ? h("p", {}, AGENT_CONTEXT[m.agent]) : null, h("p", { class: "facts-line" }, facts.join(" · ")));
    return;
  }
  if (m.type === "blackboard" && RESEARCH_ACTIONS.has(m.action)) {
    const key = `${run.id}:${m.seq}`;
    if (shown !== key) renderResearchDetail(run, step, head, el);
    el.dataset.shown = key;
    return;
  }
  if (m.type === "blackboard") {
    const waited = m.action === "waited";
    head.textContent = `Step ${step.n} · ${m.agent} ${waited ? "waited" : "timed out"}`;
    fill(
      el,
      h(
        "p",
        {},
        waited
          ? `${m.agent} needed ${m.field}, which a peer had not posted yet, so it waited ` +
              `${Math.round(m.waited_ms || 0)} ms. The wait is an in-process signal, not polling: ` +
              "the store sees exactly one read, once the field is posted."
          : `${m.agent} stopped waiting for ${m.field} and used whatever was on the scratchpad.`,
      ),
      h("p", { class: "facts-line" }, `round ${(Number(m.round) || 0) + 1} · at ${offset(m.t_ms)}`),
    );
    return;
  }

  const engine = run.layout && run.layout.engine === "postgres" ? "PostgreSQL" : "Valkey";
  head.textContent = `Step ${step.n} · ${actorOf(m)} → ${opName(m)}`;
  const facts = [plural(m.round_trips, "round trip"), size(m.bytes), duration(m.duration_ms), `round ${(m.round || 0) + 1}`, `at ${offset(m.t_ms)}`];
  if (m.cache_hit !== undefined) facts.push(m.cache_hit ? "cache hit" : "cache miss");
  // The op log names both engines' data with Valkey-style logical keys; Postgres
  // has rows instead (the session id and the scratchpad run id are the run id).
  const where =
    engine === "PostgreSQL"
      ? [h("span", { class: "muted" }, "row "), h("code", {}, `${m.store === "session" ? "session_id" : "run_id"} = ${run.id}`)]
      : [h("span", { class: "muted" }, "key "), h("code", {}, m.key || "—")];
  fill(
    el,
    h("div", { class: "dt-label" }, `${engine} command`),
    h("pre", { class: "prim" }, primitiveFor(run, m)),
    h("p", { class: "facts-line" }, facts.join(" · ")),
    h(
      "p",
      { class: "keyline" },
      where,
      m.field ? [h("span", { class: "muted" }, " field "), h("code", {}, m.field)] : null,
    ),
    m.error ? h("p", { class: "notice bad" }, `Failed with ${m.error}. The failed op is still recorded, with its duration.`) : null,
    step.note ? h("p", { class: "posted" }, h("span", { class: "muted" }, "Posted as: "), step.note) : null,
    valueSection(run, view, m),
  );
}

function valueSection(run, view, m) {
  const label = (text) => h("div", { class: "dt-label" }, text);
  const codes = (names) => names.map((n, i) => [i ? ", " : "", h("code", {}, n)]);
  if (m.store === "session") {
    if (m.op === "create") return [label("Initial state"), jsonBlock(m.value)];
    if (m.op === "get") {
      const n = Number(m.value && m.value.events_loaded) || 0;
      return h("p", {}, `Loaded the session with ${plural(n, "event")}. The ADK Runner reads the session before it invokes the agents.`);
    }
    if (m.op === "append") {
      const delta = Object.keys((m.value && m.value.actions && m.value.actions.state_delta) || {});
      return [
        delta.length ? h("p", {}, "Also updates session state: ", codes(delta)) : null,
        label("ADK event, as stored"),
        jsonBlock(m.value),
      ];
    }
    return m.value !== undefined ? jsonBlock(m.value) : null;
  }
  if (m.op === "write") return [label("Value written"), jsonBlock(m.value)];
  if (m.op === "write_many") return [label("Values written"), jsonBlock(m.value)];
  if (m.op === "read_all") {
    const names = Array.isArray(m.value) ? m.value : [];
    return h("p", {}, `Returned ${plural(names.length, "field")}: `, codes(names));
  }
  if (m.op === "read") {
    if (!m.bytes) {
      return h(
        "p",
        { class: "muted" },
        "Returned nothing: no agent has posted this field yet. Agents read it anyway, so every run issues the same operations on every store.",
      );
    }
    const e = view.pad.get(m.field);
    if (!e || e.writeSeq === null) return h("p", {}, `Returned ${size(m.bytes)}.`);
    const w = run.hood.bySeq.get(e.writeSeq);
    return [label(`Value read (posted by ${e.by}${w ? ` at step ${w.n}` : ""})`), jsonBlock(e.value)];
  }
  return null;
}

/* ------------------------------------------------------------- raw view */

async function loadRaw(run) {
  if (!run || !run.id || app.run !== run) return;
  const status = $("raw-status");
  status.textContent = "Reading the store…";
  try {
    const res = await fetch(`/api/inspect/${encodeURIComponent(run.id)}`);
    if (app.run !== run) return;
    if (res.status === 404) {
      status.textContent = "Nothing is stored for this run.";
      fill($("raw-body"));
      run.hood.rawLoadedFor = run.id;
      return;
    }
    if (!res.ok) throw new Error(String(res.status));
    const snap = await res.json();
    if (app.run !== run) return;
    renderRaw(snap);
    run.hood.rawLoadedFor = run.id;
    status.textContent = `Read at ${new Date().toLocaleTimeString("en-US")}`;
  } catch {
    if (app.run === run) status.textContent = "Couldn't read the store. Try Refresh.";
  }
}

function renderRaw(snap) {
  const engine = snap.engine === "valkey" ? "Valkey" : "PostgreSQL";
  $("raw-sub").textContent = `what is physically in ${engine} for ${snap.run_id}`;
  const blocks = Array.isArray(snap.blocks) ? snap.blocks : [];
  fill(
    $("raw-body"),
    blocks.map((b) =>
      h(
        "section",
        { class: "raw-block" },
        h(
          "div",
          { class: "raw-head" },
          h("h3", {}, b.title),
          h("code", { class: "struct" }, b.structure),
          h("span", { class: "muted" }, b.note),
        ),
        h("pre", { class: "cmd" }, b.command),
        rawTable(b),
      ),
    ),
  );
}

function rawTable(b) {
  const rows = Array.isArray(b.rows) ? b.rows : [];
  const cols = Array.isArray(b.columns) ? b.columns : [];
  if (!rows.length) return h("p", { class: "empty" }, "No rows.");
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "raw-table" },
      h("thead", {}, h("tr", {}, cols.map((c) => h("th", { scope: "col" }, c)))),
      h(
        "tbody",
        {},
        rows.map((r) => h("tr", { class: r.expired ? "expired" : null }, cols.map((c) => h("td", {}, cell(r[c]))))),
      ),
    ),
  );
}

function cell(v) {
  if (v === null || v === undefined) return h("span", { class: "muted" }, "null");
  if (typeof v === "object") {
    return h("details", { class: "cell-json" }, h("summary", {}, preview(v, 80)), jsonBlock(v));
  }
  return String(v);
}

/* ================================================================ wiring */

function showTab(name) {
  for (const btn of document.querySelectorAll(".tab")) {
    const on = btn.dataset.tab === name;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", String(on));
    btn.tabIndex = on ? 0 : -1;
  }
  for (const panel of document.querySelectorAll(".panel")) {
    panel.classList.toggle("active", panel.id === "tab-" + name);
  }
  if (name === "hood") scheduleHoodRender();
}

function wireTabs() {
  const tabs = [...document.querySelectorAll(".tab")];
  for (const btn of tabs) {
    btn.addEventListener("click", () => showTab(btn.dataset.tab));
    btn.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const step = e.key === "ArrowRight" ? 1 : tabs.length - 1;
      const next = tabs[(tabs.indexOf(btn) + step) % tabs.length];
      showTab(next.dataset.tab);
      next.focus();
    });
  }
}

function wireHood() {
  for (const btn of $("tl-filters").querySelectorAll("button")) {
    btn.addEventListener("click", () => setFilter(btn.dataset.filter));
  }
  $("tl-prev").addEventListener("click", () => selectStep(neighbour(-1), true));
  $("tl-next").addEventListener("click", () => selectStep(neighbour(1), true));
  $("tl-replay").addEventListener("click", () => (app.hood.replay !== null ? stopReplay() : startReplay()));
  $("tl-live").addEventListener("click", () => {
    stopReplay();
    setLive(!app.hood.live);
  });
  $("timeline").addEventListener("keydown", (e) => {
    const moves = { ArrowDown: 1, ArrowUp: -1, j: 1, k: -1 };
    if (e.key in moves) {
      e.preventDefault();
      selectStep(neighbour(moves[e.key]), true);
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      selectStep(edge(e.key === "End"), true);
    }
  });
  $("run-again").addEventListener("click", () => startRun());
  $("raw-refresh").addEventListener("click", () => loadRaw(app.run));
  $("raw").addEventListener("toggle", () => {
    const run = app.run;
    if ($("raw").open && run && run.status !== "running" && run.hood.rawLoadedFor !== run.id) loadRaw(run);
  });
}

/** Keep the return date after the departure date. */
function syncDates() {
  const start = $("start_date").value;
  const end = $("end_date");
  if (!start) return;
  end.min = plusDays(start, 1);
  if (!end.value || end.value <= start) end.value = plusDays(start, 4);
}

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error(String(res.status));
    app.config = { ...FALLBACK_CONFIG, ...(await res.json()) };
  } catch {
    app.config = FALLBACK_CONFIG;
  }
  const ids = app.config.stores.map((s) => s.id);
  const saved = prefs.get(STORE_PREF);
  if (ids.includes(saved)) app.store = saved;
  else app.store = ids.includes(app.config.default_store) ? app.config.default_store : ids[0];

  // Any place works; the list is only inspiration for the input's suggestions.
  fill($("dest-list"), DESTINATION_IDEAS.map((d) => h("option", { value: d })));
  const warn = $("config-warning");
  warn.textContent = app.config.has_api_key
    ? ""
    : "Planning is unavailable right now: the server has no model API key configured.";
  warn.classList.toggle("hidden", Boolean(app.config.has_api_key));
  renderStoreSwitch();
  setRunning(false);
}

async function refreshHealth() {
  const el = $("health");
  try {
    const res = await fetch("/healthz");
    if (!res.ok) throw new Error(String(res.status));
    const stores = (await res.json()).stores || {};
    fill(
      el,
      Object.entries(stores).map(([id, state]) =>
        h(
          "span",
          { class: "health-item" },
          h("span", { class: "dot " + (state === "ok" ? "ok" : "bad"), "aria-hidden": "true" }),
          `${storeLabel(id)} ${state === "ok" ? "up" : "down"}`,
        ),
      ),
    );
  } catch {
    fill(el, h("span", { class: "health-item" }, h("span", { class: "dot bad", "aria-hidden": "true" }), "server unreachable"));
  }
}

/**
 * Demo and screenshot hooks:
 *   ?tab=hood           open Under the Hood
 *   ?backend=postgres   use this store for runs from this page (not remembered)
 *   ?autorun=1          plan a trip immediately
 */
function applyUrlHooks() {
  const params = new URLSearchParams(window.location.search);
  const backend = params.get("backend");
  if (backend && app.config.stores.some((s) => s.id === backend)) {
    app.store = backend;
    renderStoreSwitch();
  }
  const tab = params.get("tab");
  if (tab === "ux" || tab === "hood") showTab(tab);
  if (params.get("autorun") === "1") startRun();
}

async function init() {
  wireTabs();
  wireHood();
  $("brief-form").addEventListener("submit", (e) => {
    e.preventDefault();
    startRun();
  });
  $("start_date").addEventListener("change", syncDates);
  syncDates();
  buildTeam(); // the idle roster
  setHoodStatus("idle");
  setLive(true);
  renderEmptyHood();
  await loadConfig();
  refreshHealth();
  applyUrlHooks();
}

init();
