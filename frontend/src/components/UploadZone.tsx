'use client';

import { useState, useRef } from 'react';
import { UploadCloud, File, X, CheckCircle, AlertCircle, Loader2 } from 'lucide-react';
import { uploadFiles } from '@/lib/api';

interface UploadZoneProps {
  onUploadSuccess?: () => void;
}

export default function UploadZone({ onUploadSuccess }: UploadZoneProps) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const filesArray = Array.from(e.target.files);
      setSelectedFiles((prev) => [...prev, ...filesArray]);
      setError(null);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files) {
      const filesArray = Array.from(e.dataTransfer.files);
      setSelectedFiles((prev) => [...prev, ...filesArray]);
      setError(null);
    }
  };

  const removeFile = (index: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) return;
    setIsUploading(true);
    setError(null);
    setSuccessMessage(null);

    try {
      await uploadFiles(selectedFiles);
      setSuccessMessage(`Successfully uploaded ${selectedFiles.length} file(s). Processing has started.`);
      setSelectedFiles([]);
      if (onUploadSuccess) onUploadSuccess();
    } catch (err: any) {
      setError(err.message || 'Upload failed. Please check server connection.');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="bg-[#111827] border border-gray-800 rounded-xl p-6 shadow-xl">
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className="border-2 border-dashed border-gray-700 hover:border-blue-500/50 rounded-xl p-8 text-center cursor-pointer transition-all bg-[#0d1322]/50 hover:bg-blue-500/[0.02]"
      >
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileChange}
          multiple
          accept=".pdf,.png,.jpg,.jpeg,.tiff,.docx,.csv,.xlsx,.txt"
          className="hidden"
        />
        <div className="w-14 h-14 bg-blue-600/10 text-blue-400 rounded-full flex items-center justify-center mx-auto mb-4 border border-blue-500/20">
          <UploadCloud className="w-7 h-7" />
        </div>
        <h3 className="text-lg font-semibold text-gray-200">
          Drop files here or click to browse
        </h3>
        <p className="text-xs text-gray-400 mt-2">
          Supports PDF, Images (PNG/JPG), DOCX, CSV, Excel, TXT (Up to 50MB)
        </p>
      </div>

      {selectedFiles.length > 0 && (
        <div className="mt-6 space-y-3">
          <div className="flex items-center justify-between text-xs font-semibold text-gray-400 uppercase tracking-wider">
            <span>Selected Files ({selectedFiles.length})</span>
            <button
              onClick={() => setSelectedFiles([])}
              className="text-gray-400 hover:text-gray-200"
            >
              Clear all
            </button>
          </div>
          <div className="max-h-40 overflow-y-auto space-y-2 pr-1">
            {selectedFiles.map((file, index) => (
              <div
                key={index}
                className="flex items-center justify-between bg-[#192237] p-2.5 rounded-lg border border-gray-800 text-sm"
              >
                <div className="flex items-center space-x-3 truncate">
                  <File className="w-4 h-4 text-blue-400 flex-shrink-0" />
                  <span className="truncate text-gray-200 font-medium">{file.name}</span>
                  <span className="text-xs text-gray-500">({(file.size / 1024 / 1024).toFixed(2)} MB)</span>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    removeFile(index);
                  }}
                  className="text-gray-400 hover:text-rose-400 p-1"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>

          <button
            onClick={handleUpload}
            disabled={isUploading}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-lg shadow-lg shadow-blue-600/20 transition-all flex items-center justify-center space-x-2 disabled:opacity-50"
          >
            {isUploading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Uploading & Processing...</span>
              </>
            ) : (
              <span>Start Processing ({selectedFiles.length} files)</span>
            )}
          </button>
        </div>
      )}

      {error && (
        <div className="mt-4 p-3 bg-rose-500/10 border border-rose-500/20 rounded-lg text-rose-400 text-sm flex items-center space-x-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {successMessage && (
        <div className="mt-4 p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-lg text-emerald-400 text-sm flex items-center space-x-2">
          <CheckCircle className="w-4 h-4 flex-shrink-0" />
          <span>{successMessage}</span>
        </div>
      )}
    </div>
  );
}
