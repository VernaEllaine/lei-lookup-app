/** Minimal RFC 4180 CSV parser — returns {headers, rows}. */
export function parseCSV(text: string): { headers: string[]; rows: string[][] } {
  const firstLine = text.split('\n')[0] ?? '';
  const commas = (firstLine.match(/,/g) ?? []).length;
  const semis = (firstLine.match(/;/g) ?? []).length;
  const tabs = (firstLine.match(/\t/g) ?? []).length;
  const delim = semis > commas ? ';' : tabs > commas ? '\t' : ',';

  const lines = text.replace(/\r\n?/g, '\n').trimEnd().split('\n');
  const parse = (line: string): string[] => {
    const cells: string[] = [];
    let cur = '';
    let inQuote = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (inQuote) {
        if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (ch === '"') { inQuote = false; }
        else { cur += ch; }
      } else {
        if (ch === '"') { inQuote = true; }
        else if (ch === delim) { cells.push(cur.trim()); cur = ''; }
        else { cur += ch; }
      }
    }
    cells.push(cur.trim());
    return cells;
  };

  const [headerLine, ...dataLines] = lines;
  const headers = parse(headerLine ?? '');
  const rows = dataLines.filter(Boolean).map(parse);
  return { headers, rows };
}

/** Trigger a browser CSV download from a string. */
export function downloadCsv(content: string, filename: string): void {
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
