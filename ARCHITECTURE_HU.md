# Deye & BESEN Vezérlő – Architektúra és Kódstruktúra Dokumentáció

Ez a dokumentum részletezi a `main.py` (és moduljai) belső tervezését, a szálkezelési modellt (threading), az adatáramlást és a BESEN Bluetooth Low Energy (BLE) protokoll megvalósítását fejlesztők számára.

---

## 1. Rendszer Architektúra és Szálkezelési (Threading) Modell

Az alkalmazás egy több-modulos Python struktúrára épül, amely egyidejűleg kezeli a hardver lekérdezését, a háttérben futó biztonsági logikákat, valamint a HTTP műszerfal szervert.

Az alapvető ciklusok egy **aszinkron eseményhurokban (Python `asyncio`)** futnak, míg a Webes Műszerfal és az API egy külön háttérszálon (background thread) fut, így garantálva a blokkolásmentes feldolgozást.

```
+------------------------------------------------------------+
|                  Háttér HTTP Szál (Thread)                 |
|                                                            |
|  [ThreadingHTTPServer] --> Kiszolgál --> [Web Dashboard]   |
|           |                                                |
|           +----------> Frissíti -------> [config.json]     |
+------------------------------------------------------------+
                               |
                      Olvasás / Frissítés
                               |
                        [ shared_state ]
                    (Lock: state_lock mutex)
                               |
                      Olvasás / Frissítés
                               v
+------------------------------------------------------------+
|               Aszinkron asyncio Fő Ciklus                  |
|                                                            |
| +-----------------+   +------------------+   +-----------+ |
| |  Deye Modbus    |   |  Töltési Logika  |   | BESEN BLE | |
| |  Lekérdező Task |   |  Ellenőrző Task  |   | BLE Task  | |
| +-----------------+   +------------------+   +-----------+ |
|        |                       |                   |       |
|  (Adatok a             (Parancsok a            (Végre-     |
|  shared_state-be)   ble_command_queue-ba)      hajtás)     |
+------------------------------------------------------------+
```

### 1.1 A Megosztott Állapot (Shared State) Tervezése
A `shared_state` dictionary egyetlen igazságforrásként (single source of truth) szolgál mind a Deye lekérdezések, mind a webes felület, mind a töltési logika felé.
*   **Szálbiztonság (Thread-Safety):** Mivel a HTTP szál beállításokat írhat (pl. "Napelemes módról Állandó módra váltás"), az eseményhurok pedig olvassa és frissíti a szenzoradatokat, az összes `shared_state` hozzáférést a `state_lock` (egy `threading.Lock`) védi a versenyhelyzetek (race conditions) elkerülése érdekében.

---

## 2. A Három Fő Aszinkron Folyamat (Tasks)

Az `asyncio` futtatókörnyezetben három párhuzamos taszk fut végtelen ciklusban:

### 2.1 `poll_deye_inverter()`
*   **Gyakoriság:** Körülbelül ~5 másodpercenként fut.
*   **Feladat:** TCP-n keresztül (IP / Port 8899) Modbus RTU kereteket küld a Solarman Logger-nek (LSW-3).
*   **Adatok:** Kiolvassa az akkumulátor SoC-t (State of Charge), feszültséget, áramerősséget, hálózati (Grid) teljesítményt (Import/Export) és a Ház (UPS) fogyasztását. Az adatokat visszírja a `shared_state`-be.

### 2.2 `run_charge_controller()`
*   **Gyakoriság:** 10 másodpercenként fut.
*   **Feladat:** Ez az alkalmazás **"Agya"**.
*   **Feltételek (Auto Módban):**
    1.  Ellenőrzi a biztonsági korlátokat: Túlterhelés védelem (`ups_load_power + charger_power > house_power_limit_w`) és Akku korlát (`battery_soc < stop_soc`). Ha bármelyik sérül, azonnali STOP parancsot küld a BLE sornak.
    2.  Ellenőrzi az indítási feltételeket: Ha az akkumulátor elérte az indulási szintet (`start_soc`), és még nem töltünk, START parancsot küld.
    3.  Statikus Áramkorlát: Nincs automatikus fel-le szabályozás (Load Balancing). Ha épp töltünk, a szoftver a beállított fix maximum áramerősséget használja, és a biztonsági szabályok alapján csak lekapcsolja (Stop), ha a feltételek sérülnek.
*   **Kimenet:** BLE parancscsomagokat (bytearray) tesz az `asyncio.Queue`-ba (`ble_command_queue`).
*   **Fázisszám (`line_id`) meghatározásának helye:** A dinamikus fázisszám-számítás (lásd 3.2) minden ciklusban a legelső lépések között fut le, még a kézi felülbírálási flag-ek (`apply_with_stop`, `apply_with_restart`) feldolgozása előtt. Ez azért fontos, mert ezek a flag-ek egy korai `continue`-t is kiválthatnak, ami már felhasználja a `line_id`-t egy BLE STOP-csomag összeállításához — ha a számítás később történne, `NameError`-t dobna.

### 2.3 `besen_ble_worker()`
*   **Gyakoriság:** Fogyasztja a `ble_command_queue`-t.
*   **Feladat:** Kapcsolódik a BESEN EVSE-hez a Windows BLE API-ján (a `bleak` könyvtáron) keresztül.
*   **Jellemző:** Úgy van megírva, hogy automatikusan próbálja újra a kapcsolódást hiba esetén. Miután elküld egy parancsot, megvárja, míg a töltő feldolgozza (ACK-ot vagy Notification-t küld).

---

## 3. BESEN Bluetooth Low Energy (BLE) Protokoll

A BESEN applikáció protokollja részben vissza lett fejtve Android Bluetooth hálózati szippantás (Snoop) alapján.

