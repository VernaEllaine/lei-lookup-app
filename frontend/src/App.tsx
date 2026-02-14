import { useState, useCallback, useRef } from 'react';
import type { RowResult, Summary } from './types';
import {
  uploadCsv,
  startLookup,
  getResults,
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

const STATUS_SORT_ORDER: Record<string, number> = {
  'AUTO-MATCHED': 0,
  'CONFIRMED': 1,
  'REVIEWED': 2,
  'REVIEW NEEDED': 3,
  'NO MATCH': 4,
};

function sortRows(rows: RowResult[]): RowResult[] {
  return [...rows].sort(
    (a, b) =>
      (STATUS_SORT_ORDER[a.match_status] ?? 5) -
      (STATUS_SORT_ORDER[b.match_status] ?? 5),
  );
}

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

  const closeRef = useRef<(() => void) | null>(null);

  const handleUpload = useCallback(async (file: File) => {
    const resp = await uploadCsv(file);
    setHeaders(resp.headers);
    setSelectedColumn(resp.detected_column || resp.headers[0] || '');
    setRows([]);
    setSummary(emptySummary);
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
        setRows((prev) => [...prev, data.row]);
      },
      (summaryData) => {
        setSummary(summaryData);
        setRunning(false);
        setStatusText('Done.');
        // Re-fetch sorted results
        getResults().then((r) => {
          setRows(r.rows);
          setSummary(r.summary);
        });
      },
      (err) => {
        setStatusText(`Error: ${err}`);
        setRunning(false);
      },
    );
    closeRef.current = close;
  }, [selectedColumn]);

  const handleCellSave = useCallback(
    async (index: number, field: string, value: string) => {
      const resp = await updateCell(index, field, value);

      if (field === 'lei') {
        // Validate the new LEI
        const validation = await validateLei(index);
        if (!validation.valid) {
          alert(validation.message);
          // Refresh to get reverted state
          const r = await getResults();
          setRows(r.rows);
          setSummary(r.summary);
          return;
        }
        if (validation.needs_confirmation) {
          const confirmed = window.confirm(validation.message + '\n\nDo you want to keep this LEI?');
          if (!confirmed) {
            // Revert: re-fetch
            const r = await getResults();
            setRows(r.rows);
            setSummary(r.summary);
            return;
          }
        }
      }

      // Refresh results
      const r = await getResults();
      setRows(r.rows);
      setSummary(r.summary);
    },
    [],
  );

  const handleConfirmAll = useCallback(async () => {
    const resp = await confirmAll();
    setSummary(resp.summary);
    const r = await getResults();
    setRows(r.rows);
    setSummary(r.summary);
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

  const displayRows = running ? rows : sortRows(rows);

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

      <ResultsTable rows={displayRows} onCellSave={handleCellSave} />
    </div>
  );
}
