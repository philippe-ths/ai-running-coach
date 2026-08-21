'use client';

import { useEffect, useState } from 'react';
import { fetchFromAPI } from '@/lib/api';

// #941: the profile hub states Strava and Telegram as VALUES ("Connected",
// "Not linked") alongside every other setting, so the summary reads as one list
// rather than a row of live controls. The controls themselves stay in
// ConnectStravaButton / LinkTelegramButton on the connections screen; this hook
// only reads, and deliberately duplicates their fetch rather than refactoring
// two working components to hoist their state.
//
// Fails to "unknown", never to "disconnected": telling a runner their Strava is
// gone because a status call failed is worse than saying nothing.

export type StravaConnection = {
  connected: boolean;
  athleteId: number | null;
};

export type TelegramConnection = {
  // Bot not configured at all -- the row is meaningless and the hub hides it,
  // matching LinkTelegramButton's own rule.
  configured: boolean;
  linked: boolean;
};

export type ConnectionStatus = {
  loading: boolean;
  strava: StravaConnection | null;
  telegram: TelegramConnection | null;
};

export function useConnectionStatus(): ConnectionStatus {
  const [loading, setLoading] = useState(true);
  const [strava, setStrava] = useState<StravaConnection | null>(null);
  const [telegram, setTelegram] = useState<TelegramConnection | null>(null);

  useEffect(() => {
    let cancelled = false;

    const readStrava = fetchFromAPI('/api/auth/strava/status')
      .then((data) => {
        if (cancelled || !data) return;
        setStrava({
          connected: Boolean(data.connected),
          athleteId: data.athlete_id ?? null,
        });
      })
      .catch(() => {
        /* leave null: unknown, not disconnected */
      });

    const readTelegram = fetchFromAPI('/api/coach/telegram/link-status')
      .then((data) => {
        if (cancelled || !data) return;
        setTelegram({
          configured: Boolean(data.configured),
          linked: Boolean(data.linked),
        });
      })
      .catch(() => {
        /* leave null */
      });

    Promise.all([readStrava, readTelegram]).finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  return { loading, strava, telegram };
}
