'use client';

import { useEffect, useState, use } from 'react';
import Link from 'next/link';
import StatusBadge from '@/components/StatusBadge';
import FieldEditor from '@/components/FieldEditor';
import { getDocument, reprocessDocument, DocumentResponse } from '@/lib/api';
import { ArrowLeft, RefreshCw, FileText, CheckCircle, AlertTriangle, Cpu, ListChecks } from 'lucide-react';

export default function DocumentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [doc, setDoc] = useState<DocumentResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'extracted' | 'raw' | 'logs'>('extracted');

  const fetchDetail = async () => {
    setLoading(true);
    try {
      const data = await getDocument(id);
      setDoc(data);
    } catch (err) {
      console.error('Failed to load detail', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDetail();
  }, [id]);

  const handleReprocess = async () => {
    if (confirm('Re-run extraction pipeline on this document?')) {
      await reprocessDocument(id);
      fetchDetail();
    }
  };

  if (loading) {
    return (
      <div className="p-12 text-center text-gray-400">
        <RefreshCw className="w-8 h-8 animate-spin mx-auto mb-2 text-blue-400" />
        <span>Loading document details...</span>
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="p-12 text-center">
        <h3 className="text-lg font-semibold text-gray-200">Document Not Found</h3>
        <Link href="/documents" className="text-blue-400 text-sm hover:underline mt-2 inline-block">
          &larr; Back to repository
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-gray-800 pb-4">
        <div>
          <Link
            href="/documents"
            className="inline-flex items-center space-x-1 text-xs text-gray-400 hover:text-gray-200 mb-2"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Back to documents</span>
          </Link>
          <div className="flex items-center space-x-3">
            <FileText className="w-6 h-6 text-blue-400" />
            <h1 className="text-xl font-bold text-white truncate max-w-md">
              {doc.original_filename}
            </h1>
            <StatusBadge status={doc.status} confidence={doc.confidence_score} />
          </div>
        </div>

        <div className="flex space-x-2">
          <button
            onClick={handleReprocess}
            className="inline-flex items-center space-x-1.5 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-medium rounded-lg border border-gray-700 transition-all"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Reprocess</span>
          </button>
        </div>
      </div>

      {/* Split-View Container */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left Panel: Raw Extracted Text / Metadata */}
        <div className="bg-[#111827] border border-gray-800 rounded-xl p-6 flex flex-col h-[700px]">
          <div className="flex items-center justify-between border-b border-gray-800 pb-3 mb-4">
            <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
              Document Text & Overview
            </h3>
            <span className="text-xs font-mono text-gray-500">
              Type: {doc.document_type || 'Unclassified'}
            </span>
          </div>

          <div className="flex-1 overflow-y-auto bg-[#0b0f19] p-4 rounded-lg border border-gray-800 font-mono text-xs text-gray-300 whitespace-pre-wrap">
            {doc.raw_text ? (
              doc.raw_text
            ) : (
              <span className="text-gray-600 italic">No text extracted yet or processing in progress...</span>
            )}
          </div>
        </div>

        {/* Right Panel: Structured Data & Human-in-the-Loop Review */}
        <div className="bg-[#111827] border border-gray-800 rounded-xl p-6 flex flex-col h-[700px]">
          {/* Tabs */}
          <div className="flex border-b border-gray-800 mb-4 space-x-4">
            <button
              onClick={() => setActiveTab('extracted')}
              className={`pb-2 text-xs font-semibold uppercase tracking-wider flex items-center space-x-1.5 border-b-2 transition-all ${
                activeTab === 'extracted'
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              <ListChecks className="w-4 h-4" />
              <span>Structured Data & Human Review</span>
            </button>
            <button
              onClick={() => setActiveTab('logs')}
              className={`pb-2 text-xs font-semibold uppercase tracking-wider flex items-center space-x-1.5 border-b-2 transition-all ${
                activeTab === 'logs'
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              <Cpu className="w-4 h-4" />
              <span>Pipeline Audit Logs ({doc.processing_logs.length})</span>
            </button>
          </div>

          {/* Tab Content */}
          <div className="flex-1 overflow-y-auto pr-1">
            {activeTab === 'extracted' && (
              <FieldEditor
                documentId={doc.id}
                extractedData={doc.extracted_data}
                onSaved={fetchDetail}
              />
            )}

            {activeTab === 'logs' && (
              <div className="space-y-3">
                {doc.processing_logs.map((log) => (
                  <div
                    key={log.id}
                    className="p-3 bg-[#192237] border border-gray-800 rounded-lg text-xs space-y-1 font-mono"
                  >
                    <div className="flex items-center justify-between text-gray-300 font-bold uppercase">
                      <span>Stage: {log.stage}</span>
                      <span
                        className={
                          log.status === 'completed'
                            ? 'text-emerald-400'
                            : log.status === 'failed'
                            ? 'text-rose-400'
                            : 'text-blue-400'
                        }
                      >
                        {log.status} ({log.duration_ms}ms)
                      </span>
                    </div>
                    {log.error_message && (
                      <p className="text-rose-400">{log.error_message}</p>
                    )}
                    {log.metadata_ && (
                      <pre className="text-[10px] text-gray-400 bg-[#0b0f19] p-2 rounded overflow-x-auto">
                        {JSON.stringify(log.metadata_, null, 2)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
