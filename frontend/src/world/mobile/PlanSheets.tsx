// The phone's Army sheet (every unit, those without orders first) and the Submit
// review sheet (what the plan does, who stays put without the Hold bonus, the
// spending, problems and draft notes, and the Submit key).

import { hexKey } from "@/board/hex";
import { Glyph } from "@/components/Glyph";
import type { components } from "@/lib/api/schema";

import { holdAllUnits } from "../autoOrder";
import type { Designate } from "../designations";
import { stocksOf, sub, type Economy } from "../economy";
import { BUILDING_LABEL, orderText, stocksLeft, stocksText, UNIT_LABEL } from "../labels";
import { planCost, planProblems } from "../plan";
import type { WorldView } from "../useWorld";
import { useSwaps } from "../useSwaps";
import type { WorldPlanning } from "../useWorldPlan";
import { at } from "../waypoints";
import { armyOf, holdAllLabel } from "./army";
import { Icon } from "./icons";
import { Caption, HpBar, Line, Sheet } from "./Sheet";

type Unit = components["schemas"]["Unit"];

function UnitIcon({ unit }: { unit: Unit }) {
  const column = unit.posture === "column";
  return (
    <Glyph name={column ? "column" : unit.type} size={24} label={column ? `${UNIT_LABEL[unit.type]}, packed column` : UNIT_LABEL[unit.type]} />
  );
}

export function ArmySheet({
  view,
  planning,
  designate,
  armySize,
  onPick,
  onClose,
}: {
  view: WorldView;
  planning: WorldPlanning;
  designate: Designate;
  armySize: number;
  /** A row was tapped: select that unit and bring it into view. */
  onPick: (unit: Unit) => void;
  onClose: () => void;
}) {
  const { idle, ordered } = armyOf(view, planning);
  const swaps = useSwaps(view, planning);
  const zone = planning.contactZone;
  const holdable = holdAllUnits(idle, planning.plan.orders, []).length;
  const total = idle.length + ordered.length;
  const idleSub = (u: Unit) =>
    [u.posture === "column" ? "packed column" : "deployed", zone.has(hexKey(u.coord.q, u.coord.r)) ? "in contact" : null, "stays put"]
      .filter((p) => p !== null)
      .join(" · ");
  return (
    <Sheet
      label="Army"
      title="Army"
      glyph="army"
      subtitle={`${total} of ${armySize} · ${idle.length} without orders`}
      onClose={onClose}
      footer={
        holdable > 0 ? (
          <button type="button" onClick={planning.holdAll} className="fhq-btn h-12 w-full gap-2 text-sm">
            <Icon name="hold" size={18} />
            {holdAllLabel(holdable, idle.length)}
          </button>
        ) : undefined
      }
    >
      {total === 0 && <p className="px-3 py-3 text-sm text-wood-500">No units in the field.</p>}
      {idle.length > 0 && <Caption>No orders · {idle.length}</Caption>}
      {idle.map((u) => (
        <Line
          key={u.id}
          icon={<UnitIcon unit={u} />}
          name={designate(u.id) ?? UNIT_LABEL[u.type]}
          sub={idleSub(u)}
          right={<HpBar hp={u.hp} />}
          onClick={() => onPick(u)}
        />
      ))}
      {ordered.length > 0 && <Caption>Ordered · {ordered.length}</Caption>}
      {ordered.map((u) => {
        const route = planning.routes.find((r) => r.unitId === u.id);
        const ordered = orderText(planning.plan.orders[u.id], route, swaps.get(u.id) ?? null, designate);
        const blocked = planning.conflicts.has(u.id);
        const text = blocked ? `blocked: ${ordered}` : ordered;
        return (
          <Line
            key={u.id}
            icon={<UnitIcon unit={u} />}
            name={designate(u.id) ?? UNIT_LABEL[u.type]}
            sub={text}
            subTone={blocked || text.startsWith("attack") || text.includes("halts") ? "text-invalid" : ""}
            right={<HpBar hp={u.hp} />}
            onClick={() => onPick(u)}
          />
        );
      })}
    </Sheet>
  );
}

