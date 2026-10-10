// The week's sessions as an agenda: pinned sessions under their day, flexible
// ones together under "Flexible this week", suggestions last.
//
// This replaces one full card per session (#830's SessionCard). A plan that
// repeats one flexible session seven times rendered seven identical cards and
// buried everything that differed; the agenda shows it as ONE row with a done
// counter, and the tick marks the next one done. Each row is one line until
// tapped, so the coach's note and the actual measures are a tap away rather
// than always in the way.
//
// The axes keep their language from the old card: intent is the stripe,
// discipline the small-caps word, placement the chip, and a suggestion is
// dashed, carries no measure and no tick, and is declined rather than ticked.

import { useState } from "react";
import Link from "next/link";
import { CheckCircle2, ChevronDown, Loader2, Scale } from "lucide-react";
import type { LoggedActivity, PlannedSession, SpacingRuleRead } from "@/lib/types/schedule";
import { formatDistanceKm, formatDuration, formatPace } from "@/lib/format";
import {
  DISCIPLINE_LABEL,
  INTENT_LABEL,
  INTENT_TEXT,
  intentStripe,
  safeDiscipline,
  safeIntent,
} from "./palette";
import { formatDayChip, todayIso, weekDays, weekdayShort } from "./dates";
import { groupSessions, placementChip, rangeSentence, rulesFor, type SessionGroup } from "./agenda";


/**
 * What the runner is being asked to DO, in a unit they can act on.
 *
 * Never `effort_score`. That is a modelled, cumulative load number: nobody can
 * go and "do 90 load", and putting it where a prescription goes invites exactly
 * the misreading the project's north star names as its hard case — load read as
 * intensity. Load sizes the BARS, where it is a proportion rather than an
 * instruction, and it labels nothing.
 *
 * A rep-structured session usually carries no `target_distance_m`, because
 * "6 × 400 m off 90 s" already is the prescription. It is still a distance the
 * runner covers, though, so #876 gives the measure column BOTH: the session's
 * total in km on top — the same unit every other card shows, and the number the
 * week's headline counts — with the rep prescription under it, because that is
 * what they actually go out and do. Warm-up and cool-down are part of the total
 * and are now stated as distances, so this is addition, not an estimate.
 *
 * That distance is `session.planned_distance_m`, computed server-side by the
 * one definition every other reader asks (#887). This card used to reimplement
 * it and then not reach its own copy: a session carrying rep structure AND a
 * duration returned early on the duration, so the card showed "40 min" while
 * the headline counted the structured kilometres from the same row. Both axes
 * are shown now, distance first, the way `adjust.py` already names every axis a
 * session carries rather than the first one.
 */
function targetLine(session: PlannedSession): string | null {
  const parts: string[] = [];
  if (session.planned_distance_m > 0) {
    parts.push(formatDistanceKm(session.planned_distance_m));
  }
  if (session.target_duration_s) parts.push(formatDuration(session.target_duration_s));
  if (parts.length) return parts.join(" · ");

  // Reps with no measurement at all: the count is all there is to show, and it
  // adds nothing to the headline either.
  const reps = session.structure?.reps_planned;
  if (reps) return `${reps} reps`;
  return null;
}

/** The reps themselves, shown under the total so the prescription survives it. */
function repLine(session: PlannedSession): string | null {
  const reps = session.structure?.reps_planned;
  const distance = session.structure?.rep_distance_m;
  if (!reps || !distance) return null;
  return `${reps} × ${Math.round(distance)} m`;
}

/**
 * #1083: a session done well short of what was asked still fills its slot and
 * counts what was actually done; it only says so. "Short" is under half of the
 * option that was done, the same line the matcher uses to recognise a session.
 */
