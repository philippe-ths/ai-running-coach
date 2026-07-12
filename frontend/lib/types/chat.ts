export interface ChatMessage {
  id: string;
  activity_id: string;
  role: "user" | "assistant";
  content: string;
  // #648 f/u: the on-demand data tools the coach ran for this assistant turn
  // (null/absent when none), so the UI can show a persistent "looked up …" trace
  // that survives a reload. User turns never carry it.
  tools_used?: string[] | null;
  created_at: string;
}
