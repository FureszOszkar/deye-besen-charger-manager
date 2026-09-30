"""Otthoni eszközök a natív apphoz: CozyLife LED-szalag, Xiaomi okos konnektor és internet-rádió.

* CozyLife LED-szalag: helyi TCP (5555-ös port), soronként egy JSON-üzenet ("\\r\\n" lezárással),
  a gyártó nyílt Home Assistant-integrációja (github.com/cozylife/hass_cozylife_local_pull) alapján.
  Lekérdezés: cmd 2, beállítás: cmd 3. Adatpontok: "1" = be/ki (0/255), "2" = mód (0 = statikus
  szín), "4" = fényerő 0–1000, "5" = színárnyalat 0–360, "6" = telítettség 0–1000.
  Kulcs vagy jelszó nincs: a helyi hálózaton bárki kapcsolhatja.
* Xiaomi konnektor: a meglévő miIO-kliens (climate_devices.miio_call). A hívás a típustól függ
  (_plug_protocol): régi miio (get_prop / set_power vagy set_on) vagy MiOT (siid 2 / piid 1).
* Internet-rádió: infra, egy BroadLinken át (climate_devices.broadlink_learn / broadlink_send).
  A távirányítón egyetlen power gomb van, ezért egy kód: a „Be” és a „Ki” is ugyanazt a
  gombnyomást küldi (a rádió az ellenkező állapotába vált). Az infra egyirányú, a rádió valódi
  állapota nem ismert — nincs lekérdezés.
* run_home_devices(): percenként lekérdezi a LED-et és a konnektort (Watchdog alatt, saját
  "home" PONG-gal), és futtatja a konnektor és a rádió napi időzítőjét: a megadott percben EGYSZER
  kapcsol, ha a program abban a percben nem fut, a kapcsolás kimarad (nincs pótlás) — mint az
  árnyékolásnál.
* A felület (app, web) által hívott műveletek atomi ellenőrzéssel: LED beállítása, konnektor
  kapcsolása, rádió power-kódjának tanítása és küldése, az időzítők mentése (config.json).
"""
import base64
import asyncio
import json
import re
import socket
import threading
import time

import config as cfg
from config import shared_state, state_lock, log_message, save_config_file
import climate_devices as dev

COZYLIFE_PORT = 5555
COZYLIFE_TIMEOUT_S = 3          # válasz-időkorlát (a gyártói integrációval egyezően)
POLL_INTERVAL_S = 60            # állapot-lekérdezés percenként (felhasználói döntés)
CHECK_STEP_S = 5                # az időzítő ennyi másodpercenként néz rá az órára (és PONG)
DEVICE_TIMEOUT_S = 15           # egy eszközművelet teljes időkorlátja a háttérfeladatban
SIM_LEARN_DELAY_S = 3           # szimulációban ennyi idő után "érkezik" a tanított kód

_TIME_PATTERN = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
_now = time.localtime           # tesztekben lecserélhető
_led_lock = threading.Lock()    # egyszerre egy művelet eszközönként
_plug_lock = threading.Lock()
_radio_lock = threading.Lock()
_sn_lock = threading.Lock()
_last_sn = 0
_sim = {"led": {"on": False, "brightness": 60, "hue": 30, "saturation": 80}, "plug": {"on": False}}

LED_LABEL = "A LED-szalag"
PLUG_LABEL = "A konnektor"
RADIO_LABEL = "Az internet-rádió"

# Xiaomi (chuangmi) konnektorok régi miio-parancsai típusonként; minden más típus MiOT-ot beszél.
_PLUG_SET_ON_MODELS = {"chuangmi.plug.v1"}                        # set_on / set_off, "on" tulajdonság
_PLUG_ON_KEY_MODELS = {"chuangmi.plug.v1", "chuangmi.plug.v3"}    # get_prop ["on"] -> true/false
_PLUG_POWER_MODELS = {"chuangmi.plug.m1", "chuangmi.plug.m3", "chuangmi.plug.v2",
                      "chuangmi.plug.hmi205", "chuangmi.plug.hmi206", "chuangmi.plug.hmi208"}
