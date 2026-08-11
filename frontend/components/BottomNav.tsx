'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, CalendarDays, Gauge, TrendingUp, LucideIcon } from 'lucide-react';
import useKeyboardOpen from '@/lib/useKeyboardOpen';

const TABS: { href: string; label: string; Icon: LucideIcon }[] = [
  { href: '/', label: 'Home', Icon: Home },
  // #830: the Schedule takes this slot. The tab bar should hold the four things
  // you open the app FOR — scrolling your own history is something you go
  // looking for; what you are doing tomorrow is something you check. The
  // /activities route is untouched and keeps its button on the home page.
  { href: '/schedule', label: 'Schedule', Icon: CalendarDays },
  { href: '/load', label: 'Load', Icon: Gauge },
  { href: '/trends', label: 'Trends', Icon: TrendingUp },
];

// Native-app-style bottom tab bar. Mobile only (md:hidden); the top NavBar's
// text links take over on larger screens.
export default function BottomNav() {
  const pathname = usePathname();
  const keyboardOpen = useKeyboardOpen();

  const isActive = (href: string) =>
    href === '/' ? pathname === '/' : pathname.startsWith(href);

  return (
    <nav
      className={`${keyboardOpen ? 'hidden' : ''} md:hidden fixed bottom-0 inset-x-0 z-40 border-t border-gray-200 bg-white/95 backdrop-blur dark:border-gray-700 dark:bg-gray-900/95 pb-[max(0.375rem,calc(env(safe-area-inset-bottom)-0.75rem))]`}
      aria-label="Primary"
    >
      <div className="flex items-stretch justify-around">
        {TABS.map(({ href, label, Icon }) => {
          const active = isActive(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className={`flex flex-1 flex-col items-center justify-center gap-0.5 min-h-[56px] pt-2 pb-1 text-[11px] font-medium transition-colors ${
                active
                  ? 'text-blue-600 dark:text-blue-400'
                  : 'text-gray-500 hover:text-blue-600 dark:text-gray-400 dark:hover:text-blue-400'
              }`}
            >
              <Icon
                className="h-6 w-6"
                strokeWidth={active ? 2.4 : 1.9}
                aria-hidden="true"
              />
              <span>{label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
