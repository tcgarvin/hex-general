// The world screen's phone layout (below 768 px wide, or a phone held sideways;
// `usePhoneLayout`). `WorldScreen` composes all the shared state (planning, the
// report, playback, designations, the forecast) and hands it here; this file only
// lays it out for a small touch screen:
//
// * a wood top bar: turn and countdown, stocks and army size as glyph + number,
//   and a menu (field manual, all worlds, account, sign out);
// * the map full screen underneath (no minimap, no zoom keys: pinch and drag),
//   with two keys on the right edge (centre on base, whole world);
// * a bottom bar with nothing selected (Army, Report, Base, and the Submit plate),
//   each opening a bottom sheet; the unit dock when one of your units is selected;
//   the transport bar while a turn plays back;
// * orders by tap (`useWorldPlan`'s `onHexTap`, rule in `autoOrder.ts`): select a
//   unit, press Move in the dock, then tap where it should go; without Move armed a
//   tap only selects your unit or inspects the hex. Notices show as a short toast
//   above the dock.

import { useEffect, useState } from "react";
import { Link } from "react-router";

import type { Axial } from "@/board/hex";
import { ResolveTurnItem } from "@/admin/ResolveTurnItem";
import { useAuth } from "@/auth/useAuth";
import { useMe } from "@/auth/useMe";
import { Glyph } from "@/components/Glyph";
import { ErrorBanner } from "@/components/ui";
import type { components } from "@/lib/api/schema";

import type { Designate } from "../designations";
import { RESOURCES, stocksOf, type Economy, type Forecast } from "../economy";
import { FieldManual, type ManualSection } from "../FieldManual";
import { RESOURCE_LABEL } from "../labels";
import { Countdown, type ReportPlay } from "../parts";
import { PlaybackBar, type BeatJump } from "../PlaybackBar";
import type { Playback } from "../playback/reconstruct";
import type { ReportItem } from "../turnReport";
import type { WorldView } from "../useWorld";
import type { ReportLoad } from "../useTurnReport";
import type { WorldPlanning } from "../useWorldPlan";
import { WorldBoard } from "../WorldBoard";
import { TACTICAL_SCALE, type WorldOverlay, type WorldRenderer } from "../WorldRenderer";
import type { WorldMap } from "../worldMap";
import { armyOf, planStatus } from "./army";
import { Icon } from "./icons";
import { HexSheet, BaseSheet } from "./PlaceSheets";
import { ArmySheet, ReviewSheet } from "./PlanSheets";
import { ReportSheet } from "./ReportSheet";
import { MoreSheet, UnitDock } from "./UnitDock";

type Unit = components["schemas"]["Unit"];

/** Where the camera keeps a hex clear of the phone chrome (top bar, keys, dock). */
const PHONE_INSETS = { left: 12, right: 60, top: 12, bottom: 210 } as const;
const NOTICE_MS = 4000;

type SheetName = "army" | "report" | "base" | "review" | "more";

export interface PhoneWorldProps {
  map: WorldMap;
  view: WorldView;
  error: string | null;
  planning: WorldPlanning;
  designate: Designate;
  economy: Economy;
  outlook: Forecast;
  /** Where "centre on base" goes. */
  home: Axial | null;
  overlay: WorldOverlay;
  panTo: { hex: Axial; seq: number; reveal?: boolean } | null;
  /** Centre the camera on a hex. */
  jump: (hex: Axial) => void;
  /** Bring a hex into view only if it is off screen or under the chrome. */
  reveal: (hex: Axial) => void;
  renderer: WorldRenderer | null;
  onRenderer: (renderer: WorldRenderer | null) => void;
  report: ReportLoad;
  items: readonly ReportItem[] | null;
  play: ReportPlay;
  trails: boolean;
  onToggleTrails: () => void;
  /** The turn that just resolved, for the banner pill. */
  resolved: { turn: number; seq: number } | null;
  /** Turn playback: what is being watched, and the playback once loaded. */
  watching: { turn: number; autoplay: boolean; jump: BeatJump | null } | null;
  playback: Playback | null;
  playbackNote: string | null;
  onBeatNow: (beat: number) => void;
  onExitWatch: () => void;
  manual: { open: boolean; setOpen: (open: boolean) => void; section: ManualSection; setSection: (s: ManualSection) => void };
  /** Reload the world after an admin resolves the turn from the menu. */
  onResolved: () => void;
}

