# Deye & BESEN Vezérlő – Architektúra és Kódstruktúra Leírás

Ez a dokumentum a `deye_besen_controller.py` szoftver belső felépítését, szálkezelési modelljét, adatfolyamát és a BESEN Bluetooth Low Energy (BLE) protokoll működését részletezi fejlesztők számára.

---

## 1. Rendszerarchitektúra és Szálkezelés

A program egyetlen fájlból áll, amely egyszerre látja el a háttérben futó hardveres lekérdezéseket, a biztonsági logikai döntéshozatalt és a webes kezelőfelület (Dashboard) kiszolgálását.

A rendszer **aszinkron eseményhurokra (Python `asyncio`)** épül, amely mellett a webes felület kiszolgálása egy külön háttérszálon fut a hálózati kérések blokkolásmentes kiszolgálása érdekében.

```
+------------------------------------------------------------+
|                  Háttérszál (HTTP Thread)                 |
|                                                            |
|  [ThreadingHTTPServer] --> Kiszolgálja --> [Web Dashboard] |
|           |                                                |
|           +----------> Módosítja ------> [config.json]     |
+------------------------------------------------------------+
                               |
                        Frissíti / Olvassa
                               |
                        [ shared_state ]
                     (Védve: state_lock mutex)
                               |
                        Frissíti / Olvassa
                               v
+------------------------------------------------------------+
|             Aszinkron Fő Hurok (asyncio Loop)              |
|                                                            |
|  [run_inverter_polling]   --> Modbus/TCP --> [Deye]        |
|  [run_charge_controller]  --> Szabályok  --> [Döntések]     |
|                                                    |       |
|                                         Sorba rak  |       |
|                                                    v       |
|  [run_ble_client]  <-- [ble_command_queue] <-------+       |
|         |                                                  |
|         +------------------> BLE parancs --> [BESEN EVSE]  |
+------------------------------------------------------------+
```

### A) Szálbiztos Állapotkezelés (`shared_state`)
Mivel a Web Server (HTTP szál) és az `asyncio` eseményhurok (főszál) párhuzamosan futnak, az adatok megosztása egy közös Python szótáron (`shared_state`) keresztül történik. A konkurens írás/olvasás ütközések elkerülése érdekében minden hozzáférés a `state_lock = threading.Lock()` kölcsönös kizárással (mutex) van védve.

---

## 2. Aszinkron Háttérfeladatok (asyncio Tasks)

Az alkalmazás indításakor a `main()` függvény az alábbi aszinkron feladatokat indítja el párhuzamosan:

### 1. `run_inverter_polling()`
* **Feladat:** Kapcsolódás a Deye inverter Wi-Fi stickjéhez (Solarman LSW-3 egység) TCP-n keresztül, és a telemetriai adatok lekérdezése 10 másodpercenként. A kapcsolat mostantól állandó (globális _persistent_inverter használatával), csak akkor bontódik le és nullázódik, ha hálózati vagy Modbus hiba történik, így elkerülve a socket memory leaket.
* **Használt könyvtár:** `pysolarmanv5` (Modbus RTU over TCP a `8899`-es porton).
* **Szálkezelési biztonság (Aszinkronizáció):** Mivel a `pysolarmanv5` lekérdezései szinkron (blocking) hálózati műveletek, a hurokban ezeket a `fetch_inverter_data_blocking()` segédfüggvénybe szervezve, az `asyncio.to_thread()` segítségével egy külön háttérszálon hajtjuk végre. Ezzel megakadályozzuk, hogy az inverter esetleges hálózati kiesése vagy lassúsága blokkolja a fő programszálat és megszakítsa a Bluetooth kapcsolatot.
* **Kiolvasott regiszterek:**
  * **Regiszter 607 (Signed 16-bit):** Inverter belső hálózati teljesítmény (Grid port).
  * **Regiszter 619 (Signed 16-bit):** Külső hálózati teljesítmény (a villanyóra melletti mérő CT-től).
  * **Regiszter 643 (Unsigned 16-bit):** Inverter UPS (Backup) kimeneti terhelése. Ez a ház teljes pillanatnyi fogyasztása.
  * **Regiszterek 672-673 (Unsigned 16-bit, összeadva):** Napelemes pillanatnyi termelés (PV) power (PV1 & PV2 Power in Watts).
  * **Regiszter 590 (Signed 16-bit):** Háztartási akkumulátor pillanatnyi teljesítménye (+ = töltés, - = kisütés).
  * **Regiszter 588 (Unsigned 16-bit):** Háztartási akkumulátor töltöttségi szintje (SoC %).
