"""Points chauds NASA FIRMS (VIIRS) : téléchargement, filtrage pays et création des signaux."""
import csv
import hashlib
import json
import math
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

from . import config, db, zones

LIMITES = ("Un point chaud n'est pas toujours un incendie : brûlage agricole, torchère ou surface chaude "
           "peuvent aussi le déclencher. Les nuages, la fumée et l'heure de passage peuvent masquer un feu.")
TAILLE_GROUPE_DEG = 0.02  # environ 2 km
_verrou = threading.Lock()


def _lire_flux(satellite: str, url: str) -> list[dict]:
    ouest, sud, est, nord = config.EMPRISE_PAYS
    points = []
    with requests.get(url, stream=True, timeout=(20, 180)) as reponse:
        reponse.raise_for_status()
        lignes = reponse.iter_lines(decode_unicode=True)
        for ligne in csv.DictReader(lignes):
            try:
                lat, lon = float(ligne["latitude"]), float(ligne["longitude"])
            except (TypeError, ValueError):
                continue
            if not (sud <= lat <= nord and ouest <= lon <= est):
                continue
            region = zones.region_du_point(lon, lat)
            if region is None:
                continue
            heure = ligne["acq_time"].zfill(4)
            points.append({
                "lon": lon, "lat": lat, "region": region, "satellite": satellite,
                "acquis_le": f"{ligne['acq_date']}T{heure[:2]}:{heure[2:]}:00+00:00",
                "date": ligne["acq_date"], "confiance": ligne["confidence"],
                "puissance_mw": float(ligne["frp"] or 0), "jour_nuit": ligne["daynight"],
            })
    return points


def grouper(points: list[dict]) -> dict:
    """Regroupe par jour et par maille de 2 km les points fiables, pour éviter une alerte par pixel."""
    groupes = defaultdict(list)
    for point in points:
        if point["confiance"] in ("nominal", "high"):
            maille = (math.floor(point["lat"] / TAILLE_GROUPE_DEG), math.floor(point["lon"] / TAILLE_GROUPE_DEG))
            groupes[(point["date"], maille)].append(point)
    return groupes


def priorite_groupe(groupe: list[dict]) -> str | None:
    """None = pas d'alerte. En zone pilote, tout groupe fiable compte ; ailleurs il faut une confirmation."""
    pilote = groupe[0]["region"] == config.ZONE_PILOTE
    fort = any(p["confiance"] == "high" for p in groupe)
    if pilote:
        return "haute" if fort or len(groupe) >= 3 else "moyenne"
    if len(groupe) >= 5 or (fort and len(groupe) >= 2):
        return "moyenne"
    return "basse" if len(groupe) >= 2 or fort else None


def _creer_alertes(points: list[dict], recu_le: str, empreinte: str) -> int:
    creees = 0
    for (date, maille), groupe in grouper(points).items():
        priorite = priorite_groupe(groupe)
        if priorite is None:
            continue
        lon = sum(p["lon"] for p in groupe) / len(groupe)
        lat = sum(p["lat"] for p in groupe) / len(groupe)
        fort = any(p["confiance"] == "high" for p in groupe)
        satellites = sorted({p["satellite"] for p in groupe})
        alerte_id = db.creer_alerte({
            "cle": f"feu:{date}:{maille[0]}:{maille[1]}",
            "type": "feu", "priorite": priorite,
            "titre": f"Point chaud à vérifier : {len(groupe)} détection{'s' if len(groupe) > 1 else ''}",
            "region": groupe[0]["region"], "zone_pilote": int(groupe[0]["region"] == config.ZONE_PILOTE),
            "geometrie": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "score": 0.9 if fort else 0.6,
            "acquis_le": min(p["acquis_le"] for p in groupe), "recu_le": recu_le,
            "source": "NASA FIRMS VIIRS", "capteur": " + ".join(satellites), "resolution_m": 375,
            "nuages_pct": None, "version_regle": "regle-feux-v1",
            "preuves": {"points": len(groupe), "puissance_max_mw": max(p["puissance_mw"] for p in groupe),
                        "confiances": sorted({p["confiance"] for p in groupe}),
                        "licence": config.LICENCE_FIRMS, "uris": [config.FLUX_FIRMS[s].format(periode="24h") for s in satellites],
                        "sha256_extrait": empreinte},
            "limites": LIMITES,
        })
        creees += alerte_id is not None
    return creees


