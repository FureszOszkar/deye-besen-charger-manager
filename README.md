# Otthonvezérlő – Helyi autótöltés és klíma vezérlő
## Rendszerdokumentáció és Felhasználói Kézikönyv

Ez a szoftver egy helyi, offline futó integrált vezérlő megoldás, amely összeköt egy **Deye háromfázisú hibrid invertert** és egy **BESEN BS20 okos autótöltőt (EVSE)**. A szoftver célja, hogy automatikusan, intelligensen és biztonságosan vezérelje az elektromos járművek töltését a napelemes energiatermelés és az otthoni akkumulátor állapota alapján.

Az autótöltés mellett a program a ház további eszközeit is kezeli, mindegyiket külön fülön: **klímák** (BroadLink infravörös adókkal, egy Xiaomi légtisztító hőmérőjével — lásd 11. fejezet), **árnyékolás** (redőny és napellenző rádiós vezérléssel, heti időzítővel — lásd 12. fejezet). A **bojler** vezérlése később jön (13. fejezet).

---

## 1. Hardver modellek és specifikációk

Ezt a szoftvert a következő hardverkörnyezetben fejlesztették és tesztelték:

*   **Hibrid Inverter:** **Deye 5 kW Hibrid Inverter** (pl. SUN-5K-SG sorozat, 5 kW maximális névleges teljesítmény)
    *   **Kommunikációs interfész:** Solarman LSW-3 Wi-Fi Logger (Modbus RTU over TCP protokoll a `8899`-es porton).
*   **Autótöltő (EVSE):** **BESEN BS20-APP-3P16A** (3 fázisú, max 16A / 11 kW okos autótöltő)
    *   **Kommunikációs interfész:** Bluetooth Low Energy (BLE) kapcsolat.
*   **Otthoni Akkumulátor:** Kisfeszültségű (48V) Lítium-Vas-Foszfát (LiFePO4 / LFP) akkupakk (pl. 20-30 kWh kapacitás) az inverterhez csatlakoztatva.
*   **Klímák vezérlése:** 3 régi, nem okos klíma, mindegyik elé egy BroadLink adó: 1 × **BroadLink RM4 Pro** (infravörös + rádiós) és 2 × **BroadLink RM4C Mini** (csak infravörös).
    *   **Kommunikációs interfész:** helyi Wi-Fi (a `broadlink` Python-könyvtárral), felhő nélkül.
*   **Hőmérő:** **Xiaomi Smart Air Purifier 4** légtisztító (modell: `zhimi.airp.mb5`) — hőmérséklet és páratartalom.
    *   **Kommunikációs interfész:** helyi Wi-Fi, a gyártó miIO protokollján (UDP `54321`-es port), az eszköz kulcsával (token).
*   **Árnyékolás:** rádiós (RF) távirányítós redőny és napellenző, a fenti **BroadLink RM4 Pro** rádiós adójával vezérelve.

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
Telepítsd a Python 3.14-et (a Windows-os exe ezzel készül; a korábban használt 3.9 támogatása megszűnt), lehetőleg egy virtuális környezetbe, majd a szükséges csomagokat:
```bash
py -3.14 -m venv .venv314
.venv314\Scripts\python.exe -m pip install bleak pysolarmanv5 pycryptodome broadlink pyinstaller
```
A legutóbb ellenőrzött verziók: `bleak 3.0.2`, `pysolarmanv5 3.0.6`, `pycryptodome 3.23.0`, `broadlink 0.19.0`, `pyinstaller 6.22.3`.

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
.venv314\Scripts\python.exe -m PyInstaller deye_besen_controller.spec --noconfirm
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
Mobil nézetben (keskeny képernyőn) a hagyományos fület-választó helyett egy félig átlátszó, jobb oldali ikondokk jelenik meg, ami a képernyő alsó harmadában, egykezes hüvelykujj-eléréssel kényelmesen elérhető. A 8 ikon jelentése, fentről lefelé:

| Ikon | Jelentés |
|---|---|
| ☀️ (nap) | Auto Solar mód |
| 🕐 (óra) | Ütemezett mód |
| ✋ (kéz) | Kézi mód |
| 📈 (aktivitás) | Mérések |
| 🌡️ (hőmérő) | Klímavezérlés |
| 💧 (csepp) | Bojler |
| ☰ (vízszintes csíkok) | Árnyékolás |
| 📄 (dokumentum) | Napló |

A Kijelentkezés gomb mobilon a fejlécben, egy külön kis ikonként érhető el (csak akkor látszik, ha a webes hitelesítés be van kapcsolva). A gomb akkor is látszik, ha az oldal hibásan vagy üresen töltődik be.

### D) Fejléc: állapotjelvények és fülek
A fejlécben a cím („Otthonvezérlő”, alatta: „Helyi autótöltés és klíma vezérlő.”) mellett két jelvénycsoport látszik:
*   **Kapcsolatok:** Inverter, Töltő, Hőmérő, Klíma1, Klíma2, Klíma3. **Zöld** = a kapcsolat él, **piros** = nem érhető el, **szürke** = nincs beállítva (nincs megadva az eszköz IP-címe a `config.json`-ban). A klímák jelvénye a hozzájuk tartozó BroadLink adó elérhetőségét mutatja.
*   **Automatizmusok:** Auto solar, Auto ütemezett, Klíma, Bojler, Árnyékolás. A jelvény **világít (türkiz)**, ha az adott automatizmus be van kapcsolva, és szürke, ha nincs. A Klíma jelvény akkor világít, ha a Klímavezérlésben az „Automata bekapcsolva” el van mentve; az Árnyékolás akkor, ha legalább egy időzítő aktív. A Bojler egyelőre mindig szürke (később jön).

