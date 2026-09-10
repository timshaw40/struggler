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
let playing = true;   // watch mode playback
let inFlight = false; // one poll chain at a time

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
    // VASSAL install ships board-calibrated marker positions; fall back to
    // the schematic calibration for non-VASSAL art.
    fetchJson("/assets/countries.json").catch(() => fetchJson("/countries.json")),
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
  enableDragPan();
  window.addEventListener("resize", layoutBoard);
  // once the image has real dimensions, re-fit with the true aspect ratio
  $("#board").addEventListener("load", layoutBoard);
  layoutBoard();
  await refresh();
  if (state.watch) setTimeout(tick, 400);
}

/* Zoom has one source of truth: --boardw, the board's rendered width. The
 * image and the chip font both derive from it in CSS, so they can never
 * drift apart — no matter how you zoom or resize the window, a chip stays
 * the same fraction of a country box. */
function layoutBoard() {
  const wrap = $("#boardwrap");
  const board = $("#board");
  if (wrap.classList.contains("noboard")) return;
  const bw = board.naturalWidth || 5100;
  const bh = board.naturalHeight || 3300;
  const base = Math.min(wrap.clientWidth / bw, wrap.clientHeight / bh) * bw - 4;
  wrap.style.setProperty("--boardw", Math.round(base * zoom) + "px");
}

/* Click-and-drag panning: hold the mouse anywhere on the map and drag; the
 * scrollable #boardwrap follows. A drag never counts as a marker click. */
function enableDragPan() {
  const wrap = $("#boardwrap");
  wrap.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const sx = e.clientX, sy = e.clientY, sl = wrap.scrollLeft, st = wrap.scrollTop;
    let moved = 0;
    const move = (ev) => {
      moved = Math.max(moved, Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy));
      wrap.scrollLeft = sl - (ev.clientX - sx);
      wrap.scrollTop = st - (ev.clientY - sy);
    };
    const up = () => {
      dragMoved = moved;
      wrap.classList.remove("dragging");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    wrap.classList.add("dragging");
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    e.preventDefault();
  });
}

let dragMoved = 0;

function setZoom(next) {
  zoom = Math.min(6, Math.max(1, next));
  layoutBoard();
}

async function refresh() {
  state = await fetchJson("/state");
  render();
}

/* Watch mode (bot vs bot): the server resolves exactly one move per
 * /state poll, so this chain paces playback. MCTS think time dominates;
 * the interval just catches instant steps (chance rolls, setup). */
function tick() {
  if (inFlight) return;
  inFlight = true;
  refresh().finally(() => {
    inFlight = false;
    if (state && state.watch && playing && !state.is_terminal) setTimeout(tick, 400);
  });
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

let countryTip = null;

/* Hover readout for a map marker: an amplification of the country's printed
 * header strip (flag | name | stability badge — red badge = battleground),
 * plus live influence. Falls back to plain text when a header asset is
 * missing. Positioned above the marker, viewport-clamped. */
function showCountryTip(el, cid, inf) {
  if (!countryTip) {
    countryTip = document.createElement("div");
    countryTip.id = "countrytip";
    countryTip.hidden = true;
    document.body.append(countryTip);
  }
  countryTip.innerHTML =
    `<img class="chead" src="/assets/headers/${cid}.png" alt="${pretty(cid)}">`
    + `<div class="cstats"><span class="us">US ${inf.US}</span> · `
    + `<span class="ussr">USSR ${inf.USSR}</span></div>`;
  const img = countryTip.querySelector("img");
  img.addEventListener("error", () => {
    img.remove();
    const name = document.createElement("div");
    name.className = "cname";
    name.textContent = pretty(cid);
    countryTip.prepend(name);
  });
  countryTip.hidden = false;
  const r = el.getBoundingClientRect();
  const w = countryTip.offsetWidth;
  const h = countryTip.offsetHeight;
  let left = r.left + r.width / 2 - w / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  let top = r.top - h - 12;
  if (top < 8) top = r.bottom + 12;
  countryTip.style.left = `${left}px`;
  countryTip.style.top = `${top}px`;
}

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
    el.innerHTML =
      `<span class="us">${inf.US}</span><span class="ussr">${inf.USSR}</span>`;
    el.addEventListener("mouseenter", () => showCountryTip(el, cid, inf));
    el.addEventListener("mouseleave", () => { countryTip.hidden = true; });
    if (target !== null) {
      el.addEventListener("click", () => {
        if (dragMoved > 6) return;  // that was a map drag, not a click
        act(target);
      });
    }
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
  el.addEventListener("mouseenter", () => showPreview(cid, el));
  el.addEventListener("mouseleave", () => { previewEl.hidden = true; });
  if (actionIndex !== null) el.addEventListener("click", () => act(actionIndex));
  return el;
}

/* Hover preview: floats just above the hovered card, viewport-clamped
 * (it drops below the card when there is no room above). */
function showPreview(cid, card) {
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
  const r = card.getBoundingClientRect();
  const w = previewEl.offsetWidth;
  const h = previewEl.offsetHeight;
  let left = r.left + r.width / 2 - w / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  let top = r.top - h - 8;
  if (top < 8) top = r.bottom + 8;
  previewEl.style.left = `${left}px`;
  previewEl.style.top = `${top}px`;
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
  const d = state.decision;
  if (!d && !busy) {
    if (state.watch && !state.is_terminal) {
      const note = document.createElement("em");
      note.textContent = playing ? "Watching · bot vs bot" : "Watching · paused";
      const btn = document.createElement("button");
      btn.textContent = playing ? "Pause" : "Resume";
      btn.addEventListener("click", () => {
        playing = !playing;
        render();
        if (playing) tick();
      });
      box.append(note, btn);
      return;
    }
    box.hidden = true;
    return;
  }
  box.hidden = false;
  if (!d) {
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

  // Country-picking happens on the map: the glowing markers are the options
  // (every one of this decision's options names a country).
  if (d.options.length && d.options.every((o) => o.payload && o.payload.country)) {
    const hint = document.createElement("em");
    hint.className = "hint";
    hint.textContent = "Click a glowing country on the map.";
    box.append(hint);
    return;
  }
  for (const o of d.options) {
    const b = document.createElement("button");
    b.textContent = optionLabel(o);
    b.addEventListener("click", () => act(o.index));
    box.append(b);
  }
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