### 3.1 BLE Szolgáltatások (Services) és Karakterisztikák (Characteristics)
*   **Szolgáltatás (Service) UUID:** Szabványos vagy gyártó-specifikus UUID (pl. UART Tx/Rx, 0xFFE0 / 0xFFE1). A kódban a Bleak egy felfedező szkripttel deríti ki. A feltételezett Service UUID: `0000ffe0-0000-1000-8000-00805f9b34fb`, Write/Read Karakterisztika: `0000ffe1-0000-1000-8000-00805f9b34fb`.

### 3.2 A Nyers Adatcsomag Felépítése (Raw Payload Structure)
Minden csomag fixen **47 bájt** hosszú, kis endian (Little Endian) vagy egyedi csomagolásban.

| Bájt Pozíció | Hossz | Leírás                                                       | Példa/Megjegyzés                                |
| :---         | :---  | :---                                                         | :---                                            |
| 0            | 1     | **Fázis azonosító (Line ID)**                                | `0x01` (1-fázis) vagy `0x02` (3-fázis)          |
| 1 - 16       | 16    | **Alkalmazás/Felhasználó Neve (Padded)**                     | ASCII `"BDmanager"` + Null bájtokkal kitöltve   |
| 17 - 32      | 16    | **Munkamenet ID (Session ID) / Jelszó**                      | ASCII ID (pl. `"2024062022001337"`) vagy padding|
| 33           | 1     | Ismeretlen (Padding)                                         | `0x00`                                          |
| 34 - 37      | 4     | **Unix Időbélyeg (Timestamp)** (Nagy Endian)                 | Sanghaj időzónára korrigálva (`get_shanghai_timestamp()`) |
| 38           | 1     | **Parancs Típus 1 (Command Type 1)**                         | Indításnál `0x01`                               |
| 39           | 1     | **Parancs Típus 2 (Command Type 2)**                         | Indításnál `0x01`                               |
| 40 - 45      | 6     | **MAC Cím (Reverse) vagy Padding**                           | `[0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]`          |
| 46           | 1     | **Áramerősség Limit (Amper)**                                | Az 1-32A közötti érték indításkor, 0x00 leállításkor |

### 3.3 A Burkoló Csomag (Wrapper Packet)
A fenti 47 bájtos nyers payload (adat) rá van csomagolva egy belső keretre, amelyet a `create_ble_packet(command_code, payload)` függvény állít elő.

*   `Fejléc (Header)`: 2 bájt (Ismeretlen fix, de valószínűleg keret kezdet)
*   `Parancs Kód (Command Code)`: 2 bájt (Pl. `0x8007` a START-hoz, `0x8008` a STOP-hoz)
*   `Hossz (Length)`: A payload hossza
*   `Adat (Payload)`: A 47 bájtos nyers adat
*   `Ellenőrzőösszeg (Checksum / CRC)`: Egyszerű XOR vagy összeg alapú CRC a védelemhez.

A START és STOP parancsok kódjai:
*   **START Parancs:** Command Code: `0x8007` (Az utolsó bájtban az Amper korláttal)
*   **STOP Parancs:** Command Code: `0x8008`

---

## 4. API Végpontok és Műszerfal Interakció

A beépített HTTP szerver kiszolgálja a statikus webes műszerfalat, és JSON API-kat biztosít az élő szinkronizációhoz (a kliens 2 másodpercenként frissíti).

### Helyi Hálózati Hozzáférés Védelme (Hitelesítés)

Ha a konfigurációban a `"web_auth_enabled"` aktív, a szerver minden bejövő kérés `Cookie` fejlécében validálja a `session` tokent.

*   **Hitelesítés nélkül:** Ha a kérés nem tartalmaz érvényes session tokent, a `/` végpont a glassmorfikus bejelentkezési felületet (`LOGIN_HTML`) adja vissza, míg az API végpontok (pl. `/api/status`, `/api/config`) `401 Unauthorized` HTTP hibát küldenek `{"status": "unauthorized", "message": "Autentikáció szükséges!"}` JSON tartalommal.
*   Minden állapotmódosító POST végpont (köztük az `/api/unlock`) érvényes session-t igényel; kizárólag az `/api/login` és a csak olvasható `/api/login_info` érhető el bejelentkezés nélkül.

**Adatvédelem / Névváltás:**
Biztonsági és adatvédelmi okokból az összes korábbi "Attila" felhasználónév "BDmanager"-re cserélve a hitelesítési folyamatban és a parancsokban (pl. `start_payload[1:17] = b"BDmanager".ljust(16, b"\x00")`). Ez megfelel a slespersen/evseMQTT projekt alapértelmezett beállításának.

*   **Kivétel:** A `/background.png` bejelentkezés nélkül lekérhető, hogy a bejelentkezési képernyő háttere be tudjon tölteni.

### Végpontok közötti API Titkosítás (E2EE)