Alattuk a **fülek**: Autótöltő, Klímavezérlés, Bojler, Árnyékolás, Egyéb. Az **Egyéb** fül csak asztali nézetben érhető el (mobilon nincs ikonja); ide kerülnek azok az eszközök, amelyek máshová nem illenek — jelenleg az internet-rádió (bal hasáb) és a konnektor (jobb hasáb), lásd a 14. fejezetet. Minden fül teljesen külön oldal; asztali nézetben a Napló mindegyik oldal alján megjelenik (mobilon a saját ikonjával érhető el). A Klímavezérlés fülön a Mérések kártya az autótöltős rész nélkül, a „Hőmérő és klímák” sorokkal látszik.

Mobilon a jelvények helyett két rövid állapotsor látszik („Kapcs.:” és „Auto.:” sor, színes pöttyökkel, ugyanazzal a jelentéssel), a fülek helyett pedig az ikondokk.

---

## 6. Működési Módok

A szoftver három fő vezérlési módot kínál, amelyeket a webes felület bal oldali kártyájának tetején lehet kiválasztani:

### 1. Napelemes (Solar Auto) Mód
Ez a teljesen autonóm, "állítsd be és felejtsd el" mód, ami a napelemes felesleg maximális kihasználására törekszik.
*   **Napelemes mód bekapcsolása:** aktiválja a napelemes felesleg-logikát.
*   **Maximális töltőáram (6-16A):** beállítja a maximális töltési sebességet.
*   **Indítási akku szint (%):** az a minimális otthoni akkumulátor-szint, ami alatt a töltés nem indulhat el (ajánlott: `100%`).
*   **Leállítási akku szint (%):** az a minimális otthoni akkumulátor töltöttség (SoC %), ami alatt a napelemes töltés leáll, hogy ne merítse le túlságosan az akkumulátort. A `0%` kikapcsolja ezt a korlátot. **Ha be van kapcsolva** (nem 0), a szerver megköveteli, hogy legalább **2 százalékponttal** alacsonyabb legyen, mint az Indítási akku szint — ez véd az ellen, hogy a két érték egybeesése (pl. mindkettő `99%`) esetén egyetlen SoC-mérési ingadozás azonnali, felesleges indítás/leállítás pattogást okozzon.
*   **Hálózati fogyasztás küszöbérték (W):** az a hálózati import-küszöb (pl. `2000 W`), ami felett elindul a késleltetett leállítás időzítője.
*   **Hálózati töltés késleltetett leállítása (perc):** segít áthidalni az átvonuló felhőket. A rendszer ennyi percig türelmes a hálózati importtal, mielőtt leállítaná a töltést. A `0` érték **azonnali** leállítást jelent, feltéve hogy a hálózati teljesítmény küszöbérték nagyobb, mint `0`.
*   **Ház UPS túlterhelés-védelem (W):** ha az UPS port terhelése meghaladja ezt az értéket, a töltés azonnal leáll (ajánlott: `3000 W` – `5000 W`, az inverter és a kismegszakítók névleges értékétől függően).
*   **Fix Áramkorlát (Nincs dinamikus szabályozás):** a szoftver a beállított fix maximális áramerősséggel indítja el a töltést. Az autó akkumulátorának és töltőelektronikájának védelme érdekében a vezérlő nem szabályozza folyamatosan fel-le a töltőáramot. A hálózati import elkerülését tisztán a BE/KI (Start/Stop) biztonsági limitek végzik.

### 2. Ütemezett (Scheduled) Mód
Lehetővé teszi az olcsó éjszakai áramtarifák vagy meghatározott töltési ablakok kihasználását.
*   **Időzített mód bekapcsolása:** aktiválja a heti ütemezési szabályokat.
*   **Napelemes szabályok futtatása az időablakokon kívül:** ha be van kapcsolva, az időablakokon kívül a rendszer visszaáll a Solar Auto szabályokra (nappal napelemről, éjjel ütemezett hálózati töltés).
*   **Heti ütemezési táblázat:** a hét minden napja egyénileg beállítható, és **naponta tetszőleges számú (max. 8) különálló időintervallum** vehető fel a "+ Töltési idő hozzáadása" gombbal:
    *   Ütemezés be/kikapcsolása (naponta).
    *   Minden időintervallumhoz: kezdő és befejező idő (saját, két számmezős óra:perc beviteli mező — az óra `00`-`24`-ig adható meg, a `24:00` a nap végét jelenti), saját áramkorlát (6-16A), és saját **Solar Auto felülírás** kapcsoló.
    *   **Éjszakai (éjfélen átnyúló) töltés:** két külön, egymást követő napi intervallummal állítható be — pl. a mai nap utolsó intervalluma "22:00"-tól "24:00"-ig, a holnapi nap első intervalluma "00:00"-tól. Egy napon belül az intervallumok nem fedhetik át egymást, de érinthetik egymást (egyik vége = másik kezdete) — ez a folytonos, megszakítás nélküli töltés normál módja; a töltés csak akkor indul újra a határon, ha az áramerősség ténylegesen változik.
    *   **Solar Auto felülírása:** ha be van jelölve egy adott intervallumnál, azon belül a napelemes és akku-leállítási szabályok figyelmen kívül maradnak (garantált éjszakai/időzített töltés).

### 3. Kézi Kényszerített (Force) Mód
Ezzel a móddal felülbírálhatsz minden automatizációt, és manuálisan adhatsz ki Start/Stop parancsokat, és állíthatod be az Amper értéket a csúszkával.
*   **Inverter-kapcsolat nélkül is működik:** a Force mód és a fix áramú ("Solar Auto felülírása" bekapcsolt) ütemezett töltés nem használ inverter-adatot, ezért akkor is elindíthatod és leállíthatod őket, ha a Deye Wi-Fi logger kapcsolata éppen piros. A Solar Auto szabályok viszont továbbra is igénylik az invertert (az akku- és hálózati adatból döntenek), ezért azok addig szüneteltetik a döntéseket. A töltő (BLE) kapcsolata minden módban szükséges.
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
    *   **Automatikus újra-bejelentkeztetés hibás oldalnál:** ha a böngésző a vezérlőoldalt a titkosító kulcs nélkül tölti be (pl. új vagy visszaállított lapon — ilyenkor az oldal üres/hibás maradna, és újratöltés sem segítene), az oldal magától kijelentkeztet, és a bejelentkező oldal jön. Ha ez valamiért nem sikerül (pl. hálózati hiba), a Kijelentkezés gomb akkor is látszik, és kézzel is ki lehet lépni.
    *   Ha nincs szükség hitelesítésre, kikapcsolható a konfigurációban (`"web_auth_enabled": false`).
