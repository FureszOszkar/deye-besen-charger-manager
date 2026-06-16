import asyncio
import sys
import os
import time
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import secrets
from bleak import BleakClient, BleakScanner
from pysolarmanv5 import PySolarmanV5

# --- ALAPÉRTELMEZETT KONFIGURÁCIÓS BEÁLLÍTÁSOK ---
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "inverter_ip": "192.168.0.100",
    "inverter_port": 8899,
    "logger_serial": 1234567890,
    "http_port": 8080,
    "start_soc": 100,
    "stop_import_limit": 2000,
    "grid_charge_duration_minutes": 30,
    "house_power_limit_w": 3000,
    "control_mode": "monitoring",          # auto / schedule / force / monitoring
    "persist_mode_on_restart": False,
    "charger_name": "ACP#DefaultName",
    "charger_mac": "00:11:22:33:44:55",
    "charger_password": "FFFFFFFFFFFF",
    "charger_max_amps": 16,
    "force_submode": "manual_stop",        # manual_start / manual_stop
    "schedule_solar_auto": False,
    "auto_enabled": False,
    "schedule_enabled": False,
    "web_auth_enabled": True,
    "web_password": "admin",
    "forced_schedule": [
        {"day": "Hétfő", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Kedd", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Szerda", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Csütörtök", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Péntek", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Szombat", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 10, "override_auto": True},
        {"day": "Vasárnap", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 10, "override_auto": True}
    ]
}

INVERTER_IP = "192.168.0.100"
INVERTER_PORT = 8899
LOGGER_SERIAL = 1234567890

CHARGER_NAME = "ACP#DefaultName"
CHARGER_MAC = "00:11:22:33:44:55"

HTTP_PORT = 8080

# BLE GATT UUID-k
CHAR_FFE4_NOTIFY = "0000ffe4-0000-1000-8000-00805f9b34fb"
CHAR_FFE9_WRITE = "0000ffe9-0000-1000-8000-00805f9b34fb"
CHAR_FFC1_WRITE = "0000ffc1-0000-1000-8000-00805f9b34fb"
CHAR_FFC2_NOTIFY = "0000ffc2-0000-1000-8000-00805f9b34fb"
CHAR_FFD3_NOTIFY = "0000ffd3-0000-1000-8000-00805f9b34fb"
CHAR_FD02_NOTIFY = "0000fd02-0000-1000-8000-00805f9b34fb"
CHAR_FFF3_WRITE = "0000fff3-0000-1000-8000-00805f9b34fb"

# Gyári alapértelmezett jelszó helyőrző a csomagok fejlécéhez
DEFAULT_PACKET_PASSWORD = bytearray([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])

# Hálózati webes bejelentkező felület (Autentikációhoz)
active_sessions = set()
WEB_AUTH_ENABLED = True
WEB_PASSWORD = "admin"

LOGIN_HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Deye & BESEN - Bejelentkezés</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            --bg-color: #0f172a;
            --text-color: #f8fafc;
            --border-color: rgba(255, 255, 255, 0.1);
            --primary: #22d3ee;
            --primary-hover: #06b6d4;
            --card-bg: rgba(16, 16, 20, 0.3);
        }
        body {
            margin: 0;
            padding: 0;
            font-family: 'Outfit', 'Inter', -apple-system, sans-serif;
            background-color: var(--bg-color);
            background-image: url('/background.png');
            background-attachment: fixed;
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            color: var(--text-color);
        }
        .login-container {
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 2.5rem;
            width: 90%;
            max-width: 380px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37), 0 0 30px rgba(34, 211, 238, 0.1);
            text-align: center;
        }
        h1 {
            font-size: 1.8rem;
            margin-bottom: 0.5rem;
            margin-top: 0;
            background: linear-gradient(135deg, #38bdf8 0%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 800;
        }
        p {
            font-size: 0.9rem;
            color: #94a3b8;
            margin-bottom: 2rem;
            margin-top: 0;
        }
        .form-group {
            margin-bottom: 1.5rem;
            text-align: left;
        }
        label {
            display: block;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.5rem;
            color: #94a3b8;
            font-weight: 600;
        }
        input[type="password"] {
            width: 100%;
            padding: 0.8rem;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: rgba(15, 23, 42, 0.6);
            color: white;
            font-size: 1rem;
            box-sizing: border-box;
            transition: all 0.3s ease;
        }
        input[type="password"]:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 10px rgba(34, 211, 238, 0.3);
        }
        .btn-login {
            width: 100%;
            padding: 0.8rem;
            border-radius: 8px;
            border: none;
            background: linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);
            color: white;
            font-weight: 700;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(2, 132, 199, 0.3);
        }
        .btn-login:hover {
            background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
            transform: translateY(-2px);
        }
        .error-box {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.4);
            color: #f87171;
            padding: 0.8rem;
            border-radius: 6px;
            font-size: 0.85rem;
            margin-bottom: 1.5rem;
            display: none;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>Deye & BESEN</h1>
        <p>A kezelőfelület eléréséhez kérjük, add meg a jelszót.</p>
        <div id="error-msg" class="error-box">Helytelen jelszó!</div>
        <form id="login-form">
            <div class="form-group">
                <label for="password">Jelszó</label>
                <input type="password" id="password" required placeholder="Jelszó">
            </div>
            <button type="submit" class="btn-login">Belépés</button>
        </form>
    </div>
    <script>
        document.getElementById('login-form').addEventListener('submit', function(e) {
            e.preventDefault();
            const password = document.getElementById('password').value;
            const errBox = document.getElementById('error-msg');
            
            fetch('/api/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ password: password })
            })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    window.location.reload();
                } else {
                    errBox.style.display = 'block';
                    document.getElementById('password').value = '';
                }
            })
            .catch(err => {
                errBox.style.display = 'block';
                errBox.innerText = 'Hiba a szerverrel való kapcsolatban!';
            });
        });
    </script>
