'use client';

import { useState } from 'react';
import { Save, Loader2 } from 'lucide-react';
import { submitCorrections } from '@/lib/api';

interface FieldEditorProps {
  documentId: string;
  extractedData: Record<string, any> | null;
  onSaved?: () => void;
}

export default function FieldEditor({ documentId, extractedData, onSaved }: FieldEditorProps) {
  const [formData, setFormData] = useState<Record<string, any>>(() => {
    if (!extractedData) return {};
    return JSON.parse(JSON.stringify(extractedData));
  });

  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const handleChange = (path: (string | number)[], value: string) => {
    setFormData((prev) => {
      const next = JSON.parse(JSON.stringify(prev)); // Deep clone for safety
      let current = next;
      for (let i = 0; i < path.length - 1; i++) {
        current = current[path[i]];
      }
      current[path[path.length - 1]] = value;
      return next;
    });
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const corrections = Object.entries(formData).map(([field_name, field_value]) => {
        const valStr = typeof field_value === 'object' && field_value !== null 
          ? JSON.stringify(field_value) 
          : String(field_value);
        return { field_name, field_value: valStr };
      });
      
      await submitCorrections(documentId, corrections);
      setMessage('Corrections saved successfully!');
      if (onSaved) onSaved();
    } catch (err: any) {
      setMessage(`Error: ${err.message || 'Failed to save corrections'}`);
    } finally {
      setSaving(false);
    }
  };

  if (!extractedData || Object.keys(extractedData).length === 0) {
    return (
      <div className="p-6 text-center text-gray-500">
        No structured data extracted for this document.
      </div>
    );
  }

  // Recursive component to render the hierarchy
  const renderNode = (key: string | number, value: any, path: (string | number)[] = []) => {
    const currentPath = [...path, key];
    const isObject = typeof value === 'object' && value !== null && !Array.isArray(value);
    const isArray = Array.isArray(value);

    // Render Arrays
    if (isArray) {
      return (
        <div key={currentPath.join('.')} className="mb-4 bg-[#1e293b] border border-gray-700 rounded-xl overflow-hidden shadow-sm">
          <div className="px-4 py-3 bg-[#192237] border-b border-gray-700 flex items-center">
             <span className="text-xs font-bold uppercase tracking-wider text-gray-300">
               {String(key).replace(/_/g, ' ')} <span className="text-gray-500 normal-case">({value.length} items)</span>
             </span>
          </div>
          <div className="p-4 space-y-4">
            {value.map((item: any, idx: number) => (
              <div key={idx} className="border-l-2 border-gray-600 pl-4 py-2">
                <span className="text-[10px] font-mono text-gray-500 uppercase tracking-wider block mb-2">Item {idx + 1}</span>
                {typeof item === 'object' && item !== null ? (
                  Object.entries(item).map(([k, v]) => renderNode(k, v, [...currentPath, idx]))
                ) : (
                  renderNode(idx, item, currentPath)
                )}
              </div>
            ))}
          </div>
        </div>
      );
    }

    // Render Objects
    if (isObject) {
      return (
        <div key={currentPath.join('.')} className="mb-4 bg-[#1e293b] border border-gray-700 rounded-xl overflow-hidden shadow-sm">
          <div className="px-4 py-3 bg-[#192237] border-b border-gray-700 flex items-center">
             <span className="text-xs font-bold uppercase tracking-wider text-gray-300">
               {String(key).replace(/_/g, ' ')}
             </span>
          </div>
          <div className="p-4 space-y-4">
            {Object.entries(value).map(([k, v]) => renderNode(k, v, currentPath))}
          </div>
        </div>
      );
    }

    // Render Primitives
    const val = value !== null && value !== undefined ? String(value) : '';
    
    return (
      <div key={currentPath.join('.')} className="flex flex-col space-y-1.5 mb-3">
        <label className="text-xs font-semibold uppercase tracking-wider text-gray-400">
          {String(key).replace(/_/g, ' ')}
        </label>
        {val.includes('\\n') || val.length > 60 ? (
          <textarea
            value={val}
            onChange={(e) => handleChange(currentPath, e.target.value)}
            rows={4}
            className="w-full bg-[#0f172a] border border-gray-700 rounded-lg p-2.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500 font-mono transition-colors resize-y"
          />
        ) : (
          <input
            type="text"
            value={val}
            onChange={(e) => handleChange(currentPath, e.target.value)}
            className="w-full bg-[#0f172a] border border-gray-700 rounded-lg p-2.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500 transition-colors"
          />
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4 pb-16 relative h-full">
      <div className="space-y-4">
        {Object.entries(formData).map(([k, v]) => renderNode(k, v))}
      </div>

      <div className="pt-4 border-t border-gray-800 flex items-center justify-between sticky bottom-0 bg-[#111827] pb-4 z-10">
        {message && (
          <span className="text-xs text-emerald-400 font-medium">{message}</span>
        )}
        <button
          onClick={handleSave}
          disabled={saving}
          className="ml-auto px-5 py-2.5 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-lg text-sm flex items-center space-x-2 shadow-lg shadow-blue-600/20 disabled:opacity-50 transition-all"
        >
          {saving ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          <span>Save Field Corrections</span>
        </button>
      </div>
    </div>
  );
}