2.  **Végpontok közötti titkosítás (AES-256-GCM):** a webes műszerfal és a Python szerver közötti kommunikáció beépített, katonai szintű titkosítással védett.
    *   **Challenge-Response bejelentkezés:** a felhasználó jelszava soha nem utazik a hálózaton. A böngésző egy HMAC alapú hitelesítési bizonyítékot (Auth Proof) generál és küld el helyette.
    *   **AES-256-GCM payload titkosítás:** sikeres bejelentkezés után minden API forgalom (parancsok és telemetria) valós időben titkosítva/visszafejtve utazik, egy PBKDF2-SHA256-tal származtatott session kulcs segítségével. Ez megvédi a rendszert a helyi hálózati lehallgatás ellen.
    *   **Session lejárat:** a bejelentkezési session tokenek 24 óra után automatikusan lejárnak, ezt követően újra be kell jelentkezni.
    *   **Feloldás csak bejelentkezve:** a biztonsági zárolás (Lockdown) feloldása (`/api/unlock`) is hitelesített session-t igényel — a helyi hálózaton senki sem tudja feloldani bejelentkezés nélkül.
    *   **Heti ütemezés validáció:** a szerver szigorúan ellenőrzi a beküldött heti ütemezés adatait (napnevek, időformátum, áramerősség-tartomány, napon belüli időintervallum-átfedések), mielőtt elmentené — ez védi a rendszert a hibás vagy rosszindulatú adatoktól.
3.  **Relévédelem (várakozási idők):** sikertelen indítási kísérlet után a program **2 perces (120 másodperces) várakozási időt** kényszerít ki. Ha a Solar Auto egy szabály miatt állítja le a töltést (ház-túlterhelés, leállítási SoC, hálózati import), utána **5 percig nem indít újra** — különben a feltétel megszűnésekor pár másodperc múlva újraindítana, és a ki-be kapcsolgatás zároláshoz vezetne. A kézi indítást ez nem érinti. A várakozási idők **csak az indítást** tiltják: ha közben mégis tölt az autó, a leállítási szabályok tovább futnak.
4.  **Fail-Safe (hibabiztos) leállítás:** ha a töltés nem indul el 60 másodpercen belül egy BLE start parancs után, a rendszer hibát naplóz. Ha ez 3 egymást követő alkalommal megtörténik, a rendszer automatikusan leállítja a további próbálkozásokat, és **Figyelés (Monitoring)** módba vált, hogy elkerülje a végtelen BLE parancsciklusokat.
    *   **A leállítás megerősítése:** STOP után a program a töltő telemetriájából ellenőrzi, hogy a töltés tényleg megállt-e. Ha 60 másodperc múlva is tölt (pl. a parancs egy Bluetooth-hiba miatt elveszett), **újraküldi a STOP-ot, és ezt ismétli, amíg a töltés le nem áll**; minden ismétlés a naplóba kerül.
    *   **Bluetooth-szakadás:** ha a töltő kapcsolata megszakad, a program nem tekinti befejezettnek a saját indítású töltést. Újracsatlakozás után az első telemetriáig nem dönt, utána a töltés továbbra is a sajátjának számít — így az időablak vége és a hálózati import szabály szakadás után is leállítja.
5.  **Hálózati aszinkronizáció és telemetria Watchdog (önjavítás):**
    *   A Deye inverter szinkron Modbus kérései (`pysolarmanv5`) egy külön háttérszálon futnak, így a hálózati fennakadások nem fagyasztják be a fő eseményhurkot.
    *   **Egyetlen inverter-kapcsolat:** a program mindig legfeljebb egy kapcsolatot tart nyitva a Wi-Fi loggerrel (ami csak nagyon kevés párhuzamos kapcsolatot fogad). Kiesés után a régi kapcsolat lezárul, és a program újat épít — elárvult kapcsolatok nem halmozódnak fel, és nem foglalják el a logger szabad helyeit.
    *   **Ritkább lekérdezés töltés közben:** a program alapból 10 másodpercenként kérdezi a loggert, aktív autótöltés közben viszont 20 másodpercenként, mert az inverter csúcsterhelésénél a logger gyakran nem válaszol. Ennek ára, hogy Solar Auto töltés közben a ház-túlterhelés, az alsó SoC és a hálózati import miatti leállítás legfeljebb kb. 10 másodperccel később reagálhat (a töltő saját terheléskezelése ettől függetlenül véd).
    *   **Türelmes várakozás a loggerre:** a program egy-egy válaszra 15 másodpercig vár, mert a logger terhelés alatt gyakran 10–15 másodperc alatt válaszol. Egy lassú, de haladó lekérdezést a Watchdog nem tekint befagyásnak (a lekérdezés minden lépése után életjelet küld), egy valóban beragadtat viszont továbbra is észrevesz.
    *   **Egy késés nem bontja a kapcsolatot:** ha a logger egyszer nem válaszol időben, a kapcsolat megmarad (a lekérdezés sikertelen, az Inverter jelzés erre az egy körre piros), és a következő lekérdezés ugyanazon a kapcsolaton megy. Csak **két egymás utáni** időtúllépés után zárja le és építi újra a kapcsolatot. Más hibáknál (pl. kapcsolati hiba) a kapcsolat továbbra is azonnal újraépül. A késve megérkező régi választ a program felismeri és eldobja.
    *   **Egyetlen kérés lekérdezésenként:** a program az inverter összes szükséges adatát egyetlen kéréssel olvassa ki a korábbi 6 helyett, így a loggert hatodannyi kérés terheli, és egy lekérdezés csak egyszer várakozik válaszra. Ha a logger ezt nem fogadná el, a program magától visszavált a 6 külön kérésre (a naplóban `[INVERTER]` sor jelzi a módot).
    *   Minden Bluetooth írási és értesítési kérés szigorú, 5 másodperces időkorláttal védett, a kapcsolat lezárása pedig 12 másodperces időkorláttal (egy elakadt lezárás naplózódik, és nem blokkolja a programot).
    *   **Kapcsolódási időtúllépés védelem:** a `BleakClient` kapcsolódási kísérletei (`client.connect()`) néha végtelenül beragadhatnak a Windows Bluetooth-verem belsejében. Ennek kezelésére a kapcsolódási kísérletek egy explicit, 20 másodperces aszinkron időkorláttal (`asyncio.wait_for`) vannak becsomagolva. Ha a kapcsolódás tovább tart, megszakad, a socket felszabadul, és új újracsatlakozási ciklus indul.
    *   Ha a kapcsolat állapota `LOGGED_IN`, de 15 másodpercig nem érkezik telemetria csomag a töltőtől, a beépített watchdog időtúllépést naplóz, lezárja a halott kapcsolatot, és tisztán újraindítja a BLE felfedezési és újracsatlakozási folyamatot.
    *   **Szálbiztos telemetria-feldolgozás:** a Bleak háttérszálról érkező értesítések a `main_loop` globális referencián keresztül, `asyncio.run_coroutine_threadsafe` segítségével kerülnek vissza a fő eseményhurok szálára, elkerülve a `RuntimeError: no running event loop` kivételeket.
