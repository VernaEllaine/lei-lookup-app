import { useState, useCallback, useRef, useEffect } from 'react';
import * as XLSX from 'xlsx';
import type { LeiDetail } from '../types';
import { lookupLeiDetails } from '../api';
import { parseCSV, downloadCsv } from '../csv';
import FileUpload from './FileUpload';
import ColumnPicker from './ColumnPicker';
import ProgressBar from './ProgressBar';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const BATCH_SIZE = 1000;
const LEI_REGEX = /^[A-Z0-9]{18}[0-9]{2}$/;

const EXPORT_HEADERS = [
  'LEI', 'Name of Issuer', 'Country of Issuer', 'Region',
  'Entity Status', 'Registration Status', 'Error',
];

function toExportRows(results: LeiDetail[]): string[][] {
  return results.map((r) => [
    r.lei, r.legal_name, r.country, r.region,
    r.entity_status, r.registration_status, r.error,
  ]);
}

function resultsToCsv(results: LeiDetail[]): string {
  const escape = (v: string) => `"${v.replace(/"/g, '""')}"`;
  return [EXPORT_HEADERS, ...toExportRows(results)]
    .map((row) => row.map(escape).join(','))
    .join('\r\n');
}

function exportXlsx(results: LeiDetail[]): void {
  const ws = XLSX.utils.aoa_to_sheet([EXPORT_HEADERS, ...toExportRows(results)]);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Issuer Details');
  XLSX.writeFile(wb, 'issuer_details.xlsx');
}

/** Guess which column holds LEI codes. */
function detectLeiColumn(headers: string[], rows: string[][]): string {
  const byName = headers.find((h) => /\blei\b/i.test(h));
  if (byName) return byName;
  for (let col = 0; col < headers.length; col++) {
    const sample = rows.slice(0, 5).map((r) => (r[col] ?? '').trim().toUpperCase());
    if (sample.some((v) => LEI_REGEX.test(v))) return headers[col];
  }
  return headers[0] ?? '';
}

/** Files that are just a list of LEIs have no header row: if any "header"
 *  cell is itself an LEI, treat the first row as data. */
