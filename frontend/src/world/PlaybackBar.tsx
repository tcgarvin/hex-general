// Turn playback on the world board: drives the renderer frame by frame and
// shows the transport bar in place of the plan bar.
//
// The transport state lives here (`usePlayback`), not in the shell, so a frame
// re-renders only this small component; the shell hears only beat changes
// (`onBeat`), for the report highlight. Keys while it is up: Space play/pause,
// Left/Right step, Esc exit (caught before the board's own hotkeys; WASD,
// Up/Down and the zoom keys still move the camera). Moving the camera by hand
// pauses camera-follow until the next beat with an action of the viewer's, or
// until Follow is pressed.

import { useEffect, useMemo, useRef, useState } from "react";

import { cameraHint, cameraTrack, frameAt } from "./playback/interpolate";
import type { Playback } from "./playback/reconstruct";
import { SPEEDS, type Speed } from "./playback/controller";
import { beatMarks, beatTitle, playDurationMs } from "./playback/session";
import { usePlayback } from "./playback/usePlayback";
import type { WorldRenderer } from "./WorldRenderer";

const BUTTON =
  "flex h-8 min-w-8 items-center justify-center rounded-sm border border-brass-600/70 bg-wood-700/90 px-1.5 text-ivory-100 shadow transition-colors hover:bg-wood-500 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brass-400 disabled:cursor-not-allowed disabled:opacity-40";

function Icon({ d, fill = false }: { d: string; fill?: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className="h-4 w-4"
      fill={fill ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={d} />
    </svg>
  );
}

/** A request to jump to a beat from outside (the report, the fights list); `seq` makes repeats count. */
export interface BeatJump {
  beat: number;
  seq: number;
}

declare global {
  interface Window {
    /** Dev builds only: drive playback from the console or a test harness. */
    __hexPlayback?: {
      seek: (t: number) => void;
      step: (by: 1 | -1) => void;
      play: () => void;
      pause: () => void;
      state: () => { t: number; beat: number; phase: number; playing: boolean; beats: number };
    };
  }
}

export function PlaybackBar({
  playback,
  renderer,
  autoplay,
  jump,
  note,
  onBeat,
  onExit,
}: {
  playback: Playback;
  renderer: WorldRenderer;
  /** Start playing at once (Play), or wait paused (a jump from the fights list). */
  autoplay: boolean;
  jump: BeatJump | null;
  /** A caveat to show (e.g. the start picture could not be loaded). */
  note: string | null;
  onBeat: (beat: number) => void;
  onExit: () => void;
}) {
  const pb = playback;
  const durationMs = useMemo(() => (k: number) => playDurationMs(pb, k), [pb]);
  const controls = usePlayback({ beatCount: pb.beats.length, durationMs, resetKey: pb.turn });
  const { state, position } = controls;
  const marks = useMemo(() => beatMarks(pb), [pb]);
  const track = useMemo(() => cameraTrack(pb), [pb]);
  const [followPaused, setFollowPaused] = useState(false);

  // Enter playback on the renderer; leaving restores the live scene.
  useEffect(() => {
    renderer.setPlayback({ visible: pb.visible[0], control: pb.control[0] });
    return () => renderer.setPlayback(null);
  }, [renderer, pb]);

  // Autoplay once per mount; a jump request lands paused on its beat.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (jump !== null) controls.jumpToBeat(jump.beat);
    else if (autoplay) controls.play();
  }, [autoplay, jump, controls]);
  const lastJump = useRef(jump?.seq ?? null);
  useEffect(() => {
    if (jump === null || jump.seq === lastJump.current) return;
    lastJump.current = jump.seq;
    controls.jumpToBeat(jump.beat);
  }, [jump, controls]);

  // Every frame: the units and effects to draw.
  const { beat, phase } = position;
  useEffect(() => {
    if (beat < 0) return;
    const frame = frameAt(pb, beat, phase);
    renderer.setPlaybackFrame(frame.units, frame.effects);
  }, [renderer, pb, beat, phase]);

  // Each beat: its sight and site owners (the end's once the last beat is done),
  // the shell's highlight, and the camera.
  const boundary = state.t >= pb.beats.length ? pb.beats.length : Math.max(0, beat);
  useEffect(() => {
    renderer.setPlayback({ visible: pb.visible[boundary], control: pb.control[boundary] });
  }, [renderer, pb, boundary]);
  const followRef = useRef(followPaused);
  useEffect(() => {
    followRef.current = followPaused;
  }, [followPaused]);
  useEffect(() => {
    if (beat < 0) return;
    onBeat(beat);
    const own = cameraHint(pb, beat) !== null;
    if (own && followRef.current) setFollowPaused(false);
    const hint = track[beat];
    if (hint !== null && (own || !followRef.current)) renderer.followHexes(hint.hexes);
  }, [renderer, pb, beat, track, onBeat]);
  useEffect(() => renderer.onUserCamera(() => setFollowPaused(true)), [renderer]);

  // Transport keys, caught before the board's hotkeys.
  const latest = useRef({ controls, onExit });
  useEffect(() => {
    latest.current = { controls, onExit };
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target;
      if (t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) {
        if (!(t instanceof HTMLInputElement && t.type === "range")) return;
      }
      const { controls: c, onExit: exit } = latest.current;
      const act = { " ": c.toggle, ArrowLeft: c.stepBack, ArrowRight: c.stepForward, Escape: exit }[e.key];
      if (act === undefined) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      act();
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, []);

  // Dev builds: a console/automation handle (the automated browser throttles rAF).
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__hexPlayback = {
      seek: (t) => latest.current.controls.seek(t),
      step: (by) => (by > 0 ? latest.current.controls.stepForward() : latest.current.controls.stepBack()),
      play: () => latest.current.controls.play(),
      pause: () => latest.current.controls.pause(),
      state: () => {
        const c = latest.current.controls;
        return { t: c.state.t, beat: c.position.beat, phase: c.position.phase, playing: c.state.playing, beats: c.state.beatCount };
      },
    };
    return () => {
      delete window.__hexPlayback;
    };
  }, []);

  const n = pb.beats.length;
  const atEnd = state.t >= n;
  const title = beat < 0 ? "" : atEnd ? "Turn over" : beatTitle(pb.beats, beat);
  const nextSpeed = SPEEDS[(SPEEDS.indexOf(state.speed) + 1) % SPEEDS.length] as Speed;
  return (
    <div className="pointer-events-none absolute bottom-3 left-1/2 flex max-w-[calc(100%-24px)] -translate-x-1/2 flex-col items-center gap-2">
      {note !== null && (
        <div className="pointer-events-auto rounded-sm border border-brass-600 bg-parchment-200 px-3 py-1.5 text-xs text-ink-900 shadow">
          {note}
        </div>
      )}
      <div
        role="toolbar"
        aria-label={`Playback of turn ${pb.turn}`}
        className="fhq-wood pointer-events-auto flex items-center gap-2 whitespace-nowrap rounded-sm border-2 border-brass-600/70 px-3 py-2 text-sm shadow-lg"
      >
        <span className="font-display text-xs uppercase tracking-wide text-brass-400">Turn {pb.turn}</span>
        <button type="button" className={BUTTON} onClick={controls.stepBack} disabled={state.t <= 0} title="Step back (←)" aria-label="Step back">
          <Icon d="M12 3.5v9L6 8zM4 3.5v9" />
        </button>
        <button
          type="button"
          className={BUTTON}
          onClick={controls.toggle}
          title={state.playing ? "Pause (Space)" : atEnd ? "Play again (Space)" : "Play (Space)"}
          aria-label={state.playing ? "Pause" : "Play"}
        >
          {state.playing ? <Icon d="M5 3.5v9M11 3.5v9" /> : <Icon d="M5 3.5v9l7-4.5z" fill />}
        </button>
        <button type="button" className={BUTTON} onClick={controls.stepForward} disabled={atEnd} title="Step forward (→)" aria-label="Step forward">
          <Icon d="M4 3.5v9L10 8zM12 3.5v9" />
        </button>
        <button
          type="button"
          className={`${BUTTON} w-11 font-data text-xs`}
          onClick={() => controls.setSpeed(nextSpeed)}
          title="Playback speed"
          aria-label={`Speed ${state.speed}x`}
        >
          {state.speed}×
        </button>
        <Scrubber t={state.t} beats={n} marks={marks} onSeek={controls.seek} onGrab={controls.pause} />
        <span className="w-36 truncate font-label text-xs uppercase tracking-wide text-ivory-100" aria-live="polite">
          {title}
        </span>
        {followPaused && (
          <button type="button" className={`${BUTTON} font-label text-xs uppercase`} onClick={() => setFollowPaused(false)} title="Let the camera follow your units again">
            Follow
          </button>
        )}
        {state.reducedMotion && <span className="font-label text-[11px] uppercase text-ivory-400" title="Your system asks for reduced motion">Steps</span>}
        <button
          type="button"
          onClick={onExit}
          className="rounded-sm border border-brass-600 bg-brass-600 px-3 py-1 font-label text-xs font-semibold uppercase tracking-wider text-wood-900 transition-colors hover:bg-brass-400"
          title="Back to planning (Esc)"
        >
          Exit
        </button>
      </div>
    </div>
  );
}

