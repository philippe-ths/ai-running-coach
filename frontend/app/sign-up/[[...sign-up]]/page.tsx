import { SignUp } from '@clerk/nextjs';

export const dynamic = 'force-dynamic';

export default function SignUpPage() {
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) {
    return (
      <div className="flex justify-center py-16 text-gray-500">
        Authentication is not configured.
      </div>
    );
  }
  return (
    <div className="flex justify-center py-12">
      <SignUp />
    </div>
  );
}
