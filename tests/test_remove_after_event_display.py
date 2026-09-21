"""Cards that leave the game when played for their event have to say so.

The rule is printed on the card: "Remove from play if used as an event." It
means the card is spent on its event but discarded as normal when played for
Ops, and the engine implements exactly that (`core.py`: the card goes to
`removed_cards` only when the event actually fired).

The data already carried it — all 110 cards have `remove_after_event`, 50 of
them true — and `serve_ui.py` already sends it. What was missing was the
display: no card face, hover preview or reveal ever showed it, so a player
looking at the UI would reasonably conclude the flag was absent.

These tests pin the display, and the data the display depends on.
"""

from __future__ import annotations

import re
from pathlib import Path

from struggler.engine.cards import load_cards

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text()
CSS = (ROOT / "ui" / "style.css").read_text()
SERVE = (ROOT / "scripts" / "serve_ui.py").read_text()
SRC = (ROOT / "src" / "struggler" / "engine" / "core.py").read_text()

PRINTED = "Remove from play if used as an event"


def body_of(name: str) -> str:
    match = re.search(rf"function {name}\(.*?\) \{{(.*?\n)\}}", APP, re.S)
    assert match, f"{name}() not found"
    return match.group(1)


# -- the data the display reads ---------------------------------------------


def test_the_flag_exists_on_every_card_and_is_not_vacuous():
    cards = load_cards()
    assert len(cards) == 110
    missing = [cid for cid, c in cards.items() if not hasattr(c, "remove_after_event")]
    assert not missing, f"cards with no remove_after_event field: {missing}"
    flagged = [cid for cid, c in cards.items() if c.remove_after_event]
    # A flag that is true for nothing or everything would make the display
    # meaningless; the real game has a large minority of them.
    assert 30 < len(flagged) < 80, f"{len(flagged)} flagged cards looks wrong"
    # Spot-check cards the printed text is unambiguous about.
    for cid in ("Containment", "Brezhnev_Doctrine", "Salt_Negotiations", "Wargames"):
        assert cards[cid].remove_after_event, cid
    for cid in ("NATO", "Marshall_Plan", "Tear_Down_This_Wall"):
        assert not cards[cid].remove_after_event, cid


def test_the_server_sends_the_flag_the_client_reads():
    """The display reads `m.remove_after_event`; the payload has to carry it."""
    assert '"remove_after_event": card.remove_after_event,' in SERVE
    assert "m.remove_after_event" in APP


def test_the_engine_removes_only_when_the_event_fired():
    """"if used as an event" — playing it for Ops discards it as normal."""
    engine = re.search(r"if fired and self\.cards\[cid\]\.remove_after_event:\s*(.*?)\n\s*else:", SRC, re.S)
    assert engine, "the fired-gated removal is gone from the engine"
    assert "self.removed_cards.append(cid)" in engine.group(1)
    # The else branch is the ordinary discard, which is what Ops play takes.
    tail = SRC[engine.end():engine.end() + 120]
    assert "self.discard_pile.append(cid)" in tail


# -- the display ------------------------------------------------------------


def test_hover_preview_shows_the_printed_footer():
    preview = body_of("showPreview")
    assert "if (m.remove_after_event)" in preview
    assert f'"{PRINTED}"' in preview or PRINTED in preview
    assert "previewEl.append(gone)" in preview
    assert ".removeplay" in CSS


def test_the_text_card_fallback_carries_it_too():
    """With no card art installed the fallback *is* the card, so it needs the
    footer or the information vanishes on a fresh clone."""
    fallback = body_of("fillCardText")
    assert "if (m.remove_after_event)" in fallback
    assert "remove from play if used as an event" in fallback
    assert ".cardremove" in CSS


def test_the_played_card_reveal_carries_it():
    """The reveal is the moment the card is actually spent, which is when the
    consequence matters."""
    reveal = body_of("showCardPlay")
    assert "if (m.remove_after_event)" in reveal
    assert PRINTED in reveal


def test_the_footer_explains_the_ops_difference():
    """The flag alone is ambiguous about Ops play; the tooltip resolves it."""
    preview = body_of("showPreview")
    assert "playing it for Ops discards it as normal" in preview
