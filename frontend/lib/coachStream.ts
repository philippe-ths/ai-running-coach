// #766: the shared coach SSE reader. One parse loop for every streamed coach
// reply (the thread turn today; the activity chat box uses the same wire
// format): buffers across network chunks, splits on the blank-line event
// delimiter, JSON-decodes each `data:` frame, and dispatches by frame shape.
// A content frame is a JSON string; object frames carry a `type` —
// 'thread' (#766: the created/continued thread announcement), 'status' (#648:
// the ephemeral fetching affordance), 'tool_trace' (#664: what a tool fetched).

import { ToolTraceEntry } from "./types/chat";
import { ProposedActionFrame } from "./types/thread";

export interface ThreadFrame {
  thread_id: string;
  created: boolean;
  title: string;
}

export interface CoachStreamHandlers {
  onText: (piece: string) => void;
  onThread?: (frame: ThreadFrame) => void;
  onProposedAction?: (frame: ProposedActionFrame) => void;
  onStatus?: (label: string) => void;
  onToolTrace?: (entry: ToolTraceEntry) => void;
}

// #995: a stream that stops early is not a stream that finished. The backend
// closes every turn with `[DONE]` — including the one it emits from its own
// `except` — so the sentinel arriving is the only proof the turn ran to an end
// the server chose. Without this the two are byte-indistinguishable: the reader
// returns normally, the caller believes the coach replied, and a severed
// connection shows up as an empty message or an endless spinner.
//
// The severing is real and not hypothetical. `maxDuration` on the API proxy
// route is a wall-clock kill, and unlike the idle timeout of #375 no heartbeat
// can hold it off — when a turn outlives it the function is terminated
// mid-response with no error frame, because there is nothing left to write one.
export class CoachStreamTruncatedError extends Error {
  constructor() {
    super('The coach stream ended before the reply was complete.');
    this.name = 'CoachStreamTruncatedError';
  }
}

export async function readCoachStream(
  res: Response,
  handlers: CoachStreamHandlers,
): Promise<void> {
  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response stream");

  const decoder = new TextDecoder();
  let buffer = "";
  let sawDone = false;

  const handleEvent = (event: string) => {
    for (const line of event.split("\n")) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6);
      if (data === "[DONE]") {
        sawDone = true;
        continue;
      }
      try {
        const parsed = JSON.parse(data);
        if (typeof parsed === "string") {
          handlers.onText(parsed);
        } else if (parsed && typeof parsed === "object") {
          if (parsed.type === "thread" && parsed.thread_id) {
            handlers.onThread?.(parsed as ThreadFrame);
          } else if (parsed.type === "proposed_action" && parsed.token) {
            handlers.onProposedAction?.(parsed as ProposedActionFrame);
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

  // Checked after the trailing partial is drained, so a `[DONE]` that arrived
  // in the same chunk as the last content frame still counts.
  if (!sawDone) throw new CoachStreamTruncatedError();
}
