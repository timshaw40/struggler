"use strict";

/* Struggler web UI. All game state comes from /state (the human seat's
 * observation); every action is one of the pending decision's options,
 * posted by index — the server never invents one and neither do we. */

const META = {};    // card id -> {number, name, ops, side, scoring, event_summary, ...}
const IMAGES = {};  // card id -> card-face filename under /assets/cards/ (optional)
const POS = {};     // country id -> {x, y} fractions of the board image

let state = null;
let busy = false;
let zoom = 1;
let previewEl = null;

const $ = (sel) => document.querySelector(sel);

const PROMPTS = {
  place_influence: "Place influence",
  coup_target: "Choose a coup target",
  realignment_target: "Choose a realignment target",
  war_target: "Choose a war target",
  event_influence: "Event: place/remove influence",
  headline_play: "Headline: choose a card",
  action_round_play: "Action round: choose a card",
  play_mode: "Play the card",
  ops_type: "Spend the ops on",
  event_ops_order: "Opponent's card resolves",
  event_choice: "Event choice",
  event_resume: "Continue",
  quagmire_discard: "Quagmire: discard an Ops card",
  held_card_discard: "Discard the held card?",
};

const MODE_LABELS = {
  ops: "for operations",
  event: "for the event",
  space_race: "for the space race",
};

const pretty = (s) => String(s).replace(/_/g, " ");
const cardName = (cid) => (META[cid] && META[cid].name) || pretty(cid);

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

async function boot() {
  const [cards, manifest, countries] = await Promise.all([
    fetchJson("/cards"),
    fetchJson("/assets/cards.json").catch(() => ({})),
    fetchJson("/countries.json"),
  ]);
  Object.assign(META, cards);
  Object.assign(IMAGES, manifest);
  Object.assign(POS, countries);
  if (!Object.keys(IMAGES).length) $("#boardwrap").classList.add("noboard");
  const preview = document.createElement("div");
  preview.id = "cardpreview";
  preview.hidden = true;
  document.body.append(preview);
  previewEl = preview;
  $("#zoomin").addEventListener("click", () => setZoom(zoom * 1.5));
  $("#zoomout").addEventListener("click", () => setZoom(zoom / 1.5));
  $("#zoomfit").addEventListener("click", () => setZoom(1));
  window.addEventListener("resize", layoutBoard);
  layoutBoard();
  showPreview("Fidel");
  await refresh();
}

/* The board image is sized in px (base fit × zoom) instead of CSS-capped, so
 * zooming grows the scrollable area and markers — %-anchored inside
 * #boardbox — stay glued to their countries at any zoom. */
function layoutBoard() {
  const wrap = $("#boardwrap");
  if (wrap.classList.contains("noboard")) return;
  const base = Math.min(wrap.clientWidth, wrap.clientHeight) - 4;
  $("#board").style.width = Math.round(base * zoom) + "px";
}

function setZoom(next) {
  zoom = Math.min(6, Math.max(1, next));
  layoutBoard();
}

async function refresh() {
  state = await fetchJson("/state");
  render();
}

