/**
 * P4 User materials (#287) — the runner's uploaded coaching materials.
 *
 * A runner uploads a markdown file (their training philosophy, a coach's plan, a
 * physio protocol, a race plan), the backend distills it into a compact
 * corpus-shaped record, and the coach reasons over it once the v7 prompt is live
 * (#288). The raw text is deliberately never returned by the API — only the
 * distilled record is (`UserMaterialRead` in `backend/app/schemas/material.py`).
 */

export type MaterialKind =
  | "philosophy"
  | "plan"
  | "protocol"
  | "race_plan"
  | "other";

export type MaterialStatus = "processing" | "active" | "failed" | "archived";

export interface DistilledMaterial {
  stance: string;
  principles: string[];
  method_framing: string;
  emphasis_hints: string[];
}

export interface UserMaterial {
  id: string;
  // `kind`/`status` are plain strings on the wire (the backend serialises the
  // enums to str); the unions above are for authoring values and exhaustive
  // rendering, so an unknown value still renders rather than failing to type.
  kind: string;
  title: string;
  filename: string;
  status: string;
  distilled: DistilledMaterial | null;
  distill_model: string | null;
  created_at: string | null;
  distilled_at: string | null;
}
