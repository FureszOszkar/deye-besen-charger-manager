# Deye & BESEN Integrált Töltésvezérlő Rendszer
## Rendszerdokumentáció és Felhasználói Kézikönyv

Ez a szoftver egy helyi hálózaton (offline) futó integrált vezérlőmegoldás, amely összekapcsolja a **Deye háromfázisú hibrid invertert** és a **BESEN BS20 okos autótöltőt (EVSE)**. A szoftver célja a napelemes energiatermelés és a háztartási akkumulátor állapotának figyelembevételével az elektromos autó töltésének teljesen automata, intelligens és biztonságos vezérlése.

---

## 1. Hardveres Típusok és Specifikációk

A szoftver az alábbi konkrét hardveres környezetben lett kifejlesztve és tesztelve:

*   **Hibrid Inverter:** **Deye 5 kW-os hibrid inverter** (pl. SUN-5K-SG széria, 5 kW maximális névleges teljesítménnyel)
*   **Kommunikációs interfész:** Solarman LSW-3 Wi-Fi Logger (Modbus RTU over TCP protokollal a `8899`-es porton).
*   **Autótöltő (EVSE):** **BESEN BS20-APP-3P16A** (3-fázisú, maximum 16A / 11 kW teljesítményű okos autótöltő)
*   **Kommunikációs interfész:** Bluetooth Low Energy (BLE) kapcsolat.
*   **Háztartási Akkumulátor:** Inverterhez kapcsolt alacsony feszültségű (48V) Lithium Iron Phosphate (LiFePO4 / LFP) akkumulátorpakk (pl. 20-30 kWh kapacitással).

---

## 2. Speciális Helyi Fizikai Feltételek és Követelmények

A Bluetooth Low Energy (BLE) és a helyi Wi-Fi hálózat stabilitása kritikus fontosságú a rendszer folyamatos, felügyelet nélküli működéséhez. A következő speciális hardveres feltételek biztosítása szükséges:

### A) Erősített USB Bluetooth (BT) Antenna / Adapter
A BESEN töltő gyári Bluetooth chipje korlátozott hatótávolsággal rendelkezik. A vezérlőszoftvert futtató számítógépbe **kötelező egy külső, nyereséges antennával (High-Gain Antenna) ellátott USB Bluetooth 5.0 (vagy újabb) adaptert** csatlakoztatni (a rendszer sikeresen tesztelve és üzemel a **Mercusys MA550H Long Range Bluetooth 5.4** adapterrel). A beépített alaplapi BT chipek vagy a kisméretű (dongle) vevők nem képesek stabil kapcsolatot fenntartani az épületen kívül elhelyezett autótöltővel.

*   **Időbélyeg (Timestamp) korrekció:** A BESEN töltő vezérlőegysége (MCU) ellenőrzi az indító parancsban lévő Unix időbélyeget a belső órájához (RTC) képest. Ha túl nagy az eltérés (pl. a budapesti időzóna és a sanghaji időzóna különbsége), elutasítja az indítást. Ennek elkerülésére a `get_shanghai_timestamp()` függvény a helyi időt +8 órás eltolással (Shanghai időzóna szerint) Unix timestamp-pé alakítja. Ezt a módosított időbélyeget használjuk a START parancsok kiküldésekor.

### Alternatív Megoldás: Mikro-számítógép (pl. Raspberry Pi) a töltő közelében
Ha a vezérlést futtató fő számítógép túl messze van, a drága külső antenna helyett kiváló alternatíva egy olcsó, Wi-Fi és Bluetooth képes mikro-számítógép (pl. **Raspberry Pi Zero 2 W, Raspberry Pi 3, 4 vagy 5**) elhelyezése a töltő közvetlen közelében (pl. a garázsban). 
Mivel a szoftver erőforrás-igénye minimális, a teljes vezérlő futtatható ezen a közeli gépen is. Ebben az elrendezésben a mikro-számítógép stabil, rövid távú Bluetooth kapcsolaton éri el a töltőt, míg az inverterrel és a helyi hálózattal a ház Wi-Fi hálózatán keresztül kommunikál.

