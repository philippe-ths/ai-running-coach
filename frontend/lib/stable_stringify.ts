export function stableStringify(value: unknown): string {
  if (value === null) return 'null';

  const t = typeof value;
  if (t === 'number' || t === 'boolean') return String(value);
  if (t === 'string') return JSON.stringify(value);
  if (t !== 'object') return JSON.stringify(value);

  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(',')}]`;
  }

  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  const parts = keys.map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k])}`);
  return `{${parts.join(',')}}`;
}
