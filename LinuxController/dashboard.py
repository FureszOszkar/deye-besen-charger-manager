import json
import re
import secrets
import os
import base64
import hashlib
import hmac as hmac_module
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from config import (
    shared_state, state_lock, log_message,
    HTTP_PORT, DEFAULT_CONFIG, save_config_file,
    WEB_AUTH_ENABLED, WEB_PASSWORD, PBKDF2_ITERATIONS
)

# --- ÜTEMEZÉS VALIDÁCIÓ ---
FORCED_SCHEDULE_DAYS = ["Hétfő", "Kedd", "Szerda", "Csütörtök", "Péntek", "Szombat", "Vasárnap"]
_TIME_PATTERN = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')


def validate_forced_schedule(schedule):
    """Ellenőrzi és normalizálja a kliensből érkező heti ütemezés listát.
    ValueError-t dob, ha a struktúra érvénytelen (rossz típus, hiányzó/duplikált nap,
    hibás időformátum, tartományon kívüli áramerősség), hogy hibás adat sose kerülhessen
    a shared_state-be vagy a config.json-be (ami újraindításkor a program összeomlását
    és a dashboardon tárolt XSS-t is okozhat, ha nincs validálva)."""
    if not isinstance(schedule, list) or len(schedule) != 7:
        raise ValueError("Az ütemezésnek pontosan 7 elemű listának kell lennie.")

    seen_days = set()
    validated = []
    for item in schedule:
        if not isinstance(item, dict):
            raise ValueError("Az ütemezés minden eleme objektum kell legyen.")

        day = item.get("day")
        if day not in FORCED_SCHEDULE_DAYS:
            raise ValueError(f"Érvénytelen nap: {day!r}")
        if day in seen_days:
            raise ValueError(f"Duplikált nap az ütemezésben: {day}")
        seen_days.add(day)

        start = item.get("start")
        stop = item.get("stop")
        if not isinstance(start, str) or not _TIME_PATTERN.match(start):
            raise ValueError(f"Érvénytelen kezdési időpont ({day}): {start!r}")
        if not isinstance(stop, str) or not _TIME_PATTERN.match(stop):
            raise ValueError(f"Érvénytelen befejezési időpont ({day}): {stop!r}")

        try:
            amps = int(item.get("amps", 16))
        except (TypeError, ValueError):
            raise ValueError(f"Érvénytelen áramerősség érték ({day}): {item.get('amps')!r}")
        if not (6 <= amps <= 16):
            raise ValueError(f"Az áramerősség 6-16A között kell legyen ({day}): {amps}")

        validated.append({
            "day": day,
            "enabled": bool(item.get("enabled", False)),
            "start": start,
            "stop": stop,
            "amps": amps,
            "override_auto": bool(item.get("override_auto", True))
        })

    if len(seen_days) != 7:
        raise ValueError("Az ütemezésnek a hét mind a 7 napját pontosan egyszer kell tartalmaznia.")

    return validated


# --- WEBES HITELESÍTÉS ---
SESSION_TTL_SECONDS = 24 * 3600  # Session-ek 24 óra után lejárnak

active_sessions = {}  # {token: {"key": session_key_bytes, "expires": epoch_seconds}}


def get_valid_session(token):
    """Visszaadja a session kulcsát, ha a token létezik és nem járt le; lejárt tokent törli."""
    if not token:
        return None
    session = active_sessions.get(token)
    if session is None:
        return None
    if time.time() > session["expires"]:
        active_sessions.pop(token, None)
        return None
    return session["key"]

# --- PSK TITKOSÍTÁSI MODUL ---
def derive_session_key(password, client_nonce, iterations):
    """PBKDF2-SHA256 kulcsszármaztatás: jelszóból + nonce-ból AES-256 kulcsot gyárt."""
    return hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'),
        client_nonce.encode('utf-8'),
        iterations=iterations, dklen=32
    )

def verify_auth_proof(session_key, received_proof_b64):
    """HMAC-SHA256 hitelesítési bizonyíték ellenőrzése."""
    expected = hmac_module.new(session_key, b"AUTH_PROOF", hashlib.sha256).digest()
    received = base64.b64decode(received_proof_b64)
    return hmac_module.compare_digest(expected, received)

def encrypt_response(session_key, data):
    """AES-256-CBC titkosítás HMAC-SHA256 hitelesítéssel a szerver válaszához (CryptoJS kompatibilis)."""
    iv = os.urandom(16)
    cipher = AES.new(session_key, AES.MODE_CBC, iv)
    plaintext = json.dumps(data).encode('utf-8')
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
    # Encrypt-then-MAC: HMAC(IV + Ciphertext)
    mac = hmac_module.new(session_key, iv + ciphertext, hashlib.sha256).digest()
    return {
        "iv": base64.b64encode(iv).decode('ascii'),
        "data": base64.b64encode(ciphertext).decode('ascii'),
        "mac": base64.b64encode(mac).decode('ascii'),
        "enc": True
    }