/** A range input over the whole turn, with a tick per beat and fights marked. */
function Scrubber({
  t,
  beats,
  marks,
  onSeek,
  onGrab,
}: {
  t: number;
  beats: number;
  marks: readonly ("own_fight" | "fight" | null)[];
  onSeek: (t: number) => void;
  onGrab: () => void;
}) {
  return (
    <div className="relative mx-1 h-8 w-56 min-[1100px]:w-72">
      <div className="pointer-events-none absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded bg-wood-900/80" />
      {marks.map((mark, k) => (
        <span
          key={k}
          aria-hidden
          className={`pointer-events-none absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full ${
            mark === "own_fight" ? "h-3 w-3 bg-[#e0503a] ring-1 ring-ivory-100" : mark === "fight" ? "h-2 w-2 bg-[#ffb347]" : "h-2 w-px bg-ivory-400"
          }`}
          style={{ left: `${((k + 0.5) / Math.max(1, beats)) * 100}%` }}
          title={mark === "own_fight" ? "Your fight" : mark === "fight" ? "A fight" : undefined}
        />
      ))}
      <input
        type="range"
        min={0}
        max={beats}
        step={0.01}
        value={t}
        aria-label="Turn position"
        onPointerDown={onGrab}
        onChange={(e) => onSeek(Number(e.target.value))}
        className="absolute inset-0 h-full w-full cursor-pointer accent-[#c9a227] opacity-90"
      />
    </div>
  );
}
