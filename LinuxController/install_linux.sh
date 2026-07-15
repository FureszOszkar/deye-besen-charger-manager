#!/usr/bin/env bash
# Deye & BESEN Toltovezerlo - telepito szkript Debian/Ubuntu alapu rendszerekhez.
# Ez a mappa (LinuxController) onmagaban tartalmazza a teljes programot -
# barhova masolhato es barmilyen nevvel elnevezhezto, a szkript automatikusan
# a tenyleges telepitesi helyet hasznalja mindenhol.
#
# Futtatas: bash install_linux.sh (a mappan belulrol, vagy barhonnan: bash /ut/LinuxController/install_linux.sh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=== Deye & BESEN Toltovezerlo - Linux telepito ==="
echo "Telepitesi hely: $SCRIPT_DIR"
echo

# 1. Rendszercsomagok ellenorzese
REQUIRED_PKGS=(python3 python3-venv python3-pip bluez)
MISSING_PKGS=()
for pkg in "${REQUIRED_PKGS[@]}"; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        MISSING_PKGS+=("$pkg")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "Hianyzo rendszercsomagok: ${MISSING_PKGS[*]}"
    echo "Telepitsd oket a kovetkezo paranccsal, majd futtasd ujra ezt a szkriptet:"
    echo
    echo "    apt install ${MISSING_PKGS[*]}"
    echo
    exit 1
fi
echo "Rendszercsomagok rendben (${REQUIRED_PKGS[*]})."

# 2. Bluetooth szolgaltatas allapota (csak figyelmeztetes, nem allitjuk at automatikusan)
if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl is-active --quiet bluetooth; then
        echo
        echo "FIGYELEM: a 'bluetooth' szolgaltatas nem fut. A BLE kommunikaciohoz szukseges:"
        echo "    systemctl enable --now bluetooth"
    fi
fi

# 3. Virtualis kornyezet letrehozasa
if [ ! -d "$VENV_DIR" ]; then
    echo
    echo "Virtualis kornyezet letrehozasa: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
else
    echo "Virtualis kornyezet mar letezik: $VENV_DIR"
fi

echo "Fuggosegek telepitese..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# 4. A systemd unit fajl legeneralasa a tenyleges, detektalt utvonallal
SERVICE_TEMPLATE="$SCRIPT_DIR/deye-besen-controller.service"
if [ -f "$SERVICE_TEMPLATE" ]; then
    sed -i "s|__APP_DIR__|$SCRIPT_DIR|g" "$SERVICE_TEMPLATE"
    echo
    echo "A deye-besen-controller.service fajl legeneralva a tenyleges utvonallal ($SCRIPT_DIR)."
fi

echo
echo "=== Telepites kesz ==="
echo
echo "Probafuttatas:"
echo "    cd \"$SCRIPT_DIR\""
echo "    \"$VENV_DIR/bin/python\" main.py"
echo
echo "Allando (systemd) szolgaltataskent inditashoz:"
echo "    1. useradd --system --no-create-home deyecontroller"
echo "    2. cp \"$SERVICE_TEMPLATE\" /etc/systemd/system/"
echo "       (az utvonalak mar a tenyleges telepitesi helyre mutatnak, nincs szukseg kezi szerkesztesre)"
echo "    3. systemctl daemon-reload"
echo "    4. systemctl enable --now deye-besen-controller"
echo
echo "Reszletek: $SCRIPT_DIR/README.md"
