// #766: the shared coach SSE reader. One parse loop for every streamed coach
// reply (the thread turn today; the activity chat box uses the same wire
// format): buffers across network chunks, splits on the blank-line event
// delimiter, JSON-decodes each `data:` frame, and dispatches by frame shape.
// A content frame is a JSON string; object frames carry a `type` —
// 'thread' (#766: the created/continued thread announcement), 'status' (#648:
// the ephemeral fetching affordance), 'tool_trace' (#664: what a tool fetched).

import { ToolTraceEntry } from "./types/chat";

export interface ThreadFrame {
  thread_id: string;
  created: boolean;
  title: string;
}

export interface CoachStreamHandlers {
  onText: (piece: string) => void;
  onThread?: (frame: ThreadFrame) => void;
  onStatus?: (label: string) => void;
  onToolTrace?: (entry: ToolTraceEntry) => void;
}

export async function readCoachStream(
  res: Response,
  handlers: CoachStreamHandlers,
): Promise<void> {
  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response stream");

  const decoder = new TextDecoder();
  let buffer = "";

  const handleEvent = (event: string) => {
    for (const line of event.split("\n")) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6);
      if (data === "[DONE]") continue;
      try {
        const parsed = JSON.parse(data);
        if (typeof parsed === "string") {
          handlers.onText(parsed);
        } else if (parsed && typeof parsed === "object") {
          if (parsed.type === "thread" && parsed.thread_id) {
            handlers.onThread?.(parsed as ThreadFrame);
          } else if (parsed.type === "status") {
            handlers.onStatus?.(parsed.label ?? "");
          } else if (parsed.type === "tool_trace" && parsed.entry) {
            handlers.onToolTrace?.(parsed.entry as ToolTraceEntry);
          }
        }
      } catch {
        // Ignore a malformed frame rather than aborting the whole stream.
      }
    }
  };

  // SSE events are delimited by a blank line. Buffer across reads so an event
  // split over two network chunks is reassembled before parsing.
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) handleEvent(event);
  }
  if (buffer.trim()) handleEvent(buffer);
}
