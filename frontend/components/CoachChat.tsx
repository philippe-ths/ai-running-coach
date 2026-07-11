'use client';

import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from 'react';
import { ChatMessage, CoachReport, isMessageReport } from '@/lib/types';
import { MessageCircle, Send, Loader2, RotateCcw, Sparkles } from 'lucide-react';
import Markdown from 'react-markdown';

interface Props {
  activityId: string;
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
            // a status frame (#648) is a JSON object {type:'status',label} shown
            // as an ephemeral "fetching" affordance, not appended to the reply.
            const parsed = JSON.parse(data);
            if (parsed && typeof parsed === 'object' && parsed.type === 'status') {
              setFetchingLabel(parsed.label ?? '');
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
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-gray-700">
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

      {/* Messages */}
      <div ref={messagesContainerRef} className="max-h-96 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !streaming && (
          <div className="py-2 space-y-3">
            <p className="text-sm text-gray-400 dark:text-gray-500 text-center">
              Ask about your workout, training plan, or anything about this session.
            </p>
            {renderStarters()}
          </div>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            {msg.role === 'user' ? (
              <div className="max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap bg-blue-600 text-white">
                {msg.content}
              </div>
            ) : (
              <div className="max-w-[85%] rounded-lg px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200">
                <div className="prose prose-sm prose-gray dark:prose-invert max-w-none prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:text-sm">
                  <Markdown>{msg.content}</Markdown>
                </div>
              </div>
            )}
          </div>
        ))}
        {streaming && streamingText && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-lg px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200">
              <div className="prose prose-sm prose-gray dark:prose-invert max-w-none prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:text-sm">
                <Markdown>{streamingText}</Markdown>
              </div>
              <span className="inline-block w-1.5 h-4 bg-gray-400 dark:bg-gray-500 ml-0.5 animate-pulse" />
            </div>
          </div>
        )}
        {streaming && !streamingText && (
          <div className="flex justify-start">
            <div className="rounded-lg px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 flex items-center gap-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              {fetchingLabel || 'Thinking...'}
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-gray-100 dark:border-gray-700 p-3">
        <div className="flex gap-2">
          {/* text-base (16px) keeps iOS Safari from auto-zooming the field on focus (#227). */}
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask your coach..."
            rows={1}
            className="flex-1 resize-none rounded-lg border border-gray-200 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 px-3 py-2 text-base focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            disabled={streaming}
          />
          <button
            onClick={() => sendMessage()}
            disabled={!input.trim() || streaming}
            className="rounded-lg bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
});

export default CoachChat;