_PLUG_MIOT_POWER = {"did": "power", "siid": 2, "piid": 1}


class HomeDeviceError(Exception):
    """Érthető, felhasználónak szóló hibaüzenet egy eszközművelet sikertelenségéről."""


def _pong():
    with state_lock:
        shared_state["task_pong"]["home"] = time.time()


# --- CozyLife LED-szalag ----------------------------------------------------------------------

def _next_sn():
    """Növekvő sorszám (ezredmásodperc), hogy egy késve érkező régi válasz ne keveredjen."""
    global _last_sn
    with _sn_lock:
        _last_sn = max(int(time.time() * 1000), _last_sn + 1)
        return _last_sn


def _cozylife_request(ip, cmd, msg, timeout=COZYLIFE_TIMEOUT_S):
    """Egy kérés egy új TCP-kapcsolaton; a válasz "msg.data" részét adja vissza."""
    sn = _next_sn()
    frame = json.dumps({"pv": 0, "cmd": cmd, "sn": str(sn), "msg": msg}, separators=(",", ":")) + "\r\n"
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection((ip, COZYLIFE_PORT), timeout=timeout) as sock:
            sock.sendall(frame.encode("utf-8"))
            buf = b""
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout()
                sock.settimeout(remaining)
                chunk = sock.recv(4096)
                if not chunk:
                    raise HomeDeviceError(f"{LED_LABEL} ({ip}) bontotta a kapcsolatot.")
                buf += chunk
                if len(buf) > 64 * 1024:
                    raise HomeDeviceError(f"{LED_LABEL} ({ip}) túl hosszú választ küldött.")
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    try:
                        resp = json.loads(line.decode("utf-8"))
                    except ValueError:
                        continue
                    # Az eszköz magától is küldhet állapotjelentést (cmd 10) — azt átugorjuk
                    if not isinstance(resp, dict) or resp.get("cmd") != cmd or str(resp.get("sn")) != str(sn):
                        continue
                    if resp.get("res") != 0:
                        raise HomeDeviceError(f"{LED_LABEL} elutasította a parancsot (kód: {resp.get('res')}).")
                    data = (resp.get("msg") or {}).get("data")
                    if not isinstance(data, dict):
                        raise HomeDeviceError(f"{LED_LABEL} hiányos választ küldött.")
                    return data
    except socket.timeout as e:
        raise HomeDeviceError(f"{LED_LABEL} ({ip}) nem válaszolt időben.") from e
    except OSError as e:
        raise HomeDeviceError(f"{LED_LABEL} ({ip}) nem érhető el: {e}") from e


def _led_from_data(data):
    """Az eszköz adatpontjaiból a felület értékei: be/ki, fényerő 0–100, szín (0–360, 0–100)."""
    def num(key):
        v = data.get(key)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    on, bright, hue, sat = num("1"), num("4"), num("5"), num("6")
    return {
        "on": None if on is None else on > 0,
        "brightness": None if bright is None else round(bright / 10),
        "hue": None if hue is None else int(hue),
        "saturation": None if sat is None else round(sat / 10),
    }


def led_query(ip):
    return _led_from_data(_cozylife_request(ip, 2, {"attr": [0]}))


def led_command(ip, payload):
    """Beállítás (cmd 3) a megadott adatpontokkal, majd a friss állapot lekérdezése."""
    _cozylife_request(ip, 3, {"attr": [int(k) for k in payload], "data": payload})
    return led_query(ip)


# --- Xiaomi konnektor --------------------------------------------------------------------------

def _plug_protocol(model):
    if model in _PLUG_ON_KEY_MODELS:
        return "on_key"
    if model in _PLUG_POWER_MODELS:
        return "power"
    return "miot"