### B) Közvetlen Rálátás (Line of Sight) a Töltőre
Az USB BT antenna és a BESEN autótöltő között a lehető legtisztább fizikai rálátást kell biztosítani. 
*   A vastag betonfalak, fémlemez burkolatok és maga a töltésre váró jármű is jelentős BLE árnyékolást okozhat.
*   A szakadozó Bluetooth jel a telemetriai adatok kimaradásához, végső soron a biztonsági leállások aktiválásához vezethet. Az antennát úgy kell elhelyezni (pl. ablak közelében), hogy a fizikai akadályok száma minimális legyen.

### C) Wi-Fi Lefedettség a Deye Inverter LSW-3 egységénél
A Deye inverter Wi-Fi stickjének folyamatos hálózati jelenléte szükséges. Győződj meg róla, hogy a helyi router 2.4 GHz-es jele stabilan és jelerősen eléri az inverter elhelyezési pontját.

---

## 3. Inverteres Akkumulátor-szabályozás és UPS Tápellátás

A vezérlőszoftver szorosan együttműködik a Deye inverteren beállított belső akkumulátor-kezelési logikával (Time-of-Use beállítások, töltési/kisütési prioritások):

**Napelemes Prioritás:** A Deye inverter belső szabályozása első sorban a ház közvetlen fogyasztóit látja el energiával, második lépcsőben a háztartási akkumulátort tölti, és csak harmadik körben táplálja vissza a felesleges energiát a hálózatba (Grid).

**Akkumulátor Védelem és indítás:** A vezérlőprogram figyeli a házi akkumulátor töltöttségi szintjét (SoC %). A `start_soc` paraméter segítségével garantálható, hogy az autó töltése csak akkor indul el, ha a háztartási akkumulátor már teljesen feltöltődött. Ezzel elkerülhető, hogy az autó idő előtt lemerítse az otthoni akkumulátort, miközben a napelem még nem termelt elegendő felesleget.

**Kritikus környezeti feltétel – Teljes ház az UPS (Backup) ágon, autótöltő a Grid (hálózati) ágon:**
* A speciális kiépítés miatt a teljes lakás az inverter UPS (tartalék) ágán helyezkedik el, de CSAK a ház. Az autótöltő (EVSE) nem a házon keresztül kapja a tápellátást, hanem közvetlenül a villanyóra mellől, az inverter előtt (a hálózati / Grid oldalon).
* Mivel a ház az UPS ágon van, így a ház teljes fogyasztása az inverter belső elektronikáján folyik keresztül, amelynek a fizikai és hardveres korlátja szigorúan 5 kW.
* Ha a ház saját fogyasztása (pl. hőszivattyú, mosógép, sütő) megközelíti vagy átlépi ezt az 5 kW-os inverter-határt, az inverter túlterhelés miatt leállhat, ami azonnali és teljes áramszünetet (blackoutot) okoz a házban.
* Emiatt a programban beállítható Ház UPS túlterhelés-védelem (`house_power_limit_w`) funkció elengedhetetlen. A vezérlő folyamatosan figyeli az invertertől érkező UPS terhelést (`ups_load_power`), és ha az átlépi a biztonsági szintet (pl. 4000 W), azonnal leállítja az autótöltést, tehermentesítve a teljes rendszert és megelőzve a lakás sötétbe borulását.
* Számítási következmény: Mivel az autótöltő nem az UPS ágon van, a fogyasztása a közműmérő (külső CT) és az inverter saját hálózati mérőjének különbségeként jelenik meg. A kezelőfelületen ez a „Nem UPS ágon lévő fogyasztók” mezőben látható, ami az autótöltő mellett az esetleges egyéb nem UPS ágon lévő külső fogyasztást is tartalmazza.

---

## 4. Felhasználói Felület és Műszerfal Útmutató

A webes felület a `http://localhost:8080` (vagy `http://127.0.0.1:8080`) címen érhető el a futtató gépről. A helyi hálózati IP-címekkel és porttal is elérhető (pl. `http://192.168.0.8:8080`). A felület prémium, áttetsző sötétszürke glassmorphic dizájnt kapott, amely mögött a gyökérkönyvtárba helyezett `background.png` háttérkép stílusosan átsejlik.

### A) Színkódolt Mérések (Áramirány Jelzése)
A kezelőfelület jobb oldalán lévő **Mérések & Visszacsatolás** kártyán a legfontosabb teljesítményadatok színkódolása a következő:
*   **Hálózati egyenleg (Grid):**
    *   **ZÖLD (Negatív érték):** Visszatáplálás / Túltermelés történik a hálózat felé (ingyen napelemes energia van).
    *   **PIROS (Pozitív érték):** Hálózati fogyasztás / Vásárolt áram történik (fizetős energia).
