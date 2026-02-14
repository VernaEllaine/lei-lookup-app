import type { UploadResponse, RowResult, Summary, PaginatedResponse, ValidateLeiResponse } from './types';

const BASE = '/api';

export async function uploadCsv(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${BASE}/upload`, { method: 'POST', body: form });
  return resp.json();
}

export function startLookup(
  column: string,
  onProgress: (data: { index: number; total: number; company: string; row: RowResult; done: boolean }) => void,
  onSummary: (summary: Summary) => void,
  onError: (err: string) => void,
): () => void {
  const es = new EventSource(`${BASE}/lookup?column=${encodeURIComponent(column)}`);

  es.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.error) {
      onError(data.error);
      es.close();
      return;
    }
    onProgress(data);
    if (data.done) {
      // Summary event will follow
    }
  };

  es.addEventListener('summary', (event) => {
    const summary = JSON.parse((event as MessageEvent).data);
    onSummary(summary);
    es.close();
  });

  es.onerror = () => {
    es.close();
  };

  return () => es.close();
}

export async function getResultsPage(
  page: number = 1,
  pageSize: number = 50,
  status?: string,
): Promise<PaginatedResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (status) params.set('status', status);
  const resp = await fetch(`${BASE}/results?${params}`);
  return resp.json();
}

export async function getResults(): Promise<PaginatedResponse> {
  return getResultsPage(1, 50);
}

export async function updateCell(
  index: number,
  field: string,
  value: string,
): Promise<{ row: RowResult; summary: Summary }> {
  const resp = await fetch(`${BASE}/results/${index}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ field, value }),
  });
  return resp.json();
}

export async function validateLei(
  index: number,
): Promise<ValidateLeiResponse> {
  const resp = await fetch(`${BASE}/results/${index}/validate-lei`, {
    method: 'PUT',
  });
  return resp.json();
}

export async function confirmAll(): Promise<{ confirmed: number; summary: Summary }> {
  const resp = await fetch(`${BASE}/confirm-all`, { method: 'POST' });
  return resp.json();
}

export function exportCsv(): void {
  window.open(`${BASE}/export`, '_blank');
}

export async function clearCache(): Promise<{ message: string; summary: Summary }> {
  const resp = await fetch(`${BASE}/cache`, { method: 'DELETE' });
  return resp.json();
}
