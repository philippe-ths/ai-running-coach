"use client";

// #981: a plan whose written sessions have run out.
//
// A TrainingPlan holds concrete sessions for its near weeks and only a
// week-shape beyond them; nothing has ever turned a shape into sessions. So a
// runner training to a real, active plan can reach a week where `has_plan` is
// true but `sessions` is empty, because the plan has simply not been written
// this far ahead yet. The week view used to fall back to its ordinary
// free-mode rendering for this, which left the runner staring at an empty
// week with no route forward: the "ask your coach" affordance for an empty
// week lives in `EmptyWeek`, which only ever renders when `has_plan` is false.
//
// This says the one true thing plainly: the plan is intact, it just has not
// been written this far ahead, rather than anything implying loss or a
// failure. Writing sessions changes what the runner agreed to, so the only
// action here opens the coach thread rather than calling an endpoint. There is
// no session-create endpoint, and writing one would bypass the offer-and-
// confirm route every other plan change goes through.

import { MessageCircle } from "lucide-react";

export default function PlanRunsOutBanner({ onAskCoach }: { onAskCoach?: () => void }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-300"
    >
      <p>
        Your plan does not have sessions written for this week yet. The plan
        itself is fine, your coach just has not written this far ahead.
      </p>

      {/* No control when the coach surface is off: a button that opens a
          sheet that cannot render would be offering the runner a thing this
          screen cannot do. */}
      {onAskCoach && (
        <button
          type="button"
          onClick={onAskCoach}
          className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-800 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700/60"
        >
          <MessageCircle size={12} aria-hidden="true" />
          Ask your coach to write the next weeks
        </button>
      )}
    </div>
  );
}
