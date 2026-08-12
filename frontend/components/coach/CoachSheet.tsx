'use client';

// #766: the coach sheet — the one conversation surface, reachable from every
// screen. Mobile: a bottom sheet over the page (half height, expandable, sized
// to the visual viewport while the keyboard is up). Desktop (md+): a docked
// right-hand panel — same thread, same state, same component. It mounts once in
// the root layout and never re-mounts on navigation, so a reply mid-stream
// survives the runner walking to another screen (ADR 0027).
//
// Coach turns render bare serif prose (the coach's voice); only the runner's
// turns sit in a bubble. A turn asked from a different screen keeps a quiet
// "asked from …" label — the label only, never that screen's data (ADR 0028).

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { ChevronDown, Loader2, Plus, Search, Send, X } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ChatMessage,
  ProposedActionFrame,
  ProposedActionResult,
  ThreadDetail,
  ThreadListItem,
  ToolTraceEntry,
} from '@/lib/types';
import { readCoachStream } from '@/lib/coachStream';
import { useCoachSheet } from './CoachSheetContext';
import ThreadSwitcher from './ThreadSwitcher';
import { useRouter } from 'next/navigation';

// Per-screen conversation starters (design spec: drawn from the screen the
// runner is on; screens without three good questions get none — three weak
// starters are worse than an empty state).
const STARTERS: Record<string, string[]> = {
  load: [
    'How is my training load looking?',
    'Am I ramping too fast?',
    'What should this week look like?',
  ],
  trends: [
    'What stands out in my trends lately?',
    'How consistent have I been this month?',
    'Is my volume where it should be?',
  ],
  activity: [
    'How was this run?',
    'How does this compare to my usual?',
    'Anything I should change next time?',
  ],
};

const SCREEN_LABELS: Record<string, string> = {
  home: 'Home',
  activities: 'Activities',
  activity: 'a run page',
  load: 'Load',
  trends: 'Trends',
  profile: 'Profile',
};

const RIBBON_LABELS: Record<string, string> = {
  home: 'Home',
  activities: 'Activities',
  activity: 'This run',
  load: 'Load',
  trends: 'Trends',
  profile: 'Profile',
};

function askedFromLabel(key: string): string {
  return SCREEN_LABELS[key] ?? key;
}

// #767: the ranges the server pointer accepts; the Trends page's "ALL" has no
// bounded server view, so it travels as identity only.
const POINTER_RANGES = new Set(['7D', '30D', '3M', '6M', '1Y']);

// The context ribbon: names what the coach is looking at with the runner, and
// is the only thing that animates on navigation — a short cross-fade of its
// text, so the change reads as the coach glancing up rather than the sheet
// reloading. It is honest by construction: the server resolves the pointer, so
// the ribbon never claims sight of something the coach cannot see (ADR 0028).
function ContextRibbon({
  screenKey,
  selections,
}: {
  screenKey: string;
  selections: { range?: string; types?: string[] } | null;
}) {
  const parts = [RIBBON_LABELS[screenKey] ?? screenKey];
  if (selections?.range) parts.push(selections.range);
  if (selections?.types?.length) parts.push(selections.types.join(', '));
  const text = parts.join(' · ');
  const [shown, setShown] = useState(text);
  const [faded, setFaded] = useState(false);
  useEffect(() => {
    if (text === shown) return;
    setFaded(true);
    const t = setTimeout(() => {
      setShown(text);
      setFaded(false);
    }, 150);
    return () => clearTimeout(t);
  }, [text, shown]);
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-gray-100 px-3.5 pb-2 dark:border-gray-700/60">
      <span className="shrink-0 font-mono text-[9px] uppercase tracking-[0.12em] text-gray-400 dark:text-gray-500">
        Looking at
      </span>
      <span
        className={`truncate text-[12px] text-gray-800 transition-opacity duration-150 motion-reduce:transition-none dark:text-gray-200 ${
          faded ? 'opacity-0 motion-reduce:opacity-100' : 'opacity-100'
        }`}
      >
        <b className="font-semibold">{shown.split(' · ')[0]}</b>
        {shown.includes(' · ') ? ` · ${shown.split(' · ').slice(1).join(' · ')}` : ''}
      </span>
    </div>
  );
}

