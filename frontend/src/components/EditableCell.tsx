import { useState, useRef, useEffect } from 'react';

interface Props {
  value: string;
  field: string;
  readOnly?: boolean;
  onSave: (field: string, value: string) => void;
}

export default function EditableCell({ value, field, readOnly, onSave }: Props) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  // Update displayed value when prop changes (e.g. after validation)
  useEffect(() => {
    setEditValue(value);
  }, [value]);

  const handleDoubleClick = () => {
    if (readOnly) {
      // Copy to clipboard
      navigator.clipboard.writeText(value).catch(() => {});
      return;
    }
    setEditing(true);
    setEditValue(value);
  };

  const commit = () => {
    setEditing(false);
    if (editValue !== value) {
      onSave(field, editValue);
    }
  };

  const cancel = () => {
    setEditing(false);
    setEditValue(value);
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        className="editable-input"
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit();
          if (e.key === 'Escape') cancel();
        }}
        onBlur={commit}
      />
    );
  }

  return (
    <span
      className={`editable-cell ${readOnly ? 'readonly' : ''}`}
      onDoubleClick={handleDoubleClick}
      title={readOnly ? 'Double-click to copy' : 'Double-click to edit'}
    >
      {value || '\u00A0'}
    </span>
  );
}
