"use client";

// #830: the schedule week — what the runner is doing next.
//
// Read-only over the plan itself: the coach writes it, so there is no
// create-a-session control here and there never will be one. The three writes
// this page makes are the runner's own record of their week — tick, untick,
// decline a suggestion — and all three answer 204 with no body, so each is
// followed by a refetch rather than a local patch: a tick moves the headline,
// the done count AND the discipline mix, which no client can correctly patch.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { fetchFromAPI } from "@/lib/api";
import { formatDateLabel } from "@/lib/format";
import { useCoachSheet } from "@/components/coach/CoachSheetContext";
import type { LoggedActivity, PlannedSession, ScheduleWeek } from "@/lib/types/schedule";
import WeekHeader from "@/components/schedule/WeekHeader";
import DayStrip from "@/components/schedule/DayStrip";
import SessionCard from "@/components/schedule/SessionCard";
import RulesPanel from "@/components/schedule/RulesPanel";
import LoggedList from "@/components/schedule/LoggedList";
import EmptyWeek from "@/components/schedule/EmptyWeek";
import PlanRunsOutBanner from "@/components/schedule/PlanRunsOutBanner";
import DraftBanner from "@/components/schedule/DraftBanner";
import IncomingChangeBanner from "@/components/schedule/IncomingChangeBanner";
import { useAmendmentStatus } from "@/lib/useAmendmentStatus";
import PreviousPlanBanner from "@/components/schedule/PreviousPlanBanner";
import HorizonView from "@/components/schedule/HorizonView";
import GoalRacePanel from "@/components/schedule/GoalRacePanel";
import { addDaysIso, todayIso } from "@/components/schedule/dates";

type View = "week" | "horizon";

const PLACEMENT_RANK: Record<string, number> = { pinned: 0, window: 1, week: 2 };

function sortSessions(sessions: PlannedSession[]): PlannedSession[] {
  return [...sessions].sort((a, b) => {
    if (a.window_start !== b.window_start) {
      return a.window_start < b.window_start ? -1 : 1;
    }
    return (PLACEMENT_RANK[a.placement] ?? 3) - (PLACEMENT_RANK[b.placement] ?? 3);
  });
}

