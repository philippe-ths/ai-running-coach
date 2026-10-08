"use client";

// #1064: the season, the coach's view of every goal together.
//
// A goal is whatever the runner wrote; the season is the coach's OPINION of it:
// what kind of goal it is, what achieving it means, when to do it, which real
// events would serve it, and how the months between now and then are spent. This
// card is read-only. The one write is "plan my season", and the goals themselves
// stay the runner's, edited in the Goals panel above.
//
// Placed directly under the Goals panel and above the view tabs, for the same
// reason the goals sit there: the season belongs to the whole schedule rather
// than to the week or the horizon, and the runner should read what the coach
// makes of their goals before the week that follows from them.
//
// Event names, links and reasons are sourced from the web. They are rendered as
// text nodes only (never HTML, never markdown) and a link is drawn only for an
// http(s) address.

import { Compass, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import type { GoalRace } from "@/lib/types/schedule";
import type {
  ChallengeStatus,
  GoalKind,
  GoalView,
  SeasonRead,
  SuggestedEvent,
} from "@/lib/types/season";
import { useSeason } from "@/lib/useSeason";
import { DISCIPLINE_LABEL, safeDiscipline } from "./palette";
import { addDaysIso, formatDayMonth, formatWhen, todayIso } from "./dates";
import { formatHeld, formatNeeded, metricLabel, metricUnit } from "./challenge";
import SeasonTimeline from "./SeasonTimeline";

const KIND_LABEL: Record<GoalKind, string> = {
  challenge: "Challenge",
  race: "Race",
  finish: "Finish well",
  completion: "Completion",
  someday: "Someday",
};

function kindLabel(kind: string): string {
  return KIND_LABEL[kind as GoalKind] ?? "Goal";
}

/** "At least 10 h in zone 2+ (any activity) every week for 10 weeks, from the week of 26 Oct" */
function describeRule(rule: ChallengeStatus["rule"]): string {
  const amount = `${formatNeeded(rule.metric, rule.at_least)} ${metricUnit(rule.metric, rule.at_least)}`;
  const scope = rule.disciplines.length
    ? rule.disciplines.map((d) => DISCIPLINE_LABEL[safeDiscipline(d)].toLowerCase()).join(" or ")
    : "any activity";
  const what =
    rule.metric === "zone_time_s"
      ? `${amount} in zone ${rule.min_zone ?? "?"}+`
      : rule.metric === "time_s"
        ? `${amount} of activity`
        : amount;
  return `At least ${what} (${scope}) every week for ${rule.weeks} weeks, from the week of ${formatDayMonth(rule.start)}`;
}

function eventWhen(e: SuggestedEvent): string | null {
  return formatWhen(e.date, e.window_start, e.window_end);
}

function eventDistance(metres: number | null): string | null {
  if (metres == null) return null;
  const km = metres / 1000;
  return `${km % 1 === 0 ? km.toFixed(0) : km.toFixed(1)} km`;
}

/** Only an http(s) address becomes a link; anything else stays plain text. */
function isWebUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

const ACTION_BUTTON =
  "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50";

export default function SeasonPanel({
  refreshToken = 0,
  onSeasonReady,
}: {
  /** Bumped by the page when the runner's goals change, so `stale` is re-read. */
  refreshToken?: number;
  /** Fired once when a season this card watched being written lands. */
  onSeasonReady?: () => void;
}) {
  const { season, loading, drafting, failed, starting, error, start } = useSeason(
    refreshToken,
    onSeasonReady,
  );

  return (
    <section
      aria-labelledby="season-heading"
      className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800 sm:p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2
          id="season-heading"
          className="flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-300"
        >
          <Compass size={15} aria-hidden="true" className="text-blue-600 dark:text-blue-400" />
          Your season
        </h2>
        {season?.status === "active" && season.plan && !season.stale && (
          <button
            type="button"
            onClick={() => void start()}
            disabled={starting || drafting}
            className={`${ACTION_BUTTON} text-xs text-blue-700 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-900/30`}
          >
            <RefreshCw size={13} aria-hidden="true" />
            Re-plan
          </button>
        )}
      </div>

      {loading && !season && (
        <p className="mt-3 text-sm text-gray-400 dark:text-gray-500">Loading your season…</p>
      )}

      {error && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>}

      {drafting && (
        <div
          role="status"
          aria-live="polite"
          className="mt-3 flex items-start gap-2 rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900/40 dark:text-gray-300"
        >
          <Loader2 size={16} className="mt-0.5 shrink-0 animate-spin" aria-hidden="true" />
          <p>
            Your coach is planning your season…
            <span className="block text-xs text-gray-500 dark:text-gray-400">
              This can take a few minutes. You can leave this page and come back.
            </span>
          </p>
        </div>
      )}

      {failed && !drafting && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
          <p>
            {season?.message ||
              "Your coach could not plan your season. Nothing has changed."}
          </p>
          <button
            type="button"
            onClick={() => void start()}
            disabled={starting}
            className={`${ACTION_BUTTON} mt-2 border border-amber-300 text-xs text-amber-900 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-900/40`}
          >
            {starting && <Loader2 size={12} className="animate-spin" aria-hidden="true" />}
            {starting ? "Asking…" : "Try again"}
          </button>
        </div>
      )}

      {!loading && !drafting && !failed && !(season?.status === "active" && season.plan) && (
        <NoSeason starting={starting} onStart={() => void start()} />
      )}

      {season?.status === "active" && season.plan && !drafting && (
        <ActiveSeason season={season} starting={starting} onReplan={() => void start()} />
      )}
    </section>
  );
}

