import { useState, useRef, useEffect } from 'react';
import type { ValidationSummary } from '../types';

interface Props {
  summary: ValidationSummary;
  onExportCsv: () => void;
  onExportXlsx: () => void;
  disabled: boolean;
}

export default function ValidationSummaryBar({ summary, onExportCsv, onExportXlsx, disabled }: Props) {
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
        <span className="badge validation-ok">{summary.ok} OK</span>
        <span className="badge validation-lapsed">{summary.lapsed} Lapsed</span>
        <span className="badge validation-invalid">{summary.invalid} Invalid</span>
        <span className="badge validation-notfound">{summary.not_found} Not Found</span>
        {summary.errors > 0 && <span className="badge validation-error">{summary.errors} Errors</span>}
      </div>
      <div className="summary-actions">
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
      </div>
    </div>
  );
}
