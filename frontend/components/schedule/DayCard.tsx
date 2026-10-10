// #1082, #1087: one day, as the recommendation has it: today by default, or
// whichever day the runner tapped on the strip, in any week.
//
// The day's sessions in their recommended order, each with the fixed-wording
// reason the server gave. A session that offers alternatives lists them under
// it: the session itself first, marked Recommended, then each alternative with
// its note on what taking it gives or costs. Doing any one fills the slot, so
// each option carries its own tick and the tick names the option.
//
// A day with more than one activity is numbered, easiest first, walks included.
// What moved this week, and anything that no longer fits, sits under the card
// in the house's amber "notable" tone: information, not an alarm.

import { CheckCircle2, Loader2 } from "lucide-react";
import type {
  PlannedSession,
  ScheduleWeek,
  SessionAlternative,
} from "@/lib/types/schedule";
import { formatDistanceKm, formatDuration } from "@/lib/format";
import { INTENT_FILL, safeIntent } from "./palette";
import { formatDayChip } from "./dates";

interface Option {
  index: number;
  title: string;
  intent: string;
  measure: string | null;
  note: string | null;
}

function measure(o: { target_duration_s?: number | null; planned_distance_m: number }): string | null {
  const parts: string[] = [];
  if (o.planned_distance_m > 0) parts.push(formatDistanceKm(o.planned_distance_m));
  if (o.target_duration_s) parts.push(formatDuration(o.target_duration_s));
  return parts.length ? parts.join(" · ") : null;
}

function optionsOf(s: PlannedSession): Option[] {
  const alts: SessionAlternative[] = s.alternatives ?? [];
  return [
    { index: 0, title: s.title, intent: s.intent, measure: measure(s), note: null },
    ...alts.map((a, i) => ({
      index: i + 1,
      title: a.title,
      intent: a.intent,
      measure: measure(a),
      note: s.alternative_notes?.[i] ?? null,
    })),
  ];
}

function minutes(seconds: number): string {
  return formatDuration(seconds);
}

