"use client";

// #879: watching a plan being written.
//
// Drafting is a slow LLM call on the worker — about a minute — and it can fail
// outright, leaving the runner's existing plan untouched. Only the empty-week
// state ever watched for the answer, so a runner who already had a plan (which
// is every runner asking for a NEW one) was told nothing at all: not that it had
// started, not that it had landed, not that it had failed. They found out by
// counting kilometres on the screen.
//
// The watching lives here rather than in either surface because two now do it —
// the Schedule screen and the coach sheet, which is where the runner usually is
// when they ask. Two copies of a poll loop drift into two answers about the same
// plan.

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchFromAPI } from "@/lib/api";
import type { DraftStatus } from "@/lib/types/schedule";

const POLL_INTERVAL_MS = 3000;

// A draft that has run far longer than one ever takes is not worth polling for
// ever: a browser tab left open would ask until it was closed. The server has
// its own staleness rule for the same event; this only stops the asking.
const MAX_POLLS = 60;

// A blip mid-draft should not abandon the watch, but a poll that keeps failing
// is not a blip. The schedule surface has a kill switch, and behind it every
// one of these answers 503 — so failures are capped tightly and separately from
// the polls that are working.
const MAX_CONSECUTIVE_ERRORS = 3;

export type DraftWatch = {
  draft: DraftStatus | null;
  /** A draft is being written right now. */
  drafting: boolean;
  /** The most recent draft failed and nothing was written. */
  failed: boolean;
  /** This hook is following a draft it was told about, rather than reporting
   *  whatever it found. A surface that should only speak about a plan the runner
   *  asked for in front of it gates on this. */
  watching: boolean;
  /** POST for a new plan and watch it. */
  start: () => Promise<void>;
  /** Start watching a draft someone else began — a confirmed card, another tab. */
  watch: () => void;
  starting: boolean;
  error: string | null;
};

type Options = {
  /** Read the current state once on mount. A surface that is ABOUT the schedule
   *  wants this; one that merely sits over it should not ask on every open, and
   *  should not surface a failure from days ago to a runner who never asked. */
  pollOnMount?: boolean;
};

/**
 * @param onPlanReady called once, when a WATCHED draft becomes active.
 */
export function useDraftStatus(
  onPlanReady?: () => void,
  { pollOnMount = true }: Options = {},
): DraftWatch {
  const [draft, setDraft] = useState<DraftStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [watching, setWatching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const polls = useRef(0);
  const errors = useRef(0);
  // The callback identity changes with its owner's state; a ref keeps the poll
  // loop from re-subscribing (and double-polling) every time it does.
  const ready = useRef(onPlanReady);
  ready.current = onPlanReady;
  // Two different questions, deliberately kept apart. `isWatching` is "the
  // runner asked for this one, in front of me", which decides whether a surface
  // should speak about it at all. `sawDrafting` is "a plan was being written
  // while I was looking", which decides whether the screen underneath needs
  // refreshing — true for a draft started on another device, where nobody asked
  // this surface for anything but the week still changed.
  const isWatching = useRef(false);
  const sawDrafting = useRef(false);

  const poll = useCallback(async () => {
    try {
      const data: DraftStatus | null = await fetchFromAPI("/api/schedule/draft");
      errors.current = 0;
      setDraft(data);
      if (data?.status === "drafting" && polls.current < MAX_POLLS) {
        sawDrafting.current = true;
        polls.current += 1;
        timer.current = setTimeout(poll, POLL_INTERVAL_MS);
        return;
      }
      if (data?.status === "active" && (sawDrafting.current || isWatching.current)) {
        sawDrafting.current = false;
        ready.current?.();
      }
      // `isWatching` deliberately survives the terminal state. It is what lets a
      // surface report the OUTCOME of the draft the runner asked for; clearing
      // it here would hide the failure at the exact moment there is a failure to
      // report. A new `watch()` is the only thing that resets it.
    } catch {
      // A failed poll says nothing about the plan either way, so it changes no
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
    sawDrafting.current = false;
    isWatching.current = true;
    setWatching(true);
    void poll();
  }, [poll]);

  // On mount, pick up a draft already in flight — asked for on another device or
  // before a reload — and report where it stands.
  useEffect(() => {
    if (pollOnMount) void poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll, pollOnMount]);

  const drafting = draft?.status === "drafting";

  const start = useCallback(async () => {
    if (starting || drafting) return;
    setStarting(true);
    setError(null);
    try {
      const data: DraftStatus = await fetchFromAPI("/api/schedule/draft", {
        method: "POST",
      });
      setDraft(data);
      watch();
    } catch {
      setError(
        "Could not ask your coach for a plan just now. Nothing has changed — try again.",
      );
    } finally {
      setStarting(false);
    }
  }, [drafting, starting, watch]);

  return {
    draft,
    drafting,
    failed: draft?.status === "failed",
    watching,
    start,
    watch,
    starting,
    error,
  };
}
