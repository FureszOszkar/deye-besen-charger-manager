import asyncio
import time
from bleak import BleakClient, BleakScanner
from pysolarmanv5 import PySolarmanV5

import config as config
from config import (
    shared_state, state_lock, log_message,
    INVERTER_IP, INVERTER_PORT, LOGGER_SERIAL,
    CHARGER_NAME, CHARGER_MAC,
    CHAR_FFE4_NOTIFY, CHAR_FFE9_WRITE, CHAR_FFC1_WRITE,
    CHAR_FFC2_NOTIFY, CHAR_FFD3_NOTIFY, CHAR_FD02_NOTIFY,
    CHAR_FFF3_WRITE, DEFAULT_PACKET_PASSWORD, DEFAULT_CONFIG,
    save_config_file
)
from safety_handler import (
    check_charger_error, check_charging_blocked,
    set_cooldown, is_in_cooldown, clear_cooldown,
    check_meter_readout_valid, mark_meter_readout_updated
)

# --- GLOBÁLIS BLE OBJEKTUMOK ÉS ÁLLAPOT ---
ble_command_queue = None
ble_rx_buffer = bytearray()
ble_state = "INIT"  # "INIT", "SENT_IDENTITY_ACK", "IDENTITY_ACKED", "SENT_LOGIN", "LOGGED_IN"
login_acknowledged = False
last_identity_ack_time = 0.0
last_login_time = 0.0
active_ble_client = None
ble_auth_event = None
ble_auth_status = None
main_loop = None

charger_serial = bytearray([0x30, 0x99, 0x83, 0x18, 0x21, 0x29, 0x44, 0x19])
# charger_password: a config modul tárolja, config.charger_password-ként olvassuk

DAYS_MAP = ["Hétfő", "Kedd", "Szerda", "Csütörtök", "Péntek", "Szombat", "Vasárnap"]

# Szükséges globális változók az eredeti kódból
_recently_cleared_sessions = {}
# _last_initiated_session_id: a config modul tárolja, config._last_initiated_session_id-ként olvassuk
last_telemetry_time = None
session_energy_accumulator = 0.0
_last_save_time = 0.0
last_sent_action = "STOP"
actual_action = "STOP"
expected_action = "STOP"


# --- UTILITY FÜGGVÉNYEK ---

def clear_ble_command_queue():
    """Kiüríti a BLE parancssorát."""
    global ble_command_queue
    if ble_command_queue is not None:
        while not ble_command_queue.empty():
            try:
                ble_command_queue.get_nowait()
                ble_command_queue.task_done()
            except asyncio.QueueEmpty:
                break


def to_signed_16(val):
    """16 bites előjeles számmá alakít."""
    return val if val < 32768 else val - 65536


# --- BLE PARANCS GENERÁLÁS ---

