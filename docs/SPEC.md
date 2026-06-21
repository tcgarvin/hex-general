# Hex Wargame v0.1 Design Spec

## 1. High-Level Concept

This is a prototype for a turn-based operational hex wargame inspired by *Panzer General* / *Civil War General*, with one major twist:

> Players issue orders during a planning phase, then all orders execute simultaneously.

The first version is deliberately small and concrete. It focuses on whether simultaneous planned movement, hex tactics, fog of war, terrain, and simple unit economics produce interesting battlefield situations.

This version does **not** include doctrine, bots, supply, readiness, commanders, factories, barracks, diplomacy, alliances, or large-scale delegation.

The goal of v0.1 is to answer one question:

> Is it fun to plan concrete unit orders, lock them in, and watch simultaneous execution create surprising but understandable tactical outcomes?

---

## 2. Design Pillars

### 2.1 Concrete Orders

Players directly command individual units. Orders are simple and explicit.

No doctrine. No AI sub-commanders. No intent language. No automation beyond deterministic execution of orders.

### 2.2 Simultaneous Execution

All players plan first. Then all orders resolve together.

The player should feel like they are writing a battlefield plan into the future, not reacting click-by-click.

### 2.3 Hex-Based Tactics

The game uses a hex map to support maneuver, flanking, blocking, terrain control, zones of control, roads, chokepoints, woods blocking vision, and bridges.

### 2.4 Simple Logistics

The first version uses **muster** as a simple economic system.

Muster is used to recruit units and is also consumed by upkeep. There is no supply system yet.

---

## 3. Map

### 3.1 Map Size

Initial prototype map:

* **40 × 40 hex grid**

The map should contain:

* plains
* woods
* hills
* river hexes
* bridge hexes
* roads
* towns
* cities

### 3.2 Settlements

There are two settlement sizes:

| Settlement | Muster Income | Defense Bonus |       Repair Bonus |
| ---------- | ------------: | ------------: | -----------------: |
| Town       |            +2 |            +2 | +2 HP when resting |
| City       |            +3 |            +2 | +2 HP when resting |

Cities and towns have the same defensive and repair behavior. Cities are more economically valuable.

There are no villages in v0.1.

### 3.3 Roads

Roads connect some towns and cities.

Prototype road rule:

> If a unit's entire movement path for the turn follows connected road hexes, it receives +1 movement for that turn.

### 3.4 Rivers

Rivers are represented as **hex terrain**, not hex-edge terrain.

A river hex represents a river crossing zone, riverbank, floodplain, or river obstacle, not necessarily literal water occupying the whole hex.

Prototype river rules:

* River hexes are impassable.
* Bridge hexes are passable river hexes.
* Roads crossing rivers create bridge hexes.
* Bridge hexes have no special defense bonus in v0.1.

---

## 4. Terrain

### 4.1 Terrain Types

| Terrain | Notes                                          |
| ------- | ---------------------------------------------- |
| Plains  | Default open terrain                           |
| Woods   | Blocks line of sight; defensive terrain        |
| Hills   | Defensive terrain; may later modify visibility |
| Town    | Settlement; income and repair                  |
| City    | Settlement; income and repair                  |
| River   | Impassable unless bridge                       |
| Bridge  | Passable river crossing                        |
| Road    | Movement modifier layered onto terrain         |

### 4.2 Terrain Defense

Terrain defense applies only to **stationary defenders**.

A unit that is moving out of a hex does not receive the defensive benefit of that hex.

| Terrain | Defense Bonus |
| ------- | ------------: |
| Plains  |             0 |
| Woods   |            +2 |
| Hills   |            +1 |
| Town    |            +2 |
| City    |            +2 |
| Bridge  |             0 |

---

## 5. Units

### 5.1 Unit Types

Initial unit set:

* Infantry
* Armor
* Artillery

No recon, engineers, aircraft, naval units, commanders, elite units, supply units, or factories in v0.1.

### 5.2 Unit Health

All units have:

* **10 HP maximum**

HP represents unit strength/steps.

### 5.3 Prototype Unit Stats

| Unit      | HP | Move | Attack | Defense | Range | Recruit Cost | Upkeep |
| --------- | -: | ---: | -----: | ------: | ----: | -----------: | -----: |
| Infantry  | 10 |    2 |      4 |       5 |     1 |            3 |      1 |
| Armor     | 10 |    4 |      7 |       4 |     1 |            6 |      2 |
| Artillery | 10 |    1 |      5 |       2 |     3 |            5 |      2 |

These numbers are prototype defaults and should be tuned after playtesting.

### 5.4 Unit Roles

#### Infantry

Infantry is slow and durable. It is good at holding terrain and towns.

