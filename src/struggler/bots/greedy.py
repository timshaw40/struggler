"""GreedyPlayer: a hand-crafted heuristic bot with no lookahead or search.

Per docs/BOTS.md: observe the current state, score every legal action of
the current decision, take the top-scoring one. Nothing more -- no
simulating future turns, no search tree, no opponent modeling.

Deliberately built as *weighted features* rather than an if/elif priority
cascade: almost every heuristic funnels through `board_value()`, a single
scalar "how good is this board for `side`" evaluation, and per-action scores
are (mostly) the marginal change to that value from taking the action, or
its dice-free expectation for chance-driven actions (coups, realignment).
This is the intended bridge to a future RL agent (see docs/BOTS.md's
roadmap): a linear model over the same features, with learned instead of
hand-set weights, is a drop-in replacement for `GreedyWeights`.

Coverage: full heuristics for every core board decision kind -- where to
place Influence, which country to Coup or Realign against, which Ops type
to spend on, which card to headline or play, and Ops vs Event vs Space Race
mode. The event-specific decision kinds (WAR_TARGET, EVENT_CHOICE,
EVENT_INFLUENCE, EVENT_OPS_ORDER, QUAGMIRE_DISCARD, HELD_CARD_DISCARD,
EVENT_RESUME, RANDOM_DISCARD's non-CHANCE siblings, ...) fall back to the
first legal option. This is a documented gap, not a bug -- the same
card-by-card growth pattern the event layer itself used; extend
`_SCORERS` as each one gets a heuristic worth writing. `EVENT_CHOICE`
itself now has two card-specific heuristics (Aldrich Ames Remix: discard
the opponent's highest-Ops card; How I Learned to Stop Worrying: never set
DEFCON to 1) inside `_score_event_choice`, dispatched by
`decision.context["event"]`; every other EVENT_CHOICE-driven card still
falls back to the first option via that same function's default 0.0.

Priority ordering falls directly out of the weight magnitudes, not out of
branch order:
  1. Never choose a Coup (or an Ops type / Coup target that could become
     one) that would drop DEFCON to 1 -- an instant loss for the acting
     side (`defcon_self_kill_penalty`, orders of magnitude above every
     other weight). The same penalty covers committing a card whose text
     resolves to DEFCON 1 on this turn, *including* an opponent's card
     played for Ops, whose event fires too (`defcon_suicide_penalty`; see
     `_defcon_suicide_risk`).
  2. A safe Coup with a good expected margin outscores placing Influence
     (`coup_base` plus the expected board-value swing).
    3. Among Influence targets, Battlegrounds and progress toward control
       dominate (`battleground_control` in `board_value`, including partial
       stacks — 1/3 in Poland beats 1/4 in Austria).
  4. A card not worth spending on Ops gets sent to the Space Race instead
     (low `ops_mode_per_point` score vs `space_race_base`).
  5. A card whose *event* is worth more than its Ops gets played for the
     event, and headlined for free if it is worth headlining
     (`_OWN_EVENT_VALUE`). This is the narrow set the playbook calls a
     default ("Always event", "Strong event", "Free event") — an unlisted
     card keeps the Ops-first default, because "Ops are paramount" is the
     article's own summary of the general case.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping, Sequence

from struggler.engine import (
    Action,
    CardSide,
    Decision,
    DecisionKind,
    Observation,
    Region,
    ScoringTier,
    Side,
    Subregion,
)
from struggler.engine.board import Board, CountryInfo
from struggler.engine.cards import action_rounds, load_cards
from struggler.engine.core import SCORING_CARD_REGION
from struggler.engine.player import Event
from struggler.engine.rules import RULES

_CARDS = load_cards()

"""Static country geography, loaded once.

