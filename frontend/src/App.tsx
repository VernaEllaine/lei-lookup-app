import { useState, useCallback, useRef } from 'react';
import type { RowResult, Summary } from './types';
import {
  uploadCsv,
  startLookup,
  getResultsPage,
  updateCell,
  validateLei,
  confirmAll,
  exportCsv,
  clearCache,
} from './api';
import FileUpload from './components/FileUpload';
import ColumnPicker from './components/ColumnPicker';
import ProgressBar from './components/ProgressBar';
import ResultsTable from './components/ResultsTable';
import SummaryBar from './components/SummaryBar';

const PAGE_SIZE = 50;

const emptySummary: Summary = {
  auto_matched: 0,
  confirmed: 0,
  reviewed: 0,
  review_needed: 0,
  no_match: 0,
  errors: 0,
  total: 0,
  cache_size: 0,
};

export default function App() {
  const [headers, setHeaders] = useState<string[]>([]);
  const [selectedColumn, setSelectedColumn] = useState('');
  const [rows, setRows] = useState<RowResult[]>([]);
  const [summary, setSummary] = useState<Summary>(emptySummary);
  const [running, setRunning] = useState(false);
  const [progressCurrent, setProgressCurrent] = useState(0);
  const [progressTotal, setProgressTotal] = useState(0);
  const [statusText, setStatusText] = useState('');

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const [totalRows, setTotalRows] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string>('');

  const closeRef = useRef<(() => void) | null>(null);

  const fetchPage = useCallback(async (page: number, status?: string) => {
    const r = await getResultsPage(page, PAGE_SIZE, status || undefined);
    setRows(r.rows);
    setTotalRows(r.total);
    setCurrentPage(r.page);
    setSummary(r.summary);
  }, []);

  const handleUpload = useCallback(async (file: File) => {
    const resp = await uploadCsv(file);
    setHeaders(resp.headers);
    setSelectedColumn(resp.detected_column || resp.headers[0] || '');
    setRows([]);
    setSummary(emptySummary);
    setTotalRows(0);
    setCurrentPage(1);
    setStatusFilter('');
    setStatusText(`Loaded ${resp.row_count} rows`);
  }, []);

  const handleRun = useCallback(() => {
    if (!selectedColumn) return;
    setRunning(true);
    setRows([]);
    setProgressCurrent(0);
    setProgressTotal(0);
    setStatusText('Starting...');

    const close = startLookup(
      selectedColumn,
      (data) => {
        setProgressCurrent(data.index + 1);
        setProgressTotal(data.total);
        setStatusText(data.company ? `Looking up: ${data.company}` : 'Skipped empty row');
      },
      (summaryData) => {
        setSummary(summaryData);
        setRunning(false);
        setStatusText('Done.');
        // Fetch first page of sorted results
        fetchPage(1, statusFilter);
      },
      (err) => {
        setStatusText(`Error: ${err}`);
        setRunning(false);
      },
    );
    closeRef.current = close;
  }, [selectedColumn, fetchPage, statusFilter]);

  const handleCellSave = useCallback(
    async (index: number, field: string, value: string) => {
      const resp = await updateCell(index, field, value);

      if (field === 'lei') {
        const validation = await validateLei(index);
        if (!validation.valid) {
          alert(validation.message);
          fetchPage(currentPage, statusFilter);
          return;
        }
        if (validation.needs_confirmation) {
          const confirmed = window.confirm(validation.message + '\n\nDo you want to keep this LEI?');
          if (!confirmed) {
            fetchPage(currentPage, statusFilter);
            return;
          }
        }
        // Re-fetch to get validated data
        fetchPage(currentPage, statusFilter);
        return;
      }

      // Delta update: patch the single row in local state
      if (resp.row) {
        setRows((prev) =>
          prev.map((r) => (r.index === resp.row.index ? resp.row : r)),
        );
        setSummary(resp.summary);
      }
    },
    [currentPage, statusFilter, fetchPage],
  );

  const handleConfirmAll = useCallback(async () => {
    const resp = await confirmAll();
    setSummary(resp.summary);
    // Update local rows: REVIEWED -> CONFIRMED
    setRows((prev) =>
      prev.map((r) =>
        r.match_status === 'REVIEWED' ? { ...r, match_status: 'CONFIRMED' } : r,
      ),
    );
  }, []);

  const handleExport = useCallback(() => {
    exportCsv();
  }, []);

  const handleClearCache = useCallback(async () => {
    if (!window.confirm('Delete all cached entities?')) return;
    const resp = await clearCache();
    setSummary(resp.summary);
    setStatusText(resp.message);
  }, []);

  const handlePageChange = useCallback(
    (page: number) => {
      fetchPage(page, statusFilter);
    },
    [fetchPage, statusFilter],
  );

  const handleStatusFilterChange = useCallback(
    (newStatus: string) => {
      setStatusFilter(newStatus);
      fetchPage(1, newStatus);
    },
    [fetchPage],
  );

  const totalPages = Math.max(1, Math.ceil(totalRows / PAGE_SIZE));

  return (
    <div className="app">
      <h1>LEI Lookup</h1>

      <FileUpload onUpload={handleUpload} disabled={running} />

      <ColumnPicker
        headers={headers}
        selected={selectedColumn}
        onChange={setSelectedColumn}
        onRun={handleRun}
        disabled={running || headers.length === 0}
      />

      <ProgressBar
        current={progressCurrent}
        total={progressTotal}
        statusText={statusText}
      />

      <SummaryBar
        summary={summary}
        onConfirmAll={handleConfirmAll}
        onExport={handleExport}
        onClearCache={handleClearCache}
        disabled={running}
      />

      {rows.length > 0 && !running && (
        <div className="table-controls">
          <div className="status-filter">
            <label>Filter: </label>
            <select
              value={statusFilter}
              onChange={(e) => handleStatusFilterChange(e.target.value)}
            >
              <option value="">All</option>
              <option value="AUTO-MATCHED">Auto-Matched</option>
              <option value="CONFIRMED">Confirmed</option>
              <option value="REVIEWED">Reviewed</option>
              <option value="REVIEW NEEDED">Review Needed</option>
              <option value="NO MATCH">No Match</option>
            </select>
          </div>

          <div className="pagination">
            <button
              className="btn"
              onClick={() => handlePageChange(currentPage - 1)}
              disabled={currentPage <= 1}
            >
              Prev
            </button>
            <span className="page-indicator">
              Page {currentPage} of {totalPages} ({totalRows} rows)
            </span>
            <button
              className="btn"
              onClick={() => handlePageChange(currentPage + 1)}
              disabled={currentPage >= totalPages}
            >
              Next
            </button>
          </div>
        </div>
      )}

      <ResultsTable rows={rows} onCellSave={handleCellSave} />

      {rows.length > 0 && !running && totalPages > 1 && (
        <div className="pagination pagination-bottom">
          <button
            className="btn"
            onClick={() => handlePageChange(currentPage - 1)}
            disabled={currentPage <= 1}
          >
            Prev
          </button>
          <span className="page-indicator">
            Page {currentPage} of {totalPages}
          </span>
          <button
            className="btn"
            onClick={() => handlePageChange(currentPage + 1)}
            disabled={currentPage >= totalPages}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