export function PhoneWorld(props: PhoneWorldProps) {
  const { map, view, planning, renderer, watching } = props;
  const [sheet, setSheet] = useState<SheetName | null>(null);
  useNoPageBounce();
  // The phone has no minimap, and its panels sit top and bottom.
  useEffect(() => {
    if (renderer === null || renderer.destroyed) return;
    renderer.setChrome({ minimap: false, insets: PHONE_INSETS });
  }, [renderer]);

  const unit = planning.selectedUnit;
  // A sheet for a unit closes when the unit is let go; the others stay over the dock.
  const open: SheetName | null = sheet === "more" && unit === null ? null : sheet;
  const close = () => setSheet(null);
  const tap = (hex: Axial) => {
    setSheet(null);
    planning.onHexTap(hex);
  };
  const pick = (u: Unit) => {
    setSheet(null);
    planning.selectUnit(u.id);
    props.reveal(u.coord);
  };
  const cycle = (dir: 1 | -1) => {
    const next = planning.cycleUnit(dir);
    if (next !== null) props.reveal(next.coord);
  };
  const hexSheet = unit === null && planning.selectedHex !== null && watching === null && open === null;

  return (
    <div className="fixed inset-0 flex touch-manipulation flex-col overflow-hidden overscroll-none bg-[#1a1d22] text-ivory-100">
      <TopBar
        view={view}
        outlook={props.outlook}
        onFieldManual={() => props.manual.setOpen(true)}
        onResolved={props.onResolved}
      />
      <div className="relative min-h-0 flex-1">
        <WorldBoard
          map={map}
          view={view}
          focus={props.home}
          overlay={props.overlay}
          panTo={props.panTo}
          onHexClick={tap}
          onRenderer={props.onRenderer}
        />
        {props.error !== null && (
          <div className="absolute inset-x-3 top-2 z-20">
            <ErrorBanner message={props.error} />
          </div>
        )}
        {props.resolved !== null && (
          <ResolvedPill
            key={props.resolved.seq}
            turn={props.resolved.turn}
            onWatch={props.play.turnPlayback.status === "idle" && watching === null ? props.play.onPlay : null}
          />
        )}
        <MapKeys renderer={renderer} home={props.home} />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col">
          <Toast planning={planning} />
          {watching !== null ? (
            props.playback !== null && renderer !== null ? (
              <PlaybackBar
                key={props.playback.turn}
                playback={props.playback}
                renderer={renderer}
                autoplay={watching.autoplay}
                jump={watching.jump}
                note={props.playbackNote}
                onBeat={props.onBeatNow}
                onExit={props.onExitWatch}
                compact
              />
            ) : (
              <div className="fhq-wood pointer-events-auto flex h-16 items-center justify-center gap-3 border-t-2 border-brass-600 pb-[env(safe-area-inset-bottom)] font-label text-sm uppercase tracking-wide">
                Loading turn {watching.turn}…
                <button type="button" onClick={props.onExitWatch} className="fhq-btn h-11 px-3 text-xs">
                  Cancel
                </button>
              </div>
            )
          ) : unit !== null ? (
            <UnitDock
              unit={unit}
              view={view}
              map={map}
              planning={planning}
              designate={props.designate}
              onCycle={cycle}
              onMore={() => {
                // Opening a sheet disarms the next tap (the More sheet may arm a stop).
                planning.arm(null);
                setSheet("more");
              }}
            />
          ) : (
            <BottomBar view={view} planning={planning} onOpen={setSheet} hasReport={props.report.status !== "none"} />
          )}
        </div>
        {open === "army" && (
          <ArmySheet view={view} planning={planning} designate={props.designate} armySize={props.outlook.armySize} onPick={pick} onClose={close} />
        )}
        {open === "report" && (
          <ReportSheet
            load={props.report}
            items={props.items}
            play={props.play}
            trails={props.trails}
            onToggleTrails={props.onToggleTrails}
            onJump={props.jump}
            onClose={close}
          />
        )}
        {open === "base" && (
          <BaseSheet
            state={view.state}
            map={map}
            economy={props.economy}
            outlook={props.outlook}
            slot={view.your_slot}
            planning={planning}
            onClose={close}
          />
        )}
        {open === "review" && (
          <ReviewSheet
            view={view}
            economy={props.economy}
            armySize={props.outlook.armySize}
            planning={planning}
            designate={props.designate}
            onPick={pick}
            onClose={close}
          />
        )}
        {open === "more" && unit !== null && (
          <MoreSheet unit={unit} view={view} planning={planning} designate={props.designate} onClose={close} />
        )}
        {hexSheet && planning.selectedHex !== null && (
          <HexSheet
            map={map}
            state={view.state}
            economy={props.economy}
            slot={view.your_slot}
            hex={planning.selectedHex}
            armySize={props.outlook.armySize}
            planning={planning}
            designate={props.designate}
            onClose={() => planning.selectUnit(null)}
          />
        )}
        {props.manual.open && (
          <FieldManual
            view={view}
            economy={props.economy}
            armySize={props.outlook.armySize}
            section={props.manual.section}
            onSection={props.manual.setSection}
            onClose={() => props.manual.setOpen(false)}
          />
        )}
      </div>
    </div>
  );
}