// Size the sheet to the VISUAL viewport while the keyboard is up: a fixed
// bottom sheet pins to the layout viewport, whose bottom is behind the
// keyboard (#683). Tracking visualViewport height+offset keeps the composer
// visible above the keyboard.
function useVisualViewportBox(): { height: number; offsetTop: number } | null {
  const [box, setBox] = useState<{ height: number; offsetTop: number } | null>(null);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const update = () => {
      const keyboardOpen = window.innerHeight - vv.height > 150;
      setBox(keyboardOpen ? { height: vv.height, offsetTop: vv.offsetTop } : null);
    };
    vv.addEventListener('resize', update);
    vv.addEventListener('scroll', update);
    update();
    return () => {
      vv.removeEventListener('resize', update);
      vv.removeEventListener('scroll', update);
    };
  }, []);
  return box;
}

const toolTraceLabel = (entry: ToolTraceEntry): string =>
  entry.label ?? 'Looked up your training data';

const toolTraceDetail = (entry: ToolTraceEntry): string => {
  if (entry.detail && entry.count != null) {
    return ` · ${entry.detail} (${entry.count} ${entry.count === 1 ? 'session' : 'sessions'})`;
  }
  if (entry.detail) return ` · ${entry.detail}`;
  if (entry.count != null) return ` · ${entry.count} ${entry.count === 1 ? 'session' : 'sessions'}`;
  return '';
};

function ToolTrace({ tools }: { tools: ToolTraceEntry[] }) {
  if (!tools.length) return null;
  return (
    <div className="mb-1.5 flex flex-wrap gap-1.5">
      {tools.map((entry, i) => (
        <span
          key={`${entry.tool}-${i}`}
          className="inline-flex items-center gap-1 rounded-full bg-blue-50 dark:bg-blue-900/30 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:text-blue-300"
        >
          <Search className="h-2.5 w-2.5 shrink-0" />
          {toolTraceLabel(entry)}
          {toolTraceDetail(entry)}
        </span>
      ))}
    </div>
  );
}

const coachProseClasses =
  'prose prose-sm prose-gray dark:prose-invert max-w-none leading-relaxed font-serif prose-p:my-2 prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:text-sm';

