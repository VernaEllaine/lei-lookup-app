import { useRef } from 'react';
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

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN,
  });

  if (rows.length === 0) return null;

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
                <div className="virtual-cell vcol-entity" title={row.entity_name}>
                  {row.entity_name}
                </div>
                <div className="virtual-cell vcol-lei" title={row.provided_lei}>
                  {row.provided_lei}
                </div>
                <div className="virtual-cell vcol-estatus">
                  {row.entity_status}
                </div>
                <div className="virtual-cell vcol-rstatus">
                  {row.registration_status}
                </div>
                <div className="virtual-cell vcol-flag validation-flag">
                  {row.flag}
                </div>
                <div className="virtual-cell vcol-slei" title={row.suggested_lei}>
                  {row.suggested_lei}
                </div>
                <div className="virtual-cell vcol-slegal" title={row.suggested_legal_name}>
                  {row.suggested_legal_name}
                </div>
                <div className="virtual-cell vcol-sconf">
                  {row.suggested_confidence}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
