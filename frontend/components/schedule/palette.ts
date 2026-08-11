// #830: the schedule's colour language.
//
// INTENT carries the read — an easy bike and an easy run are the same stimulus,
// so intent gets the colour. DISCIPLINE is named beside it and, in the mix bar
// only, drawn in ONE hue stepped light-to-dark, so it can never compete with
// intent for the eye. Every value carries an explicit dark twin: the house uses
// no CSS variables.

import type { Discipline, SessionIntent } from "@/lib/types/schedule";

export const INTENT_LABEL: Record<SessionIntent, string> = {
  rest: "Rest",
  easy: "Easy",
  long: "Long",
  quality: "Quality",
  strength: "Strength",
};

// Solid fills, tuned to the wireframe (#4f8a6d / #3f5c9a / #bd5138 / #a8a29e /
// #7c6aa8). Rest never uses one of these — it is drawn hollow and dashed.
export const INTENT_FILL: Record<SessionIntent, string> = {
  rest: "bg-stone-400 dark:bg-stone-500",
  easy: "bg-emerald-600 dark:bg-emerald-500",
  long: "bg-blue-700 dark:bg-blue-500",
  quality: "bg-orange-700 dark:bg-orange-500",
  strength: "bg-violet-500 dark:bg-violet-400",
};

export const INTENT_TEXT: Record<SessionIntent, string> = {
  rest: "text-stone-500 dark:text-stone-400",
  easy: "text-emerald-700 dark:text-emerald-400",
  long: "text-blue-700 dark:text-blue-400",
  quality: "text-orange-700 dark:text-orange-400",
  strength: "text-violet-600 dark:text-violet-400",
};

/** The card's left stripe. Rest is a dashed rule rather than a solid bar. */
export function intentStripe(intent: SessionIntent): string {
  if (intent === "rest") {
    return "border-l-2 border-dashed border-stone-400 dark:border-stone-500";
  }
  return INTENT_FILL[intent];
}

/** The day strip's pip. Rest reads as an absence, so it is hollow and dashed. */
export function intentPip(intent: SessionIntent): string {
  if (intent === "rest") {
    return "border-2 border-dashed border-stone-400 dark:border-stone-500 text-stone-500 dark:text-stone-400";
  }
  return `${INTENT_FILL[intent]} text-white`;
}

export const DISCIPLINE_LABEL: Record<Discipline, string> = {
  run: "Run",
  walk: "Walk",
  bike: "Bike",
  strength: "Strength",
  row: "Row",
  other: "Other",
};

// One hue, stepped. Run is the darkest step because it is the priority sport.
export const DISCIPLINE_FILL: Record<Discipline, string> = {
  run: "bg-blue-700 dark:bg-blue-500",
  bike: "bg-blue-600 dark:bg-blue-400",
  strength: "bg-blue-500 dark:bg-blue-300",
  row: "bg-blue-400 dark:bg-blue-200",
  walk: "bg-blue-300 dark:bg-blue-200",
  other: "bg-blue-200 dark:bg-blue-100",
};

// The letter carried inside a pip, so discipline never needs a second colour.
// Run has none: it is the default sport, and "R" is owed to Row.
export const DISCIPLINE_LETTER: Record<Discipline, string> = {
  run: "",
  walk: "W",
  bike: "B",
  strength: "S",
  row: "R",
  other: "·",
};

/** An unknown value from an LLM-written plan degrades rather than crashing. */
export function safeIntent(value: string): SessionIntent {
  return (value in INTENT_LABEL ? value : "easy") as SessionIntent;
}

export function safeDiscipline(value: string): Discipline {
  return (value in DISCIPLINE_LABEL ? value : "other") as Discipline;
}
