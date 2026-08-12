// #830: one planned session.
//
// Three independent axes read at once: PLACEMENT is the chip (a date when
// pinned, a window when floating), COMMITMENT is the border (solid for
// something agreed, dashed for a suggestion the runner never signed up to), and
// DISCIPLINE is the small-caps word beside the title. Intent is the stripe.
//
// A suggestion carries no measure column and no tick: it is an offer, so the
// only thing to do with it is take it up or decline it.

import Link from "next/link";
import { CheckCircle2, Loader2 } from "lucide-react";
import type { LoggedActivity, PlannedSession } from "@/lib/types/schedule";
import { formatDistanceKm, formatDuration, formatPace } from "@/lib/format";
import {
  DISCIPLINE_LABEL,
  INTENT_LABEL,
  INTENT_TEXT,
  intentStripe,
  safeDiscipline,
  safeIntent,
} from "./palette";
import { formatDayChip, weekdayShort } from "./dates";

function placementChip(
  session: PlannedSession,
  weekStart: string,
  weekEnd: string,
): string {
  if (session.placement === "pinned") return formatDayChip(session.window_start);
  const start = session.effective_window_start ?? session.window_start;
  const end = session.effective_window_end ?? session.window_end;
  if (start <= weekStart && end >= weekEnd) return "Any day";
  if (start === end) return formatDayChip(start);
  return `${weekdayShort(start)}–${weekdayShort(end)}`;
}

/**
 * What the runner is being asked to DO, in a unit they can act on.
 *
 * Never `effort_score`. That is a modelled, cumulative load number: nobody can
 * go and "do 90 load", and putting it where a prescription goes invites exactly
 * the misreading the project's north star names as its hard case — load read as
 * intensity. Load sizes the BARS, where it is a proportion rather than an
 * instruction, and it labels nothing.
 *
 * A rep-structured session usually carries neither a distance nor a duration,
 * because "6 × 400 m off 90 s" already is the prescription. So the structure is
 * the fallback, not a number the app worked out.
 */
function targetLine(session: PlannedSession): string | null {
  const parts: string[] = [];
  if (session.target_distance_m) parts.push(formatDistanceKm(session.target_distance_m));
  if (session.target_duration_s) parts.push(formatDuration(session.target_duration_s));
  if (parts.length) return parts.join(" · ");

  const reps = session.structure?.reps_planned;
  if (reps) {
    const distance = session.structure?.rep_distance_m;
    return distance ? `${reps} × ${Math.round(distance)} m` : `${reps} reps`;
  }
  return null;
}

function actualLine(actual: LoggedActivity): string {
  const parts: string[] = [];
  if (actual.distance_m > 0) parts.push(formatDistanceKm(actual.distance_m));
  if (actual.moving_time_s > 0) parts.push(formatDuration(actual.moving_time_s));
  if (actual.distance_m > 0 && actual.moving_time_s > 0) {
    parts.push(formatPace(actual.distance_m, actual.moving_time_s));
  }
  // Same rule as the target line: load labels nothing the runner reads. An
  // activity with neither a distance nor a duration is named by its type.
  return parts.join(" · ");
}