A webes Műszerfal kliens oldali JavaScript kódja és a Python HTTP szerver közötti forgalom hálózati lehallgatás (sniffing) elleni védelme beépített, katonai szintű kriptográfiát használ:
1.  **Jelszó Ellenőrzés:** A jelszó (plaintext) soha nem kerül elküldésre a hálózaton. A böngésző egy HMAC-SHA256 alapú `auth_proof`-ot küld (amely a szerver által generált `client_nonce` alapján készül, a 100 000 iterációs PBKDF2-SHA256 kulccsal).
2.  **Transzparens Payload Titkosítás:** A sikeres bejelentkezés után a kliens oldalon felülírt `fetch()` API automatikusan titkosít minden HTTP POST body-t. Korábban a böngésző natív WebCrypto API-ját használtuk (AES-GCM), de a mobil böngészők (pl. Chrome, Vivaldi) HTTP-n történő tiltása miatt a kliens oldal teljesen függetlenített CryptoJS alapokra állt át. A payload így AES-256-CBC módban (PKCS7 paddinggal) titkosítódik, amelyhez utólagos HMAC-SHA256 (Encrypt-then-MAC) ellenőrzőösszeg csatlakozik a manipulációk elkerülésére. A szerver a `pycryptodome` csomaggal fejti vissza a kéréseket, a válaszok JSON objektumai pedig ugyanígy titkosítva és dedikált MAC aláírással érkeznek vissza a klienshez.
3.  **Session lejárat:** Minden `active_sessions`-ben tárolt bejelentkezési token mostantól szerveroldali lejárati időbélyeget is kap (`SESSION_TTL_SECONDS`, alapértelmezetten 24 óra). A lejárt tokent a rendszer a következő hozzáférésnél automatikusan érvénytelennek tekinti és törli, így egy ellopott vagy elfelejtett süti nem marad örökre érvényes.
4.  **`GET /api/login_info`:** Autentikáció nélkül elérhető, nem titkos végpont, ami visszaadja a szerver aktuális `pbkdf2_iterations` értékét. Ezt a webes login-oldal mellett az Android widget is használja, hogy a kulcsszármaztatáshoz mindig a szerverrel megegyező iterációszámot alkalmazza, akkor is, ha az a `config.json`-ban az alapértelmezettől eltérő értékre lett állítva (lásd README, 5. szakasz).

### Reszponzív és Mobil Navigáció (Kliens Oldal)

A webes műszerfal reszponzív CSS elrendezést használ, 1024 pixeles törésponttal. E felett asztali, kéthasábos elrendezés jelenik meg; alatta egykártyás mobil nézet.

*   **Mobil Nézet Kezelő (`showSection`):** A mobil szekció-váltást kizárólag kliens oldali JavaScript kezeli. A mobilos lebegő menüben egy elemre koppintva a `showSection(sectionId)` meghívódik, amely elrejti a többi főkonténer-kártyát és csak az aktív konténert jeleníti meg teljes szélességben.

### Végpontok

*   **`GET /`**: A műszerfal HTML-jét adja vissza (hitelesítve: `DASHBOARD_HTML`, hitelesítés nélkül: `LOGIN_HTML`).
*   **`GET /background.png`**: A háttérképet adja vissza a futtatható könyvtárból (kezeli a PyInstaller ideiglenes könyvtár-környezeteket).
*   **`GET /api/status`**: A `shared_state` szótárat adja vissza JSON formátumban (hitelesítés szükséges).
*   **`GET /api/login_info`**: Nyilvános, hitelesítés nélkül elérhető végpont, visszaadja a `{"pbkdf2_iterations": <int>}` értéket. Lehetővé teszi, hogy bármely kliens (webes bejelentkezési oldal, Android widget) a szerver *aktuális* iterációszámával származtassa a session kulcsot a hardkódolt alapértelmezés helyett.
*   **`POST /api/login`**: Nyilvános bejelentkezési végpont. `{"clientNonce": "...", "authProof": "..."}` PSK kihívás-válasz csomagot fogad. Siker esetén kriptográfiailag biztonságos session tokent generál 24 órás lejárattal, elmenti a memóriában, és `Set-Cookie: session=<token>; HttpOnly; Path=/; SameSite=Lax` fejléccel adja vissza.
*   **`POST /api/logout`**: Lezárja az aktív session-t. Törli a tokent a memóriából és lejárttá teszi a sütit (`Max-Age=0`).
*   **`POST /api/unlock`**: Törli a Lockdown / Cooldown biztonsági állapotot (hitelesítés szükséges).
*   **`POST /api/config`**: Konfigurációs frissítéseket fogad (hitelesítés szükséges). Validálja, elmenti `config.json`-ba és azonnal frissíti a futó vezérlőhurokat. A `forced_schedule` mező szigorúan validálódik szerver oldalon (lásd 6. szakasz) az elfogadás előtt.
*   **`POST /api/mode`**: Módosítja az üzemmódot (monitoring / auto / schedule / force, hitelesítés szükséges).
*   **`POST /api/force_submode`**: Kézi felülbírálati almódot választ (hitelesítés szükséges).
*   **`POST /api/set_current`**: Kézzel korlátozza a töltési áramerősséget (hitelesítés szükséges).
*   **`POST /api/sim_toggle` / `POST /api/sim_data`**: Szimulációs módot kapcsol és mock telemetria paramétereket állít (hitelesítés szükséges).

---

## 5. Fejlesztői Útmutató Egyéni Adaptációkhoz

Ha ezt a vezérlőt különböző hardvereszközökhöz szeretnéd adaptálni:

### Különböző Inverter Márka Támogatása

Ha a Deye helyett más invertert kell kezelni (pl. Fronius, Huawei, Victron):
1. A `run_inverter_polling()`-ban cseréld le a `pysolarmanv5`-öt az inverter SDK-jára vagy könyvtárára (pl. Modbus TCP kliens, REST API, MQTT kliens).
2. Olvasd ki a megfelelő teljesítményértékeket (Grid, UPS/Ház fogyasztás, PV, Akku SoC, Akku teljesítmény).
3. Írd ezeket az értékeket a `shared_state` megfelelő kulcsaiba a `state_lock` kontextuson belül.

### Különböző EVSE (Autótöltő) Támogatása

Ha a BESEN helyett más töltőt kell vezérelni (pl. Go-e, Tesla Wall Connector, Shelly relék):
1. A `run_ble_client()`-ben cseréld le a BLE Bleak klienst a töltőd natív API kliensére (pl. HTTP REST hívások, helyi TCP socket, MQTT üzenetek).
2. A `run_charge_controller()` kiértékelésének végén az `ble_command_queue` csomagolása helyett közvetlenül aktiváld a töltőd start/stop vagy áramkorlát parancsait.

---

## 6. Fejlett Biztonság és Konfigurációs Validáció

