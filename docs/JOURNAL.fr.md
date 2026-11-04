# Carnet de rétro-ingénierie — head tracking AirPods sur Linux

État au **2026-11-04**. Machine : Ubuntu 24.04, GNOME Wayland, BlueZ 5.72.
Captures brutes : [captures/](captures/).

---

## 1. Ce qui marche

| | AirPods Pro 2 (`XX:XX:XX:XX:XX:XX`, PID `0x2014`) | AirPods 3 (`XX:XX:XX:XX:XX:XX`, A2564, PID `0x2013`) |
|---|---|---|
| Canal AAP L2CAP PSM 0x1001 sans root | ✅ | ✅ |
| Batterie, détection oreille, infos modèle | ✅ | ✅ |
| Flux head tracking | ✅ **~25 Hz** (SensorDataWX) | ✅ **~200 Hz** (RemoteHID) |

- **Injection souris sans root** : `/dev/uinput` a une ACL `uaccess` pour l'utilisateur de la session. Un pointeur absolu (ABS_X/ABS_Y + BTN_LEFT, comme une tablette QEMU) est classé `ID_INPUT_MOUSE` et accepté par Mutter/Wayland.
- **Raccourcis globaux sous Wayland** : impossible de capter le clavier sans root. Ils passent donc par des raccourcis personnalisés GNOME (gsettings), qui envoient une commande au contrôleur sur un socket UNIX.

---

## 2. Trame AAP de base

```
handshake            00 00 04 00 01 00 02 00 00 00 00 00 00 00 00 00   (réponse 01 00 04 00 …)
feature flags        04 00 04 00 4d 00 d7 00 00 00 00 00 00 00
notifications        04 00 04 00 0f 00 ff ff ff ff
capabilities (req)   04 00 04 00 01 00 00                                (réponse opcode 0x02)
```

Paquets reçus utiles : `0x04` batterie, `0x06` oreilles (`0`=oreille, `1`=dehors, `2`=boîtier, `3`=absent), `0x1d` infos modèle (chaînes), `0x19` pression tige.

### Socket — deux réglages indispensables
1. **Sécurité** : `setsockopt(SOL_BLUETOOTH=274, BT_SECURITY=4, BT_SECURITY_MEDIUM=2)`, sinon pas de données capteur.
2. **MTU de réception** : passer `L2CAP_OPTIONS.imtu` (SOL_L2CAP=6, opt 1) à **2582**, comme iOS. ⚠️ Découverte clé : avec le MTU Linux par défaut (**672**), les AirPods 3 **n'annoncent que les services dont le descripteur tient dans 672 octets** (iedrelay 649, neorelay 633, mando 590). À 2582, ils annoncent aussi **cma (723), devmotion6 (833), gyro (819), accel (820), kadabra (892)**.

---

## 3. Protocole RTBuddy (opcode 0x17)

Format : `04 00 04 00 17 00 | <descripteur u32 LE> | <longueur u16 LE> | <corps>`

Bits de descripteur (dissecteur pabloaul/apple-wireshark) : `0x800` Sensor (ancien), `0x20000` BatteryHealth, `0x40000` SensorV2, `0x100000` **SensorDataWX** (protobuf), entre autres.

### 3a. SensorDataWX (`00 00 10 00`) — AirPods Pro 2/3/4 ✅
```proto
message SensorDataWX { int32 seq=1; int32 log_type=2; bytes another_sensor_stream=3;
  Unknown request_all_descriptors=4; Descriptor descriptor=5; Command cmd=7;
  ServiceSetting service_settings=8; TypeAck idk_start_ack=9; TypeAck cmd_ack=12; }
message ServiceSetting { ServiceType service=1; int32 two=2 /*toujours 2*/; bytes configuration=3; }
message Command { ServiceType service=1; bytes payload=3; }
enum ServiceType { MANDO=6; IED=7; NEO=8; NEORELAY=9; ACCEL=11; GYRO=12; ACTIVITY=14; CMA=15;
                   DEVMOTION6=16; HEARTRATE=19; CMA_COMMAND=79; … }
```
- **Démarrage** : `service_settings{service, 2, 01 + u32 LE intervalle µs}`. Exemple pour DEVMOTION6 à 40 ms :
  `04 00 04 00 17 00 00 00 10 00 0f 00 08 73 42 0b 08 10 10 02 1a 05 01 40 9c 00 00`
  (`08 73` = numéro de séquence, `08 10` = service 16). Un intervalle de 0 arrête le flux.
