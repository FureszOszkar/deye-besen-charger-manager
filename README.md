# Deye & BESEN Integrált Töltővezérlő Rendszer
## Rendszerdokumentáció és Felhasználói Kézikönyv

Ez a szoftver egy helyi, offline futó integrált vezérlő megoldás, amely összeköt egy **Deye háromfázisú hibrid invertert** és egy **BESEN BS20 okos autótöltőt (EVSE)**. A szoftver célja, hogy automatikusan, intelligensen és biztonságosan vezérelje az elektromos járművek töltését a napelemes energiatermelés és az otthoni akkumulátor állapota alapján.

<img width="1884" height="885" alt="kép" src="https://github.com/user-attachments/assets/506da22f-1c1a-4b82-9252-da9a2e27e46f" />

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
Ha a vezérlőt futtató fő számítógép túl messze van, egy rendkívül hatékony alternatíva a drága, nagy hatótávolságú antenna helyett egy olcsó, Wi-Fi és Bluetooth képes mikroszámítógép (pl. **Raspberry Pi Zero 2 W, Raspberry Pi 3, 4 vagy 5**) elhelyezése a töltő közelében (pl. a garázsban). Mivel a szoftver minimális erőforrást igényel, a teljes vezérlő közvetlenül futtatható ezen a helyi eszközön — így a mikroszámítógép stabil, rövid hatótávú Bluetooth-kapcsolaton kommunikál a töltővel, miközben az invertert és a helyi hálózatot az otthoni Wi-Fi-n keresztül éri el.

### B) Közvetlen rálátás (Line of Sight) a töltőre
Biztosítani kell a lehető legtisztább fizikai rálátást az USB BT antenna és a BESEN töltő között.
*   Vastag betonfalak, fém szerkezetek/burkolatok, és a töltés alatt álló jármű maga is jelentős BLE jelcsillapítást és árnyékolást okozhat.
*   Egy instabil Bluetooth jel hiányzó telemetria-adatokhoz, végül biztonsági leállásokhoz vezethet. Helyezd el az antennát úgy (pl. ablak közelében), hogy minimalizáld a fizikai akadályokat.

### C) Wi-Fi lefedettség a Deye LSW-3 Loggernél
A Deye inverter Wi-Fi adapteréhez folyamatos helyi hálózati kapcsolat szükséges. Győződj meg róla, hogy a helyi router 2.4 GHz-es jele stabilan eléri az inverter telepítési helyét.

---

## 3. Inverter Akkumulátor Szabályozás

A vezérlő szoftver szorosan együttműködik a Deye inverter belső akkumulátor-kezelési logikájával (időzónás beállítások, töltési/kisütési prioritások):

*   **Napelemes prioritás:** A Deye belső szabályozása elsőként a ház fogyasztóit látja el energiával, másodsorban tölti az otthoni akkumulátort, harmadsorban pedig a fennmaradó felesleget a hálózatba táplálja vissza.
*   **Akkumulátor védelem és Indítási SoC:** A vezérlő figyeli az otthoni akkumulátor szintjét (SoC %). A `start_soc` paraméter segítségével (pl. 100%-ra állítva) az autótöltés csak akkor indul, ha az otthoni akkumulátor teljesen feltöltődött. Ez megakadályozza, hogy az autó idő előtt lemerítse az otthoni akkumulátort, mielőtt még elegendő napelemes felesleg lenne.
*   **Kritikus telepítési részlet — a teljes ház az UPS (Backup) ágon, a töltő a hálózati ágon:**
    *   A konkrét fizikai bekötés miatt a **teljes ház** az inverter UPS (Backup) ágára van kötve, de **kizárólag** a ház. Az autótöltő (EVSE) **nem** a házi elosztótáblán keresztül vesz fel áramot — közvetlenül a közműóra mellé van bekötve, az inverter elé (a hálózati/közmű oldalon).
    *   Mivel a ház az UPS ágon van, a teljes háztartási fogyasztás keresztülfolyik az inverter belső teljesítményelektronikáján, aminek szigorú, pontosan **5 kW-os hardverkorlátja** van.
    *   Ha a háztartási fogyasztás (pl. hőszivattyú, mosógép, sütő) megközelíti vagy túllépi ezt az 5 kW-os korlátot, az inverter túlterhelés miatt kiold, ami **azonnali és teljes áramkimaradást** okoz a házban (még akkor is, ha a közműhálózat egyébként elérhető).
    *   Ezért a szoftver **Ház UPS Túlterhelés-védelem (`house_power_limit_w`)** funkciója nem egy opcionális kényelmi funkció, hanem **kritikus védelmi vonal**. A vezérlő folyamatosan figyeli az UPS port terhelését (`ups_load_power`). Ha ez meghaladja a biztonsági küszöböt (pl. 4000 W), azonnal leállítja az autótöltőt, hogy tehermentesítse a rendszert és megelőzze az áramkimaradást.
    *   **Számítási következmény:** Mivel az autótöltő a hálózati oldalon van, a teljesítményfelvétele a fő közműóra (külső CT mérő) és az inverter belső hálózati mérőjének különbségeként számolódik. A műszerfalon ez "Nem UPS ágon lévő fogyasztók" néven jelenik meg, ami az autótöltő és minden más, nem UPS-ágon lévő fogyasztó együttes teljesítményét jelenti.
