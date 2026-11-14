// The phone's Report sheet: today's turn report panel as a bottom sheet. A line
// jumps the map to its hex and closes the sheet (during playback it jumps to its
// beat instead); Play starts turn playback.

import type { Axial } from "@/board/hex";

import { FIGHT_RESULT, SECTION_LABEL, TONE_CLASS } from "../labels";
import { ReportLine, type ReportPlay } from "../parts";
import type { ReportItem } from "../turnReport";
import type { ReportLoad } from "../useTurnReport";
import { Icon } from "./icons";
import { Caption, Sheet } from "./Sheet";

export function ReportSheet({
  load,
  items,
  play,
  trails,
  onToggleTrails,
  onJump,
  onClose,
}: {
  load: ReportLoad;
  items: readonly ReportItem[] | null;
  play: ReportPlay;
  /** Last turn's trails are drawn on the map. */
  trails: boolean;
  onToggleTrails: () => void;
  onJump: (hex: Axial) => void;
  onClose: () => void;
}) {
  if (load.status === "none") {
    return (
      <Sheet label="Report" title="Report" onClose={onClose}>
        <p className="px-3 py-3 text-sm text-wood-500">No turn has resolved yet: the first report comes after turn 0.</p>
      </Sheet>
    );
  }
  const status = play.turnPlayback.status;
  const canPlay = !play.watching && status !== "none" && status !== "waiting" && status !== "empty";
  const lineProps = (item: ReportItem) => {
    const k = items === null ? -1 : items.indexOf(item);
    const beat = play.itemBeats?.[k] ?? -1;
    return {
      item,
      current: play.beat >= 0 && beat === play.beat,
      onClick:
        play.watching && beat >= 0
          ? () => {
              play.onBeat(beat);
              onClose();
            }
          : item.at === null
            ? null
            : () => {
                onJump(item.at!);
                onClose();
              },
    };
  };
  return (
    <Sheet
      label={`Turn ${load.turn} report`}
      title={`Turn ${load.turn} report`}
      onClose={onClose}
      footer={
        <div className="flex gap-2">
          {canPlay && (
            <button
              type="button"
              onClick={() => {
                play.onPlay();
                onClose();
              }}
              className="fhq-btn h-12 flex-1 gap-2 text-sm"
            >
              <Icon name="play" size={16} />
              Watch turn {load.turn}
            </button>
          )}
          {status === "empty" && (
            <span className="flex h-12 flex-1 items-center font-label text-xs uppercase tracking-wide text-ivory-400">Nothing moved in your sight</span>
          )}
          <button type="button" onClick={onToggleTrails} data-active={trails} aria-pressed={trails} className="fhq-btn h-12 flex-1 text-sm">
            {trails ? "Hide trails" : "Show trails"}
          </button>
        </div>
      }
    >
      {load.status === "loading" && <p className="px-3 py-3 text-sm text-wood-500">Loading…</p>}
      {load.status === "error" && <p className="px-3 py-3 text-sm text-invalid">{load.message}</p>}
      {items !== null && (
        <div className="flex flex-col gap-1 px-1 pb-2 text-[15px] [&_li]:text-sm">
          {play.fights.length > 0 && (
            <div>
              <Caption>Your fights</Caption>
              <ul className="flex flex-col">
                {play.fights.map((f) => (
                  <li key={f.eventIndex} className="leading-snug">
                    <button
                      type="button"
                      onClick={() => {
                        play.onBeat(f.beat);
                        onClose();
                      }}
                      className={`flex min-h-11 w-full items-center gap-2 rounded-sm px-2 py-1 text-left active:bg-parchment-400/40 ${
                        play.beat === f.beat ? "bg-brass-400/40" : ""
                      }`}
                    >
                      <span className={`w-16 shrink-0 font-label uppercase ${TONE_CLASS[FIGHT_RESULT[f.result].tone]}`}>
                        {FIGHT_RESULT[f.result].text}
                      </span>
                      <span>{f.summary}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {items.length === 0 && <p className="px-2 py-2 text-sm text-wood-500">A quiet turn.</p>}
          {(["fighting", "base", "movement"] as const).map((section) => {
            const list = items.filter((i) => i.section === section);
            return list.length === 0 ? null : (
              <div key={section}>
                <Caption>{SECTION_LABEL[section]}</Caption>
                <ul className="flex flex-col [&_button]:min-h-11 [&_button]:px-2">
                  {list.map((item, k) => (
                    <ReportLine key={k} {...lineProps(item)} />
                  ))}
                </ul>
              </div>
            );
          })}
          {items.some((i) => i.section === "seen") && (
            <details open={play.watching || undefined} className="px-2">
              <summary className="flex min-h-11 cursor-pointer items-center font-label text-xs font-bold uppercase tracking-widest text-wood-500">
                {SECTION_LABEL.seen} ({items.filter((i) => i.section === "seen").length})
              </summary>
              <ul className="flex flex-col [&_button]:min-h-11">
                {items
                  .filter((i) => i.section === "seen")
                  .map((item, k) => (
                    <ReportLine key={k} {...lineProps(item)} />
                  ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Sheet>
  );
}
