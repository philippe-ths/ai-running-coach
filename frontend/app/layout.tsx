import './globals.css';
import type { Metadata, Viewport } from 'next';
import { Fraunces, Hanken_Grotesk, IBM_Plex_Mono } from 'next/font/google';
import { ClerkProvider } from '@clerk/nextjs';
import NavBar from '@/components/NavBar';
import BottomNav from '@/components/BottomNav';
import ThemeProvider from '@/components/ThemeProvider';

// Clerk wraps the app only when a publishable key is configured (Vercel prod +
// local dev). Without it -- CI build, the smoke harness -- the app renders
// exactly as before, with no auth surface. See middleware.ts for the matching
// pass-through gate.
const clerkEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

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
// and no double-tap zoom on mobile. `viewportFit: 'cover'` lets the layout
// extend under the iOS safe areas so `env(safe-area-inset-*)` resolves to real
// values, which the bottom tab bar uses to clear the home indicator.
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: 'cover',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const tree = (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${hanken.variable} ${fraunces.variable} ${plexMono.variable}`}
    >
      <body className="font-sans min-h-screen flex flex-col">
        <ThemeProvider>
          <NavBar />
          <main className="flex-1 max-w-4xl w-full mx-auto px-4 py-8 pb-[calc(4rem+env(safe-area-inset-bottom)+1rem)] md:pb-8">
            {children}
          </main>
          <BottomNav />
        </ThemeProvider>
      </body>
    </html>
  );

  if (!clerkEnabled) {
    return tree;
  }
  return (
    <ClerkProvider
      signInUrl={process.env.NEXT_PUBLIC_CLERK_SIGN_IN_URL || '/sign-in'}
      signUpUrl={process.env.NEXT_PUBLIC_CLERK_SIGN_UP_URL || '/sign-up'}
      afterSignInUrl={process.env.NEXT_PUBLIC_CLERK_AFTER_SIGN_IN_URL || '/'}
      afterSignOutUrl="/sign-in"
    >
      {tree}
    </ClerkProvider>
  );
}
