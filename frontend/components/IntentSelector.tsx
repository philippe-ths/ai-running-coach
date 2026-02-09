'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';

const ACTIVITY_TYPES = [
  "Easy Run", "Recovery", "Long Run", 
  "Tempo", "Intervals", "Hills", "Race"
];

interface Props {
  activityId: string;
  currentType: string | null;
  assignedClass: string | undefined;
}

export default function IntentSelector({ activityId, currentType, assignedClass }: Props) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  const handleIntentChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newIntent = e.target.value;
    setLoading(true);

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'}/api/activities/${activityId}/intent`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ user_intent: newIntent }),
      });

      if (!res.ok) throw new Error("Failed to update intent");
      router.refresh(); // Refresh page to see new metrics
    } catch (err) {
      console.error(err);
      alert("Failed to update activity type");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <label className="text-sm font-medium text-gray-700">Type:</label>
      <select
        value={currentType || assignedClass || "Easy Run"}
        onChange={handleIntentChange}
        disabled={loading}
        className="block w-40 rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm p-1 border"
      >
        {ACTIVITY_TYPES.map(type => (
          <option key={type} value={type}>{type}</option>
        ))}
      </select>
      {loading && <span className="text-xs text-gray-400">Updating...</span>}
    </div>
  );
}
