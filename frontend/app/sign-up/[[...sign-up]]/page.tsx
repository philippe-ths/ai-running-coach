import { SignUp } from '@clerk/nextjs';
import { clerkEnabled } from '@/lib/authMode';

export const dynamic = 'force-dynamic';

export default function SignUpPage() {
  if (!clerkEnabled) {
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
