import { SignIn } from '@clerk/nextjs';
import { clerkEnabled } from '@/lib/authMode';

// Catch-all route so Clerk can own its sub-paths (factor-two, SSO callback).
// Rendered only when Clerk is enabled; otherwise the app has no auth surface and
// this page is never reached (the middleware is a pass-through). Gated on the
// shared clerkEnabled so the #488 ungate renders the fallback, not a SignIn
// widget without a ClerkProvider.
export const dynamic = 'force-dynamic';

export default function SignInPage() {
  if (!clerkEnabled) {
    return (
      <div className="flex justify-center py-16 text-gray-500">
        Authentication is not configured.
      </div>
    );
  }
  return (
    <div className="flex justify-center py-12">
      <SignIn />
    </div>
  );
}
