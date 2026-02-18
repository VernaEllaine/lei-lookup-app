import { useState, useCallback, useRef } from 'react';
import type { ValidationRowResult, ValidationSummary } from '../types';
import {
  uploadValidationCsv,
  startValidation,
  getValidationResultsPage,
  exportValidationCsv,
  exportValidationXlsx,
  acceptSuggestedLei,
} from '../api';
import FileUpload from './FileUpload';
import ValidationColumnPicker from './ValidationColumnPicker';
import ProgressBar from './ProgressBar';
import ValidationResultsTable from './ValidationResultsTable';
import ValidationSummaryBar from './ValidationSummaryBar';

const DEFAULT_PAGE_SIZE = 100;
const PAGE_SIZE_OPTIONS = [50, 100, 250, 500, 0];

const emptySummary: ValidationSummary = {
  ok: 0, lapsed: 0, invalid: 0, not_found: 0, errors: 0, total: 0,
};

export default function ValidationSection() {
  const [headers, setHeaders] = useState<string[]>([]);
  const [entityColumn, setEntityColumn] = useState('');
  const [leiColumn, setLeiColumn] = useState('');
  const [rows, setRows] = useState<ValidationRowResult[]>([]);
  const [summary, setSummary] = useState<ValidationSummary>(emptySummary);
  const [running, setRunning] = useState(false);
  const [progressCurrent, setProgressCurrent] = useState(0);
  const [progressTotal, setProgressTotal] = useState(0);
  const [statusText, setStatusText] = useState('');
  const [sessionId, setSessionId] = useState('');

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [totalRows, setTotalRows] = useState(0);
  const [flagFilter, setFlagFilter] = useState('');

  const closeRef = useRef<(() => void) | null>(null);
  const sessionIdRef = useRef('');

  const fetchPage = useCallback(async (page: number, size: number, flag?: string) => {
    const effectiveSize = size === 0 ? 100000 : size;
    const r = await getValidationResultsPage(page, effectiveSize, flag || undefined, sessionIdRef.current);
    setRows(r.rows);
    setTotalRows(r.total);
    setCurrentPage(r.page);
    setSummary(r.summary);
  }, []);

  const handleUpload = useCallback(async (file: File) => {
    const resp = await uploadValidationCsv(file);
    setSessionId(resp.session_id);
    sessionIdRef.current = resp.session_id;
    setHeaders(resp.headers);
    setEntityColumn(resp.detected_entity_column || resp.headers[0] || '');
    setLeiColumn(resp.detected_lei_column || resp.headers[1] || '');
    setRows([]);
    setSummary(emptySummary);
    setTotalRows(0);
    setCurrentPage(1);
    setFlagFilter('');
    setStatusText(`Loaded ${resp.row_count} rows`);
  }, []);

  const handleRun = useCallback(() => {
    if (!entityColumn || !leiColumn || !sessionId) return;
    setRunning(true);
    setRows([]);
    setProgressCurrent(0);
    setProgressTotal(0);
    setStatusText('Starting validation...');

    const close = startValidation(
      entityColumn,
      leiColumn,
      sessionId,
      (data) => {
        setProgressCurrent((prev) => prev + 1);
        setProgressTotal(data.total);
        setStatusText(data.entity_name ? `Validating: ${data.entity_name}` : 'Validating...');
      },
      (summaryData) => {
        setSummary(summaryData);
        setRunning(false);
        setStatusText('Validation complete.');
        fetchPage(1, pageSize, flagFilter);
      },
      (err) => {
        setStatusText(`Error: ${err}`);
        setRunning(false);
      },
    );
    closeRef.current = close;
  }, [entityColumn, leiColumn, sessionId, fetchPage, pageSize, flagFilter]);

  const handleAccept = useCallback(async (rowIndex: number) => {
    const resp = await acceptSuggestedLei(sessionId, rowIndex);
    setSummary(resp.summary);
    await fetchPage(currentPage, pageSize, flagFilter);
  }, [sessionId, fetchPage, currentPage, pageSize, flagFilter]);

  const handleExportCsv = useCallback(() => {
    exportValidationCsv(sessionId);
  }, [sessionId]);

  const handleExportXlsx = useCallback(() => {
    exportValidationXlsx(sessionId);
  }, [sessionId]);

  const handlePageChange = useCallback(
    (page: number) => { fetchPage(page, pageSize, flagFilter); },
    [fetchPage, pageSize, flagFilter],
  );

  const handlePageSizeChange = useCallback(
    (newSize: number) => {
      setPageSize(newSize);
      setCurrentPage(1);
      fetchPage(1, newSize, flagFilter);
    },
    [fetchPage, flagFilter],
  );

  const handleFlagFilterChange = useCallback(
    (newFlag: string) => {
      setFlagFilter(newFlag);
      setCurrentPage(1);
      fetchPage(1, pageSize, newFlag);
    },
    [fetchPage, pageSize],
  );

  const effectivePageSize = pageSize === 0 ? totalRows : pageSize;
  const totalPages = effectivePageSize > 0 ? Math.max(1, Math.ceil(totalRows / effectivePageSize)) : 1;
  const showPagination = pageSize !== 0;

  return (
    <>
      <FileUpload onUpload={handleUpload} disabled={running} />

      <ValidationColumnPicker
        headers={headers}
        entityColumn={entityColumn}
        leiColumn={leiColumn}
        onEntityChange={setEntityColumn}
        onLeiChange={setLeiColumn}
        onRun={handleRun}
        disabled={running || headers.length === 0}
      />

      <ProgressBar
        current={progressCurrent}
        total={progressTotal}
        statusText={statusText}
      />

      <ValidationSummaryBar
        summary={summary}
        onExportCsv={handleExportCsv}
        onExportXlsx={handleExportXlsx}
        disabled={running}
      />

      {rows.length > 0 && !running && (
        <div className="table-controls">
          <div className="status-filter">
            <label>Filter: </label>
            <select
              value={flagFilter}
              onChange={(e) => handleFlagFilterChange(e.target.value)}
            >
              <option value="">All</option>
              <option value="OK">OK</option>
              <option value="LAPSED">Lapsed</option>
              <option value="INVALID">Invalid</option>
              <option value="NOT_FOUND">Not Found</option>
              <option value="ERROR">Error</option>
            </select>
          </div>

          <div className="page-size-picker">
            <label>Rows: </label>
            <select
              value={pageSize}
              onChange={(e) => handlePageSizeChange(Number(e.target.value))}
            >
              {PAGE_SIZE_OPTIONS.map((opt) => (
                <option key={opt} value={opt}>
                  {opt === 0 ? 'All' : opt}
                </option>
              ))}
            </select>
          </div>

          {showPagination && (
            <div className="pagination">
              <button className="btn" onClick={() => handlePageChange(currentPage - 1)} disabled={currentPage <= 1}>
                Prev
              </button>
              <span className="page-indicator">
                Page {currentPage} of {totalPages} ({totalRows} rows)
              </span>
              <button className="btn" onClick={() => handlePageChange(currentPage + 1)} disabled={currentPage >= totalPages}>
                Next
              </button>
            </div>
          )}

          {!showPagination && (
            <span className="page-indicator">{totalRows} rows</span>
          )}
        </div>
      )}

      <ValidationResultsTable rows={rows} onAccept={handleAccept} />

      {rows.length > 0 && !running && showPagination && totalPages > 1 && (
        <div className="pagination pagination-bottom">
          <button className="btn" onClick={() => handlePageChange(currentPage - 1)} disabled={currentPage <= 1}>
            Prev
          </button>
          <span className="page-indicator">
            Page {currentPage} of {totalPages}
          </span>
          <button className="btn" onClick={() => handlePageChange(currentPage + 1)} disabled={currentPage >= totalPages}>
            Next
          </button>
        </div>
      )}
    </>
  );
}
