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
//
// #843: three dark twins sat above the 0.67 lightness ceiling for a dark surface
// (emerald-500 0.696, orange-500 0.705, violet-400 0.709) and each moved down one
// step. The LIGHT values are untouched, so the four rounds of wireframe review
// that produced them stand. This was not only polish: `intentPip` prints the
// discipline letter in WHITE on these fills, and all three were under the 3:1
// floor for a graphical object against it (2.54 / 2.80 / 2.72). They now read
// 3.77 / 3.56 / 4.23, and adjacent-pair separation improved with them
// (worst deutan ΔE 21.1 -> 22.7).
//
// `rest` is the standing exception, in both modes: stone is under the chroma
// floor and reads as grey ON PURPOSE, because rest is an absence rather than a
// fifth category. It is drawn hollow and dashed everywhere the runner meets it.
export const INTENT_FILL: Record<SessionIntent, string> = {
  rest: "bg-stone-400 dark:bg-stone-500",
  easy: "bg-emerald-600 dark:bg-emerald-600",
  long: "bg-blue-700 dark:bg-blue-500",
  quality: "bg-orange-700 dark:bg-orange-600",
  strength: "bg-violet-500 dark:bg-violet-500",
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

// One hue, stepped by priority — run is the most prominent step because it is
// the priority sport. Five real disciplines take the ramp; `other` is the
// residual bucket and sits OUTSIDE it in neutral grey, because a residual has
// no rank to encode and six steps of one hue cannot hold a visible gap between
// each pair.
//
// The steps are not eyeballed. Six blue steps failed the ordinal checks twice
// over (blue-700↔blue-600 ΔL 0.058, under the 0.06 floor; blue-200's light end
// at 1.42:1 against white, under the 2:1 floor) and two disciplines collapsed
// onto the same dark twin. Five steps clear every check in both modes
// (light end 2.54:1 light, 2.84:1 dark). The horizon draws these as stacked
// segments at width, which is where a collapsed pair actually costs a reading.
//
// The dark twins REVERSE the anchor: on a dark surface prominence is lightness,
// so run takes the lightest step there and the ordering survives the flip.
export const DISCIPLINE_FILL: Record<Discipline, string> = {
  run: "bg-blue-950 dark:bg-blue-200",
  bike: "bg-blue-800 dark:bg-blue-300",
  strength: "bg-blue-600 dark:bg-blue-400",
  row: "bg-blue-500 dark:bg-blue-500",
  walk: "bg-blue-400 dark:bg-blue-600",
  other: "bg-gray-500 dark:bg-gray-400",
};

/** Stable draw order for a stacked mix: the ramp, darkest first, residual last. */
export const DISCIPLINE_ORDER: Discipline[] = [
  "run",
  "bike",
  "strength",
  "row",
  "walk",
  "other",
];

/** Stable draw order for an intent mix. Rest last: it is an absence. */
export const INTENT_ORDER: SessionIntent[] = [
  "easy",
  "long",
  "quality",
  "strength",
  "rest",
];

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