6.  **Anti-Flapping Cooldown:** megakadályozza a gyors Start/Stop ciklusokat egy 20 másodperces várakozási idő kikényszerítésével 2 egymást követő állapotváltozás után.
7.  **Biztonsági Zárolás (Lockdown):** minden **indítást** letilt, ha 40 másodpercen belül 5 állapotváltozás történik, vagy ha 5 percen belül 10 automatikus parancs fut le emberi beavatkozás nélkül (mozgó időablak — a régebbi, 5 percnél korábbi parancsok nem számítanak bele, így egy hosszú távon zavartalanul futó automatizmus nem gyűjt fel indokolatlanul sok bejegyzést). A műszerfalról manuális feloldást (Unlock) igényel. **Leállítás (STOP) zárolás alatt is mindig kimehet** — a zárolás és a kapcsolási korlátok csak az indítást tiltják, így egy futó töltés zárolva is leáll az időablak végén vagy egy szabály miatt.
8.  **Teljes Ház Terhelésvédelem:** a túlterhelés védelem a `(UPS Terhelés + Töltő Terhelés)` összegét értékeli ki a főmegszakítók védelme érdekében. A leállítások (a túlterhelés miattiak, a manuális Hard STOP és minden más STOP) mindig megkerülik a cooldown/lockdown korlátozásokat.
9.  **Központi Ping-Pong Watchdog (Supervisor):** egy dedikált felügyelő mechanizmus védi a szoftvert a leállásoktól. Kétféle anomáliát kezel automatikusan:
    *   *Összeomlás (Crash) védelem:* ha bármelyik háttérszál váratlan kivétellel leállna, a Watchdog a főprogram összeomlása nélkül elkapja a hibát, és azonnal újraindítja az adott szálat.
    *   *Befagyás (Freeze) védelem:* a szálak ciklikusan életjelet (PONG) hagynak a memóriában. Ha a Watchdog 30 másodpercig nem észlel életjelet egy száltól, erőszakosan leállítja, majd tiszta lappal újraindítja. A webes kiszolgáló szál is a Watchdog felügyelete alatt áll: ha teljesen elhalna, a Watchdog újraindítja; ha él, de befagyott (nem küld PONG-ot), a teljes folyamat kényszerítve újraindul, amit a systemd/`Restart=on-failure` automatikusan felügyel.
10. **Kihúzott Töltőkábel Védelem:** ha a töltőkábel nincs bedugva az autóba (amit a töltő saját BLE-telemetriája jelez), a rendszer sem automata (Solar Auto), sem ütemezett START parancsot nem küld. Induláskor, amíg a valós telemetria meg nem érkezik a töltőtől, a program biztonságosan azt feltételezi, hogy a kábel **nincs** bedugva — csak a töltő tényleges visszajelzése után enged automata parancsot.
11. **Erőforrás-figyelés (fájlleíró-védelem, Linuxon):** a Watchdog figyeli a program nyitott fájlleíróinak (socketek, csövek) számát, és 10 percenként egy összesítő sort ír a naplóba (`[WATCHDOG] Erőforrások: …`). Ha a szám egy szivárgás miatt veszélyesen megnő (700 fölé, a rendszerkorlát 1024), a program kontrolláltan kilép, és a `systemd` automatikusan újraindítja — így egy előre nem látott erőforrás-szivárgás legfeljebb rövid kiesést okoz, nem napokig tartó, észrevétlen leállást.

---

## 8. Konfiguráció és Megmaradó Állapot (Persistence)

A beállítások automatikusan mentésre kerülnek egy helyi `config.json` fájlba, amikor ténylegesen történik mentés (pl. a dashboardon módosítasz valamit, vagy egy töltési munkamenet lezárul). Ha újraindítod a szoftvert (vagy a számítógépet), az automatikusan visszatölti az utolsó beállításokat. **Fontos:** a `config.json` fájl önmagában, pusztán az indítástól **nem** jön létre automatikusan, és a webes felület nem tudja beállítani az inverter IP-t, a sorozatszámot vagy a töltő MAC-címét — ezeket a mellékelt `config_example.json` átmásolásával és kitöltésével (vagy egy meglévő működő `config.json` átvételével) kell megadni.

