'use client';

import { InputHTMLAttributes, ReactNode } from 'react';

// Form controls for the profile section screens (#941). Every one of these
// keeps the programmatic association the flat form had (#849, #873, #912): the
// choice controls are real radio inputs inside a fieldset whose legend names
// them, so a screen reader announces the group and the option together, and any
// hint is wired through aria-describedby on the fieldset rather than floating
// beside it.

export function FieldHint({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} className="text-xs text-gray-500 dark:text-gray-400">
      {children}
    </p>
  );
}

type Option = { value: string | number; label: string; note?: string };

// Radio group rendered as pill chips. Wraps to as many rows as it needs, so a
// long option set stays readable at phone width instead of truncating.
export function ChipGroup({
  legend,
  name,
  options,
  value,
  onChange,
  hintId,
}: {
  legend: string;
  name: string;
  options: Option[];
  value: string | number;
  onChange: (value: string) => void;
  hintId?: string;
}) {
  return (
    <fieldset aria-describedby={hintId}>
      <legend className="text-sm font-medium mb-2">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <label key={option.value} className="cursor-pointer">
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={(e) => onChange(e.target.value)}
              className="sr-only peer"
            />
            <span className="flex items-center min-h-[44px] px-4 rounded-full border border-gray-300 text-sm text-gray-700 transition-colors peer-checked:border-blue-600 peer-checked:bg-blue-50 peer-checked:font-semibold peer-checked:text-blue-700 peer-focus-visible:ring-2 peer-focus-visible:ring-blue-500 peer-focus-visible:ring-offset-2 dark:border-gray-600 dark:text-gray-200 dark:peer-checked:border-blue-500 dark:peer-checked:bg-blue-900/40 dark:peer-checked:text-blue-200 dark:peer-focus-visible:ring-offset-gray-800">
              {option.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

// Radio group rendered as one joined bar. For 2-3 mutually exclusive options
// where seeing them side by side IS the explanation (Monday vs Sunday).
export function SegmentedControl({
  legend,
  name,
  options,
  value,
  onChange,
  hintId,
}: {
  legend: string;
  name: string;
  options: Option[];
  value: string | number;
  onChange: (value: string) => void;
  hintId?: string;
}) {
  return (
    <fieldset aria-describedby={hintId}>
      <legend className="text-sm font-medium mb-2">{legend}</legend>
      <div className="flex rounded-lg border border-gray-300 overflow-hidden dark:border-gray-600">
        {options.map((option) => (
          <label
            key={option.value}
            className="flex-1 cursor-pointer border-r border-gray-300 last:border-r-0 dark:border-gray-600"
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={(e) => onChange(e.target.value)}
              className="sr-only peer"
            />
            <span className="flex items-center justify-center min-h-[44px] px-2 text-sm text-gray-700 transition-colors peer-checked:bg-blue-100 peer-checked:font-semibold peer-checked:text-blue-800 peer-focus-visible:ring-2 peer-focus-visible:ring-inset peer-focus-visible:ring-blue-500 dark:text-gray-200 dark:peer-checked:bg-blue-900/50 dark:peer-checked:text-blue-200">
              {option.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

// A 1..max radio row for a small integer count.
export function CountPicker({
  legend,
  name,
  max,
  value,
  onChange,
  hintId,
}: {
  legend: string;
  name: string;
  max: number;
  value: number;
  onChange: (value: string) => void;
  hintId?: string;
}) {
  return (
    <fieldset aria-describedby={hintId}>
      <legend className="text-sm font-medium mb-2">{legend}</legend>
      <div className="grid grid-cols-7 gap-1.5">
        {Array.from({ length: max }, (_, i) => i + 1).map((n) => (
          <label key={n} className="cursor-pointer">
            <input
              type="radio"
              name={name}
              value={n}
              checked={value === n}
              onChange={(e) => onChange(e.target.value)}
              className="sr-only peer"
            />
            <span className="flex items-center justify-center min-h-[44px] rounded-lg border border-gray-300 font-mono text-sm text-gray-700 transition-colors peer-checked:border-blue-600 peer-checked:bg-blue-50 peer-checked:font-semibold peer-checked:text-blue-700 peer-focus-visible:ring-2 peer-focus-visible:ring-blue-500 dark:border-gray-600 dark:text-gray-200 dark:peer-checked:border-blue-500 dark:peer-checked:bg-blue-900/40 dark:peer-checked:text-blue-200">
              {n}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

// Numeric input with its unit rendered beside it. The unit is aria-hidden
// because it is already in the visible label ("Weight (kg)").
export function NumberField({
  id,
  label,
  unit,
  value,
  onChange,
  hintId,
  ...inputProps
}: {
  id: string;
  label: string;
  unit?: string;
  value: string | number;
  onChange: (value: string) => void;
  hintId?: string;
} & Omit<InputHTMLAttributes<HTMLInputElement>, 'id' | 'value' | 'onChange'>) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium mb-1">
        {label}
      </label>
      <div className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 focus-within:ring-2 focus-within:ring-blue-500 dark:border-gray-600 dark:bg-gray-900">
        <input
          {...inputProps}
          type="number"
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-describedby={hintId}
          className="min-h-[46px] w-full bg-transparent font-mono text-base text-gray-900 outline-none dark:text-gray-100"
        />
        {unit && (
          <span aria-hidden="true" className="text-sm text-gray-500 dark:text-gray-400">
            {unit}
          </span>
        )}
      </div>
    </div>
  );
}
