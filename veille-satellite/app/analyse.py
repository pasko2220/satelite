"""Comparaison de deux dates Sentinel-2 : lecture, masque nuages, NDVI, perte de végétation."""
import hashlib
import json
import math
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import numpy as np
from PIL import Image
from rasterio import Affine, Env
from rasterio import open as ouvrir
from rasterio.enums import Resampling
from rasterio.errors import WindowError
from rasterio.features import shapes, sieve
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform, transform_bounds, transform_geom
from rasterio.windows import Window, from_bounds

from . import config, db, stac, zones

# Classes SCL Sentinel-2 exploitables : végétation, sol nu, eau, non classé.
SCL_UTILES = (4, 5, 6, 7)
COULEURS_NDVI = ((-0.2, (140, 81, 10)), (0.1, (223, 194, 125)), (0.35, (199, 233, 160)),
                 (0.6, (65, 171, 93)), (0.85, (0, 68, 27)))
COULEUR_PERTE = (255, 0, 160, 235)
COULEUR_NON_COMPARABLE = (120, 120, 120, 120)
VERSION = f"{config.VERSION_REGLE}-2"
LIMITES = ("Changement observé entre deux images : la cause n'est pas connue. Saison, récolte, sécheresse, "
           "nuages ou ombres non détectés peuvent produire le même signal. Vérification terrain nécessaire. "
           "Règle à seuil simple, pas un modèle d'IA entraîné.")

_travaux = ThreadPoolExecutor(max_workers=1)
_etats: dict[str, dict] = {}


@dataclass(frozen=True)
class Grille:
    """Grille commune en Web Mercator : les deux dates sont comparées pixel à pixel."""
    ouest: float
    nord: float
    largeur: int
    hauteur: int
    resolution: float
    latitude: float

    @property
    def transform(self):
        return from_origin(self.ouest, self.nord, self.resolution, self.resolution)

    @property
    def limites(self) -> tuple:
        return (self.ouest, self.nord - self.hauteur * self.resolution,
                self.ouest + self.largeur * self.resolution, self.nord)

    @property
    def hectares_par_pixel(self) -> float:
        return (self.resolution * math.cos(math.radians(self.latitude))) ** 2 / 10_000


def creer_grille(bbox: tuple) -> Grille:
    ouest, sud, est, nord = bbox
    xs, ys = transform("EPSG:4326", "EPSG:3857", [ouest, est], [sud, nord])
    largeur_m, hauteur_m = xs[1] - xs[0], ys[1] - ys[0]
    resolution = max(config.RESOLUTION_M, max(largeur_m, hauteur_m) / config.PIXELS_MAX)
    return Grille(xs[0], ys[1], max(1, round(largeur_m / resolution)), max(1, round(hauteur_m / resolution)),
                  resolution, (sud + nord) / 2)


def lire_bande(url: str, grille: Grille, reechantillonnage: Resampling, bandes: list[int], dtype: str) -> np.ndarray:
    """Lit seulement la portion utile de l'image distante et la ramène sur la grille."""
    sortie = np.zeros((len(bandes), grille.hauteur, grille.largeur), dtype=dtype)
    with Env(), ouvrir(url) as src:
        limites = transform_bounds("EPSG:3857", src.crs, *grille.limites, densify_pts=21)
        fenetre = from_bounds(*limites, src.transform).round_offsets(op="floor").round_lengths(op="ceil")
        entiere = Window(0, 0, src.width, src.height)
        try:
            fenetre = fenetre.intersection(entiere)
        except WindowError:
            return sortie
        cible_l = max(1, min(fenetre.width, math.ceil(fenetre.width * src.res[0] / grille.resolution)))
        cible_h = max(1, min(fenetre.height, math.ceil(fenetre.height * src.res[1] / grille.resolution)))
        donnees = src.read(bandes, window=fenetre, out_shape=(len(bandes), cible_h, cible_l),
                           resampling=reechantillonnage)
        transformee = src.window_transform(fenetre) * Affine.scale(
            fenetre.width / cible_l, fenetre.height / cible_h)
        reproject(donnees, sortie, src_transform=transformee, src_crs=src.crs, src_nodata=0,
                  dst_transform=grille.transform, dst_crs="EPSG:3857", dst_nodata=0,
                  resampling=reechantillonnage)
    return sortie


