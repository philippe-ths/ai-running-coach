"use client";

import { useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";

/**
 * A small tappable (i) affordance that reveals a one-line explanation. Built for
 * mobile: it toggles on tap (not hover, which never fires on touch) and closes on
 * an outside tap, mirroring ActivityTypeFilter's outside-click pattern.
 */
export default function InfoPopover({
  label,
  text,
}: {
  /** Accessible name for the trigger (e.g. "What does vs typical mean?"). */
  label: string;
  /** The explanation shown when the popover is open. */
  text: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  // Close on outside tap/click.
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <span ref={ref} className="relative inline-flex">
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex p-1 -m-1 text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 transition-colors"
      >
        <Info className="w-3 h-3" aria-hidden="true" />
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute left-0 top-full mt-1 z-20 w-56 max-w-[calc(100vw-2rem)] rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-2 text-xs font-normal normal-case leading-snug text-gray-600 dark:text-gray-300 shadow-lg"
        >
          {text}
        </span>
      )}
    </span>
  );
}