export default function DayCard({
  week,
  day,
  today,
  pendingId,
  onComplete,
  onUncomplete,
}: {
  week: ScheduleWeek;
  day: string;
  today: string;
  pendingId: string | null;
  onComplete: (id: string, option?: number) => void;
  onUncomplete: (id: string) => void;
}) {
  const rec = week.recommendation;
  const byId = new Map(week.sessions.map((s) => [s.id, s]));
  const isToday = day === today;
  const recommended = (rec?.days.find((d) => d.day === day)?.items ?? [])
    .map((item) => ({ item, session: byId.get(item.session_id) }))
    .filter((x): x is { item: typeof x.item; session: PlannedSession } => !!x.session);
  const doneToday = week.sessions.filter(
    (s) => s.status === "done" && s.done_on === day && s.commitment === "committed",
  );
  const rest = week.sessions.some(
    (s) => safeIntent(s.intent) === "rest" && s.placement === "pinned" && s.window_start === day,
  );
  // What moved and what no longer fits are about the week as it stands now.
  const changes = week.is_current_week ? rec?.changes ?? [] : [];
  const dropped = (week.is_current_week ? rec?.dropped ?? [] : [])
    .map((d) => byId.get(d.session_id)?.title)
    .filter((t): t is string => !!t);

  // What today adds: the recommended options, and at most the longest of each.
  const planned = recommended.reduce((sum, { session }) => sum + (session.target_duration_s ?? 0), 0);
  const most = recommended.reduce(
    (sum, { session }) =>
      sum +
      Math.max(
        session.target_duration_s ?? 0,
        ...(session.alternatives ?? []).map((a) => a.target_duration_s ?? 0),
      ),
    0,
  );

  return (
    <section className="space-y-3">
      <div className="rounded-lg border-2 border-gray-900 bg-white p-3 shadow-sm dark:border-gray-100 dark:bg-gray-800">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          {isToday ? `Today · ${formatDayChip(day)}` : formatDayChip(day)}
        </h2>
        {!recommended.length && !doneToday.length && (
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
            {rest ? "Rest day." : "Nothing planned."}
          </p>
        )}
        {planned > 0 && (
          <p className="mt-0.5 text-xs text-gray-600 dark:text-gray-300">
            {isToday ? "Today adds" : "Adds"} <span className="font-mono tabular-nums">{minutes(planned)}</span>
            {most > planned && (
              <>
                {" "}(up to <span className="font-mono tabular-nums">{minutes(most)}</span> with
                the alternatives)
              </>
            )}
          </p>
        )}

        <ol className="mt-3 space-y-3">
          {doneToday.map((s) => {
            const options = optionsOf(s);
            const done = options[s.done_option ?? 0] ?? options[0];
            return (
              <li key={s.id} className="flex items-center gap-2">
                <CheckCircle2 size={18} className="shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                <span className="min-w-0 flex-1 text-sm text-gray-500 dark:text-gray-400">
                  <span className="font-medium text-gray-700 dark:text-gray-200">{done.title}</span>
                  {(s.done_option ?? 0) > 0 && " · you picked the alternative"}
                </span>
                <button
                  type="button"
                  onClick={() => onUncomplete(s.id)}
                  disabled={pendingId === s.id}
                  className="text-xs text-gray-500 hover:underline disabled:opacity-40 dark:text-gray-400"
                >
                  Undo
                </button>
              </li>
            );
          })}

          {recommended.map(({ item, session }) => {
            const options = optionsOf(session);
            const choice = options.length > 1;
            const pending = pendingId === session.id;
            return (
              <li key={session.id}>
                <div className="flex items-center gap-2">
                  {item.order ? (
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gray-900 text-[11px] font-semibold text-white dark:bg-gray-100 dark:text-gray-900">
                      {item.order}
                    </span>
                  ) : (
                    <span className="w-5 shrink-0 text-center text-[10px] uppercase text-gray-400">·</span>
                  )}
                  <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                    {choice ? options.map((o) => o.title).join(" or ") : session.title}
                  </span>
                </div>
                {item.reason && !choice && (
                  <p className="ml-7 text-xs italic text-gray-500 dark:text-gray-400">{item.reason}</p>
                )}
                <ul className="ml-7 mt-1.5 space-y-1.5">
                  {options.map((o) => (
                    <li
                      key={o.index}
                      className={`flex items-center gap-2 rounded-md px-2 py-1.5 ${
                        !choice
                          ? ""
                          : o.index === 0
                            ? "border-2 border-gray-300 dark:border-gray-500"
                            : "border border-dashed border-gray-300 dark:border-gray-600"
                      }`}
                    >
                      <span
                        aria-hidden="true"
                        className={`h-2 w-2 shrink-0 rounded-full ${INTENT_FILL[safeIntent(o.intent)]}`}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-baseline gap-x-2">
                          {choice && (
                            <span className="text-sm font-medium text-gray-800 dark:text-gray-100">
                              {o.title}
                            </span>
                          )}
                          {o.measure && (
                            <span className="font-mono text-xs tabular-nums text-gray-600 dark:text-gray-300">
                              {o.measure}
                            </span>
                          )}
                          {choice && o.index === 0 && (
                            <span className="rounded bg-gray-900 px-1.5 text-[10px] font-semibold text-white dark:bg-gray-100 dark:text-gray-900">
                              Recommended
                            </span>
                          )}
                        </span>
                        {choice && o.index === 0 && item.reason && (
                          <span className="block text-[11px] italic text-gray-500 dark:text-gray-400">
                            {item.reason}
                          </span>
                        )}
                        {o.note && (
                          <span className="block text-[11px] italic text-gray-500 dark:text-gray-400">
                            {o.note}
                          </span>
                        )}
                      </span>
                      <button
                        type="button"
                        disabled={pending}
                        onClick={() => onComplete(session.id, choice ? o.index : undefined)}
                        aria-label={`Mark ${o.title} done`}
                        className="shrink-0 rounded-full p-1 text-gray-400 hover:bg-gray-50 disabled:opacity-40 dark:text-gray-500 dark:hover:bg-gray-700/50"
                      >
                        {pending ? (
                          <Loader2 size={20} className="animate-spin" aria-hidden="true" />
                        ) : (
                          <span
                            aria-hidden="true"
                            className="block h-5 w-5 rounded-full border-2 border-dashed border-gray-300 dark:border-gray-600"
                          />
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ol>
      </div>

      {(changes.length > 0 || dropped.length > 0) && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
          {changes.length > 0 && (
            <>
              <p className="font-semibold">What changed this week</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {changes.map((c, i) => (
                  <li key={`${i}-${c}`}>{c}</li>
                ))}
              </ul>
            </>
          )}
          {dropped.length > 0 && (
            <p className={changes.length ? "mt-2" : ""}>
              <span className="font-semibold">Doesn&rsquo;t fit the days left:</span>{" "}
              {dropped.join(", ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