*   **Akkumulátor teljesítmény:**
    *   **ZÖLD (Pozitív érték):** Az akkumulátor éppen **töltődik** a napelemről.
    *   **PIROS (Negatív érték):** Az akkumulátor éppen **merül** (energiát ad le a háznak).
*   **PV, Ház UPS, és a Nem UPS ágon lévő fogyasztók teljesítménye:** Fehér színnel jelenik meg a tiszta olvashatóság érdekében.

A szoftverben a Solar Auto szabályok (Hálózati import korlát, Akkumulátor leállítási SoC, Ház UPS túlzott terhelés védelme) egymást követően és függetlenül kerülnek kiértékelésre (soros szabályok). A "Hálózati töltés késleltetett leállítása (perc)" beállítás 0 perc értékére állításának hatására azonnali leállás történik (0 perces késleltetés) a vizsgálat letiltása helyett. A vizsgálat akkor aktív, ha a hálózati energia küszöbértéke nagyobb 0-nál. Az HTML beviteli lépésközei a Watt paraméterek esetén 1-re állnak, lehetővé téve az egyes Watt felbontású beállításokat (pl. 80 W).

### C) Élő Töltési Teljesítmény és Energia Korrekció
*   **Töltési teljesítmény panel:** A fázis táblázat mellett elhelyezett önálló, kompakt kijelző valós időben mutatja az autóba táplált összesített elektromos teljesítményt kilowattban (kW), amelyet a fázis feszültségek és áramok szorzatainak összegeként számol ki a kliensoldali felület: `(V1*I1 + V2*I2 + V3*I3) / 1000`. Tétlen állapotban a kijelző `0.00 kW` értéket mutat.
*   **Töltési energia összesen:** A BESEN töltő gyári telemetria regisztere fázisonként (az L1 fázisra) számolja az átvitt energiát. 3-fázisú töltés esetén (amikor áram folyik az L2 vagy L3 fázison is) a szoftver automatikusan 3-as szorzót alkalmaz a telemetria értékre, hogy a műszerfalon a tényleges, valós betöltött energia (kWh) jelenjen meg.

---

## 5. Rendszer Üzemmódok Használata

A vezérlő három fő üzemmódot kínál, amelyek között a bal oldali konfigurációs panel tetején válthatsz:

### 1. Auto (Solar Auto) Üzemmód
A napelemes felesleget maximalizáló intelligens üzemmód.
*   **Napelemes mód bekapcsolása:** A checkbox-szal aktiválhatod a Solar szabályozást.
*   **Maximális töltőáram (6-16A):** Meghatározza a legnagyobb töltőáramot. Ha a „Szoftveres szabályzás kikapcsolása” be van jelölve, az autó a saját fizikai korlátjával (vagy a töltőn beállított maximummal) fog tölteni.
*   **Indítási akku szint (SoC %):** A minimális házi akku szint, ami alatt a töltés nem indulhat el (ajánlott: `100%`).
*   **Leállítási akku szint (SoC %):** Az a minimális házi akkumulátor töltöttségi szint (pl. `20%`), amely alá esve a töltés azonnal leáll, hogy megóvja a háztartási akkumulátort a túlzott lemerüléstől (0% esetén a szabály inaktív).
*   **Hálózati fogyasztás küszöbérték (W):** Azt a hálózati import korlátot adja meg (pl. `2000 W`), ami felett a hálózati töltés késleltetett leállítása elindul.
*   **Hálózati töltés késleltetett leállítása (perc):** Felhőátvonulások áthidalására szolgál. A program ennyi percig engedi még a hálózati importot a leállítás előtt (ha `0`, azonnal leáll, feltéve, hogy a hálózati fogyasztás küszöbérték > 0).
*   **Ház UPS túlterhelés-védelem (W):** Ha az UPS kimenet terhelése ezt átlépi, a töltés azonnal leáll (ajánlott: `3000 W` - `5000 W` az inverter és a kismegszakítók méretétől függően).
*   **Solar Auto szabályok:** A Solar Auto szabályok (Hálózati import korlát, Akku leállítási SoC, Ház UPS túlterhelés-védelem) egymás után és függetlenül kerülnek kiértékelésre.
*   **HTML input lépésköze:** A Watt paraméterekhez tartozó HTML input lépésköz `1` W, így lehetőség van egyesével (pl. 80 W) beállítani a szabályokat.

