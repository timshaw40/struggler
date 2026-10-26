"""Local game storage and public, read-only UI explanations.

Snapshots contain private engine state on disk; only library summaries and
player-visible consequences cross the HTTP boundary.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from struggler.engine import Engine, Side, DecisionKind
from struggler.engine.core import SCORING_CARD_REGION
from struggler.engine.player import Event
from struggler.engine.replay import encode_action, decode_action
from struggler.engine.types import Decision


def plain(value):
    """JSON primitives only: enums by value, mappings as dicts, sequences as
    lists. The same conversion `serve_ui.Session._json` applies."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def encode_event(e: Event) -> dict:
    """One history event as JSON primitives, ready for `json.dump`."""
    return plain(dict(
        actor=e.actor.value,
        decision=dict(id=e.decision.id, actor=e.decision.actor.value,
                      kind=e.decision.kind.value,
                      options=[encode_action(a) for a in e.decision.options],
                      context=dict(e.decision.context)),
        action=encode_action(e.action), defcon=e.defcon, vp=e.vp,
        turn=e.turn, action_round=e.action_round,
        space_race=dict(e.space_race), military_ops=dict(e.military_ops),
        country=e.country, country_influence=dict(e.country_influence),
        country_control=e.country_control,
    ))


def encode_history(events: list[Event]) -> list[dict]:
    return [encode_event(e) for e in events]


