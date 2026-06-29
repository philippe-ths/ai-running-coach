import Link from 'next/link';
import { format } from 'date-fns';
import { ChevronRight } from 'lucide-react';

import { activityStartDate, hasMeaningfulDistance } from '@/lib/format';

interface Activity {
  id: string;
  name: string;
  type: string;
  start_date: string;
  start_date_local?: string | null;
  distance_m: number;
  moving_time_s: number;
  headline?: string | null;
}

export default function ActivityList({ activities }: { activities: Activity[] }) {
  if (!activities || activities.length === 0) {
    return <div className="text-gray-500 dark:text-gray-400 italic">No recent activities found. Try syncing.</div>;
  }

  return (
    <div className="space-y-3">
      {activities.map((activity) => (
        <Link 
          key={activity.id} 
          href={`/activity/${activity.id}`}
          className="block bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 hover:shadow-md transition-shadow"
        >
          <div className="flex justify-between items-center gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 min-w-0">
                <h3 className="font-semibold text-lg text-gray-900 dark:text-gray-100 truncate">{activity.name}</h3>
                {activity.headline && (
                  <span className="inline-block px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-200 text-xs font-medium shrink-0">
                    {activity.headline}
                  </span>
                )}
              </div>
              <div className="text-sm text-gray-500 dark:text-gray-400 flex flex-wrap gap-x-3 gap-y-1 mt-1">
                <span>{format(activityStartDate(activity), 'MMM d, yyyy')}</span>
                {hasMeaningfulDistance(activity.distance_m) && (
                  <>
                    <span aria-hidden="true">•</span>
                    <span>{(activity.distance_m / 1000).toFixed(2)} km</span>
                  </>
                )}
                <span aria-hidden="true">•</span>
                <span>{Math.floor(activity.moving_time_s / 60)} min</span>
              </div>
            </div>
            <ChevronRight className="text-gray-400 dark:text-gray-500 shrink-0" />
          </div>
        </Link>
      ))}
    </div>
  );
}
