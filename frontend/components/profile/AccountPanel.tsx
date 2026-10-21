'use client';

import { useEffect, useState } from 'react';
import { useClerk } from '@clerk/nextjs';
import { Loader2, LogOut } from 'lucide-react';

// The account screen (#967, #968): who you are signed in as, sign out, and
// delete. It replaces Clerk's own user menu, whose delete button removed the
// sign-in and left every row the app holds behind, so a re-registration landed
// on the old data. This one calls the app's own endpoint, which removes both.
//
// Two guards (#969). The runner types the account's email, which the server
// checks too: the real failure is being signed into the wrong one of two
// identical-looking accounts. And the deployment owner's account is never
// deletable here; the server says so through `deletable`.
//
// Retiring Clerk's menu also dropped its "active devices" view, the one thing it
// offered with no equivalent here. Accepted for now (#968): not worth building
// for a handful of runners, and revisited if the app grows.
//
// Rendered only when Clerk is configured: `useClerk` needs its provider.

type Account = { email: string; deletable: boolean };

const DESTROYED =
  'your activities, streams and analysis, coaching reports, conversations, ' +
  'check-ins, what your coach remembers, uploaded materials, your plan, your ' +
  'Strava connection and your profile';

export default function AccountPanel() {
  const { signOut } = useClerk();
  const [account, setAccount] = useState<Account | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch('/api/account');
        if (!res.ok) throw new Error(String(res.status));
        setAccount(await res.json());
      } catch {
        setLoadFailed(true);
      }
    })();
  }, []);

  const matches =
    account !== null && typed.trim().toLowerCase() === account.email.trim().toLowerCase();

  const deleteAccount = async () => {
    if (!matches || deleting) return;
    setDeleting(true);
    setError('');
    try {
      const res = await fetch('/api/account', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm_email: typed }),
      });
      if (res.status !== 204) {
        const detail = (await res.json().catch(() => null))?.detail;
        setError(
          typeof detail === 'string'
            ? detail
            : 'Your account could not be deleted. Nothing has changed — try again.',
        );
        return;
      }
      // The sign-in record is already gone server-side; this clears the
      // browser's session so the app does not keep acting as a deleted user.
      await signOut({ redirectUrl: '/sign-in' });
    } catch {
      setError('Your account could not be deleted. Nothing has changed — try again.');
    } finally {
      setDeleting(false);
    }
  };

  const card =
    'rounded-xl border bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800';

  return (
    <div className="space-y-4">
      <div className={card}>
        <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Signed in as
        </p>
        <p className="mt-1 break-all text-sm font-medium">
          {account?.email ?? (loadFailed ? 'Could not load your account' : '…')}
        </p>
        <button
          type="button"
          onClick={() => void signOut({ redirectUrl: '/sign-in' })}
          className="mt-4 inline-flex min-h-[40px] items-center gap-2 rounded-md border border-gray-300 px-3 text-sm font-medium hover:bg-gray-50 dark:border-gray-600 dark:hover:bg-gray-700/40"
        >
          <LogOut size={16} aria-hidden="true" />
          Sign out
        </button>
      </div>

      {account && (
        <div className={card}>
          <h2 className="text-sm font-semibold">Delete account</h2>
          {!account.deletable ? (
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
              This is the account that owns this app, so it cannot be deleted
              here. Its coaching history exists nowhere else.
            </p>
          ) : !confirming ? (
            <>
              <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
                Permanently removes your sign-in and everything the app holds
                about you.
              </p>
              <button
                type="button"
                onClick={() => setConfirming(true)}
                className="mt-4 min-h-[40px] rounded-md border border-red-300 px-3 text-sm font-medium text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-900/20"
              >
                Delete account…
              </button>
            </>
          ) : (
            <div className="mt-2 space-y-3">
              <p className="text-sm text-gray-700 dark:text-gray-300">
                This deletes {DESTROYED}. It cannot be undone, and your history
                cannot be recovered afterwards.
              </p>
              <label className="block text-sm">
                <span className="text-gray-600 dark:text-gray-400">
                  Type <span className="font-medium break-all">{account.email}</span> to confirm
                </span>
                <input
                  type="email"
                  autoComplete="off"
                  value={typed}
                  onChange={e => setTyped(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void deleteAccount()}
                  disabled={!matches || deleting}
                  className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-red-600 px-3 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
                >
                  {deleting && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                  Delete my account
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setConfirming(false);
                    setTyped('');
                    setError('');
                  }}
                  disabled={deleting}
                  className="min-h-[40px] rounded-md border border-gray-300 px-3 text-sm font-medium hover:bg-gray-50 dark:border-gray-600 dark:hover:bg-gray-700/40"
                >
                  Cancel
                </button>
              </div>
              {error && (
                <p role="alert" className="text-sm text-red-700 dark:text-red-400">
                  {error}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
