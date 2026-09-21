"""Guards for the second round of orientation work: the end of the game, the
piles, undo, help, map-only mode, the board load, and War odds.

The same rule as `test_ui_orientation.py`: the client halves are text guards
(the substance is asserted against the engine where it can be), and a guard
should fail when the feature is removed rather than merely rewritten.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from struggler.engine import DecisionKind, Engine, Side

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text()
CSS = (ROOT / "ui" / "style.css").read_text()
HTML = (ROOT / "ui" / "index.html").read_text()


def _serve_ui():
    spec = importlib.util.spec_from_file_location("serve_ui", ROOT / "scripts" / "serve_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def body_of(name: str, text: str = APP) -> str:
    """The body of a top-level function, for text guards."""
    match = re.search(rf"function {name}\(.*?\) \{{(.*?)\n\}}", text, re.S)
    assert match, f"{name}() not found"
    return match.group(1)


# -- the end of a game ------------------------------------------------------


def test_game_over_summary_shows_the_numbers_and_can_be_dismissed():
    summary = body_of("winnerSummaryHtml")
    for stat in ("Final VP", "Turn", "DEFCON", "Your record"):
        assert stat in summary, stat
    assert "game_over_reason" in summary
    assert "wreview" in summary                      # review the board
    assert 'id="winnerchip"' in HTML                 # and a way back to it
    winner = body_of("renderWinner")
    assert "winnerDismissed" in winner
    # The log has to be readable after the game, so dismissing cannot depend
    # on starting a new one.
    assert "overlay.hidden = winnerDismissed" in winner


def test_game_over_is_reachable_by_keyboard():
    keyboard = body_of("installKeyboard")
    assert "winnerDismissed = true" in keyboard, "Escape should dismiss the summary"
    winner = body_of("renderWinner")
    assert 'e.key !== "Tab"' in winner, "the dialog should trap Tab"


# -- piles ------------------------------------------------------------------


def test_piles_open_as_card_faces():
    panel = body_of("renderPanel")
    assert "pileCard(cid)" in panel
    assert "PILE_FACE_CAP" in panel
    card = body_of("pileCard")
    assert "/assets/cards/" in card
    assert "showPreview" in card, "a pile card should hover like every other card"
    assert ".pilecard" in CSS and ".pilecards" in CSS


# -- undo -------------------------------------------------------------------


def test_undo_is_offered_with_the_hand():
    hand = body_of("renderHand")
    assert "Undo" in hand
    assert "goBack" in hand
    keyboard = body_of("installKeyboard")
    assert 'toLowerCase() === "z"' in keyboard, "Cmd/Ctrl+Z should undo"
    # The chord may not fire when there is nothing to take back.
    assert "can_undo" in keyboard
    assert "#undo" in CSS


# -- help -------------------------------------------------------------------


def test_help_covers_every_decision_the_client_prompts():
    prompts = set(re.findall(
        r"^\s*([a-z_]+):", re.search(r"const PROMPTS = \{(.*?)\};", APP, re.S).group(1), re.M))
    helps = set(re.findall(
        r"^\s*([a-z_]+):", re.search(r"const HELP_KINDS = \{(.*?)\n\};", APP, re.S).group(1), re.M))
    missing = sorted(prompts - helps)
    assert not missing, f"decisions with no explanation in Help: {missing}"
    assert '<aside id="help"' in HTML
    assert "toggleHelp" in body_of("installKeyboard")
    assert "#help" in CSS


def test_documented_keys_are_actually_bound():
    keyboard = body_of("installKeyboard")
    for key in ("m", "v", "?", "u", "+", "-", "0"):
        assert f'e.key === "{key}"' in keyboard, f"Help documents {key} with nothing bound"
    assert ".hkey" in CSS


# -- map-only mode ----------------------------------------------------------


def test_map_only_mode_folds_the_panel_and_rescues_the_action_box():
    # The divider goes with the panel: nothing to drag once it is folded away.
    assert "body.mapfocus #panel,\nbody.mapfocus #colsplit { display: none; }" in CSS
    focus = body_of("toggleMapFocus")
    assert "applyDecisionPlacement()" in focus, "the action box may live in the panel"
    assert "localStorage" in focus, "the preference should survive a reload"
    # The box has to follow the panel out, or a "right column" setting would
    # hide the very controls the player needs.
    assert "panelHidden" in body_of("applyDecisionPlacement")
    assert "#focusback" in CSS and "focusback" in APP


# -- the board load ---------------------------------------------------------


def test_board_load_reports_progress_and_falls_back():
    load = body_of("loadBoard")
    assert "getReader()" in load, "progress needs a streamed response"
    assert "loadtext" in load and "--p" in load
    # Every failure path has to put the image back on the plain URL.
    assert load.count("img.src = url") >= 3
    board_img = re.search(r'<img id="board".*?>', HTML, re.S).group(0)
    assert "src=" not in board_img, "JS owns the board load now"
    assert "#boardload .loadbar" in CSS


# -- war odds ---------------------------------------------------------------


def test_odds_endpoint_covers_war_targets():
    """The three target picks that end in a die all have to answer /odds; a
    War used to fall through to "none" and leave the player guessing."""
    serve_ui = _serve_ui()
    session = serve_ui.Session(seed=3, us="human", ussr="greedy", events=True)
    engine = session.engine
    engine.board.influence["India"]["USSR"] = 3
    engine.push_war_target_choice(
        "Indo_Pakistani_War", Side.US, ["India", "Pakistan"],
        win_from=4, vp=2, military_ops=1,
    )
    payload = serve_ui.forecast_for(engine, engine.pending_decision, "India")
    assert payload["kind"] == "war"
    assert payload["needed"] == 4 and payload["wins"] == 3
    assert [r["roll"] for r in payload["rows"]] == [1, 2, 3, 4, 5, 6]
    # A country the card does not name still gets nothing.
    assert serve_ui.forecast_for(engine, engine.pending_decision, "Poland") == {"kind": "none"}

    handler = serve_ui.make_handler(session, {})
    sent: dict = {}
    request = handler.__new__(handler)   # no socket: dispatch only
    request.path = "/odds?country=India"
    request._send_json = lambda code, body, *_a: sent.update(code=code, payload=body)
    request.do_GET()
    assert sent["code"] == 200 and sent["payload"]["kind"] == "war"


def test_client_renders_war_odds_and_asks_for_them():
    assert 'TARGET_ROLL_KINDS = new Set(["coup_target", "realignment_target", "war_target"])' in APP
    assert "TARGET_ROLL_KINDS.has(d.kind)" in body_of("prefetchOdds")
    assert "TARGET_ROLL_KINDS.has(d.kind)" in body_of("legalTargetDecision")
    html = body_of("oddsHtml")
    assert 'p.kind === "war"' in html
    assert "needed" in html
    assert "this war can be fought in" in body_of("countryChoiceLine")


def test_war_roll_is_a_roll_the_client_already_animates():
    """The war table describes a WAR_ROLL, which the client must be able to
    draw: odds for a roll the player never sees would be worse than none."""
    rolls = set(re.findall(
        r"([a-z_]+): \d", re.search(r"const ROLL_KIND = \{(.*?)\};", APP, re.S).group(1)))
    assert DecisionKind.WAR_ROLL.value in rolls
    assert DecisionKind.WAR_TARGET.value in {k.value for k in DecisionKind}


# -- text boxes are sized to their content ----------------------------------


def test_decision_options_do_not_stretch_to_the_panel_width():
    """The option rows carry a width ceiling instead of `width: 100%`.

    In the floating box the ceiling is inert: the box shrink-wraps around the
    rows. In the right column it is the whole point — the column is ~320px in
    a normal window and much wider when the player drags the splitter, and a
    row of text stretched to the splitter's width is a long empty rectangle.
    """
    rule = re.search(r"#decision \{ --opt-w: ([^;]+); \}", CSS)
    assert rule, "the option-width token is gone"
    assert "min(" in rule.group(1), "an unbounded width cap would not cap anything"
    body = re.search(r"\n#decision button \{(.*?)\n\}", CSS, re.S).group(1)
    assert "width: var(--opt-w)" in body
    assert "width: 100%" not in body


def test_floating_action_box_shrink_wraps_instead_of_fixing_its_width():
    """A fixed 460px made every box as wide as the widest one.

    A single-line card prompt floated in a 900px slab of empty space. The box
    now sizes to its content and only *caps* at 460px, and the text column
    shrink-wraps next to the played card rather than taking the leftover width
    — without that, `fit-content` on the box would measure a full-width column
    and land right back on the ceiling.
    """
    block = re.search(
        r"body\.decision-center #decision:not\(\[hidden\]\) \{(.*?)\n\}", CSS, re.S).group(1)
    assert "width: fit-content" in block
    assert re.search(r"max-width: min\(460px", block)
    assert not re.search(r"^\s*width: min\(460px", block, re.M), "a fixed width is back"
    assert re.search(
        r"body\.decision-center #decision \.dcol-main \{ flex: 0 1 auto; \}", CSS), \
        "the text column must shrink-wrap for the box to track its content"


def test_the_other_text_boxes_stay_within_a_readable_measure():
    """The start screen, the help panel and the settings block, likewise.

    Each is prose or a short form: past ~430px the boxes stop gaining content
    and start gaining margin. These are the widths a 1512px window renders.
    """
    start = re.search(r"#start \.scard \{(.*?)\n\}", CSS, re.S).group(1)
    assert re.search(r"width: min\((\d+)px", start), start
    assert int(re.search(r"width: min\((\d+)px", start).group(1)) <= 460
    help_panel = re.search(r"#help \{(.*?)\n\}", CSS, re.S).group(1)
    assert int(re.search(r"width: min\((\d+)px", help_panel).group(1)) <= 380
