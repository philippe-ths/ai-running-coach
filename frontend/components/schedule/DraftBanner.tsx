"use client";

// #879: a plan being written, on a week that already has one.
//
// The empty-week panel has always reported this, so a runner with no plan was
// told a draft had started, landed or failed. A runner who already had a plan —
// which is every runner asking for a NEW one — was told none of it, because the
// only thing watching lived inside a panel their week never rendered. A draft
// then failed in production and both the runner and the coach spent four turns
// believing the plan was in.
//
// It draws nothing at rest. A plan that landed needs no announcement: the week
// under it changes, which is the announcement.

import { Loader2 } from "lucide-react";
import { useDraftStatus } from "@/lib/useDraftStatus";

export default function DraftBanner({ onPlanReady }: { onPlanReady: () => void }) {
  const { draft, drafting, failed, starting, error, start } = useDraftStatus(onPlanReady);

  if (!drafting && !failed) return null;

  return (
    <div
      // Polite rather than assertive: this is progress on something the runner
      // asked for, not an interruption.
      role="status"
      aria-live="polite"
      className={`flex items-start gap-2 rounded-md border p-3 text-sm ${
        failed
          ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200"
          : "border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-300"
      }`}
    >
      {drafting && (
        <Loader2 size={16} className="mt-0.5 shrink-0 animate-spin" aria-hidden="true" />
      )}
      <div className="space-y-2">
        {/* Verbatim: the message is written for the runner, and paraphrasing it
            here would be a second voice speaking for the coach. */}
        <p>{error ?? draft?.message}</p>

        {/* The message says "ask again", and until this button existed nothing on
            a week that already has a plan could: the only draft control lives in
            the empty-week panel, which this runner's week never renders. Telling
            someone to do a thing on a screen with no way to do it is the same
            defect as not telling them at all. */}
        {failed && (
          <button
            type="button"
            onClick={() => void start()}
            disabled={starting}
            className="inline-flex items-center gap-1.5 rounded-md border border-amber-300 px-2.5 py-1 text-xs font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-900/40"
          >
            {starting && <Loader2 size={12} className="animate-spin" aria-hidden="true" />}
            {starting ? "Asking…" : "Ask again"}
          </button>
        )}
      </div>
    </div>
  );
}
