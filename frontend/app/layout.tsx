import './globals.css';
import type { Metadata, Viewport } from 'next';
import { Fraunces, Hanken_Grotesk, IBM_Plex_Mono } from 'next/font/google';
import { ClerkProvider } from '@clerk/nextjs';
import NavBar from '@/components/NavBar';
import BottomNav from '@/components/BottomNav';
import PwaOpenInAppBanner from '@/components/PwaOpenInAppBanner';
import ThemeProvider from '@/components/ThemeProvider';
import { CoachSheetProvider } from '@/components/coach/CoachSheetContext';
import CoachSheet from '@/components/coach/CoachSheet';
import CoachLauncher from '@/components/coach/CoachLauncher';
import { clerkEnabled } from '@/lib/authMode';

// Clerk wraps the app only when `clerkEnabled` (a publishable key configured and
// not under the #488 dev ungate). Without it -- CI build, the smoke harness, or
// `make verify-local` -- the app renders with no auth surface. See
// lib/authMode.ts for the single source of truth and middleware.ts for the
// matching pass-through gate.

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
  // #228 prerequisite. `appleWebApp.capable` emits
  // <meta name="apple-mobile-web-app-capable" content="yes">, which is the one
  // thing that makes iOS launch a home-screen icon in a standalone window
  // instead of a Safari tab. Without it the #619 nudge told people to open from
  // the home screen into an experience that was never actually standalone, and
  // PwaOpenInAppBanner's `isStandalone()` could never return true, so the nudge
  // showed inside the app it was pointing at.
  // `statusBarStyle: 'default'` keeps the status bar opaque and matched to the
  // page background; 'black-translucent' would slide content under the clock.
  appleWebApp: {
    capable: true,
    title: 'Coach',
    statusBarStyle: 'default',
  },
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
          {/* #766: the coach sheet mounts at the layout level, inside one
              app-wide provider, so its conversation (and an in-flight reply)
              survives route navigation. */}
          <CoachSheetProvider>
            <NavBar />
            <PwaOpenInAppBanner />
            <main className="flex-1 max-w-4xl w-full mx-auto px-4 py-8 pb-[calc(4rem+env(safe-area-inset-bottom)+1rem)] md:pb-8">
              {children}
            </main>
            <BottomNav />
            <CoachLauncher />
            <CoachSheet />
          </CoachSheetProvider>
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
