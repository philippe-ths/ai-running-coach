import { SignIn } from '@clerk/nextjs';

// Catch-all route so Clerk can own its sub-paths (factor-two, SSO callback).
// Rendered only when Clerk is configured; with no key the app has no auth
// surface and this page is never reached (the middleware is a pass-through).
export const dynamic = 'force-dynamic';

export default function SignInPage() {
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) {
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