def plug_read(ip, token, model):
    """A konnektor be/ki állapota (True/False)."""
    proto = _plug_protocol(model)
    if proto == "on_key":
        result = dev.miio_call(ip, token, "get_prop", ["on"], label=PLUG_LABEL)
        if isinstance(result, list) and result and isinstance(result[0], bool):
            return result[0]
    elif proto == "power":
        result = dev.miio_call(ip, token, "get_prop", ["power"], label=PLUG_LABEL)
        if isinstance(result, list) and result and result[0] in ("on", "off"):
            return result[0] == "on"
    else:
        result = dev.miio_call(ip, token, "get_properties", [dict(_PLUG_MIOT_POWER)], label=PLUG_LABEL)
        if isinstance(result, list) and result and result[0].get("code") == 0 \
                and isinstance(result[0].get("value"), bool):
            return result[0]["value"]
    raise HomeDeviceError(f"{PLUG_LABEL} érthetetlen állapotot adott vissza: {result} (típus: {model or 'ismeretlen'}).")


def plug_write(ip, token, model, on):
    """A konnektor be- vagy kikapcsolása; hiba esetén HomeDeviceError."""
    proto = _plug_protocol(model)
    if proto == "miot":
        result = dev.miio_call(ip, token, "set_properties",
                               [dict(_PLUG_MIOT_POWER, value=bool(on))], label=PLUG_LABEL)
        ok = isinstance(result, list) and result and result[0].get("code") == 0
    else:
        if model in _PLUG_SET_ON_MODELS:
            result = dev.miio_call(ip, token, "set_on" if on else "set_off", [], label=PLUG_LABEL)
        else:
            result = dev.miio_call(ip, token, "set_power", ["on" if on else "off"], label=PLUG_LABEL)
        ok = result == ["ok"]
    if not ok:
        raise HomeDeviceError(f"{PLUG_LABEL} nem hajtotta végre a kapcsolást: {result}")


# --- Állapot a felület felé --------------------------------------------------------------------

def _store_led(values=None, error=""):
    with state_lock:
        st = shared_state["home"]["led"]
        was = st["reachable"]
        st["reachable"] = values is not None
        st["error"] = error
        if values is not None:
            st.update(values)
            st["updated"] = time.time()
    return was


def _store_plug(on=None, error=""):
    with state_lock:
        st = shared_state["home"]["plug"]
        was = st["reachable"]
        st["reachable"] = on is not None
        st["error"] = error
        if on is not None:
            st["on"] = on
            st["updated"] = time.time()
    return was


def _set_result(device, ok, text):
    with state_lock:
        st = shared_state["home"][device]
        st["last_result"] = text
        st["last_result_ok"] = ok
        st["last_result_time"] = time.time()


def _set_plug_result(ok, text):
    _set_result("plug", ok, text)


def _log_reachability(label, was, now, error):
    if now and was is not True:
        log_message(f"[OTTHON] {label} elérhető.")
    elif not now and was is not False:
        log_message(f"[OTTHON] {label} nem érhető el: {error}")


# --- Percenkénti lekérdezés és időzítő (háttérfeladat) -----------------------------------------

async def _poll_led():
    with state_lock:
        ip = cfg.HOME_CONFIG["led"]["ip"]
        sim = shared_state["simulation"]
        if sim:
            shared_state["home"]["led"]["configured"] = True
    if not ip and not sim:
        return
    if not _led_lock.acquire(blocking=False):
        return                      # épp egy app-parancs fut; a következő körben kérdezünk
    try:
        if sim:
            values, error = dict(_sim["led"]), ""
        else:
            try:
                values = await asyncio.wait_for(asyncio.to_thread(led_query, ip), timeout=DEVICE_TIMEOUT_S)
                error = ""
            except asyncio.TimeoutError:
                values, error = None, "a lekérdezés túllépte az időkorlátot."
            except Exception as e:
                values, error = None, str(e)
    finally:
        _led_lock.release()
    was = _store_led(values, error)
    _log_reachability(LED_LABEL, was, values is not None, error)


