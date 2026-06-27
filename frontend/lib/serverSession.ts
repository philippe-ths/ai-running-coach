import 'server-only';

import { fetchFromAPI } from '@/lib/api';
import { clerkEnabled } from '@/lib/authMode';

// Server-only Clerk session access. `import 'server-only'` guarantees this
// module (and the Clerk server SDK it pulls in) never lands in a client bundle.
// Only the two server-component pages that call the backend directly
// (app/page.tsx, app/activity/[id]/page.tsx) import this; client components hit
// the proxy, which forwards the token itself. Under the #488 ungate clerkEnabled
// is false, so this returns null and the backend degrades to the seeded user.

export async function getServerSessionToken(): Promise<string | null> {
  if (!clerkEnabled) return null;
  try {
    const { auth } = await import('@clerk/nextjs/server');
    return (await auth().getToken()) ?? null;
  } catch {
    // No active session in this render context.
    return null;
  }
}

// Drop-in for fetchFromAPI in server components: attaches the verified Clerk
// session token so the FastAPI backend can resolve the user on direct calls.
export async function serverFetch(endpoint: string, options: RequestInit = {}) {
  const token = await getServerSessionToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers['x-clerk-session-token'] = token;
  return fetchFromAPI(endpoint, { ...options, headers });
}