function wellShort(s: PlannedSession, actual: LoggedActivity): boolean {
  const index = s.done_option ?? 0;
  const option = index > 0 ? s.alternatives?.[index - 1] : s;
  if (!option) return false;
  if (option.planned_distance_m > 0 && actual.distance_m > 0) {
    return actual.distance_m < option.planned_distance_m / 2;
  }
  if (option.target_duration_s && actual.moving_time_s > 0) {
    return actual.moving_time_s < option.target_duration_s / 2;
  }
  return false;
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

interface Handlers {
  pendingId: string | null;
  onComplete: (id: string, option?: number) => void;
  onUncomplete: (id: string) => void;
  onDismiss: (id: string) => void;
}

function Tick({
  label,
  pending,
  onToggle,
  filled,
}: {
  label: string;
  pending: boolean;
  onToggle: () => void;
  filled: boolean;
}) {
  return (
    <button
      type="button"
      disabled={pending}
      onClick={onToggle}
      aria-label={label}
      className="shrink-0 rounded-full p-1.5 text-gray-400 hover:bg-gray-50 disabled:opacity-40 dark:text-gray-500 dark:hover:bg-gray-700/50"
    >
      {pending ? (
        <Loader2 size={22} className="animate-spin" aria-hidden="true" />
      ) : filled ? (
        <CheckCircle2 size={22} className="text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
      ) : (
        <span
          aria-hidden="true"
          className="block h-[22px] w-[22px] rounded-full border-2 border-dashed border-gray-300 dark:border-gray-600"
        />
      )}
    </button>
  );
}

function AgendaRow({
  group,
  weekStart,
  weekEnd,
  showChip,
  actualById,
  rules,
  handlers,
}: {
  group: SessionGroup;
  weekStart: string;
  weekEnd: string;
  showChip: boolean;
  rules: SpacingRuleRead[];
  actualById: Map<string, LoggedActivity>;
  handlers: Handlers;
}) {
  const [open, setOpen] = useState(false);
  const { first, sessions, doneCount } = group;
  const n = sessions.length;
  const intent = safeIntent(first.intent);
  const discipline = safeDiscipline(first.discipline);
  const isSuggestion = first.commitment === "suggested";
  const allDone = doneCount === n;
  const target = targetLine(first);
  const reps = repLine(first);
  const pending = sessions.some((s) => s.id === handlers.pendingId);

  // The group's tick acts on the next session not yet done; once every one is
  // done it takes back the most recent, so a mis-tap is one tap to undo.
  const next = sessions.find((s) => s.status !== "done");
  const lastDone = [...sessions].reverse().find((s) => s.status === "done");
  const tickTarget = next ?? lastDone ?? first;
  const tickLabel =
    n === 1
      ? allDone
        ? `Mark ${first.title} not done`
        : `Mark ${first.title} done`
      : next
        ? `Mark one ${first.title} done (${doneCount} of ${n} done)`
        : `Mark one ${first.title} not done (all ${n} done)`;

  const isRange = first.placement !== "pinned";
  const ruleLines = rulesFor(intent, n, rules);

  const hasMore =
    (first.alternatives?.length ?? 0) > 0 ||
    ruleLines.length > 0 ||
    !!first.detail ||
    !!reps ||
    (first.has_narrowed && first.placement !== "pinned") ||
    isSuggestion ||
    n > 1 ||
    sessions.some((s) => s.status === "done");

  return (
    <li
      className={`relative overflow-hidden rounded-lg bg-white shadow-sm dark:bg-gray-800 ${
        isSuggestion
          ? "border border-dashed border-gray-300 dark:border-gray-600"
          : "border border-gray-200 dark:border-gray-700"
      }`}
    >
      <span aria-hidden="true" className={`absolute inset-y-0 left-0 w-1 ${intentStripe(intent)}`} />

      <div className="flex items-center gap-2 py-2 pl-4 pr-2">
        <button
          type="button"
          onClick={() => hasMore && setOpen((o) => !o)}
          aria-expanded={hasMore ? open : undefined}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <div className="min-w-0 flex-1">
            <div
              className={`truncate text-sm font-semibold ${
                allDone ? "text-gray-500 line-through dark:text-gray-400" : "text-gray-900 dark:text-gray-100"
              }`}
            >
              {/* #1082: a single slot done another way is named by what was done. */}
              {n === 1 && allDone && (first.done_option ?? 0) > 0
                ? first.alternatives?.[(first.done_option ?? 1) - 1]?.title ?? first.title
                : first.title}
              {n > 1 && (
                <span className="ml-1 font-mono text-xs font-medium text-gray-500 dark:text-gray-400">
                  ×{n}
                </span>
              )}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[10px] font-medium uppercase tracking-wider">
              <span className={INTENT_TEXT[intent]}>{INTENT_LABEL[intent]}</span>
              {DISCIPLINE_LABEL[discipline] !== INTENT_LABEL[intent] && (
                <span className="text-gray-400 dark:text-gray-500">{DISCIPLINE_LABEL[discipline]}</span>
              )}
              {showChip && (
                <span className="normal-case tracking-normal text-gray-500 dark:text-gray-400">
                  {isRange
                    ? rangeSentence(group, weekStart, weekEnd)
                    : placementChip(first, weekStart, weekEnd)}
                </span>
              )}
              {ruleLines.length > 0 && (
                <span className="normal-case tracking-normal text-gray-500 dark:text-gray-400">
                  · {ruleLines.length} {ruleLines.length === 1 ? "rule" : "rules"}
                </span>
              )}
              {isSuggestion && (
                <span className="normal-case tracking-normal text-gray-500 dark:text-gray-400">
                  Suggestion
                </span>
              )}
            </div>
            {n > 1 && (
              <div className="mt-1 flex items-center gap-1" aria-hidden="true">
                {sessions.map((s) => (
                  <span
                    key={s.id}
                    className={`h-1.5 w-3 rounded-full ${
                      s.status === "done" ? "bg-emerald-500" : "bg-gray-200 dark:bg-gray-600"
                    }`}
                  />
                ))}
                <span className="ml-1 font-mono text-[10px] tabular-nums text-gray-500 dark:text-gray-400">
                  {doneCount}/{n}
                </span>
              </div>
            )}
          </div>

          {!isSuggestion && target && (
            <span className="shrink-0 text-right font-mono text-xs tabular-nums text-gray-700 dark:text-gray-200">
              {target}
              {n > 1 && <span className="block text-[10px] text-gray-400 dark:text-gray-500">each</span>}
            </span>
          )}

          {hasMore && (
            <ChevronDown
              size={16}
              aria-hidden="true"
              className={`shrink-0 text-gray-400 transition-transform ${open ? "rotate-180" : ""}`}
            />
          )}
        </button>

        {!isSuggestion && (
          <Tick
            label={tickLabel}
            pending={pending}
            filled={allDone}
            onToggle={() =>
              tickTarget.status === "done"
                ? handlers.onUncomplete(tickTarget.id)
                : handlers.onComplete(tickTarget.id)
            }
          />
        )}
      </div>

      {open && (
        <div className="space-y-1.5 border-t border-gray-100 pb-3 pl-4 pr-3 pt-2 text-xs dark:border-gray-700">
          {reps && (
            <p className="font-mono tabular-nums text-gray-600 dark:text-gray-300">{reps}</p>
          )}
          {first.detail && <p className="text-gray-600 dark:text-gray-400">{first.detail}</p>}
          {/* #1082: the other ways to fill this slot, each with its note. Doing
              one instead is a tick that names it. */}
          {(first.alternatives?.length ?? 0) > 0 && (
            <div className="space-y-1">
              <p className="font-medium text-gray-600 dark:text-gray-300">Or instead:</p>
              {first.alternatives!.map((alt, i) => {
                const next = sessions.find((s) => s.status !== "done");
                return (
                  <div key={`${alt.title}-${i}`} className="flex items-center gap-2 text-gray-600 dark:text-gray-400">
                    <span className="min-w-0 flex-1">
                      {alt.title}
                      {first.alternative_notes?.[i] && (
                        <span className="block text-[11px] italic text-gray-500 dark:text-gray-400">
                          {first.alternative_notes[i]}
                        </span>
                      )}
                    </span>
                    {!isSuggestion && next && (
                      <button
                        type="button"
                        disabled={next.id === handlers.pendingId}
                        onClick={() => handlers.onComplete(next.id, i + 1)}
                        className="shrink-0 text-xs font-medium text-blue-700 hover:underline disabled:opacity-40 dark:text-blue-400"
                      >
                        Did this instead
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {/* What the runner can and cannot do with it: where it may go, and
              the plan's rules that name it. */}
          {isRange && (
            <p className="text-gray-600 dark:text-gray-400">
              {n === 1
                ? "One session, on whichever of its days suits you."
                : `${n} separate sessions, on whichever of their days suit you.`}{" "}
              The days shown are the ones still open: days gone, days a done
              session used, and days the rules close are left out.
            </p>
          )}
          {ruleLines.length > 0 && (
            <ul className="space-y-1">
              {ruleLines.map((line) => (
                <li key={line} className="flex items-start gap-1.5 text-gray-600 dark:text-gray-400">
                  <Scale size={12} aria-hidden="true" className="mt-0.5 shrink-0 text-gray-400" />
                  {line}
                </li>
              ))}
            </ul>
          )}
          {first.has_narrowed && first.placement !== "pinned" && (
            <p className="text-[11px] text-gray-400 dark:text-gray-500">
              Window narrowed from {weekdayShort(first.window_start)}–{weekdayShort(first.window_end)}
            </p>
          )}

          {/* Each session's own record. A single session needs no list, only
              its actual once done. */}
          {sessions.map((s, i) => {
            const actual = s.completed_activity_id ? actualById.get(s.completed_activity_id) : undefined;
            if (n === 1 && !actual) return null;
            return (
              <div key={s.id} className="flex items-center gap-2 text-gray-500 dark:text-gray-400">
                {n > 1 && <span className="w-10 shrink-0">{i + 1} of {n}</span>}
                <span className="min-w-0 flex-1">
                  {s.status === "done" ? (
                    actual ? (
                      <>
                        Actual: <span className="font-mono tabular-nums">{actualLine(actual)}</span>
                        {wellShort(s, actual) && " · shorter than planned"}
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
                      </>
                    ) : (
                      "Done"
                    )
                  ) : (
                    "Not done yet"
                  )}
                </span>
                {n > 1 && (
                  <Tick
                    label={s.status === "done" ? `Mark ${s.title} ${i + 1} not done` : `Mark ${s.title} ${i + 1} done`}
                    pending={s.id === handlers.pendingId}
                    filled={s.status === "done"}
                    onToggle={() =>
                      s.status === "done" ? handlers.onUncomplete(s.id) : handlers.onComplete(s.id)
                    }
                  />
                )}
              </div>
            );
          })}

          {isSuggestion &&
            sessions.map((s) => (
              <button
                key={s.id}
                type="button"
                disabled={s.id === handlers.pendingId}
                onClick={() => handlers.onDismiss(s.id)}
                className="mr-3 font-medium text-gray-500 hover:text-gray-800 disabled:opacity-40 dark:text-gray-400 dark:hover:text-gray-100"
              >
                {s.id === handlers.pendingId ? "Dismissing…" : n > 1 ? `Dismiss ${s.title} ${sessions.indexOf(s) + 1}` : "Dismiss"}
              </button>
            ))}
        </div>
      )}
    </li>
  );
}

function Group({
  heading,
  note,
  groups,
  showChip,
  props,
}: {
  heading: string;
  note?: string;
  groups: SessionGroup[];
  showChip: boolean;
  props: AgendaProps;
}) {
  if (!groups.length) return null;
  // Rest is an absence, not a session: no card, no tick, nothing to mark done.
  // A day that is only rest says so in its heading.
  const rests = groups.filter((g) => safeIntent(g.first.intent) === "rest");
  const sessions = groups.filter((g) => safeIntent(g.first.intent) !== "rest");
  const restDetail = rests.map((g) => g.first.detail).find(Boolean);
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
        {heading}
        {rests.length > 0 && (
          <span className="ml-2 font-normal normal-case tracking-normal text-stone-500 dark:text-stone-400">
            Rest day
            {rests[0].first.placement !== "pinned" &&
              `, ${rangeSentence(rests[0], props.weekStart, props.weekEnd).toLowerCase()}`}
          </span>
        )}
      </h3>
      {restDetail && (
        <p className="mb-1.5 text-xs text-gray-500 dark:text-gray-400">{restDetail}</p>
      )}
      {note && sessions.length > 0 && (
        <p className="mb-1.5 text-xs text-gray-500 dark:text-gray-400">{note}</p>
      )}
      <ul className="space-y-1.5">
        {sessions.map((g) => (
          <AgendaRow
            key={g.key}
            group={g}
            weekStart={props.weekStart}
            weekEnd={props.weekEnd}
            showChip={showChip}
            actualById={props.actualById}
            rules={props.rules}
            handlers={props}
          />
        ))}
      </ul>
    </div>
  );
}

interface AgendaProps extends Handlers {
  committed: PlannedSession[];
  suggested: PlannedSession[];
  weekStart: string;
  weekEnd: string;
  actualById: Map<string, LoggedActivity>;
  rules: SpacingRuleRead[];
  /** A day already shown in full above (today, in the Today card). */
  hideDay?: string;
}

export default function WeekAgenda(props: AgendaProps) {
  const { committed, suggested, weekStart } = props;
  const today = todayIso();
  const pinned = committed.filter((s) => s.placement === "pinned");
  const flexible = groupSessions(committed.filter((s) => s.placement !== "pinned"));

  return (
    <section className="space-y-4">
      <h2 className="sr-only">Sessions this week</h2>
      {weekDays(weekStart)
        .filter((day) => day !== props.hideDay)
        .map((day) => (
        <Group
          key={day}
          heading={day === today ? `Today · ${formatDayChip(day)}` : formatDayChip(day)}
          groups={groupSessions(pinned.filter((s) => s.window_start === day))}
          showChip={false}
          props={props}
        />
      ))}
      <Group heading="Flexible this week" groups={flexible} showChip props={props} />
      <Group
        heading="Suggested"
        note="Offers, not commitments. Declining one changes nothing else."
        groups={groupSessions(suggested)}
        showChip
        props={props}
      />
    </section>
  );
}
