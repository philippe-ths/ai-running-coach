'use client';

// #766: the app-level coach sheet state. Mounted once in the root layout, so
// the sheet (and any in-flight reply) survives route navigation — the App
// Router re-renders pages, not the layout. This is the app's first
// app-level client context; the launcher, NavBar button, and sheet all read it.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
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

// #767: the screen's view SELECTIONS (the runner's inputs — a range, a type
// filter — never numbers). A page with parameterised state publishes them so
// the turn's pointer can name them; the server recomputes everything else.
export type ScreenSelections = {
  range?: string;
  types?: string[];
};

interface CoachSheetState {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  toggle: () => void;
  screen: ScreenIdentity;
  selections: ScreenSelections | null;
  publishSelections: (sel: ScreenSelections | null) => void;
}

const CoachSheetContext = createContext<CoachSheetState | null>(null);

export function CoachSheetProvider({ children }: { children: React.ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [selections, setSelections] = useState<ScreenSelections | null>(null);
  const pathname = usePathname();

  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const toggle = useCallback(() => setIsOpen(v => !v), []);
  const publishSelections = useCallback(
    (sel: ScreenSelections | null) => setSelections(sel),
    [],
  );

  const screen = useMemo(() => screenFromPathname(pathname ?? '/'), [pathname]);

  // Selections belong to the page that published them: the publisher hook's
  // unmount cleanup clears them when the runner leaves the page, so no
  // pathname-watching is needed here (a parent-effect clear would run AFTER
  // the child page's publish on mount and wipe it).
  const value = useMemo(
    () => ({ isOpen, open, close, toggle, screen, selections, publishSelections }),
    [isOpen, open, close, toggle, screen, selections, publishSelections],
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

/** #767: a page with parameterised view state calls this with its current
 * selections; they ride the next turn's screen pointer and the ribbon. */
export function usePublishScreenSelections(sel: ScreenSelections | null) {
  const { publishSelections } = useCoachSheet();
  const key = JSON.stringify(sel);
  useEffect(() => {
    publishSelections(sel);
    return () => publishSelections(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, publishSelections]);
}