function NoSeason({ starting, onStart }: { starting: boolean; onStart: () => void }) {
  return (
    <div className="mt-3">
      <p className="max-w-prose text-sm text-gray-600 dark:text-gray-400">
        Your coach has not looked at your goals together yet. It will say what each one
        means, when to do it, and how the months between now and then are spent.
      </p>
      <button
        type="button"
        onClick={onStart}
        disabled={starting}
        className={`${ACTION_BUTTON} mt-3 bg-blue-600 px-4 py-2 text-white hover:bg-blue-700`}
      >
        {starting && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
        {starting ? "Asking…" : "Plan my season"}
      </button>
    </div>
  );
}

function ActiveSeason({
  season,
  starting,
  onReplan,
}: {
  season: SeasonRead;
  starting: boolean;
  onReplan: () => void;
}) {
  const plan = season.plan!;
  const goalById = new Map<string, GoalRace>(season.goals.map((g) => [g.id, g]));
  const challengeById = new Map<string, ChallengeStatus>(
    season.challenges.map((c) => [c.goal_id, c]),
  );

  const timelineGoals = plan.goals.map((g) => {
    const row = goalById.get(g.goal_id);
    return {
      id: g.goal_id,
      name: row?.name ?? "Goal",
      // A booked date is the runner's own, whatever the plan says.
      day: (row?.booked ? row.race_date : null) ?? g.date ?? g.window_start,
    };
  });

  return (
    <div className="mt-3 space-y-4">
      {season.stale && (
        <div
          role="note"
          className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200"
        >
          <p>Your goals have changed since this season was planned.</p>
          <button
            type="button"
            onClick={onReplan}
            disabled={starting}
            className={`${ACTION_BUTTON} mt-2 border border-amber-300 text-xs text-amber-900 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-900/40`}
          >
            {starting ? (
              <Loader2 size={12} className="animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw size={12} aria-hidden="true" />
            )}
            {starting ? "Asking…" : "Re-plan my season"}
          </button>
        </div>
      )}

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Your coach&rsquo;s view
        </h3>
        <p className="mt-1 max-w-prose whitespace-pre-line text-sm text-gray-800 dark:text-gray-200">
          {plan.summary}
        </p>
      </div>

      {season.shortfalls.length > 0 && (
        <div
          role="note"
          className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200"
        >
          <p className="font-semibold">What this season cannot do yet</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {season.shortfalls.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <SeasonTimeline phases={plan.phases} goals={timelineGoals} />

      <ul className="divide-y divide-gray-100 dark:divide-gray-700">
        {plan.goals.map((g) => (
          <GoalRow
            key={g.goal_id}
            view={g}
            goal={goalById.get(g.goal_id) ?? null}
            status={challengeById.get(g.goal_id) ?? null}
          />
        ))}
      </ul>
    </div>
  );
}

function GoalRow({
  view,
  goal,
  status,
}: {
  view: GoalView;
  goal: GoalRace | null;
  status: ChallengeStatus | null;
}) {
  const booked = !!goal?.booked;
  // A booked goal's day is the runner's and is shown as theirs; for an unbooked
  // one the date is the coach's recommendation, and is labelled as such.
  const when = booked
    ? formatWhen(goal?.race_date, goal?.window_start, goal?.window_end) ??
      formatWhen(view.date, view.window_start, view.window_end)
    : formatWhen(view.date, view.window_start, view.window_end);
  const hasDetail = !!view.approach || view.events.length > 0;

  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          {goal?.name ?? "A goal you have since removed"}
        </h3>
        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-200">
          {kindLabel(view.kind)}
        </span>
        {booked && (
          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
            Booked
          </span>
        )}
      </div>

      <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">
        {when ? (
          <>
            <span className="text-gray-500 dark:text-gray-400">
              {booked ? "Booked for " : "Coach recommends "}
            </span>
            <span className="font-medium tabular-nums">{when}</span>
          </>
        ) : view.kind === "challenge" ? (
          // A challenge runs through weeks, stated by its rule below, not on a day.
          <span className="text-gray-500 dark:text-gray-400">Runs week by week</span>
        ) : (
          <span className="text-gray-500 dark:text-gray-400">No date</span>
        )}
      </p>

      <p className="mt-1 max-w-prose text-sm text-gray-700 dark:text-gray-300">
        <span className="font-medium">Success: </span>
        {view.success}
      </p>

      {view.challenge && <Challenge rule={view.challenge} status={status} />}

      {hasDetail && (
        <details className="group mt-2">
          <summary className="inline-flex cursor-pointer select-none items-center gap-1 rounded-md px-1 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400 dark:hover:bg-blue-900/30">
            {view.events.length > 0
              ? `Approach and ${view.events.length} ${view.events.length === 1 ? "event" : "events"} to consider`
              : "Approach"}
          </summary>
          <div className="mt-2 space-y-3 border-l-2 border-gray-200 pl-3 dark:border-gray-700">
            {view.approach && (
              <p className="max-w-prose whitespace-pre-line text-sm text-gray-700 dark:text-gray-300">
                {view.approach}
              </p>
            )}
            {view.events.length > 0 && (
              <ul className="space-y-2">
                {view.events.map((e, i) => (
                  <EventItem key={`${e.url}-${i}`} event={e} />
                ))}
              </ul>
            )}
          </div>
        </details>
      )}
    </li>
  );
}

function EventItem({ event }: { event: SuggestedEvent }) {
  const meta = [eventWhen(event), eventDistance(event.distance_m)].filter(Boolean).join(" · ");
  return (
    <li className="text-sm">
      {isWebUrl(event.url) ? (
        <a
          href={event.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-blue-700 underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400"
        >
          {event.name}
          <ExternalLink size={12} aria-hidden="true" />
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      ) : (
        <span className="font-medium text-gray-800 dark:text-gray-200">{event.name}</span>
      )}
      {meta && (
        <span className="block text-xs tabular-nums text-gray-500 dark:text-gray-400">{meta}</span>
      )}
      {event.why && (
        <span className="block text-xs text-gray-600 dark:text-gray-300">{event.why}</span>
      )}
    </li>
  );
}

/** The rule in words, and where the runner stands against it. */
function Challenge({
  rule,
  status,
}: {
  rule: ChallengeStatus["rule"];
  status: ChallengeStatus | null;
}) {
  const today = todayIso();
  const weeks = status?.weeks ?? [];
  const current = weeks.find((w) => w.week_start <= today && today < addDaysIso(w.week_start, 7));
  const notStarted = weeks.length > 0 && weeks[0].week_start > today;
  const missed = weeks.filter((w) => w.met === false);
  const label = metricLabel(rule.metric, rule.min_zone);

  let progress: string | null = null;
  if (status) {
    if (current) {
      progress = `Week ${current.index} of ${rule.weeks}`;
    } else if (notStarted) {
      progress = `Starts the week of ${formatDayMonth(weeks[0].week_start)}`;
    } else if (weeks.length > 0) {
      progress = `Finished: ${weeks.filter((w) => w.met).length} of ${rule.weeks} weeks met`;
    }
  }

  return (
    <div className="mt-2 rounded-md bg-gray-50 p-2.5 dark:bg-gray-900/40">
      <p className="max-w-prose text-sm text-gray-800 dark:text-gray-200">{describeRule(rule)}</p>

      {status && progress && (
        <p className="mt-1.5 text-xs font-medium text-gray-700 dark:text-gray-300">
          {progress}
          {!notStarted && (
            <span className="font-normal text-gray-500 dark:text-gray-400">
              {" "}
              · streak {status.streak}
              {missed.length > 0 && ` · missed: week${missed.length > 1 ? "s" : ""} ${missed.map((w) => w.index).join(", ")}`}
            </span>
          )}
        </p>
      )}

      {status && weeks.length > 0 && (
        <ol className="mt-2 flex flex-wrap gap-1" aria-label={`${label} weeks`}>
          {weeks.map((w) => {
            const isCurrent = current?.week_start === w.week_start;
            const state = w.met === true ? "met" : w.met === false ? "missed" : isCurrent ? "this week" : "ahead";
            const value = w.actual ?? w.planned;
            return (
              <li
                key={w.week_start}
                className={`flex h-6 w-6 items-center justify-center rounded text-[11px] font-semibold ${
                  w.met === true
                    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
                    : w.met === false
                      ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
                      : isCurrent
                        ? "border border-blue-600 text-blue-700 dark:border-blue-400 dark:text-blue-300"
                        : "border border-dashed border-gray-300 text-gray-400 dark:border-gray-600 dark:text-gray-500"
                }`}
                title={`Week ${w.index}, ${formatDayMonth(w.week_start)}: ${
                  value != null
                    ? `${formatHeld(rule.metric, value)} of ${formatNeeded(rule.metric, w.threshold)} ${metricUnit(rule.metric, w.threshold)}`
                    : "not planned yet"
                }, ${state}`}
              >
                <span aria-hidden="true">{w.met === true ? "✓" : w.met === false ? "✗" : w.index}</span>
                <span className="sr-only">
                  Week {w.index}, {state}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
