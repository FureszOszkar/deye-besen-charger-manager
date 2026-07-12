# Deye & BESEN Controller – Architecture and Code Structure Documentation

This document details the internal design, threading model, data flow, and the BESEN Bluetooth Low Energy (BLE) protocol implementation of the `main.py (along with its modules)` software for developers.

---

## 1. System Architecture and Threading Model

The application is fully self-contained in a multi-module Python structure, managing concurrent hardware polling, background safety logic, and the HTTP dashboard server.

The core loops run inside an **asynchronous event loop (Python `asyncio`)**, while the Web Dashboard and API are served from a separate background thread to guarantee non-blocking processing.

```
+------------------------------------------------------------+
|                  Background HTTP Thread                    |
|                                                            |
|  [ThreadingHTTPServer] --> Serves ------> [Web Dashboard]  |
|           |                                                |
|           +----------> Updates ---------> [config.json]    |
+------------------------------------------------------------+
                               |
                       Reads / Updates
                               |
                        [ shared_state ]
                    (Lock: state_lock mutex)
                               |
                       Reads / Updates
                               v
+------------------------------------------------------------+
|               Asynchronous asyncio Main Loop               |
|                                                            |
|  [run_inverter_polling]   --> Modbus/TCP --> [Deye]        |
|  [run_charge_controller]  --> Decision   --> [Evaluation]  |
|                                                    |       |
|                                         Pushes     |       |
|                                                    v       |
|  [run_ble_client]  <-- [ble_command_queue] <-------+       |
|         |                                                  |
|         +------------------> BLE Command --> [BESEN EVSE]  |
+------------------------------------------------------------+
```

### A) Thread-Safe State Management (`shared_state`)
Since the Web Server (HTTP thread) and the `asyncio` loop (main thread) run concurrently, data is shared via a single global Python dictionary called `shared_state`. To prevent race conditions, all read/write accesses to this dictionary are synchronized using a mutex (`state_lock = threading.Lock()`).

---

## 2. Asynchronous Background Tasks (asyncio Tasks)

Upon startup, the `main()` function launches several async tasks in parallel:

### 1. `run_inverter_polling()`
* **Task:** Connects to the Deye hybrid inverter's Wi-Fi stick (Solarman LSW-3 Logger) via Modbus RTU over TCP every 10 seconds. The connection is now persistent (using a global _persistent_inverter), and it disconnects and resets only if a socket/Modbus error occurs, thus avoiding socket memory leaks.
* **Library:** `pysolarmanv5` (connecting on TCP port `8899`).
* **Threading Safety (Asynchronization):** Since `pysolarmanv5` Modbus polling contains synchronous blocking network calls, these operations are isolated inside a blocking helper `fetch_inverter_data_blocking()` and run in a separate background worker thread using `asyncio.to_thread()`. This prevents network glitches on the Deye logger stick from blocking the main event loop and causing Bluetooth timeout disconnects.
* **Queried Registers:**
  * **Register 607 (Signed 16-bit):** Inverter grid-port power (internal grid meter).
  * **Register 619 (Signed 16-bit):** Utility grid power (measured by external CT clamps at the meter).
  * **Register 643 (Unsigned 16-bit):** Inverter UPS (Backup) output load. This represents the total consumption of the household.
  * **Registers 672-673 (Unsigned 16-bit, summed):** Solar photovoltaic generation (PV) power (PV1 & PV2 Power in Watts).
  * **Register 590 (Signed 16-bit):** Storage battery power (+ = charging, - = discharging).
  * **Register 588 (Unsigned 16-bit):** Storage battery State of Charge (SoC %).
* **Calculated Non-UPS load:** `charger_power = max(0, grid_power_external - grid_power_internal)`. This calculates the total load of all consumers on the grid side (before the inverter), which includes the EV charger and other non-UPS loads.