- Sur les firmwares récents, il faut d'abord demander **ACTIVITY (14)**, puis DEVMOTION6 (16). L'ancienne commande « DEF » (service 14 seul) ne fait rien sur mes Pro 2.
- Les écouteurs accusent réception avec le champ 9 : `4a 02 08 <service>`.
- **Données** : `cmd(7){service=16, payload=58 o}`, parfois plusieurs par trame. Dans le bloc :
  - `[0]=01` ;
  - `[1:9]` horodatage en ns ;
  - **`[20:26]` attitude = partie vectorielle d'un quaternion, int16 LE Q15** (w = √(1−|v|²)) ;
  - `[26:32]` vitesse de rotation ;
  - `[38:44]` accélération utilisateur ;
  - `[44:50]` gravité.
- Décodage vérifié sur un vecteur de test public : il donne exactement le quaternion attendu.

### 3b. Sensor (`00 08 00 00`) — ancien canal, utilisé par les AirPods 3
Annonce (accessoire → hôte) envoyée à la connexion :
```
04 00 04 00 17 00 | 00 08 00 00 | <len> | 00 <handle> 00 00 <c0 c1> 01 00 | <plist XML>
```
- `handle` : numéro **séquentiel, réattribué à chaque connexion** (ex. cma 0x11, devmotion6 0x12, iedrelay 0x13, gyro 0x14, accel 0x15, neorelay 0x16, mando 0x17, kadabra 0x18).
- `c0 c1` : **constant par service** (cma `86 61`, devmotion6 `87 98`, iedrelay `88 3c`, gyro `85 91`, accel `04 92`, neorelay `83 34`, mando `02 1f`, kadabra `01 b6`). Ce n'est aucun des CRC16 courants.
- `01 00` : probablement le type « ajout de périphérique ».
- Plist : `VendorID=1452`, `AccessoryService`, `MaxReportSize`, `ProtUpdate=1`, `LocationID=1`, `ReportDescriptor` (HID, base64), et parfois `BR`, `PrimaryVendorUsages`, `PRXC`.

Descripteurs HID notables :
- **devmotion6** : entrée rapport 1 = horodatage 64 bits + 182 o vendeur ; **feature rapport 1 = Sensor page 0x20, usage 0x030E « Report Interval » (u32)** ; feature rapport 2 = usage 0x031B.
- **accel** (Sensor usage 0x73) et **gyro** (0x7A) : capteurs HID standards, 3 axes en 32 bits.
- **iedrelay** : feature rapport 1 = Report Interval (u32) ; entrée rapport 2 = 166 o vendeur.

➡️ Il faudrait donc envoyer un **SetReport(feature 1, intervalle)** au handle de devmotion6.

### 3c. Format hôte → accessoire du canal 0x800 (d'après le firmware du clone OpenHuilian)
Source : désassemblage statique de `h3promax3_2607061716_live.up` (fonction `FUN_00ca78b4`). Le clone compare des octets recopiés d'un vrai iPhone, mais **rien n'est encore confirmé sur de vrais AirPods**.
- Corps commun : `00 <seq varint 2 o> 00 | <svc> …`. Le `seq` est un compteur qui démarre à 0x0180 et saute les octets bas à 0.
- **`svc`** : l'octet `c0` de l'annonce **sans le bit 0x80**. Le clone annonce `0x88`, l'hôte envoie `0x08`, et les réponses font écho. Pour les AirPods 3 : devmotion6 `0x87` donne 7, cma `0x86` donne 6, iedrelay `0x88` donne 8.
- Messages selon la longueur du corps :
  - **len 8** (en-tête seul) : demande des descripteurs ; l'accessoire renvoie ses annonces.
  - **len 10** : `<svc>` + 5 o ; réponse `00 SS SS 00 <svc> 05 66 00 00 25 c7 02 00 e0`.
  - **len 14** : **`<svc> 05 06 00 02 04 | 01 00 00 00` = démarrage** ; avec `0a 00 00 00` = arrêt. Réponse `00 SS SS 00 <svc> 03 26 00 00 <b9>`.
- **Rapport d'entrée** (79 o, toutes les 40 ms) : `04 00 04 00 17 00 00 08 00 00 43 00 | 00 SS SS 00 | <svc|0x80> 1f 04 85 00 01 | u64 horodatage | 03 00 43 | u64 | quaternion s16×3 Q15 en 0x29/0x2B/0x2D | vitesse angulaire s16×3 en 0x2F… | accéléromètre s16×3 en 0x3B…`.
- Côté SensorDataWX, le clone accepte HEARTRATE `42 0b 08 13 … 1a 04 <u32 µs>` et accuse réception avec `08 SS SS 10 03 4a 02 08 0e`.

---

