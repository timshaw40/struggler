"use strict";

/* Struggler web UI. All game state comes from /state (the human seat's
 * observation); every action is one of the pending decision's options,
 * posted by index — the server never invents one and neither do we. */

const META = {};    // card id -> {number, name, ops, side, scoring, event_summary, ...}
const IMAGES = {};  // card id -> card-face filename under /assets/cards/ (optional)
const POS = {};     // country id -> {x, y} box top-left fractions, w/h px, s stability
/* Country geography from /countryfacts: region, Battleground flag, and the
 * DEFCON floor below which no Coup/Realignment may be attempted there. Empty
 * if that endpoint is unavailable, in which case the hover tip falls back to
 * influence and stability alone. */
const FACTS = {};

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
/* The board's scale is stated one way everywhere: 1 means the whole board is
 * fitted to the map area's width, whatever view is showing. Region fits are
 * therefore about 3.07 (every region rect is roughly a third of the board
 * across), and the slider carries that same number — jumping to Europe moves
 * the slider to ~307 instead of leaving it at 100 while the map is visibly
 * three times bigger. */
const fitScale = (name) => BOARD_W / (REGIONS[name][2] - REGIONS[name][0]);
// `(name) =>` rather than a bare `fitScale`: map() would pass the index as the
// second argument, which fitScale would treat as a region name.
const MAX_SCALE = 1.8 * Math.max(...Object.keys(REGIONS).map((n) => fitScale(n)));
let scale = 1;

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
let actionBarLastAction = "";   // the resolved-action caption to fall back on
let playing = true;   // watch mode playback
let winnerFocused = false;
let winnerDismissed = false;   // "review the board" hides the summary, not the game
/* The start screen: which side you play and how much extra setup influence the
 * USA gets, chosen per game instead of buried in Settings under "(next game)". */
let startConfirmed = false;    // the player has been through it this page load
let startPending = false;      // a new game was asked for and is waiting on it
let startFocused = false;
let inFlight = false; // one poll chain at a time
let catchUpGen = 0;   // bumped whenever the game is replaced under catchUp
let lastRenderSig = null;          // skip redundant full re-renders
const expandedRows = new Set();    // history rows the user opened, by absolute index
let feedFilter = localStorage.getItem("struggler.histfilter") || "all";
const seenRows = new Set();        // rows already drawn once, for the entrance tint
let firstFeedBuild = true;
/* Where the action box lives: floating in the middle of the map, or pinned to
 * the bottom of the right column (where it has always been). One element
 * moves between the two hosts, so a decision renders identically either way,
 * and the settings toggle is the rollback. */
let decisionPlace = localStorage.getItem("struggler.decisionPlace") || "center";
let decisionDragged = false;   // once moved by hand, stop auto-positioning it
/* Map-only mode: the panel (log, piles, status) folds away and the map takes
 * the whole width. Remembered, because a player who wants the map big wants
 * it big every time. */
let panelHidden = localStorage.getItem("struggler.mapfocus") === "1";
/* Width of the log/status column, in px, when the player has dragged the
 * divider. Null means "use whatever the stylesheet says", which is what the
 * narrow-window media queries provide. */
let sidebarWidth = (() => {
  const stored = parseInt(localStorage.getItem("struggler.sidebarW") || "", 10);
  return Number.isFinite(stored) ? stored : null;
})();

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

/* Option buttons get the short form: "Operations", not "for operations". The
 * sentence form above still reads right in the log and the map banner ("US
 * plays De-Stalinization for operations"). */
const MODE_BUTTONS = {
  ops: "Operations",
  event: "Event",
  space_race: "Space race",
  un_intervention: "UN Intervention",
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
    // Undo is the one chord worth claiming: it is the shortcut players reach
    // for first, and nothing else in the page uses it.
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "z") {
      if (state && state.can_undo && !busy && !state.is_terminal) {
        e.preventDefault();
        goBack();
      }
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target && e.target.tagName) || "";
    // Let a focused button/control handle its own Enter/Space/arrows.
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") return;

    if (e.key === "Escape") {
      const st = $("#start");
      if (st && !st.hidden) { dismissStart(); return; }
      const h = $("#help");
      if (h && !h.hidden) { h.hidden = true; return; }
      const s = $("#settings");
      if (s && !s.hidden) { s.hidden = true; return; }
      // A finished game's summary is dismissible like any other overlay: the
      // log behind it is the point.
      if (state && state.is_terminal && !winnerDismissed) {
        winnerDismissed = true;
        renderWinner();
        return;
      }
      if (countryTip) countryTip.hidden = true;
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
    // Views and zoom: the map is the board, and these are the two things a
    // player does to it constantly.
    const views = Object.keys(REGIONS);
    if (e.key === "v") {
      setView(views[(views.indexOf(view) + 1) % views.length]);
      return;
    }
    // Zoom in steps proportional to the current scale, so a keypress feels the
    // same at a region fit (~3x) as it does on the whole board.
    if (e.key === "+" || e.key === "=") { setScale(scale * 1.2); return; }
    if (e.key === "-" || e.key === "_") { setScale(scale / 1.2); return; }
    if (e.key === "0") { setScale(fitScale(view)); return; }   // this view's fit
    if (e.key === "?" || e.key === "/") { toggleHelp(); return; }
    if (e.key === "m") { toggleMapFocus(); return; }
    if (e.key === "u" && state && state.can_undo && !busy && !state.is_terminal) {
      goBack();
      return;
    }
    if (busy) return;
    const btns = [...document.querySelectorAll("#decision .dcol-main > button:not(.backbtn)")];
    if (!btns.length) {
      // Placement has no box: its finish button lives in the status bar, so
      // Enter still means "I'm done" there — but only when the engine offers
      // a stop, never as a silent click on some country.
      const done = document.querySelector("#actionbar .donebtn:not(:disabled)");
      if (done && e.key === "Enter") { e.preventDefault(); done.click(); }
      return;
    }
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

/* The board is a 13 MB PNG, so on a cold load the placeholder is up for a
 * while and has to say something. Prefer a streamed fetch, which can count
 * bytes; if that is unavailable or fails, the plain <img> loads the file
 * exactly as before and the placeholder stays a plain "loading". */
async function loadBoard() {
  const img = $("#board");
  if (!img) return;
  const url = "/assets/board.png";
  const text = $("#loadtext");
  const bar = document.querySelector("#boardload .loadbar");
  const mb = (bytes) => (bytes / 1048576).toFixed(1);
  if (!window.fetch || !window.ReadableStream) {
    img.src = url;
    return;
  }
  try {
    const res = await fetch(url);
    if (!res.ok || !res.body) {
      img.src = url;
      return;
    }
    const total = Number(res.headers.get("Content-Length") || 0);
    const reader = res.body.getReader();
    const chunks = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      got += value.length;
      if (text) {
        text.textContent = total
          ? `Loading the board… ${mb(got)} of ${mb(total)} MB`
          : `Loading the board… ${mb(got)} MB`;
      }
      if (bar && total) bar.style.setProperty("--p", `${Math.round((got / total) * 100)}%`);
    }
    const blob = new Blob(chunks, { type: res.headers.get("Content-Type") || "image/png" });
    const objUrl = URL.createObjectURL(blob);
    img.addEventListener("load", () => URL.revokeObjectURL(objUrl), { once: true });
    img.src = objUrl;
    if (text) text.textContent = "Decoding the board…";
    if (bar) bar.style.setProperty("--p", "100%");
  } catch (err) {
    // Any failure is survivable: the img loads the URL directly instead.
    console.warn("board progress unavailable:", err);
    img.src = url;
  }
}

async function bootInner() {
  // The board is ~13 MB: keep a placeholder up until it has decoded.
  const boardImg = $("#board");
  const hideBoardLoad = () => { const l = $("#boardload"); if (l) l.hidden = true; };
  if (boardImg) {
    // Listeners first: loadBoard() only assigns `src` afterwards, so a cached
    // board can never fire `load` before anything is watching for it.
    boardImg.addEventListener("load", hideBoardLoad);
    boardImg.addEventListener("error", hideBoardLoad);  // don't cover the fallback
    loadBoard();
  }
  const toast = $("#toast");
  if (toast) toast.addEventListener("click", () => { toast.hidden = true; });
  // Bring the game-over summary back after "review the board" dismissed it.
  const chip = $("#wchip");
  if (chip) chip.addEventListener("click", () => {
    winnerDismissed = false;
    winnerFocused = false;
    renderWinner();
  });
  const helpBtn = $("#helpbtn");
  if (helpBtn) helpBtn.addEventListener("click", toggleHelp);
  const focusBtn = $("#focusbtn");
  if (focusBtn) focusBtn.addEventListener("click", toggleMapFocus);
  const startGo = $("#startgo");
  if (startGo) startGo.addEventListener("click", startGame);
  const startRange = $("#start-extra");
  if (startRange) startRange.addEventListener("input", () => {
    $("#start-extra-n").textContent = `+${startRange.value}`;
  });
  const startSides = $("#start");
  if (startSides) startSides.addEventListener("change", (e) => {
    if (e.target && e.target.name === "start-side") syncSideCards();
  });
  installKeyboard();

  const [cards, manifest, countries, facts] = await Promise.all([
    fetchJson("/cards"),
    fetchJson("/assets/cards.json").catch(() => ({})),
    // VASSAL install ships box centers measured off the board; fall back
    // to the schematic calibration for non-VASSAL art.
    fetchJson("/assets/countries.json").catch(() => fetchJson("/countries.json")),
    // Region/Battleground/DEFCON geography for the hover tip. Not fatal if it
    // is missing: the tip still reads out influence and stability.
    fetchJson("/countryfacts").catch(() => ({})),
  ]);
  Object.assign(META, cards);
  Object.assign(IMAGES, manifest);
  Object.assign(POS, countries);
  Object.assign(FACTS, facts);
  if (!Object.keys(IMAGES).length) $("#boardwrap").classList.add("noboard");
  const preview = document.createElement("div");
  preview.id = "cardpreview";
  preview.hidden = true;
  document.body.append(preview);
  previewEl = preview;
  actionBar = $("#actionbar");
  enableDragPan();
  enableDecisionDrag();
  enableColumnResize();
  // A remembered width has to be re-clamped for this window: it may have been
  // dragged on a wider screen.
  if (sidebarWidth !== null) setSidebarWidth(sidebarWidth, { save: false });
  else applySidebarWidth();
  // Restore a remembered map-only session before anything measures the map.
  document.body.classList.toggle("mapfocus", panelHidden);
  const focusBack = document.createElement("button");
  focusBack.type = "button";
  focusBack.id = "focusback";
  focusBack.textContent = "⛶ Show panel";
  focusBack.title = "Show the panel again (M)";
  focusBack.hidden = !panelHidden;
  focusBack.addEventListener("click", toggleMapFocus);
  $("#boardarea").append(focusBack);
  applyDecisionPlacement();
  buildViewBar();
  window.addEventListener("resize", () => {
    // A window that shrank may now be too narrow for the dragged width, so
    // re-clamp it against the new limit before measuring the map.
    if (sidebarWidth !== null) setSidebarWidth(sidebarWidth, { save: false });
    layoutBoard();
  });
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
  // The scale is the single source of truth: the rendered width follows from
  // it, and `--boardw` is written from that width so the marker chips (whose
  // size is a calc over `--boardw`) can never disagree with the map.
  const w = Math.round($("#boardarea").clientWidth * scale);
  wrap.style.setProperty("--boardw", w + "px");
}

/* Point the slider at the live scale. Shared by every path that moves the
 * zoom, so the control always reads the truth rather than only the paths that
 * remembered to update it. */
function syncZoomSlider() {
  const slider = $("#zoom");
  if (slider) slider.value = String(Math.round(scale * 100));
}

/* -- the log column's width ------------------------------------------------
 *
 * The log, the status rows and the decision box all live in the right column,
 * so its width is "how much room the map gets". Dragged by #colsplit, also
 * reachable from the keyboard (arrow keys on the divider), remembered per
 * browser, and clamped to something the map and the log can both live with.
 */
const SIDEBAR_MIN = 220;
const SIDEBAR_MAX = 760;
const SIDEBAR_LEAVE = 200;   // room the map must keep, whatever the window is

/* The limit for this window: never so wide that the map is squeezed out, and
 * never wider than the window can hold. */
function sidebarLimit() {
  const main = document.querySelector("main");
  const total = (main && main.clientWidth) || window.innerWidth || 1200;
  return Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, total - SIDEBAR_LEAVE));
}

function applySidebarWidth() {
  const root = document.documentElement;
  if (sidebarWidth === null) root.style.removeProperty("--sidebar-w");
  else root.style.setProperty("--sidebar-w", `${Math.round(sidebarWidth)}px`);
  const split = $("#colsplit");
  if (split) {
    split.setAttribute("aria-valuenow", String(Math.round(sidebarWidth ?? 0)));
    split.setAttribute("aria-valuemin", String(SIDEBAR_MIN));
    split.setAttribute("aria-valuemax", String(Math.round(sidebarLimit())));
  }
}

function setSidebarWidth(px, { save = true } = {}) {
  // Clamped to this window's limit, so a width dragged on a wide screen
  // cannot squeeze the map out on a narrow one.
  sidebarWidth = Math.max(SIDEBAR_MIN, Math.min(sidebarLimit(), Math.round(px)));
  if (save) localStorage.setItem("struggler.sidebarW", String(sidebarWidth));
  applySidebarWidth();
  // The map is measured against the map area's width, so it has to be told
  // that the area just changed size.
  layoutBoard();
}

function resetSidebarWidth() {
  sidebarWidth = null;
  localStorage.removeItem("struggler.sidebarW");
  applySidebarWidth();
  layoutBoard();
}

/* Drag the divider. The pointer is captured so the drag survives leaving the
 * window, and the map keeps the point under the cursor if the layout allows. */
