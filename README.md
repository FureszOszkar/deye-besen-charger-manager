# Deye & BESEN Integrált Töltővezérlő Rendszer
## Rendszerdokumentáció és Felhasználói Kézikönyv

Ez a szoftver egy helyi, offline futó integrált vezérlő megoldás, amely összeköt egy **Deye háromfázisú hibrid invertert** és egy **BESEN BS20 okos autótöltőt (EVSE)**. A szoftver célja, hogy automatikusan, intelligensen és biztonságosan vezérelje az elektromos járművek töltését a napelemes energiatermelés és az otthoni akkumulátor állapota alapján.

<img width="1888" height="881" alt="kép" src="https://github.com/user-attachments/assets/47b391ad-d030-4909-8c09-ecc0df7c2abe" />
---

## 1. Hardver modellek és specifikációk

Ezt a szoftvert a következő hardverkörnyezetben fejlesztették és tesztelték:

*   **Hibrid Inverter:** **Deye 5 kW Hibrid Inverter** (pl. SUN-5K-SG sorozat, 5 kW maximális névleges teljesítmény)
    *   **Kommunikációs interfész:** Solarman LSW-3 Wi-Fi Logger (Modbus RTU over TCP protokoll a `8899`-es porton).
*   **Autótöltő (EVSE):** **BESEN BS20-APP-3P16A** (3 fázisú, max 16A / 11 kW okos autótöltő)
    *   **Kommunikációs interfész:** Bluetooth Low Energy (BLE) kapcsolat.
*   **Otthoni Akkumulátor:** Kisfeszültségű (48V) Lítium-Vas-Foszfát (LiFePO4 / LFP) akkupakk (pl. 20-30 kWh kapacitás) az inverterhez csatlakoztatva.

---

## 2. Speciális Helyi Fizikai Feltételek és Követelmények

A Bluetooth Low Energy (BLE) és a helyi Wi-Fi hálózat stabilitása kritikus fontosságú a rendszer folyamatos, felügyelet nélküli működéséhez. A következő speciális hardver feltételeknek kell teljesülniük:

### A) Nagy nyereségű USB Bluetooth (BT) Antenna / Adapter
A BESEN töltő alapértelmezett Bluetooth chipjének hatótávolsága korlátozott. A vezérlő szoftvert futtató számítógépnek **rendelkeznie kell egy külső USB Bluetooth 5.0 (vagy újabb) adapterrel, amely nagy nyereségű antennával van felszerelve** (a rendszert sikeresen tesztelték a **Mercusys MA550H Long Range Bluetooth 5.4** adapterrel). A beépített alaplapi Bluetooth chipek vagy apró USB dongle-k nem képesek stabil kapcsolatot fenntartani az épületen kívül elhelyezett autótöltővel.

- **Megjegyzés az időbélyegekről:** A BESEN töltő MCU-ja ellenőrzi a Unix időbélyeget a START parancsban az időszinkronizációhoz. Ha jelentős eltérés van (pl. Budapest vs. Sanghaj), elutasíthatja a csomagot. Ennek kezelésére a `get_shanghai_timestamp()` függvény a helyi időt Unix időbélyeggé alakítja 8 órás eltolással (Sanghaj időzóna). Ezt a módosított időbélyeget használják a START parancsokban.

### Alternatív megoldás: Mikroszámítógép (pl. Raspberry Pi) a töltő közelében
Ha a vezérlőt futtató fő számítógép túl messze van, egy rendkívül hatékony alternatíva a drága, nagy hatótávolságú antenna helyett egy olcsó, Wi-Fi és Bluetooth képes mikroszámítógép (pl. **Raspberry Pi Zero 2 W, Raspberry Pi 3, 4 vagy 5**) elhelyezése a töltő közelében (pl. a garázsban).

---

## 3. Telepítés és Futtatás (Windows)

A szoftver Python-ban íródott, és Python forráskódként, vagy PyInstaller-rel egyetlen végrehajtható fájllá (EXE) fordítva futtatható Windows rendszereken.

### Futtatás Python forrásból
1. Telepítsd a Python 3.9+ verziót.
2. Telepítsd a szükséges csomagokat: `pip install bleak`
3. Futtasd a fő szkriptet: `python main.py`

### Futtatás független EXE-ként
1. Használd a mellékelt `deye_besen_controller.exe` fájlt. Nincs szükség telepítésre vagy Python környezetre.
2. Csak kattints duplán az indításhoz. A program megnyit egy parancssori ablakot a naplókhoz, és elindítja a háttérszolgáltatásokat.

