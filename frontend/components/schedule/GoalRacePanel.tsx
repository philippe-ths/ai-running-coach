"use client";

// #830, #1042: the runner states their own goals.
//
// This is the one thing on the schedule the RUNNER writes. The plan is the
// coach's, but what it is built towards is not the coach's to decide — without
// a goal stated, the coach plans for general progression and every phase in the
// plan is ungrounded. So an absent goal is drawn as something worth fixing, not
// as an empty slot.
//
// A goal is held as precisely as the runner holds it: a booked race on an exact
// date, a half "around March" with no event chosen, a volume block, a backyard
// ultra "someday". Booking a vague goal later is an EDIT of the same goal, so a
// plan already built towards it stays built towards it.
//
// It sits ABOVE the view tabs, visible in both, because the goals belong to the
// whole schedule rather than to the week or the horizon.
//
// UNITS. Runners think in kilometres; the API stores metres. The conversion
// lives here and only here, and the quick picks carry EXACT race distances —
// a half is 21097.5 m, not 21000, because a plan built against 21 km is
// building against the wrong race.

import { useCallback, useEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import { Flag, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { fetchFromAPI } from "@/lib/api";
import { formatDateLabel } from "@/lib/format";
import type { GoalRace, GoalRaceCreate, RacePriority } from "@/lib/types/schedule";
import { parseIso, todayIso, toIso } from "./dates";

/** The exact distances, in metres. A half is 21097.5 m — never 21000. */
const QUICK_PICKS: { label: string; metres: number }[] = [
  { label: "5K", metres: 5000 },
  { label: "10K", metres: 10000 },
  { label: "Half", metres: 21097.5 },
  { label: "Marathon", metres: 42195 },
];

const PRIORITIES: { value: RacePriority; label: string; hint: string }[] = [
  { value: "A", label: "A", hint: "The goal the block is built towards" },
  { value: "B", label: "B", hint: "Run properly, but not the target" },
  { value: "C", label: "C", hint: "A workout with a number on your chest" },
];

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

type When = "exact" | "around" | "none";

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

/** "1:40:00" or "47:00". */
function formatTarget(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = `${m}`.padStart(h ? 2 : 1, "0");
  const ss = `${s}`.padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** "1:40:00" or "47:00" → seconds; null when it is not a time. Two parts are
 *  always mm:ss, never guessed: "5:30" is a mile, not five and a half hours. The
 *  form reads the result back in words so "1:40" meant as a half is caught. */
function parseTarget(text: string): number | null {
  const parts = text.trim().split(":");
  if (parts.length < 2 || parts.length > 3) return null;
  if (parts.some((p) => !/^\d+$/.test(p))) return null;
  const n = parts.map(Number);
  if (n.slice(1).some((v) => v >= 60)) return null;
  const seconds = n.length === 3 ? n[0] * 3600 + n[1] * 60 + n[2] : n[0] * 60 + n[1];
  return seconds > 0 ? seconds : null;
}

/** "1 h 40 min", "47 min 30 s": the parsed target, said back. */
function sayDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return [h && `${h} h`, m && `${m} min`, s && `${s} s`].filter(Boolean).join(" ");
}

function monthLabel(iso: string): string {
  const d = parseIso(iso);
  return `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/** "8 Nov" / "~ Mar 2027" / "~ May–Jun 2027" / "No date". */
function describeWhen(goal: GoalRace): string {
  if (goal.race_date) {
    // The year only when it is not this one: "Mar 14" in October means next March.
    const year = parseIso(goal.race_date).getFullYear();
    const label = formatDateLabel(goal.race_date);
    return year === new Date().getFullYear() ? label : `${label} ${year}`;
  }
  if (goal.window_start && goal.window_end) {
    const a = parseIso(goal.window_start);
    const b = parseIso(goal.window_end);
    if (a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()) {
      return `~ ${monthLabel(goal.window_start)}`;
    }
    if (a.getFullYear() === b.getFullYear()) {
      return `~ ${MONTHS[a.getMonth()]}–${MONTHS[b.getMonth()]} ${b.getFullYear()}`;
    }
    return `~ ${monthLabel(goal.window_start)} – ${monthLabel(goal.window_end)}`;
  }
  return "No date";
}

/** The one date the plan works to: the exact date, else the start of the window. */
function readyBy(goal: GoalRace): string | null {
  return goal.race_date ?? goal.window_start;
}

/** Whole days from today to an ISO calendar day. Local-component maths, so a
 *  viewer behind UTC does not read a race as a day nearer than it is. */
function daysUntil(iso: string): number {
  const from = parseIso(todayIso()).getTime();
  const to = parseIso(iso).getTime();
  return Math.round((to - from) / 86400000);
}

/** "7 weeks away". Days while the goal is close enough to count them. A window
 *  that has opened reads as "now" rather than "already run". */
function describeGap(goal: GoalRace): string | null {
  const iso = readyBy(goal);
  if (!iso) return null;
  const days = daysUntil(iso);
  if (days < 0) return goal.race_date ? "already run" : "now";
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 14) return `${days} days away`;
  return `${Math.round(days / 7)} weeks away`;
}

/** One sentence about the goal, for the "ask your coach" prefill. */
function describeForCoach(goal: GoalRace): string {
  const bits = [describeWhen(goal)];
  if (goal.distance_m) bits.push(formatDistance(goal.distance_m));
  if (goal.target_time_s) bits.push(`target ${formatTarget(goal.target_time_s)}`);
  return `${goal.name} (${bits.join(", ")}) is my main goal. Can we build a plan towards it?`;
}

export default function GoalRacePanel({
  hasPlan,
  onChanged,
  onAskCoach,
}: {
  hasPlan: boolean;
  /** Fired after a successful create, edit or delete: the week AND the horizon
   *  both carry the goals, so both are refetched by the parent. */
  onChanged: () => void;
  onAskCoach?: (message: string) => void;
}) {
  const [races, setRaces] = useState<GoalRace[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // null = closed; "new" = adding; a goal = editing that goal.
  const [editing, setEditing] = useState<GoalRace | "new" | null>(null);

  const load = useCallback(async () => {
    try {
      const data: GoalRace[] | null = await fetchFromAPI("/api/schedule/races");
      setRaces(Array.isArray(data) ? data : []);
      setError(null);
    } catch {
      setError("Could not load your goals.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // One write at a time, and the guard is a REF: two taps in the same tick both
  // close over the same state, so only a synchronous flag stops the second.
  const busy = useRef(false);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const saved = useCallback(async () => {
    setEditing(null);
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

  // The goal the plan is built for: the runner's A, else the soonest dated one.
  const main =
    races?.find((r) => r.priority === "A" && readyBy(r)) ??
    races?.find((r) => readyBy(r)) ??
    null;
  // The nudge only exists to say the plan can now be anchored. Once a plan
  // exists it has nothing left to tell the runner, so it stops.
  const showNudge = !!main && !hasPlan;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-300">
          <Flag size={15} aria-hidden="true" className="text-rose-600 dark:text-rose-400" />
          Goals
        </h2>
        {races && races.length > 0 && editing === null && (
          <button
            type="button"
            onClick={() => setEditing("new")}
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400 dark:hover:bg-blue-900/30"
          >
            <Plus size={13} aria-hidden="true" />
            Add a goal
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

      {races !== null && races.length === 0 && editing === null && (
        <div className="mt-2">
          <p className="max-w-prose text-sm text-gray-600 dark:text-gray-400">
            You have not told your coach what you are training for. Without a goal
            your plan is built for general progression: nothing to peak for, no
            taper, no phases that mean anything. It does not need to be booked;
            &ldquo;a half, around March&rdquo; is enough to plan towards.
          </p>
          <button
            type="button"
            onClick={() => setEditing("new")}
            className="mt-3 inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:bg-blue-600 dark:hover:bg-blue-500"
          >
            <Flag size={15} aria-hidden="true" />
            Set a goal
          </button>
        </div>
      )}

      {races && races.length > 0 && (
        <div className="mt-3 space-y-2">
          {races.map((race) =>
            editing !== null && editing !== "new" && editing.id === race.id ? (
              <GoalForm
                key={race.id}
                goal={race}
                defaultPriority="A"
                onCancel={() => setEditing(null)}
                onSaved={saved}
                busy={busy}
              />
            ) : (
              <GoalRow
                key={race.id}
                race={race}
                primary={race.id === main?.id}
                pending={pendingId === race.id}
                onEdit={() => setEditing(race)}
                onRemove={remove}
              />
            ),
          )}
        </div>
      )}

      {showNudge && editing === null && (
        <div className="mt-3 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900 dark:border-blue-800 dark:bg-blue-900/25 dark:text-blue-200">
          <p>
            Your coach can now build towards {main.name}. Ask for a plan and it
            will be phased against that date rather than written for general
            progression.
          </p>
          {onAskCoach && (
            <button
              type="button"
              onClick={() => onAskCoach(describeForCoach(main))}
              className="mt-2 inline-flex items-center gap-1.5 rounded-md px-2 py-1 -ml-2 text-xs font-medium text-blue-800 hover:bg-blue-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-200 dark:hover:bg-blue-900/50"
            >
              Ask your coach to build towards it
            </button>
          )}
        </div>
      )}

      {editing === "new" && (
        <GoalForm
          goal={null}
          // A second A would compete with the first for what the block is built
          // towards, so a goal added beside one starts as B.
          defaultPriority={races?.some((r) => r.priority === "A") ? "B" : "A"}
          onCancel={() => setEditing(null)}
          onSaved={saved}
          busy={busy}
        />
      )}
    </section>
  );
}

function GoalRow({
  race,
  primary,
  pending,
  onEdit,
  onRemove,
}: {
  race: GoalRace;
  primary: boolean;
  pending: boolean;
  onEdit: () => void;
  onRemove: (race: GoalRace) => void;
}) {
  const gap = describeGap(race);
  const facts = [
    <span key="when" className="font-mono tabular-nums">{describeWhen(race)}</span>,
    race.distance_m ? <span key="dist">{formatDistance(race.distance_m)}</span> : null,
    race.target_time_s ? (
      <span key="target" className="font-mono tabular-nums">
        target {formatTarget(race.target_time_s)}
      </span>
    ) : null,
    gap ? <span key="gap" className="font-mono tabular-nums">{gap}</span> : null,
  ].filter(Boolean);

  return (
    <div
      className={`flex flex-wrap items-start justify-between gap-x-4 gap-y-1 rounded-md px-3 py-2 ${
        primary
          ? "border border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/40"
          : "border border-gray-100 bg-gray-50 dark:border-gray-700 dark:bg-gray-900/40"
      }`}
    >
      <div className="min-w-0 flex-1">
        <p
          className={`font-medium ${
            primary ? "text-rose-900 dark:text-rose-100" : "text-gray-800 dark:text-gray-200"
          }`}
        >
          <span className="break-words">{race.name}</span>
          <span
            className={`ml-2 rounded px-1.5 py-0.5 align-middle text-[10px] font-semibold ${
              primary
                ? "bg-rose-600 text-white dark:bg-rose-500"
                : "bg-gray-300 text-gray-700 dark:bg-gray-600 dark:text-gray-100"
            }`}
          >
            {race.priority}
          </span>
          {race.booked && (
            <span className="ml-1.5 rounded bg-emerald-100 px-1.5 py-0.5 align-middle text-[10px] font-semibold text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200">
              Booked
            </span>
          )}
        </p>
        <p
          className={`text-xs ${
            primary ? "text-rose-800 dark:text-rose-200" : "text-gray-600 dark:text-gray-400"
          }`}
        >
          {facts.map((f, i) => (
            <span key={i}>
              {i > 0 && " · "}
              {f}
            </span>
          ))}
        </p>
        {race.notes && (
          <p className="mt-1 whitespace-pre-line text-xs italic text-gray-600 dark:text-gray-400">
            {race.notes}
          </p>
        )}
      </div>
      <div className="flex items-center">
        <button
          type="button"
          onClick={onEdit}
          disabled={pending}
          aria-label={`Edit ${race.name}`}
          className="rounded-md p-1.5 text-gray-500 hover:bg-gray-200 disabled:opacity-40 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-gray-400 dark:hover:bg-gray-700"
        >
          <Pencil size={15} aria-hidden="true" />
        </button>
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
    </div>
  );
}

/** A month and year picked as two selects: works the same in every browser,
 *  which `<input type="month">` does not (desktop Safari renders it as text). */
function MonthPicker({
  id,
  value,
  onChange,
  years,
}: {
  id: string;
  value: { y: number; m: number };
  onChange: (v: { y: number; m: number }) => void;
  years: number[];
}) {
  const field =
    "rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";
  return (
    <span className="inline-flex gap-1">
      <select
        id={id}
        aria-label="Month"
        value={value.m}
        onChange={(e) => onChange({ ...value, m: Number(e.target.value) })}
        className={field}
      >
        {MONTHS.map((label, i) => (
          <option key={label} value={i}>
            {label}
          </option>
        ))}
      </select>
      <select
        aria-label="Year"
        value={value.y}
        onChange={(e) => onChange({ ...value, y: Number(e.target.value) })}
        className={field}
      >
        {years.map((y) => (
          <option key={y} value={y}>
            {y}
          </option>
        ))}
      </select>
    </span>
  );
}

function firstOfMonth(v: { y: number; m: number }): string {
  return toIso(new Date(v.y, v.m, 1));
}

function lastOfMonth(v: { y: number; m: number }): string {
  return toIso(new Date(v.y, v.m + 1, 0));
}

/**
 * The entry form, for a new goal or an edit of an existing one.
 *
 * Validation happens BEFORE the request, not after: a date in the past is not a
 * goal, and the runner is owed the reason rather than a 422 rendered as "that
 * did not save".
 */
function GoalForm({
  goal,
  defaultPriority,
  onCancel,
  onSaved,
  busy,
}: {
  goal: GoalRace | null;
  defaultPriority: RacePriority;
  onCancel: () => void;
  onSaved: () => void;
  busy: MutableRefObject<boolean>;
}) {
  const today = todayIso();
  const now = parseIso(today);
  const years = [0, 1, 2, 3].map((n) => now.getFullYear() + n);
  const startOf = (iso: string | null) => {
    const d = iso ? parseIso(iso) : now;
    return { y: d.getFullYear(), m: d.getMonth() };
  };

  const [name, setName] = useState(goal?.name ?? "");
  const [when, setWhen] = useState<When>(
    goal ? (goal.race_date ? "exact" : goal.window_start ? "around" : "none") : "around",
  );
  const [raceDate, setRaceDate] = useState(goal?.race_date ?? "");
  const [booked, setBooked] = useState(goal?.booked ?? false);
  const [from, setFrom] = useState(startOf(goal?.window_start ?? null));
  const [to, setTo] = useState(startOf(goal?.window_end ?? null));
  // A number = a quick pick or nothing yet; null + text = the custom km field;
  // "none" = deliberately no fixed distance (a volume block, a backyard ultra).
  const initialPick =
    goal?.distance_m == null
      ? goal
        ? "none"
        : QUICK_PICKS[2].metres
      : QUICK_PICKS.some((p) => p.metres === goal.distance_m)
        ? goal.distance_m
        : null;
  const [metres, setMetres] = useState<number | "none" | null>(initialPick);
  const [customKm, setCustomKm] = useState(
    initialPick === null && goal?.distance_m ? `${goal.distance_m / 1000}` : "",
  );
  const [target, setTarget] = useState(
    goal?.target_time_s ? formatTarget(goal.target_time_s) : "",
  );
  const [notes, setNotes] = useState(goal?.notes ?? "");
  const [priority, setPriority] = useState<RacePriority>(
    (goal?.priority as RacePriority) ?? defaultPriority,
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const customMetres = (() => {
    const km = Number(customKm);
    if (!customKm.trim() || !Number.isFinite(km) || km <= 0) return null;
    // One decimal of a metre is as fine as any race is measured, and it keeps
    // 21.1 from arriving as 21099.999999999996.
    return Math.round(km * 1000 * 10) / 10;
  })();
  const chosenMetres = metres === "none" ? null : metres ?? customMetres;

  const submit = async () => {
    if (saving || busy.current) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setProblem("Give the goal a name so you can recognise it later.");
      return;
    }
    const body: GoalRaceCreate = {
      name: trimmed,
      race_date: null,
      window_start: null,
      window_end: null,
      distance_m: chosenMetres,
      target_time_s: null,
      notes: notes.trim() || null,
      booked: false,
      priority,
    };
    if (when === "exact") {
      if (!raceDate) {
        setProblem("Pick the date, or choose “Around” if you only know roughly when.");
        return;
      }
      if (raceDate < today) {
        setProblem("That date has already passed. A goal has to be ahead of you.");
        return;
      }
      body.race_date = raceDate;
      body.booked = booked;
    } else if (when === "around") {
      body.window_start = firstOfMonth(from);
      body.window_end = lastOfMonth(to);
      if (body.window_end < body.window_start) {
        setProblem("The window ends before it starts.");
        return;
      }
      if (body.window_end < today) {
        setProblem("That window has already passed. A goal has to be ahead of you.");
        return;
      }
    }
    if (metres === null && !customMetres) {
      setProblem("Choose a distance, type one in kilometres, or pick “No fixed distance”.");
      return;
    }
    if (target.trim()) {
      const seconds = parseTarget(target);
      if (seconds === null) {
        setProblem("Write the target time like 1:40:00 or 47:00.");
        return;
      }
      body.target_time_s = seconds;
    }

    setProblem(null);
    setSaving(true);
    busy.current = true;
    try {
      await fetchFromAPI(goal ? `/api/schedule/races/${goal.id}` : "/api/schedule/races", {
        method: goal ? "PUT" : "POST",
        body: JSON.stringify(body),
      });
      onSaved();
    } catch {
      setProblem("That did not save. Nothing has changed — try again.");
    } finally {
      busy.current = false;
      setSaving(false);
    }
  };

  const field =
    "rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";
  const chip = (selected: boolean) =>
    `rounded-md border px-2.5 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
      selected
        ? "border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900"
        : "border-gray-300 text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
    }`;
  const label = "mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400";

  return (
    <div
      className={
        goal
          ? "rounded-md border border-blue-200 p-3 dark:border-blue-800"
          : "mt-4 border-t border-gray-100 pt-4 dark:border-gray-700"
      }
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
          {goal ? "Edit goal" : "Add a goal"}
        </h3>
        <button
          type="button"
          onClick={onCancel}
          aria-label="Cancel"
          className="rounded-md p-1 text-gray-500 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-gray-400 dark:hover:bg-gray-700"
        >
          <X size={15} aria-hidden="true" />
        </button>
      </div>

      <div className="mt-3">
        <label htmlFor="goal-name" className={label}>
          Goal
        </label>
        <input
          id="goal-name"
          type="text"
          value={name}
          maxLength={200}
          placeholder="Half marathon, or 10h a week through December"
          onChange={(e) => setName(e.target.value)}
          className={`${field} w-full`}
        />
      </div>

      <fieldset className="mt-3">
        <legend className={label}>When</legend>
        <div className="flex flex-wrap items-center gap-2">
          {(
            [
              ["exact", "Exact date"],
              ["around", "Around"],
              ["none", "No date"],
            ] as [When, string][]
          ).map(([value, text]) => (
            <button
              key={value}
              type="button"
              aria-pressed={when === value}
              onClick={() => setWhen(value)}
              className={chip(when === value)}
            >
              {text}
            </button>
          ))}
        </div>
        {when === "exact" && (
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <input
              id="goal-date"
              type="date"
              aria-label="Date"
              value={raceDate}
              min={today}
              onChange={(e) => setRaceDate(e.target.value)}
              className={`${field} font-mono tabular-nums`}
            />
            <label className="inline-flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={booked}
                onChange={(e) => setBooked(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300"
              />
              Booked
            </label>
          </div>
        )}
        {when === "around" && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
            <MonthPicker
              id="goal-from"
              value={from}
              years={years}
              onChange={(v) => {
                setFrom(v);
                // Most windows are one month; moving the start drags a stale end along.
                if (v.y * 12 + v.m > to.y * 12 + to.m) setTo(v);
              }}
            />
            <span>to</span>
            <MonthPicker id="goal-to" value={to} years={years} onChange={setTo} />
          </div>
        )}
        {when === "none" && (
          <p className="mt-1.5 text-[11px] text-gray-500 dark:text-gray-400">
            A direction rather than a deadline. Your coach will keep it in mind, but
            no plan is built backwards from it.
          </p>
        )}
      </fieldset>

      <fieldset className="mt-3">
        <legend className={label}>Distance</legend>
        <div className="flex flex-wrap items-center gap-2">
          {QUICK_PICKS.map((pick) => (
            <button
              key={pick.label}
              type="button"
              aria-pressed={metres === pick.metres}
              onClick={() => {
                setMetres(pick.metres);
                setCustomKm("");
              }}
              className={chip(metres === pick.metres)}
            >
              {pick.label}
            </button>
          ))}
          <span className="flex items-center gap-1.5">
            <label htmlFor="goal-km" className="text-xs text-gray-600 dark:text-gray-400">
              or
            </label>
            <input
              id="goal-km"
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
          <button
            type="button"
            aria-pressed={metres === "none"}
            onClick={() => {
              setMetres("none");
              setCustomKm("");
            }}
            className={chip(metres === "none")}
          >
            No fixed distance
          </button>
        </div>
        {/* The stored figure, shown back. A half is 21097.5 m and the runner is
            entitled to see that the exact distance was kept. */}
        {chosenMetres ? (
          <p className="mt-1.5 text-[11px] text-gray-500 dark:text-gray-400">
            Stored as <span className="font-mono tabular-nums">{chosenMetres}</span> m
          </p>
        ) : null}
      </fieldset>

      <div className="mt-3 flex flex-wrap gap-4">
        <div>
          <label htmlFor="goal-target" className={label}>
            Target time <span className="font-normal text-gray-400">(optional)</span>
          </label>
          <input
            id="goal-target"
            type="text"
            inputMode="numeric"
            value={target}
            placeholder="1:40:00"
            onChange={(e) => setTarget(e.target.value)}
            className={`${field} w-28 font-mono tabular-nums`}
          />
          {target.trim() && (
            <p className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
              {parseTarget(target) ? `= ${sayDuration(parseTarget(target)!)}` : "h:mm:ss or mm:ss"}
            </p>
          )}
        </div>
      </div>

      <fieldset className="mt-3">
        <legend className={label}>Priority</legend>
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

      <div className="mt-3">
        <label htmlFor="goal-notes" className={label}>
          Note for your coach <span className="font-normal text-gray-400">(optional)</span>
        </label>
        <textarea
          id="goal-notes"
          value={notes}
          maxLength={2000}
          rows={2}
          placeholder="Somewhere flat. First marathon, so finishing well matters more than the time."
          onChange={(e) => setNotes(e.target.value)}
          className={`${field} w-full`}
        />
      </div>

      {problem && (
        <p role="alert" className="mt-3 text-sm text-amber-700 dark:text-amber-300">
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
          {saving ? "Saving…" : "Save goal"}
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
