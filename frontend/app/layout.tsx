import './globals.css';
import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import Link from 'next/link';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'AI Running Coach',
  description: 'Local-first running advice',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${inter.className} min-h-screen flex flex-col`}>
        <nav className="border-b border-gray-200 bg-white sticky top-0 z-10">
          <div className="max-w-4xl mx-auto px-4 h-16 flex items-center justify-between">
            <Link href="/" className="font-bold text-xl tracking-tight text-blue-600">
              AI Coach
            </Link>
            <div className="text-sm text-gray-500">MVP Mode</div>
          </div>
        </nav>
        <main className="flex-1 max-w-4xl w-full mx-auto px-4 py-8">
          {children}
        </main>
      </body>
    </html>
  );
}