### 2. Ütemezett (Naptár szerinti) Üzemmód
Időalapú töltésvezérlés heti bontásban.
*   **Időzített mód bekapcsolása:** Aktiválja a heti naptár szerinti működést.
*   **Napelemes szabályok futtatása az időablakokon kívül:** Ha be van jelölve, akkor az ütemezett időablakokon kívüli időszakokban a Solar Auto szabályai lépnek érvénybe (így napközben napelemmel tölt, éjszaka pedig az ütemezett olcsó árammal).
*   **Heti naptár táblázat:** Minden napra egyedileg megadható:
    *   Aktív-e az időzítés az adott napon.
    *   Kezdési és leállítási időpont (ÓÓ:PP).
    *   Töltőáram korlát (6-16A).
    *   **Solar Auto felülírása (Prioritás):** Ha be van jelölve, akkor ebben az időablakban a napelemes és akkumulátoros leállítási szabályok felülírásra kerülnek (biztosított töltés).

A szoftverben a következő változtatások lettek megvalósítva:
1. A Solar Auto szabályok (Hálózati import korlát, Akkumulátor leállítási SoC, Ház UPS túlzott terhelés védelme) egymást követően és függetlenül kerülnek értékelésre.
2. A "Hálózati töltés késleltetett leállítása (perc)" beállítás 0 perc értékével IMMEDIÁTUS leállítást jelent, nem pedig letiltja a vizsgálatot. A vizsgálat akkor aktív, ha a hálózati áram küszöbértéke > 0.
3. Az HTML beviteli lépésközei a Watt paraméterek esetén 1-re változtak, lehetővé téve az egyes Watt felbontású beállításokat (pl. 80 W).

**Töltési rekord feldolgozása:** A `0x000A` csomag (töltési rekordok) megfelelően fel van dolgozva. Ez megakadályozza a hamis leállásokat, amelyeket a csomag telemetria-állapotként való téves értelmezése okozott. A parser kiolvassa és naplózza a töltést indító felhasználónevet a payload 1-17. bájtjaiból.

### 3. Force (Kézi Felülbírálás) Üzemmód
Azonnali kézi beavatkozásra szolgáló vezérlő felület.
*   **Kézi indítás (Start):** Azonnal elindítja a töltést a beállított áramerősséggel. Amint a töltési folyamat befejeződik (pl. tele lett az autó vagy lehúzták a kábelt), a kézi felülbírálás automatikusan megszűnik, és visszaáll a Solar/Ütemezett automatizmus.
*   **Kézi Stop (Hard Stop):** Azonnal leállítja a töltést és **felfüggeszti a Solar/Ütemezett automatizmusokat** mindaddig, amíg manuálisan vissza nem vonod a felülbírálást (vörös "Visszavonás" gomb).
*   **Ideiglenes leállítás (Soft Stop):** Leállítja az éppen futó töltést, de a háttérben futó automatikus szabályokat nem írja felül. Ha a Solar Auto feltételek újra teljesülnek, a töltés magától elindulhat.

A szoftverben az alábbi változtatások kerültek megvalósításra:
1. A Solar Auto szabályok (Hálózati import korlátja, Akkumulátor leállítási SoC, Ház UPS túlzott terhelés védelme) egymást követően és függetlenül lesznek kiértékelve (egymás utáni szabályok).
2. A "Hálózati töltés késleltetett leállítása (perc)" beállítás 0 perc értékére állítása IMMEDIÁTUS leállítást jelent (0 perces késleltetés) a vizsgálat letiltása helyett. A vizsgálat akkor aktív, ha a hálózati áram küszöbértéke nagyobb 0-nál.
3. Az HTML beviteli lépésközei a Watt paraméterek esetén 1-re változtak, lehetővé téve az egyes Watt felbontású beállításokat (pl. 80 W).

---

## 6. Biztonsági Védelmi Rétegek (Safety Guards)

A szoftver számos beépített biztonsági funkcióval rendelkezik a hardverek, a hálózat, valamint az illetéktelen beavatkozások vagy véletlen elnyomkodások elleni védelem érdekében:

1.  **Webes Jelszavas Autentikáció és Session Kezelés:** Mivel a vezérlő a helyi hálózatról is elérhető (0.0.0.0-s címre bindolva, így pl. Raspberry Pi-ről kiszolgálva), a jogosulatlan hozzáférés és a véletlen módosítások megelőzésére beépített jelszavas védelmet kapott.
    *   Az autentikáció alapértelmezetten aktív (`"web_auth_enabled": true`), alapértelmezett jelszava `"admin"`.
    *   Sikeres bejelentkezés után a kiszolgáló egy kriptográfiailag biztonságos session tokent rendel a böngészőhöz, amellyel a felhasználó jogosultságot szerez a telemetria megtekintésére és a vezérlésre.
    *   A fejlécben található **Kijelentkezés** gombbal a session azonnal lezárható.
    *   Amennyiben nincs szükség védelemre, az a konfigurációból kikapcsolható (`"web_auth_enabled": false`).
2.  **Mágneskapcsoló kímélés (Relay Guard):** Sikertelen vagy leállított töltés után a program **2 perc (120 másodperc) kötelező várakozási időt (cooldown)** tart. Ezen idő alatt semmilyen automatizmus nem indíthatja újra a töltést, megelőzve a töltő reléinek gyors tönkremenetelét (beégését).
3.  **Háromszori hiba utáni leállás (Fail-Safe Disarm):** Ha a töltésindítási parancs után 60 másodpercen belül a telemetria alapján nem indul el a töltés, a program hibát naplóz. Ha ez egymás után 3 alkalommal előfordul, a rendszer biztonsági okokból leállítja a próbálkozást és átvált **Figyelés (Monitoring)** módba, elkerülve a végtelenített BLE parancsküldési ciklusokat.
4.  **Hálózati Aszinkronizáció és Telemetria Watchdog (Kapcsolat-helyreállítás):**
    *   A Deye inverter szinkron lekérdezései (`pysolarmanv5`) egy teljesen elkülönített háttérszálon futnak, így a hálózati ingadozások nem tudják blokkolni a fő eseményhurkot.
    *   Minden Bluetooth írás és feliratkozás 5 másodperces szigorú időkorlát-védelem alatt áll.
    *   **Kapcsolódási időkorlát (Connect Timeout):** A Bleak kapcsolódási kísérlete (`client.connect()`) hajlamos lehet végtelenül leblokkolni a Windows Bluetooth rétegében. Ennek elkerülésére a kapcsolódást egy 20 másodperces aszinkron időkorlát (`asyncio.wait_for`) védi. Ha ennyi idő alatt nem jön létre a kapcsolat, a program megszakítja a kísérletet, lezárja a socketet és újracsatlakozási ciklust indít.
    *   Ha a kapcsolat állapota `LOGGED_IN`, de 15 másodpercig nem érkezik telemetriai adat a töltőtől, a program automatikusan lezárja a fagyott kapcsolatot, és újracsatlakozási folyamatot indít, biztosítva a teljesen felügyelet nélküli automatikus működést.
    *   **Szálbiztos telemetria feldolgozás:** A Bleak háttérszáláról érkező értesítéseket a program a fő eseményhurok (`main_loop`) referenciáján keresztül, az `asyncio.run_coroutine_threadsafe` függvénnyel küldi át a főszálra, megelőzve a háttérszálakon fellépő `RuntimeError: no running event loop` hibákat.

---

## 7. Futtatási és Telepítési Útmutató

### A) Szükséges Python környezet (Windows)
Telepítsd a Python 3.9+ környezetet, majd a következő parancsokkal a szükséges könyvtárakat:
```bash
pip install bleak==0.20.2 bleak-winrt==1.2.0 pysolarmanv5 pyinstaller
```

### B) Indítás Szimulációs módban
A felület és a szabályok hardver nélküli teszteléséhez:
```bash
python deye_besen_controller.py --sim
```
*A teszteléshez helyezz el egy tetszőleges képet `background.png` néven a futtatási mappa mellé.*

*   **Csúszka háttérszínezési javítás:** A dashboard betöltésekor a nagy áramerősség-csúszkák kék háttere most már helyesen tükrözi a konfigurált áramerősség értéket. Korábban a csúszkák háttere alapértelmezetten 50%-on állt a betöltéskor, és csak klikkelés után ugrott a helyére. Ezt a JavaScript inicializációs sorrendjének módosításával javítottuk.

### C) Indítás Éles módban
Futtasd a scriptet paraméter nélkül:
```bash
python deye_besen_controller.py
```

