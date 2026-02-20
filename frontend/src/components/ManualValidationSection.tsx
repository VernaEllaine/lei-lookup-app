import { useState, useCallback } from 'react';
import type { ManualValidationResult } from '../types';
import { manualValidateEntries } from '../api';

const BATCH_SIZE = 5;

// 20-char LEI pattern
const LEI_RE = /^[A-Z0-9]{20}$/;

const EXAMPLE = 'INR2EJN1ERAN0W5ZP974\nMicrosoft Corporation, INR2EJN1ERAN0W5ZP974\nApple Inc | HWUPKR0MPOU8FGXBT394';

const FLAG_COLORS: Record<string, string> = {
  OK: '#eafaf1',
  LAPSED: '#fefce8',
  INVALID: '#fdf2f1',
  NOT_FOUND: '#fdf2f1',
  ERROR: '#fdf2f1',
};

function downloadCsv(content: string, filename: string): void {
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function resultsToCsv(results: ManualValidationResult[]): string {
  const header = 'Entity Name,LEI,Legal Name,Jurisdiction,Entity Status,Registration Status,Flag,Error';
  const escape = (v: string) => `"${v.replace(/"/g, '""')}"`;
  const rows = results.map((r) =>
    [r.entity_name, r.lei, r.legal_name, r.jurisdiction,
     r.entity_status, r.registration_status, r.flag, r.error].map(escape).join(','),
  );
  return [header, ...rows].join('\r\n');
}

/** Parse a line into {entity_name, lei}.
 *  Accepts: "LEI", "Entity, LEI", "LEI, Entity", pipe-separated variants.
 */
function parseLine(line: string): { entity_name: string; lei: string } {
  const sep = line.includes('|') ? '|' : ',';
  const parts = line.split(sep).map((p) => p.trim());

  if (parts.length === 1) {
    const v = parts[0].toUpperCase();
    return LEI_RE.test(v) ? { entity_name: '', lei: v } : { entity_name: v, lei: '' };
  }

  // Two or more parts — find which one is the LEI
  const leiIdx = parts.findIndex((p) => LEI_RE.test(p.toUpperCase()));
  if (leiIdx !== -1) {
    const lei = parts[leiIdx].toUpperCase();
    const entity_name = parts.filter((_, i) => i !== leiIdx).join(', ');
    return { entity_name, lei };
  }

  // Fallback: assume "Entity Name, LEI" order
  return { entity_name: parts[0], lei: parts.slice(1).join(', ').toUpperCase() };
}

export default function ManualValidationSection() {
  const [input, setInput] = useState('');
  const [results, setResults] = useState<ManualValidationResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState({ current: 0, total: 0 });
  const [error, setError] = useState('');

  const handleValidate = useCallback(async () => {
    const entries = input
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .map(parseLine);

    if (!entries.length) return;

    setLoading(true);
    setError('');
    setResults([]);
    setProgress({ current: 0, total: entries.length });

    const accumulated: ManualValidationResult[] = [];
    for (let i = 0; i < entries.length; i += BATCH_SIZE) {
      const batch = entries.slice(i, i + BATCH_SIZE);
      try {
        const resp = await manualValidateEntries(batch);
        accumulated.push(...resp.results);
      } catch {
        batch.forEach((e) =>
          accumulated.push({
            entity_name: e.entity_name, lei: e.lei,
            entity_status: '', registration_status: '',
            legal_name: '', jurisdiction: '',
            flag: 'ERROR', error: 'Request failed',
          }),
        );
      }
      setProgress({ current: Math.min(i + BATCH_SIZE, entries.length), total: entries.length });
      setResults([...accumulated]);
    }

    setLoading(false);
    if (accumulated.length === 0) setError('No results.');
  }, [input]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleValidate();
    },
    [handleValidate],
  );

  const handleExport = useCallback(() => {
    downloadCsv(resultsToCsv(results), 'lei_manual_validation.csv');
  }, [results]);

  const ok = results.filter((r) => r.flag === 'OK').length;
  const issues = results.length - ok;

  return (
    <div className="manual-section">
      <p className="isin-desc">
        Enter one LEI per line. Optionally include the entity name separated by a
        comma or pipe: <code>Entity Name, LEI</code> or just <code>LEI</code>.
        Use Ctrl+Enter to run.{' '}
        <button className="example-link" onClick={() => setInput(EXAMPLE)} disabled={loading}>
          Try example
        </button>
      </p>

      <div className="isin-input-row">
        <textarea
          className="isin-textarea"
          rows={5}
          placeholder={EXAMPLE}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
        />
        <button
          className="btn btn-primary"
          onClick={handleValidate}
          disabled={loading || !input.trim()}
        >
          {loading ? 'Validating…' : 'Validate'}
        </button>
      </div>

      {loading && progress.total > 0 && (
        <div className="isin-progress">
          <div
            className="isin-progress-bar"
            style={{ width: `${(progress.current / progress.total) * 100}%` }}
          />
          <span className="isin-progress-text">
            {progress.current} / {progress.total} LEIs validated
          </span>
        </div>
      )}

      {error && <p className="isin-error">{error}</p>}

      {results.length > 0 && !loading && (
        <div className="isin-results-header">
          <span>
            {ok} OK &nbsp;·&nbsp; {issues} with issues
          </span>
          <button className="btn" onClick={handleExport}>Export CSV</button>
        </div>
      )}

      {results.length > 0 && (
        <div className="isin-results">
          <table className="isin-table">
            <thead>
              <tr>
                <th>Entity Name</th>
                <th>LEI</th>
                <th>Legal Name (GLEIF)</th>
                <th>Jurisdiction</th>
                <th>Entity Status</th>
                <th>Reg. Status</th>
                <th>Flag</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i} style={{ background: FLAG_COLORS[r.flag] ?? '#fff' }}>
                  <td>{r.entity_name}</td>
                  <td className="mono">{r.lei}</td>
                  <td>{r.legal_name}</td>
                  <td>{r.jurisdiction}</td>
                  <td>{r.entity_status}</td>
                  <td>{r.registration_status}</td>
                  <td><strong>{r.flag}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
