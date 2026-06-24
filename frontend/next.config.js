/** @type {import('next').NextConfig} */
const nextConfig = {
  // NOTE (Phase 2 / Clerk): the legacy dev rewrite that forwarded /api/:path*
  // straight to http://127.0.0.1:8000 was REMOVED. A plain-array rewrite is an
  // `afterFiles` rewrite, which Next checks BEFORE dynamic routes -- so it
  // shadowed the dynamic catch-all proxy at app/api/[...path]/route.ts, sending
  // client /api/* calls to the backend with NO Clerk session token (401). The
  // route handler is the proxy now: it attaches the token (and basic creds) and
  // forwards to BACKEND_URL. Client calls must use relative /api/* paths
  // (NEXT_PUBLIC_API_BASE_URL empty) so they hit the handler.
}

module.exports = nextConfig
