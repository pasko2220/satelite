"use strict";

const STATUTS = { nouveau: "Nouveau", a_examiner: "À examiner", confirme: "Confirmé", rejete: "Rejeté", publie: "Publié", cloture: "Clôturé" };
const TYPES = { feu: "Point chaud", vegetation: "Baisse de végétation" };
const PRIORITES = { haute: "Priorité haute", moyenne: "Priorité moyenne", basse: "Priorité basse" };
const ACTIONS = {
  confirme: { texte: "Confirmer", classe: "oui" },
  rejete: { texte: "Rejeter", classe: "non" },
  a_examiner: { texte: "Remettre à examiner" },
  publie: { texte: "Publier" },
  cloture: { texte: "Clôturer" },
};
const CONFIANCES = { high: "élevée", nominal: "moyenne", low: "faible" };
const ZONE_PILOTE = "Cavally";
const FEU = "#ff7a1a";

const $ = (id) => document.getElementById(id);

function el(balise, attributs = {}, ...enfants) {
  const noeud = document.createElement(balise);
  for (const [nom, valeur] of Object.entries(attributs)) {
    if (nom === "class") noeud.className = valeur;
    else if (nom.startsWith("on")) noeud.addEventListener(nom.slice(2), valeur);
    else noeud.setAttribute(nom, valeur);
  }
  noeud.append(...enfants.filter((e) => e !== null && e !== undefined));
  return noeud;
}

// En ligne, les actions exigent le code analyste ; il est gardé le temps de l'onglet, ou jusqu'à « Oublier le code ».
function lireJeton() { try { return sessionStorage.getItem("jeton-analyste") || ""; } catch { return ""; } }
function garderJeton(jeton) {
  try {
    if (jeton) sessionStorage.setItem("jeton-analyste", jeton);
    else sessionStorage.removeItem("jeton-analyste");
  } catch { /* navigation privée */ }
  $("oublier-code").hidden = !lireJeton();
}

async function api(chemin, options = {}, deuxiemeEssai = false) {
  const corps = options.corps;
  if (corps !== undefined) {
    options = { method: "POST", headers: { "Content-Type": "application/json", "X-Jeton-Analyste": lireJeton() }, body: JSON.stringify(corps) };
  }
  const reponse = await fetch(chemin, options);
  const donnees = await reponse.json().catch(() => null);
  if (reponse.status === 401 && corps !== undefined && !deuxiemeEssai) {
    const jeton = window.prompt("Code analyste (donné par l'administrateur du site) :");
    if (jeton) {
      garderJeton(jeton.trim());
      return api(chemin, { corps }, true);
    }
  }
  if (reponse.status === 401 && deuxiemeEssai) garderJeton("");  // code faux : ne pas le garder
  if (!reponse.ok) {
    const detail = donnees && donnees.detail;
    const message = Array.isArray(detail) ? detail[0].msg.replace(/^Value error, /, "") : detail;
    throw new Error(message || "Le serveur n'a pas répondu correctement.");
  }
  return donnees;
}

$("oublier-code").onclick = () => garderJeton("");
garderJeton(lireJeton());

const formatDate =(iso) => iso ? new Date(iso).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC" : "—";
const formatJour = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("fr-FR", { dateStyle: "medium", timeZone: "UTC" });
const nombre = (n, d = 0) => Number(n).toLocaleString("fr-FR", { maximumFractionDigits: d });

function chiffre(valeur, libelle) {
  return el("div", { class: "chiffre" }, el("strong", {}, String(valeur)), el("span", {}, libelle));
}

/* Carte */

const carte = L.map("carte", { zoomControl: true, preferCanvas: true, minZoom: 5, maxZoom: 16 }).fitBounds([[4.3, -8.7], [10.8, -2.4]]);
const rendu = L.canvas({ padding: 0.3 });
const fonds = {
  satellite: L.tileLayer("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg", {
    maxZoom: 16, attribution: 'Sentinel-2 cloudless 2020 par <a href="https://s2maps.eu">EOX</a>, données Copernicus',
  }),
  plan: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 16, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }),
};
fonds.satellite.addTo(carte);

