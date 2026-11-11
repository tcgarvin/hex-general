// The field manual (opened from the map keys' `?` and the account menu): the rules a
// new player needs, as short parchment pages behind folder tabs, plus the key list.
//
// Every number comes from the world itself where the client has it (the economy
// tables in the world's config, `state.config.settlement_repair`) or from the
// planner's mirrors of the engine (`UNIT_STATS`, `COLUMN_RULES`, `DEPLOY_DISTANCE`);
// the few the client has no other copy of are in `manualRules.ts`, checked against
// the engine's source by its test. The copy is plain second person, no em dashes.

import { useEffect, useRef, useState } from "react";

import { UNIT_STATS } from "@/planning/rules";

import { DEPLOY_DISTANCE } from "./contact";
import { RESOURCES, type Building, type Economy, type Stocks } from "./economy";
import { GARRISON_FORCES, MANUAL_RULES as R } from "./manualRules";
import { RECRUIT_NEEDS, RECRUIT_SITES } from "./plan";
import { COLUMN_RULES as C } from "./routing";
import { SHORTCUTS } from "./useWorldHotkeys";
import type { WorldView } from "./useWorld";
import type { SiteKind } from "./worldMap";

const MANUAL_SECTIONS = [
  { id: "turn", tab: "Turn", title: "The turn" },
  { id: "army", tab: "Army", title: "Your army" },
  { id: "orders", tab: "Orders", title: "Orders" },
  { id: "columns", tab: "Columns", title: "Columns and contact" },
  { id: "sites", tab: "Sites", title: "Sites and garrisons" },
  { id: "base", tab: "Base", title: "Your base and economy" },
  { id: "report", tab: "Report", title: "Reading the turn" },
  { id: "keys", tab: "Keys", title: "Keys" },
] as const;
export type ManualSection = (typeof MANUAL_SECTIONS)[number]["id"];

type UnitType = keyof typeof UNIT_STATS;
const UNIT_TYPES: readonly UnitType[] = ["infantry", "armor", "artillery"];
const UNIT_NAME: Record<UnitType, string> = { infantry: "Infantry", armor: "Armor", artillery: "Artillery" };
const SITE_NAME: Record<SiteKind, string> = {
  base: "Your base",
  city: "City",
  farm: "Farm",
  oil_field: "Oil field",
  steel_mill: "Steel mill",
  factory: "Factory",
};
const SITE_ORDER: readonly SiteKind[] = ["base", "city", "farm", "oil_field", "steel_mill", "factory"];
/** Sites with levels 1 to 3; cities and bases are always level 1. */
const LEVELED: ReadonlySet<SiteKind> = new Set(["farm", "oil_field", "steel_mill", "factory"]);
const BUILDING_NAME: Record<Building, string> = { barracks: "Barracks", depot: "Depot", workshop: "Workshop" };

/** "2 manpower, 6 steel" (`none` for nothing). */
function stocks(s: Stocks, none = "nothing"): string {
  const parts = RESOURCES.filter((r) => s[r] !== 0).map((r) => `${s[r]} ${r}`);
  return parts.length === 0 ? none : parts.join(", ");
}

function levelIncome(economy: Economy, kind: SiteKind, level: number): Stocks {
  const base = economy.siteIncome[kind];
  const extra = economy.levelIncome[kind];
  const n = level - 1;
  return { manpower: base.manpower + extra.manpower * n, fuel: base.fuel + extra.fuel * n, steel: base.steel + extra.steel * n };
}

function list(words: readonly string[]): string {
  return words.length <= 1 ? words.join("") : `${words.slice(0, -1).join(", ")} or ${words[words.length - 1]}`;
}

/** Where each unit type can be recruited, in words. */
function recruitWhere(type: UnitType): string {
  const sites = RECRUIT_SITES[type].map((k) => (k === "base" ? "your base" : `a ${SITE_NAME[k].toLowerCase()} you hold`));
  const need = RECRUIT_NEEDS[type];
  return `${list(sites)}${need === undefined ? "" : `, once you have a ${need}`}`;
}