Infantry may move, but it may only intentionally attack an enemy if that enemy is adjacent at the start of execution.

#### Armor

Armor is fast and offensively strong. It can move into enemy contact as part of its movement path.

Armor may move and attack in the same turn because movement into enemy contact creates combat.

#### Artillery

Artillery is slow, weak in direct combat, and attacks at range.

Artillery may either move or barrage. It may not move and barrage in the same turn.

Artillery cannot capture towns or cities.

---

## 6. Unit Cap and Muster Economy

### 6.1 Unit Cap

Each player has a hard unit cap:

* **20 units maximum**

This cap is not directly tied to towns, cities, or land control in v0.1.

### 6.2 Muster Income

| Controlled Hex | Muster Income |
| -------------- | ------------: |
| Town           |            +2 |
| City           |            +3 |

### 6.3 Upkeep

| Unit      | Upkeep |
| --------- | -----: |
| Infantry  |      1 |
| Armor     |      2 |
| Artillery |      2 |

Economy calculation:

```text
net muster = gross settlement income - unit upkeep
```

If net muster is positive, add it to the player's bank.

If upkeep exceeds income, the shortfall is paid from the bank.

The bank cannot go below zero.

There is no additional penalty for running out of muster in v0.1.

### 6.4 Muster Bank Cap

Each player has a muster bank cap:

* **20 muster maximum**

Muster above the cap is lost.

### 6.5 Recruitment

| Unit      | Recruit Cost |
| --------- | -----------: |
| Infantry  |            3 |
| Armor     |            6 |
| Artillery |            5 |

New units may be deployed at friendly cities.

Town-based deployment, barracks, factories, and unit unlocks are deferred.

---

## 7. Orders

Each unit receives one order during the planning phase.

| Order   | Applies To                 | Effect                                                                    |
| ------- | -------------------------- | ------------------------------------------------------------------------- |
| Move    | Infantry, Armor, Artillery | Follow an exact planned hex path. Combat occurs if enemy contact happens. |
| Barrage | Artillery                  | Fire at a visible target hex within range.                                |
| Rest    | All units                  | Do not move or attack; repair HP if not damaged during execution.         |
| Hold    | All units                  | Do not move; gain defensive bonus if attacked.                            |

There is no separate Attack order. For infantry and armor, attacking is a consequence of movement/contact.

### 7.1 Move Order

A Move order consists of an exact path of adjacent hexes.

Example:

```text
Unit A path: H12 -> H13 -> H14 -> H15
```

The unit attempts to move one hex per movement pulse.

If the path becomes blocked, the unit does not reroute automatically.

### 7.2 Barrage Order

A Barrage order targets a visible hex within artillery range.

* Barrage targets a hex, not a unit.
* Barrage resolves before movement pulses.
* Barrage affects the unit currently in the target hex, if any.
* Artillery that barrages cannot move that turn.
* Barrage cannot capture territory.

### 7.3 Rest Order

A Resting unit does not move or attack.

| Rest Location |                 Repair |
| ------------- | ---------------------: |
| Anywhere      |  +1 HP, up to max 7 HP |
| Town or City  | +2 HP, up to max 10 HP |

If a resting unit is attacked and takes damage, it does not repair that turn.

A resting unit fights normally if attacked, but it does not receive the Hold bonus.

### 7.4 Hold Order

A Holding unit does not move.

If attacked, it receives:

* **+1 defense bonus**

Hold does not repair HP.

---

## 8. Stacking and Zones of Control

### 8.1 Stacking

There is no stacking.

> Only one unit may occupy a hex at the end of execution.

### 8.2 Friendly Traffic Jams

If multiple friendly units attempt to enter the same hex during the same movement pulse:

* no combat occurs
* none of those units enter the hex
* all involved units remain in their previous hexes
* all involved units stop moving for the turn

### 8.3 Zones of Control

Enemy units exert a zone of control into adjacent hexes.

Prototype ZOC rule:

> A unit entering an enemy ZOC must stop moving.

A unit may attack into an enemy-occupied hex if otherwise allowed.

---

## 9. Fog of War and Visibility

### 9.1 Basic Visibility

Visibility is binary.

> If a hex is visible, the player sees what is in it. If a hex is not visible, the player does not.

No separate concealment state exists in v0.1.

### 9.2 Woods

Woods block line of sight.

Do not add special rules for hidden units inside woods yet. Either the hex is visible or it is not.

### 9.3 Prototype Vision Values

| Unit      | Vision |
| --------- | -----: |
| Infantry  |      2 |
| Armor     |      2 |
| Artillery |      2 |

Dedicated recon units are deferred.

---

## 10. Turn Structure

