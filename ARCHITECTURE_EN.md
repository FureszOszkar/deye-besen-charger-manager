# Deye & BESEN Controller – Architecture and Code Structure Documentation

This document details the internal design, threading model, data flow, and the BESEN Bluetooth Low Energy (BLE) protocol implementation of the `deye_besen_controller.py` software for developers.

---

## 1. System Architecture and Threading Model

The application is fully self-contained in a single Python file, managing concurrent hardware polling, background safety logic, and the HTTP dashboard server.

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
* **Task:** Connects to the Deye hybrid inverter's Wi-Fi stick (Solarman LSW-3 Logger) via Modbus RTU over TCP every 10 seconds.
* **Library:** `pysolarmanv5` (connecting on TCP port `8899`).
* **Threading Safety (Asynchronization):** Since `pysolarmanv5` Modbus polling contains synchronous blocking network calls, these operations are isolated inside a blocking helper `fetch_inverter_data_blocking()` and run in a separate background worker thread using `asyncio.to_thread()`. This prevents network glitches on the Deye logger stick from blocking the main event loop and causing Bluetooth timeout disconnects.
* **Queried Registers:**
  * **Register 607 (Signed 16-bit):** Inverter grid-port power (internal grid meter).
  * **Register 619 (Signed 16-bit):** Utility grid power (measured by external CT clamps at the meter).
  * **Register 643 (Unsigned 16-bit):** Inverter UPS (Backup) output load. This represents the total consumption of the household.
  * **Register 175 (Unsigned 16-bit):** Solar photovoltaic generation (PV) power.
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
* **Important GATT UUIDs:**
  * `FFE4` (Notify): Where the charger streams its telemetry (voltage, current feedback, temperature, status).
  * `FFF3` (Write): Used to write commands (Start, Stop, Current limits).
  * `FFC2` (Notify): Receives PIN/password authorization feedback.
  * `FFC1` (Write): Used to send the login credentials.

### 3. `run_charge_controller()`
* **Task:** The main control loop (runs every 5 seconds).
* **Operation:** Reads telemetry from `shared_state`, evaluates active automation rules (Auto, Scheduled, Force), and pushes packets into the `ble_command_queue` when state changes occur.
* **Safety Guards:** Evaluates the `house_power_limit_w` threshold. If the household UPS load exceeds this limit, it pushes a stop command to the queue.
* **Manual Override Processing (Soft Stop / Restart):** Manual override flags (`apply_with_stop`, `apply_with_restart`) are evaluated at the very beginning of the loop, before mode evaluation and any early loop skips (such as the `monitoring` mode return). This guarantees that the Soft Stop command is dispatched to the charger immediately when requested, even if no automated schedule or solar rules are currently running.

---

## 3. BESEN BLE Protocol and Login Handshake

The BESEN BS20 uses a custom, proprietary binary framing protocol over BLE. The following authorization handshake must execute successfully before the charger accepts commands:

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

---

## 4. API Endpoints and Dashboard Interaction

The built-in HTTP server hosts the static Web Dashboard and provides JSON APIs for live synchronization (updated every 2 seconds by the client).

### Local Network Access Protection (Authentication)
If `"web_auth_enabled"` is active in the configuration, the server validates the `session` token in the HTTP `Cookie` header for each incoming request.
* **Unauthorized Access**: If a request lacks a valid session token, requesting `/` returns the glassmorphic login interface (`LOGIN_HTML`), while other API endpoints (e.g., `/api/status`, `/api/config`) return a `401 Unauthorized` HTTP error with a `{"status": "unauthorized", "message": "Autentikáció szükséges!"}` JSON payload.
* **Exception**: Fetching `/background.png` is allowed without authentication so that the login screen background can load properly.

### Responsiveness and Mobile Navigation (Client-Side)
The Web Dashboard uses responsive CSS design with a breakpoint at `1024px`. Above this width, it displays a side-by-side desktop layout; below it, it transitions to a single-card mobile layout.
*   **Mobile View Manager (`showSection`):** Mobile section switching is managed purely via client-side JavaScript. Clicking items in the mobile overlay menu calls `showSection(sectionId)`, which hides other main container cards and displays only the active container at full screen width, preventing layout stretching.
*   **Cache-Control Headers**: To prevent layout rendering anomalies due to browser caching, the `/` web endpoint returns explicit HTTP caching headers set to `no-cache`, `no-store`, and `must-revalidate`.
*   **Tooltip Layout and Event Handling:** On mobile, tooltips display downwards below the info icons (preventing overlap with the sticky header), are restricted to `220px` in width, and align to the right on right-side components to avoid screen overflow. A global client-side event listener blocks click propagation on `.tooltip-container` elements, preventing accidental toggle changes on parent checkboxes.

### Endpoints
* **`GET /`**: Serves the single-page Dashboard HTML (`DASHBOARD_HTML` when authenticated) or the login card (`LOGIN_HTML` when unauthorized).
* **`GET /background.png`**: Serves the background image from the executable directory (handles PyInstaller temporary folder environments).
* **`GET /api/status`**: Returns the `shared_state` dictionary as JSON (authentication required).
* **`POST /api/login`**: Public login endpoint. Receives a `{"password": "..."}` JSON payload. If correct, generates a cryptographically secure session token, saves it in memory, and returns it via a `Set-Cookie: session=<token>; HttpOnly; Path=/; SameSite=Lax` header.
* **`POST /api/logout`**: Closes the active session. Removes the token from memory and expires the cookie (`Max-Age=0`).
* **`POST /api/config`**: Receives configuration updates (authentication required). Validates, saves them to `config.json`, and updates the running control loop instantly.
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