</body>
</html>"""

# Globális állapot thread-safe eléréssel
shared_state = {
    # Kapcsolatok állapota
    "inverter_connected": False,
    "charger_connected": False,
    
    # Inverter telemetria
    "grid_power": 0,          # W (Register 619 - Külső Grid CT)
    "ups_load_power": 0,      # W (Register 643 - UPS terhelés)
    "pv_power": 0,            # W (Register 175 - Napelemes termelés)
    "battery_power": 0,       # W (Register 590 - Akku teljesítmény)
    "battery_soc": 0,         # %
    "charger_power": 0,       # W (Számított nem UPS fogyasztás: Reg 619 - Reg 607)
    
    # Töltő telemetria
    "voltages": [0.0, 0.0, 0.0],  # L1, L2, L3 (V)
    "currents": [0.0, 0.0, 0.0],  # L1, L2, L3 (A)
    "energy_total": 0.0,          # kWh
    "temperature_internal": 0.0,  # °C
    "pull_plug": False,           # Csatlakozó állapota
    "charging_active": False,     # BLE szerinti futás állapot
    
    # Vezérlési paraméterek (config.json-ból töltve)
    "start_soc": 100,
    "stop_import_limit": 2000,
    "grid_charge_duration_minutes": 30,
    "house_power_limit_w": 3000,
    "control_mode": "monitoring",
    "persist_mode_on_restart": False,
    "charger_max_amps": 16,
    "active_current_limit": 0,
    "force_submode": "manual_stop",
    "schedule_solar_auto": False,
    "auto_enabled": False,
    "schedule_enabled": False,
    "forced_schedule": [
        {"day": "Hétfő", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Kedd", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Szerda", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Csütörtök", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Péntek", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 16, "override_auto": True},
        {"day": "Szombat", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 10, "override_auto": True},
        {"day": "Vasárnap", "enabled": False, "start": "08:00", "stop": "16:00", "amps": 10, "override_auto": True}
    ],
    
    # Logok és hibaüzenetek
    "logs": [],
    "error_message": "",
    "cooldown_until": 0.0,        # Timestamp a következő próbálkozásig
    
    # Szimulációs és tesztelési paraméterek
    "simulation": False,
    "sim_external_session": False,
    "sim_custom_time": "",
    "sim_custom_day": "",
    "assertion_status": "OK",
    "last_assertion_error": "",
    "apply_with_stop": False,
    "apply_with_restart": False,
    "reset_limit": False,
    "manual_start_requested": False,
    "restart_pending_start": False,
    "web_auth_enabled": True
}

state_lock = threading.Lock()
logs_limit = 50
ble_command_queue = None      # Aszinkron sor a BLE parancsoknak

# Globális BLE pufferek és állapotok a kézfogáshoz
ble_rx_buffer = bytearray()
ble_state = "INIT"  # Állapotok: "INIT", "SENT_IDENTITY_ACK", "IDENTITY_ACKED", "SENT_LOGIN", "LOGGED_IN"
login_acknowledged = False
last_identity_ack_time = 0.0
last_login_time = 0.0
active_ble_client = None      # Aktív BleakClient objektum a callbackekhez
ble_auth_event = None
ble_auth_status = None

# Dinamikus BESEN BLE azonosítók (a parancsok aláírásához)
charger_serial = bytearray([0x30, 0x99, 0x83, 0x18, 0x21, 0x29, 0x44, 0x19])
charger_password = bytearray([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])

def to_signed_16(val):
    return val if val < 32768 else val - 65536

# --- KONFIGURÁCIÓ KEZELÉS ---
def load_config():
    global shared_state, CHARGER_NAME, CHARGER_MAC, charger_password
    global INVERTER_IP, INVERTER_PORT, LOGGER_SERIAL, HTTP_PORT, WEB_AUTH_ENABLED, WEB_PASSWORD
    config = DEFAULT_CONFIG.copy()
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception as e:
            print(f"Hiba a konfigurációs fájl beolvasásakor: {e}. Alapértelmezett értékek használata.")
            
    # Állapot frissítése
    with state_lock:
        shared_state["start_soc"] = int(config["start_soc"])
        shared_state["stop_import_limit"] = int(config["stop_import_limit"])
        shared_state["grid_charge_duration_minutes"] = int(config["grid_charge_duration_minutes"])
        shared_state["house_power_limit_w"] = int(config["house_power_limit_w"])
        shared_state["persist_mode_on_restart"] = bool(config["persist_mode_on_restart"])
        shared_state["charger_max_amps"] = int(config.get("charger_max_amps", 16))
        shared_state["force_submode"] = config.get("force_submode", "manual_stop")
        shared_state["schedule_solar_auto"] = bool(config.get("schedule_solar_auto", False))
        shared_state["web_auth_enabled"] = bool(config.get("web_auth_enabled", True))
        
        # Győződjünk meg róla, hogy minden napnak van override_auto mezője
        sched = config.get("forced_schedule", DEFAULT_CONFIG["forced_schedule"])
        for day_item in sched:
            if "override_auto" not in day_item:
                day_item["override_auto"] = True
        shared_state["forced_schedule"] = sched
        
        # Indulási mód és flag-ek beállítása a persist_mode_on_restart flag alapján
        if config["persist_mode_on_restart"]:
            shared_state["control_mode"] = config.get("control_mode", "monitoring")
            if "auto_enabled" in config:
                shared_state["auto_enabled"] = bool(config["auto_enabled"])
            else:
                shared_state["auto_enabled"] = (config.get("control_mode") == "auto")
            if "schedule_enabled" in config:
                shared_state["schedule_enabled"] = bool(config["schedule_enabled"])
            else:
                shared_state["schedule_enabled"] = (config.get("control_mode") == "schedule")
        else:
            shared_state["control_mode"] = "monitoring"
            shared_state["auto_enabled"] = False
            shared_state["schedule_enabled"] = False
            shared_state["force_submode"] = "schedule"
            
    # Töltő BLE paraméterek betöltése
    CHARGER_NAME = config.get("charger_name", "ACP#DefaultName")
    CHARGER_MAC = config.get("charger_mac", "00:11:22:33:44:55")
    
    # Inverter paraméterek betöltése
    INVERTER_IP = config.get("inverter_ip", "192.168.0.100")
    INVERTER_PORT = int(config.get("inverter_port", 8899))
    LOGGER_SERIAL = int(config.get("logger_serial", 1234567890))
    HTTP_PORT = int(config.get("http_port", 8080))
    
    # Webes hitelesítés paraméterek betöltése
    WEB_AUTH_ENABLED = bool(config.get("web_auth_enabled", True))
    WEB_PASSWORD = str(config.get("web_password", "admin"))
    
    # Jelszó konverzió (Hex vagy plain text)
    pwd_str = config.get("charger_password", "FFFFFFFFFFFF")
    try:
        # Ha 12 karakter hosszú hexadecimális karakterlánc
        if len(pwd_str) == 12 and all(c in "0123456789abcdefABCDEF" for c in pwd_str):
            charger_password = bytearray(bytes.fromhex(pwd_str))
        else:
            # Sima szöveges PIN kód
            raw_pwd = pwd_str.encode('utf-8')
            temp = bytearray(raw_pwd)
            while len(temp) < 6:
                temp.append(0xFF)
            charger_password = temp[:6]
    except Exception as e:
        charger_password = bytearray([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
        print(f"Hiba a jelszó feldolgozásakor: {e}. Alapértelmezett jelszó használata.")

def save_config_file():
    with state_lock:
        try:
            # Megpróbáljuk visszaalakítani olvasható szöveggé (pl. PIN kód)
            decoded = charger_password.decode('utf-8', errors='strict')
            # Ellenőrizzük, hogy csak betűkből/számokból áll-e és nem default-e
            if decoded.isalnum() and len(decoded) <= 6:
                pwd_to_save = decoded
            else:
                pwd_to_save = charger_password.hex().upper()
        except Exception:
            pwd_to_save = charger_password.hex().upper()

        config_data = {
            "inverter_ip": INVERTER_IP,
            "inverter_port": INVERTER_PORT,
            "logger_serial": LOGGER_SERIAL,
            "http_port": HTTP_PORT,
            "start_soc": shared_state["start_soc"],
            "stop_import_limit": shared_state["stop_import_limit"],
            "grid_charge_duration_minutes": shared_state["grid_charge_duration_minutes"],
            "house_power_limit_w": shared_state["house_power_limit_w"],
            "control_mode": shared_state["control_mode"],
            "persist_mode_on_restart": shared_state["persist_mode_on_restart"],
            "charger_name": CHARGER_NAME,
            "charger_mac": CHARGER_MAC,
            "charger_password": pwd_to_save,
            "charger_max_amps": shared_state["charger_max_amps"],
            "force_submode": shared_state["force_submode"],
            "schedule_solar_auto": shared_state["schedule_solar_auto"],
            "forced_schedule": shared_state["forced_schedule"],
            "auto_enabled": shared_state["auto_enabled"],
            "schedule_enabled": shared_state["schedule_enabled"],
            "web_auth_enabled": WEB_AUTH_ENABLED,
            "web_password": WEB_PASSWORD
        }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log_message(f"Hiba a konfiguráció mentésekor: {e}")

# --- RENDERSZ NAPLÓZÁS ---
def log_message(msg):
    timestamp = time.strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    with state_lock:
        shared_state["logs"].append(full_msg)
        if len(shared_state["logs"]) > logs_limit:
            shared_state["logs"].pop(0)

# --- WEB DASHBOARD HTML & JS ---
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="hu">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Deye & BESEN Integrált Vezérlő</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(16, 16, 20, 0.3);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-color: #f1f5f9;
            --text-muted: #94a3b8;
            --primary: #38bdf8;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Outfit', sans-serif;
        }

        html, body {
            margin: 0;
            padding: 0;
            width: 100%;
            max-width: 100%;
            overflow-x: hidden;
        }

        body {
            background-color: var(--bg-color);
            background-image: url('/background.png');
            background-attachment: fixed;
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
            color: var(--text-color);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        header {
            padding: 0.8rem 1.2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            background: rgba(15, 23, 42, 0.6);
            backdrop-filter: blur(10px);
        }

        .logo-section h1 {
            font-size: 1.25rem;
            font-weight: 800;
            background: linear-gradient(135deg, #38bdf8 0%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .logo-section p {
            font-size: 0.75rem;
            color: var(--text-muted);
        }

        .header-status-container {
            display: flex;
            flex-direction: row;
            align-items: center;
            gap: 1.2rem;
        }

        .status-divider {
            width: 1px;
            height: 24px;
            background-color: var(--border-color);
        }

        .status-group {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .status-group-label {
            font-size: 0.7rem;
            color: var(--text-muted);
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-right: 0.2rem;
        }

        .badge {
            padding: 0.35rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 0.4rem;
            border: 1px solid var(--border-color);
            width: 120px;
            justify-content: center;
        }

        .badge-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--text-muted);
        }

        .badge.active {
            background: rgba(16, 185, 129, 0.1);
            border-color: rgba(16, 185, 129, 0.3);
            color: #34d399;
        }

        .badge.active .badge-dot {
            background-color: var(--success);
            box-shadow: 0 0 8px var(--success);
        }

        .badge.inactive {
            background: rgba(239, 68, 68, 0.1);
            border-color: rgba(239, 68, 68, 0.3);
            color: #f87171;
        }

        .badge.inactive .badge-dot {
            background-color: var(--danger);
            box-shadow: 0 0 8px var(--danger);
        }

        .badge.off {
            background: rgba(148, 163, 184, 0.15);
            border-color: rgba(148, 163, 184, 0.3);
            color: #94a3b8;
        }

        .badge.off .badge-dot {
            background-color: #64748b;
            box-shadow: none;
        }

        /* Egységes cián szín az aktív automatizmus jelvényeknek feltűnő glow hatással */
        #badge-toggle-auto.active, #badge-toggle-schedule.active {
            background: rgba(34, 211, 238, 0.25);
            border-color: rgba(34, 211, 238, 0.9);
            color: #22d3ee;
            box-shadow: 0 0 16px rgba(34, 211, 238, 0.7), 0 0 32px rgba(34, 211, 238, 0.35), inset 0 0 6px rgba(34, 211, 238, 0.4);
            font-weight: 700;
        }
        #badge-toggle-auto.active .badge-dot, #badge-toggle-schedule.active .badge-dot {
            background-color: #22d3ee;
            box-shadow: 0 0 8px #ffffff, 0 0 18px #22d3ee, 0 0 28px #22d3ee;
        }

        main {
            flex-grow: 1;
            padding: 1rem;
            max-width: 1400px;
            width: 100%;
            margin: 0 auto;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
            align-content: start;
        }

        /* Mobil státusz sáv stílusa */
        .status-bar-mobile {
            display: none;
            position: sticky;
            top: 54px; /* A header magassága után */
            z-index: 99;
            background: rgba(15, 23, 42, 0.9);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border-bottom: 1px solid var(--border-color);
            padding: 0.4rem 0.8rem;
            justify-content: space-around;
            align-items: center;
            font-size: 0.75rem;
            width: 100%;
        }

        .status-dot-item {
            display: flex;
            align-items: center;
            gap: 0.3rem;
            color: var(--text-muted);
            font-weight: 600;
        }

        .status-dot-item .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: #64748b;
        }

        .status-dot-item.active {
            color: var(--success);
        }

        .status-dot-item.active .dot {
            background-color: var(--success);
            box-shadow: 0 0 6px var(--success);
        }

        .status-dot-item.inactive {
            color: var(--danger);
        }

        .status-dot-item.inactive .dot {
            background-color: var(--danger);
            box-shadow: 0 0 6px var(--danger);
        }

        /* Automatikus módok színei a státusz sávban (cián szín) */
        .status-dot-item.auto-active.active {
            color: #22d3ee;
        }
        .status-dot-item.auto-active.active .dot {
            background-color: #22d3ee;
            box-shadow: 0 0 8px #22d3ee;
        }

        /* Hamburger gomb */
        .hamburger-btn {
            display: none;
            background: transparent;
            border: none;
            color: var(--text-color);
            font-size: 1.6rem;
            cursor: pointer;
            padding: 0.2rem 0.5rem;
            z-index: 110;
            outline: none;
        }

        /* Mobil menü overlay */
        .mobile-menu-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(11, 15, 25, 0.97);
            backdrop-filter: blur(15px);
            -webkit-backdrop-filter: blur(15px);
            z-index: 105;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 1.2rem;
            padding: 2rem;
            transition: opacity 0.25s ease;
            opacity: 0;
            pointer-events: none;
        }

        .mobile-menu-overlay.open {
            display: flex;
            opacity: 1;
            pointer-events: auto;
        }

        .menu-item {
            font-size: 1.35rem;
            font-weight: 600;
            color: var(--text-muted);
            cursor: pointer;
            transition: all 0.2s;
            padding: 0.6rem 1.5rem;
            border-radius: 8px;
            width: 80%;
            max-width: 300px;
            text-align: center;
            border: 1px solid transparent;
        }

        .menu-item:hover, .menu-item.active {
            color: var(--primary);
            background: rgba(56, 189, 248, 0.08);
            border-color: rgba(56, 189, 248, 0.2);
        }

        .menu-item.logout-item {
            color: var(--danger);
            margin-top: 1rem;
        }

        .menu-item.logout-item:hover {
            background: rgba(239, 68, 68, 0.08);
            border-color: rgba(239, 68, 68, 0.2);
        }

        .menu-divider {
            width: 50%;
            height: 1px;
            background: var(--border-color);
            margin: 0.5rem 0;
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1rem;
            backdrop-filter: blur(12px);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
            display: flex;
            flex-direction: column;
            gap: 0.8rem;
            min-height: 580px;
        }

        .card-title {
            font-size: 1rem;
            font-weight: 600;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.3rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .metric-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.6rem;
        }

        .metric-box {
            background: rgba(15, 23, 42, 0.4);
            border: 1px solid var(--border-color);
            padding: 0.6rem 0.8rem;
            border-radius: 8px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        .metric-label {
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-bottom: 0.2rem;
            display: inline-flex;
            align-items: center;
        }

        .metric-value {
            font-size: 1.35rem;
            font-weight: 800;
        }

        .metric-value-sub {
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 0.1rem;
            font-weight: 400;
        }

        .metric-unit {
            font-size: 0.9rem;
            font-weight: 400;
            color: var(--text-muted);
            margin-left: 0.2rem;
        }

        .surplus-val {
            color: var(--success);
            text-shadow: 0 0 10px rgba(16, 185, 129, 0.1);
        }

        .consumption-val {
            color: var(--danger);
        }

        /* Vezérlés panel */
        .control-panel {
            grid-column: span 2;
            display: flex;
            flex-direction: column;
            gap: 0.8rem;
        }

        .mode-selector {
            display: flex;
            gap: 0.4rem;
            background: rgba(15, 23, 42, 0.5);
            padding: 0.25rem;
            border-radius: 6px;
            border: 1px solid var(--border-color);
        }

        .mode-btn {
            flex: 1;
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 0.5rem;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s;
        }

        .mode-btn.active {
            background: var(--primary);
            color: #0b0f19;
            box-shadow: 0 0 10px rgba(56, 189, 248, 0.3);
        }

        .mode-config-card {
            display: none;
            flex-direction: column;
            gap: 0.8rem;
        }

        .config-form {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.8rem;
        }

        .input-row {
            grid-column: span 2;
            display: flex;
            flex-direction: column;
            gap: 0.4rem;
        }

        .input-row label {
            font-size: 0.8rem;
            color: var(--text-muted);
            display: inline-flex;
            align-items: center;
        }

        input[type="range"] {
            -webkit-appearance: none;
            width: 100%;
            background: rgba(255, 255, 255, 0.1);
            height: 6px;
            border-radius: 3px;
            outline: none;
            margin: 0.4rem 0;
        }

        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            appearance: none;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: var(--primary);
            cursor: pointer;
            box-shadow: 0 0 8px var(--primary);
            transition: transform 0.1s;
        }

        input[type="range"]::-webkit-slider-thumb:hover {
            transform: scale(1.2);
        }
        
        .submode-selector {
            display: flex;
            gap: 0.4rem;
            margin-bottom: 0.4rem;
            background: rgba(15, 23, 42, 0.3);
            padding: 0.2rem;
            border-radius: 6px;
            border: 1px solid var(--border-color);
        }
        
        .submode-btn {
            flex: 1;
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 0.4rem;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.75rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .submode-btn.active {
            background: var(--primary);
            color: #0b0f19;
            box-shadow: 0 0 8px rgba(56, 189, 248, 0.2);
        }

        .manual-btn-group {
            display: flex;
            gap: 0.8rem;
        }

        .action-btn {
            flex: 1;
            padding: 0.6rem;
            border-radius: 8px;
            border: 1px solid transparent;
            font-weight: 700;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s;
            color: white;
            text-align: center;
        }
        
        .action-btn-start {
            background: rgba(16, 185, 129, 0.12);
            border-color: rgba(16, 185, 129, 0.4);
            color: #34d399;
            box-shadow: 0 0 10px rgba(16, 185, 129, 0.1);
        }
        
        .action-btn-start:hover {
            background: rgba(16, 185, 129, 0.25);
            border-color: rgba(16, 185, 129, 0.65);
            box-shadow: 0 0 15px rgba(16, 185, 129, 0.25);
        }
        
        .action-btn-stop {
            background: rgba(239, 68, 68, 0.12);
            border-color: rgba(239, 68, 68, 0.4);
            color: #f87171;
            box-shadow: 0 0 10px rgba(239, 68, 68, 0.1);
        }
        
        .action-btn-stop:hover {
            background: rgba(239, 68, 68, 0.25);
            border-color: rgba(239, 68, 68, 0.65);
            box-shadow: 0 0 15px rgba(239, 68, 68, 0.25);
        }

        .action-btn-soft {
            background: rgba(245, 158, 11, 0.12);
            border-color: rgba(245, 158, 11, 0.4);
            color: #fbbf24;
            box-shadow: 0 0 10px rgba(245, 158, 11, 0.1);
        }

        .action-btn-soft:hover {
            background: rgba(245, 158, 11, 0.25);
            border-color: rgba(245, 158, 11, 0.65);
            box-shadow: 0 0 15px rgba(245, 158, 11, 0.25);
        }
        
        .action-btn.active-manual {
            background: rgba(255, 255, 255, 0.18) !important;
            border-color: rgba(255, 255, 255, 0.7) !important;
            color: #ffffff !important;
            box-shadow: 0 0 15px rgba(255, 255, 255, 0.3) !important;
        }

        .schedule-table {
            display: flex;
            flex-direction: column;
            gap: 0.3rem;
        }

        .schedule-row {
            display: grid;
            grid-template-columns: 60px 24px 65px 65px 1fr 150px;
            align-items: center;
            gap: 0.3rem;
            background: rgba(15, 23, 42, 0.3);
            padding: 0.2rem 0.5rem;
            border-radius: 6px;
            border: 1px solid var(--border-color);
        }
        
        .schedule-row label {
            font-size: 0.8rem;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
        }
        
        .schedule-row input[type="checkbox"] {
            width: 16px;
            height: 16px;
            cursor: pointer;
        }
        
        .schedule-row input[type="time"] {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-color);
            color: white;
            border-radius: 6px;
            padding: 0.25rem;
            font-size: 0.75rem;
            text-align: center;
            outline: none;
            width: 100%;
        }
        
        .schedule-row .slider-container {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            width: 100%;
        }
        
        .schedule-row .slider-container input[type="range"] {
            flex-grow: 1;
            margin: 0;
        }
        
        .schedule-row .slider-container span {
            font-size: 0.75rem;
            width: 30px;
            text-align: right;
            color: var(--primary);
            font-weight: 600;
        }

        .slider-val-label {
            font-weight: 800;
            color: var(--primary);
            text-shadow: 0 0 6px rgba(56, 189, 248, 0.2);
        }

        .input-group {
            display: flex;
            flex-direction: column;
            gap: 0.3rem;
        }

        .input-group label {
            font-size: 0.75rem;
            color: var(--text-muted);
            display: inline-flex;
            align-items: center;
        }

        .input-group input {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-color);
            padding: 0.45rem;
            border-radius: 6px;
            color: var(--text-color);
            font-weight: 600;
            font-size: 0.85rem;
            outline: none;
            text-align: center;
        }

        .input-group input:focus {
            border-color: var(--primary);
        }

        .checkbox-group {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            font-size: 0.8rem;
            color: var(--text-muted);
            cursor: pointer;
            user-select: none;
            margin-top: 0.1rem;
        }

        .checkbox-group label {
            display: inline-flex;
            align-items: center;
        }

        .checkbox-group input {
            cursor: pointer;
            width: 14px;
            height: 14px;
        }

        .save-btn {
            grid-column: span 2;
            background: linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);
            border: none;
            color: white;
            padding: 0.6rem;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
            transition: opacity 0.2s, transform 0.1s;
        }

        .save-btn:hover {
            opacity: 0.95;
            transform: translateY(-1px);
        }

        .save-btn:active {
            transform: translateY(0);
        }

        /* Fázis és feszültség adatok */
        .phase-table {
            width: 100%;
            border-collapse: collapse;
        }

        .phase-table th, .phase-table td {
            padding: 0.4rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }

        .phase-table th {
            color: var(--text-muted);
            font-size: 0.7rem;
            text-transform: uppercase;
            display: table-cell; /* Nem flex */
        }

        .phase-table td {
            font-size: 0.85rem;
            font-weight: 600;
        }

        /* Hiba és Cooldown üzenetek */
        .alert-box {
            padding: 0.6rem;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
            display: none;
        }

        .alert-error {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #f87171;
        }

        .alert-warning {
            background: rgba(245, 158, 11, 0.15);
            border: 1px solid rgba(245, 158, 11, 0.3);
            color: #fbbf24;
        }

        /* Log console */
        .console-container {
            grid-column: span 2;
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 0.8rem;
        }

        .console-title {
            font-size: 0.8rem;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 0.4rem;
        }

        .console-box {
            height: 120px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 0.75rem;
            color: var(--primary);
            display: flex;
            flex-direction: column;
            gap: 0.2rem;
        }

        .console-line {
            white-space: pre-wrap;
            border-left: 2px solid rgba(56, 189, 248, 0.3);
            padding-left: 0.4rem;
        }

        footer {
            padding: 0.8rem;
            text-align: center;
            font-size: 0.7rem;
            color: var(--text-muted);
            border-top: 1px solid var(--border-color);
            background: rgba(15, 23, 42, 0.4);
        }

        /* Tooltip stílusok */
        .tooltip-container {
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            margin-left: 0.35rem;
            color: #64748b; /* Slate-400 */
            font-size: 0.8rem;
            vertical-align: middle;
            transition: color 0.2s;
        }

        .tooltip-container:hover {
            color: #38bdf8; /* Sky-400 */
        }

        .tooltip-text {
            visibility: hidden;
            width: 250px;
            background-color: #0f172a; /* Slate-900 */
            color: #e2e8f0; /* Slate-200 */
            text-align: left;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 8px;
            padding: 0.6rem 0.8rem;
            position: absolute;
            z-index: 100;
            bottom: 130%; /* Megjelenítés a szöveg felett */
            left: 50%;
            transform: translateX(-50%);
            opacity: 0;
            transition: opacity 0.2s, transform 0.2s;
            font-size: 0.75rem;
            font-weight: normal;
            line-height: 1.4;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6);
            pointer-events: none;
            white-space: normal;
        }

        /* Kis nyíl a buborék aljára */
        .tooltip-text::after {
            content: "";
            position: absolute;
            top: 100%;
            left: 50%;
            margin-left: -5px;
            border-width: 5px;
            border-style: solid;
            border-color: rgba(15, 23, 42, 1) transparent transparent transparent;
        }

        .tooltip-container:hover .tooltip-text {
            visibility: visible;
            opacity: 1;
            transform: translateX(-50%) translateY(-2px);
        }

        /* Balra nyíló (jobbra igazított) tooltip a jobb szélen lévő ikonokhoz */
        .tooltip-align-left .tooltip-text {
            left: auto;
            right: 0;
            transform: translateX(0);
        }
        
        .tooltip-align-left .tooltip-text::after {
            left: auto;
            right: 15px;
            margin-left: 0;
        }
        
        .tooltip-align-left:hover .tooltip-text {
            transform: translateX(0) translateY(-2px);
        }

        /* Mobil töréspontok és stílusok (megemelt határral) */
        @media (max-width: 1024px) {
            main {
                grid-template-columns: 1fr;
                padding: 0.4rem;
            }
            header {
                position: sticky;
                top: 0;
                z-index: 100;
                flex-direction: row !important;
                justify-content: space-between;
                align-items: center;
                gap: 0.8rem;
                padding: 0.8rem 1rem;
                background: rgba(15, 23, 42, 0.85) !important;
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
            }
            .logo-section h1 {
                font-size: 1.2rem;
            }
            .logo-section p {
                font-size: 0.7rem;
            }
            .header-status-container {
                display: none !important;
            }
            .hamburger-btn {
                display: block !important;
            }
            .status-bar-mobile {
                display: flex !important;
            }
            .card {
                padding: 0.5rem;
                min-height: auto;
            }
            .mode-selector {
                /* Elrejtjük a kártyán belüli tabválasztót mobilon, mert a hamburger menü vezérli */
                display: none !important;
            }
            .config-form {
                grid-template-columns: 1fr !important;
                gap: 0.6rem;
            }
            .config-form > .input-group,
            .config-form > div {
                grid-column: span 1 !important;
            }
            .save-btn {
                grid-column: span 1 !important;
                width: 100%;
            }
            .mode-btn {
                padding: 0.4rem 0.2rem;
                font-size: 0.75rem;
            }
            .manual-btn-group > div {
                flex-direction: column !important;
                gap: 0.6rem !important;
            }
            
            /* Ütemezési naptár 3 szintes mobil nézete (prevent stretching with optimized columns, smaller gap and padding) */
            .schedule-row {
                grid-template-columns: 65px 22px 1fr 1fr;
                grid-template-rows: auto auto auto;
                justify-content: space-between;
                gap: 0.4rem;
                padding: 0.4rem;
            }
            .schedule-row .slider-container {
                grid-column: span 4;
            }
            .schedule-row .slider-container input[type="range"] {
                min-width: 0;
                width: 100%;
            }
            .schedule-row input[type="time"] {
                width: 65px !important;
                max-width: 65px !important;
                padding: 0.2rem 0.1rem;
            }
            .schedule-row div:last-child {
                grid-column: span 4;
                justify-content: flex-start;
            }
            
            .login-container {
                padding: 1.5rem;
            }

            /* Tooltipek lefelé történő megjelenítése mobilon és szélesség korlátozása (jobbra kilógás ellen) */
            .tooltip-text {
                bottom: auto !important;
                top: 130% !important;
                width: 220px !important;
            }
            .tooltip-text::after {
                top: auto !important;
                bottom: 100% !important;
                border-color: transparent transparent #0f172a transparent !important;
            }
            .tooltip-container:hover .tooltip-text {
                transform: translateX(-50%) translateY(2px) !important;
            }

            /* Jobbra igazított tooltipek a jobb szélhez közeli ikonokhoz (balra terjeszkednek) */
            #config-auto .tooltip-text,
            #config-schedule .tooltip-text,
            #config-force .tooltip-text,
            .metric-grid > div:nth-child(even) .tooltip-text {
                left: auto !important;
                right: -10px !important;
                transform: translateY(2px) !important;
            }
            #config-auto .tooltip-text::after,
            #config-schedule .tooltip-text::after,
            #config-force .tooltip-text::after,
            .metric-grid > div:nth-child(even) .tooltip-text::after {
                left: auto !important;
                right: 15px !important;
                margin-left: 0 !important;
            }
        }
    </style>
</head>
<body>

    <header>
        <div class="logo-section">
            <h1>Deye & BESEN</h1>
            <p>Helyi Napelemes Töltésvezérlő és Felügyelet</p>
        </div>
        <button class="hamburger-btn" id="hamburger-btn" onclick="toggleMobileMenu()">☰</button>
        <div class="header-status-container">
            <div class="status-group">
                <span class="status-group-label">Kapcsolatok:</span>
                <div id="badge-inverter" class="badge inactive">
                    <div class="badge-dot"></div>
                    Deye Wi-Fi
                </div>
                <div id="badge-charger" class="badge inactive">
                    <div class="badge-dot"></div>
                    BESEN BLE
                </div>
            </div>
            <div class="status-divider"></div>
            <div class="status-group">
                <span class="status-group-label">Automatizmusok:</span>
                <div id="badge-toggle-auto" class="badge off">
                    <div class="badge-dot"></div>
                    Solar Auto
                </div>
                <div id="badge-toggle-schedule" class="badge off">
                    <div class="badge-dot"></div>
                    Ütemezett
                </div>
            </div>
            <div class="status-divider" id="logout-divider" style="display: none;"></div>
            <div class="status-group" id="logout-group" style="display: none;">
                <button onclick="logout()" class="logout-btn" style="
                    background: rgba(239, 68, 68, 0.12);
                    border: 1px solid rgba(239, 68, 68, 0.3);
                    color: #f87171;
                    padding: 0.4rem 0.8rem;
                    border-radius: 6px;
                    font-size: 0.85rem;
                    font-weight: 600;
                    cursor: pointer;
                    display: inline-flex;
                    align-items: center;
                    gap: 0.4rem;
                    transition: all 0.3s ease;
                " onmouseover="this.style.background='rgba(239, 68, 68, 0.25)';" onmouseout="this.style.background='rgba(239, 68, 68, 0.12)';">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg>
                    Kijelentkezés
                </button>
            </div>
        </div>
    </header>

    <!-- Mobil tapadós státusz sáv -->
    <div class="status-bar-mobile" id="status-bar-mobile">
        <div class="status-dot-item" id="mobile-status-deye">
            <div class="dot"></div>
            <span>Deye</span>
        </div>
        <div class="status-dot-item" id="mobile-status-besen">
            <div class="dot"></div>
            <span>BESEN</span>
        </div>
        <div class="status-dot-item auto-active" id="mobile-status-auto">
            <div class="dot"></div>
            <span>Auto</span>
        </div>
        <div class="status-dot-item auto-active" id="mobile-status-schedule">
            <div class="dot"></div>
            <span>Ütemezett</span>
        </div>
    </div>

    <!-- Mobil navigációs menü overlay -->
    <div id="mobile-menu" class="mobile-menu-overlay">
        <div class="menu-item active" id="menu-item-auto" onclick="showSection('auto')">Auto Solar</div>
        <div class="menu-item" id="menu-item-schedule" onclick="showSection('schedule')">Ütemezett</div>
        <div class="menu-item" id="menu-item-force" onclick="showSection('force')">Kézi mód</div>
        <div class="menu-item" id="menu-item-measurements" onclick="showSection('measurements')">Mérések</div>
        <div class="menu-item" id="menu-item-log" onclick="showSection('log')">Napló</div>
        <div class="menu-divider"></div>
        <div class="menu-item logout-item" id="mobile-menu-logout" style="display: none;" onclick="logout()">Kijelentkezés</div>
    </div>

    <main>
        <!-- BAL OLDAL: ÁLLAPOTOK ÉS BEÁLLÍTÁSOK -->
        <div class="card">
            <div class="card-title">Rendszervezérlés & Konfiguráció</div>
            
            <div class="alert-box alert-error" id="error-box"></div>
            <div class="alert-box alert-warning" id="cooldown-box"></div>
 
            <div class="control-panel">
                <div class="mode-selector">
                    <button class="mode-btn" id="btn-auto" onclick="selectTab('auto')">Auto (Solar)</button>
                    <button class="mode-btn" id="btn-schedule" onclick="selectTab('schedule')">Ütemezett</button>
                    <button class="mode-btn" id="btn-force" onclick="selectTab('force')">Force (Kézi)</button>
                </div>

                <!-- Automatikus mód panel -->
                <div class="mode-config-card" id="config-auto">
                    <form class="config-form" onsubmit="saveAutoConfig(event)">
                        <div class="checkbox-group" style="grid-column: span 2; margin-bottom: 1rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.8rem; display: flex; align-items: center; gap: 0.5rem;">
                            <input type="checkbox" id="auto_enabled" onchange="saveAutoEnabled()" style="cursor:pointer; width: 1.2rem; height: 1.2rem;">
                            <label for="auto_enabled" style="font-size: 1.05rem; cursor:pointer; font-weight:700; color: #34d399; display: inline-flex; align-items: center;">
                                Napelemes (Solar Auto) mód bekapcsolása
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Ha be van kapcsolva, a rendszer figyeli a ház akkumulátorának töltöttségét és a napelemes termelést a töltés automatikus indításához.</span></span>
                            </label>
                        </div>
                        <div class="input-row" style="grid-column: span 2; margin-bottom: 0.5rem;">
                            <div id="auto-slider-wrapper" style="margin-bottom: 0.5rem;">
                                <div style="display:flex; justify-content:space-between; align-items:center;">
                                    <label for="auto_charger_max_amps" style="display: inline-flex; align-items: center;">
                                        Maximális töltőáram
                                        <span class="tooltip-container">ⓘ<span class="tooltip-text">A szoftver által megengedett legnagyobb töltőáram (6-16 Amper). Csak akkor érvényes, ha a szoftver szabályozza az áramot.</span></span>
                                    </label>
                                    <span><span class="slider-val-label" id="auto-amps-val">16</span> A</span>
                                </div>
                                <input type="range" id="auto_charger_max_amps" min="6" max="16" step="1" oninput="checkAutoAmpsChanged()">
                            </div>
                            <div class="checkbox-group" style="margin-top: 0.3rem;">
                                <input type="checkbox" id="auto_unmanaged_current" onchange="toggleUnmanagedCurrent('auto')">
                                <label for="auto_unmanaged_current" style="font-size: 0.85rem; cursor:pointer; display: inline-flex; align-items: center;">
                                    Töltőáram szoftveres szabályzásának kikapcsolása
                                    <span class="tooltip-container">ⓘ<span class="tooltip-text">Ha bejelöli, a szoftver nem fogja dinamikusan állítani az áramerősséget. Az autó a saját belső beállítása vagy a töltő fizikai gombja szerinti maximális sebességgel fog tölteni.</span></span>
                                </label>
                            </div>
                            <div id="auto-apply-container" style="display:none; gap: 1rem; margin-top:0.8rem; width: 100%;">
                                <button type="button" class="action-btn action-btn-stop" style="padding:0.5rem; font-size:0.8rem;" onclick="applyAutoAmps(true)">Alkalmaz (leállítással)</button>
                                <button type="button" class="action-btn action-btn-start" style="padding:0.5rem; font-size:0.8rem; background:linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);" onclick="applyAutoAmps(false)">Mentés újraindítás nélkül</button>
                            </div>
                        </div>
                        <div class="input-group" style="grid-column: span 2;">
                            <label for="auto_start_soc" style="display: inline-flex; align-items: center;">
                                Indítási akku szint (%)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Az a minimális otthoni akkumulátor töltöttség (SoC %), ami felett a napelemes töltés elindulhat. Az akku szint csökkenése önmagában nem állítja le a töltést (a leállítást a hálózati fogyasztás küszöb vagy az UPS terhelés szabályozza).</span></span>
                            </label>
                            <input type="number" id="auto_start_soc" min="1" max="100">
                        </div>
                        <div class="input-group">
                            <label for="auto_stop_import_limit" style="display: inline-flex; align-items: center;">
                                Hálózati fogyasztás küszöbérték (W)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">A hálózatból vételezett (importált) áram azon szintje, ami felett a töltés leállítási időzítője elindul. Ezzel elkerülhető, hogy borús időben hálózatból töltsük az autót.</span></span>
                            </label>
                            <input type="number" id="auto_stop_import_limit" min="100" max="10000" step="100">
                        </div>
                        <div class="input-group">
                            <label for="auto_grid_charge_duration_minutes" style="display: inline-flex; align-items: center;">
                                Hálózati töltés késleltetett leállítása (perc)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Ha a hálózati fogyasztás meghaladja a küszöbértéket, a rendszer ennyi ideig engedi még a töltést futni (pl. felhőátvonulások áthidalására). A 0 perc azonnali leállítást jelent.</span></span>
                            </label>
                            <input type="number" id="auto_grid_charge_duration_minutes" min="0" max="1440">
                        </div>
                        <div class="input-group" style="grid-column: span 2;">
                            <label for="auto_house_power_limit_w" style="display: inline-flex; align-items: center;">
                                Ház UPS (inverter) túlterhelés-védelem (W)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Az inverter UPS kimenetén mérhető maximális fogyasztás. Ha a ház egyéb fogyasztói miatt a terhelés ezen érték fölé ugrik, a töltés leáll az inverter védelmében. A 0 kikapcsolja a védelmet.</span></span>
                            </label>
                            <input type="number" id="auto_house_power_limit_w" min="0" max="20000" step="100">
                        </div>
                        <div class="checkbox-group" onclick="togglePersistCheckbox()" style="grid-column: span 2; margin-bottom: 0.5rem;">
                            <input type="checkbox" id="auto_persist_mode_on_restart" style="cursor:pointer;">
                            <label for="auto_persist_mode_on_restart" style="cursor:pointer; display: inline-flex; align-items: center;">
                                Beállítások megőrzése áramszünet után
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Ha be van jelölve, a vezérlő program újraindulásakor (pl. áramszünet vagy PC restart után) automatikusan visszaállítja a Solar és Ütemezett módok bekapcsolt állapotát.</span></span>
                            </label>
                        </div>
                        <button type="submit" class="save-btn">Auto Beállítások Mentése</button>
                    </form>
                </div>

                <!-- Ütemezett mód panel -->
                <div class="mode-config-card" id="config-schedule">
                    <div class="checkbox-group" style="margin-bottom: 0.5rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.4rem; display: flex; align-items: center; gap: 0.5rem;">
                        <input type="checkbox" id="schedule_enabled" onchange="saveScheduleEnabled()" style="cursor:pointer; width: 1.2rem; height: 1.2rem;">
                        <label for="schedule_enabled" style="font-size: 1.05rem; cursor:pointer; font-weight:700; color: #c084fc; display: inline-flex; align-items: center;">
                            Időzített töltés bekapcsolása
                            <span class="tooltip-container">ⓘ<span class="tooltip-text">Ha be van kapcsolva, a rendszer a lenti táblázatban beállított napokon és időablakokban automatikusan elindítja a töltést a megadott áramerősséggel.</span></span>
                        </label>
                    </div>
                    <div class="checkbox-group" style="margin-bottom: 0.4rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.3rem;">
                        <input type="checkbox" id="schedule_solar_auto" onchange="saveScheduleSolarAuto()">
                        <label for="schedule_solar_auto" style="font-size:0.9rem; cursor:pointer; font-weight:600; display: inline-flex; align-items: center;">
                            Ütemezés végén Solar Auto mód
                            <span class="tooltip-container">ⓘ<span class="tooltip-text">Ha be van kapcsolva, akkor az időzített időablakokon kívüli időszakokban a rendszer nem állítja le a töltést teljesen, hanem a napelemes (Solar Auto) szabályok alapján vezérli azt.</span></span>
                        </label>
                    </div>
                    <div style="font-size:0.85rem; color:var(--text-muted); text-align:center; margin-bottom: 0.3rem;">
                        Ütemezett heti beállítások
                    </div>
                    <div class="schedule-table" id="schedule-rows-container">
                        <!-- JavaScript tölti fel a napokat -->
                    </div>
                    <button class="save-btn" style="margin-top:0.4rem;" onclick="saveSchedule()">Ütemezési Naptár Mentése</button>
                </div>

                <!-- Kényszerített mód panel -->
                <div class="mode-config-card" id="config-force">
                    <!-- Felülbírálás banner és visszavonás gomb -->
                    <div id="override-banner" style="display: none; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.4); padding: 0.8rem; border-radius: 6px; margin-bottom: 1rem; align-items: center; justify-content: space-between; gap: 1rem; width: 100%;">
                        <span style="font-size: 0.85rem; color: #f87171; font-weight: 600;" id="override-banner-text">Aktív kézi felülbírálás!</span>
                        <button class="action-btn" style="flex: none; padding: 0.4rem 0.8rem; font-size: 0.8rem; background: linear-gradient(135deg, #475569 0%, #334155 100%); color: white; border-radius: 4px; border: none; font-weight: 600; cursor: pointer;" onclick="cancelManualOverride()">Visszavonás</button>
                    </div>

                    <div class="input-row">
                        <div id="force-slider-wrapper" style="margin-bottom: 0.5rem;">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <label for="force_charger_max_amps" style="display: inline-flex; align-items: center;">
                                    Kézi indítás áramkorlátja
                                    <span class="tooltip-container">ⓘ<span class="tooltip-text">Kézi indítás esetén érvényes áramerősség korlát (6-16 Amper).</span></span>
                                </label>
                                <span><span class="slider-val-label" id="force-amps-val">16</span> A</span>
                            </div>
                            <input type="range" id="force_charger_max_amps" min="6" max="16" step="1" oninput="checkForceAmpsChanged()">
                        </div>
                        <div class="checkbox-group" id="force-unmanaged-container" style="margin-top: 0.3rem;">
                            <input type="checkbox" id="force_unmanaged_current" onchange="toggleUnmanagedCurrent('force')">
                            <label for="force_unmanaged_current" style="font-size: 0.85rem; cursor:pointer; display: inline-flex; align-items: center;">
                                Töltőáram szoftveres szabályzásának kikapcsolása
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Ha bejelöli, a kézi töltés a töltő fizikai beállítása szerinti maximális sebességgel fog futni.</span></span>
                            </label>
                        </div>
                        <div id="force-apply-container" style="display:none; gap: 1rem; margin-top:0.8rem; width: 100%; margin-bottom: 0.5rem;">
                            <button type="button" class="action-btn action-btn-start" style="padding:0.5rem; font-size:0.8rem; background:linear-gradient(135deg, #38bdf8 0%, #0284c7 100%); width: 100%;" onclick="applyForceAmpsWithRestart()">Alkalmaz újraindítással</button>
                        </div>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 0.5rem; margin-bottom: 0.3rem;">
                        <span style="font-size: 0.85rem; font-weight: 600; color: var(--text-color);">Kézi vezérlés indítása:</span>
                        <span class="tooltip-container tooltip-align-left" style="font-size: 0.85rem; color: var(--primary); font-weight: 600;">
                            Kézi gombok működése ⓘ
                            <span class="tooltip-text">
                                <strong>Kézi indítás (Start):</strong> Azonnal elindítja a töltést. Amint a töltés leáll (pl. autó tele lett), a felülbírálás megszűnik, és újra az automatizmusok lépnek életbe.<br><br>
                                <strong>Kézi Stop (Hard):</strong> Azonnal leállítja a töltést, és felfüggeszti a Solar/Ütemezett vezérléseket, amíg vissza nem vonja.<br><br>
                                <strong>Soft Stop:</strong> Leállítja az aktuális töltést, de nem írja felül a szabályokat. Az automatizmusok később újra elindíthatják.
                            </span>
                        </span>
                    </div>
                    <div class="manual-btn-group" style="margin-top:0.2rem; display: flex; flex-direction: column; gap: 0.8rem; width: 100%;">
                        <div style="display: flex; gap: 0.8rem; width: 100%;">
                            <button id="btn-manual-charge-start" class="action-btn action-btn-start" style="padding: 1rem; flex: 1;" onclick="triggerForceSubmode('manual_start')">Kézi indítás (Start)</button>
                            <button id="btn-manual-charge-stop" class="action-btn action-btn-stop" style="padding: 1rem; flex: 1;" onclick="triggerForceSubmode('manual_stop')">Kézi Stop (Hard)</button>
                        </div>
                        <button id="btn-soft-stop" class="action-btn action-btn-soft" style="padding: 1rem; width: 100%;" onclick="triggerSoftStop()">Ideiglenes leállítás (Soft Stop)</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- JOBB OLDAL: ÉLŐ TELEMETRIA ÉS MÉRÉSEK -->
        <div class="card">
            <div class="card-title">
                Mérések & Visszacsatolás
                <span id="plug-status" style="font-size:0.8rem; font-weight:normal;"></span>
            </div>

            <div class="metric-grid">
                <div class="metric-box">
                    <div class="metric-label">
                        Hálózati egyenleg (Grid)
                        <span class="tooltip-container">ⓘ<span class="tooltip-text">A közműhálózat felé folyó áram. Negatív érték (zöld): napelemes betáplálás/túltermelés. Pozitív érték (piros): hálózati fogyasztás/vásárlás.</span></span>
                    </div>
                    <div id="grid-power-main" class="metric-value">0 W</div>
                    <div id="grid-power-sub" class="metric-value-sub">(0.000 kW)</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">
                        Napelemes termelés (PV)
                        <span class="tooltip-container">ⓘ<span class="tooltip-text">A napelemek által éppen termelt pillanatnyi teljesítmény Wattban.</span></span>
                    </div>
                    <div id="pv-power-main" class="metric-value">0 W</div>
                    <div id="pv-power-sub" class="metric-value-sub">(0.000 kW)</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">
                        Ház fogyasztása (UPS port)
                        <span class="tooltip-container">ⓘ<span class="tooltip-text">Az inverter UPS kimenetére kötött háztartási eszközök pillanatnyi összfogyasztása.</span></span>
                    </div>
                    <div id="ups-power-main" class="metric-value">0 W</div>
                    <div id="ups-power-sub" class="metric-value-sub">(0.000 kW)</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">
                        Nem UPS ágon lévő fogyasztók
                        <span class="tooltip-container">ⓘ<span class="tooltip-text">A nem az UPS (szünetmentes) kimenetre kötött fogyasztók (pl. autótöltő és egyéb hálózati ág) pillanatnyi összteljesítménye.</span></span>
                    </div>
                    <div id="charger-power-main" class="metric-value">0 W</div>
                    <div id="charger-power-sub" class="metric-value-sub">(0.000 kW)</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">
                        Akkumulátor teljesítmény
                        <span class="tooltip-container">ⓘ<span class="tooltip-text">A ház akkumulátorának töltési (zöld / pozitív) vagy kisütési (piros / negatív) teljesítménye.</span></span>
                    </div>
                    <div id="battery-power-main" class="metric-value">0 W</div>
                    <div id="battery-power-sub" class="metric-value-sub">(0.000 kW)</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">
                        Ház akkumulátor szint (Store)
                        <span class="tooltip-container">ⓘ<span class="tooltip-text">A hibrid inverterhez csatlakoztatott otthoni akkumulátor töltöttsége (SoC %).</span></span>
                    </div>
                    <div id="battery-soc" class="metric-value">0%</div>
                    <div id="battery-soc-label" class="metric-value-sub">Inverter telemetria (Wi-Fi)</div>
                </div>
            </div>

            <div>
                <table class="phase-table">
                    <thead>
                        <tr>
                            <th>Fázis</th>
                            <th>Feszültség</th>
                            <th>
                                Mért töltőáram (Visszacsatolás)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">A töltőkábelen (fázisonként) ténylegesen átfolyó áramerősség élő visszacsatolása az autótöltőtől.</span></span>
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>L1</td>
                            <td id="v1">0.0 V</td>
                            <td id="i1" style="color: var(--primary);">0.00 A</td>
                        </tr>
                        <tr>
                            <td>L2</td>
                            <td id="v2">0.0 V</td>
                            <td id="i2" style="color: var(--primary);">0.00 A</td>
                        </tr>
                        <tr>
                            <td>L3</td>
                            <td id="v3">0.0 V</td>
                            <td id="i3" style="color: var(--primary);">0.00 A</td>
                        </tr>
                    </tbody>
                </table>
                <div style="margin-top: 0.8rem; display: flex; flex-direction: column; gap: 0.4rem; font-size: 0.85rem; color: var(--text-muted);">
                    <div style="display: flex; justify-content: space-between;">
                        <div>
                            Töltési energia összesen:
                            <span class="tooltip-container">ⓘ<span class="tooltip-text">Az aktuális vagy legutóbbi töltési ciklus során az autóba töltött összes energiamennyiség kilowattórában.</span></span>
                            <span id="energy-total" style="color: var(--text-color); font-weight: 600; margin-left: 0.2rem;">0.00 kWh</span>
                        </div>
                        <div>
                            Töltő belső hőmérséklet:
                            <span class="tooltip-container">ⓘ<span class="tooltip-text">A BESEN autótöltő burkolatán belüli elektronika hőmérséklete.</span></span>
                            <span id="temp-internal" style="color: var(--text-color); font-weight: 600; margin-left: 0.2rem;">0.0 °C</span>
                        </div>
                    </div>
                    <div style="display: flex; justify-content: space-between; border-top: 1px solid rgba(255, 255, 255, 0.08); padding-top: 0.4rem; margin-top: 0.2rem;">
                        <div>
                            Töltő kapcsolata (BLE):
                            <span class="tooltip-container">ⓘ<span class="tooltip-text">A vezérlő szoftver és az autótöltő közötti Bluetooth (BLE) kapcsolat élő állapota (Csatlakoztatva / Nincs csatlakoztatva).</span></span>
                            <span id="charger-connection-status" style="font-weight: 600; margin-left: 0.2rem;">Betöltés...</span>
                        </div>
                        <div>
                            Kábel & Töltés állapota:
                            <span class="tooltip-container">ⓘ<span class="tooltip-text">A fizikai csatlakozó és a töltési folyamat élő állapota (pl. Kábel kihúzva, Készenlét, Töltés aktív).</span></span>
                            <span id="plug-status-inline" style="font-weight: 600; margin-left: 0.2rem;">Betöltés...</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- SZIMULÁCIÓS ÉS TESZT PANEL -->
        <div class="card" id="sim-panel-card" style="grid-column: span 2; display: flex; flex-direction: column; gap: 1rem;">
            <div class="card-title" style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid var(--border-color); padding-bottom:0.5rem;">
                Teszt és Szimulációs Vezérlő Panel
                <div style="display:flex; align-items:center; gap:0.5rem;">
                    <input type="checkbox" id="sim_mode_toggle" onchange="toggleSimulationMode()">
                    <label for="sim_mode_toggle" style="font-size:0.85rem; cursor:pointer; color:var(--primary); font-weight: 600;">Szimuláció aktiválása</label>
                </div>
            </div>
            
            <div id="sim-controls-container" style="display:none; flex-direction:column; gap:1.2rem;">
                <div style="display: flex; gap: 1.5rem; align-items: center; background: rgba(15, 23, 42, 0.3); padding: 0.8rem; border-radius: 8px;">
                    <div id="assertion-status-box" class="badge active" style="padding:0.5rem 1rem; font-size:0.85rem; border-radius:6px; background: rgba(16, 185, 129, 0.1); border-color: rgba(16, 185, 129, 0.3); color: #34d399;">
                        <div class="badge-dot" style="background-color: var(--success);"></div>
                        Logikai ellenőrzés: <span id="assertion-badge-text" style="margin-left: 0.2rem;">OK</span>
                    </div>
                    <div id="assertion-error-details" style="font-size:0.85rem; color:var(--danger); font-weight:600; display:none;"></div>
                </div>
                
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:1.5rem;">
                    <!-- Bal szimulációs oszlop -->
                    <div style="display:flex; flex-direction:column; gap:1rem;">
                        <div class="input-row">
                            <div style="display:flex; justify-content:space-between;">
                                <label for="sim_grid_power" style="display: inline-flex; align-items: center;">
                                    Szimulált Hálózati egyenleg (Grid, W)
                                    <span class="tooltip-container">ⓘ<span class="tooltip-text">A ház szimulált hálózati egyenlege (negatív = betáplálás, pozitív = fogyasztás).</span></span>
                                </label>
                                <span id="sim_grid_power_val" style="color:var(--primary); font-weight:600;">1500 W</span>
                            </div>
                            <input type="range" id="sim_grid_power" min="-6000" max="6000" step="100" value="1500" oninput="document.getElementById('sim_grid_power_val').innerText = this.value + ' W'" onchange="sendSimValue('grid_power', this.value)">
                        </div>
                        <div class="input-row">
                            <div style="display:flex; justify-content:space-between;">
                                <label for="sim_pv_power" style="display: inline-flex; align-items: center;">
                                    Szimulált PV Termelés (W)
                                    <span class="tooltip-container">ⓘ<span class="tooltip-text">A napelemek szimulált termelése.</span></span>
                                </label>
                                <span id="sim_pv_power_val" style="color:var(--primary); font-weight:600;">3200 W</span>
                            </div>
                            <input type="range" id="sim_pv_power" min="0" max="8000" step="100" value="3200" oninput="document.getElementById('sim_pv_power_val').innerText = this.value + ' W'" onchange="sendSimValue('pv_power', this.value)">
                        </div>
                        <div class="input-row">
                            <div style="display:flex; justify-content:space-between;">
                                <label for="sim_battery_soc" style="display: inline-flex; align-items: center;">
                                    Szimulált Akkumulátor SoC (%)
                                    <span class="tooltip-container">ⓘ<span class="tooltip-text">A ház szimulált akkumulátorának töltöttségi szintje.</span></span>
                                </label>
                                <span id="sim_battery_soc_val" style="color:var(--primary); font-weight:600;">75%</span>
                            </div>
                            <input type="range" id="sim_battery_soc" min="0" max="100" step="1" value="75" oninput="document.getElementById('sim_battery_soc_val').innerText = this.value + '%'" onchange="sendSimValue('battery_soc', this.value)">
                        </div>
                        <div class="input-row">
                            <div style="display:flex; justify-content:space-between;">
                                <label for="sim_battery_power" style="display: inline-flex; align-items: center;">
                                    Szimulált Akkumulátor Teljesítmény (W)
                                    <span class="tooltip-container">ⓘ<span class="tooltip-text">A ház akkumulátorának szimulált töltési (pozitív) vagy kisütési (negatív) teljesítménye.</span></span>
                                </label>
                                <span id="sim_battery_power_val" style="color:var(--primary); font-weight:600;">-1000 W</span>
                            </div>
                            <input type="range" id="sim_battery_power" min="-3000" max="3000" step="100" value="-1000" oninput="document.getElementById('sim_battery_power_val').innerText = this.value + ' W'" onchange="sendSimValue('battery_power', this.value)">
                        </div>
                        <div class="input-row">
                            <div style="display:flex; justify-content:space-between;">
                                <label for="sim_ups_load_power" style="display: inline-flex; align-items: center;">
                                    Szimulált UPS (Ház) Terhelés (W)
                                    <span class="tooltip-container">ⓘ<span class="tooltip-text">A ház fogyasztóinak szimulált terhelése az UPS kimeneten.</span></span>
                                </label>
                                <span id="sim_ups_load_power_val" style="color:var(--primary); font-weight:600;">450 W</span>
                            </div>
                            <input type="range" id="sim_ups_load_power" min="0" max="6000" step="100" value="450" oninput="document.getElementById('sim_ups_load_power_val').innerText = this.value + ' W'" onchange="sendSimValue('ups_load_power', this.value)">
                        </div>
                    </div>
                    
                    <!-- Jobb szimulációs oszlop -->
                    <div style="display:flex; flex-direction:column; gap:1.2rem; background:rgba(15, 23, 42, 0.3); padding:1rem; border-radius:12px; border:1px solid var(--border-color);">
                        <div style="font-weight:600; color:var(--text-muted); font-size:0.9rem; border-bottom:1px solid var(--border-color); padding-bottom:0.3rem;">Szimulált Hardver Állapotok</div>
                        
                        <div class="checkbox-group">
                            <input type="checkbox" id="sim_pull_plug" onchange="sendSimCheckbox('pull_plug', this.checked)">
                            <label for="sim_pull_plug" style="cursor:pointer; font-size: 0.85rem; display: inline-flex; align-items: center;">
                                Csatlakozó kábel KIHÚZVA (pull_plug)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Szimulálja, hogy a kábel ki van-e húzva a járműből.</span></span>
                            </label>
                        </div>
                        <div class="checkbox-group">
                            <input type="checkbox" id="sim_charging_active" onchange="sendSimCheckbox('charging_active', this.checked)">
                            <label for="sim_charging_active" style="cursor:pointer; font-size: 0.85rem; display: inline-flex; align-items: center;">
                                Töltési folyamat FUT (charging_active)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Szimulálja az aktív töltési folyamatot.</span></span>
                            </label>
                        </div>
                        <div class="checkbox-group">
                            <input type="checkbox" id="sim_external_session" onchange="sendSimCheckbox('sim_external_session', this.checked)">
                            <label for="sim_external_session" style="cursor:pointer; font-size: 0.85rem; display: inline-flex; align-items: center;">
                                Külső indítású töltés
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Szimulálja a mobilappal vagy fizikai gombbal elindított külső töltési folyamatot.</span></span>
                            </label>
                        </div>
                        
                        <div style="display:flex; gap:1rem; margin-top:0.5rem; width:100%;">
                            <div class="input-group" style="flex:1;">
                                <label for="sim_custom_day" style="display: inline-flex; align-items: center;">
                                    Szimulált nap
                                    <span class="tooltip-container">ⓘ<span class="tooltip-text">Felülbírálja az aktuális napot a heti ütemezés teszteléséhez.</span></span>
                                </label>
                                <select id="sim_custom_day" style="background:rgba(15, 23, 42, 0.6); border:1px solid var(--border-color); color:white; border-radius:6px; padding:0.4rem; font-size:0.85rem;" onchange="sendSimText('sim_custom_day', this.value)">
                                    <option value="">Valós nap</option>
                                    <option value="Hétfő">Hétfő</option>
                                    <option value="Kedd">Kedd</option>
                                    <option value="Szerda">Szerda</option>
                                    <option value="Csütörtök">Csütörtök</option>
                                    <option value="Péntek">Péntek</option>
                                    <option value="Szombat">Szombat</option>
                                    <option value="Vasárnap">Vasárnap</option>
                                </select>
                            </div>
                            <div class="input-group" style="flex:1;">
                                <label for="sim_custom_time" style="display: inline-flex; align-items: center;">
                                    Szimulált idő
                                    <span class="tooltip-container">ⓘ<span class="tooltip-text">Felülbírálja az aktuális időpontot (ÓÓ:PP) a heti ütemezés teszteléséhez.</span></span>
                                </label>
                                <input type="text" id="sim_custom_time" placeholder="pl. 14:30" style="padding:0.4rem; font-size:0.85rem; background:rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); color: white;" onchange="sendSimText('sim_custom_time', this.value)">
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ALSÓ SOR: NAPLÓ -->
        <div class="console-container">
            <div class="console-title">Működési Napló</div>
            <div class="console-box" id="console">
                <div class="console-line">Rendszer elindítva. Lekérdezések indítása...</div>
            </div>
        </div>
    </main>

    <footer>
        Deye & BESEN Helyi Töltésoptimalizáló Vezérlő &copy; 2026
    </footer>

    <script>
        let configLoaded = false;
        let currentConfig = {};
        let originalAutoAmps = 16;
        let originalForceAmps = 16;

        function togglePersistCheckbox() {
            const cb = document.getElementById('auto_persist_mode_on_restart');
            cb.checked = !cb.checked;
        }

        function formatPower(val, isGrid, isBattery) {
            const absVal = Math.abs(val);
            let unitMain = " kW";
            let unitSub = " W";
            
            let mainStr = "";
            let subStr = "";
            
            if (absVal >= 1000) {
                mainStr = (absVal / 1000).toFixed(3) + unitMain;
                subStr = "(" + absVal + unitSub + ")";
            } else {
                mainStr = absVal + unitSub;
                subStr = "(" + (absVal / 1000).toFixed(3) + unitMain + ")";
            }
            
            return { main: mainStr, sub: subStr };
        }

        function renderSchedule(scheduleList) {
            const container = document.getElementById('schedule-rows-container');
            container.innerHTML = '';
            if (!scheduleList || scheduleList.length === 0) return;
            
            scheduleList.forEach((sched, index) => {
                const row = document.createElement('div');
                row.className = 'schedule-row';
                
                row.innerHTML = `
                    <label>${sched.day}</label>
                    <input type="checkbox" id="sched_enabled_${index}" ${sched.enabled ? 'checked' : ''}>
                    <input type="time" id="sched_start_${index}" value="${sched.start}">
                    <input type="time" id="sched_stop_${index}" value="${sched.stop}">
                    <div class="slider-container">
                        <input type="range" id="sched_amps_${index}" min="6" max="16" step="1" value="${sched.amps || 16}" oninput="document.getElementById('sched_amps_val_${index}').innerText = this.value + 'A'">
                        <span id="sched_amps_val_${index}">${sched.amps || 16}A</span>
                    </div>
                    <div style="display:flex; align-items:center; gap:0.2rem;">
                        <input type="checkbox" id="sched_override_auto_${index}" ${sched.override_auto ? 'checked' : ''}>
                        <label for="sched_override_auto_${index}" style="font-size:0.75rem; cursor:pointer; color:var(--text-muted); display: inline-flex; align-items: center;">
                            Solar Auto felülírása
                            <span class="tooltip-container tooltip-align-left">ⓘ<span class="tooltip-text">Ha be van kapcsolva, az időablakon belül a töltés fixen futni fog a beállított árammal, teljesen figyelmen kívül hagyva a Solar Auto szabályokat (pl. akku szintet).</span></span>
                        </label>
                    </div>
                `;
                container.appendChild(row);
            });
        }

        async function saveAutoAmpsSilent(val) {
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc: currentConfig.start_soc,
                        stop_import_limit: currentConfig.stop_import_limit,
                        grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                        house_power_limit_w: currentConfig.house_power_limit_w,
                        persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                        charger_max_amps: val,
                        force_submode: currentConfig.force_submode,
                        schedule_solar_auto: currentConfig.schedule_solar_auto,
                        forced_schedule: currentConfig.forced_schedule,
                        reset_limit: true
                    })
                });
                originalAutoAmps = val;
            } catch (err) {
                console.error(err);
            }
        }

        async function saveForceAmpsSilent(val) {
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc: currentConfig.start_soc,
                        stop_import_limit: currentConfig.stop_import_limit,
                        grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                        house_power_limit_w: currentConfig.house_power_limit_w,
                        persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                        charger_max_amps: val,
                        force_submode: currentConfig.force_submode,
                        schedule_solar_auto: currentConfig.schedule_solar_auto,
                        forced_schedule: currentConfig.forced_schedule,
                        reset_limit: true
                    })
                });
                originalForceAmps = val;
            } catch (err) {
                console.error(err);
            }
        }

        function checkAutoAmpsChanged() {
            const slider = document.getElementById('auto_charger_max_amps');
            const unmanaged = document.getElementById('auto_unmanaged_current').checked;
            const container = document.getElementById('auto-apply-container');
            
            if (unmanaged) {
                slider.disabled = true;
                document.getElementById('auto-amps-val').innerText = 'Nem felügyelt (0A)';
                container.style.display = 'none';
                return;
            }
            
            slider.disabled = false;
            const currentVal = parseInt(slider.value);
            document.getElementById('auto-amps-val').innerText = currentVal;
            
            const isCharging = document.getElementById('plug-status').innerText.toLowerCase().includes('aktív');
            if (isCharging) {
                if (currentVal !== originalAutoAmps) {
                    container.style.display = 'flex';
                } else {
                    container.style.display = 'none';
                }
            } else {
                container.style.display = 'none';
                saveAutoAmpsSilent(currentVal);
            }
        }

        function checkForceAmpsChanged() {
            const slider = document.getElementById('force_charger_max_amps');
            const unmanaged = document.getElementById('force_unmanaged_current').checked;
            const container = document.getElementById('force-apply-container');
            
            if (unmanaged) {
                slider.disabled = true;
                document.getElementById('force-amps-val').innerText = 'Nem felügyelt (0A)';
                if (container) container.style.display = 'none';
                return;
            }
            
            slider.disabled = false;
            const currentVal = parseInt(slider.value);
            document.getElementById('force-amps-val').innerText = currentVal;
            
            const isCharging = document.getElementById('plug-status').innerText.toLowerCase().includes('aktív');
            if (isCharging) {
                if (currentVal !== originalForceAmps) {
                    if (container) container.style.display = 'flex';
                } else {
                    if (container) container.style.display = 'none';
                }
            } else {
                if (container) container.style.display = 'none';
                saveForceAmpsSilent(currentVal);
            }
        }

        async function applyForceAmpsWithRestart() {
            const slider = document.getElementById('force_charger_max_amps');
            const val = parseInt(slider.value);
            
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc: currentConfig.start_soc,
                        stop_import_limit: currentConfig.stop_import_limit,
                        grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                        house_power_limit_w: currentConfig.house_power_limit_w,
                        persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                        charger_max_amps: val,
                        force_submode: currentConfig.force_submode,
                        schedule_solar_auto: currentConfig.schedule_solar_auto,
                        forced_schedule: currentConfig.forced_schedule,
                        apply_with_restart: true,
                        reset_limit: false
                    })
                });
                originalForceAmps = val;
                const container = document.getElementById('force-apply-container');
                if (container) container.style.display = 'none';
                updateStatus();
            } catch (err) {
                console.error(err);
            }
        }

        async function toggleUnmanagedCurrent(mode) {
            const isChecked = document.getElementById(mode + '_unmanaged_current').checked;
            const slider = document.getElementById(mode + '_charger_max_amps');
            slider.disabled = isChecked;
            
            const isCharging = document.getElementById('plug-status').innerText.toLowerCase().includes('aktív');
            
            const sliderWrapper = document.getElementById(mode + '-slider-wrapper');
            if (sliderWrapper) {
                sliderWrapper.style.display = isChecked ? 'none' : 'block';
            }
            
            const val = isChecked ? 0 : parseInt(slider.value);
            document.getElementById(mode + '-amps-val').innerText = isChecked ? 'Nem felügyelt (0A)' : val;
            
            if (isCharging) {
                const origVal = (mode === 'auto') ? originalAutoAmps : originalForceAmps;
                const applyContainer = document.getElementById(mode + '-apply-container');
                if (applyContainer) {
                    if (val !== origVal) {
                        applyContainer.style.display = 'flex';
                    } else {
                        applyContainer.style.display = 'none';
                    }
                }
            } else {
                if (mode === 'auto') {
                    await saveAutoAmpsSilent(val);
                } else {
                    await saveForceAmpsSilent(val);
                }
                const applyContainer = document.getElementById(mode + '-apply-container');
                if (applyContainer) applyContainer.style.display = 'none';
            }
        }

        async function applyAutoAmps(withStop) {
            const charger_max_amps = parseInt(document.getElementById('auto_charger_max_amps').value);
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc: currentConfig.start_soc,
                        stop_import_limit: currentConfig.stop_import_limit,
                        grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                        house_power_limit_w: currentConfig.house_power_limit_w,
                        persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                        charger_max_amps,
                        force_submode: currentConfig.force_submode,
                        schedule_solar_auto: currentConfig.schedule_solar_auto,
                        forced_schedule: currentConfig.forced_schedule,
                        apply_with_stop: withStop,
                        reset_limit: !withStop
                    })
                });
                originalAutoAmps = charger_max_amps;
                document.getElementById('auto-apply-container').style.display = 'none';
                updateStatus();
            } catch (err) {
                console.error(err);
            }
        }

        async function saveScheduleSolarAuto() {
            const schedule_solar_auto = document.getElementById('schedule_solar_auto').checked;
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc: currentConfig.start_soc,
                        stop_import_limit: currentConfig.stop_import_limit,
                        grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                        house_power_limit_w: currentConfig.house_power_limit_w,
                        persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                        charger_max_amps: currentConfig.charger_max_amps,
                        force_submode: currentConfig.force_submode,
                        schedule_solar_auto,
                        forced_schedule: currentConfig.forced_schedule,
                        auto_enabled: currentConfig.auto_enabled,
                        schedule_enabled: currentConfig.schedule_enabled
                    })
                });
                currentConfig.schedule_solar_auto = schedule_solar_auto;
            } catch (err) {
                console.error(err);
            }
        }

        async function saveAutoEnabled() {
            const auto_enabled = document.getElementById('auto_enabled').checked;
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc: currentConfig.start_soc,
                        stop_import_limit: currentConfig.stop_import_limit,
                        grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                        house_power_limit_w: currentConfig.house_power_limit_w,
                        persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                        charger_max_amps: currentConfig.charger_max_amps,
                        force_submode: currentConfig.force_submode,
                        schedule_solar_auto: currentConfig.schedule_solar_auto,
                        forced_schedule: currentConfig.forced_schedule,
                        auto_enabled: auto_enabled,
                        schedule_enabled: currentConfig.schedule_enabled
                    })
                });
                currentConfig.auto_enabled = auto_enabled;
                updateStatus();
            } catch (err) {
                console.error(err);
            }
        }

        async function saveScheduleEnabled() {
            const schedule_enabled = document.getElementById('schedule_enabled').checked;
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc: currentConfig.start_soc,
                        stop_import_limit: currentConfig.stop_import_limit,
                        grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                        house_power_limit_w: currentConfig.house_power_limit_w,
                        persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                        charger_max_amps: currentConfig.charger_max_amps,
                        force_submode: currentConfig.force_submode,
                        schedule_solar_auto: currentConfig.schedule_solar_auto,
                        forced_schedule: currentConfig.forced_schedule,
                        auto_enabled: currentConfig.auto_enabled,
                        schedule_enabled: schedule_enabled
                    })
                });
                currentConfig.schedule_enabled = schedule_enabled;
                updateStatus();
            } catch (err) {
                console.error(err);
            }
        }

        async function triggerSoftStop() {
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc: currentConfig.start_soc,
                        stop_import_limit: currentConfig.stop_import_limit,
                        grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                        house_power_limit_w: currentConfig.house_power_limit_w,
                        persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                        charger_max_amps: currentConfig.charger_max_amps,
                        force_submode: 'schedule',
                        schedule_solar_auto: currentConfig.schedule_solar_auto,
                        forced_schedule: currentConfig.forced_schedule,
                        auto_enabled: currentConfig.auto_enabled,
                        schedule_enabled: currentConfig.schedule_enabled,
                        apply_with_stop: true
                    })
                });
                updateStatus();
            } catch (err) {
                alert("Soft Stop hiba: " + err);
            }
        }

        async function cancelManualOverride() {
            try {
                const response = await fetch('/api/force_submode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ force_submode: 'schedule' })
                });
                const res = await response.json();
                if (res.status === 'success') {
                    updateStatus();
                } else {
                    alert("Felülbírálás visszavonása sikertelen: " + res.message);
                }
            } catch (err) {
                alert("Hiba: " + err);
            }
        }

        async function updateStatus() {
            try {
                const response = await fetch('/api/status');
                if (response.status === 401) {
                    window.location.reload();
                    return;
                }
                const data = await response.json();

                // Globális konfigurációs állapot szinkronizálása
                currentConfig.start_soc = data.start_soc;
                currentConfig.stop_import_limit = data.stop_import_limit;
                currentConfig.grid_charge_duration_minutes = data.grid_charge_duration_minutes;
                currentConfig.house_power_limit_w = data.house_power_limit_w;
                currentConfig.persist_mode_on_restart = data.persist_mode_on_restart;
                currentConfig.charger_max_amps = data.charger_max_amps;
                currentConfig.forced_schedule = data.forced_schedule;
                currentConfig.force_submode = data.force_submode;
                currentConfig.schedule_solar_auto = data.schedule_solar_auto;
                currentConfig.control_mode = data.control_mode;
                currentConfig.auto_enabled = data.auto_enabled;
                currentConfig.schedule_enabled = data.schedule_enabled;

                // Kijelentkezés gomb láthatóságának kezelése
                const logoutDiv = document.getElementById('logout-divider');
                const logoutGrp = document.getElementById('logout-group');
                const mobLogout = document.getElementById('mobile-menu-logout');
                if (data.web_auth_enabled) {
                    logoutDiv.style.display = 'block';
                    logoutGrp.style.display = 'inline-flex';
                    if (mobLogout) mobLogout.style.display = 'block';
                } else {
                    logoutDiv.style.display = 'none';
                    logoutGrp.style.display = 'none';
                    if (mobLogout) mobLogout.style.display = 'none';
                }

                // Inverter kapcsolat
                const inverterBadge = document.getElementById('badge-inverter');
                if (data.inverter_connected) {
                    inverterBadge.className = "badge active";
                } else {
                    inverterBadge.className = "badge inactive";
                }

                // Töltő kapcsolat
                const chargerBadge = document.getElementById('badge-charger');
                if (data.charger_connected) {
                    chargerBadge.className = "badge active";
                } else {
                    chargerBadge.className = "badge inactive";
                }

                // Grid CT teljesítmény
                const gridFmt = formatPower(data.grid_power, true, false);
                const gridVal = document.getElementById('grid-power-main');
                gridVal.innerText = gridFmt.main;
                document.getElementById('grid-power-sub').innerText = gridFmt.sub;
                if (data.grid_power < 0) {
                    gridVal.className = "metric-value surplus-val";
                } else {
                    gridVal.className = "metric-value consumption-val";
                }

                // PV Power
                const pvFmt = formatPower(data.pv_power, false, false);
                document.getElementById('pv-power-main').innerText = pvFmt.main;
                document.getElementById('pv-power-sub').innerText = pvFmt.sub;

                // UPS Terhelés (Ház)
                const upsFmt = formatPower(data.ups_load_power, false, false);
                document.getElementById('ups-power-main').innerText = upsFmt.main;
                document.getElementById('ups-power-sub').innerText = upsFmt.sub;

                // Autótöltő fogyasztása
                const chargerFmt = formatPower(data.charger_power, false, false);
                document.getElementById('charger-power-main').innerText = chargerFmt.main;
                document.getElementById('charger-power-sub').innerText = chargerFmt.sub;

                // Akku terhelés (+/-)
                const batFmt = formatPower(data.battery_power, false, true);
                const batVal = document.getElementById('battery-power-main');
                batVal.innerText = batFmt.main;
                document.getElementById('battery-power-sub').innerText = batFmt.sub;
                if (data.battery_power >= 0) {
                    batVal.className = "metric-value surplus-val";
                } else {
                    batVal.className = "metric-value consumption-val";
                }

                // Battery SoC
                document.getElementById('battery-soc').innerHTML = data.battery_soc + '<span class="metric-unit">%</span>';

                // Töltő telemetria
                document.getElementById('energy-total').innerText = data.energy_total.toFixed(2) + ' kWh';
                document.getElementById('temp-internal').innerText = data.temperature_internal.toFixed(1) + ' °C';

                // Töltő kapcsolata (BLE)
                const chargerConnStatus = document.getElementById('charger-connection-status');
                if (chargerConnStatus) {
                    if (data.charger_connected) {
                        chargerConnStatus.innerText = "Csatlakoztatva";
                        chargerConnStatus.style.color = "var(--success)";
                    } else {
                        chargerConnStatus.innerText = "Nincs csatlakoztatva";
                        chargerConnStatus.style.color = "var(--danger)";
                    }
                }

                // Csatlakozó státusza
                const plugStatus = document.getElementById('plug-status');
                const plugStatusInline = document.getElementById('plug-status-inline');
                
                let plugText = "";
                let plugColor = "";
                
                if (data.pull_plug) {
                    plugText = "Kábel kihúzva";
                    plugColor = "var(--danger)";
                } else if (data.charging_active) {
                    let activeLimitText = data.active_current_limit > 0 ? ` (${data.active_current_limit}A limit)` : "";
                    plugText = `Töltés aktív${activeLimitText}`;
                    plugColor = "var(--success)";
                } else if (data.charger_connected) {
                    plugText = "Készenlét";
                    plugColor = "var(--primary)";
                } else {
                    plugText = "Töltő keresése...";
                    plugColor = "var(--text-muted)";
                }

                if (plugStatus) {
                    plugStatus.innerHTML = `[ ${plugText} ]`;
                    plugStatus.style.color = plugColor;
                }
                if (plugStatusInline) {
                    plugStatusInline.innerText = plugText;
                    plugStatusInline.style.color = plugColor;
                }

                // Fázis adatok
                document.getElementById('v1').innerText = data.voltages[0].toFixed(1) + ' V';
                document.getElementById('i1').innerText = data.currents[0].toFixed(2) + ' A';
                document.getElementById('v2').innerText = data.voltages[1].toFixed(1) + ' V';
                document.getElementById('i2').innerText = data.currents[1].toFixed(2) + ' A';
                document.getElementById('v3').innerText = data.voltages[2].toFixed(1) + ' V';
                document.getElementById('i3').innerText = data.currents[2].toFixed(2) + ' A';

                // Globális fejléc státusz gombok frissítése
                const badgeAuto = document.getElementById('badge-toggle-auto');
                if (badgeAuto) {
                    badgeAuto.className = data.auto_enabled ? 'badge active' : 'badge off';
                }
                const badgeSchedule = document.getElementById('badge-toggle-schedule');
                if (badgeSchedule) {
                    badgeSchedule.className = data.schedule_enabled ? 'badge active' : 'badge off';
                }

                // Mobil státusz sáv indikátorok frissítése
                const mobDeye = document.getElementById('mobile-status-deye');
                if (mobDeye) {
                    mobDeye.className = "status-dot-item " + (data.inverter_connected ? "active" : "inactive");
                }
                const mobBesen = document.getElementById('mobile-status-besen');
                if (mobBesen) {
                    mobBesen.className = "status-dot-item " + (data.charger_connected ? "active" : "inactive");
                }
                const mobAuto = document.getElementById('mobile-status-auto');
                if (mobAuto) {
                    mobAuto.className = "status-dot-item auto-active " + (data.auto_enabled ? "active" : "off");
                }
                const mobSchedule = document.getElementById('mobile-status-schedule');
                if (mobSchedule) {
                    mobSchedule.className = "status-dot-item auto-active " + (data.schedule_enabled ? "active" : "off");
                }

                // Felülbírálás banner és visszavonás gomb láthatósága
                const overrideBanner = document.getElementById('override-banner');
                const overrideBannerText = document.getElementById('override-banner-text');
                if (data.force_submode === 'manual_start') {
                    if (overrideBanner) overrideBanner.style.display = 'flex';
                    if (overrideBannerText) overrideBannerText.innerText = 'Aktív kézi felülbírálás: KÉZI INDÍTÁS';
                } else if (data.force_submode === 'manual_stop') {
                    if (overrideBanner) overrideBanner.style.display = 'flex';
                    if (overrideBannerText) overrideBannerText.innerText = 'Aktív kézi felülbírálás: KÉZI LEÁLLÍTÁS (HARD STOP)';
                } else {
                    if (overrideBanner) overrideBanner.style.display = 'none';
                }

                // Dinamikus UI elemek láthatósági szabályai
                const isCharging = data.charging_active;
                const forceUnmanagedContainer = document.getElementById('force-unmanaged-container');
                const forceSliderWrapper = document.getElementById('force-slider-wrapper');
                const autoSliderWrapper = document.getElementById('auto-slider-wrapper');
                const forceApplyContainer = document.getElementById('force-apply-container');
                const autoApplyContainer = document.getElementById('auto-apply-container');
                
                if (isCharging) {
                    if (forceUnmanagedContainer) forceUnmanagedContainer.style.display = 'block';
                    
                    const isForceUnmanaged = document.getElementById('force_unmanaged_current').checked;
                    if (forceSliderWrapper) forceSliderWrapper.style.display = isForceUnmanaged ? 'none' : 'block';
                    
                    const isAutoUnmanaged = document.getElementById('auto_unmanaged_current').checked;
                    if (autoSliderWrapper) autoSliderWrapper.style.display = isAutoUnmanaged ? 'none' : 'block';
                } else {
                    if (forceUnmanagedContainer) forceUnmanagedContainer.style.display = 'none';
                    if (forceSliderWrapper) forceSliderWrapper.style.display = 'block';
                    
                    const isAutoUnmanaged = document.getElementById('auto_unmanaged_current').checked;
                    if (autoSliderWrapper) autoSliderWrapper.style.display = isAutoUnmanaged ? 'none' : 'block';
                    
                    if (forceApplyContainer) forceApplyContainer.style.display = 'none';
                    if (autoApplyContainer) autoApplyContainer.style.display = 'none';
                }

                // Force al-üzemmódok gombjainak kiemelése
                const btnStart = document.getElementById('btn-manual-charge-start');
                const btnStop = document.getElementById('btn-manual-charge-stop');
                if (data.force_submode === 'manual_start') {
                    btnStart.className = "action-btn action-btn-start active-manual";
                    btnStop.className = "action-btn action-btn-stop";
                } else if (data.force_submode === 'manual_stop') {
                    btnStart.className = "action-btn action-btn-start";
                    btnStop.className = "action-btn action-btn-stop active-manual";
                } else {
                    btnStart.className = "action-btn action-btn-start";
                    btnStop.className = "action-btn action-btn-stop";
                }

                // Szimulációs állapotelemek frissítése
                const simPanel = document.getElementById('sim-panel-card');
                if (simPanel) {
                    simPanel.style.display = data.simulation ? 'flex' : 'none';
                }
                document.getElementById('sim_mode_toggle').checked = data.simulation;
                document.getElementById('sim-controls-container').style.display = data.simulation ? 'flex' : 'none';
                if (data.simulation) {
                    // Mágikus logikai teszt státusz jelző
                    const assertBox = document.getElementById('assertion-status-box');
                    const assertText = document.getElementById('assertion-badge-text');
                    const assertDetails = document.getElementById('assertion-error-details');
                    
                    if (data.assertion_status === 'OK') {
                        assertBox.style.background = 'rgba(16, 185, 129, 0.1)';
                        assertBox.style.borderColor = 'rgba(16, 185, 129, 0.3)';
                        assertBox.style.color = '#34d399';
                        assertText.innerText = 'OK';
                        assertDetails.style.display = 'none';
                    } else {
                        assertBox.style.background = 'rgba(239, 68, 68, 0.15)';
                        assertBox.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                        assertBox.style.color = '#f87171';
                        assertText.innerText = 'ELTÉRÉS A TERVTŐL';
                        assertDetails.innerText = data.last_assertion_error;
                        assertDetails.style.display = 'block';
                    }
                    
                    // Szinkronizáljuk a szimulációs csúszkákat és beviteli mezőket (csak ha a felhasználó épp nem húzza őket)
                    if (!document.activeElement || document.activeElement.id !== 'sim_grid_power') {
                        document.getElementById('sim_grid_power').value = data.grid_power;
                        document.getElementById('sim_grid_power_val').innerText = data.grid_power + ' W';
                    }
                    if (!document.activeElement || document.activeElement.id !== 'sim_pv_power') {
                        document.getElementById('sim_pv_power').value = data.pv_power;
                        document.getElementById('sim_pv_power_val').innerText = data.pv_power + ' W';
                    }
                    if (!document.activeElement || document.activeElement.id !== 'sim_battery_soc') {
                        document.getElementById('sim_battery_soc').value = data.battery_soc;
                        document.getElementById('sim_battery_soc_val').innerText = data.battery_soc + '%';
                    }
                    if (!document.activeElement || document.activeElement.id !== 'sim_battery_power') {
                        document.getElementById('sim_battery_power').value = data.battery_power;
                        document.getElementById('sim_battery_power_val').innerText = data.battery_power + ' W';
                    }
                    if (!document.activeElement || document.activeElement.id !== 'sim_ups_load_power') {
                        document.getElementById('sim_ups_load_power').value = data.ups_load_power;
                        document.getElementById('sim_ups_load_power_val').innerText = data.ups_load_power + ' W';
                    }
                    
                    document.getElementById('sim_pull_plug').checked = data.pull_plug;
                    document.getElementById('sim_charging_active').checked = data.charging_active;
                    document.getElementById('sim_external_session').checked = data.sim_external_session;
                    
                    if (!document.activeElement || document.activeElement.id !== 'sim_custom_day') {
                        document.getElementById('sim_custom_day').value = data.sim_custom_day;
                    }
                    if (!document.activeElement || document.activeElement.id !== 'sim_custom_time') {
                        document.getElementById('sim_custom_time').value = data.sim_custom_time;
                    }
                }

                // Konfiguráció kitöltése a szerver adataival (csak az első alkalommal)
                if (!configLoaded) {
                    document.getElementById('auto_start_soc').value = data.start_soc;
                    document.getElementById('auto_stop_import_limit').value = data.stop_import_limit;
                    document.getElementById('auto_grid_charge_duration_minutes').value = data.grid_charge_duration_minutes;
                    document.getElementById('auto_house_power_limit_w').value = data.house_power_limit_w;
                    document.getElementById('auto_persist_mode_on_restart').checked = data.persist_mode_on_restart;
                    
                    originalAutoAmps = data.charger_max_amps;
                    document.getElementById('auto_charger_max_amps').value = data.charger_max_amps > 0 ? data.charger_max_amps : 16;
                    document.getElementById('auto-amps-val').innerText = data.charger_max_amps > 0 ? data.charger_max_amps : 'Nem felügyelt (0A)';
                    document.getElementById('auto_unmanaged_current').checked = data.charger_max_amps === 0;
                    if (data.charger_max_amps === 0) {
                        document.getElementById('auto_charger_max_amps').disabled = true;
                        if (autoSliderWrapper) autoSliderWrapper.style.display = 'none';
                    }
                    
                    originalForceAmps = data.charger_max_amps;
                    document.getElementById('force_charger_max_amps').value = data.charger_max_amps > 0 ? data.charger_max_amps : 16;
                    document.getElementById('force-amps-val').innerText = data.charger_max_amps > 0 ? data.charger_max_amps : 'Nem felügyelt (0A)';
                    document.getElementById('force_unmanaged_current').checked = data.charger_max_amps === 0;
                    if (data.charger_max_amps === 0) {
                        document.getElementById('force_charger_max_amps').disabled = true;
                        if (forceSliderWrapper && isCharging) forceSliderWrapper.style.display = 'none';
                    }

                    document.getElementById('schedule_solar_auto').checked = data.schedule_solar_auto;
                    document.getElementById('auto_enabled').checked = data.auto_enabled;
                    document.getElementById('schedule_enabled').checked = data.schedule_enabled;
                    
                    renderSchedule(data.forced_schedule);
                    configLoaded = true;

                    // Induláskor beállítjuk a fület az éppen aktív szerver üzemmódra (Figyelés esetén az Auto lapra)
                    let initialTab = data.control_mode;
                    if (initialTab === 'monitoring') {
                        initialTab = 'auto';
                    }
                    selectTab(initialTab);
                }

                // Hibajelzések és Cooldown dobozok kezelése
                const errBox = document.getElementById('error-box');
                if (data.error_message) {
                    errBox.innerText = "HIBA: " + data.error_message;
                    errBox.style.display = "block";
                } else {
                    errBox.style.display = "none";
                }

                const coolBox = document.getElementById('cooldown-box');
                const now = Date.now() / 1000;
                if (data.cooldown_until > now) {
                    const diff = Math.round(data.cooldown_until - now);
                    coolBox.innerText = `Próbálkozás késleltetve (lehűlési idő): még ${diff} mp...`;
                    coolBox.style.display = "block";
                } else {
                    coolBox.style.display = "none";
                }

                // Logok frissítése
                const consoleBox = document.getElementById('console');
                consoleBox.innerHTML = '';
                data.logs.forEach(line => {
                    const div = document.createElement('div');
                    div.className = 'console-line';
                    div.innerText = line;
                    consoleBox.appendChild(div);
                });
                consoleBox.scrollTop = consoleBox.scrollHeight;

            } catch (error) {
                console.error("Hiba a műszerfal lekérdezésekor:", error);
            }
        }

        let activeTab = 'auto';
        let currentSection = 'auto';

        function selectTab(tab) {
            activeTab = tab;
            
            // Tab gombok stílusa
            document.getElementById('btn-auto').className = "mode-btn" + (activeTab === 'auto' ? ' active' : '');
            document.getElementById('btn-schedule').className = "mode-btn" + (activeTab === 'schedule' ? ' active' : '');
            document.getElementById('btn-force').className = "mode-btn" + (activeTab === 'force' ? ' active' : '');
            
            // Kártyák láthatósága
            document.getElementById('config-auto').style.display = activeTab === 'auto' ? 'flex' : 'none';
            document.getElementById('config-schedule').style.display = activeTab === 'schedule' ? 'flex' : 'none';
            document.getElementById('config-force').style.display = activeTab === 'force' ? 'flex' : 'none';
        }

        function toggleMobileMenu() {
            const menu = document.getElementById('mobile-menu');
            const btn = document.getElementById('hamburger-btn');
            if (menu.classList.contains('open')) {
                menu.classList.remove('open');
                btn.innerText = '☰';
            } else {
                menu.classList.add('open');
                btn.innerText = '✕';
            }
        }

        function showSection(section) {
            currentSection = section;
            
            // Hamburger menü kijelölés frissítése
            document.querySelectorAll('.menu-item').forEach(el => el.classList.remove('active'));
            const activeMenuItem = document.getElementById('menu-item-' + section);
            if (activeMenuItem) activeMenuItem.classList.add('active');
            
            const isMobile = window.innerWidth <= 1024;
            
            // Kártyák kiválasztása DOM-ból
            const cards = document.querySelectorAll('main > .card');
            const configCard = cards[0];
            const telemetryCard = cards[1];
            const simCard = document.getElementById('sim-panel-card');
            const logCard = document.querySelector('.console-container');
            
            if (isMobile) {
                // Mobilon mindent elrejtünk, majd csak a kiválasztottat mutatjuk meg
                if (configCard) configCard.style.display = 'none';
                if (telemetryCard) telemetryCard.style.display = 'none';
                if (simCard) simCard.style.display = 'none';
                if (logCard) logCard.style.display = 'none';
                
                if (section === 'auto' || section === 'schedule' || section === 'force') {
                    if (configCard) configCard.style.display = 'flex';
                    selectTab(section);
                } else if (section === 'measurements') {
                    if (telemetryCard) telemetryCard.style.display = 'flex';
                } else if (section === 'log') {
                    if (logCard) logCard.style.display = 'block';
                }
                
                // Menü bezárása
                const menu = document.getElementById('mobile-menu');
                if (menu) menu.classList.remove('open');
                const btn = document.getElementById('hamburger-btn');
                if (btn) btn.innerText = '☰';
            } else {
                // Asztali nézetben mindent visszaállítunk a megszokott grid elrendezésre
                if (configCard) configCard.style.display = 'flex';
                if (telemetryCard) telemetryCard.style.display = 'flex';
                
                if (simCard) {
                    const simToggle = document.getElementById('sim_enabled');
                    simCard.style.display = (simToggle && simToggle.checked) ? 'flex' : 'none';
                }
                if (logCard) logCard.style.display = 'block';
                
                if (section === 'auto' || section === 'schedule' || section === 'force') {
                    selectTab(section);
                }
            }
        }

        // Képernyő átméretezéskor frissítjük a láthatóságot
        window.addEventListener('resize', () => {
            showSection(currentSection);
        });

        async function triggerForceSubmode(submode) {
            try {
                const response1 = await fetch('/api/force_submode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ force_submode: submode })
                });
                const res1 = await response1.json();
                if (res1.status !== 'success') {
                    alert("Nem sikerült a kézi művelet: " + res1.message);
                    return;
                }
                
                const response2 = await fetch('/api/mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ control_mode: 'force' })
                });
                const res2 = await response2.json();
                if (res2.status === 'success') {
                    selectTab('force');
                    updateStatus();
                } else {
                    alert("Nem sikerült aktiválni a Force módot: " + res2.message);
                }
            } catch (err) {
                alert("Hiba: " + err);
            }
        }

        async function saveAutoConfig(event) {
            event.preventDefault();
            const start_soc = parseInt(document.getElementById('auto_start_soc').value);
            const stop_import_limit = parseInt(document.getElementById('auto_stop_import_limit').value);
            const grid_charge_duration_minutes = parseInt(document.getElementById('auto_grid_charge_duration_minutes').value);
            const house_power_limit_w = parseInt(document.getElementById('auto_house_power_limit_w').value);
            const persist_mode_on_restart = document.getElementById('auto_persist_mode_on_restart').checked;
            const charger_max_amps = document.getElementById('auto_unmanaged_current').checked ? 0 : parseInt(document.getElementById('auto_charger_max_amps').value);

            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc,
                        stop_import_limit,
                        grid_charge_duration_minutes,
                        house_power_limit_w,
                        persist_mode_on_restart,
                        charger_max_amps,
                        force_submode: currentConfig.force_submode,
                        schedule_solar_auto: currentConfig.schedule_solar_auto,
                        forced_schedule: currentConfig.forced_schedule,
                        auto_enabled: currentConfig.auto_enabled,
                        schedule_enabled: currentConfig.schedule_enabled
                    })
                });
                const result = await response.json();
                alert(result.message);
                originalAutoAmps = charger_max_amps;
                document.getElementById('auto-apply-container').style.display = 'none';
            } catch (error) {
                alert("Sikertelen mentés: " + error);
            }
        }

        async function saveSchedule() {
            const schedule = [];
            const days = ["Hétfő", "Kedd", "Szerda", "Csütörtök", "Péntek", "Szombat", "Vasárnap"];
            for (let i = 0; i < 7; i++) {
                const day = days[i];
                const enabled = document.getElementById(`sched_enabled_${i}`).checked;
                const start = document.getElementById(`sched_start_${i}`).value;
                const stop = document.getElementById(`sched_stop_${i}`).value;
                const amps = parseInt(document.getElementById(`sched_amps_${i}`).value);
                const override_auto = document.getElementById(`sched_override_auto_${i}`).checked;
                
                schedule.push({ day, enabled, start, stop, amps, override_auto });
            }
            
            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc: currentConfig.start_soc,
                        stop_import_limit: currentConfig.stop_import_limit,
                        grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                        house_power_limit_w: currentConfig.house_power_limit_w,
                        persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                        charger_max_amps: currentConfig.charger_max_amps,
                        force_submode: currentConfig.force_submode,
                        schedule_solar_auto: currentConfig.schedule_solar_auto,
                        forced_schedule: schedule,
                        auto_enabled: currentConfig.auto_enabled,
                        schedule_enabled: currentConfig.schedule_enabled
                    })
                });
                const result = await response.json();
                alert("Ütemezés elmentve!");
                currentConfig.forced_schedule = schedule;
            } catch (error) {
                alert("Sikertelen ütemezés mentés: " + error);
            }
        }

        async function toggleSimulationMode() {
            const isChecked = document.getElementById('sim_mode_toggle').checked;
            try {
                const response = await fetch('/api/sim_toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ simulation: isChecked })
                });
                const res = await response.json();
                document.getElementById('sim-controls-container').style.display = res.simulation ? 'flex' : 'none';
                updateStatus();
            } catch (err) {
                console.error(err);
            }
        }

        async function sendSimValue(field, val) {
            const bodyObj = {};
            bodyObj[field] = parseInt(val);
            try {
                await fetch('/api/sim_data', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(bodyObj)
                });
            } catch (err) {
                console.error(err);
            }
        }

        async function sendSimCheckbox(field, val) {
            const bodyObj = {};
            bodyObj[field] = !!val;
            try {
                await fetch('/api/sim_data', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(bodyObj)
                });
            } catch (err) {
                console.error(err);
            }
        }

        async function sendSimText(field, val) {
            const bodyObj = {};
            bodyObj[field] = val;
            try {
                await fetch('/api/sim_data', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(bodyObj)
                });
            } catch (err) {
                console.error(err);
            }
        }

        async function logout() {
            try {
                const response = await fetch('/api/logout', {
                    method: 'POST'
                });
                const res = await response.json();
                if (res.status === 'success') {
                    window.location.reload();
                } else {
                    alert("Kijelentkezés sikertelen!");
                }
            } catch (err) {
                alert("Hiba: " + err);
            }
        }

        // Megakadályozzuk, hogy a tooltip ikonra kattintás elnyomja a checkboxokat vagy más elemeket
        document.querySelectorAll('.tooltip-container').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                e.preventDefault();
            });
        });

        showSection('auto');
        setInterval(updateStatus, 2000);
        updateStatus();
    </script>
</body>
</html>
"""