### 3d. ✅ RÉSOLU (2026-11-04) — protocole Apple **RemoteHID** (HID sur AACP), AirPods 3 confirmés
Source : pseudo-code Hex-Rays d'iOS 26.1, `RemoteHID.framework` (`HIDRemoteDeviceAACPServer.mm`, `RemoteHID.mm`), dépôt EthanArbuckle/iPhone18-3_26.1_23B85_Restore. Validé sur mes AirPods 3.

Corps d'une trame 0x800 : `[0]=version (0)  [1]=génération (+1 par message)  [2]=0  [3]=côté*2`, puis un ou plusieurs **sous-paquets**. Chacun commence par un **u32 LE H** :
- bits 0-6 : **deviceID** ;
- bits 7-16 : longueur, en-tête compris ;
- bits 17-19 : **type** ;
- bit 20 : horodatage présent ;
- bit 21 : réponse ;
- bit 22 : octet de statut.

Les « 4 octets inexpliqués » des annonces étaient en fait ce mot H. Pour devmotion6, `87 98 01 00` = 0x00019887 donne deviceID 7, longueur 817 et type 0 (DeviceConnect).

| type | sens | contenu |
|---|---|---|
| 0 | Connect | longueur 4 = **HostConnect** (hôte → accessoire) ; plus long = DeviceConnect + plist |
| 1 | Disconnect | |
| 2 | **InputReport** | +4 = type de rapport, +5… = rapport |
| 3 | **SetReport** | +4 = type (0 entrée, 1 sortie, **2 feature**), +5… = rapport, ID en premier |
| 4 | GetReport | +4 = type, +5 = ID du rapport |
| 5 | DeviceConfirmConnect | |

**Séquence qui marche (AirPods 3, firmware 6.x) :**
1. Socket avec `BT_SECURITY_MEDIUM` et **imtu 2582**, puis handshake, flags 0x4d et notifications.
2. **HostConnect** : `04 00 04 00 17 00 00 08 00 00 08 00 | 00 gg 00 00 | 00 02 00 00`. Les DeviceConnect (plists) arrivent ensuite ; noter le deviceID de `devmotion6`.
3. **SetReport feature 1 (Report Interval)** sur devmotion6 :
   `04 00 04 00 17 00 00 08 00 00 0e 00 | 00 gg 00 00 | 07 05 06 00 | 02 | 01 <u32 LE intervalle>`
   (`07 05 06 00` = H pour deviceID 7, longueur 10, type 3). Avec 40, on obtient **environ 200 Hz**.
4. Les **InputReport** arrivent (type 2, deviceID 7, 59 o) : `00` (type) + **le même bloc de 58 o que SensorDataWX**. Le bloc commence par `01` et contient l'horodatage, **le quaternion Q15 en [20:26]**, la vitesse de rotation en [26:32], l'accélération en [38:44] et la gravité en [44:50].
5. Arrêt : SetReport feature 1 = 0.

Le GetReport feature 1 de devmotion6 répond `00 01 00 00 00 00`, soit un intervalle de 0 au repos.


### 3e. Mesures complémentaires (AirPods 3, 2026-11-04)
- **Report Interval en µs** : 20 000 → ~57 paquets/s, 10 000 → ~110/s, plancher ~210/s (≈ 5 ms). Les valeurs ≤ 5000 donnent toutes ~210/s.
- À ~180 paquets/s : 12 trous > 50 ms (max 137 ms) en 23 s (Bluetooth partagé avec l'A2DP et la souris). D'où un défaut à **100 Hz** et un rendu qui « continue sur la lancée » pendant 80 ms.
- **Gyroscope** `[26:32]` ≈ **0,122 °/s par unité** (±4000 °/s), soit la cohérence entre l'intégrale du gyroscope et le quaternion. **Gravité** `[44:50]` ≈ 1000 par g.
- Les autres services (`accel`, `gyro`, `cma`…) se démarrent de la même façon : SetReport feature 1 = intervalle. Ils sont explorables depuis l'onglet « Capteurs » de l'application.


### 3f. Limites matérielles constatées (AirPods 3, 2026-11-04)
- **Écouteurs dans le boîtier : capteurs coupés.** Aucun DeviceConnect, même après HostConnect et une reconnexion Bluetooth complète. Dès qu'un écouteur sort, les 8 services réapparaissent et le flux repart. Le mode « boîtier » est donc impossible sur les AirPods 3.
- **AirPod posé immobile hors de l'oreille : veille des capteurs.** Le gyroscope renvoie exactement 0 et l'accélération est quasi nulle pendant plusieurs secondes, même quand on le pousse ensuite.
- **Glissement par intégration de l'accélération** (capture `captures/airpods3_glide_capture.json`) :
  - 1ʳᵉ glissade d'environ 15 cm vers la droite : estimation **+19,9 cm** (bonne direction, environ 6 cm d'erreur latérale) ;
  - « immobile » tenu à la main : 9,7 cm de dérive ;
  - ensuite, glissades non vues (capteurs en veille), et des pics à 37 m/s² en reprenant l'écouteur.
  - Conclusion : **inutilisable comme souris de table** (forme ronde qui roule, veille, double intégration).
