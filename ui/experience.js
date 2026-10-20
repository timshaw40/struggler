"use strict";

/* Public game summaries, scoring explanations, and optional placement plans.
 * Private save files remain on the local server; the library receives metadata. */
let libraryReturnFocus = null;
let libraryGeneration = 0;
let connectionProblem = null;
let recoveryTimer = null;
let recoveryRunning = false;
let recoveryAttempts = 0;
let planMode = false;
let planCountries = [];
let planResult = null;
let planPosition = null;
let planPending = false;
let planGeneration = 0;
let overviewOpen = null;

const scoreText = (net) => net > 0 ? `US +${net}` : net < 0 ? `USSR +${-net}` : "Even";

function installExperience() {
  document.addEventListener('keydown', (e) => {
    if (!planMode || !$('#library').hidden || !$('#start').hidden) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
      e.preventDefault(); e.stopImmediatePropagation(); stagePlan(null);
    }
  }, true);
  $("#librarybtn").addEventListener("click", openLibrary);
  $("#start-library").addEventListener("click", openLibrary);
  $("#library-close").addEventListener("click", closeLibrary);
  $("#library-new").addEventListener("click", () => { closeLibrary(); openStart(); });
  $("#connection-retry").addEventListener("click", () => recoverConnection(true));
  $("#plan-toggle").addEventListener("click", () => {
    resetPlan();
    planMode = !planMode;
    planPosition = state && state.decision ? `${state.game_id}:${state.decision.id}` : null;
    renderExperience();
    renderBoard();
  });
  $("#plan-undo").addEventListener("click", () => stagePlan(null));
  $("#plan-clear").addEventListener("click", () => { resetPlan(); renderExperience(); renderBoard(); });
  $("#plan-commit").addEventListener("click", commitPlacementPlan);
  // Modal shortcuts work while controls are focused, before the global
  // keyboard handler intentionally yields to those controls.
  for (const modal of [$("#library"), $("#start")]) {
    modal.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault(); e.stopPropagation();
        if (modal.id === "library") closeLibrary(); else dismissStart();
      } else if (e.key === "Tab") {
        const controls = [...modal.querySelectorAll("button, input")].filter((el) => !el.disabled && el.getClientRects().length);
        const i = controls.indexOf(document.activeElement);
        if (!controls.length) return;
        if ((e.shiftKey && i <= 0) || (!e.shiftKey && i === controls.length - 1)) {
          e.preventDefault(); controls[e.shiftKey ? controls.length - 1 : 0].focus();
        }
      }
    });
  }
}

function resetPlan() {
  planGeneration += 1;
  planCountries = [];
  planResult = null;
  planPending = false;
}

function renderExperience() {
  if (!state || dicePresentationPending()) return;
  const phase = state.phase === 'setup' ? 'Setup' : state.phase === 'headline' ? 'Headline' : `Round ${state.action_round}`;
  $('#dashboard-status').innerHTML = `<span>Turn ${state.turn} · ${esc(phase)}</span><span>${esc(scoreText(state.vp))}</span>`
    + `<span class="${state.defcon <= 2 ? 'danger' : ''}">DEFCON ${state.defcon}${state.defcon === 2 ? ' · Nuclear danger' : ''}</span>`
    + `<span>${state.is_terminal ? 'Game over' : state.decision && !busy ? 'Your move' : connectionProblem ? 'Reconnecting' : 'Resolving'}</span>`;
  const save = $("#save-status");
  save.textContent = state.save_error || (state.saved_at ? `Saved ${new Date(state.saved_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}` : "");
  save.classList.toggle("save-failed", !!state.save_error);
  $("#librarybtn").hidden = !state.library_enabled;
  $("#start-library").hidden = !state.library_enabled;
  $("#start-library").textContent = state.saved_at ? "Continue / saved games" : "Saved games";
  const pos = state.decision ? `${state.game_id}:${state.decision.id}` : null;
  if (planPosition !== pos) { resetPlan(); planMode = false; planPosition = pos; }
  const d = state.decision;
  const canPlan = !state.is_terminal && !state.watch && d && d.kind === "place_influence" && !d.context.setup;
  $("#planbar").hidden = !canPlan;
  $("#plan-toggle").textContent = planMode ? "✓ Planning placements" : "Plan placements";
  $("#plan-toggle").setAttribute("aria-pressed", String(planMode));
  $("#plan-toggle").disabled = busy || planPending;
  $("#plan-controls").hidden = !planMode;
  $("#plan-count").textContent = planPending ? "Checking…" : planCountries.length ?
    `${planCountries.length} influence planned${planResult ? ` · ${placementOperationsLine({context: planResult.context}) || "spend ends here"}` : ""}` : "Click countries to plan; nothing is placed until you commit.";
  $("#plan-undo").disabled = !planCountries.length || planPending || busy;
  $("#plan-clear").disabled = !planCountries.length || planPending || busy;
  $("#plan-commit").disabled = !planResult || planPending || busy;
  $("#plan-chips").textContent = "";
  if (planMode) {
    const counts = new Map();
    planCountries.forEach((c) => counts.set(c, (counts.get(c) || 0) + 1));
    for (const [c, n] of counts) {
      const chip = document.createElement("span"); chip.textContent = `${pretty(c)} +${n}`;
      $("#plan-chips").append(chip);
    }
  }
  renderOverview();
  renderActionBar();
  layoutBoard();
}

