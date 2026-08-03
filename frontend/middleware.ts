import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { clerkEnabled } from '@/lib/authMode';

// Clerk activates only when `clerkEnabled` (a publishable key present and not
// under the #488 dev ungate). Otherwise -- CI lint/build, the smoke harness, or
// `make verify-local` -- the middleware is a pass-through, so routes load
// without a sign-in. See lib/authMode.ts for the single source of truth.

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
    //
    // `apple-icon` is excluded by name (#228). It is a generated metadata asset
    // like /icon.svg and /manifest.webmanifest, but unlike those it has no dot in
    // its path, so it would otherwise land inside the gate: `auth().protect()`
    // answers an image request with a 404, leaving the iOS home screen with no
    // icon and a screenshot of the page in its place. Excluding it here rather
    // than marking it a public route keeps all three metadata assets behaving
    // identically -- outside the middleware entirely, not merely unprotected.
    '/((?!_next|apple-icon|.*\\..*).*)',
    // Always run on API routes so the proxy can attach the session token.
    '/(api|trpc)(.*)',
  ],
};