- **Blocage après de nombreuses sessions** : les AirPods n'annoncent plus leurs capteurs alors qu'ils sont portés. Une `bluetoothctl disconnect` + `connect` les débloque. Le contrôleur le fait automatiquement après 10 s.

## 4. Ce qui a été essayé sur les AirPods 3, sans aucune réponse

| Essai | Résultat |
|---|---|
| START « alt » (service 16), « def » (14), max2 (6), devmotion6 alt, MagicPods `5f 5a…` | rien |
| Balayage `service_settings` services 1→31, intervalles 40 000 et 20 000 µs | rien |
| Services 79, 15, ordre 14 puis 16 (méthode mutix), `two` = 0/1/2 | rien, même pas d'accusé |
| `request_all_descriptors` (`22 00`, et `10 02 22 00`), `idk_start_ack` (`4a 02 08 X`) | rien |
| Handshake version mineure 3, code pays `0x29`, prise de contrôle `09 00 06 01` | rien |
| Trame 0x800 : types 0→15 dans plusieurs dispositions (zéro, écho `c0 c1`), charge `01+u32`, `02 01+u32`, sur devmotion6/accel/gyro (≈ 300 combinaisons) | rien |
| ServiceSetting encapsulé dans les trames 0x40000 / 0x1000 / 0x100 / 0x200 | seul 0x200 (BatteryHealth) répond, sans rapport avec le mouvement |
| Accusé « écho d'en-tête » des annonces | rien |
| PC déclaré Apple (`DeviceID = bluetooth:004C:0000:0000` dans `/etc/bluetooth/main.conf`) + **réappairage** | rien de plus, à part les annonces avec MTU 2582 |

Réponse « capabilities » des AirPods 3 :
`0c 01 0e 02 02 04 01 00 00 00 10 02 40 01 80 00 03 02 00 00 00 05 01 00 00 00 09 01 0b 01 07 01 00 00 00 50 02`.
On y voit `0x40` (spatialAudioSupport) et **pas `0xE0`** (« Sensor Data v2 »). Cela confirme que les AirPods 3 ne parlent pas SensorDataWX.

---

## 5. Pistes restantes

1. **Code des firmwares de clones** (OpenHuilian…) qui émulent ce canal côté accessoire. Il contient peut-être le décodage des commandes de l'iPhone. *(agent en cours)*
2. **Côté Apple** : symboles et chaînes de bluetoothd / CoreMotion (CMHeadphoneMotionManager). *(agent en cours)*
3. **Analyses publiques du firmware H1**. *(agent en cours)*
4. **Capture d'un vrai iPhone** avec les AirPods 3, via le profil de journalisation Bluetooth iOS et un sysdiagnose (`.pklg`). C'est la méthode sûre, mais elle demande d'emprunter un iPhone pendant 15 minutes.

## 6. Pièges rencontrés
- `pkill -f <motif>` tue aussi le shell qui lance la commande, si le motif apparaît dans sa ligne de commande.
- Les identifiants de service des commandes START publiées ne sont **pas** des handles dynamiques : ce sont les valeurs de l'énumération ServiceType (14 = ACTIVITY, 16 = DEVMOTION6).
- Les AirPods 3 basculent facilement vers un autre appareil, ou se déconnectent quand on les range : penser à `bluetoothctl connect`.

## Sources
- LibrePods : [docs/AAP Definitions.md](https://github.com/librepods-org/librepods/blob/main/docs/AAP%20Definitions.md), PR #176 (paquets alternatifs), PR #887 (flux 14→16, fréquence, structure des 58 o)
- [pabloaul/apple-wireshark](https://github.com/pabloaul/apple-wireshark) (rtbuddy.proto, rtbuddy.lua, PR #4 capture Pro 3)
- [Gheop/mutix](https://github.com/Gheop/mutix), [arctumn/ntpods](https://github.com/arctumn/ntpods) (MTU et publication des services)
- [ShiratsuYudachi/airpods-head-track](https://github.com/ShiratsuYudachi/airpods-head-track), [FIocker/aap-head-tracker](https://github.com/FIocker/aap-head-tracker), [batubozkan/pods-head-tracker](https://github.com/batubozkan/pods-head-tracker)
- Apple : [support.apple.com/102469](https://support.apple.com/en-us/102469) (les AirPods 3 gèrent le head tracking dynamique)
