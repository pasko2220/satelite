"""Limites administratives et tests géographiques simples."""
import json
from functools import lru_cache

from . import config


@lru_cache
def regions() -> dict:
    return json.loads(config.REGIONS.read_text(encoding="utf-8"))


def _dans_anneau(lon: float, lat: float, anneau: list) -> bool:
    dedans = False
    x1, y1 = anneau[-1][:2]
    for point in anneau:
        x2, y2 = point[:2]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            dedans = not dedans
        x1, y1 = x2, y2
    return dedans


def point_dans_geometrie(lon: float, lat: float, geometrie: dict) -> bool:
    polygones = geometrie["coordinates"]
    if geometrie["type"] == "Polygon":
        polygones = [polygones]
    for polygone in polygones:
        if _dans_anneau(lon, lat, polygone[0]) and not any(_dans_anneau(lon, lat, trou) for trou in polygone[1:]):
            return True
    return False


def region_du_point(lon: float, lat: float) -> str | None:
    for region in regions()["features"]:
        if point_dans_geometrie(lon, lat, region["geometry"]):
            return region["properties"]["shapeName"]
    return None


def part_de_zone_couverte(bbox: tuple, geometrie: dict, n: int = 5) -> float:
    """Pourcentage de la zone couvert par l'emprise d'une scène, estimé sur une grille de points."""
    ouest, sud, est, nord = bbox
    dedans = 0
    for i in range(n):
        for j in range(n):
            lon = ouest + (est - ouest) * (i + 0.5) / n
            lat = sud + (nord - sud) * (j + 0.5) / n
            dedans += point_dans_geometrie(lon, lat, geometrie)
    return 100 * dedans / n**2