class HistoryEncoder:
    """`encode_history` with a cache: events are immutable and history only
    grows between undos, so each save encodes just the events added since the
    last one. Re-encodes from the first event that differs (undo, load)."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._rows: list[dict] = []

    def encode(self, events: list[Event]) -> list[dict]:
        keep = 0
        for old, new in zip(self._events, events):
            if old is not new:
                break
            keep += 1
        self._events = self._events[:keep] + list(events[keep:])
        self._rows = self._rows[:keep] + [encode_event(e) for e in events[keep:]]
        return self._rows


def decode_history(rows: list[dict]) -> list[Event]:
    events = []
    for row in rows:
        d = row['decision']
        events.append(Event(**{
            **row, 'actor': Side(row['actor']),
            'decision': Decision(d['id'], Side(d['actor']), DecisionKind(d['kind']),
                                 tuple(decode_action(a) for a in d['options']), d['context']),
            'action': decode_action(row['action']),
        }))
    return events


class GameLibrary:
    """Atomic snapshots with stable IDs; never accepts a path from a client."""
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def path(self, game: str) -> Path:
        if not isinstance(game, str) or not re.fullmatch(r'[a-f0-9]{32}', game):
            raise ValueError('Invalid saved game')
        return self.directory / f'{game}.json'

    def read(self, game: str) -> dict:
        data = json.loads(self.path(game).read_text())
        if data.get('version') != 1:
            raise ValueError('This save uses an unsupported format')
        return data

    def write(self, game: str | None, snapshot: dict) -> tuple[str, str]:
        game = game or uuid4().hex
        path = self.path(game)
        stamp = datetime.now(timezone.utc).isoformat()
        data = {**snapshot, 'version': 1, 'id': game, 'saved_at': stamp}
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.directory, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                # json.dumps uses the C encoder; json.dump streams through the
                # pure-Python one and was ~10x slower on a full game.
                f.write(json.dumps(data, separators=(',', ':')))
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return game, stamp

    def entries(self) -> list[dict]:
        entries = []
        for path in self.directory.glob('*.json'):
            try:
                data = self.read(path.stem)
                engine = data['engine']
                entries.append(dict(
                    id=data['id'], saved_at=data['saved_at'],
                    side=data['human_side'], bot=data['bot_kind'],
                    turn=engine['turn'], action_round=engine['action_round'],
                    vp=engine['vp'], winner=engine.get('winner'),
                    reason=engine.get('game_over_reason'),
                    finished=bool(engine.get('winner')) or engine.get('phase') == 'complete',
                    moves=len(data['history']),
                ))
            except (OSError, ValueError, KeyError, TypeError):
                # One damaged file must not hide all the other saved games.
                continue
        return sorted(entries, key=lambda e: e['saved_at'], reverse=True)


def strategic_overview(engine: Engine) -> list[dict]:
    """Actual scoring previews and effective counts, including public effects.

    A clone owns Shuttle Diplomacy's transient scoring override, so inspecting
    this projection cannot consume or rewrite any live effect.
    """
    rows = []
    for card, region in SCORING_CARD_REGION.items():
        clone = copy.copy(engine)
        clone.game_effects = dict(engine.game_effects)
        preview = clone.preview_scoring(card)
        extra, ignored = clone._scoring_overrides(region)
        countries = clone.board.countries_in(region)
        bg = {c for c in countries if clone.board.countries[c].battleground or c in extra}
        counts = {}
        for side in (Side.US, Side.USSR):
            controlled = [c for c in countries if c not in ignored and clone.board.control(c) is side]
            count_bg = sum(c in bg for c in controlled)
            opponent = [c for c in countries if c not in ignored and clone.board.control(c) is side.opponent]
            missing = []
            if len(controlled) <= len(opponent):
                missing.append('control more countries than the opponent')
            if count_bg <= sum(c in bg for c in opponent):
                missing.append('control more battlegrounds than the opponent')
            if len(controlled) == count_bg:
                missing.append('control at least one non-battleground')
            counts[side.value] = dict(countries=len(controlled), battlegrounds=count_bg,
                                     non_battlegrounds=len(controlled)-count_bg,
                                     domination_needs=missing)
        rows.append({**preview, 'key': region.name, 'counts': counts, 'total_battlegrounds': len(bg),
                     'adjusted': bool(extra or ignored)})
    rows.append({**engine.preview_scoring('Southeast_Asia_Scoring'),
                 'key': 'SOUTHEAST_ASIA', 'counts': None, 'adjusted': False})
    return rows


def action_preview(engine: Engine, decision: Decision, action) -> dict:
    """Describe immediate public consequences without searching hidden cards."""
    context = decision.context
    payload = action.payload
    country = payload.get('country')
    if country and decision.kind in (DecisionKind.PLACE_INFLUENCE, DecisionKind.EVENT_INFLUENCE):
        board = copy.deepcopy(engine.board)
        before = board.control(country)
        event = decision.kind is DecisionKind.EVENT_INFLUENCE
        side = Side(context['inf_side']) if event else decision.actor
        cost = 0 if event or context.get('setup') else board.influence_cost(side, country)
        old = dict(board.influence[country])
        amount = context.get('amount', 1) if event else 1
        if event and context['op'] == 'remove':
            board.influence[country][side.value] = (0 if context['whole'] else max(0, old[side.value]-amount))
        else:
            board.influence[country][side.value] += amount
        after = board.control(country)
        delta = board.influence[country][side.value] - old[side.value]
        lines = [f"{side.value} {'adds' if delta >= 0 else 'removes'} {abs(delta)} influence."]
        lines.append(f'Costs {cost} ' + ('Op.' if cost == 1 else 'Ops.') if cost else 'No Operations cost.')
        if before != after:
            lines.append(f'{after.value} gains control.' if after else f'{before.value} loses control.')
        elif after is None:
            own = board.influence[country][side.value]
            opp = board.influence[country][side.opponent.value]
            lines.append(f'{max(0, opp + board.countries[country].stability-own)} more influence needed for {side.value} control.')
        if cost and 'ops_remaining' in context:
            lines.append(f"{context['ops_remaining']-cost} Ops remaining after this placement.")
        if board.countries[country].battleground:
            lines.append('Battleground: counts toward domination and control, plus 1 VP when controlled at scoring.')
        return dict(lines=lines, country=country, influence=board.influence[country],
                    control=after.value if after else None, cost=cost)
    card_id = payload.get('card') or context.get('card')
    card = engine.cards.get(card_id)
    mode = payload.get('mode')
    order = payload.get('order')
    lines = []
    if card:
        if card.scoring:
            preview = engine.preview_scoring(card_id)
            net = preview['net']
            lines.append(f"{preview['wins']} wins immediately." if preview['wins'] else
                         f"Scoring now: {'US' if net > 0 else 'USSR'} gains {abs(net)} VP." if net else 'Scoring now: no net VP change.')
        else:
            if mode == 'space_race':
                lines.append('Attempt the Space Race; the event does not fire. The card is discarded.')
            elif order:
                lines.append('Opponent event resolves before your Operations.' if order == 'event_first' else
                             'Spend your Operations first; the opponent event resolves afterward.')
                if card.event_summary:
                    lines.append(card.event_summary)
                if card.remove_after_event:
                    lines.append('Removed from play if the event fires.')
            elif mode == 'un_intervention':
                lines.append(f'{engine._effective_ops(decision.actor, card)} Operations. Discard UN Intervention as well to cancel the opponent event.')
                lines.append('Both cards are discarded; the cancelled event does not remove this card from play.')
            elif mode == 'event':
                lines.append(card.event_summary or 'Resolve this card’s event.')
                lines.append('Removed from play if the event fires.' if card.remove_after_event else 'Discarded after resolution.')
            elif mode == 'ops':
                lines.append(f'{engine._effective_ops(decision.actor, card)} Operations after active modifiers.')
                if engine.events_enabled and engine._is_opponent_event(decision.actor, card):
                    lines.append('Opponent event also triggers if eligible; choose the order next.')
                    if card.event_summary:
                        lines.append(card.event_summary)
                    if card.remove_after_event:
                        lines.append('Removed from play if the event fires.')
                else:
                    lines.append('The China Card passes to the opponent, unavailable until next turn.' if card_id == 'The_China_Card' else 'The event does not fire; the card is discarded.')
            elif decision.kind in (DecisionKind.ACTION_ROUND_PLAY, DecisionKind.HEADLINE_PLAY):
                lines.append(f'{engine._effective_ops(decision.actor, card)} Operations after active modifiers.')
                if decision.kind is DecisionKind.HEADLINE_PLAY:
                    lines.append(card.event_summary or 'Resolve the event in headline order.')
                elif engine.events_enabled and engine._is_opponent_event(decision.actor, card):
                    lines.append('Using for Ops also triggers the opponent’s event if eligible.')
                if card.remove_after_event:
                    lines.append('Removed from play if used as an event (including an opponent event triggered by Ops).')
    return {'lines': lines}
