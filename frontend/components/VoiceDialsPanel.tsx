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

// The balanced centre of a 1-5 axis. Default sits here because Default means no
// lean in any direction; it is a display position, never a saved value.
const MIDDLE = 3;

// Keyed by axis, and the axis LIST comes from the catalog: the axis set is data on
// the server, so adding or renaming one should not need an edit here. The radar
// renders them in catalog order, starting at 12 o'clock and going clockwise.
type DialState = Record<string, number>;

function middleDials(catalog: VoiceCatalog): DialState {
  return Object.fromEntries(catalog.axes.map((a) => [a.key, MIDDLE]));
}

function titleCase(key: string): string {
  return key.charAt(0).toUpperCase() + key.slice(1);
}

export default function VoiceDialsPanel() {
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null);
  const [dials, setDials] = useState<DialState | null>(null);
  const [preset, setPreset] = useState<string | null>(null);
  const [isDefault, setIsDefault] = useState(false);
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
        // Default is the runner having declared NOTHING — every field null. It is a
        // real state, not "the dials happen to sit in the middle", because it is
        // what switches the voice pass off entirely.
        const declaredDials = cat.axes.filter(
          (a) => (cur as unknown as Record<string, unknown>)[a.key] != null,
        );
        const undeclared =
          cur.preset === null && declaredDials.length === 0 && !cur.freetext;
        setIsDefault(undeclared);
        if (undeclared) {
          setDials(middleDials(cat));
          setLoading(false);
          return;
        }
        setDials(
          Object.fromEntries(
            cat.axes.map((a) => [
              a.key,
              (cur as unknown as Record<string, number | null>)[a.key] ?? cat.defaults[a.key],
            ]),
          ),
        );
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
    setIsDefault(false);
    setDials({ ...p.dials });
  };

  const nudge = (key: string, value: number) => {
    setIsDefault(false);
    setDials((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const chooseDefault = () => {
    if (!catalog) return;
    setIsDefault(true);
    setPreset(null);
    setFreetext('');
    // Default reads as the balanced middle of every axis, because that is what it
    // means: no lean in any direction. The values are display only — Default saves
    // as absence, so nothing downstream reads them.
    setDials(middleDials(catalog));
  };

  // The dials the runner has moved away from their chosen character. Selecting a
  // character and nudging it does not make the runner anonymous — they are on that
  // character, adjusted — so the base is named and the change shown against it.
  const deltas = useMemo(() => {
    if (!catalog || !dials || preset === null) return [];
    const base = catalog.presets.find((p) => p.key === preset);
    if (!base) return [];
    return catalog.axes.flatMap((axis) => {
      const offset = dials[axis.key] - base.dials[axis.key];
      if (offset === 0) return [];
      const pole = offset > 0 ? axis.high_pole : axis.low_pole;
      return [
        `${titleCase(axis.key)} ${offset > 0 ? '+' : ''}${offset} (toward ${pole})`,
      ];
    });
  }, [catalog, dials, preset]);

  const baseName = catalog?.presets.find((p) => p.key === preset)?.name ?? null;

  // Custom is a SELECTION, not an annotation on another one: the runner who nudged
  // The Drill Sergeant is on Custom, with Drill Sergeant as its base. Exactly one
  // tile is lit at a time, so what is selected is never ambiguous.
  const customised =
    !isDefault && (deltas.length > 0 || freetext.trim().length > 0 || preset === null);

  const radarData = useMemo(() => {
    if (!catalog || !dials) return [];
    return catalog.axes.map((axis) => ({
      axis: titleCase(axis.key),
      poles: `${axis.low_pole} - ${axis.high_pole}`,
      value: dials[axis.key],
    }));
  }, [catalog, dials]);

  const save = async () => {
    if (!dials) return;
    setSaving(true);
    setStatus(null);
    try {
      await fetchFromAPI('/api/coach/voice', {
        method: 'PUT',
        // Default persists as genuine ABSENCE, not as dials that happen to sit in
        // the middle. Storing middle values would read as a declared voice and buy
        // the runner a rewrite pass that changes nothing they asked for.
        // Built from catalog.axes rather than named fields, so a renamed axis
        // cannot silently send `undefined` and a new one cannot be silently
        // dropped — neither of which a type-check would catch.
        body: JSON.stringify({
          preset: isDefault ? null : preset,
          freetext: isDefault || !freetext.trim() ? null : freetext.trim(),
          ...Object.fromEntries(
            catalog!.axes.map((a) => [a.key, isDefault ? null : dials[a.key]]),
          ),
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
          {/* Default is a first-class choice, not the absence of one: it is the
              only option that adds nothing at all to how your report is written. */}
          <button
            type="button"
            onClick={chooseDefault}
            className={`text-left p-3 rounded border transition-colors ${
              isDefault
                ? 'border-blue-600 bg-blue-50 dark:bg-blue-950 dark:border-blue-400'
                : 'border-gray-200 dark:border-gray-600 hover:border-blue-400'
            }`}
          >
            <div className="font-semibold text-sm">Default</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">
              No voice applied; your coach as written.
            </div>
          </button>
          {catalog.presets.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => choosePreset(p)}
              className={`text-left p-3 rounded border transition-colors ${
                !isDefault && !customised && preset === p.key
                  ? 'border-blue-600 bg-blue-50 dark:bg-blue-950 dark:border-blue-400'
                  : customised && preset === p.key
                    // The character a Custom voice departed FROM. Dashed rather than
                    // filled: it is not what is selected, but it is not unrelated
                    // either, and the link should be visible in the grid rather than
                    // only readable in Custom's description.
                    ? 'border-dashed border-blue-500 dark:border-blue-400'
                    : 'border-gray-200 dark:border-gray-600 hover:border-blue-400'
              }`}
            >
              <div className="font-semibold text-sm">{p.name}</div>
              <div className="text-xs text-gray-500 dark:text-gray-400">{p.flavour}</div>
              {customised && preset === p.key && (
                <div className="text-[0.65rem] uppercase tracking-wider text-blue-600 dark:text-blue-400 mt-1">
                  Base
                </div>
              )}
            </button>
          ))}

          {/* Custom: lit once the runner departs from a character, naming the base
              they departed from rather than dropping them into an anonymous state. */}
          <div
            aria-current={customised ? 'true' : undefined}
            className={`text-left p-3 rounded border transition-colors ${
              customised
                ? 'border-blue-600 bg-blue-50 dark:bg-blue-950 dark:border-blue-400'
                : 'border-dashed border-gray-200 dark:border-gray-600 opacity-60'
            }`}
          >
            <div className="font-semibold text-sm">Custom</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">
              {customised
                ? baseName
                  ? `base: ${baseName}${deltas.length > 0 ? ` · ${deltas.join(' · ')}` : ''}${
                      freetext.trim() ? ' · your own words' : ''
                    }`
                  : 'Your own dials, no base character.'
                : 'Nudge a dial or add your own words.'}
            </div>
          </div>
        </div>
        {isDefault && (
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
            Your report is written and sent exactly as it is today — no second pass,
            no rewriting.
          </p>
        )}
        {customised && !baseName && (
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
            Pick a character above to also borrow its way of phrasing things.
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
              {/* No entry animation: this is a static mirror of the dials, redrawn on
                  every nudge, so growing it from the centre each time buys nothing and
                  leaves the shape collapsed wherever animation frames do not run. */}
              <Radar
                dataKey="value"
                stroke="#2563eb"
                fill="#2563eb"
                fillOpacity={0.35}
                isAnimationActive={false}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        <div className="space-y-4">
          {catalog.axes.map((axis) => {
            const key = axis.key;
            return (
              <div key={key}>
                <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                  <span>{axis.low_pole}</span>
                  <span className="font-medium text-gray-700 dark:text-gray-200">
                    {titleCase(key)}: {dials[key]}
                  </span>
                  <span>{axis.high_pole}</span>
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
          onClick={chooseDefault}
          className="text-sm text-gray-600 dark:text-gray-300 hover:underline"
        >
          Reset to default
        </button>
        {status && <span className="text-sm text-gray-500 dark:text-gray-400">{status}</span>}
      </div>
    </section>
  );
}
