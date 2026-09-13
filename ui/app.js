"use strict";

/* Struggler web UI. All game state comes from /state (the human seat's
 * observation); every action is one of the pending decision's options,
 * posted by index — the server never invents one and neither do we. */

const META = {};    // card id -> {number, name, ops, side, scoring, event_summary, ...}
const IMAGES = {};  // card id -> card-face filename under /assets/cards/ (optional)
const POS = {};     // country id -> {x, y} box top-left fractions, w/h px, s stability

const BOARD_W = 5100, BOARD_H = 3300;
/* Region views: rectangles of the board image (box layout, as measured by
 * the asset installer). A region renders its slice across the viewport
 * width, so rects run a similar native width (~1600px) for consistent
 * magnification — and each carries ~half a box of margin, so countries on
 * the edge render whole instead of sliced at the scroll boundary. World
 * fits the whole board. Overlapping bounds (Middle East spans Africa's
 * latitude band) resolve by lookup order — smaller regions first. */
const REGIONS = {
  "World": [0, 0, BOARD_W, BOARD_H],
  "Europe": [1510, 140, 3170, 1390],
  "Middle East": [2370, 960, 4040, 1940],
  "Asia": [3440, 860, 5100, 2790],
  "Africa": [1590, 1260, 3280, 2890],
  "Central America": [0, 1140, 1590, 2070],
  "South America": [390, 1710, 2060, 3090],
};
let view = "World";
let zoom = 1;  // 1..1.8, extra on top of the region fit

/* VASSAL SetupStack centers (native board px). Engine already tracks
 * every one of these; we just put the matching counter on the map. */
const DEFCON_AT = { 5: [1584, 2653], 4: [1741, 2653], 3: [1898, 2653], 2: [2055, 2653], 1: [2212, 2653] };
const MILOPS_AT = [[1586, 3009], [1743, 3009], [1900, 3009], [2057, 3009], [2214, 3009], [2371, 3009]];
const ROUND_AT = [[856, 258], [973, 258], [1091, 258], [1208, 258], [1326, 258], [1443, 258], [1561, 258], [1678, 258], [1796, 258]];
const TURN_AT = [null, [3538, 229], [3693, 229], [3848, 229], [4003, 229], [4158, 229], [4313, 229], [4468, 229], [4623, 229], [4778, 229], [4933, 229]];
const SPACE_AT = [[3541, 583], [3711, 583], [3881, 583], [4051, 583], [4221, 583], [4391, 583], [4561, 583], [4731, 583], [4901, 583]];
const VP_AT = [[3160, 2523], [3361, 2523], [3495, 2523], [3629, 2523], [3763, 2523], [3897, 2523], [4031, 2523], [4165, 2523], [3093, 2664], [3227, 2664], [3361, 2664], [3495, 2664], [3629, 2664], [3763, 2664], [3897, 2664], [4031, 2664], [4165, 2664], [3093, 2805], [3227, 2805], [3361, 2805], [3629, 2805], [3897, 2805], [4031, 2805], [4165, 2805], [3093, 2946], [3227, 2946], [3361, 2946], [3495, 2946], [3629, 2946], [3763, 2946], [3897, 2946], [4031, 2946], [4165, 2946], [3093, 3087], [3227, 3087], [3361, 3087], [3495, 3087], [3629, 3087], [3763, 3087], [3897, 3087], [4094, 3087]];

let state = null;
let busy = false;
let previewEl = null;
let actionBar = null;
let playing = true;   // watch mode playback
let winnerFocused = false;
let inFlight = false; // one poll chain at a time
let lastRenderSig = null;          // skip redundant full re-renders
const expandedRows = new Set();    // history rows the user opened, by absolute index

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
  un_intervention: "with UN Intervention",
};

const pretty = (s) => String(s).replace(/_/g, " ");
const cardName = (cid) => (META[cid] && META[cid].name) || pretty(cid);
// Escape any string interpolated into innerHTML: engine ids are safe today,
// but this closes the injection seam if a future value is free-form.
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

/* Surface a failure to the player instead of only the console. Auto-hides;
 * click to dismiss. */
function showError(msg) {
  const t = $("#toast");
  if (!t) { console.error(msg); return; }
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(showError._t);
  showError._t = setTimeout(() => { t.hidden = true; }, 8000);
}

/* Global shortcuts: 1–9 / Enter pick a decision option, Esc closes overlays,
 * arrows pan the map. Ignored while typing in a control or a modifier is held. */
function installKeyboard() {
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target && e.target.tagName) || "";
    // Let a focused button/control handle its own Enter/Space/arrows.
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") return;

    if (e.key === "Escape") {
      const s = $("#settings");
      if (s && !s.hidden) { s.hidden = true; return; }
      if (previewEl) previewEl.hidden = true;
      return;
    }
    if (e.key.startsWith("Arrow")) {
      const wrap = $("#boardwrap");
      const step = 80;
      if (e.key === "ArrowLeft") wrap.scrollLeft -= step;
      else if (e.key === "ArrowRight") wrap.scrollLeft += step;
      else if (e.key === "ArrowUp") wrap.scrollTop -= step;
      else if (e.key === "ArrowDown") wrap.scrollTop += step;
      else return;
      e.preventDefault();
      return;
    }
    if (busy) return;
    const btns = [...document.querySelectorAll("#decision .dcol-main > button:not(.backbtn)")];
    if (!btns.length) return;
    if (e.key === "Enter") { btns[0].click(); e.preventDefault(); return; }
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= btns.length) { btns[n - 1].click(); e.preventDefault(); }
  });
}

async function boot() {
  try {
    await bootInner();
  } catch (err) {
    console.error(err);
    showError("Failed to load the game: " + err.message);
    busy = false;
    render();
  }
}

async function bootInner() {
  // The board is ~13 MB: keep a placeholder up until it has decoded.
  const boardImg = $("#board");
  const hideBoardLoad = () => { const l = $("#boardload"); if (l) l.hidden = true; };
  if (boardImg && boardImg.complete) hideBoardLoad();
  else if (boardImg) {
    boardImg.addEventListener("load", hideBoardLoad);
    boardImg.addEventListener("error", hideBoardLoad);  // don't cover the fallback
  }
  const toast = $("#toast");
  if (toast) toast.addEventListener("click", () => { toast.hidden = true; });
  installKeyboard();

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
  actionBar = $("#actionbar");
  enableDragPan();
  buildViewBar();
  window.addEventListener("resize", layoutBoard);
  setView(view);
  busy = true;
  await refresh();
  if (state.watch) setTimeout(tick, 400);  // paced playback; catchUp is for bot replies
  else await catchUp();
  busy = false;
  render();
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
  const w = Math.round($("#boardarea").clientWidth * BOARD_W / (reg[2] - reg[0]) * zoom);
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
  const slider = document.createElement("input");
  slider.id = "zoom";
  slider.type = "range";
  slider.min = "100";
  slider.max = "180";
  slider.value = "100";
  slider.title = "Zoom";
  slider.setAttribute("aria-label", "Zoom");
  slider.addEventListener("input", () => setZoom(+slider.value / 100));
  $("#boardarea").append(slider);
}

