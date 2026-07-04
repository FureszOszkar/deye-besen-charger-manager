import json
import os
import threading
import time

# --- ALAPÉRTELMEZETT KONFIGURÁCIÓS BEÁLLÍTÁSOK ---
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "inverter_ip": "192.168.0.100",
    "inverter_port": 8899,
    "logger_serial": 1234567890,
    "http_port": 8080,
    "start_soc": 100,
    "stop_soc": 0,
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
    "pbkdf2_iterations": 100000,
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

# Inverter IP és port beállítások
INVERTER_IP = "192.168.0.100"
INVERTER_PORT = 8899
LOGGER_SERIAL = 1234567890

# Töltő BLE beállítások
CHARGER_NAME = "ACP#DefaultName"
CHARGER_MAC = "00:11:22:33:44:55"

# HTTP szerver port
HTTP_PORT = 8080

# --- BLE GATT UUID-k ---
CHAR_FFE4_NOTIFY = "0000ffe4-0000-1000-8000-00805f9b34fb"
CHAR_FFE9_WRITE = "0000ffe9-0000-1000-8000-00805f9b34fb"
CHAR_FFC1_WRITE = "0000ffc1-0000-1000-8000-00805f9b34fb"
CHAR_FFC2_NOTIFY = "0000ffc2-0000-1000-8000-00805f9b34fb"
CHAR_FFD3_NOTIFY = "0000ffd3-0000-1000-8000-00805f9b34fb"
CHAR_FD02_NOTIFY = "0000fd02-0000-1000-8000-00805f9b34fb"
CHAR_FFF3_WRITE = "0000fff3-0000-1000-8000-00805f9b34fb"

# Gyári alapértelmezett jelszó helyőrző a csomagok fejlécéhez
DEFAULT_PACKET_PASSWORD = bytearray([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])

# --- SHARED STATE (globális állapot) ---
shared_state = {
    # Watchdog PONG jelek
    "task_pong": {"inverter": time.time(), "ble": time.time(), "controller": time.time(), "simulation": time.time()},

    # Kapcsolatok állapota
    "inverter_connected": False,
    "charger_connected": False,
    "session_energy_accumulator": 0.0,
    "session_last_time": 0.0,
    "session_last_power": 0.0,
    
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
    "last_charge": {},            # Legutóbbi lezárt töltési rekord adatai
    
    # Vezérlési paraméterek (config.json-ból töltve)
    "start_soc": 100,
    "stop_soc": 0,
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
    "lockdown_active": False,
    "transition_timestamps": [],
    "consecutive_auto_commands": 0,
    
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

# Thread-safe lock az állapot módosítása számára
state_lock = threading.Lock()
logs_limit = 50

_last_initiated_session_id = ""
charger_password = bytearray([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
WEB_AUTH_ENABLED = True
WEB_PASSWORD = "admin"
PBKDF2_ITERATIONS = 100000

# --- KONFIGURÁCIÓ KEZELÉS ---
def load_config():
    global shared_state, CHARGER_NAME, CHARGER_MAC, charger_password
    global INVERTER_IP, INVERTER_PORT, LOGGER_SERIAL, HTTP_PORT, WEB_AUTH_ENABLED, WEB_PASSWORD, PBKDF2_ITERATIONS
    global _last_initiated_session_id
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
        shared_state["stop_soc"] = int(config.get("stop_soc", 0))
        shared_state["stop_import_limit"] = int(config["stop_import_limit"])
        shared_state["grid_charge_duration_minutes"] = int(config["grid_charge_duration_minutes"])
        shared_state["house_power_limit_w"] = int(config["house_power_limit_w"])
        shared_state["persist_mode_on_restart"] = bool(config["persist_mode_on_restart"])
        shared_state["charger_max_amps"] = int(config.get("charger_max_amps", 16))
        shared_state["force_submode"] = config.get("force_submode", "manual_stop")
        shared_state["schedule_solar_auto"] = bool(config.get("schedule_solar_auto", False))
        shared_state["web_auth_enabled"] = bool(config.get("web_auth_enabled", True))
        shared_state["started_by_controller"] = bool(config.get("started_by_controller", False))
        
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
    PBKDF2_ITERATIONS = int(config.get("pbkdf2_iterations", 100000))
    
    # Jelszó konverzió (Hex vagy plain text)
    pwd_str = config.get("charger_password", "FFFFFFFFFFFF")
    try:
        if len(pwd_str) == 12 and all(c in "0123456789abcdefABCDEF" for c in pwd_str):
            charger_password = bytearray(bytes.fromhex(pwd_str))
        else:
            raw_pwd = pwd_str.encode('utf-8')
            temp = bytearray(raw_pwd)
            while len(temp) < 6:
                temp.append(0xFF)
            charger_password = temp[:6]
    except Exception as e:
        charger_password = bytearray([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
        print(f"Hiba a jelszó feldolgozásakor: {e}. Alapértelmezett jelszó használata.")
        
    shared_state["last_charge"] = config.get("last_charge", {})
    _last_initiated_session_id = config.get("last_initiated_session_id", "")
    
    # Új hozzáadott állapotok betöltése a konfigurációból
    shared_state["session_energy_accumulator"] = float(config.get("session_energy_accumulator", 0.0))
    shared_state["session_last_time"] = float(config.get("session_last_time", 0.0))
    shared_state["session_last_power"] = float(config.get("session_last_power", 0.0))

def save_config_file():
    global _last_initiated_session_id
    with state_lock:
        try:
            decoded = charger_password.decode('utf-8', errors='strict')
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
            "stop_soc": shared_state["stop_soc"],
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
            "web_password": WEB_PASSWORD,
            "pbkdf2_iterations": PBKDF2_ITERATIONS,
            "started_by_controller": shared_state.get("started_by_controller", False),
            "last_charge": shared_state.get("last_charge", {}),
            "last_initiated_session_id": _last_initiated_session_id,
            "session_energy_accumulator": shared_state.get("session_energy_accumulator", 0.0),
            "session_last_time": shared_state.get("session_last_time", 0.0),
            "session_last_power": shared_state.get("session_last_power", 0.0)
        }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log_message(f"Hiba a konfiguráció mentésekor: {e}")

# --- RENDSZER NAPLÓZÁS ---
def log_message(msg):
    """Egyszerű üzenet naplózása az eredeti mintára."""
    timestamp = time.strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    with state_lock:
        shared_state["logs"].append(full_msg)
        if len(shared_state["logs"]) > logs_limit:
            shared_state["logs"].pop(0)