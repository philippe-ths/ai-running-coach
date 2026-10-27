'use client';

// #1145: "Today" / "Yesterday" are relative to the runner's own date, which only
// their device knows. On the home page the list renders on the server (UTC), so
// the first paint shows the plain date and the browser relabels it after mount.
// Both renders agree on the first paint, so there is no hydration mismatch.

import { useEffect, useState } from 'react';
import { dayHeading } from '@/lib/activityGrouping';

export default function DayHeading({ dayKey }: { dayKey: string }) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => setNow(new Date()), []);

  const [y, m, d] = dayKey.split('-').map(Number);
  return <>{dayHeading(new Date(y, m - 1, d), now)}</>;
}
