'use client';
import { useEffect, useState } from 'react';
import { AlertTriangle, ExternalLink, X } from 'lucide-react';

// The Strava OAuth callback redirects here (the app base URL) with
// ?strava_error=state_expired when the signed state was absent or expired in
// production (#599/#615) instead of silently minting an orphan account. Surface
// a clear reconnect prompt at the landing page, then strip the param so a
// refresh or back-navigation does not resurface the notice.
export default function StravaConnectNotice() {
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    if (params.get('strava_error') === 'state_expired') {
      setExpired(true);
      params.delete('strava_error');
      const qs = params.toString();
      window.history.replaceState(
        {},
        '',
        window.location.pathname + (qs ? `?${qs}` : ''),
      );
    }
  }, []);

  if (!expired) return null;

  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
    >
      <AlertTriangle size={18} className="mt-0.5 shrink-0" />
      <div className="flex-1">
        <p className="font-medium">Strava connection did not complete</p>
        <p className="mt-0.5">
          The secure link expired before you finished authorising. Please connect again.
        </p>
        <a
          href="/api/auth/strava/login"
          className="mt-2 inline-flex items-center gap-1 font-medium underline"
        >
          <ExternalLink size={14} /> Connect with Strava
        </a>
      </div>
      <button
        type="button"
        onClick={() => setExpired(false)}
        aria-label="Dismiss"
        className="shrink-0 text-amber-700 hover:text-amber-900 dark:text-amber-300 dark:hover:text-amber-100"
      >
        <X size={16} />
      </button>
    </div>
  );
}
