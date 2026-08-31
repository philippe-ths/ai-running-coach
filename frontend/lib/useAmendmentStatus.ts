"use client";

// #1003: watching a week being rewritten.
//
// `useDraftStatus`'s sibling, and it exists for the same reason that one does.
// A confirmed amendment is written on the worker, takes about half a minute, and
// can fail outright leaving the plan exactly as it was. Nothing on screen said
// any of that: the runner tapped "Update my plan", the coach said it was working
// it out, and the week stayed empty until they reloaded the page on a hunch.
//
// It is a separate hook rather than a branch inside the draft one. They answer
// different questions - a draft replaces the plan, an amendment rewrites one
// window of it - and the window is the whole point here, because the runner may
// be looking at a week the change does not touch.

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchFromAPI } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

// An amendment that has run far longer than one ever takes is not worth polling
// for ever. The server's own key expires on a similar horizon; this only stops
// the asking.
const MAX_POLLS = 60;

// A blip mid-write should not abandon the watch, but a poll that keeps failing
// is not a blip. Behind the schedule kill switch every one of these answers 503.
const MAX_CONSECUTIVE_ERRORS = 3;

export type AmendmentStatus = {
  status: "working" | "done" | "failed" | null;
  start: string | null;
  end: string | null;
  changes: string[];
  detail: string | null;
};

export type AmendmentWatch = {
  amendment: AmendmentStatus | null;
  /** A week is being rewritten right now. */
  working: boolean;
  /** The last one failed and nothing was written. */
  failed: boolean;
  /** Start watching one that has just been confirmed. */
  watch: () => void;
  /** Stop the server reporting an outcome this surface has now shown. */
  dismiss: () => Promise<void>;
  /** True when this week is the one being rewritten. */
  covers: (weekStart: string) => boolean;
};

/**
 * @param onLanded called once, when a watched amendment finishes writing.
 */
export function useAmendmentStatus(onLanded?: () => void): AmendmentWatch {
  const [amendment, setAmendment] = useState<AmendmentStatus | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const polls = useRef(0);
  const errors = useRef(0);
  // The callback identity changes with its owner's state; a ref keeps the poll
  // loop from re-subscribing every time it does.
  const landed = useRef(onLanded);
  landed.current = onLanded;
  // "A rewrite was running while I was looking", which decides whether the
  // screen underneath needs refreshing. True for one confirmed on another
  // device too, where nobody asked this surface for anything but the week
  // still changed.
  const sawWorking = useRef(false);

  const poll = useCallback(async () => {
    try {
      const data: AmendmentStatus | null = await fetchFromAPI(
        "/api/schedule/amendment",
      );
      errors.current = 0;
      setAmendment(data);
      if (data?.status === "working" && polls.current < MAX_POLLS) {
        sawWorking.current = true;
        polls.current += 1;
        timer.current = setTimeout(poll, POLL_INTERVAL_MS);
        return;
      }
      if (data?.status === "done" && sawWorking.current) {
        sawWorking.current = false;
        landed.current?.();
      }
    } catch {
      // A failed poll says nothing about the week either way, so it changes no
      // state. It only decides whether to keep asking.
      errors.current += 1;
      if (errors.current < MAX_CONSECUTIVE_ERRORS && polls.current < MAX_POLLS) {
        polls.current += 1;
        timer.current = setTimeout(poll, POLL_INTERVAL_MS);
      }
    }
  }, []);

  const watch = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    polls.current = 0;
    errors.current = 0;
    // Deliberately NOT cleared: a confirm is followed within milliseconds by a
    // poll that may still read `working`, and treating this as a fresh start
    // would lose the landing callback for the very change just confirmed.
    sawWorking.current = true;
    void poll();
  }, [poll]);

  // On mount, pick up one already in flight - confirmed on another device, or
  // before a reload, which is the case the runner actually hit.
  useEffect(() => {
    void poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  const dismiss = useCallback(async () => {
    setAmendment(null);
    try {
      await fetchFromAPI("/api/schedule/amendment/seen", { method: "POST" });
    } catch {
      // Local state is already cleared, so the runner sees the right thing; the
      // server copy expires on its own.
    }
  }, []);

  const covers = useCallback(
    (weekStart: string) => {
      if (!amendment?.start || !amendment?.end) return false;
      // Whole-week granularity: an amendment names whole weeks, so a week is
      // either inside the window or it is not.
      return weekStart >= amendment.start && weekStart <= amendment.end;
    },
    [amendment],
  );

  return {
    amendment,
    working: amendment?.status === "working",
    failed: amendment?.status === "failed",
    watch,
    dismiss,
    covers,
  };
}
