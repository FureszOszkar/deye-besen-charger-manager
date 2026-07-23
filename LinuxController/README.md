# Linux (Debian 13) Telepítési Útmutató

Ez a mappa **önmagában, teljesen önállóan** tartalmazza a Deye & BESEN Töltővezérlő futtatásához szükséges mindent: a program forráskódját (`main.py` és a többi `.py` fájl), a kiegészítő fájlokat (`crypto-js.min.js`, `background.jpg`) és a Linux-telepítéshez szükséges eszközöket. **Az egész `LinuxController` mappa átmásolható a Linux gépre — bármilyen néven, bármilyen könyvtárba —, és onnan minden útvonal-szerkesztés nélkül működik.**

A program platformfüggetlen — nincs benne Windows-specifikus hívás, a webszerver a Python szabványos könyvtárát használja —, a három harmadik féltől származó függőség (`bleak`, `pysolarmanv5`, `pycryptodome`) mindegyike támogatott Linuxon.

## Gyors telepítés

A mappa átmásolása után, onnan futtatva:

```bash
bash install_linux.sh
```

A szkript:
1. Ellenőrzi a szükséges rendszercsomagokat (`python3`, `python3-venv`, `python3-pip`, `bluez`); ha valami hiányzik, kiírja a telepítő parancsot (`apt install ...`) — automatikusan nem futtat semmit rendszerszinten.
2. Létrehoz egy virtuális környezetet (`.venv`) ebben a mappában, és telepíti a Python-függőségeket a `requirements.txt`-ből.
3. **Legenerálja a `deye-besen-controller.service` fájlt a mappa tényleges, aktuális elérési útjával** — nincs szükség kézi útvonal-szerkesztésre, bárhova is másoltad a mappát.

## Kézi telepítés lépésről lépésre

1.  **Rendszercsomagok** (Debian 13 alap Python 3.13-mal jön, ez megfelelő):
    ```bash
    apt install python3 python3-venv python3-pip bluez
    ```
    A `bluez` szükséges, mert a `bleak` csomag Linuxon a BlueZ-t használja D-Bus-on keresztül a BESEN töltővel való BLE kommunikációhoz.

2.  **Bluetooth szolgáltatás** engedélyezése és indítása:
    ```bash
    systemctl enable --now bluetooth
    ```
    Ellenőrzés: `bluetoothctl list` (adapter listázása), `hciconfig` (BT-eszköz állapota).

3.  **Virtuális környezet** (Debian PEP 668 miatt a rendszer-Pythonba nem lehet közvetlenül `pip install`-olni), a mappán belülről futtatva:
    ```bash
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    ```

4.  **Próbafuttatás**, a mappán belülről:
    ```bash
    .venv/bin/python main.py
    ```
    A dashboard ezután elérhető: `http://<gép IP-je>:8080`.

## Napi használat — indítás, leállítás, állapot, napló

Ha a `deye-besen-controller` már telepítve van systemd szolgáltatásként (lásd lent), ezekkel a parancsokkal kezelheted:

```bash
systemctl start deye-besen-controller     # indítás
systemctl stop deye-besen-controller      # leállítás
systemctl restart deye-besen-controller   # újraindítás (pl. ha "lerohadt")
systemctl status deye-besen-controller    # fut-e éppen, mikor omlott össze
journalctl -u deye-besen-controller -f    # élő napló figyelése
journalctl -u deye-besen-controller -n 80 --no-pager   # utolsó 80 sor
```

Ha a `systemctl status` `inactive (dead)`-et mutat, a `journalctl -n 80` kimenetéből általában kiderül, miért állt le (pl. hiányzó `deyecontroller` felhasználó — lásd alább, `status=217/USER` hiba esetén futtasd: `useradd --system --no-create-home deyecontroller`, majd indítsd újra).

## Fontos: a `config.json` munkakönyvtára

A program a `config.json`-t a **futtatáskori munkakönyvtárhoz relatívan** olvassa/írja, ezért mindig **ebből a mappából** kell indítani (`python main.py` a mappán belülről) — systemd service esetén ezt a `WorkingDirectory=` beállítás garantálja, amit az `install_linux.sh` automatikusan a helyes útvonalra állít be.

A `config.json` **nem jön létre automatikusan indításkor** — ez korábban tévesen volt dokumentálva. A program hiányában csak memóriában használ beépített alapértékeket (`DEFAULT_CONFIG`), fájlt csak akkor ír, ha ténylegesen történik mentés (pl. a dashboard beállítás-mentésekor, vagy egy töltési munkamenet lezárásakor) — indításkor önmagában nem.