* **Számított Nem UPS fogyasztás:** `charger_power = max(0, grid_power_external - grid_power_internal)`. Ez mutatja meg a nem UPS ágon lévő összes külső fogyasztó (autótöltő és mérőcsoport) összteljesítményét.

### 2. `run_ble_client()`
* **Feladat:** A BESEN BS20 autótöltő Bluetooth BLE kapcsolatának kezelése, a küldési sorban (`ble_command_queue`) lévő parancsok kiküldése és a beérkező adatok fogadása.
* **Használt könyvtár:** `bleak`.
* **Időkorlátok és Biztonsági Wrapperek:** A Bluetooth írási és feliratkozási műveletek nem tartalmaznak beépített időkorlát-kezelést a Bleak-ben. Ennek kiküszöbölésére a parancsokat és feliratkozásokat a `safe_ble_write()` és `safe_ble_start_notify()` wrappers függvényeken keresztül hajtjuk végre, amelyek 5 másodperces `asyncio.wait_for` időkorlátot alkalmaznak. Sikertelen vagy túlnyúló művelet esetén a kapcsolat automatikusan bontásra kerül a tiszta újracsatlakozás érdekében.
* **Bluetooth Kapcsolódási Időkorlát:** A `BleakClient` kapcsolatfelépítés Windows alatt hajlamos lehet végtelenül leblokkolni. Ennek elkerülésére a csatlakozást egy explicit `asyncio.wait_for(client.connect(), timeout=20.0)` hívásba zártuk, amely 20 másodperc után megszakítja az akadozó kapcsolódási kísérletet és újracsatlakozási ciklust indít.
* **Telemetria Watchdog (Kapcsolatfigyelő):** A kliens folyamatosan frissíti a `last_rx_time` globális változót minden beérkező telemetria csomagnál. Ha a kapcsolat státusza `LOGGED_IN`, de 15 másodperce nem érkezett adatcsomag a töltőtől, a watchdog időtúllépést naplóz, megszakítja a kapcsolatot (`client.disconnect()`), és kezdeményezi a tiszta újracsatlakozási folyamatot.
* **Szálbiztos Callback Feldolgozás (main_loop):** Mivel a Bleak a telemetria callbacket (`ble_notification_received()`) egy háttérszálon (WinRT event thread) keresztül hívja meg, az aszinkron feladatok közvetlen ütemezése (`asyncio.create_task()`) hibát dobna az eseményhurok hiánya miatt. Ennek javítására a program indításakor a főszálon elmentjük az eseményhurkot a globális `main_loop` változóba, a callbackben pedig az `asyncio.run_coroutine_threadsafe(..., main_loop)` segítségével szálbiztosan ütemezzük a csomagok feldolgozását.
* **Szálbiztos BLE Parancssor Ürítés (`clear_ble_command_queue`):** A Bluetooth kapcsolat lecsatlakozásakor vagy újracsatlakozásakor az offline időszak alatt felhalmozódott parancsok felgyülemlésének és visszatéréskor való hirtelen kiküldésének megelőzésére a rendszer a `clear_ble_command_queue()` hívással szálbiztosan kiüríti a `ble_command_queue` sorban maradt parancsokat.
* **Kulcsfontosságú UUID-k:**
  * `FFE4` (Notify): Itt küldi a töltő folyamatosan a telemetriát (feszültségek, áramok, belső hőmérséklet, állapot).
  * `FFF3` (Write): Ide írjuk a vezérlőparancsokat (Indítás, Leállítás, Áramerősség állítás).
  * `FFC2` (Notify): A bejelentkezési PIN / jelszó visszaigazolásának csatornája.
  * `FFC1` (Write): Ide írjuk be a hitelesítési (Login) jelszót.

