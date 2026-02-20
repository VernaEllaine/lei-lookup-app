interface Props {
  headers: string[];
  selected: string;
  onChange: (col: string) => void;
  onRun: () => void;
  disabled: boolean;
  label?: string;
}

export default function ColumnPicker({ headers, selected, onChange, onRun, disabled, label = 'Entity Column:' }: Props) {
  if (headers.length === 0) return null;

  return (
    <div className="column-picker">
      <label>{label}</label>
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