**Ráadásul a webes felület nem tudja beállítani** az `inverter_ip`, `charger_mac`, `logger_serial` mezőket (csak SOC-határokat, ütemezést, teljesítmény-limiteket) — ezek **kizárólag** a `config.json`-ból tölthetők be. Enélkül a program a beépített placeholder-adatokkal (`192.168.0.100`, `00:11:22:33:44:55`) próbál kapcsolódni, ami nem fog sikerülni.

**Ezért a `config.json` biztosítása nem opcionális, hanem szükséges** a valós hardverhez. Két lehetőség:
1.  **Másold ide a mellékelt [`config_example.json`](config_example.json) fájlt** `config.json` néven, és írd bele a saját inverter IP-det, sorozatszámodat, a töltő nevét/MAC-címét és jelszavát.
2.  **Vagy másold át a meglévő (pl. Windows-oldali) `config.json`-odat** ide, ha már van egy működő beállításod.

## Automatikus indítás systemd szolgáltatásként

Az `install_linux.sh` lefutása után a mappában lévő [`deye-besen-controller.service`](deye-besen-controller.service) fájl már a **tényleges, aktuális elérési utat** tartalmazza — nincs szükség kézi szerkesztésre:

```bash
useradd --system --no-create-home deyecontroller
cp deye-besen-controller.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now deye-besen-controller
```

Naplók megtekintése: `journalctl -u deye-besen-controller -f`.

**Megjegyzés:** ha a mappát a telepítés után egy másik könyvtárba mozgatod, a `.service` fájlban az útvonalak a régi helyre fognak mutatni — ilyenkor futtasd újra az `install_linux.sh`-t az új helyről, hogy a `.service` fájl frissüljön.

## Hálózat

*   A dashboard a `0.0.0.0:8080`-ra köt — nem privilegizált port, nem kell root joggal futtatni.
*   Ha más eszközről (pl. telefonról, az Android widgettel) is el akarod érni, engedd át a tűzfalon:
    ```bash
    ufw allow 8080/tcp
    ```
*   A Deye inverter-loggerrel (Modbus/Solarman, TCP/8899) azonos helyi hálózaton kell lennie a gépnek.

## Ismert probléma: Mercusys MA530/MA550H Bluetooth-adapter (`2c4e:0115`)

