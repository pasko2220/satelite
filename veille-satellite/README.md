# Veille Satellite : prototype de démonstration

Prototype de la feuille de route « Plateforme de veille environnementale par satellite et IA »,
correspondant au plan des 30 premiers jours. Pilote : Côte d'Ivoire, région du Cavally,
deux cas d'usage (feux actifs et perte de végétation), validation humaine systématique.

**Ce n'est pas un système opérationnel.** Il n'y a pas de comptes ni de rôles, et le programme
n'est accessible que depuis ce PC. Les signaux sont produits par des règles simples, pas par
un modèle d'IA entraîné.

## Démarrer

Double-clique sur `lancer.bat`. La page s'ouvre sur http://127.0.0.1:8765.
La fenêtre noire est le serveur : ferme-la pour arrêter le programme.

Aucun compte ni aucune clé ne sont nécessaires : toutes les sources sont publiques.
Une connexion Internet est nécessaire.

## Ce que fait la page

| Onglet  | Ce qu'on y fait |
|---------|-----------------|
| Feux    | Points chauds NASA FIRMS (VIIRS, deux satellites) des 24 h, 48 h ou 7 derniers jours, en Côte d'Ivoire. Le flux est rechargé au plus toutes les 3 heures. |
| Images  | 1) Choisir une zone (au maximum 25 km de côté). 2) Chercher les images Sentinel-2. 3) Comparer deux dates : image en couleurs naturelles, végétation (NDVI), baisse détectée, avec un balayage avant/après. |
| Alertes | Signaux à vérifier. Chacun peut être confirmé ou rejeté (avec une note obligatoire), puis publié et clôturé. Chaque signal a un rapport imprimable en PDF. Exports CSV et GeoJSON. |

Les signaux de feu sont créés automatiquement :
- dans le Cavally, pour toute détection de confiance moyenne ou élevée ;
- ailleurs, seulement à partir de 2 détections au même endroit (dans un rayon d'environ 2 km) le même jour.

Un signal de végétation est créé quand une comparaison trouve au moins 1 ha de baisse.

## Règle de perte de végétation (`regle-ndvi-v1`)

Un pixel est retenu si :
1. il est fiable aux deux dates : classe SCL végétation, sol nu, eau ou non classé ;
2. il n'a pas la couleur d'un nuage : bleu de l'image couleur au-dessus de 80 ;
3. il se trouve à plus de 60 m d'un nuage ou d'une ombre ;
4. son NDVI « avant » vaut au moins 0,55 (végétation dense) ;
5. son NDVI baisse d'au moins 0,25 ;
6. il fait partie d'une tache d'au moins 0,5 ha.

Les seuils se règlent dans `app/config.py`. Ils ont été réglés sur une seule zone du Cavally.
Il faut les valider avec des experts sur d'autres zones et d'autres saisons avant toute utilisation réelle.

Le « score indicatif » combine la part de zone comparable et l'ampleur de la baisse.
Ce n'est pas une probabilité.

## Limites connues

- Les nuages restent la première source d'erreur. En saison des pluies, une grande partie
  de la zone est souvent « non comparable » (en gris sur la carte) : choisis des dates de saison sèche.
- Le pourcentage de nuages affiché dans la liste concerne toute la tuile satellite, pas ta zone.
- Comparer deux saisons différentes (par exemple mars et août) peut faire apparaître une fausse baisse.
  La page affiche un avertissement dans ce cas.
- Un point chaud n'est pas toujours un incendie.
- Une seule image par date est utilisée. Une zone à cheval sur deux tuiles peut être partiellement vide.
- Avec une connexion lente, une comparaison peut prendre de longues minutes, voire échouer : le serveur
  d'images est aux États-Unis. Le 30/10/2026, une comparaison de 10 × 10 km a pris environ 2 minutes ;
  le 03/11/2026, à environ 2 Ko/s, elle n'a pas abouti. La page affiche alors un message : relance plus tard.
- Les feux : le premier chargement télécharge environ 70 Mo, les suivants environ 7 Mo (dernières 24 h).
  Les données déjà reçues s'affichent tout de suite pendant la mise à jour.

## Sources et licences

- Images : Copernicus Sentinel-2 L2A, via Earth Search (AWS Open Data), licence ouverte Copernicus.
- Feux : NASA FIRMS VIIRS (NOAA-20, S-NPP), domaine public, citation demandée.
- Limites des régions : geoBoundaries gbOpen CIV ADM2, CC BY 4.0 (Banque mondiale).
- Fond de carte : Sentinel-2 cloudless 2020 par EOX (usage non commercial), OpenStreetMap.

## Organisation du code

```
app/config.py    réglages et seuils
app/firms.py     connecteur NASA FIRMS et signaux de feu
app/stac.py      recherche Sentinel-2 (STAC) et registre de provenance
app/analyse.py   lecture des images, masque nuages, NDVI, détection de perte
app/db.py        SQLite : scènes, alertes, décisions, journal d'audit (en ajout seul)
app/rapport.py   rapport imprimable, exports CSV et GeoJSON
app/main.py      API locale (FastAPI)
web/             page : carte Leaflet, sans étape de compilation
tests/           tests sans réseau : .venv\Scripts\python -m unittest discover tests
data/            cache, base de données et résultats d'analyse (créés automatiquement)
```

## Pourquoi une carte 2D légère

Ce PC n'a pas d'accélération graphique utilisable par le navigateur
(« Microsoft Basic Render Driver »). Une carte 3D ou WebGL y serait trop lente.
Leaflet fonctionne sans carte graphique.

Pour God's Eye View (github.com/bilawalsidhu/gods-eye-view) : ce projet demande Node 24 et un globe 3D,
il ne tourne pas correctement sur ce PC. Les sorties `GET /api/feux` et `GET /api/alertes.geojson`
sont au format GeoJSON standard, prévues pour en faire une couche de ce globe sur une machine plus puissante.
