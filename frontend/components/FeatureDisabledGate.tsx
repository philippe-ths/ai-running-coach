'use client';

import { ReactNode } from 'react';

// Greys out a coach input whose backend feature flag is off (#522). When `disabled`,
// the children are dimmed, desaturated, and made non-interactive, with a short note
// explaining why. When enabled, the children render untouched. Works from both a
// client parent (with useCoachFeatureFlags) and a server parent (passing a
// server-fetched boolean).
export default function FeatureDisabledGate({
  disabled,
  note = 'Turned off in the coach configuration.',
  children,
}: {
  disabled: boolean;
  note?: string;
  children: ReactNode;
}) {
  if (!disabled) return <>{children}</>;
  return (
    <div className="relative">
      <div
        className="pointer-events-none select-none opacity-40 grayscale"
        aria-disabled="true"
      >
        {children}
      </div>
      <p className="mt-2 text-xs italic text-gray-500 dark:text-gray-400">{note}</p>
    </div>
  );
}
