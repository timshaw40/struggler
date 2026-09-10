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
    .map((p) => [p.x * BOARD_W + (p.w || 0) / 2, p.y * BOARD_H + (p.h || 0) / 2]);
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
  wrap.addEventListener("dragstart", (e) => e.preventDefault());
  wrap.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
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
    const up = () => {
      dragMoved = moved;
      wrap.classList.remove("dragging");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
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
  if (prevInf) {
    let delay = 0;
    for (const [cid, now] of Object.entries(inf)) {
      const was = prevInf[cid] || { US: 0, USSR: 0 };
      for (const side of ["US", "USSR"]) {
        const n = (now[side] || 0) - (was[side] || 0);
        for (let i = 0; i < n; i++) {
          const t = delay;
          setTimeout(() => flyPip(cid, side), t);
          delay += 120;
        }
      }
    }
  }
  prevInf = {};
  for (const [cid, v] of Object.entries(inf))
    prevInf[cid] = { US: v.US, USSR: v.USSR };
}

function flyPip(cid, side) {
  placeSound();
  const p = POS[cid];
  if (!p) return;
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
let seenHistory = -1;
let diceChain = Promise.resolve();

function drainRolls() {
  const hist = state.history || [];
  if (seenHistory < 0) { seenHistory = hist.length; return; }
  const fresh = hist.slice(seenHistory);
  seenHistory = hist.length;
  const items = [];
  for (let i = 0; i < fresh.length; i++) {
    const e = fresh[i];
    if (!ROLL_KIND[e.kind]) continue;
    if (e.kind === "realignment_actor_roll" && fresh[i + 1] && fresh[i + 1].kind === "realignment_opponent_roll") {
      items.push({ kind: "realignment", actor: e, opp: fresh[i + 1], prev: hist[seenHistory - fresh.length + i - 1] });
      i++;
    } else {
      items.push({ kind: e.kind, e, prev: hist[seenHistory - fresh.length + i - 1] });
    }
  }
  for (const item of items) diceChain = diceChain.then(() => showDice(item));
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
    box.querySelector(".dtitle").textContent = rollTitle(item);
    box.querySelector(".doutcome").textContent = "";
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
  if (view !== "World") return;  // region views are close enough
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
  flyDiff();
  renderBoard();
  renderPanel();
  renderHand();
  renderDecision();
  renderWinner();
  drainRolls();
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
  renderTracks(host);
}

function renderTracks(host) {
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n | 0));
  const tok = (name, xy, off) => {
    if (!xy) return;
    const el = document.createElement("img");
    el.className = "tracktok";
    el.src = `/assets/markers/${name}.svg`;
    el.style.left = ((xy[0] + (off || 0)) / BOARD_W * 100) + "%";
    el.style.top = ((xy[1] + (off || 0)) / BOARD_H * 100) + "%";
    host.append(el);
  };
  tok("defcon", DEFCON_AT[clamp(state.defcon, 1, 5)]);
  tok("vp", VP_AT[clamp(state.vp, -20, 20) + 20]);
  tok("turn", TURN_AT[clamp(state.turn, 1, 10)]);
  const mil = state.military_ops || {};
  tok("milops_us", MILOPS_AT[clamp(mil.US, 0, 5)], 10);
  tok("milops_ussr", MILOPS_AT[clamp(mil.USSR, 0, 5)], -10);
  const sp = state.space_race || {};
  tok("space_us", SPACE_AT[clamp(sp.US, 0, 8)], 10);
  tok("space_ussr", SPACE_AT[clamp(sp.USSR, 0, 8)], -10);
  const hl = state.phase === "headline" || state.phase === "setup" || state.phase === "predeal";
  if (hl) tok("ar_headline", ROUND_AT[0]);
  else tok((state.phasing || state.human_side) === "US" ? "ar_us" : "ar_ussr",
           ROUND_AT[clamp(state.action_round, 1, 8)]);
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
  if (busy) {
    const wait = document.createElement("div");
    wait.className = "feedrow wait";
    wait.textContent = "Calculating…";
    feed.append(wait);
  }
  for (const e of state.history.slice().reverse().slice(0, 25)) {
    const row = document.createElement("div");
    row.className = "feedrow";
    const what = e.payload.card ? cardName(e.payload.card)
      : e.country ? pretty(e.country)
      : pretty(Object.values(e.payload)[0] ?? e.kind);
    const side = e.actor === "USSR" ? "ussr" : "us";
    row.innerHTML = `<span>${pretty(e.kind)} · ${what} · T${e.turn} R${e.action_round}</span>`
      + `<b class="${side}">${e.actor}</b>`;
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

let recordedEnd = false;

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
    `<p>Play as</p>` +
    `<label><input type="radio" name="set-side" value="US"${play !== "USSR" ? " checked" : ""}> US</label>` +
    `<label><input type="radio" name="set-side" value="USSR"${play === "USSR" ? " checked" : ""}> USSR</label>` +
    `<label>US extra setup +<b id="set-extra-n">${extra}</b>` +
    `<input type="range" id="set-extra" min="0" max="6" value="${extra}"></label>` +
    `<div>` +
    (state && !state.watch ? `<button type="button" id="set-forfeit">Forfeit</button>` : "") +
    `<button type="button" id="set-new">New game</button></div>`;
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
}

async function postGame(path) {
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
    seenHistory = -1;
    prevInf = null;
    recordedEnd = false;
    state = data;
    $("#settings").hidden = true;
    while (state && !state.is_terminal && !state.decision) {
      render();
      await refresh();
    }
  } finally {
    busy = false;
    render();
  }
}

function newGame() { return postGame("/new"); }
function forfeitGame() { return postGame("/forfeit"); }

function renderWinner() {
  const overlay = $("#winner");
  if (!state.is_terminal) {
    recordedEnd = false;
    overlay.hidden = true;
    return;
  }
  if (!recordedEnd && state.winner && !state.watch) {
    bumpRecord(state.human_side, state.winner === state.human_side);
    recordedEnd = true;
  }
  overlay.hidden = false;
  const name = state.winner === "US" ? "USA" : state.winner === "USSR" ? "CCCP" : "Nobody";
  overlay.innerHTML = `<div class="cardbig">${name} wins<br><small>${state.game_over_reason || ""}
    <br><a href="#" id="again">new game</a></small></div>`;
  $("#again").addEventListener("click", (e) => { e.preventDefault(); newGame(); });
}

function toggleSettings() {
  const box = $("#settings");
  if (!box) return;
  box.hidden = !box.hidden;
  if (!box.hidden) fillSettings();
}

boot();
