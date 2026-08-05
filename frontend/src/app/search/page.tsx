'use client';

import { useState } from 'react';
import Link from 'next/link';
import StatusBadge from '@/components/StatusBadge';
import { searchDocuments, SearchResult } from '@/lib/api';
import { Search, FileText, ArrowRight, Sparkles, Filter } from 'lucide-react';

export default function SearchPage() {
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [searching, setSearching] = useState(false);

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!query.trim()) return;

    setSearching(true);
    try {
      const res = await searchDocuments(query, typeFilter || undefined, statusFilter || undefined);
      setResults(res.results);
      setTotal(res.total);
    } catch (err) {
      console.error('Search failed', err);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="border-b border-gray-800 pb-4">
        <h1 className="text-2xl font-bold text-white">Full-Text & Faceted Search</h1>
        <p className="text-xs text-gray-400 mt-1">
          Search across unstructured document text and extracted JSON fields powered by PostgreSQL full-text search.
        </p>
      </div>

      {/* Search Input Bar */}
      <form onSubmit={handleSearch} className="space-y-4">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="w-5 h-5 absolute left-3.5 top-3.5 text-gray-500" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. Acme Corp invoice total due, California agreement..."
              className="w-full bg-[#111827] border border-gray-700 focus:border-blue-500 rounded-xl pl-11 pr-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none shadow-xl"
            />
          </div>
          <button
            type="submit"
            disabled={searching || !query.trim()}
            className="px-6 py-3 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-xl text-sm shadow-lg shadow-blue-600/20 disabled:opacity-50 transition-all"
          >
            {searching ? 'Searching...' : 'Search'}
          </button>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-4 text-xs bg-[#111827] border border-gray-800 p-3 rounded-lg">
          <span className="text-gray-400 font-semibold uppercase flex items-center space-x-1">
            <Filter className="w-3.5 h-3.5 text-blue-400" />
            <span>Faceted Filters:</span>
          </span>

          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="bg-[#192237] border border-gray-700 text-gray-200 px-2.5 py-1 rounded focus:outline-none"
          >
            <option value="">All Document Types</option>
            <option value="invoice">Invoice</option>
            <option value="receipt">Receipt</option>
            <option value="contract">Contract</option>
            <option value="resume">Resume</option>
            <option value="generic">Generic</option>
          </select>

          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-[#192237] border border-gray-700 text-gray-200 px-2.5 py-1 rounded focus:outline-none"
          >
            <option value="">All Statuses</option>
            <option value="completed">Completed</option>
            <option value="needs_review">Needs Review</option>
          </select>
        </div>
      </form>

      {/* Results Section */}
      {total !== null && (
        <div className="text-xs text-gray-400 font-medium">
          Found <span className="text-white font-bold">{total}</span> matching document(s) for &ldquo;{query}&rdquo;
        </div>
      )}

      <div className="space-y-4">
        {results.map((result) => (
          <div
            key={result.id}
            className="bg-[#111827] border border-gray-800 hover:border-gray-700 p-5 rounded-xl transition-all shadow-md space-y-2"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <FileText className="w-4 h-4 text-blue-400" />
                <Link
                  href={`/documents/${result.id}`}
                  className="font-bold text-gray-100 hover:text-blue-400 text-base transition-colors"
                >
                  {result.original_filename}
                </Link>
                <span className="capitalize px-2 py-0.5 rounded text-[10px] bg-gray-800 text-gray-300 font-mono">
                  {result.document_type || 'Unclassified'}
                </span>
              </div>
              <StatusBadge status={result.status} confidence={result.confidence_score} />
            </div>

            {/* Headline snippet */}
            {result.headline && (
              <p
                className="text-xs text-gray-300 bg-[#0b0f19] p-3 rounded border border-gray-800/60 font-mono [&>mark]:bg-blue-500/30 [&>mark]:text-blue-300 [&>mark]:px-1 [&>mark]:rounded"
                dangerouslySetInnerHTML={{ __html: result.headline }}
              />
            )}

            <div className="flex items-center justify-between text-[11px] text-gray-500 pt-1">
              <span>Relevance Score: {result.relevance_score ?? 'N/A'}</span>
              <Link
                href={`/documents/${result.id}`}
                className="text-blue-400 hover:underline flex items-center space-x-1"
              >
                <span>View Details</span>
                <ArrowRight className="w-3 h-3" />
              </Link>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
