import type {
  UploadResponse, RowResult, Summary, PaginatedResponse, ValidateLeiResponse,
  ValidationUploadResponse, ValidationSummary, ValidationPaginatedResponse,
} from './types';

const BASE = '/api';

export async function uploadCsv(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${BASE}/upload`, { method: 'POST', body: form });
  return resp.json();
}

export function startLookup(
  column: string,
  sessionId: string,
  onProgress: (data: { index: number; total: number; company: string; row: RowResult; done: boolean }) => void,
  onSummary: (summary: Summary) => void,
  onError: (err: string) => void,
): () => void {
  const es = new EventSource(
    `${BASE}/lookup?column=${encodeURIComponent(column)}&session_id=${encodeURIComponent(sessionId)}`
  );

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
  sessionId?: string,
): Promise<PaginatedResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (status) params.set('status', status);
  if (sessionId) params.set('session_id', sessionId);
  const resp = await fetch(`${BASE}/results?${params}`);
  return resp.json();
}

export async function getResults(sessionId?: string): Promise<PaginatedResponse> {
  return getResultsPage(1, 50, undefined, sessionId);
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

export async function confirmAll(sessionId?: string): Promise<{ confirmed: number; summary: Summary }> {
  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  const resp = await fetch(`${BASE}/confirm-all?${params}`, { method: 'POST' });
  return resp.json();
}

export function exportCsv(sessionId?: string): void {
  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  window.open(`${BASE}/export?${params}`, '_blank');
}

export function exportXlsx(sessionId?: string): void {
  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  window.open(`${BASE}/export-xlsx?${params}`, '_blank');
}

export async function clearCache(sessionId?: string): Promise<{ message: string; summary: Summary }> {
  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  const resp = await fetch(`${BASE}/cache?${params}`, { method: 'DELETE' });
  return resp.json();
}

// ---------------------------------------------------------------------------
// LEI Validation API
// ---------------------------------------------------------------------------

export async function uploadValidationCsv(file: File): Promise<ValidationUploadResponse> {
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${BASE}/validate/upload`, { method: 'POST', body: form });
  return resp.json();
}

export function startValidation(
  entityColumn: string,
  leiColumn: string,
  sessionId: string,
  onProgress: (data: { index: number; total: number; entity_name: string; done: boolean }) => void,
  onSummary: (summary: ValidationSummary) => void,
  onError: (err: string) => void,
): () => void {
  const params = new URLSearchParams({
    entity_column: entityColumn,
    lei_column: leiColumn,
    session_id: sessionId,
  });
  const es = new EventSource(`${BASE}/validate/run?${params}`);

  es.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.error) {
      onError(data.error);
      es.close();
      return;
    }
    onProgress(data);
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

export async function getValidationResultsPage(
  page: number = 1,
  pageSize: number = 50,
  flag?: string,
  sessionId?: string,
): Promise<ValidationPaginatedResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (flag) params.set('flag', flag);
  if (sessionId) params.set('session_id', sessionId);
  const resp = await fetch(`${BASE}/validate/results?${params}`);
  return resp.json();
}

export function exportValidationCsv(sessionId?: string): void {
  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  window.open(`${BASE}/validate/export?${params}`, '_blank');
}

export function exportValidationXlsx(sessionId?: string): void {
  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  window.open(`${BASE}/validate/export-xlsx?${params}`, '_blank');
}
