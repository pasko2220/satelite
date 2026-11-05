"""Stockage SQLite : scènes, alertes, décisions humaines et journal d'audit."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY, fournisseur TEXT, collection TEXT, capteur TEXT, acquis_le TEXT,
    emprise TEXT, resolution_m REAL, nuages_pct REAL, niveau TEXT, licence TEXT,
    uri TEXT, recu_le TEXT
);
CREATE TABLE IF NOT EXISTS alertes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cle TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'nouveau', priorite TEXT NOT NULL, titre TEXT NOT NULL,
    region TEXT, zone_pilote INTEGER NOT NULL DEFAULT 0, geometrie TEXT NOT NULL,
    score REAL, acquis_le TEXT, recu_le TEXT NOT NULL, source TEXT, capteur TEXT,
    resolution_m REAL, nuages_pct REAL, version_regle TEXT, preuves TEXT, limites TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alerte_id INTEGER NOT NULL REFERENCES alertes(id),
    decision TEXT NOT NULL, note TEXT NOT NULL, auteur TEXT NOT NULL, le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, acteur TEXT NOT NULL, action TEXT NOT NULL,
    objet_type TEXT NOT NULL, objet_id TEXT NOT NULL, avant TEXT, apres TEXT, le TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_sans_modification BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'journal en ajout seul'); END;
CREATE TRIGGER IF NOT EXISTS audit_sans_suppression BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'journal en ajout seul'); END;
"""

# Cycle de vie défini par la feuille de route (section 9).
TRANSITIONS = {
    "nouveau": {"a_examiner", "confirme", "rejete"},
    "a_examiner": {"confirme", "rejete"},
    "confirme": {"publie", "cloture", "a_examiner"},
    "rejete": {"cloture", "a_examiner"},
    "publie": {"cloture"},
    "cloture": set(),
}
NOTE_OBLIGATOIRE = {"confirme", "rejete"}


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connexion():
    config.DONNEES.mkdir(exist_ok=True)
    cnx = sqlite3.connect(config.BASE, timeout=30)
    cnx.row_factory = sqlite3.Row
    try:
        with cnx:
            yield cnx
    finally:
        cnx.close()


def initialiser() -> None:
    with connexion() as cnx:
        cnx.executescript(SCHEMA)


def _journaliser(cnx, action: str, objet_type: str, objet_id, avant=None, apres=None) -> None:
    cnx.execute(
        "INSERT INTO audit (acteur, action, objet_type, objet_id, avant, apres, le) VALUES (?,?,?,?,?,?,?)",
        (config.AUTEUR_LOCAL, action, objet_type, str(objet_id), avant, apres, maintenant()),
    )


def enregistrer_scene(scene: dict) -> None:
    colonnes = ("id", "fournisseur", "collection", "capteur", "acquis_le", "emprise", "resolution_m",
                "nuages_pct", "niveau", "licence", "uri", "recu_le")
    with connexion() as cnx:
        cnx.execute(
            f"INSERT OR IGNORE INTO scenes ({','.join(colonnes)}) VALUES ({','.join('?' * len(colonnes))})",
            [scene[c] for c in colonnes],
        )


def creer_alerte(alerte: dict) -> int | None:
    """Crée une alerte une seule fois par clé ; renvoie son id, ou None si elle existait."""
    colonnes = ("cle", "type", "priorite", "titre", "region", "zone_pilote", "geometrie", "score", "acquis_le",
                "recu_le", "source", "capteur", "resolution_m", "nuages_pct", "version_regle", "preuves", "limites")
    valeurs = dict(alerte, geometrie=json.dumps(alerte["geometrie"]), preuves=json.dumps(alerte["preuves"]))
    with connexion() as cnx:
        curseur = cnx.execute(
            f"INSERT OR IGNORE INTO alertes ({','.join(colonnes)}) VALUES ({','.join('?' * len(colonnes))})",
            [valeurs[c] for c in colonnes],
        )
        if curseur.rowcount == 0:
            return None
        _journaliser(cnx, "alerte_creee", "alerte", curseur.lastrowid, apres="nouveau")
        return curseur.lastrowid


def _ligne_alerte(ligne: sqlite3.Row) -> dict:
    alerte = dict(ligne)
    alerte["geometrie"] = json.loads(alerte["geometrie"])
    alerte["preuves"] = json.loads(alerte["preuves"] or "{}")
    return alerte


def lister_alertes(statut: str | None = None) -> list[dict]:
    requete = "SELECT * FROM alertes"
    parametres: tuple = ()
    if statut:
        requete += " WHERE statut = ?"
        parametres = (statut,)
    requete += " ORDER BY zone_pilote DESC, acquis_le DESC, id DESC"
    with connexion() as cnx:
        return [_ligne_alerte(l) for l in cnx.execute(requete, parametres)]


def lire_alerte(alerte_id: int) -> dict | None:
    with connexion() as cnx:
        ligne = cnx.execute("SELECT * FROM alertes WHERE id = ?", (alerte_id,)).fetchone()
        if ligne is None:
            return None
        alerte = _ligne_alerte(ligne)
        alerte["decisions"] = [dict(l) for l in cnx.execute(
            "SELECT decision, note, auteur, le FROM decisions WHERE alerte_id = ? ORDER BY id", (alerte_id,))]
        alerte["suites"] = sorted(TRANSITIONS[alerte["statut"]])
        return alerte


def decider(alerte_id: int, decision: str, note: str) -> dict:
    note = note.strip()
    if decision in NOTE_OBLIGATOIRE and not note:
        raise ValueError("Une note est obligatoire pour confirmer ou rejeter.")
    with connexion() as cnx:
        ligne = cnx.execute("SELECT statut FROM alertes WHERE id = ?", (alerte_id,)).fetchone()
        if ligne is None:
            raise LookupError("Alerte introuvable.")
        if decision not in TRANSITIONS[ligne["statut"]]:
            raise ValueError(f"Passage impossible de « {ligne['statut']} » à « {decision} ».")
        cnx.execute("UPDATE alertes SET statut = ? WHERE id = ?", (decision, alerte_id))
        cnx.execute(
            "INSERT INTO decisions (alerte_id, decision, note, auteur, le) VALUES (?,?,?,?,?)",
            (alerte_id, decision, note, config.AUTEUR_LOCAL, maintenant()),
        )
        _journaliser(cnx, "statut_modifie", "alerte", alerte_id, avant=ligne["statut"], apres=decision)
    return lire_alerte(alerte_id)


def lire_scenes(ids: list[str]) -> list[dict]:
    marques = ",".join("?" * len(ids))
    with connexion() as cnx:
        return [dict(l) for l in cnx.execute(f"SELECT * FROM scenes WHERE id IN ({marques})", ids)]


def lire_audit(limite: int = 100) -> list[dict]:
    with connexion() as cnx:
        return [dict(l) for l in cnx.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limite,))]
