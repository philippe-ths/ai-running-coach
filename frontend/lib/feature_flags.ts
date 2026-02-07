import { CoachVerdictV3 } from "../lib/types";

export function isCoachVerdictV3(obj: any): obj is CoachVerdictV3 {
    return (
        obj &&
        typeof obj.inputs_used_line === "string" &&
        typeof obj.headline === "object" &&
        Array.isArray(obj.scorecard)
    );
}

export const FEATURE_FLAGS = {
    VERDICT_V3: process.env.NEXT_PUBLIC_VERDICT_V3 === "1",
    DEBUG_AI: process.env.NEXT_PUBLIC_DEBUG_AI === "1",
};
