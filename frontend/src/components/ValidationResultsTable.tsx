import { useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import type { ValidationRowResult } from '../types';

const FLAG_COLORS: Record<string, string> = {
  OK: '#d4edda',
  LAPSED: '#fff3cd',
  INVALID: '#f8d7da',
  NOT_FOUND: '#f8d7da',
  ERROR: '#f8d7da',
};

const ROW_HEIGHT = 44;
const OVERSCAN = 10;

interface Props {
  rows: ValidationRowResult[];
}

export default function ValidationResultsTable({ rows }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggleExpand = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN,
  });

  if (rows.length === 0) return null;

  const cell = (row: ValidationRowResult, field: string, cls: string, value: string) => {
    const key = `${row.index}-${field}`;
    const isExpanded = expanded.has(key);
    return (
      <div
        className={`virtual-cell ${cls} expandable${isExpanded ? ' expanded' : ''}`}
        title={value}
        onClick={() => toggleExpand(key)}
      >
        {value}
      </div>
    );
  };

  return (
    <div className="results-table-container">
      <table className="results-table results-header-table validation-header-table">
        <thead>
          <tr>
            <th className="vcol-entity">Entity Name</th>
            <th className="vcol-lei">Provided LEI</th>
            <th className="vcol-estatus">Entity Status</th>
            <th className="vcol-rstatus">Reg. Status</th>
            <th className="vcol-flag">Flag</th>
            <th className="vcol-slei">Suggested LEI</th>
            <th className="vcol-slegal">Suggested Legal Name</th>
            <th className="vcol-sconf">Confidence</th>
          </tr>
        </thead>
      </table>
      <div ref={scrollRef} className="results-scroll">
        <div
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            position: 'relative',
            width: '100%',
          }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const row = rows[virtualRow.index];
            const bg = FLAG_COLORS[row.flag] || undefined;
            return (
              <div
                key={row.index}
                className="virtual-row"
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  height: `${virtualRow.size}px`,
                  transform: `translateY(${virtualRow.start}px)`,
                  backgroundColor: bg,
                }}
              >
                {cell(row, 'entity', 'vcol-entity', row.entity_name)}
                {cell(row, 'lei', 'vcol-lei', row.provided_lei)}
                {cell(row, 'estatus', 'vcol-estatus', row.entity_status)}
                {cell(row, 'rstatus', 'vcol-rstatus', row.registration_status)}
                <div className="virtual-cell vcol-flag validation-flag">
                  {row.flag}
                </div>
                {cell(row, 'slei', 'vcol-slei', row.suggested_lei)}
                {cell(row, 'slegal', 'vcol-slegal', row.suggested_legal_name)}
                {cell(row, 'sconf', 'vcol-sconf', row.suggested_confidence)}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
