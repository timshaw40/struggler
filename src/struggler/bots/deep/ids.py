"""Identifier mapping between struggler and DeepStruggle.

Cards share the printed card number (DeepStruggle's card id *is* the number).
Countries are matched by name, read from the DeepStruggle engine itself so no
table can drift from the build in use; two names differ in spelling. The
Chinese Civil War pseudo-country has no DeepStruggle counterpart, which is why
a `deep` seat refuses that optional rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from struggler.engine.cards import load_cards
from struggler.engine.types import Side

#: DeepStruggle spelling -> struggler id, where the plain normalisation differs.
_COUNTRY_ALIASES = {"United Kingdom": "UK", "Dominican Rep": "Dominican_Republic"}

#: `ts::Player` values.
DS_US, DS_USSR, DS_NONE = 1, -1, 0

#: `ts::Region` order, which DeepStruggle's region slots and Chernobyl use.
DS_REGIONS = ("EUROPE", "ASIA", "MIDDLE_EAST", "AFRICA", "CENTRAL_AMERICA", "SOUTH_AMERICA")

N_COUNTRIES = 84


def ds_side(side: Side | str | None) -> int:
    value = side.value if isinstance(side, Side) else side
    return {"US": DS_US, "USSR": DS_USSR}.get(value, DS_NONE)


def side_from_ds(player: int) -> Side | None:
    return {DS_US: Side.US, DS_USSR: Side.USSR}.get(player)


def _norm(name: str) -> str:
    return name.lower().replace(" ", "_").replace("/", "_").replace(".", "").replace("-", "_")


@dataclass(frozen=True)
class IdMap:
    """Both directions for countries and cards."""

    country_to_ds: dict[str, int]
    country_from_ds: dict[int, str]
    card_to_ds: dict[str, int] = field(default_factory=dict)
    card_from_ds: dict[int, str] = field(default_factory=dict)

    @classmethod
    def build(cls, ds_country_names: list[str], struggler_countries: list[str]) -> "IdMap":
        by_norm = {_norm(cid): cid for cid in struggler_countries}
        to_ds: dict[str, int] = {}
        for ds_id, name in enumerate(ds_country_names):
            cid = _COUNTRY_ALIASES.get(name) or by_norm.get(_norm(name))
            if cid is None:
                raise ValueError(f"DeepStruggle country {name!r} has no struggler match")
            to_ds[cid] = ds_id
        cards = load_cards()
        card_to = {cid: card.number for cid, card in cards.items()}
        return cls(
            country_to_ds=to_ds,
            country_from_ds={v: k for k, v in to_ds.items()},
            card_to_ds=card_to,
            card_from_ds={v: k for k, v in card_to.items()},
        )
