"use client";

// #830: the runner states their own goal race.
//
// This is the one thing on the schedule the RUNNER writes. The plan is the
// coach's, but what it is built towards is not the coach's to decide — without
// a race stated, the coach is told "No race stated. Plan for general
// progression." and every phase in the plan is ungrounded. So an absent race is
// drawn as something worth fixing, not as an empty slot.
//
// It sits ABOVE the view tabs, visible in both, because the race belongs to the
// whole schedule rather than to the week or the horizon.
//
// UNITS. Runners think in kilometres; the API stores metres. The conversion
// lives here and only here, and the quick picks carry EXACT race distances —
// a half is 21097.5 m, not 21000, because a plan built against 21 km is
// building against the wrong race.

import { useCallback, useEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import { Flag, Loader2, Plus, Trash2, X } from "lucide-react";
import { fetchFromAPI } from "@/lib/api";
import { formatDateLabel } from "@/lib/format";
import type { GoalRace, RacePriority } from "@/lib/types/schedule";
import { parseIso, todayIso } from "./dates";

/** The exact distances, in metres. A half is 21097.5 m — never 21000. */
const QUICK_PICKS: { label: string; metres: number }[] = [
  { label: "5K", metres: 5000 },
  { label: "10K", metres: 10000 },
  { label: "Half", metres: 21097.5 },
  { label: "Marathon", metres: 42195 },
];

const PRIORITIES: { value: RacePriority; label: string; hint: string }[] = [
  { value: "A", label: "A", hint: "The race the block is built towards" },
  { value: "B", label: "B", hint: "Run properly, but not the target" },
  { value: "C", label: "C", hint: "A workout with a number on your chest" },
];

/**
 * The runner's own words for a stored distance.
 *
 * A stored 21097.5 has to read back as "Half marathon", or the runner cannot
 * tell whether the exact distance was kept. Anything off the four canonical
 * distances is shown as the kilometres it is.
 */
function formatDistance(metres: number): string {
  const named: Record<number, string> = {
    5000: "5K",
    10000: "10K",
    21097.5: "Half marathon",
    42195: "Marathon",
  };
  if (named[metres]) return named[metres];
  const km = metres / 1000;
  return `${km % 1 === 0 ? km.toFixed(0) : km.toFixed(1)} km`;
}

/** Whole days from today to an ISO calendar day. Local-component maths, so a
 *  viewer behind UTC does not read a race as a day nearer than it is. */
function daysUntil(iso: string): number {
  const from = parseIso(todayIso()).getTime();
  const to = parseIso(iso).getTime();
  return Math.round((to - from) / 86400000);
}

/** "7 weeks away". Days while the race is close enough to count them. */
function describeGap(iso: string): string {
  const days = daysUntil(iso);
  if (days < 0) return "already run";
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 14) return `${days} days away`;
  const weeks = Math.round(days / 7);
  return `${weeks} weeks away`;
}