async def _poll_plug():
    with state_lock:
        plug = dict(cfg.HOME_CONFIG["plug"])
        sim = shared_state["simulation"]
        if sim:
            shared_state["home"]["plug"]["configured"] = True
    if not (plug["ip"] and plug["token"]) and not sim:
        return
    if not _plug_lock.acquire(blocking=False):
        return
    try:
        if sim:
            on, error = _sim["plug"]["on"], ""
        else:
            try:
                on = await asyncio.wait_for(
                    asyncio.to_thread(plug_read, plug["ip"], plug["token"], plug["model"]),
                    timeout=DEVICE_TIMEOUT_S)
                error = ""
            except asyncio.TimeoutError:
                on, error = None, "a lekérdezés túllépte az időkorlátot."
            except Exception as e:
                on, error = None, str(e)
    finally:
        _plug_lock.release()
    was = _store_plug(on, error)
    _log_reachability(PLUG_LABEL, was, on is not None, error)


def _plug_switch_blocking(on):
    """Kapcsolás + friss állapot (blokkoló). Visszatérés: (siker, üzenet)."""
    with state_lock:
        plug = dict(cfg.HOME_CONFIG["plug"])
        sim = shared_state["simulation"]
    if not (plug["ip"] and plug["token"]) and not sim:
        return False, "Nincs megadva a konnektor IP-címe vagy kulcsa (config.json)."
    if sim:
        _sim["plug"]["on"] = bool(on)
        state = _sim["plug"]["on"]
    else:
        plug_write(plug["ip"], plug["token"], plug["model"], on)
        state = plug_read(plug["ip"], plug["token"], plug["model"])
    _store_plug(state)
    return True, f"Konnektor {'bekapcsolva' if state else 'kikapcsolva'}."


async def _run_plug_timer(fired):
    now = _now()
    hhmm = time.strftime("%H:%M", now)
    stamp = time.strftime("%Y-%m-%d ", now) + hhmm
    fired = {f for f in fired if f[1] == stamp}     # a régi percek bejegyzései nem kellenek
    with state_lock:
        plug = cfg.HOME_CONFIG["plug"]
        enabled = plug["enabled"]
        times = dict(plug["schedule"][now.tm_wday])
    if not enabled:
        return fired
    for action in cfg.HOME_SCHEDULE_KEYS["plug"]:      # két be-ki pár: on/off és on2/off2
        if times.get(action) != hhmm or (action, stamp) in fired:
            continue
        fired.add((action, stamp))
        switch_on = action in ("on", "on2")
        label = "bekapcsolás" if switch_on else "kikapcsolás"
        if not _plug_lock.acquire(blocking=False):
            log_message(f"[OTTHON] A konnektor időzített {label}a kimaradt: épp egy másik művelet fut.")
            continue
        try:
            ok, msg = await asyncio.wait_for(asyncio.to_thread(_plug_switch_blocking, switch_on),
                                             timeout=DEVICE_TIMEOUT_S)
        except asyncio.TimeoutError:
            ok, msg = False, "a kapcsolás túllépte az időkorlátot."
        except Exception as e:
            ok, msg = False, str(e)
        finally:
            _plug_lock.release()
        _set_plug_result(ok, ("Időzítve — " + msg) if ok else f"Időzített {label} sikertelen: {msg}")
        log_message(f"[OTTHON] Konnektor időzített {label} ({hhmm}): {'kész' if ok else 'sikertelen — ' + msg}")
    return fired


