import assert from 'node:assert/strict';
import test from 'node:test';
import { createVeilleSatelliteLayer } from './index.js';
import { normalizeVeilleSnapshot } from './records.js';
import { createVeilleSatelliteSource } from './source.js';

const fire = {
  type: 'Feature',
  id: 4,
  geometry: { type: 'Point', coordinates: [-7.6, 6.05] },
  properties: {
    id: 4,
    type: 'feu',
    statut: 'nouveau',
    priorite: 'haute',
    titre: 'Point chaud à vérifier : 3 détections',
    region: 'Cavally',
    zone_pilote: 1,
    score: 0.9,
    acquis_le: '2026-10-29T13:25:00+00:00',
  },
};
const ring = [
  [-7.62, 6.06],
  [-7.61, 6.06],
  [-7.61, 6.07],
  [-7.62, 6.06],
];
const loss = {
  type: 'Feature',
  id: 17,
  geometry: {
    type: 'GeometryCollection',
    geometries: [{ type: 'Polygon', coordinates: [ring] }],
  },
  properties: {
    id: 17,
    type: 'vegetation',
    statut: 'rejete',
    priorite: 'moyenne',
    titre: 'Baisse de végétation à vérifier : 11.7 ha',
    region: 'Cavally',
    zone_pilote: 1,
    score: 0.63,
    acquis_le: '2026-03-12T10:59:38Z',
  },
};

test('fires become points and losses become outlines', () => {
  const rows = normalizeVeilleSnapshot({ features: [fire, loss] });
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0].point, [-7.6, 6.05]);
  assert.equal(rows[0].pilotZone, true);
  assert.equal(rows[1].polygons.length, 1);
  assert.equal(rows[1].status, 'rejete');
});

test('a malformed or duplicated alert rejects the whole snapshot', () => {
  assert.equal(normalizeVeilleSnapshot({}), null);
  assert.equal(normalizeVeilleSnapshot({ features: [fire, fire] }), null);
  const badStatus = structuredClone(fire);
  badStatus.properties.statut = 'peut-etre';
  assert.equal(normalizeVeilleSnapshot({ features: [badStatus] }), null);
  const badPoint = structuredClone(fire);
  badPoint.geometry.coordinates = [400, 6];
  assert.equal(normalizeVeilleSnapshot({ features: [badPoint] }), null);
});

test('the source needs an absolute service URL and reads the export', async () => {
  assert.throws(() => createVeilleSatelliteSource({}), TypeError);
  const asked = [];
  const source = createVeilleSatelliteSource({
    baseUrl: 'https://veille.example.org/',
    fetchImpl: async (url, init) => {
      asked.push([url, init.credentials]);
      return { ok: true, json: async () => ({ features: [fire] }) };
    },
  });
  const rows = await source.getSnapshot();
  assert.deepEqual(asked, [
    ['https://veille.example.org/api/alertes.geojson', 'omit'],
  ]);
  assert.equal(rows.length, 1);
  assert.equal(
    source.reportUrl(4),
    'https://veille.example.org/api/alertes/4/rapport',
  );
});

test('a late refresh cannot publish after disable', async () => {
  let resolve;
  let signal;
  const viewer = { dataSources: { add() {}, remove() {} } };
  const layer = createVeilleSatelliteLayer({
    source: {
      getSnapshot(options) {
        signal = options.signal;
        return new Promise((done) => {
          resolve = done;
        });
      },
    },
  });
  layer.init(viewer);
  layer.enable(viewer);
  const pending = layer.update(viewer);
  layer.disable(viewer);
  assert.equal(signal.aborted, true);
  resolve(normalizeVeilleSnapshot({ features: [fire] }));
  assert.equal(await pending, false);
  assert.equal(layer.getStats().count, 0);
  layer.destroy(viewer);
});
