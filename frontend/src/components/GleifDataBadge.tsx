import { useEffect, useState } from 'react';
import type { GleifLocalStatus } from '../types';
import { getGleifLocalStatus } from '../api';

const STALE_AFTER_DAYS = 3;
const REFRESH_MS = 30 * 60 * 1000;

function formatCount(value?: string): string {
  const n = Number(value);
  if (!n) return '';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

function parseDate(value?: string): Date | null {
  if (!value) return null;
  const d = new Date(value.slice(0, 10) + 'T00:00:00');
  return isNaN(d.getTime()) ? null : d;
}

export default function GleifDataBadge() {
  const [status, setStatus] = useState<GleifLocalStatus | null>(null);

  useEffect(() => {
    const load = () => getGleifLocalStatus().then(setStatus).catch(() => setStatus(null));
    load();
    const id = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(id);
  }, []);

  if (!status) return null;

  if (!status.available) {
    return <p className="gleif-badge">Live GLEIF API</p>;
  }

  const published = parseDate(status.publish_date);
  const ageDays = published ? (Date.now() - published.getTime()) / 86_400_000 : Infinity;
  const stale = ageDays > STALE_AFTER_DAYS;
  const dateText = published
    ? published.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
    : 'unknown date';
  const leis = formatCount(status.lei_count);

  return (
    <p
      className={`gleif-badge${stale ? ' stale' : ''}`}
      title={[
        status.isin_count && `${formatCount(status.isin_count)} ISIN mappings`,
        status.loaded_at && `loaded ${status.loaded_at}`,
        stale && 'Local copy is out of date; check the scheduled refresh.',
      ].filter(Boolean).join(' · ')}
    >
      GLEIF data: {dateText}
      {leis && ` · ${leis} LEIs`}
    </p>
  );
}