### 3. `run_charge_controller()`
* **Feladat:** A vezérlő fő döntési ciklusa 5 másodpercenként fut le.
* **Működés:** Beolvassa a `shared_state`-ből az inverter és a töltő pillanatnyi állapotát, kiértékeli az aktív üzemmódot (Auto, Scheduled, Force), majd ha szükséges, parancs-csomagot helyez el a `ble_command_queue` sorba.
* **Egységesített Solar Auto szabályok:** A döntési körben a napelemes szabályozás egyetlen strukturált és egységes logikai blokkban fut le. A töltés indítása az akkumulátor indítási küszöbe alapján történik (`battery_soc >= start_soc`), a töltés leállítását pedig az alábbi 3 védelmi szabály kiértékelése vezérli sorrendben és függetlenül egymástól:
  1. *Ház túlterhelés-védelem:* Ha az UPS terhelés meghaladja a `house_power_limit_w` korlátot, a töltés azonnal leáll.
  2. *Akkumulátor lemerülés-védelem:* Ha a `stop_soc` > 0 és a házi akkumulátor töltöttsége a `stop_soc` limit alá esik, a töltés azonnal leáll.
  3. *Hálózati import limit:* Ha a hálózati import meghaladja a `stop_import_limit` értéket, elindul egy késleltetés, és ha ez eléri a `grid_charge_duration_minutes` percet, a töltés leáll.
* **Grid charge delayed shutdown:** A "Hálózati töltés késleltetett leállítása (perc)" beállítás 0 értékre állításakor azonnali leállást jelent, nem pedig letiltja a vizsgálatot. A vizsgálat akkor aktív, ha a hálózati energia küszöbe (`grid_power_threshold`) nagyobb mint 0.
* **HTML input lépéskövek:** A Watt paraméterek HTML beviteli mezőjének lépéskövei `step=1` értékre állnak, így lehetőség van egyszeres Watt felbontású beállításokra (pl. 80 W).
* **Kézi Leállítások Feldolgozása (Soft Stop / Restart):** A manuális override flageket (`apply_with_stop`, `apply_with_restart`) a hurok legelső pontján dolgozza fel a rendszer, még az üzemmód kiértékelése és a `monitoring` mód miatti esetleges korai visszaugrás (`continue`) előtt. Ezzel biztosítjuk, hogy az Ideiglenes Leállítás (Soft Stop) gomb megnyomásakor a stop parancs azonnal kiküldésre kerüljön, még akkor is, ha nincs aktív szabályozó automatizmus a háttérben.
* **Kézi indítási versenyhelyzet-kezelés:** A kézi indítási folyamat BLE kiküldését védő `manual_start_requested` állapotjelző beépítésével megakadályoztuk, hogy a hurok elején lévő alapállapot-visszaállító logika idő előtt leállítsa a kézi indítás (`manual_start`) üzemmódot a parancs elküldése előtt.

---

## 3. A BESEN BLE Kommunikációs Protokoll és Kézfogás

A BESEN BS20 egy egyedi, zárt bináris keret-protokollal kommunikál BLE-n keresztül. A sikeres kapcsolat felépítéséhez a következő bejelentkezési folyamatnak kell lefutnia:

**Shanghai időzónás időbélyeg:**
A BESEN EVSE MCU-ja ellenőrzi a kapott Unix időbélyeget a START parancsban. Ha az eltérés túl nagy (pl. Budapest vs. Shanghai), elutasítja a csomagot. Ennek javítására bevezettük a `get_shanghai_timestamp()` függvényt, ami a helyi időt +8 órás eltolással (Shanghai időzóna szerint) Unix timestamp-pé alakítja. Ezzel a módosított időbélyeggel küldjük ki a START parancsokat a `ts = int(time.time())` helyett.

### A) Bejelentkezési folyamat (Login Handshake)

