'use client';

import { useEffect } from 'react';
import { AlertTriangle } from 'lucide-react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto max-w-2xl p-8">
      <div
        role="alert"
        className="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-900/30 dark:text-red-200"
      >
        <AlertTriangle size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
        <span>Something went wrong loading this page. Your data is untouched.</span>
      </div>
      <button
        onClick={reset}
        className="mt-4 min-h-[44px] rounded-md bg-blue-600 px-4 text-sm font-medium text-white hover:bg-blue-700"
      >
        Try again
      </button>
    </div>
  );
}