function renderOverview() {
  if (dicePresentationPending()) return;
  const host = $("#strategic-overview");
  const signature = JSON.stringify([state.game_id, state.strategy, state.military_ops, state.space_race,
                                    state.defcon, state.turn_effects, state.game_effects, overviewOpen,
                                    planResult && planResult.strategy]);
  if (host.dataset.signature === signature) return;
  host.dataset.signature = signature;
  const rows = state.strategy || [];
  const mil = state.military_ops || {};
  const space = state.space_race || {};
  const shortfall = (side) => Math.max(0, state.defcon - (mil[side] || 0));
  host.innerHTML = `<div class="overview-tracks"><span>Military Ops <b>US ${mil.US || 0} · USSR ${mil.USSR || 0}</b></span>`
    + `<span>Space <b>US ${space.US || 0} · USSR ${space.USSR || 0}</b></span></div>`
    + `<p class="overview-note">At current DEFCON, military shortfall: US ${shortfall("US")} · USSR ${shortfall("USSR")} VP at turn end.</p>`
    + `<div class="overview-caption">If scored now <small>Each region separately</small></div>`;
  for (const row of rows) {
    const wrap = document.createElement("div");
    wrap.className = "region-score";
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-expanded", String(overviewOpen === row.key));
    const projected = planResult && planResult.strategy.find((r) => r.key === row.key);
    const projectedChange = projected && (projected.net !== row.net || projected.wins !== row.wins);
    const net = row.wins ? `${row.wins} wins` : scoreText(row.net);
    button.innerHTML = `<span>${esc(row.region)}</span><b class="${row.wins === "US" || row.net > 0 ? "us" : row.wins === "USSR" || row.net < 0 ? "ussr" : ""}">${esc(net)}</b>`
      + (projectedChange ? `<small>Plan → ${esc(projected.wins ? `${projected.wins} wins` : scoreText(projected.net))}</small>` : "");
    button.addEventListener("click", () => { overviewOpen = overviewOpen === row.key ? null : row.key; renderOverview(); });
    wrap.append(button);
    if (overviewOpen === row.key) {
      const detail = document.createElement("div"); detail.className = "region-detail";
      for (const [key, side] of [["us", "US"], ["ussr", "USSR"]]) {
        const c = row.counts && row.counts[side];
        const value = row[key];
        const line = document.createElement("p");
        line.textContent = `${side}: ${row.wins === side ? "Control · victory" : (value.tier || (c ? "No presence" : "country scoring"))} · ${value.vp} VP`
          + (c ? ` · ${c.countries} countries, ${c.battlegrounds}/${row.total_battlegrounds} battlegrounds.` : ". Thailand scores 2; other controlled countries score 1.");
        detail.append(line);
        if (c && c.domination_needs.length && value.tier !== "Control") {
          const needs = document.createElement("p"); needs.className = "overview-note";
          needs.textContent = `${side} domination needs: ${c.domination_needs.join("; ")}.`;
          detail.append(needs);
        }
      }
      if (row.wins) { const p = document.createElement("p"); p.textContent = `${row.wins} controls Europe: immediate victory when scored.`; detail.append(p); }
      if (row.adjusted) { const p = document.createElement("p"); p.textContent = "Includes active scoring effects."; detail.append(p); }
      const zoom = document.createElement("button"); zoom.type = "button"; zoom.textContent = "Show region";
      zoom.addEventListener("click", () => setView(row.region === "Southeast Asia" ? "Asia" : row.region));
      detail.append(zoom); wrap.append(detail);
    }
    host.append(wrap);
  }
  const effects = Object.entries({...state.game_effects, ...state.turn_effects});
  if (effects.length) {
    const details = document.createElement("details");
    details.innerHTML = `<summary>Active effects (${effects.length})</summary>`;
    for (const [key, value] of effects) {
      const meta = EFFECT_META[key];
      const p = document.createElement("p");
      p.textContent = `${meta ? meta.short : pretty(key)}${meta && meta.sideValue ? ` (${value})` : ""}: ${meta ? meta.note : "Active game modifier."}`;
      details.append(p);
    }
    host.append(details);
  }
}