### 2. `run_ble_client()`
* **Task:** Manages the BLE connection to the BESEN BS20 car charger, processes the `ble_command_queue`, and receives notifications.
* **Library:** `bleak`.
* **Timeouts and Safe Wrappers:** Bluetooth write and notify registrations lack built-in timeout handling in Bleak. To prevent freezes, all interactions are performed via custom wrappers `safe_ble_write()` and `safe_ble_start_notify()`, which enforce a 5.0 second timeout limit using `asyncio.wait_for()`. Any error or timeout triggers a clean client disconnection to start the reconnection loop.
* **Bluetooth Connection Timeout:** Establishing a connection with `BleakClient` under Windows is prone to hanging indefinitely. To prevent this, the connection process is wrapped in an explicit `asyncio.wait_for(client.connect(), timeout=20.0)` call, which terminates the blocked attempt after 20 seconds and restarts the connection cycle.
* **Telemetry Watchdog:** The client tracks telemetry activity via a global `last_rx_time` timestamp. If the connection state is `LOGGED_IN` but no telemetry packets are received from the charger for 15 seconds, the watchdog triggers a connection reset and cleanly restarts the BLE discovery and reconnection process.
* **Thread-Safe Callback Processing (main_loop):** Since Bleak invokes the telemetry callback (`ble_notification_received()`) on its own background thread (WinRT event thread), directly scheduling async tasks (`asyncio.create_task()`) would fail due to the absence of a running event loop in that thread. To resolve this, we store the main event loop reference in the global `main_loop` variable at startup and use `asyncio.run_coroutine_threadsafe(..., main_loop)` to safely dispatch packet processing back to the main thread's event loop.
* **Thread-Safe BLE Queue Clearing (`clear_ble_command_queue`):** To prevent command accumulation during offline periods and avoid sudden bursts of commands upon reconnection, the system implements a thread-safe `clear_ble_command_queue()` function that clears any leftover items in the `ble_command_queue` whenever a disconnection or reconnection occurs.
* **Important GATT UUIDs:**
  * `FFE4` (Notify): Where the charger streams its telemetry (voltage, current feedback, temperature, status).
  * `FFF3` (Write): Used to write commands (Start, Stop, Current limits).
  * `FFC2` (Notify): Receives PIN/password authorization feedback.
  * `FFC1` (Write): Used to send the login credentials.

### 3. `run_charge_controller()`
* **Task:** The main control loop runs every 5 seconds.
* **Operation:** Reads telemetry from `shared_state`, evaluates active automation rules (Auto, Scheduled, Force), and pushes packets into the `ble_command_queue` when state changes occur.
* **Unified Solar Auto Rules:** The solar charging logic evaluates three protection rules sequentially and independently:
  1. *Grid Import Limit:* Charging stops if grid import exceeds the `stop_import_limit`.
  2. *Battery Stop SoC:* Charging stops if the home battery SoC drops below the `stop_soc` limit.
  3. *House UPS Overload Protection:* Charging stops immediately if the UPS load exceeds the `house_power_limit_w` threshold.
* **Grid Charge Delayed Shutdown:** Setting "Grid charge delayed shutdown (minutes)" to 0 minutes results in an IMMEDIATE shutdown rather than disabling the check, provided the grid power threshold is greater than 0.
* **HTML Input Step Values:** The HTML input step values for Watt parameters are set to `step=1`, allowing single Watt resolution settings (e.g., 80 W).
* **Manual Override Handling (Soft Stop / Restart):** Processes manual overrides (`apply_with_stop`, `apply_with_restart`) at the very beginning of the loop, before mode evaluation or the early `continue` in `monitoring` mode. This guarantees that clicking "Soft Stop" stops charging immediately, even without active automation rules.
* **Manual Start Race Condition Fix:** Implements the `manual_start_requested` state flag to guard the BLE transmission, preventing the startup sequence from being prematurely reset to `schedule` mode by the loop's cleanup checks before the START command packet is successfully sent via BLE.
* **Phase Detection (`line_id`) Placement:** The dynamic phase-count detection (see 3B below) is computed once, at the very top of each loop iteration — before the manual override flags (`apply_with_stop` / `apply_with_restart`) are processed. Since those flags can trigger an early `continue` that sends a BLE STOP packet, `line_id` must already be defined at that point; otherwise the task raises `NameError` and gets restarted by the Watchdog, dropping the pending command.

