import { useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import type { RowResult } from '../types';
import EditableCell from './EditableCell';

const STATUS_COLORS: Record<string, string> = {
  'AUTO-MATCHED': '#d4edda',
  'CONFIRMED': '#b8daff',
  'REVIEWED': '#cce5ff',
  'REVIEW NEEDED': '#fff3cd',
  'NO MATCH': '#f8d7da',
};

const ROW_HEIGHT = 44;
const OVERSCAN = 10;

interface Props {
  rows: RowResult[];
  onCellSave: (index: number, field: string, value: string) => void;
}

export default function ResultsTable({ rows, onCellSave }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // Track which cells are expanded: "rowIndex-field"
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

  return (
    <div className="results-table-container">
      <table className="results-table results-header-table">
        <thead>
          <tr>
            <th className="col-entity">Entity Name</th>
            <th className="col-lei">LEI</th>
            <th className="col-legal">Legal Name</th>
            <th className="col-jurisdiction">Jurisdiction</th>
            <th className="col-status">Status</th>
            <th className="col-confidence">Confidence</th>
            <th className="col-match">Match Status</th>
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
            const bg =
              STATUS_COLORS[row.match_status] ||
              (row.match_status.startsWith('ERROR') ? '#f8d7da' : undefined);
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
                <div
                  className={`virtual-cell col-entity expandable${expanded.has(`${row.index}-entity`) ? ' expanded' : ''}`}
                  onClick={() => toggleExpand(`${row.index}-entity`)}
                  title={row.entity_name}
                >
                  <EditableCell
                    value={row.entity_name}
                    field="entity_name"
                    readOnly
                    onSave={() => {}}
                  />
                </div>
                <div className="virtual-cell col-lei">
                  <EditableCell
                    value={row.lei}
                    field="lei"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </div>
                <div
                  className={`virtual-cell col-legal expandable${expanded.has(`${row.index}-legal`) ? ' expanded' : ''}`}
                  onClick={() => toggleExpand(`${row.index}-legal`)}
                  title={row.legal_name}
                >
                  <EditableCell
                    value={row.legal_name}
                    field="legal_name"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </div>
                <div className="virtual-cell col-jurisdiction">
                  <EditableCell
                    value={row.jurisdiction}
                    field="jurisdiction"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </div>
                <div className="virtual-cell col-status">
                  <EditableCell
                    value={row.status}
                    field="status"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </div>
                <div className="virtual-cell col-confidence">
                  <EditableCell
                    value={row.confidence}
                    field="confidence"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </div>
                <div className="virtual-cell col-match match-status">
                  {row.match_status}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
