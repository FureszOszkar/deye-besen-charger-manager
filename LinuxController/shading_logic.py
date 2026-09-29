"""Árnyékolás: redőny és napellenző rádiós (RF) vezérlése BroadLink RM4 Pro-val, napi időzítővel.

* run_shading_timer(): percenként megnézi, hogy valamelyik aktív eszköznek most van-e az aznapi
  Fel/Le időpontja; ha igen, abban a percben EGYSZER elküldi a jelet (Watchdog alatt, saját
  "shading" PONG-gal). Ha a program abban a percben nem fut, a mozgás kimarad (nincs pótlás).
* A felület által hívott műveletek: időzítő-beállítás mentése (a config.json-ba, induláskor onnan
  töltődik vissza), RF-kód tanítása (háttérszálban, két lépésben), kézi próba (Fel/Le).
"""
import asyncio
import base64
import re
import threading
import time

import config as cfg
from config import shared_state, state_lock, log_message, save_config_file
import climate_devices as dev

CHECK_STEP_S = 5                # ennyi másodpercenként néz rá az órára (és PONG a Watchdognak)
SEND_TIMEOUT_S = 15             # egy küldés teljes időkorlátja
SIM_LEARN_STAGE_S = 2           # szimulációban a tanítás lépéseinek hossza

_TIME_PATTERN = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
_DEVICE_LABELS = {"roller": "Redőny", "awning": "Napellenző"}
_ACTION_KEYS = {"up": "rf_up", "down": "rf_down"}
_ACTION_LABELS = {
    "roller": {"up": "Fel", "down": "Le"},
    "awning": {"up": "Be (Fel)", "down": "Ki (Le)"},
}
_device_locks = {d: threading.Lock() for d in cfg.SHADING_DEVICES}
_now = time.localtime           # tesztekben lecserélhető


def _pong():
    with state_lock:
        shared_state["task_pong"]["shading"] = time.time()


def _set_result(device, ok, text):
    with state_lock:
        d = shared_state["shading"]["devices"][device]
        d["last_result"] = text
        d["last_result_ok"] = ok
        d["last_result_time"] = time.time()


def _send_blocking(device, action):
    """Egy RF-kód kiküldése (blokkoló). Visszatérés: (siker, üzenet)."""
    with state_lock:
        ip = cfg.SHADING_CONFIG["broadlink_ip"]
        code = cfg.SHADING_CONFIG["devices"][device][_ACTION_KEYS[action]]
        sim = shared_state["simulation"]
    label = f"{_DEVICE_LABELS[device]} {_ACTION_LABELS[device][action]}"
    if not code:
        return False, f"A(z) {label} kód még nincs megtanítva."
    if not ip and not sim:
        return False, "Nincs megadva az árnyékolás BroadLink IP-címe (config.json)."
    if not sim:
        dev.broadlink_send(ip, code)
    return True, f"Elküldve: {label}."


# --- Időzítő ---------------------------------------------------------------------------

async def run_shading_timer():
    """A redőny és a napellenző napi időzítője (Watchdog alatt)."""
    fired = set()   # {(eszköz, művelet, "YYYY-MM-DD HH:MM")} — egy percben csak egyszer
    while True:
        _pong()
        now = _now()
        hhmm = time.strftime("%H:%M", now)
        stamp = time.strftime("%Y-%m-%d ", now) + hhmm
        day = now.tm_wday
        fired = {f for f in fired if f[2] == stamp}   # a régi percek bejegyzései nem kellenek
        for device in cfg.SHADING_DEVICES:
            with state_lock:
                d = cfg.SHADING_CONFIG["devices"][device]
                enabled = d["enabled"]
                times = dict(d["schedule"][day])
            if not enabled:
                continue
            for action in ("up", "down"):
                if times.get(action) != hhmm or (device, action, stamp) in fired:
                    continue
                fired.add((device, action, stamp))
                label = f"{_DEVICE_LABELS[device]} {_ACTION_LABELS[device][action]}"
                if not _device_locks[device].acquire(blocking=False):
                    log_message(f"[ÁRNYÉKOLÁS] Időzített {label} kimaradt: épp tanítás/próba folyik.")
                    continue
                try:
                    ok, msg = await asyncio.wait_for(asyncio.to_thread(_send_blocking, device, action),
                                                     timeout=SEND_TIMEOUT_S)
                except asyncio.TimeoutError:
                    ok, msg = False, "a küldés túllépte az időkorlátot."
                except Exception as e:
                    ok, msg = False, str(e)
                finally:
                    _device_locks[device].release()
                _set_result(device, ok, ("Időzítve — " + msg) if ok else f"Időzített {label} sikertelen: {msg}")
                log_message(f"[ÁRNYÉKOLÁS] Időzített {label} ({hhmm}): {'elküldve' if ok else 'sikertelen — ' + msg}")
        await asyncio.sleep(CHECK_STEP_S)


# --- Beállítások (felület) ---------------------------------------------------------------

