'use client';
import { useEffect, useState } from 'react';
import { CheckCircle2, Send, X } from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';

type LinkStatus = {
  configured: boolean;
  linked: boolean;
};

// Binds the runner's Telegram chat so coach reports/receipts route to them and
// their RPE/pain/done taps are authorized as theirs (#477). "Link" mints a
// one-time deep link and opens Telegram at the bot's /start; the bot webhook
// captures the chat and binds it. We do not poll for the bind here — the runner
// returns and the status re-fetches on next mount.
export default function LinkTelegramButton() {
  const [status, setStatus] = useState<LinkStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () =>
    fetchFromAPI('/api/coach/telegram/link-status')
      .then((data: LinkStatus | null) => {
        if (data) setStatus(data);
      })
      .catch(() => setStatus({ configured: false, linked: false }));

  useEffect(() => {
    let cancelled = false;
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

  // Hidden until status loads, and entirely when the bot is not configured
  // (the deep link cannot be built) so the profile stays clean.
  if (status === null || !status.configured) return null;

  const handleLink = async () => {
    setBusy(true);
    try {
      const data = await fetchFromAPI('/api/coach/telegram/link-token', { method: 'POST' });
      if (data?.deep_link) window.open(data.deep_link, '_blank', 'noopener');
    } catch {
      // ignore: the button stays available to retry
    } finally {
      setBusy(false);
    }
  };

  const handleUnlink = async () => {
    setBusy(true);
    try {
      await fetchFromAPI('/api/coach/telegram/link', { method: 'DELETE' });
      await refresh();
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  if (status.linked) {
    return (
      <div className="flex flex-col gap-1">
        <span className="inline-flex items-center gap-2 text-sm text-green-700 dark:text-green-300">
          <CheckCircle2 size={16} />
          <span>Telegram linked</span>
        </span>
        <button
          type="button"
          onClick={handleUnlink}
          disabled={busy}
          className="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:underline ml-6 disabled:opacity-50"
          title="Stop routing coach messages to your Telegram"
        >
          <X size={12} />
          Unlink
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={handleLink}
      disabled={busy}
      className="inline-flex items-center gap-2 bg-[#229ED9] text-white px-4 py-2 rounded font-semibold hover:bg-[#1b8ec2] transition-colors disabled:opacity-50"
      title="Connect Telegram so your coach messages and check-in taps reach you"
    >
      <Send size={18} />
      {busy ? 'Opening Telegram…' : 'Link Telegram'}
    </button>
  );
}
