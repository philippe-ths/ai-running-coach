import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';

// #964: the shell both legal pages share, so /privacy and /terms cannot drift
// apart in framing or typography. Deliberately a server component with no
// client state: these pages must render for a signed-out visitor (and for
// Google's OAuth review, which fetches them unauthenticated), so nothing here
// may depend on a session. See middleware.ts, where both paths are declared
// public.

export default function LegalPage({
  title,
  updated,
  children,
}: {
  title: string;
  updated: string;
  children: React.ReactNode;
}) {
  return (
    <main className="mx-auto max-w-3xl px-5 py-10 sm:px-6 sm:py-14">
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 transition-colors hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to the app
      </Link>

      <header className="mt-6">
        <h1 className="font-serif text-3xl text-gray-900 sm:text-4xl dark:text-gray-50">
          {title}
        </h1>
        <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
          Last updated {updated}
        </p>
      </header>

      <article className="prose prose-gray mt-8 max-w-none dark:prose-invert prose-headings:font-serif prose-h2:mt-10 prose-h2:mb-3 prose-h2:text-xl prose-p:leading-relaxed prose-li:my-1">
        {children}
      </article>

      <footer className="mt-14 border-t border-gray-200 pt-6 text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
        <nav className="flex gap-5">
          <Link href="/privacy" className="hover:text-gray-800 dark:hover:text-gray-200">
            Privacy Policy
          </Link>
          <Link href="/terms" className="hover:text-gray-800 dark:hover:text-gray-200">
            Terms of Service
          </Link>
        </nav>
      </footer>
    </main>
  );
}