### Linux (Debian 13)
A [`LinuxController`](LinuxController) mappa önmagában, teljesen önállóan tartalmazza a Linux alatti futtatáshoz szükséges mindent — bármilyen néven, bármilyen könyvtárba átmásolható a Linux gépre. A telepítéshez és a systemd szolgáltatáskénti üzemeltetéshez lásd a [`LinuxController/README.md`](LinuxController/README.md) útmutatót.

### A Vezérlőpult (Dashboard) elérése
Indítás után a webes felület elérhető a helyi hálózaton keresztül.
*   **URL:** `http://localhost:8000` (vagy a gép helyi IP címe, pl. `http://192.16.8.1.100:8000`)
*   **Alapértelmezett Jelszó:** `admin` (Ezt a kód jelszavában, vagy szükség esetén a konfigurációban lehet megváltoztatni)

---

## 4. Működési Módok

A szoftver három fő vezérlési módot kínál, amelyeket a webes felületen lehet kiválasztani:

### 1. Napelemes (Solar) Auto Mód
Ez a teljesen autonóm, "állítsd be és felejtsd el" mód. A vezérlő folyamatosan figyeli a Deye invertert és az akkumulátor szintet.
*   **Indítási feltétel:** Ha az otthoni akkumulátor állapota (SoC) eléri az *Indítási SoC küszöböt* (pl. 95%), és a napelemes termelés elegendő.
*   **Leállítási feltételek:**
    *   Ha a ház áramfogyasztása túl magasra nők (Túlterhelés védelem).
    *   Ha az otthoni akkumulátor lemerül a megadott *Leállítási SoC küszöb* alá (pl. 50%).
*   **Fix Áramkorlát (Nincs dinamikus szabályozás):** A szoftver a beállított fix maximális áramerősséggel indítja el a töltést (vagy az autó beépített fedélzeti töltőjének korlátjával). Az autó akkumulátorának és töltőelektronikájának védelme érdekében a vezérlő nem szabályozza folyamatosan fel-le a töltőáramot. A hálózati import elkerülését tisztán a BE/KI (Start/Stop) biztonsági limitek végzik.

### 2. Ütemezett (Scheduled) Mód
Lehetővé teszi az olcsó éjszakai áramtarifák vagy meghatározott töltési ablakok kihasználását.
*   **Időablak:** Megadhatsz egy Kezdési (pl. 23:00) és egy Befejezési időt (pl. 06:00).
*   **Fix áramerősség:** A töltő egy állandó, előre beállított Amper értékkel (pl. 16A) fog tölteni az időablak alatt.
*   **Napelemes felülbírálat (Opcionális):** Ha az időablakon *kívül* vagyunk, a szoftver visszaállhat a normál Solar Auto viselkedésre, így nappal is tölthetsz napelemről.
*   **Fix Áram felülbírálata:** Opcionálisan engedélyezheted a dinamikus (auto) áramszabályozást az éjszakai ablak alatt is, bár általában fix maximummal töltünk hálózatról.

### 3. Kézi Kényszerített (Force) Mód
Ezzel a móddal felülbírálhatsz minden automatizációt, és manuálisan adhatsz ki Start/Stop parancsokat, és állíthatod be az Amper értéket a csúszkával, akár a gyári applikációban.
*   A "Force Charge" gomb megnyomásával azonnal elindul a töltés a megadott Amperrel (figyelmen kívül hagyva a napelemet és az akkut).
*   A "Hard Stop" gombbal azonnal leállíthatod a töltést.
*   **Figyelem:** Kényszerített módban a rendszer a Ház Túlterhelés Védelmét is figyelmen kívül hagyhatja (kivéve, ha az extra biztonsági funkciókba beleütközik).

---

