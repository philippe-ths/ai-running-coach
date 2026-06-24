'use client';

import { SignedIn, SignedOut, SignInButton, UserButton } from '@clerk/nextjs';

// The nav's auth affordance: the Clerk user menu when signed in, a sign-in
// button when not. Rendered only when Clerk is configured (NavBar gates this),
// so it is never mounted without a ClerkProvider above it.
export default function AuthControls() {
  return (
    <div className="flex items-center">
      <SignedIn>
        <UserButton afterSignOutUrl="/sign-in" />
      </SignedIn>
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
