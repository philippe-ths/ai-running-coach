// #1087, #1089: a chip names its sport with an icon, so nothing needs decoding
// and no two sports share a mark. The sport is the session's `activity_type`
// (Strava's names, set by the coach) or, once done, the type actually recorded;
// without one, the discipline. Sports with no icon of their own share a family
// icon, and anything unknown gets the generic one, so a new sport never breaks.
// lucide has no runner or oars, so those two are drawn here in lucide's style.

import type { ComponentType } from "react";
import {
  Activity,
  Bike,
  Dribbble,
  Dumbbell,
  Flag,
  Footprints,
  HeartPulse,
  Mountain,
  MountainSnow,
  PersonStanding,
  Sailboat,
  Snowflake,
  Waves,
} from "lucide-react";
import type { Discipline } from "@/lib/types/schedule";

type IconProps = { size?: number | string; strokeWidth?: number | string; className?: string };

function drawn(paths: string[], circle?: [number, number, number]) {
  function Drawn({ size = 12, strokeWidth = 2, className }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        aria-hidden="true"
      >
        {circle && <circle cx={circle[0]} cy={circle[1]} r={circle[2]} />}
        {paths.map((d) => (
          <path key={d} d={d} />
        ))}
      </svg>
    );
  }
  return Drawn;
}

const Runner = drawn(["M13 8l-2 6 4 3v5", "M11 14l-2 3H5", "M7 10l3-2h3l2 3 3 1"], [15, 4, 2]);
const Oars = drawn(["M4 20L16 8", "M20 20L8 8", "M16 8l2-4 2 2-4 2", "M8 8L6 4 4 6l4 2"]);

interface Sport {
  key: string;
  label: string;
  Icon: ComponentType<IconProps>;
}

const RUN: Sport = { key: "run", label: "Run", Icon: Runner };
const WALK: Sport = { key: "walk", label: "Walk", Icon: Footprints };
const HIKE: Sport = { key: "hike", label: "Hike", Icon: Mountain };
const BIKE: Sport = { key: "bike", label: "Bike", Icon: Bike };
const STRENGTH: Sport = { key: "strength", label: "Strength", Icon: Dumbbell };
const ROW: Sport = { key: "row", label: "Row or paddle", Icon: Oars };
const SWIM: Sport = { key: "swim", label: "Swim", Icon: Waves };
const SAIL: Sport = { key: "sail", label: "Sail or surf", Icon: Sailboat };
const SNOW: Sport = { key: "snow", label: "Snow", Icon: MountainSnow };
const ICE: Sport = { key: "ice", label: "Skate", Icon: Snowflake };
const MIND: Sport = { key: "mind", label: "Yoga or Pilates", Icon: PersonStanding };
const CARDIO: Sport = { key: "cardio", label: "Cardio", Icon: HeartPulse };
const BALL: Sport = { key: "ball", label: "Ball sport", Icon: Dribbble };
const GOLF: Sport = { key: "golf", label: "Golf", Icon: Flag };
const OTHER: Sport = { key: "other", label: "Other", Icon: Activity };

// Strava's sport names, lower-cased.
const BY_TYPE: Record<string, Sport> = {
  run: RUN, trailrun: RUN, virtualrun: RUN,
  walk: WALK, hike: HIKE, rockclimbing: HIKE, snowshoe: HIKE,
  ride: BIKE, virtualride: BIKE, ebikeride: BIKE, mountainbikeride: BIKE,
  emountainbikeride: BIKE, gravelride: BIKE, handcycle: BIKE, velomobile: BIKE,
  weighttraining: STRENGTH, crossfit: STRENGTH,
  rowing: ROW, virtualrow: ROW, kayaking: ROW, canoeing: ROW, standuppaddling: ROW,
  swim: SWIM,
  sail: SAIL, surfing: SAIL, windsurf: SAIL, kitesurf: SAIL,
  alpineski: SNOW, backcountryski: SNOW, nordicski: SNOW, snowboard: SNOW, rollerski: SNOW,
  iceskate: ICE, inlineskate: ICE, skateboard: ICE,
  yoga: MIND, pilates: MIND,
  workout: CARDIO, elliptical: CARDIO, stairstepper: CARDIO, highintensityintervaltraining: CARDIO,
  soccer: BALL, tennis: BALL, badminton: BALL, pickleball: BALL, racquetball: BALL,
  squash: BALL, tabletennis: BALL,
  golf: GOLF,
};

const BY_DISCIPLINE: Record<Discipline, Sport> = {
  run: RUN, walk: WALK, bike: BIKE, strength: STRENGTH, row: ROW, other: OTHER,
};

/** The sport to show: the named type when known, else the discipline's. */
export function sportOf(activityType: string | null | undefined, discipline: Discipline): Sport {
  const named = activityType ? BY_TYPE[activityType.toLowerCase()] : undefined;
  return named ?? BY_DISCIPLINE[discipline] ?? OTHER;
}

/** The sport by its own name when the coach gave one ("Trail run", "Yoga"),
 * else the family's. The legend names families, since they share an icon. */
export function sportName(activityType: string | null | undefined, discipline: Discipline): string {
  if (activityType && BY_TYPE[activityType.toLowerCase()]) {
    const words = activityType.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  return sportOf(activityType, discipline).label;
}

export default function SportIcon({
  activityType,
  discipline,
  size = 12,
  className,
}: {
  activityType?: string | null;
  discipline: Discipline;
  size?: number;
  className?: string;
}) {
  const { Icon } = sportOf(activityType, discipline);
  return <Icon size={size} strokeWidth={2.25} className={className} aria-hidden="true" />;
}