function choisirFond(nom) {
  for (const [cle, couche] of Object.entries(fonds)) {
    if (cle === nom) couche.addTo(carte); else couche.remove();
  }
  $("fond-satellite").setAttribute("aria-pressed", nom === "satellite");
  $("fond-plan").setAttribute("aria-pressed", nom === "plan");
}
$("fond-satellite").onclick = () => choisirFond("satellite");
$("fond-plan").onclick = () => choisirFond("plan");

const coucheFeux = L.layerGroup().addTo(carte);
const coucheAlerte = L.layerGroup().addTo(carte);
let coucheAnalyse = null;
let cadreZone = null;

async function chargerRegions() {
  const regions = await api("/api/regions");
  L.geoJSON(regions, {
    interactive: false,
    style: (f) => f.properties.shapeName === ZONE_PILOTE
      ? { color: "#4fd1a1", weight: 2.5, fill: false }
      : { color: "#ffffff", weight: 0.7, opacity: 0.45, fill: false },
  }).addTo(carte);
}

/* Onglets */

const ONGLETS = ["feux", "images", "alertes"];
function ouvrirOnglet(nom) {
  for (const o of ONGLETS) {
    const actif = o === nom;
    $(`onglet-${o}`).setAttribute("aria-selected", actif);
    $(`onglet-${o}`).tabIndex = actif ? 0 : -1;
    $(`vue-${o}`).hidden = !actif;
  }
}
ONGLETS.forEach((o, i) => {
  $(`onglet-${o}`).onclick = () => ouvrirOnglet(o);
  $(`onglet-${o}`).onkeydown = (e) => {
    const pas = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!pas) return;
    const suivant = ONGLETS[(i + pas + ONGLETS.length) % ONGLETS.length];
    ouvrirOnglet(suivant);
    $(`onglet-${suivant}`).focus();
  };
});

/* Feux */

function dessinerFeux(donnees) {
  coucheFeux.clearLayers();
  const compte = {};
  let pilote = 0;
  for (const f of donnees.features) {
    const p = f.properties;
    const [lon, lat] = f.geometry.coordinates;
    compte[p.region] = (compte[p.region] || 0) + 1;
    pilote += p.region === ZONE_PILOTE;
    // La confiance se lit aussi à la forme : un anneau vide pour les détections faibles.
    const faible = p.confiance === "low";
    L.circleMarker([lat, lon], {
      renderer: rendu, radius: p.confiance === "high" ? 7 : 5, color: faible ? FEU : "#fff", weight: faible ? 2 : 1,
      fillColor: FEU, fillOpacity: faible ? 0 : 0.95,
    }).bindTooltip(`${p.region} · confiance ${CONFIANCES[p.confiance] || p.confiance}<br>${formatDate(p.acquis_le)} · ${p.satellite}`)
      .addTo(coucheFeux);
  }
  $("chiffres-feux").replaceChildren(chiffre(nombre(donnees.features.length), "détections affichées"), chiffre(nombre(pilote), `dans le ${ZONE_PILOTE}`));
  const classement = Object.entries(compte).sort((a, b) => b[1] - a[1]).slice(0, 8);
  if (!(ZONE_PILOTE in compte)) classement.push([ZONE_PILOTE, 0]);
  $("regions-feux").replaceChildren(...classement.map(([region, n]) =>
    el("li", { class: region === ZONE_PILOTE ? "pilote" : "" }, el("span", {}, region), el("span", {}, nombre(n)))));
  const meta = donnees.meta;
  const etat = $("etat-feux");
  etat.classList.toggle("erreur", Boolean(meta.erreur));
  etat.textContent = meta.erreur || `Dernier passage satellite : ${formatDate(meta.dernier_passage)}. Flux reçu : ${formatDate(meta.recu_le)}.`;
  if (meta.actualisation) {
    etat.textContent += " Mise à jour en cours en arrière-plan…";
    clearTimeout(suiviFeux);
    suiviFeux = setTimeout(() => chargerFeux(), 30000);
  }
}
let suiviFeux = null;

async function chargerFeux(actualiser = false) {
  const jours = document.querySelector('input[name="periode"]:checked').value;
  $("actualiser-feux").disabled = true;
  if (actualiser) $("etat-feux").textContent = "Téléchargement du flux NASA, jusqu'à une minute…";
  try {
    dessinerFeux(await api(`/api/feux?jours=${jours}&actualiser=${actualiser}`));
    await chargerAlertes();
  } catch (erreur) {
    $("etat-feux").classList.add("erreur");
    $("etat-feux").textContent = erreur.message;
  } finally {
    $("actualiser-feux").disabled = false;
  }
}
$("actualiser-feux").onclick = () => chargerFeux(true);
document.querySelectorAll('input[name="periode"]').forEach((r) => { r.onchange = () => chargerFeux(); });

