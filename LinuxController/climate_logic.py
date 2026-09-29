"""Klímák (Solar Fűtés) — 1. fázis: eszközkapcsolat és kézi vezérlés.

* run_climate_polling(): a légtisztító hőmérséklet/páratartalom kiolvasása percenként
  (Watchdog alatt, saját "climate" PONG-gal).
* A felület (dashboard) által hívott műveletek: IR-kód tanítása és kézi próba-küldés. Az eszközök
  címe, a légtisztító tokenje és a klímák neve csak a config.json-ban állítható. Ezek a webszerver szálaiból futnak; a tanítás külön háttérszálban.
"""
import asyncio
import base64
import threading
import time

import config as cfg
from config import shared_state, state_lock, log_message, save_config_file
import climate_devices as dev

SENSOR_POLL_INTERVAL_S = 60     # légtisztító-olvasás gyakorisága
SENSOR_READ_TIMEOUT_S = 15      # egy olvasás teljes időkorlátja (a hálózati korlátok felett)
PONG_STEP_S = 5                 # várakozás közben ennyi másodpercenként PONG (Watchdog: 30 s)
SIM_LEARN_DELAY_S = 3           # szimulációban ennyi idő után "érkezik" a tanított kód

_unit_locks = [threading.Lock() for _ in range(cfg.CLIMATE_UNIT_COUNT)]
_CODE_KEYS = {"heat_on": "ir_on_heat", "cool_on": "ir_on_cool", "off": "ir_off"}
_CODE_LABELS = {"heat_on": "fűtés BE", "cool_on": "hűtés BE", "off": "KI"}


def _pong():
    with state_lock:
        shared_state["task_pong"]["climate"] = time.time()


def _unit_label(i):
    with state_lock:
        name = cfg.CLIMATE_CONFIG["units"][i]["name"]
    return f"{i + 1}. klíma" + (f" ({name})" if name else "")


def _set_unit_result(i, ok, text):
    with state_lock:
        unit = shared_state["climate"]["units"][i]
        unit["last_result"] = text
        unit["last_result_ok"] = ok
        unit["last_result_time"] = time.time()


# --- Légtisztító olvasása ---------------------------------------------------------

async def _read_sensor_once(previous_connected):
    with state_lock:
        ip = cfg.CLIMATE_CONFIG["sensor"]["ip"]
        token = cfg.CLIMATE_CONFIG["sensor"]["token"]
        sim = shared_state["simulation"]

    if sim:
        values, error = {"temperature": 21.5, "humidity": 45.0}, ""
    elif not ip or not token:
        values, error = None, ""
    else:
        try:
            values = await asyncio.wait_for(asyncio.to_thread(dev.purifier_read, ip, token),
                                            timeout=SENSOR_READ_TIMEOUT_S)
            error = ""
        except asyncio.TimeoutError:
            values, error = None, "A légtisztító olvasása túllépte az időkorlátot."
        except Exception as e:
            values, error = None, str(e)

    connected = values is not None
    with state_lock:
        sensor = shared_state["climate"]["sensor"]
        sensor["connected"] = connected
        sensor["error"] = error
        if connected:
            sensor["temperature"] = values["temperature"]
            sensor["humidity"] = values["humidity"]
            sensor["updated"] = time.time()

    if connected and not previous_connected:
        log_message(f"[KLÍMA] Légtisztító elérhető: {values['temperature']:.1f} °C, "
                    f"{values['humidity']:.0f}% páratartalom.")
    elif not connected and previous_connected:
        log_message(f"[KLÍMA] A légtisztító nem érhető el: {error or 'nincs beállítva'}")
    elif not connected and error and previous_connected is None:
        log_message(f"[KLÍMA] A légtisztító nem érhető el: {error}")
    return connected