def decrypt_request(session_key, encrypted_data):
    """AES-256-CBC visszafejtés HMAC-SHA256 ellenőrzéssel a kliens kéréseihez."""
    iv = base64.b64decode(encrypted_data["iv"])
    ciphertext = base64.b64decode(encrypted_data["data"])
    received_mac = base64.b64decode(encrypted_data["mac"])
    
    # Verify MAC first
    expected_mac = hmac_module.new(session_key, iv + ciphertext, hashlib.sha256).digest()
    if not hmac_module.compare_digest(expected_mac, received_mac):
        raise ValueError("MAC ellenőrzés sikertelen (Adat módosult vagy hibás kulcs)!")
        
    cipher = AES.new(session_key, AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return json.loads(plaintext.decode('utf-8'))

# --- HTML SABLONOK ---

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
    <script src="/crypto-js.min.js"></script>
    <script>
        const PBKDF2_ITERATIONS = {{PBKDF2_ITERATIONS}};

        document.getElementById('login-form').addEventListener('submit', function(e) {
            e.preventDefault();
            const password = document.getElementById('password').value;
            const errBox = document.getElementById('error-msg');
            const btn = document.querySelector('.btn-login');
            btn.disabled = true;
            btn.innerText = 'Titkosítás...';

            // Mivel a CryptoJS PBKDF2 szinkron és tovább tart, setTimeout-tal aszinkronná tesszük a UI frissítése miatt
            setTimeout(async function() {
                try {
                    // 1. Véletlenszerű nonce generálása (CryptoJS random)
                    const clientNonceWordArray = CryptoJS.lib.WordArray.random(16);
                    const clientNonce = CryptoJS.enc.Hex.stringify(clientNonceWordArray);

                    // 2. Kulcsszármaztatás (PBKDF2 - szoftveres)
                    const salt = CryptoJS.enc.Utf8.parse(clientNonce);
                    const key = CryptoJS.PBKDF2(password, salt, {
                        keySize: 256 / 32, // 256 bit = 8 words
                        iterations: PBKDF2_ITERATIONS,
                        hasher: CryptoJS.algo.SHA256
                    });
                    const sessionKeyStr = CryptoJS.enc.Hex.stringify(key);

                    // 3. Hitelesítési bizonyíték (HMAC)
                    const authProof = CryptoJS.HmacSHA256("AUTH_PROOF", key);
                    const authProofB64 = CryptoJS.enc.Base64.stringify(authProof);

                    // 4. Kulcs mentése a sessionStorage-ba (a dashboard-nak)
                    sessionStorage.setItem('_psk_key_hex', sessionKeyStr);
                    sessionStorage.setItem('_psk_nonce', clientNonce);

                    // 5. Küldés: jelszó SOSEM utazik a hálózaton!
                    const res = await fetch('/api/login', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ clientNonce: clientNonce, authProof: authProofB64 })
                    });
                    const data = await res.json();

                    if (data.status === 'success') {
                        window.location.reload();
                    } else {
                        sessionStorage.removeItem('_psk_key_hex');
                        sessionStorage.removeItem('_psk_nonce');
                        errBox.style.display = 'block';
                        errBox.innerText = data.message || 'Helytelen jelszó!';
                        document.getElementById('password').value = '';
                        btn.disabled = false;
                        btn.innerText = 'Belépés';
                    }
                } catch (err) {
                    console.error(err);
                    sessionStorage.removeItem('_psk_key_hex');
                    sessionStorage.removeItem('_psk_nonce');
                    errBox.style.display = 'block';
                    errBox.innerText = 'Hiba a titkosítási folyamatban!';
                    btn.disabled = false;
                    btn.innerText = 'Belépés';
                }
            }, 50); // Kis késleltetés, hogy a UI frissüljön
        });
    </script>

