"""Recherche de scènes Sentinel-2 dans le catalogue STAC public."""
import re
from urllib.parse import urlparse

import requests

from . import config, db, zones

MOTIF_SCENE = re.compile(r"^S2[A-D]_[0-9A-Z]{5}_\d{8}_\d+_L2A$")
SEQUENCE = re.compile(r"_(\d+)_L2A$")


def rechercher(bbox: tuple, debut: str, fin: str, nuages_max: float) -> list[dict]:
    reponse = requests.post(config.STAC_RECHERCHE, timeout=(15, 60), json={
        "collections": [config.COLLECTION],
        "bbox": list(bbox),
        "datetime": f"{debut}T00:00:00Z/{fin}T23:59:59Z",
        "limit": 100,
        "query": {"eo:cloud_cover": {"lte": nuages_max}},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    })
    reponse.raise_for_status()

    # Une même prise peut être retraitée : on garde sa version la plus récente.
    uniques: dict[tuple, dict] = {}
    for item in reponse.json().get("features", []):
        proprietes = item["properties"]
        couverture = zones.part_de_zone_couverte(bbox, item["geometry"])
        if couverture < 80:
            continue
        cle = (proprietes["datetime"][:10], proprietes.get("grid:code"), proprietes["platform"])
        sequence = int(SEQUENCE.search(item["id"]).group(1))
        if cle not in uniques or sequence > uniques[cle]["sequence"]:
            uniques[cle] = {
                "id": item["id"], "sequence": sequence, "date": proprietes["datetime"][:10],
                "acquis_le": proprietes["datetime"], "satellite": proprietes["platform"].capitalize(),
                "nuages_pct": round(proprietes["eo:cloud_cover"], 1), "tuile": proprietes.get("grid:code"),
                "couverture_zone_pct": round(couverture),
            }
    return sorted(uniques.values(), key=lambda s: s["acquis_le"], reverse=True)


def lire_item(scene_id: str) -> dict:
    """Relit une scène depuis le catalogue et n'accepte que les hôtes d'images attendus."""
    if not MOTIF_SCENE.match(scene_id):
        raise ValueError("Identifiant de scène invalide.")
    reponse = requests.get(f"{config.STAC_ITEMS}/{scene_id}", timeout=(15, 60))
    reponse.raise_for_status()
    item = reponse.json()
    for bande in ("red", "nir", "scl", "visual"):
        if urlparse(item["assets"][bande]["href"]).hostname not in config.HOTES_IMAGES:
            raise ValueError("Source d'image non autorisée.")
    proprietes = item["properties"]
    db.enregistrer_scene({
        "id": item["id"], "fournisseur": "Earth Search (AWS Open Data)", "collection": config.COLLECTION,
        "capteur": f"{proprietes['platform']} MSI", "acquis_le": proprietes["datetime"],
        "emprise": str(item["bbox"]), "resolution_m": config.RESOLUTION_M,
        "nuages_pct": proprietes["eo:cloud_cover"], "niveau": f"L2A, chaîne {proprietes.get('s2:processing_baseline')}",
        "licence": config.LICENCE_S2, "uri": f"{config.STAC_ITEMS}/{item['id']}", "recu_le": db.maintenant(),
    })
    return item
