// Server components hit the backend directly with basic auth (env vars stay
// server-side). Client components hit relative /api/* paths; the catch-all
// route handler in frontend/app/api/[...path]/route.ts proxies those to the
// backend with the same auth.

const isServer = typeof window === 'undefined';

const SERVER_BASE_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  'http://127.0.0.1:8000';
const CLIENT_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

function buildAuthHeader(): string | null {
  const user = process.env.BACKEND_BASIC_AUTH_USER;
  const pass = process.env.BACKEND_BASIC_AUTH_PASSWORD;
  if (!user || !pass) return null;
  const token = Buffer.from(`${user}:${pass}`).toString('base64');
  return `Basic ${token}`;
}

export async function fetchFromAPI(endpoint: string, options: RequestInit = {}) {
  const baseUrl = isServer ? SERVER_BASE_URL : CLIENT_BASE_URL;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };
  if (isServer) {
    const auth = buildAuthHeader();
    if (auth) headers.Authorization = auth;
  }
  const res = await fetch(`${baseUrl}${endpoint}`, {
    ...options,
    headers,
    cache: 'no-store',
  });

  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`API Error: ${res.statusText}`);
  }

  return res.json();
}
