'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from 'recharts';
import { fetchFromAPI } from '@/lib/api';
import type { DialKey, VoiceCatalog, VoiceConfig } from '@/lib/types';

// The four dials in canonical order. The radar renders them at 12/3/6/9 o'clock
// (a deliberate build choice — axis order changes the shape; ADR 0013 / brief #5):
// data order [warmth, humor, directness, energy] places warmth at top (12) and the
// rest clockwise.
const DIAL_KEYS: DialKey[] = ['warmth', 'humor', 'directness', 'energy'];

type DialState = Record<DialKey, number>;

function dialsFromCatalogDefault(catalog: VoiceCatalog): DialState {
  return {
    warmth: catalog.default_warmth,
    humor: catalog.default_humor,
    directness: catalog.default_directness,
    energy: catalog.default_energy,
  };
}

export default function VoiceDialsPanel() {
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null);
  const [dials, setDials] = useState<DialState | null>(null);
  const [preset, setPreset] = useState<string | null>(null);
  const [freetext, setFreetext] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    fetchFromAPI('/api/coach/voice')
      .then((data: VoiceConfig | null) => {
        if (!data) {
          setLoading(false);
          return;
        }
        const cat = data.catalog;
        const cur = data.current;
        setCatalog(cat);
        setPreset(cur.preset);
        setFreetext(cur.freetext ?? '');
        setDials({
          warmth: cur.warmth ?? cat.default_warmth,
          humor: cur.humor ?? cat.default_humor,
          directness: cur.directness ?? cat.default_directness,
          energy: cur.energy ?? cat.default_energy,
        });
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  // Picking a preset sets all four dials to its DNA; the runner can then nudge.
  // The preset stays selected so the coach keeps injecting its example messages
  // (coupling decision: examples come from the stored preset, nudging changes the
  // dial numbers only).
  const choosePreset = (p: VoiceCatalog['presets'][number]) => {
    setPreset(p.key);
    setDials({ warmth: p.warmth, humor: p.humor, directness: p.directness, energy: p.energy });
  };

  const nudge = (key: DialKey, value: number) => {
    setDials((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const resetToDefault = () => {
    if (!catalog) return;
    setPreset(null);
    setFreetext('');
    setDials(dialsFromCatalogDefault(catalog));
  };

  const radarData = useMemo(() => {
    if (!catalog || !dials) return [];
    return DIAL_KEYS.map((key) => {
      const axis = catalog.axes.find((a) => a.key === key);
      return {
        axis: key.charAt(0).toUpperCase() + key.slice(1),
        poles: axis ? `${axis.low_pole} - ${axis.high_pole}` : key,
        value: dials[key],
      };
    });
  }, [catalog, dials]);

  const save = async () => {
    if (!dials) return;
    setSaving(true);
    setStatus(null);
    try {
      await fetchFromAPI('/api/coach/voice', {
        method: 'PUT',
        body: JSON.stringify({
          preset,
          warmth: dials.warmth,
          humor: dials.humor,
          directness: dials.directness,
          energy: dials.energy,
          freetext: freetext.trim() ? freetext.trim() : null,
        }),
      });
      setStatus('Voice saved.');
    } catch (err) {
      console.error(err);
      setStatus('Could not save voice.');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="text-sm text-gray-500 dark:text-gray-400">Loading voice…</div>;
  if (!catalog || !dials) return null;

  return (
    <section className="space-y-5 bg-white dark:bg-gray-800 p-6 border dark:border-gray-700 rounded-xl shadow-sm">
      <div>
        <h2 className="text-xl font-bold">Coach Voice</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          How your coach talks to you. Pick a starting character, then nudge the dials.
          This changes the delivery only — never the facts, the numbers, or any safety
          guidance.
        </p>
      </div>

      {/* Preset cast */}
      <div>
        <label className="block text-sm font-medium mb-2">Starting character</label>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {catalog.presets.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => choosePreset(p)}
              className={`text-left p-3 rounded border transition-colors ${
                preset === p.key
                  ? 'border-blue-600 bg-blue-50 dark:bg-blue-950 dark:border-blue-400'
                  : 'border-gray-200 dark:border-gray-600 hover:border-blue-400'
              }`}
            >
              <div className="font-semibold text-sm">{p.name}</div>
              <div className="text-xs text-gray-500 dark:text-gray-400">{p.flavour}</div>
            </button>
          ))}
        </div>
        {preset === null && (
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
            Custom dials (no preset). Pick a character above to also borrow its way of
            phrasing things.
          </p>
        )}
      </div>

      {/* Radar mirror + dials */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-center">
        <div className="h-56" aria-hidden="true">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={radarData} outerRadius="75%">
              <PolarGrid />
              <PolarAngleAxis dataKey="axis" tick={{ fontSize: 12 }} />
              <PolarRadiusAxis domain={[1, 5]} tick={false} axisLine={false} />
              <Radar dataKey="value" stroke="#2563eb" fill="#2563eb" fillOpacity={0.35} />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        <div className="space-y-4">
          {DIAL_KEYS.map((key) => {
            const axis = catalog.axes.find((a) => a.key === key);
            return (
              <div key={key}>
                <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                  <span>{axis?.low_pole}</span>
                  <span className="font-medium text-gray-700 dark:text-gray-200">
                    {key.charAt(0).toUpperCase() + key.slice(1)}: {dials[key]}
                  </span>
                  <span>{axis?.high_pole}</span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={5}
                  step={1}
                  value={dials[key]}
                  onChange={(e) => nudge(key, Number(e.target.value))}
                  className="w-full"
                  aria-label={`${key} dial`}
                />
              </div>
            );
          })}
        </div>
      </div>

      <p className="text-xs text-gray-400 dark:text-gray-500 -mt-2">
        The radar is an expressive mirror of your dials, not a precise readout.
      </p>

      {/* Free-text escape-hatch */}
      <div>
        <label className="block text-sm font-medium mb-1">In your own words (optional)</label>
        <textarea
          value={freetext}
          onChange={(e) => setFreetext(e.target.value)}
          rows={2}
          maxLength={1000}
          placeholder="e.g. talk to me like a calm, dry mentor; no hype."
          className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
        />
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          Your coach reads this only to shape its tone. It can never change your data or
          skip a safety message.
        </p>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="bg-blue-600 text-white py-2 px-4 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save Voice'}
        </button>
        <button
          type="button"
          onClick={resetToDefault}
          className="text-sm text-gray-600 dark:text-gray-300 hover:underline"
        >
          Reset to default
        </button>
        {status && <span className="text-sm text-gray-500 dark:text-gray-400">{status}</span>}
      </div>
    </section>
  );
}
