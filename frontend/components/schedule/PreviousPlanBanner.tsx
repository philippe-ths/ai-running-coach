"use client";

// #857: the way back to a plan the coach replaced.
//
// Writing a plan supersedes the one the runner was training to. Nothing was ever
// destroyed, but nothing could reach the old plan either, which made drafting
// the only proposed action in the set that did not come back with a tap. The
// route back has to be HERE, on the week the replacement landed on, because that
// is where the runner notices their plan has changed.
//
// It draws nothing at rest. A runner who has only ever had one plan has nothing
// to go back to, and a banner saying so on every visit would be noise.
//
// The write answers 204 and changes the headline, the sessions, the rules and
// the horizon at once, so it is followed by a refetch of the week rather than a
// local patch, the same reason the tick, untick and dismiss controls refetch.

import { useCallback, useEffect, useState } from "react";
import { Loader2, Undo2 } from "lucide-react";
import { fetchFromAPI } from "@/lib/api";
import { formatDateLabel } from "@/lib/format";
import type { PreviousPlan } from "@/lib/types/schedule";

export default function PreviousPlanBanner({
  onRestored,
  refreshToken = 0,
}: {
  /** The week has changed underneath: refetch it. */
  onRestored: () => void;
  /** Bumped by the owner when the plan may have changed for another reason
   *  (a draft landed), so the offer is re-read rather than going stale. */
  refreshToken?: number;
}) {
  const [previous, setPrevious] = useState<PreviousPlan | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data: PreviousPlan | null = await fetchFromAPI("/api/schedule/plans/previous");
      setPrevious(data ?? null);
    } catch {
      // Silent. This is an extra way back, not the screen's own content: a
      // runner whose week loaded fine should not be told an offer they never
      // asked for could not be fetched.
      setPrevious(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const restore = useCallback(async () => {
    if (!previous?.plan_id || restoring) return;
    setRestoring(true);
    setError(null);
    try {
      await fetchFromAPI(`/api/schedule/plans/${previous.plan_id}/restore`, {
        method: "POST",
      });
      await load();
      onRestored();
    } catch {
      setError("That did not go through. Nothing has changed, so try again.");
    } finally {
      setRestoring(false);
    }
  }, [previous, restoring, load, onRestored]);

  if (!previous?.plan_id) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-300"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          {/* Verbatim: the message is written for the runner, and a second
              wording here would be a second voice speaking for the coach. */}
          <p>{previous.message}</p>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {describe(previous)}
          </p>
        </div>

        {/* Only when the server says it can be restored. Offering a button that
            answers 422 would be telling the runner to do a thing the screen
            cannot do: the defect #879 was raised for, in reverse. */}
        {previous.restorable && (
          <button
            type="button"
            onClick={() => void restore()}
            disabled={restoring}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-800 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700/60"
          >
            {restoring ? (
              <Loader2 size={12} className="animate-spin" aria-hidden="true" />
            ) : (
              <Undo2 size={12} aria-hidden="true" />
            )}
            {restoring ? "Going back…" : "Go back to it"}
          </button>
        )}
      </div>

      {error && (
        <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>
      )}
    </div>
  );
}

/**
 * What going back would actually give you.
 *
 * Two facts, both of which the runner needs and neither of which the other
 * implies: when the plan was written (how old its thinking is) and how much of
 * it still lies ahead (whether it is worth going back to at all).
 */
function describe(previous: PreviousPlan): string {
  const parts: string[] = [];
  if (previous.generated_at) {
    parts.push(`Written ${formatDateLabel(previous.generated_at.slice(0, 10))}`);
  }
  parts.push(
    previous.sessions_ahead === 1
      ? "1 session still ahead"
      : `${previous.sessions_ahead} sessions still ahead`,
  );
  return parts.join(" · ");
}