---

## 3. BESEN BLE Protocol and Login Handshake

The BESEN BS20 uses a custom, proprietary binary framing protocol over BLE. The following authorization handshake must execute successfully before the charger accepts commands:

**Shanghai Timezone Timestamp:**
The BESEN EVSE MCU checks the received Unix timestamp in the START command. If the difference is too large (e.g., Budapest vs. Shanghai), it rejects the packet.
To address this, we introduced the `get_shanghai_timestamp()` function, which converts the local time to a Unix timestamp with an 8-hour offset (Shanghai timezone). We use this adjusted timestamp for START commands instead of `ts = int(time.time())`.

### A) Login Handshake Sequence

```
  Vezérlő Szoftver (Bleak)                           BESEN BS20 Töltő
          |                                                  |
          | <--------------- BLE Connection ---------------->|
          |                                                  |
          | <--- Notify FFE4 (0x0002 Identity Request) ------|
   [INIT] |                                                  |
          | ---- Write FFF3 (0x0002 Identity ACK) ---------->|
[SENT_ACK]| (Replying with the device serial number)         |
          |                                                  |
          | <--- Notify FFE4 (0x0002 Identity Success) ------|
 [ACKED]  |                                                  |
          | ---- Write FFC1 (0x0001 Login Request) --------->|
          |      (6-byte obfuscated PIN code)                |
[SENT_LGN]|                                                  |
          | <--- Notify FFC2 (0x00 Auth Success) ------------|
[LOGGED]  |                                                  |
          v                                                  v
     (Auth Accepted: Full telemetry stream and write permissions active)
```

### B) Binary Packet Structure (Frame format)
All sent and received packets share a fixed binary frame format:

**Dynamic Line ID (Phase Detection):**
The first byte of the command payload is the line identifier (`line_id`), which must be `0x01` for single-phase charging and `0x02` for three-phase charging. The system determines this dynamically based on the measured L2 and L3 phase voltages from the inverter. If active 3-phase voltages are detected (>50V), `line_id` is set to `2` (`0x02`), otherwise it defaults to `1` (`0x01`).

**START Command Payload Structure:**
The payload (`payload = packet[21:]`) for the charger start command (START, `0x8007`) follows this specific binary layout:
*   `payload[0]`: Phase count identifier (`line_id`).
*   `payload[1:17]`: Username (modified to `"BDmanager"`, ASCII-encoded, padded with 0x00 bytes).
*   `payload[17:33]`: Dynamic session charging ID (format: `YYYYMMDDHHMM1337`).
*   `payload[33]`: Default startup flag (`0x00`).
*   `payload[34:38]`: Shanghai timezone Unix timestamp (4-byte integer, Big-Endian format).
*   `payload[38]`: Auto-start flag (`0x01`).
*   `payload[39]`: Online mode indicator (`0x01`).
*   `payload[40:46]`: Limit bypass control bytes (`0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF`).
*   `payload[46]`: Maximum charging current limit (`charger_max_amps`).

| Byte Offset | Length | Description | Value / Example |
|---|---|---|---|
| **0 - 1** | 2 bytes | Packet Header | `0x06, 0x01` |
| **2 - 3** | 2 bytes | Total Frame Length (Big-Endian) | e.g., `0x00, 0x2F` (47 bytes) |
| **4** | 1 byte | Key Type | `0x00` |
| **5 - 12** | 8 bytes | Charger unique serial number | e.g., `0x30, 0x99, 0x83...` |
| **13 - 18** | 6 bytes | Charger password (PIN) | e.g., `0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF` (ASCII representation) |
| **19 - 20** | 2 bytes | Command ID | e.g., `0x80, 0x07` (Start), `0x80, 0x08` (Stop) |
| **21 - N** | Variable | Command Payload | Parameter bytes, status values |
| **N+1 - N+2** | 2 bytes | Modbus CRC16 Checksum | Calculated from byte 0 to end of payload |
| **N+3 - N+4** | 2 bytes | Packet Tail | `0x0F, 0x02` |

