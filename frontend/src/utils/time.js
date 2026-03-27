export const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

export const LOCAL_TZ_ABBR = (() => {
  try {
    return new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' })
      .formatToParts(new Date())
      .find(p => p.type === 'timeZoneName')?.value || '';
  } catch { return ''; }
})();

export const IST_TZ = 'Asia/Kolkata';

export const IST_TZ_ABBR = 'IST';

export function nowIST() {
  return new Date().toLocaleTimeString('en-IN', {
    timeZone: IST_TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

export function nowLocal() {
  return new Date().toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

export function formatLocalTime(isoStr) {
  if (!isoStr) return '—';
  return new Date(isoStr).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }) + ' ' + LOCAL_TZ_ABBR;
}

export function formatLocalDateTime(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }) + ' ' + LOCAL_TZ_ABBR;
}

export function formatISTTime(isoStr) {
  if (!isoStr) return '—';
  return new Date(isoStr).toLocaleTimeString('en-IN', {
    timeZone: IST_TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }) + ' IST';
}

export function formatChartLabel(isoStr, timeframe) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  if (['5m', '1H'].includes(timeframe)) {
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
  }
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
