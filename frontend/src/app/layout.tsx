import './globals.css';
import Navbar from '@/components/Navbar';

export const metadata = {
  title: 'DocIntel — Document Intelligence Platform',
  description: 'Turn messy documents into structured, queryable data.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#0b0f19] text-gray-100 min-h-screen flex flex-col antialiased">
        <Navbar />
        <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {children}
        </main>
        <footer className="border-t border-gray-800 py-6 text-center text-xs text-gray-500">
          DocIntel Document Intelligence Platform &copy; 2026 — Built for Zamp Engineering Challenge
        </footer>
      </body>
    </html>
  );
}
