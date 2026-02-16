import { useState, useRef, useEffect } from 'react';
import type { Summary } from '../types';

interface Props {
  summary: Summary;
  onConfirmAll: () => void;
  onExportCsv: () => void;
  onExportXlsx: () => void;
  onClearCache: () => void;
  disabled: boolean;
}

export default function SummaryBar({ summary, onConfirmAll, onExportCsv, onExportXlsx, onClearCache, disabled }: Props) {
  const [exportOpen, setExportOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setExportOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className="summary-bar">
      <div className="summary-counts">
        <span className="badge auto">{summary.auto_matched} Auto-matched</span>
        <span className="badge confirmed">{summary.confirmed} Confirmed</span>
        <span className="badge reviewed">{summary.reviewed} Reviewed</span>
        <span className="badge review-needed">{summary.review_needed} Review needed</span>
        <span className="badge no-match">{summary.no_match} No match</span>
        {summary.errors > 0 && <span className="badge error">{summary.errors} Errors</span>}
        <span className="badge cache">Cache: {summary.cache_size}</span>
      </div>
      <div className="summary-actions">
        <button
          className="btn"
          onClick={onConfirmAll}
          disabled={disabled || summary.reviewed === 0}
        >
          Confirm All
        </button>
        <div className="export-dropdown" ref={dropdownRef}>
          <button
            className="btn"
            onClick={() => setExportOpen(!exportOpen)}
            disabled={disabled || summary.total === 0}
          >
            Export ▾
          </button>
          {exportOpen && (
            <div className="export-dropdown-menu">
              <button onClick={() => { onExportCsv(); setExportOpen(false); }}>Export CSV</button>
              <button onClick={() => { onExportXlsx(); setExportOpen(false); }}>Export XLSX</button>
            </div>
          )}
        </div>
        <button className="btn btn-danger" onClick={onClearCache} disabled={disabled}>
          Clear Cache
        </button>
      </div>
    </div>
  );
}