Each turn has the following phases:

1. Planning Phase
2. Order Lock
3. Execution Phase
4. Capture Phase
5. Repair Phase
6. Economy Phase
7. Recruitment Phase
8. Review / Replay

A turn does not need to represent a specific amount of real-world time.

It is simply one operational step.

---

## 11. Execution Model

### 11.1 Overview

Execution is simultaneous and deterministic.

Movement resolves in discrete **movement pulses**.

If the fastest unit has 4 movement, execution has up to 4 movement pulses.

```text
Pulse 1: all units with at least 1 movement attempt their first path step
Pulse 2: all units with at least 2 movement attempt their second path step
Pulse 3: units with at least 3 movement attempt their third path step
Pulse 4: units with at least 4 movement attempt their fourth path step
```

### 11.2 Combat Ends Movement

If a unit enters combat during a movement pulse, its movement ends for the turn.

Even if the unit destroys its opponent, it does not continue moving after that combat.

### 11.3 Execution Sequence

#### Phase 1: Barrage

All artillery Barrage orders resolve simultaneously.

* Barrage targets a hex.
* If a unit occupies that hex at the start of execution, it may take damage.
* Barrage occurs before movement.

#### Phase 2: Movement Pulses

For each movement pulse:

1. Identify all units with remaining path steps.
2. Each eligible unit attempts to move one hex along its path.
3. Invalid moves fail and end movement.
4. Friendly conflicts become traffic jams.
5. Enemy conflicts become combat events.
6. Units that successfully move may continue in the next pulse.
7. Units that fight, collide, or are blocked stop moving for the turn.

#### Phase 3: Capture

After movement and combat are complete, infantry and armor units capture any town or city they occupy.

Artillery cannot capture.

#### Phase 4: Repair

Resting units repair if eligible.

#### Phase 5: Economy

Muster income and upkeep are calculated.

#### Phase 6: Recruitment

Players may recruit units if they have muster and are below the unit cap.

---

## 12. Combat Events

### 12.1 When Combat Occurs

Combat occurs when opposing units contest a hex or edge during execution.

Examples:

* A unit moves into an enemy-occupied hex.
* Enemy units attempt to enter the same empty hex during the same pulse.
* Enemy units attempt to swap hexes.
* A moving unit is blocked by an enemy unit in its path.

### 12.2 Simultaneous Damage

Combat damage is simultaneous.

All participating units deal damage before any destroyed units are removed.

Both attackers and defenders can take damage.

### 12.3 No Retreat

There is no retreat in v0.1.

Units either survive in place, fail to enter contested space, or are destroyed.

### 12.4 Multi-Unit Attacks

Multiple units may attack the same defender.

Each attacking unit applies its full attack value.

There is no reduced support-only attack in v0.1.

This is intentional and follows the *Panzer General* / *Civil War General* pattern: a position should be defended by a network of units, not by a single isolated unit expected to survive a dogpile.

### 12.5 Occupying a Contested Hex

Combat can damage or destroy units, but movement into contested space only succeeds if the contest is clearly resolved.

Default rule:

> A unit may enter a contested hex only if all opposition contesting that hex is destroyed and exactly one eligible entrant from the surviving side is designated to occupy it.

If opposing survivors remain, nobody newly enters the hex.

If multiple friendly entrants survive and the enemy is destroyed, the designated lead entrant enters if it survived. If the lead entrant is destroyed, no fallback entrant enters in v0.1.

### 12.6 Lead Entrant

When multiple friendly units are ordered into the same enemy-occupied or potentially contested hex, one may be designated as the **lead entrant**.

The lead entrant is the only unit eligible to occupy the hex after combat.

If no lead is selected, the game chooses deterministically using a fixed tie-breaker:

1. Armor before Infantry before Artillery
2. Higher current HP
3. Lower unit ID

### 12.7 Friendly Collision

If only friendly units contest a hex, there is no combat.

All involved units fail to move and stop for the turn.

### 12.8 Multi-Side Contests

If units from more than two sides attempt to enter or occupy the same hex:

1. All involved units participate in a single combat event.
2. Damage is resolved simultaneously.
3. If exactly one side has surviving eligible claimants and a lead entrant survives, that unit enters.
4. If multiple sides have survivors, no one enters and survivors remain in their previous hexes.
5. If all units are destroyed, the hex remains empty.

### 12.9 Edge Swaps

If opposing units attempt to swap hexes across the same edge during the same pulse:

* they fight an edge clash
* both units stop moving
* if both survive, both remain in their original hexes
* if one is destroyed and the other survives, the survivor completes the move
* if both are destroyed, both hexes become empty

### 12.10 Terrain in Combat