function setZoom(z) {
  const wrap = $("#boardwrap");
  const old = $("#boardbox").offsetWidth || 1;
  const cx = wrap.scrollLeft + wrap.clientWidth / 2;
  const cy = wrap.scrollTop + wrap.clientHeight / 2;
  zoom = z;
  layoutBoard();
  const k = $("#boardbox").offsetWidth / old;
  wrap.scrollLeft = Math.max(0, cx * k - wrap.clientWidth / 2);
  wrap.scrollTop = Math.max(0, cy * k - wrap.clientHeight / 2);
}

function setView(name) {
  view = name;
  zoom = 1;  // region/world fit is the country-level default
  const slider = $("#zoom");
  if (slider) slider.value = "100";
  if (countryTip) countryTip.hidden = true;
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



/* Click-and-drag panning: hold the mouse anywhere on the map and drag; the
 * scrollable #boardwrap follows. A drag never counts as a marker click. */
let dragCleanup = null;

function enableDragPan() {
  const wrap = $("#boardwrap");
  wrap.addEventListener("dragstart", (e) => e.preventDefault());
  wrap.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    // If a previous drag ended off-window (no mouseup), its listeners are
    // still attached; drop them before adding a fresh pair.
    if (dragCleanup) dragCleanup();
    dragMoved = 0;
    const sx = e.clientX, sy = e.clientY, sl = wrap.scrollLeft, st = wrap.scrollTop;
    let moved = 0;
    const move = (ev) => {
      moved = Math.max(moved, Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy));
      if (moved <= 6) return;
      ev.preventDefault();
      wrap.classList.add("dragging");
      wrap.scrollLeft = sl - (ev.clientX - sx);
      wrap.scrollTop = st - (ev.clientY - sy);
    };
    const cleanup = () => {
      dragMoved = moved;
      wrap.classList.remove("dragging");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", cleanup);
      window.removeEventListener("blur", cleanup);
      dragCleanup = null;
    };
    dragCleanup = cleanup;
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", cleanup);
    window.addEventListener("blur", cleanup);  // released outside the window
  });
}

let dragMoved = 0;

async function refresh() {
  try {
    state = await fetchJson("/state");
  } catch (err) {
    showError("Lost connection to the game server.");
    throw err;
  }
  render();
}

/* Watch mode (bot vs bot): the server resolves exactly one move per
 * /state poll, so this chain paces playback. MCTS think time dominates;
 * the interval just catches instant steps (chance rolls, setup). */
function tick() {
  if (inFlight) return;
  inFlight = true;
  refresh().catch(() => {}).finally(() => {
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
    showError("That move could not be sent: " + err.message);
  } finally {
    busy = false;
    render();
  }
}

function placeSound() {
  if (localStorage.getItem("struggler.sound") === "0") return;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  const ctx = placeSound.ctx || (placeSound.ctx = new AC());
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.type = "square";
  o.frequency.value = 880;
  g.gain.setValueAtTime(0.07, ctx.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.08);
  o.connect(g).connect(ctx.destination);
  o.start();
  o.stop(ctx.currentTime + 0.09);
}

/* US chits fly in from the left edge, USSR from the right, then a short
 * click. Overlay only — the board already shows the new count. */
let prevInf = null;
/* Any influence increase (human or bot) flies a chit. First snapshot is
 * silent so the opening board doesn't rain counters. Stagger a batch so
 * a bot dump of 6 doesn't stack on one frame. */
function flyDiff() {
  const inf = state.influence || {};
  const jobs = [];
  if (prevInf) {
    for (const [cid, now] of Object.entries(inf)) {
      const was = prevInf[cid] || { US: 0, USSR: 0 };
      for (const side of ["US", "USSR"]) {
        const n = (now[side] || 0) - (was[side] || 0);
        for (let i = 0; i < n; i++) jobs.push([cid, side]);
      }
    }
  }
  prevInf = {};
  for (const [cid, v] of Object.entries(inf))
    prevInf[cid] = { US: v.US, USSR: v.USSR };
  if (!jobs.length) return;
  // Placements animate after any queued card reveal finishes.
  fxGate.then(() => {
    jobs.forEach(([cid, side], i) => setTimeout(() => flyPip(cid, side), i * 120));
  });
}

function flyPip(cid, side) {
  const p = POS[cid];
  if (!p) return;
  placeSound();  // only for a placement that actually hits the board
  const box = $("#boardbox").getBoundingClientRect();
  const x = box.left + (p.x + (p.w || 0) / 2 / BOARD_W) * box.width;
  const y = box.top + (p.y + 0.62 * (p.h || 0) / BOARD_H) * box.height;
  const img = document.createElement("img");
  img.className = "flypip";
  img.src = `/assets/markers/${side.toLowerCase()}_uncontrolled.svg`;
  const startX = side === "USSR" ? innerWidth + 24 : -72;
  img.style.transform = `translate(${startX}px, ${y - 24}px)`;
  document.body.append(img);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    img.style.transform = `translate(${x - 24}px, ${y - 24}px)`;
  }));
  img.addEventListener("transitionend", () => img.remove());
}

const ROLL_KIND = {
  coup_roll: 1, war_roll: 1, space_race_roll: 1, contest_roll: 1,
  quagmire_roll: 1, realignment_actor_roll: 1, realignment_opponent_roll: 1,
};
const DIE = ["", "⚀", "⚁", "⚂", "⚃", "⚄", "⚅"];
let seenTotal = -1;
let pendingRealignActor = null;  // actor roll awaiting its opponent roll across polls
let diceChain = Promise.resolve();
let fxGate = Promise.resolve();  // opponent card reveals; pips and dice wait on it

function clearDiceBox() {
  const box = $("#dicebox");
  box.querySelector(".dice").textContent = "";
  box.querySelector(".doutcome").textContent = "";
  const old = box.querySelector(".playcard");
  if (old) old.remove();
}