**Biztonságos mentés:** a program a `config.json`-t úgy menti, hogy egy megszakadt mentés (áramszünet, kényszerleállítás) ne hagyhasson csonka fájlt: előbb ideiglenes fájlba ír, és csak a teljes kiírás után cseréli le a régit. A csere előtti példány **`config.json.bak`** néven megmarad. Ha induláskor a `config.json` sérült vagy hiányzik, a program **az utolsó jó mentést (`config.json.bak`) tölti be**, a sérült fájlt félreteszi (`config.json.corrupt-<dátum>`, nem írja felül), és hibaüzenetet ír a naplóba és a felületre. Ha a `.bak` sem használható, alapértékekkel indul. (Ha a program felhasználója nem hozhat létre fájlt a mappában, a mentés közvetlen írással történik, és erről egyszer figyelmeztet a napló.)

**Hibás űrlap:** ha a felületen egy mentést a szerver elutasít (pl. üres mező, a Start % és a Stop % túl közel van), csak hibaüzenet jön vissza; a futó üzemmód, az automatizmusok és a többi beállítás **nem változik**.

A műszerfal (Dashboard) a következő beállításokat biztosítja:
*   **Indítási SoC (%)** - Amikor eléri, indul a Solar Auto töltés.
*   **Leállítási SoC (%)** - Amikor alá esik, megáll a Solar Auto töltés.
*   **Ház Túlterhelés-védelem (W)** - Ha a Deye UPS terhelése meghaladja ezt (pl. 3000W), a töltés biztonsági okokból leáll.
*   **Max Hálózati Import (W)** - Hálózati türelem-határ. Ha efelett húzunk a hálózatról, leáll a töltés.
*   **Hálózati Import Időkorlát (Perc)** - Mennyi ideig tolerálja a rendszer a fenti hálózati import túllépést, mielőtt leállítaná a töltést (pl. 5 perc, hogy a felhőátvonulásokat átvészelje).
*   **Üzemmód Megjegyzése Újraindításkor** - Kapcsoló, amivel a vezérlő emlékszik a legutóbb használt módra (Auto/Schedule/Force).
*   **Klímavezérlés, Árnyékolás és az app eszközei:** a klímák beállításai (11. fejezet), az árnyékolás időzítői (12. fejezet), a konnektor és az internet-rádió időzítője (14. fejezet) és a felületen megtanított infravörös/rádiós kódok is a `config.json`-ba mentődnek (`"climate"`, `"shading"` és `"home_devices"` blokk), így áramszünet vagy újraindítás után visszatöltődnek. Az eszközök **IP-címét és a légtisztító kulcsát (token) viszont csak a `config.json`-ban lehet megadni**, a felületen nem — mintája a `config_example.json`-ban található.
*   *Rejtett haladó beállítás (csak a `config.json`-ban módosítható)*: `"pbkdf2_iterations"` - A jelszó titkosítás erőssége (alapértelmezett: 100000). Gyengébb mikroszámítógépeken (pl. Raspberry Pi Zero) érdemes lehet csökkenteni (pl. 50000-re) a gyorsabb bejelentkezés érdekében. Ez az érték szabadon módosítható: a webes felület és az `AndroidWidget` mappában található widget is dinamikusan lekérdezi az aktuális beállítást a szervertől bejelentkezéskor, nem kell hozzájuk illeszteni a kliens oldalt.

---

## 9. Hibakeresés és Műszerfal

A műszerfalon található egy beépített "Konzol" és "Hibadobozok", amelyek valós idejű visszajelzést adnak:
*   **Sárga Figyelmeztetés:** Lehűlési (Cooldown) időzítő aktív (megakadályozza, hogy a Bluetooth parancsok túl gyorsan spammeljék a töltőt).
*   **Piros Hiba:** Kapcsolódási problémák az Inverterrel (Modbus) vagy a Töltővel (BLE). Piros inverter esetén a Solar Auto döntések szünetelnek, de a Force és a fix áramú ütemezett töltés működik; piros töltő esetén semmilyen töltésvezérlés nem működik.
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

*   A widget **Wi-Fin, vagy Wi-Fi nélkül aktív VPN-en (pl. Tailscale) keresztül frissül**, ha a szerver elérhető. Wi-Fin kb. 5 másodpercenként, Wi-Fi nélkül VPN-en át (mobilneten) a mobiladat kímélése miatt 30 másodpercenként. Mobiladaton VPN nélkül üresen (átlátszón) marad. Otthonon kívüli eléréshez a widget beállításában a szerver címe helyett a NAS VPN-es (pl. Tailscale 100.x.y.z) címét kell megadni; hazatérve (Wi-Fin) azonnal visszaáll az 5 másodperces ütem.
*   A frissítés a képernyő bekapcsolt állapotában, kb. 5 másodpercenként történik (lezárt telefonon energiatakarékosságból szünetel).
*   A widgetre **koppintva** azonnali kézi frissítés kényszeríthető; a koppintás közben a számok a képernyőn maradnak.
*   A widget ellenálló a **WiFi-hálózatok közötti váltásra**: ha elhagyod a saját hálózatod hatósugarát, majd visszatérsz, az adatok néhány másodpercen belül maguktól helyreállnak.

### Biztonsági jegyzet

A widget a dashboard-jelszót helyben, a telefon privát tárterületén (`SharedPreferences`) őrzi. Az alkalmazás `android:allowBackup="false"` beállítással tiltja az Android-mentést, hogy a jelszó ne legyen kinyerhető `adb backup`-pal. A widget és a szerver közötti kommunikáció végponttól végpontig titkosított (AES-256 + HMAC), a webes felülettel azonos módon.

---

## 11. Klímavezérlés

A **Klímavezérlés** fülön 3 régi, nem okos klíma kezelhető a távirányítójuk infravörös jelével, BroadLink adókon keresztül. Hőmérőként egy Xiaomi légtisztító szolgál (hőmérséklet; a páratartalom csak kijelzés). A cél: napelemes **visszatáplálás** idején a klímák a felesleges energiából fűtsenek (télen) vagy hűtsenek (nyáron).