Terrain defense applies only to stationary defenders occupying the contested hex.

Examples:

* A holding infantry unit in woods is attacked: woods defense applies.
* A unit moving out of woods collides with an enemy: woods defense does not apply.
* Two units move into the same empty plains hex: no terrain defense applies.
* Two units move into the same empty town hex: no stationary defender exists, so no town defense applies.

---

## 13. Combat Formula

The first implementation should use deterministic combat.

Randomness is deferred.

### 13.1 Prototype Formula

```text
raw damage = attacker attack - defender defense - terrain/hold modifiers
final damage = clamp(raw damage, 1, 5)
```

Where:

* terrain modifiers apply only to stationary defenders
* Hold gives +1 defense
* damage is applied simultaneously
* units at 0 HP are destroyed

This is intentionally simple and tunable.

### 13.2 Optional HP Scaling

If damaged units should fight less effectively, use HP scaling:

```text
effective attack = base attack × current HP / 10
effective defense = base defense × current HP / 10
```

Round as needed.

This should be tested. It may make damaged units too fragile, but it gives HP meaningful gameplay weight.

---

## 14. Capture and Control

### 14.1 Capturing Settlements

A town or city is captured when an infantry or armor unit occupies it at the end of execution.

Artillery cannot capture.

### 14.2 No Intrinsic Garrisons

Towns and cities do not have intrinsic defending units.

A settlement only resists capture if an actual unit occupies or contests it.

---

## 15. Victory Conditions

Victory conditions are scenario-defined.

Recommended v0.1 defaults:

### 15.1 City Elimination

A player loses if they control zero cities at the end of a turn.

### 15.2 Turn Limit Victory

If the turn limit is reached, score controlled settlements:

| Settlement | Victory Points |
| ---------- | -------------: |
| Town       |              1 |
| City       |              3 |

Highest score wins.

There is no HQ or capital city in v0.1.

---

## 16. User Experience Requirements

### 16.1 Planning

The player must be able to:

* select a unit
* see movement range
* assign an exact path
* assign Barrage, Rest, or Hold
* see predicted movement pulses
* see likely traffic conflicts with own units
* review all orders before locking

### 16.2 Execution Replay

After execution, the game should show a replay or step-through log.

The replay should make clear:

* where each unit tried to move
* where movement was blocked
* where traffic jams occurred
* where combat occurred
* which units took damage
* which units were destroyed
* which towns/cities changed control
* how much muster was gained/spent/lost to upkeep

### 16.3 Combat Explanation

Combat results should include concise explanations, such as:

```text
Blue Armor attacked Red Infantry in Woods.
Red Infantry received +2 terrain defense.
Blue Armor dealt 2 damage.
Red Infantry dealt 1 damage.
Both units survived; Blue Armor did not enter the hex.
```

---

## 17. Explicitly Out of Scope for v0.1

The following are intentionally excluded:

* supply
* readiness
* fuel
* ammo
* logistics units
* barracks
* factories
* villages
* commanders
* doctrine
* bots
* alliances
* diplomacy
* fog-of-war concealment inside visible woods
* recon units
* engineers
* air units
* naval units
* unit upgrades
* experience levels
* random combat results
* retreats
* overrun/continuing movement after combat
* stacking
* simultaneous multi-turn queued orders
* asynchronous live-service timers
* pay-to-win progression

---

## 18. Prototype Success Criteria

The v0.1 prototype is successful if it produces situations like:

* a player feints along a road while armor moves around a flank
* woods block visibility and create uncertainty
* bridges become natural chokepoints
* careless path planning creates traffic jams
* artillery punishes predictable static positions
* cities and towns matter without creating a logistics spreadsheet
* a player can take a competent turn quickly even while controlling many units
* simultaneous resolution creates surprises that feel fair after replay

The design should not reward constant logins, excessive micromanagement, or accounting-heavy optimization.

---

## 19. Summary of Locked v0.1 Defaults

```text
Map: 40x40 hex grid
Players: initially 2
Settlements: towns and cities only
Units: infantry, armor, artillery
HP: 10 per unit
Unit cap: 20
Muster bank cap: 20
Town: +2 muster, +2 defense, +2 repair when resting
City: +3 muster, +2 defense, +2 repair when resting
No villages
No supply
No readiness
No stacking
No retreat
No randomness initially
Orders: Move, Barrage, Rest, Hold
Movement: exact path, resolved in simultaneous pulses
Combat: simultaneous damage
Multi-unit attacks: full attack from each participating unit
Occupation: only if opposition is destroyed and lead entrant survives
Rivers: hex terrain, impassable except bridge hexes
Victory: scenario-defined; default city elimination or VP at turn limit
```
