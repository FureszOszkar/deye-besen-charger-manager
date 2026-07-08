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

## 4. Szoftver Funkciók és Biztonság

### 4.1 Áramkorlát (Current Limit) Váltás
A legtöbb "buta" töltőhöz hasonlóan, a BESEN BS20 **nem támogatja a töltési Amper megváltoztatását repülés (töltés) közben**. A szoftver úgy kerüli meg ezt a problémát, hogy ha a felhasználó a webes felületen megváltoztatja az Amper limitet, a vezérlő:
1.  STOP parancsot küld a jelenlegi munkamenetre.
2.  Beállít egy Cooldown időzítőt (pl. 15 másodperc), hogy a töltő reléi kioldjanak és a hardver visszaálljon.
3.  15 másodperc múlva START parancsot küld az ÚJ Amper limit értékkel.

### 4.2 Webes Műszerfal Konfiguráció Automatikus Mentése
Minden konfigurációs változás (pl. Akku SoC szintek átállítása) a műszerfalról a `/api/config` REST végponton keresztül érkezik POST kérésként. A megosztott memóriában történő frissítés után a rendszer azonnal kiírja azt a lemezre (`config.json`), biztosítva, hogy egy esetleges áramszünet után a rendszer pontosan ugyanott tudja folytatni.
*   **Konfiguráció Validáció:** A mentés során (valamint a fájlból történő betöltéskor) a rendszer kettős védelmet alkalmaz a logikai hibák ellen. Ha a felhasználó az akkumulátor indítási szintjét (`start_soc`) alacsonyabbra állítaná a leállítási küszöbnél (`stop_soc`), a kliensoldali JavaScript és a szerveroldali API is elutasítja a módosítást egy hibaüzenettel. Rendszerinduláskor (fájlból olvasva) pedig automatikusan azonos szintre emeli az értékeket, megakadályozva a végtelen kapcsolási ciklusok kialakulását.
*   **Heti ütemezés (`forced_schedule`) szigorú validációja:** A `POST /api/config`-ra érkező `forced_schedule` tömböt a szerver `validate_forced_schedule()` függvénye ellenőrzi, mielőtt bármi bekerülne a `shared_state`-be vagy a `config.json`-be: pontosan 7 elem, mindegyik a hét egy-egy napjára (duplikáció nélkül, a magyar napnevek whitelistjéből), `start`/`stop` szigorú `HH:MM` formátumban, `amps` 6–16 közötti egész szám. Enélkül egy hibás `day` érték a dashboard `innerHTML`-jébe kerülve tárolt XSS-t okozhatott volna, egy hibás listaelem pedig a következő újraindításkor `load_config()`-ot omlasztotta volna össze (a program véglegesen le nem induló állapotba kerülhetett volna, amíg valaki kézzel ki nem javítja a `config.json`-t). A `renderSchedule()` kliensoldali függvény védekező második rétegként a napnevet `textContent`-tel írja be `innerHTML` helyett, a `load_config()` pedig `try/except`-tel védi magát egy esetleg mégis sérült `config.json` ellen.

### 4.3 Állapotellenőrzés
Amikor egy töltés megkezdődik, a szoftver `simulated_charging_active = True`-ra áll, és figyeli a Hálózat (Grid) áramfelvételét, hogy megerősítse: az autó ténylegesen csatlakoztatva van és vesz fel áramot. A napló kiírja a "Külső vezérlésű töltés észlelve" (External charging session detected) üzenetet, ha valaki manuálisan (pl. a fali fizikai gombbal vagy a telefonos gyári applikációval) indította el a töltést a szoftver tudta nélkül. Ilyenkor a vezérlő átadja az irányítást és nem avatkozik be, amíg az manuálisan le nem áll (kivétel a vészleállítás túlterhelés miatt).

