// The phone's Base sheet (stocks in words with storage and net, buildings, and
// recruiting at the base) and the hex sheet: what the desktop hex panel shows for
// a tapped hex without one of your units (site, militia, owner, recruiting there).

import { axialEqual, hexKey, type Axial } from "@/board/hex";
import { Glyph } from "@/components/Glyph";
import type { components } from "@/lib/api/schema";

import type { Designate } from "../designations";
import { levelOf, RESOURCES, siteIncome, stocksOf, type Building, type Economy, type Forecast } from "../economy";
import { BUILDING_LABEL, RESOURCE_LABEL, siteTitle, stocksText, townStatus, UNIT_LABEL } from "../labels";
import { neutralStatus } from "../hexInfo";
import { isMilitia, militiaName, startingHp } from "../militia";
import { StocksGlyphs } from "../parts";
import { buildOption, recruitOptions } from "../plan";
import { TOWN_VISION } from "../playback/visibility";
import { siteIconUrl } from "../siteArt";
import type { WorldPlanning } from "../useWorldPlan";
import type { WorldMap } from "../worldMap";
import { Caption, HpBar, Line, Sheet } from "./Sheet";

type WorldState = components["schemas"]["WorldState"];

const ROW_KEY = "fhq-btn h-11 w-[84px] shrink-0 px-1 text-xs";

/** The base site the player holds, if any. */
function heldBase(state: WorldState, map: WorldMap, slot: string): Axial | null {
  const held = state.settlements.find((s) => s.controlled_by === slot && map.siteAt(s.coord.q, s.coord.r)?.kind === "base");
  return held?.coord ?? null;
}

/** The recruit rows for a site (the base, or a city or factory you hold). */
function RecruitRows({
  state,
  map,
  economy,
  slot,
  hex,
  armySize,
  planning,
}: {
  state: WorldState;
  map: WorldMap;
  economy: Economy;
  slot: string;
  hex: Axial;
  armySize: number;
  planning: WorldPlanning;
}) {
  const staged = planning.plan.recruits[hexKey(hex.q, hex.r)];
  const options = recruitOptions(state, map, economy, slot, hex, planning.plan, armySize);
  return (
    <>
      {options.map((o) => {
        const on = staged?.type === o.type;
        const blocked = !on && o.blocked.length > 0;
        return (
          <Line
            key={o.type}
            icon={<Glyph name={o.type} size={24} />}
            name={UNIT_LABEL[o.type]}
            sub={blocked ? o.blocked.join("; ") : <StocksGlyphs stocks={o.cost} />}
            subTone={blocked ? "text-invalid" : ""}
            right={
              <button
                type="button"
                data-active={on}
                disabled={blocked}
                title={on ? "Cancel this recruit" : blocked ? o.blocked.join("; ") : `Recruit for ${stocksText(o.cost)}`}
                onClick={() => planning.setRecruit(hex, on ? null : o.type)}
                className={ROW_KEY}
              >
                {on ? "Cancel" : staged !== undefined ? "Switch" : "Recruit"}
              </button>
            }
          />
        );
      })}
    </>
  );
}

export function BaseSheet({
  state,
  map,
  economy,
  outlook,
  slot,
  planning,
  onClose,
}: {
  state: WorldState;
  map: WorldMap;
  economy: Economy;
  outlook: Forecast;
  slot: string;
  planning: WorldPlanning;
  onClose: () => void;
}) {
  const player = state.players.find((p) => p.id === slot);
  if (player === undefined) throw new Error(`no player ${slot} in the world state`);
  const stocks = stocksOf(player);
  const work = player.construction ?? null;
  const building = work !== null && work.completes_on > state.turn;
  const queued = planning.plan.build;
  const base = heldBase(state, map, slot);
  const status =
    work !== null && building
      ? `building ${BUILDING_LABEL[work.building]} ${work.level}, done turn ${work.completes_on}`
      : work !== null
        ? `${BUILDING_LABEL[work.building]} ${work.level} completes this turn`
        : queued !== null
          ? `${BUILDING_LABEL[queued]} starts this turn`
          : "nothing under construction";
  return (
    <Sheet label="Base" title="Base" glyph="base" subtitle={status} onClose={onClose}>
      <div className="border-b border-ink-900/20 py-1">
        {RESOURCES.map((r) => {
          const net = outlook.net[r];
          const short = outlook.short.includes(r as "fuel" | "manpower");
          return (
            <div key={r} className="flex h-7 items-center gap-2 px-3 font-data text-sm">
              <Glyph name={r} size={16} />
              <span className="w-[86px] font-label text-[15px]">{RESOURCE_LABEL[r]}</span>
              <span className={short ? "text-invalid" : ""}>
                {stocks[r]} / {outlook.storage}
              </span>
              <span className={`ml-auto ${net >= 0 ? "text-[#466428]" : "text-invalid"}`}>
                {net >= 0 ? "+" : ""}
                {net} a turn
              </span>
            </div>
          );
        })}
        {outlook.short.length > 0 && (
          <p className="px-3 pb-1 text-sm font-semibold text-invalid">Out of {outlook.short.join(" and ")} next turn: a unit will disband.</p>
        )}
      </div>
      <Caption>Buildings</Caption>
      {(Object.keys(BUILDING_LABEL) as Building[]).map((b) => {
        const level = levelOf(player, b);
        const option = buildOption(state, economy, slot, b, planning.plan);
        const staged = queued === b;
        const blocked = option !== null && !staged && option.blocked.length > 0;
        return (
          <Line
            key={b}
            icon={<Glyph name={b} size={24} />}
            name={`${BUILDING_LABEL[b]} ${level}/${economy.buildings[b].length}`}
            sub={
              option === null ? (
                "complete"
              ) : (
                <>
                  Lv {option.level}: <StocksGlyphs stocks={option.cost} /> · {option.turns} turns
                  {blocked ? ` · ${option.blocked.join("; ")}` : ""}
                </>
              )
            }
            subTone={blocked ? "text-invalid" : ""}
            right={
              option === null ? undefined : (
                <button
                  type="button"
                  data-active={staged}
                  disabled={blocked}
                  title={
                    staged
                      ? "Cancel this build"
                      : blocked
                        ? option.blocked.join("; ")
                        : queued !== null
                          ? `Build this instead of ${BUILDING_LABEL[queued]}: one building at a time`
                          : `Start level ${option.level} this turn`
                  }
                  onClick={() => planning.setBuild(staged ? null : b)}
                  className={ROW_KEY}
                >
                  {staged ? "Cancel" : queued !== null ? "Switch" : "Build"}
                </button>
              )
            }
          />
        );
      })}
      <Caption>Recruit at the base</Caption>
      {base === null ? (
        <p className="px-3 pb-3 text-sm text-wood-500">You hold no base.</p>
      ) : (
        <RecruitRows state={state} map={map} economy={economy} slot={slot} hex={base} armySize={outlook.armySize} planning={planning} />
      )}
    </Sheet>
  );
}

