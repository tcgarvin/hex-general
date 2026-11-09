"""Server-side order validation — the HTTP boundary's gatekeeper (roadmap Phase 5).

The execution engine deliberately *trusts* its orders: it assumes each order
references a real unit, paths are contiguous, barrages are in range, and recruits
are legal, and it crashes loudly if not (see `barrage.py`, which names "full order
legality/visibility" as this boundary's job). This module is that boundary. It
takes the typed orders parsed from an untrusted request body plus the submitting
player's slot, and rejects anything statically illegal with `OrderValidationError`
(which the route turns into HTTP 422) before the orders are ever stored.

What is validated here is **static** legality — facts that do not depend on the
opponent's simultaneous orders:

  * ownership — every order targets a unit the caller owns; recruits are for the
    caller's own slot (this is the security boundary: a token cannot move enemy
    units);
  * one order per unit;
  * move and march paths are contiguous, on-map, and avoid impassable terrain
    (SPEC §3.4);
  * travel destinations and waypoints are on-map and passable, and a column
    route to them exists (WORLD_SPEC §5);
  * orders fit the unit's posture: Move and Barrage need a deployed unit, Deploy
    needs a packed column (WORLD_SPEC §4);
  * barrage is by an artillery unit at an on-map hex within range (SPEC §7.2);
  * recruits target a city the caller controls, are affordable from the current
    bank, and stay under the unit cap (SPEC §6.5, §6.1). In a world game they
    target a site of the right kind (tanks at a factory, guns need a workshop)
    and, with any build order, are affordable from the current stocks;
  * world base orders are the caller's own: a build only when nothing else is
    under construction and the building has a level left, and disband picks only
    from the caller's units.

**Dynamic** interactions — ZOC stops, stacking conflicts, edge swaps, and the
exact end-of-turn bank after economy — are the engine's authority and are not
re-checked here. Recruitment legality is validated against the *current* state
(banks/control before this turn resolves); the rare capture-then-recruit-in-one-
turn combo is therefore not accepted by the API in v0.1 even though the engine
supports it. This conservatism keeps a locked turn guaranteed to resolve.
"""

from __future__ import annotations

from app.engine.hex_coords import Axial, distance, is_adjacent
from app.engine.economy_rules import Stocks, WorldEconomy
from app.engine.models import (
    BarrageOrder,
    DeployOrder,
    EconomyOrders,
    GameState,
    Hex,
    MarchOrder,
    MoveOrder,
    Order,
    PlayerId,
    Posture,
    Recruitment,
    TravelOrder,
    Unit,
)
from app.engine.recruitment import WORLD_RECRUIT_NEEDS, WORLD_RECRUIT_SITES
from app.engine.routing import find_route
from app.engine.stats import TERRAIN_STATS, UNIT_STATS, Terrain, UnitType


class OrderValidationError(Exception):
    """A submitted order is statically illegal for the requesting player."""


def validate_submission(
    state: GameState,
    slot: PlayerId,
    orders: list[Order],
    recruitments: list[Recruitment],
    economy_orders: EconomyOrders | None = None,
) -> None:
    """Validate one player's full submission against ``state``.

    ``economy_orders`` are a world player's base orders (``None``: none sent).

    Raises `OrderValidationError` on the first problem found; returns ``None`` when
    the whole submission is statically legal for ``slot``.
    """
    units_by_id = {u.id: u for u in state.units}
    hexes_by_coord = state.map.index

    seen: set[int] = set()
    for order in orders:
        unit = units_by_id.get(order.unit_id)
        if unit is None:
            raise OrderValidationError(f"order references unknown unit {order.unit_id}")
        if unit.owner != slot:
            raise OrderValidationError(f"unit {order.unit_id} is not owned by {slot!r}")
        if order.unit_id in seen:
            raise OrderValidationError(f"unit {order.unit_id} has more than one order")
        seen.add(order.unit_id)

        if isinstance(order, MoveOrder | BarrageOrder) and (
            unit.posture is Posture.COLUMN
        ):
            raise OrderValidationError(
                f"unit {order.unit_id} is packed; it can march, deploy, rest or hold"
            )
        if isinstance(order, DeployOrder) and unit.posture is not Posture.COLUMN:
            raise OrderValidationError(
                f"unit {order.unit_id} is not packed, so it cannot deploy"
            )
        if isinstance(order, MoveOrder | MarchOrder):
            _validate_path(order, unit, hexes_by_coord)
        elif isinstance(order, BarrageOrder):
            _validate_barrage(order, unit, hexes_by_coord)
        elif isinstance(order, TravelOrder):
            _validate_travel(order, unit, state)

    economy = state.config.economy
    if economy is None:
        if economy_orders is not None:
            raise OrderValidationError("base orders are only for world games")
        _validate_recruitments(state, slot, recruitments, hexes_by_coord)
        return
    spent = _validate_economy_orders(state, economy, slot, economy_orders)
    _validate_world_recruitments(state, economy, slot, recruitments, spent)


