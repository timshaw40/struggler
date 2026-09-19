"""A small learned board-value model, and a pure-stdlib ridge fit.

Purpose: replace the hand-tuned `board_value` scalar (and, in MCTS, the
expensive greedy rollout's terminal estimate) with a learned function of the
same *public* board state. Every feature is antisymmetric — `f(side) =
-f(opponent)` — so a bias-free linear model automatically satisfies
`value(US) + value(USSR) = 1`, which is what a two-player zero-sum value
needs. No hidden information is used (influence, region tiers/bonuses and the
tracks are all public), so this cannot leak an opponent's hand.

The feature vector is ~19-dimensional, which makes a closed-form ridge fit
(normal equations) cheap in pure Python — no numpy/torch dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from struggler.engine import Region, ScoringTier, Side
from struggler.engine.board import Board

REGIONS: tuple[Region, ...] = tuple(Region)
_TIER = {
    ScoringTier.NONE: 0.0,
    ScoringTier.PRESENCE: 1.0,
    ScoringTier.DOMINATION: 2.0,
    ScoringTier.CONTROL: 3.0,
}

FEATURE_NAMES: tuple[str, ...] = (
    tuple(f"tier_{r.name}" for r in REGIONS)
    + tuple(f"bonus_{r.name}" for r in REGIONS)
    + ("battlegrounds", "countries", "influence", "vp", "military_ops", "space_race", "china_card")
)
DIM = len(FEATURE_NAMES)


def value_features(
    board: Board,
    side: Side,
    *,
    vp: int,
    military_ops: Mapping[str, int],
    space_race: Mapping[str, int],
    china_card_owner: Side | str,
) -> list[float]:
    """Antisymmetric public-state features from `side`'s perspective."""
    opp = side.opponent
    feats: list[float] = []

    for r in REGIONS:
        feats.append(_TIER[board.region_tier(side, r)] - _TIER[board.region_tier(opp, r)])
    for r in REGIONS:
        feats.append(float(board.region_bonus_vp(side, r) - board.region_bonus_vp(opp, r)))

    bg = ctrl = bg_o = ctrl_o = 0
    inf = inf_o = 0
    for cid, info in board.countries.items():
        owner = board.control(cid)
        if owner is side:
            ctrl += 1
            if info.battleground:
                bg += 1
        elif owner is opp:
            ctrl_o += 1
            if info.battleground:
                bg_o += 1
        inf += board.influence[cid][side.value]
        inf_o += board.influence[cid][opp.value]
    feats.extend([float(bg - bg_o), float(ctrl - ctrl_o), float(inf - inf_o)])

    signed_vp = vp if side is Side.US else -vp
    feats.append(signed_vp / 20.0)
    feats.append((military_ops.get(side.value, 0) - military_ops.get(opp.value, 0)) / 5.0)
    feats.append((space_race.get(side.value, 0) - space_race.get(opp.value, 0)) / 8.0)
    owner = china_card_owner.value if isinstance(china_card_owner, Side) else china_card_owner
    feats.append(1.0 if owner == side.value else (-1.0 if owner == opp.value else 0.0))
    assert len(feats) == DIM
    return feats


def features_from_engine(engine: Any, side: Side) -> list[float]:
    return value_features(
        engine.board,
        side,
        vp=engine.vp,
        military_ops=engine.military_ops,
        space_race=engine.space_race,
        china_card_owner=engine.china_card_owner,
    )


@dataclass
class LinearValue:
    weights: list[float] = field(default_factory=lambda: [0.0] * DIM)

    def raw(self, features: Sequence[float]) -> float:
        return sum(w * x for w, x in zip(self.weights, features))

    def value(self, features: Sequence[float]) -> float:
        """Win-probability-like score in [0, 1] for the feature's side."""
        return max(0.0, min(1.0, 0.5 + 0.5 * self.raw(features)))

    def value_for_engine(self, engine: Any, side: Side) -> float:
        return self.value(features_from_engine(engine, side))

    def to_dict(self) -> dict[str, Any]:
        return {"weights": list(self.weights), "features": list(FEATURE_NAMES)}

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "LinearValue":
        return cls(weights=list(json.loads(Path(path).read_text())["weights"]))


def fit_ridge(X: Sequence[Sequence[float]], y: Sequence[float], l2: float = 1.0) -> list[float]:
    """Closed-form ridge regression: w = (XᵀX + λI)⁻¹ Xᵀy, Gaussian elimination."""
    if not X:
        return [0.0] * DIM
    d = len(X[0])
    xtx = [[0.0] * d for _ in range(d)]
    xty = [0.0] * d
    for xi, yi in zip(X, y):
        for i in range(d):
            v = xi[i]
            xty[i] += v * yi
            row = xtx[i]
            for j in range(d):
                row[j] += v * xi[j]
    for i in range(d):
        xtx[i][i] += l2
    return _solve(xtx, xty)


def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            continue  # singular column: leave weight 0
        M[col], M[piv] = M[piv], M[col]
        inv = 1.0 / M[col][col]
        for r in range(n):
            if r == col:
                continue
            factor = M[r][col] * inv
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                M[r][c] -= factor * M[col][c]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(n)]
