import * as Cesium from 'cesium';
export { normalizeVeilleSnapshot } from './records.js';
export { createVeilleSatelliteSource } from './source.js';

// Status drives the look, so a rejected signal never reads as an active one.
// Shape and size also change with status: colour is never the only cue.
const STATUS_STYLES = Object.freeze({
  nouveau: { css: '#ffd23f', pixelSize: 11, outline: 2 },
  a_examiner: { css: '#ffd23f', pixelSize: 11, outline: 2 },
  confirme: { css: '#ff3d6e', pixelSize: 14, outline: 3 },
  publie: { css: '#ff3d6e', pixelSize: 14, outline: 3 },
  rejete: { css: '#9aa5a0', pixelSize: 7, outline: 1 },
  cloture: { css: '#9aa5a0', pixelSize: 7, outline: 1 },
});

/** Own one display of the satellite-watch alerts and its refresh lifecycle. */
export function createVeilleSatelliteLayer({ source } = {}) {
  if (typeof source?.getSnapshot !== 'function')
    throw new TypeError('Satellite watch requires a snapshot source');
  let _viewer = null;
  let _request = null;
  let _dataSource = null;
  let _count = 0;
  let _lastUpdate = null;
  let _lastError = null;
  let _enabled = false;

  function entitiesFor(row) {
    const style = STATUS_STYLES[row.status];
    const color = Cesium.Color.fromCssColorString(style.css);
    const properties = {
      alertId: row.alertId,
      type: row.type,
      status: row.status,
      priority: row.priority,
      region: row.region,
      score: row.score,
      acquiredAt: row.acquiredAt,
      reportUrl: source.reportUrl?.(row.alertId) ?? null,
    };
    if (row.point) {
      return [
        new Cesium.Entity({
          id: `veille:${row.stableId}`,
          name: row.title,
          position: Cesium.Cartesian3.fromDegrees(row.point[0], row.point[1]),
          point: {
            pixelSize: style.pixelSize,
            color: color.withAlpha(0.9),
            outlineColor: Cesium.Color.BLACK.withAlpha(0.8),
            outlineWidth: style.outline,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
          properties,
        }),
      ];
    }
    return row.polygons.map(
      (ring, index) =>
        new Cesium.Entity({
          id: `veille:${row.stableId}:${index}`,
          name: row.title,
          polygon: {
            hierarchy: Cesium.Cartesian3.fromDegreesArray(ring.flat()),
            material: color.withAlpha(0.35),
            classificationType: Cesium.ClassificationType.BOTH,
          },
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArray(ring.flat()),
            width: style.outline + 1,
            material: color,
            clampToGround: true,
          },
          properties,
        }),
    );
  }

  return {
    id: 'veille-satellite',
    name: 'Satellite Watch Alerts',
    icon: '🛰',
    source: 'Veille Satellite', // i18n-ignore: product name
    updateInterval: 300000,

    init(viewer) {
      if (_viewer) throw new Error('Satellite watch is already initialized');
      _viewer = viewer;
      _dataSource = new Cesium.CustomDataSource('veille-satellite');
      _dataSource.show = false;
      viewer.dataSources.add(_dataSource);
      _count = 0;
      _lastUpdate = null;
      _lastError = null;
      _enabled = false;
    },

    enable() {
      _enabled = true;
      if (_dataSource) _dataSource.show = true;
    },

    disable() {
      _request?.abort();
      _request = null;
      _enabled = false;
      if (_dataSource) _dataSource.show = false;
    },

    async update() {
      if (!_enabled || !_dataSource) return false;
      _request?.abort();
      const request = new AbortController();
      _request = request;
      try {
        const rows = await source.getSnapshot({ signal: request.signal });
        if (request.signal.aborted || _request !== request || !_enabled)
          return false;
        const next = rows.flatMap(entitiesFor);
        _dataSource.entities.suspendEvents();
        _dataSource.entities.removeAll();
        for (const entity of next) _dataSource.entities.add(entity);
        _dataSource.entities.resumeEvents();
        _count = rows.length;
        _lastUpdate = Date.now();
        _lastError = null;
        return true;
      } catch (e) {
        if (request.signal.aborted || _request !== request || !_enabled)
          return false;
        console.warn('[Data:VeilleSatellite] Fetch error:', e);
        _lastError = e?.message ?? String(e);
        return false;
      } finally {
        if (_request === request) _request = null;
      }
    },

    destroy(viewer = _viewer) {
      _request?.abort();
      _request = null;
      _viewer = null;
      _enabled = false;
      if (_dataSource) {
        viewer?.dataSources.remove(_dataSource, true);
        _dataSource = null;
      }
      _count = 0;
      _lastUpdate = null;
      _lastError = null;
    },

    getStats() {
      return { count: _count, lastUpdate: _lastUpdate, error: _lastError };
    },
  };
}
