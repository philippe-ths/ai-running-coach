// Shared by the Voice and Stance dial panels: a 1-5 range input announces a bare
// number, so the accessible name carries the dial and both poles, and the value text
// says which way the number leans. Built from the catalog's own pole labels.
const MIN = 1;
const MAX = 5;

export function dialLabel(name: string, lowPole: string, highPole: string): string {
  return `${name}, from ${lowPole} (${MIN}) to ${highPole} (${MAX})`;
}

export function dialValueText(value: number, lowPole: string, highPole: string): string {
  const middle = (MIN + MAX) / 2;
  const lean =
    value === middle ? 'balanced' : `toward ${value < middle ? lowPole : highPole}`;
  return `${value} of ${MAX}, ${lean}`;
}
