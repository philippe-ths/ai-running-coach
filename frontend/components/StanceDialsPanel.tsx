'use client';

import { useEffect, useMemo, useState } from 'react';
import { fetchFromAPI } from '@/lib/api';
import type { EmphasisKey, StanceCatalog, StanceConfig } from '@/lib/types';

// The two emphasis axes in canonical order: x = data_sentiment, y = process_outcome.
const EMPHASIS_KEYS: EmphasisKey[] = ['data_sentiment', 'process_outcome'];

type EmphasisState = Record<EmphasisKey, number>;

function emphasisFromCatalogDefault(catalog: StanceCatalog): EmphasisState {
  return {
    data_sentiment: catalog.default_data_sentiment,
    process_outcome: catalog.default_process_outcome,
  };
}

export default function StanceDialsPanel() {
  const [catalog, setCatalog] = useState<StanceCatalog | null>(null);
  const [emphasis, setEmphasis] = useState<EmphasisState | null>(null);
  const [school, setSchool] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    fetchFromAPI('/api/coach/stance')
      .then((data: StanceConfig | null) => {
        if (!data) {
          setLoading(false);
          return;
        }
        const cat = data.catalog;
        const cur = data.current;
        setCatalog(cat);
        setSchool(cur.school);
        setEmphasis({
          data_sentiment: cur.data_sentiment ?? cat.default_data_sentiment,
          process_outcome: cur.process_outcome ?? cat.default_process_outcome,
        });
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  const nudge = (key: EmphasisKey, value: number) => {
    setEmphasis((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const resetToDefault = () => {
    if (!catalog) return;
    setSchool(null);
    setEmphasis(emphasisFromCatalogDefault(catalog));
  };

  const axisByKey = useMemo(() => {
    const map: Record<string, { low_pole: string; high_pole: string }> = {};
    catalog?.axes.forEach((a) => {
      map[a.key] = { low_pole: a.low_pole, high_pole: a.high_pole };
    });
    return map;
  }, [catalog]);

  const save = async () => {
    if (!emphasis) return;
    setSaving(true);
    setStatus(null);
    try {
      await fetchFromAPI('/api/coach/stance', {
        method: 'PUT',
        body: JSON.stringify({
          school,
          data_sentiment: emphasis.data_sentiment,
          process_outcome: emphasis.process_outcome,
        }),
      });
      setStatus('Stance saved.');
    } catch (err) {
      console.error(err);
      setStatus('Could not save stance.');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="text-sm text-gray-500 dark:text-gray-400">Loading stance…</div>;
  if (!catalog || !emphasis) return null;

  // The quadrant mirror: x = data_sentiment (1 Data → 5 Sentiment), y = process_outcome
  // (1 Process → 5 Outcome). Map 1-5 to 0-100%; invert y so Outcome (5) sits at the top.
  const dotX = ((emphasis.data_sentiment - 1) / 4) * 100;
  const dotY = (1 - (emphasis.process_outcome - 1) / 4) * 100;
  const xAxis = axisByKey['data_sentiment'];
  const yAxis = axisByKey['process_outcome'];

  return (
    <section className="space-y-5 bg-white dark:bg-gray-800 p-6 border dark:border-gray-700 rounded-xl shadow-sm">
      <div>
        <h2 className="text-xl font-bold">Coaching Stance</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          The training philosophy your coach reasons from, and what it puts first. This
          changes emphasis and framing only — never the facts, the numbers, your real
          goal, or any safety guidance.
        </p>
      </div>

      {/* School picker */}
      <div>
        <label className="block text-sm font-medium mb-2">Training philosophy</label>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {catalog.schools.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setSchool(s.key)}
              className={`text-left p-3 rounded border transition-colors ${
                school === s.key
                  ? 'border-blue-600 bg-blue-50 dark:bg-blue-950 dark:border-blue-400'
                  : 'border-gray-200 dark:border-gray-600 hover:border-blue-400'
              }`}
            >
              <div className="font-semibold text-sm">{s.name}</div>
              <div className="text-xs text-gray-500 dark:text-gray-400">{s.stance}</div>
            </button>
          ))}
        </div>
        {school === null && (
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
            No philosophy chosen — your coach uses the house default (aerobic base). Pick
            one above to steer how it frames your training.
          </p>
        )}
      </div>

      {/* Emphasis quadrant mirror + sliders */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-center">
        <div aria-hidden="true">
          <div className="relative w-full aspect-square border dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-900">
            {/* centre cross-hairs */}
            <div className="absolute left-1/2 top-0 bottom-0 border-l border-dashed border-gray-200 dark:border-gray-700" />
            <div className="absolute top-1/2 left-0 right-0 border-t border-dashed border-gray-200 dark:border-gray-700" />
            {/* pole labels */}
            <span className="absolute top-1 left-1/2 -translate-x-1/2 text-[10px] text-gray-400">
              {yAxis?.high_pole}
            </span>
            <span className="absolute bottom-1 left-1/2 -translate-x-1/2 text-[10px] text-gray-400">
              {yAxis?.low_pole}
            </span>
            <span className="absolute left-1 top-1/2 -translate-y-1/2 text-[10px] text-gray-400">
              {xAxis?.low_pole}
            </span>
            <span className="absolute right-1 top-1/2 -translate-y-1/2 text-[10px] text-gray-400">
              {xAxis?.high_pole}
            </span>
            {/* the runner's point */}
            <div
              className="absolute w-3 h-3 rounded-full bg-blue-600 -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${dotX}%`, top: `${dotY}%` }}
            />
          </div>
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">
            An expressive mirror of where your emphasis sits, not a precise readout.
          </p>
        </div>

        <div className="space-y-4">
          {EMPHASIS_KEYS.map((key) => {
            const axis = axisByKey[key];
            return (
              <div key={key}>
                <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                  <span>{axis?.low_pole}</span>
                  <span className="font-medium text-gray-700 dark:text-gray-200">{emphasis[key]}</span>
                  <span>{axis?.high_pole}</span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={5}
                  step={1}
                  value={emphasis[key]}
                  onChange={(e) => nudge(key, Number(e.target.value))}
                  className="w-full"
                  aria-label={`${key} emphasis`}
                />
              </div>
            );
          })}
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="bg-blue-600 text-white py-2 px-4 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save Stance'}
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
