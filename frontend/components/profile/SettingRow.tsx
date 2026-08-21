'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { ReactNode } from 'react';

// The hub's building blocks (#941): a titled group of rows, each stating a
// setting's CURRENT VALUE and linking to the screen that edits it. Rows are
// real links so they keep link semantics -- keyboard, focus ring, open in new
// tab -- and so Next keeps the profile route mounted when only the query
// changes, which is what makes the hub feel instant.

export function SettingGroup({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const headingId = `group-${title.toLowerCase().replace(/[^a-z]+/g, '-')}`;
  return (
    <section aria-labelledby={headingId}>
      <h2
        id={headingId}
        className="px-1 pb-2 text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400"
      >
        {title}
      </h2>
      <div className="overflow-hidden rounded-xl border bg-white dark:border-gray-700 dark:bg-gray-800">
        {children}
      </div>
    </section>
  );
}

export function SettingRow({
  href,
  name,
  value,
  valueTone = 'default',
  mono = false,
}: {
  href: string;
  name: string;
  // The current value, read out after the name so the row announces as
  // "Goal, Half marathon" rather than leaving the value as decoration.
  value: string;
  valueTone?: 'default' | 'muted' | 'attention';
  mono?: boolean;
}) {
  const toneClass =
    valueTone === 'attention'
      ? 'text-amber-700 dark:text-amber-400'
      : valueTone === 'muted'
        ? 'text-gray-400 dark:text-gray-500'
        : 'text-gray-500 dark:text-gray-400';

  return (
    <Link
      href={href}
      className="flex min-h-[56px] items-center gap-3 border-b px-4 py-2 transition-colors last:border-b-0 hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 dark:border-gray-700 dark:hover:bg-gray-700/40"
    >
      <span className="flex-1 text-[15px]">{name}</span>
      <span className={`text-sm ${toneClass} ${mono ? 'font-mono' : ''}`}>{value}</span>
      <ChevronRight
        size={18}
        aria-hidden="true"
        className="shrink-0 text-gray-400 dark:text-gray-500"
      />
    </Link>
  );
}
