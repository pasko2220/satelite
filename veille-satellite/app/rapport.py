"""Rapport d'alerte imprimable et exports CSV / GeoJSON."""
import csv
import io
from html import escape

from . import db

STATUTS = {"nouveau": "Nouveau", "a_examiner": "À examiner", "confirme": "Confirmé", "rejete": "Rejeté",
           "publie": "Publié", "cloture": "Clôturé"}
TYPES = {"feu": "Point chaud", "vegetation": "Baisse de végétation"}
COLONNES_CSV = ("id", "type", "statut", "priorite", "titre", "region", "zone_pilote", "score", "acquis_le",
                "recu_le", "source", "capteur", "resolution_m", "nuages_pct", "version_regle", "longitude", "latitude")


def _ligne(libelle: str, valeur) -> str:
    return f"<tr><th>{escape(libelle)}</th><td>{escape('—' if valeur is None else str(valeur))}</td></tr>"


def _liste_images(alerte: dict) -> str:
    analyse_id = alerte["preuves"].get("analyse_id")
    if not analyse_id:
        return ""
    dates = alerte["preuves"]["dates"]
    legendes = (("avant.png", f"Avant : {dates['avant']}"), ("apres.png", f"Après : {dates['apres']}"),
                ("changement.png", "Baisse détectée (magenta), non comparable (gris)"))
    cartes = "".join(
        f'<figure><img src="/donnees/analyses/{escape(analyse_id)}/{nom}" alt="{escape(legende)}">'
        f"<figcaption>{escape(legende)}</figcaption></figure>" for nom, legende in legendes)
    return f"<h2>Images</h2><div class='images'>{cartes}</div>"


def _provenance(alerte: dict) -> str:
    scenes = db.lire_scenes(alerte["preuves"]["scenes"]) if alerte["preuves"].get("scenes") else []
    if scenes:
        lignes = "".join(
            f"<tr><td>{escape(s['id'])}</td><td>{escape(s['capteur'])}</td><td>{escape(s['acquis_le'])}</td>"
            f"<td>{s['nuages_pct']:.1f} %</td><td>{escape(s['niveau'])}</td><td>{escape(s['recu_le'])}</td></tr>"
            for s in scenes)
        tableau = ("<table><tr><th>Scène</th><th>Capteur</th><th>Acquisition</th><th>Nuages (tuile)</th>"
                   f"<th>Niveau</th><th>Reçue le</th></tr>{lignes}</table>")
        licences = sorted({s["licence"] for s in scenes})
        return tableau + "<p>" + escape(" ; ".join(licences)) + "</p>" + "".join(
            f"<p class='uri'>{escape(s['uri'])}</p>" for s in scenes)
    preuves = alerte["preuves"]
    uris = "".join(f"<p class='uri'>{escape(u)}</p>" for u in preuves.get("uris", []))
    return f"<p>{escape(preuves.get('licence', ''))}</p>{uris}<p class='uri'>SHA-256 de l'extrait : " \
           f"{escape(preuves.get('sha256_extrait', ''))}</p>"


def html_alerte(alerte: dict) -> str:
    preuves = alerte["preuves"]
    details = [_ligne("Statut", STATUTS[alerte["statut"]]), _ligne("Type", TYPES[alerte["type"]]),
               _ligne("Priorité", alerte["priorite"]), _ligne("Région", alerte["region"]),
               _ligne("Date d'acquisition", alerte["acquis_le"]), _ligne("Date de réception", alerte["recu_le"]),
               _ligne("Source", alerte["source"]), _ligne("Capteur", alerte["capteur"]),
               _ligne("Résolution", f"{alerte['resolution_m']} m"),
               _ligne("Score indicatif", alerte["score"]), _ligne("Règle", alerte["version_regle"])]
    if alerte["type"] == "vegetation":
        details += [_ligne("Surface concernée", f"{preuves['perte_ha']} ha"),
                    _ligne("Zone comparable", f"{preuves['comparable_pct']} %"),
                    _ligne("Baisse médiane du NDVI", preuves["baisse_ndvi_mediane"]),
                    _ligne("Seuils", "NDVI avant ≥ {ndvi_vegetation} ; baisse ≥ {baisse_ndvi} ; "
                                     "tache ≥ {surface_min_ha} ha".format(**preuves["seuils"]))]
    else:
        details += [_ligne("Détections", preuves["points"]), _ligne("Puissance maximale", f"{preuves['puissance_max_mw']} MW")]

    decisions = "".join(
        f"<tr><td>{escape(d['le'])}</td><td>{escape(STATUTS.get(d['decision'], d['decision']))}</td>"
        f"<td>{escape(d['auteur'])}</td><td>{escape(d['note'])}</td></tr>" for d in alerte["decisions"])
    tableau_decisions = (f"<table><tr><th>Date</th><th>Décision</th><th>Par</th><th>Note</th></tr>{decisions}</table>"
                         if decisions else "<p><strong>Aucune décision humaine : ce signal n'est pas confirmé.</strong></p>")
    alertes = "".join(f"<li>{escape(a)}</li>" for a in preuves.get("avertissements", []))
    bloc_alertes = f"<h2>Avertissements</h2><ul>{alertes}</ul>" if alertes else ""
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Rapport {alerte['id']} · Veille Satellite</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/rapport.css"><script src="/rapport.js" defer></script></head><body>
<p class="action"><button id="imprimer" type="button">Imprimer ou enregistrer en PDF</button></p>
<p class="marque">Veille Satellite · Prototype de démonstration</p>
<h1>{escape(alerte['titre'])}</h1>
<p class="avis">Aide à la décision. Ce document signale un changement observé ; il n'établit ni une cause,
ni une responsabilité, et ne constitue pas une preuve juridique.</p>
<table>{''.join(details)}</table>
{bloc_alertes}{_liste_images(alerte)}
<h2>Limites connues</h2><p>{escape(alerte['limites'] or '')}</p>
<h2>Validation humaine</h2>{tableau_decisions}
<h2>Provenance</h2>{_provenance(alerte)}
</body></html>"""


def _cellule(valeur):
    """Empêche un tableur d'interpréter une cellule comme une formule."""
    if isinstance(valeur, str) and valeur[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + valeur
    return valeur


def _point(geometrie: dict) -> tuple:
    if geometrie["type"] == "Point":
        return tuple(geometrie["coordinates"])
    premiers = [g["coordinates"][0][0] for g in geometrie.get("geometries", [])]
    return tuple(premiers[0]) if premiers else (None, None)


def csv_alertes(alertes: list[dict]) -> str:
    sortie = io.StringIO()
    ecrivain = csv.writer(sortie, delimiter=";")
    ecrivain.writerow(COLONNES_CSV)
    for alerte in alertes:
        longitude, latitude = _point(alerte["geometrie"])
        ligne = dict(alerte, longitude=longitude, latitude=latitude)
        ecrivain.writerow([_cellule(ligne[c]) for c in COLONNES_CSV])
    return "﻿" + sortie.getvalue()


def geojson_alertes(alertes: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": a["id"], "geometry": a["geometrie"],
         "properties": {c: a[c] for c in COLONNES_CSV if c in a}} for a in alertes]}
