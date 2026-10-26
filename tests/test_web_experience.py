"""Persistence, secrecy, and consequence checks for the browser experience."""
import json

import pytest

from test_web_ui import serve_ui
from struggler.engine import Engine, Side, DecisionKind
from struggler.web_support import action_preview, strategic_overview, GameLibrary


def test_library_restores_exact_position_and_rng_with_custom_setup(tmp_path):
    session = serve_ui.Session(11, 'human', 'greedy', True, library_dir=tmp_path)
    session.restart(include_ccw=False, setup_us_extra=2)
    session.advance()
    session.act(0)
    assert 0 < session.game_id < 2**53  # exact in browser JSON numbers
    saved_id = session.save_id
    expected = session.engine.serialize()
    public_history = session.state()['history']
    restored = serve_ui.Session(99, 'human', 'random', False, library_dir=tmp_path)
    assert restored.save_id == saved_id
    assert restored.engine.serialize() == expected
    assert restored.state()['history'] == public_history
    assert restored.bot_kind == 'greedy'
    assert restored.setup_us_extra == 2
    assert not restored.include_ccw
    # The snapshot includes the frozen placement reachability and RNG state,
    # so continuing both games with the same legal actions must match exactly.
    for _ in range(10):
        session._step(session.engine.legal_actions()[0])
        restored._step(restored.engine.legal_actions()[0])
    assert restored.engine.serialize() == session.engine.serialize()


def test_unpaired_headline_survives_save_without_becoming_public(tmp_path):
    session = serve_ui.Session(3, 'human', 'greedy', True, library_dir=tmp_path)
    while session.engine.pending_decision.kind is not DecisionKind.HEADLINE_PLAY:
        session._step(session.engine.legal_actions()[0])
    session._step(session.engine.legal_actions()[0])
    session.save_game()  # requests save at their end; `_step` itself does not
    assert len(session.history._pending_headline) == 1
    pending = session.history._pending_headline[0]
    restored = serve_ui.Session(1, 'human', 'greedy', True, library_dir=tmp_path)
    assert restored.engine.serialize() == session.engine.serialize()
    assert restored.history._pending_headline == session.history._pending_headline
    assert not any(e['kind'] == 'headline_play' for e in restored.state()['history'])
    assert pending.action.payload['card'] not in json.dumps(restored.library.entries())


def test_saved_undo_rewinds_the_durable_position(tmp_path):
    session = serve_ui.Session(3, 'human', 'greedy', True, library_dir=tmp_path)
    session.advance()
    before = session.engine.serialize()
    session.act(0)
    assert session.engine.serialize() != before
    session.undo()
    restored = serve_ui.Session(99, 'human', 'greedy', True, library_dir=tmp_path)
    assert restored.engine.serialize() == before


def test_library_keeps_multiple_games_results_and_hides_private_state(tmp_path):
    session = serve_ui.Session(4, 'human', 'greedy', True, library_dir=tmp_path)
    first = session.save_id
    session.forfeit()
    entries = session.library.entries()
    finished = next(e for e in entries if e['id'] == first)
    assert finished['finished'] and finished['winner'] == 'USSR'
    assert len(entries) == 2
    assert set(finished) == {'id', 'saved_at', 'side', 'bot', 'turn', 'action_round', 'vp', 'winner', 'reason', 'finished', 'moves'}
    assert 'hands' not in json.dumps(entries) and 'rng_state' not in json.dumps(entries)
    session.load_game(first)
    assert session.engine.is_terminal and session.engine.winner is Side.USSR


def test_corrupt_save_and_bad_paths_cannot_replace_live_game(tmp_path):
    session = serve_ui.Session(4, 'human', 'greedy', True, library_dir=tmp_path)
    before = session.engine.serialize()
    with pytest.raises(ValueError):
        session.load_game('../../secret')
    assert session.engine.serialize() == before
    (tmp_path / ('a'*32 + '.json')).write_text('{broken')
    assert len(session.library.entries()) == 1
    with pytest.raises(ValueError):
        session.load_game('a'*32)
    assert session.engine.serialize() == before


