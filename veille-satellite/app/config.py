"""Réglages du prototype. Rien ici n'est secret : les sources utilisées sont ouvertes."""
import os
from pathlib import Path

# Lecture partielle des images distantes : à définir avant d'importer rasterio.
# Délais larges : depuis la Côte d'Ivoire, le serveur d'images peut mettre 30 s à répondre.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "45")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "240")
os.environ.setdefault("GDAL_INGESTED_BYTES_AT_OPEN", "65536")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("VSI_CACHE", "TRUE")

RACINE = Path(__file__).resolve().parent.parent
WEB = RACINE / "web"
DONNEES = Path(os.environ.get("VEILLE_DONNEES") or RACINE / "data")
ANALYSES = DONNEES / "analyses"
BASE = DONNEES / "veille.sqlite3"
CACHE_FEUX = DONNEES / "feux.json"
REGIONS = Path(__file__).resolve().parent / "donnees" / "regions_civ.geojson"

# Sentinel-2 (recherche STAC et images publiques, sans compte).
STAC_RECHERCHE = "https://earth-search.aws.element84.com/v1/search"
STAC_ITEMS = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items"
COLLECTION = "sentinel-2-l2a"
HOTES_IMAGES = {"sentinel-cogs.s3.us-west-2.amazonaws.com"}
LICENCE_S2 = "Données Copernicus Sentinel, licence ouverte Copernicus (contenu modifié)"

# NASA FIRMS : fichiers publics sans clé. Le fichier 7 jours (~35 Mo) sert au premier chargement,
# ensuite seul le fichier 24 h (quelques Mo) est téléchargé et fusionné.
FLUX_FIRMS = {
    "NOAA-20": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_{periode}.csv",
    "S-NPP": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_{periode}.csv",
}
JOURS_CONSERVES_FEUX = 7
LICENCE_FIRMS = "NASA FIRMS, domaine public (citation demandée)"
CACHE_FEUX_HEURES = 3
ACTUALISATION_FORCEE_MINUTES = 10  # le bouton « Actualiser » est public : pas plus d'un téléchargement NASA par 10 min

# Périmètre du pilote.
ZONE_PILOTE = "Cavally"
EMPRISE_PAYS = (-8.7, 4.2, -2.4, 10.8)

# Une zone d'analyse reste petite : le calcul se fait sur ce PC.
COTE_MIN_KM = 0.5
COTE_MAX_KM = 25
RESOLUTION_M = 10
PIXELS_MAX = 1500

# Règle de perte de végétation : un seuil simple, pas un modèle entraîné.
VERSION_REGLE = "regle-ndvi-v1"
NDVI_VEGETATION = 0.55
BAISSE_NDVI = 0.25
SURFACE_MIN_HA = 0.5
SURFACE_ALERTE_HA = 1.0
MARGE_NUAGES_M = 60
# Petits nuages oubliés par le masque SCL : bleu de l'image couleur (0-255) trop élevé.
# Mesuré dans le Cavally : forêt ~40, sol défriché ≤ 65, nuages ≥ 80.
BLEU_NUAGE = 80
ECART_SAISON_JOURS = 45
COMPARABLE_MIN_PCT = 60

AUTEUR_LOCAL = "analyste-local"

# Mise en ligne (Render). En local, ces variables restent vides.
# VEILLE_HOTES : noms de domaine acceptés, ex. "veille-satellite.onrender.com".
# VEILLE_ORIGINES : sites autorisés à lire l'API depuis le navigateur, ex. le globe 3D.
# VEILLE_JETON_ANALYSTE : code exigé pour lancer une analyse ou décider d'une alerte.
_liste = lambda nom: [v.strip() for v in os.environ.get(nom, "").split(",") if v.strip()]  # noqa: E731
# Render fournit lui-même l'adresse publique du service dans RENDER_EXTERNAL_HOSTNAME.
HOTES = ["127.0.0.1", "localhost", *_liste("VEILLE_HOTES"), *_liste("RENDER_EXTERNAL_HOSTNAME")]
ORIGINES = ["http://127.0.0.1:8765", "http://localhost:8765", *_liste("VEILLE_ORIGINES")]
JETON_ANALYSTE = os.environ.get("VEILLE_JETON_ANALYSTE", "")
EN_LIGNE = len(HOTES) > 2