def _radio_send_blocking():
    """A power kód kiküldése (blokkoló); a hívó tartsa a _radio_lock-ot. Visszatérés: (siker, üzenet)."""
    with state_lock:
        radio = dict(cfg.HOME_CONFIG["radio"])
        sim = shared_state["simulation"]
    if not radio["ir_power"]:
        return False, "A power kód még nincs megtanítva."
    if not radio["broadlink_ip"] and not sim:
        return False, "Nincs megadva a rádió BroadLinkjének IP-címe (config.json)."
    with state_lock:
        shared_state["home"]["radio"]["busy"] = "send"
    try:
        if not sim:
            dev.broadlink_send(radio["broadlink_ip"], radio["ir_power"])
    finally:
        with state_lock:
            shared_state["home"]["radio"]["busy"] = ""
    with state_lock:   # „Utoljára: power …” (az infra egyirányú, ez csak az elküldött gombnyomás)
        shared_state["home"]["radio"]["last_sent"] = time.time()
    return True, "Power gombnyomás elküldve" + (" (szimuláció)." if sim else ".")


async def _run_radio_timer(fired):
    """A rádió napi időzítője: a „Be” és a „Ki” időpontban is a power kódot küldi (percenként egyszer)."""
    now = _now()
    hhmm = time.strftime("%H:%M", now)
    stamp = time.strftime("%Y-%m-%d ", now) + hhmm
    fired = {f for f in fired if f[1] == stamp}
    with state_lock:
        radio = cfg.HOME_CONFIG["radio"]
        enabled = radio["enabled"]
        times = dict(radio["schedule"][now.tm_wday])
    if not enabled:
        return fired
    for action in ("on", "off"):
        if times.get(action) != hhmm or (action, stamp) in fired:
            continue
        fired.add((action, stamp))
        label = "bekapcsolás" if action == "on" else "kikapcsolás"
        if not _radio_lock.acquire(blocking=False):
            log_message(f"[OTTHON] Az internet-rádió időzített {label}a kimaradt: épp egy másik művelet fut.")
            continue
        try:
            ok, msg = await asyncio.wait_for(asyncio.to_thread(_radio_send_blocking), timeout=DEVICE_TIMEOUT_S)
        except asyncio.TimeoutError:
            ok, msg = False, "a küldés túllépte az időkorlátot."
        except Exception as e:
            ok, msg = False, str(e)
        finally:
            _radio_lock.release()
        _set_result("radio", ok, (f"Időzítve ({label}) — " + msg) if ok else f"Időzített {label} sikertelen: {msg}")
        log_message(f"[OTTHON] Internet-rádió időzített {label} ({hhmm}): {'kész' if ok else 'sikertelen — ' + msg}")
    return fired


async def run_home_devices():
    """A LED-szalag és a konnektor percenkénti lekérdezése + a konnektor és a rádió időzítője
    (Watchdog alatt)."""
    with state_lock:   # a szimulációs mód a config betöltése után kapcsol be: a „beállított” jelzés frissítése
        cfg.refresh_home_public_state()
    fired_plug, fired_radio = set(), set()
    last_poll = None
    while True:
        _pong()
        if last_poll is None or time.monotonic() - last_poll >= POLL_INTERVAL_S:
            last_poll = time.monotonic()
            await _poll_led()
            _pong()
            await _poll_plug()
            _pong()
        fired_plug = await _run_plug_timer(fired_plug)
        fired_radio = await _run_radio_timer(fired_radio)
        await asyncio.sleep(CHECK_STEP_S)


# --- A felület (app) által hívott műveletek ---------------------------------------------------