function withHeaders(headers: string[], rows: string[][]): { headers: string[]; rows: string[][] } {
  if (!headers.some((h) => LEI_REGEX.test(h.trim().toUpperCase()))) return { headers, rows };
  const names = headers.map((_, i) => (headers.length === 1 ? 'LEI' : `Column ${i + 1}`));
  return { headers: names, rows: [headers, ...rows] };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type Mode = 'manual' | 'csv';

const MANUAL_EXAMPLE = '8156009027DEC776E736\n549300187TP95ZWG8155\nHWUPKR0MPOU8FGXBT394';

export default function IssuerDetailsSection() {
  const [mode, setMode] = useState<Mode>('csv');

  // Manual mode
  const [input, setInput] = useState('');

  // CSV mode
  const [csvHeaders, setCsvHeaders] = useState<string[]>([]);
  const [csvRows, setCsvRows] = useState<string[][]>([]);
  const [leiColumn, setLeiColumn] = useState('');

  // Shared
  const [results, setResults] = useState<LeiDetail[]>([]);
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
      let parsed: { headers: string[]; rows: string[][] };

      if (isXlsx) {
        const data = new Uint8Array(ev.target?.result as ArrayBuffer);
        const wb = XLSX.read(data, { type: 'array' });
        const ws = wb.Sheets[wb.SheetNames[0]];
        const sheet: string[][] = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '' });
        parsed = {
          headers: (sheet[0] ?? []).map(String),
          rows: sheet.slice(1).map((r) => r.map(String)),
        };
      } else {
        parsed = parseCSV((ev.target?.result as string) ?? '');
      }

      const { headers, rows } = withHeaders(parsed.headers, parsed.rows);
      setCsvHeaders(headers);
      setCsvRows(rows);
      setLeiColumn(detectLeiColumn(headers, rows));
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
  // Lookup logic (shared by both modes). Keeps input order and duplicates so
  // exports line up row-for-row with the source file.
  // -------------------------------------------------------------------------

  const runLookup = useCallback(async (leis: string[]) => {
    const codes = leis.map((s) => s.trim().toUpperCase()).filter(Boolean);
    if (!codes.length) { setError('No LEI codes found.'); return; }

    setLoading(true);
    setError('');
    setResults([]);
    setProgress({ current: 0, total: codes.length });
    setStatusText('Starting…');

    const accumulated: LeiDetail[] = [];
    for (let i = 0; i < codes.length; i += BATCH_SIZE) {
      const batch = codes.slice(i, i + BATCH_SIZE);
      try {
        const resp = await lookupLeiDetails(batch);
        accumulated.push(...resp.results);
      } catch {
        batch.forEach((lei) =>
          accumulated.push({
            lei, legal_name: '', country: '', region: '',
            entity_status: '', registration_status: '',
            error: 'Request failed',
          }),
        );
      }
      const current = Math.min(i + BATCH_SIZE, codes.length);
      setProgress({ current, total: codes.length });
      setStatusText(`Looking up LEI ${current} of ${codes.length}…`);
      setResults([...accumulated]);
    }

    setLoading(false);
    setStatusText('Done.');
  }, []);

  const handleManualLookup = useCallback(() => {
    runLookup(input.split(/[\n,;\s]+/));
  }, [input, runLookup]);

  const handleCsvLookup = useCallback(() => {
    const colIndex = csvHeaders.indexOf(leiColumn);
    if (colIndex === -1) { setError('Selected column not found.'); return; }
    runLookup(csvRows.map((r) => r[colIndex] ?? ''));
  }, [csvHeaders, csvRows, leiColumn, runLookup]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleManualLookup();
    },
    [handleManualLookup],
  );

  // -------------------------------------------------------------------------
  // Export
  // -------------------------------------------------------------------------

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

  const notFound = results.filter((r) => r.error).length;
  const europe = results.filter((r) => r.region === 'Europe').length;
  const global = results.filter((r) => r.region === 'Global').length;

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

      <p className="isin-desc">
        Returns the issuer's legal name, country (ISO 3166-1 alpha-2, legal address) and
        region for each LEI. Europe covers the EEA, UK, Switzerland, European microstates
        and dependencies, the Western Balkans, Moldova, Ukraine and Belarus; everything else
        is Global.
      </p>

      {/* Manual mode */}
      {mode === 'manual' && (
        <div className="manual-section">
          <p className="isin-desc">
            Enter one or more LEI codes (one per line or comma-separated).
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
            selected={leiColumn}
            onChange={setLeiColumn}
            onRun={handleCsvLookup}
            disabled={loading || csvHeaders.length === 0}
            label="LEI Column:"
          />
        </>
      )}

      <ProgressBar current={progress.current} total={progress.total} statusText={statusText} />

      {error && <p className="isin-error">{error}</p>}

      {/* Summary bar */}
      {results.length > 0 && !loading && (
        <div className="summary-bar">
          <div className="summary-counts">
            <span className="badge auto">{results.length - notFound} found</span>
            <span className="badge confirmed">{europe} Europe</span>
            <span className="badge cache">{global} Global</span>
            {notFound > 0 && <span className="badge no-match">{notFound} not found</span>}
          </div>
          <div className="summary-actions">
            <div className="export-dropdown" ref={exportDropdownRef}>
              <button className="btn" onClick={() => setExportOpen(!exportOpen)}>
                Export ▾
              </button>
              {exportOpen && (
                <div className="export-dropdown-menu">
                  <button onClick={() => { downloadCsv(resultsToCsv(results), 'issuer_details.csv'); setExportOpen(false); }}>
                    Export CSV
                  </button>
                  <button onClick={() => { exportXlsx(results); setExportOpen(false); }}>
                    Export XLSX
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Results, in input order; rows that weren't found stay in place */}
      {results.length > 0 && (
        <div className="isin-results">
          <table className="isin-table">
            <thead>
              <tr>
                <th>LEI</th>
                <th>Name of Issuer</th>
                <th>Country of Issuer</th>
                <th>Region</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i} className={r.error ? 'issuer-row-error' : undefined}>
                  <td className="mono">{r.lei}</td>
                  <td>{r.error || r.legal_name}</td>
                  <td>{r.country}</td>
                  <td>{r.region}</td>
                  <td>{r.error ? '' : `${r.entity_status} / ${r.registration_status}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