# --- HTTP KÉRÉS KEZELŐ KILENS ---
class ControllerHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def get_cookie(self, name):
        cookie_header = self.headers.get('Cookie')
        if not cookie_header:
            return None
        parts = cookie_header.split(';')
        for part in parts:
            part = part.strip()
            if '=' in part:
                k, v = part.split('=', 1)
                if k == name:
                    return v
        return None

    def is_authenticated(self):
        if not WEB_AUTH_ENABLED:
            return True
        session_token = self.get_cookie('session')
        if session_token and session_token in active_sessions:
            return True
        return False

    def do_GET(self):
        global shared_state
        if self.path == '/background.png':
            import os
            import sys
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            bg_path = os.path.join(base_dir, 'background.png')
            if os.path.exists(bg_path):
                self.send_response(200)
                self.send_header('Content-type', 'image/png')
                self.end_headers()
                with open(bg_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "Fájl nem található")
            return

        # Ellenőrizzük az autentikációt minden más GET végpontnál
        if not self.is_authenticated():
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Expires', '0')
                self.end_headers()
                self.wfile.write(LOGIN_HTML.encode('utf-8'))
            else:
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "unauthorized", "message": "Autentikáció szükséges!"}).encode('utf-8'))
            return

        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode('utf-8'))
        elif self.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            with state_lock:
                status_json = json.dumps(shared_state)
            self.wfile.write(status_json.encode('utf-8'))
        else:
            self.send_error(404, "Fájl nem található")

    def do_POST(self):
        global shared_state
        
        # Bejelentkezés kezelése (mindig engedélyezett hitelesítés nélkül is)
        if self.path == '/api/login':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                login_data = json.loads(post_data.decode('utf-8'))
                submitted_pwd = login_data.get("password")
                if submitted_pwd == WEB_PASSWORD:
                    # Biztonságos token generálása
                    token = secrets.token_hex(16)
                    active_sessions.add(token)
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Set-Cookie', f'session={token}; HttpOnly; Path=/; SameSite=Lax')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                    log_message("Sikeres webes bejelentkezés.")
                else:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Helytelen jelszó!"}).encode('utf-8'))
                    log_message("Sikertelen webes bejelentkezési kísérlet.")
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": f"Hiba: {e}"}).encode('utf-8'))
            return

        # Minden más POST végpont ellenőrzése
        if not self.is_authenticated():
            self.send_response(401)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "unauthorized", "message": "Autentikáció szükséges!"}).encode('utf-8'))
            return

        # Kijelentkezés végpont
        if self.path == '/api/logout':
            session_token = self.get_cookie('session')
            if session_token in active_sessions:
                active_sessions.remove(session_token)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Set-Cookie', 'session=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            log_message("Sikeres webes kijelentkezés.")
            return

        elif self.path == '/api/config':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                config_data = json.loads(post_data.decode('utf-8'))
                with state_lock:
                    shared_state["start_soc"] = int(config_data["start_soc"])
                    shared_state["stop_import_limit"] = int(config_data["stop_import_limit"])
                    shared_state["grid_charge_duration_minutes"] = int(config_data["grid_charge_duration_minutes"])
                    shared_state["house_power_limit_w"] = int(config_data["house_power_limit_w"])
                    shared_state["persist_mode_on_restart"] = bool(config_data["persist_mode_on_restart"])
                    if "charger_max_amps" in config_data:
                        shared_state["charger_max_amps"] = int(config_data["charger_max_amps"])
                    if "force_submode" in config_data:
                        shared_state["force_submode"] = config_data["force_submode"]
                    if "forced_schedule" in config_data:
                        shared_state["forced_schedule"] = config_data["forced_schedule"]
                    if "schedule_solar_auto" in config_data:
                        shared_state["schedule_solar_auto"] = bool(config_data["schedule_solar_auto"])
                    if "auto_enabled" in config_data:
                        shared_state["auto_enabled"] = bool(config_data["auto_enabled"])
                    if "schedule_enabled" in config_data:
                        shared_state["schedule_enabled"] = bool(config_data["schedule_enabled"])
                    
                    # Alkalmazási és újraindítási flagek
                    if config_data.get("apply_with_stop"):
                        shared_state["apply_with_stop"] = True
                    if config_data.get("apply_with_restart"):
                        shared_state["apply_with_restart"] = True
                    if config_data.get("reset_limit"):
                        shared_state["reset_limit"] = True
                
                save_config_file()
                log_message("Új konfigurációs paraméterek elmentve.")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Beállítások sikeresen mentve!"}).encode('utf-8'))
            except Exception as e:
                self.send_error(400, f"Hibás adatformátum: {e}")
                
        elif self.path == '/api/mode':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                mode_data = json.loads(post_data.decode('utf-8'))
                new_mode = mode_data.get("control_mode")
                if new_mode in ("monitoring", "auto", "schedule", "force"):
                    with state_lock:
                        shared_state["control_mode"] = new_mode
                        # Ha kézzel módosítjuk az üzemmódot, töröljük a hibajelzést és a cooldown-t
                        shared_state["error_message"] = ""
                        shared_state["cooldown_until"] = 0.0
                    
                    save_config_file()
                    log_message(f"Üzemmód váltás: {new_mode.upper()}")
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                else:
                    self.send_error(400, "Érvénytelen üzemmód")
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

        elif self.path == '/api/force_submode':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                mode_data = json.loads(post_data.decode('utf-8'))
                new_submode = mode_data.get("force_submode")
                if new_submode in ("manual_start", "manual_stop", "schedule"):
                    with state_lock:
                        shared_state["force_submode"] = new_submode
                        # Töröljük a hibajelzést és a cooldown-t
                        shared_state["error_message"] = ""
                        shared_state["cooldown_until"] = 0.0
                        if new_submode == "manual_start":
                            shared_state["manual_start_requested"] = True
                    
                    save_config_file()
                    log_message(f"Kényszerített al-üzemmód váltás: {new_submode.upper()}")
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                else:
                    self.send_error(400, "Érvénytelen al-üzemmód")
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

        elif self.path == '/api/set_current':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                current_data = json.loads(post_data.decode('utf-8'))
                amps = int(current_data.get("charger_max_amps", 16))
                if 6 <= amps <= 16:
                    with state_lock:
                        shared_state["charger_max_amps"] = amps
                    save_config_file()
                    log_message(f"Töltési áramerősség korlát beállítva: {amps} A")
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success", "message": "Áramerősség sikeresen frissítve!"}).encode('utf-8'))
                else:
                    self.send_error(400, "Érvénytelen áramerősség (6-16A)")
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

        elif self.path == '/api/sim_toggle':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                sim_val = bool(data.get("simulation", False))
                with state_lock:
                    shared_state["simulation"] = sim_val
                    if sim_val:
                        # Alapértelmezett értékek szimulációhoz
                        shared_state["inverter_connected"] = True
                        shared_state["charger_connected"] = True
                        shared_state["battery_soc"] = 75
                        shared_state["grid_power"] = 1500
                        shared_state["pv_power"] = 3200
                        shared_state["battery_power"] = -1000
                        shared_state["ups_load_power"] = 450
                        shared_state["pull_plug"] = False
                        shared_state["charging_active"] = False
                        shared_state["sim_external_session"] = False
                        shared_state["sim_custom_time"] = ""
                        shared_state["sim_custom_day"] = ""
                        shared_state["assertion_status"] = "OK"
                        shared_state["last_assertion_error"] = ""
                
                log_message(f"Szimulációs mód {'BEKAPCSOLVA' if sim_val else 'KIKAPCSOLVA'}")
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "simulation": sim_val}).encode('utf-8'))
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

        elif self.path == '/api/sim_data':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                with state_lock:
                    if "grid_power" in data:
                        shared_state["grid_power"] = int(data["grid_power"])
                    if "pv_power" in data:
                        shared_state["pv_power"] = int(data["pv_power"])
                    if "battery_soc" in data:
                        shared_state["battery_soc"] = int(data["battery_soc"])
                    if "battery_power" in data:
                        shared_state["battery_power"] = int(data["battery_power"])
                    if "ups_load_power" in data:
                        shared_state["ups_load_power"] = int(data["ups_load_power"])
                    if "pull_plug" in data:
                        shared_state["pull_plug"] = bool(data["pull_plug"])
                    if "charging_active" in data:
                        shared_state["charging_active"] = bool(data["charging_active"])
                    if "sim_external_session" in data:
                        shared_state["sim_external_session"] = bool(data["sim_external_session"])
                    if "sim_custom_time" in data:
                        shared_state["sim_custom_time"] = str(data["sim_custom_time"]).strip()
                    if "sim_custom_day" in data:
                        shared_state["sim_custom_day"] = str(data["sim_custom_day"]).strip()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