/* Headline reveal: both cards center-screen in resolution order (higher
 * Ops first, ties US-first — the engine's own rule). A US Defectors
 * cancels the USSR headline outright. */
function showHeadlines(usCid, ussrCid) {
  return new Promise((resolve) => {
    const box = $("#dicebox");
    clearDiceBox();
    box.querySelector(".dtitle").textContent = "Headline Phase";
    const wrap = document.createElement("div");
    wrap.className = "hcards";
    const cancelled = usCid === "Defectors";
    const order = cancelled ? ["US"] : headlineOrder(usCid, ussrCid);
    for (const side of ["USSR", "US"]) {
      const cid = side === "US" ? usCid : ussrCid;
      const cell = document.createElement("div");
      const tag = document.createElement("div");
      tag.className = "htag";
      tag.textContent = side + (cancelled && side === "USSR" ? " — cancelled"
        : order[0] === side ? " — resolves first" : "");
      cell.append(tag);
      if (IMAGES[cid]) {
        const img = document.createElement("img");
        img.src = `/assets/cards/${IMAGES[cid]}`;
        img.alt = cardName(cid);
        cell.append(img);
      } else {
        const name = document.createElement("div");
        name.textContent = cardName(cid);
        cell.append(name);
      }
      wrap.append(cell);
    }
    box.querySelector(".dice").append(wrap);
    box.hidden = false;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      box.hidden = true;
      box.onclick = null;
      resolve();
    };
    box.onclick = finish;
    setTimeout(finish, 3200);
  });
}

function headlineOrder(usCid, ussrCid) {
  const ops = (cid) => (META[cid] && META[cid].ops) || 0;
  if (ops(usCid) === ops(ussrCid)) return ["US", "USSR"];
  return ops(usCid) > ops(ussrCid) ? ["US", "USSR"] : ["USSR", "US"];
}

/* Opponent card plays show the card face briefly, then the placements
 * fly in and the dice/scores follow. Own plays are skipped — the human
 * already sees their hand and the move box. */
function showCardPlay(cid, actor) {
  return new Promise((resolve) => {
    const box = $("#dicebox");
    const m = META[cid] || {};
    clearDiceBox();
    box.querySelector(".dtitle").textContent = `${actor} plays ${m.name || pretty(cid)}`;
    if (IMAGES[cid]) {
      const img = document.createElement("img");
      img.className = "playcard";
      img.src = `/assets/cards/${IMAGES[cid]}`;
      img.alt = m.name || cid;
      box.querySelector(".dice").append(img);
    }
    box.querySelector(".doutcome").textContent = m.event_summary || "";
    box.hidden = false;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      box.hidden = true;
      box.onclick = null;
      resolve();
    };
    box.onclick = finish;
    setTimeout(finish, 2300);
  });
}

function drainRolls() {
  const hist = state.history || [];
  // `hist` is a truncated window of the server's history, so use the
  // monotonic `history_len` as the cursor, not hist.length.
  const total = state.history_len ?? hist.length;
  if (seenTotal < 0) {
    seenTotal = total;
    // On a fresh load show the most recent action immediately (no replay of
    // the whole batch), so the banner has context before the next move.
    if (hist.length) showAction(hist[hist.length - 1], hist[hist.length - 2]);
    return;
  }
  const freshCount = Math.max(0, Math.min(total - seenTotal, hist.length));
  seenTotal = total;
  const fresh = hist.slice(hist.length - freshCount);
  const start = hist.length - fresh.length;
  const headlines = fresh.filter((e) => e.kind === "headline_play" && e.payload.card);
  if (headlines.length >= 2) {
    const bySide = {};
    for (const e of headlines) bySide[e.actor] = e.payload.card;
    if (bySide.US && bySide.USSR)
      fxGate = fxGate.then(() => showHeadlines(bySide.US, bySide.USSR));
  }
  for (const e of fresh) {
    if (e.kind === "action_round_play"
        && e.payload.card && e.actor !== state.human_side) {
      fxGate = fxGate.then(() => showCardPlay(e.payload.card, e.actor));
    }
  }
  diceChain = Promise.all([diceChain, fxGate]);
  const items = [];
  for (let i = 0; i < fresh.length; i++) {
    const e = fresh[i];
    const prev = hist[start + i - 1];
    if (e.kind === "realignment_actor_roll") {
      if (pendingRealignActor) {  // a prior actor roll never got its opponent roll
        items.push({ kind: "realignment_actor_roll", e: pendingRealignActor.actor, prev: pendingRealignActor.prev });
        pendingRealignActor = null;
      }
      if (fresh[i + 1] && fresh[i + 1].kind === "realignment_opponent_roll") {
        items.push({ kind: "realignment", actor: e, opp: fresh[i + 1], prev });
        i++;
      } else {
        // Watch mode resolves one step per poll, so the two rolls arrive in
        // separate batches: hold the actor roll for its opponent roll.
        pendingRealignActor = { kind: "realignment", actor: e, prev };
      }
      continue;
    }
    if (e.kind === "realignment_opponent_roll" && pendingRealignActor) {
      pendingRealignActor.opp = e;
      items.push(pendingRealignActor);
      pendingRealignActor = null;
      continue;
    }
    if (ROLL_KIND[e.kind]) items.push({ kind: e.kind, e, prev });
  }
  for (const item of items) diceChain = diceChain.then(() => showDice(item));
  for (let i = 0; i < fresh.length; i++) {
    const e = fresh[i];
    if (e.payload.card && /_Scoring$/.test(e.payload.card)) {
      const prev = hist[start + i - 1];
      diceChain = diceChain.then(() => showScore(e, prev));
    }
  }
  // Center-of-map caption: one update per fresh event, in cursor order.
  // Driven by `fresh` (not `items`, which re-emits a buffered realignment
  // actor roll) so it never double-shows.
  for (let i = 0; i < fresh.length; i++) {
    showAction(fresh[i], hist[start + i - 1]);
  }
}

function showScore(e, prev) {
  return new Promise((resolve) => {
    const box = $("#dicebox");
    clearDiceBox();
    box.querySelector(".dtitle").textContent = cardName(e.payload.card);
    const d = prev ? e.vp - prev.vp : 0;
    const swing = d > 0 ? `US +${d}` : d < 0 ? `USSR +${-d}` : "no swing";
    box.querySelector(".doutcome").textContent =
      prev ? `${swing} · VP ${prev.vp} → ${e.vp}` : `${swing} · VP ${e.vp}`;
    box.hidden = false;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      box.hidden = true;
      box.onclick = null;
      resolve();
    };
    box.onclick = finish;
    setTimeout(finish, 3200);
  });
}

