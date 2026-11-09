// The world screen (WORLD_SPEC §11, W6 slice 1): header, board, and side panels.
//
// This slice is the read side: the whole map, your units and sites, fog, the
// turn clock and your economy. Planning orders (travel, builds, recruits) lands in
// the next slice on top of this shell.

import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import type { Axial } from "@/board/hex";
import { AccountMenu } from "@/components/AccountMenu";
import { Centered, ErrorBanner, Loading } from "@/components/ui";
import type { components } from "@/lib/api/schema";

import { NEUTRAL_OWNER } from "./colors";
import {
  economyOf,
  forecast,
  levelOf,
  RESOURCES,
  stocksOf,
  type Building,
  type Forecast,
  type Stocks,
} from "./economy";
import { useWorld, type WorldView } from "./useWorld";
import { WorldBoard } from "./WorldBoard";
import type { WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];

export function WorldShell() {
  const { id } = useParams<{ id: string }>();
  if (id === undefined) throw new Error("WorldShell needs a world id route param");
  const load = useWorld(id);
  if (load.status === "loading") return <Loading fill label="Loading the world…" />;
  if (load.status === "error") {
    return (
      <Centered fill>
        <ErrorBanner message={load.message} />
        <Link to="/" className="text-sm text-brass-400 hover:underline">
          ← Back
        </Link>
      </Centered>
    );
  }
  return <WorldScreen map={load.map} view={load.view} error={load.error} />;
}

function WorldScreen({ map, view, error }: { map: WorldMap; view: WorldView; error: string | null }) {
  const [selected, setSelected] = useState<Axial | null>(null);
  const plan = useMemo(() => forecast(view.state, map, view.your_slot), [view, map]);
  const base = useMemo(() => baseOf(view.state, map, view.your_slot), [view, map]);

  return (
    <div className="fixed inset-0 flex flex-col bg-[#1a1d22] text-ivory-100">
      <WorldHeader view={view} plan={plan} />
      {error !== null && (
        <div className="absolute left-1/2 top-14 z-10 -translate-x-1/2">
          <ErrorBanner message={error} />
        </div>
      )}
      <div className="relative flex-1">
        <WorldBoard map={map} view={view} focus={base} onHexClick={setSelected} />
        <div className="pointer-events-none absolute right-3 top-3 flex w-72 flex-col gap-3">
          <BasePanel state={view.state} slot={view.your_slot} />
          {selected !== null && (
            <HexPanel map={map} state={view.state} slot={view.your_slot} hex={selected} onClose={() => setSelected(null)} />
          )}
        </div>
      </div>
    </div>
  );
}

function baseOf(state: WorldState, map: WorldMap, slot: string): Axial | null {
  const held = state.settlements.find(
    (s) => s.controlled_by === slot && map.siteAt(s.coord.q, s.coord.r)?.kind === "base",
  );
  return held?.coord ?? state.units.find((u) => u.owner === slot)?.coord ?? null;
}

// --- header -----------------------------------------------------------------------

const RESOURCE_LABEL = { manpower: "Manpower", fuel: "Fuel", steel: "Steel" } as const;

function WorldHeader({ view, plan }: { view: WorldView; plan: Forecast }) {
  const player = view.state.players.find((p) => p.id === view.your_slot);
  const stocks = player === undefined ? null : stocksOf(player);
  return (
    <header className="fhq-wood flex h-12 shrink-0 items-center gap-5 border-b-2 border-brass-600/70 px-4 text-sm">
      <Link to="/" className="font-display text-base uppercase tracking-wide text-ivory-100">
        hex-general
      </Link>
      <Stat label="Turn">
        <span className="font-data">{view.turn}</span>
      </Stat>
      <Countdown clock={view.clock} nextTickAt={view.next_tick_at ?? null} />
      {stocks !== null &&
        RESOURCES.map((r) => (
          <Stat key={r} label={RESOURCE_LABEL[r]}>
            <span className={`font-data ${plan.short.includes(r as "fuel" | "manpower") ? "text-invalid" : ""}`}>
              {stocks[r]}
              <span className="text-ivory-400">/{plan.storage}</span>
            </span>{" "}
            <span className={plan.net[r] >= 0 ? "text-valid" : "text-invalid"}>
              ({plan.net[r] >= 0 ? "+" : ""}
              {plan.net[r]})
            </span>
          </Stat>
        ))}
      <Stat label="Army">
        <span className="font-data">
          {plan.units}
          <span className="text-ivory-400">/{plan.armySize}</span>
        </span>
      </Stat>
      {plan.short.length > 0 && (
        <span className="font-label text-xs font-semibold uppercase tracking-wider text-invalid">
          Out of {plan.short.join(" and ")} next turn: a unit will disband
        </span>
      )}
      <div className="ml-auto">
        <AccountMenu />
      </div>
    </header>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="font-label text-[11px] uppercase tracking-wider text-ivory-400">{label}</span>
      {children}
    </span>
  );
}