def get_shanghai_timestamp():
    """Shanghai időzóna szerinti timestamp."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now()
    shanghai_time = now.replace(tzinfo=timezone(timedelta(hours=8)))
    local_time = shanghai_time.astimezone(now.astimezone().tzinfo)
    return int(local_time.timestamp())


def create_ble_packet(cmd_type, payload=b""):
    """BLE csomagot generál parancs típus és payload alapján."""
    global charger_serial

    frame = bytearray()
    frame.extend([0x06, 0x01])
    frame.extend([0x00, 0x00])
    frame.append(0x00)
    frame.extend(charger_serial)
    frame.extend(config.charger_password)
    frame.extend([(cmd_type >> 8) & 0xFF, cmd_type & 0xFF])
    frame.extend(payload)
    frame.extend([0x00, 0x00])
    frame.extend([0x0F, 0x02])

    total_len = len(frame)
    frame[2] = (total_len >> 8) & 0xFF
    frame[3] = total_len & 0xFF

    checksum = 0
    for i in range(total_len - 4):
        checksum += frame[i]

    frame[total_len - 4] = (checksum >> 8) & 0xFF
    frame[total_len - 3] = checksum & 0xFF

    return bytes(frame)


def copy_packet_with_new_cmd(packet, new_cmd_id):
    """Csomag másolása új parancs ID-val."""
    frame = bytearray(packet)

    if len(frame) >= 19:
        frame[13:19] = config.charger_password

    frame[19] = (new_cmd_id >> 8) & 0xFF
    frame[20] = new_cmd_id & 0xFF

    total_len = len(frame)
    checksum = 0
    for i in range(total_len - 4):
        checksum += frame[i]

    frame[total_len - 4] = (checksum >> 8) & 0xFF
    frame[total_len - 3] = checksum & 0xFF
    return bytes(frame)


# --- INVERTER POLLING ---

# Persistent inverter connection
_persistent_inverter = None


def fetch_inverter_data_blocking():
    """Inverter telemetria adatainak szinkron lekérdezése."""
    global _persistent_inverter

    if _persistent_inverter is None:
        log_message(f"Deye Inverter kapcsolat felépítése: {INVERTER_IP}:{INVERTER_PORT} (S/N: {LOGGER_SERIAL})...")
        try:
            _persistent_inverter = PySolarmanV5(INVERTER_IP, LOGGER_SERIAL, port=INVERTER_PORT, auto_reconnect=True)
        except Exception as e:
            log_message(f"Deye Inverter kapcsolat hiba: {e}")
            raise

    try:
        pv_regs = _persistent_inverter.read_holding_registers(register_addr=672, quantity=2)
        pv_power = sum(pv_regs)  # PV1 and PV2 power, sum them to get pv_power
        grid_power_internal = to_signed_16(
            _persistent_inverter.read_holding_registers(register_addr=607, quantity=1)[0])
        grid_power_external = to_signed_16(
            _persistent_inverter.read_holding_registers(register_addr=619, quantity=1)[0])
        battery_soc = _persistent_inverter.read_holding_registers(register_addr=588, quantity=1)[0]
        ups_load_power = _persistent_inverter.read_holding_registers(register_addr=643, quantity=1)[0]
        battery_power = to_signed_16(_persistent_inverter.read_holding_registers(register_addr=590, quantity=1)[0])

        # Autótöltő fogyasztásának kiszámítása (Külső Grid CT - Inverter saját Grid portja)
        charger_power = max(0, grid_power_external - grid_power_internal)

        return {
            "grid_power": grid_power_external,
            "battery_soc": battery_soc,
            "ups_load_power": ups_load_power,
            "pv_power": pv_power,
            "battery_power": battery_power,
            "charger_power": charger_power
        }
    except Exception as e:
        try:
            if _persistent_inverter is not None:
                _persistent_inverter.disconnect()
        except Exception:
            pass
        _persistent_inverter = None
        raise


async def run_inverter_polling():
    """Aszinkron inverter adatlekérdezés task."""
    global shared_state

    while True:
        if shared_state["simulation"]:
            with state_lock:
                shared_state["task_pong"]["inverter"] = time.time()
            await asyncio.sleep(10)
            continue

        try:
            # Háttérszálon hívjuk meg a szinkron lekérdezést, így nem blokkolja az asyncio event loopot
            data = await asyncio.to_thread(fetch_inverter_data_blocking)

            with state_lock:
                shared_state["grid_power"] = data["grid_power"]
                shared_state["battery_soc"] = data["battery_soc"]
                shared_state["ups_load_power"] = data["ups_load_power"]
                shared_state["pv_power"] = data["pv_power"]
                shared_state["battery_power"] = data["battery_power"]
                shared_state["charger_power"] = data["charger_power"]
                shared_state["inverter_connected"] = True

            log_message(
                f"Deye Inverter: Grid={data['grid_power']}W, UPS={data['ups_load_power']}W, Nem_UPS={data['charger_power']}W, PV={data['pv_power']}W, Akku={data['battery_power']}W (SoC={data['battery_soc']}%)")

        except Exception as e:
            with state_lock:
                shared_state["inverter_connected"] = False
            log_message(f"Deye Logger lekérdezési hiba ({INVERTER_IP}): {e}. Újrapróbálkozás 10 másodperc múlva...")

        with state_lock:
            shared_state["task_pong"]["inverter"] = time.time()
        await asyncio.sleep(10)


# --- BLE KLIENS KEZELÉS ---

# Aszinkron BLE csatlakozó feladat
async def run_ble_client():
    global shared_state, ble_command_queue

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
            with state_lock:
                shared_state["task_pong"]["ble"] = time.time()
        return

    while True:
        try:
            log_message(f"Keresés indítása a következőhöz: {CHARGER_NAME} ({CHARGER_MAC})")
            device = await BleakScanner.find_device_by_filter(
                lambda d, ad: (d.name and CHARGER_NAME in d.name) or d.address.upper() == CHARGER_MAC.upper(),
                timeout=10.0
            )

            if device is None:
                log_message(
                    "Aktív kereséssel nem találom a töltőt a közelben. Megpróbálok közvetlenül MAC címre kapcsolódni...")
                target = CHARGER_MAC
            else:
                log_message(f"Töltő megtalálva: {device.name} ({device.address})")
                target = device

            log_message(f"Kapcsolódás a következőhöz: {target}...")
            client = BleakClient(target)
            try:
                # Biztonságos csatlakozás aszinkron időkorláttal a WinRT/Bluetooth lefagyások elkerülésére
                await asyncio.wait_for(client.connect(), timeout=20.0)
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
                    await safe_ble_start_notify(client, CHAR_FFC2_NOTIFY, ble_notification_received)
                    log_message("-> [NOTIFY] Feliratkozás az FFC2 (jelszó státusz) csatornára aktív.")
                except Exception as e:
                    log_message(f"-> [NOTIFY ERROR] FFC2 feliratkozási hiba: {e}")

                # 2. Elküldjük a jelszót az FFC1-re a soros port feloldásához
                global ble_auth_event, ble_auth_status
                ble_auth_event = asyncio.Event()
                ble_auth_status = None

                # Jelszó előkészítése: a BLE chip 12 bájtos formátumot vár (pl. jelszó + jelszó)
                auth_pwd = bytes(config.charger_password)
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
                    await safe_ble_write(client, CHAR_FFC1_WRITE, auth_pwd, response=False)
                except Exception as e:
                    log_message(f"-> [BLE AUTH ERROR] Jelszó küldési hiba: {e}")

                # Várakozás a visszaigazolásra az FFC2-ről (maximum 2.0 másodpercig)
                try:
                    await asyncio.wait_for(ble_auth_event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    log_message("-> [BLE AUTH TIMEOUT] Nem érkezett válasz az FFC2 csatornáról 2.0 másodpercen belül.")

                # Ha a jelszó hibás volt, megpróbáljuk a gyári alapértelmezett "000000000000" jelszót
                if ble_auth_status != 0:
                    log_message(
                        "-> [BLE AUTH RETRY] Megadott jelszó sikertelen. Megpróbáljuk a gyári alapértelmezett '000000000000' jelszót...")
                    ble_auth_event.clear()
                    ble_auth_status = None
                    default_pwd = b"000000000000"
                    try:
                        log_message(
                            f"-> [BLE AUTH] Gyári jelszó küldése az FFC1 csatornára: {default_pwd.hex().upper()}")
                        await safe_ble_write(client, CHAR_FFC1_WRITE, default_pwd, response=False)
                        await asyncio.wait_for(ble_auth_event.wait(), timeout=2.0)
                        if ble_auth_status == 0:
                            log_message(
                                "-> [SZINKRON] A gyári jelszó sikeres volt a BLE chip feloldásához. A csomagok fejlécében a konfigurált jelszót használjuk.")
                    except Exception as e:
                        log_message(f"-> [BLE AUTH ERROR] Gyári jelszó küldési hiba: {e}")

                # 3. Feliratkozás az UART olvasó csatornára (FFE4)
                await safe_ble_start_notify(client, CHAR_FFE4_NOTIFY, ble_notification_received)
                log_message("-> [NOTIFY] Feliratkozás az FFE4 (READ) csatornára aktív.")

                try:
                    await safe_ble_start_notify(client, CHAR_FFF3_WRITE, ble_notification_received)
                    log_message("-> [NOTIFY] Feliratkozás az FFF3 csatornára aktív.")
                except Exception as e:
                    log_message(f"-> [NOTIFY ERROR] FFF3 feliratkozási hiba: {e}")

                try:
                    await safe_ble_start_notify(client, CHAR_FFD3_NOTIFY, ble_notification_received)
                    log_message("-> [NOTIFY] Feliratkozás az FFD3 csatornára aktív.")
                except Exception as e:
                    log_message(f"-> [NOTIFY ERROR] FFD3 feliratkozási hiba: {e}")

                try:
                    await safe_ble_start_notify(client, CHAR_FD02_NOTIFY, ble_notification_received)
                    log_message("-> [NOTIFY] Feliratkozás az FD02 csatornára aktív.")
                except Exception as e:
                    log_message(f"-> [NOTIFY ERROR] FD02 feliratkozási hiba: {e}")

                # Watchdog inicializálása a friss kapcsolathoz
                global last_rx_time
                last_rx_time = time.time()

                # Csatlakozás után ürítjük az offline időszak alatt felhalmozódott parancsokat
                clear_ble_command_queue()
                log_message("-> [BLE QUEUE] Offline időszak alatt felhalmozódott parancsok törölve.")

                # Kapcsolat alatti parancsküldő hurok
                while client.is_connected:
                    # Watchdog ellenőrzés: ha bejelentkezett állapotban vagyunk és 15 másodperce nem kaptunk adatot, megszakítjuk a kapcsolatot
                    if ble_state == "LOGGED_IN" and time.time() - last_rx_time > 15.0:
                        log_message(
                            "-> [WATCHDOG TIMEOUT] Nincs beérkező telemetria 15 másodperce. Kapcsolat kényszerített lezárása...")
                        try:
                            await client.disconnect()
                        except Exception:
                            pass
                        raise Exception("Telemetria timeout (15s)")
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
                        await safe_ble_write(client, CHAR_FFE9_WRITE, packet, response=True)
                        ble_command_queue.task_done()
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(1)
                    
                    with state_lock:
                        shared_state["task_pong"]["ble"] = time.time()
            except Exception as e:
                raise e
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        except Exception as e:
            with state_lock:
                shared_state["charger_connected"] = False
                shared_state["charging_active"] = False
                shared_state["currents"] = [0.0, 0.0, 0.0]
            active_ble_client = None
            log_message(f"Bluetooth kapcsolat megszakadt: {e}. Újracsatlakozás 5 másodperc múlva...")

            # Hiba/szakadás esetén azonnal ürítjük a parancssort
            clear_ble_command_queue()

            with state_lock:
                shared_state["task_pong"]["ble"] = time.time()
            await asyncio.sleep(5)
# Aszinkron Inverter adatlekérdező task
# Globális telemetria időpont és BLE biztonsági wrapperek
last_rx_time = 0.0

# BLE kezelés logika ide kerül
async def safe_ble_write(client, char, data, response=True, timeout=5.0):
    try:
        await asyncio.wait_for(client.write_gatt_char(char, data, response=response), timeout=timeout)
        return True
    except Exception as e:
        log_message(f"-> [BLE WRITE TIMEOUT/ERROR] Csatorna: {char}, Hiba: {e}. Kapcsolat kényszerített lezárása...")
        try:
            await client.disconnect()
        except Exception:
            pass
        raise e


async def safe_ble_start_notify(client, char, callback, timeout=5.0):
    try:
        await asyncio.wait_for(client.start_notify(char, callback), timeout=timeout)
        return True
    except Exception as e:
        log_message(f"-> [BLE NOTIFY TIMEOUT/ERROR] Csatorna: {char}, Hiba: {e}. Kapcsolat kényszerített lezárása...")
        try:
            await client.disconnect()
        except Exception:
            pass
        raise e


_persistent_inverter = None


# --- TÖLTÉSI VEZÉRLŐ ---

# Aszinkron Fő Döntési és Vezérlő Task
async def run_charge_controller():
    global shared_state, ble_command_queue

    # Vezérlési segédváltozók

    async def try_send_ble_action(packet, action, is_manual_hard_stop=False, is_safety_stop=False, is_manual_start=False):
        global shared_state
        with state_lock:
            if shared_state.get("lockdown_active") and not (is_manual_hard_stop or is_safety_stop):
                log_message("[VEZÉRLÉS] Parancs blokkolva: A vezérlő biztonsági zárolás (lockdown) alatt áll.")
                return False

            import time
            now = time.time()
            ts = shared_state.get("transition_timestamps", [])
            ts = [t for t in ts if now - t < 40]
            shared_state["transition_timestamps"] = ts
            
            recent_20s = [t for t in ts if now - t < 20]
            if len(recent_20s) >= 2 and not (is_manual_hard_stop or is_safety_stop):
                shared_state["cooldown_until"] = now + 20
                log_message("[VEZÉRLÉS] Parancs blokkolva: Túl gyakori kapcsolás (cooldown).")
                return False
                
            if len(ts) >= 4 and not (is_manual_hard_stop or is_safety_stop):
                shared_state["lockdown_active"] = True
                log_message("[VEZÉRLÉS] Parancs blokkolva: 40 mp-en belüli 5. kapcsolás, RENDSZER ZÁROLVA.")
                return False
                
            if not (is_manual_hard_stop or is_manual_start):
                c = shared_state.get("consecutive_auto_commands", 0)
                c += 1
                shared_state["consecutive_auto_commands"] = c
                if c >= 10:
                    shared_state["lockdown_active"] = True
                    log_message("[VEZÉRLÉS] RENDSZER ZÁROLVA: Túl sok egymás utáni automata parancs (10).")
                    if action == "START":
                        return False
            else:
                shared_state["consecutive_auto_commands"] = 0
                
            ts.append(now)

        await ble_command_queue.put(packet)
        return True

    last_sent_action = None  # "START" vagy "STOP"
    start_command_time = None  # Időpont, mikor a Start parancsot kiküldtük (timeout figyeléshez)
    consecutive_failures = 0  # Egymást követő sikertelen indítási kísérletek száma
    import_exceeded_since = None  # Időpont, mikor a fogyasztás először túllépte a limitet

    def time_to_minutes(t_str):
        try:
            h, m = map(int, t_str.split(':'))
            return h * 60 + m
        except Exception:
            return 0

    while True:
        with state_lock:
            shared_state["task_pong"]["controller"] = time.time()
        await asyncio.sleep(5)  # 5 másodpercenként értékeljük ki a helyzetet

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
            charger_power = shared_state["charger_power"]
            battery_soc = shared_state["battery_soc"]
            charging_active = shared_state["charging_active"]
            pull_plug = shared_state["pull_plug"]
            cooldown_until = shared_state["cooldown_until"]

            start_soc = shared_state["start_soc"]
            stop_soc = shared_state["stop_soc"]
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

        # line_id meghatározása áthelyezve a ciklus elejére

        # --- ALKALMAZÁSI ÉS ÚJRAINDÍTÁSI FLAGEK FELDOLGOZÁSA (Előrehozva a korai returnök elé) ---
        # 1. Kézi leállítás (Soft Stop)
        if apply_with_stop:
            log_message("[VEZÉRLÉS] Felhasználói árammódosítás leállítással kérték. Töltés LEÁLLÍTÁSA...")
            stop_payload = bytearray(47)
            stop_payload[0] = line_id
            stop_payload[1:17] = b"BDmanager".ljust(16, b"\x00")
            packet = create_ble_packet(0x8008, bytes(stop_payload))
            if not await try_send_ble_action(packet, "STOP", is_manual_hard_stop=True):
                continue
            last_sent_action = "STOP"
            start_command_time = None
            with state_lock:
                shared_state["active_current_limit"] = 0
                shared_state["apply_with_stop"] = False
                shared_state["started_by_controller"] = False
            save_config_file()
            continue

        # 2. Kézi újraindítás (Alkalmaz gomb Force módban)
        if apply_with_restart:
            log_message(
                "[VEZÉRLÉS] Felhasználói árammódosítás újraindítással kérték. Töltés leállítása, újraindítás 15 mp múlva...")
            stop_payload = bytearray(47)
            stop_payload[0] = line_id
            stop_payload[1:17] = b"BDmanager".ljust(16, b"\x00")
            packet = create_ble_packet(0x8008, bytes(stop_payload))
            if not await try_send_ble_action(packet, "STOP", is_manual_hard_stop=True):
                continue
            last_sent_action = "STOP"
            start_command_time = None
            cooldown_time = current_time + 15.0
            with state_lock:
                shared_state["active_current_limit"] = 0
                shared_state["cooldown_until"] = cooldown_time
                shared_state["apply_with_restart"] = False
                shared_state["restart_pending_start"] = True
                shared_state["started_by_controller"] = False
            save_config_file()
            continue

        # Ha a töltés nem aktív és nem várunk megerősítésre, engedélyezzük az új parancsokat
        if not charging_active and start_command_time is None:
            last_sent_action = None
            if shared_state.get("started_by_controller", False):
                with state_lock:
                    shared_state["started_by_controller"] = False
                save_config_file()

            # Kézi indítás felülbírálás lecsengése
            if force_submode == "manual_start" and not manual_start_requested:
                log_message(
                    "[VEZÉRLÉS] A kézi indítású töltés befejeződött vagy megszakadt. Felülbírálás visszavonva, visszatérés automatikus módokhoz.")
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
            if shared_state.get("started_by_controller", False):
                with state_lock:
                    shared_state["started_by_controller"] = False
                save_config_file()
            continue

        # Ha a töltő kábele ki van húzva, nem hajtunk végre parancsokat
        if pull_plug:
            last_sent_action = None
            start_command_time = None
            import_exceeded_since = None
            continue

        # Idő és nap meghatározása (szimulációban felülbírálható)
        local_time = time.localtime()
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
            else:  # Éjszakai átnyúlás a jelenlegi napon
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

            if start_m > stop_m:  # Átnyúló volt
                if current_minutes < stop_m:
                    in_interval = True
                    target_amps = amps
                    override_auto = day_override

        # Ellenőrizzük a kapcsolatokat (csak ha nem szimulációról van szó)
        if not sim_mode and (not inverter_ok or not charger_ok):
            # Kapcsolat nélkül nem tudunk biztonságosan parancsot végrehajtani
            continue

        # Szimulált vagy valós külső indítás kezelése
        is_external_session = sim_external_session if sim_mode else (
            not shared_state.get("started_by_controller", False))

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
                    cooldown_time = current_time + 120.0  # 2 perc lehűlés

                    with state_lock:
                        shared_state["cooldown_until"] = cooldown_time

                    log_message(
                        f"[HIBA] Töltésindítási kísérlet sikertelen (Timeout). Próbálkozás: {consecutive_failures}/3")

                    if consecutive_failures >= 3:
                        # Rendszer leállítása Csak figyelés módba (kikapcsoljuk az automatizmusokat)
                        with state_lock:
                            shared_state["auto_enabled"] = False
                            shared_state["schedule_enabled"] = False
                            shared_state["force_submode"] = "schedule"
                            shared_state["control_mode"] = "monitoring"
                            shared_state[
                                "error_message"] = "Töltésindítás 3 kísérlet után sem sikerült. Rendszer leállítva."
                            shared_state["persist_mode_on_restart"] = False
                        save_config_file()
                        log_message(
                            "[BIZTONSÁG] Automatikus mód leállítva a sorozatos sikertelen kísérletek miatt. Kézi ellenőrzés szükséges.")
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
                    else:  # Solar auto az időablakon belül
                        if not charging_active:
                            expected_action = "START" if battery_soc >= start_soc else "KEEP"
                        else:
                            expected_stop = False
                            if (house_power_limit_w > 0 and (ups_load_power + charger_power) > house_power_limit_w):
                                expected_stop = True
                            elif (stop_soc > 0 and battery_soc < stop_soc):
                                expected_stop = True

                            if expected_stop:
                                expected_action = "STOP"
                            elif is_external_session:
                                expected_action = "KEEP"
                            else:
                                if stop_import_limit > 0 and grid_power > stop_import_limit:
                                    if grid_charge_duration_minutes == 0:
                                        expected_action = "STOP"
                                    elif import_exceeded_since is not None and current_time - import_exceeded_since >= grid_charge_duration_minutes * 60:
                                        expected_action = "STOP"
                                    else:
                                        expected_action = "KEEP"
                                else:
                                    expected_action = "KEEP"
                else:  # Időablakon kívül
                    if schedule_solar_auto:
                        if not charging_active:
                            expected_action = "START" if battery_soc >= start_soc else "KEEP"
                        else:
                            expected_stop = False
                            if (house_power_limit_w > 0 and (ups_load_power + charger_power) > house_power_limit_w):
                                expected_stop = True
                            elif (stop_soc > 0 and battery_soc < stop_soc):
                                expected_stop = True

                            if expected_stop:
                                expected_action = "STOP"
                            elif is_external_session:
                                expected_action = "KEEP"
                            else:
                                if stop_import_limit > 0 and grid_power > stop_import_limit:
                                    if grid_charge_duration_minutes == 0:
                                        expected_action = "STOP"
                                    elif import_exceeded_since is not None and current_time - import_exceeded_since >= grid_charge_duration_minutes * 60:
                                        expected_action = "STOP"
                                    else:
                                        expected_action = "KEEP"
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
                    expected_stop = False
                    if (house_power_limit_w > 0 and (ups_load_power + charger_power) > house_power_limit_w):
                        expected_stop = True
                    elif (stop_soc > 0 and battery_soc < stop_soc):
                        expected_stop = True

                    if expected_stop:
                        expected_action = "STOP"
                    elif is_external_session:
                        expected_action = "KEEP"
                    else:
                        if stop_import_limit > 0 and grid_power > stop_import_limit:
                            if grid_charge_duration_minutes == 0:
                                expected_action = "STOP"
                            elif import_exceeded_since is not None and current_time - import_exceeded_since >= grid_charge_duration_minutes * 60:
                                expected_action = "STOP"
                            else:
                                expected_action = "KEEP"
                        else:
                            expected_action = "KEEP"

        actual_action = "KEEP"

        # --- ÜZEMMÓD-ALAPÚ DÖNTÉSEK ---

        # Határozzuk meg a fázisszámot (line_id: 1 = 1-fázis, 2 = 3-fázis) a mért feszültségek alapján
        with state_lock:
            v2 = shared_state["voltages"][1]
            v3 = shared_state["voltages"][2]
        line_id = 2 if (v2 > 50.0 or v3 > 50.0) else 1

        # Határozzuk meg, hogy a Solar Auto szabályokat kell-e alkalmaznunk
        use_solar_auto_rules = False
        if mode == "auto":
            use_solar_auto_rules = True
        elif mode == "schedule":
            if in_interval:
                if not override_auto:
                    use_solar_auto_rules = True
            else:
                if schedule_solar_auto:
                    use_solar_auto_rules = True

        # 1. Kényszerített (Force Charge) Mód
        if mode == "force":
            if manual_start_requested:
                start_amps = 16 if charger_max_amps == 0 else charger_max_amps
                log_message(f"[VEZÉRLÉS] Kényszerített kézi töltés indítása ({start_amps}A)...")
                start_payload = bytearray(47)
                start_payload[0] = line_id
                start_payload[1:17] = b"BDmanager".ljust(16, b"\x00")
                charge_id = time.strftime("%Y%m%d%H%M") + "1337"
                config._last_initiated_session_id = charge_id
                start_payload[17:33] = charge_id.encode('ascii')
                start_payload[33] = 0x00
                ts = get_shanghai_timestamp()
                start_payload[34:38] = ts.to_bytes(4, 'big')
                start_payload[38] = 0x01
                start_payload[39] = 0x01
                start_payload[40:46] = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
                start_payload[46] = start_amps

                packet = create_ble_packet(0x8007, bytes(start_payload))
                if not await try_send_ble_action(packet, "START", is_manual_start=True):
                    continue

                last_sent_action = "START"
                start_command_time = current_time
                actual_action = "START"
                with state_lock:
                    shared_state["manual_start_requested"] = False
                    shared_state["force_submode"] = "manual_start"
                    shared_state["active_current_limit"] = charger_max_amps
                    shared_state["started_by_controller"] = True
                save_config_file()

            elif force_submode == "manual_stop":
                if charging_active or last_sent_action == "START":
                    log_message("[VEZÉRLÉS] Kényszerített kézi töltés leállítása...")
                    stop_payload = bytearray(47)
                    stop_payload[0] = line_id
                    stop_payload[1:17] = b"BDmanager".ljust(16, b"\x00")
                    packet = create_ble_packet(0x8008, bytes(stop_payload))
                    if not await try_send_ble_action(packet, "STOP", is_manual_hard_stop=True):
                        continue

                    last_sent_action = "STOP"
                    actual_action = "STOP"
                    with state_lock:
                        shared_state["active_current_limit"] = 0
                        shared_state["started_by_controller"] = False
                    save_config_file()
                else:
                    # Már nem aktív a töltés, törölhetjük a manual_stop állapotot
                    # Mivel a /api/force_submode már kikapcsolta az automatikus módokat, a rendszer "monitoring"-ra vált
                    if shared_state.get("started_by_controller", False):
                        with state_lock:
                            shared_state["started_by_controller"] = False
                    log_message("[VEZÉRLÉS] A kézi leállítás befejeződött. Felülbírálás törölve, rendszer átvált figyelő (monitoring) módba.")
                    with state_lock:
                        shared_state["force_submode"] = "schedule"
                    save_config_file()

        # 2. Ütemezett időablak fix áramkorláttal (Prioritás BE)
        elif mode == "schedule" and in_interval and override_auto:
            if not charging_active and last_sent_action != "START":
                start_amps = 16 if target_amps == 0 else target_amps
                log_message(f"[VEZÉRLÉS] Ütemezési időablak aktív (Prioritás BE). Töltés indítása ({start_amps}A)...")
                start_payload = bytearray(47)
                start_payload[0] = line_id
                start_payload[1:17] = b"BDmanager".ljust(16, b"\x00")
                charge_id = time.strftime("%Y%m%d%H%M") + "1337"
                config._last_initiated_session_id = charge_id
                start_payload[17:33] = charge_id.encode('ascii')
                start_payload[33] = 0x00
                ts = get_shanghai_timestamp()
                start_payload[34:38] = ts.to_bytes(4, 'big')
                start_payload[38] = 0x01
                start_payload[39] = 0x01
                start_payload[40:46] = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
                start_payload[46] = start_amps

                packet = create_ble_packet(0x8007, bytes(start_payload))
                if not await try_send_ble_action(packet, "START", is_manual_start=True):
                    continue

                last_sent_action = "START"
                start_command_time = current_time
                actual_action = "START"
                with state_lock:
                    shared_state["active_current_limit"] = target_amps
                    shared_state["started_by_controller"] = True
                save_config_file()

            elif charging_active and not is_external_session:
                if target_amps > 0:
                    if active_current_limit == 0:
                        log_message(f"[VEZÉRLÉS] Ütemezett töltés baseline rögzítve: {target_amps}A (leállítás nélkül)")
                        with state_lock:
                            shared_state["active_current_limit"] = target_amps
                    elif active_current_limit != target_amps:
                        log_message(
                            f"[VEZÉRLÉS] Ütemezett áramerősség változás ({active_current_limit}A -> {target_amps}A). Újraindítás...")
                        stop_payload = bytearray(47)
                        stop_payload[0] = line_id
                        stop_payload[1:17] = b"BDmanager".ljust(16, b"\x00")
                        packet = create_ble_packet(0x8008, bytes(stop_payload))
                        await ble_command_queue.put(packet)

                        last_sent_action = "STOP"
                        start_command_time = None
                        cooldown_time = current_time + 15.0
                        actual_action = "RESTART"
                        with state_lock:
                            shared_state["active_current_limit"] = 0
                            shared_state["cooldown_until"] = cooldown_time
                            shared_state["started_by_controller"] = False
                        save_config_file()

        # 3. Összevont Solar Auto szabályok
        elif use_solar_auto_rules:
            # --- INDÍTÁSI FELTÉTEL ---
            if not charging_active and last_sent_action != "START":
                if battery_soc >= start_soc:
                    start_amps = 16 if charger_max_amps == 0 else charger_max_amps
                    log_message(
                        f"[VEZÉRLÉS] Solar Auto feltételek teljesültek (Akku SoC: {battery_soc}% >= {start_soc}%). Töltés INDÍTÁSA ({start_amps}A)...")

                    start_payload = bytearray(47)
                    start_payload[0] = line_id
                    start_payload[1:17] = b"BDmanager".ljust(16, b"\x00")
                    charge_id = time.strftime("%Y%m%d%H%M") + "1337"
                    config._last_initiated_session_id = charge_id
                    start_payload[17:33] = charge_id.encode('ascii')
                    start_payload[33] = 0x00
                    ts = get_shanghai_timestamp()
                    start_payload[34:38] = ts.to_bytes(4, 'big')
                    start_payload[38] = 0x01
                    start_payload[39] = 0x01
                    start_payload[40:46] = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
                    start_payload[46] = start_amps

                    packet = create_ble_packet(0x8007, bytes(start_payload))
                    if not await try_send_ble_action(packet, "START"):
                        continue

                    last_sent_action = "START"
                    start_command_time = current_time
                    import_exceeded_since = None
                    actual_action = "START"
                    with state_lock:
                        shared_state["active_current_limit"] = charger_max_amps
                        shared_state["started_by_controller"] = True
                    save_config_file()

            # --- LEÁLLÍTÁSI FELTÉTELEK ---
            elif charging_active:
                should_stop = False
                reason = ""

                # BIZTONSÁGI LEÁLLÍTÁSOK (minden aktív töltésre kikényszerítjük, akár külső indítású is!)
                # 1. szabály: Ház túlterhelés-védelem (Deye UPS port terhelés alapján)
                if house_power_limit_w > 0 and (ups_load_power + charger_power) > house_power_limit_w:
                    should_stop = True
                    reason = f"Ház UPS terhelése ({ups_load_power}W) + Töltő ({charger_power}W) meghaladta a korlátot ({house_power_limit_w} W)"

                # 2. szabály: Opcionális Akkumulátor leállítási szint korlát (stop_soc)
                if not should_stop and stop_soc > 0 and battery_soc < stop_soc:
                    should_stop = True
                    reason = f"Akku töltöttsége ({battery_soc}%) a leállítási küszöb ({stop_soc}%) alá esett"

                # NORMÁL NAPELEM/HÁLÓZAT SZABÁLYOK (csak ha nem külső indítású a töltés)
                if not should_stop and not is_external_session:
                    # 3. szabály: Hálózati töltés időtartama leállítás előtt
                    if stop_import_limit > 0:
                        if grid_power > stop_import_limit:
                            if grid_charge_duration_minutes == 0:
                                should_stop = True
                                reason = f"Hálózati import terhelés ({grid_power} W) meghaladta a limitet ({stop_import_limit} W) azonnali leállítással"
                            elif import_exceeded_since is None:
                                import_exceeded_since = current_time
                                log_message(
                                    f"[VEZÉRLÉS] Hálózati terhelés ({grid_power} W) meghaladta a limitet ({stop_import_limit} W). Hálózati töltés időzítő elindítva ({grid_charge_duration_minutes} perc)...")
                            elif current_time - import_exceeded_since >= grid_charge_duration_minutes * 60:
                                should_stop = True
                                reason = f"Hálózati töltési időkorlát ({grid_charge_duration_minutes} perc) letelt"
                        else:
                            if import_exceeded_since is not None:
                                import_exceeded_since = None
                                log_message("[VEZÉRLÉS] Hálózati terhelés visszaesett a limit alá. Időzítő törölve.")

                if should_stop:
                    log_message(f"[VEZÉRLÉS] Solar Auto leállítási ok teljesült: {reason}. Töltés LEÁLLÍTÁSA...")

                    stop_payload = bytearray(47)
                    stop_payload[0] = line_id
                    stop_payload[1:17] = b"BDmanager".ljust(16, b"\x00")
                    packet = create_ble_packet(0x8008, bytes(stop_payload))
                    if not await try_send_ble_action(packet, "STOP", is_safety_stop=True):
                        continue

                    last_sent_action = "STOP"
                    actual_action = "STOP"
                    import_exceeded_since = None
                    with state_lock:
                        shared_state["active_current_limit"] = 0
                        shared_state["started_by_controller"] = False
                    save_config_file()

        # 4. Időablakon kívül, Solar Auto nélkül -> Töltés leállítása
        else:
            if (charging_active or last_sent_action == "START") and not is_external_session:
                log_message("[VEZÉRLÉS] Ütemezési időablakon kívül vagyunk (Solar Auto KI). Töltés LEÁLLÍTÁSA...")
                stop_payload = bytearray(47)
                stop_payload[0] = line_id
                stop_payload[1:17] = b"BDmanager".ljust(16, b"\x00")
                packet = create_ble_packet(0x8008, bytes(stop_payload))
                if not await try_send_ble_action(packet, "STOP"):
                    continue

                last_sent_action = "STOP"
                actual_action = "STOP"
                with state_lock:
                    shared_state["active_current_limit"] = 0
                    shared_state["started_by_controller"] = False
                save_config_file()

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

    """
    Kész- VÁZLAT: Ezt az eredeti run_charge_controller() függvény teljes kódjával kell helyettesíteni.
    Feladata: A teljes töltési logika vezérlése.
    """
    log_message("Töltési vezérlő indítva")

    while True:
        await asyncio.sleep(2)
        # Töltési logika ide kerül


# --- BLE CSOMAG FELDOLGOZÁS ---

# Aszinkron csomagfeldolgozó és állapotgép
async def process_assembled_packet(packet, client):
    global charger_serial, ble_state, login_acknowledged
    global last_identity_ack_time, last_login_time, shared_state
    global last_telemetry_time, session_energy_accumulator, _last_save_time

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
                await safe_ble_write(client, CHAR_FFE9_WRITE, ack_packet, response=True)
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
                    await safe_ble_write(client, CHAR_FFE9_WRITE, ack_packet, response=True)
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
                await safe_ble_write(client, CHAR_FFE9_WRITE, login_packet, response=True)
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
                    await safe_ble_write(client, CHAR_FFE9_WRITE, login_packet, response=True)
                    last_login_time = current_time
                except Exception as e:
                    log_message(f"-> [BLE WRITE ERROR] Login újraküldési hiba: {e}")

        elif ble_state == "LOGGED_IN":
            log_message("-> [RECONNECT] Töltő beacon észlelve LOGGED_IN állapotban. Kapcsolat újraindítása...")
            ble_state = "SENT_IDENTITY_ACK"
            last_identity_ack_time = current_time
            ack_packet = create_ble_packet(0x8002, b"")
            try:
                await safe_ble_write(client, CHAR_FFE9_WRITE, ack_packet, response=True)
                log_message("-> [BLE WRITE SUCCESS] Identity Ack (0x8002) elküldve újrakapcsolódáshoz!")
            except Exception as e:
                log_message(f"-> [BLE WRITE ERROR] Újrakapcsolódási Ack küldési hiba: {e}")

    elif cmd_id == 0x0003:
        log_message("-> [HEARTBEAT] Ping (0x0003) érkezett, Heartbeat Pong (0x8003) küldése...")
        pong_packet = create_ble_packet(0x8003, b"\x01")
        try:
            await safe_ble_write(client, CHAR_FFE9_WRITE, pong_packet, response=True)
            log_message("-> [BLE WRITE SUCCESS] Heartbeat Pong (0x8003) sikeresen elküldve!")
        except Exception as e:
            log_message(f"-> [BLE WRITE ERROR] Heartbeat Pong küldési hiba: {e}")

    elif cmd_id == 0x0155:
        log_message(
            f"-> [BLE ACK] A BLE chip visszaigazolta a csomag átvételét (0x0155). Jelenlegi állapot: {ble_state}")
        if ble_state == "SENT_IDENTITY_ACK":
            log_message("-> [HANDSHAKE SUCCESS] A töltő elfogadta az Identity Ack-ot!")
            ble_state = "IDENTITY_ACKED"
            log_message("-> [LOGIN] Bejelentkezési parancs (0x8001) küldése...")
            ts = int(current_time)
            login_payload = bytearray([0x01])
            login_payload.extend([(ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF])
            login_packet = create_ble_packet(0x8001, bytes(login_payload))
            try:
                await safe_ble_write(client, CHAR_FFE9_WRITE, login_packet, response=True)
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
                await safe_ble_write(client, CHAR_FFE9_WRITE, login_packet, response=True)
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

    elif cmd_id == 0x000A:
        global _recently_cleared_sessions
        current_time = time.time()

        # Toroljuk a 60 masodpercnel regebbi bejegyzeseket
        _recently_cleared_sessions = {k: v for k, v in _recently_cleared_sessions.items() if current_time - v < 60}

        payload = packet[21:]
        if len(payload) >= 88:
            try:
                session_id = payload[33:49].replace(b'\x00', b'').decode('ascii', errors='replace').strip()
                stop_reason = payload[17:33].replace(b'\x00', b'').decode('ascii', errors='replace').strip()
                duration = int.from_bytes(payload[72:76], byteorder='big')
                energy = int.from_bytes(payload[84:88], byteorder='big') * 10
                user_id = payload[1:17].replace(b'\x00', b'').decode('ascii', errors='replace').strip()
            except Exception as e:
                log_message(f"-> [HIBA] 0x000A csomag feldolgozasa kozben hiba tortent: {e}")
                return

            if not session_id or not session_id.startswith('20'):
                return

            if session_id not in _recently_cleared_sessions or current_time - _recently_cleared_sessions[
                session_id] > 15:
                ack_payload = bytearray([0x01]) + payload[33:49] + bytearray([0x01])
                ack_packet = create_ble_packet(0x800A, bytes(ack_payload))
                await ble_command_queue.put(ack_packet)

                _recently_cleared_sessions[session_id] = current_time

                if session_id == config._last_initiated_session_id:
                    shared_state["last_charge"] = {
                        "session_id": session_id,
                        "user_id": user_id,
                        "stop_reason": stop_reason,
                        "duration": duration,
                        "energy": energy,
                        "timestamp": current_time
                    }
                    save_config_file()
                    log_message(
                        f"-> [STATUS] 0x800A csomag elkuldve a munkamenet lezarasara (session_id: {session_id}). Mentett adatok: {shared_state['last_charge']}")
                else:
                    log_message(
                        f"-> [STATUS] 0x800A csomag elkuldve a munkamenet lezarasara (session_id: {session_id}). (Nem saját szoftveres indítás, nem mentjük.)")

    elif cmd_id in (0x0004, 0x000D):
        if ble_state != "LOGGED_IN":
            ble_state = "LOGGED_IN"
            login_acknowledged = True
            log_message("-> [STATUS] Telemetria észlelve, állapot: LOGGED_IN")

        payload = packet[21:]
        if len(payload) >= 33:
            v1 = ((payload[1] << 8) | payload[2]) * 0.1
            i1 = ((payload[3] << 8) | payload[4]) * 0.01

            v2 = ((payload[25] << 8) | payload[26]) * 0.1
            i2 = ((payload[27] << 8) | payload[28]) * 0.01
            v3 = ((payload[29] << 8) | payload[30]) * 0.1
            i3 = ((payload[31] << 8) | payload[32]) * 0.01

            t_val = (payload[13] << 8) | payload[14]
            temp_int = (t_val - 20000) * 0.01 if t_val != 0xFFFF else -1.0

            output_state = payload[19]
            plug_state = payload[18]
            is_charging = (output_state == 0x01) or (i1 > 0.1 or i2 > 0.1 or i3 > 0.1)
            is_plug_disconnected = (plug_state == 0x01)

            current_power = v1 * i1 + v2 * i2 + v3 * i3

            if is_charging:
                current_time = time.time()
                if last_telemetry_time is None:
                    if shared_state["session_last_time"] > 0:
                        dt = current_time - shared_state["session_last_time"]
                        if 0 < dt < 10800:
                            P_avg = (shared_state["session_last_power"] + current_power) / 2.0
                            dE_gap = (P_avg * dt) / 3600000.0
                            session_energy_accumulator = shared_state["session_energy_accumulator"] + dE_gap
                            log_message(
                                f"-> [STATUS] Újraindítás utáni energiakorrekció: {dE_gap:.4f} kWh a kiesett {dt:.1f} másodpercre (Átlag teljesítmény: {P_avg:.1f} W).")
                        else:
                            session_energy_accumulator = shared_state["session_energy_accumulator"]
                    else:
                        session_energy_accumulator = shared_state["session_energy_accumulator"]
                    last_telemetry_time = current_time
                else:
                    dt = current_time - last_telemetry_time
                    if 0 < dt < 30:
                        dE = (current_power * dt) / 3600000.0
                        session_energy_accumulator += dE
                        last_telemetry_time = current_time

                with state_lock:
                    shared_state["charger_connected"] = True
                    shared_state["pull_plug"] = is_plug_disconnected
                    shared_state["voltages"] = [v1, v2, v3]
                    shared_state["currents"] = [i1, i2, i3]
                    shared_state["energy_total"] = session_energy_accumulator
                    shared_state["temperature_internal"] = temp_int
                    shared_state["charging_active"] = True
                    shared_state["session_energy_accumulator"] = session_energy_accumulator
                    shared_state["session_last_time"] = current_time
                    shared_state["session_last_power"] = current_power

                if current_time - _last_save_time >= 60.0:
                    _last_save_time = current_time
                    save_config_file()
            else:
                session_energy_accumulator = 0.0
                last_telemetry_time = None
                with state_lock:
                    if shared_state["session_energy_accumulator"] > 0 or shared_state["session_last_time"] > 0:
                        shared_state["energy_total"] = 0.0
                        shared_state["session_energy_accumulator"] = 0.0
                        shared_state["session_last_time"] = 0.0
                        shared_state["session_last_power"] = 0.0
                    shared_state["charger_connected"] = True
                    shared_state["pull_plug"] = is_plug_disconnected
                    shared_state["voltages"] = [0, 0, 0]
                    shared_state["currents"] = [0.0, 0.0, 0.0]
                    shared_state["temperature_internal"] = temp_int
                    shared_state["charging_active"] = False
                    shared_state["active_current_limit"] = 0
                save_config_file()


# BLE Értesítéskezelő (GATT NOTIFY) callback
def ble_notification_received(sender, data_bytes):
    global ble_rx_buffer, active_ble_client, last_rx_time
    last_rx_time = time.time()

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
                if main_loop is not None:
                    try:
                        asyncio.run_coroutine_threadsafe(process_assembled_packet(packet, active_ble_client), main_loop)
                    except Exception as e:
                        print(f"Hiba a csomag ütemezésekor: {e}")
                else:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(process_assembled_packet(packet, active_ble_client))
                    except Exception:
                        try:
                            asyncio.create_task(process_assembled_packet(packet, active_ble_client))
                        except Exception as e:
                            print(f"Hiba a csomag aszinkron indításakor: {e}")
        else:
            print("Figyelmeztetés: Hibás csomagvégződés, elvetve.")

    """
    Kész- VÁZLAT: Ezt az eredeti process_assembled_packet() függvény teljes kódjával kell helyettesíteni.
    Feladata: Beérkező BLE csomagok feldolgozása és állapotgép kezelése.
    """

    pass