export default function GoalRacePanel({
  hasPlan,
  onChanged,
  onAskCoach,
}: {
  hasPlan: boolean;
  /** Fired after a successful create or delete: the week AND the horizon both
   *  carry the race, so both are refetched by the parent. */
  onChanged: () => void;
  onAskCoach?: (message: string) => void;
}) {
  const [races, setRaces] = useState<GoalRace[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const data: GoalRace[] | null = await fetchFromAPI("/api/schedule/races");
      setRaces(Array.isArray(data) ? data : []);
      setError(null);
    } catch {
      setError("Could not load your races.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // One write at a time, and the guard is a REF: two taps in the same tick both
  // close over the same state, so only a synchronous flag stops the second.
  const busy = useRef(false);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const created = useCallback(async () => {
    setFormOpen(false);
    await load();
    onChanged();
  }, [load, onChanged]);

  const remove = useCallback(
    async (race: GoalRace) => {
      if (busy.current) return;
      busy.current = true;
      setPendingId(race.id);
      setError(null);
      try {
        await fetchFromAPI(`/api/schedule/races/${race.id}`, { method: "DELETE" });
        await load();
        onChanged();
      } catch {
        setError("That did not save. Nothing has changed — try again.");
      } finally {
        busy.current = false;
        setPendingId(null);
      }
    },
    [load, onChanged],
  );

  const goal = races?.[0];
  const rest = races?.slice(1) ?? [];
  // The nudge only exists to say the plan can now be anchored. Once a plan
  // exists it has nothing left to tell the runner, so it stops.
  const showNudge = !!goal && !hasPlan;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-300">
          <Flag size={15} aria-hidden="true" className="text-rose-600 dark:text-rose-400" />
          Goal race
        </h2>
        {races && races.length > 0 && !formOpen && (
          <button
            type="button"
            onClick={() => setFormOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400 dark:hover:bg-blue-900/30"
          >
            <Plus size={13} aria-hidden="true" />
            Add another race
          </button>
        )}
      </div>

      {error && (
        <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      {/* Only while the read is still outstanding. A failed read already has its
          message above, and printing "Loading…" under it would promise a result
          that is not coming. */}
      {races === null && !error && (
        <p className="mt-3 text-sm text-gray-400 dark:text-gray-500">Loading…</p>
      )}

      {races !== null && races.length === 0 && !formOpen && (
        <div className="mt-2">
          <p className="max-w-prose text-sm text-gray-600 dark:text-gray-400">
            You have not told your coach what you are training for. Without a race
            your plan is built for general progression — no date to peak for, no
            taper, no phases that mean anything. Name the race and the whole plan
            gets a target.
          </p>
          <button
            type="button"
            onClick={() => setFormOpen(true)}
            className="mt-3 inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:bg-blue-600 dark:hover:bg-blue-500"
          >
            <Flag size={15} aria-hidden="true" />
            Set your goal race
          </button>
        </div>
      )}

      {goal && (
        <div className="mt-3 space-y-2">
          <RaceRow
            race={goal}
            primary
            pending={pendingId === goal.id}
            onRemove={remove}
          />
          {rest.map((race) => (
            <RaceRow
              key={race.id}
              race={race}
              primary={false}
              pending={pendingId === race.id}
              onRemove={remove}
            />
          ))}
        </div>
      )}

      {showNudge && (
        <div className="mt-3 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900 dark:border-blue-800 dark:bg-blue-900/25 dark:text-blue-200">
          <p>
            Your coach can now build towards {goal.name}. Ask for a plan and it
            will be phased against that date rather than written for general
            progression.
          </p>
          {onAskCoach && (
            <button
              type="button"
              onClick={() =>
                onAskCoach(
                  `${goal.name} on ${formatDateLabel(goal.race_date)} is my goal race — ` +
                    `${formatDistance(goal.distance_m)}, ${describeGap(goal.race_date)}. ` +
                    `Can we build a plan towards it?`,
                )
              }
              className="mt-2 inline-flex items-center gap-1.5 rounded-md px-2 py-1 -ml-2 text-xs font-medium text-blue-800 hover:bg-blue-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-200 dark:hover:bg-blue-900/50"
            >
              Ask your coach to build towards it
            </button>
          )}
        </div>
      )}

      {formOpen && (
        <RaceForm
          onCancel={() => setFormOpen(false)}
          onCreated={created}
          busy={busy}
        />
      )}
    </section>
  );
}

function RaceRow({
  race,
  primary,
  pending,
  onRemove,
}: {
  race: GoalRace;
  primary: boolean;
  pending: boolean;
  onRemove: (race: GoalRace) => void;
}) {
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-md px-3 py-2 ${
        primary
          ? "border border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/40"
          : "border border-gray-100 bg-gray-50 dark:border-gray-700 dark:bg-gray-900/40"
      }`}
    >
      <div className="min-w-0">
        <p
          className={`truncate font-medium ${
            primary
              ? "text-rose-900 dark:text-rose-100"
              : "text-gray-800 dark:text-gray-200"
          }`}
        >
          {race.name}
          <span
            className={`ml-2 rounded px-1.5 py-0.5 align-middle text-[10px] font-semibold ${
              primary
                ? "bg-rose-600 text-white dark:bg-rose-500"
                : "bg-gray-300 text-gray-700 dark:bg-gray-600 dark:text-gray-100"
            }`}
          >
            {race.priority}
          </span>
        </p>
        <p
          className={`text-xs ${
            primary
              ? "text-rose-800 dark:text-rose-200"
              : "text-gray-600 dark:text-gray-400"
          }`}
        >
          <span className="font-mono tabular-nums">
            {formatDateLabel(race.race_date)}
          </span>
          {" · "}
          {formatDistance(race.distance_m)}
          {" · "}
          <span className="font-mono tabular-nums">{describeGap(race.race_date)}</span>
        </p>
      </div>
      <button
        type="button"
        onClick={() => onRemove(race)}
        disabled={pending}
        aria-label={`Remove ${race.name}`}
        className="rounded-md p-1.5 text-gray-500 hover:bg-gray-200 disabled:opacity-40 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-gray-400 dark:hover:bg-gray-700"
      >
        {pending ? (
          <Loader2 size={15} className="animate-spin" aria-hidden="true" />
        ) : (
          <Trash2 size={15} aria-hidden="true" />
        )}
      </button>
    </div>
  );
}

/**
 * The entry form.
 *
 * Validation happens BEFORE the POST, not after: a date in the past is not a
 * goal, and the runner is owed the reason rather than a 422 rendered as "that
 * did not save".
 */
function RaceForm({
  onCancel,
  onCreated,
  busy,
}: {
  onCancel: () => void;
  onCreated: () => void;
  busy: MutableRefObject<boolean>;
}) {
  const today = todayIso();
  const [name, setName] = useState("");
  const [raceDate, setRaceDate] = useState("");
  // null = one of the quick picks is selected; a string = the custom km field is
  // in play. Keeping the custom value as typed text lets "21.1" exist mid-entry
  // without the field fighting the runner.
  const [metres, setMetres] = useState<number | null>(QUICK_PICKS[2].metres);
  const [customKm, setCustomKm] = useState("");
  const [priority, setPriority] = useState<RacePriority>("A");
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const customMetres = (() => {
    const km = Number(customKm);
    if (!customKm.trim() || !Number.isFinite(km) || km <= 0) return null;
    // One decimal of a metre is as fine as any race is measured, and it keeps
    // 21.1 from arriving as 21099.999999999996.
    return Math.round(km * 1000 * 10) / 10;
  })();

  const chosenMetres = metres ?? customMetres;

  const submit = async () => {
    if (saving || busy.current) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setProblem("Give the race a name so you can recognise it later.");
      return;
    }
    if (!raceDate) {
      setProblem("Pick the date you are racing.");
      return;
    }
    if (raceDate < today) {
      setProblem(
        "That date has already passed. A goal race has to be ahead of you — pick the next one.",
      );
      return;
    }
    if (!chosenMetres || chosenMetres <= 0) {
      setProblem("Choose a distance, or type one in kilometres.");
      return;
    }

    setProblem(null);
    setSaving(true);
    busy.current = true;
    try {
      await fetchFromAPI("/api/schedule/races", {
        method: "POST",
        body: JSON.stringify({
          name: trimmed,
          race_date: raceDate,
          distance_m: chosenMetres,
          priority,
        }),
      });
      onCreated();
    } catch {
      setProblem("That did not save. Nothing has changed — try again.");
    } finally {
      busy.current = false;
      setSaving(false);
    }
  };

  const field =
    "rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";

  return (
    <div className="mt-4 border-t border-gray-100 pt-4 dark:border-gray-700">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
          Add a race
        </h3>
        <button
          type="button"
          onClick={onCancel}
          aria-label="Cancel adding a race"
          className="rounded-md p-1 text-gray-500 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-gray-400 dark:hover:bg-gray-700"
        >
          <X size={15} aria-hidden="true" />
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-4">
        <div className="min-w-[12rem] flex-1">
          <label
            htmlFor="race-name"
            className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400"
          >
            Race
          </label>
          <input
            id="race-name"
            type="text"
            value={name}
            maxLength={200}
            placeholder="Amsterdam Half"
            onChange={(e) => setName(e.target.value)}
            className={`${field} w-full`}
          />
        </div>
        <div>
          <label
            htmlFor="race-date"
            className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400"
          >
            Date
          </label>
          <input
            id="race-date"
            type="date"
            value={raceDate}
            min={today}
            onChange={(e) => setRaceDate(e.target.value)}
            className={`${field} font-mono tabular-nums`}
          />
        </div>
      </div>

      <fieldset className="mt-3">
        <legend className="mb-1 text-xs font-medium text-gray-600 dark:text-gray-400">
          Distance
        </legend>
        <div className="flex flex-wrap items-center gap-2">
          {QUICK_PICKS.map((pick) => {
            const selected = metres === pick.metres;
            return (
              <button
                key={pick.label}
                type="button"
                aria-pressed={selected}
                onClick={() => {
                  setMetres(pick.metres);
                  setCustomKm("");
                }}
                className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                  selected
                    ? "border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900"
                    : "border-gray-300 text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
                }`}
              >
                {pick.label}
              </button>
            );
          })}
          <span className="flex items-center gap-1.5">
            <label
              htmlFor="race-km"
              className="text-xs text-gray-600 dark:text-gray-400"
            >
              or
            </label>
            <input
              id="race-km"
              type="number"
              inputMode="decimal"
              min="0.1"
              step="0.1"
              value={customKm}
              placeholder="km"
              onChange={(e) => {
                setCustomKm(e.target.value);
                // Typing a distance IS choosing it; leaving a quick pick lit
                // beside a different number would show two answers at once.
                setMetres(null);
              }}
              className={`${field} w-24 font-mono tabular-nums`}
            />
          </span>
        </div>
        {/* The stored figure, shown back. A half is 21097.5 m and the runner is
            entitled to see that the exact distance was kept. */}
        <p className="mt-1.5 text-[11px] text-gray-500 dark:text-gray-400">
          {chosenMetres ? (
            <>
              Stored as{" "}
              <span className="font-mono tabular-nums">{chosenMetres}</span> m
            </>
          ) : (
            "Pick one, or type the distance in kilometres."
          )}
        </p>
      </fieldset>

      <fieldset className="mt-3">
        <legend className="mb-1 text-xs font-medium text-gray-600 dark:text-gray-400">
          Priority
        </legend>
        <div className="flex flex-wrap items-center gap-2">
          {PRIORITIES.map((p) => {
            const selected = priority === p.value;
            return (
              <button
                key={p.value}
                type="button"
                aria-pressed={selected}
                onClick={() => setPriority(p.value)}
                className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                  selected
                    ? "border-rose-600 bg-rose-600 text-white dark:border-rose-500 dark:bg-rose-500"
                    : "border-gray-300 text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
                }`}
              >
                {p.label}
              </button>
            );
          })}
          <span className="text-[11px] text-gray-500 dark:text-gray-400">
            {PRIORITIES.find((p) => p.value === priority)?.hint}
          </span>
        </div>
      </fieldset>

      {problem && (
        <p
          role="alert"
          className="mt-3 text-sm text-amber-700 dark:text-amber-300"
        >
          {problem}
        </p>
      )}

      <div className="mt-4 flex items-center gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        >
          {saving && <Loader2 size={15} className="animate-spin" aria-hidden="true" />}
          {saving ? "Saving…" : "Save race"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md px-3 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-gray-300 dark:hover:bg-gray-700"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