async function act(index) {
  if (busy) return;
  busy = true;
  render();
  try {
    const data = await fetch("/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    }).then((r) => r.json());
    state = data.state || data;  // error replies carry the current state
  } catch (err) {
    console.error(err);
  } finally {
    busy = false;
    render();
  }
}

// -- option helpers ---------------------------------------------------------

function findOption(pred) {
  const d = state.decision;
  if (!d) return null;
  const hit = d.options.find(pred);
  return hit ? hit.index : null;
}

const countryOption = (cid) => findOption((o) => o.payload && o.payload.country === cid);
const cardOption = (cid) => findOption((o) => o.payload && o.payload.card === cid);

function optionLabel(o) {
  const p = o.payload || {};
  if (p.country) return pretty(p.country);
  if (p.card) return p.card === "none" ? "Pass" : cardName(p.card);
  if (p.mode) return MODE_LABELS[p.mode] || pretty(p.mode);
  for (const key of ["type", "order", "choice"]) {
    if (key in p) return pretty(p[key]);
  }
  if (o.kind === "event_resume") return "Continue";
  return "OK";
}

// -- rendering ---------------------------------------------------------------

function render() {
  if (!state) return;
  renderBoard();
  renderPanel();
  renderHand();
  renderDecision();
  renderWinner();
}

function renderBoard() {
  $("#board").onerror = () => $("#boardwrap").classList.add("noboard");
  const host = $("#markers");
  host.textContent = "";
  if ($("#boardwrap").classList.contains("noboard")) return;
  const d = state.decision;
  for (const [cid, inf] of Object.entries(state.influence)) {
    const pos = POS[cid];
    const target = d ? countryOption(cid) : null;
    if (!pos || (target === null && inf.US === 0 && inf.USSR === 0)) continue;
    const el = document.createElement("div");
    el.className = "marker" + (target !== null ? " legal" : "");
    el.style.left = pos.x * 100 + "%";
    el.style.top = pos.y * 100 + "%";
    el.title = `${pretty(cid)} — US ${inf.US} / USSR ${inf.USSR}`;
    el.innerHTML =
      `<span class="us">${inf.US}</span><span class="ussr">${inf.USSR}</span>`;
    if (target !== null) el.addEventListener("click", () => act(target));
    host.append(el);
  }
}

function kv(label, value) {
  const row = document.createElement("div");
  row.className = "kv";
  row.innerHTML = `<span>${label}</span><b>${value}</b>`;
  return row;
}

function renderPanel() {
  const vp = state.vp === 0 ? "tied"
    : state.vp > 0 ? `US +${state.vp}` : `USSR +${-state.vp}`;
  const status = $("#status");
  status.textContent = "";
  status.append(
    kv("Turn", `${state.turn} · round ${state.action_round}`),
    kv("DEFCON", state.defcon),
    kv("VP", vp),
    kv("Space race", `US box ${state.space_race.US} / USSR box ${state.space_race.USSR}`),
    kv("Mil ops", `US ${state.military_ops.US} / USSR ${state.military_ops.USSR}`),
    kv("China card", `${state.china_card_owner}${state.china_card_available ? "" : " (face-down)"}`),
    kv("Opponent", `hand ${state.opponent_hand_size} · draw ${state.draw_pile_size}`),
  );

  const chips = $("#effects");
  chips.textContent = "";
  const effects = { ...state.turn_effects, ...state.game_effects };
  for (const [k, v] of Object.entries(effects)) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${pretty(k)}: ${typeof v === "boolean" ? (v ? "on" : "off") : pretty(v)}`;
    chips.append(chip);
  }

  const piles = $("#piles");
  piles.textContent = "";
  if (state.discard_pile.length) {
    const row = document.createElement("div");
    row.className = "kv";
    const names = state.discard_pile.slice(-4).map(cardName).join(", ");
    row.innerHTML = `<span>Discard (${state.discard_pile.length})</span><b>${names}</b>`;
    piles.append(row);
  }

  const feed = $("#feed");
  feed.textContent = "";
  const title = document.createElement("h2");
  title.textContent = "History";
  title.style.fontSize = "13px";
  feed.append(title);
  for (const e of state.history.slice().reverse().slice(0, 25)) {
    const row = document.createElement("div");
    row.className = "feedrow";
    const what = e.payload.card ? cardName(e.payload.card)
      : e.country ? pretty(e.country)
      : pretty(Object.values(e.payload)[0] ?? e.kind);
    row.innerHTML = `<b>${e.actor}</b> ${pretty(e.kind)} · ${what} · T${e.turn} R${e.action_round}`;
    feed.append(row);
  }
}

function cardEl(cid, actionIndex) {
  const m = META[cid] || {};
  const el = document.createElement("div");
  el.className = "card" + (m.side ? ` side-${m.side.toLowerCase()}` : "")
    + (actionIndex !== null ? " playable" : "");
  if (IMAGES[cid]) {
    const img = document.createElement("img");
    img.src = `/assets/cards/${IMAGES[cid]}`;
    img.alt = m.name || cid;
    img.loading = "lazy";
    img.addEventListener("error", () => {
      img.remove();
      fillCardText(el, m, cid);
    });
    el.append(img);
  } else {
    fillCardText(el, m, cid);
  }
  if (m.event_summary) el.title = m.event_summary;
  el.addEventListener("mouseenter", () => showPreview(cid));
  el.addEventListener("mouseleave", () => { previewEl.hidden = true; });
  if (actionIndex !== null) el.addEventListener("click", () => act(actionIndex));
  return el;
}

/* Hover preview: a fixed, pointer-transparent pane near the playbar. */
function showPreview(cid) {
  if (!previewEl) return;
  previewEl.textContent = "";
  const m = META[cid] || {};
  if (IMAGES[cid]) {
    const img = document.createElement("img");
    img.src = `/assets/cards/${IMAGES[cid]}`;
    img.alt = m.name || cid;
    previewEl.append(img);
  } else {
    fillCardText(previewEl, m, cid);
  }
  previewEl.hidden = false;
}

function fillCardText(el, m, cid) {
  const name = document.createElement("div");
  name.className = "cardname";
  name.textContent = m.name || cid;
  const ops = document.createElement("div");
  ops.className = "cardops";
  ops.textContent = m.scoring ? "scoring card" : `ops ${m.ops}`;
  el.append(name, ops);
}

function renderHand() {
  const host = $("#hand");
  host.textContent = "";
  for (const cid of state.hand) host.append(cardEl(cid, cardOption(cid)));
}

function renderDecision() {
  const box = $("#decision");
  box.textContent = "";
  if (state.is_terminal) return;
  const d = state.decision;
  if (!d || busy) {
    const note = document.createElement("em");
    note.textContent = busy ? "Opponent thinking…" : "Resolving…";
    box.append(note);
    return;
  }
  const prompt = document.createElement("strong");
  prompt.textContent = PROMPTS[d.kind] || pretty(d.kind);
  box.append(prompt);

  const ctx = Object.entries(d.context || {})
    .map(([k, v]) => `${pretty(k)}: ${typeof v === "boolean" ? (v ? "yes" : "no") : pretty(v)}`)
    .join(" · ");
  if (ctx) {
    const line = document.createElement("div");
    line.className = "ctx";
    line.textContent = ctx;
    box.append(line);
  }

  // Every option is always a button — board-click and hand-click are
  // just shortcuts on top of this complete, safe fallback.
  const row = document.createElement("div");
  row.className = "options";
  for (const o of d.options) {
    const b = document.createElement("button");
    b.textContent = optionLabel(o);
    b.addEventListener("click", () => act(o.index));
    row.append(b);
  }
  box.append(row);
}

function renderWinner() {
  const overlay = $("#winner");
  if (!state.is_terminal) {
    overlay.hidden = true;
    return;
  }
  overlay.hidden = false;
  const name = state.winner === "US" ? "USA" : state.winner === "USSR" ? "CCCP" : "Nobody";
  overlay.innerHTML = `<div class="cardbig">${name} wins<br><small>${state.game_over_reason || ""}
    <br><a href="javascript:location.reload()">new game</a> (restart serve_ui.py with a new --seed)</small></div>`;
}

boot();
