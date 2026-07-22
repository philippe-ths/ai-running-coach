'use client';

import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from 'react';
import { ChatMessage, CoachReport, ToolTraceEntry, isMessageReport } from '@/lib/types';
import { MessageCircle, Send, Loader2, RotateCcw, Sparkles, Search } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface Props {
  activityId: string;
}

// #664: the friendly past-tense label is now server-owned (query_tools.TOOL_TRACE_LABELS,
// carried on each trace entry), consolidating the #663 duplication. This is only a
// defensive fallback for an entry that somehow arrives without one.
const toolTraceLabel = (entry: ToolTraceEntry): string =>
  entry.label ?? 'Looked up your training data';

// #664: render one trace chip's "what was fetched" tail — the resolved window and a
// result count — from the server-derived entry. e.g. "· last 30 days (12 sessions)".
const toolTraceDetail = (entry: ToolTraceEntry): string => {
  const parts: string[] = [];
  if (entry.detail) parts.push(entry.detail);
  if (entry.count != null) parts.push(`${entry.count} ${entry.count === 1 ? 'session' : 'sessions'}`);
  if (!parts.length) return '';
  // "last 30 days (12 sessions)" when both present; either alone otherwise.
  return entry.detail && entry.count != null
    ? ` · ${entry.detail} (${entry.count} ${entry.count === 1 ? 'session' : 'sessions'})`
    : ` · ${parts.join(' ')}`;
};

// The persistent "looked up …" trace rendered above an assistant turn that ran one
// or more data tools. Survives a reload because tools_used is stored on the message.
// #664: one chip PER tool call (no name de-dup), each showing what it fetched, so a
// multi-window turn no longer collapses to a single chip.
function ToolTrace({ tools }: { tools: ToolTraceEntry[] }) {
  if (!tools.length) return null;
  return (
    <div className="mb-1.5 flex flex-wrap gap-1.5">
      {tools.map((entry, i) => (
        <span
          key={`${entry.tool}-${i}`}
          className="inline-flex items-center gap-1 rounded-full bg-blue-50 dark:bg-blue-900/30 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:text-blue-300"
        >
          <Search className="w-2.5 h-2.5 shrink-0" />
          {toolTraceLabel(entry)}{toolTraceDetail(entry)}
        </span>
      ))}
    </div>
  );
}

// Imperative handle so a sibling (the report panel's question chips) can kick off
// the chat without lifting all of the chat's state up. `ask` expands the chat,
// brings it into view, and either sends `text` (a tapped quick-reply) or just
// focuses the input for a free-form answer (no text).
export interface CoachChatHandle {
  ask: (text?: string) => void;
}