/* Zone d'analyse */

let zone = null;

function surfaceTexte(z) {
  const largeur = (z.est - z.ouest) * 111.32 * Math.cos(((z.sud + z.nord) / 2) * Math.PI / 180);
  const hauteur = (z.nord - z.sud) * 110.57;
  return `${nombre(largeur, 1)} × ${nombre(hauteur, 1)} km`;
}

function definirZone(z) {
  zone = z;
  cadreZone?.remove();
  cadreZone = L.rectangle([[z.sud, z.ouest], [z.nord, z.est]], { color: "#f2b84b", weight: 2, fill: false, interactive: false }).addTo(carte);
  $("etat-zone").textContent = `Zone de ${surfaceTexte(z)} choisie.`;
  $("chercher-scenes").disabled = false;
  $("scenes").replaceChildren();
  $("etat-scenes").textContent = "";
  scenes.avant = scenes.apres = null;
  majChoixDates();
}

$("zone-centre").onclick = () => {
  const c = carte.getCenter();
  const dLat = 5 / 110.57;
  const dLon = 5 / (111.32 * Math.cos(c.lat * Math.PI / 180));
  definirZone({ ouest: c.lng - dLon, sud: c.lat - dLat, est: c.lng + dLon, nord: c.lat + dLat });
  if (carte.getZoom() < 10) carte.fitBounds(cadreZone.getBounds());
};

let dessin = null;

function arreterDessin() {
  dessin?.provisoire?.remove();
  dessin = null;
  carte.dragging.enable();
  document.body.classList.remove("dessin");
  $("consigne-dessin").hidden = true;
}

$("dessiner-zone").onclick = () => {
  dessin = {};
  carte.dragging.disable();
  document.body.classList.add("dessin");
  $("consigne-dessin").hidden = false;
};
carte.on("mousedown", (e) => {
  if (!dessin) return;
  dessin.depart = e.latlng;
  dessin.provisoire = L.rectangle([e.latlng, e.latlng], { color: "#f2b84b", weight: 2, fillOpacity: 0.1, interactive: false }).addTo(carte);
});
carte.on("mousemove", (e) => {
  if (dessin?.depart) dessin.provisoire.setBounds([dessin.depart, e.latlng]);
});
carte.on("mouseup", (e) => {
  if (!dessin?.depart) return;
  const b = L.latLngBounds(dessin.depart, e.latlng);
  arreterDessin();
  if (b.getEast() - b.getWest() > 0.002) definirZone({ ouest: b.getWest(), sud: b.getSouth(), est: b.getEast(), nord: b.getNorth() });
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && dessin) arreterDessin(); });

/* Scènes */

const scenes = { liste: [], avant: null, apres: null };

function majChoixDates() {
  const { avant, apres } = scenes;
  $("lancer-analyse").disabled = !(avant && apres && avant.id !== apres.id);
  $("choix-dates").textContent = avant && apres
    ? `${formatJour(avant.date)} → ${formatJour(apres.date)}`
    : "Choisis une image « avant » et une image « après ».";
  document.querySelectorAll(".scene button").forEach((b) => {
    b.setAttribute("aria-pressed", scenes[b.dataset.role]?.id === b.dataset.id);
  });
}

function preselectionner() {
  // La saison sèche donne les images les plus nettes : on propose la même période sur deux années.
  const liste = scenes.liste;
  if (liste.length < 2) return;
  const apres = [...liste].sort((a, b) => a.nuages_pct - b.nuages_pct).find((s) => (Date.now() - new Date(s.date)) / 864e5 < 365) || liste[0];
  const jourAnnee = (s) => (new Date(s.date) - new Date(s.date.slice(0, 4) + "-01-01")) / 864e5;
  const ecart = (s) => { const d = Math.abs(jourAnnee(s) - jourAnnee(apres)); return Math.min(d, 365 - d); };
  const avant = liste
    .filter((s) => (new Date(apres.date) - new Date(s.date)) / 864e5 > 200)
    .sort((a, b) => (ecart(a) + a.nuages_pct) - (ecart(b) + b.nuages_pct))[0] || liste[liste.length - 1];
  scenes.avant = avant === apres ? null : avant;
  scenes.apres = apres;
}