export function HexSheet({
  map,
  state,
  economy,
  slot,
  hex,
  armySize,
  planning,
  designate,
  onClose,
}: {
  map: WorldMap;
  state: WorldState;
  economy: Economy;
  slot: string;
  hex: Axial;
  armySize: number;
  planning: WorldPlanning;
  designate: Designate;
  onClose: () => void;
}) {
  const cell = map.hexAt(hex.q, hex.r);
  const site = map.siteAt(hex.q, hex.r);
  const settlement = state.settlements.find((s) => axialEqual(s.coord, hex));
  const others = state.units.filter((u) => axialEqual(u.coord, hex));
  const owner = settlement?.controlled_by ?? null;
  const recruits = recruitOptions(state, map, economy, slot, hex, planning.plan, armySize).length > 0;
  const title = site !== undefined ? siteTitle(site, settlement === undefined ? undefined : owner, slot) : (cell?.terrain ?? "Hex");
  const where = `(${hex.q}, ${hex.r})${site === undefined || site.kind === "city" ? ` · ${cell?.terrain}` : ""}${cell?.road ? " · road" : ""}`;
  return (
    <Sheet label={title} title={title} glyph={site?.kind} subtitle={where} onClose={onClose}>
      {site !== undefined && (
        <div className="flex items-start gap-3 border-b border-ink-900/20 px-3 py-2 text-[15px]">
          <img src={siteIconUrl(site.kind)} alt="" className="h-12 w-12 shrink-0" />
          <div className="flex flex-col gap-1">
            {site.kind !== "base" && (
              <span>
                Produces <StocksGlyphs stocks={siteIncome(economy, site)} className="font-data" /> a turn.
              </span>
            )}
            <span>
              {settlement === undefined ? "Vacant base site." : owner === null ? `${neutralStatus(site.garrison)}.` : owner === slot ? "Held by you." : `Held by ${owner}.`}
            </span>
          </div>
        </div>
      )}
      {site === undefined && cell?.terrain === "town" && settlement !== undefined && (
        <div className="flex flex-col gap-1 border-b border-ink-900/20 px-3 py-2 text-[15px]">
          <span>{townStatus(owner, slot)}.</span>
          <span className="text-sm text-wood-500">Earns nothing; whoever holds it sees {TOWN_VISION} hexes around it, through woods.</span>
        </div>
      )}
      {site === undefined && others.length === 0 && <p className="px-3 py-3 text-sm text-wood-500">Nothing to order here. Tap one of your units to give it orders.</p>}
      {recruits && (
        <>
          <Caption>Recruit here</Caption>
          <RecruitRows state={state} map={map} economy={economy} slot={slot} hex={hex} armySize={armySize} planning={planning} />
        </>
      )}
      {others.length > 0 && (
        <>
          <Caption>Units here</Caption>
          {others.map((u) => (
            <Line
              key={u.id}
              icon={<Glyph name={u.type} size={22} />}
              name={u.owner === slot ? (designate(u.id) ?? `Your ${u.type}`) : isMilitia(u) ? militiaName(u.type) : `${u.owner} ${u.type}`}
              sub={u.posture === "column" ? "packed column" : "deployed"}
              right={<HpBar hp={u.hp} max={startingHp(u, map)} />}
              onClick={u.owner === slot ? () => planning.selectUnit(u.id) : undefined}
            />
          ))}
        </>
      )}
    </Sheet>
  );
}