function enableColumnResize() {
  const split = $("#colsplit");
  const panel = $("#panel");
  if (!split || !panel) return;
  const drag = (e) => {
    e.preventDefault();
    split.classList.add("dragging");
    document.body.classList.add("colresize");
    const move = (ev) => setSidebarWidth(window.innerWidth - ev.clientX, { save: false });
    const done = () => {
      split.classList.remove("dragging");
      document.body.classList.remove("colresize");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", done);
      window.removeEventListener("blur", done);
      if (sidebarWidth !== null) {
        localStorage.setItem("struggler.sidebarW", String(sidebarWidth));
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", done);
    window.addEventListener("blur", done);
  };
  split.addEventListener("pointerdown", drag);
  split.addEventListener("dblclick", resetSidebarWidth);
  split.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 48 : 16;
    const here = sidebarWidth ?? panel.offsetWidth;
    if (e.key === "ArrowLeft") setSidebarWidth(here + step);        // wider log
    else if (e.key === "ArrowRight") setSidebarWidth(here - step);  // wider map
    else if (e.key === "Home") setSidebarWidth(SIDEBAR_MIN);
    else if (e.key === "End") setSidebarWidth(sidebarLimit());
    else return;
    e.preventDefault();
  });
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
  slider.min = "100";                       // whole board fitted to the width
  slider.max = String(Math.round(MAX_SCALE * 100));
  slider.value = String(Math.round(scale * 100));
  slider.title = "Zoom (100 = the whole board fits the width)";
  slider.setAttribute("aria-label", "Zoom");
  slider.addEventListener("input", () => setScale(+slider.value / 100));
  $("#boardarea").append(slider);
}

/* Keep the point at the middle of the viewport in the middle while the scale
 * changes: without this, zooming drags the map out from under the cursor. */
function setScale(next) {
  const z = Math.max(1, Math.min(MAX_SCALE, next));
  const wrap = $("#boardwrap");
  const old = $("#boardbox").offsetWidth || 1;
  const cx = wrap.scrollLeft + wrap.clientWidth / 2;
  const cy = wrap.scrollTop + wrap.clientHeight / 2;
  scale = z;
  syncZoomSlider();
  layoutBoard();
  const k = $("#boardbox").offsetWidth / old;
  wrap.scrollLeft = Math.max(0, cx * k - wrap.clientWidth / 2);
  wrap.scrollTop = Math.max(0, cy * k - wrap.clientHeight / 2);
}

function setView(name) {
  view = name;
  // Every view lands at its own fit — World is the whole board, Europe is
  // Europe — and the bar reads that same number, because there is only one
  // scale in the client now.
  scale = fitScale(name);
  syncZoomSlider();
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
      // game_id + decision_id let the server reject a stale/duplicate click
      // instead of advancing whatever decision happens to be pending.
      body: JSON.stringify({
        index,
        game_id: state.game_id,
        decision_id: state.decision ? state.decision.id : null,
      }),
    }).then((r) => r.json());
    if (data.error && data.state) {
      console.warn("/action rejected:", data.error);
      state = data.state;  // resync to the server's real position
      return;
    }
    state = data.state || data;  // error replies carry the current state
  } catch (err) {
    console.error(err);
    showError("That move could not be sent: " + err.message);
  } finally {
    busy = false;
    render();
    // The board has the new counters already, but the player's chit is still
    // flying in. Re-render the decision box once it lands, so "the opponent is
    // thinking" is announced after their influence arrives rather than on top
    // of it. (The FX chain also re-renders on drain; this is the case where the
    // placement is still in the air at that point.)
    waitForSettled(() => { if (state) renderDecision(); });
  }
}

/* How long a chit takes to fly to the board. Must match .flypip's transform
 * transition in style.css: the clack is scheduled for the moment of impact,
 * not the moment the chit leaves the edge of the screen. */
const FLY_MS = 400;

/* One shared Web Audio context for every cue on the page: browsers cap how
 * many a document may open, and the placement clack and the "your move" chime
 * have no reason to own one each. Null where Web Audio is unavailable. */
function audioCtx() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  const ctx = audioCtx.ctx || (audioCtx.ctx = new AC());
  if (ctx.state === "suspended") ctx.resume();  // first click may not have unlocked it yet
  return ctx;
}

/* "The game is waiting on you": two soft rising notes. Deliberately shorter
 * and quieter than the placement clack — it has to carry across a room to a
 * player who has looked away, without sounding like an alarm. */
function cueSound() {
  if (localStorage.getItem("struggler.sound") === "0") return;
  const ctx = audioCtx();
  if (!ctx) return;
  const t0 = ctx.currentTime + 0.02;
  for (const [freq, at, level] of [[784, 0, 0.05], [1046.5, 0.12, 0.04]]) {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(freq, t0 + at);
    g.gain.setValueAtTime(0.0001, t0 + at);
    g.gain.exponentialRampToValueAtTime(level, t0 + at + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.3);
    o.connect(g).connect(ctx.destination);
    o.start(t0 + at);
    o.stop(t0 + at + 0.34);
  }
}

/* Short white noise, made once and reused: the contact tick of a cardboard
 * counter meeting a paper map. */
function noiseBuffer(ctx) {
  const frames = Math.max(1, Math.floor(ctx.sampleRate * 0.05));
  const buf = ctx.createBuffer(1, frames, ctx.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < frames; i++) data[i] = Math.random() * 2 - 1;
  return buf;
}

/* One influence chit landing: a filtered noise tick for the contact, over a
 * pair of damped low partials that give it a body, with a small downward
 * bend so it reads as something with weight rather than a beep. Each hit is
 * detuned a little, and placements landing inside a batch step up so a drop
 * of three influence sounds like tok, tok, tok instead of a machine. */
function placeSound() {
  if (localStorage.getItem("struggler.sound") === "0") return;
  const ctx = audioCtx();
  if (!ctx) return;

  const now = ctx.currentTime;
  const since = placeSound.last === undefined ? Infinity : now - placeSound.last;
  const run = since < 0.3 ? Math.min((placeSound.run ?? 0) + 1, 4) : 0;
  placeSound.last = now;
  placeSound.run = run;
  const pitch = (1 + run * 0.07) * (0.97 + Math.random() * 0.06);

  const noise = ctx.createBufferSource();
  noise.buffer = placeSound.noise || (placeSound.noise = noiseBuffer(ctx));
  const band = ctx.createBiquadFilter();
  band.type = "bandpass";
  band.frequency.value = 2300 * pitch;
  band.Q.value = 0.8;
  const tick = ctx.createGain();
  tick.gain.setValueAtTime(0.08, now);
  tick.gain.exponentialRampToValueAtTime(0.0006, now + 0.03);
  noise.connect(band).connect(tick).connect(ctx.destination);
  noise.start(now);
  noise.stop(now + 0.05);

  for (const [freq, level, decay] of [[214, 0.085, 0.15], [321, 0.04, 0.1]]) {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = "triangle";
    const f = freq * pitch;
    o.frequency.setValueAtTime(f * 1.11, now);
    o.frequency.exponentialRampToValueAtTime(f, now + 0.05);
    g.gain.setValueAtTime(level, now);
    g.gain.exponentialRampToValueAtTime(0.0006, now + decay);
    o.connect(g).connect(ctx.destination);
    o.start(now);
    o.stop(now + decay + 0.02);
  }
}

/* US chits fly in from the left edge, USSR from the right, then a short
 * click. Overlay only — the board already shows the new count. */
let prevInf = null;
/* Any influence increase (human or bot) flies a chit. First snapshot is
 * silent so the opening board doesn't rain counters. Stagger a batch so
 * a bot dump of 6 doesn't stack on one frame. */
/* Board-write diff since the last render, split by side so the player's own
 * placements can animate before the opponent's turn. */
function flyDiff() {
  const inf = state.influence || {};
  const human = [], opp = [];
  if (prevInf) {
    for (const [cid, now] of Object.entries(inf)) {
      const was = prevInf[cid] || { US: 0, USSR: 0 };
      for (const side of ["US", "USSR"]) {
        const n = (now[side] || 0) - (was[side] || 0);
        for (let i = 0; i < n; i++) (side === state.human_side ? human : opp).push([cid, side]);
      }
    }
  }
  prevInf = {};
  for (const [cid, v] of Object.entries(inf))
    prevInf[cid] = { US: v.US, USSR: v.USSR };
  return { human, opp };
}

/* Queue a set of chip fly-ins and WAIT for them to land, so the next FX (the
 * opponent's move) doesn't start on top of them. */
function placementStagger(n) {
  if (n < 2) return 0;
  // A fixed 120 ms stagger makes a seven-influence setup dump run for 840 ms
  // while the player is already clicking the next country. Shrink it so the
  // whole batch still reads as a sequence and still lands inside its budget.
  return Math.min(PLACEMENT_STAGGER_MS, Math.floor(PLACEMENT_STAGGER_BUDGET_MS / (n - 1)));
}

function flyBatch(jobs, stagger) {
  return new Promise((resolve) => {
    jobs.forEach(([cid, side], i) => setTimeout(() => flyPip(cid, side), i * stagger));
    // Resolve when the last chit lands (plus a hair), not on a fixed guess.
    setTimeout(resolve, (jobs.length - 1) * stagger + FLY_MS + 60);
  });
}

function enqueuePlacements(jobs, { priority = false } = {}) {
  if (!jobs.length) return;
  const stagger = placementStagger(jobs.length);
  // Past this depth the queue is animating a board the player has already left
  // behind. Their own chits fly at once so the click still answers; the
  // opponent's are dropped outright, since the markers are already correct.
  if (fxBacklog >= FX_BACKLOG_LIMIT) {
    if (priority) {
      // Still track the flight: a deep backlog must not hide the note behind
      // the player's own chit, which is the whole point of this path.
      fxLanded = flyBatch(jobs, stagger);
      trackLanded(fxLanded);
    }
    return;
  }
  const batch = enqueueFx(() => flyBatch(jobs, stagger));
  if (batch) fxLanded = batch;
}

/* Keep the "chits are still landing" flag honest for a batch that is not on the
 * FX chain (see the backlog branch above). Counted, not a boolean: a second
 * batch can start while the first is still in the air. */
let chitsInFlight = 0;
/* Resolvers waiting for the board to settle (see waitForSettled). */
const settleWaiters = new Set();

function trackLanded(promise) {
  chitsInFlight += 1;
  promise.finally(() => {
    chitsInFlight -= 1;
    flushSettleWaiters();
  });
}

/* Is the board still settling — an FX queued, or a chit in the air? */
function settling() {
  return busy || fxBacklog > 0 || chitsInFlight > 0;
}

/* Run `fn` once the board has settled, immediately when it already has. This
 * keeps "Opponent thinking…" from appearing while the player's own influence is
 * still flying in — the chits are an overlay, so the counters were already
 * correct when the state arrived. */
function waitForSettled(fn) {
  if (!settling()) { fn(); return; }
  settleWaiters.add(fn);
}

function flushSettleWaiters() {
  if (settling()) return;
  const waiting = [...settleWaiters];
  settleWaiters.clear();
  for (const fn of waiting) fn();
}

/* A short beat between the player's move and the opponent's, so the two turns
 * don't read as one blur. */
function enqueueBeat(ms = 400) {
  enqueueFx(() => new Promise((resolve) => setTimeout(resolve, ms)));
}

function flyPip(cid, side) {
  const p = POS[cid];
  if (!p) return;
  // Only for a placement that actually hits the board, and timed to the
  // landing rather than the launch.
  setTimeout(placeSound, FLY_MS);
  const box = $("#boardbox").getBoundingClientRect();
  // Land on the side's own influence column, not the box centre: the pips sit
  // at 4%..48% (US) and 52%..96% (USSR) of the box width, so their midpoints
  // are 26% and 74%. Landing on the dashed divider made every chit look a
  // half-column off from the counter it was announcing.
  const column = side === "USSR" ? 0.74 : 0.26;
  const x = box.left + (p.x + (p.w || 0) * column / BOARD_W) * box.width;
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
/* Past this many queued FX, an opponent's roll is dropped rather than burying
 * the player's own move behind bot animation (see drainRolls). */
const FX_ROLL_DROP_LIMIT = 5;
let seenTotal = -1;
let pendingRealignActor = null;  // actor roll awaiting its opponent roll across polls
/* One ordered FX queue: card reveals, dice and scoring popups all run in the
 * order their events happened. (Previously reveals and dice were separate
 * chains, and dice waited on *all* reveals — so a card played later could jump
 * ahead of an earlier roll.) `fxBacklog` lets a deep opponent batch drop its
 * own rolls rather than bury the player's. */
let fxChain = Promise.resolve();
let fxBacklog = 0;
/* When the last placement of a spend lands, the opponent's turn begins. The
 * chits are an overlay — the board's counters are already correct — so the
 * "Opponent thinking…" note has to wait for them, or it claims the turn moved
 * on while the player is still watching their own influence arrive. */
let fxLanded = Promise.resolve();

/* Chit fly-in pacing. The stagger shrinks as a batch grows so one placement
 * can't run past the player's next click, and a queue deeper than the limit
 * stops growing: at that point the animation is describing a board the player
 * has already moved on from (see enqueuePlacements). */
const PLACEMENT_STAGGER_MS = 120;
const PLACEMENT_STAGGER_BUDGET_MS = 420;
const FX_BACKLOG_LIMIT = 3;

function enqueueFx(fn) {
  fxBacklog += 1;
  const queued = fxChain
    .then(fn)
    .catch((err) => console.error("FX error", err))  // one bad FX can't wedge the queue
    .finally(() => {
      fxBacklog -= 1;
      flushSettleWaiters();
      // When the queue drains, refresh the decision box (it was showing
      // "Resolving…" and can now show the next decision / "Opponent thinking…").
      if (fxBacklog === 0 && state) queueMicrotask(() => {
        if (!state) return;
        renderDecision();
        // The feed's "Thinking…" row is the other half of the same claim: drop
        // it here too, or it lingers past the last chit landing.
        renderPanel();
      });
    });
  fxChain = queued;
  return queued;
}

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
    // The opponent playing YOUR card for Ops fires YOUR event (rule 5.2); say so.
    const yourEvent = m.side && m.side === state.human_side;
    box.querySelector(".dtitle").textContent =
      `${actor} plays ${m.name || pretty(cid)}` + (yourEvent ? " — your event fires" : "");
    if (IMAGES[cid]) {
      const img = document.createElement("img");
      img.className = "playcard";
      img.src = `/assets/cards/${IMAGES[cid]}`;
      img.alt = m.name || cid;
      box.querySelector(".dice").append(img);
    }
    box.querySelector(".doutcome").textContent = m.event_summary || "";
    // The reveal is where "was that card spent or burned?" is decided, so the
    // printed footer belongs here too.
    if (m.remove_after_event) {
      const gone = document.createElement("div");
      gone.className = "removeplay";
      gone.textContent = "Remove from play if used as an event";
      box.querySelector(".doutcome").append(gone);
    }
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
    if (bySide.US && bySide.USSR) enqueueFx(() => showHeadlines(bySide.US, bySide.USSR));
  }

  // One pass, in event order, queueing each event's FX onto the shared chain.
  const enqueueDiceItem = (item) => {
    // Always show the player's own rolls, and every roll in a watched game --
    // there is no player action to protect there, and dropping both sides'
    // rolls is why watch mode looked like it never rolled.
    const who = item.kind === "score" ? null : actorOf(item.e || item.actor || item.opp);
    const protectingPlayer = who !== null && !state.watch && who !== state.human_side;
    if (protectingPlayer && fxBacklog >= FX_ROLL_DROP_LIMIT) return;
    enqueueFx(() => (item.kind === "score" ? showScore(item.e, item.prev) : showDice(item)));
  };

  for (let i = 0; i < fresh.length; i++) {
    const e = fresh[i];
    const prev = hist[start + i - 1];

    if (e.kind === "action_round_play" && e.payload.card && e.actor !== state.human_side) {
      enqueueFx(() => showCardPlay(e.payload.card, e.actor));
    }

    if (e.kind === "realignment_actor_roll") {
      if (pendingRealignActor) {  // a prior actor roll never got its opponent roll
        enqueueDiceItem({ kind: "realignment_actor_roll", e: pendingRealignActor.actor, prev: pendingRealignActor.prev });
        pendingRealignActor = null;
      }
      if (fresh[i + 1] && fresh[i + 1].kind === "realignment_opponent_roll") {
        enqueueDiceItem({ kind: "realignment", actor: e, opp: fresh[i + 1], prev });
        i++;
      } else {
        // Watch mode resolves one step per poll, so the two rolls arrive in
        // separate batches: hold the actor roll for its opponent roll.
        pendingRealignActor = { kind: "realignment", actor: e, prev };
      }
    } else if (e.kind === "realignment_opponent_roll" && pendingRealignActor) {
      pendingRealignActor.opp = e;
      enqueueDiceItem(pendingRealignActor);
      pendingRealignActor = null;
    } else if (ROLL_KIND[e.kind]) {
      enqueueDiceItem({ kind: e.kind, e, prev });
    }

    if (e.payload.card && /_Scoring$/.test(e.payload.card)) {
      enqueueDiceItem({ kind: "score", e, prev });
    }
  }

  // Center-of-map caption: one update per fresh event, in cursor order.
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