async def _check_units_once():
    """A beállított BroadLinkek elérhetőségének ellenőrzése (a fejléc Klíma1–3 jelvényéhez).
    A művelet alatt álló (tanítás/küldés) klímát kihagyjuk."""
    with state_lock:
        units = [(i, u["broadlink_ip"]) for i, u in enumerate(cfg.CLIMATE_CONFIG["units"])]
        sim = shared_state["simulation"]
    for i, ip in units:
        _pong()
        with state_lock:
            busy = shared_state["climate"]["units"][i]["busy"]
            previous = shared_state["climate"]["units"][i]["reachable"]
        if busy:
            continue
        if not ip:
            reachable = None
        elif sim:
            reachable = True
        else:
            try:
                reachable = await asyncio.wait_for(asyncio.to_thread(dev.broadlink_reachable, ip),
                                                   timeout=dev.BROADLINK_TIMEOUT_S + 3)
            except asyncio.TimeoutError:
                reachable = False
        with state_lock:
            shared_state["climate"]["units"][i]["reachable"] = reachable
        if reachable is not None and reachable != previous:
            log_message(f"[KLÍMA] {_unit_label(i)}: BroadLink ({ip}) "
                        f"{'elérhető' if reachable else 'nem érhető el'}.")


async def run_climate_polling():
    """A légtisztító és a BroadLinkek percenkénti ellenőrzése (Watchdog alatt)."""
    previous_connected = None
    while True:
        _pong()
        previous_connected = await _read_sensor_once(previous_connected)
        await _check_units_once()
        waited = 0
        while waited < SENSOR_POLL_INTERVAL_S:
            _pong()
            await asyncio.sleep(PONG_STEP_S)
            waited += PONG_STEP_S


# --- Klímavezérlés beállításai (felület) -------------------------------------------------

def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value  # NaN kizárva


def update_climate_settings(data):
    """A Klímavezérlés beállításainak ATOMI mentése a config.json-ba (a Solar Auto mintájára):
    előbb minden mező ellenőrzése — üres/hibás mező esetén semmi nem változik —, csak utána
    alkalmazás. Az automata a 2. fázisban használja őket. Visszatérés: (siker, üzenet)."""
    if not isinstance(data, dict):
        return False, "Hibás adatformátum."
    mode = data.get("mode")
    auto_enabled = data.get("auto_enabled")
    units = data.get("units")
    if mode not in ("heat", "cool"):
        return False, "Érvénytelen üzemmód (fűtés/hűtés)."
    if not isinstance(auto_enabled, bool):
        return False, "Hibás adatformátum."
    if not isinstance(units, list) or len(units) != cfg.CLIMATE_UNIT_COUNT:
        return False, "Hibás adatformátum."

    labels = {"soc": "Akkuszint", "target_temp": "Célhőmérséklet",
              "on_minutes": "Bekapcsolási idő", "off_minutes": "Kikapcsolási idő"}
    values = {}
    for key, label in labels.items():
        v = data.get(key)
        if not _is_number(v):
            return False, f"{label}: hiányzó vagy érvénytelen érték."
        values[key] = v
    if not 0 <= values["soc"] <= 100:
        return False, "Akkuszint: 0 és 100 % között kell legyen."
    for key in ("on_minutes", "off_minutes"):
        if values[key] < 0:
            return False, f"{labels[key]}: nem lehet negatív."

    parsed_units = []
    for i, unit in enumerate(units):
        if not isinstance(unit, dict):
            return False, "Hibás adatformátum."
        w, exp = unit.get("surplus_w"), unit.get("expected_w")
        if not _is_number(w) or w < 0:
            return False, f"Klíma{i + 1}: a visszatermelés (W) hiányzik, érvénytelen vagy negatív."
        if not _is_number(exp) or exp < 0:
            return False, f"Klíma{i + 1}: a várható fogyasztás (W) hiányzik, érvénytelen vagy negatív."
        parsed_units.append((w, exp))

    with state_lock:
        s = cfg.CLIMATE_CONFIG["settings"]
        s["mode"] = mode
        s["auto_enabled"] = auto_enabled
        s.update(values)
        for i, (w, exp) in enumerate(parsed_units):
            cfg.CLIMATE_CONFIG["units"][i]["surplus_w"] = w
            cfg.CLIMATE_CONFIG["units"][i]["expected_w"] = exp
        cfg.refresh_climate_public_state()
    save_config_file()
    log_message(f"[KLÍMA] Beállítások mentve ({'fűtés' if mode == 'heat' else 'hűtés'}, "
                f"automata {'bekapcsolva' if auto_enabled else 'kikapcsolva'}).")
    return True, "Mentve."


# --- IR-kód tanítása és kézi küldése (felület) -----------------------------------------

