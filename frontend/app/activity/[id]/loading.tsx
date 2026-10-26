export default function Loading() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading activity">
      <div className="h-4 w-40 animate-pulse rounded bg-gray-200 dark:bg-gray-700" />
      <div className="h-9 w-2/3 animate-pulse rounded bg-gray-200 dark:bg-gray-700" />
      <div className="h-24 animate-pulse rounded-xl bg-gray-200 dark:bg-gray-700" />
      <div className="h-64 animate-pulse rounded-xl bg-gray-200 dark:bg-gray-700" />
    </div>
  );
}