/* Dice are drawn, not typed. The old version spun the font's die glyphs
 * (⚀-⚅) with a setInterval, which looked different on every machine and could
 * not turn; these are 3x3 pip faces on a CSS 3D cube that tumbles and lands
 * showing the number that was actually rolled. */
const PIP_LAYOUT = {
  1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8],
  6: [0, 2, 3, 5, 6, 8],
};
/* Where each value sits on the cube, as the cube rotation that brings that
 * side to the front: [rotateX, rotateY]. Whole extra turns are free, so the
 * tumble adds multiples of 360 to whichever axis the face does not use. */
const FACE_SPIN = {
  1: [0, 0], 2: [0, 180], 3: [0, -90], 4: [0, 90], 5: [-90, 0], 6: [90, 0],
};

function dieFace(value) {
  const face = document.createElement("div");
  face.className = `dieface f${value}`;
  for (let i = 0; i < 9; i++) {
    const cell = document.createElement("span");
    if (PIP_LAYOUT[value].includes(i)) cell.className = "pip";
    face.append(cell);
  }
  return face;
}

function buildDie() {
  const die = document.createElement("div");
  die.className = "die";
  const cube = document.createElement("div");
  cube.className = "cube";
  for (const v of [1, 2, 3, 4, 5, 6]) cube.append(dieFace(v));
  die.append(cube);
  return die;
}

/* Short wooden rattle on the bounce, firmer clack on the landing. Same lazy
 * AudioContext and the same Sound switch as the influence chits. */
function diceNoise(kind) {
  if (localStorage.getItem("struggler.sound") === "0") return;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  const ctx = placeSound.ctx || (placeSound.ctx = new AC());
  if (ctx.state === "suspended") ctx.resume();
  const now = ctx.currentTime;
  const clack = kind === "clack";
  const noise = ctx.createBufferSource();
  noise.buffer = placeSound.noise || (placeSound.noise = noiseBuffer(ctx));
  const band = ctx.createBiquadFilter();
  band.type = "bandpass";
  band.frequency.value = clack ? 1400 : 2900;
  band.Q.value = clack ? 1.1 : 0.7;
  const gain = ctx.createGain();
  const peak = clack ? 0.11 : 0.045;
  const decay = clack ? 0.07 : 0.03;
  gain.gain.setValueAtTime(peak, now);
  gain.gain.exponentialRampToValueAtTime(0.0005, now + decay);
  noise.connect(band).connect(gain).connect(ctx.destination);
  noise.start(now);
  noise.stop(now + decay + 0.02);
}

/* Tumble every die and land it on its value. Returns the time the last one
 * settles, so the caller knows when to show the result line. */
function rollDice(host, values) {
  host.textContent = "";
  const settle = 1050, stagger = 110;
  values.forEach((value, i) => {
    const die = buildDie();
    const cube = die.querySelector(".cube");
    const [faceX, faceY] = FACE_SPIN[value] || [0, 0];
    cube.style.transition = "none";
    cube.style.transform =
      `rotateX(${Math.round(200 + Math.random() * 220)}deg) rotateY(${Math.round(200 + Math.random() * 260)}deg)`;
    host.append(die);
    setTimeout(() => {
      cube.style.transition = `transform ${settle}ms cubic-bezier(.18, 1.12, .3, 1)`;
      cube.style.transform = `rotateX(${720 + faceX}deg) rotateY(${1080 + faceY}deg)`;
      diceNoise("tick");
      setTimeout(() => { die.classList.add("landed"); diceNoise("clack"); }, settle - 60);
    }, 50 + i * stagger);
  });
  return 50 + Math.max(0, values.length - 1) * stagger + settle + 220;
}