*   A Solar Auto szabályok (Hálózati Import Limit, Akku Leállítási SoC, Ház UPS Túlterhelés-védelem) egymástól függetlenül, sorrendben kerülnek kiértékelésre.
*   A "Hálózati töltés késleltetett leállítása (perc)" mező `0` percre állítása **azonnali** leállítást jelent (0 perc késleltetés), nem a ellenőrzés kikapcsolását — az ellenőrzés akkor aktív, ha a hálózati teljesítmény küszöbérték nagyobb, mint 0.
*   A Watt paraméterek HTML input mezőinek lépésköze `step=1`, ami egyetlen Watt felbontású beállításokat tesz lehetővé (pl. 80 W).

---

## 4. Telepítés és Futtatás

A szoftver Python-ban íródott, és Python forráskódként, vagy PyInstaller-rel egyetlen végrehajtható fájllá (EXE) fordítva futtatható Windows rendszereken.

### A) Python Környezet Beállítása (Windows)
Telepítsd a Python 3.9+ verziót, majd a szükséges csomagokat:
```bash
pip install bleak==0.20.2 bleak-winrt==1.2.0 pysolarmanv5 pyinstaller
```

### B) Futtatás Szimulációs Módban
A webes felület és a szabályok teszteléséhez valódi hardver nélkül:
```bash
python main.py --sim
```
*Megjegyzés: helyezz egy tetszőleges `background.png` nevű képet a fájl melletti könyvtárba a háttérkép megjelenítésének teszteléséhez.*

### C) Futtatás Éles Módban
Futtasd a szkriptet paraméterek nélkül:
```bash
python main.py
```

### D) Fordítás önálló `.exe` fájllá
A projekt gyökerében található `deye_besen_controller.spec` fájl tartalmazza a fordítási konfigurációt (ikon, egyfájlos csomagolás). A fordításhoz:
```powershell
py -m PyInstaller deye_besen_controller.spec --noconfirm
```
A fordítás után a létrejövő `dist\deye_besen_controller.exe` fájlt másold vissza a projekt gyökerébe. A futtatáshoz az exe mellé szükséges a `crypto-js.min.js` és (opcionálisan) a `background.png` fájl is, mivel ezeket a program futásidőben, az exe mellől tölti be.

### E) Linux (Debian 13)
A [`LinuxController`](LinuxController) mappa önmagában, teljesen önállóan tartalmazza a Linux alatti futtatáshoz szükséges mindent — bármilyen néven, bármilyen könyvtárba átmásolható a Linux gépre. A telepítéshez és a systemd szolgáltatáskénti üzemeltetéshez lásd a [`LinuxController/README.md`](LinuxController/README.md) útmutatót.

### A Vezérlőpult (Dashboard) elérése
Indítás után a webes felület elérhető a helyi hálózaton keresztül.
*   **URL:** `http://localhost:8080` (vagy a gép helyi IP címe, pl. `http://192.168.0.100:8080`)
*   **Alapértelmezett Jelszó:** `admin` (Ezt a kód jelszavában, vagy szükség esetén a konfigurációban lehet megváltoztatni)

