"""Record the engine's damage for a table of matchups, for the damage preview's tests.

Run from ``api/`` (it imports the engine)::

    uv run python ../frontend/src/world/__fixtures__/combat_fixture.py

Writes ``frontend/src/world/__fixtures__/combat.json``. Each row is one fight the
real engine resolved on a two-hex world map: an attacker hex of plains and a target
hex of some ground (plains, hills, woods, a town, or a founded site on plains).

- ``melee`` rows run `resolve_contest` (api/app/engine/combat.py) with the ground
  from `GameState.terrain_defense`: one or two attackers stepping into a deployed
  defender that may Hold or be disordered, at full or low HP.
- ``barrage`` rows run `resolve_barrage` (api/app/engine/barrage.py) from a gun on
  the attacker hex: a deployed occupant (holding or not, disordered or not) and/or
  a packed column in the target hex.

``combatPreview.test.ts`` checks that `combatPreview.ts` reproduces every row.
Regenerate after a change to unit stats, terrain, or the damage rules;
``api/tests/test_frontend_fixtures.py`` fails while the committed JSON is stale.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

from app.engine.barrage import resolve_barrage
from app.engine.combat import resolve_contest
from app.engine.economy_rules import SiteKind
from app.engine.events import BarrageResolved
from app.engine.hex_coords import Axial
from app.engine.models import (
    BarrageOrder,
    GameConfig,
    GameMap,
    GameState,
    Hex,
    HoldOrder,
    MapSite,
    Order,
    Player,
    Posture,
    Settlement,
    Unit,
    VictoryConfig,
)
from app.engine.stats import Terrain, UnitType

OUT = Path(__file__).with_name("combat.json")

FROM = Axial(q=0, r=0)
TARGET = Axial(q=1, r=0)
TYPES = [UnitType.INFANTRY, UnitType.ARMOR, UnitType.ARTILLERY]
# (label, terrain, whether a founded site stands on the target hex)
GROUNDS: list[tuple[str, Terrain, bool]] = [
    ("plains", Terrain.PLAINS, False),
    ("hills", Terrain.HILLS, False),
    ("woods", Terrain.WOODS, False),
    ("town", Terrain.TOWN, False),
    ("site", Terrain.PLAINS, True),
]


def _state(terrain: Terrain, site: bool, units: list[Unit]) -> GameState:
    game_map = GameMap(
        width=2,
        height=1,
        hexes=[
            Hex(coord=FROM, terrain=Terrain.PLAINS),
            Hex(coord=TARGET, terrain=terrain),
        ],
        sites=[MapSite(coord=TARGET, kind=SiteKind.FARM, garrison=0)] if site else [],
    )
    return GameState(
        turn=1,
        map=game_map,
        settlements=[Settlement(coord=TARGET, controlled_by="red")] if site else [],
        units=units,
        players=[Player(id="blue"), Player(id="red")],
        config=GameConfig(victory=VictoryConfig(turn_limit=10)),
    )


def _melee_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    attacker_sets = [[t] for t in TYPES] + [[UnitType.ARMOR, UnitType.INFANTRY]]
    for (
        attackers,
        defender_type,
        (label, terrain, site),
        hold,
        disordered,
        hps,
    ) in itertools.product(
        attacker_sets,
        TYPES,
        GROUNDS,
        [False, True],
        [False, True],
        [(10, 10), (1, 2)],
    ):
        attacker_hp, defender_hp = hps
        defender = Unit(
            id=1,
            type=defender_type,
            owner="red",
            coord=TARGET,
            hp=defender_hp,
            disordered=disordered,
        )
        units = [
            Unit(id=10 + k, type=t, owner="blue", coord=FROM, hp=attacker_hp)
            for k, t in enumerate(attackers)
        ]
        state = _state(terrain, site, [defender, *units])
        outcome = resolve_contest(
            TARGET,
            defender,
            units,
            {1} if hold else set(),
            state.terrain_defense(TARGET),
        )
        rows.append(
            {
                "ground": label,
                "terrain": terrain.value,
                "settlement_ground": state.settlement_ground(TARGET),
                "attackers": [{"type": u.type.value, "hp": u.hp} for u in units],
                "defender": {
                    "type": defender_type.value,
                    "hp": defender_hp,
                    "hold": hold,
                    "disordered": disordered,
                },
                "out": {
                    "dealt": [outcome.results[u.id].damage_dealt for u in units],
                    "taken": [outcome.results[u.id].damage_taken for u in units],
                    "attackers_destroyed": [
                        outcome.results[u.id].destroyed for u in units
                    ],
                    "defender_hp_after": outcome.results[1].hp_after,
                    "defender_destroyed": outcome.results[1].destroyed,
                },
            }
        )
    return rows


def _barrage_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # What stands in the target hex: a deployed unit, a packed column, or both.
    for (
        occupant_type,
        (label, terrain, site),
        stack,
        hold,
        disordered,
        hp,
    ) in itertools.product(
        TYPES,
        GROUNDS,
        ["deployed", "column", "both"],
        [False, True],
        [False, True],
        [10, 2],
    ):
        if stack == "column" and (hold or disordered):
            continue  # a column gets neither
        gun = Unit(id=9, type=UnitType.ARTILLERY, owner="blue", coord=FROM, hp=10)
        occupants: list[Unit] = []
        if stack in ("deployed", "both"):
            occupants.append(
                Unit(
                    id=1,
                    type=occupant_type,
                    owner="red",
                    coord=TARGET,
                    hp=hp,
                    disordered=disordered,
                )
            )
        if stack in ("column", "both"):
            occupants.append(
                Unit(
                    id=2,
                    type=UnitType.INFANTRY if stack == "both" else occupant_type,
                    owner="red",
                    coord=TARGET,
                    hp=hp,
                    posture=Posture.COLUMN,
                )
            )
        state = _state(terrain, site, [gun, *occupants])
        orders: list[Order] = [BarrageOrder(unit_id=9, target=TARGET)]
        if hold and stack != "column":
            orders.append(HoldOrder(unit_id=1))
        _, events = resolve_barrage(state, orders)
        (event,) = events
        assert isinstance(event, BarrageResolved)
        hits = ([event.hit] if event.hit is not None else []) + event.extra_hits
        rows.append(
            {
                "ground": label,
                "terrain": terrain.value,
                "settlement_ground": state.settlement_ground(TARGET),
                "occupants": [
                    {
                        "type": u.type.value,
                        "hp": u.hp,
                        "column": u.posture is Posture.COLUMN,
                        "hold": hold and u.posture is Posture.DEPLOYED,
                        "disordered": u.disordered,
                    }
                    for u in occupants
                ],
                "out": [
                    {
                        "damage": h.damage,
                        "hp_after": h.hp_after,
                        "destroyed": h.destroyed,
                    }
                    for h in hits
                ],
            }
        )
    return rows


def build() -> str:
    """The fixture's JSON text, as `main` writes it."""
    fixture = {"melee": _melee_rows(), "barrage": _barrage_rows()}
    # One compact row per line: small, and a rule change shows as changed rows.
    parts = []
    for key, rows in fixture.items():
        body = ",\n".join(json.dumps(r, separators=(",", ":")) for r in rows)
        parts.append(f'"{key}":[\n{body}\n]')
    return "{" + ",\n".join(parts) + "}\n"


def main() -> None:
    text = build()
    OUT.write_text(text)
    fixture = json.loads(text)
    print(
        f"wrote {OUT} ({len(fixture['melee'])} melee, "
        f"{len(fixture['barrage'])} barrage rows)"
    )


if __name__ == "__main__":
    main()