function rollValues(item) {
  if (item.kind === "realignment") return [item.actor.payload.value, item.opp.payload.value];
  const p = item.e.payload;
  if (p.sponsor_roll) return [p.sponsor_roll, p.defender_roll].filter(Boolean);
  return [p.value];
}

function rollTitle(item) {
  const e = item.e || item.actor;
  const c = e.context || {};
  if (item.kind === "realignment") return `${c.side} realigns ${pretty(c.country)}`;
  if (e.kind === "coup_roll") return `${c.side} coups ${pretty(c.country)}`;
  if (e.kind === "war_roll") return `${cardName(c.card)} — ${pretty(c.target)}`;
  if (e.kind === "space_race_roll") return `${c.side} attempts the space race`;
  if (e.kind === "quagmire_roll") return "Quagmire / Bear Trap";
  if (e.kind === "contest_roll") return cardName(c.event) || pretty(c.event);
  return pretty(e.kind);
}

function infLine(e, cid) {
  const inf = e.country_influence;
  if (!inf || !cid) return "";
  const ctrl = e.country_control ? `, ${e.country_control} controls` : "";
  return ` ${pretty(cid)} is now US ${inf.US} / USSR ${inf.USSR}${ctrl}.`;
}

function rollOutcome(item) {
  const e = item.e || item.opp;
  const c = (item.e || item.actor).context || {};
  const prev = item.prev;
  if (item.kind === "realignment") {
    return `${c.side} rolled ${item.actor.payload.value}, opponent ${item.opp.payload.value}.`
      + infLine(item.opp, c.country);
  }
  const n = e.payload.value;
  let out = "";
  if (e.kind === "coup_roll") {
    out = `Rolled ${n}.` + infLine(e, c.country);
    if (prev && e.defcon < prev.defcon) out += ` DEFCON drops to ${e.defcon}.`;
  } else if (e.kind === "war_roll") {
    out = `Rolled ${n}.` + infLine(e, c.target);
    if (prev && e.vp !== prev.vp) out += e.vp > (prev.vp || 0) ? " Attacker scores VP." : " VP shifts.";
  } else if (e.kind === "space_race_roll") {
    const before = prev && prev.space_race ? prev.space_race[c.side] : 0;
    const now = (e.space_race || {})[c.side];
    out = `Rolled ${n}. ` + (now > before ? `${c.side} advances to box ${now}.` : `${c.side} fails to advance.`);
    if (prev && e.vp !== prev.vp) out += " VP scored.";
  } else if (e.kind === "quagmire_roll") {
    out = n <= 4 ? `Rolled ${n} — free of the trap.` : `Rolled ${n} — still trapped.`;
  } else if (e.kind === "contest_roll") {
    const p = e.payload;
    out = `Sponsor ${p.sponsor_roll}, defender ${p.defender_roll}.`;
    if (prev && e.vp !== prev.vp) out += " Winner takes VP.";
  } else {
    out = `Rolled ${n}.`;
  }
  return out;
}