---

## 5. Kezelőfelület és Műszerfal Útmutató

A webes felület a `http://localhost:8080` (vagy `http://127.0.0.1:8080`) címen érhető el a vezérlőt futtató gépről. Más eszközökről (pl. mobiltelefon, tablet) a helyi hálózaton a gép helyi IP-címét és portját kell használni (pl. `http://192.168.0.100:8080`). A felület prémium, áttetsző sötétszürke "glassmorphic" dizájnt használ, ami átengedi a `background.png` háttérképet a kártyák mögött.

### A) Színkódolt telemetria (áramlási irány)
A jobb oldali **"Mérések & Visszacsatolás"** kártyán a legfontosabb teljesítményértékek színkódoltak:
*   **Hálózati egyenleg (Grid):**
    *   **ZÖLD (negatív érték):** napelemes visszatáplálás / hálózati export (ingyenes napelemes energia elérhető).
    *   **PIROS (pozitív érték):** hálózati import / fogyasztás (vásárolt hálózati áram).
*   **Akkumulátor teljesítmény:**
    *   **ZÖLD (pozitív érték):** az akkumulátor jelenleg **töltődik** napelemes energiából.
    *   **PIROS (negatív érték):** az akkumulátor jelenleg **kisül** (energiát ad a háznak).
*   A **PV, Ház UPS terhelés és a nem UPS ágon lévő fogyasztók** fehér színnel jelennek meg, tiszta olvashatóság érdekében.

### B) Élő töltési teljesítmény és energia-korrekció
*   **Töltési teljesítmény panel:** egy önálló, kompakt panel a fázis-táblázat mellett mutatja az autóba folyó élő teljesítményt kilowattban (kW). Ez kliens oldalon számolódik: `(V1*I1 + V2*I2 + V3*I3) / 1000`. Inaktív töltésnél természetesen `0.00 kW`-t mutat.
*   **Összes töltési energia:** a BESEN töltő nyers telemetria-regiszterei csak az elsődleges fázis (L1) energia-akkumulációját követik. 3-fázisú töltés esetén (amikor L2 vagy L3 fázison is folyik áram) a vezérlő automatikusan 3.0-szoros szorzót alkalmaz a telemetria-értékre, hogy a műszerfalon a ténylegesen az akkumulátorba juttatott összes energia (kWh) jelenjen meg.

### C) Mobil navigáció (ikondokk)
Mobil nézetben (keskeny képernyőn) a hagyományos fület-választó helyett egy félig átlátszó, jobb oldali ikondokk jelenik meg, ami a képernyő alsó harmadában, egykezes hüvelykujj-eléréssel kényelmesen elérhető. Az 5 ikon jelentése, fentről lefelé:

| Ikon | Jelentés |
|---|---|
| ☀️ (nap) | Auto Solar mód |
| 🕐 (óra) | Ütemezett mód |
| ✋ (kéz) | Kézi mód |
| 📈 (aktivitás) | Mérések |
| 📄 (dokumentum) | Napló |

A Kijelentkezés gomb mobilon a fejlécben, egy külön kis ikonként érhető el (csak akkor látszik, ha a webes hitelesítés be van kapcsolva).

---

## 6. Működési Módok

A szoftver három fő vezérlési módot kínál, amelyeket a webes felület bal oldali kártyájának tetején lehet kiválasztani:

### 1. Napelemes (Solar Auto) Mód
Ez a teljesen autonóm, "állítsd be és felejtsd el" mód, ami a napelemes felesleg maximális kihasználására törekszik.
*   **Napelemes mód bekapcsolása:** aktiválja a napelemes felesleg-logikát.
*   **Maximális töltőáram (6-16A):** beállítja a maximális töltési sebességet.
*   **Indítási akku szint (%):** az a minimális otthoni akkumulátor-szint, ami alatt a töltés nem indulhat el (ajánlott: `100%`).
*   **Hálózati fogyasztás küszöbérték (W):** az a hálózati import-küszöb (pl. `2000 W`), ami felett elindul a késleltetett leállítás időzítője.
*   **Hálózati töltés késleltetett leállítása (perc):** segít áthidalni az átvonuló felhőket. A rendszer ennyi percig türelmes a hálózati importtal, mielőtt leállítaná a töltést. A `0` érték **azonnali** leállítást jelent, feltéve hogy a hálózati teljesítmény küszöbérték nagyobb, mint `0`.
*   **Ház UPS túlterhelés-védelem (W):** ha az UPS port terhelése meghaladja ezt az értéket, a töltés azonnal leáll (ajánlott: `3000 W` – `5000 W`, az inverter és a kismegszakítók névleges értékétől függően).
*   **Fix Áramkorlát (Nincs dinamikus szabályozás):** a szoftver a beállított fix maximális áramerősséggel indítja el a töltést. Az autó akkumulátorának és töltőelektronikájának védelme érdekében a vezérlő nem szabályozza folyamatosan fel-le a töltőáramot. A hálózati import elkerülését tisztán a BE/KI (Start/Stop) biztonsági limitek végzik.

### 2. Ütemezett (Scheduled) Mód
Lehetővé teszi az olcsó éjszakai áramtarifák vagy meghatározott töltési ablakok kihasználását.
*   **Időzített mód bekapcsolása:** aktiválja a heti ütemezési szabályokat.
*   **Napelemes szabályok futtatása az időablakokon kívül:** ha be van kapcsolva, az időablakokon kívül a rendszer visszaáll a Solar Auto szabályokra (nappal napelemről, éjjel ütemezett hálózati töltés).
*   **Heti ütemezési táblázat:** a hét minden napja egyénileg beállítható:
    *   Ütemezés be/kikapcsolása.
    *   Kezdő és befejező idő (ÓÓ:PP).
    *   Áramkorlát (6-16A).
    *   **Solar Auto felülírása:** ha be van jelölve, az időablak alatt a napelemes és akku-leállítási szabályok figyelmen kívül maradnak (garantált éjszakai/időzített töltés).

### 3. Kézi Kényszerített (Force) Mód
Ezzel a móddal felülbírálhatsz minden automatizációt, és manuálisan adhatsz ki Start/Stop parancsokat, és állíthatod be az Amper értéket a csúszkával.
*   **Kézi indítás (Start):** azonnal elindítja a töltést a beállított árammal. Amint a töltés befejeződik (pl. az autó tele lett, vagy kihúzták a kábelt), a kézi felülbírálás automatikusan megszűnik, és visszaáll a Solar/Ütemezett automatizmus.
*   **Kézi Stop (Hard Stop):** azonnal leállítja a töltést, és **felfüggeszti az összes Solar/Ütemezett automatizmust**, amíg kézzel vissza nem vonod a piros "Visszavonás" gombbal.
*   **Ideiglenes leállítás (Soft Stop):** leállítja az aktuális töltési munkamenetet, de nem függeszti fel a szabályokat. Ha később ismét teljesülnek a Solar Auto feltételek, a töltés automatikusan újraindulhat.
*   **Figyelem:** Kényszerített módban a rendszer a Ház Túlterhelés Védelmét is figyelmen kívül hagyhatja (kivéve, ha az extra biztonsági funkciókba beleütközik).

---

## 7. Fejlett Biztonság és Titkosítás

A szoftver számos biztonsági mechanizmust tartalmaz a hardver és a hálózat védelmére, valamint a jogosulatlan beavatkozás vagy véletlen félrekattintás megelőzésére:

1.  **Webes jelszó-hitelesítés és session-kezelés:** mivel a vezérlő más eszközökről is elérhető a helyi hálózaton (`0.0.0.0`-hoz kötve, pl. Raspberry Pi-n futtatva), tartalmaz egy jelszóval védett hitelesítési réteget.
    *   A hitelesítés alapból aktív (`"web_auth_enabled": true`), alapértelmezett jelszó: `"admin"`.
    *   Sikeres bejelentkezés után a szerver kriptográfiailag biztonságos session tokent rendel a böngészőhöz, ami feljogosítja a telemetria megtekintésére és a rendszer vezérlésére.
    *   A fejlécben lévő **Kijelentkezés** gombbal a felhasználó azonnal törölheti a session-jét.
    *   Ha nincs szükség hitelesítésre, kikapcsolható a konfigurációban (`"web_auth_enabled": false`).