const CoachChat = forwardRef<CoachChatHandle, Props>(function CoachChat({ activityId }, ref) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  // #648: an ephemeral "fetching your data" label shown while the coach runs a
  // data-lookup tool round, before the reply itself starts streaming.
  const [fetchingLabel, setFetchingLabel] = useState('');
  const [expanded, setExpanded] = useState(false);
  // I3: the coach's own next-steps, offered as one-tap conversation starters so
  // the chat reads as a continuation of the report rather than a blank box.
  const [starters, setStarters] = useState<string[]>([]);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = useCallback((force = false) => {
    // Scroll the messages container itself — NOT scrollIntoView, which bubbles
    // up to every scrollable ancestor and on an iOS PWA yanks the whole page so
    // the input scrolls out of view (#223). Pin to the bottom only when the user
    // is already near it, so streaming tokens don't fight a user who scrolled up
    // to re-read. Instant (no smooth) so per-token updates don't pile up
    // animations into perpetual jitter. `force` overrides the near-bottom guard
    // for the user's own send, so the streaming bubble (and its #648 "fetching"
    // affordance) is guaranteed visible rather than stranded below the fold.
    const el = messagesContainerRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (force || nearBottom) el.scrollTop = el.scrollHeight;
  }, []);

  // Load chat history on mount
  useEffect(() => {
    async function loadHistory() {
      try {
        const res = await fetch(`/api/activities/${activityId}/coach-chat`);
        if (res.ok) {
          const data = await res.json();
          if (data.messages && data.messages.length > 0) {
            setMessages(data.messages);
            setExpanded(true);
          }
        }
      } catch {
        // Silently fail — chat history is optional
      }
    }
    loadHistory();
  }, [activityId]);

  // Pull the coach's next-steps from the existing report to seed tappable
  // conversation starters (I3). Read-only (generate=false): if there is no report
  // yet, or it is the legacy structured shape, there are simply no starters.
  useEffect(() => {
    async function loadStarters() {
      try {
        const res = await fetch(
          `/api/activities/${activityId}/coach-report?generate=false&force=false`,
        );
        if (!res.ok) return;
        const data: CoachReport = await res.json();
        const body = data.report;
        if (body && isMessageReport(body)) {
          const actions = (body.next_steps ?? [])
            .map((s) => s.action?.trim())
            .filter((a): a is string => Boolean(a))
            .slice(0, 3);
          setStarters(actions);
        }
      } catch {
        // Starters are progressive enhancement — never block the chat on them.
      }
    }
    loadStarters();
  }, [activityId]);

  useEffect(() => {
    if (!expanded) return;
    // #648: force to the bottom when the runner has just sent (their own action),
    // so the streaming bubble and its "fetching your data" affordance are visible
    // rather than stranded below the fold; otherwise only pin when already near the
    // bottom, so streaming tokens don't fight a reader who scrolled up. fetchingLabel
    // is a dep so the chip appearing keeps the view pinned.
    const justSent = messages[messages.length - 1]?.role === 'user';
    scrollToBottom(justSent);
  }, [messages, streamingText, fetchingLabel, expanded, scrollToBottom]);

  // When the panel first opens (typically with loaded history), jump straight to
  // the latest turn — like a chat app opening its transcript at the bottom rather
  // than the top. The messages effect above won't do this on open because a fresh
  // scroll position isn't "near bottom", so force it once here.
  useEffect(() => {
    if (expanded) scrollToBottom(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  // Auto-grow the input as the runner types so a long message is never clipped
  // inside a fixed one-row box. Cap the height (~160px, ~6 lines) so a very long
  // draft scrolls internally instead of swallowing the chat; clearing the input
  // (e.g. after send) resets it back to a single row.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const sendMessage = useCallback(async (textArg?: string) => {
    // textArg lets a tapped starter chip send without going through the input box.
    const text = (textArg ?? input).trim();
    if (!text || streaming) return;

    if (textArg === undefined) setInput('');
    setStreaming(true);
    setStreamingText('');
    setFetchingLabel('');

    // Optimistic user message
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      activity_id: activityId,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMsg]);

    try {
      const res = await fetch(`/api/activities/${activityId}/coach-chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });

      if (!res.ok) {
        throw new Error(`Chat failed (${res.status})`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response stream');

      const decoder = new TextDecoder();
      let fullResponse = '';
      // #648 f/u / #664: accumulate the trace records of what the coach fetched this
      // turn (one per tool call, describing the resolved window + count) so we can
      // attach them to the finalized assistant message and render a persistent
      // "looked up …" trace that survives a reload.
      const toolsUsed: ToolTraceEntry[] = [];
      // SSE events are delimited by a blank line. Buffer across reads so an
      // event split over two network chunks is reassembled before parsing.
      let buffer = '';

      const handleEvent = (event: string) => {
        for (const line of event.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') continue;
          try {
            // The backend JSON-encodes each chunk so multi-line markdown
            // survives the SSE protocol intact. A content frame is a JSON string;
            // a status frame (#648) is a JSON object {type:'status',label,tool}
            // shown as an ephemeral "fetching" affordance (label); a tool_trace
            // frame (#664) is {type:'tool_trace',entry:{tool,label,detail,count}}
            // banked into the persistent trace, not appended to the reply.
            const parsed = JSON.parse(data);
            if (parsed && typeof parsed === 'object' && parsed.type === 'status') {
              setFetchingLabel(parsed.label ?? '');
            } else if (parsed && typeof parsed === 'object' && parsed.type === 'tool_trace') {
              if (parsed.entry && typeof parsed.entry === 'object') {
                toolsUsed.push(parsed.entry as ToolTraceEntry);
              }
            } else if (typeof parsed === 'string') {
              fullResponse += parsed;
              setStreamingText(fullResponse);
              setFetchingLabel(''); // the reply is streaming now: clear the affordance
            }
          } catch {
            // Ignore a malformed frame rather than aborting the whole stream.
          }
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() ?? ''; // keep the trailing partial event
        for (const event of events) handleEvent(event);
      }
      if (buffer.trim()) handleEvent(buffer);

      // Finalize: replace streaming text with a proper message
      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        activity_id: activityId,
        role: 'assistant',
        content: fullResponse,
        tools_used: toolsUsed.length ? toolsUsed : null,
        created_at: new Date().toISOString(),
      };
      setMessages(prev => [...prev, assistantMsg]);
      setStreamingText('');
    } catch (err) {
      // Surface a friendly, actionable message rather than the raw fetch error
      // (Safari reports a dropped connection as the opaque "Load failed") (#223).
      const errorMsg: ChatMessage = {
        id: crypto.randomUUID(),
        activity_id: activityId,
        role: 'assistant',
        content: "Sorry, I couldn't reach your coach just now. Please try again.",
        created_at: new Date().toISOString(),
      };
      setMessages(prev => [...prev, errorMsg]);
      setStreamingText('');
    } finally {
      setStreaming(false);
      setFetchingLabel('');
    }
  }, [input, streaming, activityId]);

  const resetChat = async () => {
    try {
      await fetch(`/api/activities/${activityId}/coach-chat`, { method: 'DELETE' });
      setMessages([]);
      setStreamingText('');
    } catch {
      // Silently fail
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // I3: tap a coach suggestion to open the conversation already pointed at it.
  const startConversation = (action: string) => {
    if (streaming) return;
    setExpanded(true);
    void sendMessage(`Tell me more about this suggestion: ${action}`);
  };

  // Let the report panel's question chips start the chat. Tapping a quick-reply
  // sends its label; a "custom" option opens the chat focused so the runner can
  // type their own answer. The timeout lets the expanded view mount before we
  // scroll/focus (mirrors the expand button below, #226).
  useImperativeHandle(
    ref,
    () => ({
      ask: (text?: string) => {
        if (streaming) return;
        setExpanded(true);
        const trimmed = text?.trim();
        setTimeout(() => {
          inputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          if (trimmed) {
            void sendMessage(trimmed);
          } else {
            inputRef.current?.focus({ preventScroll: true });
          }
        }, 100);
      },
    }),
    [streaming, sendMessage],
  );

  const renderStarters = () => {
    if (starters.length === 0) return null;
    return (
      <div className="flex flex-wrap gap-2">
        {starters.map((action, i) => (
          <button
            key={i}
            onClick={() => startConversation(action)}
            disabled={streaming}
            title={action}
            className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/30 px-3 py-1.5 text-xs font-medium text-blue-700 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-colors text-left disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Sparkles className="w-3 h-3 shrink-0" />
            <span>{action.length > 48 ? `${action.slice(0, 47).trimEnd()}…` : action}</span>
          </button>
        ))}
      </div>
    );
  };

  if (!expanded) {
    return (
      <div className="space-y-2">
        <button
          onClick={() => {
            setExpanded(true);
            // Bring the input into view before focusing it (#226). focus() with
            // preventScroll avoids the browser's own scroll jump fighting ours.
            setTimeout(() => {
              inputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
              inputRef.current?.focus({ preventScroll: true });
            }, 100);
          }}
          className="w-full bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4 hover:border-blue-300 dark:hover:border-blue-700 hover:shadow-md transition-all flex items-center justify-center gap-2 text-gray-600 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400"
        >
          <MessageCircle className="w-4 h-4" />
          <span className="text-sm font-medium">Continue the conversation with your coach</span>
        </button>
        {starters.length > 0 && (
          <div className="px-1">
            <p className="text-xs text-gray-400 dark:text-gray-500 mb-1.5">
              Or pick up where your coach left off:
            </p>
            {renderStarters()}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-2">
          <MessageCircle className="w-4 h-4 text-blue-600 dark:text-blue-400" />
          Chat with Coach
        </h3>
        <div className="flex items-center gap-2">
          {messages.length > 0 && !streaming && (
            <button
              onClick={resetChat}
              className="text-xs text-gray-400 dark:text-gray-500 hover:text-red-500 flex items-center gap-1 transition-colors"
              title="Clear chat history"
            >
              <RotateCcw className="w-3 h-3" />
              Reset
            </button>
          )}
          {messages.length === 0 && (
            <button
              onClick={() => setExpanded(false)}
              className="text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
            >
              Close
            </button>
          )}
        </div>
      </div>

      {/* Messages. Coach turns render bare (prose on the surface, like a letter);
          only the runner's own turns sit in a bubble, so the transcript reads as
          a document being written to them rather than a chatbot log. */}
      <div ref={messagesContainerRef} className="chat-scroll h-[min(60vh,34rem)] overflow-y-auto px-4 py-4 space-y-5">
        {messages.length === 0 && !streaming && (
          <div className="py-2 space-y-3">
            <p className="text-sm text-gray-400 dark:text-gray-500 text-center">
              Ask about your workout, training plan, or anything about this session.
            </p>
            {renderStarters()}
          </div>
        )}
        {messages.map((msg) => (
          msg.role === 'user' ? (
            <div key={msg.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words bg-blue-600 text-white">
                {msg.content}
              </div>
            </div>
          ) : (
            <div key={msg.id} className="text-[15px] text-gray-800 dark:text-gray-100">
              <ToolTrace tools={msg.tools_used ?? []} />
              <div className="prose prose-sm prose-gray dark:prose-invert max-w-none leading-relaxed prose-p:my-2 prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:text-sm">
                <Markdown remarkPlugins={[remarkGfm]}>{msg.content}</Markdown>
              </div>
            </div>
          )
        ))}
        {streaming && streamingText && (
          <div className="text-[15px] text-gray-800 dark:text-gray-100">
            <div className="prose prose-sm prose-gray dark:prose-invert max-w-none leading-relaxed prose-p:my-2 prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:text-sm">
              <Markdown remarkPlugins={[remarkGfm]}>{streamingText}</Markdown>
            </div>
            <span className="inline-block w-1.5 h-4 align-middle bg-gray-400 dark:bg-gray-500 ml-0.5 animate-pulse" />
          </div>
        )}
        {streaming && !streamingText && (
          <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            {fetchingLabel || 'Thinking...'}
          </div>
        )}
      </div>

      {/* Input. A single soft, borderless field (the whole row is one rounded
          surface that lights a focus ring), with the send button tucked inside —
          no outline, no attach/mic/model chrome. */}
      <div className="p-3">
        <div className="flex items-end gap-1.5 rounded-2xl bg-gray-100 dark:bg-gray-900/70 px-2 py-1.5 transition-shadow focus-within:ring-2 focus-within:ring-blue-500/40">
          {/* text-base (16px) keeps iOS Safari from auto-zooming the field on focus (#227). */}
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask your coach..."
            rows={1}
            className="chat-scroll flex-1 resize-none overflow-y-auto border-0 bg-transparent px-2 py-1.5 text-base text-gray-900 dark:text-gray-100 placeholder:text-gray-400 dark:placeholder:text-gray-500 focus:outline-none focus:ring-0"
            disabled={streaming}
          />
          <button
            onClick={() => sendMessage()}
            disabled={!input.trim() || streaming}
            aria-label="Send message"
            className="shrink-0 rounded-xl bg-blue-600 p-2 text-white transition-colors hover:bg-blue-700 disabled:bg-transparent disabled:text-gray-400 dark:disabled:text-gray-600 disabled:cursor-not-allowed"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
});

export default CoachChat;