function showDice(item) {
  return new Promise((resolve) => {
    const box = $("#dicebox");
    clearDiceBox();
    box.querySelector(".dtitle").textContent = rollTitle(item);
    const dice = box.querySelector(".dice");
    const vals = rollValues(item);
    box.hidden = false;
    let n = 0;
    const tick = setInterval(() => {
      dice.textContent = vals.map(() => DIE[(Math.random() * 6 | 0) + 1]).join(" ");
      if (++n < 14) return;
      clearInterval(tick);
      dice.textContent = vals.map((v) => DIE[v]).join(" ");
      box.querySelector(".doutcome").textContent = rollOutcome(item);
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        box.hidden = true;
        box.onclick = null;
        resolve();
      };
      box.onclick = finish;
      setTimeout(finish, 2800);
    }, 70);
  });
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
  if ("choice" in p) {
    // Same friendly mapping the banner/feed use, so buttons and log agree.
    if (p.choice in CHOICE_WORDS) return CHOICE_WORDS[p.choice];
    if (META[p.choice]) return cardName(p.choice);
    return pretty(p.choice);
  }
  for (const key of ["type", "order"]) {
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
  // Works in every view: markers are positioned in board coordinates and the
  // tip is placed from the element's viewport rect, so region views are fine.
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

/* The side actually acting: CHANCE decisions carry the real side in their
 * context (side/owner/attacker/sponsor), never on `actor`. */
function actorOf(e) {
  if (e.actor && e.actor !== "CHANCE") return e.actor;
  const c = e.context || {};
  return c.side || c.owner || c.attacker || c.sponsor || "CHANCE";
}

const TRAP_NAMES = { bear_trap: "Bear Trap", quagmire: "Quagmire" };
const CHOICE_WORDS = {
  none: "nothing", stop: "stop", done: "done", skip: "skip", refuse: "refuse",
  decline: "decline", participate: "participate", boycott: "boycott",
  end_game: "end the game", reshuffle_now: "reshuffle",
};
const choiceWord = (c) => CHOICE_WORDS[c] || pretty(c);

/* The center-of-map caption for one resolved event. Returns "" for internal
 * steps (keep the previous caption up). Covers every DecisionKind; anything
 * unforeseen falls back to `feedSummary`. */
function actionText(e, prev) {
  const who = actorOf(e);
  const c = e.context || {};
  const p = e.payload || {};
  const kind = e.kind;
  if (kind === "event_resume" || kind === "deal_card") return "";

  let line;
  if (kind === "headline_play") {
    if (!p.card || p.card === "reshuffle_now") return "";
    line = `${who} headlines ${cardName(p.card)}`;
  } else if (kind === "action_round_play") {
    if (!p.card) return "";
    line = `${who} plays ${cardName(p.card)}`;
    if (/_Scoring$/.test(p.card)) line += ` — ${vpSwing(e, prev)}`;
  } else if (kind === "play_mode") {
    if (!c.card) return "";
    const mode = p.mode === "un_intervention"
      ? "with UN Intervention (event cancelled)"
      : (MODE_LABELS[p.mode] || pretty(p.mode));
    line = `${who} plays ${cardName(c.card)} ${mode}`;
  } else if (kind === "ops_type") {
    const ops = c.ops != null ? ` ${c.ops}` : "";
    line = `${who} spends${ops} ops on ${pretty(p.type)}`;
    if (c.bonus) line += ` (+1 ${pretty(c.bonus)})`;
  } else if (kind === "coup_target") {
    line = `${who} targets ${pretty(p.country)} for a coup`;
  } else if (kind === "coup_roll") {
    line = `${who} coups ${pretty(c.country)} — rolled ${p.value}`;
  } else if (kind === "realignment_target") {
    line = `${who} targets a realignment in ${pretty(p.country)}`;
  } else if (kind === "realignment_actor_roll") {
    line = `${who} realigns ${pretty(c.country)} — rolled ${p.value}`;
  } else if (kind === "realignment_opponent_roll") {
    line = `${who} realigns ${pretty(c.country)} — ${c.actor_roll} vs ${p.value}`;
  } else if (kind === "space_race_roll") {
    line = `${who} attempts the space race — rolled ${p.value}`;
  } else if (kind === "event_ops_order") {
    const order = p.order === "event_first" ? "their event first" : "ops first";
    line = `${who} plays ${cardName(c.card)} for ops — ${order}`;
  } else if (kind === "war_target") {
    line = `${who} plays ${cardName(c.card)} — attacks ${pretty(p.country)}`;
  } else if (kind === "war_roll") {
    line = `${cardName(c.card)}: ${who} attacks ${pretty(c.target)} — rolled ${p.value}`;
  } else if (kind === "event_influence") {
    const verb = c.op === "remove" ? "removes influence from" : "adds influence to";
    line = `${cardName(c.event)}: ${who} ${verb} ${pretty(p.country)}`;
  } else if (kind === "event_choice") {
    const label = c.event ? `${cardName(c.event)}: ` : "";
    line = `${label}${who} chooses ${choiceWord(p.choice)}`;
  } else if (kind === "random_discard") {
    const owner = c.owner || who;
    if (c.purpose === "five_year_plan")
      line = `Five Year Plan: USSR discards ${cardName(p.card)} at random`;
    else if (c.purpose === "grain_sales")
      line = `Grain Sales: USSR reveals ${cardName(p.card)} to the US`;
    else if (c.purpose === "terrorism")
      line = `Terrorism: ${owner} discards ${cardName(p.card)}`;
    else
      line = `${owner} discards ${cardName(p.card)}`;
  } else if (kind === "contest_roll") {
    line = `${cardName(c.event)}: sponsor ${p.sponsor_roll} vs defender ${p.defender_roll}`;
  } else if (kind === "quagmire_discard") {
    const trap = TRAP_NAMES[c.key] || pretty(c.key);
    line = c.forced_scoring
      ? `${who} must play ${cardName(p.card)} (${trap})`
      : `${who} discards ${cardName(p.card)} to ${trap}`;
  } else if (kind === "quagmire_roll") {
    const trap = TRAP_NAMES[c.key] || pretty(c.key);
    line = `${who} rolls ${p.value} to escape ${trap} — ${p.value <= 4 ? "free" : "still trapped"}`;
  } else if (kind === "held_card_discard") {
    line = (!p.card || p.card === "none")
      ? `${who} keeps their held card`
      : `${who} discards held card ${cardName(p.card)}`;
  } else if (kind === "place_influence") {
    line = c.setup
      ? `${who} sets up ${pretty(p.country)}`
      : `${who} adds influence to ${pretty(p.country)}`;
  } else {
    line = feedSummary(e, prev);  // safe fallback for anything unforeseen
  }

  if (prev && e.defcon !== prev.defcon) line += ` · DEFCON ${prev.defcon} → ${e.defcon}`;
  return line;
}

function showAction(e, prev) {
  if (!actionBar) return;
  const text = actionText(e, prev);
  if (!text) return;  // internal step: leave the current caption up
  actionBar.textContent = text;
  actionBar.title = text;  // full text on hover (the pill may ellipsize)
  actionBar.hidden = false;
  actionBar.classList.remove("bump");
  void actionBar.offsetWidth;  // restart the pop
  actionBar.classList.add("bump");
  const live = $("#live");
  if (live) live.textContent = text;  // announced to screen readers
}

function clearAction() {
  if (actionBar) { actionBar.hidden = true; actionBar.textContent = ""; actionBar.title = ""; }
  const live = $("#live");
  if (live) live.textContent = "";
}

function render() {
  if (!state) return;
  // Skip redundant full re-renders: a change to any of these implies the DOM
  // needs rebuilding. This is what keeps the 60-row feed, hand, and markers
  // from being torn down every poll (and lets expanded rows survive).
  const d = state.decision;
  const sig = [
    state.seed, state.history_len, state.is_terminal, state.can_undo, busy, playing,
    d ? `${d.kind}:${d.options.length}` : "-",
  ].join("|");
  if (sig === lastRenderSig) return;
  lastRenderSig = sig;

  drainRolls();  // first: queue card reveals so pips and dice wait on them
  flyDiff();
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
  host.classList.toggle("busy", busy);  // no map picks while submitting
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
    if (pos.w && pos.h) {
      // Marker IS the country rectangle. Pips are % of this box, so they
      // stay in the ovals at every region zoom and slider zoom.
      el.classList.add("boxed");
      el.style.left = pos.x * 100 + "%";
      el.style.top = pos.y * 100 + "%";
      el.style.width = pos.w / BOARD_W * 100 + "%";
      el.style.height = pos.h / BOARD_H * 100 + "%";
      el.style.setProperty("--strip", (32 / pos.h * 100).toFixed(1) + "%");
    } else {
      el.style.left = pos.x * 100 + "%";
      el.style.top = pos.y * 100 + "%";
    }
    const ctrl = controlOf(cid, inf);
    el.innerHTML = pip("us", inf.US, ctrl === "US") + pip("ussr", inf.USSR, ctrl === "USSR");
    el.setAttribute("aria-label", `${pretty(cid)} — US ${inf.US} / USSR ${inf.USSR}`);
    el.addEventListener("mouseenter", () => showCountryTip(el, cid, inf));
    el.addEventListener("mouseleave", () => { if (countryTip) countryTip.hidden = true; });
    if (target !== null) {
      el.classList.add("actionable");
      el.tabIndex = 0;
      el.setAttribute("role", "button");
      el.addEventListener("click", () => {
        if (dragMoved > 6) return;  // that was a map drag, not a click
        act(target);
      });
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (dragMoved <= 6) act(target); }
      });
    }
    host.append(el);
  }
  renderTracks(host);
}

