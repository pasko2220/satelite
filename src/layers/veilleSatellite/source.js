import { normalizeVeilleSnapshot } from './records.js';

/** Request and validate the companion service's alert export. */
export function createVeilleSatelliteSource({
  baseUrl,
  fetchImpl = (...args) => globalThis.fetch(...args),
} = {}) {
  const root = String(baseUrl ?? '').replace(/\/+$/, '');
  if (!/^https?:\/\/[^/]+/.test(root))
    throw new TypeError('Satellite watch needs the service base URL');
  return {
    baseUrl: root,
    /** Analyst page for one alert, served by the companion service. */
    reportUrl(alertId) {
      return `${root}/api/alertes/${encodeURIComponent(alertId)}/rapport`;
    },
    async getSnapshot({ signal } = {}) {
      signal?.throwIfAborted();
      const response = await fetchImpl(`${root}/api/alertes.geojson`, {
        signal,
        credentials: 'omit',
      });
      if (!response.ok)
        throw new Error(`Satellite watch HTTP ${response.status}`);
      const payload = await response.json();
      signal?.throwIfAborted();
      const rows = normalizeVeilleSnapshot(payload);
      if (!rows) throw new Error('Malformed satellite watch response');
      return rows;
    },
  };
}
