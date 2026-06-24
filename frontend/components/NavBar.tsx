'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { User } from 'lucide-react';

const LINKS = [
  { href: '/activities', label: 'Activities' },
  { href: '/load', label: 'Load' },
  { href: '/trends', label: 'Trends' },
  { href: '/profile', label: 'Profile' },
];

export default function NavBar() {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === '/' ? pathname === '/' : pathname.startsWith(href);

  return (
    <nav className="border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900 sticky top-0 z-30 overflow-x-hidden">
      <div className="max-w-4xl mx-auto px-4 h-16 flex items-center justify-between gap-4">
        <Link
          href="/"
          className="font-bold text-xl tracking-tight text-blue-600 shrink-0"
        >
          AI Coach
        </Link>
        <div className="hidden md:flex items-center gap-1 sm:gap-2">
          {LINKS.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={`flex items-center min-h-[44px] px-3 rounded-md text-sm font-medium transition-colors ${
                isActive(href)
                  ? 'text-blue-600 bg-blue-50 dark:text-blue-400 dark:bg-blue-900/30'
                  : 'text-gray-600 hover:text-blue-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:text-blue-400 dark:hover:bg-gray-800'
              }`}
            >
              {label}
            </Link>
          ))}
        </div>
        {/* On mobile the nav links live in the bottom tab bar; Profile stays
            here as a top-right icon (the bottom bar omits it). */}
        <Link
          href="/profile"
          aria-label="Profile"
          aria-current={isActive('/profile') ? 'page' : undefined}
          className={`md:hidden flex items-center justify-center h-11 w-11 rounded-full transition-colors ${
            isActive('/profile')
              ? 'text-blue-600 bg-blue-50 dark:text-blue-400 dark:bg-blue-900/30'
              : 'text-gray-600 hover:text-blue-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:text-blue-400 dark:hover:bg-gray-800'
          }`}
        >
          <User className="h-6 w-6" aria-hidden="true" />
        </Link>
      </div>
    </nav>
  );
}
