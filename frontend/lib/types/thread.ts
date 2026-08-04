// #766 (ADR 0027): the runner's coach threads — the switcher list shape, the
// single-thread read, and the turn-send payload. Mirrors backend
// app/schemas/thread.py.

import { ChatMessage } from "./chat";

export interface ThreadAnchor {
  activity_id: string;
  name?: string | null;
  start_date?: string | null;
  distance_m?: number | null;
  type?: string | null;
}

export interface ThreadListItem {
  id: string;
  // Server-resolved display title: the written/runner title when set, else the
  // first user message trimmed. Never empty for a thread with a turn in it.
  title: string;
  snippet?: string | null;
  last_message_at?: string | null;
  anchor?: ThreadAnchor | null;
}

export interface ThreadDetail {
  id: string;
  title: string;
  created_at: string;
  last_message_at?: string | null;
  anchor?: ThreadAnchor | null;
  messages: ChatMessage[];
}

export interface ProposedActionFrame {
  action_type: "check_in" | "intent" | "split_block" | "merge_blocks";
  token: string;
  description: string;
  confirm_label: string;
  dismiss_label: string;
}

export interface ThreadMessageSend {
  message: string;
  thread_id?: string;
  anchor_activity_id?: string;
  asked_from?: string;
}
