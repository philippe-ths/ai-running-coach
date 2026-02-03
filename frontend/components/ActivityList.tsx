import Link from 'next/link';
import { format } from 'date-fns';
import { ChevronRight } from 'lucide-react';

interface Activity {
  id: string;
  name: string;
  type: string;
  start_date: string;
  distance_m: number;
  moving_time_s: number;
  activity_class?: string; 
}

export default function ActivityList({ activities }: { activities: Activity[] }) {
  if (!activities || activities.length === 0) {
    return <div className="text-gray-500 italic">No recent activities found. Try syncing.</div>;
  }

  return (
    <div className="space-y-3">
      {activities.map((activity) => (
        <Link 
          key={activity.id} 
          href={`/activity/${activity.id}`}
          className="block bg-white border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow"
        >
          <div className="flex justify-between items-center">
            <div>
              <h3 className="font-semibold text-lg text-gray-900">{activity.name}</h3>
              <div className="text-sm text-gray-500 flex gap-3 mt-1">
                <span>{format(new Date(activity.start_date), 'MMM d, yyyy')}</span>
                <span>•</span>
                <span>{(activity.distance_m / 1000).toFixed(2)} km</span>
                <span>•</span>
                <span>{Math.floor(activity.moving_time_s / 60)} min</span>
              </div>
            </div>
            <ChevronRight className="text-gray-400" />
          </div>
        </Link>
      ))}
    </div>
  );
}
