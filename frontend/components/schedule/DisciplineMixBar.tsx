// #830: the week's load split by discipline.
//
// Sized by effort_score, the only unit that sums across a gym session and a
// turbo ride. ONE hue in stepped shades, because intent already owns colour;
// solid = done, faded = still to come.
//
// It shows PLANNED AND LOGGED load together, deliberately: a bike ride nobody
// prescribed is still load in the runner's week, and hiding it would make the
// bar disagree with the sessions listed below it. #885 is the consequence of
// saying so badly — the caption read "Planned load by discipline" while a
// runner whose plan was four runs saw Bike, Walk and Strength under it, none of
// which their plan had asked for.

import type { DisciplineLoad } from "@/lib/types/schedule";
import { DISCIPLINE_FILL, DISCIPLINE_LABEL, safeDiscipline } from "./palette";

type Mode = "planned" | "logged";

interface Segment {
  key: string;
  label: string;
  fill: string;
  total: number;
  done: number;
  sessions: number;
}

function buildSegments(items: DisciplineLoad[], mode: Mode): Segment[] {
  return items
    .map((item) => {
      const d = safeDiscipline(item.discipline);
      // In planned mode a discipline the runner overshot still has to fit, so
      // the segment is the larger of the two and "done" is capped by it.
      const total =
        mode === "planned"
          ? Math.max(item.planned_effort_score, item.logged_effort_score)
          : item.logged_effort_score;
      const done = Math.min(item.logged_effort_score, total);
      return {
        key: d,
        label: DISCIPLINE_LABEL[d],
        fill: DISCIPLINE_FILL[d],
        total,
        done,
        // Counted the same way the segment is SIZED (#885). It used to be
        // "planned, or logged if nothing was planned", so the number meant
        // planned sessions on one row of the legend and logged sessions on the
        // next, with nothing to tell them apart. This is how many sessions the
        // discipline accounts for in the week the bar is drawing.
        sessions:
          mode === "planned"
            ? Math.max(item.planned_sessions, item.logged_sessions)
            : item.logged_sessions,
      };
    })
    .filter((s) => s.total > 0)
    .sort((a, b) => b.total - a.total);
}

export default function DisciplineMixBar({
  items,
  mode,
}: {
  items: DisciplineLoad[];
  mode: Mode;
}) {
  const segments = buildSegments(items, mode);
  const sum = segments.reduce((acc, s) => acc + s.total, 0);
  // "This week's", not "Planned": the bar carries planned and logged load
  // together, so a caption claiming the plan asked for all of it puts work the
  // runner chose to do into a plan they never agreed to (#885).
  const caption =
    mode === "planned"
      ? "This week's load by discipline · solid = done"
      : "Logged load by discipline";

  if (sum <= 0) {
    return (
      <div className="mt-4">
        <div className="h-3 w-full rounded-full bg-gray-100 dark:bg-gray-700" />
        <p className="mt-2 text-[11px] text-gray-400 dark:text-gray-500">
          No load on this week yet.
        </p>
      </div>
    );
  }

  // The text alternative for the bar: the same reading, in words. Proportions
  // rather than raw load — the bar shows a share of the week, and "37 of 90
  // load done" states a quantity the runner cannot act on or verify.
  const description = segments
    .map((s) => {
      const share = Math.round((s.total / sum) * 100);
      const done = s.total > 0 ? Math.round((s.done / s.total) * 100) : 0;
      return `${s.label} ${share} percent of the week, ${done} percent done`;
    })
    .join("; ");

  return (
    <div className="mt-4">
      <div
        className="flex h-3 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-700"
        role="img"
        aria-label={`${caption}. ${description}.`}
      >
        {segments.map((s) => {
          const donePct = (s.done / sum) * 100;
          const todoPct = ((s.total - s.done) / sum) * 100;
          return (
            <div key={s.key} className="flex h-full" style={{ width: `${(s.total / sum) * 100}%` }}>
              {donePct > 0 && (
                <div className={`h-full ${s.fill}`} style={{ width: `${(s.done / s.total) * 100}%` }} />
              )}
              {todoPct > 0 && (
                <div
                  className={`h-full opacity-30 ${s.fill}`}
                  style={{ width: `${((s.total - s.done) / s.total) * 100}%` }}
                />
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {segments.map((s) => (
          <span
            key={s.key}
            className="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400"
          >
            <span className={`h-2 w-2 shrink-0 rounded-sm ${s.fill}`} aria-hidden="true" />
            {s.label} {s.sessions}
          </span>
        ))}
      </div>
      <p className="mt-1.5 text-[11px] text-gray-400 dark:text-gray-500">{caption}</p>
    </div>
  );
}
