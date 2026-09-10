"use strict";

/* Struggler web UI. All game state comes from /state (the human seat's
 * observation); every action is one of the pending decision's options,
 * posted by index — the server never invents one and neither do we. */

const META = {};    // card id -> {number, name, ops, side, scoring, event_summary, ...}
const IMAGES = {};  // card id -> card-face filename under /assets/cards/ (optional)
const POS = {};     // country id -> {x, y} board fractions, s stability

const BOARD_W = 5100, BOARD_H = 3300;
/* Region views: rectangles of the board image (box layout, as measured by
 * the asset installer). A region renders its slice across the viewport
 * width, so rects run a similar native width (~1600px) for consistent
 * magnification — and each carries ~half a box of margin, so countries on
 * the edge render whole instead of sliced at the scroll boundary. World
 * fits the whole board. Overlapping bounds (Mid-East spans Africa's
 * latitude band) resolve by lookup order — smaller regions first. */
const REGIONS = {
  "World": [0, 0, BOARD_W, BOARD_H],
  "C. America": [0, 1140, 1590, 2070],
  "S. America": [390, 1710, 2060, 3090],
  "Mid-East": [2370, 960, 4040, 1940],
  "Europe": [1510, 140, 3170, 1390],
  "Asia": [3440, 860, 5100, 2790],
  "Africa": [1590, 1260, 3280, 2890],
};
let view = "Europe";

let state = null;
let busy = false;
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
    // VASSAL install ships box centers measured off the board; fall back
    // to the schematic calibration for non-VASSAL art.
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
  enableDragPan();
  buildViewBar();
  window.addEventListener("resize", layoutBoard);
  setView(view);
  await refresh();
  if (state.watch) setTimeout(tick, 400);
}

/* The map renders the current view's slice across the available width. The
 * width is still written as one CSS variable — --boardw, the only knob —
 * so the chip size in CSS derives from the same number the map scales by;
 * they can't disagree. Measure the stable parent (#boardarea, min-width 0:
 * always exactly the viewport): measuring the scroll container itself feeds
 * the zoomed content width back into the next computation and diverges.
 * A region is taller than the window at that scale: vertical scroll (or
 * drag) covers it, as before. */
function layoutBoard() {
  const wrap = $("#boardwrap");
  if (wrap.classList.contains("noboard")) return;
  const reg = REGIONS[view];
  const w = Math.round($("#boardarea").clientWidth * BOARD_W / (reg[2] - reg[0]));
  wrap.style.setProperty("--boardw", w + "px");
}

/* View switcher: buttons over the map's top-right corner. */
function buildViewBar() {
  const bar = document.createElement("div");
  bar.id = "viewbar";
  for (const name of Object.keys(REGIONS)) {
    const b = document.createElement("button");
    b.textContent = name;
    b.addEventListener("click", () => setView(name));
    bar.append(b);
  }
  $("#boardarea").append(bar);
}

function setView(name) {
  view = name;
  for (const b of document.querySelectorAll("#viewbar button"))
    b.classList.toggle("active", b.textContent === name);
  layoutBoard();
  const wrap = $("#boardwrap");
  const k = $("#boardbox").offsetWidth / BOARD_W;
  const [l, t] = REGIONS[name];
  wrap.scrollLeft = Math.max(0, l * k - 20);
  wrap.scrollTop = Math.max(0, t * k - 20);
  renderBoard();  // marker sizing is per-view; renderBoard guards null
}

let lastJump = "";
/* Country-picking decisions target one subregion, so the map follows the
 * action: switch to the region holding the most legal targets. (Canada is
 * Europe-scoring but sits west of the Europe rect — requiring every target
 * inside a rect sent the setup jump to World.) Same target set is skipped
 * so re-renders never yank the view back. */