```
  Vezérlő Szoftver (Bleak)                           BESEN BS20 Töltő
          |                                                  |
          | <--------------- Bluetooth Kapcsolat ------------>|
          |                                                  |
          | <--- Notify FFE4 (0x0002 Identity Request) ------|
   [INIT] |                                                  |
          | ---- Write FFF3 (0x0002 Identity ACK) ---------->|
[SENT_ACK]| (Válaszként az egyedi szériaszámmal)             |
          |                                                  |
          | <--- Notify FFE4 (0x0002 Identity Success) ------|
 [ACKED]  |                                                  |
          | ---- Write FFC1 (0x0001 Login Request) --------->|
          |      (6-bájtos titkosított PIN-kód)              |
[SENT_LGN]|                                                  |
          | <--- Notify FFC2 (0x00 Auth Success) ------------|
[LOGGED]  |                                                  |
          v                                                  v
   (Kapcsolat elfogadva: Teljes kétirányú adatfolyam és parancsküldési jog)
```

### B) Bináris Csomagszerkezet (Frame format)
Minden küldött és fogadott csomag egy rögzített struktúrájú bináris tömb:

**Dinamikus vonalazonosító (Fázisdetektálás):**
A parancsok fejlécében lévő első bájt a vonalazonosító (`line_id`), ami 1-fázisú töltésnél `0x01`, 3-fázisú töltésnél `0x02` kell legyen. Ezt a rendszer dinamikusan határozza meg az invertertől mért L2 és L3 fázisfeszültségek alapján. Ha a feszültségek alapján aktív 3 fázis észlelhető (>50V), a `line_id` értéke `2` (`0x02`), ellenkező esetben `1` (`0x01`).

**START csomag payload felépítése:**
Az autótöltő indítási (START, `0x8007`) parancsának hasznos terhe (`payload = packet[21:]`) az alábbi struktúrát követi:
*   `payload[0]`: Fázisszám azonosító (`line_id`).
*   `payload[1:17]`: Felhasználónév (biztonsági okokból `"BDmanager"`, ASCII-vel kódolva, 0x00 bájtokkal feltöltve).
*   `payload[17:33]`: Dinamikus töltés-azonosító (session ID, formátuma: `YYYYMMDDHHMM1337`).
*   `payload[33]`: Alapértelmezett indítási mód (`0x00`).
*   `payload[34:38]`: Shanghai időzónás Unix időbélyeg (egész számként, Big-Endian formátumban).
*   `payload[38]`: Automatikus indítás jelző (`0x01`).
*   `payload[39]`: Online mód jelző (`0x01`).
*   `payload[40:46]`: Korlátokat feloldó alapértelmezett vezérlőbájtok (`0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF`).
*   `payload[46]`: Maximális áramerősség limit (`charger_max_amps`).

| Bájt eltolás (Offset) | Hossz | Leírás | Érték / Példa |
|---|---|---|---|
| **0 - 1** | 2 bájt | Fejléc (Header) | `0x06, 0x01` |
| **2 - 3** | 2 bájt | Teljes csomaghossz (Big-Endian) | Pl. `0x00, 0x2F` (47 bájt) |
| **4** | 1 bájt | Kulcs típus (Key Type) | `0x00` |
| **5 - 12** | 8 bájt | Töltő egyedi szériaszáma | Pl. `0x30, 0x99, 0x83...` |
| **13 - 18** | 6 bájt | Töltő jelszava (PIN kód) | Pl. `0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF` (ASCII hexadecimálisan) |
| **19 - 20** | 2 bájt | Parancskód (Command ID) | Pl. `0x80, 0x07` (Start), `0x80, 0x08` (Stop) |
| **21 - N** | Változó | Hasznos teher (Payload) | Paraméterek, állapot adatok |
| **N+1 - N+2** | 2 bájt | Ellenőrzőösszeg (CRC16/Modbus) | A 0. bájttól a hasznos teher végéig számítva |
| **N+3 - N+4** | 2 bájt | Záró bájtok (Tail) | `0x0F, 0x02` |

