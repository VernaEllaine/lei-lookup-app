import { useCallback, useRef, useState } from 'react';

interface Props {
  onUpload: (file: File) => void;
  disabled: boolean;
}

export default function FileUpload({ onUpload, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [fileName, setFileName] = useState('');

  const handleFile = useCallback(
    (file: File) => {
      setFileName(file.name);
      onUpload(file);
    },
    [onUpload],
  );

  return (
    <div
      className={`file-upload ${dragOver ? 'drag-over' : ''}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const file = e.dataTransfer.files[0];
        if (file) handleFile(file);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".csv"
        style={{ display: 'none' }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
        }}
        disabled={disabled}
      />
      <button
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        className="btn"
      >
        Browse CSV
      </button>
      <span className="file-name">
        {fileName ? fileName : 'Drop a CSV file here or click Browse'}
      </span>
    </div>
  );
}
