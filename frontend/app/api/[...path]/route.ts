import { NextRequest } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { clerkEnabled } from '@/lib/authMode';

const BACKEND_URL = process.env.BACKEND_URL;
const BASIC_USER = process.env.BACKEND_BASIC_AUTH_USER;
const BASIC_PASS = process.env.BACKEND_BASIC_AUTH_PASSWORD;
// With Clerk off the proxy forwards only the basic service secret (CI/smoke, or
// the #488 dev ungate), so the backend degrades to the single seeded user. The
// session token rides a dedicated header so it never collides with the basic
// Authorization credential. See lib/authMode.ts for the single source of truth.
const CLERK_ENABLED = clerkEnabled;
const SESSION_TOKEN_HEADER = 'x-clerk-session-token';

const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'transfer-encoding',
  'te',
  'trailer',
  'upgrade',
  'proxy-authenticate',
  'proxy-authorization',
  'content-encoding',
  'content-length',
]);

async function proxy(
  req: NextRequest,
  { params }: { params: { path: string[] } }
) {
  if (!BACKEND_URL) {
    return new Response('BACKEND_URL not configured', { status: 500 });
  }

  const pathSegments = params.path ?? [];
  const target = `${BACKEND_URL}/api/${pathSegments.join('/')}${req.nextUrl.search}`;

  const upstreamHeaders = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase()) && key.toLowerCase() !== 'host') {
      upstreamHeaders.set(key, value);
    }
  });
  if (BASIC_USER && BASIC_PASS) {
    const token = Buffer.from(`${BASIC_USER}:${BASIC_PASS}`).toString('base64');
    upstreamHeaders.set('authorization', `Basic ${token}`);
  }
  // Forward the verified Clerk session token so the backend can resolve the
  // user. The browser carries the Clerk cookie; auth().getToken() mints the JWT
  // server-side. Never trust an incoming session-token header from the client.
  upstreamHeaders.delete(SESSION_TOKEN_HEADER);
  if (CLERK_ENABLED) {
    try {
      const { getToken } = auth();
      const sessionToken = await getToken();
      if (sessionToken) {
        upstreamHeaders.set(SESSION_TOKEN_HEADER, sessionToken);
      }
    } catch {
      // No active session (or called outside Clerk context): forward nothing
      // and let the backend reject with 401.
    }
  }

  const init: RequestInit = {
    method: req.method,
    headers: upstreamHeaders,
    redirect: 'manual',
  };
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    init.body = await req.arrayBuffer();
  }

  const upstream = await fetch(target, init);

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) {
      responseHeaders.set(key, value);
    }
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;

export const dynamic = 'force-dynamic';

// The coach-chat endpoint streams an SSE response that can run for tens of
// seconds. Vercel's default function duration (~10s) severs the stream
// mid-response — the browser then reports a bare "Load failed" (#223). Raise
// the ceiling so the full stream can complete. 60s is the Hobby maximum and
// well within Pro's; the underlying LLM call is bounded well below this.
export const maxDuration = 60;

// Streaming requires the Node.js runtime (the default), pinned here so the
// pass-through of `upstream.body` is never statically optimized or buffered.
export const runtime = 'nodejs';
