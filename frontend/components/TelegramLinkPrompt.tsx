'use client';
import { useEffect, useState } from 'react';
import { Send, X } from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';
import LinkTelegramButton from '@/components/LinkTelegramButton';

type LinkStatus = {
  configured: boolean;
  linked: boolean;
};

// #604: a runner who connected Strava but never completed Telegram /start
// linking silently receives no receipts/openers/reports (Telegram is the only
// channel). Make that visible: show a warm prompt to link, but ONLY when
// linking is available (configured) and not yet done (!linked). Never when the
// bot is unconfigured (nothing to link) or already linked (nothing to nag).
//
// The action reuses LinkTelegramButton directly so the one-tap /start flow is
// not duplicated. Dismissal is persisted in localStorage so it does not nag on
// every load; if the runner later links, the prompt stops showing on its own
// (driven by `linked`) regardless of the dismissal flag.
const DISMISS_KEY = 'telegram_link_prompt_dismissed';

export default function TelegramLinkPrompt() {
  const [status, setStatus] = useState<LinkStatus | null>(null);
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Start hidden (dismissed=true) to avoid an SSR/hydration flash; reveal
    // only after we know both the persisted dismissal and the live status.
    try {
      setDismissed(window.localStorage.getItem(DISMISS_KEY) === '1');
    } catch {
      setDismissed(false);
    }
    fetchFromAPI('/api/coach/telegram/link-status')
      .then((data: LinkStatus | null) => {
        if (!cancelled && data) setStatus(data);
      })
      .catch(() => {
        if (!cancelled) setStatus({ configured: false, linked: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleDismiss = () => {
    try {
      window.localStorage.setItem(DISMISS_KEY, '1');
    } catch {
      // ignore: dismissal simply won't persist across loads
    }
    setDismissed(true);
  };

  if (status === null || !status.configured || status.linked || dismissed) {
    return null;
  }

  return (
    <div
      role="status"
      className="flex flex-col gap-3 rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 text-sm text-sky-900 dark:border-sky-500/40 dark:bg-sky-500/10 dark:text-sky-200 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-start gap-3">
        <Send size={18} className="mt-0.5 shrink-0" />
        <div>
          <p className="font-medium">Link Telegram to get your coach&apos;s messages</p>
          <p className="mt-0.5 text-sky-800/80 dark:text-sky-200/70">
            Your runs are analysed, but without Telegram there is nowhere to send them.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3 self-end sm:self-auto">
        <LinkTelegramButton />
        <button
          type="button"
          onClick={handleDismiss}
          aria-label="Dismiss"
          className="shrink-0 text-sky-700 hover:text-sky-900 dark:text-sky-300 dark:hover:text-sky-100"
        >
          <X size={16} />
        </button>
      </div>
    </div>
  );
}
