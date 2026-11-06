# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Régression sur enregistrements RÉELS étiquetés (AirPods 3). Chaque fichier absent -> test ignoré.

Format : [[t_arrivée, phase, hex_trame_RemoteHID], ...]. Pour ajouter un jeu de données, ajouter une
entrée à DATASETS : (fichier, source, cibles). Une cible = (phase, types d'événements, op, n) ;
les événements sont attribués à la phase active LAG s plus tôt (temps de réaction).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gestures as gz  # noqa: E402

CAPTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures")
# profil tête calibré par l'utilisateur (config.json, 2013_head) : axes de l'écouteur porté
HEAD_PROFILE = {"yaw_sign": 1, "lat": [-0.733, 0.4821, -0.4798], "pitch_sign": 1, "roll_sign": -1}
LAG = 0.5
CLICKS = ("teeth_click", "teeth_double")
HEAD = ("nod", "shake", "tilt_left", "tilt_right", "look_up", "look_down")
TILTS = ("tilt_left", "tilt_right")

DATASETS = {
    # jeu étiqueté (silence, claquements, mastication, parole, mime, gestes de tête), IMU brute 201 Hz
    "gestures_labeled.json": ("raw", [
        ("dents_simple", ("teeth_click",), ">=", 5),
        ("dents_double", ("teeth_double",), ">=", 3),
        ("silence", CLICKS, "<=", 1), ("parle", CLICKS, "<=", 1), ("mime", CLICKS, "<=", 1),
        ("mache", CLICKS, "<=", 2),
        *[(ph, CLICKS, "<=", 1) for ph in ("hoche", "secoue", "penche_g", "penche_d", "haut", "bas")],
        *[(ph, HEAD, "<=", 1) for ph in ("silence", "dents_simple", "dents_double", "mache", "parle", "mime")],
        ("hoche", ("nod",), ">=", 4), ("secoue", ("shake",), ">=", 4),
        ("hoche", ("shake",) + TILTS, "<=", 0), ("secoue", ("nod",) + TILTS, "<=", 1),
        # penche_g et penche_d : l'écouteur a mesuré la MÊME rotation dans les deux phases
        # (intégrale gyro identique) -> seule la détection est vérifiée, pas le côté pour penche_d
        ("penche_g", ("tilt_left",), ">=", 2), ("penche_d", TILTS, ">=", 2),
        ("haut", ("look_up",), ">=", 2), ("bas", ("look_down",), ">=", 2),
        ("haut", ("look_down",), "<=", 0), ("bas", ("look_up",), "<=", 0),
    ]),
    # assis / marche / debout / trot (sur place) / assis : devmotion6 ~50 Hz
    "cma_walk.json": ("auto", [
        ("marche", CLICKS + ("nod", "shake"), "<=", 0), ("trot", CLICKS + ("nod", "shake"), "<=", 0),
        ("marche", CLICKS + HEAD, "<=", 2), ("trot", CLICKS + HEAD, "<=", 2),
    ]),
}
# données sans gestes volontaires (étiquettes invalides) : seulement un contrôle de faux positifs
UNLABELED_STILL = {"inventory_musique.json": 6}


def run(name, source, **cfg):
    rows = gz.load_recording(os.path.join(CAPTURES, name), source)
    ev, st, rej, _ = gz.replay(rows, {"require_activation": False, **cfg}, HEAD_PROFILE)
    return gz.phase_report(ev, rows, LAG), ev, st


class TestRealRecordings(unittest.TestCase):
    def check(self, name, extra=(), **cfg):
        path = os.path.join(CAPTURES, name)
        if not os.path.exists(path):
            self.skipTest(f"{name} absent")
        source, targets = DATASETS[name]
        targets = list(targets) + list(extra)
        rep, _, st = run(name, source, **cfg)
        fails = []
        for ph, types, op, n in targets:
            if ph not in rep:
                fails.append(f"phase {ph} absente")
                continue
            got = sum(rep[ph].get(t, 0) for t in types)
            if not (got >= n if op == ">=" else got <= n):
                fails.append(f"{ph}: {'+'.join(types)} = {got} (attendu {op} {n})")
        self.assertFalse(fails, "\n".join(fails) + f"\npar phase : {rep}")
        return st

    def test_gestures_labeled(self):
        st = self.check("gestures_labeled.json")
        self.assertGreater(st["rate_hz"], 180)          # IMU brute à ~201 Hz, horodatage capteur
        self.assertLess(st["dup_ratio"], 0.01)

    def test_low_threshold_does_not_lose_clicks(self):
        """Régression : baisser click_thresh faisait PERDRE des claquements (durée mesurée jusqu'à lo/2,
        rebond d'ouverture de la mâchoire compté comme mastication, réfractaire relancé par une petite
        impulsion). 7 claquements simples et 5 doubles dans l'enregistrement."""
        for thr in (0.6, 0.45, 0.3):
            self.check("gestures_labeled.json", [("dents_simple", ("teeth_click",), ">=", 7),
                                                 ("dents_double", ("teeth_double",), ">=", 4)], click_thresh=thr)
        for thr in (0.45, 0.3):
            self.check("cma_walk.json", click_thresh=thr)

    def test_cma_walk(self):
        self.check("cma_walk.json")

    def test_unlabeled_still(self):
        for name, max_events in UNLABELED_STILL.items():
            if not os.path.exists(os.path.join(CAPTURES, name)):
                continue
            for source in ("raw", "dm6"):
                _, ev, _ = run(name, source)
                self.assertLessEqual(len(ev), max_events, (name, source, [e["type"] for e in ev]))

    def test_raw_and_devmotion_share_axes(self):
        """IMU brute et devmotion6 : même repère et mêmes unités (°/s), horloges différentes."""
        path = os.path.join(CAPTURES, "gestures_labeled.json")
        if not os.path.exists(path):
            self.skipTest("absent")
        raw = [r for r in gz.load_recording(path, "raw") if r["label"] == "hoche"]
        dm6 = [r for r in gz.load_recording(path, "dm6") if r["label"] == "hoche"]
        for k in range(3):
            a = max(abs(r["w"][k]) for r in raw)
            b = max(abs(r["w"][k]) for r in dm6)
            self.assertAlmostEqual(a / b, 1.0, delta=0.25)


if __name__ == "__main__":
    unittest.main()
