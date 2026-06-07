'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const LINKS = [
  { href: '/trends', label: 'Trends' },
  { href: '/profile', label: 'Profile' },
];

export default function NavBar() {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === '/' ? pathname === '/' : pathname.startsWith(href);

  return (
    <nav className="border-b border-gray-200 bg-white sticky top-0 z-10">
      <div className="max-w-4xl mx-auto px-4 h-16 flex items-center justify-between gap-4">
        <Link
          href="/"
          className="font-bold text-xl tracking-tight text-blue-600 shrink-0"
        >
          AI Coach
        </Link>
        <div className="flex items-center gap-1 sm:gap-2">
          {LINKS.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={`flex items-center min-h-[44px] px-3 rounded-md text-sm font-medium transition-colors ${
                isActive(href)
                  ? 'text-blue-600 bg-blue-50'
                  : 'text-gray-600 hover:text-blue-600 hover:bg-gray-50'
              }`}
            >
              {label}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  );
}