### 4.4 Fejlett Biztonság: Cooldown és Lockdown
A töltő reléinek és vezérlőjének védelme érdekében a gyors állapotváltások (flapping) és végtelen ciklusok ellen beépített védelmek:
1. **Cooldown (20s ablak):** Egy csúszó 20 másodperces időablak legfeljebb 2 állapotváltást (pl. 1 start, 1 stop) engedélyez. A harmadik váltást ebben az ablakban a rendszer blokkolja egy 20 másodperces "lehűlési" várakozással.
2. **Lockdown (40s ablak):** Ha 40 másodpercen belül 4 állapotváltás történik, az 5. próbálkozásnál a rendszer "Lockdown" (Zárolás) állapotba kerül, és letilt minden további automatikus vagy normál kézi parancsot, amíg a felhasználó a műszerfalon keresztül fel nem oldja (Unlock).
3. **Végtelen Auto-Ciklus Védelem:** Ha a rendszer emberi beavatkozás nélkül egymás után 10 automatikus START/STOP parancsot hajt végre, kényszerített STOP-ot küld és Zárolt (Lockdown) állapotba kerül. Az automatikus vészleállítások (pl. alacsony akku szint) az `is_safety_stop` flag-et használják, amely megőrzi ezt a ciklus-számlálót, így garantálva a védelmet a rossz konfigurációból fakadó végtelen kapcsolgatás (flapping) ellen.
4. **Hard Stop Override (Kényszerleállítás):** A műszerfalról kiadott manuális "Hard STOP" (kényszerített leállítás) parancs biztonsági okokból mindig, kivétel nélkül megkerüli a Cooldown és Lockdown korlátozásokat.
5. **Feloldás csak bejelentkezve:** A `/api/unlock` végpont (ami a Lockdown-t oldja fel) ugyanúgy az autentikációs ellenőrzés (`is_authenticated()`) mögé van kötve, mint minden más állapotmódosító végpont — így a helyi hálózaton bejelentkezés nélkül senki nem tudja feloldani a biztonsági zárolást.

### 4.5 Továbbfejlesztett Ház Túlterhelés Védelem
A ház túlterhelés védelmi logikája a teljes terhelést a `(UPS Terhelés + Töltő Terhelés)` képlettel számolja ki. Ha ez az összeg meghaladja a beállított `house_power_limit_w` konfigurációt, a töltő azonnal leáll. Ez a biztonsági vészleállítás szintén megkerüli a Cooldown és Lockdown késleltetéseket, hogy megelőzze a kismegszakító leoldását.

### 4.6 Központi Ping-Pong Watchdog (Supervisor)
A `main.py`-ban található egy dedikált végtelen ciklus, amely 5 másodpercenként felügyeli a három fő aszinkron feladat (Inverter, BLE, Töltésvezérlő) egészségét. A Watchdog kétféle hibát detektál:
1. **Crash védelem:** Ha a feladat `task.done()` állapota True, ellenőrzi, hogy dobott-e kivételt (`task.result()`). Ha a szál egy hiba miatt leállt, a Watchdog elkapja a kivételt és újra létrehozza a feladatot.
2. **Freeze (Befagyás) védelem:** Minden háttérszál a természetes futási ciklusának végén egy "PONG" időbélyeget frissít a `shared_state["task_pong"]` szótárban. Ha a Watchdog azt észleli, hogy egy szál több mint 30 másodperce nem küldött PONG jelet (pl. egy blokkoló hálózati művelet miatt), akkor a beragadt feladatot `task.cancel()` hívással megszakítja, és a következő ciklusban tisztán újraindítja. Ez az architektúra biztosítja a robusztus működést anélkül, hogy mesterséges pingeket kényszerítene a szálakba.

### 4.7 Végpontok közötti API Titkosítás (E2EE)
A webes Műszerfal kliens oldali JavaScript kódja és a Python HTTP szerver közötti forgalom hálózati lehallgatás (sniffing) elleni védelme beépített, katonai szintű kriptográfiát használ:
1.  **Jelszó Ellenőrzés:** A jelszó (plaintext) soha nem kerül elküldésre a hálózaton. A böngésző egy HMAC-SHA256 alapú `auth_proof`-ot küld (amely a szerver által generált `client_nonce` alapján készül, a 100 000 iterációs PBKDF2-SHA256 kulccsal).
2.  **Transzparens Payload Titkosítás:** A sikeres bejelentkezés után a kliens oldalon felülírt `fetch()` API automatikusan titkosít minden HTTP POST body-t. Korábban a böngésző natív WebCrypto API-ját használtuk (AES-GCM), de a mobil böngészők (pl. Chrome, Vivaldi) HTTP-n történő tiltása miatt a kliens oldal teljesen függetlenített CryptoJS alapokra állt át. A payload így AES-256-CBC módban (PKCS7 paddinggal) titkosítódik, amelyhez utólagos HMAC-SHA256 (Encrypt-then-MAC) ellenőrzőösszeg csatlakozik a manipulációk elkerülésére. A szerver a `pycryptodome` csomaggal fejti vissza a kéréseket, a válaszok JSON objektumai pedig ugyanígy titkosítva és dedikált MAC aláírással érkeznek vissza a klienshez.
3.  **Session lejárat:** Minden `active_sessions`-ben tárolt bejelentkezési token mostantól szerveroldali lejárati időbélyeget is kap (`SESSION_TTL_SECONDS`, alapértelmezetten 24 óra). A lejárt tokent a rendszer a következő hozzáférésnél automatikusan érvénytelennek tekinti és törli, így egy ellopott vagy elfelejtett süti nem marad örökre érvényes.
4.  **`GET /api/login_info`:** Autentikáció nélkül elérhető, nem titkos végpont, ami visszaadja a szerver aktuális `pbkdf2_iterations` értékét. Ezt a webes login-oldal mellett az Android widget is használja, hogy a kulcsszármaztatáshoz mindig a szerverrel megegyező iterációszámot alkalmazza, akkor is, ha az a `config.json`-ban az alapértelmezettől eltérő értékre lett állítva (lásd README, 5. szakasz).