def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def led_set(data):
    """LED-szalag beállítása: {"on": bool, "brightness": 0–100, "hue": 0–360, "saturation": 0–100}
    bármely részhalmaza (a színhez a színárnyalat és a telítettség együtt kell). ATOMI: előbb minden
    mező ellenőrzése, hibánál semmi nem megy ki. Visszatérés: (siker, üzenet)."""
    if not isinstance(data, dict):
        return False, "Hibás adatformátum."
    allowed = {"on", "brightness", "hue", "saturation"}
    if not data or set(data) - allowed:
        return False, "Hibás adatformátum."
    on = data.get("on")
    if "on" in data and not isinstance(on, bool):
        return False, "Hibás adatformátum (be/ki)."
    if on is False and len(data) > 1:
        return False, "Kikapcsoláskor nem küldhető fényerő vagy szín."
    limits = {"brightness": (0, 100, "Fényerő"), "hue": (0, 360, "Színárnyalat"), "saturation": (0, 100, "Telítettség")}
    for key, (lo, hi, name) in limits.items():
        if key in data and (not _is_number(data[key]) or not lo <= data[key] <= hi):
            return False, f"{name}: {lo} és {hi} közötti szám kell."
    if ("hue" in data) != ("saturation" in data):
        return False, "A színhez a színárnyalat és a telítettség együtt kell."

    if on is False:
        payload = {"1": 0}
    else:
        payload = {"1": 255}
        if "brightness" in data:
            payload["4"] = int(round(data["brightness"] * 10))
        if "hue" in data:
            payload["2"] = 0
            payload["5"] = int(round(data["hue"]))
            payload["6"] = int(round(data["saturation"] * 10))

    with state_lock:
        ip = cfg.HOME_CONFIG["led"]["ip"]
        sim = shared_state["simulation"]
    if not ip and not sim:
        return False, "Nincs megadva a LED-szalag IP-címe (config.json)."
    if not _led_lock.acquire(blocking=False):
        return False, "A LED-szalagnál már folyamatban van egy művelet."
    try:
        if sim:
            s = _sim["led"]
            s["on"] = payload["1"] > 0
            if "4" in payload:
                s["brightness"] = round(payload["4"] / 10)
            if "5" in payload:
                s["hue"], s["saturation"] = payload["5"], round(payload["6"] / 10)
            values = dict(s)
        else:
            values = led_command(ip, payload)
        _store_led(values)
        return True, "LED-szalag beállítva."
    except Exception as e:
        _store_led(None, str(e))
        return False, str(e)
    finally:
        _led_lock.release()


def plug_set(data):
    """Konnektor kapcsolása az appból: {"on": bool}. Visszatérés: (siker, üzenet)."""
    if not isinstance(data, dict) or set(data) != {"on"} or not isinstance(data["on"], bool):
        return False, "Hibás adatformátum."
    if not _plug_lock.acquire(blocking=False):
        return False, "A konnektornál már folyamatban van egy művelet."
    try:
        ok, msg = _plug_switch_blocking(data["on"])
    except Exception as e:
        ok, msg = False, str(e)
        _store_plug(None, msg)
    finally:
        _plug_lock.release()
    _set_plug_result(ok, msg)
    log_message(f"[OTTHON] Konnektor kézi kapcsolás: {msg}" if ok else f"[OTTHON] Konnektor kapcsolása sikertelen: {msg}")
    return ok, msg


def _save_schedule(device, log_name, data):
    """Egy napi be/ki időzítő ATOMI mentése a config.json-ba (konnektor, rádió):
    {"enabled": bool, "schedule": [7 × {az eszköz időpont-mezői: "HH:MM"|""}]}. A mezők
    (cfg.HOME_SCHEDULE_KEYS) mind kötelezők: a konnektornál on/off/on2/off2, a rádiónál on/off.
    Hiányzó mező = elutasítás, hogy egy régi (csak on/off-ot küldő) app ne törölje a 2. párt."""
    if not isinstance(data, dict):
        return False, "Hibás adatformátum."
    enabled = data.get("enabled")
    schedule = data.get("schedule")
    if not isinstance(enabled, bool) or not isinstance(schedule, list) or len(schedule) != len(cfg.WEEK_DAYS):
        return False, "Hibás adatformátum."
    parsed = []
    for i, day in enumerate(schedule):
        if not isinstance(day, dict):
            return False, "Hibás adatformátum."
        entry = {}
        for key in cfg.HOME_SCHEDULE_KEYS[device]:
            value = day.get(key)
            if not isinstance(value, str):
                return False, "Hibás adatformátum."
            value = value.strip()
            if value and not _TIME_PATTERN.match(value):
                return False, f"{cfg.WEEK_DAYS[i]}: érvénytelen időpont „{value}” (ÓÓ:PP kell, vagy üres)."
            entry[key] = value
        parsed.append(entry)

    with state_lock:
        block = cfg.HOME_CONFIG[device]
        block["enabled"] = enabled
        for i, entry in enumerate(parsed):
            block["schedule"][i].update(entry)
        cfg.refresh_home_public_state()
    save_config_file()
    log_message(f"[OTTHON] {log_name} időzítő mentve ({'aktív' if enabled else 'kikapcsolva'}).")
    return True, "Mentve."


