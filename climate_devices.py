"""Klíma-vezérlés eszközrétege: BroadLink IR-adók és Xiaomi légtisztító (hőmérséklet, páratartalom).

Minden függvény BLOKKOLÓ (hálózati I/O): a hívó asyncio.to_thread()-ben vagy külön szálban futtassa.
Minden socket hívásonként nyílik és záródik (a 2026-09-19-i szivárgás tanulsága): a broadlink
könyvtár ezt maga is így csinálja (with/finally), a saját miIO-kliens szintén.
"""
import base64
import hashlib
import json
import random
import socket
import struct
import time

import broadlink
from broadlink.exceptions import ReadError, StorageError
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# --- BroadLink (IR) ---------------------------------------------------------------

BROADLINK_TIMEOUT_S = 5        # egy-egy BroadLink hálózati művelet időkorlátja
LEARN_TIMEOUT_S = 30           # ennyi ideig vár a távirányító gombnyomására tanításkor
LEARN_POLL_S = 1


class ClimateDeviceError(Exception):
    """Érthető, felhasználónak szóló hibaüzenet egy eszközművelet sikertelenségéről."""


def _broadlink_connect(ip):
    """Kapcsolódás egy BroadLink IR-adóhoz IP alapján (hello + auth)."""
    try:
        dev = broadlink.hello(ip, timeout=BROADLINK_TIMEOUT_S)
    except Exception as e:
        raise ClimateDeviceError(f"A BroadLink eszköz ({ip}) nem válaszol: {e}") from e
    if not hasattr(dev, "send_data"):
        raise ClimateDeviceError(
            f"A(z) {ip} címen lévő eszköz nem ismert IR-távirányító "
            f"(típuskód: 0x{dev.devtype:04X}, {dev.model or 'ismeretlen modell'}).")
    if getattr(dev, "is_locked", False):
        raise ClimateDeviceError(
            f"A BroadLink eszköz ({ip}) zárolva van: a BroadLink appban kapcsold ki a "
            f"„Lock device” (eszköz zárolása) beállítást.")
    dev.timeout = BROADLINK_TIMEOUT_S
    try:
        dev.auth()
    except Exception as e:
        raise ClimateDeviceError(f"A BroadLink eszköz ({ip}) hitelesítése sikertelen: {e}") from e
    return dev


def broadlink_reachable(ip):
    """Elérhető-e a BroadLink a hálózaton (csak "hello", hitelesítés és parancs nélkül)."""
    try:
        broadlink.hello(ip, timeout=BROADLINK_TIMEOUT_S)
        return True
    except Exception:
        return False


def broadlink_describe(ip):
    """Az eszköz rövid leírása (modell, típuskód) a felülethez."""
    dev = _broadlink_connect(ip)
    return f"{dev.manufacturer} {dev.model} (0x{dev.devtype:04X})"


def broadlink_send(ip, code_b64):
    """Egy korábban tanított IR-kód kiküldése."""
    try:
        data = base64.b64decode(code_b64, validate=True)
    except Exception as e:
        raise ClimateDeviceError(f"Érvénytelen tárolt IR-kód: {e}") from e
    dev = _broadlink_connect(ip)
    try:
        dev.send_data(data)
    except Exception as e:
        raise ClimateDeviceError(f"Az IR-kód küldése sikertelen ({ip}): {e}") from e


def broadlink_learn(ip, timeout_s=LEARN_TIMEOUT_S):
    """Tanítás: az eszköz figyelő módba lép, a felhasználó megnyomja a távirányító gombját.
    Visszaadja a rögzített kódot base64-ként."""
    dev = _broadlink_connect(ip)
    try:
        dev.enter_learning()
    except Exception as e:
        raise ClimateDeviceError(f"A tanító mód indítása sikertelen ({ip}): {e}") from e
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(LEARN_POLL_S)
        try:
            data = dev.check_data()
        except (ReadError, StorageError):
            continue  # még nem érkezett gombnyomás
        except Exception as e:
            raise ClimateDeviceError(f"A tanítás közben hiba történt ({ip}): {e}") from e
        if data:
            return base64.b64encode(data).decode("ascii")
    raise ClimateDeviceError(f"{timeout_s} másodperc alatt nem érkezett távirányító-jel.")


# --- Xiaomi légtisztító (miIO / MiOT) ---------------------------------------------

MIIO_PORT = 54321
MIIO_TIMEOUT_S = 5
# Xiaomi Smart Air Purifier 4 (zhimi.airp.mb5) MiOT-specifikáció, "environment" szolgáltatás
# (home.miot-spec.com/spec/zhimi.airp.mb5): relative-humidity = siid 3 / piid 1,
# temperature = siid 3 / piid 7.
PURIFIER_PROPERTIES = (
    {"did": "humidity", "siid": 3, "piid": 1},
    {"did": "temperature", "siid": 3, "piid": 7},
)

