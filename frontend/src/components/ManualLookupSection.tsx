import { useState, useCallback } from 'react';
import type { ManualLookupResult } from '../types';
import { manualLookupNames, manualValidateEntries } from '../api';
import EditableCell from './EditableCell';

// Process one name at a time to show live progress
const BATCH_SIZE = 1;

const EXAMPLE = 'Apple Inc\nDeutsche Bank AG\nCarrefour SA';

const STATUS_COLORS: Record<string, string> = {
  'AUTO-MATCHED': '#eafaf1',
  'REVIEW NEEDED': '#fefce8',
  'NO MATCH': '#fdf2f1',
  'ERROR': '#fdf2f1',
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

function resultsToCsv(results: ManualLookupResult[]): string {
  const header = 'Query,LEI,Legal Name,Jurisdiction,Status,Confidence,Match Status,Candidates,Error';
  const escape = (v: string) => `"${v.replace(/"/g, '""')}"`;
  const rows = results.map((r) =>
    [r.query, r.lei, r.legal_name, r.jurisdiction, r.status,
     r.confidence, r.match_status, r.candidates, r.error].map(escape).join(','),
  );
  return [header, ...rows].join('\r\n');
}

export default function ManualLookupSection() {
  const [input, setInput] = useState('');
  const [results, setResults] = useState<ManualLookupResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState({ current: 0, total: 0 });
  const [error, setError] = useState('');

  const handleLookup = useCallback(async () => {
    const names = input.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!names.length) return;

    setLoading(true);
    setError('');
    setResults([]);
    setProgress({ current: 0, total: names.length });

    const accumulated: ManualLookupResult[] = [];
    for (let i = 0; i < names.length; i += BATCH_SIZE) {
      const batch = names.slice(i, i + BATCH_SIZE);
      try {
        const resp = await manualLookupNames(batch.join(','));
        accumulated.push(...resp.results);
      } catch {
        batch.forEach((query) =>
          accumulated.push({
            query, lei: '', legal_name: '', jurisdiction: '',
            status: '', confidence: '', match_status: 'ERROR',
            candidates: '', error: 'Request failed',
          }),
        );
      }
      setProgress({ current: Math.min(i + BATCH_SIZE, names.length), total: names.length });
      setResults([...accumulated]);
    }

    setLoading(false);
    if (accumulated.length === 0) setError('No results.');
  }, [input]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleLookup();
    },
    [handleLookup],
  );

  const handleExport = useCallback(() => {
    downloadCsv(resultsToCsv(results), 'lei_manual_lookup.csv');
  }, [results]);

  const handleLeiSave = useCallback(async (index: number, newLei: string) => {
    const row = results[index];
    try {
      const resp = await manualValidateEntries([{ entity_name: row.query, lei: newLei }]);
      const v = resp.results[0];

      if (v.flag === 'INVALID' || v.flag === 'NOT_FOUND' || v.flag === 'ERROR') {
        alert(`Invalid LEI: ${v.error || v.flag}`);
        return;
      }

      if (v.flag === 'LAPSED') {
        const confirmed = window.confirm(
          `This LEI (${newLei}) is lapsed for ${v.legal_name}.\n\nDo you want to keep it?`,
        );
        if (!confirmed) return;
      }

      setResults((prev) =>
        prev.map((r, i) =>
          i === index
            ? { ...r, lei: newLei, legal_name: v.legal_name, jurisdiction: v.jurisdiction, status: v.entity_status, match_status: 'CONFIRMED' }
            : r,
        ),
      );
    } catch {
      alert('Failed to validate LEI. Please try again.');
    }
  }, [results]);

  return (
    <div className="manual-section">
      <p className="isin-desc">
        Enter one company name per line. Results stream in as each name is resolved.
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
          onClick={handleLookup}
          disabled={loading || !input.trim()}
        >
          {loading ? 'Looking up…' : 'Look Up'}
        </button>
      </div>

      {loading && progress.total > 0 && (
        <div className="isin-progress">
          <div
            className="isin-progress-bar"
            style={{ width: `${(progress.current / progress.total) * 100}%` }}
          />
          <span className="isin-progress-text">
            {progress.current} / {progress.total} names processed
          </span>
        </div>
      )}

      {error && <p className="isin-error">{error}</p>}

      {results.length > 0 && !loading && (
        <div className="isin-results-header">
          <span>
            {results.filter((r) => r.match_status === 'AUTO-MATCHED').length} auto-matched
            &nbsp;·&nbsp;
            {results.filter((r) => r.match_status === 'REVIEW NEEDED').length} review needed
            &nbsp;·&nbsp;
            {results.filter((r) => r.match_status === 'NO MATCH' || r.match_status === 'ERROR').length} unresolved
          </span>
          <button className="btn" onClick={handleExport}>Export CSV</button>
        </div>
      )}

      {results.length > 0 && (
        <div className="isin-results">
          <table className="isin-table">
            <thead>
              <tr>
                <th>Query</th>
                <th>LEI</th>
                <th>Legal Name</th>
                <th>Jurisdiction</th>
                <th>Confidence</th>
                <th>Match Status</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i} style={{ background: STATUS_COLORS[r.match_status] ?? '#fff' }}>
                  <td>{r.query}</td>
                  <td className="mono">
                    <EditableCell
                      value={r.lei}
                      field="lei"
                      onSave={(_field, value) => handleLeiSave(i, value)}
                    />
                  </td>
                  <td>{r.legal_name}</td>
                  <td>{r.jurisdiction}</td>
                  <td>{r.confidence}</td>
                  <td>{r.match_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
