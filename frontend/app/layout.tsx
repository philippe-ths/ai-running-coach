import './globals.css';
import type { Metadata, Viewport } from 'next';
import { Inter } from 'next/font/google';
import NavBar from '@/components/NavBar';
import ThemeProvider from '@/components/ThemeProvider';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'AI Running Coach',
  description: 'Local-first running advice',
};

// Lock the viewport so the app behaves like a native screen: no pinch-to-zoom
// and no double-tap zoom on mobile.
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.className} min-h-screen flex flex-col`}>
        <ThemeProvider>
          <NavBar />
          <main className="flex-1 max-w-4xl w-full mx-auto px-4 py-8">
            {children}
          </main>
        </ThemeProvider>
      </body>
    </html>
  );
}
