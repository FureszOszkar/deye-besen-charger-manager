import time
from config import shared_state, state_lock, log_message, DEFAULT_CONFIG

# --- BIZTONSÁGKEZELÉS ÉS COOLDOWN LOGIKA ---

def check_charger_error():
    """Ellenőrzi, hogy a töltőnek van-e hiba."""
    with state_lock:
        return shared_state.get("charger_error", False)

def check_charging_blocked():
    """Ellenőrzi, hogy a töltés blokkolva van-e."""
    with state_lock:
        return shared_state.get("charging_blocked", False)

def get_stop_reason():
    """Lekérdezi a leállás okát."""
    with state_lock:
        return shared_state.get("stop_reason", "")

def get_cooldown_remaining():
    """Lekérdezi a hátralévő hűtési időt másodpercben."""
    with state_lock:
        cooldown_end = shared_state.get("charger_cooldown_end", 0)
    
    current_time = time.time()
    remaining = cooldown_end - current_time
    return max(0, remaining)

def is_in_cooldown():
    """Megállapítja, hogy éppen cooldown-ban van-e."""
    return get_cooldown_remaining() > 0

def set_cooldown(duration_seconds):
    """Beállít egy cooldown időtartamot (másodpercben)."""
    current_time = time.time()
    cooldown_end = current_time + duration_seconds
    with state_lock:
        shared_state["charger_cooldown_end"] = cooldown_end
    log_message(f"[BIZTONSÁGI] Cooldown beállítva: {duration_seconds} másodperc")

def clear_cooldown():
    """Törli a cooldown-t."""
    with state_lock:
        shared_state["charger_cooldown_end"] = 0
        shared_state["cooldown_remaining"] = 0
        shared_state["cooldown_before_restart"] = False
    log_message("[BIZTONSÁGI] Cooldown törlve")

def set_charging_blocked(blocked, reason=""):
    """Beállítja, hogy a töltés blokkolva legyen-e."""
    with state_lock:
        shared_state["charging_blocked"] = blocked
        if reason:
            shared_state["stop_reason"] = reason
    
    if blocked:
        log_message(f"[BIZTONSÁGI BLOKK] Töltés blokkolva: {reason}")
    else:
        log_message("[BIZTONSÁGI BLOKK] Töltés feloldva")

def set_charger_error(error_state, error_msg=""):
    """Beállítja a töltő hiba állapotát."""
    with state_lock:
        shared_state["charger_error"] = error_state
        if error_msg:
            shared_state["last_error_log"] = error_msg
    
    if error_state:
        log_message(f"[TÖLTŐ HIBA] {error_msg}")

def check_meter_readout_valid():
    """Ellenőrzi, hogy az utolsó méterleolvasás érvényes-e."""
    with state_lock:
        return shared_state.get("meter_readout_updated", False)

def mark_meter_readout_updated():
    """Jelzi, hogy a méterleolvasás frissült."""
    with state_lock:
        shared_state["meter_readout_updated"] = True

def mark_meter_readout_stale():
    """Jelzi, hogy a méterleolvasás elavult."""
    with state_lock:
        shared_state["meter_readout_updated"] = False

def check_inverter_connected():
    """Ellenőrzi, hogy az inverter csatlakoztatva van-e."""
    with state_lock:
        return shared_state.get("inverter_connected", False)

def check_charger_connected():
    """Ellenőrzi, hogy a töltő csatlakoztatva van-e."""
    with state_lock:
        return shared_state.get("charger_connected", False)

def check_plug_pulled():
    """Ellenőrzi, hogy a csatlakozó kihúzva van-e."""
    with state_lock:
        return shared_state.get("pull_plug", False)

def validate_safety_conditions():
    """Validálja az összes biztonsági feltételt."""
    conditions = {
        "inverter_connected": check_inverter_connected(),
        "charger_connected": check_charger_connected(),
        "meter_valid": check_meter_readout_valid(),
        "no_error": not check_charger_error(),
        "not_blocked": not check_charging_blocked(),
        "plug_inserted": not check_plug_pulled(),
        "not_in_cooldown": not is_in_cooldown()
    }
    
    all_valid = all(conditions.values())
    return all_valid, conditions

def get_soc_limits():
    """Lekérdezi az akkumulátor SoC határait."""
    with state_lock:
        start_soc = shared_state.get("start_soc", DEFAULT_CONFIG["start_soc"])
        stop_soc = shared_state.get("stop_soc", DEFAULT_CONFIG["stop_soc"])
    
    return start_soc, stop_soc

def get_import_limit():
    """Lekérdezi a hálózati importálás limitjét (W)."""
    with state_lock:
        return shared_state.get("stop_import_limit", DEFAULT_CONFIG["stop_import_limit"])

def get_house_power_limit():
    """Lekérdezi a házfogyasztás limitjét (W)."""
    with state_lock:
        return shared_state.get("house_power_limit_w", DEFAULT_CONFIG["house_power_limit_w"])

def get_battery_soc():
    """Lekérdezi az akkumulátor SoC%-át."""
    with state_lock:
        return shared_state.get("battery_soc", 0)

def get_grid_power():
    """Lekérdezi a hálózati teljesítményt (W)."""
    with state_lock:
        return shared_state.get("grid_power", 0)

def get_pv_power():
    """Lekérdezi a napelemes teljesítményt (W)."""
    with state_lock:
        return shared_state.get("pv_power", 0)

def get_battery_power():
    """Lekérdezi az akkumulátor teljesítményt (W)."""
    with state_lock:
        return shared_state.get("battery_power", 0)