function showDice(item) {
  return new Promise((resolve) => {
    const box = $("#dicebox");
    clearDiceBox();
    box.querySelector(".dtitle").textContent = rollTitle(item);
    const values = rollValues(item);
    const settled = rollDice(box.querySelector(".dice"), values);
    box.hidden = false;
    box.classList.add("rolling");
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      box.hidden = true;
      box.classList.remove("rolling");
      box.onclick = null;
      resolve();
    };
    // The result reads once the dice have stopped, then holds long enough to
    // take in; a click moves on immediately.
    setTimeout(() => {
      box.classList.remove("rolling");
      box.querySelector(".doutcome").textContent = rollOutcome(item);
    }, settled);
    box.onclick = finish;
    setTimeout(finish, settled + 1700);
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
  if (p.stop) return "Done";  // end the "up to" Ops spend early (6.1.3 / 6.2.2)
  if (p.country) return pretty(p.country);
  if (p.card) return p.card === "none" ? "Pass" : cardName(p.card);
  if (p.mode) return MODE_BUTTONS[p.mode] || MODE_LABELS[p.mode] || pretty(p.mode);
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

/* "MIDDLE_EAST" -> "Middle East": the region names are engine enum values. */
const regionLabel = (name) => String(name).toLowerCase().split("_")
  .map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

/* What the printed board already says, said again in words: who controls the
 * country, whether it is a Battleground (so Scoring and the Coup DEFCON rule
 * both treat it differently), its stability, and the region it scores in. */
function countryFactsLine(cid, inf) {
  const f = FACTS[cid] || {};
  const bits = [];
  const ctrl = controlOf(cid, inf);
  if (ctrl) bits.push(`<b class="${ctrl === "US" ? "us" : "ussr"}">${ctrl} controls</b>`);
  if (f.battleground) bits.push('<b class="fbg">Battleground</b>');
  const stab = (POS[cid] && POS[cid].s) || f.stability;
  if (stab) bits.push(`Stability ${stab}`);
  if (f.region) bits.push(esc(regionLabel(f.region)));
  return bits.join(" · ");
}

/* The line that answers "may I do that here?": what an Ops placement costs,
 * or — during a Coup/Realignment target pick — why this country is not on the
 * list. The two reasons come from the same rules the engine used to build the
 * options, so the tip can only ever confirm what a click would do. */
function countryChoiceLine(cid) {
  const d = state && state.decision;
  if (!d) return "";
  const legal = (d.options || []).some((o) => o.payload && o.payload.country === cid);
  if (d.kind === "place_influence" && !d.context.setup && legal) {
    const cost = placementCost(cid, state.influence);
    return cost === 2
      ? '<div class="cnote warn">Costs 2 Ops — opponent-controlled</div>'
      : '<div class="cnote">Costs 1 Op</div>';
  }
  if ((d.kind === "coup_target" || d.kind === "realignment_target") && !legal) {
    const f = FACTS[cid] || {};
    const opp = state.human_side === "US" ? "USSR" : "US";
    const row = (state.influence || {})[cid] || {};
    const why = (row[opp] || 0) <= 0
      ? "No enemy influence — nothing to attack"
      : f.min_defcon && state.defcon < f.min_defcon
        ? `DEFCON ${state.defcon} bars ${d.kind === "coup_target" ? "Coups" : "Realignments"} in ${esc(regionLabel(f.region))}`
        : "Not a legal target";
    return `<div class="cnote">${why}</div>`;
  }
  // A war's targets are the countries its card names, so a hover outside that
  // list explains itself instead of showing an empty table.
  if (d.kind === "war_target" && !legal) {
    return '<div class="cnote">Not a country this war can be fought in</div>';
  }
  return "";
}

/* Hover readout for a map marker: an amplification of the country's printed
 * header strip (flag | name | stability badge — red badge = battleground),
 * plus live influence, then the facts the art does not carry. Falls back to
 * plain text when a header asset is missing. Positioned above the marker,
 * viewport-clamped. */
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
    + `<span class="ussr">USSR ${inf.USSR}</span></div>`
    + `<div class="cfacts">${countryFactsLine(cid, inf)}</div>`
    + countryChoiceLine(cid)
    + `<div class="odds"></div>`;
  countryTip.hidden = false;
  tipCid = cid;
  fillOdds(cid);
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
  countryTipPlace = place;   // the odds block lands later and needs re-placing
}

/* ---- hover odds ---------------------------------------------------------
 *
 * Before committing to a Coup or a Realignment the player is guessing at the
 * dice. The engine owns those rules, so the numbers come from it
 * (Engine.coup_forecast / realignment_forecast via GET /odds) rather than
 * being re-derived here, and the tooltip just lays them out: a per-die table
 * for a Coup, the 36-outcome split for a Realignment.
 */
let tipCid = null;
let countryTipPlace = null;
const oddsCache = new Map();   // `${decision id}:${country}` -> payload | Promise

/* Every target pick that ends in a die roll, so the hover table and its
 * prefetch can never be wired up for one and forgotten for the others. */
const TARGET_ROLL_KINDS = new Set(["coup_target", "realignment_target", "war_target"]);

function oddsKey(d, cid) {
  return `${d.id}:${cid}`;
}

function requestOdds(d, cid) {
  const key = oddsKey(d, cid);
  let entry = oddsCache.get(key);
  if (entry !== undefined) return entry;
  entry = fetchJson(`/odds?country=${encodeURIComponent(cid)}`)
    .then((payload) => { oddsCache.set(key, payload); return payload; })
    .catch(() => { oddsCache.delete(key); return null; });
  oddsCache.set(key, entry);
  return entry;
}

/* Warm the cache as soon as a target decision appears: the whole point of the
 * tooltip is that it is there the moment the cursor lands, and a fetch on
 * hover both lags and races the click that follows it. */
function prefetchOdds(d) {
  if (!d || !TARGET_ROLL_KINDS.has(d.kind)) return;
  for (const o of d.options || []) {
    const cid = o.payload && o.payload.country;
    if (cid) requestOdds(d, cid);
  }
  // The cache is keyed by decision, so an old decision's entries are dead
  // weight; keep the map small.
  if (oddsCache.size > 60) {
    for (const key of oddsCache.keys()) {
      if (!key.startsWith(`${d.id}:`)) oddsCache.delete(key);
    }
  }
}

function legalTargetDecision(cid) {
  const d = state && state.decision;
  if (!d || !TARGET_ROLL_KINDS.has(d.kind)) return null;
  const hit = (d.options || []).some((o) => o.payload && o.payload.country === cid);
  return hit ? d : null;
}

function oddsRow(p, r) {
  const bits = [];
  const opp = p.side === "US" ? "USSR" : "US";
  if (r.removed) bits.push(`<span class="ussr">−${r.removed} ${opp}</span>`);
  if (r.added) bits.push(`<span class="us">+${r.added} ${p.side}</span>`);
  if (!bits.length) bits.push(`<span class="odnone">no effect</span>`);
  if (r.defcon) bits.push(`<span class="oddefcon">DEFCON −1</span>`);
  return `<tr><td class="d6">${r.roll}</td><td>${bits.join(" ")}</td></tr>`;
}

function oddsHtml(p) {
  if (!p || p.kind === "none") return "";
  if (p.kind === "coup") {
    const mod = p.modifier ? ` · modifier ${p.modifier > 0 ? "+" : ""}${p.modifier}` : "";
    const head = `Coup odds · ${p.ops} Ops − ${2 * p.stability} stability${mod}`;
    const best = p.rows[p.rows.length - 1];
    const take = p.rows.filter((r) => r.added > 0).length;
    const note = p.loses_game
      ? `<div class="odwarn">Cuban Missile Crisis: this coup loses the game.</div>`
      : p.defcon_drop
        ? `<div class="odwarn">Battleground: DEFCON drops whatever you roll.</div>`
        : "";
    const summary = best.added
      ? `Takes the country on ${take} of 6 rolls.`
      : best.removed
        ? `Removes influence on ${p.rows.filter((r) => r.removed > 0).length} of 6.`
        : `Removes nothing: needs ${Math.max(1, 2 * p.stability - p.ops - p.modifier + 1)}+ on the die.`;
    return `<div class="odhead">${esc(head)}</div>`
      + `<table class="odtable">${p.rows.map((r) => oddsRow(p, r)).join("")}</table>`
      + `<div class="odsum">${esc(summary)}</div>` + note;
  }
  if (p.kind === "realignment") {
    const pct = (n) => Math.round((n / 36) * 100);
    const mod = p.modifier ? ` · Iran-Contra ${p.modifier}` : "";
    const deltas = p.outcomes.map((o) => o.delta);
    const head = `Realignment odds · ${p.side} +${p.own_bonus} vs ${p.side === "US" ? "USSR" : "US"} +${p.opponent_bonus}${mod}`;
    const opp = p.side === "US" ? "USSR" : "US";
    // Count what actually happens to the board, not the raw win/loss split:
    // winning rolls are worthless when the opponent has nothing there to
    // remove, and losing rolls cost nothing when you have nothing to lose.
    const count = (pred) => p.outcomes.filter(pred).reduce((n, o) => n + o.count, 0);
    const removes = count((o) => o.delta > 0);
    const costs = count((o) => o.delta < 0);
    const flat = 36 - removes - costs;
    // The bar matches the counts underneath it, not the raw margin split.
    const bar = `<div class="odbar">`
      + `<span class="w" style="width:${pct(removes)}%"></span>`
      + `<span class="t" style="width:${pct(flat)}%"></span>`
      + `<span class="l" style="width:${pct(costs)}%"></span></div>`;
    const bits = [`<b>${removes}/36</b> remove ${esc(opp)} influence`];
    if (costs) bits.push(`<b>${costs}/36</b> you lose influence`);
    bits.push(`<b>${flat}/36</b> nothing changes`);

    const partsLine = (name, parts) => {
      const items = [];
      if (parts.adjacency) items.push("+1 adjacent");
      if (parts.neighbours) {
        items.push(`+${parts.neighbours} controlled neighbour${parts.neighbours > 1 ? "s" : ""}`);
      }
      if (parts.influence) items.push("+1 more influence there");
      return items.length ? `${name} ${items.join(", ")}` : null;
    };
    // `|| {}`: a payload from an older server simply has no breakdown.
    const detail = [partsLine(p.side, p.own_parts || {}), partsLine(opp, p.opponent_parts || {})]
      .filter(Boolean).join(" · ");
    return `<div class="odhead">${esc(head)}</div>` + bar
      + `<div class="odline">${bits.join(" · ")}</div>`
      + `<div class="odline">Best ${deltas[deltas.length - 1] > 0 ? "+" : ""}${deltas[deltas.length - 1]} · `
      + `worst ${deltas[0]} · average ${p.expected_delta > 0 ? "+" : ""}${p.expected_delta}</div>`
      + (detail ? `<div class="odline odsmall">${esc(detail)}</div>` : "");
  }
  if (p.kind === "war") {
    // A war is one die with a fixed penalty, so the whole table is six lines:
    // what each face would do to this country.
    const opp = p.side === "US" ? "USSR" : "US";
    const head = `War odds · needs ${p.needed}+ on the die`
      + (p.penalty ? ` (−${p.penalty} for ${esc(opp)}-controlled neighbours)` : "");
    const rows = p.rows.map((r) => {
      const win = r.win
        ? `<b>win</b> · +${r.vp} VP`
          + (r.seized ? ` · take ${r.seized} influence` : "")
        : "nothing happens";
      return `<tr><td class="d6">${r.roll}</td><td>${win}</td></tr>`;
    }).join("");
    const summary = p.wins === 6
      ? "Wins on any roll."
      : p.wins === 0
        ? "Cannot win as the board stands."
        : `Wins on ${p.wins} of 6 rolls.`;
    return `<div class="odhead">${esc(head)}</div>`
      + `<table class="odtable">${rows}</table>`
      + `<div class="odsum">${esc(summary)}</div>`;
  }
  return "";
}

async function fillOdds(cid) {
  const d = legalTargetDecision(cid);
  const slot = countryTip && countryTip.querySelector(".odds");
  if (!slot) return;
  if (!d) { slot.innerHTML = ""; return; }
  let payload = oddsCache.get(oddsKey(d, cid));
  if (payload && typeof payload.then === "function") {
    // Still in flight (a hover that beat the prefetch): say so rather than
    // showing nothing.
    slot.innerHTML = `<div class="odhead odwait">Reading the odds…</div>`;
    payload = await payload;
  }
  if (!payload) return;
  // The pointer may have moved on, or the tip been rebuilt, while that was in
  // flight; only fill the slot if it still belongs to this country.
  if (!countryTip || countryTip.hidden || tipCid !== cid || !slot.isConnected) return;
  slot.innerHTML = oddsHtml(payload);
  if (countryTipPlace) countryTipPlace();
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

/* Maps every engine effect key (turn_effects / game_effects) to the card that
 * set it, a compact label, and a one-line effect description. The four
 * space_race_* keys are Space Race box abilities, not cards. `sideValue` means
 * the stored value names the affected side; `valueIsRegion` means it names a
 * region. Keep in sync with events.py / core.py (the HUD warns on any key
 * missing here). */
const EFFECT_META = {
  // per-turn
  containment: { card: "Containment", short: "Containment", scope: "turn", note: "US Operations +1 this turn (max 4)." },
  brezhnev: { card: "Brezhnev_Doctrine", short: "Brezhnev", scope: "turn", note: "USSR Operations +1 this turn (max 4)." },
  red_scare: { card: "Red_Scare_Purge", short: "Red Scare", scope: "turn", sideValue: true, note: "That side's Operations \u22121 this turn." },
  u2_incident: { card: "U2_Incident", short: "U2 Incident", scope: "turn", note: "USSR +1 VP if UN Intervention is played this turn." },
  nuclear_subs: { card: "Nuclear_Subs", short: "Nuclear Subs", scope: "turn", note: "US coups in battlegrounds don't lower DEFCON this turn." },
  vietnam_revolts: { card: "Vietnam_Revolts", short: "Vietnam Revolts", scope: "turn", note: "USSR +1 Op when all Ops go to Southeast Asia this turn." },
  la_death_squads: { card: "Latin_American_Death_Squads", short: "Death Squads", scope: "turn", sideValue: true, note: "That side's coups +1 / opponent's \u22121 in Central & South America this turn." },
  iran_contra: { card: "Iran_Contra_Scandal", short: "Iran\u2013Contra", scope: "turn", note: "US realignment rolls \u22121 this turn." },
  chernobyl: { card: "Chernobyl", short: "Chernobyl", scope: "turn", valueIsRegion: true, note: "USSR can't add Influence by Ops in the named region this turn." },
  yuri_samantha: { card: "Yuri_and_Samantha", short: "Yuri & Samantha", scope: "turn", note: "USSR +1 VP per US coup this turn." },
  salt: { card: "Salt_Negotiations", short: "SALT", scope: "turn", note: "All coup rolls \u22121 this turn." },
  cuban_missile_crisis: { card: "Cuban_Missile_Crisis", short: "Cuban Missile Crisis", scope: "turn", sideValue: true, urgent: true, note: "Any coup by the flagged side loses the game." },
  we_will_bury_you: { card: "We_Will_Bury_You", short: "We Will Bury You", scope: "turn", urgent: true, note: "USSR +3 VP unless the US plays UN Intervention this Action Round." },
  north_sea_oil_extra: { card: "North_Sea_Oil", short: "North Sea Oil (extra AR)", scope: "turn", note: "US gets an extra Action Round this turn." },
  // game-long
  marshall_or_warsaw: { card: null, short: "Marshall Plan / Warsaw Pact", scope: "game", note: "Enables NATO." },
  nato: { card: "NATO", short: "NATO", scope: "game", note: "USSR can't coup or realign US-controlled Europe; Brush War blocked." },
  us_japan_pact: { card: "US_Japan_Mutual_Defense_Pact", short: "US/Japan Pact", scope: "game", note: "USSR can't coup or realign Japan." },
  degaulle_france: { card: "De_Gaulle_Leads_France", short: "De Gaulle", scope: "game", note: "NATO doesn't protect France." },
  willy_brandt: { card: "Willy_Brandt", short: "Willy Brandt", scope: "game", note: "NATO doesn't protect West Germany." },
  john_paul: { card: "John_Paul_II_Elected_Pope", short: "John Paul II", scope: "game", note: "Solidarity may be played." },
  camp_david: { card: "Camp_David_Accords", short: "Camp David", scope: "game", note: "Arab-Israeli War can't be played." },
  iranian_hostage: { card: "Iranian_Hostage_Crisis", short: "Iran Hostage", scope: "game", note: "Terrorism makes the US discard 2." },
  iron_lady: { card: "The_Iron_Lady", short: "Iron Lady", scope: "game", note: "Socialist Governments can't be played." },
  awacs: { card: "AWACS_Sale_to_Saudis", short: "AWACS", scope: "game", note: "Muslim Revolution can't be played." },
  reformer: { card: "The_Reformer", short: "Reformer", scope: "game", note: "USSR can't coup in Europe." },
  flower_power: { card: "Flower_Power", short: "Flower Power", scope: "game", note: "USSR +2 VP per US war card." },
  evil_empire: { card: "An_Evil_Empire", short: "Evil Empire", scope: "game", note: "Flower Power is cancelled." },
  formosan_resolution: { card: "Formosan_Resolution", short: "Formosan", scope: "game", note: "Taiwan counts as a battleground until the US plays the China Card." },
  shuttle_diplomacy: { card: "Shuttle_Diplomacy", short: "Shuttle Diplomacy", scope: "game", note: "\u22121 USSR battleground at the next Middle East/Asia scoring." },
  north_sea_oil: { card: "North_Sea_Oil", short: "North Sea Oil", scope: "game", note: "OPEC can't be played." },
  norad: { card: "NORAD", short: "NORAD", scope: "game", note: "US +1 Influence when DEFCON drops to 2 during an Action Round." },
  bear_trap: { card: "Bear_Trap", short: "Bear Trap", scope: "game", note: "USSR is trapped: discard + roll each Action Round." },
  quagmire: { card: "Quagmire", short: "Quagmire", scope: "game", note: "US is trapped: discard + roll each Action Round." },
  missile_envy_forced: { card: "Missile_Envy", short: "Missile Envy", scope: "game", sideValue: true, note: "That side must spend Missile Envy on Operations next Action Round." },
  // Space Race abilities
  space_race_double_attempt_holder: { card: null, short: "2nd attempt", scope: "space", sideValue: true, note: "A second Space Race attempt each turn." },
  space_race_headline_reveal_holder: { card: null, short: "headline peek", scope: "space", sideValue: true, note: "Picks its Headline second and sees the opponent's." },
  space_race_discard_holder: { card: null, short: "discard Held Card", scope: "space", sideValue: true, note: "May discard the Held Card." },
  space_race_extra_round_holder: { card: null, short: "extra Action Round", scope: "space", sideValue: true, note: "Gets an extra Action Round." },
};
const warnedEffectKeys = new Set();

function effectEntry(key, value, defaultScope) {
  let meta = EFFECT_META[key];
  if (!meta) {
    if (!warnedEffectKeys.has(key)) {
      warnedEffectKeys.add(key);
      console.warn("Unknown effect key — add it to EFFECT_META:", key);
    }
    meta = { card: null, short: pretty(key), scope: defaultScope, note: "" };
  }
  const scope = meta.scope || defaultScope;
  let side = null;
  if (meta.sideValue && (value === "US" || value === "USSR")) side = value;
  if (!side && meta.card && META[meta.card]) {
    const s = META[meta.card].side;
    if (s === "US" || s === "USSR") side = s;
  }
  let note = meta.note || "";
  if (meta.valueIsRegion && value) note += ` (${pretty(value)})`;
  return { short: meta.short, note, side, scope, urgent: !!meta.urgent, card: meta.card };
}

/* Active-effects HUD: only effects currently in force (present in
 * turn_effects / game_effects), grouped by duration. Hover a chip for the
 * effect text and a card-face preview. */
function renderEffectsHud() {
  const host = $("#effectshud");
  if (!host || !state) return;
  host.textContent = "";
  const entries = [];
  for (const [k, v] of Object.entries(state.turn_effects || {})) entries.push(effectEntry(k, v, "turn"));
  for (const [k, v] of Object.entries(state.game_effects || {})) entries.push(effectEntry(k, v, "game"));
  let any = false;
  for (const [scope, label] of [["turn", "This turn"], ["game", "Game"], ["space", "\u{1F680} Space Race"]]) {
    const items = entries.filter((e) => e.scope === scope).sort((a, b) => a.short.localeCompare(b.short));
    if (!items.length) continue;
    any = true;
    const group = document.createElement("div");
    group.className = "effgroup";
    const lab = document.createElement("span");
    lab.className = "efflabel";
    lab.textContent = label;
    group.append(lab);
    for (const e of items) {
      const chip = document.createElement("span");
      const cls = e.side === "US" ? "us" : e.side === "USSR" ? "ussr" : "neutral";
      chip.className = `effchip ${cls}${e.urgent ? " urgent" : ""}`;
      chip.textContent = e.short;
      chip.title = e.note || e.short;
      if (e.card) {
        chip.addEventListener("mouseenter", () => showPreview(e.card, chip));
        chip.addEventListener("mouseleave", () => { if (previewEl) previewEl.hidden = true; });
      }
      group.append(chip);
    }
    host.append(group);
  }
  host.hidden = !any;
}

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
    const bonuses = c.bonus || [];
    if (bonuses.length) line += ` (+${bonuses.length} ${bonuses.map(pretty).join(" + ")})`;
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
    const owner = META[c.card] && META[c.card].side;
    const owner_word = owner === "US" ? "US" : owner === "USSR" ? "USSR" : "the";
    const order = p.order === "event_first"
      ? `${owner_word} event resolves first`
      : "ops resolve first";
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
    line = `${who} adds influence to ${pretty(p.country)}`;
  } else {
    line = feedSummary(e, prev);  // safe fallback for anything unforeseen
  }

  if (prev && e.defcon !== prev.defcon) line += ` · DEFCON ${prev.defcon} → ${e.defcon}`;
  return line;
}