_MIIO_HELLO = bytes.fromhex("21310020" + "ff" * 28)


def _md5(data):
    return hashlib.md5(data).digest()


def _miio_keys(token):
    key = _md5(token)
    iv = _md5(key + token)
    return key, iv


def miio_encrypt(token, plaintext):
    key, iv = _miio_keys(token)
    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plaintext, 16))


def miio_decrypt(token, ciphertext):
    key, iv = _miio_keys(token)
    return unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext), 16)


def miio_build_packet(token, device_id, stamp, payload):
    """miIO csomag: 32 bájtos fejléc (magic, hossz, 0, eszköz-azonosító, időbélyeg, MD5) + titkosított törzs."""
    body = miio_encrypt(token, payload)
    header = struct.pack(">HHI", 0x2131, 32 + len(body), 0) + device_id + struct.pack(">I", stamp)
    checksum = _md5(header + token + body)
    return header + checksum + body


def miio_parse_packet(token, packet):
    """Válaszcsomag ellenőrzése (MD5) és visszafejtése; a visszafejtett JSON-t adja vissza."""
    if len(packet) < 32 or packet[:2] != b"\x21\x31":
        raise ClimateDeviceError("Érvénytelen válasz a légtisztítótól.")
    length = struct.unpack(">H", packet[2:4])[0]
    body = packet[32:length]
    if _md5(packet[:16] + token + body) != packet[16:32]:
        raise ClimateDeviceError("A légtisztító válaszának ellenőrzőösszege hibás (rossz token?).")
    if not body:
        raise ClimateDeviceError("Üres válasz a légtisztítótól.")
    plain = miio_decrypt(token, body).rstrip(b"\x00")
    return json.loads(plain.decode("utf-8"))


def _parse_token(token_hex):
    try:
        token = bytes.fromhex(token_hex)
    except (ValueError, TypeError) as e:
        raise ClimateDeviceError("A légtisztító tokenje nem érvényes (32 hexadecimális karakter kell).") from e
    if len(token) != 16:
        raise ClimateDeviceError("A légtisztító tokenje nem érvényes (32 hexadecimális karakter kell).")
    return token


def miio_call(ip, token_hex, method, params, timeout=MIIO_TIMEOUT_S):
    """Egy miIO parancs (kézfogás + kérés + válasz) egyetlen, lezárt UDP-sockettel."""
    token = _parse_token(token_hex)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(_MIIO_HELLO, (ip, MIIO_PORT))
            hello, _ = sock.recvfrom(1024)
        except socket.timeout as e:
            raise ClimateDeviceError(f"A légtisztító ({ip}) nem válaszol.") from e
        except OSError as e:
            raise ClimateDeviceError(f"A légtisztító ({ip}) nem érhető el: {e}") from e
        if len(hello) < 32 or hello[:2] != b"\x21\x31":
            raise ClimateDeviceError("Érvénytelen kézfogás-válasz a légtisztítótól.")
        device_id = hello[8:12]
        stamp = struct.unpack(">I", hello[12:16])[0]

        msg_id = random.randint(1, 9999)
        payload = json.dumps({"id": msg_id, "method": method, "params": params},
                             separators=(",", ":")).encode("utf-8")
        deadline = time.monotonic() + timeout
        try:
            sock.sendto(miio_build_packet(token, device_id, stamp + 1, payload), (ip, MIIO_PORT))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout()
                sock.settimeout(remaining)
                raw, _ = sock.recvfrom(4096)
                reply = miio_parse_packet(token, raw)
                if reply.get("id") == msg_id:
                    break  # egy korábbi, késve érkező válasz esetén tovább várunk
        except socket.timeout as e:
            raise ClimateDeviceError(f"A légtisztító ({ip}) nem válaszolt a kérésre (rossz token?).") from e
        except OSError as e:
            raise ClimateDeviceError(f"A légtisztító ({ip}) nem érhető el: {e}") from e
    if "error" in reply:
        raise ClimateDeviceError(f"A légtisztító hibát jelzett: {reply['error']}")
    return reply.get("result")


def purifier_read(ip, token_hex):
    """Hőmérséklet (°C) és páratartalom (%) kiolvasása. Visszatérés: {"temperature": x, "humidity": y}."""
    result = miio_call(ip, token_hex, "get_properties", [dict(p) for p in PURIFIER_PROPERTIES])
    values = {}
    for item in result or []:
        if item.get("code") == 0 and item.get("did") in ("temperature", "humidity"):
            values[item["did"]] = item.get("value")
    if "temperature" not in values or "humidity" not in values:
        raise ClimateDeviceError(f"A légtisztító nem adott hőmérséklet/páratartalom értéket: {result}")
    return {"temperature": float(values["temperature"]), "humidity": float(values["humidity"])}