function Countdown({ clock, nextTickAt }: { clock: string; nextTickAt: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);
  if (clock !== "twice_daily" || nextTickAt === null) {
    return <Stat label="Clock">manual</Stat>;
  }
  const at = new Date(nextTickAt);
  const minutes = Math.max(0, Math.round((at.getTime() - now) / 60_000));
  const when = at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return (
    <Stat label="Resolves">
      <span className="font-data">
        {minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`}
      </span>{" "}
      <span className="text-ivory-400">({when})</span>
    </Stat>
  );
}

// --- panels -----------------------------------------------------------------------

const BUILDING_LABEL: Record<Building, string> = {
  barracks: "Barracks",
  depot: "Depot",
  workshop: "Workshop",
};

function stocksText(s: Stocks): string {
  const parts = RESOURCES.filter((r) => s[r] !== 0).map((r) => `${s[r]} ${r}`);
  return parts.length === 0 ? "free" : parts.join(", ");
}

function Panel({ title, children, onClose }: { title: string; children: React.ReactNode; onClose?: () => void }) {
  return (
    <section className="pointer-events-auto rounded-sm border border-wood-700 bg-parchment-200 p-3 text-sm text-ink-900 shadow-lg">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-display text-sm uppercase tracking-wide">{title}</h2>
        {onClose !== undefined && (
          <button type="button" onClick={onClose} className="font-label text-xs uppercase text-wood-500 hover:text-ink-900">
            Close
          </button>
        )}
      </div>
      {children}
    </section>
  );
}

function BasePanel({ state, slot }: { state: WorldState; slot: string }) {
  const economy = economyOf(state);
  const player = state.players.find((p) => p.id === slot);
  if (player === undefined) return null;
  const work = player.construction ?? null;
  return (
    <Panel title="Base">
      <ul className="flex flex-col gap-1">
        {(Object.keys(BUILDING_LABEL) as Building[]).map((b) => {
          const level = levelOf(player, b);
          const levels = economy.buildings[b];
          const next = levels[level];
          return (
            <li key={b} className="flex justify-between gap-2">
              <span>
                {BUILDING_LABEL[b]} <span className="font-data">{level}</span>
                <span className="text-wood-500">/{levels.length}</span>
              </span>
              <span className="font-data text-xs text-wood-500">
                {next === undefined ? "complete" : `next: ${stocksText(next.cost)}, ${next.turns}t`}
              </span>
            </li>
          );
        })}
      </ul>
      {work !== null && (
        <p className="mt-2 text-xs">
          Building {BUILDING_LABEL[work.building]} {work.level}, done turn{" "}
          <span className="font-data">{work.completes_on}</span>.
        </p>
      )}
    </Panel>
  );
}

const SITE_LABEL = {
  base: "Base",
  city: "City",
  farm: "Farm",
  oil_field: "Oil field",
  steel_mill: "Steel mill",
  factory: "Factory",
} as const;

const TIER_LABEL = ["", "Militia", "Garrison", "Strongpoint", "Fortress"];

function HexPanel({
  map,
  state,
  slot,
  hex,
  onClose,
}: {
  map: WorldMap;
  state: WorldState;
  slot: string;
  hex: Axial;
  onClose: () => void;
}) {
  const economy = economyOf(state);
  const cell = map.hexAt(hex.q, hex.r);
  const site = map.siteAt(hex.q, hex.r);
  const settlement = state.settlements.find((s) => s.coord.q === hex.q && s.coord.r === hex.r);
  const units = state.units.filter((u) => u.coord.q === hex.q && u.coord.r === hex.r);
  const owner = settlement?.controlled_by ?? null;
  return (
    <Panel title={site !== undefined ? SITE_LABEL[site.kind] : (cell?.terrain ?? "Hex")} onClose={onClose}>
      <p className="font-data text-xs text-wood-500">
        ({hex.q}, {hex.r}) · {cell?.terrain}
        {cell?.road ? " · road" : ""}
      </p>
      {site !== undefined && (
        <div className="mt-1 flex flex-col gap-0.5">
          {site.kind !== "base" && <span>Produces {stocksText(economy.siteIncome[site.kind])} a turn.</span>}
          {site.garrison > 0 && (
            <span>
              Garrison tier {site.garrison}: {TIER_LABEL[site.garrison]}
            </span>
          )}
          <span>
            {settlement === undefined
              ? "Vacant base site."
              : owner === null
                ? "Neutral."
                : owner === slot
                  ? "Held by you."
                  : `Held by ${owner}.`}
          </span>
        </div>
      )}
      {units.length > 0 && (
        <ul className="mt-2 flex flex-col gap-0.5">
          {units.map((u) => (
            <li key={u.id} className="flex justify-between">
              <span>
                {u.owner === slot ? "Your" : u.owner === NEUTRAL_OWNER ? "Garrison" : u.owner} {u.type}
                {u.posture === "column" ? " (column)" : ""}
              </span>
              <span className="font-data">{u.hp} HP</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
