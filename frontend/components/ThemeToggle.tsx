'use client';

import { useEffect, useState } from 'react';
import { useTheme } from 'next-themes';
import { Sun, Moon, Monitor } from 'lucide-react';

const MODES = [
  { value: 'light', icon: Sun, label: 'Light' },
  { value: 'dark', icon: Moon, label: 'Dark' },
  { value: 'system', icon: Monitor, label: 'System' },
] as const;

export default function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  // next-themes only knows the real theme after hydration; render a stable
  // placeholder first so server and client markup match.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return <div className="w-[84px] h-7" aria-hidden="true" />;
  }

  return (
    <div className="flex items-center rounded-full border border-gray-200 dark:border-gray-700 p-0.5">
      {MODES.map(({ value, icon: Icon, label }) => (
        <button
          key={value}
          type="button"
          aria-label={`${label} theme`}
          title={`${label} theme`}
          onClick={() => setTheme(value)}
          className={`p-1 rounded-full transition-colors ${
            theme === value
              ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300'
              : 'text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300'
          }`}
        >
          <Icon size={14} />
        </button>
      ))}
    </div>
  );
}