### D) Önálló `.exe` fájl fordítása
A Windows-os ékezetes mappák miatti fordítási hibák elkerülésére a fordítás átmeneti ékezetmentes könyvtárakon keresztül javasolt:
```powershell
py -m PyInstaller --onefile --clean --distpath "C:\Users\<Felhasználó>\dist_temp" --workpath "C:\Users\<Felhasználó>\build_temp" deye_besen_controller.py
```
A sikeres fordítás után a generált `deye_besen_controller.exe` fájl visszamásolható a projekt fő könyvtárába.

---

A konfigurációs fájl (config.json) beállításai határozzák meg a rendszer működését és viselkedését. A felhasználók ezen beállításokon keresztül testre szabhatják az alkalmazást igényeiknek megfelelően. A fájlban található paraméterek között a naplózás szintje, az adatbázis kapcsolati karakterláncok, valamint a biztonsági beállítások szerepelnek. Ezek a beállítások befolyásolják a rendszer teljesítményét és megbízhatóságát.

A program indításakor beolvassa a `config.json` fájlt. Ha nem létezik, létrehozza a beépített alapértelmezett értékekkel (`DEFAULT_CONFIG`). Az alábbi táblázat részletezi az egyes konfigurációs kulcsok szerepét:

A szoftvernek közelmúltan a következő változtatásokat hajtották végre:
1. A Solar Auto szabályok (Hálózati import korlát, Akkumulátor leállítási SoC, Ház UPS túlterhelésvédelem) egymást követően és függetlenül lesznek kiértékelve (egymás utáni szabályok).
2. A "Hálózati töltés késleltetett leállítása (perc)" beállítás 0 perc értékére állításának hatására azonnali leállást (0 perces késleltetés) érvényesül, nem pedig letiltja a vizsgálatot. A vizsgálat akkor aktív, ha a hálózati áram küszöbértéke nagyobb 0-nál.
3. Az HTML beviteli lépésközei a Watt paraméterek esetén 1-re változtak, lehetővé téve az egyes Watt felbontású beállításokat (pl. 80 W).

---

## Köszönetnyilvánítás / Acknowledgments

*   **slespersen:** Külön köszönet a [slespersen/evseMQTT](https://github.com/slespersen/evseMQTT) GitHub projektért! Az ő munkája és a BESEN BS20 töltő Bluetooth Low Energy (BLE) protokolljának feltárása rengeteg kísérletezéstől és visszafejtéstől kímélte meg a fejlesztőket, megalapozva a vezérlőszoftver stabil BLE kommunikációját.
*   **Antigravity (Google DeepMind):** Köszönet az AI alapú páros programozó asszisztensnek a szoftver refaktorálásáért, az aszinkron vezérlési és szimulációs hurkok megalkotásáért, a biztonsági logikák beépítéséért, a prémium glassmorphic webes felület kidolgozásáért és a teljes kétnyelvű dokumentáció elkészítéséért.

---

## Fontos figyelmeztetés és Hibabejelentés / Disclaimer & Bug Reporting

> [!WARNING]
> **Felelősségkizárás:** A szoftver alapos tesztelése és a beépített biztonsági funkciók (cooldown, túlterhelés-védelem, fail-safe) ellenére előfordulhatnak nem várt logikai vagy szoftveres hibák. A szoftver használata kizárólag saját felelősségre történik. A fejlesztők nem vállalnak felelősséget az inverterben, az akkumulátorban, az autótöltőben vagy a háztartási elektromos hálózatban keletkező esetleges károkért. A szoftverben a Solar Auto szabályok (Hálózati import korlátja, Akkumulátor leállítási SoC, Ház UPS túlterhelésvédelem) mostantól egymás után és függetlenül értékelődnek. A "Hálózati töltés késleltetett leállítása (perc)" beállítás 0 perc értékére állítását mostanában azonnali leállítást jelent, nem pedig letiltást. A vizsgálat aktív, ha a hálózati energia küszöbértéke nagyobb, mint 0. Az HTML beviteli lépéskövei a Watt paraméterekhez mostantól 1-re állítva lehetnek, így egyedüli Watt felbontású beállítások is elvégezhetők (pl. 80 W).

Ha a használat során logikai hibát, nem várt viselkedést vagy hibás működést tapasztalsz, kérjük, **jelezd azt a projekt GitHub felületén (Issues)**, hogy javítani tudjuk! Minden visszajelzést nagyon szépen köszönünk!
