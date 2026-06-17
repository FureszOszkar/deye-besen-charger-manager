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
* **Feladat:** Kapcsolódás a Deye inverter Wi-Fi stickjéhez (Solarman LSW-3 egység) TCP-n keresztül, és a telemetriai adatok lekérdezése 10 másodpercenként.
* **Használt könyvtár:** `pysolarmanv5` (Modbus RTU over TCP a `8899`-es porton).
* **Szálkezelési biztonság (Aszinkronizáció):** Mivel a `pysolarmanv5` lekérdezései szinkron (blocking) hálózati műveletek, a hurokban ezeket a `fetch_inverter_data_blocking()` segédfüggvénybe szervezve, az `asyncio.to_thread()` segítségével egy külön háttérszálon hajtjuk végre. Ezzel megakadályozzuk, hogy az inverter esetleges hálózati kiesése vagy lassúsága blokkolja a fő programszálat és megszakítsa a Bluetooth kapcsolatot.
* **Kiolvasott regiszterek:**
  * **Regiszter 607 (Signed 16-bit):** Inverter belső hálózati teljesítmény (Grid port).
  * **Regiszter 619 (Signed 16-bit):** Külső hálózati teljesítmény (a villanyóra melletti mérő CT-től).
  * **Regiszter 643 (Unsigned 16-bit):** Inverter UPS (Backup) kimeneti terhelése. Ez a ház teljes pillanatnyi fogyasztása.
  * **Regiszter 175 (Unsigned 16-bit):** Napelemes pillanatnyi termelés (PV).
  * **Regiszter 590 (Signed 16-bit):** Háztartási akkumulátor pillanatnyi teljesítménye (+ = töltés, - = kisütés).
  * **Regiszter 588 (Unsigned 16-bit):** Háztartási akkumulátor töltöttségi szintje (SoC %).
* **Számított Nem UPS fogyasztás:** `charger_power = max(0, grid_power_external - grid_power_internal)`. Ez mutatja meg a nem UPS ágon lévő összes külső fogyasztó (autótöltő és mérőcsoport) összteljesítményét.

### 2. `run_ble_client()`
* **Feladat:** A BESEN BS20 autótöltő Bluetooth BLE kapcsolatának kezelése, a küldési sorban (`ble_command_queue`) lévő parancsok kiküldése és a beérkező adatok fogadása.
* **Használt könyvtár:** `bleak`.
* **Időkorlátok és Biztonsági Wrapperek:** A Bluetooth írási és feliratkozási műveletek nem tartalmaznak beépített időkorlát-kezelést a Bleak-ben. Ennek kiküszöbölésére a parancsokat és feliratkozásokat a `safe_ble_write()` és `safe_ble_start_notify()` wrappers függvényeken keresztül hajtjuk végre, amelyek 5 másodperces `asyncio.wait_for` időkorlátot alkalmaznak. Sikertelen vagy túlnyúló művelet esetén a kapcsolat automatikusan bontásra kerül a tiszta újracsatlakozás érdekében.
* **Telemetria Watchdog (Kapcsolatfigyelő):** A kliens folyamatosan frissíti a `last_rx_time` globális változót minden beérkező telemetria csomagnál. Ha a kapcsolat státusza `LOGGED_IN`, de 15 másodperce nem érkezett adatcsomag a töltőtől, a watchdog időtúllépést naplóz, megszakítja a kapcsolatot (`client.disconnect()`), és kezdeményezi a tiszta újracsatlakozási folyamatot.
* **Kulcsfontosságú UUID-k:**
  * `FFE4` (Notify): Itt küldi a töltő folyamatosan a telemetriát (feszültségek, áramok, belső hőmérséklet, állapot).
  * `FFF3` (Write): Ide írjuk a vezérlőparancsokat (Indítás, Leállítás, Áramerősség állítás).
  * `FFC2` (Notify): A bejelentkezési PIN / jelszó visszaigazolásának csatornája.
  * `FFC1` (Write): Ide írjuk be a hitelesítési (Login) jelszót.

### 3. `run_charge_controller()`
* **Feladat:** A vezérlő fő döntési ciklusa (5 másodpercenként fut le).
* **Működés:** Beolvassa a `shared_state`-ből az inverter és a töltő pillanatnyi állapotát, kiértékeli az aktív üzemmódot (Auto, Scheduled, Force), majd ha szükséges, parancs-csomagot helyez el a `ble_command_queue` sorba.
* **Biztonsági ellenőrzések:** Itt fut a `house_power_limit_w` túlterhelés-védelem kiértékelése is. Ha az UPS terhelés meghaladja a megadott korlátot, a ciklus leállító parancsot ad ki a töltő felé.

---

## 3. A BESEN BLE Kommunikációs Protokoll és Kézfogás

A BESEN BS20 egy egyedi, zárt bináris keret-protokollal kommunikál BLE-n keresztül. A sikeres kapcsolat felépítéséhez a következő bejelentkezési folyamatnak kell lefutnia:

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

---

## 4. API Végpontok és a Kezelőfelület Kapcsolata

A webes felület egy beépített HTTP szerveren fut. A dinamikus adatszinkronizáció JSON API-n keresztül történik 2 másodperces frissítési ciklussal.

### Helyi hálózati hozzáférés-védelem (Autentikáció)
Ha a `"web_auth_enabled"` konfiguráció aktív, a kiszolgáló minden kérésnél ellenőrzi a HTTP `Cookie` fejlécben lévő `session` azonosítót.
* **Hitelesítetlen elérés**: Ha a kérés nem tartalmaz érvényes session tokent, a `/` elérésére a beépített bejelentkező felület (`LOGIN_HTML`) töltődik be, míg az API végpontok (pl. `/api/status`, `/api/config`) `401 Unauthorized` HTTP hibát adnak vissza `{"status": "unauthorized", "message": "Autentikáció szükséges!"}` JSON formátumban.
* **Kivétel**: A `/background.png` lekérése hitelesítés nélkül is engedélyezett, hogy a bejelentkező felület háttere megfelelően megjelenhessen.

### Reszponzivitás és Mobil Navigáció (Kliensoldal)
A webes felület CSS media queryk segítségével teljesen reszponzív. A töréspont meg van emelve `1024px`-re, ami felett asztali (kétoszlopos), alatta pedig mobil (egykártyás) elrendezés jelenik meg.
*   **Mobil navigáció (`showSection`):** Mobilon a szekciók közötti navigációt a kliensoldali JavaScript végzi. A hamburger menüben történő kattintáskor a `showSection(sectionId)` függvény elrejti a többi fő konténert, és csak a kiválasztott szekció kártyáját helyezi el a DOM-ban (`display: flex` vagy `display: grid` módban), megelőzve az oldalszélesség túlnyúlását.
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
