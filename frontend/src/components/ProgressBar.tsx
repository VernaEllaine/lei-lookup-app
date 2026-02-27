interface Props {
  current: number;
  total: number;
  statusText: string;
}

export default function ProgressBar({ current, total, statusText }: Props) {
  if (total === 0) return null;

  const pct = Math.round((current / total) * 100);

  return (
    <div className="progress-section">
      <span className="progress-status">{statusText}</span>
      <div className="progress-bar-row">
        <div className="progress-bar-container">
          <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="progress-count">
          {current} / {total}
        </span>
      </div>
    </div>
  );
}