export function FieldManual({
  view,
  economy,
  armySize,
  section,
  onSection,
  onClose,
}: {
  view: WorldView;
  economy: Economy;
  /** Your army size for this turn's recruits (the header's `/N`). */
  armySize: number;
  section: ManualSection;
  onSection: (section: ManualSection) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const pages = useRef<Partial<Record<ManualSection, HTMLElement | null>>>({});
  const [search, setSearch] = useState<{ query: string; hits: ManualSection[] }>({ query: "", hits: [] });
  // Focus the manual so its keys (Esc, Tab) act on it rather than the board.
  useEffect(() => dialog.current?.focus(), []);

  // Searching shows every page whose text has the words, in order.
  const find = (query: string) => {
    const words = query.toLowerCase().split(/\s+/).filter((w) => w !== "");
    const hits = MANUAL_SECTIONS.filter(({ id }) => {
      const text = pages.current[id]?.textContent?.toLowerCase() ?? "";
      return words.every((w) => text.includes(w));
    }).map((s) => s.id);
    setSearch({ query, hits });
  };
  const searching = search.query.trim() !== "";
  const shown = (id: ManualSection) => (searching ? search.hits.includes(id) : id === section);

  const ctx = { view, economy, armySize };
  return (
    <div
      className="pointer-events-auto absolute inset-0 z-30 flex items-center justify-center bg-black/35 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-label="Field manual"
        tabIndex={-1}
        onKeyDown={(e) => {
          if (e.key !== "Escape") return;
          e.stopPropagation();
          onClose();
        }}
        className="fhq-frame fhq-frame-lg flex h-[44rem] max-h-full w-[46rem] max-w-full flex-col px-2 pb-2 pt-1 text-sm outline-none"
      >
        <div className="mb-1.5 flex items-center gap-3">
          <h2 className="fhq-plate shrink-0 font-display text-sm uppercase tracking-wide">Field manual</h2>
          <input
            type="search"
            value={search.query}
            onChange={(e) => find(e.target.value)}
            placeholder="Search the manual"
            aria-label="Search the manual"
            className="ml-auto h-7 w-48 min-w-0 border border-wood-500 bg-ivory-100 px-2 text-xs text-ink-900 placeholder:text-ink-900/40 focus:outline-2 focus:outline-brass-600"
          />
          <button
            type="button"
            onClick={onClose}
            title="Close (Esc)"
            className="font-label text-xs uppercase text-wood-500 hover:text-ink-900 focus-visible:outline-2 focus-visible:outline-brass-600"
          >
            Close
          </button>
        </div>
        <div role="tablist" aria-label="Manual sections" className="flex flex-wrap items-end gap-0.5 px-1">
          {MANUAL_SECTIONS.map((s) => {
            const active = !searching && s.id === section;
            return (
              <button
                key={s.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => {
                  setSearch({ query: "", hits: [] });
                  onSection(s.id);
                }}
                className={`fhq-tab ${active ? "fhq-tab-active" : ""} font-label text-xs font-semibold uppercase tracking-wider`}
              >
                {s.tab}
              </button>
            );
          })}
        </div>
        <div className="fhq-page min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {searching && search.hits.length === 0 && <p className="text-wood-500">Nothing in the manual matches “{search.query.trim()}”.</p>}
          {MANUAL_SECTIONS.map((s) => (
            <section
              key={s.id}
              ref={(el) => {
                pages.current[s.id] = el;
              }}
              hidden={!shown(s.id)}
              aria-labelledby={`manual-${s.id}`}
              className="mb-4 last:mb-0"
            >
              <h3 id={`manual-${s.id}`} className="mb-1.5 font-display text-base uppercase tracking-wide">
                {s.title}
              </h3>
              <Page id={s.id} ctx={ctx} />
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

interface Ctx {
  view: WorldView;
  economy: Economy;
  armySize: number;
}

function Page({ id, ctx }: { id: ManualSection; ctx: Ctx }) {
  switch (id) {
    case "turn":
      return <TurnPage view={ctx.view} />;
    case "army":
      return <ArmyPage ctx={ctx} />;
    case "orders":
      return <OrdersPage />;
    case "columns":
      return <ColumnsPage />;
    case "sites":
      return <SitesPage economy={ctx.economy} />;
    case "base":
      return <BasePage ctx={ctx} />;
    case "report":
      return <ReportPage />;
    case "keys":
      return <KeysPage />;
  }
}

// --- building blocks -----------------------------------------------------------------

function P({ children }: { children: React.ReactNode }) {
  return <p className="mb-2 leading-snug">{children}</p>;
}

function H({ children }: { children: React.ReactNode }) {
  return <h4 className="mb-0.5 mt-3 font-label text-xs font-semibold uppercase tracking-wider text-wood-500">{children}</h4>;
}

/** A term and its explanation, for the order and rule lists. */
function Terms({ items }: { items: readonly (readonly [React.ReactNode, React.ReactNode])[] }) {
  return (
    <dl className="mb-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 leading-snug">
      {items.map(([term, text], k) => (
        <div key={k} className="contents">
          <dt className="font-label font-semibold uppercase tracking-wide whitespace-nowrap">{term}</dt>
          <dd>{text}</dd>
        </div>
      ))}
    </dl>
  );
}

function Table({ head, rows }: { head: readonly string[]; rows: readonly (readonly React.ReactNode[])[] }) {
  return (
    <div className="mb-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} className="px-1.5 py-0.5 text-left font-label text-[11px] font-semibold uppercase tracking-wider text-wood-500">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, k) => (
            <tr key={k} className="border-t border-wood-500/30">
              {row.map((cell, j) => (
                <td key={j} className={`px-1.5 py-1 align-top ${j === 0 ? "font-semibold" : ""}`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const N = ({ children }: { children: React.ReactNode }) => <span className="font-data">{children}</span>;

// --- pages --------------------------------------------------------------------------

function TurnPage({ view }: { view: WorldView }) {
  const next = view.next_tick_at ?? null;
  return (
    <>
      <P>
        Everyone plans at the same time, then the whole world resolves at once. Nothing moves while you plan.
      </P>
      <P>
        {view.clock === "twice_daily" ? (
          <>
            This world resolves at {R.tickTimes}
            {next !== null && (
              <>
                {" "}(next: <N>{new Date(next).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}</N>)
              </>
            )}
            . The header counts down to it.
          </>
        ) : (
          <>This world has no schedule: it resolves when its keeper runs the clock.</>
        )}
      </P>
      <P>
        Give your orders, then press <b>Submit plan</b> (Ctrl+Enter). You can change them and <b>Resubmit</b> as often as
        you like until the turn resolves; the last plan you sent is the one that runs. Unsent changes survive a reload of
        this tab, but only a submitted plan counts.
      </P>
      <P>A unit with no order stays where it is (without the Hold bonus), or carries on with its Travel order.</P>
      <H>What resolves, in order</H>
      <ol className="mb-2 list-decimal space-y-0.5 pl-5 leading-snug">
        <li>Barrages land.</li>
        <li>
          <N>{C.pulsesPerTurn}</N> movement pulses. Deployed units step one hex a pulse in the first few; columns keep
          stepping through all of them. Units fight the moment they meet.
        </li>
        <li>Sites change hands.</li>
        <li>Resting units repair.</li>
        <li>The economy: buildings finish, income and upkeep, shortages, storage limits, new buildings start.</li>
        <li>Recruits appear.</li>
      </ol>
      <H>Fighting</H>
      <P>
        Damage is the attacker's attack minus the defender's defence, minus terrain and Hold for a defender that stays
        put; never less than <N>{R.damageMin}</N> or more than <N>{R.damageMax}</N>. Both sides strike at once, and every
        attacker strikes in full, so ganging up works. An attacker takes the hex only if every defender in it is destroyed;
        nobody retreats. A unit fights at full strength down to its last HP.
      </P>
      <P>
        Every unit controls the six hexes around it. A unit that starts a pulse next to an enemy is engaged: it can attack
        that enemy, stay, or step back to a hex no enemy controls, but not slide along the enemy line.
      </P>
      <P>
        Stepping back is not a sure escape. Everyone steps on pulse <N>1</N> at once, and a unit whose hex is attacked
        that pulse is pinned: it stays and defends (with its terrain, but no Hold bonus, since it was ordered to move),
        and its own move or attack is cancelled for the turn. So an adjacent enemy that attacks this turn catches a unit
        stepping away; it gets away only if no enemy strikes its hex on that first pulse. Holding instead adds the Hold
        bonus.
      </P>
      <H>Terrain</H>
      <Table
        head={["Ground", "Defence", "Notes"]}
        rows={[
          ["Plains", <N>+{R.terrainDefense.plains}</N>, "Open ground."],
          ["Woods", <N>+{R.terrainDefense.woods}</N>, "Blocks sight: you see into woods, not through them."],
          ["Hills", <N>+{R.terrainDefense.hills}</N>, ""],
          ["Town, city, site", <N>+{R.settlementDefense}</N>, "Any site's hex counts. Resting here repairs faster."],
          ["Bridge", <N>+{R.terrainDefense.bridge}</N>, "The only way over a river."],
          ["River, lake", "", "Impassable."],
          [
            "Road",
            "",
            `A Move whose whole path runs on road gets +${R.roadBonus} hex. A column spends ${C.roadStepPulses} pulse a road step instead of ${C.offroadStepPulses}.`,
          ],
        ]}
      />
      <P>Terrain and Hold help only a unit standing still in its hex, never one moving.</P>
    </>
  );
}

function ArmyPage({ ctx }: { ctx: Ctx }) {
  const { economy, view } = ctx;
  const heal = view.state.config.settlement_repair;
  const role: Record<UnitType, string> = {
    infantry: "Cheap, steady line troops. Holds ground and captures sites.",
    armor: "Fastest and hardest hitting. Captures sites.",
    artillery: `Shells a hex up to ${UNIT_STATS.artillery.range} away before anyone moves. Weak up close; cannot capture.`,
  };
  return (
    <>
      <Table
        head={["Unit", "HP", "Move", "Attack", "Defence", "Range", "Sight", "What it is for"]}
        rows={UNIT_TYPES.map((t) => {
          const s = UNIT_STATS[t];
          return [
            UNIT_NAME[t],
            <N>{s.maxHp}</N>,
            <N>{s.move}</N>,
            <N>{s.attack}</N>,
            <N>{s.defense}</N>,
            <N>{s.range}</N>,
            <N>{s.vision}</N>,
            role[t],
          ];
        })}
      />
      <P>
        You see only what your units see: the hexes within their sight, but not through woods. Enemy units out of sight
        are simply not shown.
      </P>
      <H>Health</H>
      <P>
        A unit has up to <N>{UNIT_STATS.infantry.maxHp}</N> HP, shown as pips: amber at 6 and below, red at 3 and below. At 0
        it is destroyed.
      </P>
      <P>
        Rest to repair: <N>+{R.restAnywhere.heal}</N> HP a turn up to <N>{R.restAnywhere.ceiling}</N> anywhere, or{" "}
        <N>+{heal}</N> a turn up to full on a town, a city or any site's hex. A unit that loses HP during the turn does not
        repair that turn.
      </P>
      <H>Costs</H>
      <Table
        head={["Unit", "Recruit", "Upkeep a turn", "Recruited at"]}
        rows={UNIT_TYPES.map((t) => [UNIT_NAME[t], stocks(economy.recruitCost[t], "free"), stocks(economy.upkeep[t], "none"), recruitWhere(t)])}
      />
    </>
  );
}

function OrdersPage() {
  return (
    <>
      <P>Select a unit (click it, or Tab through your army), pick an order on its panel, then click the map.</P>
      <Terms
        items={[
          [
            "Travel",
            "Click any hex. The unit packs into a column and marches there over as many turns as it takes, along the route shown. Hover a hex first to see the arrival time. Choose Deploy or Stay packed for arrival. It keeps marching each turn until it arrives, runs into the enemy, or you give it a new order.",
          ],
          [
            "Move",
            `Deployed units only. Click a highlighted hex: an exact path this turn, one hex a pulse, up to the unit's move (+${R.roadBonus} if the whole path is road). If the path is blocked the unit stops where it is.`,
          ],
          [
            "Attack",
            "There is no attack order: infantry and armor attack by moving into an enemy's hex. Several units moving into the same hex all strike.",
          ],
          [
            "Barrage",
            `Artillery only. Click a hex within ${UNIT_STATS.artillery.range}: it is shelled before anything moves, hitting every unit in it. The gun does not move that turn. Barrage never captures.`,
          ],
          ["Rest", "Stay put and repair (see Your army)."],
          ["Hold", `Stay put and dig in: +${R.holdBonus} defence if attacked. A column on Hold stops where it is, still packed.`],
          ["Deploy", "Columns only. Unpack where it stands at the end of the turn, in good order, ready to fight next turn."],
          ["Clear", "Drops the unit's order for this turn (X or Delete)."],
        ]}
      />
      <H>Relief swaps</H>
      <P>
        Two of your deployed units next to each other can trade places on the first pulse: Shift+click the neighbour, or
        use Swap on the unit panel. This is how a fresh unit relieves a worn one on the line. Each still obeys enemy
        control: an engaged unit cannot step into another hex the enemy controls.
      </P>
      <H>Disband list</H>
      <P>
        Tick “Disband first in a shortage” on a unit's panel to choose who goes if you run short (see Your base and
        economy).
      </P>
    </>
  );
}

function ColumnsPage() {
  const packTurnRoad = (C.pulsesPerTurn - C.packPulses) / C.roadStepPulses;
  const packTurnOff = (C.pulsesPerTurn - C.packPulses) / C.offroadStepPulses;
  return (
    <>
      <P>A column is a unit packed for the road: fast, but blind and fragile. Travel orders use columns.</P>
      <Terms
        items={[
          [
            "Packing",
            `A deployed unit given Travel packs during the first ${C.packPulses} pulses, then marches. A unit that starts the turn next to an enemy cannot pack: step it away first.`,
          ],
          [
            "Speed",
            `A turn has ${C.pulsesPerTurn} pulses. A road step takes ${C.roadStepPulses}, any other step ${C.offroadStepPulses}: ${C.pulsesPerTurn / C.roadStepPulses} road hexes a turn or ${C.pulsesPerTurn / C.offroadStepPulses} off road (${packTurnRoad} or ${packTurnOff} on the turn it packs).`,
          ],
          [
            "Stacking",
            `Up to ${R.stackCap} of your columns can share a hex, plus one deployed unit. A column that would enter a full hex waits.`,
          ],
          [
            "Weak spots",
            `Columns cannot attack or capture and control no hexes. They cannot enter a hex next to an enemy. They defend at −${R.columnDefensePenalty} with no terrain or Hold bonus, and a barrage hits every column in the hex.`,
          ],
        ]}
      />
      <H>Contact</H>
      <P>
        A column halts and deploys when its next step would bring it within <N>{DEPLOY_DISTANCE}</N> hexes of a hostile
        unit (even one hidden in woods), when an enemy comes within <N>{DEPLOY_DISTANCE}</N> of it, or when it is attacked
        or shelled. Neutral garrisons count. While you plan a Travel order, the hover note warns when the route runs into
        contact.
      </P>
      <P>
        A column that deploys on contact is <b>disordered</b> for the next turn: −<N>{R.disorderDefensePenalty}</N>{" "}
        defence, and it cannot attack. Deploying by choice (the Deploy order, or Deploy on arrival) is clean. Scout ahead
        or stop short to arrive in order.
      </P>
      <P>
        A deploying column takes its own hex if none of your deployed units is there, otherwise the nearest free hex
        within <N>{R.spillRadius}</N> that no enemy controls. With no room it stays packed and exposed. Deploying ends its
        move and its Travel order.
      </P>
    </>
  );
}

function SitesPage({ economy }: { economy: Economy }) {
  // "2 infantry and 1 artillery"; a below-strength unit is militia.
  const tiers = Object.entries(GARRISON_FORCES).map(([tier, units]) => {
    const counts = new Map<string, number>();
    for (const [type, hp] of units) {
      const name = hp < UNIT_STATS[type].maxHp ? `${type} at ${hp} HP (militia)` : type;
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    const words = [...counts].map(([name, n]) => `${n} ${name}`);
    return [`Tier ${tier}`, words.join(" and ")] as const;
  });
  return (
    <>
      <P>Sites are what you fight over: each one you hold pays you every turn. Farms, oil fields, steel mills and factories come in levels 1 to 3 (higher levels are rarer).</P>
      <Table
        head={["Site", "Level 1", "Level 2", "Level 3", "Recruits"]}
        rows={SITE_ORDER.map((kind) => {
          const recruits = UNIT_TYPES.filter((t) => RECRUIT_SITES[t].includes(kind)).map((t) => UNIT_NAME[t]);
          const at = (level: number) => stocks(levelIncome(economy, kind, level));
          return [
            SITE_NAME[kind],
            at(1),
            LEVELED.has(kind) ? at(2) : "",
            LEVELED.has(kind) ? at(3) : "",
            recruits.length === 0 ? "" : recruits.join(", "),
          ];
        })}
      />
      <H>Garrisons</H>
      <P>
        Neutral sites start with a garrison, tougher farther from the bases and one tier tougher on a level 3 site. The
        first unit stands on the site, the rest beside it. Garrisons always hold, never move, and are not replaced once
        beaten. A site's panel says what holds it.
      </P>
      <Terms items={tiers} />
      <H>Capturing</H>
      <P>
        End the turn with deployed infantry or armor on the site's hex, with every defender there destroyed. Artillery and
        columns cannot capture. A site you take pays you that same turn. Enemy sites fall the same way; a base falls when
        its hex is taken. A vacant base site cannot be captured.
      </P>
      <P>
        Every <N>{economy.army.sites_per_point}</N> sites you hold (your base does not count) add 1 to your army size.
      </P>
    </>
  );
}

function BasePage({ ctx }: { ctx: Ctx }) {
  const { economy, armySize } = ctx;
  const a = economy.army;
  const fuelBurners = UNIT_TYPES.filter((t) => economy.upkeep[t].fuel > 0).map((t) => UNIT_NAME[t].toLowerCase());
  const storage = economy.storage;
  const unlocks = (b: Building) =>
    UNIT_TYPES.filter((t) => RECRUIT_NEEDS[t] === b)
      .map((t) => UNIT_NAME[t].toLowerCase())
      .join(" and ");
  const effect: Record<Building, string> = {
    barracks: `+${a.per_barracks} army size a level.`,
    depot: `Storage ${storage.slice(1).join(", then ")} a resource.`,
    workshop: `Lets you recruit ${unlocks("workshop")}.`,
  };
  return (
    <>
      <P>
        You run on three resources: <b>manpower</b>, <b>fuel</b> and <b>steel</b>. Each turn your sites pay their income
        and your units cost their upkeep. You can store <N>{storage[0]}</N> of each (more with a depot); anything above
        that is lost. The header shows your stocks, the limit and next turn's change.
      </P>
      <H>Buildings</H>
      <P>
        One building at a time, at your base. Order it on the Base buildings panel: it starts when the turn resolves,
        paid then, and is cancelled (nothing paid) if you cannot cover it by then.
      </P>
      <Table
        head={["Building", "Each level: cost, turns", "Effect"]}
        rows={(Object.keys(BUILDING_NAME) as Building[]).map((b) => [
          BUILDING_NAME[b],
          <span className="font-data">
            {economy.buildings[b].map((l, k) => (
              <span key={k} className="block">
                {k + 1}: {stocks(l.cost, "free")}, {l.turns} turns
              </span>
            ))}
          </span>,
          effect[b],
        ])}
      />
      <H>Army size</H>
      <P>
        You can field <N>{a.base}</N> units, plus <N>{a.per_barracks}</N> per barracks level, plus 1 for every{" "}
        <N>{a.sites_per_point}</N> sites you hold, up to <N>{a.ceiling}</N>. Yours is <N>{armySize}</N> this turn. Recruiting
        stops at it. A building finishes before recruits appear, so a barracks level due this turn already counts for
        this turn's recruits (and a workshop due this turn already lets you recruit guns).
      </P>
      <H>Recruiting</H>
      <Terms items={UNIT_TYPES.map((t) => [UNIT_NAME[t], `At ${recruitWhere(t)}, for ${stocks(economy.recruitCost[t], "nothing")}.`] as const)} />
      <P>
        Choose a recruit on the site's panel. The new unit appears at full HP when the turn resolves, paid then, if the site
        is still yours, no deployed unit stands on it, and your army is below its size.
      </P>
      <H>Shortages</H>
      <P>
        If upkeep would take fuel or manpower below zero, one unit disbands for each resource that runs short: for fuel, a
        unit that burns it ({list(fuelBurners)}); for manpower, any unit. Units on your disband list go first, otherwise
        the weakest (lowest HP, then the newest). The header warns you a turn ahead.
      </P>
      <P>Lose every site and every unit and you are out of the world.</P>
    </>
  );
}

function ReportPage() {
  return (
    <>
      <P>
        When a turn resolves, a banner drops in and the turn report opens on the left: your fights, then fighting, your
        base, movement, and what else you saw. Click a line to move the map there. While the report is open, trails show
        where units went.
      </P>
      <P>
        <b>Play</b> on the report (or <b>Watch</b> on the banner) replays the turn on the board, pulse by pulse. Space plays
        or pauses, ← and → step, the speed key cycles speeds, and the bar marks the fights (red for yours). Click a report
        line or a fight to jump to it. Moving the camera stops it following your units; Follow brings that back. Exit or
        Esc returns to planning.
      </P>
      <P>
        Hover any hex to see what is there. While Travel is armed, the hover note shows the arrival time and warns about
        contact on the way.
      </P>
    </>
  );
}

function KeysPage() {
  return (
    <>
      <dl className="mb-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
        {SHORTCUTS.map((s, k) => (
          <div key={k} className="contents">
            <dt className="font-label font-semibold whitespace-nowrap">{s.keys}</dt>
            <dd>{s.does}</dd>
          </div>
        ))}
      </dl>
      <P>Drag the map or use the minimap to move; scroll or pinch to zoom.</P>
    </>
  );
}