The heuristic is handed an `Observation`, not a `Board`, and a few rules are
about where countries *are* rather than what influence sits on them (8.1.5's
DEFCON geography, the article's battleground regions). This is the same
`data/countries.json` the engine's Board reads, so the two cannot disagree.
"""
_COUNTRY_INFO: dict[str, CountryInfo] = Board().countries

_TIER_VALUE = {
    ScoringTier.NONE: 0.0,
    ScoringTier.PRESENCE: 1.0,
    ScoringTier.DOMINATION: 2.0,
    ScoringTier.CONTROL: 3.0,
}

# Sentinel for "no candidate target exists" in the OPS_TYPE proxy searches
# below -- large enough to never win against a real (bounded) score, without
# using -inf, which would make `weights.x + _NO_OPTION` still -inf and hide
# arithmetic mistakes.
_NO_OPTION = -1_000_000_000.0


@dataclass(frozen=True)
class GreedyWeights:
    """Every knob GreedyPlayer's heuristics use. Grouped by the feature they
    price, not by decision kind, since several decision kinds share them.
    """

    # -- board_value(): the static "how good is this position" evaluation --
    region_tier: float = 6.0
    country_control: float = 2.0
    battleground_control: float = 5.0

    # -- DEFCON safety (priority #1: never die to DEFCON 1) --
    defcon_self_kill_penalty: float = 1_000_000.0
    defcon_caution: float = 4.0  # scaled by (5 - defcon): risk-aversion as DEFCON drops, short of the fatal case
    # Playing a DEFCON sucker (see _defcon_suicide_risk) is the same class of
    # mistake as couping into DEFCON 1, so it gets the same penalty.
    defcon_suicide_penalty: float = 1_000_000.0
    # How I Learned to Stop Worrying picks a DEFCON *level* to set. Small: the
    # levels differ only in how much Military Operations pressure they put on
    # the opponent versus how much Coup freedom they leave open, and the
    # decision has no competing option to be outbid against -- the only thing
    # that must dominate is "never 1", which is the self-kill penalty above.
    defcon_setting_weight: float = 0.5
    # Taking DEFCON from 3 to 2 is free while the hand holds nothing that only
    # becomes unplayable at 2, and expensive when it does (see
    # `_unavoidable_strands`): a card whose text resolves to DEFCON 1 cannot be
    # committed at all once the marker is there, so holding two of them with the
    # Space Race attempt spent is a lost position entered several turns earlier.
    # Per stranded card, above a Battleground control (5.0) and below the
    # sentinels above, so it can outbid a good Coup but never a suicide.
    defcon_strand_penalty: float = 15.0
    # The other half of the same rule: dispose of such a card while the marker
    # is still high. Sized above `space_race_card_bonus` (8.0) -- a card that is
    # unplayable at DEFCON 2 is a stronger Space Race candidate than one that is
    # merely an awful event -- and below `ops_mode_per_point` x a 4-Ops card
    # (12.0), because spacing still costs the Ops.
    strand_disposal_bonus: float = 10.0

    # -- per-ops-type base preference (before the marginal/expected board_value swing) --
    coup_base: float = 5.0
    realignment_base: float = 1.0
    influence_base: float = 1.0
    doubled_cost_penalty: float = 1.0  # discourages placing into opponent-controlled ("doubled") countries

    # -- which card, and how to spend it --
    space_race_base: float = 4.0
    space_race_vp_weight: float = 3.0
    space_race_ops_penalty: float = 1.5  # a high-Ops card is worth more spent on Ops than "wasted" on the Space Race
    # "the real job of the Space Race is to discard truly awful opponent events
    # that you cannot mitigate in any meaningful way" — the per-card judgement
    # lives in _USSR_SPACE_RACE / _US_SPACE_RACE. Sized above space_race_base
    # (4.0) so a listed card outranks an unlisted one at equal Ops and VP, but
    # below ops_mode_per_point × a 4-Ops card (12.0), because the article also
    # warns against over-spacing: "Ops are paramount."
    space_race_card_bonus: float = 8.0
    # "when discarding your opponent's vital events, you want to discard them
    # on Turns 3 and 7, rather than on Turns 2 or 6" — see _is_reshuffle_turn.
    # A bonus for *waiting* one more turn, sized below the space-race card
    # bonus so it bends the timing of a disposal without reversing the decision
    # to dispose.
    reshuffle_timing_bonus: float = 4.0
    # "The first kind of realignment, and the best kind, is the realignment that
    # eliminates your opponent's access to the region." Bigger than a single
    # battleground control swing (5.0), because the article calls it the best
    # kind of realignment, and it denies the whole region rather than one
    # country.
    realignment_access_bonus: float = 8.0
    # "In general, realignments only occur at DEFCON 2." Above that they are
    # competing with the coup the article prefers.
    realignment_at_defcon_2_bonus: float = 3.0
    realignment_above_defcon_2_penalty: float = 3.0
    ops_mode_per_point: float = 3.0
    event_mode_penalty: float = 30.0  # events off / unimplemented event: playing "event" is a no-op discard
    scoring_card_weight: float = 2.0  # per net VP the region would score, signed favorably/unfavorably
    hold_high_ops_weight: float = 0.5  # prefer headlining a low-Ops card, keeping high-Ops ones for Operations
    opponent_headline_penalty: float = 50.0  # never headline an opponent-side event
    # Playing an opponent-side card for Ops hands the opponent its Event (5.2);
    # prefer own/neutral cards at equal Ops.
    opponent_event_ops_penalty: float = 6.0
    action_round_ops_weight: float = 1.0
    # -- scoreboard, see `scoreboard_value` -----------------------------------
    # `board_value` prices the board only, so the two things that end games
    # (the VP track and the Military Operations requirement) were invisible to
    # every scorer that used it. A VP is deliberately worth less than a
    # Battleground control (5.0): the score is the point of the game, but not
    # at the price of a country. `milops_weight` scales the requirement term
    # relative to VP -- at 1.0 one level of shortfall is worth one VP, which is
    # the printed penalty.
    vp_weight: float = 2.0
    milops_weight: float = 1.0
    # Turn-1 USSR headline bonus for the five canonical openings.
    t1_headline_bonus: float = 40.0
    t1_iran_coup_bonus: float = 20.0

    # -- turn 1 per-country plan, from Twilight Strategy's Turn 1 article --
    # Sized to sit below the headline bonus (40) and above the ordinary
    # board-value swing a single Influence point buys (battleground control is
    # 5.0), so these order *which country* without overriding "never lose to
    # DEFCON" or "never headline the opponent's card".
    t1_plan_bonus: float = 12.0
    # The US pass on answering the Iran coup (see the module comment): a US
    # coup at the same Iran the USSR just took is a DEFCON-3 gamble the article
    # rejects, so it is priced under the influence alternatives.
    t1_us_retaliatory_coup_penalty: float = 25.0


# Standard openings (Twilight Strategy). Targets are influence AFTER setup,
# including printed at-start (E. Germany already has 3).
_SETUP_TARGET = {
    Side.USSR: {"East_Germany": 4, "Poland": 4, "Yugoslavia": 1},
    Side.US: {"West_Germany": 4, "Italy": 3},
}
_USSR_T1_HEADLINES = frozenset({
    "Red_Scare_Purge",
    "Suez_Crisis",
    "Arab_Israeli_War",
    "Socialist_Governments",
    "Vietnam_Revolts",
})

# -- turn 1, taken from Twilight Strategy's "General Strategy: Turn 1" --------
#
# The article is ~600 words of prose. What follows is that prose as the ordered
# priorities this heuristic prices; each group quotes the sentence it encodes,
# so a later reader can check a rule against its source instead of trusting the
# number.
#
# USSR — "On AR1, you realistically only have two options: coup Iran, or coup /
# play for Italy ... modern Twilight Struggle thinking is that access to
# Pakistan and India is simply too important":
#   * the five headline cards (above);
#   * AR1 is the Iran coup, not the Italy play;
#   * then Greece/Turkey in Europe, Egypt+Libya and Jordan/Lebanon in the
#     Middle East, and eastward expansion out of western Asia.
#
# US — "your goal should be to survive rather than triumph ... your main
# objective is simply not to fall behind too much in board position and VPs",
# with the article's own list, in its order:
#   1. "Protect Israel via Lebanon and/or Jordan."
#   2. "Make your way through Egypt into Libya before Nasser wipes you out."
#   3. "Gun for Thailand via Malaysia."
#   4. "Shore up South Korea while guarding against the Korean War."
#   5. "When you have a chance, take Greece and Turkey before the USSR does."
# plus "if the USSR opening coup of Iran is too good, then I wouldn't bother
# dropping DEFCON to 3 by couping Iran back" — the US never answers the Iran
# coup in kind.

# -- turn 1: USSR -----------------------------------------------------------
# ME/Asia countries the article names as the USSR's expansion, and the
# specifically-invited targets (Nasser's Egypt/Libya pair, Jordan/Lebanon to
# squeeze Israel, Greece/Turkey in Europe).
_USSR_T1_MIDDLE_EAST = ("Egypt", "Libya", "Jordan", "Lebanon", "Iran", "Syria", "Iraq")
_USSR_T1_ASIA = (
    "Afghanistan", "Pakistan", "India", "Thailand", "South_Korea", "Malaysia",
    "Indonesia", "Burma", "Laos_Cambodia", "Vietnam", "Taiwan", "Japan",
)
_USSR_T1_EUROPE = ("Greece", "Turkey")
# "if the US is still in Israel, taking Jordan and/or Lebanon puts some real
# pressure on the US position."
_ISRAEL_PRESSURE = ("Jordan", "Lebanon")

# -- turn 1: US -------------------------------------------------------------
# The article's list, in its order. "Protect Israel via Lebanon and/or Jordan."
_US_T1_ISRAEL_SHIELD = ("Lebanon", "Jordan")
# "Make your way through Egypt into Libya before Nasser wipes you out."
_US_T1_MIDDLE_EAST = ("Egypt", "Libya", "Israel")
# "Gun for Thailand via Malaysia."
_US_T1_ASIA = ("Malaysia", "Thailand", "South_Korea", "Japan", "Taiwan", "Philippines")
# "Shore up South Korea while guarding against the Korean War."
_US_T1_SOUTH_KOREA = ("South_Korea", "Japan")
# "When you have a chance, take Greece and Turkey before the USSR does."
_US_T1_EUROPE = ("Greece", "Turkey")



# -- board evaluation ---------------------------------------------------------


def board_value(weights: GreedyWeights, board: Board, side: Side) -> float:
    """A static heuristic value of the current board for `side` (higher is
    better): regional Presence/Domination/Control tiers plus progress
    toward control in every country (full credit at control, partial
    before — so 1/3 in Poland outranks 1/4 in Austria). Battlegrounds
    use `battleground_control`, others `country_control`."""
    opponent = side.opponent
    value = 0.0
    for region in Region:
        value += _TIER_VALUE[board.region_tier(side, region)] * weights.region_tier
        value -= _TIER_VALUE[board.region_tier(opponent, region)] * weights.region_tier
    for cid, info in board.countries.items():
        own = board.influence[cid][side.value]
        opp = board.influence[cid][opponent.value]
        per = weights.battleground_control if info.battleground else weights.country_control
        need = opp + info.stability
        opp_need = own + info.stability
        if need:
            value += per * min(1.0, own / need)
        if opp_need:
            value -= per * min(1.0, opp / opp_need)
    return value


def _marginal_gain(weights: GreedyWeights, board: Board, side: Side, country: str, delta: int) -> float:
    """`board_value` swing from adding `delta` Influence points for `side` in
    `country`, leaving `board` exactly as found."""
    before = board_value(weights, board, side)
    board.influence[country][side.value] += delta
    after = board_value(weights, board, side)
    board.influence[country][side.value] -= delta
    return after - before


def scoreboard_value(
    weights: GreedyWeights,
    side: Side,
    *,
    vp: int,
    defcon: int,
    military_ops: Mapping[str, int],
    turn: int,
    action_round: int,
) -> float:
    """The score, in the same units as `board_value`.

    `board_value` prices the board and nothing else, which left the bot
    indifferent to the two things that actually end a game: the VP track and
    the Military Operations requirement. `bots/value.py`'s learned value takes
    both (plus the turn) as features, so this is the hand-written version of
    the same three terms -- deliberately simple, and sized so that a VP is
    worth less than a Battleground control (`vp_weight` 2.0 against 5.0): the
    score is the point of the game, but a 2-VP swing is not worth a country.

    The Military Operations term is the one with teeth. It is scored as a
    *shortfall* (0 while the track is at or above the DEFCON level, `defcon`
    minus the track above it) times how near the end of the turn we are, since
    the requirement is only assessed at the end of the turn: a shortfall on the
    last action round is real VP to the opponent, the same shortfall on the
    first is cheap to fix. "Coup when you are behind on Military Ops" then
    falls out of the score instead of needing a rule of its own -- and the
    fixed 1 VP per level means a Coup that closes a shortfall is worth about
    what a VP is worth, which is the trade the card text describes.

    `vp` is US-positive (`Engine._change_vp_by`), so it is flipped for the
    USSR.
    """
    value = weights.vp_weight * (vp if side is Side.US else -vp)
    return value + _milops_term(
        weights,
        side,
        defcon=defcon,
        military_ops=military_ops,
        turn=turn,
        action_round=action_round,
    )


def _milops_term(
    weights: GreedyWeights,
    side: Side,
    *,
    defcon: int,
    military_ops: Mapping[str, int],
    turn: int,
    action_round: int,
) -> float:
    """The Military Operations half of `scoreboard_value`: the *difference* in
    shortfall between the two sides, times how near the end of the turn we are.
    """
    rounds = max(1, action_rounds(turn))
    imminence = min(1.0, action_round / rounds)
    own_shortfall = max(0, defcon - military_ops.get(side.value, 0))
    their_shortfall = max(0, defcon - military_ops.get(side.opponent.value, 0))
    return weights.vp_weight * weights.milops_weight * imminence * (
        their_shortfall - own_shortfall
    )


def milops_gain(weights: GreedyWeights, observation: Observation, side: Side, ops: int) -> float:
    """What adding `ops` to `side`'s Military Operations track is worth.

    A Coup is the only Ops type that pays the requirement (2.3.5: Coups and
    war Events count toward it, Realignments do not), so this is the term that
    makes "Coup when you are behind on Military Ops" fall out of the score
    rather than needing a rule of its own. It is a *difference* of
    `_milops_term`, so the two cannot drift apart.
    """
    kwargs = {
        "defcon": observation.defcon,
        "turn": observation.turn,
        "action_round": observation.action_round,
    }
    before = _milops_term(weights, side, military_ops=observation.military_ops, **kwargs)
    after_ops = dict(observation.military_ops)
    after_ops[side.value] = after_ops.get(side.value, 0) + ops
    after = _milops_term(weights, side, military_ops=after_ops, **kwargs)
    return after - before


def _sync_board(board: Board, observation: Observation) -> None:
    for cid, values in observation.influence.items():
        board.influence[cid]["US"] = values.get("US", 0)
        board.influence[cid]["USSR"] = values.get("USSR", 0)


# -- shared per-country rule replicas (public game data/rules, not hidden state) --


def _in_bonus_region(info: CountryInfo, bonus: str | list[str] | None) -> bool:
    # `bonus` is a list of live region bonuses (7.4 aggregates: a China Card
    # play in SE Asia under Vietnam Revolts earns both), but older callers may
    # still pass a single region name.
    if isinstance(bonus, str):
        bonus = [bonus]
    if not bonus:
        return False
    return any(
        (b == "asia" and info.region is Region.ASIA)
        or (b == "se_asia" and Subregion.SOUTHEAST_ASIA in info.subregions)
        for b in bonus
    )


def _coup_roll_modifier_estimate(observation: Observation, side: Side, info: CountryInfo) -> float:
    mod = 0.0
    te = observation.turn_effects
    lads = te.get("la_death_squads")
    if lads and info.region in (Region.CENTRAL_AMERICA, Region.SOUTH_AMERICA):
        mod += 1.0 if side.value == lads else -1.0
    if te.get("salt"):
        mod -= 1.0
    return mod


def _coup_risks_defcon(observation: Observation, side: Side, info: CountryInfo) -> bool:
    """Whether a Coup here could degrade DEFCON at all: only Battleground
    countries do, and even those not while Nuclear Subs exempts this side."""
    if not info.battleground:
        return False
    return not (side is Side.US and bool(observation.turn_effects.get("nuclear_subs")))


"""DEFCON safety, from Twilight Strategy's "General Strategy: DEFCON".

The governing rule: "you lose the game if DEFCON drops to 1 on your turn. It
doesn't matter who 'caused' it: if it happened on your watch, you're
responsible for humanity's destruction." The engine implements exactly that
(8.1.3: the *phasing* player loses), so what the bot has to avoid is playing a
card whose resolution can put DEFCON at 1 while it is the phasing side.

The article sorts the danger into four categories, and the third and fourth are
not worth modelling: "Neutral events that degrade DEFCON ... you would have to
be daft to play either of these for the event at DEFCON 2. Simply play them for
Operations and you won't lose the game." Playing them for Ops is what the
ordinary event_mode_penalty already prefers, so a special rule would only
duplicate it.

Category 1 — "Cards that unconditionally degrade DEFCON": playing the event is
fatal at DEFCON 2.

Category 2 — "Cards that allow your opponent to conduct Operations": the event
hands the opponent Ops, and they can coup a battleground with them. "...you can
never play your opponent's events from this list on your turn when DEFCON is 2
and your opponent can drop DEFCON by couping a battleground of yours (keeping in
mind DEFCON restrictions)." Note this is a *conditional* danger: the article
says Lone Gunman is unplayable "if the US has any influence in a battleground in
South America, Central America, or Africa" — so the test is whether the opponent
actually has a legal battleground coup somewhere, which is the same predicate
the engine uses for the 8.1.5 region restrictions.
"""

# "Cards that unconditionally degrade DEFCON" — event resolution drops the
# marker by one, so at DEFCON 2 the phasing player loses.
_DEFCON_UNCONDITIONAL = frozenset({
    "Duck_and_Cover",
    "We_Will_Bury_You",
    "Soviets_Shoot_Down_KAL_007",
})

# "Cards that allow your opponent to conduct Operations" — the event resolves
# into free Ops for the other side, who can spend them on a battleground coup.
_DEFCON_OPPS_FOR_OPPONENT = frozenset({
    "CIA_Created",          # 1 US Op
    "Lone_Gunman",          # 1 USSR Op
    "Grain_Sales_to_Soviets",  # 2 US Ops (or a random discard)
    "Tear_Down_This_Wall",  # 3 US Ops, and per the article it permits a coup
                            # in Europe despite 8.1.5
})

# The regions 8.1.5 leaves coupeable at DEFCON 2 (the article names the same
# three when it explains when Lone Gunman is safe).
_DEFCON_2_COUPABLE = frozenset({Region.AFRICA, Region.CENTRAL_AMERICA, Region.SOUTH_AMERICA})


def _opponent_can_coup_a_battleground(observation: Observation, side: Side) -> bool:
    """Whether `side`'s opponent has any battleground they could legally coup
    right now, dropping DEFCON as a result.

    This is the article's condition for category 2 ("...and your opponent can
    drop DEFCON by couping a battleground of yours (keeping in mind DEFCON
    restrictions)"). At DEFCON 2 the only coupeable regions are Africa and the
    Americas, so influence in a European or Asian battleground is not enough.
    """
    opponent = side.opponent
    min_defcon = RULES["coup_min_defcon"]
    at_defcon_2 = observation.defcon <= 2
    for cid, info in _COUNTRY_INFO.items():
        if not info.battleground:
            continue
        if observation.influence[cid].get(side.value, 0) <= 0:
            continue        # 6.2.1 needs enemy Influence to coup into
        if at_defcon_2 and info.region not in _DEFCON_2_COUPABLE:
            continue
        if observation.defcon < min_defcon.get(info.region.name, 1):
            continue
        return True
    return False


def _defcon_suicide_risk(observation: Observation, side: Side, cid: str, mode: str) -> bool:
    """Whether committing `cid` as `mode` can lose the game to DEFCON 1.

    The question is not which mode was picked, but *whose event text resolves*
    on this side's turn, and whether that text is fatal at this DEFCON. Two
    things can resolve a card's text:

    - its own event, which is `mode == "event"` -- and equally a Headline pick,
      since 5.1 resolves a headlined card as its event;
    - the *opponent's* event, which fires whenever their card is played for
      Ops (`Engine._push_play_mode`: "An opponent's event also fires when their
      card is played for Ops"). The engine never offers a voluntary "event"
      mode for an opponent's card (`_play_modes`), so an Ops play is the *only*
      way a bot can trigger one -- and pricing only the `event` mode left the
      Ops door open. Measured over 40 self-played greedy games before this
      rule: 30 of the 33 DEFCON-1 losses resolved through an opponent card
      played for Ops (Duck and Cover and KAL-007 almost always), each one
      choosing `event_first` at the `EVENT_OPS_ORDER` prompt that follows.

    The Space Race and UN Intervention never resolve the text, so they are
    always safe. NEUTRAL cards never fire as the opponent's event, so an Ops
    play of one is safe too.
    """
    if observation.defcon > 2:
        # Categories 1 and 2 both need the marker at 2: with DEFCON at 3 a
        # single drop lands on 2, which is bad play but not a loss.
        return False
    card = _CARDS.get(cid)
    if card is None:
        return False
    if mode == "event":
        resolves = True
    elif mode == "ops":
        # Mirrors `Engine._is_opponent_event`: only a card that belongs to the
        # opponent fires on an Ops play. One's own (or a neutral) card's text
        # is inert unless it is played as an event.
        resolves = card.side.value == side.opponent.value
    else:
        return False  # space_race / un_intervention never resolve the text
    if not resolves:
        return False
    if cid in _DEFCON_UNCONDITIONAL:
        return True
    if cid in _DEFCON_OPPS_FOR_OPPONENT:
        # The opponent still has to have somewhere to spend the Ops, and it
        # must be the *opponent* who gets them: CIA Created gives the US Ops,
        # so it only threatens a USSR phasing player, and vice versa.
        return _opponent_can_coup_a_battleground(observation, side)
    return False


def _space_race_attempts_left(observation: Observation, side: Side) -> int:
    """Space Race attempts this side still has this turn.

    One, or two with Captured Nazi Scientist (`Engine._space_attempts_allowed`,
    read off the public `game_effects` rather than assumed).
    """
    allowed = 2 if observation.game_effects.get("space_race_double_attempt_holder") == side.value else 1
    return max(0, allowed - observation.space_race_attempts.get(side.value, 0))


def _unavoidable_strands(observation: Observation, side: Side) -> int:
    """Cards in hand that would be *unplayable* at DEFCON 2 and cannot be
    disposed of first.

    At DEFCON 2 an opponent's card whose text resolves to DEFCON 1 cannot be
    committed at all: the Ops play fires their event, the `event` mode is never
    offered for their card, and UN Intervention has to be held for it. The
    Space Race is the only door left, one attempt a turn (two with Captured
    Nazi Scientist, which is why the allowance is read rather than assumed).

    So a card like that in hand is a countdown, and the DEFCON marker is the
    clock: it has to be spaced, or played while the marker is still high. This
    is the position the fixed bot still walks into -- 42 Ops plays over 40
    games, every one of them with the card as the only mode on offer, i.e. a
    decision made turns earlier and then paid for. `_strand_penalty` is what
    charges for it at the moment the marker is about to drop.
    """
    at_two = replace(observation, defcon=2)
    stranded = sum(
        1 for cid in observation.hand if _defcon_suicide_risk(at_two, side, cid, "ops")
    )
    return max(0, stranded - _space_race_attempts_left(observation, side))


def _strand_penalty(weights: GreedyWeights, observation: Observation, side: Side, info: CountryInfo) -> float:
    """The cost of taking DEFCON from 3 to 2 while the hand is stranded.

    Only a drop *to* 2 matters: at DEFCON 3 the Coup is otherwise legal and
    cheap, and above 3 there is a turn's worth of slack. `info` is the Coup
    target, since a non-Battleground Coup never moves the marker.
    """
    if observation.defcon != 3 or not _coup_risks_defcon(observation, side, info):
        return 0.0
    return weights.defcon_strand_penalty * _unavoidable_strands(observation, side)


def _strand_disposal_bonus(
    weights: GreedyWeights, observation: Observation, side: Side, cid: str
) -> float:
    """Extra Space Race preference for a card that would be unplayable at
    DEFCON 2: get rid of it while the marker is still high, because the Space
    Race attempt is the only door left once it is not.

    Only at DEFCON 3 or below. Above that the ordinary scoring decides, and
    playing such a card for Ops is fine -- the marker recovers at the end of
    every turn (see `Engine._end_of_turn`), so there is no countdown yet.
    """
    if observation.defcon > 3:
        return 0.0
    at_two = replace(observation, defcon=2)
    if not _defcon_suicide_risk(at_two, side, cid, "ops"):
        return 0.0
    return weights.strand_disposal_bonus


def _coup_is_suicide(observation: Observation, side: Side) -> bool:
    """Cuban Missile Crisis: any Coup by the flagged side this turn loses the
    game outright, so no target is ever safe (unlike the DEFCON risk above,
    which only some targets carry)."""
    return observation.turn_effects.get("cuban_missile_crisis") == side.value


def _expected_coup_gain(
    weights: GreedyWeights,
    board: Board,
    observation: Observation,
    side: Side,
    country: str,
    info: CountryInfo,
    ops: int,
) -> float:
    """Expected `board_value` swing of a Coup roll at `country`, using the
    average die roll (3.5) in place of an actual roll -- a Coup's outcome
    formula (margin = roll + ops - 2*stability + modifier) is linear in the
    roll, so this is the true expectation, not just a point estimate."""
    opponent = side.opponent
    modifier = _coup_roll_modifier_estimate(observation, side, info)
    expected_margin = 3.5 + ops - 2 * info.stability + modifier
    opp_inf = board.influence[country][opponent.value]
    opp_removed = int(round(max(0.0, min(expected_margin, opp_inf))))
    leftover = int(round(max(0.0, expected_margin - opp_removed)))

    before = board_value(weights, board, side)
    board.influence[country][opponent.value] -= opp_removed
    board.influence[country][side.value] += leftover
    after = board_value(weights, board, side)
    board.influence[country][opponent.value] += opp_removed
    board.influence[country][side.value] -= leftover
    return after - before


def _realignment_bonus(board: Board, side: Side, country: str) -> float:
    """Mirrors engine.core.Engine._realignment_bonus -- kept in sync by
    hand since this is an independent duplicate, not shared code. The
    region-bonus extra attempt (China Card in Asia / Vietnam Revolts in SE
    Asia) is deliberately NOT modeled here: it would add "count remaining
    Ops-type-choice attempts as still in-region" bookkeeping to a bot that
    already has no lookahead and only proxy (not exact) legality elsewhere
    in this module -- disproportionate complexity for its value."""
    bonus = 1.0 if board.is_adjacent(side.value, country) else 0.0
    bonus += sum(1 for n in board.neighbors(country) if board.control(n) is side)
    if board.influence[country][side.value] > board.influence[country][side.opponent.value]:
        bonus += 1.0
    return bonus


def _realignment_modifier(observation: Observation, side: Side) -> float:
    return -1.0 if (side is Side.US and observation.turn_effects.get("iran_contra")) else 0.0


def _effective_ops_estimate(card, observation: Observation, side: Side) -> int:
    ops = card.ops
    te = observation.turn_effects
    if te.get("containment") and side is Side.US:
        ops += 1
    if te.get("brezhnev") and side is Side.USSR:
        ops += 1
    if te.get("red_scare") == side.value:
        ops -= 1
    return max(1, ops)


def _space_race_expected_vp(observation: Observation, side: Side) -> float:
    pos = observation.space_race.get(side.value, 0)
    if pos >= RULES["space_race_max_box"]:
        return 0.0
    next_box = pos + 1
    box = RULES["space_race_boxes"][str(next_box)]
    probability = box["roll_max"] / 6.0
    first = observation.space_race.get(side.opponent.value, 0) < next_box
    vp = box["vp_first"] if first else box["vp_second"]
    return probability * vp


def _se_asia_scoring_net(board: Board) -> float:
    """Southeast Asia scoring, net US-positive: +2 VP for control of Thailand,
    +1 VP per other controlled SE Asia country (mirrors the engine's
    `_score_southeast_asia`, which isn't reachable from a bare Board)."""
    net = 0.0
    for cid, info in board.countries.items():
        if Subregion.SOUTHEAST_ASIA not in info.subregions:
            continue
        value = 2.0 if cid == "Thailand" else 1.0
        ctrl = board.control(cid)
        if ctrl is Side.US:
            net += value
        elif ctrl is Side.USSR:
            net -= value
    return net


def _scoring_card_favorability(board: Board, side: Side, cid: str) -> float:
    if cid == "Southeast_Asia_Scoring":
        net = _se_asia_scoring_net(board)
    else:
        region = SCORING_CARD_REGION.get(cid)
        if region is None:
            return 0.0
        net = board.score_region(region)  # positive favors US
    return net if side is Side.US else -net


# -- per-decision-kind scorers -------------------------------------------------


def _score_setup_place(board: Board, side: Side, country: str) -> float:
    """Fill the standard opening stacks before anywhere else."""
    want = _SETUP_TARGET[side].get(country)
    if want is None:
        return -10.0
    have = board.influence[country][side.value]
    if have >= want:
        return -1.0
    bg = 100.0 if board.countries[country].battleground else 10.0
    return bg + (want - have)


def _score_place_influence(weights: GreedyWeights, board: Board, observation: Observation, action: Action) -> float:
    side = observation.side
    if action.payload.get("stop"):  # end the "up to" Ops spend (6.1.3)
        return 0.0
    country = action.payload["country"]
    if observation.pending_decision.context.get("setup"):
        return _score_setup_place(board, side, country)
    cost = board.influence_cost(side, country)
    gain = _marginal_gain(weights, board, side, country, 1)
    return (
        weights.influence_base
        + gain
        + _t1_placement_bonus(weights, board, observation, side, country)
        - (cost - 1) * weights.doubled_cost_penalty
    )


def _t1_placement_bonus(
    weights: GreedyWeights, board: Board, observation: Observation, side: Side, country: str
) -> float:
    """Turn-1 placement priority from the article, or 0 outside turn 1.

    Only the tiers matter: the ordinary board_value swing decides between two
    countries in the same tier, and a country the article does not name gets
    nothing at all (so later-turn instincts still apply when the plan is
    exhausted).
    """
    if observation.turn != 1:
        return 0.0
    if side is Side.USSR:
        # Middle East first (Iran, and the Nasser/Jordan/Lebanon plays the
        # article calls out), then Asia eastward, then the European grab.
        tier = 3 if country in _USSR_T1_MIDDLE_EAST else 0
        if country in _USSR_T1_ASIA:
            tier = max(tier, 2)
        if country in _USSR_T1_EUROPE:
            tier = max(tier, 1)
        # "taking Jordan and/or Lebanon puts some real pressure on the US
        # position" — only while Israel is actually US-held, and then it is the
        # sharpest play on the board, so it outranks the general Middle East
        # tier rather than tying with it.
        if country in _ISRAEL_PRESSURE and board.influence["Israel"]["US"] > 0:
            tier = 4
        return weights.t1_plan_bonus * tier / 4.0
    # US: the article's five priorities, in its order, as descending tiers.
    # "Protect Israel via Lebanon and/or Jordan." (and Israel itself)
    tier = 5 if country in _US_T1_ISRAEL_SHIELD or country == "Israel" else 0
    # "Make your way through Egypt into Libya before Nasser wipes you out."
    if country in _US_T1_MIDDLE_EAST:
        tier = max(tier, 4)
    # "Gun for Thailand via Malaysia."
    if country in _US_T1_ASIA:
        tier = max(tier, 3)
    # "Shore up South Korea while guarding against the Korean War."
    if country in _US_T1_SOUTH_KOREA:
        tier = max(tier, 2)
    # "When you have a chance, take Greece and Turkey before the USSR does."
    if country in _US_T1_EUROPE:
        tier = max(tier, 1)
    return weights.t1_plan_bonus * tier / 5.0


def _score_coup_target(weights: GreedyWeights, board: Board, observation: Observation, action: Action) -> float:
    side = observation.side
    country = action.payload["country"]
    info = board.countries[country]
    decision = observation.pending_decision
    ops = decision.context["ops"]
    bonus = decision.context.get("bonus") or []
    # 7.4 aggregates: each live region bonus whose region contains the target
    # adds +1 Op to the coup (China Card in Asia + Vietnam Revolts in SE Asia
    # on a SE Asia target = +2).
    ops += sum(1 for b in bonus if _in_bonus_region(info, b))

    if _coup_is_suicide(observation, side):
        return -weights.defcon_self_kill_penalty
    if observation.defcon <= 2 and _coup_risks_defcon(observation, side, info):
        return -weights.defcon_self_kill_penalty

    gain = _expected_coup_gain(weights, board, observation, side, country, info, ops)
    caution = weights.defcon_caution * (5 - observation.defcon)
    score = weights.coup_base + gain - caution - _strand_penalty(weights, observation, side, info)
    if (
        observation.turn == 1
        and side is Side.USSR
        and country == "Iran"
        and observation.defcon >= 4
    ):
        score += weights.t1_iran_coup_bonus
    # "If the USSR opening coup of Iran is too good, then I wouldn't bother
    # dropping DEFCON to 3 by couping Iran back" — the US declines to answer
    # in kind. Only turn 1, and only on Iran: this is that specific judgement,
    # not a general reluctance to coup.
    if (
        observation.turn == 1
        and side is Side.US
        and country == "Iran"
    ):
        score -= weights.t1_us_retaliatory_coup_penalty
    return score


"""Realignments, from Twilight Strategy's "General Strategy: Realignments".

Two ideas beyond what the margin maths already captures:

"In general, realignments only occur at DEFCON 2." Above that they compete with
the battleground coup the article calls "a more powerful method to alter a
region in your favor", so they are discounted; at DEFCON 2 they are one of the
few ways left to attack a battleground, so they are preferred.

"The first kind of realignment, and the best kind, is the realignment that
eliminates your opponent's access to the region. ... not only has your opponent
lost the battleground, he has also lost any opportunity to put the influence
back in. This means you are free, on your next turn, to play in influence and
take over the country." That access is worth more than the markers removed,
and it is only real when the country is the opponent's last foothold there.
"""


def _realignment_severs_access(board: Board, opponent: Side, country: str) -> bool:
    """Whether the opponent's presence in `country` is their only way into its
    region — the article's "isolated influence with nothing next to it".

    Removing the last such foothold denies them the whole region until an event
    or a coup opens it again, which is the payoff the article describes.
    """
    if board.influence[country][opponent.value] <= 0:
        return False
    for cid, info in _COUNTRY_INFO.items():
        if cid == country or info.region is not _COUNTRY_INFO[country].region:
            continue
        if board.influence[cid][opponent.value] > 0:
            return False
    return True


def _score_realignment_target(
    weights: GreedyWeights, board: Board, observation: Observation, action: Action
) -> float:
    side = observation.side
    opponent = side.opponent
    if action.payload.get("stop"):  # end the Ops spend on realignment rolls
        return 0.0
    country = action.payload["country"]
    # "In general, realignments only occur at DEFCON 2. In most cases,
    # battleground coups are a more powerful method to alter a region in your
    # favor. But once DEFCON drops to 2, you must search for other ways to
    # attack your opponent's battlegrounds." At DEFCON 3+ a realignment is
    # still legal, but it is competing with the coup the article prefers, so it
    # is discounted rather than forbidden.
    low_defcon = observation.defcon <= 2
    own_bonus = _realignment_bonus(board, side, country)
    opp_bonus = _realignment_bonus(board, opponent, country)
    expected_margin = own_bonus - opp_bonus + _realignment_modifier(observation, side)

    before = board_value(weights, board, side)
    if expected_margin > 0:
        removed = int(round(min(expected_margin, board.influence[country][opponent.value])))
        board.influence[country][opponent.value] -= removed
        after = board_value(weights, board, side)
        board.influence[country][opponent.value] += removed
    elif expected_margin < 0:
        removed = int(round(min(-expected_margin, board.influence[country][side.value])))
        board.influence[country][side.value] -= removed
        after = board_value(weights, board, side)
        board.influence[country][side.value] += removed
    else:
        after = before
    gain = after - before
    # "The first kind of realignment, and the best kind, is the realignment that
    # eliminates your opponent's access to the region. ... not only has your
    # opponent lost the battleground, he has also lost any opportunity to put
    # the influence back in." board_value only sees the marker leaving; this
    # prices the access that goes with it.
    if expected_margin > 0 and _realignment_severs_access(board, opponent, country):
        gain += weights.realignment_access_bonus
    if low_defcon:
        gain += weights.realignment_at_defcon_2_bonus
    else:
        gain -= weights.realignment_above_defcon_2_penalty
    return weights.realignment_base + gain


def _best_influence_value(
    weights: GreedyWeights, board: Board, side: Side, ops: int
) -> float:
    best = None
    for cid in board.countries:
        if not board.is_reachable(side, cid):
            continue
        if board.influence_cost(side, cid) > ops:
            continue
        gain = _marginal_gain(weights, board, side, cid, 1)
        if best is None or gain > best:
            best = gain
    return best if best is not None else _NO_OPTION


def _best_coup_value(
    weights: GreedyWeights, board: Board, observation: Observation, side: Side, ops: int, bonus: list[str] | None
) -> float | None:
    """Best expected Coup value among proxy-legal targets, or None if every
    one of them would be a DEFCON self-kill. Region-lock effects beyond
    `RULES["coup_min_defcon"]` (NATO, The Reformer, ...) are not replicated
    here -- out of scope for v1 (core board decisions); see the module docstring."""
    if _coup_is_suicide(observation, side):
        return None  # every target loses the game under Cuban Missile Crisis
    opponent = side.opponent
    best = None
    for cid, info in board.countries.items():
        if board.influence[cid][opponent.value] <= 0:
            continue
        if observation.defcon < RULES["coup_min_defcon"].get(info.region.name, 1):
            continue
        if observation.defcon <= 2 and _coup_risks_defcon(observation, side, info):
            continue
        target_ops = ops + sum(1 for b in (bonus or []) if _in_bonus_region(info, b))
        gain = _expected_coup_gain(weights, board, observation, side, cid, info, target_ops)
        # A Coup that takes DEFCON to 2 while the hand is stranded is priced
        # here too, not just at COUP_TARGET: this is what decides *whether* to
        # Coup at all (`_score_ops_type`), so the cost has to be visible before
        # a target is ever named.
        gain -= _strand_penalty(weights, observation, side, info)
        if best is None or gain > best:
            best = gain
    return best


def _best_realignment_value(weights: GreedyWeights, board: Board, observation: Observation, side: Side) -> float:
    opponent = side.opponent
    best = None
    for cid, info in board.countries.items():
        if board.influence[cid][opponent.value] <= 0:
            continue
        if observation.defcon < RULES["coup_min_defcon"].get(info.region.name, 1):
            continue
        own_bonus = _realignment_bonus(board, side, cid)
        opp_bonus = _realignment_bonus(board, opponent, cid)
        value = own_bonus - opp_bonus + _realignment_modifier(observation, side)
        if best is None or value > best:
            best = value
    return best if best is not None else _NO_OPTION


def _score_ops_type(weights: GreedyWeights, board: Board, observation: Observation, action: Action) -> float:
    side = observation.side
    ctx = observation.pending_decision.context
    ops = ctx["ops"]
    bonus = ctx.get("bonus")
    ops_type = action.payload["type"]

    if ops_type == "influence":
        return weights.influence_base + _best_influence_value(weights, board, side, ops)
    if ops_type == "coup":
        best = _best_coup_value(weights, board, observation, side, ops, bonus)
        if best is None:
            # No Coup target is safe at the current DEFCON: refuse "coup" as
            # an Ops type outright, rather than let COUP_TARGET default into
            # a self-kill (priority #1).
            return -weights.defcon_self_kill_penalty
        caution = weights.defcon_caution * (5 - observation.defcon)
        # A Coup is also the one Ops type that pays the Military Operations
        # requirement, so it carries the shortfall it closes (see
        # `milops_gain`): that is what makes "Coup when you are behind on
        # Military Ops" fall out of the score instead of needing its own rule.
        return weights.coup_base + best - caution + milops_gain(weights, observation, side, ops)
    return weights.realignment_base + _best_realignment_value(weights, board, observation, side)


def _score_headline(weights: GreedyWeights, board: Board, observation: Observation, action: Action) -> float:
    side = observation.side
    cid = action.payload["card"]
    if cid not in _CARDS:  # a non-card option (see _score_action_round_play)
        return 0.0
    card = _CARDS[cid]
    if card.scoring:
        return weights.scoring_card_weight * _scoring_card_favorability(board, side, cid)
    # A headline resolves as the card's event (5.1), so a DEFCON sucker here is
    # the same loss -- and the article notes the headline is the *tempting*
    # place to play one: "Usually the USSR is unwilling to lower DEFCON during
    # their headline, so it's generally safe for the US to play a
    # DEFCON-lowering headline." Safe for whoever headlines second; fatal for
    # the phasing player here, which is who this prices.
    if _defcon_suicide_risk(observation, side, cid, "event"):
        return -weights.defcon_suicide_penalty
    # Headlining an opponent-side event fires it *for them*. Never do that.
    if (card.side is CardSide.US and side is Side.USSR) or (
        card.side is CardSide.USSR and side is Side.US
    ):
        return -weights.opponent_headline_penalty
    # Own/neutral: spend a low-Ops card here and keep higher-Ops ones for Ops.
    score = -weights.hold_high_ops_weight * card.ops
    # A card whose event is worth firing is worth firing for free here: the
    # headline costs no action round, so the event value adds to the low-Ops
    # preference instead of competing with it. This is where the playbook puts
    # several of the priced events ("Headline it", "Great headline").
    score += _own_event_value(side, observation, cid)
    if (
        observation.turn == 1
        and side is Side.USSR
        and cid in _USSR_T1_HEADLINES
    ):
        score += weights.t1_headline_bonus
    return score


def _score_action_round_play(
    weights: GreedyWeights, board: Board, observation: Observation, action: Action
) -> float:
    side = observation.side
    cid = action.payload["card"]
    # `sit_out` is the engine's own pseudo-card for conceding the remaining
    # Action Rounds (4.5-D, offered only with an empty hand). It is a legal
    # option but not a card, so it has no entry in the card table — looking it
    # up raised KeyError, which killed every rollout of this decision and left
    # the bot unable to answer at all.
    if cid not in _CARDS:
        return 0.0
    card = _CARDS[cid]
    if card.scoring:
        return weights.scoring_card_weight * _scoring_card_favorability(board, side, cid)
    ops = _effective_ops_estimate(card, observation, side)
    score = weights.action_round_ops_weight * ops
    if card.side.value == side.opponent.value:
        score -= weights.opponent_event_ops_penalty  # its Event fires for them
    # The mode choice happens *after* this one, so a card that cannot be
    # committed safely has to be priced here as well -- `_score_play_mode` never
    # sees a card that was not picked. This is where the fixed bot still lost
    # games: at DEFCON 2, holding five cards, it picked We Will Bury You for its
    # 4 Ops (the highest score in the hand), and every mode left for that card
    # was fatal.
    #
    # Every suicide-risk card is the opponent's, so the Space Race is the one
    # legal way to dispose of it (the engine refuses own events, the China Card,
    # UN Intervention and scoring cards, and none of those can be a DEFCON
    # degrader here). With an attempt left, the play is to dispose of it, which
    # is worth more than any Ops in the hand; without one, the card is a loss
    # waiting for the moment the hand empties, and only "nothing else to play"
    # should reach it.
    if _defcon_suicide_risk(observation, side, cid, "ops"):
        if _space_race_attempts_left(observation, side) > 0:
            score += weights.strand_disposal_bonus
        else:
            score -= weights.defcon_suicide_penalty
    return score


"""Space Race card selection, from Twilight Strategy's "General Strategy: The
Space Race".

The article's headline: "The number one mistake beginning players make in
Twilight Struggle is to send too many cards off to space." Its rule for what
actually belongs there:

"the real job of the Space Race is to discard truly awful opponent events that
you cannot mitigate in any meaningful way. In this context, 'truly awful'
means: cards that will immediately lose you the game (e.g., DEFCON suicide
cards); cards that provide your opponent access to a region (e.g.,
De-Stalinization); cards that remove your access to a region (e.g., Voice of
America); cards whose Ops value is not enough to repair its damage (e.g., Ussuri
River Skirmish); cards that give your opponent multiple plays in a row (e.g.,
Quagmire/Bear Trap); cards that give your opponent lots of VPs (e.g., OPEC)."

The two lists below are that judgement, card by card, per the side that must
dispose of them. They are keys into `_CARDS`, and a test asserts every one
exists — an id that silently disappeared would otherwise turn a "send this to
space" rule into a no-op.

What this changes: the pre-existing `space_race_base + VP - ops penalty`
formula has no idea which cards are awful, so it spaces on Ops value alone.
These lists add the article's judgement on top of that, and never subtract from
it (spacing is still allowed for cards the article does not name — it just is
not *preferred*).
"""

# "As USSR — These are the US events that I tend to Space Race." The
# DEFCON-safety ones are already lethal under _defcon_suicide_risk when they
# apply; listing them here covers the cases where they are merely awful.
_USSR_SPACE_RACE = frozenset({
    # DEFCON suicide cards (the article's top priority)
    "CIA_Created",
    "Grain_Sales_to_Soviets",
    "Soviets_Shoot_Down_KAL_007",
    "Star_Wars",
    "Tear_Down_This_Wall",
    # "As for non-DEFCON suicide cards"
    "East_European_Unrest",
    "Five_Year_Plan",
    "NORAD",
    "Special_Relationship",
    "Alliance_for_Progress",
    "Bear_Trap",
    "Colonial_Rear_Guards",
    "John_Paul_II_Elected_Pope",
    "Our_Man_In_Tehran",
    "Puppet_Governments",
    "The_Voice_Of_America",
    "Ussuri_River_Skirmish",
    "AWACS_Sale_to_Saudis",
    "Solidarity",
})

# "As US — These are the USSR events that I tend to Space Race."
_US_SPACE_RACE = frozenset({
    # DEFCON suicide cards (the article's top priority)
    "Lone_Gunman",
    "We_Will_Bury_You",
    "Ortega_Elected_in_Nicaragua",
    # "And the non-DEFCON cards"
    "Decolonization",
    "De_Stalinization",
    "Fidel",
    "Socialist_Governments",
    "Liberation_Theology",
    "Muslim_Revolution",
    "OPEC",
    "Quagmire",
    "South_African_Unrest",
    "Glasnost",
    "Iranian_Hostage_Crisis",
    "The_Reformer",
})


"""Reshuffle timing, from Twilight Strategy's "General Strategy: Reshuffles".

"when discarding your opponent's vital events, you want to discard them on
Turns 3 and 7, rather than on Turns 2 or 6." The reason: a card discarded on
Turn 3 waits until the *next* reshuffle (Turn 7) before it can come back, while
one discarded on Turn 2 returns as soon as Turn 3.

The article's turn numbers are a description of the physical deck. This engine
reshuffles when the draw pile empties (`_reshuffle_discard_into_draw`), so
whether those turns are the right ones is a question about *this* deck, not
about the article. Measured over 40 self-played games: the reshuffle lands on
Turn 3 in 40/40 and Turn 7 in 11/40, with rare stragglers at 1 and 9 — so the
article's two turns are the right ones here as well, and the rule below uses
them directly rather than guessing.
"""

# The turns the deck reshuffles: disposing of an opponent's vital event *before*
# one of these guarantees it returns at the next one.
_RESHUFFLE_TURNS = frozenset({3, 7})


def _is_reshuffle_turn(turn: int) -> bool:
    return turn in _RESHUFFLE_TURNS


def _reshuffle_timing_bonus(weights: GreedyWeights, side: Side, observation: Observation, cid: str) -> float:
    """Prefer to spend a vital opponent event on the *last* turn before a
    reshuffle, so it cannot come back for as long as possible.

    "So as a US player, if I draw either or both in the Early War, I will do my
    best to hold onto them until Turn 3 before discarding them with Blockade,
    the Space Race, or UN Intervention. This guarantees that they cannot be
    reintroduced to the deck until Turn 7 at the earliest."

    Only applies to a card the article calls vital *and* that this side wants to
    dispose of (see `_space_race_card_bonus`), and only on the turn before a
    reshuffle would recycle it — i.e. while waiting still buys something.
    """
    card = _CARDS.get(cid)
    if card is None or card.side.value == side.value:
        return 0.0          # "your opponent's vital events"
    wants = _USSR_SPACE_RACE if side is Side.USSR else _US_SPACE_RACE
    if cid not in wants:
        return 0.0
    # Disposing *on* a reshuffle turn is the goal: the card then sits in the
    # discard until the following reshuffle rather than being recycled into the
    # next deal.
    return weights.reshuffle_timing_bonus if _is_reshuffle_turn(observation.turn) else 0.0


def _space_race_card_bonus(weights: GreedyWeights, side: Side, cid: str) -> float:
    """The article's "this card belongs in space" judgement, per side.

    Only for the opponent's cards: "There's no real advantage to playing your
    opponents' recurring events instead of spacing them. The only relevant
    question, therefore, is whether it's worth sending to space or using the
    Ops" — which is exactly the choice this bonus pushes toward spacing.
    """
    card = _CARDS.get(cid)
    if card is None or card.side.value == side.value:
        return 0.0
    wants = _USSR_SPACE_RACE if side is Side.USSR else _US_SPACE_RACE
    return weights.space_race_card_bonus if cid in wants else 0.0


# -- own-event valuation ------------------------------------------------------
#
# The bot fired *no* own event in 1298 opportunities over 40 self-played games
# (behavior probe, seeds 1-40): `_score_play_mode`'s event branch was a flat
# `-event_mode_penalty`, so Operations always won, and the bot played every
# game as an Ops-only opponent while being forced to hand the other side every
# event it played against itself. That is a bigger strategic hole than any
# single card rule in this file.
#
# An entry is the event's worth in `board_value` units -- the same scale the
# alternative is measured on, since `_score_play_mode` compares this number
# directly against `ops_mode_per_point x card.ops` (a 3-Ops card is 9.0). So
# the number *is* the judgement "firing this beats spending its Ops", and a
# card only needs an entry when that is true.
#
# Only the extremes are claimed. The playbook's conditional advice ("Event
# when they've actually invested in the Middle East", "Worthless played late")
# is deliberately *not* encoded: an unlisted card keeps the Ops-first default,
# which is right far more often than a guess would be. The two exceptions that
# do carry their condition are in `_EARLY_TURN_EVENTS`, because "first action
# round of the turn" is a fact this bot can read off the observation.
#
# Every entry is a playbook entry, quoted in docs/STRATEGY.md: the card list
# below is the playbook's "Always event" / "Strong event" / "Free event" set,
# not an independent opinion. `test_greedy.py` pins the ids against cards.json
# and pins the playbook's "never event" cards *out* of the table.

_EVENT_DECISIVE = 14.0  # "Always event": beats spending its Ops almost anywhere
_EVENT_STRONG = 10.0  # "Strong event" / "Free event" with a large board effect
_EVENT_FREE = 7.0  # "Free event": a clear gain, but beats only 1-2 Ops

_OWN_EVENT_VALUE: dict[str, dict[str, float]] = {
    # -- USSR own events --
    "Fidel": {"USSR": _EVENT_DECISIVE},  # "Always event."
    "Nasser": {"USSR": _EVENT_DECISIVE},  # "Always event."
    "Allende": {"USSR": _EVENT_DECISIVE},  # "Always event. Your door into South America."
    "Portuguese_Empire_Crumbles": {"USSR": _EVENT_DECISIVE},  # "Always event."
    "Liberation_Theology": {"USSR": _EVENT_DECISIVE},  # "Always event."
    "De_Stalinization": {"USSR": _EVENT_DECISIVE},  # "Really powerful event."
    "Decolonization": {"USSR": _EVENT_DECISIVE},  # "Strong event."
    "Ortega_Elected_in_Nicaragua": {"USSR": _EVENT_STRONG},
    "Warsaw_Pact_Formed": {"USSR": _EVENT_FREE},
    "De_Gaulle_Leads_France": {"USSR": _EVENT_FREE},
    "Romanian_Abdication": {"USSR": _EVENT_FREE},  # "Free event."
    "Che": {"USSR": _EVENT_FREE},
    "Cultural_Revolution": {"USSR": _EVENT_FREE},
    "Marine_Barracks_Bombing": {"USSR": _EVENT_FREE},  # "Free event. Take it."
    "Pershing_II_Deployed": {"USSR": _EVENT_FREE},
    "Glasnost": {"USSR": _EVENT_FREE},
    "Iranian_Hostage_Crisis": {"USSR": _EVENT_FREE},
    # -- US own events --
    "Marshall_Plan": {"US": _EVENT_DECISIVE},  # "Always event, early."
    "North_Sea_Oil": {"US": _EVENT_STRONG},  # "an extra action round this turn"
    "The_Voice_Of_America": {"US": _EVENT_STRONG},  # "Strong event"
    "Camp_David_Accords": {"US": _EVENT_FREE},
    "Panama_Canal_Returned": {"US": _EVENT_FREE},  # "Free event."
    "OAS_Founded": {"US": _EVENT_FREE},  # "Free event. Take the influence."
    "Sadat_Expels_Soviets": {"US": _EVENT_FREE},  # "Free event."
    "Nixon_Plays_The_China_Card": {"US": _EVENT_FREE},
    "An_Evil_Empire": {"US": _EVENT_FREE},  # "Free 1 VP ... Play it."
    "The_Iron_Lady": {"US": _EVENT_FREE},
    "Ussuri_River_Skirmish": {"US": _EVENT_FREE},
    # -- neutral events, either side --
    "Junta": {"US": _EVENT_FREE, "USSR": _EVENT_FREE},  # "Event, then coup in the same region."
    "Brush_War": {"US": _EVENT_FREE, "USSR": _EVENT_FREE},  # "Really good event"
    "ABM_Treaty": {"US": _EVENT_FREE, "USSR": _EVENT_FREE},  # "Always event, and do a coup..."
    "Captured_Nazi_Scientist": {"US": _EVENT_FREE, "USSR": _EVENT_FREE},  # "Always event when the next box pays."
}

# "Event, first AR of the turn. Worthless played late." -- a condition the bot
# can read, so these carry it rather than being dropped from the table.
_EARLY_TURN_EVENTS = frozenset({"Containment", "Brezhnev_Doctrine"})


def _own_event_value(side: Side, observation: Observation, cid: str) -> float:
    """What firing `cid`'s event is worth to `side`, or 0.0 for "no judgement".

    0.0 does not mean worthless: it means the card keeps the Ops-first default
    in `_score_play_mode`, which is where every unlisted card lands. Only an
    own or a neutral event can be fired by `side` -- the engine never offers
    the `event` mode for the opponent's card (playing one for Ops fires *their*
    event, which is `_defcon_suicide_risk`'s problem, not a value to chase).
    """
    if not observation.events_enabled:
        # The mode is still on offer, but nothing resolves: a no-op discard is
        # never better than spending the card's Ops.
        return 0.0
    card = _CARDS.get(cid)
    if card is None:
        return 0.0
    if card.side.value in (Side.US.value, Side.USSR.value) and card.side.value != side.value:
        return 0.0  # their event, not ours to fire
    if cid in _EARLY_TURN_EVENTS:
        return _EVENT_DECISIVE if observation.action_round <= 1 else 0.0
    return _OWN_EVENT_VALUE.get(cid, {}).get(side.value, 0.0)


def _score_play_mode(weights: GreedyWeights, board: Board, observation: Observation, action: Action) -> float:
    side = observation.side
    cid = observation.pending_decision.context["card"]
    if cid not in _CARDS:  # a non-card option (see _score_action_round_play)
        return 0.0
    card = _CARDS[cid]
    mode = action.payload["mode"]
    ops = _effective_ops_estimate(card, observation, side)

    if mode == "space_race":
        expected_vp = _space_race_expected_vp(observation, side)
        return (
            weights.space_race_base
            + weights.space_race_vp_weight * expected_vp
            + _space_race_card_bonus(weights, side, cid)
            + _reshuffle_timing_bonus(weights, side, observation, cid)
            + _strand_disposal_bonus(weights, observation, side, cid)
            - weights.space_race_ops_penalty * ops
        )
    if mode == "ops":
        score = weights.ops_mode_per_point * ops
        if card.side.value == side.opponent.value:
            score -= weights.opponent_event_ops_penalty  # fires their Event
            # ...and if that Event is a DEFCON suicide, the Ops play loses the
            # game exactly as an event play of one's own would: see
            # _defcon_suicide_risk. Checked *after* the ordinary penalty so the
            # refusal is one value, not a score that another weight could
            # outbid.
            if _defcon_suicide_risk(observation, side, cid, mode):
                return -weights.defcon_suicide_penalty
        return score
    if mode == "un_intervention":
        # UN Intervention cancels the opponent card's Event, so no penalty.
        return weights.ops_mode_per_point * ops
    # mode == "event": a DEFCON sucker played for its event at DEFCON 2 loses
    # the game outright, which outranks whatever the card does (see
    # _defcon_suicide_risk).
    if _defcon_suicide_risk(observation, side, cid, mode):
        return -weights.defcon_suicide_penalty
    value = _own_event_value(side, observation, cid)
    if value:
        return value
    # No judgement for this card, so the Ops-first default stands. That covers
    # three cases: the event layer is off (a no-op discard), the card's event
    # is not implemented yet, or it is one the playbook does not call worth
    # its Ops ("usually better to use for ops"). The table above says which
    # cards are priced and why the rest deliberately are not.
    return -weights.event_mode_penalty


def _score_event_choice(weights: GreedyWeights, board: Board, observation: Observation, action: Action) -> float:
    """Dispatches by `decision.context["event"]`. Every EVENT_CHOICE-driven
    card other than the ones named here still returns 0.0 for all of its
    options, i.e. still falls back to the first legal one (see module
    docstring)."""
    event = observation.pending_decision.context.get("event")
    if event == "Aldrich_Ames_Remix":
        # Force the discard of the opponent's most valuable card in hand,
        # proxied by its Ops value (a scoring card's real cost -- losing the
        # region -- isn't modeled by GreedyPlayer's no-lookahead heuristics
        # elsewhere either; see _score_play_mode above).
        card = _CARDS.get(action.payload["choice"])
        return float(card.ops) if card is not None else 0.0
    if event == "How_I_Learned_to_Stop_Worrying":
        # "Set DEFCON to any level, then +5 to the phasing side's Military
        # Operations track" -- the options *are* DEFCON levels, and the first
        # one is an immediate loss for the side choosing it (8.1.3). This is
        # the one card in the deck whose first-listed option is suicide, and
        # the unscored fallback walked into it: 3 of 40 self-played games
        # ended here before this rule.
        #
        # Above 1, the level is a trade: a higher DEFCON raises the
        # opponent's Military Operations requirement (the requirement *is* the
        # DEFCON level, and the +5 Ops this event grants cover the phasing
        # side's own at any level), while a lower one restricts where Coups
        # may be made. The bot takes the highest level, which is the one that
        # cannot lose to a DEFCON drop on its own turn -- the same
        # safety-first reading the card's own playbook entry takes
        # ("Usually better to use for ops. If evented, never set defcon to 1").
        level = action.payload.get("choice")
        if level is None:
            return 0.0
        if level == "1":
            return -weights.defcon_suicide_penalty
        return weights.defcon_setting_weight * float(level)
    return 0.0


_SCORERS: dict[DecisionKind, Callable[[GreedyWeights, Board, Observation, Action], float]] = {
    DecisionKind.PLACE_INFLUENCE: _score_place_influence,
    DecisionKind.COUP_TARGET: _score_coup_target,
    DecisionKind.REALIGNMENT_TARGET: _score_realignment_target,
    DecisionKind.OPS_TYPE: _score_ops_type,
    DecisionKind.HEADLINE_PLAY: _score_headline,
    DecisionKind.ACTION_ROUND_PLAY: _score_action_round_play,
    DecisionKind.PLAY_MODE: _score_play_mode,
    DecisionKind.EVENT_CHOICE: _score_event_choice,
}


class GreedyPlayer:
    """See module docstring. Stateless across turns beyond its own scratch
    `Board` (re-synced from `observation.influence` -- public state -- on
    every call, never the engine's own `Board`)."""

    def __init__(self, weights: GreedyWeights | None = None) -> None:
        self.weights = weights or GreedyWeights()
        self._board = Board()

    def option_scores(self, observation: Observation) -> list[float]:
        """The heuristic score of every legal option (aligned with
        `pending_decision.options`). Used as a search prior; falls back to 0.0
        for decision kinds this bot has no scorer for."""
        decision: Decision = observation.pending_decision
        scorer = _SCORERS.get(decision.kind)
        if scorer is None:
            return [0.0] * len(decision.options)
        _sync_board(self._board, observation)
        return [scorer(self.weights, self._board, observation, a) for a in decision.options]

    def choose_action(self, observation: Observation, history: Sequence[Event]) -> Action:
        decision: Decision = observation.pending_decision
        if not decision.options:
            # An engine-stuck state: fail loudly with the kind, rather than a
            # bare IndexError/ValueError from options[0]/max().
            raise RuntimeError(
                f"no legal options for {decision.kind.value} (engine stuck state)"
            )
        scorer = _SCORERS.get(decision.kind)
        if scorer is None:
            return decision.options[0]
        _sync_board(self._board, observation)
        return max(
            decision.options,
            key=lambda action: scorer(self.weights, self._board, observation, action),
        )
