'use client';

import QueryChat from '@/components/QueryChat';
import Navbar from '@/components/Navbar';

export default function QueryPage() {
  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc' }}>
      <Navbar />
      <main style={{ maxWidth: '900px', margin: '0 auto', padding: '24px 16px', height: 'calc(100vh - 64px)' }}>
        <QueryChat />
      </main>
    </div>
  );
}