### C) Telemetry Payload Structure (Command ID: 0x0004 & 0x000D)
The telemetry payload (`payload = packet[21:]`) contains the live status measurements:
*   `payload[1:3]`: L1 voltage (scale: 0.1, V)
*   `payload[3:5]`: L1 current (scale: 0.01, A)
*   `payload[5:9]`: Real-time active power (Big-Endian, W)
*   `payload[9:13]`: L1 charging energy register (Big-Endian, scale: 0.01, Wh). *Note: On 3-phase setups, the controller multiplies this by 3.0 to obtain the total session energy.*
*   `payload[13:15]`: Internal temperature (scale: 0.01, offset: -200 °C)
*   `payload[18]`: Physical plug state
*   `payload[19]`: Charging output state
*   `payload[25:27]`: L2 voltage (scale: 0.1, V)
*   `payload[27:29]`: L2 current (scale: 0.01, A)
*   `payload[29:31]`: L3 voltage (scale: 0.1, V)
*   `payload[31:33]`: L3 current (scale: 0.01, A)

**Charge Record Processing (`0x000A` packet):**
At the end of a session (or upon reconnection), the charger transmits the `0x000A` (decimal 10) command packet, which contains historical session records.
The exact binary layout of the payload (`payload = packet[21:]`) is as follows:
*   `payload[0]`: Phase/line identifier (`line_id`).
*   `payload[1:17]`: Initiating user RFID card UID (ASCII string, e.g., `"62316176FDFFCBD8"`).
*   `payload[17:33]`: Stop reason / stopping user (ASCII string, e.g., `"Pull Plug"`).
*   `payload[33:49]`: Date-based session identifier (ASCII string, e.g., `"2026061017388996"`).
*   `payload[64:68]`: Starting Unix timestamp (4-byte Big-Endian uint32).
*   `payload[68:72]`: Ending Unix timestamp (4-byte Big-Endian uint32).
*   `payload[72:76]`: Charging duration in seconds (4-byte Big-Endian uint32).
*   `payload[76:80]`: Starting energy register reading in 10 Wh units (4-byte Big-Endian uint32).
*   `payload[80:84]`: Ending energy register reading in 10 Wh units (4-byte Big-Endian uint32).
*   `payload[84:88]`: Session energy delivered in 10 Wh units (4-byte Big-Endian uint32). *Note: The software multiplies this by 10 to obtain the correct Wh value.*

**Selective Logging and Background Clearance**:
The charger broadcasts all uncleared history records sequentially upon reconnection (including external mobile app sessions), prompting the controller to implement an intelligent selective logging and background clearance mechanism. When starting a charge (Manual, Scheduled, or Solar Auto), the generated `charge_id` is stored in the global `_last_initiated_session_id` and saved to `config.json`. Upon receiving a `0x000A` packet, the controller compares its `session_id` with `_last_initiated_session_id`. If they match, the session data is stored in `shared_state["last_charge"]` and persisted to `config.json`, where it is displayed on the dashboard when charging is inactive. If they do not match (external or old backlog session), the controller silently acknowledges and clears the record by sending `0x800A` back to the charger, ensuring it does not get stuck, without overwriting its own session history on the dashboard. The `payload[95:97]` contains the number of power log entries as a 2-byte Big-Endian uint16, and `payload[97:]` consists of a time-series power log array where each entry is a 2-byte Big-Endian integer in Watts (e.g., `0x0FC8` = 4040 W).

---

## 4. API Endpoints and Dashboard Interaction

The built-in HTTP server hosts the static Web Dashboard and provides JSON APIs for live synchronization (updated every 2 seconds by the client).

