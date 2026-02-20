import { useState, useCallback, useRef, useEffect } from 'react';
import type { IsinResult } from '../types';
import { lookupIsins, exportIsinXlsx } from '../api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const BATCH_SIZE = 5; // ISINs sent per backend request

const FLAG_COLORS: Record<string, string> = {
  ACTIVE: '#d4edda',
  INACTIVE: '#f8d7da',
  ANNULLED: '#f8d7da',
};

function statusColor(entityStatus: string): string {
  return FLAG_COLORS[entityStatus] ?? '#fff';
}

/** Minimal RFC 4180 CSV parser — returns {headers, rows}. */
function parseCSV(text: string): { headers: string[]; rows: string[][] } {
  // Detect delimiter: semicolon or tab if they appear more than commas on line 1
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
  // 1. Header name match
  const isinPattern = /\bisin\b/i;
  const byName = headers.find((h) => isinPattern.test(h));
  if (byName) return byName;
  // 2. Data pattern: 12-char alphanumeric starting with 2 uppercase letters
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
  const header = 'ISIN,Security Name,Security Type,LEI,Legal Entity Name,Country,Entity Status,Registration Status,Error';
  const escape = (v: string) => `"${v.replace(/"/g, '""')}"`;
  const rows = results.map((r) =>
    [r.isin, r.security_name, r.security_type, r.lei, r.legal_name, r.country, r.entity_status, r.registration_status, r.error]
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
  const [csvFilename, setCsvFilename] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Shared
  const [results, setResults] = useState<IsinResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState({ current: 0, total: 0 });
  const [error, setError] = useState('');

  // -------------------------------------------------------------------------
  // CSV file loading
  // -------------------------------------------------------------------------

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setCsvFilename(file.name);
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = (ev.target?.result as string) ?? '';
      const { headers, rows } = parseCSV(text);
      setCsvHeaders(headers);
      setCsvRows(rows);
      setIsinColumn(detectIsinColumn(headers, rows));
      setResults([]);
      setError('');
      setProgress({ current: 0, total: 0 });
    };
    reader.readAsText(file, 'utf-8');
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

    const accumulated: IsinResult[] = [];
    for (let i = 0; i < unique.length; i += BATCH_SIZE) {
      const batch = unique.slice(i, i + BATCH_SIZE);
      try {
        const resp = await lookupIsins(batch.join(','));
        accumulated.push(...resp.results);
      } catch {
        batch.forEach((isin) =>
          accumulated.push({
            isin, lei: '', security_name: '', security_type: '',
            legal_name: '', country: '',
            entity_status: '', registration_status: '',
            error: 'Request failed',
          }),
        );
      }
      setProgress({ current: Math.min(i + BATCH_SIZE, unique.length), total: unique.length });
      setResults([...accumulated]);
    }

    setLoading(false);
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
  const isRunning = loading;

  return (
    <div className="isin-section">

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
        <>
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
              disabled={isRunning}
            />
            <button
              className="btn btn-primary"
              onClick={handleManualLookup}
              disabled={isRunning || !input.trim()}
            >
              {isRunning ? 'Looking up…' : 'Look Up'}
            </button>
          </div>
        </>
      )}

      {/* CSV mode */}
      {mode === 'csv' && (
        <>
          <p className="isin-desc">
            Upload a CSV file containing ISIN codes. The ISIN column will be
            auto-detected or you can select it manually.
          </p>
          <div className="isin-csv-controls">
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.tsv,.txt"
              style={{ display: 'none' }}
              onChange={handleFileChange}
            />
            <button
              className="btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={isRunning}
            >
              {csvFilename ? `📄 ${csvFilename}` : 'Choose CSV file'}
            </button>

            {csvHeaders.length > 0 && (
              <>
                <label className="isin-col-label">ISIN column:</label>
                <select
                  className="isin-col-select"
                  value={isinColumn}
                  onChange={(e) => setIsinColumn(e.target.value)}
                  disabled={isRunning}
                >
                  {csvHeaders.map((h) => (
                    <option key={h} value={h}>{h}</option>
                  ))}
                </select>
                <span className="isin-row-count">{csvRows.length} rows</span>
                <button
                  className="btn btn-primary"
                  onClick={handleCsvLookup}
                  disabled={isRunning || !isinColumn}
                >
                  {isRunning ? 'Looking up…' : 'Run Lookup'}
                </button>
              </>
            )}
          </div>
        </>
      )}

      {/* Progress */}
      {isRunning && progress.total > 0 && (
        <div className="isin-progress">
          <div
            className="isin-progress-bar"
            style={{ width: `${(progress.current / progress.total) * 100}%` }}
          />
          <span className="isin-progress-text">
            {progress.current} / {progress.total} ISINs processed
          </span>
        </div>
      )}

      {error && <p className="isin-error">{error}</p>}

      {/* Results header + export */}
      {results.length > 0 && !isRunning && (
        <div className="isin-results-header">
          <span>{found.length} found, {notFound.length} not found</span>
          <div className="export-dropdown" ref={exportDropdownRef}>
            <button className="btn" onClick={() => setExportOpen(!exportOpen)}>
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
      )}

      {/* Found results */}
      {found.length > 0 && (
        <div className="isin-results">
          <table className="isin-table">
            <thead>
              <tr>
                <th>ISIN</th>
                <th>Security Name</th>
                <th>Security Type</th>
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
                  <td>{r.security_name}</td>
                  <td>{r.security_type}</td>
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
    </div>
  );
}