def update_shading_config(data):
    """Egy eszköz időzítő-beállításának ATOMI mentése a config.json-ba: előbb minden mező
    ellenőrzése, csak utána alkalmazás. Várt formátum:
    {"device": "roller"|"awning", "enabled": bool, "schedule": [7 × {"up": "HH:MM"|"", "down": ...}]}"""
    if not isinstance(data, dict):
        return False, "Hibás adatformátum."
    device = data.get("device")
    enabled = data.get("enabled")
    schedule = data.get("schedule")
    if device not in cfg.SHADING_DEVICES:
        return False, "Ismeretlen eszköz."
    if not isinstance(enabled, bool) or not isinstance(schedule, list) or len(schedule) != len(cfg.WEEK_DAYS):
        return False, "Hibás adatformátum."
    parsed = []
    for i, day in enumerate(schedule):
        if not isinstance(day, dict):
            return False, "Hibás adatformátum."
        entry = {}
        for key in ("up", "down"):
            value = day.get(key, "")
            if not isinstance(value, str):
                return False, "Hibás adatformátum."
            value = value.strip()
            if value and not _TIME_PATTERN.match(value):
                return False, f"{cfg.WEEK_DAYS[i]}: érvénytelen időpont „{value}” (ÓÓ:PP kell, vagy üres)."
            entry[key] = value
        parsed.append(entry)

    with state_lock:
        d = cfg.SHADING_CONFIG["devices"][device]
        d["enabled"] = enabled
        for i, entry in enumerate(parsed):
            d["schedule"][i]["up"] = entry["up"]
            d["schedule"][i]["down"] = entry["down"]
        cfg.refresh_shading_public_state()
    save_config_file()
    log_message(f"[ÁRNYÉKOLÁS] {_DEVICE_LABELS[device]} időzítő mentve "
                f"({'aktív' if enabled else 'kikapcsolva'}).")
    return True, "Mentve."


# --- RF-kód tanítása és kézi küldése (felület) ----------------------------------------------

def _check_args(device, action):
    if device not in cfg.SHADING_DEVICES:
        return "Ismeretlen eszköz."
    if action not in _ACTION_KEYS:
        return "Érvénytelen művelet (up/down)."
    return None


def start_learn(device, action):
    """RF-tanítás indítása háttérszálban. Visszatérés: (siker, üzenet)."""
    err = _check_args(device, action)
    if err:
        return False, err
    with state_lock:
        ip = cfg.SHADING_CONFIG["broadlink_ip"]
        sim = shared_state["simulation"]
    if not ip and not sim:
        return False, "Nincs megadva az árnyékolás BroadLink IP-címe (config.json)."
    if not _device_locks[device].acquire(blocking=False):
        return False, "Ennél az eszköznél már folyamatban van egy művelet."
    with state_lock:
        d = shared_state["shading"]["devices"][device]
        d["busy"] = f"learn_{action}"
        d["stage"] = "start"
    threading.Thread(target=_learn_worker, args=(device, action, ip, sim), daemon=True).start()
    label = f"{_DEVICE_LABELS[device]} {_ACTION_LABELS[device][action]}"
    return True, (f"Tanítás indult ({label}). 1. lépés: amikor a felület kéri, TARTSD NYOMVA a távirányító "
                  f"gombját, amíg a frekvencia megvan; 2. lépés: utána EGYSZER nyomd meg.")


def _set_stage(device, stage):
    with state_lock:
        shared_state["shading"]["devices"][device]["stage"] = stage


def _learn_worker(device, action, ip, sim):
    label = f"{_DEVICE_LABELS[device]} {_ACTION_LABELS[device][action]}"
    try:
        log_message(f"[ÁRNYÉKOLÁS] {label} RF-kód tanítása indult ({'szimuláció' if sim else ip}).")
        if sim:
            _set_stage(device, "hold")
            time.sleep(SIM_LEARN_STAGE_S)
            _set_stage(device, "press")
            time.sleep(SIM_LEARN_STAGE_S)
            code = base64.b64encode(f"SIM-RF-{device}-{action}".encode()).decode("ascii")
        else:
            code = dev.broadlink_learn_rf(ip, on_stage=lambda s: _set_stage(device, s))
        with state_lock:
            cfg.SHADING_CONFIG["devices"][device][_ACTION_KEYS[action]] = code
            cfg.refresh_shading_public_state()
        save_config_file()
        _set_result(device, True, f"Megtanulva: {label} kód.")
        log_message(f"[ÁRNYÉKOLÁS] {label} RF-kód megtanulva és elmentve.")
    except Exception as e:
        _set_result(device, False, f"Tanítás sikertelen: {e}")
        log_message(f"[ÁRNYÉKOLÁS] {label} RF-kód tanítása sikertelen: {e}")
    finally:
        with state_lock:
            d = shared_state["shading"]["devices"][device]
            d["busy"] = ""
            d["stage"] = ""
        _device_locks[device].release()


def send_code(device, action):
    """Kézi próba-küldés (a hívó szálban, legfeljebb néhány másodperc). Visszatérés: (siker, üzenet)."""
    err = _check_args(device, action)
    if err:
        return False, err
    if not _device_locks[device].acquire(blocking=False):
        return False, "Ennél az eszköznél már folyamatban van egy művelet."
    try:
        with state_lock:
            shared_state["shading"]["devices"][device]["busy"] = f"send_{action}"
        ok, msg = _send_blocking(device, action)
    except Exception as e:
        ok, msg = False, str(e)
    finally:
        with state_lock:
            shared_state["shading"]["devices"][device]["busy"] = ""
        _device_locks[device].release()
    _set_result(device, ok, msg if ok else f"Küldés sikertelen: {msg}")
    log_message(f"[ÁRNYÉKOLÁS] Kézi próba — {msg}" if ok else f"[ÁRNYÉKOLÁS] Kézi próba sikertelen: {msg}")
    return ok, msg