### 6.1 Áramkorlát (Current Limit) Váltás
A legtöbb "buta" töltőhöz hasonlóan, a BESEN BS20 **nem támogatja a töltési Amper megváltoztatását repülés (töltés) közben**. A szoftver úgy kerüli meg ezt a problémát, hogy ha a felhasználó a webes felületen megváltoztatja az Amper limitet, a vezérlő:
1.  STOP parancsot küld a jelenlegi munkamenetre.
2.  Beállít egy Cooldown időzítőt (pl. 15 másodperc), hogy a töltő reléi kioldjanak és a hardver visszaálljon.
3.  15 másodperc múlva START parancsot küld az ÚJ Amper limit értékkel.

A töltőáram csúszkán a felhasználó mindig egy konkrét 6-16A értéket állít be. A korábbi "szoftveres szabályzás kikapcsolása" jelölőnégyzet (ami `0` értéket küldött és a `charging_logic.py`-ban `0 → 16A` átváltást váltott ki) megszűnt — `charger_max_amps` értéke mindig érvényes, konkrét szám kell legyen (6-16).

### 6.2 Webes Műszerfal Konfiguráció Automatikus Mentése
Minden konfigurációs változás (pl. Akku SoC szintek átállítása) a műszerfalról a `/api/config` REST végponton keresztül érkezik POST kérésként. A megosztott memóriában történő frissítés után a rendszer azonnal kiírja azt a lemezre (`config.json`), biztosítva, hogy egy esetleges áramszünet után a rendszer pontosan ugyanott tudja folytatni.
*   **Atomi, szerver oldali validáció (üres mező elleni védelem):** A `/api/config` handler az összes numerikus mező értékét kiolvassa `config_data`-ból ELŐSZÖR, alkalmazás ELŐTT. Ha bármelyik `null` értékű (JSON `null`, amit a kliens küld üres mező esetén — `JSON.stringify(NaN) === null`) VAGY `charger_max_amps` nem esik 6-16 közé: a handler `load_config()` hívással visszaállítja `shared_state`-t az utolsó jó `config.json`-ból (semmilyen érték sem módosul), majd `{"status": "error", "message": "Hibás adattartalom miatt visszaállt a konfig az eredetire"}` JSON választ küld. Csak ha minden mező érvényes, alkalmazza azokat egyszerre, `with state_lock:` blokkon belül (atomi). Ez a mechanizmus akkor is véd, ha jövőbeni kliens kód tévesen `|| fallback` koerciókat tartalmaz — mert az üres mező `NaN`-t ad, ami `null`-ként érkezik a szerverre.
*   **Konfiguráció Validáció:** A `start_soc < stop_soc` ellenőrzés az alkalmazás előtt fut a szerveroldalon. Rendszerinduláskor (fájlból olvasva) a `load_config()` szintén elvégzi ezt az ellenőrzést és automatikusan azonos szintre emeli az értékeket, megakadályozva a végtelen kapcsolási ciklusok kialakulását.
*   **Heti ütemezés (`forced_schedule`) szigorú validációja:** A `POST /api/config`-ra érkező `forced_schedule` tömböt a szerver `validate_forced_schedule()` függvénye ellenőrzi, mielőtt bármi bekerülne a `shared_state`-be vagy a `config.json`-be: pontosan 7 elem, mindegyik a hét egy-egy napjára (duplikáció nélkül, a magyar napnevek whitelistjéből), `start`/`stop` szigorú `HH:MM` formátumban, `amps` 6–16 közötti egész szám. Enélkül egy hibás `day` érték a dashboard `innerHTML`-jébe kerülve tárolt XSS-t okozhatott volna, egy hibás listaelem pedig a következő újraindításkor `load_config()`-ot omlasztotta volna össze (a program véglegesen le nem induló állapotba kerülhetett volna, amíg valaki kézzel ki nem javítja a `config.json`-t). A `renderSchedule()` kliensoldali függvény védekező második rétegként a napnevet `textContent`-tel írja be `innerHTML` helyett, a `load_config()` pedig `try/except`-tel védi magát egy esetleg mégis sérült `config.json` ellen.

### 6.3 Állapotellenőrzés
Amikor egy töltés megkezdődik, a szoftver `simulated_charging_active = True`-ra áll, és figyeli a Hálózat (Grid) áramfelvételét, hogy megerősítse: az autó ténylegesen csatlakoztatva van és vesz fel áramot. A napló kiírja a "Külső vezérlésű töltés észlelve" (External charging session detected) üzenetet, ha valaki manuálisan (pl. a fali fizikai gombbal vagy a telefonos gyári applikációval) indította el a töltést a szoftver tudta nélkül. Ilyenkor a vezérlő átadja az irányítást és nem avatkozik be, amíg az manuálisan le nem áll (kivétel a vészleállítás túlterhelés miatt).