**Jelenlegi állapot:** az eszközkapcsolat, a kódok tanítása, a kézi próba és a beállítások mentése működik. **Az automata még nem kapcsol semmit** — ez a következő fejlesztési lépésben jön (lásd lent, „Tervezett működés”).

### Előfeltételek (egyszeri beállítás)
1.  **BroadLink adók:** add hozzá őket a BroadLink alkalmazásban a Wi-Fi-hálózathoz, majd az eszköz beállításainál **kapcsold ki a „Lock device” opciót** (különben a program nem tud velük beszélni). Adj nekik **fix IP-címet** a routerben.
2.  **Légtisztító:** szükség van a **kulcsára (token)**, ezt pl. a „Xiaomi Cloud Tokens Extractor” programmal lehet kiolvasni a Xiaomi-fiókból. Ha a légtisztítót később újra párosítod, a kulcs megváltozik. Ennek is adj **fix IP-címet**.
3.  **Hálózat:** a vezérlőt futtató gép és az összes eszköz **ugyanazon a helyi hálózaton** legyen — vendéghálózatról az eszközök nem érhetők el.
4.  **`config.json`:** írd be az IP-címeket és a kulcsot (a felületen ezek nem adhatók meg). Példa (a számok csak minták):
    ```json
    "climate": {
        "sensor": {"ip": "192.168.0.50", "token": "<32 karakteres kulcs>"},
        "units": [
            {"name": "Nappali", "broadlink_ip": "192.168.0.51"},
            {"name": "Háló", "broadlink_ip": "192.168.0.52"},
            {"name": "Dolgozó", "broadlink_ip": "192.168.0.53"}
        ]
    }
    ```
    A `name` a helyiség neve (a felületen a „Klíma1–3” jelölés mellett látszik). A többi mezőt (kódok, beállítások) a program tölti ki a felületről.

### Mérések: „Hőmérő és klímák”
A Klímavezérlés fül Mérések kártyáján (az autótöltős rész nélkül) látszik a légtisztító állapota (hőmérséklet, páratartalom, utolsó frissítés), és klímánként, hogy mely kódok vannak már megtanítva (Fűtés BE / Hűtés BE / KI). A program **percenként** kiolvassa a légtisztítót és ellenőrzi a BroadLink adók elérhetőségét; ennek eredménye a fejléc Hőmérő és Klíma1–3 jelvényén látszik, a változásokat a Napló is rögzíti.

### Kódok tanítása és kézi próba
A Klímavezérlés kártya alján a becsukható **„Eszközök (tanítás, próba)”** rész tartalmazza klímánként a gombokat. Minden klímához három kód kell: **fűtés BE**, **hűtés BE** és **KI** (a klímák külön BE és KI kódot használnak, nem ki-be kapcsolót).
1.  Állítsd be a távirányítón a kívánt állapotot (pl. fűtés, a kívánt hőfokkal).
2.  Nyomd meg a megfelelő **„Tanítás …”** gombot, majd 30 másodpercen belül nyomd meg a távirányító gombját a BroadLink adó felé fordítva.
3.  Siker esetén a kód a `config.json`-ba mentődik, és a ✓ jel megjelenik. A **„Próba …”** gombbal kipróbálhatod.

### Beállítások
A Klímavezérlés kártya tetején adhatók meg az automata beállításai. A **„Beállítások mentése”** gomb csak akkor ment, ha minden mező ki van töltve és érvényes — hibás vagy üres mezőnél semmi nem változik, és a program kiírja, melyik mezővel van gond.
*   **Fűtés / Hűtés:** az üzemmód; ez dönti el, melyik BE kód megy ki.
*   **Automata bekapcsolva:** az automata be- vagy kikapcsolása (a fejléc Klíma jelvénye ezt mutatja).
*   **Akkuszint (%)**, **Célhőmérséklet (°C**, tizedes pontossággal, pl. `22.1`), **Bekapcsolási idő (perc)**, **Kikapcsolási idő (perc)**.
*   **Klímánként:**
    *   **Visszatermelés a bekapcsoláshoz (W):** az a visszatermelési **szint**, amelynél ez a klíma is menjen. Példa: `1500`, `2000`, `2500` W = ennyi visszatermelésnél akarok 1, 2, illetve 3 klímát használni. **A bekapcsolási sorrendet ezek a számok adják:** a legalacsonyabb küszöbű klíma indul először (egyenlő küszöbnél a kisebb sorszámú), a leállítás fordított sorrendben megy.
    *   **Várható fogyasztás (W):** mennyit fogyaszt az adott klíma működés közben (lásd lent, miért kell).
*   **Magyarázó sor:** minden klíma sora alatt a program kiírja, hogyan számol a gyakorlatban, gépelés közben is frissítve, pl. *„Bekapcsol, ha a visszatermelés + 1. klíma várható fogyasztása (600 W) eléri a 2000 W-ot.”* Ha a küszöböket átírod, a szövegek a küszöbök szerinti sorrendhez igazodnak.

### Tervezett működés (a következő fejlesztési lépés, még nem működik)
*   **Számolt termelés:** a mért visszatáplálás és a már futó klímák várható fogyasztásának összege. A hálózati mérőn a visszatáplálás negatív érték (pl. `−1500 W`), ezért a program a visszatáplálást a mérő értékének ellentettjeként számolja: exportnál pozitív, hálózati vételezésnél negatív szám. Így egy futó klíma a saját fogyasztása miatt nem kapcsol le, mert a fogyasztása visszaadódik a számításban.
*   **Bekapcsolás:** a sorban következő klíma bekapcsol, ha megfelelő az akkuszint, a számolt termelés a „Bekapcsolási idő” percig eléri a klíma küszöbét, és a hőmérséklet megfelelő: **fűtésnél a mért hőmérséklet ≤ célhőmérséklet, hűtésnél ≥ célhőmérséklet** (tizedes pontossággal, minden klímánál ugyanaz a célérték). A hőmérséklet csak bekapcsoláskor számít; utána a klímák saját hőmérője szabályoz.
*   **Kikapcsolás:** a legutóbb bekapcsolt klíma leáll, ha a számolt termelés a „Kikapcsolási idő” percig a küszöbe alatt marad; utána a következő ugyanígy.
*   **Példa** (küszöbök 1500 / 2000 W, várható fogyasztás 600 / 700 W):
    *   Nem megy klíma, a mérő `−1500 W` → számolt termelés 1500 → az 1. klíma bekapcsol.
    *   Az 1. klíma megy, a mérő `−1400 W` → 1400 + 600 = 2000 → a 2. klíma is bekapcsol (ha a hőmérséklet még mindig megfelelő).
    *   Felhő: a mérő `+200 W` (vételezés) → −200 + 600 + 700 = 1100 < 2000 → a 2. klíma leáll; utána a mérő kb. `−500 W` → 500 + 600 = 1100 < 1500 → az 1. klíma is leáll (mindkettő a türelmi idő után).

