/**
 * API Client for backend REST endpoints.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface DocumentListItem {
  id: string;
  original_filename: string;
  mime_type: string | null;
  file_size_bytes: number | null;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'needs_review';
  document_type: string | null;
  confidence_score: number | null;
  created_at: string;
  processed_at: string | null;
}

export interface ExtractedField {
  id: string;
  field_name: string;
  field_value: string | null;
  field_type: string | null;
  confidence: number | null;
  human_verified: boolean;
  source_location: string | null;
}

export interface ProcessingLog {
  id: string;
  stage: 'classify' | 'parse' | 'extract' | 'validate';
  status: 'started' | 'completed' | 'failed' | 'skipped';
  duration_ms: number | null;
  metadata_: Record<string, any> | null;
  error_message: string | null;
  reasoning: string | null;
  agent_steps: any[] | null;
  created_at: string;
}

export interface DocumentResponse {
  id: string;
  original_filename: string;
  mime_type: string | null;
  file_size_bytes: number | null;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'needs_review';
  document_type: string | null;
  confidence_score: number | null;
  raw_text: string | null;
  extracted_data: Record<string, any> | null;
  created_at: string;
  updated_at: string;
  processed_at: string | null;
  extracted_fields: ExtractedField[];
  processing_logs: ProcessingLog[];
}

export interface DocumentListResponse {
  documents: DocumentListItem[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface SearchResult {
  id: string;
  original_filename: string;
  document_type: string | null;
  status: string;
  confidence_score: number | null;
  relevance_score: number | null;
  headline: string | null;
  created_at: string;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
  page: number;
  per_page: number;
}

export interface DashboardStats {
  total_documents: number;
  completed: number;
  processing: number;
  needs_review: number;
  failed: number;
  by_type: Record<string, number>;
  avg_confidence: number | null;
}

export async function uploadFiles(files: File[]) {
  const formData = new FormData();
  files.forEach((f) => formData.append('files', f));

  const res = await fetch(`${API_BASE}/api/documents/upload`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) throw new Error('Upload failed');
  return res.json();
}

export async function getDocuments(page = 1, perPage = 20, status?: string, type?: string): Promise<DocumentListResponse> {
  const params = new URLSearchParams({
    page: page.toString(),
    per_page: perPage.toString(),
  });
  if (status) params.append('status', status);
  if (type) params.append('type', type);

  const res = await fetch(`${API_BASE}/api/documents?${params.toString()}`);
  if (!res.ok) throw new Error('Failed to fetch documents');
  return res.json();
}

export async function getDocument(id: string): Promise<DocumentResponse> {
  const res = await fetch(`${API_BASE}/api/documents/${id}`);
  if (!res.ok) throw new Error('Failed to fetch document detail');
  return res.json();
}

export async function submitCorrections(id: string, corrections: { field_name: string; field_value: string }[]) {
  const res = await fetch(`${API_BASE}/api/documents/${id}/fields`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ corrections }),
  });
  if (!res.ok) throw new Error('Failed to submit corrections');
  return res.json();
}

export async function reprocessDocument(id: string) {
  const res = await fetch(`${API_BASE}/api/documents/${id}/reprocess`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error('Failed to reprocess document');
  return res.json();
}

export async function deleteDocument(id: string) {
  const res = await fetch(`${API_BASE}/api/documents/${id}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete document');
}

export async function searchDocuments(query: string, type?: string, status?: string): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query });
  if (type) params.append('type', type);
  if (status) params.append('status', status);

  const res = await fetch(`${API_BASE}/api/search?${params.toString()}`);
  if (!res.ok) throw new Error('Search request failed');
  return res.json();
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const res = await fetch(`${API_BASE}/api/documents/stats/dashboard`);
  if (!res.ok) throw new Error('Failed to fetch stats');
  return res.json();
}
