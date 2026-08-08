/**
 * Voice — the runner-declared coach voice (ADR 0012/0013, reshaped by #822).
 *
 * The runner picks a character, nudges five 1-5 dials, and may add a free-text
 * escape-hatch. The catalog (axes + presets) comes from the backend so the picker
 * and radar render from one source of truth.
 *
 * Dials are keyed BY AXIS rather than one field per axis: the axis set is data on
 * the server, and a rename there should not ripple through these types, the panel
 * and every test. It rippled through all three when Directness became Force.
 */

export interface VoiceDials {
  preset: string | null;
  warmth: number | null;
  humor: number | null;
  force: number | null;
  energy: number | null;
  length: number | null;
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
  dials: Record<string, number>;
}

export interface VoiceCatalog {
  axes: VoiceAxisInfo[];
  presets: VoicePresetInfo[];
  defaults: Record<string, number>;
}

export interface VoiceConfig {
  current: VoiceDials;
  catalog: VoiceCatalog;
}

export type DialKey = "warmth" | "humor" | "force" | "energy" | "length";
