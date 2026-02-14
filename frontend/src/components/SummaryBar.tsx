import type { Summary } from '../types';

interface Props {
  summary: Summary;
  onConfirmAll: () => void;
  onExport: () => void;
  onClearCache: () => void;
  disabled: boolean;
}

export default function SummaryBar({ summary, onConfirmAll, onExport, onClearCache, disabled }: Props) {
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
        <button
          className="btn"
          onClick={onExport}
          disabled={disabled || summary.total === 0}
        >
          Export CSV
        </button>
        <button className="btn btn-danger" onClick={onClearCache} disabled={disabled}>
          Clear Cache
        </button>
      </div>
    </div>
  );
}
