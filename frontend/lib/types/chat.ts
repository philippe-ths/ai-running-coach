// #648 f/u / #664: one on-demand data tool the coach ran for an assistant turn,
// describing WHAT it fetched (resolved window + result count), not just that it ran.
// Every field is server-derived (the friendly label, the window, the count); the
// UI renders it as one chip. Pre-#664 rows persisted only tool-name strings; the
// backend read schema coerces those into this record shape on load, so the client
// always sees objects.
export interface ToolTraceEntry {
  tool: string;
  // Past-tense friendly verb (e.g. "Looked up your training history"), server-owned.
  label?: string | null;
  // Compact "what": the resolved window ("last 30 days") or a session date.
  detail?: string | null;
  // Result count where meaningful (sessions found / totalled); null for a single
  // session lookup.
  count?: number | null;
}

export interface ChatMessage {
  id: string;
  // #765: null for a thread-only turn (a thread need not be anchored to an
  // activity); the activity chat box's rows always carry it.
  activity_id?: string | null;
  role: "user" | "assistant";
  content: string;
  // #648 f/u / #664: the on-demand data tools the coach ran for this assistant turn
  // (null/absent when none), so the UI can show a persistent "looked up …" trace
  // that survives a reload. User turns never carry it.
  tools_used?: ToolTraceEntry[] | null;
  // #766: the label of the screen this turn was asked from (ADR 0028: past turns
  // retain the label only), rendered as the quiet "asked from …" note when it
  // differs from the screen the sheet is on now.
  asked_from?: string | null;
  created_at: string;
}