$("chercher-scenes").onclick = async () => {
  const etat = $("etat-scenes");
  etat.classList.remove("erreur");
  etat.textContent = "Recherche dans le catalogue…";
  $("chercher-scenes").disabled = true;
  try {
    scenes.liste = await api("/api/scenes", { corps: { zone, debut: $("debut").value, fin: $("fin").value, nuages_max: Number($("nuages").value) } });
    etat.textContent = scenes.liste.length
      ? `${scenes.liste.length} images trouvées. Les nuages indiqués concernent toute la tuile, pas seulement ta zone. Deux dates sont déjà proposées.`
      : "Aucune image : élargis les dates ou le seuil de nuages.";
    $("scenes").replaceChildren(...scenes.liste.map((s) => {
      const bouton = (role, texte) => el("button", {
        type: "button", "data-role": role, "data-id": s.id, "aria-pressed": "false",
        "aria-label": `${texte} : ${formatJour(s.date)}`,
        onclick: () => { scenes[role] = s; majChoixDates(); },
      }, texte);
      return el("div", { class: "scene" },
        el("div", {}, formatJour(s.date), el("small", {}, `${nombre(s.nuages_pct, 1)} % de nuages · ${s.satellite}`)),
        bouton("avant", "Avant"), bouton("apres", "Après"));
    }));
    preselectionner();
    majChoixDates();
  } catch (erreur) {
    etat.classList.add("erreur");
    etat.textContent = erreur.message;
  } finally {
    $("chercher-scenes").disabled = false;
  }
};

/* Analyse */

let analyse = null;

$("lancer-analyse").onclick = async () => {
  const etat = $("etat-analyse");
  etat.classList.remove("erreur");
  etat.textContent = "Démarrage…";
  $("lancer-analyse").disabled = true;
  $("resultat").hidden = true;
  try {
    const { id } = await api("/api/analyses", { corps: { zone, scene_avant: scenes.avant.id, scene_apres: scenes.apres.id } });
    for (;;) {
      const suivi = await api(`/api/analyses/${id}`);
      if (suivi.statut === "termine") { afficherResultat(suivi.resultat); break; }
      if (suivi.statut === "erreur") throw new Error(suivi.message);
      etat.textContent = `${suivi.etape}… (une à trois minutes au total)`;
      await new Promise((r) => setTimeout(r, 1500));
    }
    etat.textContent = "";
    await chargerAlertes();
  } catch (erreur) {
    etat.classList.add("erreur");
    etat.textContent = erreur.message;
  } finally {
    majChoixDates();
  }
};

function afficherCalque() {
  if (!analyse) return;
  const nom = document.querySelector('input[name="calque"]:checked').value;
  const [o, s, e, n] = analyse.bbox;
  coucheAnalyse?.remove();
  coucheAnalyse = L.imageOverlay(`/donnees/analyses/${analyse.id}/${nom}`, [[s, o], [n, e]], {
    opacity: $("opacite").value / 100, alt: "Résultat de la comparaison",
  }).addTo(carte);
  const legende = $("legende");
  if (nom === "changement.png") {
    legende.replaceChildren(
      el("div", {}, el("span", { class: "pastille", style: "background:#ff00a0" }), "Baisse de végétation détectée"),
      el("div", {}, el("span", { class: "pastille", style: "background:#787878" }), "Non comparable : nuages, ombres ou pas d'image"),
      el("div", {}, "Le reste de la zone : pas de baisse au-dessus du seuil"));
  } else if (nom.startsWith("ndvi")) {
    legende.replaceChildren(el("span", { class: "degrade" }), el("span", { class: "degrade-texte" }, el("span", {}, "Sol nu"), el("span", {}, "Végétation dense")));
  } else {
    legende.replaceChildren();
  }
}
document.querySelectorAll('input[name="calque"]').forEach((r) => { r.onchange = afficherCalque; });
$("opacite").oninput = () => coucheAnalyse?.setOpacity($("opacite").value / 100);

