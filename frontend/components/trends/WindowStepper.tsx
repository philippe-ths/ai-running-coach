"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { formatDateLabel } from "@/lib/format";

interface Props {
  // The window currently on screen (ISO dates), so the label always states
  // what is actually shown rather than assuming "today" (#948).
  periodStart: string;
  periodEnd: string;
  canStepBack: boolean;
  canStepForward: boolean;
  onStepBack: () => void;
  onStepForward: () => void;
}

/** Previous/next arrows + a "what window is this" label, matching the Load
 * page's week-stepper treatment (chevron buttons either side of a centered
 * label) so the three window-navigation surfaces read as one product. */
export default function WindowStepper({
  periodStart,
  periodEnd,
  canStepBack,
  canStepForward,
  onStepBack,
  onStepForward,
}: Props) {
  return (
    <div className="inline-flex items-center gap-1">
      <button
        type="button"
        aria-label="Previous period"
        onClick={onStepBack}
        disabled={!canStepBack}
        className="p-2 rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-30"
      >
        <ChevronLeft size={18} />
      </button>
      <span className="text-sm font-medium text-gray-600 dark:text-gray-400 min-w-[9.5rem] text-center">
        {formatDateLabel(periodStart)} – {formatDateLabel(periodEnd)}
      </span>
      <button
        type="button"
        aria-label="Next period"
        onClick={onStepForward}
        disabled={!canStepForward}
        className="p-2 rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-30"
      >
        <ChevronRight size={18} />
      </button>
    </div>
  );
}
