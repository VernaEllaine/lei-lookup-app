interface Props {
  headers: string[];
  entityColumn: string;
  leiColumn: string;
  onEntityChange: (col: string) => void;
  onLeiChange: (col: string) => void;
  onRun: () => void;
  disabled: boolean;
}

export default function ValidationColumnPicker({
  headers, entityColumn, leiColumn, onEntityChange, onLeiChange, onRun, disabled,
}: Props) {
  if (headers.length === 0) return null;

  return (
    <div className="column-picker">
      <label>Entity Name Column:</label>
      <select value={entityColumn} onChange={(e) => onEntityChange(e.target.value)} disabled={disabled}>
        {headers.map((h) => (
          <option key={h} value={h}>{h}</option>
        ))}
      </select>
      <label>LEI Column:</label>
      <select value={leiColumn} onChange={(e) => onLeiChange(e.target.value)} disabled={disabled}>
        {headers.map((h) => (
          <option key={h} value={h}>{h}</option>
        ))}
      </select>
      <button onClick={onRun} disabled={disabled} className="btn btn-primary">
        Run Validation
      </button>
    </div>
  );
}
