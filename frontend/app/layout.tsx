import './globals.css';
import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import NavBar from '@/components/NavBar';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'AI Running Coach',
  description: 'Local-first running advice',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${inter.className} min-h-screen flex flex-col`}>
        <NavBar />
        <main className="flex-1 max-w-4xl w-full mx-auto px-4 py-8">
          {children}
        </main>
      </body>
    </html>
  );
}