/** While the phone layout is up, the page itself never scrolls or bounces behind the map. */
function useNoPageBounce(): void {
  useEffect(() => {
    const root = document.documentElement;
    const before = { html: root.style.overscrollBehavior, body: document.body.style.overscrollBehavior, overflow: document.body.style.overflow };
    root.style.overscrollBehavior = "none";
    document.body.style.overscrollBehavior = "none";
    document.body.style.overflow = "hidden";
    return () => {
      root.style.overscrollBehavior = before.html;
      document.body.style.overscrollBehavior = before.body;
      document.body.style.overflow = before.overflow;
    };
  }, []);
}

// --- top bar ----------------------------------------------------------------------

function TopBar({
  view,
  outlook,
  onFieldManual,
  onResolved,
}: {
  view: WorldView;
  outlook: Forecast;
  onFieldManual: () => void;
  onResolved: () => void;
}) {
  const player = view.state.players.find((p) => p.id === view.your_slot);
  const stocks = player === undefined ? null : stocksOf(player);
  return (
    <header className="fhq-wood relative z-40 flex h-[calc(48px+env(safe-area-inset-top))] shrink-0 items-center gap-2.5 whitespace-nowrap border-b-2 border-brass-600 pl-[max(10px,env(safe-area-inset-left))] pr-[max(2px,env(safe-area-inset-right))] pt-[env(safe-area-inset-top)] font-data text-sm">
      <div className="flex items-center gap-1.5">
        <Glyph name="turn" size={16} label="Turn" />
        <span>{view.turn}</span>
        <Countdown clock={view.clock} nextTickAt={view.next_tick_at ?? null} compact />
      </div>
      <div className="ml-auto flex min-w-0 items-center gap-2.5 overflow-hidden">
        {stocks !== null &&
          RESOURCES.map((r) => (
            <span key={r} className={`flex items-center gap-0.5 ${outlook.short.includes(r as "fuel" | "manpower") ? "text-invalid" : ""}`}>
              <Glyph name={r} size={16} label={RESOURCE_LABEL[r]} />
              {stocks[r]}
            </span>
          ))}
        <span className="flex items-center gap-0.5">
          <Glyph name="army" size={16} label="Army size" />
          {outlook.units}/{outlook.armySize}
        </span>
      </div>
      <Menu view={view} onFieldManual={onFieldManual} onResolved={onResolved} />
    </header>
  );
}

function Menu({
  view,
  onFieldManual,
  onResolved,
}: {
  view: WorldView;
  onFieldManual: () => void;
  onResolved: () => void;
}) {
  const { user, signOut } = useAuth();
  const me = useMe();
  const isAdmin = me.status === "ready" && me.me.is_admin;
  const [open, setOpen] = useState(false);
  const item = "flex min-h-12 w-full items-center px-4 text-left font-label text-base text-ivory-100 active:bg-ink-900/60";
  return (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Menu"
        aria-expanded={open}
        className="flex size-11 items-center justify-center text-ivory-100"
      >
        <Icon name="menu" size={22} />
      </button>
      {open && (
        <>
          <button className="fixed inset-0 z-10 cursor-default" aria-hidden tabIndex={-1} onClick={() => setOpen(false)} />
          <nav className="fhq-wood-deep absolute right-1 z-20 mt-1 flex w-64 flex-col rounded-sm border border-brass-600/50 py-1 shadow-lg shadow-black/50">
            <p className="truncate px-4 py-2 text-xs text-ivory-400">{user?.email ?? "Signed in"}</p>
            <button
              type="button"
              className={item}
              onClick={() => {
                setOpen(false);
                onFieldManual();
              }}
            >
              Field manual
            </button>
            <Link to="/worlds" className={item} onClick={() => setOpen(false)}>
              All worlds
            </Link>
            <Link to="/account" className={item} onClick={() => setOpen(false)}>
              Account
            </Link>
            {isAdmin && (
              <ResolveTurnItem
                gameId={view.game_id}
                turn={view.turn}
                active={view.status === "active"}
                onResolved={() => {
                  setOpen(false);
                  onResolved();
                }}
                className={item}
              />
            )}
            <button
              type="button"
              className={`${item} border-t border-wood-500/40`}
              onClick={() => {
                setOpen(false);
                void signOut();
              }}
            >
              Sign out
            </button>
          </nav>
        </>
      )}
    </div>
  );
}

// --- map overlays -----------------------------------------------------------------