def start_web_server():
    server_address = ('0.0.0.0', HTTP_PORT)
    httpd = ThreadingHTTPServer(server_address, ControllerHTTPHandler)
    log_message(f"Web Dashboard elindítva, elérhető a helyi hálózaton a {HTTP_PORT}-as porton.")
    httpd.serve_forever()

# --- BLE PARANCS GENERÁLÁS (LOGIN / START / STOP) ---
def create_ble_packet(cmd_type, payload=b""):
    global charger_serial, charger_password
    
    frame = bytearray()
    frame.extend([0x06, 0x01]) # Header
    frame.extend([0x00, 0x00]) # Csomaghossz placeholder
    frame.append(0x00)         # Key type
    frame.extend(charger_serial)
    frame.extend(charger_password)
    frame.extend([(cmd_type >> 8) & 0xFF, cmd_type & 0xFF]) # Command ID
    frame.extend(payload)      # Hasznos teher
    frame.extend([0x00, 0x00]) # Checksum placeholder
    frame.extend([0x0F, 0x02]) # Tail
    
    total_len = len(frame)
    frame[2] = (total_len >> 8) & 0xFF
    frame[3] = total_len & 0xFF
    
    # Checksum kiszámítása (egyszerű bájtok összege)
    checksum = 0
    for i in range(total_len - 4):
        checksum += frame[i]
        
    frame[total_len - 4] = (checksum >> 8) & 0xFF
    frame[total_len - 3] = checksum & 0xFF
    
    return bytes(frame)

