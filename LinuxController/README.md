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

## Fontos: a `config.json` munkakönyvtára

A program a `config.json`-t a **futtatáskori munkakönyvtárhoz relatívan** olvassa/írja, ezért mindig **ebből a mappából** kell indítani (`python main.py` a mappán belülről) — systemd service esetén ezt a `WorkingDirectory=` beállítás garantálja, amit az `install_linux.sh` automatikusan a helyes útvonalra állít be.

A `config.json` nem része a mappának — első indításkor a program alapértelmezett beállításokkal létrehozza. Ha a meglévő (pl. Windows-oldali) beállításaidat át akarod hozni, másold ide a saját `config.json` fájlodat az első indítás előtt.

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

Mielőtt belevágnál a lenti lépésekbe, ellenőrizd, hogy a jelenlegi kernel driver-je már ismeri-e ezt az adaptert (idővel a hivatalos Debian-kernel is tartalmazni fogja a javítást):

```bash
modinfo btusb | grep -i "2c4e"
```

*   Ha ez **ad vissza egy sort** (pl. `alias: usb:v2C4Ep0115d*...`), a javítás **már benne van** a futó kernelben — nincs szükség a lenti manuális lépésekre, a normál `apt upgrade` elég.
*   Ha **nincs kimenet**, a hiba még fennáll, és a manuális javítás szükséges (lásd lent).

Ugyanígy **kernelfrissítés után** is érdemes újra lefuttatni ezt az ellenőrzést, mielőtt a lenti patch-elést megismételnéd — lehet, hogy időközben feleslegessé vált.

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
