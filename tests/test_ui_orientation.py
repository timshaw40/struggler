"""Guards for the three orientation cues the player reads before acting.

Each one exists because a player had to guess: whether the game was waiting
on them at all (the tab title), what a country is worth and whether an attack
there is even legal (the hover tip), and why a card in hand does nothing (the
hand bar). The client halves are text guards in the style of
`test_dice_ui.py`; the substance — that the tip and the hand cannot disagree
with the rules the engine enforces — is asserted against the engine itself.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from struggler.engine import DecisionKind, Engine, Side

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text()


def _serve_ui():
    spec = importlib.util.spec_from_file_location("serve_ui", ROOT / "scripts" / "serve_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- the engine facts both cues are built from -----------------------------


def test_play_restriction_flags_the_scoring_deadline_and_missile_envy():
    """The two ways a hand narrows, in the order the engine applies them."""
    engine = Engine.new_game(seed=4)
    side = Side.USSR
    # Reach this side's last round of the turn without hard-coding the round
    # structure: the play index before which nothing of its is left.
    total = engine._total_action_rounds()
    last = max(i for i in range(total) if engine._side_for_play_index(i) is side)
    engine._ars_played = last + 1
    assert engine._remaining_action_rounds(side) == 1

    engine.hands[side.value] = ["Middle_East_Scoring", "Asia_Scoring"]
    assert engine.play_restriction(side) == "scoring_deadline"

    # A forced Missile Envy with rounds to spare is the only restriction.
    engine._ars_played = 1
    assert engine._remaining_action_rounds(side) > 1
    engine.game_effects["missile_envy_forced"] = side.value
    engine.hands[side.value] = ["Missile_Envy", "Asia_Scoring"]
    assert engine.play_restriction(side) == "missile_envy"

    # Both at once: the scoring deadline wins, because carrying a scoring card
    # past the end of the turn is never legal.
    engine._ars_played = last + 1
    engine.hands[side.value] = ["Missile_Envy", "Asia_Scoring"]
    assert engine.play_restriction(side) == "scoring_deadline"

    # An ordinary hand is open.
    engine.hands[side.value] = ["Missile_Envy", "Asia_Scoring"]
    engine.game_effects.pop("missile_envy_forced")
    engine._ars_played = 1
    assert engine.play_restriction(side) is None


def test_play_restriction_agrees_with_the_cards_actually_offered():
    """Play a whole game choosing the first option: at every action-round
    decision the reported restriction must describe the option list exactly,
    which is what lets the hand bar name the card that is missing and why."""
    engine = Engine.new_game(seed=19)
    seen = 0
    restriction_seen: set[str | None] = set()
    steps = 0
    while not engine.is_terminal and steps < 8000:
        decision = engine.pending_decision
        if decision is None:
            break
        if decision.kind is DecisionKind.ACTION_ROUND_PLAY:
            actor = decision.actor
            hand = set(engine.hands[actor.value])
            offered = {a.payload.get("card") for a in decision.options}
            restriction = engine.play_restriction(actor)
            restriction_seen.add(restriction)
            seen += 1
            if restriction == "missile_envy":
                assert offered == {"Missile_Envy"}, (actor, offered)
            elif restriction == "scoring_deadline":
                scoring = {c for c in hand if engine.cards[c].scoring}
                assert offered == scoring, (actor, offered)
            else:
                # The whole hand, plus the China Card when this side holds it.
                assert hand <= offered, (actor, hand - offered)
                extra = offered - hand
                assert extra <= {"The_China_Card", "sit_out"}, (actor, extra)
        engine.step(decision.options[0])
        steps += 1
    assert seen >= 10, f"only {seen} action rounds exercised"
    assert restriction_seen <= {None, "scoring_deadline", "missile_envy"}


def test_country_facts_defcon_floor_matches_the_engine_rule():
    """The floor the tooltip quotes is the one the engine enforces (8.1.5),
    for every region, including the regions with no floor at all."""
    engine = Engine.new_game(seed=12)
    facts = engine.country_facts()
    # Influence in every country so the "opponent must be present" clause never
    # masks the DEFCON one; a US attacker skips the USSR-only effect locks.
    for cid in engine.board.countries:
        engine.board.influence[cid]["USSR"] = 1
    for cid, fact in facts.items():
        for defcon in range(1, 6):
            engine.defcon = defcon
            allowed = engine._usable_coup_realign_target(Side.US, cid)
            assert allowed == (defcon >= fact["min_defcon"]), (cid, defcon, fact)
    assert len(facts) == len(engine.board.countries) > 80


def test_country_facts_match_the_shipped_country_data():
    """Region and Battleground in the facts must be the data file's values,
    since the tip phrases them as rules ("Battleground" changes the Coup
    DEFCON penalty and the Scoring multiplier)."""
    engine = Engine.new_game(seed=12)
    raw = json.loads((ROOT / "src" / "struggler" / "data" / "countries.json").read_text())
    facts = engine.country_facts()
    for cid, entry in raw["countries"].items():
        if cid not in facts:  # Chinese Civil War is optional
            continue
        assert facts[cid]["region"] == entry["region"]
        assert facts[cid]["battleground"] is entry["battleground"]
        assert facts[cid]["stability"] == entry["stability"]


# -- the server half --------------------------------------------------------


def test_countryfacts_route_serves_the_geography_the_tip_needs():
    """Dispatch the real handler for /countryfacts without a socket: binding a
    listening port is not permitted in every sandbox, and the interesting part
    is the route and its payload, not the write to the wire."""
    serve_ui = _serve_ui()
    session = serve_ui.Session(seed=3, us="human", ussr="greedy", events=True)
    handler = serve_ui.make_handler(session, {})
    sent: dict = {}

    def capture(code: int, payload: object, *_args: object) -> None:
        sent.update(code=code, payload=payload)

    request = handler.__new__(handler)  # no socket, no __init__: do_GET only
    request.path = "/countryfacts"
    request._send_json = capture
    request.do_GET()

    assert sent["code"] == 200
    facts = sent["payload"]
    assert facts["Poland"] == {
        "region": "EUROPE",
        "battleground": True,
        "stability": 3,
        "min_defcon": 5,
    }
    assert facts["Iran"]["min_defcon"] == 3
    assert facts["Angola"]["min_defcon"] == 1  # no floor in Africa
    assert set(facts) == set(session.engine.board.countries)


def test_state_surfaces_the_play_restriction_only_when_it_applies():
    serve_ui = _serve_ui()
    session = serve_ui.Session(seed=3, us="human", ussr="greedy", events=True)
    session.advance()
    decision = session.engine.pending_decision
    payload = session.state()
    if (
        decision is not None
        and decision.actor is session.human_side
        and decision.kind is DecisionKind.ACTION_ROUND_PLAY
    ):
        assert payload["play_restriction"] == session.engine.play_restriction(
            session.human_side
        )
    else:
        assert payload["play_restriction"] is None
    # The field is always present, so the client never has to feature-detect it.
    assert "play_restriction" in payload


# -- the client half --------------------------------------------------------


def test_client_announces_the_turn_in_the_tab():
    assert "function renderCue()" in APP
    # Called from the one place every state change flows through.
    render = re.search(r"function render\(\) \{(.*?)\n\}", APP, re.S)
    assert render, "render() not found"
    assert "renderCue();" in render.group(1)
    assert "document.title = title" in APP
    assert "data:image/svg+xml," in APP  # the drawn favicon
    # The chime is opt-out like every other sound, and only fires on the beat
    # where the turn comes back while the player is elsewhere.
    chime = re.search(r"function cueSound\(\) \{(.*?)\n\}", APP, re.S)
    assert chime, "cueSound() not found"
    assert 'localStorage.getItem("struggler.sound") === "0"' in chime.group(1)
    assert 'document.hidden' in APP


def test_hover_tip_carries_the_country_facts():
    tip = re.search(r"function showCountryTip\(.*?\) \{(.*?)\n\}", APP, re.S)
    assert tip, "showCountryTip() not found"
    for hook in ("countryFactsLine(cid, inf)", "countryChoiceLine(cid)"):
        assert hook in tip.group(1), hook
    facts = re.search(r"function countryFactsLine\(.*?\) \{(.*?)\n\}", APP, re.S).group(1)
    for fact in ("controls", "Battleground", "Stability"):
        assert fact in facts, fact
    choice = re.search(r"function countryChoiceLine\(.*?\) \{(.*?)\n\}", APP, re.S).group(1)
    # Both halves of the "may I attack here?" answer, from the engine's floor.
    assert "min_defcon" in choice
    assert "No enemy influence" in choice
    assert "Costs 2 Ops" in choice


def test_unplayable_cards_say_why():
    hand = re.search(r"function renderHand\(\) \{(.*?)\n\}", APP, re.S)
    assert hand, "renderHand() not found"
    assert "cardBlockReason(cid)" in hand.group(1)
    assert "handstate" in hand.group(1)  # one caption when the whole bar is inert
    reason = re.search(r"function cardBlockReason\(.*?\) \{(.*?)\n\}", APP, re.S)
    assert reason, "cardBlockReason() not found"
    body = reason.group(1)
    for branch in ("scoring_deadline", "missile_envy", "Waiting for the opponent"):
        assert branch in body, branch
    card = re.search(r"function cardEl\(.*?\) \{(.*?)\n\}", APP, re.S)
    assert card, "cardEl() not found"
    assert 'aria-disabled' in card.group(1)
    assert 'locked' in card.group(1)
