'use client';

import Link from 'next/link';
import { ChevronLeft, Loader2 } from 'lucide-react';
import { ReactNode } from 'react';

// The chrome every profile detail screen shares (#941): back to the hub on the
// left, the screen's name as the page heading, and -- where the screen edits
// profile fields -- one Save on the right.
//
// Screens that own their own save (the coach dial panels, the connection
// buttons) pass no onSave and get no button, which is what makes it
// unambiguous which control writes what. The old page put a "Save Profile"
// button above three panels that each saved separately.

export default function SectionScreen({
  title,
  description,
  backHref = '/profile',
  onSave,
  saving = false,
  children,
}: {
  title: string;
  description?: string;
  backHref?: string;
  onSave?: () => void;
  saving?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-6 flex items-center gap-2">
        <Link
          href={backHref}
          className="-ml-2 flex min-h-[44px] items-center gap-1 rounded-md px-2 text-blue-600 transition-colors hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400 dark:hover:bg-blue-900/30"
        >
          <ChevronLeft size={20} aria-hidden="true" />
          <span className="text-sm">Profile</span>
        </Link>

        <h1 className="flex-1 truncate text-center text-lg font-semibold">{title}</h1>

        {onSave ? (
          <button
            type="submit"
            onClick={onSave}
            disabled={saving}
            className="flex min-h-[44px] items-center gap-1.5 rounded-md px-3 text-sm font-semibold text-blue-600 transition-colors hover:bg-blue-50 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400 dark:hover:bg-blue-900/30"
          >
            {saving && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
            {saving ? 'Saving…' : 'Save'}
          </button>
        ) : (
          // Keeps the heading optically centred against the back link.
          <span aria-hidden="true" className="w-[72px]" />
        )}
      </div>

      {description && (
        <p className="mb-5 text-sm text-gray-500 dark:text-gray-400">{description}</p>
      )}

      {children}
    </div>
  );
}