# Csomag másolása új Command ID-val és újraszámolt ellenőrző összeggel
def copy_packet_with_new_cmd(packet, new_cmd_id):
    global charger_password
    frame = bytearray(packet)
    
    # Injektáljuk a konfigurált jelszót a másolt csomag 13-18. bájtjaiba
    if len(frame) >= 19:
        frame[13:19] = charger_password
        
    frame[19] = (new_cmd_id >> 8) & 0xFF
    frame[20] = new_cmd_id & 0xFF
    
    total_len = len(frame)
    checksum = 0
    for i in range(total_len - 4):
        checksum += frame[i]
        
    frame[total_len - 4] = (checksum >> 8) & 0xFF
    frame[total_len - 3] = checksum & 0xFF
    return bytes(frame)

# Aszinkron csomagfeldolgozó és állapotgép
async def process_assembled_packet(packet, client):
    global charger_serial, charger_password, ble_state, login_acknowledged
    global last_identity_ack_time, last_login_time, shared_state
    
    if len(packet) < 21:
        return
        
    extracted_serial = packet[5:13]
    extracted_password = packet[13:19]
    cmd_id = (packet[19] << 8) | packet[20]
    
    log_message(f"-> [BLE RX] Csomag érkezett: ID=0x{cmd_id:04X} (Hossz: {len(packet)} bájt)")
    
    # Csak érvényes sorozatszámot és ismert parancsokat szinkronizálunk
    if cmd_id in (0x0001, 0x0004, 0x000D, 0x0006, 0x000A, 0x0002, 0x0101, 0x0155, 0x0003):
        is_valid_serial = any(b != 0x00 and b != 0xFF for b in extracted_serial)
        if is_valid_serial:
            if charger_serial != extracted_serial:
                charger_serial = bytearray(extracted_serial)
                log_message(f"-> [SZINKRON] Új érvényes sorozatszám rögzítve: {charger_serial.hex().upper()}")
                
    current_time = time.time()
    
    # Kézfogás és bejelentkezés állapotgép
    if cmd_id == 0x0001:
        if ble_state == "INIT":
            log_message("-> [HANDSHAKE] Identity Ack (0x8002) csomag küldése...")
            ack_packet = create_ble_packet(0x8002, b"")
            try:
                await client.write_gatt_char(CHAR_FFE9_WRITE, ack_packet, response=True)
                ble_state = "SENT_IDENTITY_ACK"
                last_identity_ack_time = current_time
                log_message("-> [BLE WRITE SUCCESS] Identity Ack (0x8002) elküldve a FFE9-re!")
            except Exception as e:
                log_message(f"-> [BLE WRITE ERROR] Ack küldési hiba: {e}")
                
        elif ble_state == "SENT_IDENTITY_ACK":
            if current_time - last_identity_ack_time > 4.0:
                log_message("-> [HANDSHAKE RETRY] Identity Ack (0x8002) újraküldése...")
                ack_packet = create_ble_packet(0x8002, b"")
                try:
                    await client.write_gatt_char(CHAR_FFE9_WRITE, ack_packet, response=True)
                    last_identity_ack_time = current_time
                except Exception as e:
                    log_message(f"-> [BLE WRITE ERROR] Ack újraküldési hiba: {e}")
                    
        elif ble_state == "IDENTITY_ACKED":
            log_message("-> [LOGIN] Bejelentkezési parancs (0x8001) küldése...")
            ts = int(current_time)
            login_payload = bytearray([0x01])
            login_payload.extend([(ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF])
            login_packet = create_ble_packet(0x8001, bytes(login_payload))
            try:
                await client.write_gatt_char(CHAR_FFE9_WRITE, login_packet, response=True)
                ble_state = "SENT_LOGIN"
                last_login_time = current_time
                log_message("-> [BLE WRITE SUCCESS] Bejelentkezési parancs elküldve a FFE9-re!")
            except Exception as e:
                log_message(f"-> [BLE WRITE ERROR] Login küldési hiba: {e}")
                
        elif ble_state == "SENT_LOGIN":
            if current_time - last_login_time > 5.0:
                log_message("-> [LOGIN RETRY] Bejelentkezési parancs (0x8001) újraküldése...")
                ts = int(current_time)
                login_payload = bytearray([0x01])
                login_payload.extend([(ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF])
                login_packet = create_ble_packet(0x8001, bytes(login_payload))
                try:
                    await client.write_gatt_char(CHAR_FFE9_WRITE, login_packet, response=True)
                    last_login_time = current_time
                except Exception as e:
                    log_message(f"-> [BLE WRITE ERROR] Login újraküldési hiba: {e}")
                    
        elif ble_state == "LOGGED_IN":
            log_message("-> [RECONNECT] Töltő beacon észlelve LOGGED_IN állapotban. Kapcsolat újraindítása...")
            ble_state = "SENT_IDENTITY_ACK"
            last_identity_ack_time = current_time
            ack_packet = create_ble_packet(0x8002, b"")
            try:
                await client.write_gatt_char(CHAR_FFE9_WRITE, ack_packet, response=True)
                log_message("-> [BLE WRITE SUCCESS] Identity Ack (0x8002) elküldve újrakapcsolódáshoz!")
            except Exception as e:
                log_message(f"-> [BLE WRITE ERROR] Újrakapcsolódási Ack küldési hiba: {e}")
                
    elif cmd_id == 0x0003:
        log_message("-> [HEARTBEAT] Ping (0x0003) érkezett, Heartbeat Pong (0x8003) küldése...")
        pong_packet = create_ble_packet(0x8003, b"\x01")
        try:
            await client.write_gatt_char(CHAR_FFE9_WRITE, pong_packet, response=True)
            log_message("-> [BLE WRITE SUCCESS] Heartbeat Pong (0x8003) sikeresen elküldve!")
        except Exception as e:
            log_message(f"-> [BLE WRITE ERROR] Heartbeat Pong küldési hiba: {e}")

    elif cmd_id == 0x0155:
        log_message(f"-> [BLE ACK] A BLE chip visszaigazolta a csomag átvételét (0x0155). Jelenlegi állapot: {ble_state}")
        if ble_state == "SENT_IDENTITY_ACK":
            log_message("-> [HANDSHAKE SUCCESS] A töltő elfogadta az Identity Ack-ot!")
            ble_state = "IDENTITY_ACKED"
            log_message("-> [LOGIN] Bejelentkezési parancs (0x8001) küldése...")
            ts = int(current_time)
            login_payload = bytearray([0x01])
            login_payload.extend([(ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF])
            login_packet = create_ble_packet(0x8001, bytes(login_payload))
            try:
                await client.write_gatt_char(CHAR_FFE9_WRITE, login_packet, response=True)
                ble_state = "SENT_LOGIN"
                last_login_time = current_time
                log_message("-> [BLE WRITE SUCCESS] Bejelentkezési parancs elküldve a FFE9-re!")
            except Exception as e:
                log_message(f"-> [BLE WRITE ERROR] Login küldési hiba: {e}")
        elif ble_state == "SENT_LOGIN":
            log_message("-> [LOGIN SUCCESS] Sikeresen bejelentkeztünk a töltőbe! Telemetria engedélyezve.")
            ble_state = "LOGGED_IN"
            login_acknowledged = True
            
    elif cmd_id == 0x0002:
        if ble_state in ("INIT", "SENT_IDENTITY_ACK"):
            log_message("-> [HANDSHAKE SUCCESS] A töltő MCU visszaigazolta az Identity Ack-ot (0x0002)!")
            ble_state = "IDENTITY_ACKED"
            log_message("-> [LOGIN] Bejelentkezési parancs (0x8001) küldése...")
            ts = int(current_time)
            login_payload = bytearray([0x01])
            login_payload.extend([(ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF])
            login_packet = create_ble_packet(0x8001, bytes(login_payload))
            try:
                await client.write_gatt_char(CHAR_FFE9_WRITE, login_packet, response=True)
                ble_state = "SENT_LOGIN"
                last_login_time = current_time
                log_message("-> [BLE WRITE SUCCESS] Bejelentkezési parancs elküldve a FFE9-re!")
            except Exception as e:
                log_message(f"-> [BLE WRITE ERROR] Login küldési hiba: {e}")
                
    elif cmd_id == 0x0101:
        if ble_state in ("INIT", "SENT_IDENTITY_ACK", "IDENTITY_ACKED", "SENT_LOGIN"):
            log_message("-> [LOGIN SUCCESS] Sikeresen bejelentkeztünk a töltőbe (státusz alapján)!")
            ble_state = "LOGGED_IN"
            login_acknowledged = True
        
    elif cmd_id in (0x0004, 0x000D):
        if ble_state != "LOGGED_IN":
            ble_state = "LOGGED_IN"
            login_acknowledged = True
            log_message("-> [STATUS] Telemetria észlelve, állapot: LOGGED_IN")
            
        payload = packet[21:]
        if len(payload) >= 33:
            v1 = ((payload[1] << 8) | payload[2]) * 0.1
            i1 = ((payload[3] << 8) | payload[4]) * 0.01
            
            energy_raw = (payload[9] << 24) | (payload[10] << 16) | (payload[11] << 8) | payload[12]
            energy_kwh = energy_raw * 0.01 / 1000.0
            
            t_val = (payload[13] << 8) | payload[14]
            temp_int = (t_val - 20000) * 0.01 if t_val != 0xFFFF else -1.0
            
            v2 = ((payload[25] << 8) | payload[26]) * 0.1
            i2 = ((payload[27] << 8) | payload[28]) * 0.01
            v3 = ((payload[29] << 8) | payload[30]) * 0.1
            i3 = ((payload[31] << 8) | payload[32]) * 0.01
            
            output_state = payload[19]
            plug_state = payload[18]
            is_charging = (output_state == 0x01) or (i1 > 0.1 or i2 > 0.1 or i3 > 0.1)
            is_plug_disconnected = (plug_state == 0x01)
            
            with state_lock:
                shared_state["charger_connected"] = True
                shared_state["pull_plug"] = is_plug_disconnected
                shared_state["voltages"] = [v1, v2, v3]
                shared_state["currents"] = [i1, i2, i3]
                shared_state["energy_total"] = energy_kwh
                shared_state["temperature_internal"] = temp_int
                shared_state["charging_active"] = is_charging
                # Ha nem aktív a töltés, nullázzuk az áramerősség korlátot
                if not is_charging:
                    shared_state["active_current_limit"] = 0

# BLE Értesítéskezelő (GATT NOTIFY) callback
def ble_notification_received(sender, data_bytes):
    global ble_rx_buffer, active_ble_client
    
    # Külön kezeljük a FFC2 jelszó visszaigazolást
    sender_str = str(sender).lower()
    if "ffc2" in sender_str:
        status_val = data_bytes[0] if len(data_bytes) > 0 else -1
        global ble_auth_status
        ble_auth_status = status_val
        if ble_auth_event is not None:
            ble_auth_event.set()
        if status_val == 0:
            log_message("-> [BLE AUTH SUCCESS] A BLE chip elfogadta a jelszót (Visszajelzés: 0)!")
        else:
            log_message(f"-> [BLE AUTH ERROR] Hibás BLE jelszó vagy hiba (Visszajelzés: {status_val})!")
        return

    if len(data_bytes) < 4:
        return
        
    # Csatlakozó kihúzás észlelése
    if b"Pull Plug" in data_bytes:
        with state_lock:
            shared_state["pull_plug"] = True
            shared_state["charging_active"] = False
            shared_state["currents"] = [0.0, 0.0, 0.0]
        log_message("FIGYELEM: A csatlakozó kábel kihúzásra került a töltőből!")
        return

    ble_rx_buffer.extend(data_bytes)
    
    while True:
        header_idx = ble_rx_buffer.find(b'\x06\x01')
        if header_idx == -1:
            if len(ble_rx_buffer) > 0 and ble_rx_buffer[-1] == 0x06:
                ble_rx_buffer = bytearray([0x06])
            else:
                ble_rx_buffer.clear()
            break
            
        if header_idx > 0:
            del ble_rx_buffer[:header_idx]
            
        if len(ble_rx_buffer) < 4:
            break
            
        length = (ble_rx_buffer[2] << 8) | ble_rx_buffer[3]
        if length < 21 or length > 250:
            del ble_rx_buffer[:2]
            continue
            
        if len(ble_rx_buffer) < length:
            break
            
        packet = bytes(ble_rx_buffer[:length])
        del ble_rx_buffer[:length]
        
        if len(packet) >= 4 and packet[-2:] == b'\x0f\x02':
            if active_ble_client is not None:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(process_assembled_packet(packet, active_ble_client))
                except Exception:
                    asyncio.create_task(process_assembled_packet(packet, active_ble_client))
        else:
            print("Figyelmeztetés: Hibás csomagvégződés, elvetve.")

# Aszinkron BLE csatlakozó feladat
async def run_ble_client():
    global shared_state, ble_command_queue, charger_password
    
    if shared_state["simulation"]:
        while True:
            await asyncio.sleep(1)
            # Szimulációban csak ürítjük a sort és naplózzuk
            try:
                packet = ble_command_queue.get_nowait()
                cmd_id = (packet[19] << 8) | packet[20]
                if cmd_id == 0x8007:
                    cmd_name = "START"
                    with state_lock:
                        shared_state["charging_active"] = True
                elif cmd_id == 0x8008:
                    cmd_name = "STOP"
                    with state_lock:
                        shared_state["charging_active"] = False
                elif cmd_id == 0x8107:
                    cmd_name = "SET_CURRENT"
                else:
                    cmd_name = f"CMD_0x{cmd_id:04X}"
                log_message(f"[Szimulált BLE] Parancs elküldve a töltő felé: {cmd_name}")
                ble_command_queue.task_done()
            except asyncio.QueueEmpty:
                pass
        return

    while True:
        try:
            log_message(f"Keresés indítása a következőhöz: {CHARGER_NAME} ({CHARGER_MAC})")
            device = await BleakScanner.find_device_by_filter(
                lambda d, ad: (d.name and CHARGER_NAME in d.name) or d.address.upper() == CHARGER_MAC.upper(),
                timeout=10.0
            )
            
            if device is None:
                log_message("Aktív kereséssel nem találom a töltőt a közelben. Megpróbálok közvetlenül MAC címre kapcsolódni...")
                target = CHARGER_MAC
            else:
                log_message(f"Töltő megtalálva: {device.name} ({device.address})")
                target = device
                
            log_message(f"Kapcsolódás a következőhöz: {target}...")
            # Időkorlát növelése 30 másodpercre a gyenge jel miatt
            async with BleakClient(target, timeout=30.0) as client:
                log_message(f"Sikeresen csatlakozva a BESEN töltőhöz: {CHARGER_MAC}")
                with state_lock:
                    shared_state["charger_connected"] = True
                
                # Állapotok és pufferek alaphelyzetbe állítása új kapcsolatnál
                global ble_rx_buffer, ble_state, login_acknowledged, last_identity_ack_time, last_login_time, active_ble_client
                ble_rx_buffer.clear()
                ble_state = "INIT"
                login_acknowledged = False
                last_identity_ack_time = 0.0
                last_login_time = 0.0
                active_ble_client = client
                
                # 1. Feliratkozás a jelszó visszaigazolásra (FFC2)
                try:
                    await client.start_notify(CHAR_FFC2_NOTIFY, ble_notification_received)
                    log_message("-> [NOTIFY] Feliratkozás az FFC2 (jelszó státusz) csatornára aktív.")
                except Exception as e:
                    log_message(f"-> [NOTIFY ERROR] FFC2 feliratkozási hiba: {e}")
                
                # 2. Elküldjük a jelszót az FFC1-re a soros port feloldásához
                global ble_auth_event, ble_auth_status
                ble_auth_event = asyncio.Event()
                ble_auth_status = None
                
                # Jelszó előkészítése: a BLE chip 12 bájtos formátumot vár (pl. jelszó + jelszó)
                auth_pwd = bytes(charger_password)
                if len(auth_pwd) == 6:
                    auth_pwd = auth_pwd + auth_pwd
                elif len(auth_pwd) < 6:
                    temp = bytearray(auth_pwd)
                    while len(temp) < 6:
                        temp.append(0xFF)
                    auth_pwd = bytes(temp + temp)
                
                # Első kísérlet a megadott jelszóval
                try:
                    log_message(f"-> [BLE AUTH] Jelszó küldése az FFC1 csatornára: {auth_pwd.hex().upper()}")
                    await client.write_gatt_char(CHAR_FFC1_WRITE, auth_pwd, response=False)
                except Exception as e:
                    log_message(f"-> [BLE AUTH ERROR] Jelszó küldési hiba: {e}")
                
                # Várakozás a visszaigazolásra az FFC2-ről (maximum 2.0 másodpercig)
                try:
                    await asyncio.wait_for(ble_auth_event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    log_message("-> [BLE AUTH TIMEOUT] Nem érkezett válasz az FFC2 csatornáról 2.0 másodpercen belül.")
                
                # Ha a jelszó hibás volt, megpróbáljuk a gyári alapértelmezett "000000000000" jelszót
                if ble_auth_status != 0:
                    log_message("-> [BLE AUTH RETRY] Megadott jelszó sikertelen. Megpróbáljuk a gyári alapértelmezett '000000000000' jelszót...")
                    ble_auth_event.clear()
                    ble_auth_status = None
                    default_pwd = b"000000000000"
                    try:
                        log_message(f"-> [BLE AUTH] Gyári jelszó küldése az FFC1 csatornára: {default_pwd.hex().upper()}")
                        await client.write_gatt_char(CHAR_FFC1_WRITE, default_pwd, response=False)
                        await asyncio.wait_for(ble_auth_event.wait(), timeout=2.0)
                        if ble_auth_status == 0:
                            log_message("-> [SZINKRON] A gyári jelszó sikeres volt a BLE chip feloldásához. A csomagok fejlécében a konfigurált jelszót használjuk.")
                    except Exception as e:
                        log_message(f"-> [BLE AUTH ERROR] Gyári jelszó küldési hiba: {e}")
                
                # 3. Feliratkozás az UART olvasó csatornára (FFE4)
                await client.start_notify(CHAR_FFE4_NOTIFY, ble_notification_received)
                log_message("-> [NOTIFY] Feliratkozás az FFE4 (READ) csatornára aktív.")
                
                try:
                    await client.start_notify(CHAR_FFF3_WRITE, ble_notification_received)
                    log_message("-> [NOTIFY] Feliratkozás az FFF3 csatornára aktív.")
                except Exception as e:
                    log_message(f"-> [NOTIFY ERROR] FFF3 feliratkozási hiba: {e}")
                    
                try:
                    await client.start_notify(CHAR_FFD3_NOTIFY, ble_notification_received)
                    log_message("-> [NOTIFY] Feliratkozás az FFD3 csatornára aktív.")
                except Exception as e:
                    log_message(f"-> [NOTIFY ERROR] FFD3 feliratkozási hiba: {e}")
                    
                try:
                    await client.start_notify(CHAR_FD02_NOTIFY, ble_notification_received)
                    log_message("-> [NOTIFY] Feliratkozás az FD02 csatornára aktív.")
                except Exception as e:
                    log_message(f"-> [NOTIFY ERROR] FD02 feliratkozási hiba: {e}")
                
                # Kapcsolat alatti parancsküldő hurok
                while client.is_connected:
                    try:
                        # Ha érkezik parancs a sorba, azonnal kiküldjük
                        packet = ble_command_queue.get_nowait()
                        cmd_id = (packet[19] << 8) | packet[20]
                        if cmd_id == 0x8007:
                            cmd_name = "START"
                        elif cmd_id == 0x8008:
                            cmd_name = "STOP"
                        elif cmd_id == 0x8107:
                            cmd_name = "SET_CURRENT"
                        else:
                            cmd_name = f"CMD_0x{cmd_id:04X}"
                        log_message(f"BLE Parancs kiküldése: {cmd_name} (Hossz: {len(packet)} bájt)")
                        
                        # A parancsokat is az FFE9 (UART RX) csatornára küldjük!
                        await client.write_gatt_char(CHAR_FFE9_WRITE, packet, response=True)
                        ble_command_queue.task_done()
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(1)
                        
        except Exception as e:
            with state_lock:
                shared_state["charger_connected"] = False
                shared_state["charging_active"] = False
                shared_state["currents"] = [0.0, 0.0, 0.0]
            active_ble_client = None
            log_message(f"Bluetooth kapcsolat megszakadt: {e}. Újracsatlakozás 5 másodperc múlva...")
            await asyncio.sleep(5)

# Aszinkron Inverter adatlekérdező task
async def run_inverter_polling():
    global shared_state
    
    while True:
        if shared_state["simulation"]:
            await asyncio.sleep(10)
            continue
            
        try:
            # Solarman V5 alapú lekérdezés megnyitása a logger sticks felé
            inverter = PySolarmanV5(INVERTER_IP, LOGGER_SERIAL, port=INVERTER_PORT, auto_reconnect=True)
            
            # Belső hálózati teljesítmény (Register 607) - Signed Short
            grid_power_internal = to_signed_16(inverter.read_holding_registers(register_addr=607, quantity=1)[0])
            # Külső hálózati teljesítmény (Register 619) - Signed Short
            grid_power_external = to_signed_16(inverter.read_holding_registers(register_addr=619, quantity=1)[0])
            # Házi Akkumulátor SoC (Register 588) - Unsigned Short
            battery_soc = inverter.read_holding_registers(register_addr=588, quantity=1)[0]
            # UPS terhelés / ház fogyasztás (Register 643) - Unsigned Short
            ups_load_power = inverter.read_holding_registers(register_addr=643, quantity=1)[0]
            # Napelem termelés (Register 175) - Unsigned Short
            pv_power = inverter.read_holding_registers(register_addr=175, quantity=1)[0]
            # Akkumulátor terhelés (Register 590) - Signed Short
            battery_power = to_signed_16(inverter.read_holding_registers(register_addr=590, quantity=1)[0])
            
            # Autótöltő fogyasztásának kiszámítása (Külső Grid CT - Inverter saját Grid portja)
            charger_power = max(0, grid_power_external - grid_power_internal)
            
            inverter.disconnect()
            
            with state_lock:
                shared_state["grid_power"] = grid_power_external
                shared_state["battery_soc"] = battery_soc
                shared_state["ups_load_power"] = ups_load_power
                shared_state["pv_power"] = pv_power
                shared_state["battery_power"] = battery_power
                shared_state["charger_power"] = charger_power
                shared_state["inverter_connected"] = True
                
            log_message(f"Deye Inverter: Grid={grid_power_external}W, UPS={ups_load_power}W, Nem_UPS={charger_power}W, PV={pv_power}W, Akku={battery_power}W (SoC={battery_soc}%)")
            
        except Exception as e:
            with state_lock:
                shared_state["inverter_connected"] = False
            log_message(f"Deye Logger lekérdezési hiba ({INVERTER_IP}): {e}. Újrapróbálkozás 10 másodperc múlva...")
            
        await asyncio.sleep(10)

# Aszinkron Fő Döntési és Vezérlő Task
async def run_charge_controller():
    global shared_state, ble_command_queue
    
    # Vezérlési segédváltozók
    last_sent_action = None       # "START" vagy "STOP"
    start_command_time = None     # Időpont, mikor a Start parancsot kiküldtük (timeout figyeléshez)
    consecutive_failures = 0      # Egymást követő sikertelen indítási kísérletek száma
    import_exceeded_since = None  # Időpont, mikor a fogyasztás először túllépte a limitet
    
    def time_to_minutes(t_str):
        try:
            h, m = map(int, t_str.split(':'))
            return h * 60 + m
        except Exception:
            return 0
            
    while True:
        await asyncio.sleep(5)    # 5 másodpercenként értékeljük ki a helyzetet
        
        current_time = time.time()
        
        # Mentjük a lokális változókat lock alatt
        with state_lock:
            # 1. Határozzuk meg az aktív vezérlési módot (Precedence)
            if shared_state.get("force_submode", "schedule") in ("manual_start", "manual_stop"):
                effective_mode = "force"
            elif shared_state.get("schedule_enabled", False):
                effective_mode = "schedule"
            elif shared_state.get("auto_enabled", False):
                effective_mode = "auto"
            else:
                effective_mode = "monitoring"
                
            shared_state["control_mode"] = effective_mode
            mode = effective_mode
            
            inverter_ok = shared_state["inverter_connected"]
            charger_ok = shared_state["charger_connected"]
            grid_power = shared_state["grid_power"]
            ups_load_power = shared_state["ups_load_power"]
            battery_soc = shared_state["battery_soc"]
            charging_active = shared_state["charging_active"]
            pull_plug = shared_state["pull_plug"]
            cooldown_until = shared_state["cooldown_until"]
            
            start_soc = shared_state["start_soc"]
            stop_import_limit = shared_state["stop_import_limit"]
            grid_charge_duration_minutes = shared_state["grid_charge_duration_minutes"]
            house_power_limit_w = shared_state["house_power_limit_w"]
            
            charger_max_amps = shared_state["charger_max_amps"]
            force_submode = shared_state["force_submode"]
            forced_schedule = shared_state["forced_schedule"]
            active_current_limit = shared_state["active_current_limit"]
            auto_enabled = shared_state.get("auto_enabled", False)
            schedule_enabled = shared_state.get("schedule_enabled", False)
            
            sim_mode = shared_state["simulation"]
            sim_external_session = shared_state["sim_external_session"]
            sim_custom_time = shared_state["sim_custom_time"]
            sim_custom_day = shared_state["sim_custom_day"]
            
            # Manuális beavatkozási flagek
            apply_with_stop = shared_state["apply_with_stop"]
            apply_with_restart = shared_state["apply_with_restart"]
            reset_limit = shared_state["reset_limit"]
            schedule_solar_auto = shared_state["schedule_solar_auto"]
            manual_start_requested = shared_state.get("manual_start_requested", False)
            restart_pending_start = shared_state.get("restart_pending_start", False)

        # Ha a töltés nem aktív és nem várunk megerősítésre, engedélyezzük az új parancsokat
        if not charging_active and start_command_time is None:
            last_sent_action = None
            
            # Kézi indítás felülbírálás lecsengése
            if force_submode == "manual_start":
                log_message("[VEZÉRLÉS] A kézi indítású töltés befejeződött vagy megszakadt. Felülbírálás visszavonva, visszatérés automatikus módokhoz.")
                with state_lock:
                    shared_state["force_submode"] = "schedule"
                    # Újraszámoljuk az aktív módot
                    if shared_state.get("schedule_enabled", False):
                        effective_mode = "schedule"
                    elif shared_state.get("auto_enabled", False):
                        effective_mode = "auto"
                    else:
                        effective_mode = "monitoring"
                    shared_state["control_mode"] = effective_mode
                    mode = effective_mode
                    force_submode = "schedule"

        # Ha Figyelés (monitoring) üzemmódban vagyunk, alaphelyzetbe állítunk mindent
        if mode == "monitoring":
            last_sent_action = None
            start_command_time = None
            consecutive_failures = 0
            import_exceeded_since = None
            continue

        # Ha a töltő kábele ki van húzva, nem hajtunk végre parancsokat
        if pull_plug:
            last_sent_action = None
            start_command_time = None
            import_exceeded_since = None
            continue

        # Idő és nap meghatározása (szimulációban felülbírálható)
        local_time = time.localtime()
        DAYS_MAP = ["Hétfő", "Kedd", "Szerda", "Csütörtök", "Péntek", "Szombat", "Vasárnap"]
        
        current_day_name = DAYS_MAP[local_time.tm_wday]
        current_minutes = local_time.tm_hour * 60 + local_time.tm_min
        
        if sim_mode:
            if sim_custom_day and sim_custom_day in DAYS_MAP:
                current_day_name = sim_custom_day
            if sim_custom_time and ":" in sim_custom_time:
                try:
                    sh, sm = map(int, sim_custom_time.split(':'))
                    current_minutes = sh * 60 + sm
                except Exception:
                    pass

        # Heti ütemezési időablak előzetes kiszámítása (minden üzemmódban elérhetővé tesszük a szabályellenőrzéshez)
        current_day_idx = DAYS_MAP.index(current_day_name)
        prev_day_name = DAYS_MAP[(current_day_idx - 1) % 7]
        
        current_sched = next((item for item in forced_schedule if item["day"] == current_day_name), None)
        prev_sched = next((item for item in forced_schedule if item["day"] == prev_day_name), None)
        
        in_interval = False
        target_amps = 16
        override_auto = True
        
        # Aktuális nap ütemezése
        if current_sched and current_sched.get("enabled", False):
            start_m = time_to_minutes(current_sched["start"])
            stop_m = time_to_minutes(current_sched["stop"])
            amps = current_sched.get("amps", 16)
            day_override = current_sched.get("override_auto", True)
            
            if start_m < stop_m:
                if start_m <= current_minutes < stop_m:
                    in_interval = True
                    target_amps = amps
                    override_auto = day_override
            else: # Éjszakai átnyúlás a jelenlegi napon
                if current_minutes >= start_m:
                    in_interval = True
                    target_amps = amps
                    override_auto = day_override
                    
        # Előző napi átnyúló ütemezés
        if not in_interval and prev_sched and prev_sched.get("enabled", False):
            start_m = time_to_minutes(prev_sched["start"])
            stop_m = time_to_minutes(prev_sched["stop"])
            amps = prev_sched.get("amps", 16)
            day_override = prev_sched.get("override_auto", True)
            
            if start_m > stop_m: # Átnyúló volt
                if current_minutes < stop_m:
                    in_interval = True
                    target_amps = amps
                    override_auto = day_override

        # Ellenőrizzük a kapcsolatokat (csak ha nem szimulációról van szó)
        if not sim_mode and (not inverter_ok or not charger_ok):
            # Kapcsolat nélkül nem tudunk biztonságosan parancsot végrehajtani
            continue

        # Szimulált vagy valós külső indítás kezelése
        is_external_session = sim_external_session if sim_mode else (last_sent_action != "START")

        # --- INDÍTÁS ELLENŐRZÉSI TIMEOUT LOGIKA ---
        if start_command_time is not None:
            if charging_active:
                # Töltés sikeresen elindult! Visszaállítjuk a hiba számlálót
                consecutive_failures = 0
                start_command_time = None
                log_message("[VEZÉRLÉS] A töltés elindulása megerősítve a telemetria alapján.")
            elif not sim_mode:
                # A timeout számlálása csak valós BLE módban fusson le
                elapsed = current_time - start_command_time
                if elapsed >= 60.0:
                    # Letelt a 60 másodperces timeout, de nem indult el a töltés
                    consecutive_failures += 1
                    start_command_time = None
                    last_sent_action = None
                    cooldown_time = current_time + 120.0 # 2 perc lehűlés
                    
                    with state_lock:
                        shared_state["cooldown_until"] = cooldown_time
                    
                    log_message(f"[HIBA] Töltésindítási kísérlet sikertelen (Timeout). Próbálkozás: {consecutive_failures}/3")
                    
                    if consecutive_failures >= 3:
                        # Rendszer leállítása Csak figyelés módba (kikapcsoljuk az automatizmusokat)
                        with state_lock:
                            shared_state["auto_enabled"] = False
                            shared_state["schedule_enabled"] = False
                            shared_state["force_submode"] = "schedule"
                            shared_state["control_mode"] = "monitoring"
                            shared_state["error_message"] = "Töltésindítás 3 kísérlet után sem sikerült. Rendszer leállítva."
                            shared_state["persist_mode_on_restart"] = False
                        save_config_file()
                        log_message("[BIZTONSÁG] Automatikus mód leállítva a sorozatos sikertelen kísérletek miatt. Kézi ellenőrzés szükséges.")
                    continue
                else:
                    # Még várunk a timeout-on belül
                    continue

        # Ha lehűlési idő alatt vagyunk, nem küldünk új parancsot
        if current_time < cooldown_until:
            continue

        # Ha letelt a lehűlés és újraindításra várunk
        if restart_pending_start:
            with state_lock:
                shared_state["restart_pending_start"] = False
            restart_pending_start = False
            if mode == "force":
                with state_lock:
                    shared_state["manual_start_requested"] = True
                manual_start_requested = True

        # --- ALKALMAZÁSI ÉS ÚJRAINDÍTÁSI FLAGEK FELDOLGOZÁSA ---
        # 1. Kézi leállítás
        if apply_with_stop:
            log_message("[VEZÉRLÉS] Felhasználói árammódosítás leállítással kérték. Töltés LEÁLLÍTÁSA...")
            stop_payload = bytearray(47)
            stop_payload[0] = 0x01
            packet = create_ble_packet(0x8008, bytes(stop_payload))
            await ble_command_queue.put(packet)
            last_sent_action = "STOP"
            start_command_time = None
            with state_lock:
                shared_state["active_current_limit"] = 0
                shared_state["apply_with_stop"] = False
            continue

        # 2. Kézi újraindítás (Alkalmaz gomb Force módban)
        if apply_with_restart:
            log_message("[VEZÉRLÉS] Felhasználói árammódosítás újraindítással kérték. Töltés leállítása, újraindítás 15 mp múlva...")
            stop_payload = bytearray(47)
            stop_payload[0] = 0x01
            packet = create_ble_packet(0x8008, bytes(stop_payload))
            await ble_command_queue.put(packet)
            last_sent_action = "STOP"
            start_command_time = None
            cooldown_time = current_time + 15.0
            with state_lock:
                shared_state["active_current_limit"] = 0
                shared_state["cooldown_until"] = cooldown_time
                shared_state["apply_with_restart"] = False
                shared_state["restart_pending_start"] = True
            continue

        # 3. Mentés újraindítás nélkül (reset_limit)
        if reset_limit:
            # Megkeressük az aktuális céláramot a mód alapján
            if mode == "schedule":
                new_limit = target_amps if in_interval else (charger_max_amps if schedule_solar_auto else 0)
            else:
                new_limit = charger_max_amps
            log_message(f"[VEZÉRLÉS] Áramerősség limit frissítve újraindítás nélkül. Új baseline: {new_limit}A")
            with state_lock:
                shared_state["active_current_limit"] = new_limit
                shared_state["reset_limit"] = False
            active_current_limit = new_limit

        # Szimulációs elvárt állapot meghatározása (Assertion Engine)
        expected_action = "KEEP"
        if sim_mode:
            if pull_plug:
                expected_action = "STOP" if charging_active else "KEEP"
            elif mode == "monitoring":
                expected_action = "KEEP"
            elif mode == "force":
                if manual_start_requested:
                    expected_action = "START"
                elif force_submode == "manual_stop":
                    expected_action = "STOP" if (charging_active or last_sent_action == "START") else "KEEP"
                else:
                    expected_action = "KEEP"
            elif mode == "schedule":
                if in_interval:
                    if override_auto:
                        if not charging_active:
                            expected_action = "START"
                        else:
                            if is_external_session:
                                expected_action = "KEEP"
                            elif target_amps > 0 and active_current_limit > 0 and active_current_limit != target_amps:
                                expected_action = "RESTART"
                            else:
                                expected_action = "KEEP"
                    else: # Solar auto az időablakon belül
                        if not charging_active:
                            expected_action = "START" if battery_soc >= start_soc else "KEEP"
                        else:
                            if is_external_session:
                                expected_action = "KEEP"
                            else:
                                if (house_power_limit_w > 0 and ups_load_power > house_power_limit_w) or                                    (grid_charge_duration_minutes > 0 and import_exceeded_since is not None and current_time - import_exceeded_since >= grid_charge_duration_minutes * 60):
                                    expected_action = "STOP"
                                else:
                                    expected_action = "KEEP"
                else: # Időablakon kívül
                    if schedule_solar_auto:
                        if not charging_active:
                            expected_action = "START" if battery_soc >= start_soc else "KEEP"
                        else:
                            if is_external_session:
                                expected_action = "KEEP"
                            else:
                                if (house_power_limit_w > 0 and ups_load_power > house_power_limit_w) or                                    (grid_charge_duration_minutes > 0 and import_exceeded_since is not None and current_time - import_exceeded_since >= grid_charge_duration_minutes * 60):
                                    expected_action = "STOP"
                                else:
                                    expected_action = "KEEP"
                    else:
                        if charging_active:
                            expected_action = "KEEP" if is_external_session else "STOP"
                        else:
                            expected_action = "KEEP"
            elif mode == "auto":
                if not charging_active:
                    expected_action = "START" if battery_soc >= start_soc else "KEEP"
                else:
                    if is_external_session:
                        expected_action = "KEEP"
                    else:
                        if (house_power_limit_w > 0 and ups_load_power > house_power_limit_w) or                            (grid_charge_duration_minutes > 0 and import_exceeded_since is not None and current_time - import_exceeded_since >= grid_charge_duration_minutes * 60):
                            expected_action = "STOP"
                        else:
                            expected_action = "KEEP"

        actual_action = "KEEP"

        # --- ÜZEMMÓD-ALAPÚ DÖNTÉSEK ---
        
        # 1. Kényszerített (Force Charge) Mód
        if mode == "force":
            if manual_start_requested:
                start_amps = 16 if charger_max_amps == 0 else charger_max_amps
                log_message(f"[VEZÉRLÉS] Kényszerített kézi töltés indítása ({start_amps}A)...")
                start_payload = bytearray(47)
                start_payload[0] = 0x01
                start_payload[33] = 0x00
                ts = int(time.time())
                start_payload[34] = (ts >> 24) & 0xFF
                start_payload[35] = (ts >> 16) & 0xFF
                start_payload[36] = (ts >> 8) & 0xFF
                start_payload[37] = ts & 0xFF
                start_payload[38] = 0x01
                start_payload[39] = 0x01
                start_payload[46] = start_amps
                
                packet = create_ble_packet(0x8007, bytes(start_payload))
                await ble_command_queue.put(packet)
                
                last_sent_action = "START"
                start_command_time = current_time
                actual_action = "START"
                with state_lock:
                    shared_state["manual_start_requested"] = False
                    shared_state["force_submode"] = "manual_start"
                    shared_state["active_current_limit"] = charger_max_amps
                
            elif force_submode == "manual_stop":
                if charging_active or last_sent_action == "START":
                    log_message("[VEZÉRLÉS] Kényszerített kézi töltés leállítása...")
                    stop_payload = bytearray(47)
                    stop_payload[0] = 0x01
                    
                    packet = create_ble_packet(0x8008, bytes(stop_payload))
                    await ble_command_queue.put(packet)
                    
                    last_sent_action = "STOP"
                    actual_action = "STOP"
                    with state_lock:
                        shared_state["active_current_limit"] = 0

        # 2. Ütemezett (Schedule) Mód
        elif mode == "schedule":
            if in_interval:
                if override_auto:
                    # Ütemezés az első
                    if not charging_active and last_sent_action != "START":
                        start_amps = 16 if target_amps == 0 else target_amps
                        log_message(f"[VEZÉRLÉS] Ütemezési időablak aktív (Prioritás BE). Töltés indítása ({start_amps}A)...")
                        start_payload = bytearray(47)
                        start_payload[0] = 0x01
                        start_payload[33] = 0x00
                        ts = int(time.time())
                        start_payload[34] = (ts >> 24) & 0xFF
                        start_payload[35] = (ts >> 16) & 0xFF
                        start_payload[36] = (ts >> 8) & 0xFF
                        start_payload[37] = ts & 0xFF
                        start_payload[38] = 0x01
                        start_payload[39] = 0x01
                        start_payload[46] = start_amps
                        
                        packet = create_ble_packet(0x8007, bytes(start_payload))
                        await ble_command_queue.put(packet)
                        
                        last_sent_action = "START"
                        start_command_time = current_time
                        actual_action = "START"
                        with state_lock:
                            shared_state["active_current_limit"] = target_amps
                            
                    elif charging_active and not is_external_session:
                        # Változott az ütemezés szerinti áram? (Csak ha nem unmanaged)
                        if target_amps > 0:
                            if active_current_limit == 0:
                                # Baseline rögzítése leállítás nélkül
                                log_message(f"[VEZÉRLÉS] Ütemezett töltés baseline rögzítve: {target_amps}A (leállítás nélkül)")
                                with state_lock:
                                    shared_state["active_current_limit"] = target_amps
                            elif active_current_limit != target_amps:
                                log_message(f"[VEZÉRLÉS] Ütemezett áramerősség változás ({active_current_limit}A -> {target_amps}A). Újraindítás...")
                                stop_payload = bytearray(47)
                                stop_payload[0] = 0x01
                                packet = create_ble_packet(0x8008, bytes(stop_payload))
                                await ble_command_queue.put(packet)
                                
                                last_sent_action = "STOP"
                                start_command_time = None
                                cooldown_time = current_time + 15.0
                                actual_action = "RESTART"
                                with state_lock:
                                    shared_state["active_current_limit"] = 0
                                    shared_state["cooldown_until"] = cooldown_time
                else:
                    # Solar auto szabályok az időablakon belül
                    # --- INDÍTÁSI FELTÉTEL ---
                    if not charging_active and last_sent_action != "START":
                        if battery_soc >= start_soc:
                            start_amps = 16 if charger_max_amps == 0 else charger_max_amps
                            log_message(f"[VEZÉRLÉS] Solar Auto feltételek teljesültek az időablakon belül. Töltés INDÍTÁSA ({start_amps}A)...")
                            start_payload = bytearray(47)
                            start_payload[0] = 0x01
                            start_payload[33] = 0x00
                            ts = int(time.time())
                            start_payload[34] = (ts >> 24) & 0xFF
                            start_payload[35] = (ts >> 16) & 0xFF
                            start_payload[36] = (ts >> 8) & 0xFF
                            start_payload[37] = ts & 0xFF
                            start_payload[38] = 0x01
                            start_payload[39] = 0x01
                            start_payload[46] = start_amps
                            
                            packet = create_ble_packet(0x8007, bytes(start_payload))
                            await ble_command_queue.put(packet)
                            
                            last_sent_action = "START"
                            start_command_time = current_time
                            actual_action = "START"
                            with state_lock:
                                shared_state["active_current_limit"] = charger_max_amps
                    # --- LEÁLLÍTÁSI FELTÉTELEK (csak ha nem külső session) ---
                    elif charging_active and not is_external_session:
                        should_stop = False
                        reason = ""
                        if house_power_limit_w > 0 and ups_load_power > house_power_limit_w:
                            should_stop = True
                            reason = f"Ház UPS terhelése ({ups_load_power} W) meghaladta a korlátot ({house_power_limit_w} W)"
                        elif grid_charge_duration_minutes > 0:
                            if grid_power > stop_import_limit:
                                if import_exceeded_since is None:
                                    import_exceeded_since = current_time
                                elif current_time - import_exceeded_since >= grid_charge_duration_minutes * 60:
                                    should_stop = True
                                    reason = f"Hálózati töltési időkorlát ({grid_charge_duration_minutes} perc) letelt"
                            else:
                                import_exceeded_since = None
                                
                        if should_stop:
                            log_message(f"[VEZÉRLÉS] Solar Auto leállítási ok teljesült az időablakon belül: {reason}. Töltés LEÁLLÍTÁSA...")
                            stop_payload = bytearray(47)
                            stop_payload[0] = 0x01
                            packet = create_ble_packet(0x8008, bytes(stop_payload))
                            await ble_command_queue.put(packet)
                            
                            last_sent_action = "STOP"
                            actual_action = "STOP"
                            import_exceeded_since = None
                            with state_lock:
                                shared_state["active_current_limit"] = 0
            else:
                # Időablakon kívül
                if schedule_solar_auto:
                    # Solar auto szabályok
                    if not charging_active and last_sent_action != "START":
                        if battery_soc >= start_soc:
                            start_amps = 16 if charger_max_amps == 0 else charger_max_amps
                            log_message(f"[VEZÉRLÉS] Solar Auto feltételek teljesültek az időablakon kívül. Töltés INDÍTÁSA ({start_amps}A)...")
                            start_payload = bytearray(47)
                            start_payload[0] = 0x01
                            start_payload[33] = 0x00
                            ts = int(time.time())
                            start_payload[34] = (ts >> 24) & 0xFF
                            start_payload[35] = (ts >> 16) & 0xFF
                            start_payload[36] = (ts >> 8) & 0xFF
                            start_payload[37] = ts & 0xFF
                            start_payload[38] = 0x01
                            start_payload[39] = 0x01
                            start_payload[46] = start_amps
                            
                            packet = create_ble_packet(0x8007, bytes(start_payload))
                            await ble_command_queue.put(packet)
                            
                            last_sent_action = "START"
                            start_command_time = current_time
                            actual_action = "START"
                            with state_lock:
                                shared_state["active_current_limit"] = charger_max_amps
                    elif charging_active and not is_external_session:
                        should_stop = False
                        reason = ""
                        if house_power_limit_w > 0 and ups_load_power > house_power_limit_w:
                            should_stop = True
                            reason = f"Ház UPS terhelése ({ups_load_power} W) meghaladta a korlátot ({house_power_limit_w} W)"
                        elif grid_charge_duration_minutes > 0:
                            if grid_power > stop_import_limit:
                                if import_exceeded_since is None:
                                    import_exceeded_since = current_time
                                elif current_time - import_exceeded_since >= grid_charge_duration_minutes * 60:
                                    should_stop = True
                                    reason = f"Hálózati töltési időkorlát ({grid_charge_duration_minutes} perc) letelt"
                            else:
                                import_exceeded_since = None
                                
                        if should_stop:
                            log_message(f"[VEZÉRLÉS] Solar Auto leállítási ok teljesült az időablakon kívül: {reason}. Töltés LEÁLLÍTÁSA...")
                            stop_payload = bytearray(47)
                            stop_payload[0] = 0x01
                            packet = create_ble_packet(0x8008, bytes(stop_payload))
                            await ble_command_queue.put(packet)
                            
                            last_sent_action = "STOP"
                            actual_action = "STOP"
                            import_exceeded_since = None
                            with state_lock:
                                shared_state["active_current_limit"] = 0
                else:
                    # Nincs Solar Auto -> Leállítás (csak ha nem külső indítású)
                    if (charging_active or last_sent_action == "START") and not is_external_session:
                        log_message("[VEZÉRLÉS] Ütemezési időablakon kívül vagyunk (Solar Auto KI). Töltés LEÁLLÍTÁSA...")
                        stop_payload = bytearray(47)
                        stop_payload[0] = 0x01
                        packet = create_ble_packet(0x8008, bytes(stop_payload))
                        await ble_command_queue.put(packet)
                        
                        last_sent_action = "STOP"
                        actual_action = "STOP"
                        with state_lock:
                            shared_state["active_current_limit"] = 0

        # 3. Automatikus (Solar Auto) Mód
        elif mode == "auto":
            # --- INDÍTÁSI FELTÉTEL ---
            if not charging_active and last_sent_action != "START":
                if battery_soc >= start_soc:
                    start_amps = 16 if charger_max_amps == 0 else charger_max_amps
                    log_message(f"[VEZÉRLÉS] Feltételek teljesültek (Akku SoC: {battery_soc}% >= {start_soc}%). Töltés INDÍTÁSA ({start_amps}A)...")
                    
                    start_payload = bytearray(47)
                    start_payload[0] = 0x01
                    start_payload[33] = 0x00
                    ts = int(time.time())
                    start_payload[34] = (ts >> 24) & 0xFF
                    start_payload[35] = (ts >> 16) & 0xFF
                    start_payload[36] = (ts >> 8) & 0xFF
                    start_payload[37] = ts & 0xFF
                    start_payload[38] = 0x01
                    start_payload[39] = 0x01
                    start_payload[46] = start_amps
                    
                    packet = create_ble_packet(0x8007, bytes(start_payload))
                    await ble_command_queue.put(packet)
                    
                    last_sent_action = "START"
                    start_command_time = current_time
                    import_exceeded_since = None
                    actual_action = "START"
                    with state_lock:
                        shared_state["active_current_limit"] = charger_max_amps
                        
            # --- LEÁLLÍTÁSI FELTÉTELEK (csak ha nem külső session) ---
            elif charging_active and not is_external_session:
                should_stop = False
                reason = ""
                
                # 1. szabály: Ház túlterhelés-védelem (Deye UPS port terhelés alapján)
                if house_power_limit_w > 0 and ups_load_power > house_power_limit_w:
                    should_stop = True
                    reason = f"Ház UPS terhelése ({ups_load_power} W) meghaladta a korlátot ({house_power_limit_w} W)"
                
                # 2. szabály: Hálózati töltés időtartama leállítás előtt
                elif grid_charge_duration_minutes > 0:
                    if grid_power > stop_import_limit:
                        if import_exceeded_since is None:
                            import_exceeded_since = current_time
                            log_message(f"[VEZÉRLÉS] Hálózati terhelés ({grid_power} W) meghaladta a limitet ({stop_import_limit} W). Hálózati töltés időzítő elindítva ({grid_charge_duration_minutes} perc)...")
                        elif current_time - import_exceeded_since >= grid_charge_duration_minutes * 60:
                            should_stop = True
                            reason = f"Hálózati töltési időkorlát ({grid_charge_duration_minutes} perc) letelt"
                    else:
                        # Ha a fogyasztás visszaesik a limit alá, töröljük az időzítőt
                        if import_exceeded_since is not None:
                            import_exceeded_since = None
                            log_message("[VEZÉRLÉS] Hálózati terhelés visszaesett a limit alá. Időzítő törölve.")
                
                if should_stop:
                    log_message(f"[VEZÉRLÉS] Leállítási ok teljesült: {reason}. Töltés LEÁLLÍTÁSA...")
                    
                    stop_payload = bytearray(47)
                    stop_payload[0] = 0x01
                    
                    packet = create_ble_packet(0x8008, bytes(stop_payload))
                    await ble_command_queue.put(packet)
                    
                    last_sent_action = "STOP"
                    actual_action = "STOP"
                    import_exceeded_since = None
                    with state_lock:
                        shared_state["active_current_limit"] = 0

        # --- SZABÁLYELLENŐRZÉS KIÉRTÉKELÉSE ---
        if sim_mode:
            # Összevetjük a várt és a tényleges műveletet
            if expected_action == actual_action:
                with state_lock:
                    shared_state["assertion_status"] = "OK"
                    shared_state["last_assertion_error"] = ""
            else:
                # Speciális eset: a restart művelet ténylegesen STOP-pal kezdődik a sorban
                if expected_action == "RESTART" and actual_action == "STOP":
                    with state_lock:
                        shared_state["assertion_status"] = "OK"
                        shared_state["last_assertion_error"] = ""
                else:
                    err_msg = f"ELTÉRÉS A TERVTŐL! Elvárt: {expected_action}, Tényleges végrehajtott: {actual_action}"
                    log_message(f"[LOGIKAI TESZT HIBA] {err_msg}")
                    with state_lock:
                        shared_state["assertion_status"] = "WARNING"
                        shared_state["last_assertion_error"] = err_msg

# Szimulációs háttér taskok (ha nincs hardver)
async def run_simulation_telemetry():
    global shared_state
    log_message("Szimulációs telemetria hurok aktív.")
    
    while True:
        await asyncio.sleep(2)
        with state_lock:
            # Csak szimulációban frissítünk mesterséges értékeket
            if shared_state["simulation"]:
                shared_state["inverter_connected"] = True
                shared_state["charger_connected"] = True
                
                # Alapértelmezett UPS terhelés, ha nincs megadva
                if shared_state["ups_load_power"] == 0:
                    shared_state["ups_load_power"] = 450
                
                # Szimuláljuk a csatlakozást és a töltő működését a beállított mód szerint
                if not shared_state["pull_plug"]:
                    if shared_state["charging_active"]:
                        shared_state["voltages"] = [230.1, 229.4, 231.2]
                        # 3 fázison 10A-es töltés
                        shared_state["currents"] = [10.0, 10.0, 10.0]
                        shared_state["energy_total"] += 0.005
                        shared_state["temperature_internal"] = 38.5
                    else:
                        shared_state["voltages"] = [231.5, 230.8, 232.0]
                        shared_state["currents"] = [0.0, 0.0, 0.0]
                        shared_state["temperature_internal"] = 24.2
                else:
                    shared_state["voltages"] = [0.0, 0.0, 0.0]
                    shared_state["currents"] = [0.0, 0.0, 0.0]
                    shared_state["charging_active"] = False

# Szimulációs konzol figyelő (ha tesztelés céljából fut és konzolról akarunk értéket állítani)
def console_simulation_input():
    global shared_state
    print("\n=======================================================")
    print("SZIMULÁCIÓS MÓD AKTÍV.")
    print("Parancsok hálózati értékek szimulálásához a konzolról:")
    print("  - Írj be egy számot (pl. -3000): Hálózati teljesítmény (- = felesleg, + = fogyasztás)")
    print("  - Írj be egy százalékot 0-100 között (pl. 85): Házi akkumulátor SoC%")
    print("  - 'ups <szám>' (pl. ups 2500): UPS házfogyasztás beállítása (W)")
    print("  - 'pv <szám>' (pl. pv 4000): Napelemes termelés beállítása (W)")
    print("  - 'bat <szám>' (pl. bat -1200): Akkumulátor terhelés beállítása (+/- W)")
    print("  - 'pull': Csatlakozó kábel kihúzása")
    print("  - 'plug': Csatlakozó kábel visszadugása")
    print("  - 'start': Manuálisan elindított szimulált töltés")
    print("  - 'stop': Manuálisan leállított szimulált töltés")
    print("=======================================================\n")
    
    while True:
        try:
            line = input().strip()
            if not line:
                continue
                
            if line.lower() == "pull":
                with state_lock:
                    shared_state["pull_plug"] = True
                    shared_state["charging_active"] = False
                log_message("[Szimuláció] Töltő csatlakozó kihúzva.")
            elif line.lower() == "plug":
                with state_lock:
                    shared_state["pull_plug"] = False
                log_message("[Szimuláció] Töltő csatlakozó visszadugva.")
            elif line.lower() == "start":
                with state_lock:
                    shared_state["charging_active"] = True
                log_message("[Szimuláció] Töltés futásállapot elindítva.")
            elif line.lower() == "stop":
                with state_lock:
                    shared_state["charging_active"] = False
                log_message("[Szimuláció] Töltés futásállapot leállítva.")
            elif line.lower().startswith("ups "):
                try:
                    val = int(line.split()[1])
                    with state_lock:
                        shared_state["ups_load_power"] = val
                    log_message(f"[Szimuláció] UPS házfogyasztás szint módosítva: {val} W")
                except Exception as ex:
                    print(f"Hibás UPS formátum: {ex}. Használat: ups <szám>")
            elif line.lower().startswith("pv "):
                try:
                    val = int(line.split()[1])
                    with state_lock:
                        shared_state["pv_power"] = val
                    log_message(f"[Szimuláció] Napelemes PV termelés szint módosítva: {val} W")
                except Exception as ex:
                    print(f"Hibás PV formátum: {ex}. Használat: pv <szám>")
            elif line.lower().startswith("bat "):
                try:
                    val = int(line.split()[1])
                    with state_lock:
                        shared_state["battery_power"] = val
                    log_message(f"[Szimuláció] Akkumulátor terhelés szint módosítva: {val} W")
                except Exception as ex:
                    print(f"Hibás akku terhelés formátum: {ex}. Használat: bat <szám>")
            elif line.startswith("-") or line.isdigit():
                val = int(line)
                if 0 <= val <= 100 and not line.startswith("-"):
                    with state_lock:
                        shared_state["battery_soc"] = val
                    log_message(f"[Szimuláció] Akku SoC szint módosítva: {val}%")
                else:
                    with state_lock:
                        shared_state["grid_power"] = val
                    log_message(f"[Szimuláció] Hálózati teljesítmény módosítva: {val} W")
            else:
                print("Nem támogatott szimulációs parancs.")
        except Exception as e:
            print(f"Hiba a konzolos bemenetnél: {e}")

# --- FŐ PROGRAM BELÉPÉSI PONT ---
async def main():
    global shared_state, ble_command_queue
    
    print("=== Deye & BESEN Integrált Telemetria és Töltésvezérlő ===")
    
    # Konfiguráció betöltése fájlból
    load_config()
    
    ble_command_queue = asyncio.Queue()
    
    # Parancssori argumentum ellenőrzése
    if len(sys.argv) > 1 and sys.argv[1] == "--sim":
        with state_lock:
            shared_state["simulation"] = True
            shared_state["battery_soc"] = 75
            shared_state["grid_power"] = 1500
            shared_state["pv_power"] = 3200
            shared_state["battery_power"] = -1000
            shared_state["ups_load_power"] = 450
        log_message("A program SZIMULÁCIÓS módban indul.")
        
        # Konzolos beviteli szál elindítása szimulációhoz
        sim_thread = threading.Thread(target=console_simulation_input, daemon=True)
        sim_thread.start()
        
        # Aszinkron szimulációs háttér task indítása
        asyncio.create_task(run_simulation_telemetry())
    else:
        log_message(f"Deye Inverter Logger beállítva: {INVERTER_IP}:{INVERTER_PORT} (S/N: {LOGGER_SERIAL})")
        log_message(f"BESEN Charger BLE beállítva: {CHARGER_NAME} ({CHARGER_MAC})")
        
    # HTTP Dashboard Szerver elindítása háttérszálban
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    
    # Összefogjuk és elindítjuk a párhuzamos aszinkron feladatokat
    await asyncio.gather(
        run_inverter_polling(),
        run_ble_client(),
        run_charge_controller()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram leállítva a felhasználó által.")
        sys.exit(0)