function renderTracks(host) {
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n | 0));
  const tok = (name, xy, off, label) => {
    if (!xy) return;
    const el = document.createElement("img");
    el.className = "tracktok";
    el.src = `/assets/markers/${name}.svg`;
    el.alt = label || "";
    el.title = label || "";
    el.setAttribute("aria-label", label || "");
    el.style.left = ((xy[0] + (off || 0)) / BOARD_W * 100) + "%";
    el.style.top = ((xy[1] + (off || 0)) / BOARD_H * 100) + "%";
    host.append(el);
  };
  tok("defcon", DEFCON_AT[clamp(state.defcon, 1, 5)], 0, `DEFCON ${state.defcon}`);
  tok("vp", VP_AT[clamp(state.vp, -20, 20) + 20], 0, `VP ${state.vp}`);
  tok("turn", TURN_AT[clamp(state.turn, 1, 10)], 0, `Turn ${state.turn}`);
  const mil = state.military_ops || {};
  tok("milops_us", MILOPS_AT[clamp(mil.US, 0, 5)], 10, `US military ops ${mil.US}`);
  tok("milops_ussr", MILOPS_AT[clamp(mil.USSR, 0, 5)], -10, `USSR military ops ${mil.USSR}`);
  const sp = state.space_race || {};
  tok("space_us", SPACE_AT[clamp(sp.US, 0, 8)], 10, `US space race box ${sp.US}`);
  tok("space_ussr", SPACE_AT[clamp(sp.USSR, 0, 8)], -10, `USSR space race box ${sp.USSR}`);
  const hl = state.phase === "headline" || state.phase === "setup" || state.phase === "predeal";
  if (hl) tok("ar_headline", ROUND_AT[0], 0, "Headline phase");
  else tok((state.phasing || state.human_side) === "US" ? "ar_us" : "ar_ussr",
           ROUND_AT[clamp(state.action_round, 1, 8)], 0,
           `${(state.phasing || state.human_side)} action round`);
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
  row.innerHTML = `<span>${esc(label)}</span><b>${esc(value)}</b>`;
  return row;
}

function renderGameStatus() {
  const el = $("#gamestatus");
  if (!el || !state) return;
  const phase = state.phase === "action_rounds" ? `Round ${state.action_round}`
    : state.phase === "headline" ? "Headline"
    : state.phase === "setup" ? "Setup"
    : pretty(state.phase);
  const vp = state.vp;
  const vpText = vp === 0 ? "VP even" : vp > 0 ? `VP US +${vp}` : `VP USSR +${-vp}`;
  const vpCls = vp > 0 ? "us" : vp < 0 ? "ussr" : "";
  el.innerHTML =
    esc(`Turn ${state.turn} · ${phase} · `)
    + `<span class="defcon${state.defcon <= 2 ? " danger" : ""}">DEFCON ${state.defcon}</span>`
    + esc(" · ")
    + `<span class="${vpCls}">${esc(vpText)}</span>`;
}

