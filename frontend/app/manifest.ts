import type { MetadataRoute } from 'next';

// #228 prerequisite. The app had no web-app manifest at all, so "Add to Home
// Screen" produced a bookmark that reopened in a Safari tab rather than a
// standalone window. That made two shipped things wrong: the home-screen
// experience was never actually standalone, and PwaOpenInAppBanner's
// `isStandalone()` check could never return true, so the #619 nudge kept
// showing inside the very app it was telling you to open.
//
// This does NOT fix the Telegram-opens-Safari routing itself -- iOS gives an
// external app no way to hand a URL to an installed web app, which needs a
// native shell with Universal Links or a custom URL scheme. It fixes the
// standalone launch the nudge depends on.
//
// Served at /manifest.webmanifest. The Clerk middleware matcher excludes paths
// containing a dot, so this stays reachable without a session -- which it must
// be, since iOS and Android fetch it before any sign-in.

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'AI Running Coach',
    short_name: 'Coach',
    description: 'Opinionated post-run analysis from your own training data',
    start_url: '/',
    display: 'standalone',
    orientation: 'portrait',
    // The light-theme paper background from globals.css. A manifest carries one
    // colour, so the app's dark theme is handled by the page itself rather than
    // here; this only tints the platform chrome at launch.
    background_color: '#fdfbf7',
    theme_color: '#fdfbf7',
    icons: [
      {
        // The existing app/icon.svg, served by Next's metadata file convention.
        // Chrome accepts a vector icon with sizes "any" for installability, so
        // this needs no committed binary asset. iOS ignores manifest icons and
        // uses the apple-touch-icon from app/apple-icon.tsx instead.
        src: '/icon.svg',
        sizes: 'any',
        type: 'image/svg+xml',
        purpose: 'any',
      },
    ],
  };
}