A README fő fájlja a **Mercusys MA550H Long Range Bluetooth 5.4** adaptert ajánlja a töltő BLE hatótávjához. Ennek az adapternek (és a rokon MA530 modellnek) a USB azonosítója (`2c4e:0115`) **2026 júniusáig hiányzott a Linux kernel `btusb` drivere eszköz-táblájából** ([upstream commit `ce21a5c`](https://github.com/torvalds/linux/commit/ce21a5cf3d1fd92b84ea9ad2b7c7240aff2162d2), *"Bluetooth: btusb: Add Mercusys MA530 for Realtek RTL8761BUV"*).

**Tünet:** a program elindul, az inverter/dashboard működik, de a töltő BLE-kapcsolódása instabil vagy sosem sikerül. A `dmesg`-ben `Bluetooth: hci0: Opcode 0x2042 failed: -16` hiba látszik, a `bluetoothctl scan on` pedig másodpercek után magától leáll ("discovering off"), mielőtt bármit találna. Az ok: a kernel nem ismeri fel Realtek-chipként az adaptert, ezért nem tölti be hozzá a helyes gyártói firmware-t (`rtl8761bu_fw.bin`), és a "LE Extended Scan" parancs elhasal.

### Ellenőrzés: szükséges-e még a manuális javítás?

**FIGYELEM — a `modinfo btusb | grep 2c4e` NEM alkalmas ellenőrzés:** a Realtek-eszköztábla bejegyzései nem kerülnek be a modul alias-listájába, így ez a parancs a *helyesen patchelt* modulon is üres kimenetet ad. (Ez a dokumentáció korábban tévesen ezt javasolta.)

A megbízható ellenőrzés a **futás közbeni viselkedés**: csatlakoztatott adapter mellett nézd meg a kernel-naplót:

```bash
dmesg | grep -i "RTL"
```

*   Ha látszanak a `RTL: examining hci_ver=...` és `RTL: loading rtl_bt/rtl8761bu_fw.bin` sorok (hibaüzenet nélkül), a driver **helyesen, Realtek-chipként kezeli** az adaptert — a javítás aktív (vagy már a hivatalos kernel is tartalmazza), nincs teendő.
*   Ha **nincsenek RTL-sorok**, és/vagy `Bluetooth: hci0: Opcode 0x2042 failed: -16` hiba látszik, a hiba fennáll — a manuális javítás szükséges (lásd lent).

**Kernelfrissítés után** ez az ellenőrzés újra elvégzendő: az új kernel a saját (javítatlan) moduljával jön, tehát a patch-elést valószínűleg meg kell ismételni — kivéve, ha az upstream javítás (lásd a commit-hivatkozást fent) időközben már leért a Debian-kernelbe, amit a fenti `dmesg`-ellenőrzés mutat meg.

### Manuális javítás (ha a fenti ellenőrzés negatív)

1.  Fejlécek és forrás telepítése a futó kernelhez:
    ```bash
    apt install -y linux-headers-$(uname -r) build-essential linux-source
    cd /usr/src
    tar xf linux-source-*.tar.xz
    ```
2.  A `drivers/bluetooth/btusb.c`-ben keresd meg a *"Additional Realtek 8761BUV Bluetooth devices"* blokkot, és szúrj be bele egy sort a többi mintájára:
    ```c
    { USB_DEVICE(0x2c4e, 0x0115), .driver_info = BTUSB_REALTEK |
                                                 BTUSB_WIDEBAND_SPEECH },
    ```
3.  Fordítás csak a bluetooth-modulra, a futó kernelhez illeszkedő build-fával:
    ```bash
    cd /usr/src/linux-source-*
    make -C /lib/modules/$(uname -r)/build M=$(pwd)/drivers/bluetooth modules
    ```
4.  Telepítés (tartós, újraindítás után is megmarad):
    ```bash
    cp drivers/bluetooth/btusb.ko /lib/modules/$(uname -r)/kernel/drivers/bluetooth/btusb.ko
    depmod -a
    rmmod btusb && modprobe btusb
    ```
5.  Ellenőrzés — a `dmesg | tail` mostantól `RTL: examining hci_ver=...` és `RTL: loading rtl_bt/rtl8761bu_fw.bin` sorokat mutasson, hiba nélkül. A `{ echo "scan on"; sleep 15; echo "devices"; echo "scan off"; } | bluetoothctl` pedig stabilan fusson végig 15 másodpercig, és listázza a töltőt.

## Opcionális: a BT-adapter automatikus táp-ciklizálása a program indulásakor (`uhubctl`)

A Realtek RTL8761BU-alapú adapterek firmware-e intenzív BLE-terhelés (pl. egy szoftverhiba miatti újrapróbálkozás-vihar) után beragadhat olyan állapotba, amiben a keresés/kapcsolódás nem működik. Ilyenkor csak az adapter **áramtalanítása** segít — a fizikai kihúzás-visszadugás, vagy annak szoftveres megfelelője: az USB-port VBUS-tápjának ki-be kapcsolása a `uhubctl` eszközzel.

**FONTOS — ami éles tesztben NEM vált be:** a USB unbind/bind (`/sys/bus/usb/drivers/usb/unbind` + `bind`). Ez nem áramtalanít, a beragadt firmware nem resetelődik, sőt éles tesztben az adapter tőle vált teljesen elérhetetlenné (`command 0xfc61 tx timeout`, `RTL: Read reg16 failed (-110)` a dmesg-ben). **Ezt ne használd, és ne automatizáld.**

A `uhubctl`-es táp-ciklizálás viszont éles tesztben bizonyítottan működik (HP MicroServer Gen6, Debian 13): a port ténylegesen `off` állapotba kerül, az adapter újra-enumerálódik és frissen újratölti a firmware-t. Automatizálva a program indulására, minden (kézi vagy watchdog általi) újraindulás friss adapterrel történik — mellékhatásként a BLE-csatlakozás is érezhetően gyorsabb.

**Előfeltétel:** a `uhubctl` csak olyan USB-vezérlőn működik, ami ténylegesen támogatja a portonkénti táp-kapcsolást. Ellenőrzés (veszélytelen, csak listáz): `apt install uhubctl`, majd `uhubctl` — ha a hubok listázódnak `ppps` jelzéssel, érdemes a lenti éles tesztet elvégezni; a valódi bizonyíték az, ha a ciklizálás után a `dmesg`-ben `USB disconnect` → új enumeráció → hibátlan `RTL: loading rtl_bt/rtl8761bu_fw.bin` sorozat látszik.

1.  Szkript létrehozása (a `<MAPPA>`-t cseréld a telepítési útvonaladra; más adapternél a `VID_PID`-et igazítsd):
    ```bash
    cat > <MAPPA>/bt-powercycle.sh << 'EOF'
    #!/usr/bin/env bash
    # A BT adapter VALODI tap-ciklizalasa (VBUS off/on) uhubctl-lel -- a fizikai
    # kihuzas-visszadugas szoftveres megfeleloje. A program indulasa elott fut
    # (systemd ExecStartPre), igy minden indulasnal friss adapterrel indulunk.
    set -u

    VID_PID="2c4e:0115"

    # Az adapter aktualis helyenek (hub + port) megkeresese, igy mas portba
    # atdugva is mukodik
    read -r HUB PORT <<< "$(uhubctl 2>/dev/null | awk -v id="$VID_PID" '
        /^Current status for hub/ { hub=$5 }
        index($0, "[" id) > 0 { p=$2; sub(":", "", p); print hub, p; exit }')"

    if [ -n "${HUB:-}" ] && [ -n "${PORT:-}" ]; then
        echo "BT adapter tap-ciklizalasa: hub $HUB, port $PORT"
        uhubctl -l "$HUB" -p "$PORT" -a cycle -d 3
        # Varakozas: ujra-enumeracio + firmware-betoltes + BlueZ ujraregisztracio
        sleep 8
    else
        echo "FIGYELEM: $VID_PID BT adapter nem talalhato uhubctl-lel - tap-ciklizalas kihagyva."
    fi
    exit 0
    EOF
    chmod +x <MAPPA>/bt-powercycle.sh
    ```
    A szkript mindig 0-val lép ki, így hiányzó adapter/nem támogatott hub esetén sem akadályozza a program indulását.

2.  Éles teszt kézzel (rövid BT-szakadással jár): `bash <MAPPA>/bt-powercycle.sh`, majd `dmesg | tail -15` — a fent leírt hibátlan újra-enumerációt kell látnod.

3.  Bekötés a systemd unitba — a sor eleji `+` fontos: attól fut ez az egy lépés root-jogosultsággal, miközben maga a program a korlátozott `deyecontroller` felhasználóként fut:
    ```bash
    sed -i '/^\[Service\]/a ExecStartPre=+<MAPPA>/bt-powercycle.sh' /etc/systemd/system/deye-besen-controller.service
    systemctl daemon-reload
    systemctl restart deye-besen-controller
    ```

4.  Ellenőrzés: `systemctl status deye-besen-controller` — az `ExecStartPre` `SUCCESS`-szel fusson le, majd a `journalctl -u deye-besen-controller -f` naplóban másodperceken belül fel kell épülnie a BLE-kapcsolatnak.

## Opcionális: napló-alapú "élettartam" figyelés (watchdog)

A program belül is figyeli magát (lásd a fő README-t), de ez csak addig ér valamit, amíg maga a Python event loop fut. Ha teljesen lefagyna, egy **külső, systemd-alapú ellenőrzés** biztonsági hálóként újraindíthatja. Ez teljesen opcionális — akinek nem kell, hagyja ki.

**A módszer:** egy systemd `.timer` néhány percenként megnézi, volt-e bármilyen új sor a szolgáltatás naplójában (`journalctl`) az elmúlt N percben. Ha nem, a szolgáltatás valószínűleg lefagyott, és a szkript kényszerű `systemctl restart`-ot hajt végre.

1.  Figyelő szkript létrehozása (a `<MAPPA>`-t cseréld a saját telepítési útvonaladra):
    ```bash
    cat > <MAPPA>/deye-besen-controller-watchdog.sh << 'EOF'
    #!/usr/bin/env bash
    set -euo pipefail

    SERVICE="deye-besen-controller"
    STALE_MINUTES=10

    COUNT=$(journalctl -u "$SERVICE" --since "-${STALE_MINUTES} min" --no-pager -q | wc -l)

    if [ "$COUNT" -eq 0 ]; then
        logger -t deye-watchdog "Nincs uj naplobejegyzes ${STALE_MINUTES} perce - $SERVICE ujrainditasa."
        systemctl restart "$SERVICE"
    fi
    EOF
    chmod +x <MAPPA>/deye-besen-controller-watchdog.sh
    ```
    A `STALE_MINUTES` értékét igény szerint módosíthatod.

2.  Systemd service + timer létrehozása (a `<MAPPA>`-t itt is cseréld):
    ```bash
    cat > /etc/systemd/system/deye-besen-controller-watchdog.service << 'EOF'
    [Unit]
    Description=Deye Besen Controller - naplo alapu elettartam-ellenorzes

    [Service]
    Type=oneshot
    ExecStart=<MAPPA>/deye-besen-controller-watchdog.sh
    EOF

    cat > /etc/systemd/system/deye-besen-controller-watchdog.timer << 'EOF'
    [Unit]
    Description=Deye Besen Controller watchdog - 5 percenkent ellenorzes

    [Timer]
    OnBootSec=5min
    OnUnitActiveSec=5min

    [Install]
    WantedBy=timers.target
    EOF

    systemctl daemon-reload
    systemctl enable --now deye-besen-controller-watchdog.timer
    ```

3.  Ellenőrzés:
    ```bash
    systemctl list-timers deye-besen-controller-watchdog.timer
    journalctl -u deye-besen-controller-watchdog.service --no-pager
    ```
