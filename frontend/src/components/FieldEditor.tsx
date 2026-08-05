'use client';

import { useState } from 'react';
import { Save, CheckCircle, AlertCircle, Loader2 } from 'lucide-react';
import { submitCorrections } from '@/lib/api';

interface FieldEditorProps {
  documentId: string;
  extractedData: Record<string, any> | null;
  onSaved?: () => void;
}

export default function FieldEditor({ documentId, extractedData, onSaved }: FieldEditorProps) {
  const [formData, setFormData] = useState<Record<string, string>>(() => {
    if (!extractedData) return {};
    const flat: Record<string, string> = {};
    for (const [k, v] of Object.entries(extractedData)) {
      if (typeof v === 'object' && v !== null) {
        flat[k] = JSON.stringify(v, null, 2);
      } else {
        flat[k] = v !== null && v !== undefined ? String(v) : '';
      }
    }
    return flat;
  });

  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const handleChange = (key: string, value: string) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const corrections = Object.entries(formData).map(([field_name, field_value]) => ({
        field_name,
        field_value,
      }));
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

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4">
        {Object.entries(formData).map(([key, val]) => (
          <div key={key} className="space-y-1">
            <label className="text-xs font-semibold uppercase tracking-wider text-gray-400">
              {key.replace(/_/g, ' ')}
            </label>
            {val.includes('\n') || val.length > 60 ? (
              <textarea
                value={val}
                onChange={(e) => handleChange(key, e.target.value)}
                rows={3}
                className="w-full bg-[#192237] border border-gray-700 rounded-lg p-2.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500 font-mono"
              />
            ) : (
              <input
                type="text"
                value={val}
                onChange={(e) => handleChange(key, e.target.value)}
                className="w-full bg-[#192237] border border-gray-700 rounded-lg p-2.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
              />
            )}
          </div>
        ))}
      </div>

      <div className="pt-4 border-t border-gray-800 flex items-center justify-between">
        {message && (
          <span className="text-xs text-emerald-400 font-medium">{message}</span>
        )}
        <button
          onClick={handleSave}
          disabled={saving}
          className="ml-auto px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-lg text-sm flex items-center space-x-2 shadow-lg shadow-blue-600/20 disabled:opacity-50 transition-all"
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
