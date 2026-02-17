export interface ChatMessage {
  id: string;
  activity_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}
