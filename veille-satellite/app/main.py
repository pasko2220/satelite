"""API et page web de la veille satellite (en local, ou en ligne sur Render)."""
import hmac
import math
import re
import time
from contextlib import asynccontextmanager
from datetime import date

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import config  # doit rester avant analyse : prépare l'environnement de lecture des images
from . import analyse, db, firms, rapport, stac, zones

CSP = ("default-src 'self'; img-src 'self' data: https://tiles.maps.eox.at https://*.tile.openstreetmap.org; "
       "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'none'")
MOTIF_ANALYSE = re.compile(r"^[0-9a-f]{16}$")


class Zone(BaseModel):
    ouest: float
    sud: float
    est: float
    nord: float

    @model_validator(mode="after")
    def verifier(self):
        o, s, e, n = config.EMPRISE_PAYS
        if not (o <= self.ouest < self.est <= e and s <= self.sud < self.nord <= n):
            raise ValueError("La zone doit se trouver en Côte d'Ivoire.")
        largeur = (self.est - self.ouest) * 111.32 * math.cos(math.radians((self.sud + self.nord) / 2))
        hauteur = (self.nord - self.sud) * 110.57
        if max(largeur, hauteur) > config.COTE_MAX_KM:
            raise ValueError(f"Zone trop grande : {config.COTE_MAX_KM} km de côté au maximum.")
        if min(largeur, hauteur) < config.COTE_MIN_KM:
            raise ValueError("Zone trop petite : 500 m de côté au minimum.")
        return self

    @property
    def bbox(self) -> tuple:
        return tuple(round(v, 6) for v in (self.ouest, self.sud, self.est, self.nord))


class RechercheScenes(BaseModel):
    zone: Zone
    debut: date
    fin: date
    nuages_max: float = Field(default=30, ge=0, le=100)


class DemandeAnalyse(BaseModel):
    zone: Zone
    scene_avant: str = Field(max_length=64)
    scene_apres: str = Field(max_length=64)


class Decision(BaseModel):
    decision: str = Field(max_length=20)
    note: str = Field(default="", max_length=2000)


@asynccontextmanager
async def demarrage(_):
    db.initialiser()
    config.ANALYSES.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Veille Satellite", lifespan=demarrage, docs_url=None, redoc_url=None, openapi_url=None)

def _jeton_valide(requete: Request) -> bool:
    """En ligne, toute action (analyse, décision) exige le code analyste ; la lecture reste publique."""
    if not config.JETON_ANALYSTE:
        return not config.EN_LIGNE
    return hmac.compare_digest(requete.headers.get("x-jeton-analyste", ""), config.JETON_ANALYSTE)


# Contre l'essai de milliers de codes : 10 mauvais codes par adresse en 15 minutes,
# et 100 pour tout le site (une adresse se change facilement).
FENETRE_ESSAIS = 15 * 60
ESSAIS_MAX = {"adresse": 10, "site": 100}
_echecs: dict[str, list[float]] = {}


def _recents(cle: str, maintenant: float) -> int:
    dates = [t for t in _echecs.get(cle, []) if maintenant - t < FENETRE_ESSAIS]
    if dates:
        _echecs[cle] = dates
    else:
        _echecs.pop(cle, None)
    return len(dates)


def _trop_d_essais(adresse: str, maintenant: float) -> bool:
    return (_recents(adresse, maintenant) >= ESSAIS_MAX["adresse"]
            or _recents("*site*", maintenant) >= ESSAIS_MAX["site"])


def _noter_echec(adresse: str, maintenant: float):
    if len(_echecs) > 10_000:  # mémoire bornée
        _echecs.clear()
    for cle in (adresse, "*site*"):
        _echecs.setdefault(cle, []).append(maintenant)


@app.middleware("http")
async def protections(requete: Request, suivant):
    if requete.method == "POST":
        origine = requete.headers.get("origin")
        if origine and origine not in config.ORIGINES and origine != f"{requete.url.scheme}://{requete.url.netloc}":
            return JSONResponse({"detail": "Origine refusée."}, status_code=403)
        adresse = requete.client.host if requete.client else "?"
        maintenant = time.monotonic()
        if _trop_d_essais(adresse, maintenant):
            return JSONResponse({"detail": "Trop de codes incorrects : réessaie dans 15 minutes."}, status_code=429)
        if not _jeton_valide(requete):
            if requete.headers.get("x-jeton-analyste"):
                _noter_echec(adresse, maintenant)
            return JSONResponse({"detail": "Code analyste manquant ou incorrect."}, status_code=401)
    reponse = await suivant(requete)
    reponse.headers["Content-Security-Policy"] = CSP
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Toujours revalider : sinon le navigateur garde une ancienne version de la page après une mise à jour.
    reponse.headers.setdefault("Cache-Control", "no-cache")
    return reponse


