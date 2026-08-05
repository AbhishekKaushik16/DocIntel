'use client';

import { useEffect, useState } from 'react';
import DocumentTable from '@/components/DocumentTable';
import { getDocuments, DocumentListItem, deleteDocument } from '@/lib/api';
import { Filter, RefreshCw, ChevronLeft, ChevronRight } from 'lucide-react';

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('');
  const [loading, setLoading] = useState(true);

  const fetchDocs = async () => {
    setLoading(true);
    try {
      const res = await getDocuments(page, 15, statusFilter || undefined, typeFilter || undefined);
      setDocuments(res.documents);
      setTotalPages(res.total_pages);
    } catch (err) {
      console.error('Failed to fetch documents', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocs();
  }, [page, statusFilter, typeFilter]);

  const handleDelete = async (id: string) => {
    if (confirm('Are you sure you want to delete this document?')) {
      await deleteDocument(id);
      fetchDocs();
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-gray-800 pb-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Document Repository</h1>
          <p className="text-xs text-gray-400 mt-1">Browse, filter, and manage all processed documents.</p>
        </div>

        <button
          onClick={fetchDocs}
          className="inline-flex items-center space-x-2 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-medium rounded-lg border border-gray-700 transition-all"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Filters Bar */}
      <div className="bg-[#111827] border border-gray-800 p-4 rounded-xl flex flex-wrap items-center gap-4 text-xs">
        <div className="flex items-center space-x-2 text-gray-400 font-semibold uppercase tracking-wider">
          <Filter className="w-4 h-4 text-blue-400" />
          <span>Filters:</span>
        </div>

        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(1);
          }}
          className="bg-[#192237] border border-gray-700 text-gray-200 px-3 py-1.5 rounded-lg focus:outline-none focus:border-blue-500"
        >
          <option value="">All Statuses</option>
          <option value="completed">Completed</option>
          <option value="needs_review">Needs Review</option>
          <option value="processing">Processing</option>
          <option value="failed">Failed</option>
          <option value="pending">Pending</option>
        </select>

        <select
          value={typeFilter}
          onChange={(e) => {
            setTypeFilter(e.target.value);
            setPage(1);
          }}
          className="bg-[#192237] border border-gray-700 text-gray-200 px-3 py-1.5 rounded-lg focus:outline-none focus:border-blue-500"
        >
          <option value="">All Document Types</option>
          <option value="invoice">Invoice</option>
          <option value="receipt">Receipt</option>
          <option value="contract">Contract</option>
          <option value="resume">Resume</option>
          <option value="generic">Generic</option>
        </select>

        {(statusFilter || typeFilter) && (
          <button
            onClick={() => {
              setStatusFilter('');
              setTypeFilter('');
              setPage(1);
            }}
            className="text-blue-400 hover:underline text-xs"
          >
            Reset Filters
          </button>
        )}
      </div>

      {/* Table */}
      <DocumentTable documents={documents} onDelete={handleDelete} />

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-gray-400 pt-2">
          <span>Page {page} of {totalPages}</span>
          <div className="flex space-x-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="p-1.5 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-50"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="p-1.5 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-50"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