### Local Network Access Protection (Authentication)
If `"web_auth_enabled"` is active in the configuration, the server validates the `session` token in the HTTP `Cookie` header for each incoming request.
* **Unauthorized Access**: If a request lacks a valid session token, requesting `/` returns the glassmorphic login interface (`LOGIN_HTML`), while other API endpoints (e.g., `/api/status`, `/api/config`) return a `401 Unauthorized` HTTP error with a `{"status": "unauthorized", "message": "Autentikáció szükséges!"}` JSON payload.
* All state-mutating POST endpoints (including `/api/unlock`) require a valid session; only `/api/login` and the read-only `/api/login_info` are reachable unauthenticated.

**Privacy / Name change:**
For security and privacy reasons, all previous username entries of "Attila" have been replaced with "BDmanager" in authentication and commands (e.g., `start_payload[1:17] = b"BDmanager".ljust(16, b"\x00")`). This matches the default setting in the slespersen/evseMQTT project.
* **Exception**: Fetching `/background.png` is allowed without authentication so that the login screen background can load properly.

### End-to-End API Encryption (E2EE)
To protect the traffic between the Web Dashboard's client-side JavaScript and the Python HTTP server from local network sniffing, the system uses built-in, military-grade cryptography:
1.  **Password Verification:** The user's password (plaintext) is never transmitted over the network. The browser sends an HMAC-SHA256 based `auth_proof` (generated using a `client_nonce` provided by the server and a configurable-iteration PBKDF2-SHA256 session key — see `pbkdf2_iterations` in `config.json`, 100,000 by default).
2.  **Transparent Payload Encryption:** Upon successful login, the overridden `fetch()` API on the client side automatically encrypts every HTTP POST body. Previously, the browser's native WebCrypto API was used (AES-GCM), but due to mobile browsers (e.g., Chrome, Vivaldi) blocking it over HTTP, the client side transitioned to a fully independent CryptoJS implementation. The payload is now encrypted using AES-256-CBC (with PKCS7 padding), coupled with a subsequent HMAC-SHA256 (Encrypt-then-MAC) checksum to prevent manipulations. The server decrypts requests using the `pycryptodome` package, and the JSON responses are securely transmitted back to the client fully encrypted and signed with a dedicated MAC.
3.  **Session Expiry:** Each `active_sessions` entry now carries a server-side expiry timestamp (`SESSION_TTL_SECONDS`, 24 hours by default). Expired tokens are rejected and purged lazily on the next access, so a leaked or forgotten cookie does not remain valid indefinitely.

### Responsiveness and Mobile Navigation (Client-Side)
The Web Dashboard uses responsive CSS design with a breakpoint at `1024px`. Above this width, it displays a side-by-side desktop layout; below it, it transitions to a single-card mobile layout.
*   **Mobile View Manager (`showSection`):** Mobile section switching is managed purely via client-side JavaScript. Clicking items in the mobile overlay menu calls `showSection(sectionId)`, which hides other main container cards and displays only the active container at full screen width, preventing layout stretching.

