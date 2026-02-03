import { fetchFromAPI } from '@/lib/api';
import ActivityList from '@/components/ActivityList';
import ConnectStravaButton from '@/components/ConnectStravaButton';
import SyncButton from '@/components/SyncButton';
import Link from 'next/link';

// Force dynamic since we fetch user data (no static cache for dashboard)
export const dynamic = 'force-dynamic';

export default async function Dashboard() {
  let activities = [];
  try {
    activities = await fetchFromAPI('/api/activities?limit=10') || [];
  } catch (e) {
    console.error("Failed to fetch activities", e);
  }

  // Calculate stats from fetched activities
  // In a real app, this should come from a dedicated /api/stats endpoint
  // to aggregate correctly over date ranges (e.g. this week vs last week).
  // For MVP Step 9/11, we aggregate locally on the clientside from recent list.
  
  const totalDistance = activities.reduce((sum: number, act: any) => sum + (act.distance_m || 0), 0);
  const totalTime = activities.reduce((sum: number, act: any) => sum + (act.moving_time_s || 0), 0);
  const hardDays = activities.filter((act: any) => {
      // Very crude "hard day" check if HR > 150 or label contains Hard/Tempo
      // Ideally check derived metric "activity_class"
      return (act.avg_hr && act.avg_hr > 155) || (act.name && act.name.match(/Tempo|Interval|Race/i));
  }).length;

  return (
    <div className="space-y-8">
      <header className="flex justify-between items-start">
        <div>
            <h1 className="text-3xl font-bold text-gray-900">Weekly Summary</h1>
            <p className="text-gray-600 mt-1">Here is what is happening this week.</p>
        </div>
        <div className="flex gap-3">
            <SyncButton />
            <Link href="/profile" className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50">
                Edit Profile
            </Link>
            <ConnectStravaButton />
        </div>
      </header>

      {/* Week Stats Placeholder */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white p-4 rounded-lg border shadow-sm">
            <div className="text-sm text-gray-500">Distance</div>
            <div className="text-2xl font-bold">{(totalDistance / 1000).toFixed(1)} km</div>
        </div>
        <div className="bg-white p-4 rounded-lg border shadow-sm">
            <div className="text-sm text-gray-500">Time</div>
            <div className="text-2xl font-bold">{(totalTime / 3600).toFixed(1)} h</div>
        </div>
        <div className="bg-white p-4 rounded-lg border shadow-sm">
            <div className="text-sm text-gray-500">Hard Days</div>
            <div className="text-2xl font-bold">{hardDays}</div>
        </div>
        <div className="bg-white p-4 rounded-lg border shadow-sm">
            <div className="text-sm text-gray-500">Load Trend</div>
            <div className="text-2xl font-bold">--%</div>
        </div>
      </div>

      <section>
        <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-bold text-gray-800">Recent Activities</h2>
            {/* Sync trigger could go here */}
        </div>
        <ActivityList activities={activities} />
      </section>
    </div>
  );
}
