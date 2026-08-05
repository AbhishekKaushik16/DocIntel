'use client';

import { useEffect, useState } from 'react';
import UploadZone from '@/components/UploadZone';
import StatsOverview from '@/components/StatsOverview';
import DocumentTable from '@/components/DocumentTable';
import { getDashboardStats, getDocuments, DashboardStats, DocumentListItem, deleteDocument } from '@/lib/api';
import { Sparkles, RefreshCw } from 'lucide-react';

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentDocs, setRecentDocs] = useState<DocumentListItem[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    setLoading(true);
    try {
      const [s, docsRes] = await Promise.all([
        getDashboardStats(),
        getDocuments(1, 5),
      ]);
      setStats(s);
      setRecentDocs(docsRes.documents);
    } catch (err) {
      console.error('Failed to load dashboard data', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleDelete = async (id: string) => {
    if (confirm('Are you sure you want to delete this document?')) {
      await deleteDocument(id);
      loadData();
    }
  };

  return (
    <div className="space-y-8">
      {/* Header Banner */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-gray-800 pb-6">
        <div>
          <div className="inline-flex items-center space-x-2 px-2.5 py-1 rounded-full text-xs font-semibold bg-blue-500/10 text-blue-400 border border-blue-500/20 mb-2">
            <Sparkles className="w-3.5 h-3.5" />
            <span>AI-Powered Extraction & Classification</span>
          </div>
          <h1 className="text-3xl font-extrabold text-white tracking-tight">
            Document Intelligence Platform
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            Upload messy invoices, receipts, contracts, and resumes to convert them into structured, queryable data.
          </p>
        </div>

        <button
          onClick={loadData}
          className="inline-flex items-center space-x-2 px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm font-medium rounded-lg border border-gray-700 transition-all shadow-sm"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Stats Cards */}
      <StatsOverview stats={stats} />

      {/* Upload Zone */}
      <div>
        <h3 className="text-lg font-semibold text-gray-200 mb-3">Upload Documents</h3>
        <UploadZone onUploadSuccess={loadData} />
      </div>

      {/* Recent Documents Table */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-semibold text-gray-200">Recent Documents</h3>
        </div>
        <DocumentTable documents={recentDocs} onDelete={handleDelete} />
      </div>
    </div>
  );
}