### Endpoints
* **`GET /`**: Serves the single-page Dashboard HTML (`DASHBOARD_HTML` when authenticated) or the login card (`LOGIN_HTML` when unauthorized).
* **`GET /background.png`**: Serves the background image from the executable directory (handles PyInstaller temporary folder environments).
* **`GET /api/status`**: Returns the `shared_state` dictionary as JSON (authentication required).
* **`GET /api/login_info`**: Public, unauthenticated endpoint returning `{"pbkdf2_iterations": <int>}`. Lets any client (the web login page, the Android widget) derive the session key with the server's *current* iteration count instead of assuming a hardcoded default.
* **`POST /api/login`**: Public login endpoint. Receives a `{"clientNonce": "...", "authProof": "..."}` PSK challenge-response payload. If correct, generates a cryptographically secure session token with a 24-hour expiry, saves it in memory, and returns it via a `Set-Cookie: session=<token>; HttpOnly; Path=/; SameSite=Lax` header.
* **`POST /api/logout`**: Closes the active session. Removes the token from memory and expires the cookie (`Max-Age=0`).
* **`POST /api/unlock`**: Clears the Lockdown / Cooldown safety state (authentication required).
* **`POST /api/config`**: Receives configuration updates (authentication required). Validates, saves them to `config.json`, and updates the running control loop instantly. The `forced_schedule` field is strictly validated server-side (see Section 6 below) before being accepted.
* **`POST /api/mode`**: Modifies the operating mode (monitoring / auto / schedule / force, authentication required).
* **`POST /api/force_submode`**: Selects manual override submode (authentication required).
* **`POST /api/set_current`**: Manually limits charging current (authentication required).
* **`POST /api/sim_toggle` / `POST /api/sim_data`**: Toggles simulation mode and sets mock telemetry parameters (authentication required).

---

## 5. Developer Guide for Custom Adaptations

If you want to adapt this controller for different hardware devices:

### Supporting a Different Inverter Brand
To interface with an inverter other than Deye (e.g., Fronius, Huawei, Victron):
1. In `run_inverter_polling()`, replace `pysolarmanv5` with your inverter's SDK or library (e.g., Modbus TCP client, REST API, or MQTT client).
2. Fetch the corresponding power values (Grid, UPS/House load, PV, Battery SoC, Battery Power).
3. Write these values into the corresponding keys of `shared_state` inside the `state_lock` context.

### Supporting a Different EVSE (Car Charger)
To control a charger other than BESEN (e.g., Go-e, Tesla Wall Connector, Shelly relays):
1. In `run_ble_client()`, replace the BLE Bleak client with your charger's native API client (e.g., HTTP REST calls, local TCP sockets, or MQTT messages).
2. At the end of the `run_charge_controller()` evaluation, instead of pushing a packet to `ble_command_queue`, trigger your charger's start/stop or current-limit commands directly.

---

## 6. Advanced Safety and Configuration Validation

### 6.1 Cooldown and Lockdown
To protect the charger from rapid state switching (flapping) and infinite start/stop loops:
1. **Cooldown (20s window):** A sliding 20-second window allows a maximum of 2 state transitions.
2. **Lockdown (40s window):** If 4 state transitions occur within a 40-second window, the system enters a Lockdown mode on the 5th attempt.
3. **Infinite Auto-Loop Protection:** If the system executes 10 consecutive automated start/stop commands without user interaction, it forces a STOP and enters Lockdown mode. Automated safety stops (e.g., due to low battery SoC) use the `is_safety_stop` flag which preserves this counter, ensuring protection against infinite flapping caused by misconfiguration.
4. **Hard Stop Override:** The manual 'Hard STOP' command from the dashboard always bypasses Cooldown and Lockdown constraints for safety reasons.
5. **Authenticated Unlock Only:** `/api/unlock`, which clears an active Lockdown, is only reachable after successful login — it sits behind the same `is_authenticated()` check as every other state-mutating endpoint, so an unauthenticated client on the local network cannot release the safety lock.

### 6.2 Configuration Validation
When saving configurations via the dashboard or loading them from disk, the system applies logical validations. If a user attempts to set the start threshold (`start_soc`) lower than the stop threshold (`stop_soc`), the client-side JavaScript and the server-side API both reject the modification with an error. Upon loading from the configuration file, the system automatically elevates the start value to match the stop value to prevent rapid, infinite switching loops.

**`forced_schedule` schema validation:** The weekly schedule array received by `POST /api/config` is validated server-side (`validate_forced_schedule()` in `dashboard.py`) before being written to `shared_state` or `config.json`:
* Must be a list of exactly 7 entries, one per weekday, no duplicates.
* `day` must match one of the 7 Hungarian weekday names exactly (whitelist, not free text).
* `start` / `stop` must match a strict `HH:MM` pattern.
* `amps` must be an integer between 6 and 16.

