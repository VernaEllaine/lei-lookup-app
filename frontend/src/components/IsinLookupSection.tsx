import { useState, useCallback } from 'react';
import type { IsinResult } from '../types';
import { lookupIsins } from '../api';

const FLAG_COLORS: Record<string, string> = {
  ACTIVE: '#d4edda',
  INACTIVE: '#f8d7da',
  ANNULLED: '#f8d7da',
};

function statusColor(entityStatus: string): string {
  return FLAG_COLORS[entityStatus] ?? '#fff';
}

export default function IsinLookupSection() {
  const [input, setInput] = useState('');
  const [results, setResults] = useState<IsinResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleLookup = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed) return;
    setLoading(true);
    setError('');
    setResults([]);
    try {
      const resp = await lookupIsins(trimmed);
      setResults(resp.results);
      if (resp.results.length === 0) setError('No results found.');
    } catch (e) {
      setError('Request failed. Check your connection and try again.');
    } finally {
      setLoading(false);
    }
  }, [input]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleLookup();
    },
    [handleLookup],
  );

  const found = results.filter((r) => !r.error);
  const notFound = results.filter((r) => r.error);

  return (
    <div className="isin-section">
      <p className="isin-desc">
        Enter one or more ISIN codes (one per line or comma-separated) to find
        the Legal Entity Identifier (LEI) of the issuer.
      </p>

      <div className="isin-input-row">
        <textarea
          className="isin-textarea"
          rows={4}
          placeholder={'US0378331005\nDE0007164600\nGB0002634946'}
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

      {error && <p className="isin-error">{error}</p>}

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

      {notFound.length > 0 && (
        <div className="isin-not-found">
          <h4>Not found / errors</h4>
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
