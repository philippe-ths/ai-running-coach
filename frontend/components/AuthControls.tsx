'use client';

import { SignedOut, SignInButton } from '@clerk/nextjs';

// The nav's auth affordance: a sign-in button when signed out, and nothing when
// signed in. Clerk's user menu was retired (#968): it duplicated Profile under
// another name, and its delete removed the sign-in while orphaning every row
// (#967). Sign out and delete now live on Profile > Account. Rendered only when
// Clerk is configured (NavBar gates this), so it is never mounted without a
// ClerkProvider above it.
export default function AuthControls() {
  return (
    <div className="flex items-center">
      <SignedOut>
        <SignInButton mode="redirect">
          <button className="min-h-[40px] px-3 rounded-md text-sm font-medium text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-900/30">
            Sign in
          </button>
        </SignInButton>
      </SignedOut>
    </div>
  );
}
