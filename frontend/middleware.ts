import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';

// Clerk activates only when a publishable key is present (Vercel prod + local
// dev with .env.local). With no key -- CI's lint/build and the smoke harness,
// which run against a mock backend with no auth -- the middleware is a
// pass-through, so routes load without a sign-in. NEXT_PUBLIC_* vars are inlined
// at build time, so this is fixed per build environment.
const clerkEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

// Public routes never require a session: the auth pages themselves, and /api/*
// (the proxy forwards the Clerk token and the FastAPI backend is the real
// enforcer -- redirecting an XHR to an HTML sign-in page would just break it).
const isPublicRoute = createRouteMatcher([
  '/sign-in(.*)',
  '/sign-up(.*)',
  '/api/(.*)',
]);

const enforce = clerkMiddleware((auth, req) => {
  if (!isPublicRoute(req)) {
    auth().protect();
  }
});

export default clerkEnabled ? enforce : () => NextResponse.next();

export const config = {
  matcher: [
    // Run on everything except Next internals and files with an extension.
    '/((?!_next|.*\\..*).*)',
    // Always run on API routes so the proxy can attach the session token.
    '/(api|trpc)(.*)',
  ],
};