2.  **Végpontok közötti titkosítás (AES-256-GCM):** a webes műszerfal és a Python szerver közötti kommunikáció beépített, katonai szintű titkosítással védett.
    *   **Challenge-Response bejelentkezés:** a felhasználó jelszava soha nem utazik a hálózaton. A böngésző egy HMAC alapú hitelesítési bizonyítékot (Auth Proof) generál és küld el helyette.
    *   **AES-256-GCM payload titkosítás:** sikeres bejelentkezés után minden API forgalom (parancsok és telemetria) valós időben titkosítva/visszafejtve utazik, egy PBKDF2-SHA256-tal származtatott session kulcs segítségével. Ez megvédi a rendszert a helyi hálózati lehallgatás ellen.
    *   **Session lejárat:** a bejelentkezési session tokenek 24 óra után automatikusan lejárnak, ezt követően újra be kell jelentkezni.
    *   **Feloldás csak bejelentkezve:** a biztonsági zárolás (Lockdown) feloldása (`/api/unlock`) is hitelesített session-t igényel — a helyi hálózaton senki sem tudja feloldani bejelentkezés nélkül.
    *   **Heti ütemezés validáció:** a szerver szigorúan ellenőrzi a beküldött heti ütemezés adatait (napnevek, időformátum, áramerősség-tartomány), mielőtt elmentené — ez védi a rendszert a hibás vagy rosszindulatú adatoktól.
3.  **Relévédelem (Cooldown):** minden leállított vagy sikertelen töltési kísérlet után a program **2 perces (120 másodperces) várakozási időt** kényszerít ki. Ez idő alatt semmilyen automatizmus nem indíthatja újra a töltést, védve a töltő fizikai reléit a idő előtti kopástól és beragadástól.
4.  **Fail-Safe (hibabiztos) leállítás:** ha a töltés nem indul el 60 másodpercen belül egy BLE start parancs után, a rendszer hibát naplóz. Ha ez 3 egymást követő alkalommal megtörténik, a rendszer automatikusan leállítja a további próbálkozásokat, és **Figyelés (Monitoring)** módba vált, hogy elkerülje a végtelen BLE parancsciklusokat.
5.  **Hálózati aszinkronizáció és telemetria Watchdog (önjavítás):**
    *   A Deye inverter szinkron Modbus kérései (`pysolarmanv5`) egy külön háttérszálon futnak, így a hálózati fennakadások nem fagyasztják be a fő eseményhurkot.
    *   Minden Bluetooth írási és értesítési kérés szigorú, 5 másodperces időkorláttal védett.
    *   **Kapcsolódási időtúllépés védelem:** a `BleakClient` kapcsolódási kísérletei (`client.connect()`) néha végtelenül beragadhatnak a Windows Bluetooth-verem belsejében. Ennek kezelésére a kapcsolódási kísérletek egy explicit, 20 másodperces aszinkron időkorláttal (`asyncio.wait_for`) vannak becsomagolva. Ha a kapcsolódás tovább tart, megszakad, a socket felszabadul, és új újracsatlakozási ciklus indul.
    *   Ha a kapcsolat állapota `LOGGED_IN`, de 15 másodpercig nem érkezik telemetria csomag a töltőtől, a beépített watchdog időtúllépést naplóz, lezárja a halott kapcsolatot, és tisztán újraindítja a BLE felfedezési és újracsatlakozási folyamatot.
    *   **Szálbiztos telemetria-feldolgozás:** a Bleak háttérszálról érkező értesítések a `main_loop` globális referencián keresztül, `asyncio.run_coroutine_threadsafe` segítségével kerülnek vissza a fő eseményhurok szálára, elkerülve a `RuntimeError: no running event loop` kivételeket.