function renderPanel() {
  renderGameStatus();

  const status = $("#status");
  status.textContent = "";
  status.append(
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
  const pileRow = (label, cards) => {
    if (!cards.length) return;
    const row = document.createElement("div");
    row.className = "kv pile";
    const names = cards.slice(-4).map(cardName).join(", ");
    row.title = "Show the whole pile";
    row.innerHTML = `<span>${esc(label)} (${cards.length})</span><b>${esc(names)}</b>`;
    const list = document.createElement("div");
    list.className = "pilelist";
    list.hidden = true;
    list.textContent = cards.map(cardName).join(", ");
    row.addEventListener("click", () => { list.hidden = !list.hidden; });
    piles.append(row, list);
  };
  pileRow("Discard", state.discard_pile);
  pileRow("Removed", state.removed_cards || []);

  const feed = $("#feed");
  feed.textContent = "";
  const title = document.createElement("h2");
  title.textContent = "History";
  feed.append(title);
  const waiting = busy || (state.watch && playing && !state.is_terminal);
  if (waiting) {
    const wait = document.createElement("div");
    wait.className = "feedrow wait";
    wait.textContent = state.watch ? "Playing…" : "Thinking…";
    feed.append(wait);
  }
  const hist = state.history || [];
  const total = state.history_len ?? hist.length;
  const rows = hist.slice().reverse().slice(0, 60);  // server sends 60
  rows.forEach((e, j) => {
    const older = rows[j + 1];  // reversed: next item is earlier in time
    const who = actorOf(e);
    const abs = total - 1 - j;  // monotonic id, stable across polls
    const row = document.createElement("div");
    row.className = "feedrow";
    const main = document.createElement("div");
    main.className = "feedmain";
    const label = document.createElement("span");
    let text = actionText(e, older) || pretty(e.kind);
    if (text.startsWith(who + " ")) text = text.slice(who.length + 1);  // actor is badged
    label.textContent = `${text} · T${e.turn} R${e.action_round}`;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "feedtoggle";
    const actor = document.createElement("b");
    actor.className = who === "USSR" ? "ussr" : "us";
    actor.textContent = who;
    main.append(label, toggle, actor);
    const detail = document.createElement("div");
    detail.className = "feeddetail";
    const open = expandedRows.has(abs);
    detail.hidden = !open;
    toggle.textContent = open ? "−" : "+";
    for (const line of eventDetail(e, older)) {
      const p = document.createElement("div");
      p.textContent = line;
      detail.append(p);
    }
    toggle.addEventListener("click", () => {
      const nowOpen = detail.hidden;
      detail.hidden = !nowOpen;
      toggle.textContent = nowOpen ? "−" : "+";
      if (nowOpen) expandedRows.add(abs); else expandedRows.delete(abs);
    });
    row.append(main, detail);
    feed.append(row);
  });
}

function vpSwing(e, older) {
  if (!older || e.vp === older.vp) return older ? "no swing" : "";
  const d = e.vp - older.vp;
  return d > 0 ? `US +${d}` : `USSR +${-d}`;
}

function feedSummary(e, older) {
  const t = `T${e.turn} R${e.action_round}`;
  const inf = e.country_influence;
  if ((e.kind === "place_influence" || e.kind === "event_influence") && e.country && inf)
    return `${pretty(e.kind)} · ${pretty(e.country)} → US ${inf.US} / USSR ${inf.USSR} · ${t}`;
  if (e.kind === "coup_roll" && e.country)
    return `coup · ${pretty(e.country)} · rolled ${e.payload.value} (ops ${e.context?.ops ?? "?"}) · ${t}`;
  if (e.kind === "realignment_opponent_roll" && e.country)
    return `realign · ${pretty(e.country)} · ${e.context?.actor_roll ?? "?"} vs ${e.payload.value} · ${t}`;
  if (e.payload.card && /Scoring$/.test(e.payload.card))
    return `${cardName(e.payload.card)} · ${vpSwing(e, older)} · ${t}`;
  const what = e.payload.card ? cardName(e.payload.card)
    : e.country ? pretty(e.country)
    : pretty(Object.values(e.payload)[0] ?? e.kind);
  return `${pretty(e.kind)} · ${what} · ${t}`;
}

function eventDetail(e, older) {
  const lines = [];
  if (older) {
    if (e.vp !== older.vp) {
      const d = e.vp - older.vp;
      lines.push(`VP ${older.vp} → ${e.vp} (${d > 0 ? `US +${d}` : `USSR +${-d}`})`);
    }
    if (e.defcon !== older.defcon) lines.push(`DEFCON ${older.defcon} → ${e.defcon}`);
  }
  if (e.country && e.country_influence) {
    const inf = e.country_influence;
    const ctrl = e.country_control ? `, ${e.country_control} controls` : "";
    lines.push(`${pretty(e.country)}: US ${inf.US} / USSR ${inf.USSR}${ctrl}`);
  }
  const c = e.context || {};
  if (c.ops !== undefined) lines.push(`Ops spent: ${c.ops}`);
  if (c.card && c.card !== (e.payload.card || null)) lines.push(`Card: ${cardName(c.card)}`);
  for (const k of ["mode", "type", "order", "choice"]) {
    if (c[k] !== undefined && c[k] !== null) lines.push(`${pretty(k)}: ${pretty(c[k])}`);
  }
  const p = e.payload || {};
  if (p.value !== undefined) lines.push(`Rolled: ${p.value}`);
  if (p.sponsor_roll !== undefined)
    lines.push(`Sponsor ${p.sponsor_roll} vs defender ${p.defender_roll}`);
  if (!lines.length) lines.push("No further detail.");
  return lines;
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
  if (actionIndex !== null) {
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.setAttribute("aria-label", `Play ${cardName(cid)}`);
    el.addEventListener("click", () => act(actionIndex));
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); act(actionIndex); }
    });
  }
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
  const pv = (state.score_preview || {})[cid];
  if (m.scoring && pv) {
    const box = document.createElement("div");
    box.className = "scorepreview";
    if (pv.wins) {
      box.innerHTML = `If played: <b>${pv.wins} wins outright</b> (${pretty(pv.region)} control)`;
    } else {
      const side = pv.net > 0 ? "us" : pv.net < 0 ? "ussr" : "";
      const swing = pv.net > 0 ? `US +${pv.net}` : pv.net < 0 ? `USSR +${-pv.net}` : "even";
      const tier = (s) => s.tier ? `${s.tier} ${s.vp}` : `${s.vp}`;
      box.innerHTML = `If played: <b class="${side}">${swing}</b> · `
        + `US ${tier(pv.us)} vs USSR ${tier(pv.ussr)} · VP ${state.vp} → ${pv.vp_after}`;
    }
    previewEl.append(box);
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

/* Human-readable decision context: curated keys only. Engine internals
 * (candidate lists, setup bookkeeping, placed objects) never reach the UI. */
function ctxLine(d) {
  const c = d.context || {};
  const parts = [];
  const val = (k, v) => {
    if (k === "card" || k === "event") return cardName(v);
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (typeof v === "object") return null;  // placed objects, etc.
    return pretty(v);
  };
  for (const k of ["card", "event", "ops", "ops_remaining", "remaining", "mode", "type", "order", "choice", "subregion", "bonus", "country"]) {
    if (c[k] === undefined || c[k] === null) continue;
    if ((k === "card" || k === "event") && (c[k] === "none" || c[k] === "HIDDEN_CARD")) continue;
    const v = val(k, c[k]);
    if (v === null) continue;
    parts.push(`<b>${esc(k === "event" ? "Event" : pretty(k))}</b> ${esc(v)}`);
  }
  return parts.join(" · ");
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
  const side = state.human_side === "USSR" ? "ussr" : "us";
  const head = document.createElement("div");
  head.className = "dhead";
  const move = document.createElement("div");
  move.className = `dside ${side}`;
  move.textContent = `${state.human_side} Move`;
  head.append(move);
  if (state.can_undo) {
    const back = document.createElement("button");
    back.type = "button";
    back.className = "backbtn";
    back.textContent = "← Back";
    back.addEventListener("click", goBack);
    head.append(back);
  }
  box.append(head);

  const played = (d.context || {}).card || (d.context || {}).event;
  const hasCard = played && played !== "none" && played !== "HIDDEN_CARD";
  const main = document.createElement("div");
  main.className = "dcol-main";
  if (hasCard) {
    const cols = document.createElement("div");
    cols.className = "dcols";
    const cardcol = document.createElement("div");
    cardcol.className = "dcol-card";
    const cap = document.createElement("div");
    cap.className = "playedcap";
    cap.textContent = "Played Card";
    cardcol.append(cap, cardEl(played, null));
    cols.append(main, cardcol);
    box.append(cols);
  } else {
    box.append(main);
  }

  const prompt = document.createElement("strong");
  prompt.textContent = PROMPTS[d.kind] || pretty(d.kind);
  main.append(prompt);

  const ctxHtml = ctxLine(d);
  if (ctxHtml) {
    const line = document.createElement("div");
    line.className = "ctx";
    line.innerHTML = ctxHtml;
    main.append(line);
  }

  // Country-picking happens on the map: the glowing markers are the options
  // (every one of this decision's options names a country). The view never
  // moves on its own — it stays where the player put it.
  if (d.options.length && d.options.every((o) => o.payload && o.payload.country)) {
    const hint = document.createElement("em");
    hint.className = "hint";
    hint.textContent = "Click a glowing country on the map.";
    main.append(hint);
    return;
  }
  for (const o of d.options) {
    const b = document.createElement("button");
    b.innerHTML = `<b>${esc(optionLabel(o))}</b>`;
    b.disabled = busy;  // a stale option while an action is in flight
    b.addEventListener("click", () => act(o.index));
    main.append(b);
  }
}

function recordOf(side) {
  try {
    const r = JSON.parse(localStorage.getItem("struggler.record") || "{}");
    return r[side] || { w: 0, l: 0 };
  } catch {
    return { w: 0, l: 0 };
  }
}

function bumpRecord(side, win) {
  const all = (() => {
    try { return JSON.parse(localStorage.getItem("struggler.record") || "{}"); }
    catch { return {}; }
  })();
  const s = all[side] || { w: 0, l: 0 };
  if (win) s.w += 1; else s.l += 1;
  all[side] = s;
  localStorage.setItem("struggler.record", JSON.stringify(all));
}

function fillSettings() {
  const box = $("#settings");
  const side = state ? state.human_side : "US";
  const rec = recordOf(side);
  const play = (localStorage.getItem("struggler.side") || side);
  const extra = localStorage.getItem("struggler.usExtra") ?? "2";
  box.innerHTML =
    `<p>You (${side}): ${rec.w}–${rec.l}</p>` +
    `<p>Seed ${state ? state.seed : "—"}</p>` +
    `<label><input type="checkbox" id="set-sound"${localStorage.getItem("struggler.sound") !== "0" ? " checked" : ""}> Sound</label>` +
    `<label><input type="checkbox" id="set-ccw"${localStorage.getItem("struggler.ccw") !== "0" ? " checked" : ""}> Chinese Civil War (next game)</label>` +
    `<p>Play as <em>(next game)</em></p>` +
    `<label><input type="radio" name="set-side" value="US"${play !== "USSR" ? " checked" : ""}> US</label>` +
    `<label><input type="radio" name="set-side" value="USSR"${play === "USSR" ? " checked" : ""}> USSR</label>` +
    `<label>US extra setup <em>(next game)</em> +<b id="set-extra-n">${extra}</b>` +
    `<input type="range" id="set-extra" min="0" max="6" value="${extra}"></label>` +
    `<div>` +
    (state && !state.watch ? `<button type="button" id="set-forfeit">Forfeit</button>` : "") +
    `<button type="button" id="set-new">New game</button>` +
    `<button type="button" id="set-reset" title="Clear the local win/loss record">Reset record</button></div>`;
  $("#set-sound").addEventListener("change", (e) => {
    localStorage.setItem("struggler.sound", e.target.checked ? "1" : "0");
  });
  $("#set-ccw").addEventListener("change", (e) => {
    localStorage.setItem("struggler.ccw", e.target.checked ? "1" : "0");
  });
  for (const r of document.querySelectorAll("input[name=set-side]")) {
    r.addEventListener("change", () => localStorage.setItem("struggler.side", r.value));
  }
  $("#set-extra").addEventListener("input", (e) => {
    localStorage.setItem("struggler.usExtra", e.target.value);
    $("#set-extra-n").textContent = e.target.value;
  });
  const f = $("#set-forfeit");
  if (f) f.addEventListener("click", forfeitGame);
  $("#set-new").addEventListener("click", newGame);
  $("#set-reset").addEventListener("click", () => {
    localStorage.removeItem("struggler.record");
    fillSettings();
  });
}

async function postGame(path) {
  if (busy) return;  // a second click must not forfeit/restart twice
  busy = true;
  render();
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        include_ccw: localStorage.getItem("struggler.ccw") !== "0",
        side: localStorage.getItem("struggler.side") || "US",
        setup_us_extra: +(localStorage.getItem("struggler.usExtra") ?? 2),
      }),
    });
    if (!res.ok) throw new Error(path + " " + res.status);
    const data = await res.json();
    if (data.error && !data.influence) throw new Error(data.error);
    if (data.forfeit && state) bumpRecord(state.human_side, false);
    seenTotal = -1;
    pendingRealignActor = null;
    prevInf = null;
    expandedRows.clear();
    clearAction();
    state = data;
    $("#settings").hidden = true;
    if (state.watch) {
      if (playing) setTimeout(tick, 400);  // resume paced watch playback
    } else {
      await catchUp();
    }
  } catch (err) {
    console.error(err);
    showError("Could not start the game: " + err.message);
  } finally {
    busy = false;
    render();
  }
}

