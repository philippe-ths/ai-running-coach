"use client";

// #1003: the week says a change is coming, and says when it arrives.
//
// A confirmed amendment is written on the worker. Until this existed the week
// underneath simply did not change, and the runner found out by reloading the
// page on a hunch. The draft has had `DraftBanner` since #879 for exactly this;
// the smaller verb had nothing.
//
// It draws only over the week actually being rewritten. An amendment names a
// window, and a runner looking at a week outside it should see no spinner over
// a week nothing is happening to.

import type { AmendmentWatch } from "@/lib/useAmendmentStatus";

export default function IncomingChangeBanner({
  watch,
  weekStart,
}: {
  watch: AmendmentWatch;
  weekStart: string;
}) {
  const { amendment, working, failed, covers, dismiss } = watch;
  if (!amendment?.status || !covers(weekStart)) return null;

  if (working) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-100"
      >
        <span
          aria-hidden="true"
          className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-amber-400 border-t-transparent"
        />
        <span>
          Your coach is writing this week. It will appear here in a moment.
        </span>
      </div>
    );
  }

  if (failed) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-start justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-100"
      >
        <span>
          Your coach could not write this week, so your plan is unchanged.
          {amendment.detail ? ` ${amendment.detail}.` : ""} There is more in your
          conversation.
        </span>
        <button
          type="button"
          onClick={() => void dismiss()}
          className="shrink-0 font-medium underline underline-offset-2"
        >
          Dismiss
        </button>
      </div>
    );
  }

  // Landed. Said once, then dismissed, so a runner who watched it arrive is not
  // told again every time they come back to the week.
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-start justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-950/40 dark:text-emerald-100"
    >
      <span>
        This week is in.
        {amendment.changes.length > 0 ? ` ${amendment.changes.join(" ")}` : ""}
      </span>
      <button
        type="button"
        onClick={() => void dismiss()}
        className="shrink-0 font-medium underline underline-offset-2"
      >
        Got it
      </button>
    </div>
  );
}
