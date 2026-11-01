// TacoSort's phone page. Talks only to the TacoSort server it came from (phone.py).
"use strict";

const $ = (id) => document.getElementById(id);
const JOB_TITLES = { preview: "Preview", sort: "Sort my Inbox now", undo: "Undo last sort", learn: "Learn from my folders" };
let moveLimit = 50;
let polling = null;
let lastJobSeen = "";

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-TacoSort": "1" },
    body: JSON.stringify(body),
  };
  let resp;
  try {
    resp = await fetch(path, { cache: "no-store", ...opts });
  } catch (e) {
    throw new Error("Can't reach TacoSort. Is the computer on, and Tailscale switched on on this phone?");
  }
  let data = {};
  try { data = await resp.json(); } catch (e) { /* not JSON: use the status below */ }
  if (!resp.ok) throw new Error(data.error || `TacoSort answered ${resp.status}.`);
  return data;
}

function showProblem(text) {
  $("problem").textContent = text || "";
  $("problem").hidden = !text;
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const today = new Date();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return `today ${time}`;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

// ---- tabs -----------------------------------------------------------------------------------------
function showTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("on", b.dataset.tab === name);
  for (const s of document.querySelectorAll(".tab")) s.hidden = s.id !== `tab-${name}`;
  if (name === "home") loadStatus();
  if (name === "moves") loadMoves();
  if (name === "settings") loadSettings();
  if (name === "sort") loadJob();
}

// ---- home -----------------------------------------------------------------------------------------
async function loadStatus() {
  try {
    const s = await api("/api/status");
    showProblem("");
    $("sched").checked = s.schedule.on;
    $("sched-text").textContent = s.schedule.on ? `On: new mail is sorted every ${s.schedule.interval} minutes.`
      : "Off: nothing is sorted until you turn it on.";
    const r = s.last_run;
    $("last-run").textContent = r ? `${when(r.started)}: moved ${r.moved} of ${r.scanned} checked` : "Not yet";
    $("today").textContent = `Moved today: ${s.moved_today}` + (r && r.errors.length ? ` · Problem: ${r.errors[0]}` : "");
    const list = $("accounts");
    list.replaceChildren();
    for (const a of s.accounts) {
      const li = el("li");
      li.append(el("span", a.enabled ? "dot on" : "dot"), document.createTextNode(a.email + (a.enabled ? "" : " (off)")));
      list.append(li);
    }
    if (!s.accounts.length) list.append(el("li", "muted", "No accounts yet: add one in TacoSort on your computer."));
    $("version").textContent = `TacoSort ${s.version}`;
    setBusy(s.job && s.job.running);
  } catch (e) {
    showProblem(e.message);
  }
}

async function toggleSchedule() {
  const on = $("sched").checked;
  if (!on && !confirm("Turn off background sorting? New mail stays in the Inbox until you turn it on again.")) {
    $("sched").checked = true;
    return;
  }
  $("sched").disabled = true;
  try {
    await api("/api/schedule", { on });
  } catch (e) {
    showProblem(e.message);
  }
  $("sched").disabled = false;
  loadStatus();
}

// ---- sort -----------------------------------------------------------------------------------------
function setBusy(running) {
  $("busy").hidden = !running;
  for (const id of ["do-preview", "do-sort", "do-undo", "do-learn"]) $(id).disabled = !!running;
}

async function startJob(kind) {
  const asks = {
    sort: "Move the emails in your Inbox into folders now?\n\nYou can put them back with Undo last sort.",
    undo: "Put the emails from the last sort back where they were?",
  };
  if (asks[kind] && !confirm(asks[kind])) return;
  try {
    const data = await api("/api/job", { kind });
    showProblem("");
    renderJob(data.job);
    pollJob();
  } catch (e) {
    showProblem(e.message);
  }
}

async function loadJob() {
  try {
    const data = await api("/api/job");
    renderJob(data.job);
    if (data.job && data.job.running) pollJob();
  } catch (e) {
    showProblem(e.message);
  }
}

function pollJob() {
  if (polling) return;
  polling = setInterval(async () => {
    try {
      const data = await api("/api/job");
      renderJob(data.job);
      if (!data.job || !data.job.running) {
        clearInterval(polling);
        polling = null;
      }
    } catch (e) {
      clearInterval(polling);
      polling = null;
      showProblem(e.message);
    }
  }, 1500);
}

function renderJob(job) {
  setBusy(job && job.running);
  if (!job) { $("job").hidden = true; return; }
  $("job").hidden = false;
  $("job-title").textContent = JOB_TITLES[job.kind] || job.kind;
  $("job-log").textContent = job.lines.join("\n");
  const result = $("job-result");
  const key = `${job.kind}|${job.started}|${job.finished}`;
  if (job.running) {
    $("job-summary").textContent = "Working… (you can leave this page; it carries on)";
    result.replaceChildren();
    lastJobSeen = "";
    return;
  }
  if (key === lastJobSeen) return;
  lastJobSeen = key;
  result.replaceChildren();
  if (job.error) {
    $("job-summary").textContent = "That didn't work";
    result.append(el("p", "note", job.error));
    return;
  }
  const r = job.result || {};
  for (const p of r.problems || []) result.append(el("p", "note", p));
  if (job.kind === "preview") {
    const n = r.moves.length;
    $("job-summary").textContent = n ? `${n} email${n === 1 ? "" : "s"} would move; ${r.kept} would stay` : `Nothing would move (${r.scanned} checked)`;
    renderPreview(r.moves, result);
  } else if (job.kind === "sort") {
    $("job-summary").textContent = `Moved ${r.moved} email${r.moved === 1 ? "" : "s"}`;
  } else if (job.kind === "undo") {
    $("job-summary").textContent = r.undone ? `Put ${r.undone} email${r.undone === 1 ? "" : "s"} back` : "Nothing to undo";
  } else if (job.kind === "learn") {
    $("job-summary").textContent = `Learned ${r.rules} rule${r.rules === 1 ? "" : "s"} from ${r.folders} folders`;
  }
}

