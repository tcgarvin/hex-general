import { useCallback, useEffect, useState } from "react";

import { useMe } from "@/auth/useMe";
import { Button, ButtonLink, Card, ErrorBanner } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { AdminWorld, AdminWorldList } from "@/lib/api/types";
import { worldName } from "@/lib/worldName";

import { AdminGate } from "./AdminGate";
import {
  type Clock,
  confirmsDelete,
  type CreateForm,
  DEFAULT_FORM,
  errorText,
  localTime,
  MAX_SLOTS,
  randomSeed,
  scheduleText,
  shortId,
  untilText,
  validateCreateForm,
} from "./adminLogic";

/**
 * The admin screen (`/admin`): every world with its seats and clock, and the
 * world-management calls `scripts/prod-world.py` makes (create, resolve a turn,
 * delete, run the clock sweep). Shown to admins only (`GET /me`'s `is_admin`);
 * the API checks the role again on every call.
 *
 * No native dialogs: tick, delete and the clock sweep confirm in the page, and a
 * delete needs the world's short id typed in. World creation can take minutes on
 * Cloud Run, so the request has no client-side timeout (Cloud Run ends it at
 * 300 s) and the form stays locked until it answers.
 */
export function AdminPage() {
  const me = useMe();
  return (
    <AdminGate me={me}>
      <AdminScreen />
    </AdminGate>
  );
}

/** The action in flight; one at a time, so a slow create can't overlap a delete. */
type Busy = { kind: "create"; since: number } | { kind: "tick" | "delete"; gameId: string } | { kind: "clock" };