def corriger_reflectance(valeurs: np.ndarray, item: dict) -> np.ndarray:
    """Le décalage de 1000 est déjà appliqué aux images quand boa_offset_applied est vrai,
    même si les métadonnées de bande indiquent encore -0,1 (vérifié sur des pixels de forêt)."""
    proprietes = item["properties"]
    chaine = float(proprietes.get("s2:processing_baseline") or 0)
    if chaine >= 4 and not proprietes.get("earthsearch:boa_offset_applied", False):
        return np.where(valeurs > 1000, valeurs - 1000, 1).astype("float32")
    return valeurs.astype("float32")


def dilater(masque: np.ndarray, iterations: int) -> np.ndarray:
    resultat = masque.copy()
    for _ in range(iterations):
        voisins = resultat.copy()
        voisins[1:, :] |= resultat[:-1, :]
        voisins[:-1, :] |= resultat[1:, :]
        voisins[:, 1:] |= resultat[:, :-1]
        voisins[:, :-1] |= resultat[:, 1:]
        resultat = voisins
    return resultat


def masque_utile(scl: np.ndarray, resolution: float, bleu: np.ndarray | None = None) -> np.ndarray:
    """Pixels fiables, avec une marge autour des nuages et ombres dont les bords trompent le NDVI."""
    observe = scl > 0
    douteux = observe & ~np.isin(scl, SCL_UTILES)
    if bleu is not None:
        douteux |= observe & (bleu > config.BLEU_NUAGE)
    return observe & ~dilater(douteux, round(config.MARGE_NUAGES_M / resolution))


def calculer_ndvi(rouge: np.ndarray, pir: np.ndarray) -> np.ndarray:
    somme = pir + rouge
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(somme > 0, (pir - rouge) / somme, np.nan).astype("float32")


def detecter_perte(ndvi_avant: np.ndarray, ndvi_apres: np.ndarray, comparable: np.ndarray,
                   hectares_par_pixel: float) -> np.ndarray:
    brute = comparable & (ndvi_avant >= config.NDVI_VEGETATION) & \
        ((ndvi_apres - ndvi_avant) <= -config.BAISSE_NDVI)
    pixels_min = max(2, round(config.SURFACE_MIN_HA / hectares_par_pixel))
    return sieve(brute.astype("uint8"), size=pixels_min, connectivity=8).astype(bool) & brute


def _surface_m2(geometrie: dict) -> float:
    def anneau(points):
        return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:]))) / 2
    exterieur, *trous = geometrie["coordinates"]
    return anneau(exterieur) - sum(anneau(t) for t in trous)


def extraire_polygones(perte: np.ndarray, grille: Grille, maximum: int = 50) -> list[dict]:
    polygones = []
    echelle = math.cos(math.radians(grille.latitude)) ** 2 / 10_000
    for geometrie, _ in shapes(perte.astype("uint8"), mask=perte, transform=grille.transform, connectivity=8):
        polygones.append({"hectares": round(_surface_m2(geometrie) * echelle, 2), "geometrie": geometrie})
    polygones.sort(key=lambda p: p["hectares"], reverse=True)
    for polygone in polygones[:maximum]:
        polygone["geometrie"] = transform_geom("EPSG:3857", "EPSG:4326", polygone["geometrie"], precision=6)
    return polygones[:maximum]


def image_ndvi(ndvi: np.ndarray, masque: np.ndarray) -> np.ndarray:
    seuils = [s for s, _ in COULEURS_NDVI]
    rgba = np.zeros((*ndvi.shape, 4), dtype="uint8")
    for canal in range(3):
        rgba[..., canal] = np.interp(np.nan_to_num(ndvi), seuils, [c[canal] for _, c in COULEURS_NDVI])
    rgba[..., 3] = masque * 255
    return rgba


def image_changement(perte: np.ndarray, comparable: np.ndarray) -> np.ndarray:
    rgba = np.zeros((*perte.shape, 4), dtype="uint8")
    rgba[~comparable] = COULEUR_NON_COMPARABLE
    rgba[perte] = COULEUR_PERTE
    return rgba


def _sauver(dossier, nom: str, rgba: np.ndarray, empreintes: dict) -> None:
    chemin = dossier / nom
    Image.fromarray(rgba).save(chemin, optimize=False)
    empreintes[nom] = hashlib.sha256(chemin.read_bytes()).hexdigest()


