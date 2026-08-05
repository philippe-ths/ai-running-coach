'use client';

import { useEffect, useState } from 'react';
import { fetchFromAPI } from '@/lib/api';

// The enabled-state of every coach input that has a UI surface (#522). Mirrors the
// backend `GET /api/coach/feature-flags` (settings.coach_ui_feature_flags). A value
// of false means the input is gated off and its panel should render greyed.
export type CoachFeatureFlags = {
  voice: boolean;
  stance: boolean;
  user_materials: boolean;
  sleep_quality: boolean;
  stops_analysis: boolean;
  // #784: not an input but a SURFACE — false takes the coach thread down
  // (launcher, sheet, and the report's conversational options).
  threads: boolean;
};

// Default to everything ENABLED so a fetch failure never wrongly greys a live input
// (fail open: the worst case is the panel works as before, not a silent lie).
export const DEFAULT_COACH_FEATURE_FLAGS: CoachFeatureFlags = {
  voice: true,
  stance: true,
  user_materials: true,
  sleep_quality: true,
  stops_analysis: true,
  threads: true,
};

export function useCoachFeatureFlags(): CoachFeatureFlags {
  const [flags, setFlags] = useState<CoachFeatureFlags>(DEFAULT_COACH_FEATURE_FLAGS);
  useEffect(() => {
    let active = true;
    fetchFromAPI('/api/coach/feature-flags')
      .then((data) => {
        if (active && data) {
          setFlags({ ...DEFAULT_COACH_FEATURE_FLAGS, ...data });
        }
      })
      .catch(() => {
        /* fail open: keep defaults */
      });
    return () => {
      active = false;
    };
  }, []);
  return flags;
}