### 6.4 Fejlett Biztonság: Cooldown és Lockdown
A töltő reléinek és vezérlőjének védelme érdekében a gyors állapotváltások (flapping) és végtelen ciklusok ellen beépített védelmek:
1. **Cooldown (20s ablak):** Egy csúszó 20 másodperces időablak legfeljebb 2 állapotváltást (pl. 1 start, 1 stop) engedélyez. A harmadik váltást ebben az ablakban a rendszer blokkolja egy 20 másodperces "lehűlési" várakozással.
2. **Lockdown (40s ablak):** Ha 40 másodpercen belül 4 állapotváltás történik, az 5. próbálkozásnál a rendszer "Lockdown" (Zárolás) állapotba kerül, és letilt minden további automatikus vagy normál kézi parancsot, amíg a felhasználó a műszerfalon keresztül fel nem oldja (Unlock).
3. **Végtelen Auto-Ciklus Védelem:** Ha a rendszer emberi beavatkozás nélkül egymás után 10 automatikus START/STOP parancsot hajt végre, kényszerített STOP-ot küld és Zárolt (Lockdown) állapotba kerül. Az automatikus vészleállítások (pl. alacsony akku szint) az `is_safety_stop` flag-et használják, amely megőrzi ezt a ciklus-számlálót, így garantálva a védelmet a rossz konfigurációból fakadó végtelen kapcsolgatás (flapping) ellen.
4. **Hard Stop Override (Kényszerleállítás):** A műszerfalról kiadott manuális "Hard STOP" (kényszerített leállítás) parancs biztonsági okokból mindig, kivétel nélkül megkerüli a Cooldown és Lockdown korlátozásokat.
5. **Feloldás csak bejelentkezve:** A `/api/unlock` végpont (ami a Lockdown-t oldja fel) ugyanúgy az autentikációs ellenőrzés (`is_authenticated()`) mögé van kötve, mint minden más állapotmódosító végpont — így a helyi hálózaton bejelentkezés nélkül senki nem tudja feloldani a biztonsági zárolást.

### 6.5 Továbbfejlesztett Ház Túlterhelés Védelem
A ház túlterhelés védelmi logikája a teljes terhelést a `(UPS Terhelés + Töltő Terhelés)` képlettel számolja ki. Ha ez az összeg meghaladja a beállított `house_power_limit_w` konfigurációt, a töltő azonnal leáll. Ez a biztonsági vészleállítás szintén megkerüli a Cooldown és Lockdown késleltetéseket, hogy megelőzze a kismegszakító leoldását.

### 6.6 Központi Ping-Pong Watchdog (Supervisor)
A `main.py`-ban található egy dedikált végtelen ciklus, amely 5 másodpercenként felügyeli a három fő aszinkron feladat (Inverter, BLE, Töltésvezérlő) egészségét. A Watchdog kétféle hibát detektál:
1. **Crash védelem:** Ha a feladat `task.done()` állapota True, ellenőrzi, hogy dobott-e kivételt (`task.result()`). Ha a szál egy hiba miatt leállt, a Watchdog elkapja a kivételt és újra létrehozza a feladatot.
2. **Freeze (Befagyás) védelem:** Minden háttérszál a természetes futási ciklusának végén egy "PONG" időbélyeget frissít a `shared_state["task_pong"]` szótárban. Ha a Watchdog azt észleli, hogy egy szál több mint 30 másodperce nem küldött PONG jelet (pl. egy blokkoló hálózati művelet miatt), akkor a beragadt feladatot `task.cancel()` hívással megszakítja, és a következő ciklusban tisztán újraindítja. Ez az architektúra biztosítja a robusztus működést anélkül, hogy mesterséges pingeket kényszerítene a szálakba.

**A webszerver szál felügyelete:** a `web_thread` (a `ThreadingHTTPServer`-t futtató szál) nem asyncio taszk, ezért nem illeszkedik közvetlenül a fenti `task.cancel()`-alapú mintába — mégis be van vonva a Watchdog felügyelet alá, ugyanazzal a PONG-elvvel:
*   A `dashboard.py`-ban a `ControllerHTTPServer` (a `ThreadingHTTPServer` alosztálya) felülírja a `service_actions()` metódust, amit a `serve_forever()` minden ciklusában (kb. 0.5 mp-enként) meghív, függetlenül attól, jött-e kliens-kérés — ez frissíti a `task_pong["web"]` időbélyeget.
*   A `ControllerHTTPHandler` minden kapcsolat socketjén 30 másodperces időkorlátot állít be (`self.request.settimeout(30)`) a `setup()`-ban — ez megakadályozza, hogy egy beragadt kliens-kapcsolat (pl. mobiltelefon, aminek elalszik a rádiója válaszküldés közben) örökre lefoglalva tartson egy kiszolgáló szálat.
*   Ha a `web_thread` teljesen elhalt (`is_alive() == False`), a Watchdog biztonságosan újraindítja csak azt a szálat.
*   Ha a szál él, de 30 másodpercig nincs `"web"` PONG (ténylegesen befagyott, az elfogadó hurok nem pörög), a Watchdog **a teljes folyamatot kilépteti** (`os._exit(1)`) — mivel egy natív Python szálat nem lehet biztonságosan kívülről megszakítani, ez az egyetlen megbízható helyreállítási út. A `deye-besen-controller.service`-ben lévő `Restart=on-failure` + `RestartSec=5` ezt automatikusan, ember nélkül újraindítja.

---

## 7. Legutóbbi javítások

### 2026-07-08

Egy átvizsgálási kör a következő hibákat tárta fel és javította — itt gyűjtve össze, mivel a fenti szakaszokban leírt viselkedést is érintik:

*   **`line_id` NameError:** A fázisszám-számítás korábban a ciklus közepén futott le, azután, hogy a kézi felülbírálási flag-ek már felhasználhatták volna a `line_id`-t egy korai `continue`-ban. Áthelyezve a ciklus legelejére (lásd 2.2 szakasz).
*   **`/api/unlock` autentikáció-bypass:** A végpontot korábban az `is_authenticated()` ellenőrzés előtt kezelte a szerver, így bejelentkezés nélkül bárki feloldhatta a Lockdown-t a helyi hálózaton. Áthelyezve az autentikációs kapu mögé (lásd 6.4/5. pont).
*   **`forced_schedule` validáció (tárolt XSS + újraindításkori összeomlás):** Lásd a teljes leírást a 6.2 szakaszban.
*   **Session lejárat:** A bejelentkezési session-ök korábban sosem jártak le. Mostantól 24 óra után automatikusan érvénytelenné válnak (lásd 4. szakasz).
*   **Felesleges lemezírás üresjáratban:** A BLE telemetria-feldolgozó korábban minden nem-töltő telemetria csomagnál (kb. másodpercenként) meghívta a `save_config_file()`-t, ami felesleges lemez-/SD-kártya-terhelést jelentett gyengébb hardveren (pl. Raspberry Pi). Mostantól csak akkor ment, ha ténylegesen le kellett zárni egy aktív munkamenetet.
*   **Halott kód eltávolítása:** Két, fejlesztés közben ott felejtett "VÁZLAT" kódrészlet (a `run_charge_controller()` és a `ble_notification_received()` végén, a fő `while True` ciklus után, tehát sosem futottak le), valamint a `ControllerHTTPHandler` osztályon egy duplikált, elavult `is_authenticated()` / `get_cookie()` / `log_message()` metódus-definíció törölve lett. Ez utóbbi nem csak felesleges kód volt: mivel Python egy osztályban az utoljára definiált azonos nevű metódust tartja meg, ez a duplikátum csendben felülírta volna a session-lejáratot már ismerő `is_authenticated()`-et, ami a fenti session-lejárat javítást hatástalanná tette volna.
*   **Android widget — PBKDF2 iterációszám:** A widget korábban hardkódolt `100000`-es iterációszámmal származtatta a session kulcsot, miközben a szerver `pbkdf2_iterations` értéke felhasználó által állítható (a README kifejezetten ajánlja csökkenteni gyengébb hardveren, pl. Raspberry Pi Zero-n). A widget mostantól bejelentkezés előtt lekéri ezt az értéket a `GET /api/login_info`-ról, `100000`-es fallback-kel, ha a lekérdezés bármiért sikertelen.
*   **Android widget — `allowBackup`:** Az `AndroidManifest.xml` korábban `android:allowBackup="true"`-t állított, miközben a dashboard jelszó titkosítás nélkül, plaintext `SharedPreferences`-ben tárolódik — ez `adb backup`-pal kinyerhetővé tette a jelszót nem rootolt eszközön is. Mostantól `"false"`.

### 2026-07-29

Biztonsági javítások — az eredeti incidens gyökérokait szünteti meg, ahol a rendszer betöltetlen/sérült `config.json` esetén hihető, de hamis alapértékekkel indult el, és a webes felületen üres mezőkkel is lehetséges volt menteni.

*   **`config.py` — beégetett alapértékek eltávolítása:** A `DEFAULT_CONFIG` és a `shared_state` inicializálás hat numerikus vezérlési mezőjén (`start_soc`, `stop_soc`, `stop_import_limit`, `grid_charge_duration_minutes`, `house_power_limit_w`, `charger_max_amps`) az alapértékek `None`-ra változtak. A `load_config()` mostantól csak akkor konvertál `int()`-re, ha az érték nem `None`. Ha `config.json` hiányzik, a `shared_state` ezekben a mezőkben `None`-t tartalmaz; a `charging_logic.py` ezzel `TypeError`-t dob az első összehasonlításnál, amit a `main.py` watchdog elkapva biztonságosan újraindítja a taszkot — töltés nem indul.

*   **`charging_logic.py` — `0 → 16A` konverziós minta törlése:** A `start_amps = 16 if charger_max_amps == 0 else charger_max_amps` és a `start_amps = 16 if target_amps == 0 else target_amps` sorok törölve (három előfordulás). A `charger_max_amps == 0` eredeti kettős jelentése (szándékos "nem felügyelt" vs. betöltetlen/hibás adat) megszűnt: a `charger_max_amps` mostantól mindig konkrét, érvényes szám (6-16), amit a `/api/config` szerver oldali validáció garantál.

*   **`dashboard.py` — "Töltőáram szoftveres szabályozásának kikapcsolása" funkció teljes törlése:** A HTML jelölőnégyzetek (`auto_unmanaged_current`, `force-unmanaged-container`), a `toggleUnmanagedCurrent(mode)` JS függvény, és az összes kapcsolódó `unmanaged` ág (a `scheduleAmpsSave`, `checkAutoAmpsChanged`, `checkForceAmpsChanged` és `updateStatus()` függvényekben) törölve.

*   **`dashboard.py` — `saveAutoConfig` üres mező koerciók törlése:** Az `|| 100` és `|| 0` fallback koerciók törölve az öt numerikus mezőről. Üres mező esetén `parseInt` → `NaN` → `JSON.stringify` → `null` kerül a kérésbe, amit a szerver elutasít. Hiba esetén (szerver `status: error` vagy hálózati hiba) a kliens megjeleníti a hibaüzenetet és `updateStatus()`-szal visszatölti a formot a mentett értékekre.

*   **`dashboard.py` — `/api/config` atomi validáció:** Lásd a 6.2 szakasz frissített leírását. A korábbi szekvenciális, mezőnkénti alkalmazás helyett az összes numerikus mező egyszerre validálódik és alkalmaz, `load_config()`-alapú visszaállítással hibánál.

### 2026-07-31

