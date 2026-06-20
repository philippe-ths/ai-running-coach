'use client';

import { useMemo } from 'react';
import { Map as MapIcon } from 'lucide-react';
import { ActivityStream } from '@/lib/types';

interface RoutePathProps {
  streams?: ActivityStream[];
}

// Internal SVG units for the longer axis. Coordinates are projected into a box
// whose larger span maps to this many units; the shorter span keeps its true
// proportion so the route is never stretched.
const PROJECTION_SIZE = 1000;
// Padding inside the viewBox so the stroke is not clipped at the bounding edges.
const PADDING = 12;
// Cap on rendered points. A long run can carry thousands of latlng samples;
// the route shape survives light downsampling and the DOM stays cheap (cf. #368).
const MAX_POINTS = 2000;

interface ProjectedRoute {
  d: string;
  width: number;
  height: number;
}

/**
 * Project a [lat, lng][] track into an SVG path that preserves the route's true
 * proportions. Longitude degrees are scaled by cos(meanLat) so east-west
 * distance is not exaggerated, and the Y axis is flipped because latitude
 * increases northward while SVG y increases downward. Returns null when the
 * track has no usable span (absent, too few points, or a single location).
 */
export function projectRoute(latlng: unknown): ProjectedRoute | null {
  if (!Array.isArray(latlng)) return null;

  // Keep only well-formed [lat, lng] pairs of finite numbers.
  const points: [number, number][] = [];
  for (const p of latlng) {
    if (
      Array.isArray(p) &&
      p.length >= 2 &&
      Number.isFinite(p[0]) &&
      Number.isFinite(p[1])
    ) {
      points.push([p[0], p[1]]);
    }
  }
  if (points.length < 2) return null;

  // Downsample evenly when the track is dense, always keeping the last point.
  const step = Math.ceil(points.length / MAX_POINTS);
  const sampled = step > 1 ? points.filter((_, i) => i % step === 0) : points;
  if (sampled[sampled.length - 1] !== points[points.length - 1]) {
    sampled.push(points[points.length - 1]);
  }

  let minLat = Infinity;
  let maxLat = -Infinity;
  let sumLat = 0;
  for (const [lat] of sampled) {
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
    sumLat += lat;
  }
  const meanLatRad = (sumLat / sampled.length) * (Math.PI / 180);
  const lngScale = Math.cos(meanLatRad);

  // Project to planar coords: x corrected for longitude convergence, y = lat.
  const xs = sampled.map(([, lng]) => lng * lngScale);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const spanX = maxX - minX;
  const spanY = maxLat - minLat;

  // Degenerate track (a single location, or no movement): nothing to draw.
  if (spanX <= 0 && spanY <= 0) return null;

  const scale = PROJECTION_SIZE / Math.max(spanX, spanY);
  const width = spanX * scale;
  const height = spanY * scale;

  const commands = sampled.map(([lat], i) => {
    const x = (xs[i] - minX) * scale + PADDING;
    const y = (maxLat - lat) * scale + PADDING; // flip Y
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)},${y.toFixed(1)}`;
  });

  return {
    d: commands.join(' '),
    width: width + PADDING * 2,
    height: height + PADDING * 2,
  };
}

export default function RoutePath({ streams }: RoutePathProps) {
  const route = useMemo(() => {
    const latlng = streams?.find((s) => s.stream_type === 'latlng')?.data;
    return projectRoute(latlng);
  }, [streams]);

  // No GPS track (indoor ride, weights, treadmill) or a degenerate one: render
  // nothing rather than an empty box.
  if (!route) return null;

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <h4 className="font-semibold text-sm flex items-center gap-2 text-gray-700 dark:text-gray-300 mb-3">
        <MapIcon size={16} />
        Route
      </h4>
      <svg
        viewBox={`0 0 ${route.width.toFixed(1)} ${route.height.toFixed(1)}`}
        preserveAspectRatio="xMidYMid meet"
        className="w-full h-56 text-blue-500 dark:text-blue-400"
        role="img"
        aria-label="Recorded route path"
      >
        <path
          d={route.d}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}
