/**
 * P1.1 Voice — the runner-declared coach voice (ADR 0012/0013).
 *
 * The runner picks a preset, nudges four 1-5 dials, and may add a free-text
 * escape-hatch. The catalog (axes + presets) comes from the backend so the picker
 * and radar render from one source of truth.
 */

export interface VoiceDials {
  preset: string | null;
  warmth: number | null;
  humor: number | null;
  directness: number | null;
  energy: number | null;
  freetext: string | null;
}

export interface VoiceAxisInfo {
  key: string;
  low_pole: string;
  high_pole: string;
}

export interface VoicePresetInfo {
  key: string;
  name: string;
  flavour: string;
  warmth: number;
  humor: number;
  directness: number;
  energy: number;
}

export interface VoiceCatalog {
  axes: VoiceAxisInfo[];
  presets: VoicePresetInfo[];
  default_warmth: number;
  default_humor: number;
  default_directness: number;
  default_energy: number;
}

export interface VoiceConfig {
  current: VoiceDials;
  catalog: VoiceCatalog;
}

export type DialKey = "warmth" | "humor" | "directness" | "energy";
