'use client';

// #766: the thread switcher — the thread's name IS the control. Tapping the
// title in the thread bar drops this panel over the transcript: start a new
// thread, resume another, rename, or delete. Threads idle beyond a month fold
// under "Earlier" so the list stays about the present.

import { useState } from 'react';
import { Check, Pencil, Plus, Trash2, X } from 'lucide-react';
import { ThreadListItem } from '@/lib/types';

// A thread idle beyond this folds under "Earlier" (handoff decision: one month).
const EARLIER_AFTER_DAYS = 31;

function relativeWhen(iso?: string | null): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  const hours = (Date.now() - then) / 3_600_000;
  if (hours < 1) return 'now';
  if (hours < 24) return `${Math.floor(hours)}h`;
  if (hours < 24 * 7) {
    return new Date(iso).toLocaleDateString(undefined, { weekday: 'short' });
  }
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

function anchorChip(t: ThreadListItem): string | null {
  if (!t.anchor) return null;
  const parts: string[] = [];
  if (t.anchor.start_date) {
    parts.push(
      new Date(t.anchor.start_date).toLocaleDateString(undefined, {
        weekday: 'short',
      }),
    );
  }
  if (t.anchor.distance_m != null) {
    parts.push(`${(t.anchor.distance_m / 1000).toFixed(1)} km`);
  }
  return parts.length ? parts.join(' · ') : (t.anchor.name ?? null);
}

interface Props {
  threads: ThreadListItem[];
  currentThreadId: string | null;
  onSelect: (id: string) => void;
  onNewThread: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}

export default function ThreadSwitcher({
  threads,
  currentThreadId,
  onSelect,
  onNewThread,
  onRename,
  onDelete,
  onClose,
}: Props) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  // Two-tap delete: first tap arms, second confirms (no blocking dialogs).
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);

  const cutoff = Date.now() - EARLIER_AFTER_DAYS * 24 * 3_600_000;
  const recent = threads.filter(
    t => !t.last_message_at || new Date(t.last_message_at).getTime() >= cutoff,
  );
  const earlier = threads.filter(
    t => t.last_message_at && new Date(t.last_message_at).getTime() < cutoff,
  );

  const commitRename = (id: string) => {
    const title = renameDraft.trim();
    setRenamingId(null);
    if (title) onRename(id, title);
  };

  const renderRow = (t: ThreadListItem) => (
    <div
      key={t.id}
      className="border-b border-gray-100 dark:border-gray-700/60 py-2.5 last:border-b-0"
    >
      {renamingId === t.id ? (
        <div className="flex items-center gap-2">
          <input
            autoFocus
            value={renameDraft}
            onChange={e => setRenameDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') commitRename(t.id);
              if (e.key === 'Escape') setRenamingId(null);
            }}
            className="flex-1 rounded-md border border-gray-300 dark:border-gray-600 bg-transparent px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            onClick={() => commitRename(t.id)}
            aria-label="Save name"
            className="p-1.5 text-blue-600 dark:text-blue-400"
          >
            <Check className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <div className="group flex items-start gap-2">
          <button onClick={() => onSelect(t.id)} className="min-w-0 flex-1 text-left">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-[13px] font-semibold text-gray-900 dark:text-gray-100">
                {t.title}
                {t.id === currentThreadId && (
                  <span className="ml-1.5 font-mono text-[9px] tracking-widest text-blue-600 dark:text-blue-400">
                    NOW
                  </span>
                )}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-gray-400 dark:text-gray-500">
                {relativeWhen(t.last_message_at)}
              </span>
            </div>
            {t.snippet && (
              <div className="truncate text-[12px] text-gray-500 dark:text-gray-400">
                {t.snippet}
              </div>
            )}
            {anchorChip(t) && (
              <span className="mt-1 inline-block rounded-full border border-gray-200 dark:border-gray-600 px-1.5 py-px font-mono text-[9px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
                {anchorChip(t)}
              </span>
            )}
          </button>
          <div className="flex shrink-0 items-center gap-0.5 pt-0.5">
            {confirmingDeleteId === t.id ? (
              <button
                onClick={() => {
                  setConfirmingDeleteId(null);
                  onDelete(t.id);
                }}
                className="rounded-md bg-red-600 px-2 py-1 text-[11px] font-semibold text-white"
              >
                Delete?
              </button>
            ) : (
              <>
                <button
                  onClick={() => {
                    setRenamingId(t.id);
                    setRenameDraft(t.title);
                  }}
                  aria-label={`Rename ${t.title}`}
                  className="p-1.5 text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => setConfirmingDeleteId(t.id)}
                  aria-label={`Delete ${t.title}`}
                  className="p-1.5 text-gray-300 hover:text-red-500 dark:text-gray-600 dark:hover:text-red-400"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );

  return (
    <>
      {/* Scrim over the transcript; a tap outside closes the switcher. */}
      <div
        className="absolute inset-0 z-[4] bg-slate-900/30"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="absolute inset-x-2.5 top-12 z-[5] max-h-[70%] overflow-y-auto chat-scroll rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 pb-2 pt-1 shadow-xl">
        <button
          onClick={onNewThread}
          className="flex w-full items-center gap-2 py-2.5 text-[13px] font-semibold text-blue-600 dark:text-blue-400"
        >
          <Plus className="h-4 w-4" /> Start a new thread
        </button>
        {recent.map(renderRow)}
        {earlier.length > 0 && (
          <div className="pt-2 font-mono text-[9px] uppercase tracking-widest text-gray-400 dark:text-gray-500">
            Earlier
          </div>
        )}
        {earlier.map(renderRow)}
        {threads.length === 0 && (
          <p className="py-2 text-[12px] text-gray-400 dark:text-gray-500">
            No conversations yet.
          </p>
        )}
        <button
          onClick={onClose}
          aria-label="Close thread list"
          className="absolute right-2 top-2 p-1 text-gray-300 hover:text-gray-500 dark:text-gray-600"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </>
  );
}