def _validate_path(
    order: MoveOrder | MarchOrder, unit: Unit, hexes_by_coord: dict[Axial, Hex]
) -> None:
    """Path must be a contiguous chain of on-map, passable hexes from the unit."""
    prev = unit.coord
    for step in order.path:
        if not is_adjacent(prev, step):
            raise OrderValidationError(
                f"unit {order.unit_id} path step {step} is not adjacent to {prev}"
            )
        hex_ = hexes_by_coord.get(step)
        if hex_ is None:
            raise OrderValidationError(
                f"unit {order.unit_id} path step {step} is off-map"
            )
        if not TERRAIN_STATS[hex_.terrain].passable:
            raise OrderValidationError(
                f"unit {order.unit_id} path step {step} is impassable terrain"
            )
        prev = step


def _validate_travel(order: TravelOrder, unit: Unit, state: GameState) -> None:
    """Waypoints and destination must be passable, and the route must exist.

    Routing ignores units (WORLD_SPEC §5), so reachability here is a fact about
    the static map and the engine's own routing will agree with it.
    """
    if order.destination == unit.coord:
        raise OrderValidationError(
            f"unit {order.unit_id} is already at its travel destination"
        )
    for stop in (*order.via, order.destination):
        hex_ = state.map.index.get(stop)
        if hex_ is None:
            raise OrderValidationError(
                f"unit {order.unit_id} travel waypoint {stop} is off-map"
            )
        if not TERRAIN_STATS[hex_.terrain].passable:
            raise OrderValidationError(
                f"unit {order.unit_id} travel waypoint {stop} is impassable terrain"
            )
    if not find_route(state.map, unit.coord, order.destination, order.via):
        raise OrderValidationError(
            f"unit {order.unit_id} has no route to {order.destination}"
        )


def _validate_barrage(
    order: BarrageOrder, unit: Unit, hexes_by_coord: dict[Axial, Hex]
) -> None:
    """Only artillery may barrage, at an on-map hex within its range (SPEC §7.2)."""
    if unit.type is not UnitType.ARTILLERY:
        raise OrderValidationError(
            f"unit {order.unit_id} is {unit.type} and cannot barrage"
        )
    if order.target not in hexes_by_coord:
        raise OrderValidationError(
            f"barrage target {order.target} for unit {order.unit_id} is off-map"
        )
    reach = UNIT_STATS[UnitType.ARTILLERY].attack_range
    if distance(unit.coord, order.target) > reach:
        raise OrderValidationError(
            f"barrage target {order.target} is out of range "
            f"(>{reach}) for unit {order.unit_id}"
        )


def _validate_recruitments(
    state: GameState,
    slot: PlayerId,
    recruitments: list[Recruitment],
    hexes_by_coord: dict[Axial, Hex],
) -> None:
    """Recruits must be the caller's own, at a controlled city, and affordable.

    Affordability and the unit cap are checked against the *current* bank and unit
    count, accumulating across the submission so two recruits can't each spend the
    same coin. The engine re-validates at the recruitment phase against end-of-turn
    state and remains the final authority (SPEC §6.5).
    """
    if not recruitments:
        return

    control_at = {s.coord: s.controlled_by for s in state.settlements}
    bank_by_player = {p.id: p.bank for p in state.players}
    if slot not in bank_by_player:
        raise OrderValidationError(f"unknown player slot {slot!r}")

    unit_count = sum(1 for u in state.units if u.owner == slot)
    cap = state.unit_cap_for(slot)
    spent = 0
    claimed: set[Axial] = set()
    for recruit in recruitments:
        if recruit.owner != slot:
            raise OrderValidationError(
                f"recruit for {recruit.owner!r} is not owned by caller {slot!r}"
            )
        hex_ = hexes_by_coord.get(recruit.coord)
        if hex_ is None or hex_.terrain is not Terrain.CITY:
            raise OrderValidationError(f"recruit at {recruit.coord} is not a city")
        if control_at.get(recruit.coord) != slot:
            raise OrderValidationError(
                f"recruit at {recruit.coord} is not a city controlled by {slot!r}"
            )
        if recruit.coord in claimed:
            raise OrderValidationError(
                f"two recruits target the same city {recruit.coord}"
            )
        claimed.add(recruit.coord)

        unit_count += 1
        if unit_count > cap:
            raise OrderValidationError(f"recruiting exceeds the unit cap of {cap}")
        spent += UNIT_STATS[recruit.type].recruit_cost
        if spent > bank_by_player[slot]:
            raise OrderValidationError(
                f"recruits cost {spent} but {slot!r} has only "
                f"{bank_by_player[slot]} banked"
            )


