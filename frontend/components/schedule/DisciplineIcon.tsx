// #1087: a chip names its sport with an icon, so nothing needs decoding and no
// two sports share a mark (a letter "S" read as strength or swimming).
// lucide has no runner, so the run is drawn here in lucide's own style.

import { Activity, Bike, Dumbbell, Footprints, Waves } from "lucide-react";
import type { Discipline } from "@/lib/types/schedule";

function Runner({ size, className }: { size: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <circle cx="15" cy="4" r="2" />
      <path d="M13 8l-2 6 4 3v5" />
      <path d="M11 14l-2 3H5" />
      <path d="M7 10l3-2h3l2 3 3 1" />
    </svg>
  );
}

const ICONS = { walk: Footprints, bike: Bike, strength: Dumbbell, row: Waves, other: Activity };

export default function DisciplineIcon({
  discipline,
  size = 12,
  className,
}: {
  discipline: Discipline;
  size?: number;
  className?: string;
}) {
  if (discipline === "run") return <Runner size={size} className={className} />;
  const Icon = ICONS[discipline];
  return <Icon size={size} strokeWidth={2.25} className={className} aria-hidden="true" />;
}