function afficherResultat(r) {
  analyse = r;
  $("resultat").hidden = false;
  $("chiffres-analyse").replaceChildren(
    chiffre(`${nombre(r.perte_ha, 1)} ha`, "de baisse détectée"),
    chiffre(`${nombre(r.comparable_pct)} %`, "de la zone comparable"),
    chiffre(r.ndvi_median.avant === null ? "—" : nombre(r.ndvi_median.avant, 2), `NDVI médian, ${formatJour(r.dates.avant)}`),
    chiffre(r.ndvi_median.apres === null ? "—" : nombre(r.ndvi_median.apres, 2), `NDVI médian, ${formatJour(r.dates.apres)}`));
  $("avertissements").replaceChildren(...r.avertissements.map((a) => el("li", {}, a)));
  const lien = $("lien-alerte");
  lien.replaceChildren();
  if (r.alerte_id) {
    lien.append(el("button", { class: "discret", type: "button", onclick: () => ouvrirAlerte(r.alerte_id) }, "Ouvrir le signal à vérifier"));
  } else {
    lien.textContent = "Pas assez de surface concernée pour créer un signal.";
  }
  document.querySelector('input[name="calque"][value="changement.png"]').checked = true;
  afficherCalque();
  const [o, s, e, n] = r.bbox;
  carte.fitBounds([[s, o], [n, e]]);
}

$("ouvrir-comparateur").onclick = () => {
  const base = `/donnees/analyses/${analyse.id}`;
  const vue = $("comparateur-vue");
  $("image-avant").src = `${base}/avant.png`;
  $("image-apres").src = `${base}/apres.png`;
  $("etiquette-avant").textContent = `Avant : ${formatJour(analyse.dates.avant)}`;
  $("etiquette-apres").textContent = `Après : ${formatJour(analyse.dates.apres)}`;
  $("comparateur-titre").textContent = "Avant / après, en couleurs naturelles";
  const [o, s, e, n] = analyse.bbox;
  const largeur = (e - o) * Math.cos(((s + n) / 2) * Math.PI / 180);
  vue.style.aspectRatio = `${largeur / (n - s)}`;
  placerCurseur();
  $("comparateur").showModal();
};
function placerCurseur() {
  const position = $("curseur").value;
  $("voile-avant").style.clipPath = `inset(0 ${100 - position}% 0 0)`;
  $("trait").style.left = `${position}%`;
}
$("curseur").oninput = placerCurseur;

/* Alertes */

let alerteActive = null;

async function chargerAlertes() {
  const statut = $("filtre-statut").value;
  const alertes = await api(`/api/alertes${statut ? `?statut=${statut}` : ""}`);
  const ouvertes = statut ? null : alertes.filter((a) => ["nouveau", "a_examiner"].includes(a.statut)).length;
  if (ouvertes !== null) $("compteur-alertes").textContent = ouvertes || "";
  const liste = $("alertes");
  if (!alertes.length) {
    liste.replaceChildren(el("li", { class: "vide" }, "Aucun signal pour ce filtre."));
    return;
  }
  liste.replaceChildren(...alertes.map((a) => el("li", {},
    el("button", {
      class: `alerte ${a.priorite}`, type: "button", "aria-current": a.id === alerteActive ? "true" : "false",
      onclick: () => ouvrirAlerte(a.id),
    },
      el("strong", {}, a.titre),
      el("small", {}, `${a.region || "Région inconnue"} · ${formatDate(a.acquis_le)}`),
      el("span", { class: "badges" },
        el("span", { class: "badge" }, STATUTS[a.statut]),
        el("span", { class: "badge" }, PRIORITES[a.priorite]),
        a.zone_pilote ? el("span", { class: "badge pilote" }, "Zone pilote") : null)))));
}
$("filtre-statut").onchange = chargerAlertes;

function ligneInfo(terme, valeur) {
  return [el("dt", {}, terme), el("dd", {}, valeur === null || valeur === undefined ? "—" : String(valeur))];
}