def _check_unit_args(unit, which):
    if not isinstance(unit, int) or isinstance(unit, bool) or not 0 <= unit < cfg.CLIMATE_UNIT_COUNT:
        return "Érvénytelen klíma-sorszám."
    if which not in _CODE_KEYS:
        return "Érvénytelen kód-típus (heat_on/cool_on/off)."
    return None


def start_learn(unit, which):
    """Tanítás indítása háttérszálban. Visszatérés: (siker, üzenet)."""
    err = _check_unit_args(unit, which)
    if err:
        return False, err
    with state_lock:
        ip = cfg.CLIMATE_CONFIG["units"][unit]["broadlink_ip"]
        sim = shared_state["simulation"]
    if not ip and not sim:
        return False, "Nincs megadva a BroadLink IP-címe (config.json)."
    if not _unit_locks[unit].acquire(blocking=False):
        return False, "Ennél a klímánál már folyamatban van egy művelet."
    with state_lock:
        shared_state["climate"]["units"][unit]["busy"] = f"learn_{which}"
    threading.Thread(target=_learn_worker, args=(unit, which, ip, sim), daemon=True).start()
    return True, (f"Tanítás indult ({_CODE_LABELS[which]}): {dev.LEARN_TIMEOUT_S} másodpercen belül "
                  f"nyomd meg a távirányító gombját a BroadLink felé fordítva.")


def _learn_worker(unit, which, ip, sim):
    label = _unit_label(unit)
    try:
        log_message(f"[KLÍMA] {label}: {_CODE_LABELS[which]} kód tanítása indult ({'szimuláció' if sim else ip}).")
        if sim:
            time.sleep(SIM_LEARN_DELAY_S)
            code = base64.b64encode(f"SIM-{unit}-{which}".encode()).decode("ascii")
        else:
            code = dev.broadlink_learn(ip)
        with state_lock:
            cfg.CLIMATE_CONFIG["units"][unit][_CODE_KEYS[which]] = code
            cfg.refresh_climate_public_state()
        save_config_file()
        _set_unit_result(unit, True, f"Megtanulva: {_CODE_LABELS[which]} kód.")
        log_message(f"[KLÍMA] {label}: {_CODE_LABELS[which]} kód megtanulva és elmentve.")
    except Exception as e:
        _set_unit_result(unit, False, f"Tanítás sikertelen: {e}")
        log_message(f"[KLÍMA] {label}: {_CODE_LABELS[which]} kód tanítása sikertelen: {e}")
    finally:
        with state_lock:
            shared_state["climate"]["units"][unit]["busy"] = ""
        _unit_locks[unit].release()


def send_code(unit, which):
    """Kézi próba-küldés (a hívó szálban, legfeljebb néhány másodperc). Visszatérés: (siker, üzenet)."""
    err = _check_unit_args(unit, which)
    if err:
        return False, err
    with state_lock:
        ip = cfg.CLIMATE_CONFIG["units"][unit]["broadlink_ip"]
        code = cfg.CLIMATE_CONFIG["units"][unit][_CODE_KEYS[which]]
        sim = shared_state["simulation"]
    if not code:
        return False, f"A(z) {_CODE_LABELS[which]} kód még nincs megtanítva."
    if not ip and not sim:
        return False, "Nincs megadva a BroadLink IP-címe (config.json)."
    if not _unit_locks[unit].acquire(blocking=False):
        return False, "Ennél a klímánál már folyamatban van egy művelet."
    label = _unit_label(unit)
    try:
        with state_lock:
            shared_state["climate"]["units"][unit]["busy"] = f"send_{which}"
        if not sim:
            dev.broadlink_send(ip, code)
        msg = f"Elküldve: {_CODE_LABELS[which]} parancs."
        _set_unit_result(unit, True, msg)
        log_message(f"[KLÍMA] {label}: kézi {_CODE_LABELS[which]} parancs elküldve"
                    f"{' (szimuláció)' if sim else ''}.")
        return True, msg
    except Exception as e:
        _set_unit_result(unit, False, f"Küldés sikertelen: {e}")
        log_message(f"[KLÍMA] {label}: kézi {_CODE_LABELS[which]} parancs sikertelen: {e}")
        return False, str(e)
    finally:
        with state_lock:
            shared_state["climate"]["units"][unit]["busy"] = ""
        _unit_locks[unit].release()