This closes two problems an unvalidated payload previously allowed: (a) a crafted `day` string being interpolated into the dashboard's schedule row `innerHTML` (stored XSS, since the value round-trips through `/api/status` on every page load), and (b) a malformed schedule persisted to `config.json` crashing `load_config()` on the *next* application restart (a non-dict list entry raises `TypeError` before the crash-handling logic even runs), effectively bricking the controller until the file was manually repaired. As defense-in-depth, the client-side `renderSchedule()` also now writes the `day` value via `textContent` instead of interpolating it into `innerHTML`. `config.py`'s `load_config()` additionally wraps the schedule-normalization loop in a `try/except`, falling back to the default schedule instead of crashing if an already-corrupted `config.json` is loaded.

### 6.3 Improved House Overload Protection
The home overload protection logic calculates the total load as (UPS Load + Charger Load). If this sum exceeds the house_power_limit_w configuration, the charger is immediately stopped. This safety stop always bypasses Cooldown and Lockdown delays.

---

## 7. Recent Fixes and Hardening (2026-07-08)

A review pass found and fixed the following issues. Summarized here since they affect behavior described elsewhere in this document:

* **`line_id` NameError:** Phase detection was previously computed mid-loop, after the manual override flags could already trigger an early `continue` that referenced `line_id`. Moved to the top of `run_charge_controller()`'s loop body (see Section 2.3).
* **`/api/unlock` authentication bypass:** The endpoint was handled before the `is_authenticated()` check, letting anyone on the local network clear a safety Lockdown without logging in. Moved behind the authentication gate (see Section 6.1).
* **`forced_schedule` validation (stored XSS + restart crash):** See Section 6.2 for the full description and fix.
* **Session expiry:** Login sessions previously never expired. Now capped at 24 hours (see Section 4, End-to-End API Encryption).
* **Idle-state disk writes:** The BLE telemetry handler previously called `save_config_file()` on every non-charging telemetry packet (roughly once per second), which is unnecessary disk/SD-card wear on low-power hosts (e.g. Raspberry Pi). It now only saves when an active session actually needs to be cleared.
* **Dead code removal:** Two leftover draft/"VÁZLAT" code blocks (unreachable code after the main `while True` loops in `run_charge_controller()` and `ble_notification_received()`) and a duplicate, stale `is_authenticated()` / `get_cookie()` / `log_message()` method definition on `ControllerHTTPHandler` were removed. The duplicate handler methods were not merely dead weight — because Python resolves a class's last matching method definition, the duplicate `is_authenticated()` silently overrode the session-expiry-aware version, which would have made the session TTL fix above a no-op.
* **Android widget — PBKDF2 iteration count:** The widget derived its session key using a hardcoded `100000` iteration count, while the server's `pbkdf2_iterations` is user-configurable (the main README recommends lowering it on weak hardware like a Raspberry Pi Zero). The widget now calls `GET /api/login_info` before deriving the key, with `100000` retained only as a fallback if that call fails.
* **Android widget — `allowBackup`:** `AndroidManifest.xml` set `android:allowBackup="true"` while storing the dashboard password in plaintext `SharedPreferences`, making the password extractable via `adb backup` on a non-rooted device. Set to `"false"`.

---

## 8. Android Widget (`AndroidWidget/`)

The project includes a standalone native Android app (Kotlin) that shows the system's live telemetry through a home-screen widget. It builds as its own APK: the GitHub Actions workflow (`.github/workflows/android_widget_build.yml`) runs `assembleDebug` on every push touching `AndroidWidget/**` and uploads the APK as an artifact.

### 8.1 Components