function useNow(intervalMs: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function AdminScreen() {
  const [list, setList] = useState<AdminWorldList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy | null>(null);
  const [newId, setNewId] = useState<string | null>(null);
  const now = useNow(busy?.kind === "create" ? 1_000 : 30_000);

  const load = useCallback(async (): Promise<boolean> => {
    try {
      const { data, error: apiError, response } = await api.GET("/dev/worlds");
      if (apiError) {
        setError(errorText(response.status, apiError));
        return false;
      }
      setList(data);
      return true;
    } catch (err) {
      setError(errorText(null, err));
      return false;
    }
  }, []);

  useEffect(() => {
    // The first fetch: `load` sets state only after its request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  /** Run one admin call: lock the page, show its error or notice, then reload the list. */
  async function act(
    next: Busy,
    call: () => Promise<{ error?: unknown; response: Response }>,
    done: () => string,
  ): Promise<boolean> {
    setBusy(next);
    setError(null);
    setNotice(null);
    try {
      const { error: apiError, response } = await call();
      if (apiError !== undefined) {
        setError(errorText(response.status, apiError));
        return false;
      }
      setNotice(done());
      return true;
    } catch (err) {
      setError(errorText(null, err));
      return false;
    } finally {
      await load();
      setBusy(null);
    }
  }

  async function create(body: { slots: number; bots: number; seed: number; clock: Clock }): Promise<boolean> {
    let created = "";
    return act(
      { kind: "create", since: Date.now() },
      async () => {
        const result = await api.POST("/dev/worlds", { body });
        if (result.data) {
          created = result.data.game_id;
          setNewId(created);
        }
        return result;
      },
      () => `Created ${worldName(created)} (${shortId(created)}): ${body.slots} slots, ${body.bots} bots, seed ${body.seed}.`,
    );
  }

  async function tick(world: AdminWorld): Promise<boolean> {
    let turn = world.turn;
    return act(
      { kind: "tick", gameId: world.game_id },
      async () => {
        const result = await api.POST("/dev/worlds/{game_id}/tick", {
          params: { path: { game_id: world.game_id } },
        });
        if (result.data) turn = result.data.turn;
        return result;
      },
      () => `Resolved ${worldName(world.game_id)} (${shortId(world.game_id)}): now on turn ${turn}.`,
    );
  }

  async function remove(world: AdminWorld): Promise<boolean> {
    return act(
      { kind: "delete", gameId: world.game_id },
      () => api.DELETE("/dev/worlds/{game_id}", { params: { path: { game_id: world.game_id } } }),
      () => `Deleted ${worldName(world.game_id)} (${shortId(world.game_id)}).`,
    );
  }

  async function sweep(): Promise<boolean> {
    let summary = "";
    return act(
      { kind: "clock" },
      async () => {
        const result = await api.POST("/dev/clock");
        if (result.data) {
          summary = `resolved ${result.data.ticked.length} due world(s); ${result.data.skipped.length} already done.`;
        }
        return result;
      },
      () => `Clock sweep: ${summary}`,
    );
  }

  return (
    <div className="flex flex-1 flex-col items-center gap-4 px-4 py-6">
      <div className="flex w-full max-w-3xl flex-col gap-4">
        <Card>
          <header className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h1 className="font-display text-2xl uppercase tracking-wide">World admin</h1>
              <p className="mt-1 text-sm text-wood-700">
                {list === null ? "Scheduled ticks: loading…" : scheduleText(list.schedule)}
              </p>
            </div>
            <ClockSweep busy={busy} onRun={sweep} />
          </header>
        </Card>

        {error !== null && (
          <div className="flex flex-col gap-1">
            <div className="whitespace-pre-wrap break-words">
              <ErrorBanner message={error} />
            </div>
            <button
              type="button"
              className="self-end font-label text-xs uppercase tracking-wider text-ivory-400 hover:text-ivory-100"
              onClick={() => setError(null)}
            >
              Dismiss
            </button>
          </div>
        )}
        {notice !== null && (
          <p role="status" className="fhq-note px-4 py-2 text-sm">
            {notice}
          </p>
        )}

        <CreateWorld busy={busy} now={now} onCreate={create} />

        <Card>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-display text-lg uppercase tracking-wide">
              Worlds{list !== null && ` (${list.worlds.length})`}
            </h2>
            <Button variant="ghost" disabled={busy !== null} onClick={() => void load()}>
              Refresh
            </Button>
          </div>
          <p className="mt-1 text-xs text-wood-700">
            Open shows the board only in a world this account holds a seat in; elsewhere it shows the
            &ldquo;closed to you&rdquo; notice. That is expected for an admin.
          </p>
          {list === null && error === null && (
            <p className="mt-3 font-label text-sm uppercase tracking-wider text-wood-500">Loading worlds…</p>
          )}
          {list !== null && list.worlds.length === 0 && <p className="mt-3 text-sm">No worlds yet.</p>}
          {list !== null && list.worlds.length > 0 && (
            <ul className="mt-3 flex flex-col divide-y divide-wood-500/40 border-y border-wood-500/40">
              {list.worlds.map((world) => (
                <WorldItem
                  key={world.game_id}
                  world={world}
                  now={now}
                  busy={busy}
                  isNew={world.game_id === newId}
                  onTick={tick}
                  onDelete={remove}
                  onCopyError={setError}
                />
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

const FIELD =
  "w-full rounded-sm border border-wood-500 bg-ivory-100 px-3 py-2 text-sm text-ink-900 focus:outline-2 focus:outline-brass-600 disabled:opacity-60";
const LABEL = "font-label text-xs font-semibold uppercase tracking-wider text-wood-700";

/** The create-world form, prefilled with the rehearsal world. */
function CreateWorld({
  busy,
  now,
  onCreate,
}: {
  busy: Busy | null;
  now: Date;
  onCreate: (body: { slots: number; bots: number; seed: number; clock: Clock }) => Promise<boolean>;
}) {
  const [form, setForm] = useState<CreateForm>(DEFAULT_FORM);
  const [showErrors, setShowErrors] = useState(false);
  const check = validateCreateForm(form);
  const errors = showErrors && !check.ok ? check.errors : {};
  const creating = busy?.kind === "create";
  const set = (field: keyof CreateForm) => (value: string) => setForm((f) => ({ ...f, [field]: value }));

  return (
    <Card>
      <h2 className="font-display text-lg uppercase tracking-wide">Create a world</h2>
      <form
        className="mt-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (busy !== null) return;
          if (!check.ok) {
            setShowErrors(true);
            return;
          }
          setShowErrors(false);
          void onCreate(check.body);
        }}
      >
        <fieldset disabled={busy !== null} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <NumberField label={`Slots (1 to ${MAX_SLOTS})`} value={form.slots} onChange={set("slots")} error={errors.slots} />
          <NumberField label="Bots (at most the slots)" value={form.bots} onChange={set("bots")} error={errors.bots} />
          <div className="flex flex-col gap-1">
            <span className={LABEL}>Seed</span>
            <div className="flex gap-2">
              <input
                className={FIELD}
                inputMode="numeric"
                aria-label="Seed"
                value={form.seed}
                onChange={(e) => set("seed")(e.target.value)}
              />
              <Button type="button" variant="ghost" onClick={() => set("seed")(randomSeed())}>
                Random
              </Button>
            </div>
            {errors.seed !== undefined && <span className="text-xs text-invalid">{errors.seed}</span>}
          </div>
          <label className="flex flex-col gap-1">
            <span className={LABEL}>Clock</span>
            <select
              className={FIELD}
              value={form.clock}
              onChange={(e) => set("clock")(e.target.value as Clock)}
            >
              <option value="twice_daily">Twice daily (scheduled)</option>
              <option value="manual">Manual (resolves only when ticked)</option>
            </select>
          </label>
        </fieldset>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={busy !== null}>
            {creating ? "Creating…" : "Create world"}
          </Button>
          {creating && (
            <p role="status" className="text-sm text-wood-700">
              Generating the world: {Math.max(0, Math.round((now.getTime() - busy.since) / 1000))} s. A large
              world takes a minute or more on the server; keep this page open.
            </p>
          )}
        </div>
      </form>
    </Card>
  );
}

function NumberField({
  label,
  value,
  onChange,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error: string | undefined;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className={LABEL}>{label}</span>
      <input className={FIELD} inputMode="numeric" value={value} onChange={(e) => onChange(e.target.value)} />
      {error !== undefined && <span className="text-xs text-invalid">{error}</span>}
    </label>
  );
}

/** The clock sweep button, with its in-page confirmation. */
function ClockSweep({ busy, onRun }: { busy: Busy | null; onRun: () => Promise<boolean> }) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <Button variant="ghost" disabled={busy !== null} onClick={() => setConfirming(true)}>
        Run clock sweep now
      </Button>
    );
  }
  return (
    <div className="fhq-note flex w-full flex-col gap-2 px-3 py-2 text-sm sm:w-auto sm:max-w-xs">
      <p>Resolve every scheduled world whose turn is due, as the scheduler does. Worlds not yet due are left alone.</p>
      <div className="flex gap-2">
        <Button
          disabled={busy !== null}
          onClick={() => {
            void onRun().then(() => setConfirming(false));
          }}
        >
          {busy?.kind === "clock" ? "Running…" : "Run sweep"}
        </Button>
        <Button variant="ghost" disabled={busy !== null} onClick={() => setConfirming(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

const STATUS_STYLE: Record<AdminWorld["status"], string> = {
  active: "border-valid bg-valid/20 text-ink-900",
  lobby: "border-warn text-canvas-600",
  finished: "border-wood-500 text-wood-500",
};

/** One world: what it is, who is in it, its clock, and its actions. */
function WorldItem({
  world,
  now,
  busy,
  isNew,
  onTick,
  onDelete,
  onCopyError,
}: {
  world: AdminWorld;
  now: Date;
  busy: Busy | null;
  isNew: boolean;
  onTick: (world: AdminWorld) => Promise<boolean>;
  onDelete: (world: AdminWorld) => Promise<boolean>;
  onCopyError: (message: string) => void;
}) {
  const [confirm, setConfirm] = useState<"tick" | "delete" | null>(null);
  const [typed, setTyped] = useState("");
  const [copied, setCopied] = useState(false);
  const short = shortId(world.game_id);
  const name = worldName(world.game_id);
  const mine = busy !== null && "gameId" in busy && busy.gameId === world.game_id;

  async function copy() {
    try {
      await navigator.clipboard.writeText(world.game_id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      onCopyError(`Could not copy the world id (${world.game_id}): ${errorText(null, err)}`);
    }
  }

  return (
    <li className={`flex flex-col gap-2 py-3 ${isNew ? "bg-parchment-400/25 -mx-2 px-2" : ""}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-display text-base uppercase tracking-wide">{name}</span>
        <span className="font-data text-sm text-wood-700" title={world.game_id}>
          {short}
        </span>
        <button
          type="button"
          className="font-label text-[11px] uppercase tracking-wider text-canvas-600 underline-offset-2 hover:underline"
          onClick={() => void copy()}
        >
          {copied ? "Copied" : "Copy id"}
        </button>
        <span
          className={`rounded-sm border px-1.5 font-label text-[11px] font-semibold uppercase tracking-wider ${STATUS_STYLE[world.status]}`}
        >
          {world.status}
        </span>
        {isNew && (
          <span className="rounded-sm border border-brass-600 px-1.5 font-label text-[11px] font-semibold uppercase tracking-wider text-canvas-600">
            New
          </span>
        )}
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
        <Term>Size</Term>
        <dd>
          <span className="font-data">{world.slots}</span> slots · map{" "}
          <span className="font-data">
            {world.width}×{world.height}
          </span>
          {world.seed !== null && (
            <>
              {" "}
              · seed <span className="font-data">{world.seed}</span>
            </>
          )}
        </dd>
        <Term>Seats</Term>
        <dd>
          <span className="font-data">{world.humans}</span> human{world.humans === 1 ? "" : "s"} ·{" "}
          <span className="font-data">{world.bots}</span> bot{world.bots === 1 ? "" : "s"} ·{" "}
          <span className="font-data">{world.open}</span> open
        </dd>
        <Term>Turn</Term>
        <dd>
          <span className="font-data">{world.turn}</span> ·{" "}
          {world.clock === "twice_daily" ? "twice-daily clock" : "manual clock"}
        </dd>
        <Term>Next tick</Term>
        <dd>
          {world.next_tick_at === null ? (
            world.clock === "manual" ? (
              "only when resolved by hand"
            ) : (
              "none scheduled"
            )
          ) : (
            <>
              {localTime(new Date(world.next_tick_at))}{" "}
              <span className="text-wood-700">({untilText(new Date(world.next_tick_at), now)})</span>
            </>
          )}
        </dd>
        <Term>Created</Term>
        <dd>{localTime(new Date(world.created_at))}</dd>
      </dl>

      {confirm === null && (
        <div className="flex flex-wrap gap-2">
          <ButtonLink variant="ghost" to={`/world/${world.game_id}`}>
            Open
          </ButtonLink>
          <Button
            variant="ghost"
            disabled={busy !== null || world.status !== "active"}
            title={world.status !== "active" ? "Only an active world has a turn to resolve" : undefined}
            onClick={() => setConfirm("tick")}
          >
            Resolve turn now
          </Button>
          <Button
            variant="ghost"
            className="text-invalid!"
            disabled={busy !== null}
            onClick={() => {
              setTyped("");
              setConfirm("delete");
            }}
          >
            Delete
          </Button>
        </div>
      )}

      {confirm === "tick" && (
        <div className="fhq-note flex flex-col gap-2 px-3 py-2 text-sm">
          <p>
            Resolve turn <span className="font-data">{world.turn}</span> of {name} ({short}) now? The bots plan, every
            stored plan resolves and the world moves to turn <span className="font-data">{world.turn + 1}</span>.
            {world.clock === "twice_daily" && " Its next scheduled tick stays where it is."}
          </p>
          <div className="flex gap-2">
            <Button
              disabled={busy !== null}
              onClick={() => {
                void onTick(world).then(() => setConfirm(null));
              }}
            >
              {mine ? "Resolving…" : `Resolve turn ${world.turn}`}
            </Button>
            <Button variant="ghost" disabled={busy !== null} onClick={() => setConfirm(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {confirm === "delete" && (
        <form
          className="fhq-note flex flex-col gap-2 border-invalid! px-3 py-2 text-sm"
          onSubmit={(e) => {
            e.preventDefault();
            if (busy !== null || !confirmsDelete(typed, world.game_id)) return;
            void onDelete(world).then((ok) => {
              if (!ok) setConfirm(null);
            });
          }}
        >
          <p>
            Delete {name} ({short}) with its players, orders, turns and map? This cannot be undone. Type{" "}
            <span className="font-data font-semibold">{short}</span> to confirm.
          </p>
          <input
            className={FIELD}
            aria-label={`Type ${short} to confirm`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <div className="flex gap-2">
            <Button type="submit" disabled={busy !== null || !confirmsDelete(typed, world.game_id)}>
              {mine ? "Deleting…" : "Delete world"}
            </Button>
            <Button type="button" variant="ghost" disabled={busy !== null} onClick={() => setConfirm(null)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </li>
  );
}

function Term({ children }: { children: React.ReactNode }) {
  return <dt className="font-label text-xs font-semibold uppercase tracking-wider text-wood-700 leading-5">{children}</dt>;
}
