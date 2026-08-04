'use client';

// #766: the app-level coach sheet state. Mounted once in the root layout, so
// the sheet (and any in-flight reply) survives route navigation — the App
// Router re-renders pages, not the layout. This is the app's first
// app-level client context; the launcher, NavBar button, and sheet all read it.

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from 'react';
import { usePathname } from 'next/navigation';

// The screen identity the sheet knows client-side (ADR 0028: identity and
// selections only — labels, never numbers; the server resolves facts). Slice 2
// carries the label as per-turn provenance (`asked_from`); slice 3 (#767) turns
// it into the resolved screen-context pointer.
export interface ScreenIdentity {
  // Stable key stored as asked_from ("home" | "activities" | "activity" |
  // "load" | "trends" | "profile").
  key: string;
  // Human form for the UI ("Home", "Tuesday's run"…).
  label: string;
  // Present on an activity page: the id the sheet may anchor a new thread to.
  activityId?: string;
}

export function screenFromPathname(pathname: string): ScreenIdentity {
  const activityMatch = pathname.match(/^\/activity\/([^/]+)/);
  if (activityMatch) {
    return { key: 'activity', label: 'This run', activityId: activityMatch[1] };
  }
  if (pathname.startsWith('/activities')) return { key: 'activities', label: 'Activities' };
  if (pathname.startsWith('/load')) return { key: 'load', label: 'Load' };
  if (pathname.startsWith('/trends')) return { key: 'trends', label: 'Trends' };
  if (pathname.startsWith('/profile')) return { key: 'profile', label: 'Profile' };
  return { key: 'home', label: 'Home' };
}

interface CoachSheetState {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  toggle: () => void;
  screen: ScreenIdentity;
}

const CoachSheetContext = createContext<CoachSheetState | null>(null);

export function CoachSheetProvider({ children }: { children: React.ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const pathname = usePathname();

  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const toggle = useCallback(() => setIsOpen(v => !v), []);

  const screen = useMemo(() => screenFromPathname(pathname ?? '/'), [pathname]);

  const value = useMemo(
    () => ({ isOpen, open, close, toggle, screen }),
    [isOpen, open, close, toggle, screen],
  );

  return (
    <CoachSheetContext.Provider value={value}>
      {children}
    </CoachSheetContext.Provider>
  );
}

export function useCoachSheet(): CoachSheetState {
  const ctx = useContext(CoachSheetContext);
  if (!ctx) throw new Error('useCoachSheet must be used within CoachSheetProvider');
  return ctx;
}