def test_save_failure_is_visible_and_does_not_stop_the_game(tmp_path):
    blocker = tmp_path / 'file'; blocker.write_text('not a directory')
    session = serve_ui.Session(4, 'human', 'greedy', True, library_dir=blocker)
    assert session.state()['save_error'].startswith('Automatic save failed')
    session.step_once()
    assert session.history.history and session.engine.board.influence['Poland']['USSR'] == 1


def test_region_overview_matches_engine_and_does_not_consume_shuttle():
    engine = Engine.new_game(seed=2)
    engine.game_effects['shuttle_diplomacy'] = True
    engine.game_effects['formosan'] = True
    engine.board.influence['Thailand']['US'] = 2
    before = engine.serialize()
    rows = strategic_overview(engine)
    assert engine.serialize() == before
    for row in rows:
        cid = 'Southeast_Asia_Scoring' if row['key'] == 'SOUTHEAST_ASIA' else row['region'].replace(' ', '_') + '_Scoring'
        clone = Engine.deserialize(before)
        expected = clone.preview_scoring(cid)
        assert row['net'] == expected['net'] and row['wins'] == expected['wins']
    asia = next(r for r in rows if r['key'] == 'ASIA')
    assert asia['counts']['US']['battlegrounds'] >= 1
    assert asia['adjusted']


def test_placement_preview_matches_real_action_cost_and_control():
    session = serve_ui.Session(3, 'human', 'greedy', True)
    engine = session.engine
    engine.board.influence['Canada'] = {'US': 3, 'USSR': 7}
    engine.begin_influence_operations(Side.US, 3)
    d = engine.pending_decision
    action = next(a for a in d.options if a.payload.get('country') == 'Canada')
    before = engine.serialize()
    preview = action_preview(engine, d, action)
    assert engine.serialize() == before
    assert preview['cost'] == 2
    assert preview['control'] is None
    engine.step(action)
    assert engine.board.influence['Canada'] == preview['influence']
    assert engine.pending_decision.context['ops_remaining'] == 1


def test_event_removal_preview_accounts_for_whole_stack():
    engine = Engine.new_game(seed=1)
    engine.board.influence['France'] = {'US': 6, 'USSR': 0}
    engine.push_event_influence('Test', 'remove', Side.USSR, Side.US, 1, ['France'], whole=True)
    d = engine.pending_decision
    preview = action_preview(engine, d, d.options[0])
    engine.step(d.options[0])
    assert preview['influence'] == engine.board.influence['France']
    assert 'US removes 6 influence.' in preview['lines']


def test_plan_is_read_only_validates_whole_batch_and_commits_with_one_undo(tmp_path):
    session = serve_ui.Session(3, 'human', 'greedy', True, library_dir=tmp_path)
    engine = session.engine
    engine.begin_influence_operations(Side.US, 3)
    d = engine.pending_decision
    before = engine.serialize()
    clone, actions = session.placement_plan(['Canada', 'Canada'], session.game_id, d.id)
    assert engine.serialize() == before
    assert clone.board.influence['Canada']['US'] == engine.board.influence['Canada']['US'] + 2
    with pytest.raises(ValueError):
        session.commit_plan(['Canada', 'Poland'], session.game_id, d.id)
    assert session.engine.serialize() == before
    with pytest.raises(RuntimeError):
        session.placement_plan(['Canada'], session.game_id, d.id + 1)
    session.commit_plan(['Canada', 'Canada'], session.game_id, d.id)
    assert session.engine.serialize() == clone.serialize()
    session.undo()
    assert session.engine.serialize() == before
    assert GameLibrary(tmp_path).read(session.save_id)['engine']['vp'] == before['vp']