export default function CoachSheet() {
  const router = useRouter();
  const { enabled, isOpen, close, screen, selections, pendingPrompt, consumePendingPrompt } =
    useCoachSheet();
  const vvBox = useVisualViewportBox();

  const [threads, setThreads] = useState<ThreadListItem[]>([]);
  // null = the "New thread" state: nothing exists server-side until the runner speaks.
  const [currentThreadId, setCurrentThreadId] = useState<string | null>(null);
  const [currentTitle, setCurrentTitle] = useState('New thread');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [anchorActivityId, setAnchorActivityId] = useState<string | null>(null);
  const [headReport, setHeadReport] = useState<string | null>(null);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [streamingTools, setStreamingTools] = useState<ToolTraceEntry[]>([]);
  const [fetchingLabel, setFetchingLabel] = useState('');
  const [proposedAction, setProposedAction] = useState<ProposedActionFrame | null>(null);
  const [confirmingAction, setConfirmingAction] = useState(false);
  const [actionError, setActionError] = useState('');
  const [actionDone, setActionDone] = useState('');
  const hasAutoOpenedThread = useRef(false);

  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = useCallback((force = false) => {
    // Scroll the container itself, never scrollIntoView (#223).
    const el = messagesContainerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (force || nearBottom) el.scrollTop = el.scrollHeight;
  }, []);

  const refreshThreads = useCallback(async (): Promise<ThreadListItem[]> => {
    try {
      const res = await fetch('/api/coach/threads');
      if (!res.ok) return [];
      const data = await res.json();
      setThreads(data.threads ?? []);
      return data.threads ?? [];
    } catch {
      return [];
    }
  }, []);

  const openThread = useCallback(async (id: string) => {
    setSwitcherOpen(false);
    setCurrentThreadId(id);
    setHeadReport(null);
    setProposedAction(null);
    try {
      const res = await fetch(`/api/coach/threads/${id}`);
      if (!res.ok) return;
      const detail: ThreadDetail = await res.json();
      setCurrentTitle(detail.title);
      setMessages(detail.messages);
      setAnchorActivityId(detail.anchor?.activity_id ?? null);
      // An anchored thread shows its activity's report at its head — rendered
      // from the stored report, never copied into the thread (ADR 0027).
      if (detail.anchor?.activity_id) {
        try {
          const rep = await fetch(
            `/api/activities/${detail.anchor.activity_id}/coach-report?generate=false&force=false`,
          );
          if (rep.ok) {
            const repData = await rep.json();
            const msg = repData?.report?.message;
            if (typeof msg === 'string' && msg.trim()) setHeadReport(msg);
          }
        } catch {
          // The head report is a nicety; the thread works without it.
        }
      }
      setTimeout(() => scrollToBottom(true), 0);
    } catch {
      // Leave whatever was on screen.
    }
  }, [scrollToBottom]);

  const startNewThread = useCallback(() => {
    setSwitcherOpen(false);
    setCurrentThreadId(null);
    setCurrentTitle('New thread');
    setMessages([]);
    setHeadReport(null);
    setProposedAction(null);
    // Born on an activity page, a new thread anchors to that run (a framing
    // hint, never a data boundary — ADR 0027).
    setAnchorActivityId(screen.activityId ?? null);
  }, [screen.activityId]);

  // On first open: land on the screen's own thread when there is one (an
  // activity page with an anchored thread), else the most recent conversation,
  // else the New-thread state.
  useEffect(() => {
    if (!isOpen) return;
    void (async () => {
      const list = await refreshThreads();
      if (hasAutoOpenedThread.current) return;
      hasAutoOpenedThread.current = true;
      const anchored = screen.activityId
        ? list.find(t => t.anchor?.activity_id === screen.activityId)
        : undefined;
      const target = anchored ?? list[0];
      if (target) void openThread(target.id);
      else startNewThread();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText, scrollToBottom]);

  // #770: a question handed over by the report's conversational options lands in
  // the composer, ready to send or edit.
  useEffect(() => {
    if (!isOpen || !pendingPrompt) return;
    setInput(pendingPrompt);
    consumePendingPrompt();
    inputRef.current?.focus({ preventScroll: true });
  }, [isOpen, pendingPrompt, consumePendingPrompt]);

  // Auto-grow the composer (capped ~6 lines), reset when cleared.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const sendMessage = useCallback(
    async (textArg?: string) => {
      const text = (textArg ?? input).trim();
      if (!text || streaming) return;
      if (textArg === undefined) setInput('');
      setStreaming(true);
      setStreamingText('');
      setStreamingTools([]);
      setFetchingLabel('');
      setProposedAction(null);

      const optimistic: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: text,
        asked_from: screen.key,
        created_at: new Date().toISOString(),
      };
      setMessages(prev => [...prev, optimistic]);
      setTimeout(() => scrollToBottom(true), 0);

      let fullResponse = '';
      const toolsUsed: ToolTraceEntry[] = [];
      try {
        const res = await fetch('/api/coach/threads/messages', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: text,
            thread_id: currentThreadId ?? undefined,
            anchor_activity_id:
              currentThreadId === null ? anchorActivityId ?? undefined : undefined,
            // #767: the screen POINTER — identity + the runner's selections
            // only; the server recomputes every number (ADR 0028).
            screen: {
              screen: screen.key,
              activity_id: screen.activityId ?? undefined,
              range:
                selections?.range && POINTER_RANGES.has(selections.range)
                  ? selections.range
                  : undefined,
              types: selections?.types?.length ? selections.types : undefined,
            },
          }),
        });
        if (!res.ok) throw new Error(`Thread turn failed (${res.status})`);

        await readCoachStream(res, {
          onText: piece => {
            fullResponse += piece;
            setStreamingText(fullResponse);
            setFetchingLabel('');
          },
          onThread: frame => {
            setCurrentThreadId(frame.thread_id);
            if (frame.title) setCurrentTitle(frame.title);
          },
          onProposedAction: frame => setProposedAction(frame),
          onStatus: label => setFetchingLabel(label),
          onToolTrace: entry => {
            toolsUsed.push(entry);
            setStreamingTools([...toolsUsed]);
          },
        });

        const assistantMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: fullResponse,
          tools_used: toolsUsed.length ? toolsUsed : null,
          asked_from: screen.key,
          created_at: new Date().toISOString(),
        };
        setMessages(prev => [...prev, assistantMsg]);
        setStreamingText('');
        void refreshThreads();
      } catch {
        const errorMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: "Sorry, I couldn't reach your coach just now. Please try again.",
          created_at: new Date().toISOString(),
        };
        setMessages(prev => [...prev, errorMsg]);
        setStreamingText('');
      } finally {
        setStreaming(false);
        setFetchingLabel('');
        setStreamingTools([]);
      }
    },
    [input, streaming, currentThreadId, anchorActivityId, screen.key, screen.activityId, selections, refreshThreads, scrollToBottom],
  );

  const confirmProposedAction = useCallback(async () => {
    if (!proposedAction || confirmingAction) return;
    setConfirmingAction(true);
    setActionError('');
    setActionDone('');
    try {
      const res = await fetch('/api/coach/threads/actions/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: proposedAction.token }),
      });
      if (!res.ok) throw new Error(String(res.status));
      // A write that lands somewhere the runner is not looking says so; the rest
      // are visible behind the sheet the moment it refreshes.
      const result = (await res.json().catch(() => null)) as ProposedActionResult | null;
      setActionDone(result?.message ?? '');
      setProposedAction(null);
      // The write lands on the runner's record, not in the thread, so refresh
      // the screen behind the sheet and leave the transcript where they are.
      router.refresh();
    } catch (err) {
      // A tap that changed nothing must say so. An offer is single-use and
      // short-lived, so a 404 means it is spent or stale, not that it failed.
      setActionError(
        String((err as Error)?.message) === '404'
          ? 'That offer has expired. Ask me again and I can redo it.'
          : "That didn't go through. Try again in a moment.",
      );
    } finally {
      setConfirmingAction(false);
    }
  }, [confirmingAction, proposedAction, router]);

  const renameThread = useCallback(
    async (id: string, title: string) => {
      try {
        await fetch(`/api/coach/threads/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title }),
        });
        if (id === currentThreadId) setCurrentTitle(title);
        void refreshThreads();
      } catch {
        // The old name stands.
      }
    },
    [currentThreadId, refreshThreads],
  );

  const deleteThread = useCallback(
    async (id: string) => {
      try {
        await fetch(`/api/coach/threads/${id}`, { method: 'DELETE' });
      } catch {
        return;
      }
      const list = await refreshThreads();
      if (id === currentThreadId) {
        const next = list[0];
        if (next) void openThread(next.id);
        else startNewThread();
      }
    },
    [currentThreadId, refreshThreads, openThread, startNewThread],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  };

  if (!enabled || !isOpen) return null;

  const starters = currentThreadId === null ? STARTERS[screen.key] ?? [] : [];

  // Mobile geometry: bottom sheet at half height (grabber tap expands); while
  // the keyboard is up, size to the visual viewport so the composer stays
  // visible. Desktop (md+): a docked right panel.
  const mobileStyle = vvBox
    ? { top: vvBox.offsetTop, height: vvBox.height }
    : undefined;
  const mobileHeightClass = vvBox ? '' : expanded ? 'h-[92dvh]' : 'h-[68dvh]';

  return (
    <div
      style={mobileStyle}
      className={`fixed inset-x-0 bottom-0 z-50 flex flex-col overflow-hidden border-t border-gray-200 bg-white shadow-[0_-8px_30px_rgba(23,23,23,0.10)] dark:border-gray-700 dark:bg-gray-800 dark:shadow-[0_-8px_30px_rgba(0,0,0,0.45)] ${
        vvBox ? 'rounded-none' : 'rounded-t-2xl'
      } ${mobileHeightClass} md:inset-x-auto md:bottom-4 md:right-4 md:top-20 md:h-auto md:w-[380px] md:rounded-xl md:border md:shadow-xl`}
      role="dialog"
      aria-label="Coach"
    >
      {/* Grabber (mobile): tap toggles half <-> full. */}
      <button
        onClick={() => setExpanded(v => !v)}
        aria-label={expanded ? 'Collapse the sheet' : 'Expand the sheet'}
        className="mx-auto mt-2 mb-0.5 block h-1 w-9 shrink-0 rounded-full bg-gray-300 dark:bg-gray-600 md:hidden"
      />

      {/* Thread bar: the name is the switcher. */}
      <div className="relative flex shrink-0 items-center justify-between gap-2 px-3.5 pb-2 pt-1.5 md:pt-3">
        <button
          onClick={() => setSwitcherOpen(v => !v)}
          className="flex min-w-0 items-center gap-1.5 text-[13.5px] font-semibold text-gray-900 dark:text-gray-100"
          aria-expanded={switcherOpen}
          aria-label="Switch thread"
        >
          <span className="truncate">{currentTitle}</span>
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-gray-300 dark:border-gray-600">
            <ChevronDown className="h-2.5 w-2.5 text-gray-400" />
          </span>
        </button>
        <div className="flex shrink-0 items-center gap-2 text-gray-400 dark:text-gray-500">
          <button onClick={startNewThread} aria-label="Start a new thread" className="p-1.5 hover:text-blue-600 dark:hover:text-blue-400">
            <Plus className="h-4 w-4" />
          </button>
          <button onClick={close} aria-label="Close the coach" className="p-1.5 hover:text-gray-600 dark:hover:text-gray-300">
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Context ribbon (#767): what the coach is looking at with the runner. */}
      <ContextRibbon screenKey={screen.key} selections={selections} />

      {/* Transcript region (the switcher drops over it). */}
      <div className="relative flex min-h-0 flex-1 flex-col">
        {switcherOpen && (
          <ThreadSwitcher
            threads={threads}
            currentThreadId={currentThreadId}
            onSelect={id => void openThread(id)}
            onNewThread={startNewThread}
            onRename={(id, title) => void renameThread(id, title)}
            onDelete={id => void deleteThread(id)}
            onClose={() => setSwitcherOpen(false)}
          />
        )}

        <div
          ref={messagesContainerRef}
          className="chat-scroll flex-1 overflow-y-auto px-4 py-3.5"
        >
          {/* Anchored thread: the run's report at the head, a read-time
              projection of the stored report (ADR 0027). */}
          {headReport && (
            <div className="mb-4 border-b border-gray-100 pb-4 dark:border-gray-700/60">
              <div className="mb-1.5 font-mono text-[9px] uppercase tracking-widest text-gray-400 dark:text-gray-500">
                Your report for this run
              </div>
              <div className={`${coachProseClasses} selectable`}>
                <Markdown remarkPlugins={[remarkGfm]}>{headReport}</Markdown>
              </div>
            </div>
          )}

          {messages.length === 0 && !streaming && (
            <div className="space-y-3 py-2">
              <p className="font-serif text-[15px] leading-snug text-gray-800 dark:text-gray-100">
                What do you want to know?
              </p>
              <p className="text-[12px] text-gray-500 dark:text-gray-400">
                I can look up anything in your training.
              </p>
              {starters.length > 0 && (
                <div className="flex flex-col items-start gap-2 pt-1">
                  {starters.map(s => (
                    <button
                      key={s}
                      onClick={() => void sendMessage(s)}
                      className="rounded-full border border-gray-300 px-3 py-1.5 text-left text-[12.5px] text-gray-800 transition-colors hover:border-blue-500 hover:text-blue-600 dark:border-gray-600 dark:text-gray-200 dark:hover:border-blue-400 dark:hover:text-blue-400"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="space-y-4">
            {messages.map(msg =>
              msg.role === 'user' ? (
                <div key={msg.id} className="flex flex-col items-end gap-0.5">
                  {msg.asked_from && msg.asked_from !== screen.key && (
                    <span className="font-mono text-[9px] tracking-wide text-gray-400 dark:text-gray-500">
                      asked from {askedFromLabel(msg.asked_from)}
                    </span>
                  )}
                  <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-blue-600 px-3.5 py-2 text-sm leading-relaxed text-white">
                    {msg.content}
                  </div>
                </div>
              ) : (
                <div key={msg.id} className="text-[15px] text-gray-800 dark:text-gray-100">
                  <ToolTrace tools={msg.tools_used ?? []} />
                  <div className={`${coachProseClasses} selectable`}>
                    <Markdown remarkPlugins={[remarkGfm]}>{msg.content}</Markdown>
                  </div>
                </div>
              ),
            )}

            {streaming && streamingText && (
              <div className="text-[15px] text-gray-800 dark:text-gray-100">
                <ToolTrace tools={streamingTools} />
                <div className={`${coachProseClasses} selectable`}>
                  <Markdown remarkPlugins={[remarkGfm]}>{streamingText}</Markdown>
                </div>
                <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-gray-400 align-middle dark:bg-gray-500" />
              </div>
            )}
            {streaming && !streamingText && (
              <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {fetchingLabel || 'Thinking...'}
              </div>
            )}

            {proposedAction && (
              <div className="flex flex-col gap-2 rounded-xl border border-blue-600 bg-white px-3 py-3 dark:bg-gray-800">
                <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-blue-600">
                  Your call
                </div>
                <div className="text-[12.5px] leading-relaxed text-gray-800 dark:text-gray-100">
                  {proposedAction.description}
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => void confirmProposedAction()}
                    disabled={confirmingAction}
                    className="rounded-lg bg-blue-600 px-3 py-1.5 text-[12px] font-semibold text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
                  >
                    {confirmingAction ? 'Working...' : proposedAction.confirm_label}
                  </button>
                  <button
                    onClick={() => {
                      setProposedAction(null);
                      setActionError('');
                    }}
                    disabled={confirmingAction}
                    className="rounded-lg border border-gray-300 px-3 py-1.5 text-[12px] font-medium text-gray-500 transition-colors hover:bg-gray-50 dark:border-gray-600 dark:text-gray-400 dark:hover:bg-gray-700/40"
                  >
                    {proposedAction.dismiss_label}
                  </button>
                </div>
                {actionError && (
                  <div className="text-[11.5px] text-gray-500 dark:text-gray-400">{actionError}</div>
                )}
              </div>
            )}

            {actionDone && !proposedAction && (
              <div className="rounded-xl border border-gray-200 px-3 py-2 text-[11.5px] text-gray-500 dark:border-gray-700 dark:text-gray-400">
                {actionDone}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Composer. */}
      <div className="shrink-0 border-t border-gray-100 p-2.5 dark:border-gray-700/60">
        <div className="flex items-end gap-1.5 rounded-2xl bg-gray-100 px-2 py-1.5 transition-shadow focus-within:ring-2 focus-within:ring-blue-500/40 dark:bg-gray-900/70">
          {/* text-base (16px) keeps iOS Safari from auto-zooming on focus (#227). */}
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask your coach..."
            rows={1}
            className="chat-scroll flex-1 resize-none overflow-y-auto border-0 bg-transparent px-2 py-1.5 text-base text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-0 dark:text-gray-100 dark:placeholder:text-gray-500"
            disabled={streaming}
          />
          <button
            onClick={() => void sendMessage()}
            disabled={!input.trim() || streaming}
            aria-label="Send message"
            className="shrink-0 rounded-xl bg-blue-600 p-2 text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-transparent disabled:text-gray-400 dark:disabled:text-gray-600"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
