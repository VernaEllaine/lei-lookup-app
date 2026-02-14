import type { RowResult } from '../types';
import EditableCell from './EditableCell';

const STATUS_COLORS: Record<string, string> = {
  'AUTO-MATCHED': '#d4edda',
  'CONFIRMED': '#b8daff',
  'REVIEWED': '#cce5ff',
  'REVIEW NEEDED': '#fff3cd',
  'NO MATCH': '#f8d7da',
};

interface Props {
  rows: RowResult[];
  onCellSave: (index: number, field: string, value: string) => void;
}

export default function ResultsTable({ rows, onCellSave }: Props) {
  if (rows.length === 0) return null;

  return (
    <div className="results-table-container">
      <table className="results-table">
        <thead>
          <tr>
            <th>Entity Name</th>
            <th>LEI</th>
            <th>Legal Name</th>
            <th>Jurisdiction</th>
            <th>Status</th>
            <th>Confidence</th>
            <th>Match Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const bg = STATUS_COLORS[row.match_status] || (row.match_status.startsWith('ERROR') ? '#f8d7da' : undefined);
            return (
              <tr key={row.index} style={{ backgroundColor: bg }}>
                <td>
                  <EditableCell
                    value={row.entity_name}
                    field="entity_name"
                    readOnly
                    onSave={() => {}}
                  />
                </td>
                <td>
                  <EditableCell
                    value={row.lei}
                    field="lei"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </td>
                <td>
                  <EditableCell
                    value={row.legal_name}
                    field="legal_name"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </td>
                <td>
                  <EditableCell
                    value={row.jurisdiction}
                    field="jurisdiction"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </td>
                <td>
                  <EditableCell
                    value={row.status}
                    field="status"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </td>
                <td>
                  <EditableCell
                    value={row.confidence}
                    field="confidence"
                    onSave={(field, value) => onCellSave(row.index, field, value)}
                  />
                </td>
                <td className="match-status">{row.match_status}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