export function ReviewSheet({
  view,
  economy,
  armySize,
  planning,
  designate,
  onPick,
  onClose,
}: {
  view: WorldView;
  economy: Economy;
  armySize: number;
  planning: WorldPlanning;
  designate: Designate;
  onPick: (unit: Unit) => void;
  onClose: () => void;
}) {
  const { plan } = planning;
  const player = view.state.players.find((p) => p.id === view.your_slot);
  const { idle, ordered } = armyOf(view, planning);
  const swaps = useSwaps(view, planning);
  const holdable = holdAllUnits(idle, plan.orders, []).length;
  const cost = planCost(plan, view.state, economy, view.your_slot);
  const left = player === undefined ? null : sub(stocksOf(player), cost);
  const problems = [...planProblems(plan, view.state, economy, view.your_slot, armySize), ...(planning.submitError !== null ? [planning.submitError] : [])];
  const recruits = Object.values(plan.recruits);
  const spends = cost.manpower > 0 || cost.fuel > 0 || cost.steel > 0;
  const disabled = planning.submitting || problems.length > 0 || (!planning.dirty && planning.submitted);
  const total = idle.length + ordered.length;
  const status = planning.dirty
    ? "Unsent changes"
    : planning.submitted
      ? "Submitted: you can change it until the turn resolves"
      : "Nothing submitted yet";
  return (
    <Sheet
      label={`Plan for turn ${view.turn}`}
      title={`Turn ${view.turn} plan`}
      subtitle={`${ordered.length} of ${total} units ordered`}
      onClose={onClose}
      footer={
        <button type="button" onClick={() => void planning.submit()} disabled={disabled} className="fhq-commit h-[60px] w-full flex-col gap-0.5 py-0 text-lg leading-5">
          {planning.submitting ? "Sending…" : planning.submitted ? "Resubmit" : "Submit plan"}
          <span className="font-data text-xs font-normal normal-case tracking-normal">
            {disabled && !planning.submitting && problems.length === 0 ? "already in: nothing new to send" : "you can change it until the turn resolves"}
          </span>
        </button>
      }
    >
      {(planning.droppedTurn !== null || planning.restored || planning.setAside) && (
        <div className="flex flex-col gap-2 border-b border-ink-900/20 px-3 py-2 text-sm">
          {planning.droppedTurn !== null && (
            <p>Turn {planning.droppedTurn} resolved before your last changes were sent; they were dropped.</p>
          )}
          {planning.restored && <p>Unsent changes restored from before the page reloaded. Submit to send them, or revert.</p>}
          {planning.setAside && (
            <>
              <p>A newer plan was submitted elsewhere, so this tab's unsent draft was set aside. The plan shown is the one the server holds.</p>
              <div className="flex gap-2">
                <button type="button" onClick={planning.restoreSetAside} className="fhq-btn h-11 flex-1 text-xs">
                  Use my draft instead
                </button>
                <button type="button" onClick={planning.dismissSetAside} className="fhq-btn fhq-btn-quiet h-11 flex-1 text-xs">
                  Dismiss
                </button>
              </div>
            </>
          )}
        </div>
      )}
      {problems.length > 0 && (
        <div className="border-b border-ink-900/20 px-3 py-2 text-sm font-semibold text-invalid">
          {problems.map((p) => (
            <p key={p}>{p}</p>
          ))}
        </div>
      )}
      {idle.length > 0 && (
        <>
          <Caption
            action={
              holdable > 0 ? (
                <button type="button" onClick={planning.holdAll} className="fhq-btn h-11 px-3 text-xs">
                  Hold all {holdable}
                </button>
              ) : undefined
            }
          >
            Staying put, no Hold bonus · {idle.length}
          </Caption>
          {idle.map((u) => (
            <Line
              key={u.id}
              icon={<UnitIcon unit={u} />}
              name={designate(u.id) ?? UNIT_LABEL[u.type]}
              sub={u.posture === "column" ? "packed column" : undefined}
              right={<Icon name="chevron" size={18} />}
              onClick={() => onPick(u)}
            />
          ))}
        </>
      )}
      {ordered.length > 0 && (
        <>
          <Caption>Ordered · {ordered.length}</Caption>
          {ordered.map((u) => {
            const route = planning.routes.find((r) => r.unitId === u.id);
            const ordered = orderText(plan.orders[u.id], route, swaps.get(u.id) ?? null, designate);
            const blocked = planning.conflicts.has(u.id);
            return (
              <Line
                key={u.id}
                icon={<UnitIcon unit={u} />}
                name={designate(u.id) ?? UNIT_LABEL[u.type]}
                sub={blocked ? `blocked: ${ordered}` : ordered}
                subTone={blocked ? "text-invalid" : ""}
                right={<Icon name="chevron" size={18} />}
                onClick={() => onPick(u)}
              />
            );
          })}
        </>
      )}
      <Caption>Spending</Caption>
      <div className="px-3 pb-3 font-data text-sm">
        {recruits.map((r) => (
          <p key={`${r.coord.q},${r.coord.r}`}>
            Recruit {UNIT_LABEL[r.type].toLowerCase()} at {at(r.coord)}
          </p>
        ))}
        {plan.build !== null && <p>Start {BUILDING_LABEL[plan.build]}</p>}
        {plan.disband.length > 0 && <p>{plan.disband.length} on the disband list</p>}
        {spends ? (
          <p className="mt-1">
            Spends {stocksText(cost)}
            {left !== null && <span className="text-wood-500">; leaves {stocksLeft(left)}</span>}
          </p>
        ) : (
          <p>Nothing: no recruits, no building started.</p>
        )}
      </div>
      <div className="flex items-center gap-2 border-t border-ink-900/20 px-3 py-2 text-sm">
        <span className={planning.dirty ? "font-semibold text-[#9a6510]" : "text-wood-500"}>{status}</span>
        {planning.dirty && (planning.submitted || planning.restored) && (
          <button type="button" onClick={planning.revert} className="fhq-btn fhq-btn-quiet ml-auto h-11 px-3 text-xs">
            Revert
          </button>
        )}
      </div>
    </Sheet>
  );
}
