import { useState, useCallback, useRef, useEffect } from 'react';
import * as XLSX from 'xlsx';
import type { IsinResult } from '../types';
import { lookupIsins, exportIsinXlsx } from '../api';
import FileUpload from './FileUpload';
import ColumnPicker from './ColumnPicker';
import ProgressBar from './ProgressBar';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const BATCH_SIZE = 5;

const FLAG_COLORS: Record<string, string> = {
  ACTIVE: '#eafaf1',
  INACTIVE: '#fdf2f1',
  ANNULLED: '#fdf2f1',
};

function statusColor(entityStatus: string): string {
  return FLAG_COLORS[entityStatus] ?? '#fff';
}

/** Minimal RFC 4180 CSV parser — returns {headers, rows}. */
function parseCSV(text: string): { headers: string[]; rows: string[][] } {
  const firstLine = text.split('\n')[0] ?? '';
  const commas = (firstLine.match(/,/g) ?? []).length;
  const semis = (firstLine.match(/;/g) ?? []).length;
  const tabs = (firstLine.match(/\t/g) ?? []).length;
  const delim = semis > commas ? ';' : tabs > commas ? '\t' : ',';

  const lines = text.replace(/\r\n?/g, '\n').trimEnd().split('\n');
  const parse = (line: string): string[] => {
    const cells: string[] = [];
    let cur = '';
    let inQuote = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (inQuote) {
        if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (ch === '"') { inQuote = false; }
        else { cur += ch; }
      } else {
        if (ch === '"') { inQuote = true; }
        else if (ch === delim) { cells.push(cur.trim()); cur = ''; }
        else { cur += ch; }
      }
    }
    cells.push(cur.trim());
    return cells;
  };

  const [headerLine, ...dataLines] = lines;
  const headers = parse(headerLine ?? '');
  const rows = dataLines.filter(Boolean).map(parse);
  return { headers, rows };
}

/** Guess which column holds ISIN codes. */
function detectIsinColumn(headers: string[], rows: string[][]): string {
  const isinPattern = /\bisin\b/i;
  const byName = headers.find((h) => isinPattern.test(h));
  if (byName) return byName;
  const isinRegex = /^[A-Z]{2}[A-Z0-9]{10}$/;
  for (let col = 0; col < headers.length; col++) {
    const sample = rows.slice(0, 5).map((r) => r[col] ?? '');
    if (sample.some((v) => isinRegex.test(v.trim()))) return headers[col];
  }
  return headers[0] ?? '';
}