</body>
</html>"""

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

        .mobile-only-break, .show-mobile {
            display: none;
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
            transition: background 0.1s;
        }

        /* Számszerű beviteli mezők aktív/inaktív színezése */
        .input-inactive {
            border-color: rgba(255, 255, 255, 0.08) !important;
            color: var(--text-muted) !important;
        }
        .input-active {
            border-color: var(--primary) !important;
            color: var(--primary) !important;
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
            .mobile-only-break {
                display: inline !important;
            }
            .hide-mobile {
                display: none !important;
            }
            .show-mobile {
                display: inline !important;
            }
            #active-charging-view {
                flex-wrap: nowrap !important;
                gap: 0.5rem !important;
            }
            #active-charging-view > div:first-child {
                flex: 1.4 !important;
                min-width: 0 !important;
                max-width: none !important;
            }
            #active-charging-view > div:last-child {
                flex: 0.6 !important;
                min-width: 0 !important;
                max-width: none !important;
                padding: 0.5rem 0.3rem !important;
            }
            #charging-power-val {
                font-size: 1.4rem !important;
            }
            .console-box {
                height: 360px !important;
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
            <div class="alert-box alert-error" id="lockdown-box" style="display: none;">
                Túl gyakori töltés ki-be kapcsolás miatt a következő parancs csak feloldás után érhető el. 
                <button class="btn btn-primary" style="margin-left: 10px; padding: 5px 10px; font-size: 0.9em;" onclick="unlockLockdown()">Tiltás feloldása</button>
            </div>
 
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
                        <div class="input-group">
                            <label for="auto_start_soc" style="display: inline-flex; align-items: center;">
                                Indítási akku szint (%)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Az a minimális otthoni akkumulátor töltöttség (SoC %), ami felett a napelemes töltés elindulhat.</span></span>
                            </label>
                            <input type="number" id="auto_start_soc" min="1" max="100">
                        </div>
                        <div class="input-group">
                            <label for="auto_stop_soc" style="display: inline-flex; align-items: center;">
                                Leállítási akku szint (%)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Az a minimális otthoni akkumulátor töltöttség (SoC %), ami alatt a napelemes töltés leáll (hogy ne merítse le túlságosan az akkumulátort). A 0% kikapcsolja ezt a korlátot.</span></span>
                            </label>
                            <input type="number" id="auto_stop_soc" min="0" max="100" oninput="updateInputStatus('auto_stop_soc')">
                        </div>
                        <div class="input-group">
                            <label for="auto_stop_import_limit" style="display: inline-flex; align-items: center;">
                                Hálózati fogyasztás küszöbérték (W)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">A hálózatból vételezett (importált) áram azon szintje, ami felett a leállítási időzítő elindul. A 0 W kikapcsolja ezt a korlátot.</span></span>
                            </label>
                            <input type="number" id="auto_stop_import_limit" min="0" max="10000" step="1" oninput="updateInputStatus('auto_stop_import_limit')">
                        </div>
                        <div class="input-group">
                            <label for="auto_grid_charge_duration_minutes" style="display: inline-flex; align-items: center;">
                                Hálózati töltés késleltetett leállítása (perc)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Ha a hálózati fogyasztás meghaladja a küszöbértéket, ennyi ideig engedi még a töltést futni. A 0 perc azonnali leállítást jelent.</span></span>
                            </label>
                            <input type="number" id="auto_grid_charge_duration_minutes" min="0" max="1440" oninput="updateInputStatus('auto_grid_charge_duration_minutes')">
                        </div>
                        <div class="input-group" style="grid-column: span 2;">
                            <label for="auto_house_power_limit_w" style="display: inline-flex; align-items: center;">
                                Teljes ház terhelési korlát (UPS + House) (W)
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">Az UPS kimenet és a külső hálózati ág összege. A védelem kikapcsolásához írj be 0-t.</span></span>
                            </label>
                            <input type="number" id="auto_house_power_limit_w" min="0" max="20000" step="1">
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
                <!-- AKTÍV TÖLTÉS NÉZET -->
                <div id="active-charging-view" style="display: flex; gap: 1rem; flex-wrap: wrap; align-items: stretch; margin-bottom: 0.8rem;">
                    <div style="flex: 1.5; min-width: 280px; max-width: 450px;">
                        <table class="phase-table" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th>Fázis</th>
                                    <th>Feszültség</th>
                                    <th>
                                        Mért töltőáram <span class="hide-mobile">(Visszacsatolás)</span>
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
                    </div>
                    <div style="flex: 1; min-width: 200px; max-width: 250px; display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 1rem; background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 8px; box-sizing: border-box;">
                        <div style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem; text-align: center; line-height: 1.2;">
                            <span class="charging-power-title-line">Töltési</span><br class="mobile-only-break">
                            <span class="charging-power-title-line">teljesítmény
                                <span class="tooltip-container">ⓘ<span class="tooltip-text">A három fázis pillanatnyi teljesítményének összege kilowattban (kW).</span></span>
                            </span>
                        </div>
                        <div id="charging-power-val" style="font-size: 2.2rem; font-weight: 700; color: var(--primary);">0.00 kW</div>
                    </div>
                </div>

                <!-- UTOLSÓ TÖLTÉS ÖSSZESÍTŐ NÉZET -->
                <div id="last-charge-summary-view" style="display: none; gap: 1rem; flex-wrap: wrap; align-items: stretch; margin-bottom: 0.8rem;">
                    <!-- Feszültség táblázat (mindig látható feszültségek) -->
                    <div style="flex: 1; min-width: 150px; max-width: 220px;">
                        <table class="phase-table" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th>Fázis</th>
                                    <th>Feszültség</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td>L1</td>
                                    <td id="v1-inactive">0.0 V</td>
                                </tr>
                                <tr>
                                    <td>L2</td>
                                    <td id="v2-inactive">0.0 V</td>
                                </tr>
                                <tr>
                                    <td>L3</td>
                                    <td id="v3-inactive">0.0 V</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <!-- Legutóbbi töltés összesítése -->
                    <div style="flex: 2; min-width: 280px; display: flex; flex-direction: column; justify-content: center; padding: 1rem; background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 8px; box-sizing: border-box;">
                        <div style="font-size: 0.85rem; color: var(--primary); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.8rem; border-bottom: 1px solid rgba(255, 255, 255, 0.08); padding-bottom: 0.4rem;">
                            Legutóbbi saját töltés összesítése
                        </div>
                        <table style="width: 100%; font-size: 0.9rem; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 0.3rem 0; color: var(--text-muted);">Indítás ideje:</td>
                                <td id="last-charge-start" style="padding: 0.3rem 0; font-weight: 600; text-align: right;">-</td>
                            </tr>
                            <tr>
                                <td style="padding: 0.3rem 0; color: var(--text-muted);">Időtartam:</td>
                                <td id="last-charge-duration" style="padding: 0.3rem 0; font-weight: 600; text-align: right;">-</td>
                            </tr>
                            <tr>
                                <td style="padding: 0.3rem 0; color: var(--text-muted);">Betöltött energia:</td>
                                <td id="last-charge-energy" style="padding: 0.3rem 0; font-weight: 600; text-align: right; color: var(--success);">-</td>
                            </tr>
                            <tr>
                                <td style="padding: 0.3rem 0; color: var(--text-muted);">Leállítás oka:</td>
                                <td id="last-charge-reason" style="padding: 0.3rem 0; font-weight: 600; text-align: right;">-</td>
                            </tr>
                        </table>
                    </div>
                </div>
                <div style="margin-top: 0.8rem; display: flex; flex-direction: column; gap: 0.4rem; font-size: 0.85rem; color: var(--text-muted);">
                    <div style="display: flex; justify-content: space-between;">
                        <div>
                            <span class="hide-mobile">Töltési energia összesen</span><span class="show-mobile">Összes energia</span>:
                            <span class="tooltip-container">ⓘ<span class="tooltip-text">Az aktuális vagy legutóbbi töltési ciklus során az autóba töltött összes energiamennyiség kilowattórában.</span></span>
                            <span id="energy-total" style="color: var(--text-color); font-weight: 600; margin-left: 0.2rem;">0.00 kWh</span>
                        </div>
                        <div>
                            <span class="hide-mobile">Töltő belső hőmérséklet</span><span class="show-mobile">Belső hőfok</span>:
                            <span class="tooltip-container">ⓘ<span class="tooltip-text">A BESEN autótöltő burkolatán belüli elektronika hőmérséklete.</span></span>
                            <span id="temp-internal" style="color: var(--text-color); font-weight: 600; margin-left: 0.2rem;">0.0 °C</span>
                        </div>
                    </div>
                    <div style="display: flex; justify-content: space-between; border-top: 1px solid rgba(255, 255, 255, 0.08); padding-top: 0.4rem; margin-top: 0.2rem;">
                        <div>
                            <span class="hide-mobile">Töltő kapcsolata (BLE)</span><span class="show-mobile">Kapcsolat (BLE)</span>:
                            <span class="tooltip-container">ⓘ<span class="tooltip-text">A vezérlő szoftver és az autótöltő közötti Bluetooth (BLE) kapcsolat élő állapota (Csatlakoztatva / Nincs csatlakoztatva).</span></span>
                            <span id="charger-connection-status" style="font-weight: 600; margin-left: 0.2rem;">Betöltés...</span>
                        </div>
                        <div>
                            <span class="hide-mobile">Kábel & Töltés állapota</span><span class="show-mobile">Állapot</span>:
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

    <script src="/crypto-js.min.js"></script>
    <script>
        // === PSK TITKOSÍTÁSI MODUL (Kliens oldal, CryptoJS alapú AES-CBC + HMAC) ===
        let _sessionKeyHex = null;

        // SessionKey betöltése a sessionStorage-ból (a login oldal mentette oda)
        (function initPSK() {
            const storedKey = sessionStorage.getItem('_psk_key_hex');
            if (storedKey) {
                _sessionKeyHex = storedKey;
            }
        })();

        function _pskEncrypt(sessionKeyHex, plaintext) {
            const iv = CryptoJS.lib.WordArray.random(16);
            const key = CryptoJS.enc.Hex.parse(sessionKeyHex);
            const encrypted = CryptoJS.AES.encrypt(plaintext, key, {
                iv: iv,
                mode: CryptoJS.mode.CBC,
                padding: CryptoJS.pad.Pkcs7
            });
            const ciphertext = encrypted.ciphertext;
            const macData = iv.clone().concat(ciphertext);
            const mac = CryptoJS.HmacSHA256(macData, key);
            
            return JSON.stringify({
                iv: CryptoJS.enc.Base64.stringify(iv),
                data: CryptoJS.enc.Base64.stringify(ciphertext),
                mac: CryptoJS.enc.Base64.stringify(mac),
                enc: true
            });
        }

        function _pskDecrypt(sessionKeyHex, ivB64, dataB64, macB64) {
            const iv = CryptoJS.enc.Base64.parse(ivB64);
            const ciphertext = CryptoJS.enc.Base64.parse(dataB64);
            const receivedMac = CryptoJS.enc.Base64.parse(macB64);
            const key = CryptoJS.enc.Hex.parse(sessionKeyHex);
            
            // HMAC ellenőrzése
            const macData = iv.clone().concat(ciphertext);
            const expectedMac = CryptoJS.HmacSHA256(macData, key);
            if (expectedMac.toString() !== receivedMac.toString()) {
                throw new Error("MAC ellenőrzés sikertelen!");
            }
            
            const cipherParams = CryptoJS.lib.CipherParams.create({ ciphertext: ciphertext });
            const decrypted = CryptoJS.AES.decrypt(cipherParams, key, {
                iv: iv,
                mode: CryptoJS.mode.CBC,
                padding: CryptoJS.pad.Pkcs7
            });
            return JSON.parse(decrypted.toString(CryptoJS.enc.Utf8));
        }

        // Globális fetch() felülírása: átlátszó titkosítás/visszafejtés
        const _originalFetch = window.fetch;
        window.fetch = async function(url, options = {}) {
            // Login és statikus fájlok: nem titkosítjuk
            if (url === '/api/login' || url === '/background.png' || url === '/crypto-js.min.js') {
                return _originalFetch(url, options);
            }

            // Kimenő body titkosítása (POST kérések)
            if (options.body && _sessionKeyHex) {
                const encryptedBody = _pskEncrypt(_sessionKeyHex, options.body);
                options.body = encryptedBody;
                if (!options.headers) options.headers = {};
                options.headers['Content-Type'] = 'application/json';
            }

            // Eredeti fetch hívás
            const response = await _originalFetch(url, options);

            // 401 kezelés: kijelentkeztetés
            if (response.status === 401) {
                sessionStorage.removeItem('_psk_key_hex');
                sessionStorage.removeItem('_psk_nonce');
                window.location.reload();
                return response;
            }

            // Válasz visszafejtése, ha titkosított
            if (_sessionKeyHex && response.ok) {
                try {
                    const cloned = response.clone();
                    const json = await cloned.json();
                    if (json && json.enc && json.iv && json.data && json.mac) {
                        const decrypted = _pskDecrypt(_sessionKeyHex, json.iv, json.data, json.mac);
                        return new Response(JSON.stringify(decrypted), {
                            status: response.status,
                            statusText: response.statusText,
                            headers: response.headers
                        });
                    }
                } catch (e) {
                    // Nem JSON vagy nem titkosított válasz: eredeti response visszaadása
                }
            }

            return response;
        };
        // === PSK MODUL VÉGE ===

        let configLoaded = false;
        let currentConfig = {};
        let originalAutoAmps = 16;
        let originalForceAmps = 16;

        function updateInputStatus(inputId) {
            const input = document.getElementById(inputId);
            if (!input) return;
            const val = parseInt(input.value) || 0;
            
            if (val === 0) {
                input.classList.add('input-inactive');
                input.classList.remove('input-active');
            } else {
                input.classList.remove('input-inactive');
                input.classList.add('input-active');
            }
        }

        function formatPower(val, isGrid, isBattery) {
            let unitMain = " kW";
            let unitSub = " W";
            
            let mainStr = (val / 1000).toFixed(3) + unitMain;
            let subStr = "(" + val + unitSub + ")";
            
            return { main: mainStr, sub: subStr };
        }

        function updateSliderBackground(slider) {
            const min = parseFloat(slider.min) || 0;
            const max = parseFloat(slider.max) || 100;
            const val = parseFloat(slider.value) || 0;
            const percentage = ((val - min) / (max - min)) * 100;
            
            slider.style.background = `linear-gradient(to right, var(--primary) ${percentage}%, rgba(255, 255, 255, 0.1) ${percentage}%)`;
        }

        function togglePersistCheckbox() {
            const cb = document.getElementById('auto_persist_mode_on_restart');
            cb.checked = !cb.checked;
        }

        function renderSchedule(scheduleList) {
            const container = document.getElementById('schedule-rows-container');
            container.innerHTML = '';
            if (!scheduleList || scheduleList.length === 0) return;
            
            scheduleList.forEach((sched, index) => {
                const row = document.createElement('div');
                row.className = 'schedule-row';
                
                row.innerHTML = `
                    <label class="sched-day-label"></label>
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
                // A napnevet textContent-tel írjuk be (nem innerHTML-be interpolálva),
                // hogy egy esetleg mégis átcsúszó rosszindulatú 'day' érték se hajtódjon végre HTML/script-ként.
                row.querySelector('.sched-day-label').textContent = sched.day;
                container.appendChild(row);
            });
        }

        // Közös POST-segéd az áramerősség-mentésekhez: ellenőrzi a HTTP-státuszt ÉS a szerver
        // válaszát is, hiba esetén pedig LÁTHATÓ hibaüzenetet ad (korábban a hibák csak a
        // böngészőkonzolba kerültek, így a felhasználó nem tudta meg, hogy a mentés meghiúsult).
        // Visszatérés: true = sikeres mentés, false = sikertelen.
        async function postAmpsConfig(payload, failContext) {
            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const result = await response.json().catch(() => null);
                if (!response.ok || !result || result.status !== 'success') {
                    const detail = (result && result.message) ? result.message : ('HTTP ' + response.status);
                    alert('HIBA: ' + failContext + ' nem sikerült! (' + detail + ')');
                    return false;
                }
                return true;
            } catch (err) {
                console.error(err);
                alert('HIBA: ' + failContext + ' nem sikerült! (hálózati hiba, a szerver nem érhető el)');
                return false;
            }
        }

        // Csúszka-mentések késleltetése (debounce) és sorosítása:
        // - a debounce miatt húzás közben NEM indul minden lépésnél külön kérés, csak megálláskor;
        // - a mentések láncolása (promise chain) garantálja, hogy egyszerre csak egy kérés fut,
        //   és a küldés pillanatában mindig a csúszka AKTUÁLIS értéke megy ki. Korábban a húzás
        //   minden lépése párhuzamos kérést indított, amelyek sorrendje nem volt garantált --
        //   így előfordulhatott, hogy egy köztes érték (pl. 15A) érkezett meg utolsóként a
        //   szerverre, és az maradt elmentve a ténylegesen beállított érték helyett.
        let ampsSaveTimers = { auto: null, force: null };
        let ampsSaveChain = Promise.resolve();

        function scheduleAmpsSave(mode) {
            clearTimeout(ampsSaveTimers[mode]);
            ampsSaveTimers[mode] = setTimeout(() => {
                ampsSaveChain = ampsSaveChain.then(() => {
                    const unmanaged = document.getElementById(mode + '_unmanaged_current').checked;
                    const slider = document.getElementById(mode + '_charger_max_amps');
                    const val = unmanaged ? 0 : parseInt(slider.value);
                    return (mode === 'auto') ? saveAutoAmpsSilent(val) : saveForceAmpsSilent(val);
                });
            }, 400);
        }

        async function saveAutoAmpsSilent(val) {
            const ok = await postAmpsConfig({
                start_soc: currentConfig.start_soc,
                stop_soc: currentConfig.stop_soc,
                stop_import_limit: currentConfig.stop_import_limit,
                grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                house_power_limit_w: currentConfig.house_power_limit_w,
                persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                charger_max_amps: val,
                force_submode: currentConfig.force_submode,
                schedule_solar_auto: currentConfig.schedule_solar_auto,
                forced_schedule: currentConfig.forced_schedule,
                reset_limit: true
            }, 'az áramerősség mentése');
            if (ok) {
                originalAutoAmps = val;
                currentConfig.charger_max_amps = val;
            }
        }

        async function saveForceAmpsSilent(val) {
            const ok = await postAmpsConfig({
                start_soc: currentConfig.start_soc,
                stop_soc: currentConfig.stop_soc,
                stop_import_limit: currentConfig.stop_import_limit,
                grid_charge_duration_minutes: currentConfig.grid_charge_duration_minutes,
                house_power_limit_w: currentConfig.house_power_limit_w,
                persist_mode_on_restart: currentConfig.persist_mode_on_restart,
                charger_max_amps: val,
                force_submode: currentConfig.force_submode,
                schedule_solar_auto: currentConfig.schedule_solar_auto,
                forced_schedule: currentConfig.forced_schedule,
                reset_limit: true
            }, 'az áramerősség mentése');
            if (ok) {
                originalForceAmps = val;
                currentConfig.charger_max_amps = val;
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
                scheduleAmpsSave('auto');
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
                scheduleAmpsSave('force');
            }
        }

        async function applyForceAmpsWithRestart() {
            const slider = document.getElementById('force_charger_max_amps');
            const val = parseInt(slider.value);

            const ok = await postAmpsConfig({
                start_soc: currentConfig.start_soc,
                stop_soc: currentConfig.stop_soc,
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
            }, 'a töltés közbeni áram-módosítás alkalmazása');
            if (ok) {
                originalForceAmps = val;
                currentConfig.charger_max_amps = val;
                const container = document.getElementById('force-apply-container');
                if (container) container.style.display = 'none';
                updateStatus();
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
                // A mentés a közös, sorosított ütemezőn keresztül megy, hogy ne versenyezhessen
                // egy éppen függőben lévő csúszka-mentéssel
                scheduleAmpsSave(mode);
                const applyContainer = document.getElementById(mode + '-apply-container');
                if (applyContainer) applyContainer.style.display = 'none';
            }
        }

        async function applyAutoAmps(withStop) {
            const charger_max_amps = parseInt(document.getElementById('auto_charger_max_amps').value);
            const ok = await postAmpsConfig({
                start_soc: currentConfig.start_soc,
                stop_soc: currentConfig.stop_soc,
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
            }, 'a töltés közbeni áram-módosítás alkalmazása');
            if (ok) {
                originalAutoAmps = charger_max_amps;
                currentConfig.charger_max_amps = charger_max_amps;
                document.getElementById('auto-apply-container').style.display = 'none';
                updateStatus();
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
                        stop_soc: currentConfig.stop_soc,
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
                        stop_soc: currentConfig.stop_soc,
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
                        stop_soc: currentConfig.stop_soc,
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
                        stop_soc: currentConfig.stop_soc,
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
                currentConfig.stop_soc = data.stop_soc;
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
                gridVal.className = "metric-value";

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
                batVal.className = "metric-value";

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

                // Pillanatnyi töltési teljesítmény számítása
                const p1 = data.voltages[0] * data.currents[0];
                const p2 = data.voltages[1] * data.currents[1];
                const p3 = data.voltages[2] * data.currents[2];
                const totalPowerKW = (p1 + p2 + p3) / 1000.0;

                const powerEl = document.getElementById('charging-power-val');
                if (powerEl) {
                    powerEl.innerText = totalPowerKW.toFixed(2) + ' kW';
                }

                // Fázis adatok
                document.getElementById('v1').innerText = data.voltages[0].toFixed(1) + ' V';
                document.getElementById('i1').innerText = data.currents[0].toFixed(2) + ' A';
                document.getElementById('v2').innerText = data.voltages[1].toFixed(1) + ' V';
                document.getElementById('i2').innerText = data.currents[1].toFixed(2) + ' A';
                document.getElementById('v3').innerText = data.voltages[2].toFixed(1) + ' V';
                document.getElementById('i3').innerText = data.currents[2].toFixed(2) + ' A';

                const v1Inactive = document.getElementById('v1-inactive');
                if (v1Inactive) v1Inactive.innerText = data.voltages[0].toFixed(1) + ' V';
                const v2Inactive = document.getElementById('v2-inactive');
                if (v2Inactive) v2Inactive.innerText = data.voltages[1].toFixed(1) + ' V';
                const v3Inactive = document.getElementById('v3-inactive');
                if (v3Inactive) v3Inactive.innerText = data.voltages[2].toFixed(1) + ' V';

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
                
                const activeView = document.getElementById('active-charging-view');
                const lastChargeView = document.getElementById('last-charge-summary-view');

                if (isCharging) {
                    if (activeView) activeView.style.display = 'flex';
                    if (lastChargeView) lastChargeView.style.display = 'none';
                } else if (data.last_charge && data.last_charge.session_id) {
                    if (activeView) activeView.style.display = 'none';
                    if (lastChargeView) lastChargeView.style.display = 'flex';

                    let startTimeStr = "-";
                    const sid = data.last_charge.session_id;
                    if (sid && sid.length >= 12) {
                        startTimeStr = sid.substring(0, 4) + "-" + sid.substring(4, 6) + "-" + sid.substring(6, 8) + " " + sid.substring(8, 10) + ":" + sid.substring(10, 12);
                    }

                    let durStr = "-";
                    const dur = data.last_charge.duration;
                    if (dur !== undefined) {
                        const hrs = Math.floor(dur / 3600);
                        const mins = Math.floor((dur % 3600) / 60);
                        const secs = dur % 60;
                        durStr = hrs + "ó " + mins + "p " + secs + "mp";
                    }

                    let energyStr = "-";
                    const eng = data.last_charge.energy;
                    if (eng !== undefined) {
                        energyStr = (eng / 1000.0).toFixed(2) + ' kWh';
                    }

                    let reasonStr = data.last_charge.stop_reason || "-";
                    const reasonMap = {
                        "soft_stop": "Szoftveresen leállítva",
                        "ManualStop": "Kézzel leállítva",
                        "SOCLimitReached": "SOC limit elérve",
                        "TimeLimitReached": "Időkeret elérve",
                        "UnderCurrent": "Áramerősség túl alacsony",
                        "PowerLimitReached": "Fogyasztási limit elérve",
                        "PlugPulled": "Kábel kihúzva",
                        "Finished": "Töltés befejeződött",
                        "Card": "Kártyás leállítás",
                        "App": "App leállítás",
                        "Emergency": "Vészleállítás",
                        "OverTemperature": "Túlmelegedés",
                        "OverCurrent": "Túláram",
                        "OverVoltage": "Túlfeszültség",
                        "UnderVoltage": "Alacsony feszültség",
                        "GroundFault": "Földelési hiba",
                        "RelayWeld": "Relé beragadás",
                        "DiodeFault": "Dióda hiba"
                    };
                    if (reasonMap[reasonStr]) reasonStr = reasonMap[reasonStr];

                    const elStart = document.getElementById('last-charge-start');
                    if (elStart) elStart.innerText = startTimeStr;
                    const elDur = document.getElementById('last-charge-duration');
                    if (elDur) elDur.innerText = durStr;
                    const elEnergy = document.getElementById('last-charge-energy');
                    if (elEnergy) elEnergy.innerText = energyStr;
                    const elReason = document.getElementById('last-charge-reason');
                    if (elReason) elReason.innerText = reasonStr;
                } else {
                    if (activeView) activeView.style.display = 'flex';
                    if (lastChargeView) lastChargeView.style.display = 'none';
                }

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
                    document.getElementById('auto_stop_soc').value = data.stop_soc || 0;
                    updateInputStatus('auto_stop_soc');
                    
                    document.getElementById('auto_stop_import_limit').value = data.stop_import_limit;
                    updateInputStatus('auto_stop_import_limit');
                    
                    document.getElementById('auto_grid_charge_duration_minutes').value = data.grid_charge_duration_minutes;
                    updateInputStatus('auto_grid_charge_duration_minutes');
                    
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

                    // Összes meglévő csúszka háttér kitöltésének beállítása (pl. töltőáram csúszka)
                    document.querySelectorAll('input[type="range"]').forEach(updateSliderBackground);

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
                const lockBox = document.getElementById('lockdown-box');
                const now = Date.now() / 1000;
                
                let isLockedOrCooldown = false;

                if (data.lockdown_active) {
                    lockBox.style.display = "block";
                    coolBox.style.display = "none";
                    isLockedOrCooldown = true;
                } else {
                    lockBox.style.display = "none";
                    if (data.cooldown_until > now) {
                        const diff = Math.round(data.cooldown_until - now);
                        coolBox.innerText = `Próbálkozás késleltetve (lehűlési idő): még ${diff} mp...`;
                        coolBox.style.display = "block";
                        isLockedOrCooldown = true;
                    } else {
                        coolBox.style.display = "none";
                    }
                }

                // Gombok tiltása, de a Hard Stop aktív marad
                const forceStartBtn = document.querySelector('button[onclick="manualAction(\\'manual_start\\')"]');
                if (forceStartBtn) {
                    forceStartBtn.disabled = isLockedOrCooldown;
                    forceStartBtn.style.opacity = isLockedOrCooldown ? "0.5" : "1";
                    forceStartBtn.style.cursor = isLockedOrCooldown ? "not-allowed" : "pointer";
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

        async function unlockLockdown() {
            try {
                const res = await fetch('/api/unlock', { method: 'POST' });
                if (res.ok) {
                    updateStatus();
                }
            } catch (e) {
                console.error("Hiba a feloldás során:", e);
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
            const start_soc = parseInt(document.getElementById('auto_start_soc').value) || 100;
            const stop_soc = parseInt(document.getElementById('auto_stop_soc').value) || 0;
            const stop_import_limit = parseInt(document.getElementById('auto_stop_import_limit').value) || 0;
            const grid_charge_duration_minutes = parseInt(document.getElementById('auto_grid_charge_duration_minutes').value) || 0;
            const house_power_limit_w = parseInt(document.getElementById('auto_house_power_limit_w').value) || 0;
            const persist_mode_on_restart = document.getElementById('auto_persist_mode_on_restart').checked;
            const charger_max_amps = document.getElementById('auto_unmanaged_current').checked ? 0 : parseInt(document.getElementById('auto_charger_max_amps').value);

            if (start_soc < stop_soc) {
                alert("Hiba: A Start % (" + start_soc + "%) nem lehet kisebb a Leállítási küszöbnél (" + stop_soc + "%)!");
                return;
            }

            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_soc,
                        stop_soc,
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

        // Csúszkák húzásakor automatikusan frissítjük a hátterüket
        document.addEventListener('input', (e) => {
            if (e.target.type === 'range') {
                updateSliderBackground(e.target);
            }
        });

        // Csúszka sávra való kattintás/koppintás javítása
        document.addEventListener('click', (e) => {
            const slider = e.target.closest('input[type="range"]');
            if (!slider) return;
            
            const rect = slider.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const width = rect.width;
            
            const min = parseFloat(slider.min) || 0;
            const max = parseFloat(slider.max) || 100;
            const step = parseFloat(slider.step) || 1;
            
            let pct = clickX / width;
            if (pct < 0) pct = 0;
            if (pct > 1) pct = 1;
            
            let rawVal = min + pct * (max - min);
            let steppedVal = Math.round(rawVal / step) * step;
            if (steppedVal < min) steppedVal = min;
            if (steppedVal > max) steppedVal = max;
            
            if (parseFloat(slider.value) !== steppedVal) {
                slider.value = steppedVal;
                slider.dispatchEvent(new Event('input', { bubbles: true }));
                slider.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });

        showSection('auto');
        setInterval(updateStatus, 2000);
        updateStatus();
    </script>
</body>
</html>"""


