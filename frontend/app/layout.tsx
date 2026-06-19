import './globals.css';
import type { Metadata, Viewport } from 'next';
import { Fraunces, Hanken_Grotesk, IBM_Plex_Mono } from 'next/font/google';
import NavBar from '@/components/NavBar';
import ThemeProvider from '@/components/ThemeProvider';

// Three deliberate type roles: Fraunces (serif) is the coach's voice + display
// headings, Hanken Grotesk (sans) is body + UI, IBM Plex Mono is numbers/data.
const fraunces = Fraunces({
  subsets: ['latin'],
  style: ['normal', 'italic'],
  variable: '--font-fraunces',
  display: 'swap',
});
const hanken = Hanken_Grotesk({
  subsets: ['latin'],
  variable: '--font-hanken',
  display: 'swap',
});
const plexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-plex-mono',
  display: 'swap',
});

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
    <html
      lang="en"
      suppressHydrationWarning
      className={`${hanken.variable} ${fraunces.variable} ${plexMono.variable}`}
    >
      <body className="font-sans min-h-screen flex flex-col">
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