### C) Telemetria csomagok (0x0004 és 0x000D) hasznos terhének felépítése (Payload Structure)
A telemetria és állapot jelentések hasznos terhe (`payload = packet[21:]`) tartalmazza az élő méréseket:
*   `payload[1:3]`: L1 feszültség (szorzó: 0.1, V)

**Töltési rekord feldolgozása (`0x000A` csomag):**
A töltő a töltési folyamatok végén (és újracsatlakozáskor) küldi a `0x000A` (decimal 10) parancskódú csomagot, amely a töltési előzményeket (munkamenet naplót) tartalmazza.
A hasznos teher (`payload = packet[21:]`) pontos bájtszerkezete a következő:
*   `payload[0]`: Fázisszám/vonalazonosító (`line_id`).
*   `payload[1:17]`: Indító felhasználó RFID kártya azonosítója (ASCII string, pl. `"62316176FDFFCBD8"`).
*   `payload[17:33]`: Leállítási ok / leállító felhasználó (ASCII string, pl. `"Pull Plug"`).
*   `payload[33:49]`: Dátum alapú session ID (ASCII string, pl. `"2026061017388996"`).
*   `payload[64:68]`: Kezdő Unix időbélyeg (Big-Endian uint32).
*   `payload[68:72]`: Befejező Unix időbélyeg (Big-Endian uint32).
*   `payload[72:76]`: Töltési időtartam másodpercben (Big-Endian uint32).
*   `payload[76:80]`: Kezdő mérőóra állás 10 Wh egységben (Big-Endian uint32).
*   `payload[80:84]`: Befejező mérőóra állás 10 Wh egységben (Big-Endian uint32).
*   `payload[84:88]`: Munkamenet (Session) energia 10 Wh egységben (Big-Endian uint32). *Megjegyzés: A szoftver ezt megszorozza 10-zel a pontos Wh érték kalkulációjához.*

**Szelektív naplózás és Háttérbeli lezárás**:
A töltő újracsatlakozáskor automatikusan elküldi az összes korábbi le nem zárt rekordot sorban egymás után (akár idegen mobilappos indításokét is), a szoftver egy intelligens szelektív logolási és háttértörlési folyamatot alkalmaz:
1. Az indítási folyamatoknál (Kézi, Ütemezett, Solar Auto) a generált `charge_id`-t elmentjük a `_last_initiated_session_id` globális változóba és a `config.json` fájlba.
2. `0x000A` csomag vételekor összevetjük a `session_id`-t a `_last_initiated_session_id` értékével.
3. Ha egyezik: Elmentjük a rekordot a `shared_state["last_charge"]` alá, és kiírjuk a `config.json` lemezre. Ez jelenik meg a Dashboard-on legutóbbi töltésként.
4. Ha nem egyezik (idegen app vagy régi beragadt rekord): A szoftver csendben kiküldi a lezárást (`0x800A` nyugta) a töltőnek, hogy törlődjön a memóriájából és ne ragadjon be, de **NEM** írja felül a saját szoftveres töltési naplónkat a felületen.
*   `payload[95:97]`: Teljesítménynapló bejegyzések száma (Big-Endian uint16).
*   `payload[97:]`: Idősoros teljesítmény-log adatsor (minden bejegyzés 2-bájtos Big-Endian egész szám W-ban, pl. `0x0FC8` = 4040 W).

A szoftverben a következő változásokat hajtottunk végre:
1. A Solar Auto szabályok (Hálózati import korlátja, Akkumulátor leállítási SoC-ja, Ház UPS túlzott terhelésvédelem) egymás után és függetlenül lesznek kiértékelve.
2. A "Hálózati töltés késleltetett leállítása (perc)" beállítás 0 perc értékére állításakor azonnali leállást jelent, nem pedig letiltja a vizsgálatot. A vizsgálat akkor aktív, ha a hálózati energia küszöbértéke nagyobb mint 0.
3. Az HTML beviteli lépésközei a Watt paraméterekhez 1-re lettek állítva, így lehet egy W-os felbontású beállítást megadni (pl. 80 W).