* **`DeyeWidgetProvider`** (`AppWidgetProvider`): manages the widget lifecycle. `onUpdate()` inflates the view, wires up the tap handler, then starts the refresh loop and the 15-minute keep-alive work with the `KEEP` policy. `onDisabled()` (when the last widget is removed) cancels both.
* **`WidgetConfigActivity`**: the configuration screen shown when the widget is placed. Saves the server IP, dashboard password (plaintext), and background transparency (`bg_alpha`) into `SharedPreferences` named `DeyePrefs`.
* **`WidgetUpdateWorker`** (`Worker`): the main refresh loop (see 8.3).
* **`WidgetKeepAliveWorker`** (`Worker`): a 15-minute safety net that revives the main loop if it has died (see 8.4).
* **`ScreenUnlockReceiver`** (`BroadcastReceiver`): a best-effort screen/boot event listener; the refresh chain does **not** rely on it exclusively (see 8.4).
* **`CryptoUtils`**: the same key derivation and E2EE decryption the server uses (PBKDF2, AES-256-CBC, HMAC-SHA256).

### 8.2 Data and authentication flow

The widget uses the same encrypted protocol as the web interface:

1. **`GET /api/login_info`** – fetches the server's current `pbkdf2_iterations` (fallback: `100000`) so key derivation matches the server even when the value differs from the default.
2. **`POST /api/login`** – the widget generates a `clientNonce`, derives a session key from it and the password via PBKDF2, and sends an `authProof`. On success the server returns a `session=` cookie.
3. **`GET /api/status`** – fetches the telemetry with the session cookie. If the response is `enc:true`, it is verified and decrypted with the session key (AES-256-CBC + HMAC-SHA256), and the widget view is updated via `partiallyUpdateAppWidget()`.

On session expiry / `401`, the widget clears the in-memory token and key and re-authenticates on the next cycle.

### 8.3 Refresh model

`widget_info.xml` sets `updatePeriodMillis="0"` — the system performs **no** periodic update; the widget manages refreshes itself with a continuous loop. `WidgetUpdateWorker.doWork()` iterates (roughly every 5 seconds) while the screen is on (`PowerManager.isInteractive`) and Wi-Fi is available. On a locked screen the loop stops on its own (power saving); without Wi-Fi it shows a blank/transparent state.

### 8.4 Wi-Fi resilience (network-switch handling)

A prior bug caused the widget's data to get "stuck" when the user left their own Wi-Fi range and later returned. The root causes were fixed across several layers:

* **`InterruptedException` handling and self-restart:** WorkManager stops the Worker at the 10-minute runtime limit by interrupting the thread, which makes `Thread.sleep()` throw `InterruptedException`. Previously nothing caught it, so `doWork()` died by exception and never restarted. Now a `try/finally` catches it, and the `finally` block re-enqueues the loop with the `REPLACE` policy, **guaranteeing** a restart as long as the screen is active.
* **15-minute heartbeat (`WidgetKeepAliveWorker`):** a periodic `PeriodicWorkRequest` that revives the main loop with the `KEEP` policy if it has died for any reason (process death, undelivered broadcast). It does nothing while the loop is alive. WorkManager persists it across device reboots.
* **Network callback:** while the loop runs, a `ConnectivityManager.registerNetworkCallback()` watches Wi-Fi (`TRANSPORT_WIFI`) availability. On returning home, as soon as Wi-Fi is available again the waiting cycle breaks immediately and refreshes — no need to wait out the 5 seconds.
* **Captive-portal login validation:** `doLogin()` no longer accepts any HTTP 200. It stores a session only if the response truly came from our server: a JSON `{"status":"success"}` body **and** a real `session=` cookie. Without this, a redirect page's HTTP 200 on a foreign network would "poison" the session with an empty token.
* **Tap fallback:** tapping the widget makes `onUpdate()` restart the loop with `KEEP`, as a manual last resort.

The `ScreenUnlockReceiver` (`USER_PRESENT` / `SCREEN_OFF` / `BOOT_COMPLETED`) is only an accelerator on devices where the event is delivered; because of Android 8+ implicit-broadcast restrictions (and the fact that `SCREEN_OFF` cannot be delivered to a manifest receiver), refresh reliability rests on the mechanisms above rather than on it.