function previewLines(index) {
  return (state.action_previews && state.action_previews[String(index)] || {}).lines || [];
}

function addOptionPreview(button, index) {
  const lines = previewLines(index);
  if (!lines.length) return;
  const note = document.createElement("small");
  note.className = "consequence-note";
  note.textContent = lines.join(" ");
  button.append(note);
}

function placementPreviewHtml(country) {
  if (planMode && planResult) {
    const next = planResult.previews[country];
    const lines = next ? ['Next planned point:', ...next.lines] : ['No more points can be planned here in this spend.'];
    const inf = planResult.influence[country];
    if (inf) lines.unshift(`Planned result: US ${inf.US} / USSR ${inf.USSR}${planResult.controls[country] ? ` · ${planResult.controls[country]} control` : ' · uncontrolled'}.`);
    return lines.map((line) => `<div class="cnote consequence">${esc(line)}</div>`).join('');
  }
  const option = state.decision && state.decision.options.find((o) => o.payload.country === country);
  if (!option) return "";
  return previewLines(option.index).map((line) => `<div class="cnote consequence">${esc(line)}</div>`).join("");
}

async function stagePlan(country) {
  if (!planMode || planPending || busy || dicePresentationPending()) return;
  const proposed = country ? [...planCountries, country] : planCountries.slice(0, -1);
  if (!proposed.length) { resetPlan(); renderExperience(); renderBoard(); return; }
  const generation = ++planGeneration;
  planPending = true; renderExperience();
  try {
    const result = await experiencePost('/plan', {countries: proposed, game_id: state.game_id, decision_id: state.decision.id});
    if (generation !== planGeneration) return;
    planCountries = proposed; planResult = result;
  } catch (err) { showError(err.message); }
  finally {
    if (generation === planGeneration) { planPending = false; renderExperience(); renderBoard(); }
  }
}

async function commitPlacementPlan() {
  if (!planResult || planPending || busy || dicePresentationPending()) return;
  busy = true; render();
  try {
    const next = await experiencePost('/plan/commit', {countries: planCountries, game_id: state.game_id, decision_id: state.decision.id});
    resetPlan(); planMode = false; state = next;
  } catch (err) {
    if (err instanceof TypeError) reportConnectionProblem(err); else showError(err.message);
  }
  finally { busy = false; lastRenderSig = null; render(); }
}

function mapPick(index) {
  if (planMode) {
    const option = state.decision.options.find((o) => o.index === index);
    if (option && option.payload.country) stagePlan(option.payload.country);
    return;
  }
  act(index);
}

function planAllows(country) {
  return !planMode || !planResult || planResult.next_options.includes(country);
}

async function experiencePost(path, body) {
  const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok || data.error) {
    if (data.state) { state = data.state; lastRenderSig = null; render(); }
    throw new Error(data.error || "Could not complete this request.");
  }
  return data;
}