function jumpToTargets(d) {
  const key = d.options.map((o) => o.index).join(",");
  if (key === lastJump) return;
  const pts = d.options
    .map((o) => POS[o.payload.country])
    .filter(Boolean)
    .map((p) => [p.x * BOARD_W, p.y * BOARD_H]);
  if (!pts.length) return;
  lastJump = key;
  const inside = (reg, x, y) =>
    reg[0] <= x && x <= reg[2] && reg[1] <= y && y <= reg[3];
  let best = view, bestN = 0;
  for (const n of Object.keys(REGIONS)) {
    if (n === "World") continue;
    const c = pts.filter(([x, y]) => inside(REGIONS[n], x, y)).length;
    if (c > bestN) { best = n; bestN = c; }
  }
  if (best !== view) setView(best);
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
  countryTip.hidden = false;
  // Positioning must happen after the strip image decodes: until then the
  // tip's height is just the stats line, and the loading image would grow
  // the tip downward onto the very country being hovered.
  const place = () => {
    const r = el.getBoundingClientRect();
    const w = countryTip.offsetWidth;
    const h = countryTip.offsetHeight;
    let left = r.left + r.width / 2 - w / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    let top = r.top - h - 12;
    if (top < 8) top = r.bottom + 12;
    countryTip.style.left = `${left}px`;
    countryTip.style.top = `${top}px`;
  };
  place();
  const img = countryTip.querySelector("img");
  img.addEventListener("load", place);
  img.addEventListener("error", () => {
    img.remove();
    const name = document.createElement("div");
    name.className = "cname";
    name.textContent = pretty(cid);
    countryTip.prepend(name);
    place();
  });
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
  if (!state) return;
  $("#board").onerror = () => $("#boardwrap").classList.add("noboard");
  const host = $("#markers");
  host.textContent = "";
  if ($("#boardwrap").classList.contains("noboard")) return;
  const d = state.decision;
  for (const [cid, inf] of Object.entries(state.influence)) {
    const pos = POS[cid];
    const target = d ? countryOption(cid) : null;
    // No influence and not a legal target: blank box, VASSAL-style.
    // A 0/0 legal target still renders (empty glow, no pips) so it stays clickable.
    if (!pos || (target === null && inf.US === 0 && inf.USSR === 0)) continue;
    const el = document.createElement("div");
    el.className = "marker" + (target !== null ? " legal" : "");
    el.style.left = pos.x * 100 + "%";
    el.style.top = pos.y * 100 + "%";
    // Pips scale to the country's own body (tall bodies get bigger pips):
    // a side lands near 4/5 of body height, sitting in the influence
    // columns below the strip VASSAL-style. em, so it tracks zoom for
    // free; World keeps standard size (overview, floor chips).
    // Schematic fallback has no h and stays standard everywhere.
    if (view !== "World" && pos.h) {
      const boxF = Math.min((pos.h - 32) * 0.0132, 1.6);
      if (boxF > 1.01) el.style.fontSize = boxF.toFixed(2) + "em";
    }
    const ctrl = controlOf(cid, inf);
    el.innerHTML = pip("us", inf.US, ctrl === "US") + pip("ussr", inf.USSR, ctrl === "USSR");
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

/* Which side (if any) controls the country: influence margin >= stability —
 * the engine's own rule (board.control), mirrored here so each pip shows
 * the right face. Schematic fallback countries carry no stability: no
 * faces there. */
function controlOf(cid, inf) {
  const s = POS[cid] && POS[cid].s;
  if (!s) return null;
  if (inf.US - inf.USSR >= s) return "US";
  if (inf.USSR - inf.US >= s) return "USSR";
  return null;
}

/* One side's influence pip: the VASSAL face (white while merely present,
 * colored once the side controls) with our count over it, VASSAL-style. */
function pip(side, n, controlled) {
  if (n === 0) return "";  // no chit for an empty side
  const face = controlled ? "controlled" : "uncontrolled";
  return `<span class="pip ${side}${controlled ? " controlled" : ""}">` +
    `<img src="/assets/markers/${side}_${face}.svg" alt="">` +
    `<b>${n}</b></span>`;
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
  // (every one of this decision's options names a country), and the map
  // follows the action by jumping to the region holding them.
  if (d.options.length && d.options.every((o) => o.payload && o.payload.country)) {
    jumpToTargets(d);
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
