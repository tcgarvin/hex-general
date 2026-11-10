"""World economy phase: stocks, shortages and construction (economy draft §02, §05).

Replaces the v0.1 muster phase (`app.engine.economy`) in a world game, at the
same point in the turn (after capture, before recruitment). For each player, in
roster order:

1. **Construction** finishing this turn completes: the building gains its level.
2. **Income and upkeep:** each held site earns its kind's income at its level
   (`WorldEconomy.income`); each unit costs its type's upkeep.
3. **Shortages:** if fuel would go below zero, one fuel-burning unit disbands; if
   manpower would, one unit disbands. The player's ``disband`` pick goes first;
   otherwise the weakest qualifying unit (lowest HP, then newest). One unit per
   resource per turn, so a shortage fixed quickly costs one unit, not an arm.
4. **Storage:** each resource is floored at 0 and limited by the depot level.
5. **Build order:** the next level of the ordered building starts, paid now, if
   the stocks cover it; otherwise it is voided and nothing is paid. Building
   while another level is under construction, or past the top level, is a static
   violation that validation rejects, so the engine raises on it.

Pure: copies the state, mutates nothing else, performs no I/O.
"""

from __future__ import annotations

from typing import Literal

from app.engine.economy_rules import Building, Stocks, WorldEconomy
from app.engine.events import (
    ConstructionCompleted,
    ConstructionStarted,
    ConstructionVoided,
    ConstructionVoidedReason,
    Event,
    StocksResolved,
    UnitDisbanded,
)
from app.engine.models import (
    Construction,
    EconomyOrders,
    GameState,
    Player,
    PlayerId,
    Unit,
)
from app.engine.movement import EngineError


def resolve_world_economy(
    state: GameState, economy_orders: list[EconomyOrders]
) -> tuple[GameState, list[Event]]:
    """Run the world economy phase for every player (see the module docstring)."""
    economy = state.config.economy
    if economy is None:
        raise EngineError("resolve_world_economy needs a world economy config")
    new_state = state.fork()
    by_owner = _orders_by_owner(new_state, economy_orders)
    sites = new_state.map.site_index

    income: dict[PlayerId, Stocks] = {p.id: Stocks() for p in new_state.players}
    for settlement in new_state.settlements:
        if settlement.controlled_by is not None:
            site = sites[settlement.coord]
            income[settlement.controlled_by] += economy.income(site.kind, site.level)

    events: list[Event] = []
    for player in new_state.players:
        orders = by_owner.get(player.id, EconomyOrders(owner=player.id))
        events.extend(_complete_construction(player, new_state.turn))
        events.extend(
            _settle_stocks(new_state, economy, player, income[player.id], orders)
        )
        events.extend(_start_construction(economy, player, orders, new_state.turn))
    return new_state, events


def _orders_by_owner(
    state: GameState, economy_orders: list[EconomyOrders]
) -> dict[PlayerId, EconomyOrders]:
    known = {p.id for p in state.players}
    by_owner: dict[PlayerId, EconomyOrders] = {}
    for orders in economy_orders:
        if orders.owner not in known:
            raise EngineError(f"economy orders for unknown player {orders.owner!r}")
        if orders.owner in by_owner:
            raise EngineError(f"two sets of economy orders for {orders.owner!r}")
        by_owner[orders.owner] = orders
    return by_owner


def _complete_construction(player: Player, turn: int) -> list[Event]:
    work = player.construction
    if work is None or work.completes_on > turn:
        return []
    player.buildings = {**player.buildings, work.building: work.level}
    player.construction = None
    return [
        ConstructionCompleted(
            player=player.id, building=work.building, level=work.level
        )
    ]


def _settle_stocks(
    state: GameState,
    economy: WorldEconomy,
    player: Player,
    income: Stocks,
    orders: EconomyOrders,
) -> list[Event]:
    owned = [u for u in state.units if u.owner == player.id]
    upkeep = Stocks()
    for unit in owned:
        upkeep += economy.upkeep[unit.type]
    before = player.stocks
    raw = before + income - upkeep

    events: list[Event] = []
    gone: set[int] = set()
    if raw.fuel < 0:
        fuel_units = [u for u in owned if economy.uses_fuel(u.type)]
        events.append(_disband(state, fuel_units, orders, "fuel", gone))
    if raw.manpower < 0:
        candidates = [u for u in owned if u.id not in gone]
        if candidates:
            events.append(_disband(state, candidates, orders, "manpower", gone))

    limit = economy.storage_limit(player.level(Building.DEPOT))
    after = raw.clamped(limit)
    lost = Stocks(
        manpower=max(0, raw.manpower - limit),
        fuel=max(0, raw.fuel - limit),
        steel=max(0, raw.steel - limit),
    )
    player.stocks = after
    events.insert(
        0,
        StocksResolved(
            player=player.id,
            income=income,
            upkeep=upkeep,
            before=before,
            after=after,
            lost_to_storage=lost,
        ),
    )
    return events


def _disband(
    state: GameState,
    candidates: list[Unit],
    orders: EconomyOrders,
    shortage: Literal["fuel", "manpower"],
    gone: set[int],
) -> UnitDisbanded:
    """Remove the player's first qualifying pick, else the weakest candidate."""
    by_id = {u.id: u for u in candidates}
    picked = next((by_id[i] for i in orders.disband if i in by_id), None)
    victim = picked or min(candidates, key=lambda u: (u.hp, -u.id))
    gone.add(victim.id)
    state.units = [u for u in state.units if u.id != victim.id]
    state.standing_orders = [o for o in state.standing_orders if o.unit_id != victim.id]
    return UnitDisbanded(
        unit_id=victim.id,
        unit_type=victim.type,
        owner=victim.owner,
        coord=victim.coord,
        shortage=shortage,
        chosen=picked is not None,
    )


def _start_construction(
    economy: WorldEconomy, player: Player, orders: EconomyOrders, turn: int
) -> list[Event]:
    building = orders.build
    if building is None:
        return []
    if player.construction is not None:
        raise EngineError(
            f"{player.id!r} ordered {building} while {player.construction.building} "
            "is under construction"
        )
    level = player.level(building) + 1
    if level > economy.max_level(building):
        raise EngineError(f"{player.id!r} {building} is already at its top level")
    spec = economy.buildings[building][level - 1]
    if not player.stocks.covers(spec.cost):
        return [
            ConstructionVoided(
                player=player.id,
                building=building,
                reason=ConstructionVoidedReason.INSUFFICIENT_STOCKS,
            )
        ]
    player.stocks = player.stocks - spec.cost
    player.construction = Construction(
        building=building, level=level, completes_on=turn + spec.turns
    )
    return [
        ConstructionStarted(
            player=player.id,
            building=building,
            level=level,
            completes_on=turn + spec.turns,
            cost=spec.cost,
        )
    ]
