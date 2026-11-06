# MousePods → OpenTrack : suivi de tête pour simulateurs

Les AirPods deviennent un « TrackIR » : en tournant la tête, la caméra du jeu (simulateur de vol,
de course…) suit. MousePods envoie les angles de la tête à **OpenTrack**, qui les transmet au jeu.

```
AirPods ──Bluetooth──▶ MousePods ──UDP 127.0.0.1:4242──▶ OpenTrack ──FreeTrack/TrackIR, X-Plane…──▶ jeu
```

## 1. Activer la sortie dans MousePods

Au choix :

* pour une session : `./airpods_mouse_controller.py --opentrack`
* durablement : interface → **Réglages** → carte **OpenTrack** → « Envoyer le suivi de tête à OpenTrack »,
  ou dans `config.json` :

```json
"opentrack": {"enabled": true, "host": "127.0.0.1", "port": 4242, "rate_hz": 120}
```

Par défaut, **le curseur est figé** tant que la sortie OpenTrack est active
(`"pause_mouse_when_opentrack": true`) : la tête pilote la caméra, pas la souris. Mettre `false` pour
garder les deux.

Autres réglages (`config.json`, section `opentrack`) :

| clé | défaut | rôle |
|---|---|---|
| `invert_yaw` / `invert_pitch` / `invert_roll` | `false` | inverser un axe |
| `scale_yaw` / `scale_pitch` / `scale_roll` | `1.0` | gain par axe (OpenTrack a aussi ses courbes) |
| `smoothing` | `0.2` | lissage 0…1 (filtre 1€) ; OpenTrack a ses propres filtres |
| `rate_hz` | `120` | paquets par seconde (indépendant de la fréquence du capteur) |
| `drift_correction` | `true` | la gravité recale tangage et roulis |
| `drift_tau_s` | `2.0` | constante de temps de cette correction (s) |
| `yaw_autocenter_s` | `0` | > 0 : le lacet revient doucement au centre quand la tête est immobile |
| `lean_experimental` | `false` | **expérimental** : estime x/z par un modèle « pivot au cou » (non vérifié) |

Calibre d'abord la position « tête » (`--calibrate` ou page Calibration) : les axes envoyés à
OpenTrack sont ceux du profil calibré, quelle que soit la façon dont l'écouteur est porté.

## 2. Régler OpenTrack

1. Installer OpenTrack (paquet de la distribution, Flatpak, ou <https://github.com/opentrack/opentrack>).
2. **Entrée (Input)** : `UDP over network` → bouton de réglage → **port 4242** (valeur par défaut).
3. **Sortie (Output)**, selon le jeu :
   * **Jeu Windows sous Wine / Proton (Steam)** : `Wine -- Windows layer for Unix`. Dans ses
     réglages, choisir Proton (version + identifiant Steam du jeu) ou le préfixe Wine, et cocher
     l'émulation **FreeTrack** et/ou **TrackIR (NPClient)**. Lancer OpenTrack *avant* le jeu.
   * **X-Plane natif Linux** : sortie `X-Plane` (plugin fourni avec OpenTrack).
   * **FlightGear** : sortie `FlightGear`.
   * Jeux qui acceptent un joystick : sortie `libevdev joystick` (axes virtuels).
4. **Start**. Les angles bougent dans OpenTrack quand tu tournes la tête.
5. Ajuster les courbes (Mapping) : un petit mouvement de tête peut donner un grand angle de vue.

## 3. Recentrer

Regarde l'écran bien en face et recentre :

* raccourci **Ctrl+Alt+C** (installé par `--install-hotkeys`),
* `./airpods_mouse_controller.py --send recenter`,
* bouton « Recentrer » de l'interface, ou une pression sur la tige si tu l'as associée à `recenter`.

Le recentrage de MousePods remet les angles envoyés à zéro. OpenTrack a aussi sa propre touche
« Center » : l'une ou l'autre suffit.

## 4. Limites

* **3 degrés de liberté seulement** (lacet, tangage, roulis). Les AirPods n'ont pas de caméra :
  impossible de mesurer le déplacement de la tête (se pencher vers l'avant/le côté). x, y, z = 0.
* **Dérive du lacet** : sans magnétomètre, aucune référence absolue pour le cap. Le biais du gyroscope
  est ré-estimé à chaque immobilité, mais un léger décalage s'accumule : recentrer (Ctrl+Alt+C) de
  temps en temps. Tangage et roulis sont recalés en continu par la gravité.
* **Latence Bluetooth** : ~10–30 ms de plus qu'un TrackIR, et de petites coupures quand le lien est
  chargé (audio A2DP + capteurs). 100 Hz (`sensor_interval_us: 10000`) est le meilleur compromis.
* AirPods 3 : capteurs coupés dans le boîtier ; il faut porter au moins un écouteur.

## Détails techniques

Paquet « UDP over network » : 6 `double` (48 octets), ordre x, y, z (cm), lacet, tangage, roulis
(degrés), envoyés tels quels — format vérifié dans le code source d'OpenTrack (voir l'en-tête de
`opentrack_out.py`). Les angles viennent de l'intégration des vitesses calibrées de `MotionEngine`
(et non du quaternion des AirPods, dont le repère dépend du port de l'écouteur) ; les raisons sont
détaillées dans `opentrack_out.py`. Tests : `tests/test_opentrack.py`.
