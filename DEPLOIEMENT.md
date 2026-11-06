# Mise en ligne sur Render

Ce dépôt contient deux applications :

| Dossier | Service Render | Rôle |
|---|---|---|
| racine | `satelite-globe` | Le globe 3D (God's Eye View, licence MIT) avec la couche **Alertes de veille satellite** |
| `veille-satellite/` | `satelite-veille` | Le serveur d'analyse : feux NASA FIRMS, comparaison Sentinel-2, alertes, validation humaine, rapports |

Le globe lit les alertes du serveur d'analyse. Le public peut **voir**. Pour **agir** (lancer une analyse,
confirmer ou rejeter une alerte), il faut le **code analyste**.

## 1. Créer les deux services

1. Sur https://dashboard.render.com : **New** → **Blueprint**, puis choisis le dépôt `pasko2220/satelite`.
2. Render lit `render.yaml` et propose les deux services. Laisse vides les champs qu'il demande pour l'instant,
   puis clique sur **Apply**.
3. Attends la fin des deux constructions. La première prend de 10 à 20 minutes.

## 2. Relier les deux services

Chaque service affiche son adresse en haut de sa page, par exemple `https://satelite-globe.onrender.com`.

- Dans **satelite-globe** → **Environment** : `VITE_VEILLE_API_URL` = l'adresse de **satelite-veille**.
- Dans **satelite-veille** → **Environment** : `VEILLE_ORIGINES` = l'adresse de **satelite-globe**.

Enregistre : Render redémarre les services.

## 3. Les clés API

Dans **satelite-globe** → **Environment**, remplis celles que tu veux :

| Variable | Pour quoi | Gratuit ? |
|---|---|---|
| `CESIUM_ION_TOKEN` | Relief et villes en 3D | Oui (usage personnel non commercial) |
| `GOOGLE_MAPS_API_KEY` | Villes photoréalistes 3D, recherche de lieux | Payant au-delà du quota |
| `GOOGLE_MAPS_SERVER_API_KEY` | Recherche Google côté serveur | Payant au-delà du quota |
| `OPENAI_API_KEY` | Commande vocale, résumé IA | **Payant** |
| `GEMINI_API_KEY` | Voix avec Gemini | Payant au-delà du quota |
| `AISSTREAM_API_KEY` | Navires en direct | Oui |
| `FIRMS_MAP_KEY` | Feux NASA dans le globe | Oui |
| `TOMTOM_API_KEY` | Couleurs du trafic | Oui (quota) |
| `MAPILLARY_CLIENT_TOKEN` | Photos au niveau de la rue | Oui |
| `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET` | Plus de requêtes pour les avions | Oui |
| `LL2_API_TOKEN` | Plus de requêtes pour les lancements | Payant |

> **Attention :** le site est public. **Tout visiteur** utilise tes clés. Pour OpenAI, Google et Gemini,
> fixe une **limite de dépense** chez chaque fournisseur avant de mettre la clé.

## 4. Le code analyste

Dans **satelite-veille** → **Environment**, la variable `VEILLE_JETON_ANALYSTE` a été créée automatiquement.
Clique sur l'œil pour la voir. C'est ce code que la page d'analyse demande avant toute action.
Ne le partage qu'avec les analystes.

- Le code reste dans l'onglet jusqu'à sa fermeture, ou jusqu'au bouton **Oublier le code analyste** (en haut à droite).
- Après 10 codes faux en 15 minutes depuis une même adresse (100 pour tout le site), le serveur refuse les essais
  pendant 15 minutes.
- **Si le code a fuité**, ou si un analyste s'en va : change la valeur de `VEILLE_JETON_ANALYSTE`
  (**Generate** ou un nouveau texte long), puis **Save**. L'ancien code ne marche plus, pour tout le monde.

L'espace analyste est l'adresse de **satelite-veille** elle-même.

## Limites de la formule gratuite

- Un service gratuit s'endort après 15 minutes sans visite. Le réveil prend environ une minute
  (plus pour le globe).
- **Les alertes et décisions sont effacées à chaque redémarrage**, car la formule gratuite n'a pas de disque
  permanent. Pour les garder : formule payante + disque (**Disks**, monté sur `/donnees`).
- Le globe charge beaucoup de données. S'il s'arrête pour manque de mémoire, passe `satelite-globe`
  sur une formule avec plus de mémoire.