### Fejlett Biztonsági Mechanizmusok
- **Anti-Flapping Cooldown (Várakozási idő):** Megakadályozza a gyors Start/Stop ciklusokat egy 20 másodperces várakozási idő kikényszerítésével 2 egymást követő állapotváltozás után.
- **Biztonsági Zárolás (Lockdown):** Teljesen zárolja a rendszert, ha 40 másodpercen belül 5 állapotváltozás történik, vagy ha 10 egymást követő automatikus parancs fut le emberi beavatkozás nélkül. A műszerfalról (dashboard) manuális feloldást (Unlock) igényel.
- **Teljes Ház Terhelésvédelem:** A túlterhelés védelem a `(UPS Terhelés + Töltő Terhelés)` összegét értékeli ki a főmegszakítók védelme érdekében. A túlterhelésből fakadó leállítások és a manuális Hard STOP parancsok mindig megkerülik a cooldown/lockdown korlátozásokat.
- **Központi Ping-Pong Watchdog (Supervisor):** Egy dedikált felügyelő mechanizmus védi a szoftvert a leállásoktól. Kétféle hálózati/szoftveres anomáliát képes automatikusan kezelni:
    - *Összeomlás (Crash) védelem:* Ha bármelyik háttérszál váratlan kivétellel (pl. eldobott hálózati kapcsolat) leállna, a Watchdog a főprogram összeomlása nélkül elkapja a hibát, és azonnal újraindítja az adott szálat.
    - *Befagyás (Freeze) védelem:* A szálak működésük során ciklikusan életjelet (PONG) hagynak a memóriában. Ha a Watchdog 30 másodpercig nem észlel életjelet egy száltól (pl. végtelen TCP várakozás miatt befagyott), erőszakosan leállítja, majd tiszta lappal újraindítja.

---

## 5. Konfiguráció és Megmaradó Állapot (Persistence)

A beállítások automatikusan mentésre kerülnek egy helyi `config.json` fájlba, amikor ténylegesen történik mentés (pl. a dashboardon módosítasz valamit, vagy egy töltési munkamenet lezárul). Ha újraindítod a szoftvert (vagy a számítógépet), az automatikusan visszatölti az utolsó beállításokat. **Fontos:** a `config.json` fájl önmagában, pusztán az indítástól **nem** jön létre automatikusan, és a webes felület nem tudja beállítani az inverter IP-t, a sorozatszámot vagy a töltő MAC-címét — ezeket a mellékelt `config_example.json` átmásolásával és kitöltésével (vagy egy meglévő működő `config.json` átvételével) kell megadni.

A műszerfal (Dashboard) a következő beállításokat biztosítja:
*   **Indítási SoC (%)** - Amikor eléri, indul a Solar Auto töltés.
*   **Leállítási SoC (%)** - Amikor alá esik, megáll a Solar Auto töltés.
*   **Ház Túlterhelés-védelem (W)** - Ha a Deye UPS terhelése meghaladja ezt (pl. 3000W), a töltés biztonsági okokból leáll.
*   **Max Hálózati Import (W)** - Hálózati türelem-határ. Ha efelett húzunk a hálózatról, leáll a töltés.
*   **Hálózati Import Időkorlát (Perc)** - Mennyi ideig tolerálja a rendszer a fenti hálózati import túllépést, mielőtt leállítaná a töltést (pl. 5 perc, hogy a felhőátvonulásokat átvészelje).
*   **Üzemmód Megjegyzése Újraindításkor** - Kapcsoló, amivel a vezérlő emlékszik a legutóbb használt módra (Auto/Schedule/Force).
*   *Rejtett haladó beállítás (csak a `config.json`-ban módosítható)*: `"pbkdf2_iterations"` - A jelszó titkosítás erőssége (alapértelmezett: 100000). Gyengébb mikroszámítógépeken (pl. Raspberry Pi Zero) érdemes lehet csökkenteni (pl. 50000-re) a gyorsabb bejelentkezés érdekében. Ez az érték szabadon módosítható: a webes felület és az `AndroidWidget` mappában található widget is dinamikusan lekérdezi az aktuális beállítást a szervertől bejelentkezéskor, nem kell hozzájuk illeszteni a kliens oldalt.

---

## 6. Biztonság és Titkosítás (End-to-End Encryption)

A rendszer beépített, végpontok közötti (End-to-End) titkosítással védi a webes felület és a Python szerver közötti kommunikációt. A helyi hálózaton az adatok lehallgatása (sniffing) ellen a következő védelmi vonalak működnek:
- **Jelszóvédelem (Challenge-Response):** A felhasználói jelszó soha nem utazik a hálózaton. A bejelentkezés során a böngésző egy HMAC alapú hitelesítési bizonyítékot (Auth Proof) küld a szervernek.
- **AES-256-GCM Titkosítás:** A sikeres bejelentkezést követően a böngésző és a szerver minden API forgalmat (parancsokat és visszatérő adatokat) erős, katonai szintű AES-256-GCM titkosítással rejt el a hálózaton. A titkosítás alapjául a bejelentkezéskor generált ideiglenes kulcs szolgál (PBKDF2-SHA256).
- **Session lejárat:** A bejelentkezés után kapott session token 24 óra után automatikusan lejár, ezt követően újra be kell jelentkezni.
- **Feloldás csak bejelentkezve:** A biztonsági zárolás (Lockdown) feloldása (`/api/unlock`) is autentikációt igényel — a helyi hálózaton bejelentkezés nélkül senki nem tudja feloldani.
- **Ütemezés (heti terv) validáció:** A heti ütemezés mentésekor a szerver szigorúan ellenőrzi a beküldött adatokat (napnevek, időformátum, áramerősség-tartomány), mielőtt elmentené — ez védi a rendszert a hibás vagy rosszindulatú adatoktól.