def update_plug_schedule(data):
    """A konnektor napi időzítőjének mentése, naponta két be-ki párral (lásd _save_schedule)."""
    return _save_schedule("plug", "Konnektor", data)


def update_radio_schedule(data):
    """Az internet-rádió napi időzítőjének mentése (lásd _save_schedule)."""
    return _save_schedule("radio", "Internet-rádió", data)


def radio_learn(data):
    """A power kód tanításának indítása háttérszálban (infra, 30 mp). Kérés: {}.
    Visszatérés: (siker, üzenet)."""
    if not isinstance(data, dict) or data:
        return False, "Hibás adatformátum."
    with state_lock:
        ip = cfg.HOME_CONFIG["radio"]["broadlink_ip"]
        sim = shared_state["simulation"]
    if not ip and not sim:
        return False, "Nincs megadva a rádió BroadLinkjének IP-címe (config.json)."
    if not _radio_lock.acquire(blocking=False):
        return False, "A rádiónál már folyamatban van egy művelet."
    with state_lock:
        shared_state["home"]["radio"]["busy"] = "learn"
    threading.Thread(target=_radio_learn_worker, args=(ip, sim), daemon=True).start()
    return True, (f"Tanítás indult: {dev.LEARN_TIMEOUT_S} másodpercen belül nyomd meg a távirányító "
                  f"power gombját a BroadLink felé fordítva.")


def _radio_learn_worker(ip, sim):
    try:
        log_message(f"[OTTHON] Internet-rádió: power kód tanítása indult ({'szimuláció' if sim else ip}).")
        if sim:
            time.sleep(SIM_LEARN_DELAY_S)
            code = base64.b64encode(b"SIM-radio-power").decode("ascii")
        else:
            code = dev.broadlink_learn(ip)
        with state_lock:
            cfg.HOME_CONFIG["radio"]["ir_power"] = code
            cfg.refresh_home_public_state()
        save_config_file()
        _set_result("radio", True, "Megtanulva: power kód.")
        log_message("[OTTHON] Internet-rádió: power kód megtanulva és elmentve.")
    except Exception as e:
        _set_result("radio", False, f"Tanítás sikertelen: {e}")
        log_message(f"[OTTHON] Internet-rádió: power kód tanítása sikertelen: {e}")
    finally:
        with state_lock:
            shared_state["home"]["radio"]["busy"] = ""
        _radio_lock.release()


def radio_send(data):
    """Egy power gombnyomás (web: próba, app: Be/Ki). Kérés: {}. Visszatérés: (siker, üzenet)."""
    if not isinstance(data, dict) or data:
        return False, "Hibás adatformátum."
    if not _radio_lock.acquire(blocking=False):
        return False, "A rádiónál már folyamatban van egy művelet."
    try:
        ok, msg = _radio_send_blocking()
    except Exception as e:
        ok, msg = False, str(e)
    finally:
        _radio_lock.release()
    _set_result("radio", ok, msg if ok else f"Küldés sikertelen: {msg}")
    log_message(f"[OTTHON] Internet-rádió kézi power gombnyomás: {msg}" if ok
                else f"[OTTHON] Internet-rádió: a power gombnyomás sikertelen: {msg}")
    return ok, msg