# Ajoutés après le middleware ci-dessus : ils s'exécutent donc avant lui.
# Le globe 3D, sur un autre domaine, peut lire l'API et les images, sans pouvoir rien modifier.
app.add_middleware(CORSMiddleware, allow_origins=config.ORIGINES, allow_methods=["GET"], allow_headers=[])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.HOTES)


@app.get("/api/etat")
def lire_etat():
    return {"jeton_requis": bool(config.JETON_ANALYSTE) or config.EN_LIGNE, "zone_pilote": config.ZONE_PILOTE}


@app.get("/api/regions")
def lire_regions():
    return zones.regions()


@app.get("/api/feux")
def lire_feux(jours: int = Query(default=7, ge=1, le=7), actualiser: bool = False):
    return firms.en_geojson(firms.actualiser(forcer=actualiser), jours)


@app.post("/api/scenes")
def chercher_scenes(demande: RechercheScenes):
    if demande.fin < demande.debut:
        raise HTTPException(422, "La date de fin précède la date de début.")
    try:
        return stac.rechercher(demande.zone.bbox, demande.debut.isoformat(), demande.fin.isoformat(), demande.nuages_max)
    except requests.RequestException:
        raise HTTPException(502, "Catalogue d'images injoignable. Vérifie Internet puis réessaie.")


@app.post("/api/analyses")
def demarrer_analyse(demande: DemandeAnalyse):
    for scene in (demande.scene_avant, demande.scene_apres):
        if not stac.MOTIF_SCENE.match(scene):
            raise HTTPException(422, "Identifiant de scène invalide.")
    if demande.scene_avant == demande.scene_apres:
        raise HTTPException(422, "Choisis deux images différentes.")
    return {"id": analyse.lancer(demande.zone.bbox, demande.scene_avant, demande.scene_apres)}


@app.get("/api/analyses/{analyse_id}")
def suivre_analyse(analyse_id: str):
    etat = analyse.lire(analyse_id) if MOTIF_ANALYSE.match(analyse_id) else None
    if etat is None:
        raise HTTPException(404, "Analyse introuvable.")
    return etat


@app.get("/api/alertes")
def lire_alertes(statut: str | None = None):
    return db.lister_alertes(statut if statut in db.TRANSITIONS else None)


@app.get("/api/alertes.geojson")
def exporter_geojson():
    return JSONResponse(rapport.geojson_alertes(db.lister_alertes()), media_type="application/geo+json")


@app.get("/api/alertes.csv")
def exporter_csv():
    return Response(rapport.csv_alertes(db.lister_alertes()), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="alertes.csv"'})


@app.get("/api/alertes/{alerte_id}")
def lire_alerte(alerte_id: int):
    alerte = db.lire_alerte(alerte_id)
    if alerte is None:
        raise HTTPException(404, "Alerte introuvable.")
    return alerte


@app.post("/api/alertes/{alerte_id}/decision")
def enregistrer_decision(alerte_id: int, demande: Decision):
    try:
        return db.decider(alerte_id, demande.decision, demande.note)
    except LookupError as erreur:
        raise HTTPException(404, str(erreur))
    except ValueError as erreur:
        raise HTTPException(422, str(erreur))


@app.get("/api/alertes/{alerte_id}/rapport", response_class=HTMLResponse)
def lire_rapport(alerte_id: int):
    alerte = db.lire_alerte(alerte_id)
    if alerte is None:
        raise HTTPException(404, "Alerte introuvable.")
    return rapport.html_alerte(alerte)


@app.get("/api/audit")
def lire_audit():
    return db.lire_audit()


config.ANALYSES.mkdir(parents=True, exist_ok=True)
app.mount("/donnees/analyses", StaticFiles(directory=config.ANALYSES), name="analyses")
app.mount("/", StaticFiles(directory=config.WEB, html=True), name="web")
