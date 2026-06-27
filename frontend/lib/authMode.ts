// Single source of truth for whether the Clerk auth surface is active.
//
// Clerk is ON when a publishable key is present (Vercel prod + local dev with a
// key in .env.local). It is OFF with no key (CI build + the smoke harness, which
// run against a mock backend) and under the dev-only LOCAL_NO_AUTH ungate (#488),
// which drives the seeded local stack for browser verification without a sign-in.
//
// Every Clerk-gating site imports `clerkEnabled` from here so the decisions can
// never disagree: layout's <ClerkProvider> must be present exactly when any
// component renders a Clerk widget (<SignedIn>/<UserButton>/...), or those
// widgets throw "can only be used within <ClerkProvider>".
//
// NEXT_PUBLIC_* values are inlined at build time, so this is fixed per build
// environment. LOCAL_NO_AUTH is honoured ONLY in a non-production build, so it
// can never disable the gate in a Vercel build; the backend stays the real
// enforcer regardless (`Settings.clerk_enabled`).
const localNoAuth =
  process.env.NEXT_PUBLIC_LOCAL_NO_AUTH === 'true' &&
  process.env.NODE_ENV !== 'production';

export const clerkEnabled =
  Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) && !localNoAuth;