/** Trigger a browser CSV download from a string. */
function downloadCsv(content: string, filename: string): void {
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function resultsToCsv(results: IsinResult[]): string {
  const header = 'ISIN,LEI,Legal Entity Name,Country,Entity Status,Registration Status,Error';
  const escape = (v: string) => `"${v.replace(/"/g, '""')}"`;
  const rows = results.map((r) =>
    [r.isin, r.lei, r.legal_name, r.country, r.entity_status, r.registration_status, r.error]
      .map(escape)
      .join(','),
  );
  return [header, ...rows].join('\r\n');
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type Mode = 'manual' | 'csv';

const MANUAL_EXAMPLE = 'US0378331005\nDE0007164600\nGB0002634946';

export default function IsinLookupSection() {
  const [mode, setMode] = useState<Mode>('csv');

  // Manual mode
  const [input, setInput] = useState('');

  // CSV mode
  const [csvHeaders, setCsvHeaders] = useState<string[]>([]);
  const [csvRows, setCsvRows] = useState<string[][]>([]);
  const [isinColumn, setIsinColumn] = useState('');

  // Shared
  const [results, setResults] = useState<IsinResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState({ current: 0, total: 0 });
  const [statusText, setStatusText] = useState('');
  const [error, setError] = useState('');

  // -------------------------------------------------------------------------
  // File loading (CSV or XLSX)
  // -------------------------------------------------------------------------

  const handleFileUpload = useCallback((file: File) => {
    const reader = new FileReader();
    const isXlsx = file.name.toLowerCase().endsWith('.xlsx');

    reader.onload = (ev) => {
      let headers: string[];
      let rows: string[][];

      if (isXlsx) {
        const data = new Uint8Array(ev.target?.result as ArrayBuffer);
        const wb = XLSX.read(data, { type: 'array' });
        const ws = wb.Sheets[wb.SheetNames[0]];
        const sheet: string[][] = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '' });
        headers = (sheet[0] ?? []).map(String);
        rows = sheet.slice(1).map((r) => r.map(String));
      } else {
        const text = (ev.target?.result as string) ?? '';
        ({ headers, rows } = parseCSV(text));
      }

      setCsvHeaders(headers);
      setCsvRows(rows);
      setIsinColumn(detectIsinColumn(headers, rows));
      setResults([]);
      setError('');
      setProgress({ current: 0, total: 0 });
      setStatusText(`Loaded ${rows.length} rows`);
    };

    if (isXlsx) {
      reader.readAsArrayBuffer(file);
    } else {
      reader.readAsText(file, 'utf-8');
    }
  }, []);

  // -------------------------------------------------------------------------
  // Lookup logic (shared by both modes)
  // -------------------------------------------------------------------------

  const runLookup = useCallback(async (isins: string[]) => {
    const unique = [...new Set(isins.map((s) => s.trim().toUpperCase()).filter(Boolean))];
    if (!unique.length) { setError('No ISIN codes found.'); return; }

    setLoading(true);
    setError('');
    setResults([]);
    setProgress({ current: 0, total: unique.length });
    setStatusText('Starting…');

    const accumulated: IsinResult[] = [];
    for (let i = 0; i < unique.length; i += BATCH_SIZE) {
      const batch = unique.slice(i, i + BATCH_SIZE);
      try {
        const resp = await lookupIsins(batch.join(','));
        accumulated.push(...resp.results);
      } catch {
        batch.forEach((isin) =>
          accumulated.push({
            isin, lei: '', legal_name: '', country: '',
            entity_status: '', registration_status: '',
            error: 'Request failed',
          }),
        );
      }
      const current = Math.min(i + BATCH_SIZE, unique.length);
      setProgress({ current, total: unique.length });
      setStatusText(`Looking up ISIN ${current} of ${unique.length}…`);
      setResults([...accumulated]);
    }

    setLoading(false);
    setStatusText('Done.');
    if (accumulated.length === 0) setError('No results found.');
  }, []);

  const handleManualLookup = useCallback(() => {
    const isins = input.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
    runLookup(isins);
  }, [input, runLookup]);

  const handleCsvLookup = useCallback(() => {
    const colIndex = csvHeaders.indexOf(isinColumn);
    if (colIndex === -1) { setError('Selected column not found.'); return; }
    const isins = csvRows.map((r) => r[colIndex] ?? '').filter(Boolean);
    runLookup(isins);
  }, [csvHeaders, csvRows, isinColumn, runLookup]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleManualLookup();
    },
    [handleManualLookup],
  );

  // -------------------------------------------------------------------------
  // Export
  // -------------------------------------------------------------------------

  const handleExportCsv = useCallback(() => {
    downloadCsv(resultsToCsv(results), 'isin_lookup_results.csv');
  }, [results]);

  const handleExportXlsx = useCallback(() => {
    exportIsinXlsx(results);
  }, [results]);

  const [exportOpen, setExportOpen] = useState(false);
  const exportDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (exportDropdownRef.current && !exportDropdownRef.current.contains(e.target as Node)) {
        setExportOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const found = results.filter((r) => !r.error);
  const notFound = results.filter((r) => r.error);

  return (
    <>

      {/* Mode toggle */}
      <div className="isin-mode-toggle">
        <button
          className={`isin-mode-btn${mode === 'csv' ? ' active' : ''}`}
          onClick={() => setMode('csv')}
        >
          CSV Import
        </button>
        <button
          className={`isin-mode-btn${mode === 'manual' ? ' active' : ''}`}
          onClick={() => setMode('manual')}
        >
          Manual Input
        </button>
      </div>

      {/* Manual mode */}
      {mode === 'manual' && (
        <div className="manual-section">
          <p className="isin-desc">
            Enter one or more ISIN codes (one per line or comma-separated).
            Use Ctrl+Enter to run.{' '}
            <button className="example-link" onClick={() => setInput(MANUAL_EXAMPLE)} disabled={loading}>
              Try example
            </button>
          </p>
          <div className="isin-input-row">
            <textarea
              className="isin-textarea"
              rows={4}
              placeholder={MANUAL_EXAMPLE}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
            />
            <button
              className="btn btn-primary"
              onClick={handleManualLookup}
              disabled={loading || !input.trim()}
            >
              {loading ? 'Looking up…' : 'Look Up'}
            </button>
          </div>
        </div>
      )}

      {/* CSV mode */}
      {mode === 'csv' && (
        <>
          <FileUpload onUpload={handleFileUpload} disabled={loading} />
          <ColumnPicker
            headers={csvHeaders}
            selected={isinColumn}
            onChange={setIsinColumn}
            onRun={handleCsvLookup}
            disabled={loading || csvHeaders.length === 0}
            label="ISIN Column:"
          />
        </>
      )}

      {/* Progress */}
      <ProgressBar
        current={progress.current}
        total={progress.total}
        statusText={statusText}
      />

      {error && <p className="isin-error">{error}</p>}

      {/* Results summary bar */}
      {results.length > 0 && !loading && (
        <div className="summary-bar">
          <div className="summary-counts">
            <span className="badge auto">{found.length} found</span>
            {notFound.length > 0 && (
              <span className="badge no-match">{notFound.length} not found</span>
            )}
          </div>
          <div className="summary-actions">
            <div className="export-dropdown" ref={exportDropdownRef}>
              <button
                className="btn"
                onClick={() => setExportOpen(!exportOpen)}
                disabled={results.length === 0}
              >
                Export ▾
              </button>
              {exportOpen && (
                <div className="export-dropdown-menu">
                  <button onClick={() => { handleExportCsv(); setExportOpen(false); }}>Export CSV</button>
                  <button onClick={() => { handleExportXlsx(); setExportOpen(false); }}>Export XLSX</button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Found results */}
      {found.length > 0 && (
        <div className="isin-results">
          <table className="isin-table">
            <thead>
              <tr>
                <th>ISIN</th>
                <th>LEI</th>
                <th>Legal Entity Name</th>
                <th>Country</th>
                <th>Entity Status</th>
                <th>Registration Status</th>
              </tr>
            </thead>
            <tbody>
              {found.map((r, i) => (
                <tr key={i} style={{ background: statusColor(r.entity_status) }}>
                  <td className="mono">{r.isin}</td>
                  <td className="mono">{r.lei}</td>
                  <td>{r.legal_name}</td>
                  <td>{r.country}</td>
                  <td>{r.entity_status}</td>
                  <td>{r.registration_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Not found / errors */}
      {notFound.length > 0 && (
        <div className="isin-not-found">
          <h4>Not found / errors ({notFound.length})</h4>
          <table className="isin-table isin-table-error">
            <thead>
              <tr>
                <th>ISIN</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {notFound.map((r, i) => (
                <tr key={i}>
                  <td className="mono">{r.isin}</td>
                  <td>{r.error}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
