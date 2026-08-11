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
import { addDaysIso, todayIso } from "@/components/schedule/dates";

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
  // null = "the current week", which is what the API defaults to. Navigation
  // sets an explicit day and the API resolves it to the runner's own week
  // boundary, so the client never has to know where their week starts.
  const [weekParam, setWeekParam] = useState<string | null>(null);
  const [week, setWeek] = useState<ScheduleWeek | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

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

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Schedule</h1>
          <p className="mt-1 text-gray-600 dark:text-gray-400">
            The week ahead, and how it is going.
          </p>
        </div>
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
      </header>

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

      {week && isEmpty && <EmptyWeek onPlanReady={refresh} />}

      {week && !isEmpty && (
        <>
          <WeekHeader
            week={week}
            freeMode={freeMode}
            onAskCoach={coach.enabled ? askCoach : undefined}
          />

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
  );
}