A csomag feldolgozása megelőzi a hamis leállásokat. Korábban ugyanis a kezeletlen csomag első bájtjait a rendszer tévesen töltési állapotváltozásként értelmezte (mivel az RFID kártyaszám első betűi nem egyeztek a várt státuszokkal), ami a töltés váratlan leállását okozta.
*   `payload[3:5]`: L1 áramerősség (szorzó: 0.01, A)
*   `payload[5:9]`: Pillanatnyi aktív teljesítmény (Big-Endian, W)
*   `payload[9:13]`: L1 töltési energia regiszter (Big-Endian, szorzó: 0.01, Wh). *Megjegyzés: 3-fázisú töltésnél a szoftver ezt felszorozza 3-mal a valós összesített értékhez.*
*   `payload[13:15]`: Belső hőmérséklet (szorzó: 0.01, offset: -200 °C)
*   `payload[18]`: Csatlakozó kábel állapota (Plug State)
*   `payload[19]`: Töltési kimenet állapota (Output State)
*   `payload[25:27]`: L2 feszültség (szorzó: 0.1, V)
*   `payload[27:29]`: L2 áramerősség (szorzó: 0.01, A)
*   `payload[29:31]`: L3 feszültség (szorzó: 0.1, V)
*   `payload[31:33]`: L3 áramerősség (szorzó: 0.01, A)

---

## 4. API Végpontok és a Kezelőfelület Kapcsolata

A webes felület egy beépített HTTP szerveren fut. A dinamikus adatszinkronizáció JSON API-n keresztül történik 2 másodperces frissítési ciklussal.

### Helyi hálózati hozzáférés-védelem (Autentikáció)
Ha a `"web_auth_enabled"` konfiguráció aktív, a kiszolgáló minden kérésnél ellenőrzi a HTTP `Cookie` fejlécben lévő `session` azonosítót.
* **Hitelesítetlen elérés**: Ha a kérés nem tartalmaz érvényes session tokent, a `/` elérésére a beépített bejelentkező felület (`LOGIN_HTML`) töltődik be, míg az API végpontok (pl. `/api/status`, `/api/config`) `401 Unauthorized` HTTP hibát adnak vissza `{"status": "unauthorized", "message": "Autentikáció szükséges!"}` JSON formátumban.

**Személyes adatok tisztítása (Privacy / Névcsere):**
Biztonsági és adatvédelmi okokból az összes korábbi "Attila" felhasználónév bejegyzés le lett cserélve `"BDmanager"`-re a hitelesítésben és a parancsokban (pl. `start_payload[1:17] = b"BDmanager".ljust(16, b"\\x00")`). Ez megegyezik a slespersen/evseMQTT projekt alapértelmezett beállításával.
* **Kivétel**: A `/background.png` lekérése hitelesítés nélkül is engedélyezett, hogy a bejelentkező felület háttere megfelelően megjelenhessen.

### Reszponzivitás és Mobil Navigáció (Kliensoldal)
A webes felület CSS media queryk segítségével teljesen reszponzív. A töréspont meg van emelve `1024px`-re, ami felett asztali (kétoszlopos), alatta pedig mobil (egykártyás) elrendezés jelenik meg.
*   **Mobil navigáció (`showSection`):** Mobilon a szekciók közötti navigációt a kliensoldali JavaScript végzi. A hamburger menüben történő kattintáskor a `showSection(sectionId)` függvény elrejti a többi fő konténert, és csak a kiválasztott szekció kártyáját helyezi el a DOM-ban (`display: flex` vagy `display: grid` módban), megelőzve az oldalszélesség túlnyúlását.