function showAction(e, prev) {
  if (!actionBar) return;
  actionBarLastAction = actionText(e, prev);
  if (!actionBarLastAction) return;  // internal step: leave the caption up
  renderActionBar({ bump: true });
  const live = $("#live");
  if (live) live.textContent = actionBarLastAction;  // announced to screen readers
}

function clearAction() {
  actionBarLastAction = "";
  if (actionBar) {
    actionBar.hidden = true;
    actionBar.textContent = "";
    actionBar.title = "";
    actionBar.classList.remove("hasdone");
  }
  const live = $("#live");
  if (live) live.textContent = "";
}

/* The status bar is the map's own line of text, and placement now depends on
 * it: "who is placing, how much is left, and how to finish" lives here, and
 * the Done button it grows is the only way to end the spend early. Its own
 * element (#actionbar) is separate from the decision box, so the map keeps its
 * full height. */
function renderActionBar({ bump = false } = {}) {
  if (!actionBar) return;
  const d = isMapOnlyPick(state && state.decision) ? state.decision : null;
  const doneOpt = d && d.options.find((o) => o.payload && o.payload.stop);
  // The button belongs to the decision, so it goes before anything can return
  // early — otherwise a Done from the last spend sits live over the next one.
  const stale = actionBar.querySelector("button.donebtn");
  if (stale) stale.remove();
  actionBar.classList.toggle("hasdone", !!doneOpt);
  let text = actionBarLastAction;
  if (d) text = placementDirective(d);
  else if (!text) {
    actionBar.hidden = true;
    return;
  }
  actionBar.textContent = text;
  actionBar.title = text;  // full text on hover (the pill may ellipsize)
  actionBar.hidden = false;
  if (bump) {
    actionBar.classList.remove("bump");
    void actionBar.offsetWidth;  // restart the pop
    actionBar.classList.add("bump");
  }
  // The spend's own "stop" option, as a button in the bar, rebuilt from this
  // decision every time so a stale index cannot be clicked after the decision
  // has moved on.
  if (doneOpt) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "donebtn";
    b.textContent = optionLabel(doneOpt) || "Done";
    b.disabled = busy;
    b.title = "Finish placing (Enter)";
    b.addEventListener("click", () => act(doneOpt.index));
    actionBar.append(b);
  }
}

function render() {
  if (!state) return;
  // Skip redundant full re-renders: a change to any of these implies the DOM
  // needs rebuilding. This is what keeps the 60-row feed, hand, and markers
  // from being torn down every poll (and lets expanded rows survive).
  const d = state.decision;
  const sig = [
    state.seed, state.history_len, state.is_terminal, state.can_undo, busy, playing,
    state.play_restriction || "-",
    d ? `${d.kind}:${d.options.length}` : "-",
    // Placement's directive carries a budget that changes without the option
    // list changing, so the bar must not be skipped by the render dedupe.
    d && (d.context || {}).remaining != null ? `rem:${d.context.remaining}` : "-",
    d ? `left:${placementOpsLeft(d)}` : "-",
    Object.keys(state.turn_effects || {}).length,
    Object.keys(state.game_effects || {}).length,
  ].join("|");
  if (sig === lastRenderSig) return;
  lastRenderSig = sig;

  // Order the FX so one side's turn reads as one beat: the player's placements
  // land first, then a short pause, then the opponent's reveals/rolls/placements.
  prefetchOdds(state.decision);
  const fly = flyDiff();
  // The player's own chits are priority: if their click lands while the queue
  // is still busy, it must not sit behind it.
  enqueuePlacements(fly.human, { priority: true });
  // Only pause between the turns when the opponent actually did something.
  // catchUp() polls the server faster than the FX play, so an unguarded beat
  // charged 400 ms of dead air to every one of the bot's setup placements.
  if (fly.opp.length) enqueueBeat();
  drainRolls();
  enqueuePlacements(fly.opp);
  renderBoard();
  renderPanel();
  renderEffectsHud();
  renderHand();
  renderDecision();
  renderActionBar();
  renderWinner();
  renderStart();
  renderCue();
}

/* The tab is the player's HUD whenever the game is not the front window: a
 * bot can think for a minute at a time, so whose move it is has to be legible
 * from the tab strip alone, without coming back to the table. */
let cueWasMine = null;     // null until the first render, so a reload is quiet
let faviconAlert = null;

function renderCue() {
  if (!state) return;
  const over = !!state.is_terminal;
  // `state.decision` only ever carries the human's own decision (the server
  // resolves bot and CHANCE steps before it answers), so a decision here
  // really does mean "the game is waiting on you".
  const mine = !over && !state.watch && !busy && !!state.decision;
  let title;
  if (over) {
    const name = state.winner === "US" ? "USA" : state.winner === "USSR" ? "CCCP" : "Nobody";
    title = `Struggler — ${name} wins`;
  } else if (state.watch) {
    title = playing ? "Struggler — bot vs bot" : "Struggler — paused";
  } else if (mine) {
    title = "● Your move — Struggler";
  } else if (settling()) {
    // Same rule as the feed's row: the player's own move is still going down,
    // so the tab says so rather than "the opponent has the floor".
    title = "Struggler — resolving…";
  } else {
    title = `Struggler — Turn ${state.turn}, Round ${state.action_round}`;
  }
  if (document.title !== title) document.title = title;
  setFavicon(mine);
  // One chime, only on the beat where the turn comes back to the player, and
  // only when they are looking at something else. A reload is silent.
  if (mine && cueWasMine === false && document.hidden) cueSound();
  cueWasMine = mine;
}

/* A drawn favicon rather than a file: one less thing to install, and the dot
 * is the only information the tab strip can carry at 16px. */
function setFavicon(alert) {
  if (faviconAlert === alert) return;
  faviconAlert = alert;
  const dot = alert ? "#f4c04f" : "#4a5262";
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    + '<rect width="32" height="32" rx="7" fill="#1b2029"/>'
    + `<circle cx="16" cy="16" r="8.5" fill="${dot}"/></svg>`;
  let link = document.querySelector("link[rel='icon']");
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.append(link);
  }
  link.href = "data:image/svg+xml," + encodeURIComponent(svg);
}

function renderBoard() {
  if (!state) return;
  $("#board").onerror = () => $("#boardwrap").classList.add("noboard");
  // A hovered marker is destroyed on rebuild, so its mouseleave may never fire;
  // hide the tip explicitly or it can linger over the map (and the dice popup).
  if (countryTip) countryTip.hidden = true;
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
    if (target !== null && d && d.kind === "place_influence" && !d.context.setup
        && placementCost(cid, state.influence) === 2) {
      const badge = document.createElement("span");
      badge.className = "cost2";
      badge.textContent = "2";
      badge.title = "Opponent-controlled: placing here costs 2 Ops";
      el.append(badge);
    }
    el.setAttribute("aria-label", `${pretty(cid)} — US ${inf.US} / USSR ${inf.USSR}`);
    el.addEventListener("mouseenter", () => showCountryTip(el, cid, inf));
    el.addEventListener("mouseleave", () => { if (countryTip) countryTip.hidden = true; });
    el.addEventListener("focus", () => showCountryTip(el, cid, inf));  // keyboard users too
    el.addEventListener("blur", () => { if (countryTip) countryTip.hidden = true; });
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
/* Ops cost to place one Influence in `cid` for the human side: 2 when the
 * opponent controls it (the doubling rule), else 1. Setup is flat 1. */
function placementCost(cid, inf) {
  const s = POS[cid] && POS[cid].s;
  const row = inf[cid];
  if (!s || !row) return 1;
  const human = state.human_side, opp = human === "US" ? "USSR" : "US";
  return row[opp] - row[human] >= s ? 2 : 1;
}

/* Ops still available in the current placement spend (normal or region-bonus). */
function placementOpsLeft(d) {
  const c = d.context || {};
  if (c.setup) return null;
  if (c.ops_remaining != null) return c.ops_remaining;
  if (c.base != null && c.spent != null) {
    // Region-bonus spend: each still-alive bonus region contributes +1 to the
    // budget (7.4 aggregates — China Card in Asia + Vietnam Revolts in SE Asia).
    return c.base + (c.bonus || []).length - c.spent;
  }
  return null;
}

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

  const piles = $("#piles");
  piles.textContent = "";
  // A pile is a stack of cards, so it opens as cards: the same art the hand
  // uses, with the same hover preview. The collapsed row keeps the last few
  // names, which is what the player glances at ("is that in the discard
  // yet?"), and the count is the point of the row.
  const pileRow = (label, cards) => {
    if (!cards.length) return;
    const row = document.createElement("div");
    row.className = "kv pile";
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-expanded", "false");
    const names = cards.slice(-4).map(cardName).join(", ");
    row.title = "Show every card in this pile";
    row.innerHTML = `<span>${esc(label)} (${cards.length})</span><b>${esc(names)}</b>`;
    const list = document.createElement("div");
    list.className = "pilecards";
    list.hidden = true;
    // The discard can reach ~110 cards by the late war: draw the first screen
    // worth and let the player ask for the rest, so opening a pile is never a
    // hundred-image stall.
    for (const cid of cards.slice(0, PILE_FACE_CAP)) list.append(pileCard(cid));
    if (cards.length > PILE_FACE_CAP) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "pilemore";
      more.textContent = `…and ${cards.length - PILE_FACE_CAP} more`;
      more.addEventListener("click", (e) => {
        e.stopPropagation();  // clicking the cards must not close the pile
        more.remove();
        for (const cid of cards.slice(PILE_FACE_CAP)) list.append(pileCard(cid));
      });
      list.append(more);
    }
    const toggle = () => {
      list.hidden = !list.hidden;
      row.setAttribute("aria-expanded", String(!list.hidden));
    };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
    piles.append(row, list);
  };
  pileRow("In play", state.in_play_cards || []);
  pileRow("Discard", state.discard_pile);
  pileRow("Removed", state.removed_cards || []);

  const feed = $("#feed");
  feed.textContent = "";
  const title = document.createElement("h2");
  title.textContent = "History";
  feed.append(title);
  // "Thinking…" claims the opponent is considering a position. It is only
  // honest once the player's own move is on the board: while their click is
  // still in flight (`busy`) or their chit is still landing (`settling`), the
  // row would announce the bot over influence the player has not seen arrive.
  const waiting = (state.watch && playing && !state.is_terminal)
    || (!busy && !settling());
  if (waiting) {
    const wait = document.createElement("div");
    wait.className = "feedrow wait";
    wait.textContent = state.watch ? "Playing…" : "Thinking…";
    feed.append(wait);
  }
  const hist = state.history || [];
  // `hist` is a truncated window of the server's history, so use the
  // monotonic `history_len` as the cursor, not hist.length.
  const total = state.history_len ?? hist.length;
  const entries = foldBookkeeping(annotateHistory(hist).map((entry, i) => ({
    ...entry,
    abs: total - hist.length + i,
    parts: rowParts(entry.e, entry.before, entry.older, entry.side),
  })));
  feed.append(vpStrip(hist));
  buildFeedFilters();

  const shown = entries.slice().reverse().filter((entry) => feedMatches(entry, feedFilter));
  let group = null;
  for (const entry of shown) {
    const { e, older, parts } = entry;
    const label = `TURN ${e.turn} · R${e.action_round}`;
    if (label !== group) {
      group = label;
      const head = document.createElement("div");
      head.className = "feedturn";
      head.textContent = label;
      feed.append(head);
    }
    const row = document.createElement("div");
    // The rail carries who acted; the class also drives the entrance tint.
    row.className = `feedrow ${parts.actor === "USSR" ? "ussr" : parts.actor === "US" ? "us" : "sys"}`;
    row.dataset.abs = String(entry.abs);
    row.setAttribute("aria-label", `${parts.actor}: ${parts.sentence}`);
    // Animate only rows that appeared while the page was open: the first
    // build would otherwise ripple the whole window on load.
    if (!firstFeedBuild && !seenRows.has(entry.abs)) row.classList.add("fresh");
    seenRows.add(entry.abs);

    row.append(feedGlyph(parts.glyph));
    const title = document.createElement("span");
    title.className = "ftitle";
    title.textContent = parts.title || parts.sentence;
    title.title = parts.sentence;
    row.append(title);
    if (parts.delta) {
      const delta = document.createElement("b");
      delta.className = `fdelta${parts.deltaSide ? ` ${parts.deltaSide === "USSR" ? "ussr" : "us"}` : ""}`;
      delta.textContent = parts.delta;
      if (parts.deltaTitle) delta.title = parts.deltaTitle;
      row.append(delta);
    }
    if (parts.card && IMAGES[parts.card]) {
      const thumb = document.createElement("img");
      thumb.className = "fcard";
      thumb.src = `/assets/cards/${IMAGES[parts.card]}`;
      thumb.alt = "";
      thumb.loading = "lazy";
      thumb.title = cardName(parts.card);
      thumb.addEventListener("error", () => thumb.remove());
      row.append(thumb);
    }
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "feedtoggle";
    const detail = document.createElement("div");
    detail.className = "feeddetail";
    const open = expandedRows.has(entry.abs);
    detail.hidden = !open;
    toggle.textContent = open ? "−" : "+";
    toggle.setAttribute("aria-label", open ? "Hide detail" : "Show detail");
    for (const line of eventDetail(e, older)) {
      const p = document.createElement("div");
      p.textContent = line;
      detail.append(p);
    }
    toggle.addEventListener("click", () => {
      const nowOpen = detail.hidden;
      detail.hidden = !nowOpen;
      toggle.textContent = nowOpen ? "−" : "+";
      toggle.setAttribute("aria-label", nowOpen ? "Hide detail" : "Show detail");
      if (nowOpen) expandedRows.add(entry.abs); else expandedRows.delete(entry.abs);
    });
    row.append(toggle, detail);
    feed.append(row);
  }
  // Keep the "already seen" set from growing across a long session: anything
  // outside the server's window can never be re-rendered.
  for (const abs of seenRows) if (abs < total - 200) seenRows.delete(abs);
  firstFeedBuild = false;
}