async function catchUp() {
  let prev = -1, stall = 0;
  while (state && !state.is_terminal && !state.decision) {
    const n = state.history_len ?? (state.history || []).length;
    if (n === prev) {
      if (++stall > 3) break;
    } else stall = 0;
    prev = n;
    render();
    await refresh();
  }
}

function newGame() {
  // No prompt once the game is already over (the winner screen's own link).
  if (state && !state.is_terminal && !confirm("Start a new game? The current game is abandoned.")) return;
  return postGame("/new");
}

function forfeitGame() {
  if (busy || !confirm("Forfeit this game? Your opponent wins.")) return;
  return postGame("/forfeit");
}

async function goBack() {
  if (busy) return;  // a second click must not fire a second /back (409)
  busy = true;
  render();
  try {
    const res = await fetch("/back", { method: "POST" });
    if (!res.ok) throw new Error("/back " + res.status);
    state = await res.json();
    seenTotal = -1;
    pendingRealignActor = null;
    prevInf = null;
    expandedRows.clear();
    clearAction();
  } catch (err) {
    console.error(err);
    showError("Could not undo that move.");
  } finally {
    busy = false;
    render();
  }
}

function renderWinner() {
  const overlay = $("#winner");
  if (!state.is_terminal) {
    overlay.hidden = true;
    overlay.dataset.forSeed = "";
    winnerFocused = false;
    return;
  }
  clearAction();  // nothing should linger behind the game-over overlay
  // Record once per game, keyed by the (monotonic) seed: a browser reload of
  // the final screen must not pad the record with the same result again.
  if (state.winner && !state.watch) {
    const key = `struggler.recorded:${state.seed}`;
    if (!localStorage.getItem(key)) {
      bumpRecord(state.human_side, state.winner === state.human_side);
      localStorage.setItem(key, "1");
    }
  }
  overlay.hidden = false;
  // Build the dialog once per game; rebuilding every render would destroy the
  // focused "new game" link and drop keyboard focus.
  if (overlay.dataset.forSeed !== String(state.seed)) {
    overlay.dataset.forSeed = String(state.seed);
    winnerFocused = false;
    const name = state.winner === "US" ? "USA" : state.winner === "USSR" ? "CCCP" : "Nobody";
    overlay.innerHTML = `<div class="cardbig">${esc(name)} wins<br><small>${esc(state.game_over_reason || "")}
      <br><a href="#" id="again">new game</a></small></div>`;
    $("#again").addEventListener("click", (e) => { e.preventDefault(); newGame(); });
  }
  if (!winnerFocused) {  // move focus into the dialog once, not every render
    winnerFocused = true;
    $("#again").focus();
  }
}

function toggleSettings() {
  const box = $("#settings");
  if (!box) return;
  box.hidden = !box.hidden;
  if (!box.hidden) fillSettings();
}

boot();
