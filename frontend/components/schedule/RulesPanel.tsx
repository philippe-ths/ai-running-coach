// #830: the rules in play, and anything the current placement cannot satisfy.
//
// A violation is INFORMATION, not an alarm: the week is the runner's, and a
// clash usually means life happened. Amber, the house's "up/notable" colour,
// never red.

import { AlertTriangle } from "lucide-react";
import type { RuleViolation, SpacingRuleRead } from "@/lib/types/schedule";

export default function RulesPanel({
  rules,
  violations,
}: {
  rules: SpacingRuleRead[];
  violations: RuleViolation[];
}) {
  if (!rules.length && !violations.length) return null;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">Rules in play</h2>

      {rules.length > 0 ? (
        <ul className="mt-2 space-y-1.5">
          {rules.map((rule, i) => (
            <li
              key={`${rule.kind}-${i}`}
              className="flex items-start gap-2 text-xs text-gray-600 dark:text-gray-400"
            >
              <span
                aria-hidden="true"
                className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-gray-400 dark:bg-gray-500"
              />
              <span>
                {rule.statement}
                {rule.source === "runner" && (
                  <span className="ml-1.5 text-[10px] uppercase tracking-wider text-gray-400 dark:text-gray-500">
                    yours
                  </span>
                )}
                {/* The coach's own phrasing/reasoning, subordinate to the rule
                    above — suppressed when it says nothing the statement did
                    not already say. */}
                {rule.label && rule.label !== rule.statement && (
                  <span className="block text-[11px] text-gray-400 dark:text-gray-500">
                    {rule.label}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
          No spacing rules on this week.
        </p>
      )}

      {violations.length > 0 && (
        <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-900/20">
          <h3 className="flex items-center gap-1.5 text-xs font-semibold text-amber-800 dark:text-amber-300">
            <AlertTriangle size={13} aria-hidden="true" />
            {violations.length === 1
              ? "One rule this week cannot satisfy"
              : `${violations.length} rules this week cannot satisfy`}
          </h3>
          <ul className="mt-2 space-y-1.5">
            {violations.map((v, i) => (
              <li key={`${v.kind}-${i}`} className="text-xs text-amber-800 dark:text-amber-200">
                <span className="font-medium">{v.statement}</span>
                <span className="text-amber-700 dark:text-amber-300"> — {v.detail}</span>
                {v.label && v.label !== v.statement && (
                  <span className="block text-[11px] text-amber-700/70 dark:text-amber-300/70">
                    {v.label}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
