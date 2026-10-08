"use client";

// #1064: reading the season, and watching it being planned.
//
// Planning a season is a slow model call on the worker, so the POST answers 202
// and this follows `useDraftStatus`'s idiom: poll the read while it says
// "drafting", cap the polls, and tolerate a blip but not a run of failures.
//
// A separate hook rather than a branch of the draft one: they answer different
// questions. The draft writes sessions; the season is the coach's opinion of the
// goals those sessions will serve.

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchFromAPI } from "@/lib/api";
import type { SeasonRead } from "@/lib/types/season";

const POLL_INTERVAL_MS = 3000;

// A season is a longer job than a draft (web search, thinking), but one that has
// run for 15 minutes is not coming back; stop asking rather than poll for ever.
const MAX_POLLS = 300;

// Behind the schedule kill switch every poll answers 503: cap the failures
// separately from the polls that work.
const MAX_CONSECUTIVE_ERRORS = 3;

export type SeasonWatch = {
  season: SeasonRead | null;
  /** The first read has not answered yet. */
  loading: boolean;
  drafting: boolean;
  failed: boolean;
  starting: boolean;
  error: string | null;
  /** Ask the coach to (re)plan the season, and watch it. */
  start: () => Promise<void>;
};

/**
 * @param refreshToken bumped by the owner when the runner's goals change, so the
 *   `stale` flag is re-read rather than left reporting a season that was current.
 * @param onReady called once, when a season this hook saw drafting becomes active.
 */
export function useSeason(refreshToken = 0, onReady?: () => void): SeasonWatch {
  const [season, setSeason] = useState<SeasonRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const polls = useRef(0);
  const errors = useRef(0);
  const sawDrafting = useRef(false);
  const alive = useRef(true);
  // A ref keeps the poll loop from re-subscribing when the owner's callback
  // changes identity.
  const ready = useRef(onReady);
  ready.current = onReady;

  const poll = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    try {
      const data: SeasonRead | null = await fetchFromAPI("/api/schedule/season");
      if (!alive.current) return;
      errors.current = 0;
      setSeason(data);
      setLoading(false);
      if (data?.status === "drafting" && polls.current < MAX_POLLS) {
        sawDrafting.current = true;
        polls.current += 1;
        timer.current = setTimeout(poll, POLL_INTERVAL_MS);
        return;
      }
      if (data?.status === "active" && sawDrafting.current) {
        sawDrafting.current = false;
        ready.current?.();
      }
    } catch {
      if (!alive.current) return;
      // A failed poll says nothing about the season either way.
      errors.current += 1;
      setLoading(false);
      if (errors.current < MAX_CONSECUTIVE_ERRORS && polls.current < MAX_POLLS) {
        polls.current += 1;
        timer.current = setTimeout(poll, POLL_INTERVAL_MS);
      }
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    polls.current = 0;
    errors.current = 0;
    void poll();
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
    // A bumped token is "the goals changed": read again from the top.
  }, [poll, refreshToken]);

  const drafting = season?.status === "drafting";

  const start = useCallback(async () => {
    if (starting || drafting) return;
    setStarting(true);
    setError(null);
    try {
      await fetchFromAPI("/api/schedule/season", { method: "POST" });
    } catch {
      // A 409 means one is already being written, which is what the runner
      // wanted to happen. Anything else is a real failure, and the read below
      // tells the two apart without guessing from a status string.
      try {
        const now: SeasonRead | null = await fetchFromAPI("/api/schedule/season");
        if (now?.status !== "drafting") {
          setError(
            "Could not ask your coach to plan your season just now. Nothing has changed, try again.",
          );
          setStarting(false);
          return;
        }
      } catch {
        setError(
          "Could not ask your coach to plan your season just now. Nothing has changed, try again.",
        );
        setStarting(false);
        return;
      }
    }
    polls.current = 0;
    errors.current = 0;
    // The POST created the drafting row before it answered, so the first read
    // sees it; `sawDrafting` is set by the poll that actually does.
    await poll();
    setStarting(false);
  }, [drafting, poll, starting]);

  return {
    season,
    loading,
    drafting,
    failed: season?.status === "failed",
    starting,
    error,
    start,
  };
}