---

## 7. Hibakeresés és Műszerfal

A műszerfalon található egy beépített "Konzol" és "Hibadobozok", amelyek valós idejű visszajelzést adnak:
*   **Sárga Figyelmeztetés:** Lehűlési (Cooldown) időzítő aktív (megakadályozza, hogy a Bluetooth parancsok túl gyorsan spammeljék a töltőt).
*   **Piros Hiba:** Kapcsolódási problémák az Inverterrel (Modbus) vagy a Töltővel (BLE).
*   **Piros Lockdown:** A biztonsági zárolás (Flapping védelem) aktiválódott, kézi feloldás szükséges.
*   **Konzol kimenet:** Részletes hálózati és töltési eseményeket naplóz.

**Biztonsági Jegyzet:** Ez a szoftver hálózati szinten (Modbus/BLE) lép kapcsolatba a hardverrel. Bár beépített biztonsági limitekkel rendelkezik (pl. Deye MAX áram korlátok), a konfigurációk (például a ház túlterhelés határának) beállításakor a helyi hálózat fizikai teherbírását figyelembe kell venni! Használd saját felelősségre.

---

## 8. Android Widget

A projekt tartalmaz egy különálló Android-alkalmazást is (`AndroidWidget` mappa), amely egy **kezdőképernyős widgettel** jeleníti meg a rendszer élő adatait, anélkül hogy meg kellene nyitni a böngészős műszerfalat. Az alkalmazás APK-ját a GitHub Actions automatikusan lefordítja.

### Mit mutat a widget?

A widget a szerverrel megegyező, titkosított kapcsolaton keresztül másodpercenként frissülő értékeket jelenít meg:
*   **Napelem** (PV termelés, W)
*   **Hálózat** (hálózati be-/kitáplálás, W)
*   **Akku SoC** (akkumulátor töltöttség, %)
*   **Akku Telj.** (akkumulátor teljesítménye, W)
*   **Ház** (házi fogyasztás / UPS terhelés, W)
*   **Autó töltés** (a BESEN töltő aktuális teljesítménye, W)

### Telepítés és beállítás

1.  Töltsd le és telepítsd az APK-t a telefonra (a GitHub Actions build artifactjából).
2.  Helyezd ki a **„Deye-Besen adatok"** widgetet a kezdőképernyőre.
3.  A kihelyezéskor automatikusan megnyíló beállító képernyőn add meg:
    *   **Szerver IP-cím** – a vezérlőt futtató gép helyi IP-címe (a widget a `8080`-as porton csatlakozik).
    *   **Jelszó** – ugyanaz a dashboard-jelszó, amivel a webes felületre belépsz.
    *   **Háttér átlátszóság** – a csúszkával a widget háttérképének átlátszósága állítható.
4.  A **Mentés** gombbal a widget aktiválódik.

### Működés

*   A widget **csak akkor frissül, ha a telefon WiFi-n van** és a szerver elérhető. Idegen hálózaton vagy mobiladaton üresen (átlátszón) marad, majd hazatérve automatikusan újra megjelennek az adatok.
*   A frissítés a képernyő bekapcsolt állapotában, kb. 5 másodpercenként történik (lezárt telefonon energiatakarékosságból szünetel).
*   A widgetre **koppintva** azonnali kézi frissítés kényszeríthető.
*   A widget ellenálló a **WiFi-hálózatok közötti váltásra**: ha elhagyod a saját hálózatod hatósugarát, majd visszatérsz, az adatok néhány másodpercen belül maguktól helyreállnak.

### Biztonsági jegyzet

A widget a dashboard-jelszót helyben, a telefon privát tárterületén (`SharedPreferences`) őrzi. Az alkalmazás `android:allowBackup="false"` beállítással tiltja az Android-mentést, hogy a jelszó ne legyen kinyerhető `adb backup`-pal. A widget és a szerver közötti kommunikáció végponttól végpontig titkosított (AES-256 + HMAC), a webes felülettel azonos módon.