def _validate_economy_orders(
    state: GameState,
    economy: WorldEconomy,
    slot: PlayerId,
    orders: EconomyOrders | None,
) -> Stocks:
    """Check a world player's base orders; return what the build order costs."""
    if orders is None:
        return Stocks()
    if orders.owner != slot:
        raise OrderValidationError(
            f"base orders for {orders.owner!r} are not owned by caller {slot!r}"
        )
    owned = {u.id for u in state.units if u.owner == slot}
    if len(set(orders.disband)) != len(orders.disband):
        raise OrderValidationError("a unit is listed twice in the disband pick")
    strangers = [i for i in orders.disband if i not in owned]
    if strangers:
        raise OrderValidationError(f"disband pick names units not yours: {strangers}")
    if orders.build is None:
        return Stocks()
    player = state.player(slot)
    work = player.construction
    if work is not None and work.completes_on > state.turn:
        raise OrderValidationError(
            f"{work.building} level {work.level} is still under construction"
        )
    level = player.level(orders.build)
    if work is not None and work.building is orders.build:
        level = work.level  # finishing this turn: the order builds the next one
    if level >= economy.max_level(orders.build):
        raise OrderValidationError(f"{orders.build} is already at its top level")
    cost = economy.buildings[orders.build][level].cost
    if not player.stocks.covers(cost):
        raise OrderValidationError(
            f"{orders.build} level {level + 1} costs {cost} but stocks are "
            f"{player.stocks}"
        )
    return cost


def _validate_world_recruitments(
    state: GameState,
    economy: WorldEconomy,
    slot: PlayerId,
    recruitments: list[Recruitment],
    spent: Stocks,
) -> None:
    """World recruits: the right kind of held site, affordable, under army size.

    Costs accumulate on top of any build order (``spent``) and are checked
    against the current stocks, as v0.1 checks the current bank.
    """
    if not recruitments:
        return
    player = state.player(slot)
    sites = state.map.site_index
    control_at = {s.coord: s.controlled_by for s in state.settlements}
    unit_count = sum(1 for u in state.units if u.owner == slot)
    cap = state.unit_cap_for(slot)
    claimed: set[Axial] = set()
    for recruit in recruitments:
        if recruit.owner != slot:
            raise OrderValidationError(
                f"recruit for {recruit.owner!r} is not owned by caller {slot!r}"
            )
        site = sites.get(recruit.coord)
        if site is None or site.kind not in WORLD_RECRUIT_SITES[recruit.type]:
            allowed = sorted(WORLD_RECRUIT_SITES[recruit.type])
            raise OrderValidationError(
                f"{recruit.type} recruits only at {allowed}, not {recruit.coord}"
            )
        needs = WORLD_RECRUIT_NEEDS.get(recruit.type)
        if needs is not None and player.level(needs) < 1:
            raise OrderValidationError(f"{recruit.type} needs a {needs}")
        if control_at.get(recruit.coord) != slot:
            raise OrderValidationError(f"{recruit.coord} is not held by {slot!r}")
        if recruit.coord in claimed:
            raise OrderValidationError(f"two recruits target {recruit.coord}")
        claimed.add(recruit.coord)
        unit_count += 1
        if unit_count > cap:
            raise OrderValidationError(f"recruiting exceeds the army size of {cap}")
        spent = spent + economy.recruit_cost[recruit.type]
        if not player.stocks.covers(spent):
            raise OrderValidationError(
                f"orders cost {spent} but stocks are {player.stocks}"
            )
