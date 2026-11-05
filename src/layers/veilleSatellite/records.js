/**
 * Satellite-watch alerts from the companion Python service (veille-satellite/).
 * Its GeoJSON export carries fire hotspots as Points and vegetation-loss
 * outlines as a GeometryCollection of Polygons. Field names stay in the
 * service's French, as they are data, not UI text.
 */
const TYPES = new Set(['feu', 'vegetation']);
const STATUSES = new Set([
  'nouveau',
  'a_examiner',
  'confirme',
  'rejete',
  'publie',
  'cloture',
]);
const PRIORITIES = new Set(['haute', 'moyenne', 'basse']);

const isPosition = (p) =>
  Array.isArray(p) &&
  p.length >= 2 &&
  Number.isFinite(p[0]) &&
  Math.abs(p[0]) <= 180 &&
  Number.isFinite(p[1]) &&
  Math.abs(p[1]) <= 90;

function readPolygon(geometry) {
  const ring = geometry?.coordinates?.[0];
  if (geometry?.type !== 'Polygon' || !Array.isArray(ring) || ring.length < 4)
    return null;
  return ring.every(isPosition) ? ring.map(([lon, lat]) => [lon, lat]) : null;
}

/** Validate a complete export before it can replace the displayed alerts. */
export function normalizeVeilleSnapshot(geojson) {
  if (!Array.isArray(geojson?.features)) return null;
  const rows = [];
  const ids = new Set();
  for (const feature of geojson.features) {
    const properties = feature?.properties;
    const geometry = feature?.geometry;
    if (!properties || typeof properties !== 'object') return null;
    const id = feature.id ?? properties.id;
    if (!Number.isInteger(id) || ids.has(id)) return null;
    ids.add(id);
    if (
      !TYPES.has(properties.type) ||
      !STATUSES.has(properties.statut) ||
      !PRIORITIES.has(properties.priorite)
    )
      return null;

    let point = null;
    let polygons = [];
    if (geometry?.type === 'Point') {
      if (!isPosition(geometry.coordinates)) return null;
      point = [geometry.coordinates[0], geometry.coordinates[1]];
    } else if (geometry?.type === 'GeometryCollection') {
      polygons = (geometry.geometries ?? []).map(readPolygon);
      if (polygons.some((p) => p === null)) return null;
      // A loss with no outline left (all filtered out) has nothing to draw.
      if (!polygons.length) continue;
    } else {
      return null;
    }

    rows.push({
      stableId: `veille-${id}`,
      alertId: id,
      type: properties.type,
      status: properties.statut,
      priority: properties.priorite,
      title: typeof properties.titre === 'string' ? properties.titre : '',
      region: typeof properties.region === 'string' ? properties.region : null,
      pilotZone: properties.zone_pilote === 1,
      score: Number.isFinite(properties.score) ? properties.score : null,
      acquiredAt:
        typeof properties.acquis_le === 'string' ? properties.acquis_le : null,
      point,
      polygons,
    });
  }
  return rows;
}