---

## 12. Árnyékolás

Az **Árnyékolás** fülön egy rádiós távirányítós **redőny** és **napellenző** vezérelhető a BroadLink RM4 Pro rádiós adójával, napi időzítővel.

### Beállítás
*   A `config.json`-ban add meg az RM4 Pro IP-címét: `"shading": {"broadlink_ip": "192.168.0.51", ...}` (a szám csak minta; ugyanaz az RM4 Pro lehet, amelyik egy klímát is vezérel). A többi mezőt a program tölti ki a felületről.
*   **Rádiós kódok tanítása** az oldal alján, a becsukható **„Eszközök (RF-tanítás, próba)”** részben, két lépésben:
    1.  **Tartsd nyomva** a távirányító gombját a BroadLink felé fordítva, amíg a felület a 2. lépést nem mutatja (a BroadLink ekkor megkeresi a távirányító frekvenciáját).
    2.  Engedd el, majd **egyszer** nyomd meg a gombot.
    
    Mindkét eszközhöz két kód kell: a redőnynél **Fel** és **Le**, a napellenzőnél **Be (Fel)** és **Ki (Le)**. A **„Próba …”** gombokkal kipróbálhatók.

### Időzítő
*   A **Redőny** és a **Napellenző** kártyán a hét minden napjára külön megadható egy **Fel** és egy **Le** időpont. Az **üres mező** azt jelenti, hogy aznap nincs mozgás.
*   Az **„Időzítő aktív”** kapcsolóval a két eszköz időzítője egymástól függetlenül kikapcsolható.
*   A program a megadott percben **egyszer** küldi el a jelet. Ha a program abban a percben nem fut (pl. áramszünet), az a mozgás kimarad, utólag nem pótolja.
*   A beállítás a `config.json`-ba mentődik, és újraindítás vagy áramszünet után visszatöltődik. A fejléc Árnyékolás jelvénye akkor világít, ha legalább egy időzítő aktív.

---

## 13. Bojler

A **Bojler** fül és a fejléc Bojler jelvénye jelenleg csak helyőrző: a vezérlése később készül el. Addig a jelvény szürke, az oldalon csak egy „Később.” feliratú kártya és (asztali nézetben) a Napló látszik.

---

## 14. Otthonvezérlő app (Android), LED-szalag, konnektor és internet-rádió

Az Android widget alkalmazása (ugyanaz az APK) egy **„Otthonvezérlő” indítóikont** is kap: egy egyszerűsített, a Xiaomi Home-ra hasonlító alkalmazást az alapfunkciókhoz. A részletes beállítások maradnak a webes felületen. Otthonon kívül a Tailscale-lel működik (lásd a távoli elérésnél), mobilneten is.

### Első indítás
Az app a szerver címét és a jelszót kéri — ugyanaz a tárolt beállítás, mint a widgeté (bármelyikben módosítod, mindkettő azt használja). Címnek otthon a NAS helyi IP-je, otthonon kívüli használathoz a NAS Tailscale-címe (100.x.y.z) adható meg. A beállítás később a főképernyő fogaskerék-ikonjával érhető el.

### Főképernyő
*   **Energia-csempék** kis ikonokkal: napelem, hálózat, akku, ház — ugyanazzal a színkóddal, mint a webes Mérések kártyán (hálózat zöld = visszatáplálás, piros = vételezés; akku zöld = töltődik, piros = merül).
*   **Autótöltő:** a mód, töltés közben az aktuális teljesítmény **pirossal**; **Indítás** (= a webes „Kézi indítás”) és **Leállítás** (= a webes „Ideiglenes leállítás”, soft stop). Ha a Solar Auto vagy az Ütemezett automatizmus be van kapcsolva, **az egész csempe lüktet**.
*   **Klímák:** hőmérséklet és páratartalom (a légtisztítóé), „Utoljára: …” (az utoljára küldött parancs és ideje — az infra egyirányú, a klíma valódi állapotát a program nem tudja), **Fűtés BE / Hűtés BE / KI** gombok. Ha a klíma-automata be van kapcsolva, a csempék lüktetnek.
*   **Redőny, napellenző, LED-szalag, konnektor, internet-rádió:** a csempére bárhol koppintva (vagy a nyíllal ›) a részletek nyílnak; **kapcsolni csak a csempe gombjaival lehet**, így a részletek megnyitása nem kapcsol véletlenül. Egy gomb a saját művelete alatt nem indít újabbat (lassú kapcsolaton a dupla koppintás egy parancsot küld).
*   **Időzítők mentése:** a részletképernyőkön a **Mentés** gomb halvány, amíg az időzítő be nem töltődött a szerverről, és addig nem ment — így egy üres űrlap nem törölheti a szerveren lévő időzítőt.
*   **Redőny, napellenző:** Fel/Le (Be/Ki) gombok; a részleteken a heti időzítő (ugyanaz, mint a webes Árnyékolás oldalon).
*   **LED-szalag, konnektor:** **Be** és **Ki** gomb; a részleteken LED: fényerő, szín; konnektor: napi időzítő két be-ki párral.
*   **Internet-rádió:** „Utoljára: power …” (az utoljára küldött gombnyomás ideje) és az időzítő állapota; a **„Be/Ki (power)”** gomb egy power gombnyomást küld; a részleteken a napi időzítő.
*   Amíg az app előtérben van, 2 másodpercenként frissül (mint a webes felület); háttérben nem kérdez.
*   A megjelenés a telefon beállítását követi: világos vagy **sötét mód**.