export default function SchedulePage() {
  // The week stays the default view: it is what the runner is doing next, and
  // the horizon is the step back from it.
  const [view, setView] = useState<View>("week");
  // null = "the current week", which is what the API defaults to. Navigation
  // sets an explicit day and the API resolves it to the runner's own week
  // boundary, so the client never has to know where their week starts.
  const [weekParam, setWeekParam] = useState<string | null>(null);
  const [week, setWeek] = useState<ScheduleWeek | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  // Bumped when a goal race is added or removed. The week is refetched directly;
  // the horizon owns its own fetch, so it is told through this token — the race
  // marker is part of the horizon payload, not something the client can draw.
  const [raceToken, setRaceToken] = useState(0);
  // #857: bumped when the ACTIVE plan may have changed for a reason the
  // previous-plan offer cannot see (a draft landed). The offer re-reads itself
  // after its own restore, so it does not need telling about that one.
  const [planToken, setPlanToken] = useState(0);

  const coach = useCoachSheet();

  const load = useCallback(async (param: string | null, silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const qs = param ? `?week_start=${param}` : "";
      const data: ScheduleWeek | null = await fetchFromAPI(`/api/schedule/week${qs}`);
      if (data) {
        setWeek(data);
      } else if (!silent) {
        setError("Your schedule is not available right now.");
      }
    } catch {
      if (!silent) setError("Could not load your schedule.");
      else setActionError("Saved, but the week could not be refreshed. Reload to see it.");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(weekParam);
  }, [weekParam, load]);

  const refresh = useCallback(() => load(weekParam, true), [load, weekParam]);

  const onRaceChanged = useCallback(() => {
    load(weekParam, true);
    setRaceToken((t) => t + 1);
  }, [load, weekParam]);

  // A new plan landing, and going back to the previous one, both swap what the
  // whole schedule says: the week AND the horizon change together, so both are
  // told. #857.
  // #1003: an amendment rewrites one window, so unlike a draft it changes the
  // week without changing which plan is current. Only the week needs re-reading.
  const amendmentWatch = useAmendmentStatus(refresh);

  const onPlanChanged = useCallback(() => {
    load(weekParam, true);
    setRaceToken((t) => t + 1);
    setPlanToken((t) => t + 1);
  }, [load, weekParam]);

  // One in-flight write at a time. The guard is a REF, not the state: two taps
  // landing in the same tick both close over `pendingId === null` and both fire,
  // because the state has not re-rendered between them. The ref updates
  // synchronously, so the second tap sees the first.
  const busy = useRef(false);
  const runAction = useCallback(
    async (id: string, path: string, method: "POST" | "DELETE") => {
      if (busy.current) return;
      busy.current = true;
      setPendingId(id);
      setActionError(null);
      try {
        await fetchFromAPI(path, { method });
        await load(weekParam, true);
      } catch {
        setActionError("That did not save. Nothing has changed — try again.");
      } finally {
        busy.current = false;
        setPendingId(null);
      }
    },
    [load, weekParam],
  );

  const onComplete = useCallback(
    (id: string) => runAction(id, `/api/schedule/sessions/${id}/complete`, "POST"),
    [runAction],
  );
  const onUncomplete = useCallback(
    (id: string) => runAction(id, `/api/schedule/sessions/${id}/complete`, "DELETE"),
    [runAction],
  );
  const onDismiss = useCallback(
    (id: string) => runAction(id, `/api/schedule/sessions/${id}/dismiss`, "POST"),
    [runAction],
  );

  const live = useMemo(
    () => (week ? week.sessions.filter((s) => s.status !== "dismissed") : []),
    [week],
  );
  const committed = useMemo(
    () => sortSessions(live.filter((s) => s.commitment === "committed")),
    [live],
  );
  const suggested = useMemo(
    () => sortSessions(live.filter((s) => s.commitment === "suggested")),
    [live],
  );

  // #981: an active plan that has run out of written sessions for this week.
  // A plan holds concrete sessions for its near weeks and a shape beyond them,
  // so a runner can reach a week where `has_plan` is true and there is nothing
  // committed to do. That is the same week free mode covers, but a plan running
  // dry is a different situation from never having asked for one, and it earns
  // a different message and a different action.
  //
  // Gated on the committed sessions actually on screen rather than on
  // `headline.planned_sessions`, because those two can disagree: the headline
  // excludes a prescribed rest, so a week holding nothing but a rest day counts
  // zero there while the runner plainly has something written.
  //
  // Never for a week that has already gone. A past week the plan said nothing
  // about is history, and telling the runner their plan "has not been written
  // this far ahead" about last March offers a fix for something that is not a
  // problem. `week_end` compares as an ISO date string, which sorts
  // chronologically.
  const planRunsOut =
    !!week &&
    week.has_plan &&
    committed.length === 0 &&
    week.week_end >= todayIso();

  // A done session's actual measures come from the week's own logged rows, so
  // the card reports the same numbers the headline was summed from.
  const actualById = useMemo(() => {
    const map = new Map<string, LoggedActivity>();
    for (const a of week?.logged ?? []) {
      if (a.activity_id) map.set(a.activity_id, a);
    }
    return map;
  }, [week]);

  const label = week
    ? week.is_current_week
      ? "This week"
      : `${formatDateLabel(week.week_start)} – ${formatDateLabel(week.week_end)}`
    : "This week";

  const freeMode = !!week && week.headline.planned_sessions === 0;
  const isEmpty =
    !!week && !week.has_plan && week.sessions.length === 0 && week.logged.length === 0;

  const askCoach = () =>
    coach.openWith(
      week
        ? `Talk me through my week of ${formatDateLabel(week.week_start)} — what matters most, and what can move?`
        : "Talk me through my week — what matters most, and what can move?",
    );

  // #981: the plan-runs-dry prompt asks for sessions, not a rewrite, and names
  // the block already agreed on so the coach extends it rather than starting
  // over.
  const askForNextWeeks = () =>
    coach.openWith(
      week
        ? `My plan has no sessions written yet for the week of ${formatDateLabel(week.week_start)}. Could you write the next few weeks of sessions from the block we already agreed on?`
        : "My plan has no sessions written yet for this week. Could you write the next few weeks of sessions from the block we already agreed on?",
    );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Schedule</h1>
          <p className="mt-1 text-gray-600 dark:text-gray-400">
            The week ahead, and how it is going.
          </p>
        </div>
        {view === "week" && (
          <div className="flex items-center gap-1">
            <button
              type="button"
              aria-label="Previous week"
              onClick={() => week && setWeekParam(addDaysIso(week.week_start, -7))}
              disabled={!week}
              className="rounded-md p-2 text-gray-500 hover:bg-gray-100 disabled:opacity-30 dark:text-gray-400 dark:hover:bg-gray-700/50"
            >
              <ChevronLeft size={18} aria-hidden="true" />
            </button>
            <span className="min-w-[8rem] text-center text-sm font-medium text-gray-600 dark:text-gray-400">
              {label}
            </span>
            <button
              type="button"
              aria-label="Next week"
              onClick={() => week && setWeekParam(addDaysIso(week.week_start, 7))}
              disabled={!week}
              className="rounded-md p-2 text-gray-500 hover:bg-gray-100 disabled:opacity-30 dark:text-gray-400 dark:hover:bg-gray-700/50"
            >
              <ChevronRight size={18} aria-hidden="true" />
            </button>
          </div>
        )}
      </header>

      {/* Above the tabs, deliberately: the race the plan is built towards belongs
          to the whole schedule, not to one of its two views. */}
      <GoalRacePanel
        hasPlan={!!week?.has_plan}
        onChanged={onRaceChanged}
        onAskCoach={coach.enabled ? coach.openWith : undefined}
      />

      <ViewTabs view={view} onChange={setView} />

      {view === "horizon" && (
        <div id="panel-horizon" role="tabpanel" aria-labelledby="tab-horizon">
          <HorizonView refreshToken={raceToken} />
        </div>
      )}

      {view === "week" && (
        <div id="panel-week" role="tabpanel" aria-labelledby="tab-week" className="space-y-6">
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300">
          {error}
        </div>
      )}

      {actionError && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
          {actionError}
        </div>
      )}

      {loading && !week && (
        <div className="py-16 text-center text-gray-400 dark:text-gray-500">
          Loading your week…
        </div>
      )}

      {/* A plan being written lands on the week behind it, so the week is the
          place that has to say so. The empty-week panel reports its own draft,
          so this stands down for it rather than the two speaking at once. */}
      {week && !isEmpty && <DraftBanner onPlanReady={onPlanChanged} />}

      {/* #857: the way back to a plan the coach replaced, on the week the
          replacement landed on. Draws nothing when there is nothing to go back
          to, and shares the empty-week stand-down for the same reason the draft
          banner does: a runner with no plan at all has no superseded one. */}
      {week && !isEmpty && (
        <PreviousPlanBanner onRestored={onPlanChanged} refreshToken={planToken} />
      )}

      {/* #1003: over the week actually being rewritten, empty or not. An empty
          week is the commonest place to confirm one from, so this deliberately
          does NOT stand down for the empty state the way the draft banner does. */}
      {week && (
        <IncomingChangeBanner watch={amendmentWatch} weekStart={week.week_start} />
      )}

      {week && isEmpty && <EmptyWeek onPlanReady={refresh} />}

      {week && !isEmpty && (
        <>
          <WeekHeader
            week={week}
            freeMode={freeMode}
            onAskCoach={coach.enabled ? askCoach : undefined}
          />

          {/* #981: this stands in for the missing "Planned" section below,
              right where the runner would otherwise look for it. */}
          {planRunsOut && (
            <PlanRunsOutBanner onAskCoach={coach.enabled ? askForNextWeeks : undefined} />
          )}

          <DayStrip
            weekStart={week.week_start}
            sessions={live}
            logged={week.logged}
            today={todayIso()}
          />

          {committed.length > 0 && (
            <section>
              <h2 className="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-300">
                Planned
              </h2>
              <ul className="space-y-2">
                {committed.map((s) => (
                  <SessionCard
                    key={s.id}
                    session={s}
                    actual={
                      s.completed_activity_id
                        ? actualById.get(s.completed_activity_id)
                        : undefined
                    }
                    weekStart={week.week_start}
                    weekEnd={week.week_end}
                    pending={pendingId === s.id}
                    onComplete={onComplete}
                    onUncomplete={onUncomplete}
                    onDismiss={onDismiss}
                  />
                ))}
              </ul>
            </section>
          )}

          {suggested.length > 0 && (
            <section>
              <h2 className="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-300">
                Suggested
              </h2>
              <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
                Offers, not commitments. Declining one changes nothing else.
              </p>
              <ul className="space-y-2">
                {suggested.map((s) => (
                  <SessionCard
                    key={s.id}
                    session={s}
                    weekStart={week.week_start}
                    weekEnd={week.week_end}
                    pending={pendingId === s.id}
                    onComplete={onComplete}
                    onUncomplete={onUncomplete}
                    onDismiss={onDismiss}
                  />
                ))}
              </ul>
            </section>
          )}

          <RulesPanel rules={week.rules} violations={week.violations} />

          <LoggedList logged={week.logged} />
        </>
      )}
        </div>
      )}
    </div>
  );
}