def _lire_scene(item: dict, grille: Grille, avancer, nom: str) -> dict:
    actifs = item["assets"]
    avancer(f"{nom} : bande rouge")
    rouge = lire_bande(actifs["red"]["href"], grille, Resampling.bilinear, [1], "uint16")[0]
    avancer(f"{nom} : proche infrarouge")
    pir = lire_bande(actifs["nir"]["href"], grille, Resampling.bilinear, [1], "uint16")[0]
    avancer(f"{nom} : masque de nuages")
    scl = lire_bande(actifs["scl"]["href"], grille, Resampling.nearest, [1], "uint8")[0]
    avancer(f"{nom} : image en couleurs")
    visuel = lire_bande(actifs["visual"]["href"], grille, Resampling.bilinear, [1, 2, 3], "uint8")
    masque = masque_utile(scl, grille.resolution, visuel[2]) & (rouge > 0) & (pir > 0)
    ndvi = calculer_ndvi(corriger_reflectance(rouge, item), corriger_reflectance(pir, item))
    rgba = np.dstack([visuel[0], visuel[1], visuel[2], (visuel.max(axis=0) > 0) * 255]).astype("uint8")
    return {"ndvi": ndvi, "masque": masque, "rgba": rgba, "item": item}


def _mediane(scene: dict) -> float | None:
    return round(float(np.nanmedian(scene["ndvi"][scene["masque"]])), 3) if scene["masque"].any() else None


def avertissements(item_avant: dict, item_apres: dict, comparable_pct: float) -> list[str]:
    messages = []
    jour_a = date.fromisoformat(item_avant["properties"]["datetime"][:10])
    jour_b = date.fromisoformat(item_apres["properties"]["datetime"][:10])
    if jour_b <= jour_a:
        messages.append("L'image « après » n'est pas postérieure à l'image « avant ».")
    ecart = abs(jour_a.timetuple().tm_yday - jour_b.timetuple().tm_yday)
    ecart = min(ecart, 365 - ecart)
    if ecart > config.ECART_SAISON_JOURS:
        messages.append(f"Dates à {ecart} jours d'écart dans l'année : la saison peut expliquer une partie "
                        "du changement.")
    if comparable_pct < config.COMPARABLE_MIN_PCT:
        messages.append(f"Seulement {comparable_pct:.0f} % de la zone est comparable (nuages, ombres ou "
                        "absence d'image) : résultat partiel.")
    return messages


def _executer(analyse_id: str, bbox: tuple, id_avant: str, id_apres: str) -> None:
    dossier = config.ANALYSES / analyse_id
    dossier.mkdir(parents=True, exist_ok=True)

    def avancer(etape: str) -> None:
        _etats[analyse_id] = {"statut": "en_cours", "etape": etape}

    try:
        avancer("Lecture du catalogue")
        items = stac.lire_item(id_avant), stac.lire_item(id_apres)
        grille = creer_grille(bbox)
        avant = _lire_scene(items[0], grille, avancer, "Avant")
        apres = _lire_scene(items[1], grille, avancer, "Après")

        avancer("Calcul des changements")
        comparable = avant["masque"] & apres["masque"]
        perte = detecter_perte(avant["ndvi"], apres["ndvi"], comparable, grille.hectares_par_pixel)
        polygones = extraire_polygones(perte, grille)

        avancer("Enregistrement des résultats")
        empreintes: dict[str, str] = {}
        _sauver(dossier, "avant.png", avant["rgba"], empreintes)
        _sauver(dossier, "apres.png", apres["rgba"], empreintes)
        _sauver(dossier, "ndvi_avant.png", image_ndvi(avant["ndvi"], avant["masque"]), empreintes)
        _sauver(dossier, "ndvi_apres.png", image_ndvi(apres["ndvi"], apres["masque"]), empreintes)
        _sauver(dossier, "changement.png", image_changement(perte, comparable), empreintes)

        comparable_pct = 100 * float(comparable.mean())
        perte_ha = float(perte.sum()) * grille.hectares_par_pixel
        baisse = float(np.nanmedian((avant["ndvi"] - apres["ndvi"])[perte])) if perte.any() else 0.0
        resultat = {
            "id": analyse_id, "bbox": list(bbox), "version": VERSION, "cree_le": db.maintenant(),
            "scenes": {"avant": id_avant, "apres": id_apres},
            "dates": {"avant": items[0]["properties"]["datetime"][:10], "apres": items[1]["properties"]["datetime"][:10]},
            "resolution_m": round(grille.resolution, 1),
            "surface_zone_ha": round(grille.largeur * grille.hauteur * grille.hectares_par_pixel),
            "comparable_pct": round(comparable_pct, 1), "perte_ha": round(perte_ha, 1),
            "baisse_ndvi_mediane": round(baisse, 3),
            "ndvi_median": {"avant": _mediane(avant), "apres": _mediane(apres)},
            "avertissements": avertissements(*items, comparable_pct),
            "seuils": {"ndvi_vegetation": config.NDVI_VEGETATION, "baisse_ndvi": config.BAISSE_NDVI,
                       "surface_min_ha": config.SURFACE_MIN_HA},
            "polygones": polygones, "polygones_total_ha": round(sum(p["hectares"] for p in polygones), 1),
            "sha256": empreintes,
        }
        resultat["alerte_id"] = _creer_alerte(resultat, items)
        (dossier / "resultat.json").write_text(json.dumps(resultat), encoding="utf-8")
        _etats[analyse_id] = {"statut": "termine"}
    except Exception as erreur:
        traceback.print_exc()
        _etats[analyse_id] = {"statut": "erreur", "message": _message_erreur(erreur)}