function vpSwing(e, older) {
  if (!older || e.vp === older.vp) return older ? "no swing" : "";
  const d = e.vp - older.vp;
  return d > 0 ? `US +${d}` : `USSR +${-d}`;
}

/* -- history feed ---------------------------------------------------------
 *
 * The feed is a browsing surface, not a transcript: rows lead with the country
 * or card and the number that changed, a left rail carries who acted, and the
 * sentence moves into the expansion. One glyph per kind of event, hand-drawn
 * because the page is dependency-free, so a row can be recognised before it is
 * read. Colour stays reserved for the two sides plus neutral for the system.
 */

const FEED_GLYPHS = {
  influence: "<circle cx='12' cy='12' r='6'/><circle cx='12' cy='12' r='9.4' stroke-dasharray='1.5 3.2'/>",
  coup: "<circle cx='12' cy='12' r='6'/><path d='M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4'/>",
  realign: "<path d='M4 8.5h11l-3-3M20 15.5H9l3 3'/>",
  score: "<path d='M7 21V4'/><path d='M7 5h10.5l-2.4 3.5 2.4 3.5H7'/>",
  headline: "<path d='M12 4l2.3 4.8 5.2.7-3.8 3.5.9 5.1-4.6-2.5-4.6 2.5.9-5.1L4.5 9.5l5.2-.7z'/>",
  card: "<rect x='6' y='4' width='12' height='16' rx='2'/><path d='M9 9h6M9 12.5h6'/>",
  space: "<path d='M12 2.8c2.4 2.2 3.6 5 3.6 7.9v2.6H8.4v-2.6c0-2.9 1.2-5.7 3.6-7.9z'/><path d='M8.4 13.3L6 17h12l-2.4-3.7'/><path d='M10.4 17.8L12 21.4l1.6-3.6'/>",
  war: "<path d='M12 3v6M12 15v6M3 12h6M15 12h6M6.4 6.4l3 3M14.6 14.6l3 3M17.6 6.4l-3 3M9.4 14.6l-3 3'/>",
  // Ops: a counter with a plus, for "spend this card's Operations".
  ops: "<circle cx='12' cy='12' r='8.5'/><path d='M12 8.2v7.6M8.2 12h7.6'/>",
  // UN Intervention: a globe, for the card that cancels an event.
  un: "<circle cx='12' cy='12' r='8.5'/><path d='M3.5 12h17M12 3.5c2.6 2.6 2.6 14.4 0 17M12 3.5c-2.6 2.6-2.6 14.4 0 17'/>",
  done: "<path d='M4.5 12.8l5 4.7 10-11'/>",
  system: "<circle cx='12' cy='12' r='2.6'/>",
};