async function ouvrirAlerte(id) {
  const a = await api(`/api/alertes/${id}`);
  alerteActive = id;
  coucheAlerte.clearLayers();
  const forme = L.geoJSON(a.geometrie, {
    style: { color: "#ffe45c", weight: 2, fill: false },
    pointToLayer: (_, position) => L.circleMarker(position, { radius: 14, color: "#ffe45c", weight: 3, fill: false }),
  }).addTo(coucheAlerte);
  if (a.geometrie.type === "Point") carte.setView(forme.getBounds().getCenter(), 13);
  else carte.fitBounds(forme.getBounds().pad(0.3));

  const infos = [
    ...ligneInfo("Type", TYPES[a.type]), ...ligneInfo("Priorité", PRIORITES[a.priorite]),
    ...ligneInfo("Région", a.region), ...ligneInfo("Acquisition", formatDate(a.acquis_le)),
    ...ligneInfo("Réception", formatDate(a.recu_le)), ...ligneInfo("Source", a.source),
    ...ligneInfo("Satellite", a.capteur), ...ligneInfo("Résolution", `${nombre(a.resolution_m, 1)} m`),
    ...ligneInfo("Nuages (tuile)", a.nuages_pct === null ? null : `${nombre(a.nuages_pct, 1)} %`),
    ...ligneInfo("Score indicatif", a.score === null ? null : `${nombre(a.score * 100)} / 100`),
    ...ligneInfo("Règle", a.version_regle),
  ];
  if (a.type === "feu") infos.push(...ligneInfo("Détections", a.preuves.points), ...ligneInfo("Puissance max.", `${nombre(a.preuves.puissance_max_mw, 1)} MW`));
  else infos.push(...ligneInfo("Dates comparées", `${formatJour(a.preuves.dates.avant)} → ${formatJour(a.preuves.dates.apres)}`), ...ligneInfo("Zone comparable", `${nombre(a.preuves.comparable_pct)} %`));

  const note = el("textarea", { id: "note-decision", placeholder: "Sur quoi repose ta décision ?", "aria-label": "Note de décision" });
  const message = el("p", { class: "etat", "aria-live": "polite" });
  const decider = async (decision) => {
    message.classList.remove("erreur");
    try {
      await api(`/api/alertes/${id}/decision`, { corps: { decision, note: note.value } });
      await chargerAlertes();
      await ouvrirAlerte(id);
    } catch (erreur) {
      message.classList.add("erreur");
      message.textContent = erreur.message;
    }
  };

  const suites = Object.keys(ACTIONS).filter((s) => a.suites.includes(s));
  const panneau = $("dossier");
  panneau.hidden = false;
  panneau.replaceChildren(...[
    el("div", { class: "dossier-tete" }, el("span", {}, `Dossier n° ${a.id} · ${STATUTS[a.statut]}`),
      el("button", { class: "discret", type: "button", onclick: fermerDossier }, "Fermer")),
    el("h2", {}, a.titre),
    el("dl", {}, ...infos),
    el("h3", {}, "Limites connues"),
    el("p", {}, a.limites),
    ...(a.preuves.avertissements || []).map((t) => el("p", { class: "etat" }, `À noter : ${t}`)),
    suites.length ? el("h3", {}, "Décision de l'analyste") : null,
    suites.length ? note : null,
    suites.length ? el("div", { class: "actions" }, ...suites.map((s) =>
      el("button", { type: "button", class: ACTIONS[s].classe || "", onclick: () => decider(s) }, ACTIONS[s].texte))) : null,
    message,
    a.decisions.length ? el("h3", {}, "Historique") : null,
    a.decisions.length ? el("ul", { class: "historique" }, ...a.decisions.map((d) =>
      el("li", {}, el("strong", {}, STATUTS[d.decision]), ` : ${d.note || "sans note"}`, el("br"), el("small", {}, `${formatDate(d.le)} · ${d.auteur}`)))) : null,
    el("p", {}, el("a", { href: `/api/alertes/${a.id}/rapport`, target: "_blank", rel: "noopener" }, "Ouvrir le rapport")),
  ].filter(Boolean));
  await chargerAlertes();
}

function fermerDossier() {
  $("dossier").hidden = true;
  alerteActive = null;
  coucheAlerte.clearLayers();
  chargerAlertes();
}

/* Démarrage */

const aujourdhui = new Date();
const ilYaDeuxAns = new Date(aujourdhui);
ilYaDeuxAns.setFullYear(aujourdhui.getFullYear() - 2);
$("fin").value = aujourdhui.toISOString().slice(0, 10);
$("debut").value = ilYaDeuxAns.toISOString().slice(0, 10);

chargerRegions().catch(() => {});
chargerAlertes().catch(() => {});
chargerFeux();
