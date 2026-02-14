interface Props {
  headers: string[];
  selected: string;
  onChange: (col: string) => void;
  onRun: () => void;
  disabled: boolean;
}

export default function ColumnPicker({ headers, selected, onChange, onRun, disabled }: Props) {
  if (headers.length === 0) return null;

  return (
    <div className="column-picker">
      <label>Entity Column:</label>
      <select
        value={selected}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        {headers.map((h) => (
          <option key={h} value={h}>
            {h}
          </option>
        ))}
      </select>
      <button onClick={onRun} disabled={disabled} className="btn btn-primary">
        Run Lookup
      </button>
    </div>
  );
}