function renderPreview(moves, into) {
  const groups = new Map();
  for (const m of moves) {
    const k = `${m.account} → ${m.to}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(m);
  }
  const sorted = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  for (const [name, items] of sorted) {
    const box = el("details", "folder-group");
    const head = el("summary");
    head.textContent = `${name} (${items.length})`;
    box.append(head);
    const ul = el("ul");
    for (const m of items.slice(0, 200)) {
      const li = el("li");
      li.append(el("div", "who", m.sender), el("div", "subj", m.subject || "(no subject)"), el("div", "why small muted", m.reason));
      ul.append(li);
    }
    if (items.length > 200) ul.append(el("li", "muted", `…and ${items.length - 200} more`));
    box.append(ul);
    into.append(box);
  }
}

// ---- moves ----------------------------------------------------------------------------------------
async function loadMoves() {
  try {
    const data = await api(`/api/moves?limit=${moveLimit}`);
    const list = $("moves");
    list.replaceChildren();
    for (const m of data.moves) {
      const li = el("li", m.undone ? "undone" : "");
      li.append(
        el("div", "when", `${when(m.at)} · ${m.account}${m.undone ? " · put back" : ""}`),
        el("div", "who", m.sender),
        el("div", "subj", m.subject || "(no subject)"),
        el("div", "path", `${m.from} → ${m.to}`),
        el("div", "why", m.reason),
      );
      list.append(li);
    }
    if (!data.moves.length) list.append(el("li", "muted", "Nothing has been moved yet."));
    $("more-moves").hidden = data.moves.length < moveLimit || moveLimit >= 500;
  } catch (e) {
    showProblem(e.message);
  }
}

// ---- settings -------------------------------------------------------------------------------------
async function loadSettings() {
  try {
    const data = await api("/api/options");
    const groups = $("groups");
    groups.replaceChildren();
    for (const g of data.groups) {
      const card = el("div", "card");
      card.append(el("h2", "", g.title));
      for (const o of g.options) {
        const row = el("label", "opt");
        const tick = el("input");
        tick.type = "checkbox";
        tick.checked = o.ticked;
        tick.dataset.field = o.field;
        const text = el("div");
        text.append(el("div", "", o.label));
        if (o.help) text.append(el("p", "help", o.help));
        if (o.unit) {
          const num = el("input", "num");
          num.type = "number";
          num.inputMode = "numeric";
          num.min = o.low;
          num.max = o.high;
          num.value = o.number;
          num.dataset.number = o.field;
          num.disabled = !o.ticked;
          tick.addEventListener("change", () => { num.disabled = !tick.checked; });
          const line = el("div");
          line.append(num, document.createTextNode(` ${o.unit}`));
          text.append(line);
        }
        row.append(tick, text);
        card.append(row);
      }
      groups.append(card);
    }
    const folders = $("folders");
    folders.replaceChildren();
    for (const f of data.folders) {
      const row = el("label", "opt");
      const tick = el("input");
      tick.type = "checkbox";
      tick.checked = f.ticked;
      tick.dataset.folder = f.key;
      const text = el("div");
      text.append(el("div", "", f.folder));
      if (f.about) text.append(el("p", "help", f.about));
      row.append(tick, text);
      folders.append(row);
    }
    $("interval").value = data.interval;
    $("settings-note").hidden = true;
  } catch (e) {
    showProblem(e.message);
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const opts = {};
  for (const t of document.querySelectorAll("#groups input[data-field]")) {
    const num = document.querySelector(`#groups input[data-number="${t.dataset.field}"]`);
    opts[t.dataset.field] = { ticked: t.checked, number: num ? num.value : 0 };
  }
  const folders = {};
  for (const t of document.querySelectorAll("#folders input[data-folder]")) folders[t.dataset.folder] = t.checked;
  const note = $("settings-note");
  try {
    const data = await api("/api/options", { options: opts, folders, interval: $("interval").value });
    note.className = data.ok ? "note good" : "note";
    note.textContent = data.ok ? data.note : "Not saved: " + data.problems.join(" ");
  } catch (e) {
    note.className = "note";
    note.textContent = e.message;
  }
  note.hidden = false;
}

// ---- start ----------------------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
  $("sched").addEventListener("change", toggleSchedule);
  $("do-preview").addEventListener("click", () => startJob("preview"));
  $("do-sort").addEventListener("click", () => startJob("sort"));
  $("do-undo").addEventListener("click", () => startJob("undo"));
  $("do-learn").addEventListener("click", () => startJob("learn"));
  $("more-moves").addEventListener("click", () => { moveLimit = Math.min(moveLimit + 100, 500); loadMoves(); });
  $("settings").addEventListener("submit", saveSettings);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadStatus(); });
  const start = location.hash.slice(1);
  showTab(["home", "sort", "moves", "settings"].includes(start) ? start : "home");
});
