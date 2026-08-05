'use client';

import Link from 'next/link';
import { FileText, Eye, RefreshCw, Trash2, ArrowRight } from 'lucide-react';
import StatusBadge from './StatusBadge';
import { DocumentListItem } from '@/lib/api';

interface DocumentTableProps {
  documents: DocumentListItem[];
  onRefresh?: () => void;
  onDelete?: (id: string) => void;
}

export default function DocumentTable({ documents, onRefresh, onDelete }: DocumentTableProps) {
  if (documents.length === 0) {
    return (
      <div className="bg-[#111827] border border-gray-800 rounded-xl p-12 text-center">
        <FileText className="w-12 h-12 text-gray-600 mx-auto mb-3" />
        <h4 className="text-lg font-semibold text-gray-300">No documents found</h4>
        <p className="text-sm text-gray-500 mt-1">Upload your first document above to get started.</p>
      </div>
    );
  }

  return (
    <div className="bg-[#111827] border border-gray-800 rounded-xl overflow-hidden shadow-xl">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm text-gray-300">
          <thead className="bg-[#192237] text-xs uppercase font-semibold text-gray-400 border-b border-gray-800">
            <tr>
              <th className="px-6 py-4">Document</th>
              <th className="px-6 py-4">Type</th>
              <th className="px-6 py-4">Status</th>
              <th className="px-6 py-4">Confidence</th>
              <th className="px-6 py-4">Uploaded</th>
              <th className="px-6 py-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {documents.map((doc) => (
              <tr key={doc.id} className="hover:bg-[#161e2e]/50 transition-colors">
                <td className="px-6 py-4 font-medium text-gray-200">
                  <div className="flex items-center space-x-3">
                    <FileText className="w-4 h-4 text-blue-400 flex-shrink-0" />
                    <span className="truncate max-w-xs">{doc.original_filename}</span>
                  </div>
                </td>
                <td className="px-6 py-4">
                  <span className="capitalize px-2.5 py-0.5 rounded text-xs bg-gray-800 text-gray-300 border border-gray-700 font-mono">
                    {doc.document_type || 'Unclassified'}
                  </span>
                </td>
                <td className="px-6 py-4">
                  <StatusBadge status={doc.status} confidence={doc.confidence_score} />
                </td>
                <td className="px-6 py-4 font-mono text-xs">
                  {doc.confidence_score !== null ? (
                    <span
                      className={
                        doc.confidence_score >= 0.8
                          ? 'text-emerald-400'
                          : doc.confidence_score >= 0.5
                          ? 'text-amber-400'
                          : 'text-rose-400'
                      }
                    >
                      {(doc.confidence_score * 100).toFixed(0)}%
                    </span>
                  ) : (
                    <span className="text-gray-500">—</span>
                  )}
                </td>
                <td className="px-6 py-4 text-gray-400 text-xs">
                  {new Date(doc.created_at).toLocaleString()}
                </td>
                <td className="px-6 py-4 text-right space-x-2">
                  <Link
                    href={`/documents/${doc.id}`}
                    className="inline-flex items-center space-x-1 text-xs text-blue-400 hover:text-blue-300 font-medium px-2.5 py-1 rounded bg-blue-500/10 hover:bg-blue-500/20 border border-blue-500/20 transition-all"
                  >
                    <Eye className="w-3.5 h-3.5" />
                    <span>View / Review</span>
                  </Link>
                  {onDelete && (
                    <button
                      onClick={() => onDelete(doc.id)}
                      className="inline-flex items-center text-xs text-gray-500 hover:text-rose-400 p-1 rounded hover:bg-rose-500/10 transition-all"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