# --- HTTP KEZELŐ OSZTÁLY ---

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
        return get_valid_session(session_token) is not None

    def get_session_key(self):
        """Visszaadja az aktuális session titkosítási kulcsát (lejárt session esetén None-t)."""
        session_token = self.get_cookie('session')
        return get_valid_session(session_token)

    def _read_encrypted_body(self):
        """POST body olvasása és visszafejtése, ha titkosított."""
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data.decode('utf-8'))
        if data.get("enc"):
            session_key = self.get_session_key()
            if session_key:
                return decrypt_request(session_key, data)
            raise ValueError("Titkosított kérés érvényes session kulcs nélkül")
        return data

    def _send_encrypted_json(self, data, status=200, extra_headers=None):
        """JSON válasz küldése, titkosítva ha van session kulcs."""
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        session_key = self.get_session_key()
        if session_key and WEB_AUTH_ENABLED:
            response_data = encrypt_response(session_key, data)
        else:
            response_data = data
        self.wfile.write(json.dumps(response_data).encode('utf-8'))

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
            
        if self.path == '/crypto-js.min.js':
            import os
            import sys
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            crypto_path = os.path.join(base_dir, 'crypto-js.min.js')
            if os.path.exists(crypto_path):
                self.send_response(200)
                self.send_header('Content-type', 'application/javascript; charset=utf-8')
                self.end_headers()
                with open(crypto_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "Fájl nem található")
            return

        if self.path == '/api/login_info':
            # Autentikáció nélkül elérhető: a kliensnek (webes login oldal, Android widget)
            # ismernie kell a PBKDF2 iterációszámot a session kulcs származtatásához, MÉG a
            # bejelentkezés előtt. Nem titkos érték, a LOGIN_HTML is tartalmazza ugyanezt.
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"pbkdf2_iterations": PBKDF2_ITERATIONS}).encode('utf-8'))
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
                self.wfile.write(LOGIN_HTML.replace('{{PBKDF2_ITERATIONS}}', str(PBKDF2_ITERATIONS)).encode('utf-8'))
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
            with state_lock:
                status_data = json.loads(json.dumps(shared_state))
            self._send_encrypted_json(status_data)
        else:
            self.send_error(404, "Fájl nem található")

    def do_POST(self):
        global shared_state
        
        # Bejelentkezés kezelése - PSK Challenge-Response (jelszó SOSEM utazik a hálózaton)
        if self.path == '/api/login':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                login_data = json.loads(post_data.decode('utf-8'))
                client_nonce = login_data.get("clientNonce")
                auth_proof = login_data.get("authProof")
                
                if client_nonce and auth_proof:
                    # Kulcsszármaztatás a szerveren tárolt jelszóból + kliens nonce-ból
                    session_key = derive_session_key(WEB_PASSWORD, client_nonce, PBKDF2_ITERATIONS)
                    
                    if verify_auth_proof(session_key, auth_proof):
                        # Sikeres hitelesítés: session kulcs eltárolása lejárati idővel
                        token = secrets.token_hex(16)
                        active_sessions[token] = {"key": session_key, "expires": time.time() + SESSION_TTL_SECONDS}
                        
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.send_header('Set-Cookie', f'session={token}; HttpOnly; Path=/; SameSite=Lax')
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                        log_message("Sikeres webes bejelentkezés (PSK titkosított csatorna).")
                    else:
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "error", "message": "Helytelen jelszó!"}).encode('utf-8'))
                        log_message("Sikertelen webes bejelentkezési kísérlet (PSK).")
                else:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Hiányzó titkosítási adatok!"}).encode('utf-8'))
                    log_message("Bejelentkezési kísérlet hiányzó PSK adatokkal.")
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
                active_sessions.pop(session_token, None)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Set-Cookie', 'session=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            log_message("Sikeres webes kijelentkezés.")
            return

        elif self.path == '/api/unlock':
            with state_lock:
                shared_state["lockdown_active"] = False
                shared_state["transition_timestamps"].clear()
                shared_state["consecutive_auto_commands"] = 0
            log_message("🔐 Biztonsági zárolás (lockdown) és cooldown feloldva a felhasználó által.")
            self._send_encrypted_json({"status": "success"})
            return

        elif self.path == '/api/config':
            try:
                config_data = self._read_encrypted_body()
                with state_lock:
                    if "start_soc" in config_data:
                        shared_state["start_soc"] = int(config_data["start_soc"])
                    if "stop_soc" in config_data:
                        shared_state["stop_soc"] = int(config_data["stop_soc"])
                        
                    # Biztonsági ellenőrzés: start_soc nem lehet kisebb, mint stop_soc
                    if shared_state.get("start_soc", 100) < shared_state.get("stop_soc", 0):
                        self._send_encrypted_json({"status": "error", "message": "Hiba: A Start % nem lehet kisebb a Stop %-nál!"})
                        return
                    if "stop_import_limit" in config_data:
                        shared_state["stop_import_limit"] = int(config_data["stop_import_limit"])
                    if "grid_charge_duration_minutes" in config_data:
                        shared_state["grid_charge_duration_minutes"] = int(config_data["grid_charge_duration_minutes"])
                    if "house_power_limit_w" in config_data:
                        shared_state["house_power_limit_w"] = int(config_data["house_power_limit_w"])
                    if "persist_mode_on_restart" in config_data:
                        shared_state["persist_mode_on_restart"] = bool(config_data["persist_mode_on_restart"])
                    if "charger_max_amps" in config_data:
                        # Validáció: 0 = "nem felügyelt" (érvényes), egyébként csak 6-16A fogadható el.
                        # A /api/set_current végpont eddig is validált, de ez a végpont (amit a
                        # csúszkák ténylegesen használnak) korábban ellenőrzés nélkül elfogadott
                        # bármilyen egész értéket.
                        amps_val = int(config_data["charger_max_amps"])
                        if amps_val != 0 and not (6 <= amps_val <= 16):
                            self._send_encrypted_json({"status": "error", "message": f"Érvénytelen áramerősség: {amps_val}A (megengedett: 0 vagy 6-16A)"})
                            return
                        shared_state["charger_max_amps"] = amps_val
                    if "force_submode" in config_data:
                        shared_state["force_submode"] = config_data["force_submode"]
                    if "forced_schedule" in config_data:
                        try:
                            shared_state["forced_schedule"] = validate_forced_schedule(config_data["forced_schedule"])
                        except ValueError as ve:
                            self._send_encrypted_json({"status": "error", "message": f"Hiba az ütemezésben: {ve}"})
                            return
                    if "schedule_solar_auto" in config_data:
                        shared_state["schedule_solar_auto"] = bool(config_data["schedule_solar_auto"])
                    if "auto_enabled" in config_data:
                        new_auto = bool(config_data["auto_enabled"])
                        if new_auto and not shared_state.get("auto_enabled", False):
                            shared_state["force_submode"] = "schedule"
                        shared_state["auto_enabled"] = new_auto
                    if "schedule_enabled" in config_data:
                        new_sched = bool(config_data["schedule_enabled"])
                        if new_sched and not shared_state.get("schedule_enabled", False):
                            shared_state["force_submode"] = "schedule"
                        shared_state["schedule_enabled"] = new_sched
                    
                    # Alkalmazási és újraindítási flagek
                    if config_data.get("apply_with_stop"):
                        shared_state["apply_with_stop"] = True
                    if config_data.get("apply_with_restart"):
                        shared_state["apply_with_restart"] = True
                    if config_data.get("reset_limit"):
                        shared_state["reset_limit"] = True
                
                save_config_file()
                log_message("Új konfigurációs paraméterek elmentve.")
                
                self._send_encrypted_json({"status": "success", "message": "Beállítások sikeresen mentve!"})
            except Exception as e:
                self.send_error(400, f"Hibás adatformátum: {e}")
                
        elif self.path == '/api/mode':
            try:
                mode_data = self._read_encrypted_body()
                new_mode = mode_data.get("control_mode")
                if new_mode in ("monitoring", "auto", "schedule", "force"):
                    with state_lock:
                        shared_state["control_mode"] = new_mode
                        if new_mode != "force":
                            shared_state["force_submode"] = "schedule"
                        # Ha kézzel módosítjuk az üzemmódot, töröljük a hibajelzést és a cooldown-t
                        shared_state["error_message"] = ""
                        shared_state["cooldown_until"] = 0.0
                    
                    save_config_file()
                    log_message(f"Üzemmód váltás: {new_mode.upper()}")
                    
                    self._send_encrypted_json({"status": "success"})
                else:
                    self.send_error(400, "Érvénytelen üzemmód")
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

        elif self.path == '/api/force_submode':
            try:
                mode_data = self._read_encrypted_body()
                new_submode = mode_data.get("force_submode")
                if new_submode in ("manual_start", "manual_stop", "schedule"):
                    with state_lock:
                        shared_state["force_submode"] = new_submode
                        # Töröljük a hibajelzést és a cooldown-t
                        shared_state["error_message"] = ""
                        shared_state["cooldown_until"] = 0.0
                        if new_submode == "manual_start":
                            shared_state["manual_start_requested"] = True
                        elif new_submode == "manual_stop":
                            # A felhasználó logikája alapján: a Kézi leállítás kikapcsolja az automatikus módokat
                            shared_state["auto_enabled"] = False
                            shared_state["schedule_enabled"] = False
                    
                    save_config_file()
                    log_message(f"Kényszerített al-üzemmód váltás: {new_submode.upper()}")
                    
                    self._send_encrypted_json({"status": "success"})
                else:
                    self.send_error(400, "Érvénytelen al-üzemmód")
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

        elif self.path == '/api/set_current':
            try:
                current_data = self._read_encrypted_body()
                amps = int(current_data.get("charger_max_amps", 16))
                if 6 <= amps <= 16:
                    with state_lock:
                        shared_state["charger_max_amps"] = amps
                    save_config_file()
                    log_message(f"Töltési áramerősség korlát beállítva: {amps} A")
                    
                    self._send_encrypted_json({"status": "success", "message": "Áramerősség sikeresen frissítve!"})
                else:
                    self.send_error(400, "Érvénytelen áramerősség (6-16A)")
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

        elif self.path == '/api/sim_toggle':
            try:
                data = self._read_encrypted_body()
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
                self._send_encrypted_json({"status": "success", "simulation": sim_val})
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

        elif self.path == '/api/sim_data':
            try:
                data = self._read_encrypted_body()
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
                
                self._send_encrypted_json({"status": "success"})
            except Exception as e:
                self.send_error(400, f"Hiba: {e}")

# --- WEBSZERVER INDÍTÁS ---

def start_web_server():
    server_address = ('0.0.0.0', HTTP_PORT)
    httpd = ThreadingHTTPServer(server_address, ControllerHTTPHandler)
    log_message(f"Web Dashboard elindítva, elérhető a helyi hálózaton a {HTTP_PORT}-as porton.")
    httpd.serve_forever()