export default function SessionCard({
  session,
  actual,
  weekStart,
  weekEnd,
  pending,
  onComplete,
  onUncomplete,
  onDismiss,
}: {
  session: PlannedSession;
  actual?: LoggedActivity;
  weekStart: string;
  weekEnd: string;
  pending: boolean;
  onComplete: (id: string) => void;
  onUncomplete: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const intent = safeIntent(session.intent);
  const discipline = safeDiscipline(session.discipline);
  const isSuggestion = session.commitment === "suggested";
  const isDone = session.status === "done";
  const target = targetLine(session);

  return (
    <li
      className={`relative overflow-hidden rounded-lg bg-white pl-4 pr-3 py-3 shadow-sm dark:bg-gray-800 ${
        isSuggestion
          ? "border border-dashed border-gray-300 dark:border-gray-600"
          : "border border-gray-200 dark:border-gray-700"
      }`}
    >
      <span
        aria-hidden="true"
        className={`absolute inset-y-0 left-0 w-1 ${intentStripe(intent)}`}
      />

      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span
              className={`text-sm font-semibold ${
                isDone
                  ? "text-gray-500 line-through dark:text-gray-400"
                  : "text-gray-900 dark:text-gray-100"
              }`}
            >
              {session.title}
            </span>
            <span className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500">
              {DISCIPLINE_LABEL[discipline]}
            </span>
            <span className={`text-[10px] font-medium uppercase tracking-wider ${INTENT_TEXT[intent]}`}>
              {INTENT_LABEL[intent]}
            </span>
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600 dark:bg-gray-700 dark:text-gray-300">
              {placementChip(session, weekStart, weekEnd)}
            </span>
            {isSuggestion && (
              <span className="rounded-full border border-dashed border-gray-300 px-2 py-0.5 text-[11px] text-gray-500 dark:border-gray-600 dark:text-gray-400">
                Suggestion
              </span>
            )}
          </div>

          {session.detail && (
            <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">{session.detail}</p>
          )}

          {session.has_narrowed && session.placement !== "pinned" && (
            <p className="mt-1 text-[11px] text-gray-400 dark:text-gray-500">
              Window narrowed from {weekdayShort(session.window_start)}–
              {weekdayShort(session.window_end)}
            </p>
          )}

          {isDone && actual && (
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Actual: <span className="font-mono tabular-nums">{actualLine(actual)}</span>
              {actual.activity_id && (
                <>
                  {" · "}
                  <Link
                    href={`/activity/${actual.activity_id}`}
                    className="text-blue-700 hover:underline dark:text-blue-400"
                  >
                    View run
                  </Link>
                </>
              )}
            </p>
          )}
        </div>

        {/* Measure column. A suggestion has none — there is nothing to hit yet. */}
        {!isSuggestion && target && (
          <div className="shrink-0 text-right">
            <div className="font-mono text-sm tabular-nums text-gray-700 dark:text-gray-200">
              {target}
            </div>
            <div className="text-[10px] uppercase tracking-wider text-gray-400 dark:text-gray-500">
              target
            </div>
          </div>
        )}

        {/* The tick. A suggestion is declined, not ticked. */}
        {!isSuggestion && (
          <button
            type="button"
            disabled={pending}
            onClick={() => (isDone ? onUncomplete(session.id) : onComplete(session.id))}
            aria-label={isDone ? `Mark ${session.title} not done` : `Mark ${session.title} done`}
            className="shrink-0 rounded-full p-1 text-gray-400 hover:bg-gray-50 disabled:opacity-40 dark:text-gray-500 dark:hover:bg-gray-700/50"
          >
            {pending ? (
              <Loader2 size={20} className="animate-spin" aria-hidden="true" />
            ) : isDone ? (
              <CheckCircle2
                size={20}
                className="text-emerald-600 dark:text-emerald-400"
                aria-hidden="true"
              />
            ) : (
              <span
                aria-hidden="true"
                className="block h-5 w-5 rounded-full border-2 border-dashed border-gray-300 dark:border-gray-600"
              />
            )}
          </button>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3">
        {isSuggestion ? (
          <button
            type="button"
            disabled={pending}
            onClick={() => onDismiss(session.id)}
            className="text-xs font-medium text-gray-500 hover:text-gray-800 disabled:opacity-40 dark:text-gray-400 dark:hover:text-gray-100"
          >
            {pending ? "Dismissing…" : "Dismiss"}
          </button>
        ) : (
          !isDone && (
            <button
              type="button"
              disabled={pending}
              onClick={() => onComplete(session.id)}
              className="text-xs font-medium text-blue-700 hover:underline disabled:opacity-40 dark:text-blue-400"
            >
              {pending ? "Saving…" : "Mark done"}
            </button>
          )
        )}
      </div>
    </li>
  );
}
