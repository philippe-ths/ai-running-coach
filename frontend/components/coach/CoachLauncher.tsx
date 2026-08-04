'use client';

// #766: the coach launcher — the one place "ask the coach" lives on mobile.
// Bottom-centre above the tab bar, deliberately NOT tab-shaped: a raised
// circular button in the accent colour with a paper ring, so it reads as an
// action floating over the bar rather than a fifth destination (the design
// spec's open question, resolved this way). Stands down with the tab bar when
// the keyboard opens, and while the sheet itself is open.

import { MessageCircle } from 'lucide-react';
import useKeyboardOpen from '@/lib/useKeyboardOpen';
import { useCoachSheet } from './CoachSheetContext';

export default function CoachLauncher() {
  const { enabled, isOpen, open } = useCoachSheet();
  const keyboardOpen = useKeyboardOpen();

  if (!enabled || isOpen || keyboardOpen) return null;

  return (
    <button
      onClick={open}
      aria-label="Ask your coach"
      className="md:hidden fixed left-1/2 -translate-x-1/2 z-40 bottom-[calc(3.85rem+env(safe-area-inset-bottom))] flex h-14 w-14 items-center justify-center rounded-full bg-blue-600 text-white shadow-[0_6px_18px_rgba(37,99,235,0.35)] ring-[3px] ring-white dark:ring-gray-900 transition-transform active:scale-95"
    >
      <MessageCircle className="h-6 w-6" aria-hidden="true" />
    </button>
  );
}
