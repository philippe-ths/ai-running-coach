'use client';

import { useEffect, useState } from 'react';

// The on-screen keyboard shrinks the VISUAL viewport but not the LAYOUT viewport,
// so a `position: fixed; bottom: 0` bar stays pinned to the layout-viewport bottom —
// which is now hidden behind the keyboard, leaving the bar stranded partway up the
// screen over page content (iOS Safari especially, #683). Fixed bottom chrome is
// not useful while typing, so hide it whenever the keyboard is open and restore it
// on close. The `visualViewport` height dropping well below the layout height is
// the reliable cross-browser keyboard signal; the threshold ignores the smaller
// URL-bar deltas. Shared by BottomNav and the coach sheet/launcher (#766).
const KEYBOARD_HEIGHT_THRESHOLD_PX = 150;

export default function useKeyboardOpen(): boolean {
  const [keyboardOpen, setKeyboardOpen] = useState(false);

  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const update = () => {
      setKeyboardOpen(window.innerHeight - vv.height > KEYBOARD_HEIGHT_THRESHOLD_PX);
    };
    vv.addEventListener('resize', update);
    update();
    return () => vv.removeEventListener('resize', update);
  }, []);

  return keyboardOpen;
}