def _message_erreur(erreur: Exception) -> str:
    texte = str(erreur).lower()
    if any(m in texte for m in ("curl", "http", "timeout", "timed out", "not recognized")):
        return ("Le serveur d'images n'a pas répondu à temps (connexion lente ou coupée). "
                "Relance dans quelques minutes : la comparaison reprendra au début.")
    return "L'analyse a échoué. Le détail est dans la fenêtre du serveur."


def _creer_alerte(resultat: dict, items: tuple) -> int | None:
    if resultat["perte_ha"] < config.SURFACE_ALERTE_HA:
        return None
    region = None
    if resultat["polygones"]:
        anneau = resultat["polygones"][0]["geometrie"]["coordinates"][0]
        region = zones.region_du_point(sum(p[0] for p in anneau) / len(anneau), sum(p[1] for p in anneau) / len(anneau))
    # Le score résume la qualité de la comparaison et l'ampleur de la baisse : ce n'est pas une probabilité.
    score = round(0.5 * resultat["comparable_pct"] / 100 + 0.5 * min(1, resultat["baisse_ndvi_mediane"] / 0.5), 2)
    return db.creer_alerte({
        "cle": f"vegetation:{resultat['id']}", "type": "vegetation",
        "priorite": "haute" if resultat["perte_ha"] >= 25 else "moyenne",
        "titre": f"Baisse de végétation à vérifier : {resultat['perte_ha']:.1f} ha",
        "region": region, "zone_pilote": int(region == config.ZONE_PILOTE),
        "geometrie": {"type": "GeometryCollection", "geometries": [p["geometrie"] for p in resultat["polygones"]]},
        "score": score, "acquis_le": items[1]["properties"]["datetime"], "recu_le": db.maintenant(),
        "source": "Copernicus Sentinel-2 L2A", "capteur": " / ".join(i["properties"]["platform"] for i in items),
        "resolution_m": resultat["resolution_m"], "nuages_pct": items[1]["properties"]["eo:cloud_cover"],
        "version_regle": resultat["version"],
        "preuves": {"analyse_id": resultat["id"], "scenes": [i["id"] for i in items], "dates": resultat["dates"],
                    "comparable_pct": resultat["comparable_pct"], "perte_ha": resultat["perte_ha"],
                    "baisse_ndvi_mediane": resultat["baisse_ndvi_mediane"], "seuils": resultat["seuils"],
                    "avertissements": resultat["avertissements"], "sha256": resultat["sha256"]},
        "limites": LIMITES,
    })


def identifiant(bbox: tuple, id_avant: str, id_apres: str) -> str:
    return hashlib.sha256(f"{bbox}|{id_avant}|{id_apres}|{VERSION}".encode()).hexdigest()[:16]


def lancer(bbox: tuple, id_avant: str, id_apres: str) -> str:
    analyse_id = identifiant(bbox, id_avant, id_apres)
    if not (config.ANALYSES / analyse_id / "resultat.json").exists() and \
            _etats.get(analyse_id, {}).get("statut") not in ("en_attente", "en_cours"):
        _etats[analyse_id] = {"statut": "en_attente", "etape": "En attente"}
        _travaux.submit(_executer, analyse_id, bbox, id_avant, id_apres)
    return analyse_id


def lire(analyse_id: str) -> dict | None:
    chemin = config.ANALYSES / analyse_id / "resultat.json"
    if chemin.exists():
        return {"statut": "termine", "resultat": json.loads(chemin.read_text(encoding="utf-8"))}
    return _etats.get(analyse_id)