*   **`dashboard.py` — mobil navigáció: hamburger menü lecserélése jobb oldali ikondokkra:** a korábbi, teljes képernyős, középre igazított hamburger-overlay menü (5 szöveges menüpont) helyett egy állandóan látható, félig átlátszó, jobb oldali függőleges ikondokk jelenik meg mobil nézetben (`position: fixed; right: 0; bottom: 12vh`), a képernyő alsó harmadában — egykezes, jobb kezes hüvelykujj-eléréssel. Az 5 ikon (nap/Auto Solar, óra/Ütemezett, kéz/Kézi mód, aktivitás/Mérések, dokumentum/Napló) felirat nélküli, tooltip nélküli SVG ikon. A Kijelentkezés gomb a fejlécbe költözött, kis ikonként, csak akkor látszik, ha `web_auth_enabled` igaz. A Kézi mód (`#config-force`) mobil nézetben `min-height: calc(100dvh - 210px)`-et kapott, hogy a vezérlőgombok mindig a látható terület alsó harmadában, konzisztens helyen jelenjenek meg, egykezes eléréshez optimalizálva.
*   **`dashboard.py` — csendes E2EE visszafejtési hiba javítása (kényszerített újra-bejelentkeztetés):** Mobilon előfordult, hogy egy korábban megnyitott lap újranyitásakor (böngésző bezárása/újranyitása után, feltehetően mobil bfcache/tab-freeze miatt) a dashboard nem kért új bejelentkezést, és elavult/hiányos adatokat mutatott (pl. a töltőáram csúszka a HTML-be égetett `16`-os alapértéken ragadt). Gyökérok: a kliens oldali `window.fetch` felülírásban, ha a `_pskDecrypt()` MAC-ellenőrzése hibázott (mert a lap JS-állapota — köztük az elavult session-kulcs — befagyasztva élte túl a bezárást, miközben a szerver közben újraindult / új session-kulcsot generált), a `catch` ág **csendben elnyelte a hibát**, és az eredeti, még titkosított JSON blobot adta vissza a hívónak a valódi adat helyett. Ez a `updateStatus()`-ban ahhoz vezetett, hogy a numerikus mezők mind `undefined` értéket kaptak, amit a DOM-frissítő kód (`.value = data.charger_max_amps`) csendben eldobott egy `range` inputon — nem volt hibaüzenet, nem volt kikényszerített újra-bejelentkeztetés. A `catch` ág mostantól — a `401 Unauthorized` ághoz hasonlóan — törli a helyi session-kulcsot (`sessionStorage`) és kikényszeríti az oldal újratöltését, ha bármilyen visszafejtési/MAC-hiba történik. Ez biztosítja, hogy elavult vagy hiányos adat sosem jelenhet meg csendben, és gyakoribb re-authentikációt is kikényszerít biztonsági szempontból.

### 2026-08-08

Két, egymástól független hibakör javítása: a webszerver szálfelhalmozódása (aminek tünete volt a mobilon időnként visszatérő adatnélküli/hiányos dashboard), és a Mérések oldal mobil elrendezési hibája.

*   **Webszerver szálfelhalmozódás — socket-időkorlát + Watchdog-felügyelet:** lásd a 6.6 szakasz frissített leírását. A gyökérok az volt, hogy a `ThreadingHTTPServer` minden kapcsolathoz külön szálat indított, de nem volt rajta időkorlát — egy elalvó mobilrádiójú kliens miatt beragadt szál sosem szabadult fel. A `ControllerHTTPHandler.setup()` mostantól 30 mp-es `socket.timeout`-ot állít be minden kapcsolatra; a `ControllerHTTPServer` (a `ThreadingHTTPServer` alosztálya) `service_actions()`-szal PONG-jelet küld a Watchdog-nak; a Watchdog a webszerver szálat is figyeli (crash esetén újraindítja csak a szálat, befagyás esetén — mivel natív szálat nem lehet biztonságosan megszakítani — a teljes folyamatot lépteti ki `os._exit(1)`-gyel, amit a systemd `Restart=on-failure` automatikusan újraindít).

*   **`dashboard.py` — Mérések oldal mobil elrendezési hiba (a jobb oldali ikondokk kicsúszott a képernyőről):** mobil nézetben, kizárólag a Mérések oldalon, a jobb oldali ikondokk (lásd 2026-07-31 bejegyzés) a viewport szélén túlra tolódott, csak kizoomolva vagy egyáltalán nem volt látható. Három, egymást kiegészítő ok/javítás:
    1.  **`html`/`body` `overflow-x` felcserélése:** az eredeti `html, body { overflow-x: hidden; }` szabály mindkét elemre explicit overflow-értéket adott, ami a CSS specifikáció szerint blokkolta a `body`→`html` felszivárgást, és a `body`-t önálló görgetési dobozzá tette (dupla görgetősáv — a Mérések oldalon kétszer kellett felfelé húzni a lapot). A javítás: `overflow-x: hidden` kizárólag a `body`-n maradt (a `html`-ről levéve) — így a szabályos felszivárgás érvényesül (egyetlen görgetési doboz), és a `body` közvetlenül elfojtja a saját vízszintes túlcsordulását is (megelőzve, hogy a böngésző kitágítsa az elrendezési viewportot egy túlcsorduló elem miatt).
    2.  **`.phase-table { table-layout: fixed !important; }`** (mobil media query): a Kézi töltés alatti FÁZIS/FESZÜLTSÉG/ÁRAM táblázat (`#active-charging-view`-ban) nem respektálta az összenyomott flex-konténerét (`min-width: 0`) — egy sima `<table>` nem megy a tartalma természetes minimum-szélessége alá `table-layout: fixed` nélkül, így 403px-re nőtt egy 344px-es dobozban.
    3.  **`.telemetry-status-rows > div > div:last-child .tooltip-text`** jobbra-igazítás (mobil media query): a "Belső hőfok" és "Állapot" sorok tooltip-jei (a `.metric-grid`-en kívül, egy külön `justify-content: space-between` szekcióban) nem kapták meg a `metric-grid`-nél már meglévő jobbra-igazított tooltip kezelést (`right: -10px; left: auto`), ezért az alapértelmezett középre-igazítással a jobb oldali elemek tooltip-doboza (220px széles) kilógott a képernyőről. A `.telemetry-status-rows` class hozzáadásával ez a wrapper is bekerült a jobbra-igazító szabály hatálya alá.

---

## 8. Android Widget (`AndroidWidget/`)

A projekthez tartozik egy különálló natív Android-alkalmazás (Kotlin), amely egy kezdőképernyős widgettel jeleníti meg a rendszer élő telemetriáját. Saját APK-ként fordul, a GitHub Actions workflow (`.github/workflows/android_widget_build.yml`) minden `AndroidWidget/**` érintő pushra lefuttatja az `assembleDebug`-ot, és artifactként feltölti az APK-t.