function MapKeys({ renderer, home }: { renderer: WorldRenderer | null; home: Axial | null }) {
  return (
    <div className="pointer-events-auto absolute right-[max(8px,env(safe-area-inset-right))] top-2.5 z-10 flex flex-col gap-2" role="toolbar" aria-label="Map controls">
      <button
        type="button"
        className="fhq-btn size-11 px-0"
        disabled={renderer === null || home === null}
        onClick={() => home !== null && renderer?.centerOn(home, TACTICAL_SCALE)}
        aria-label="Centre on your base"
        title="Centre on your base"
      >
        <Icon name="home" size={22} />
      </button>
      <button
        type="button"
        className="fhq-btn size-11 px-0"
        disabled={renderer === null}
        onClick={() => renderer?.fitAll()}
        aria-label="Show the whole world"
        title="Show the whole world"
      >
        <Icon name="fit" size={22} />
      </button>
    </div>
  );
}

/** "Turn N resolved · Watch", a small pill under the top bar for a few seconds. */
function ResolvedPill({ turn, onWatch }: { turn: number; onWatch: (() => void) | null }) {
  // No translate class: the fhq-drop keyframes centre it (translate -50%) themselves.
  return (
    <div
      role="status"
      className="fhq-wood pointer-events-none absolute left-1/2 top-2.5 z-20 flex h-12 animate-[fhq-drop_6s_ease-out_forwards] items-center gap-2 whitespace-nowrap rounded-[5px] border border-brass-600 pl-3 pr-1.5 shadow-[0_2px_6px_rgba(0,0,0,0.55)]"
    >
      <span className="font-display text-sm uppercase tracking-wide text-brass-400">Turn {turn} resolved</span>
      {onWatch !== null && (
        <button type="button" onClick={onWatch} className="fhq-btn pointer-events-auto h-9 gap-1.5 px-3 text-[13px]">
          <Icon name="play" size={14} />
          Watch
        </button>
      )}
    </div>
  );
}

/** The last refused tap's notice, for a few seconds, just above the dock. */
function Toast({ planning }: { planning: WorldPlanning }) {
  const notice = planning.notice;
  const [expired, setExpired] = useState<number | null>(null);
  useEffect(() => {
    if (notice === null) return;
    const id = setTimeout(() => setExpired(notice.seq), NOTICE_MS);
    return () => clearTimeout(id);
  }, [notice]);
  if (notice === null || notice.seq === expired) return null;
  return (
    <div className="mx-auto mb-2 max-w-[calc(100%-24px)]">
      <p role="status" className="fhq-tip border-invalid! px-3 py-2 text-center text-sm font-semibold text-invalid">
        {notice.text}
      </p>
    </div>
  );
}

// --- bottom bar -------------------------------------------------------------------

function BottomBar({
  view,
  planning,
  onOpen,
  hasReport,
}: {
  view: WorldView;
  planning: WorldPlanning;
  onOpen: (sheet: SheetName) => void;
  hasReport: boolean;
}) {
  const { idle } = armyOf(view, planning);
  const label = planning.submitting ? "Sending…" : planning.submitted ? (planning.dirty ? "Resubmit" : "Submitted") : "Submit plan";
  return (
    <nav
      aria-label="Game"
      className="fhq-wood pointer-events-auto flex items-start gap-0.5 border-t-2 border-brass-600 pb-[max(10px,env(safe-area-inset-bottom))] pl-[max(4px,env(safe-area-inset-left))] pr-[max(8px,env(safe-area-inset-right))] pt-[7px]"
    >
      <Tab label="Army" onClick={() => onOpen("army")} badge={idle.length}>
        <Glyph name="army" size={26} />
      </Tab>
      <Tab label="Report" onClick={() => onOpen("report")} dim={!hasReport}>
        <Icon name="report" size={26} />
      </Tab>
      <Tab label="Base" onClick={() => onOpen("base")}>
        <Glyph name="base" size={26} />
      </Tab>
      <button
        type="button"
        onClick={() => onOpen("review")}
        className="fhq-commit ml-auto h-[56px] w-[172px] min-w-0 flex-col gap-0 py-0 text-base leading-[18px]"
        aria-label={`${label}: review the plan`}
      >
        {label}
        <span className="max-w-full truncate font-data text-xs font-normal leading-[14px] normal-case tracking-normal">{planStatus(view, planning)}</span>
      </button>
    </nav>
  );
}

function Tab({
  label,
  onClick,
  badge = 0,
  dim = false,
  children,
}: {
  label: string;
  onClick: () => void;
  badge?: number;
  dim?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex h-14 w-[62px] shrink-0 flex-col items-center justify-center gap-[3px] font-label text-[11px] font-semibold uppercase tracking-wider text-ivory-100 ${dim ? "opacity-60" : ""}`}
    >
      <span className="relative flex">
        {children}
        {badge > 0 && (
          <span
            aria-label={`${badge} without orders`}
            className="absolute left-[19px] top-[-5px] h-[17px] min-w-[17px] rounded-[9px] bg-warn px-1 text-center font-label text-xs font-bold leading-[17px] tracking-normal text-ink-900"
          >
            {badge}
          </span>
        )}
      </span>
      {label}
    </button>
  );
}
