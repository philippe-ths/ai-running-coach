'use client';
import { useEffect, useState } from 'react';
import { Home, X } from 'lucide-react';

// #228 mitigation. On iOS, tapping the "view in app" link inside Telegram opens
// Safari, not the installed home-screen PWA, because iOS offers no way for an
// external app to route a URL into an installed web app (that needs a native
// shell with Universal Links or a custom URL scheme -- out of scope here). This
// is a UX nudge only: when we are in a browser tab (not the standalone PWA) on
// an iOS device, show a subtle, dismissible hint to reopen from the home screen.
// It never claims to auto-route, and dismissal is remembered so it does not nag.

const DISMISS_KEY = 'pwa-open-in-app-dismissed';

function isStandalone(): boolean {
  if (typeof window === 'undefined') return false;
  const mm =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(display-mode: standalone)').matches;
  // iOS Safari does not implement the display-mode media query; it exposes a
  // non-standard `navigator.standalone` boolean instead.
  const iosStandalone =
    (navigator as unknown as { standalone?: boolean }).standalone === true;
  return mm || iosStandalone;
}

function isIos(): boolean {
  if (typeof navigator === 'undefined') return false;
  const ua = navigator.userAgent || '';
  if (/iPad|iPhone|iPod/.test(ua)) return true;
  // iPadOS 13+ reports a desktop-Mac UA; disambiguate by touch support.
  return ua.includes('Macintosh') && navigator.maxTouchPoints > 1;
}

export default function PwaOpenInAppBanner() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    // Only nudge iOS browser tabs: that is where the Telegram-opens-Safari bug
    // lives, and the "home screen" framing is Safari-specific. Skip when already
    // in the standalone PWA, or when the user has dismissed the hint before.
    if (isStandalone() || !isIos()) return;
    try {
      if (window.localStorage.getItem(DISMISS_KEY) === '1') return;
    } catch {
      // localStorage can throw in private mode; fall through and just show it.
    }
    setShow(true);
  }, []);

  if (!show) return null;

  const dismiss = () => {
    setShow(false);
    try {
      window.localStorage.setItem(DISMISS_KEY, '1');
    } catch {
      // Best-effort persistence; a throw here only means it may reappear later.
    }
  };

  return (
    <div
      role="note"
      className="flex items-start gap-3 border-b border-gray-200 bg-gray-50 px-4 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-300"
    >
      <Home size={16} className="mt-0.5 shrink-0 text-gray-500 dark:text-gray-400" />
      <p className="flex-1">
        For the best experience, open Running Coach from your home screen. Not
        added yet? Tap the Share icon, then <span className="font-medium">Add to Home Screen</span>.
      </p>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        className="shrink-0 text-gray-400 hover:text-gray-700 dark:hover:text-gray-100"
      >
        <X size={16} />
      </button>
    </div>
  );
}