---

## 5. Legutóbbi javítások (2026-07-08)

Egy átvizsgálási kör a következő hibákat tárta fel és javította — itt gyűjtve össze, mivel a fenti szakaszokban leírt viselkedést is érintik:

*   **`line_id` NameError:** A fázisszám-számítás korábban a ciklus közepén futott le, azután, hogy a kézi felülbírálási flag-ek már felhasználhatták volna a `line_id`-t egy korai `continue`-ban. Áthelyezve a ciklus legelejére (lásd 2.2 szakasz).
*   **`/api/unlock` autentikáció-bypass:** A végpontot korábban az `is_authenticated()` ellenőrzés előtt kezelte a szerver, így bejelentkezés nélkül bárki feloldhatta a Lockdown-t a helyi hálózaton. Áthelyezve az autentikációs kapu mögé (lásd 4.4/5. pont).
*   **`forced_schedule` validáció (tárolt XSS + újraindításkori összeomlás):** Lásd a teljes leírást a 4.2 szakaszban.
*   **Session lejárat:** A bejelentkezési session-ök korábban sosem jártak le. Mostantól 24 óra után automatikusan érvénytelenné válnak (lásd 4.7 szakasz).
*   **Felesleges lemezírás üresjáratban:** A BLE telemetria-feldolgozó korábban minden nem-töltő telemetria csomagnál (kb. másodpercenként) meghívta a `save_config_file()`-t, ami felesleges lemez-/SD-kártya-terhelést jelentett gyengébb hardveren (pl. Raspberry Pi). Mostantól csak akkor ment, ha ténylegesen le kellett zárni egy aktív munkamenetet.
*   **Halott kód eltávolítása:** Két, fejlesztés közben ott felejtett "VÁZLAT" kódrészlet (a `run_charge_controller()` és a `ble_notification_received()` végén, a fő `while True` ciklus után, tehát sosem futottak le), valamint a `ControllerHTTPHandler` osztályon egy duplikált, elavult `is_authenticated()` / `get_cookie()` / `log_message()` metódus-definíció törölve lett. Ez utóbbi nem csak felesleges kód volt: mivel Python egy osztályban az utoljára definiált azonos nevű metódust tartja meg, ez a duplikátum csendben felülírta volna a session-lejáratot már ismerő `is_authenticated()`-et, ami a fenti session-lejárat javítást hatástalanná tette volna.
*   **Android widget — PBKDF2 iterációszám:** A widget korábban hardkódolt `100000`-es iterációszámmal származtatta a session kulcsot, miközben a szerver `pbkdf2_iterations` értéke felhasználó által állítható (a README kifejezetten ajánlja csökkenteni gyengébb hardveren, pl. Raspberry Pi Zero-n). A widget mostantól bejelentkezés előtt lekéri ezt az értéket a `GET /api/login_info`-ról, `100000`-es fallback-kel, ha a lekérdezés bármiért sikertelen.
*   **Android widget — `allowBackup`:** Az `AndroidManifest.xml` korábban `android:allowBackup="true"`-t állított, miközben a dashboard jelszó titkosítás nélkül, plaintext `SharedPreferences`-ben tárolódik — ez `adb backup`-pal kinyerhetővé tette a jelszót nem rootolt eszközön is. Mostantól `"false"`.