def fusionner(anciens: list[dict], nouveaux: list[dict]) -> list[dict]:
    """Réunit cache et nouveau fichier sans doublon, en ne gardant que les derniers jours."""
    limite = (datetime.now(timezone.utc) - timedelta(days=config.JOURS_CONSERVES_FEUX)).isoformat()
    uniques = {(p["satellite"], p["acquis_le"], p["lat"], p["lon"]): p for p in anciens + nouveaux}
    return sorted((p for p in uniques.values() if p["acquis_le"] >= limite), key=lambda p: p["acquis_le"])


def _lire_cache() -> dict | None:
    try:
        return json.loads(config.CACHE_FEUX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def actualiser(forcer: bool = False) -> dict:
    """Renvoie le cache tout de suite ; s'il est ancien, la mise à jour se fait en arrière-plan.
    Sans cache (premier lancement), on attend le téléchargement."""
    cache = _lire_cache()
    age = datetime.now(timezone.utc) - datetime.fromisoformat(cache["recu_le"]) if cache and cache["recu_le"] else None
    if age is not None and age < timedelta(minutes=config.ACTUALISATION_FORCEE_MINUTES):
        forcer = False
    if age is not None and not forcer and age < timedelta(hours=config.CACHE_FEUX_HEURES):
        return cache
    if age is None:
        with _verrou:
            return _telecharger(_lire_cache())
    if _verrou.acquire(blocking=False):
        def tache():
            try:
                _telecharger(_lire_cache())
            finally:
                _verrou.release()
        threading.Thread(target=tache, daemon=True).start()
    return dict(cache, actualisation=True)


def _telecharger(cache: dict | None) -> dict:
    age = datetime.now(timezone.utc) - datetime.fromisoformat(cache["recu_le"]) if cache and cache["recu_le"] else None
    # Le fichier 24 h suffit si le cache date de moins de 20 heures : rien n'est perdu entre les deux.
    periode = "24h" if age is not None and age < timedelta(hours=20) else "7d"

    points, erreurs = [], []
    for satellite, url in config.FLUX_FIRMS.items():
        try:
            points += _lire_flux(satellite, url.format(periode=periode))
        except (requests.RequestException, ValueError, KeyError) as erreur:
            erreurs.append(f"{satellite} : {type(erreur).__name__}")

    if not points and erreurs:
        if cache:
            return dict(cache, erreur="Flux NASA injoignable : données plus anciennes affichées.")
        return {"recu_le": None, "points": [], "erreur": "Flux NASA injoignable.", "alertes_creees": 0}

    points = fusionner(cache["points"] if cache and periode == "24h" else [], points)
    recu_le = db.maintenant()
    empreinte = hashlib.sha256(json.dumps(points, sort_keys=True).encode()).hexdigest()
    resultat = {"recu_le": recu_le, "points": points, "sha256": empreinte,
                "erreur": "; ".join(erreurs) or None,
                "alertes_creees": _creer_alertes(points, recu_le, empreinte)}
    config.CACHE_FEUX.write_text(json.dumps(resultat), encoding="utf-8")
    return resultat


def en_geojson(donnees: dict, jours: int) -> dict:
    limite = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()
    retenus = [p for p in donnees["points"] if p["acquis_le"] >= limite]
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                      "properties": {k: v for k, v in p.items() if k not in ("lon", "lat")}} for p in retenus],
        "meta": {"recu_le": donnees["recu_le"], "erreur": donnees.get("erreur"),
                 "actualisation": donnees.get("actualisation", False),
                 "dernier_passage": max((p["acquis_le"] for p in retenus), default=None),
                 "source": config.LICENCE_FIRMS, "jours": jours},
    }