/* The same glyphs as markup, for the option buttons (built with innerHTML). */
function glyphSvg(kind, cls = "ficon") {
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor"` +
    ` stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
    `${FEED_GLYPHS[kind] || FEED_GLYPHS.system}</svg>`;
}

/* Which glyph an option button leads with. */
function optionGlyph(o) {
  const p = o.payload || {};
  if (p.stop) return "done";
  if (p.mode === "ops") return "ops";
  if (p.mode === "space_race") return "space";
  if (p.mode === "un_intervention") return "un";
  if (p.mode === "event") return "headline";
  if (p.country) return "influence";
  if (p.type === "coup") return "coup";
  if (p.type === "realignment") return "realign";
  if (p.type === "influence") return "influence";
  if ("choice" in p) return "card";
  if (p.card) return "card";
  return "system";
}

const CARD_KINDS = new Set([
  "headline_play", "action_round_play", "event_choice", "event_ops_order",
  "event_resume", "play_mode", "ops_type", "event_influence",
]);
const COUP_KINDS = new Set(["coup_target", "coup_roll"]);
/* Decisions whose answer is a country on the map: the floating action box has
 * to keep out of their way (see renderDecision). */
const COUNTRY_PICK_KINDS = new Set([
  "place_influence", "coup_target", "realignment_target", "war_target",
]);
const REALIGN_KINDS = new Set([
  "realignment_target", "realignment_actor_roll", "realignment_opponent_roll",
]);
const WAR_KINDS = new Set(["war_target", "war_roll", "contest_roll"]);
const SYSTEM_KINDS = new Set([
  "random_discard", "quagmire_discard", "quagmire_roll", "held_card_discard",
  "deal_card",
]);

function feedGlyph(kind) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "ficon");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.6");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.innerHTML = FEED_GLYPHS[kind] || FEED_GLYPHS.system;
  return svg;
}

/* One row's glanceable parts, from the event and the influence the country
 * held before it (null when the window starts mid-game and the "before" is
 * unknown — better a total than a wrong delta). */
function rowParts(e, before, older, side) {
  const actor = side || actorOf(e);
  const p = e.payload || {};
  const card = p.card || null;
  const sentence = actionText(e, older) || pretty(e.kind);
  const scoring = card && /Scoring$/.test(card);
  const base = { actor, card, sentence, delta: "", deltaSide: null, title: "", glyph: "system" };

  if (scoring) {
    return { ...base, glyph: "score", title: cardName(card), delta: vpSwing(e, older) };
  }
  if (e.country && (e.kind === "place_influence" || e.kind === "event_influence")) {
    const now = e.country_influence || {};
    let delta = `${now.US || 0}–${now.USSR || 0}`;
    let deltaSide = null;
    if (before) {
      const du = (now.US || 0) - (before.US || 0);
      const ds = (now.USSR || 0) - (before.USSR || 0);
      if (du > 0) { delta = `+${du} US`; deltaSide = "US"; }
      else if (ds > 0) { delta = `+${ds} USSR`; deltaSide = "USSR"; }
      else if (du < 0) { delta = `${du} US`; deltaSide = "US"; }
      else if (ds < 0) { delta = `${ds} USSR`; deltaSide = "USSR"; }
    }
    return { ...base, glyph: "influence", title: pretty(e.country), delta, deltaSide };
  }
  if (COUP_KINDS.has(e.kind) && e.country) {
    const ops = e.context && e.context.ops !== undefined ? ` · ops ${e.context.ops}` : "";
    return {
      ...base, glyph: "coup", title: pretty(e.country),
      delta: p.value !== undefined ? `rolled ${p.value}${ops}` : `coup${ops}`,
    };
  }
  if (REALIGN_KINDS.has(e.kind) && e.country) {
    const rolls = e.context && e.context.actor_roll !== undefined
      ? `${e.context.actor_roll} v ${p.value}` : "";
    return { ...base, glyph: "realign", title: pretty(e.country), delta: rolls };
  }
  if (e.kind === "space_race_roll") {
    const box = e.space_race && e.space_race[actor] !== undefined ? `box ${e.space_race[actor]}` : "";
    return { ...base, glyph: "space", title: "Space Race", delta: p.value !== undefined ? `rolled ${p.value}` : box };
  }
  if (WAR_KINDS.has(e.kind)) {
    return {
      ...base, glyph: "war", title: e.country ? pretty(e.country) : pretty(e.kind),
      delta: p.value !== undefined ? `rolled ${p.value}` : "",
    };
  }
  if (card) {
    return {
      ...base,
      glyph: e.kind === "headline_play" ? "headline" : "card",
      title: cardName(card),
    };
  }
  if (CARD_STEP_KINDS.has(e.kind)) {
    // Only reachable when a step has no card row to fold into (a window that
    // starts mid-action); name the card so the row still makes sense.
    const ctx = (e.context && e.context.card) || null;
    return {
      ...base, glyph: "card", title: pretty(e.kind),
      delta: ctx ? cardName(ctx) : "",
    };
  }
  if (SYSTEM_KINDS.has(e.kind)) {
    return { ...base, glyph: "system", title: pretty(e.kind) };
  }
  return { ...base, glyph: "card", title: pretty(e.kind) };
}

/* Chronological pass: each event with the influence its country held before
 * it, so a row can show +1 rather than a running total. The server sends only
 * a window, so a country first seen inside it has no "before" — null, and the
 * row falls back to the totals. */
function annotateHistory(hist) {
  const seen = new Map();
  let lastSide = null;
  return hist.map((e, i) => {
    const before = e.country ? seen.get(e.country) || null : null;
    if (e.country && e.country_influence) seen.set(e.country, { ...e.country_influence });
    // Dice rolls are recorded against CHANCE; the row still belongs to whoever
    // caused the roll, so the side carries forward from the last real actor.
    let side = actorOf(e);
    if (side === "CHANCE") side = lastSide;
    else lastSide = side;
    return { e, before, side, older: i > 0 ? hist[i - 1] : null };
  });
}

/* One card play is four decisions — the card, the mode, the Ops type, then the
 * placement — and one coup is a target plus a roll. Rendered raw, that reads as
 * the same card twice and an "ops type" row nobody asked for, so the
 * bookkeeping steps fold into the card row as its "how", and a target that is
 * immediately answered by its own roll is dropped (the roll carries the
 * country anyway). */
const CARD_STEP_KINDS = new Set(["play_mode", "ops_type", "event_ops_order", "event_resume"]);
const TARGET_KINDS = new Set(["coup_target", "realignment_target"]);

function cardStepText(e) {
  const p = e.payload || {};
  if (p.mode) return pretty(p.mode);
  if (p.type) return pretty(p.type).split(" ")[0];   // "influence", "coup", "realignment"
  if (p.order) return pretty(p.order);
  return pretty(e.kind);
}

function foldBookkeeping(entries) {
  const out = [];
  entries.forEach((entry, i) => {
    const { e, parts } = entry;
    const prev = out[out.length - 1];
    if (CARD_STEP_KINDS.has(e.kind) && prev && prev.e.actor === e.actor && prev.parts.card) {
      prev.steps.push(cardStepText(e));
      return;
    }
    if (TARGET_KINDS.has(e.kind) && e.country) {
      const next = entries[i + 1];
      const rollKind = e.kind === "coup_target" ? "coup_roll" : "realignment_actor_roll";
      // The roll is recorded against CHANCE, so compare the country and let
      // the actor differ.
      if (next && next.e.kind === rollKind && next.e.country === e.country) {
        return;   // the roll row names the same country
      }
    }
    out.push({ ...entry, steps: [] });
  });
  for (const entry of out) {
    // Two steps at most: a row is a glance, and the whole chain is in the
    // expansion.
    if (entry.steps.length && !entry.parts.delta) {
      entry.parts.delta = entry.steps.slice(0, 2).join(" · ");
      entry.parts.deltaTitle = entry.steps.join(" · ");
    }
  }
  return out;
}

const FEED_FILTERS = [
  ["all", "All"],
  ["US", "US"],
  ["USSR", "USSR"],
  ["scoring", "Scoring"],
  ["cards", "Cards"],
];

function feedMatches(entry, filter) {
  if (filter === "all") return true;
  // `parts.actor` is the side carried forward past CHANCE; the raw event's
  // actor is CHANCE on every dice row, which is not what a player means by
  // "show me the USSR's moves".
  const card = entry.parts.card;
  if (filter === "US" || filter === "USSR") return entry.parts.actor === filter;
  if (filter === "scoring") return !!(card && /Scoring$/.test(card));
  return !!card;
}

/* The chips live outside #feed because the feed is rebuilt on every poll: a
 * chip inside it can be replaced between mousedown and mouseup, which swallows
 * the click entirely. */
function updateFilterChips() {
  for (const b of document.querySelectorAll("#feedbar .chip")) {
    b.setAttribute("aria-pressed", String(b.dataset.key === feedFilter));
  }
}

function buildFeedFilters() {
  const host = $("#feedbar");
  if (host.dataset.built) return;
  const row = document.createElement("div");
  row.className = "feedfilters";
  row.setAttribute("role", "group");
  row.setAttribute("aria-label", "Filter history");
  for (const [key, label] of FEED_FILTERS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.dataset.key = key;
    b.textContent = label;
    b.addEventListener("click", () => {
      feedFilter = feedFilter === key && key !== "all" ? "all" : key;
      localStorage.setItem("struggler.histfilter", feedFilter);
      updateFilterChips();
      renderPanel();
    });
    row.append(b);
  }
  host.append(row);
  host.dataset.built = "1";
  updateFilterChips();
}

/* The game's arc: VP across the window, filled by whoever is ahead, with a
 * tick where a scoring card landed. Reading the trend is the one thing rows
 * cannot do. */
function vpStrip(hist) {
  const wrap = document.createElement("div");
  wrap.className = "vpstrip";
  const head = document.createElement("div");
  head.className = "vphead";
  const vp = state.vp || 0;
  const label = document.createElement("span");
  label.textContent = "VP";
  const now = document.createElement("b");
  now.className = vp >= 0 ? "us" : "ussr";
  now.textContent = vp === 0 ? "even" : `${vp > 0 ? "+" : ""}${vp} ${vp > 0 ? "US" : "USSR"}`;
  head.append(label, now);
  wrap.append(head);
  if (hist.length < 2) return wrap;

  const W = 100, H = 30, MID = H / 2, SPAN = 20;   // VP scale: ±20 fills the strip
  const y = (v) => MID - (Math.max(-SPAN, Math.min(SPAN, v)) / SPAN) * (MID - 2);
  const x = (i) => (i / (hist.length - 1)) * W;
  const pts = hist.map((e, i) => `${x(i).toFixed(2)},${y(e.vp || 0).toFixed(2)}`).join(" ");
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label",
    `VP over the last ${hist.length} events, now ${vp === 0 ? "even" : `${vp} ${vp > 0 ? "US" : "USSR"}`}`);
  const area = `M 0,${MID} L ${pts.replace(/ /g, " L ")} L ${W},${MID} Z`;
  const areaPath = () => {
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", area);
    return p;
  };
  const line = document.createElementNS(ns, "path");
  line.setAttribute("d", `M ${pts.replace(/ /g, " L ")}`);
  line.setAttribute("class", "vpline");
  const mid = document.createElementNS(ns, "line");
  mid.setAttribute("x1", "0"); mid.setAttribute("x2", String(W));
  mid.setAttribute("y1", String(MID)); mid.setAttribute("y2", String(MID));
  mid.setAttribute("class", "vpmid");
  svg.append(mid);
  for (const [cls, clip] of [["us", "pos"], ["ussr", "neg"]]) {
    const a = areaPath();
    a.setAttribute("class", `vparea ${cls}`);
    a.setAttribute("clip-path", `url(#vp${clip})`);
    const cp = document.createElementNS(ns, "clipPath");
    cp.setAttribute("id", `vp${clip}`);
    const r = document.createElementNS(ns, "rect");
    r.setAttribute("x", "0"); r.setAttribute("width", String(W));
    r.setAttribute("y", clip === "pos" ? "0" : String(MID));
    r.setAttribute("height", String(MID));
    cp.append(r);
    svg.append(cp, a);
  }
  hist.forEach((e, i) => {
    if (!(e.payload && e.payload.card && /Scoring$/.test(e.payload.card))) return;
    const t = document.createElementNS(ns, "line");
    t.setAttribute("x1", x(i).toFixed(2)); t.setAttribute("x2", x(i).toFixed(2));
    t.setAttribute("y1", "0"); t.setAttribute("y2", String(H));
    t.setAttribute("class", "vptick");
    const ttl = document.createElementNS(ns, "title");
    ttl.textContent = `T${e.turn}: ${cardName(e.payload.card)} (${vpSwing(e, hist[i - 1])})`;
    t.append(ttl);
    svg.append(t);
  });
  svg.append(line);
  wrap.append(svg);
  return wrap;
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

function cardEl(cid, actionIndex, locked = null) {
  const m = META[cid] || {};
  const el = document.createElement("div");
  el.className = "card" + (m.side ? ` side-${m.side.toLowerCase()}` : "")
    + (actionIndex !== null ? " playable" : "")
    + (locked ? " locked" : "");
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
  // A locked card's reason outranks the card text: the player is asking why
  // nothing happens, not what the card does.
  if (locked) {
    el.title = `${m.name || cid} — ${locked}` + (m.event_summary ? `\n\n${m.event_summary}` : "");
    el.setAttribute("aria-disabled", "true");
  } else if (m.event_summary) el.title = m.event_summary;
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
  // "Remove from play if used as an event." — printed on the card itself, and
  // the difference between spending a card twice and spending it once. Shown
  // under the face because the VASSAL art cannot be relied on to carry it at
  // preview size.
  if (m.remove_after_event) {
    const gone = document.createElement("div");
    gone.className = "removeplay";
    gone.textContent = "Remove from play if used as an event";
    gone.title = "Playing this for its event sends it out of the game; "
      + "playing it for Ops discards it as normal.";
    previewEl.append(gone);
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
  // The text-card fallback stands in for the printed face, so it carries the
  // printed footer too.
  if (m.remove_after_event) {
    const gone = document.createElement("div");
    gone.className = "cardremove";
    gone.textContent = "remove from play if used as an event";
    el.append(gone);
  }
}

/* Why a card in hand cannot be played right now, in the player's words, or
 * null when it can. Only two of these are about the card itself (the scoring
 * deadline and a forced Missile Envy, both reported by the engine); the rest
 * are about the decision the game is actually waiting on. A dead click with
 * no explanation is the thing being fixed here. */
function cardBlockReason(cid) {
  if (!state) return "Loading…";
  if (state.is_terminal) return "The game is over";
  if (busy) return "Resolving your last move…";
  const d = state.decision;
  if (!d) return state.watch ? "This seat is a bot" : "Waiting for the opponent";
  if (d.kind === "headline_play" || d.kind === "action_round_play") {
    const r = state.play_restriction;
    if (r === "scoring_deadline") return "A Scoring card must be played this round";
    if (r === "missile_envy") return "Missile Envy must be played this round";
    return "Not offered this round";
  }
  return `Not now — ${(PROMPTS[d.kind] || pretty(d.kind)).toLowerCase()}`;
}

function renderHand() {
  const host = $("#hand");
  host.textContent = "";
  // Undo lives here, not only inside the decision box: the mistake a player
  // most wants back (Ops spent in the wrong country) happens mid-chain, while
  // the box is already asking for the next one.
  if (state.can_undo && !busy && !state.is_terminal) {
    const undo = document.createElement("button");
    undo.type = "button";
    undo.id = "undo";
    undo.textContent = "← Undo";
    undo.title = "Take back the last move (⌘Z or Ctrl+Z)";
    undo.addEventListener("click", goBack);
    host.append(undo);
  }
  const locked = [];
  for (const cid of state.hand) {
    const opt = cardOption(cid);
    const why = opt === null ? cardBlockReason(cid) : null;
    if (why) locked.push(why);
    host.append(cardEl(cid, opt, why));
  }
  // Whole hand inert: say it once in the bar instead of leaving the player to
  // hover every card to find out why.
  if (state.hand.length && locked.length === state.hand.length) {
    const note = document.createElement("div");
    note.id = "handstate";
    note.textContent = locked[0];
    host.prepend(note);
  }
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
  // "ops_remaining" is omitted here: the placement box shows it prominently
  // (see renderDecision) rather than as a small context line.
  for (const k of ["card", "event", "ops", "remaining", "mode", "type", "order", "choice", "subregion", "bonus", "country"]) {
    if (c[k] === undefined || c[k] === null) continue;
    if ((k === "card" || k === "event") && (c[k] === "none" || c[k] === "HIDDEN_CARD")) continue;
    const v = val(k, c[k]);
    if (v === null) continue;
    // The card is named by the prompt above it ("Play the card"), so a "Card"
    // label here just repeats the heading: name it and stop.
    if (k === "card" || k === "event") {
      parts.push(`<b>${esc(v)}</b>`);
      continue;
    }
    parts.push(`<b>${esc(pretty(k))}</b> ${esc(v)}`);
  }
  return parts.join(" · ");
}

/* Influence placement is the one decision driven entirely from the map, so it
 * gets the map's full height and a directive in the status bar instead of a
 * box listing every legal country. */
function isMapOnlyPick(d) {
  return !!d && d.kind === "place_influence"
    && d.options.some((o) => o.payload && o.payload.country);
}

/* The placement directive the box used to carry ("US: place 1 influence in
 * Western Europe · 7 remaining"), now the status bar's job. Setup reads from
 * the engine's `remaining`; an Ops spend from the pending decision's own
 * budget, with "last Op" landings spelled out because the next click ends the
 * spend. */
function placementDirective(d) {
  const c = d.context || {};
  const who = state.human_side === "US" ? "USA" : "CCCP";
  let line = `${who}: click a country to add 1 influence`;
  const where = [];
  // Region names arrive as enum values ("WESTERN_EUROPE"); read them out the
  // way every other surface does.
  if (c.subregion) where.push(regionLabel(c.subregion));
  if (c.restriction) where.push(pretty(c.restriction));
  if (where.length) line += ` in ${where.join(" ")}`;
  // Setup spends a count of *influence*; an Ops spend spends *Ops*, and one Op
  // can buy two influence in an opponent-controlled country. Two different
  // budgets, so they get two different sentences rather than one that counts
  // the wrong thing. (And a placement reached from an event carries neither.)
  if (c.setup && typeof c.remaining === "number") {
    line += ` — ${c.remaining} influence remaining`;
  } else {
    const ops = placementOperationsLine(d);
    if (ops) line += ` — ${ops}`;
  }
  return line;
}

/* "3 Ops remaining · last Op" for an Ops spend, "" when the decision does not
 * carry a budget the player can count. */
function placementOperationsLine(d) {
  const left = placementOpsLeft(d);
  if (left == null) return "";
  if (left > 1) return `${left} Ops remaining`;
  if (left === 1) return "1 Op remaining — last one";
  return "Last Op — this click ends the spend";
}

function renderDecision() {
  const box = $("#decision");
  box.textContent = "";
  const d = state.decision;
  // Placement has no box at all (see isMapOnlyPick). Every other country pick
  // needs the map, so its box drops to the bottom band to leave the map's
  // height alone; the rest sit in the middle. Once the player drags a box,
  // their spot wins for the session.
  const pick = !isMapOnlyPick(d) && !!d && COUNTRY_PICK_KINDS.has(d.kind);
  box.classList.toggle("pick", pick);
  if (decisionDragged) box.classList.remove("pick");
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
    // Don't claim the opponent is "thinking" while the player's own chits are
    // still in the air: the placement they just made is what they are watching.
    note.textContent = settling() ? "Resolving your move…" : "Opponent thinking…";
    box.append(note);
    // …and when those chits land, say so, rather than leaving "Resolving…" up
    // until the next poll happens to arrive.
    if (settling()) waitForSettled(() => { if (state && !state.decision) renderDecision(); });
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

  // Placement (and only placement) is map-only: the glowing countries are the
  // whole interface for it, the map keeps its full height, and the directive
  // lives in the status bar. Its "stop" option (the end of the spend) comes
  // from #actionbar's Done button, so there is nothing left to render here.
  if (isMapOnlyPick(d)) {
    box.hidden = true;   // the map is the whole interface for this one
    return;
  }
  // Every other country-picking decision keeps the box: the glowing markers are
  // the options (each one names a country), and the box carries both the
  // directive and a keyboard-reachable list. The view never moves on its own —
  // it stays where the player put it. An "up to" Ops spend (6.1.3 / 6.2.2) on
  // a *target* decision offers a country-less stop option, which is rendered
  // as its own button below.
  const countryOpts = d.options.filter((o) => o.payload && o.payload.country);
  const stopOpts = d.options.filter((o) => o.payload && o.payload.stop);
  if (countryOpts.length && countryOpts.length + stopOpts.length === d.options.length) {
    const anyDouble = d.kind === "place_influence" && !d.context.setup
      && countryOpts.some((o) => placementCost(o.payload.country, state.influence) === 2);
    const hint = document.createElement("em");
    hint.className = "hint";
    hint.textContent = "Click a glowing country on the map, or pick one here."
      + (anyDouble ? " An orange 2 badge is opponent-controlled and costs 2 Ops." : "");
    main.append(hint);
    // A persistent, keyboard-accessible list of the legal countries.
    const list = document.createElement("div");
    list.className = "countrybtns";
    for (const o of countryOpts) {
      const doubles = d.kind === "place_influence" && !d.context.setup
        && placementCost(o.payload.country, state.influence) === 2;
      const b = document.createElement("button");
      b.type = "button";
      b.innerHTML = `<b>${esc(pretty(o.payload.country))}</b>`
        + (doubles ? ` <span class="cost2txt">2 Ops</span>` : "");
      b.disabled = busy;
      b.addEventListener("click", () => act(o.index));
      list.append(b);
    }
    main.append(list);
    for (const o of stopOpts) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "stopbtn opt";
      b.innerHTML = `${glyphSvg(optionGlyph(o), "ficon opticon")}<b>${esc(optionLabel(o))}</b>`;
      b.disabled = busy;
      b.addEventListener("click", () => act(o.index));
      main.append(b);
    }
    return;
  }
  for (const o of d.options) {
    const b = document.createElement("button");
    b.className = "opt";
    b.innerHTML = `${glyphSvg(optionGlyph(o), "ficon opticon")}<b>${esc(optionLabel(o))}</b>`;
    b.disabled = busy;  // a stale option while an action is in flight
    b.addEventListener("click", () => act(o.index));
    main.append(b);
  }
}

/* How many card faces a pile draws before it asks. A screenful also fits the
 * 320px column, so the pile opens without scrolling sideways. */
const PILE_FACE_CAP = 48;

/* One card inside a pile: the face itself, at hand-card fidelity, with the
 * same hover preview every other card surface has. */
function pileCard(cid) {
  const el = document.createElement("div");
  el.className = "pilecard";
  el.title = cardName(cid);
  if (IMAGES[cid]) {
    const img = document.createElement("img");
    img.src = `/assets/cards/${IMAGES[cid]}`;
    img.alt = cardName(cid);
    img.loading = "lazy";
    img.addEventListener("error", () => { img.remove(); el.textContent = cardName(cid); });
    el.append(img);
  } else {
    el.textContent = cardName(cid);
  }
  el.addEventListener("mouseenter", () => showPreview(cid, el));
  el.addEventListener("mouseleave", () => { previewEl.hidden = true; });
  return el;
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
    `<p>Action box</p>` +
    `<label><input type="radio" name="set-place" value="center"${decisionPlace === "center" ? " checked" : ""}> Middle of the map <em>(drag it by its header)</em></label>` +
    `<label><input type="radio" name="set-place" value="panel"${decisionPlace === "panel" ? " checked" : ""}> Right column</label>` +
    // These are the defaults the start screen opens on, not a second way to
    // start a game (see openStart).
    `<p>New game defaults <em>(the start screen)</em></p>` +
    `<label><input type="radio" name="set-side" value="US"${play !== "USSR" ? " checked" : ""}> play as US</label>` +
    `<label><input type="radio" name="set-side" value="USSR"${play === "USSR" ? " checked" : ""}> play as USSR</label>` +
    `<label>USA extra influence +<b id="set-extra-n">${extra}</b>` +
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
  for (const r of document.querySelectorAll("input[name=set-place]")) {
    r.addEventListener("change", () => {
      decisionPlace = r.value;
      localStorage.setItem("struggler.decisionPlace", decisionPlace);
      applyDecisionPlacement();
    });
  }
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
  catchUpGen += 1;   // any boot poll still in flight is now stale
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
  // Boot polls the server while the bot sets up, which can take a while. The
  // start screen invites the player to deal a new game during that window, so
  // a poll that was already in flight must not land on top of the new game.
  const gen = catchUpGen;
  let prev = -1, stall = 0;
  while (state && !state.is_terminal && !state.decision && gen === catchUpGen) {
    const n = state.history_len ?? (state.history || []).length;
    if (n === prev) {
      if (++stall > 3) break;
    } else stall = 0;
    prev = n;
    render();
    let next;
    try {
      next = await fetchJson("/state");
    } catch (err) {
      showError("Lost connection to the game server.");
      return;
    }
    if (gen !== catchUpGen) return;   // a new game started while this was out
    state = next;
    render();
  }
}

function newGame() {
  // The start screen is the confirmation now — it is where the side and the
  // extra influence are chosen, and its own button is the "yes".
  openStart();
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

/* -- the start screen -----------------------------------------------------
 *
 * A game nobody has touched yet: still setting up, nothing to take back. The
 * seed deliberately plays no part in this, because the server restarts its
 * counting at `--seed` on every launch, so a seed-keyed marker would silently
 * suppress the screen after a server restart.
 */
function freshGame() {
  // Deliberately not gated on `busy`: boot is busy while the bot sets up, and
  // that is exactly the window in which the player should be choosing a side.
  return !!state && !state.watch && !state.is_terminal
    && !state.can_undo && state.turn === 1 && state.phase === "setup";
}

/* Both controls are per-game choices with a remembered default, so the screen
 * opens on whatever was used last. */
function seedStartControls() {
  const overlay = $("#start");
  if (!overlay) return;
  const side = localStorage.getItem("struggler.side") || "US";
  const extra = +(localStorage.getItem("struggler.usExtra") ?? 2);
  for (const r of overlay.querySelectorAll("input[name=start-side]")) {
    r.checked = r.value === side;
  }
  syncSideCards();
  const range = $("#start-extra");
  if (range) range.value = String(extra);
  const shown = $("#start-extra-n");
  if (shown) shown.textContent = `+${extra}`;
}

/* The card is the visible state; the radio inside it stays the control. */
function syncSideCards() {
  const overlay = $("#start");
  if (!overlay) return;
  for (const label of overlay.querySelectorAll(".sside")) {
    const input = label.querySelector("input");
    label.classList.toggle("sel", !!(input && input.checked));
  }
}

function renderStart() {
  const overlay = $("#start");
  if (!overlay) return;
  const want = !!state && !state.watch
    && (startPending || (!startConfirmed && freshGame()));
  overlay.hidden = !want;
  if (!want) {
    startFocused = false;
    return;
  }
  if (!overlay.dataset.seeded) {
    overlay.dataset.seeded = "1";
    seedStartControls();
  }
  if (!startFocused) {
    startFocused = true;
    const pick = overlay.querySelector("input:checked") || overlay.querySelector("input");
    if (pick) pick.focus();
  }
}

function openStart() {
  startPending = true;
  startFocused = false;
  const overlay = $("#start");
  if (overlay) overlay.dataset.seeded = "";  // re-read the stored defaults
  renderStart();
}

/* Escape leaves the game that is already on the table rather than dealing a
 * new one: the screen is a question, not a commitment. */
function dismissStart() {
  startPending = false;
  startConfirmed = true;
  startFocused = false;
  const overlay = $("#start");
  if (overlay) overlay.hidden = true;
  render();
}

async function startGame() {
  const overlay = $("#start");
  const picked = overlay && overlay.querySelector("input[name=start-side]:checked");
  const extra = +$("#start-extra").value;
  localStorage.setItem("struggler.side", picked ? picked.value : "US");
  localStorage.setItem("struggler.usExtra", String(extra));
  startPending = false;
  startConfirmed = true;   // the fresh game about to arrive must not re-ask
  startFocused = false;
  $("#start").hidden = true;
  await postGame("/new");
  fillSettings();          // the settings defaults follow the choice
}

function renderWinner() {
  const overlay = $("#winner");
  const chip = $("#winnerchip");
  if (!state.is_terminal) {
    overlay.hidden = true;
    overlay.dataset.forSeed = "";
    winnerFocused = false;
    winnerDismissed = false;
    if (chip) chip.hidden = true;
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
  // Build the dialog once per game; rebuilding every render would destroy the
  // focused buttons and drop keyboard focus.
  if (overlay.dataset.forSeed !== String(state.seed)) {
    overlay.dataset.forSeed = String(state.seed);
    winnerFocused = false;
    winnerDismissed = false;
    overlay.innerHTML = winnerSummaryHtml();
    // Watch mode has no seat to restart, so it gets the review button only.
    const again = $("#again");
    if (again) again.addEventListener("click", () => newGame());
    $("#wreview").addEventListener("click", () => {
      winnerDismissed = true;
      renderWinner();
      $("#wchip").focus();
    });
    const focusable = () => [...overlay.querySelectorAll("button")];
    // Keep Tab inside the dialog: the page behind it is a finished game, not
    // something to wander into.
    overlay.addEventListener("keydown", (e) => {
      if (e.key !== "Tab") return;
      const items = focusable();
      const at = items.indexOf(document.activeElement);
      const next = (at + (e.shiftKey ? -1 : 1) + items.length) % items.length;
      e.preventDefault();
      items[next].focus();
    });
  }
  overlay.hidden = winnerDismissed;
  if (chip) chip.hidden = !winnerDismissed;
  if (!winnerFocused && !winnerDismissed) {  // focus the dialog once, not every render
    winnerFocused = true;
    const first = overlay.querySelector("button");
    if (first) first.focus();
  }
}

/* The end of a game is the one moment the player wants the numbers: what the
 * final score was, how far the game got, and how the record now stands. The
 * summary is also the only way to reach the log of the game they just played
 * (see the "review" button), which the overlay used to cover for good. */
function winnerSummaryHtml() {
  const name = state.winner === "US" ? "USA" : state.winner === "USSR" ? "CCCP" : "Nobody";
  const you = state.human_side;
  const youName = you === "US" ? "USA" : "CCCP";
  const vp = state.vp || 0;
  const vpText = vp === 0 ? "level" : vp > 0 ? `US +${vp}` : `USSR +${-vp}`;
  const rec = recordOf(you);
  const won = state.winner === you;
  const stat = (label, value) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
  return `<div class="wcard">`
    + `<h2>${esc(name)} wins</h2>`
    + `<p class="wsub">${esc(state.game_over_reason || "")}</p>`
    + (state.watch ? ""
      : `<p class="wyou ${won ? "win" : "loss"}">You played ${esc(youName)} — `
        + `${won ? "win" : "loss"}</p>`)
    + `<dl class="wsum">`
    + stat("Final VP", vpText)
    + stat("Turn", String(state.turn))
    + stat("DEFCON", String(state.defcon))
    + stat("Your record", `${rec.w}–${rec.l}`)
    + `</dl>`
    + `<div class="wbtns">`
    + (state.watch ? "" : `<button type="button" id="again">New game</button>`)
    + `<button type="button" id="wreview">Review the board</button>`
    + `</div></div>`;
}

/* -- help -----------------------------------------------------------------
 *
 * One sentence per decision, worded to match what the engine actually does
 * (see coup_forecast / realignment_forecast, which the hover tables come
 * from), plus the two things the board art cannot tell a new player: how a
 * turn is shaped, and how to read a marker. The engine remains the authority;
 * this is the map, not the rulebook.
 */
const HELP_KINDS = {
  place_influence: "Place Influence: each Op puts one Influence in a country. "
    + "A country the opponent controls costs 2 Ops.",
  coup_target: "Coup: one die + your Ops − twice the country's Stability. "
    + "That much enemy Influence is removed, and anything left over becomes "
    + "yours. A coup in a Battleground country drops DEFCON by 1.",
  realignment_target: "Realignment: both sides roll one die and add their "
    + "modifiers (adjacency, who controls the neighbours, influence already "
    + "there). The higher roll removes the loser's Influence by the margin; "
    + "a tie does nothing. Your Ops are not added to the roll.",
  war_target: "War: the card names a region, and you pick the country it is "
    + "fought in. The card's own die decides the result.",
  headline_play: "Headline: both sides play one card face down and reveal "
    + "together, before the first action round.",
  action_round_play: "Action round: play one card — for its event, for "
    + "Operations, for the Space Race, or as the event it cancels.",
  play_mode: "How is this card being used: its event, or its Operations?",
  ops_type: "Spend the Ops on Influence, on a Coup, or on Realignment rolls.",
  event_ops_order: "The card was played for Operations, so its event still "
    + "fires for the opponent — choose whether that happens before or after.",
  event_choice: "The card asks you to choose.",
  event_influence: "Place or remove Influence as the card directs.",
  event_resume: "Continue when you are ready for the card to resolve.",
  quagmire_discard: "Quagmire: discard an Ops card to clear the trap.",
  held_card_discard: "Decide whether to discard the card being held.",
};

const HELP_KEYS = [
  ["1–9, Enter", "pick an option"],
  ["←↑→↓", "pan the map"],
  ["+ − 0", "zoom in, out, reset"],
  ["V", "next map view"],
  ["U or ⌘Z", "undo the last move"],
  ["M", "map only (hide the panel)"],
  ["?", "this panel"],
  ["Esc", "close panels"],
];

function helpHtml() {
  const d = state && state.decision;
  const now = d
    ? `<p class="hnow">${esc(PROMPTS[d.kind] || pretty(d.kind))}</p>`
      + `<p>${esc(HELP_KINDS[d.kind] || "Follow the prompt in the action box.")}</p>`
    : `<p class="hnow">Nothing is waiting on you</p>`
      + `<p>${state && state.watch ? "This is a bot-vs-bot game." : "Waiting for the opponent."}</p>`;
  const played = d && ((d.context || {}).card || (d.context || {}).event);
  const card = played && played !== "none" && played !== "HIDDEN_CARD"
    ? `<h3>Card in play</h3><p class="hcard">${esc(cardName(played))}</p>`
      + `<p>${esc((META[played] || {}).event_summary || "No text for this card.")}</p>`
    : "";
  const keys = HELP_KEYS
    .map(([k, what]) => `<div class="hkey"><kbd>${esc(k)}</kbd><span>${esc(what)}</span></div>`)
    .join("");
  return `<h2>How to play</h2>`
    + `<h3>Right now</h3>${now}`
    + card
    + `<h3>A turn</h3>`
    + `<p>Each turn opens with a headline, then the action rounds alternate. `
    + `Playing a scoring card scores its region. Influence never disappears `
    + `on its own — it is removed by coups, realignments, wars and events.</p>`
    + `<h3>Reading the map</h3>`
    + `<p>A marker is one country's Influence: the white pip is a country `
    + `someone is merely present in, the coloured pip is one they control `
    + `(control is ahead by at least the country's Stability). A gold `
    + `<b>2</b> badge means a placement there costs 2 Ops. Battlegrounds are `
    + `worth double in Scoring and cost DEFCON when couped.</p>`
    + `<h3>Defeat</h3>`
    + `<p>DEFCON 1 loses the game for whoever caused it, and a side at 20 VP `
    + `wins outright. Europe control ends it too.</p>`
    + `<h3>Keys</h3><div class="hkeys">${keys}</div>`;
}

function renderHelp() {
  const box = $("#help");
  if (!box) return;
  box.innerHTML = helpHtml();
  box.hidden = false;
}

/* The help panel follows the game: whatever is being asked right now is the
 * first thing in it, so "?" answers the question actually on screen. */
function toggleHelp() {
  const box = $("#help");
  if (!box) return;
  if (box.hidden) renderHelp();
  else box.hidden = true;
}

function toggleMapFocus() {
  panelHidden = !panelHidden;
  localStorage.setItem("struggler.mapfocus", panelHidden ? "1" : "0");
  document.body.classList.toggle("mapfocus", panelHidden);
  // The action box can be living in the panel that just disappeared.
  applyDecisionPlacement();
  layoutBoard();
  const back = $("#focusback");
  if (back) {
    back.hidden = !panelHidden;
    back.setAttribute("aria-pressed", String(panelHidden));
  }
}

function toggleSettings() {
  const box = $("#settings");
  if (!box) return;
  box.hidden = !box.hidden;
  if (!box.hidden) fillSettings();
}

/* Move the action box between the map and the right column. */
function applyDecisionPlacement() {
  const box = $("#decision");
  if (!box) return;
  // Map-only mode has no column to hold it, so the box floats regardless of
  // the stored preference; turning the panel back on restores it.
  const centered = decisionPlace === "center" || panelHidden;
  const host = centered ? $("#boardarea") : $("#panel");
  if (host && box.parentElement !== host) host.append(box);
  document.body.classList.toggle("decision-center", centered);
  if (!centered) {
    box.style.left = "";
    box.style.top = "";
    box.style.transform = "";
  }
  if (state) renderDecision();   // may run before the first /state reply
}

/* Drag the floating box out of the way of the countries you are picking. Only
 * the header drags, so the option buttons keep behaving like buttons. */
function enableDecisionDrag() {
  const box = $("#decision");
  let grab = null;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  box.addEventListener("pointerdown", (ev) => {
    if (!document.body.classList.contains("decision-center")) return;
    if (ev.target.closest("button, a, input, select")) return;
    const rect = box.getBoundingClientRect();
    grab = { dx: ev.clientX - rect.left, dy: ev.clientY - rect.top };
    box.classList.add("dragging");
    box.setPointerCapture(ev.pointerId);
    ev.preventDefault();
  });
  box.addEventListener("pointermove", (ev) => {
    if (!grab) return;
    decisionDragged = true;
    const area = $("#boardarea").getBoundingClientRect();
    const rect = box.getBoundingClientRect();
    box.style.transform = "none";
    box.style.left = `${Math.round(clamp(ev.clientX - area.left - grab.dx, 4, area.width - rect.width - 4))}px`;
    box.style.top = `${Math.round(clamp(ev.clientY - area.top - grab.dy, 4, area.height - rect.height - 4))}px`;
  });
  const release = () => { grab = null; box.classList.remove("dragging"); };
  box.addEventListener("pointerup", release);
  box.addEventListener("pointercancel", release);
}

boot();