6.  **Anti-Flapping Cooldown:** megakadályozza a gyors Start/Stop ciklusokat egy 20 másodperces várakozási idő kikényszerítésével 2 egymást követő állapotváltozás után.
7.  **Biztonsági Zárolás (Lockdown):** teljesen zárolja a rendszert, ha 40 másodpercen belül 5 állapotváltozás történik, vagy ha 10 egymást követő automatikus parancs fut le emberi beavatkozás nélkül. A műszerfalról manuális feloldást (Unlock) igényel.
8.  **Teljes Ház Terhelésvédelem:** a túlterhelés védelem a `(UPS Terhelés + Töltő Terhelés)` összegét értékeli ki a főmegszakítók védelme érdekében. A túlterhelésből fakadó leállítások és a manuális Hard STOP parancsok mindig megkerülik a cooldown/lockdown korlátozásokat.
9.  **Központi Ping-Pong Watchdog (Supervisor):** egy dedikált felügyelő mechanizmus védi a szoftvert a leállásoktól. Kétféle anomáliát kezel automatikusan:
    *   *Összeomlás (Crash) védelem:* ha bármelyik háttérszál váratlan kivétellel leállna, a Watchdog a főprogram összeomlása nélkül elkapja a hibát, és azonnal újraindítja az adott szálat.
    *   *Befagyás (Freeze) védelem:* a szálak ciklikusan életjelet (PONG) hagynak a memóriában. Ha a Watchdog 30 másodpercig nem észlel életjelet egy száltól, erőszakosan leállítja, majd tiszta lappal újraindítja. A webes kiszolgáló szál is a Watchdog felügyelete alatt áll: ha teljesen elhalna, a Watchdog újraindítja; ha él, de befagyott (nem küld PONG-ot), a teljes folyamat kényszerítve újraindul, amit a systemd/`Restart=on-failure` automatikusan felügyel.

---

## 8. Konfiguráció és Megmaradó Állapot (Persistence)

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

## 9. Hibakeresés és Műszerfal

A műszerfalon található egy beépített "Konzol" és "Hibadobozok", amelyek valós idejű visszajelzést adnak:
*   **Sárga Figyelmeztetés:** Lehűlési (Cooldown) időzítő aktív (megakadályozza, hogy a Bluetooth parancsok túl gyorsan spammeljék a töltőt).
*   **Piros Hiba:** Kapcsolódási problémák az Inverterrel (Modbus) vagy a Töltővel (BLE).
*   **Piros Lockdown:** A biztonsági zárolás (Flapping védelem) aktiválódott, kézi feloldás szükséges.
*   **Konzol kimenet:** Részletes hálózati és töltési eseményeket naplóz.

**Biztonsági Jegyzet:** Ez a szoftver hálózati szinten (Modbus/BLE) lép kapcsolatba a hardverrel. Bár beépített biztonsági limitekkel rendelkezik (pl. Deye MAX áram korlátok), a konfigurációk (például a ház túlterhelés határának) beállításakor a helyi hálózat fizikai teherbírását figyelembe kell venni! Használd saját felelősségre.

---

## 10. Android Widget

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

---

## Köszönetnyilvánítás

Külön köszönet a [slespersen/evseMQTT](https://github.com/slespersen/evseMQTT) GitHub projektnek! A BESEN BS20 töltő Bluetooth Low Energy (BLE) protokolljának visszafejtésével és implementálásával kapcsolatos munkája szilárd alapot adott a vezérlő BLE kommunikációjához. Külön köszönet az AI-alapú páros programozó asszisztensnek a kód refaktorálásáért, az aszinkron vezérlő- és szimulációs hurkok megalkotásáért, a biztonsági védelmek beépítéséért, a prémium glassmorphic webes műszerfal kifejlesztéséért, és a teljes kétnyelvű dokumentáció összeállításáért.

---

## Felelősség kizárása és hibajelentés

Ez a szoftver hálózati/hardver szinten (Modbus/BLE) lép kapcsolatba valódi elektromos berendezésekkel. Bár a fentiekben leírt több rétegű biztonsági mechanizmus védi a rendszert, a felhasználó saját felelősségére használja — a helyi hálózat és elektromos rendszer fizikai teherbírását mindig figyelembe kell venni a konfiguráció beállításakor.

Ha logikai hibát, váratlan viselkedést vagy hibás működést tapasztalsz használat közben, kérjük **jelezd a repository GitHub Issues szakaszában**, hogy javíthassuk! Köszönjük szépen a visszajelzést!