### LED-szalag (CozyLife)
*   **Helyben, felhő nélkül** vezérelhető (a szalag a 5555-ös TCP-porton fogad parancsokat). **Kulcs vagy jelszó nincs**, a helyi hálózaton bárki kapcsolhatja.
*   Adj neki fix IP-t a routerben, és írd a `config.json`-ba (lásd lent). A CozyLife alkalmazás ezután nem kell hozzá.

### Xiaomi okos konnektor
*   Helyben, a légtisztítóéval azonos protokollon (miIO) vezérelhető. Kell hozzá a **kulcsa (token)** és a **típusazonosítója** (pl. `chuangmi.plug.hmi206`) — mindkettőt ugyanaz a „Xiaomi Cloud Tokens Extractor” írja ki, mint a légtisztítónál. Fix IP a routerben.
*   **Webes felület:** az asztali nézet **Egyéb** fülén (jobb hasáb) is látszik: állapot (be/ki, elérhető-e), **Bekapcsolás** és **Kikapcsolás** gomb, alatta az időzítő.
*   **Napi időzítő, két be-ki párral** (pl. lámpa reggel és este): a hét minden napjára **1. Be / 1. Ki / 2. Be / 2. Ki** időpont (üres = aznap abban a párban nincs kapcsolás), „Időzítő aktív” kapcsolóval. Az appban és a weben ugyanaz az időzítő; az appban a napok rövid névvel (H, K, Sze…), egy sorban látszanak. A program a megadott percben **egyszer** kapcsol; ha abban a percben nem fut, a kapcsolás kimarad (nincs pótlás). A beállítás a `config.json`-ba mentődik; egy régi, egy páros beállítás az 1. párba kerül.
*   A két páros időzítőt csak az új app tudja menteni: a régi app (egy párral) mentését a program elutasítja, hogy ne törölje a 2. párt — frissítsd az appot.

### Internet-rádió (infra, BroadLink)
*   A rádió infravörös távirányítóját egy meglévő BroadLink adó utánozza (pl. a nappali klímáé). A távirányítón **egyetlen power gomb** van, ezért **egy kódot** kell megtanítani: a „Be” és a „Ki” is ugyanazt a gombnyomást küldi, a rádió az ellenkező állapotába vált. Az infra egyirányú, a rádió valódi állapotát a program nem tudja — ha épp nem abban az állapotban van, amire számítasz, a gombnyomás az ellenkezőjére kapcsolja.
*   **Tanítás és próba a webes felületen**, az **Egyéb** fülön (csak asztali nézetben): **„Tanítás (power)”**, majd 30 másodpercen belül nyomd meg a távirányító power gombját a BroadLink felé fordítva; a **„Próba (power)”** gombbal kipróbálható. A kártyán látszik, hogy a kód megvan-e, és mikor ment ki utoljára gombnyomás.
*   **Napi időzítő** a webes Egyéb fülön és az appban is (ugyanaz az időzítő): a hét minden napjára egy Be és egy Ki időpont (üres = aznap nincs kapcsolás), „Időzítő aktív” kapcsolóval. Mindkét időpontban a power gombnyomás megy ki, a megadott percben **egyszer** (nincs pótlás).
*   Ha ugyanazt a BroadLinket egy klíma is használja, és pont egyszerre megy ki egy klíma- és egy rádióparancs, az egyik ritkán hibát adhat; a felület ezt kiírja, és újra lehet próbálni.

### `config.json`
```json
"home_devices": {
    "led":   {"name": "LED-szalag", "ip": "192.168.0.60"},
    "plug":  {"name": "Konnektor", "ip": "192.168.0.61", "token": "<32 karakteres kulcs>", "model": "<típusazonosító>"},
    "radio": {"name": "Internet-rádió", "broadlink_ip": "192.168.0.62"}
}
```
(A számok csak minták. Az időzítőket és a rádió megtanított kódját a program tölti ki.) A program **percenként** lekérdezi a LED-szalag és a konnektor állapotát a helyi hálózaton, és parancs után azonnal is, így az app akkor is friss állapotot mutat, ha a fali gombbal vagy máshonnan kapcsoltad őket. A LED-szalag csak az appban jelenik meg; a konnektor és a rádió a webes Egyéb fülön is.

---

## Köszönetnyilvánítás

Külön köszönet a [slespersen/evseMQTT](https://github.com/slespersen/evseMQTT) GitHub projektnek! A BESEN BS20 töltő Bluetooth Low Energy (BLE) protokolljának visszafejtésével és implementálásával kapcsolatos munkája szilárd alapot adott a vezérlő BLE kommunikációjához. Külön köszönet az AI-alapú páros programozó asszisztensnek a kód refaktorálásáért, az aszinkron vezérlő- és szimulációs hurkok megalkotásáért, a biztonsági védelmek beépítéséért, a prémium glassmorphic webes műszerfal kifejlesztéséért, és a teljes kétnyelvű dokumentáció összeállításáért.

---

## Felelősség kizárása és hibajelentés

Ez a szoftver hálózati/hardver szinten (Modbus/BLE) lép kapcsolatba valódi elektromos berendezésekkel. Bár a fentiekben leírt több rétegű biztonsági mechanizmus védi a rendszert, a felhasználó saját felelősségére használja — a helyi hálózat és elektromos rendszer fizikai teherbírását mindig figyelembe kell venni a konfiguráció beállításakor.

Ha logikai hibát, váratlan viselkedést vagy hibás működést tapasztalsz használat közben, kérjük **jelezd a repository GitHub Issues szakaszában**, hogy javíthassuk! Köszönjük szépen a visszajelzést!