def test_explicit_game_flags_start_a_fresh_game_instead_of_restoring():
    for argv, restore in (
        ([], True),
        (["--port", "9000"], True),
        (["--us", "mcts", "--ussr", "mcts"], False),
        (["--seed", "7"], False),
        (["--no-events"], False),
        (["--fresh"], False),
    ):
        args, got = serve_ui.parse_args(argv)
        assert got is restore, argv
    args, _ = serve_ui.parse_args(["--seed", "7"])
    assert (args.us, args.ussr, args.seed, args.events) == ("human", "mcts", 7, True)


def test_fresh_session_keeps_the_library_and_uses_its_own_seats(tmp_path):
    first = serve_ui.Session(5, 'human', 'greedy', True, library_dir=tmp_path)
    old_id = first.save_id
    fresh = serve_ui.Session(77, 'random', 'human', False, library_dir=tmp_path, restore=False)
    assert not fresh.restored
    assert (fresh.us, fresh.ussr, fresh.seed, fresh.events) == ('random', 'human', 77, False)
    assert fresh.save_id != old_id
    assert {e['id'] for e in fresh.library.entries()} >= {old_id, fresh.save_id}


def test_a_finished_last_game_is_not_restored(tmp_path):
    session = serve_ui.Session(4, 'human', 'greedy', True, library_dir=tmp_path)
    session.engine._win(Side.USSR, "forfeit")
    session.save_game()
    finished = session.save_id
    restored = serve_ui.Session(9, 'human', 'random', True, library_dir=tmp_path)
    assert not restored.restored
    assert restored.save_id != finished
    assert restored.seed == 9 and restored.bot_kind == 'random'

def test_saves_happen_per_request_not_per_step(tmp_path, monkeypatch):
    session = serve_ui.Session(3, 'human', 'greedy', True, library_dir=tmp_path)
    calls = []
    monkeypatch.setattr(session, 'save_game', lambda: calls.append(len(session.history.history)))
    session.advance()  # the bot's setup and CHANCE steps before the human acts
    assert len(calls) == 1
    steps_before = len(session.history.history)
    session.act(0)
    assert calls[-2] == steps_before + 1  # the human's move is saved before bots reply
    assert len(calls) == 3


def test_undo_point_is_saved_as_a_history_prefix_and_restores(tmp_path):
    session = serve_ui.Session(3, 'human', 'greedy', True, library_dir=tmp_path)
    session.advance()
    before = session.engine.serialize()
    session.act(0)
    raw = json.loads((tmp_path / f'{session.save_id}.json').read_text())
    assert 'history_len' in raw['undo'] and 'history' not in raw['undo']
    restored = serve_ui.Session(99, 'human', 'greedy', True, library_dir=tmp_path)
    restored.undo()
    assert restored.engine.serialize() == before
    assert len(restored.history.history) == raw['undo']['history_len']


def test_a_save_written_with_a_full_undo_history_still_loads(tmp_path):
    session = serve_ui.Session(3, 'human', 'greedy', True, library_dir=tmp_path)
    session.advance()
    before = session.engine.serialize()
    session.act(0)
    path = tmp_path / f'{session.save_id}.json'
    raw = json.loads(path.read_text())
    n = raw['undo'].pop('history_len')
    raw['undo']['history'] = raw['history'][:n]  # the format #38 wrote
    path.write_text(json.dumps(raw))
    restored = serve_ui.Session(99, 'human', 'greedy', True, library_dir=tmp_path)
    restored.undo()
    assert restored.engine.serialize() == before


def test_history_encoder_cache_matches_a_full_encode_across_undo():
    from struggler.web_support import HistoryEncoder, encode_history
    session = serve_ui.Session(3, 'human', 'greedy', True)
    encoder = HistoryEncoder()
    session.advance()
    assert encoder.encode(session.history.history) == encode_history(session.history.history)
    session.act(0)
    assert encoder.encode(session.history.history) == encode_history(session.history.history)
    session.undo()  # history rewinds; the cache must drop the undone tail
    assert encoder.encode(session.history.history) == encode_history(session.history.history)
    assert json.loads(json.dumps(encoder.encode(session.history.history))) == encode_history(session.history.history)