### 8.1 Komponensek

*   **`DeyeWidgetProvider`** (`AppWidgetProvider`): a widget életciklusát kezeli. Az `onUpdate()` felrakja a nézetet, beállítja a koppintás-kezelőt, majd `KEEP` politikával elindítja a frissítő hurkot és a 15 perces életben tartó munkát. Az `onDisabled()` (az utolsó widget levételekor) mindkettőt leállítja.
*   **`WidgetConfigActivity`**: a widget kihelyezésekor megnyíló beállító képernyő. A `DeyePrefs` nevű `SharedPreferences`-be menti a szerver IP-t, a dashboard jelszót (plaintext) és a háttér átlátszóságot (`bg_alpha`).
*   **`WidgetUpdateWorker`** (`Worker`): a fő frissítő hurok (lásd 8.3).
*   **`WidgetKeepAliveWorker`** (`Worker`): 15 percenként futó biztonsági háló, amely újraéleszti a fő hurkot, ha az meghalt volna (lásd 8.4).
*   **`ScreenUnlockReceiver`** (`BroadcastReceiver`): best-effort képernyő- és boot-esemény figyelő; a frissítési lánc **nem** épül rá kizárólagosan (lásd 8.4).
*   **`CryptoUtils`**: a szerverrel megegyező kulcsszármaztatás és E2EE visszafejtés (PBKDF2, AES-256-CBC, HMAC-SHA256).

### 8.2 Adat- és hitelesítési folyamat

A widget ugyanazt a titkosított protokollt használja, mint a webes felület:

1.  **`GET /api/login_info`** – lekéri a szerver aktuális `pbkdf2_iterations` értékét (fallback: `100000`), hogy a kulcsszármaztatás egyezzen a szerverrel akkor is, ha az érték az alapértelmezettől eltér.
2.  **`POST /api/login`** – a widget generál egy `clientNonce`-t, ebből és a jelszóból PBKDF2-vel session kulcsot származtat, és egy `authProof`-ot küld. Siker esetén a szerver `session=` sütit ad vissza.
3.  **`GET /api/status`** – a session sütivel lekéri a telemetriát. A választ (ha `enc:true`) a session kulccsal AES-256-CBC + HMAC-SHA256 ellenőrzéssel visszafejti, és a `partiallyUpdateAppWidget()`-tel frissíti a widget nézetét.

Session lejárat / `401` esetén a widget törli a memóriában tartott tokent és kulcsot, és a következő körben újra bejelentkezik.

### 8.3 Frissítési modell

A `widget_info.xml`-ben `updatePeriodMillis="0"` — a rendszer **nem** végez periodikus frissítést; a widget maga menedzseli a frissítést egy folyamatos hurokkal. A `WidgetUpdateWorker.doWork()` addig ismétel (kb. 5 másodperces ciklusidővel), amíg a képernyő be van kapcsolva (`PowerManager.isInteractive`) és a WiFi elérhető. Lezárt képernyőn a hurok magától leáll (energiatakarékosság), WiFi hiányában pedig üres/átlátszó állapotot mutat.

### 8.4 WiFi-ellenállóság (hálózatváltás-kezelés)

Egy korábbi hibában a widget adata „beragadt", ha a felhasználó elhagyta a saját WiFi hatósugarát, majd visszatért. A gyökérokok több rétegben javítva lettek:

*   **`InterruptedException`-kezelés és önújraindítás:** A WorkManager a Worker 10 perces futásidő-limitjének lejártakor a szál megszakításával (interrupt) állítja le a hurkot, amitől a `Thread.sleep()` `InterruptedException`-t dob. Ezt korábban semmi nem kapta el, így a `doWork()` kivétellel halt meg, és sosem indult újra. Mostantól a `try/finally` szerkezet elkapja, és a `finally` ág `REPLACE` politikával **garantáltan újraütemezi** a hurkot, ameddig a képernyő aktív.
*   **15 perces heartbeat (`WidgetKeepAliveWorker`):** Periodikus `PeriodicWorkRequest`, amely `KEEP` politikával újraéleszti a fő hurkot, ha az bármi miatt meghalt (process-halál, el nem kézbesített broadcast). Élő hurok mellett nem csinál semmit. A WorkManager ezt a telefon újraindítása után is megőrzi.
*   **Hálózat-callback:** A hurok futása alatt egy `ConnectivityManager.registerNetworkCallback()` figyeli a WiFi (`TRANSPORT_WIFI`) elérhetőségét. Hazatéréskor, amint a WiFi újra elérhető, a várakozó ciklus azonnal megszakad és frissít — nem kell kivárni az 5 másodpercet.
*   **Captive-portál elleni login-validáció:** A `doLogin()` már nem fogad el akármilyen HTTP 200-at. Csak akkor tárolja el a session-t, ha a válasz tényleg a mi szerverünktől jött: JSON `{"status":"success"}` body **és** valódi `session=` süti is érkezett. Enélkül idegen hálózaton egy átirányító oldal HTTP 200-a üres tokennel „mérgezte meg" a session-t.
*   **Koppintás-mentőöv:** A widgetre koppintva az `onUpdate()` `KEEP`-pel újraindítja a hurkot, kézi végső lehetőségként.

A `ScreenUnlockReceiver` (`USER_PRESENT` / `SCREEN_OFF` / `BOOT_COMPLETED`) csak gyorsítás azokon az eszközökön, ahol az esemény megérkezik; az Android 8+ implicit broadcast korlátozásai (és az, hogy a `SCREEN_OFF` manifest-receivernek nem kézbesíthető) miatt a frissítés megbízhatósága nem rá, hanem a fenti mechanizmusokra épül.