async function openLibrary() {
  libraryReturnFocus = document.activeElement;
  const box = $("#library"); box.hidden = false;
  $("#library-close").focus();
  const host = $("#library-list"); host.textContent = "Loading saved games…";
  const generation = ++libraryGeneration;
  try {
    const data = await fetchJson("/games");
    if (generation !== libraryGeneration || box.hidden) return;
    host.textContent = "";
    if (!data.games.length) host.textContent = "Your games will be saved here automatically.";
    for (const game of data.games) {
      const card = document.createElement("article"); card.className = "saved-game";
      const finished = game.finished;
      card.innerHTML = `<div><b>${finished ? game.winner ? `${esc(game.winner)} won` : "Finished" : "In progress"} · You: ${esc(game.side)}</b>`
        + `<p>Turn ${game.turn} · Round ${game.action_round} · ${esc(scoreText(game.vp))} · ${esc(pretty(game.bot))} bot</p>`
        + `<small>${esc(new Date(game.saved_at).toLocaleString())}${game.reason ? ` · ${esc(pretty(game.reason))}` : ""}</small></div>`;
      const button = document.createElement("button"); button.type = "button";
      button.textContent = finished ? "View result" : "Continue";
      button.addEventListener("click", () => loadSavedGame(game.id, button));
      card.append(button); host.append(card);
    }
  } catch (err) { host.textContent = `Could not read the library: ${err.message}. Close and try again.`; }
}

function closeLibrary() {
  libraryGeneration += 1;
  $("#library").hidden = true;
  if (libraryReturnFocus && libraryReturnFocus.isConnected) libraryReturnFocus.focus();
}

async function loadSavedGame(id, button) {
  if (busy) return;
  busy = true; button.disabled = true;
  await fxChain;
  catchUpGen += 1;
  try {
    const data = await experiencePost('/games/load', {id});
    resetPlan(); planMode = false;
    seenTotal = -1; prevInf = null; pendingRealignActor = null;
    expandedRows.clear(); seenRows.clear(); oddsCache.clear(); firstFeedBuild = true;
    clearAction(); state = data; lastRenderSig = null;
    startConfirmed = true; startPending = false;
    $("#start").hidden = true; closeLibrary();
    render();
    if (!state.watch) await catchUp();
    else if (playing) setTimeout(tick, 400);
    $("#librarybtn").focus();
  } catch (err) { showError(`Could not load this game: ${err.message}`); }
  finally { busy = false; button.disabled = false; render(); }
}

function reportConnectionProblem(err) {
  connectionProblem = err.message || String(err);
  if (err.serverFailure) recoveryAttempts = 5;
  const box = $("#connection-status"); box.hidden = false;
  $("#connection-message").textContent = state && state.saved_at && !state.save_error
    ? "Connection interrupted. Your last saved move is safe on this computer."
    : "Connection interrupted. Reconnecting to the game server.";
  if (err.serverFailure) $('#connection-message').textContent = `Opponent could not complete its move: ${connectionProblem}. Choose Retry to try again.`;
  $("#connection-retry").textContent = "Retry";
  if (!recoveryTimer && recoveryAttempts < 5) {
    recoveryTimer = setTimeout(() => { recoveryTimer = null; recoverConnection(false); }, Math.min(1000 * 2 ** recoveryAttempts, 15000));
  }
}

function clearConnectionProblem() {
  connectionProblem = null; recoveryAttempts = 0;
  if (recoveryTimer) clearTimeout(recoveryTimer);
  recoveryTimer = null;
  $("#connection-status").hidden = true;
}

async function recoverConnection(manual) {
  if (recoveryRunning) return;
  recoveryRunning = true; recoveryAttempts += 1;
  $("#connection-message").textContent = "Reconnecting…";
  $("#connection-retry").disabled = true;
  try {
    // Never replay a POST after a dropped response: read the authoritative
    // position first, so a successful-but-unacknowledged move is not duplicated.
    if (!Object.keys(META).length) {
      if (manual) window.location.reload();
      else throw new Error('Reload to finish loading the game');
      return;
    }
    const next = await fetchJson('/position');
    const switched = state && state.game_id !== next.game_id;
    if (switched) {
      seenTotal = -1; prevInf = null; oddsCache.clear(); expandedRows.clear(); clearAction();
    }
    state = next; clearConnectionProblem(); busy = false; lastRenderSig = null; render();
    if (!next.watch && !next.is_terminal && !next.decision) {
      busy = true;
      try { await catchUp(); } finally { busy = false; render(); }
    }
  } catch (err) {
    reportConnectionProblem(err);
    if (manual || recoveryAttempts >= 5) $("#connection-message").textContent = "Server unavailable. Restart it, then choose Retry to recover the saved game.";
  } finally { recoveryRunning = false; $("#connection-retry").disabled = false; }
}