const VIEWS: { value: View; label: string }[] = [
  { value: "week", label: "This week" },
  { value: "horizon", label: "Next 3 months" },
];

/**
 * The two views of one schedule.
 *
 * A real tablist rather than two links: both views are the same page and the
 * week must stay the default. Roving tabindex plus arrow keys is the WAI
 * pattern — one Tab stop for the group, arrows to move within it — so the
 * control is reachable and operable without a pointer.
 */
function ViewTabs({
  view,
  onChange,
}: {
  view: View;
  onChange: (view: View) => void;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = VIEWS.findIndex((v) => v.value === view);
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % VIEWS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + VIEWS.length) % VIEWS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = VIEWS.length - 1;
    else return;
    event.preventDefault();
    onChange(VIEWS[next].value);
    // The newly selected tab is the group's only tab stop, so focus has to
    // follow the selection or the next Tab press would leave the group.
    document.getElementById(`tab-${VIEWS[next].value}`)?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label="Schedule view"
      onKeyDown={onKeyDown}
      className="flex gap-1 border-b border-gray-200 dark:border-gray-700"
    >
      {VIEWS.map((v) => {
        const selected = v.value === view;
        return (
          <button
            key={v.value}
            id={`tab-${v.value}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={`panel-${v.value}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(v.value)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
              selected
                ? "border-blue-600 text-blue-700 dark:border-blue-400 dark:text-blue-300"
                : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-gray-600 dark:hover:text-gray-200"
            }`}
          >
            {v.label}
          </button>
        );
      })}
    </div>
  );
}
