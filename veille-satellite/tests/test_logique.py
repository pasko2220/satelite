"""Tests sans réseau. Lancer depuis veille-satellite : .venv\\Scripts\\python -m unittest discover tests"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import analyse, config, db, firms, rapport, zones  # noqa: E402

GRILLE = analyse.Grille(0, 1000, 100, 100, 10, 6)
HA = GRILLE.hectares_par_pixel


class Ndvi(unittest.TestCase):
    def test_valeurs(self):
        ndvi = analyse.calculer_ndvi(np.array([300.0, 0.0]), np.array([2700.0, 0.0]))
        self.assertAlmostEqual(float(ndvi[0]), 0.8, places=5)
        self.assertTrue(np.isnan(ndvi[1]))

    def test_decalage_deja_applique_non_retire(self):
        item = {"properties": {"s2:processing_baseline": "05.13", "earthsearch:boa_offset_applied": True}}
        self.assertEqual(float(analyse.corriger_reflectance(np.array([316], "uint16"), item)[0]), 316)

    def test_decalage_retire_si_absent(self):
        item = {"properties": {"s2:processing_baseline": "05.13", "earthsearch:boa_offset_applied": False}}
        self.assertEqual(float(analyse.corriger_reflectance(np.array([1316], "uint16"), item)[0]), 316)

    def test_ancienne_chaine_sans_decalage(self):
        item = {"properties": {"s2:processing_baseline": "03.01"}}
        self.assertEqual(float(analyse.corriger_reflectance(np.array([316], "uint16"), item)[0]), 316)


class Masque(unittest.TestCase):
    def test_marge_autour_des_nuages(self):
        scl = np.full((20, 20), 4, "uint8")
        scl[10, 10] = 9
        masque = analyse.masque_utile(scl, 10)
        self.assertFalse(masque[10, 10])
        self.assertFalse(masque[10, 16])  # 60 m de marge
        self.assertTrue(masque[10, 17])

    def test_petit_nuage_blanc_exclu(self):
        scl = np.full((20, 20), 4, "uint8")
        bleu = np.full((20, 20), 40, "uint8")
        bleu[5, 5] = 150
        masque = analyse.masque_utile(scl, 10, bleu)
        self.assertFalse(masque[5, 5])
        self.assertTrue(masque[15, 15])

    def test_sol_defriche_garde(self):
        scl = np.full((5, 5), 5, "uint8")
        self.assertTrue(analyse.masque_utile(scl, 10, np.full((5, 5), 65, "uint8")).all())

    def test_pixel_sans_donnee_exclu(self):
        scl = np.full((5, 5), 4, "uint8")
        scl[0, 0] = 0
        self.assertFalse(analyse.masque_utile(scl, 10)[0, 0])


class Perte(unittest.TestCase):
    def setUp(self):
        self.avant = np.full((100, 100), 0.8, "float32")
        self.apres = self.avant.copy()
        self.tout = np.ones((100, 100), bool)

    def test_foret_coupee_detectee(self):
        self.apres[10:40, 10:40] = 0.3
        perte = analyse.detecter_perte(self.avant, self.apres, self.tout, HA)
        self.assertEqual(int(perte.sum()), 900)

    def test_tache_trop_petite_ignoree(self):
        self.apres[10:12, 10:12] = 0.3
        self.assertFalse(analyse.detecter_perte(self.avant, self.apres, self.tout, HA).any())

    def test_baisse_faible_ignoree(self):
        self.apres[10:40, 10:40] = 0.65
        self.assertFalse(analyse.detecter_perte(self.avant, self.apres, self.tout, HA).any())

    def test_zone_deja_nue_ignoree(self):
        self.avant[10:40, 10:40] = 0.4
        self.apres[10:40, 10:40] = 0.1
        self.assertFalse(analyse.detecter_perte(self.avant, self.apres, self.tout, HA).any())

    def test_pixels_non_comparables_ignores(self):
        self.apres[10:40, 10:40] = 0.3
        self.tout[10:40, 10:40] = False
        self.assertFalse(analyse.detecter_perte(self.avant, self.apres, self.tout, HA).any())

    def test_polygone_a_la_bonne_surface(self):
        perte = np.zeros((100, 100), bool)
        perte[10:40, 10:40] = True
        polygones = analyse.extraire_polygones(perte, GRILLE)
        self.assertEqual(len(polygones), 1)
        self.assertAlmostEqual(polygones[0]["hectares"], 900 * HA, places=1)


class Feux(unittest.TestCase):
    @staticmethod
    def point(region="Nawa", confiance="nominal"):
        return {"lon": -6.0, "lat": 6.0, "region": region, "date": "2026-10-29", "confiance": confiance}

    def test_faible_confiance_jamais_alerte(self):
        self.assertEqual(firms.grouper([self.point(confiance="low")]), {})

    def test_detection_isolee_hors_pilote_ignoree(self):
        self.assertIsNone(firms.priorite_groupe([self.point()]))

    def test_detection_isolee_en_zone_pilote_alerte(self):
        self.assertEqual(firms.priorite_groupe([self.point(config.ZONE_PILOTE)]), "moyenne")

    def test_plusieurs_detections_regroupees(self):
        self.assertEqual(len(firms.grouper([self.point(), self.point()])), 1)

    def test_fusion_sans_doublon_et_sans_vieux_points(self):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        vieux = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        p = dict(self.point(), satellite="NOAA-20", acquis_le=recent)
        fusion = firms.fusionner([p, dict(p, acquis_le=vieux)], [dict(p)])
        self.assertEqual(len(fusion), 1)


class Zones(unittest.TestCase):
    def test_guiglo_dans_le_cavally(self):
        self.assertEqual(zones.region_du_point(-7.49, 6.54), "Cavally")

    def test_hors_pays(self):
        self.assertIsNone(zones.region_du_point(-10.0, 6.0))


class Rapport(unittest.TestCase):
    def test_formule_neutralisee_dans_csv(self):
        self.assertEqual(rapport._cellule("=1+1"), "'=1+1")
        self.assertEqual(rapport._cellule("Nawa"), "Nawa")


class Statuts(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.reglage = config.DONNEES, config.BASE
        config.DONNEES = Path(self.dossier.name)
        config.BASE = config.DONNEES / "test.sqlite3"
        db.initialiser()
        self.id = db.creer_alerte({
            "cle": "essai", "type": "feu", "priorite": "moyenne", "titre": "Essai", "region": "Nawa", "zone_pilote": 0,
            "geometrie": {"type": "Point", "coordinates": [-6, 6]}, "score": 0.6, "acquis_le": "2026-10-29T10:00:00+00:00",
            "recu_le": "2026-10-29T12:00:00+00:00", "source": "Essai", "capteur": "Essai", "resolution_m": 375,
            "nuages_pct": None, "version_regle": "essai", "preuves": {}, "limites": "Essai",
        })

    def tearDown(self):
        config.DONNEES, config.BASE = self.reglage
        self.dossier.cleanup()

    def test_doublon_ignore(self):
        alerte = db.lire_alerte(self.id)
        alerte.update(preuves={}, cle="essai")
        self.assertIsNone(db.creer_alerte(alerte))

    def test_note_obligatoire(self):
        with self.assertRaises(ValueError):
            db.decider(self.id, "confirme", "   ")
        self.assertEqual(db.lire_alerte(self.id)["statut"], "nouveau")

    def test_publication_directe_refusee(self):
        with self.assertRaises(ValueError):
            db.decider(self.id, "publie", "Trop tôt")

    def test_parcours_complet_et_audit(self):
        db.decider(self.id, "confirme", "Vu sur les deux images")
        db.decider(self.id, "publie", "")
        alerte = db.decider(self.id, "cloture", "")
        self.assertEqual(alerte["statut"], "cloture")
        self.assertEqual(alerte["suites"], [])
        self.assertEqual(len(db.lire_audit()), 4)

    def test_journal_non_modifiable(self):
        with self.assertRaises(sqlite3.IntegrityError), db.connexion() as cnx:
            cnx.execute("DELETE FROM audit")


class EnLigne(unittest.TestCase):
    """Règles d'accès quand le site est public."""

    def requete(self, jeton=None):
        from starlette.requests import Request
        entetes = [(b"x-jeton-analyste", jeton.encode())] if jeton is not None else []
        return Request({"type": "http", "headers": entetes})

    def setUp(self):
        from app import main
        self.main = main
        self.reglage = config.JETON_ANALYSTE, config.EN_LIGNE

    def tearDown(self):
        config.JETON_ANALYSTE, config.EN_LIGNE = self.reglage

    def test_local_sans_code(self):
        config.JETON_ANALYSTE, config.EN_LIGNE = "", False
        self.assertTrue(self.main._jeton_valide(self.requete()))

    def test_en_ligne_sans_code_configure_refuse_tout(self):
        config.JETON_ANALYSTE, config.EN_LIGNE = "", True
        self.assertFalse(self.main._jeton_valide(self.requete("n'importe")))

    def test_en_ligne_bon_et_mauvais_code(self):
        config.JETON_ANALYSTE, config.EN_LIGNE = "secret-essai", True
        self.assertTrue(self.main._jeton_valide(self.requete("secret-essai")))
        self.assertFalse(self.main._jeton_valide(self.requete("faux")))
        self.assertFalse(self.main._jeton_valide(self.requete()))


if __name__ == "__main__":
    unittest.main()