**Csúszka háttérszínezési javítás:**
A műszerfal betöltésekor a nagy áramerősség-csúszkák kék háttere alapértelmezetten 50%-on állt (félállásban ragadt), függetlenül a konfigurációban megadott valós áramerősségtől (pl. 6A), amíg a felhasználó bele nem kattintott. Ezt a JavaScript inicializációs sorrendjének javításával oldottuk meg: az `updateSliderBackground` hívást a konfigurációs értékek DOM-ba való betöltése után futtatjuk le, így a csúszka a tényleges konfigurált áramértéknek megfelelő kitöltéssel jelenik meg betöltéskor.
*   **Szolgáltatott fejlécek**: A gyorsítótárazási hibák elkerülése érdekében a `/` végpont lekérésekor a szerver explicit módon `no-cache`, `no-store` és `must-revalidate` fejléceket küld a kliens felé.
*   **Tooltip elrendezés és eseménykezelés:** Mobilon a tooltip-ek az info ikonok alá nyílnak meg lefelé (elkerülve a tapadós fejléc általi takarást), szélességük `220px`-re korlátozódik, és a jobb szélen lévő elemeknél balra felé terjeszkednek a képernyő-túlnyúlás megelőzésére. Globális kliensoldali eseménykezelés blokkolja a click események buborékolását a `.tooltip-container` elemeknél, megelőzve a szülő checkboxok véletlen átváltását.

### Végpontok
* **`GET /`**: Visszaadja a statikus Dashboard HTML kódot (`DASHBOARD_HTML`, ha hitelesített) vagy a bejelentkező lapot (`LOGIN_HTML`, ha nem hitelesített).
* **`GET /background.png`**: Kiszolgálja a háttérképet a futtatható program mappájából (támogatja a becsomagolt `.exe` környezetet is).
* **`GET /api/status`**: Lekéri a teljes `shared_state` adatstruktúrát JSON formátumban (csak hitelesítetten).
* **`POST /api/login`**: Nyilvános bejelentkezési végpont. Megkapja a `{"password": "..."}` JSON-t. Helyes jelszó esetén generál egy kriptográfiailag biztonságos session tokent, elmenti a memóriába, és visszaadja a `Set-Cookie: session=<token>; HttpOnly; Path=/; SameSite=Lax` fejlécet.
* **`POST /api/logout`**: Munkamenet lezárása. Törli a session tokent a memóriából, és érvényteleníti a sütit a kliensnél (`Max-Age=0`).
* **`POST /api/config`**: Mentésre küldi az új beállításokat (csak hitelesítetten). A beküldött adatokat a program ellenőrzi, lemezre írja a `config.json` fájlba, és azonnal érvényesíti a futó vezérlőhurokban.
* **`POST /api/mode`**: Átállítja a működési módot (monitoring / auto / schedule / force, csak hitelesítetten).
* **`POST /api/force_submode`**: Kézi vezérlés almódját állítja be (csak hitelesítetten).
* **`POST /api/set_current`**: Töltőáramot korlátozza kézzel (csak hitelesítetten).
* **`POST /api/sim_toggle` / `POST /api/sim_data`**: Szimulációs mód állapota és bemenetei (csak hitelesítetten).

---

## 5. Bővítési és Módosítási Útmutató

Ha szeretnéd a kódot saját igényeidre szabni vagy más eszközökhöz illeszteni, az alábbi helyeken kell módosítanod:

### Másik Inverter típus illesztése
Ha nem Deye hibrid invertered van (pl. Fronius, Huawei, Victron), akkor a `run_inverter_polling()` függvényt kell átírnod:
1. Cseréld le a `pysolarmanv5` hívást a saját invertered kommunikációs könyvtárára (pl. standard Modbus TCP, REST API vagy MQTT kliens).
2. Olvasd ki a saját invertered megfelelő értékeit (Grid, UPS/House load, PV, Battery SoC, Battery Power).
3. Írd be őket a megfelelő kulcsok alatt a `shared_state` objektumba a `state_lock` blokkon belül.

### Másik Autótöltő típus illesztése
Ha a BESEN helyett egy másik autótöltőt (pl. Go-e, Tesla Wall Connector, Shelly relé) szeretnél vezérelni:
1. A `run_ble_client()` aszinkron feladatot cseréld le az új töltő kommunikációs moduljára (pl. HTTP API hívások vagy helyi MQTT parancsok).
2. A `run_charge_controller()` döntési ciklus végén a `ble_command_queue.put(packet)` helyett közvetlenül az új töltőnek küldd el az indítási, leállítási vagy áramállítási parancsokat.
