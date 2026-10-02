# Otthonvezérlő – Architecture and Code Structure Documentation

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
Since the Web Server (HTTP thread) and the `asyncio` loop (main thread) run concurrently, data is shared via a single global Python dictionary called `shared_state`. To prevent race conditions, all read/write accesses to this dictionary are synchronized using a mutex (`state_lock = threading.RLock()` — re-entrant, so the same thread may re-acquire it while already holding it). `log_message()` itself acquires this lock, so with a plain `Lock` calling `log_message()` while holding `state_lock` self-deadlocks the whole event loop — see the 2026-08-13 entry.

---

## 2. Asynchronous Background Tasks (asyncio Tasks)

Upon startup, the `main()` function launches several async tasks in parallel:

### 1. `run_inverter_polling()`
* **Task:** Connects to the Deye hybrid inverter's Wi-Fi stick (Solarman LSW-3 Logger) via Modbus RTU over TCP every 10 seconds (`_INVERTER_POLL_INTERVAL_S`), or every 20 seconds while charging is active (`shared_state["charging_active"]`, from the charger's own BLE telemetry; `_INVERTER_POLL_INTERVAL_CHARGING_S`), because the logger often fails to respond when the inverter is under peak load. The wait (`_inverter_poll_wait()`) runs in steps of at most 5 s with a PONG to the Watchdog before each step and at the end, so the PONG gap is at most one step or one poll, regardless of the interval; the interval is re-evaluated between steps, so after charging stops the 10 s rhythm returns within 5 s. The connection is persistent: exactly **one** `PySolarmanV5` instance (global `_persistent_inverter`, guarded by `_inverter_lock`), created with `auto_reconnect=False` and a 15-second `socket_timeout` (the library uses it for connecting and for every response wait; during a poll a PONG goes to the Watchdog after every step — connection build, each read — via `_inverter_pong()`, so a slow but progressing poll may take longer than the 30 s limit, while a genuinely stuck step gets no PONG and the Watchdog catches it). The library's internal reconnection is deliberately disabled because it left orphaned instances (connection + reader thread + pipe pair) behind until the file descriptors ran out (see the 2026-09-19 entry). On any error `_close_inverter()` closes and discards the instance and the next call builds a fresh one; if an existing connection fails with an instant connection error, it is rebuilt once immediately within the same call.
* **Library:** `pysolarmanv5` (connecting on TCP port `8899`).
* **Single-request read:** the telemetry registers (588 SoC, 590 battery, 607 internal grid, 619 external grid, 643 UPS, 672-673 PV) fall into one range, so `_read_inverter_registers()` reads them by default with a single `read_holding_registers(588, 86)` request (Modbus limit: 125), and `_inverter_data_from_regs()` derives the same values as before. If the logger/inverter rejects the range with an "unsupported" Modbus error (`IllegalDataAddressError` / `IllegalFunctionError` / `IllegalDataValueError`), it switches back to the 6 separate requests (`_INVERTER_REGS_SINGLE`) for the rest of the process, with a log line; transient errors (timeout, connection error, `ServerDeviceBusyError`) do not switch. On the first successful read an `[INVERTER] Olvasási mód: …` ("read mode") line shows the mode.
* **A single late response does not drop the connection (`_on_inverter_read_error()`):** on a timeout (`_INVERTER_TIMEOUT_ERRORS = (queue.Empty, TimeoutError)`) the connection is closed only on the **second consecutive** occurrence (`_INVERTER_TIMEOUTS_BEFORE_CLOSE = 2`, counter: `_inverter_timeout_streak`); on the first one the connection is kept, the poll fails (the inverter indicator is red for that round), and the log shows `[INVERTER] A logger nem válaszolt időben (1/2) — a kapcsolat megmarad.` (the logger did not answer in time, the connection is kept). A successful read and `_close_inverter()` reset the counter; every other error still closes the connection immediately. A late-arriving old response is discarded by `pysolarmanv5`'s sequence-number check, so it cannot be mixed up with the next request's response.
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
* **Timeouts and Safe Wrappers:** Bluetooth write and notify registrations lack built-in timeout handling in Bleak. To prevent freezes, all interactions are performed via custom wrappers `safe_ble_write()` and `safe_ble_start_notify()`, which enforce a 5.0 second timeout limit using `asyncio.wait_for()`. Any error or timeout triggers a clean client disconnection to start the reconnection loop. The disconnection itself is time-limited too, via `safe_ble_disconnect()` (12 seconds, above `bleak`'s own internal 10-second limit for the BlueZ disconnect signal): a hung disconnect is logged and abandoned instead of blocking the task until the Watchdog cancels it.
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
* **Connection condition (before the mode branching):** the charger (BLE) connection is required in every mode (`charger_connected`), while the inverter connection (`inverter_connected`) is required **only for the Solar Auto rules** (`use_solar_auto_rules`: Auto mode, a scheduled window without "Override Solar Auto", or outside a window with "Solar Auto after schedule" enabled), because only they decide from inverter data (SoC, grid and UPS power). Force (manual) mode and fixed-current scheduled charging (and the stop outside a window) run without the inverter. `use_solar_auto_rules` is therefore computed before the condition. Manual stop (`apply_with_stop`/`apply_with_restart`) already ran before the condition.
* **Unified Solar Auto Rules:** The solar charging logic evaluates three protection rules sequentially and independently:
  1. *Grid Import Limit:* Charging stops if grid import exceeds the `stop_import_limit`.
  2. *Battery Stop SoC:* Charging stops if the home battery SoC drops below the `stop_soc` limit.
  3. *House UPS Overload Protection:* Charging stops immediately if the UPS load exceeds the `house_power_limit_w` threshold.
* **Grid Charge Delayed Shutdown:** Setting "Grid charge delayed shutdown (minutes)" to 0 minutes results in an IMMEDIATE shutdown rather than disabling the check, provided the grid power threshold is greater than 0.
* **HTML Input Step Values:** The HTML input step values for Watt parameters are set to `step=1`, allowing single Watt resolution settings (e.g., 80 W).
* **Manual Override Handling (Soft Stop / Restart):** Processes manual overrides (`apply_with_stop`, `apply_with_restart`) at the very beginning of the loop, before mode evaluation or the early `continue` in `monitoring` mode. This guarantees that clicking "Soft Stop" stops charging immediately, even without active automation rules.
* **Manual Start Race Condition Fix:** Implements the `manual_start_requested` state flag to guard the BLE transmission, preventing the startup sequence from being prematurely reset to `schedule` mode by the loop's cleanup checks before the START command packet is successfully sent via BLE.
* **Phase Detection (`line_id`) Placement:** The dynamic phase-count detection (see 3B below) is computed once, at the very top of each loop iteration — before the manual override flags (`apply_with_stop` / `apply_with_restart`) are processed. Since those flags can trigger an early `continue` that sends a BLE STOP packet, `line_id` must already be defined at that point; otherwise the task raises `NameError` and gets restarted by the Watchdog, dropping the pending command.

### 4. `run_climate_polling()` (`climate_logic.py`)
* **Frequency:** every 60 seconds (`SENSOR_POLL_INTERVAL_S`); the wait runs in 5 s steps (`PONG_STEP_S`), each preceded by a `"climate"` PONG to the Watchdog.
* **Air purifier (`_read_sensor_once()`):** runs `climate_devices.purifier_read()` via `asyncio.to_thread()` with a 15 s overall timeout (`SENSOR_READ_TIMEOUT_S`). The result goes to `shared_state["climate"]["sensor"]` (temperature, humidity, timestamp, error). Only state changes are logged (`[KLÍMA] Légtisztító elérhető / nem érhető el` — purifier reachable / not reachable). No IP or key → no read (the badge is grey).
* **BroadLinks (`_check_units_once()`):** per air conditioner `broadlink_reachable()` (only `hello`, without authentication or commands); a unit that is currently learning/sending is skipped. Result: `units[i]["reachable"]` (the header Klíma1–3 badges), with a log line on change.
* **In simulation** (`--sim`) made-up readings and "reachable" devices, without network traffic.
* Supervised by the Watchdog like the other tasks (restarted on crash or on a missing PONG).

### 5. `run_shading_timer()` (`shading_logic.py`)
* **Frequency:** checks the clock every 5 seconds (`CHECK_STEP_S`), with a `"shading"` PONG at the start of every round.
* **Operation:** if an enabled device's up/down time for the current day equals the current `HH:MM`, the signal is sent **once** in that minute (the `fired` set records what was sent, keyed by the minute stamp). There is no catch-up: if the program is not running in that minute, the movement is skipped. Sending runs in `asyncio.to_thread()` with a 15 s timeout (`SEND_TIMEOUT_S`); if learning/testing is in progress on the device (`_device_locks`), the timed movement is skipped with a log line.
* The current time (`_now()`) is replaceable in tests (virtual clock).
* Supervised by the Watchdog like the other tasks.

### 6. Climate and shading modules
* **`climate_devices.py` — device layer (blocking calls, run in worker threads):**
  * **BroadLink:** `broadlink.hello(ip)` + `auth()` (unicast to the IP address, because network discovery — broadcast — does not work on every network). Infrared learning: `enter_learning()` + `check_data()` for up to 30 s. Radio (RF) learning in two steps (`broadlink_learn_rf()`, only on RF-capable devices, e.g. RM4 Pro): frequency sweep while the user holds the button (`sweep_frequency()` / `check_frequency()`, ≤30 s), then capturing one press (`find_rf_packet()` / `check_data()`, ≤30 s); if the frequency is not found, `cancel_sweep_frequency()`. A clear error message on an infrared-only device. Codes are stored as base64. Each network operation has a 5 s timeout.
  * **Xiaomi air purifier — own miIO client** (no external library): UDP `54321`, handshake (`hello` packet) → device ID and timestamp; the request is AES-128-CBC encrypted (key = MD5(token), IV = MD5(key + token)), the header checksum is MD5(header + token + body). The `get_properties` call requests `siid 3 / piid 7` (temperature) and `siid 3 / piid 1` (humidity). Each call uses a single socket that is closed afterwards.
  * Every error comes back as `ClimateDeviceError` with a user-facing Hungarian message.
* **`climate_logic.py`:** task 4; code learning (`start_learn()`, in a worker thread, one lock per air conditioner — one operation at a time), manual test send (`send_code()`); code types `heat_on` / `cool_on` / `off` → `ir_on_heat` / `ir_on_cool` / `ir_off`. Saving the settings: `update_climate_settings()` (see 6.2).
* **`shading_logic.py`:** task 5; timer saving (`update_shading_config()`, see 6.2), RF learning in a worker thread reporting the steps ("hold" / "press") to the dashboard, manual test.
* **`config.py`:** `DEFAULT_CONFIG["climate"]` (`sensor` {ip, token}; 3 × `units` {name, broadlink_ip, ir_on_heat, ir_on_cool, ir_off, surplus_w, expected_w}; `settings` {mode, auto_enabled, soc, target_temp, on_minutes, off_minutes}) and `DEFAULT_CONFIG["shading"]` (broadlink_ip; `devices` roller/awning {enabled, rf_up, rf_down, schedule: 7 days × {day, up, down}}). `normalize_climate_config()` / `normalize_shading_config()` fix up incomplete or old structures at load time (missing field → default; numeric settings default to `None`, see the 2026-07-29 entry). `refresh_climate_public_state()` / `refresh_shading_public_state()` put only what the dashboard needs into `shared_state`: **the air purifier key and the learned codes are not included**, only whether they are set.

### 7. `run_home_devices()` (`home_devices.py`) — LED strip, smart plug and internet radio
* **Frequency:** a `"home"` PONG and a timer check (plug and radio) every 5 s; the state of both devices is queried **every minute** (`POLL_INTERVAL_S`), in a worker thread, with a 15 s timeout. If an app command is running on the device (per-device lock), the query is skipped for that round. Only reachability changes are logged (`[OTTHON] …`).
* **CozyLife LED strip** (based on the manufacturer's open Home Assistant integration): local TCP 5555, one JSON message per line (`\r\n`), `{"pv":0,"cmd":…,"sn":…,"msg":…}`; query `cmd 2` (`attr [0]`), set `cmd 3` (`attr` + `data`). Only a response with the same `cmd` and `sn` counts (the device's unsolicited `cmd 10` state report is skipped), `res ≠ 0` = rejected. Data points: `1` on/off (0/255), `2` mode (0 = static colour), `4` brightness 0–1000, `5` hue 0–360, `6` saturation 0–1000 (0–100 towards the UI). No key.
* **Xiaomi plug:** the existing `climate_devices.miio_call()` (error messages name "A konnektor" — the `label` parameter). The call depends on `model`: `chuangmi.plug.v1` `set_on`/`set_off` + `get_prop ["on"]`; `v3` `set_power` + `get_prop ["on"]`; `m1`/`m3`/`v2`/`hmi205`/`hmi206`/`hmi208` `set_power`/`get_prop ["power"]`; everything else MiOT (`get/set_properties`, siid 2 / piid 1).
* **Plug timer:** like the shading timer — at the day's times, once in that minute (`fired` set), no catch-up; `_now()` is replaceable in tests. **Two on/off pairs** per day: `on`/`on2` switch on, `off`/`off2` switch off.
* **Internet radio (infrared):** through a BroadLink (`home_devices.radio.broadlink_ip`, only in `config.json`), with a single **power** code (`ir_power`), because the remote has one power button: "Be" (on) and "Ki" (off) send the same button press, and the radio switches to its opposite state. There is no query (IR is one-way); the public state only holds the time of the last button press sent (`last_sent`). Learning: in a worker thread with the existing `climate_devices.broadlink_learn()` (30 s), following the air conditioners' learning; sending: `broadlink_send()`. Own lock (`_radio_lock`): during learning or sending a second operation is rejected, and a timed button press is skipped (logged). **Radio timer:** like the plug timer, but it sends the power code at both the on and the off time.
* **In simulation** a made-up state that reacts to commands, without network; the devices count as configured (`run_home_devices()` refreshes the public state at start, because simulation mode is switched on after the config is loaded); radio learning saves a made-up code after 3 s.
* **`config.py`:** `DEFAULT_CONFIG["home_devices"]` (`led` {name, ip}; `plug` {name, ip, token, model, enabled, schedule: 7 days × {day, on, off, on2, off2}}; `radio` {name, broadlink_ip, ir_power, enabled, schedule: 7 days × {day, on, off}}; the time fields per device: `HOME_SCHEDULE_KEYS`), `normalize_home_config()` (an old single-pair plug setting goes into the 1st pair, the 2nd pair stays empty), `refresh_home_public_state()`; the plug key and the radio's learned code are not put into `shared_state["home"]` (only `has_code`).

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
*   **Desktop-only page ("Egyéb", `showPage('other')`):** the header tab row is hidden on mobile and the page has no dock icon, so it is not reachable on mobile. If the window is narrowed to mobile width while this page is open, the Measurements card is shown (with Measurements selected in the dock); widened again, the Egyéb page returns.

### Endpoints
* **`GET /`**: Serves the single-page Dashboard HTML (`DASHBOARD_HTML` when authenticated) or the login card (`LOGIN_HTML` when unauthorized). When serving `DASHBOARD_HTML`, the server fills in the initial visibility of the logout buttons (`{{LOGOUT_MOBILE_STYLE}}`, `{{LOGOUT_DIVIDER_STYLE}}`, `{{LOGOUT_GROUP_STYLE}}`) and the `_WEB_AUTH_ENABLED` JS constant (`{{WEB_AUTH_ENABLED_JS}}`) from `WEB_AUTH_ENABLED`, so they do not depend on the `/api/status` response.
* **`GET /background.png`**: Serves the background image from the executable directory (handles PyInstaller temporary folder environments).
* **`GET /api/status`**: Returns the `shared_state` dictionary as JSON (authentication required).
* **`GET /api/login_info`**: Public, unauthenticated endpoint returning `{"pbkdf2_iterations": <int>}`. Lets any client (the web login page, the Android widget) derive the session key with the server's *current* iteration count instead of assuming a hardcoded default.
* **`POST /api/login`**: Public login endpoint. Receives a `{"clientNonce": "...", "authProof": "..."}` PSK challenge-response payload. If correct, generates a cryptographically secure session token with a 24-hour expiry, saves it in memory, and returns it via a `Set-Cookie: session=<token>; HttpOnly; Path=/; SameSite=Lax` header.
* **`POST /api/logout`**: Closes the active session. Removes the token from memory and expires the cookie (`Max-Age=0`). It can be called without a body or encryption (it only uses the cookie), so a page loaded without the key can log out with it too (automatic logout, see the 2026-09-26 entry).
* **`POST /api/unlock`**: Clears the Lockdown / Cooldown safety state (authentication required).
* **`POST /api/config`**: Receives configuration updates (authentication required). Validates, saves them to `config.json`, and updates the running control loop instantly. The `forced_schedule` field is strictly validated server-side (see Section 6 below) before being accepted.
* **`POST /api/mode`**: Modifies the operating mode (monitoring / auto / schedule / force, authentication required).
* **`POST /api/force_submode`**: Selects manual override submode (authentication required).
* **`POST /api/set_current`**: Manually limits charging current (authentication required).
* **`POST /api/sim_toggle` / `POST /api/sim_data`**: Toggles simulation mode and sets mock telemetry parameters (authentication required).
* **`POST /api/climate/settings`**: The air conditioner control settings (`{"mode": "heat"|"cool", "auto_enabled": bool, "soc", "target_temp", "on_minutes", "off_minutes", "units": [3 × {"surplus_w", "expected_w"}]}`), with atomic validation (see 6.2), saved to `config.json`.
* **`POST /api/climate/learn`** / **`POST /api/climate/send`**: Learn (in the background) / manually test-send an infrared code; `{"unit": 0..2, "code": "heat_on"|"cool_on"|"off"}`.
* **`POST /api/shading/config`**: One shading device's timer (`{"device": "roller"|"awning", "enabled": bool, "schedule": [7 × {"up": "HH:MM"|"", "down": ...}]}`), with atomic validation.
* **`POST /api/shading/learn`** / **`POST /api/shading/send`**: Learn a radio code (in two steps, in the background) / manual test; `{"device": "roller"|"awning", "action": "up"|"down"}`.
* **`POST /api/led/set`** (app): any subset of `{"on": bool, "brightness": 0–100, "hue": 0–360, "saturation": 0–100}`; colour needs `hue` and `saturation` together; no other field with switch-off. Atomic validation, then the fresh state in `shared_state["home"]["led"]`.
* **`POST /api/plug/set`** (web, app): `{"on": bool}`. **`POST /api/plug/schedule`** (web, app): `{"enabled": bool, "schedule": [7 × {"on": "HH:MM"|"", "off": ..., "on2": ..., "off2": ...}]}`, with atomic validation, saved to `config.json`. **All four fields are required**: a missing field (e.g. a save from the old single-pair app) is rejected and nothing changes — so the old app cannot delete the 2nd pair.
* **`POST /api/radio/learn`** (web) / **`POST /api/radio/send`** (web, app): learn the internet radio's power code (in the background) / one power button press; the body is an empty object (`{}`), any other field is rejected. **`POST /api/radio/schedule`** (web, app): like the plug's, but with one pair per day (`on`, `off`, both required).
* The device IP addresses and the air purifier key can **not** be set through the API, only in `config.json`.

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
0. **They block only starting:** the limits below (and the `cooldown_until` cooldown) block START commands only. **A STOP can always go out** — previously the plain out-of-window STOP was blocked in lockdown and charging continued. During a cooldown the stop rules also keep running if the car is charging.
1. **Cooldown (20s window):** A sliding 20-second window allows a maximum of 2 state transitions.
2. **Lockdown (40s window):** If 4 state transitions occur within a 40-second window, the system enters a Lockdown mode on the 5th attempt.
3. **Auto-Loop Protection (5-minute sliding window):** If the system executes 10 automated start/stop commands within a 5-minute window without user interaction, it enters Lockdown mode. The counter is a timestamped list (`auto_command_timestamps`) that drops entries older than 5 minutes on every check — so a long-running, healthy automation (e.g. 1-2 automated commands per day) never accumulates an unbounded count; only genuinely frequent (once-a-minute-or-faster) flapping trips it. (Before 2026-08-13 this was a plain, never-decreasing counter, which meant a perfectly healthy, purely automated system would theoretically have hit the lockdown threshold eventually, purely with the passage of time — see the 2026-08-13 changelog entry.) Automated safety stops (e.g., due to low battery SoC) use the `is_safety_stop` flag which preserves this counter, ensuring protection against infinite flapping caused by misconfiguration.
4. **Hard Stop Override:** The manual 'Hard STOP' command from the dashboard always bypasses Cooldown and Lockdown constraints for safety reasons.
5. **Authenticated Unlock Only:** `/api/unlock`, which clears an active Lockdown, is only reachable after successful login — it sits behind the same `is_authenticated()` check as every other state-mutating endpoint, so an unauthenticated client on the local network cannot release the safety lock.

### 6.2 Configuration Validation
When saving configurations via the dashboard or loading them from disk, the system applies logical validations.

**A rejected save restores nothing:** on validation errors the handler only returns an error message. It used to call `load_config()`, which — with `persist_mode_on_restart` off — switched to monitoring and turned the automations off, so a rejected form silently stopped the schedule.

**Safe writing to disk (`save_config_file`):** the snapshot is taken under `state_lock` with a sequence number; the write happens under a separate lock (`_save_lock`), so threads do not write at the same time and an older snapshot cannot overwrite a newer one. The write goes to a temporary file (`config.json.tmp`, `fsync`), the old file becomes `config.json.bak`, then `os.replace` puts the new one in place. If no file can be created in the folder, a direct write is used (with a one-time log warning).

**Corrupt or missing `config.json` (`_read_saved_config`):** the corrupt file is moved aside as `config.json.corrupt-<date>`, `config.json.bak` is loaded (and copied back as `config.json`), and the error goes to the log and the dashboard's error box (`error_message`). If the `.bak` cannot be read either: defaults.

**Atomic server-side null validation (empty-field protection):** The `/api/config` handler reads all numeric field values from `config_data` FIRST, before applying any of them. If any value is `null` (JSON null — sent when the client has an empty input field, since `JSON.stringify(NaN) === null`) OR `charger_max_amps` is outside the 6-16A range, the handler: (1) calls `load_config()` to restore `shared_state` from the last known-good `config.json` (no partial mutation occurs), and (2) returns `{"status": "error", "message": "Hibás adattartalom miatt visszaállt a konfig az eredetire"}` as clean JSON. Only when every field is valid are they applied atomically inside a single `with state_lock:` block. This server-side guard protects against empty fields regardless of what the client code does — if client code ever introduces a `|| fallback` coercion, the value silently becomes a plausible number, but if it doesn't and the empty field reaches the server as `null`, the server will always catch it.

**`start_soc < stop_soc` check** runs before any mutation (not after, as in the original sequential handler). Upon loading from the configuration file, `load_config()` also performs this check and automatically elevates `start_soc` to match `stop_soc` to prevent infinite switching loops.

**SoC hysteresis (minimum gap between Start and Stop %):** if `stop_soc > 0` (enabled), `/api/config` rejects the save when `start_soc - stop_soc < 2` (less than a 2-percentage-point gap) — this prevents the Start and Stop thresholds from coinciding (e.g. both at 99%), which would otherwise let a single SoC measurement fluctuation trigger immediate, unnecessary start/stop flapping.

**`forced_schedule` — multiple time windows per day, strict validation:** each day is `{day, enabled, windows: [...]}`, where `windows` is a **variable-length list** of independent time windows (`{start, stop, amps, override_auto}`, max 8/day). Every window is **contained within a single calendar day** — there is no midnight-spanning window in the code; overnight charging is configured as two consecutive daily windows (e.g. today's `"22:00"-"24:00"` plus tomorrow's `"00:00"-"06:00"`), and `stop` also accepts the special end-of-day value `"24:00"`. The weekly schedule array received by `POST /api/config` is validated server-side (`validate_forced_schedule()` in `dashboard.py`) before being written to `shared_state` or `config.json`:
* Must be a list of exactly 7 entries, one per weekday, no duplicates.
* `day` must match one of the 7 Hungarian weekday names exactly (whitelist, not free text).
* Each window's `start` / `stop` must match a strict `HH:MM` pattern (or `"24:00"` for `stop`), with `start < stop`.
* Each window's `amps` must be an integer between 6 and 16.
* **Windows within the same day must not overlap** — touching (one window's end equals the next one's start) is allowed and is the normal way to represent continuous charging.

This closes two problems an unvalidated payload previously allowed: (a) a crafted `day` string being interpolated into the dashboard's schedule row `innerHTML` (stored XSS, since the value round-trips through `/api/status` on every page load), and (b) a malformed schedule persisted to `config.json` crashing `load_config()` on the *next* application restart (a non-dict list entry raises `TypeError` before the crash-handling logic even runs), effectively bricking the controller until the file was manually repaired. As defense-in-depth, the client-side `renderScheduleFromDraft()` also writes the `day` value via `textContent` instead of interpolating it into `innerHTML`. `config.py`'s `load_config()` additionally wraps the schedule-normalization loop in a `try/except`, falling back to the default schedule instead of crashing if an already-corrupted `config.json` is loaded, and automatically migrates the old (single-window-per-day) format into the new `windows`-based structure.

**Climate and shading settings — the same principle:** `update_climate_settings()` and `update_shading_config()` **validate every field first**, and only if all are valid do they apply them together, under `state_lock`, then save. On an error nothing changes, and the response names the invalid field. Climate: mode `heat`/`cool`, `auto_enabled` bool, battery level 0–100, minutes ≥ 0, per air conditioner the W values (`surplus_w`, `expected_w`) numbers ≥ 0 (the order of the thresholds is not checked — the thresholds themselves give the order); `bool`, strings, `NaN` and `null` are rejected (the client sends `NaN` for an empty field, which arrives as `null` — there is no `||` default filling). Shading: exactly 7 days, times `HH:MM` or empty.

### 6.3 Improved House Overload Protection
The home overload protection logic calculates the total load as (UPS Load + Charger Load). If this sum exceeds the house_power_limit_w configuration, the charger is immediately stopped. This safety stop always bypasses Cooldown and Lockdown delays.

---

## 7. Recent Fixes and Hardening

### 2026-10-02

> **Status:** these changes exist only on the `javitasok-2026-10-02` branch. They ran live for a short time; the user saw the inverter connection being dropped, so the running version was restored. The cause has not been investigated. Details: `VALTOZASOK_2026-10-02.md`.

**Fixes after the review, steps 1–2: safe config saving and charge control (`config.py`, `dashboard.py`, `charging_logic.py`, `main.py`, service file).** At the user's request the whole software was reviewed; these are the most serious of the findings.
* **Config:** saving was not atomic (a power cut during a save could leave a truncated file, after which the program would have started with defaults — password "admin", no device addresses or learned codes). Now a temporary file + replace, `config.json.bak`, and loading the `.bak` when the file is corrupt (user decision). Details: 6.2.
* **Form error:** `/api/config` validation errors no longer call `load_config()` (see 6.2).
* **Log:** output is flushed before a forced exit (`sys.stdout.flush()`), and the service file sets `PYTHONUNBUFFERED=1` — the last lines before a stop used to be lost.
* **Bluetooth dropout:** the controller treated a dropout as the end of charging and cleared `started_by_controller`; after reconnecting the session counted as externally started, so it was not stopped at the end of the time window or by the grid-import rule. Fix: `charger_ready` — the controller decides only when the charger is connected **and** telemetry has arrived since the (re)connect (`charger_telemetry_ok`).
* **Lockdown:** the lockdown also blocked the plain out-of-window STOP. Now the lockdown and the switching limits block only starting (6.1).
* **Start/stop cycling:** after a rule-based stop Solar Auto restarted immediately (it only checked the battery level). It now waits 5 minutes (`RULE_STOP_RESTART_WAIT_S`; user decision: fixed 5 minutes); a manual start is not affected.
* **Cooldown:** during a cooldown the stop rules did not run either. Now the cooldown blocks only starting.
* **Stop confirmation:** a STOP could be lost (BLE error) while the program assumed it was sent. It now checks the telemetry and repeats the STOP every 60 s (`STOP_CONFIRM_TIMEOUT_S`) until charging stops (user decision); `started_by_controller` is cleared by the confirmed stop, not by sending the STOP.
* **Energy counter:** after a telemetry gap longer than 30 s it stopped counting until the end of the session. Fixed.
* **Missing charging current:** on a manual start the controller died with an exception and the Watchdog restarted it every 10 seconds. Now an error message (`/api/force_submode`) and a one-time log line; a mode with missing (`None`) settings is skipped.
* **Verification:** new `config_save_test` (26 cases: interrupted save, concurrent saves, older snapshot, corrupt/missing file, `.bak`, unwritable folder, rejected forms) and `charge_controller_l2_test` (38 cases with the real controller and a virtual clock: dropout mid-window then window end, lockdown, the 5-minute wait, overload during cooldown, missing settings, STOP repeats, energy counter); the same test fails in 12 places on the pre-fix code. The earlier tests still pass. `--sim` check: the mode survives a rejected form; Solar Auto start → overload → STOP → confirmation → no immediate restart, no lockdown.
* **App fixes (`MainActivity.kt`, `AppDetailActivities.kt`, `AppUi.kt`, `DeyeWidgetProvider.kt`, `WidgetUpdateWorker.kt`):**
    * a cancelled request (leaving the screen) is not an error: `CancellationException` is rethrown, no "Nincs kapcsolat: … was cancelled" bar;
    * on a connection error only the tile container (`body`) is dimmed, not the header and the error bar;
    * the pulse animators pause in `onPause` and are cancelled in `onDestroy`;
    * `AppUi.action()`: while the same action (endpoint + body) is running, another tap does not start a second one; the manual charge start has its own flag;
    * detail screens: `saveButton()` is dimmed and does not save until the first successful load;
    * widget: the provider's full update writes back the last displayed values (`applyCached()`, stored in `DeyePrefs`), a tap asks the running loop for an immediate refresh (`refreshNow`); without a widget `enqueueLoop()` does not schedule, so the loop is not restarted after the last widget is removed.
    * **Verification:** local build; in the emulator with a delaying proxy in front of the simulation server: no "cancelled" bar after a cancellation (the old app showed it 4 times out of 4), Save sends no request before the load (the old app did), a double tap sends one request (the old app two), the error bar is at full brightness. **Not verified:** the two widget fixes and the animators (no widget was placed on the emulator) — to be tried on the phone.
* **Observation:** the post-dropout bug was timing-dependent in the old code — if the controller ran between the reconnect and the first telemetry, it re-sent START in scheduled mode (which accidentally "gave back" the session); otherwise the session counted as external.

### 2026-10-01

**App: tapping a tile opens the details, switching only with buttons (`MainActivity.kt`).** The LED strip and plug tiles are short, the small arrow was hard to hit, and tapping the tile switched on/off — opening the details often turned the lamp on.
* **Decisions (user, based on the mockup):** on the tiles with a detail screen (roller shutter, awning, LED, plug, radio) tapping anywhere on the tile opens the details; the LED and plug tiles got **Be / Ki** (on / off) buttons; the radio tile no longer sends a button press, only the "Be/Ki (power)" button does.
* **Verification:** in the emulator against the simulation server: tapping the plug, LED, radio and roller shutter tiles opened the details, and the plug and LED state and the radio's "Utoljára" time did not change; the Be / Ki buttons switched. (In the emulator the error bar toggling on and off — because of the truncated responses described earlier — shifted the screen, so taps had to be aimed from a fresh screen dump every time.)

### 2026-09-30

**Smart plug on the Egyéb tab, two on/off pairs per day (`config.py`, `home_devices.py`, `dashboard.py`, `AndroidWidget/`).** The plug switches a lamp, needed in the morning and in the evening; until now there was only one pair per day, and the plug was only visible in the app.
* **Decisions (user, based on the mockup):** two pairs only for the plug (the radio keeps one pair); labels "1. Be / 1. Ki / 2. Be / 2. Ki"; on the Egyéb tab side by side: radio in the left column, plug in the right; in the app one row per day with short day names.
* **Server:** the plug's days are `{on, off, on2, off2}` (`HOME_SCHEDULE_KEYS`); the timer checks all four; saving requires each device's own fields, all mandatory (a save from the old app is rejected and cannot delete the 2nd pair); old config → the 2nd pair is empty.
* **Web:** "Konnektor" card (state, Bekapcsolás / Kikapcsolás, last result) and "Időzítő" (timer) card with the four columns; the radio's cards moved to the left column (content unchanged).
* **App:** `ScheduleEditor` takes any number of columns (full day names for two, short names for more); `PlugActivity` with four columns.
* **Verification:** the `home_devices` test 80/80 (new: missing 2nd pair / old app rejected, saving two pairs, old config, 2nd-pair timer with a virtual clock and logs); the climate, shading and reachability tests pass. `--sim`: the two columns, on/off, saving two pairs and reloading them after a page reload; not reachable at 375 px. Emulator: the four columns fit, the saved times load, clearing one time and saving from the app showed up on the server with the rest unchanged.

**Internet radio + new "Egyéb" (other) tab (desktop web) (`home_devices.py`, `config.py`, `dashboard.py`, `AndroidWidget/`).** The user wants to control their internet radio too, like the plug (on/off + daily timer), by infrared, through the living room's BroadLink. The code is learned on the web dashboard, on a new **Egyéb** tab available only in desktop view (devices that fit nowhere else will go here later). Details: section 2, item 7 and section 4.
* **Decisions (user):** the remote has one power button → **one code**, "Be" and "Ki" send the same button press; only the radio for now (TV, humidifier etc. later); on the web learning + test + timer; in the app a tile (tap and button = power button press) and a detail screen with the timer.
* **Server:** `home_devices.radio` in the config (the BroadLink IP can only be set there); learning in a worker thread, sending, the radio timer in `run_home_devices()`; three new endpoints (`/api/radio/learn`, `/api/radio/send`, `/api/radio/schedule`). Saving the plug and radio timers goes through a shared atomic function (`_save_schedule()`); the plug's behaviour is unchanged.
* **Web:** Egyéb tab after Árnyékolás; "Internet-rádió" card (the BroadLink's name taken from the air conditioners when the IP matches, e.g. "Nappali BroadLink"; whether the code is learned, "Utoljára: power …"; Tanítás (learn) / Próba (test)) and an "Időzítő" (timer) card (warning about the power button, 7-day table, Mentés/save); the Log below.
* **App:** "Internet-rádió" tile ("Utoljára: power … · időzítő aktív", "Be/Ki (power)" button), `RadioActivity` (power button, timer), `ic_app_radio` icon.
* **Verification:** the `home_devices` test 72/72 (new: old config without radio, wrong types; learning without IP, with a bad request, with a stubbed BroadLink — code saved, not in the public state; a second operation rejected during learning; sending, failed sending; timer save with atomic validation, the plug's unchanged; timer with a virtual clock — power at both the on and off time, once per minute, not on an empty day, not when disabled, skipped while the radio is busy; simulation), the climate, shading and reachability tests pass. `--sim` browser test: learning, test, timer save and reload, the cards as in the mockup; at 375 px the tab is not visible and Measurements is shown instead of Egyéb. App: local build; in the emulator the tile, the button press ("Utoljára: power …"), the detail screen (saved timer loaded) and saving the timer from the app worked. The detail screen's first load in the emulator several times gave a "bad base-64" / truncated-response error (200/200 intact from the PC) — this is the emulator phenomenon described earlier; the screen fills in the form on its first successful load.

**Otthonvezérlő app (Android) + LED strip and Xiaomi smart plug (`home_devices.py`, `config.py`, `main.py`, `dashboard.py`, `climate_logic.py`, `climate_devices.py`, `AndroidWidget/`).** The user asked for a simplified, Xiaomi Home-like native app for the basic functions (reachable from outside via Tailscale), and two new devices: a CozyLife LED strip and a Xiaomi smart plug. Details: section 2, item 7 and 8.5.
* **Server:** new `home_devices.py` (CozyLife client, plug via the existing miIO client, per-minute queries, plug timer, under the Watchdog with a `"home"` PONG); three new endpoints (`/api/led/set`, `/api/plug/set`, `/api/plug/schedule`); for the air conditioners the last sent command and its time (`last_sent`) for the app's "Utoljára: …" line; the miIO client's error messages name the device (`label`). The web dashboard is unchanged.
* **App:** main screen (energy tiles with icons and colour code; charger, climate, shading, LED and plug tiles; pulsing when automation is on), detail screens (LED, plug, roller shutter, awning), settings, light/dark mode following the phone.
* **Verification:** a new test (48 cases: CozyLife with a fake TCP server — query, set, a state report arriving first, rejection, timeout, non-existent device; plug with a fake miIO server with five models/call styles; wrong key; atomic endpoint validation; timer with a virtual clock; simulation), the climate test with a `last_sent` case, all earlier tests pass; `--sim` test of the new endpoints; the app built locally (Gradle 8.11.1, JDK 17). **Live (on the NAS, 2026-09-30):** the CozyLife LED strip (firmware 1.0.8) is reachable and controllable locally — it reports the on state as `"1":1` (not 255); the program treats every non-zero value as on. The Xiaomi plug (`chuangmi.plug.hmi206`, legacy miio: `get_prop ["power"]` / `set_power`) can be queried and switched. The app works on the phone (at home and via Tailscale).
* **App fix after the phone test:** the tiles overflowed the screen — `GridLayout` measured longer texts at their natural width — so every grid was replaced by equal-weight `LinearLayout` rows (`AppUi.row()`, `AppUi.twoColumns()`); the pulsing became gentler and slower (the user's choice: white ↔ `#E0F2FE`, dark `#1E2128` ↔ `#163247`, 2.4 s cycle); the error bar is one line (first line, max. 80 characters). Verification: in a local emulator (Android 35) against the simulation server, light and dark mode, then on the phone.
* **Emulator observation:** in the Android emulator the end of the `/api/status` response was sometimes lost (truncated JSON, about 1 in 5 requests). Directly from the PC 200/200 were intact, and it never occurred in the app on the phone; by the user's decision the server is therefore unchanged (JSON responses are sent without `Content-Length`, their end is signalled by closing the connection).

**Android widget: refresh on mobile data too, through a VPN (Tailscale) (`WidgetUpdateWorker.kt`).** The user reaches the controller from outside the home via Tailscale; until now the widget only queried on Wi-Fi. The user's decision: query on Wi-Fi or through an active VPN, every 30 s on mobile data, keep 5 s on Wi-Fi. Details: 8.3. The server address (the NAS's Tailscale address) is entered by the user on the widget's settings screen; no code change was needed for that.
* **Verification:** The APK was built locally (8.5), the widget included. To be tried on the phone: 5 s on home Wi-Fi, ~30 s on mobile data with Tailscale on, blank on mobile data without Tailscale, 5 s immediately after returning home.

**Climate control: temperature offset removed, explanation line for the thresholds, phase-2 rule clarified (`config.py`, `climate_logic.py`, `dashboard.py`).** According to the user the per-unit temperature offset is unnecessary complexity; at switch-on the same target temperature applies to every air conditioner.
* **Offset removed:** the `temp_offset` field was removed from the configuration, the save validation and the dashboard. A `temp_offset` left in an old `config.json` is ignored at load time (`normalize_climate_config()` builds from the default structure) and disappears on the next save; a `temp_offset` sent in a request does not get into the config.
* **Target temperature with one decimal:** the field's step is 0.1 (the air purifier measures with 0.1 degree resolution).
* **Phase-2 rule (user decision):**
  * **calculated production** = export + the expected consumption of the running air conditioners, where export = −`grid_power` (on the meter the negative value is export, so it is positive when exporting and negative when importing);
  * the per-unit `surplus_w` is a **cumulative** level (e.g. 1500 / 2000 / 2500 W = 1, 2, 3 air conditioners at these levels);
  * **the switch-on order is given by the thresholds** (ascending; with equal thresholds the lower number first), switch-off in reverse order; no order check;
  * switch-on: calculated production ≥ threshold for X minutes, battery level, and for heating measured ≤ target, for cooling measured ≥ target (with one decimal); switch-off: the most recently switched-on unit stops if the calculated production stays below its threshold for X minutes.
  * This corrects the 2026-09-29 wording, which did not fix the meaning of the threshold.
* **Explanation line (`renderClimateExplain()`):** a live text under each unit's row that orders the units by the field values (by threshold) and shows whose expected consumption is added, e.g. "Bekapcsol, ha a visszatermelés + 1. klíma várható fogyasztása (600 W) eléri a 2000 W-ot." (switches on when the export + unit 1's expected consumption (600 W) reaches 2000 W). It runs on typing (`input` event), when the form is loaded and when the unit names are refreshed; with an empty threshold "Add meg a küszöböt." (enter the threshold), with an empty preceding consumption "–".
* **Verification:** the climate test passes 50/50 (offset cases removed; new: an old config with `temp_offset` loads without error and the field disappears; an offset sent in the request does not get into the config). `--sim` browser test: the explanation lines gave the expected text with 1500/2000/2500, with unit 3 having the lowest threshold, with equal thresholds, with an empty threshold and an empty consumption; saved, reloaded after a restart (target temperature 22.1); desktop and 375 px views.

### 2026-09-29

**Climate control: settings can be saved, "Várható fogyasztás (W)" (expected consumption) per air conditioner (`config.py`, `climate_logic.py`, `dashboard.py`).** Until now the settings fields of the Klímavezérlés card were only shown disabled. At the user's request they can now be saved; the automation (phase 2) does not use them yet, which the card text also says.
* **Configuration:** a new `climate.settings` block (`mode`, `auto_enabled`, `soc`, `target_temp`, `on_minutes`, `off_minutes`) and per air conditioner `surplus_w`, `expected_w`, `temp_offset` (the latter removed by the 2026-09-30 change). Numbers default to `None` (no hardcoded values); an old `config.json` → empty fields, automation off.
* **Expected consumption (`expected_w`):** the user's point — a running air conditioner reduces the export by its own consumption, so in phase 2 the decisions start from the **calculated production** (export + the expected consumption of the running air conditioners), and the per-unit threshold is a cumulative level — see the 2026-09-30 entry for the exact rule. Only the field and saving are done now.
* **Saving:** `POST /api/climate/settings` → `update_climate_settings()`, with atomic validation (see 6.2).
* **Dashboard:** the fields are editable; the Fűtés/Hűtés (heating/cooling) button toggles; the form is filled only once (`climateFormLoaded`) so that the 2 s refresh does not overwrite typing, and is reloaded after saving. The header Klíma badge shows the saved `settings.auto_enabled`. The "Klímánként" (per air conditioner) table has a new middle column; on mobile the headers are shortened (Bekapcs. (W) | Fogyaszt. (W); the Eltolás column was removed on 09-30).
* **Verification:** the climate test passes 50/50 (new cases: missing, `NaN`, out-of-range, string and `bool` values, a 2-unit list, missing/negative/string expected consumption are rejected, and then nothing changes and nothing is saved; valid data is saved, visible in the public state, and kept when reloaded from `config.json`; old config → empty). `--sim` browser test: rejected with an empty field, saved when filled in, the badge lights up, every value comes back after a restart; desktop and 375 px views.

**Shading and Boiler (placeholder) pages (`shading_logic.py`, `climate_devices.py`, `config.py`, `main.py`, `dashboard.py`).** The user wanted to control a radio roller shutter and awning with the BroadLink RM4 Pro, with a simple daily timer, the two devices switchable off independently, the setting stored in `config.json` (reloaded after a power outage).
* **New tabs:** Bojler (boiler — only a "Később." (later) card + Log) and Árnyékolás (shading); a new "Árnyékolás" badge and a grey "Bojler" badge among the Automations; new mobile dots and two new dock icons.
* **Shading page:** Redőny (roller shutter, Fel/Le — up/down) and Napellenző (awning, Be (Fel)/Ki (Le)) cards, with an "Időzítő aktív" (timer active) switch and a daily up/down time table (empty = no movement that day); a collapsible "Eszközök (RF-tanítás, próba)" section with step-by-step guidance.
* **Backend:** `run_shading_timer()` (task 5), atomic timer saving, RF learning in a worker thread, manual test; `climate_devices.broadlink_learn_rf()` (section 2, item 6); `config.py` `shading` block.
* **Mobile layout:** cards spanning two columns (`grid-column: span 2`) opened a hidden second grid column on mobile — the Boiler, shading devices and simulation cards use `grid-column: auto` on mobile.
* **Other:** in the "Eszközök" section the air conditioners got the "Klíma1–3" labels matching the header badges (with the room name next to them).
* **Verification:** a new shading test (25 cases: timer with a virtual clock incl. a day change; atomic validation; two-step RF learning; learning and sending), all earlier tests still pass; `--sim` browser test.

### 2026-09-27

**Inverter connection: a single late response does not drop it (`charging_logic.py`).** The log regularly showed the program closing and rebuilding the logger connection after a single timeout. The user's decision: keep the connection, close it only after two consecutive late responses, and show the indicator red on the first one. Details: section 2, item 1 ("A single late response does not drop the connection").
* **Verification:** a new test (6 cases: the connection is kept on the first late response and closed on the second consecutive one; an intermediate success or a rebuild resets the counter; other errors close immediately; a dropped connection is rebuilt immediately), the leak test (fake logger server: no orphaned connection remains) and the poll-timing test adapted to the new behaviour, passing.

**Climate control — phase 1: device connection and manual control (`climate_devices.py`, `climate_logic.py`, `config.py`, `main.py`, `dashboard.py`).** 3 non-smart air conditioners with BroadLink infrared transmitters (1 × RM4 Pro, 2 × RM4C Mini) and a Xiaomi Smart Air Purifier 4 as the thermometer (section 2, items 4 and 6).
* **Separate tab:** climate control is on a completely separate page and does not mix with the car charger UI (user request); on desktop the Klímavezérlés card is on the left, the Measurements card without the car charger part on the right, with the "Hőmérő és klímák" rows.
* **Device settings only in `config.json`:** the IP addresses and the air purifier key cannot be set on the dashboard (the former `update_climate_config()` was removed); the learning and test buttons moved to the collapsible "Eszközök (tanítás, próba)" section.
* **Codes:** per air conditioner heating ON, cooling ON and OFF (the air conditioners use separate ON/OFF codes).
* **Settings preview:** the automation settings on the Klímavezérlés card as disabled fields (made saveable by the 2026-09-29 change).
* **Verification:** a test with a fake air purifier UDP server (independent miIO implementation: read, wrong key, silent device, invalid key format) and a stubbed BroadLink library (learning, sending, timeout without a button press, locked device, corrupt stored code, busy unit, invalid parameters); live on the NAS: the purifier readings matched the Mi Home app, two air conditioners' codes were learned and tested.

**Rename and new header (`dashboard.py`).** The program's new name is **Otthonvezérlő** (home controller; subtitle "Helyi autótöltés és klíma vezérlő." — local EV charging and air conditioner controller). The header now has two rows: on top the title, the Connections (Inverter, Töltő, Hőmérő, Klíma1–3) and Automations (Auto solar, Auto ütemezett, Klíma, …) badges and logout; below it the tabs. On mobile two status rows (`Kapcs.:`, `Auto.:`). File and service names (`deye_besen_controller.exe`, `deye-besen-controller.service`) are unchanged.

### 2026-09-26

**Logout on a broken/empty page (`dashboard.py`).** The user occasionally (mostly on mobile) got an empty, broken dashboard on which neither reloading nor a new tab could force a fresh page, and the logout button was not visible either, so they could not log in again. **Cause:** the logout buttons (`#mobile-logout-btn`, `#logout-group`/`#logout-divider`) started hidden and only appeared after a successful `/api/status` response. **Likely background (not proven):** the session cookie is valid (so the server serves the dashboard) but the encryption key is missing from the tab-bound `sessionStorage`, so the encrypted status cannot be read; the existing "decryption failed" branch also led here (it removed the key and reloaded). JS cannot delete the HttpOnly cookie, so reloading did not help.

* **The server fills in the buttons' initial visibility** when serving `GET /` (placeholders, from `WEB_AUTH_ENABLED`), together with the `_WEB_AUTH_ENABLED` JS constant. `updateStatus()` only touches the buttons for a `boolean` `web_auth_enabled` value, so an incomplete response cannot hide them again.
* **Automatic logout when loaded without the key:** with password protection enabled, if there is no key in `sessionStorage`, the page immediately calls `/api/logout` and reloads after a successful logout → login page. The 2 s status polling does not start in that case. On a network or unexpected error there is no reload (no loop is possible) and the button is visible. It never runs with protection disabled (there is never a key there, and the dashboard would come back after logout too).
* **`logout()`:** always removes the `sessionStorage` keys and reloads regardless of the response content; it shows an `alert` only on an actual network error.
* **Verification:** with the exe running in `--sim` mode, in a browser: after a normal login the button is visible in desktop and mobile (375 px) views; after removing the `sessionStorage` key and reloading, the page switched to the login page by itself, and after logging in again a normal page appeared; in the HTML sent by the server the buttons arrived with a visible style and the `_WEB_AUTH_ENABLED = true` flag (independent of the status); the manual logout button led to the login page. Running the automatic-logout snippet read from the page with a stubbed `fetch`: 0 reloads on a network error (no loop), 1 on a successful logout, and it does not run with protection disabled.

**Less frequent logger polling during active charging (`charging_logic.py`).** The user observed that when the inverter runs at peak (typically while the car charges), the Deye WiFi logger answers only about every 2nd-3rd request. The user's decision: 20 s during active charging in every mode, 10 s otherwise.

* **Interval:** `_inverter_poll_interval()` returns 20 or 10 s based on `shared_state["charging_active"]` (the charger's BLE telemetry, reliable without the inverter). The error message ("Újrapróbálkozás N másodperc múlva…", i.e. "retrying in N seconds") shows the actual interval; on errors during charging it is also 20 s.
* **Chunked wait with PONG (`_inverter_poll_wait()`):** the Watchdog cancels a task after 30 s without a PONG. Previously the PONG came after the poll, followed by a single sleep, so the gap = sleep + poll time; with a 20 s sleep a slow failing poll would have caused a false "freeze". Now the wait runs in steps of at most 5 s with a PONG before each step and at the end, so the gap is at most one step or one poll.
* **Accepted consequence:** during Solar Auto charging the house overload / low SoC / grid import stop can react up to about 10 s later; the charger's own DLM still protects. Whether the lower rate improves the logger's response ratio is not proven — to be checked live with the hourly error counts.
* **Verification:** a test against the real `run_inverter_polling()` with a virtual clock and a stubbed poll: 10 s between polls without charging, 20 s while charging (on top of the poll time); after charging stopped the next poll came within 5 s, then a 10 s rhythm; with a 12 s failing poll the largest PONG gap was 12 s and the log showed the right interval. Control: the old structure with a 20 s sleep gave a 32 s gap for the same poll (a Watchdog alarm).

**Windows build environment: Python 3.9 → 3.14.** The Python 3.9 base installation on the development machine got damaged (`python.exe`, `python39.dll` and the `DLLs` folder were missing), and 3.9 is end-of-life. A new virtual environment (`.venv314`) was created with Python 3.14.7 and the latest packages: `bleak` 0.21.1 → 3.0.2 (the `winrt-*` packages instead of `bleak_winrt`), `pyinstaller` 6.22.3, `pysolarmanv5` 3.0.6, `pycryptodome` 3.23.0. The bleak calls used by the code (`find_device_by_filter`, `connect`, `disconnect`, `write_gatt_char` with an explicit `response`, `start_notify`, the `is_connected` property) are unchanged in the new version. The Linux version running on the NAS was already installed unpinned with the latest packages (`LinuxController/requirements.txt`) and is not affected. **Verification:** importing every module under 3.14, the 2026-09-20 integration test and the polling-interval test, building the exe and running it with `--sim`. **Not verified:** a real Bluetooth connection of the Windows exe (`--sim` mode does not use BLE).

**Inverter `socket_timeout` 10 → 15 s, PONG after every step of a poll (`charging_logic.py`).** Since the 09-19 fix the user noticed more connection problems. A daily breakdown of the NAS log (successful polls / errors) confirmed it: the error ratio was 1-6% on the days before the fix (09-15…09-17) and 4-26%, about 13% on average, after it (09-19…09-26) (09-18, the leak day, 79%, not representative). By error type, **timeouts** (empty-message error lines) grew from 62-258 to 203-1240 per day (about 600 on average), while "No socket available" stayed roughly the same (9-142 → 55-224). **Correction to the 2026-09-19 entry:** reducing `socket_timeout` from 15 to 10 s was a wrong decision — under load the logger often answers in 10-15 s. The reasoning given there ("connect + one read = 20 s, well under the Watchdog limit") was also inaccurate: one poll is 6 separate reads, each with its own timeout.

* **`_INVERTER_SOCKET_TIMEOUT = 15`** (the library uses it for connecting and for every response wait).
* **`_inverter_pong()`:** `fetch_inverter_data_blocking()` sends a PONG after acquiring the lock, after every `_open_inverter()` and after every single read (from the worker thread, under the `state_lock` RLock). The Watchdog gap is thus at most one operation (≤15 s) regardless of the total poll length; a genuinely stuck step (or an `_inverter_lock` held by another thread) gets no PONG, and the Watchdog still catches it.
* **Verification:** a test against the real `fetch_inverter_data_blocking()` with a stubbed `PySolarmanV5` and a virtual clock: with a 12 s response time the poll succeeds (with the 10 s limit — control — it times out), the largest PONG gap is 12 s while the whole poll took 73 s (which would have been the gap with the old structure); a 16 s response or connect times out, the connection is closed, gap ≤15 s; a dropped persistent connection plus a slow logger is rebuilt immediately, gap ≤14 s. The leak test (fake logger server: no orphaned connection/thread left), the polling-interval test and the red-inverter integration test still pass.

**Inverter telemetry with a single request instead of 6 (`charging_logic.py`).** A poll used to be 6 separate Modbus requests (672, 607, 619, 588, 643, 590); each waited for its own response and a timeout on any of them failed the whole poll — 6 requests every 10 s to a logger already struggling under load. The registers fall into one range (588…673, 86 registers), so a single request now reads them by default.

* **`_read_inverter_registers()`:** `read_holding_registers(588, 86)` with a response-length check (incomplete response → `ValueError`, the usual error handling); the values are derived by the new `_inverter_data_from_regs()` (unchanged interpretation: `to_signed_16`, `charger_power`). A PONG after every read.
* **Fallback:** on an "unsupported" Modbus error (`IllegalDataAddressError` / `IllegalFunctionError` / `IllegalDataValueError`) it switches, within the same call and on the same connection, to the 6 separate requests and stays there for the rest of the process (`_inverter_range_read`), with a log line. Transient errors do not switch.
* **Not verified in advance:** whether the real logger/inverter accepts the 86-register range — this shows up live (`[INVERTER] Olvasási mód: …` or the fallback log line), and the daily error-ratio summary shows the effect.
* **Verification:** a test with a stubbed inverter: on 500 random register tables (signed values included) the range read gives exactly the same result as the pre-change, per-register `_read_inverter_registers()`, and sends only 1 request; on an "illegal address" error correct data in the same call and a switch, after which the range is not tried again; no switch on a timeout, `ServerDeviceBusyError` or an incomplete response; the mode log line appears once. The 15 s timeout test, the leak test, the polling-interval test and the red-inverter test also pass on the final code.

### 2026-09-20

**Force and fixed-current scheduled charging without the inverter connection.** The user repeatedly could not start charging because the inverter (Deye WiFi logger) connection was red — although neither the battery nor the sun mattered to them. The logger drops out often in the evening (around 16-23 h, judging by the hourly error counts in the log), exactly when they usually charge, and resetting the logger was the only workaround. **Cause:** `run_charge_controller()` skipped the whole iteration (`continue`) before the mode branching whenever `not inverter_ok or not charger_ok` — which also blocked Force (manual) mode, fixed-current ("Override Solar Auto") scheduled charging and the stop outside a window, although none of them use inverter data. Only the Solar Auto rules use inverter data (`battery_soc`, grid and UPS power). Manual stop (`apply_with_stop`/`apply_with_restart`) already ran before the condition, which is why it worked.

* **Split connection condition** (`charging_logic.py`): the charger (BLE) connection is required in every mode; the inverter connection only when `use_solar_auto_rules` is true. The `use_solar_auto_rules` computation (it depends only on the mode and the schedule) moved above the condition, with unchanged content. Solar Auto still makes no decisions from stale inverter data.
* **Log line:** when a Force or fixed-current scheduled start happens without the inverter connection, a `[VEZÉRLÉS] Inverter-kapcsolat nélkül indult a töltés (…)` line is logged (the text is Hungarian: "charging started without the inverter connection").
* **Protection level:** the Force and fixed-current scheduled branches never had software safety stops (house overload, grid import, low SoC), so this change does not reduce protection compared to a green inverter; without the inverter connection only the charger's own DLM protects in these modes. A Solar Auto session already running still gets no decisions during an inverter outage (an existing, unchanged limit).
* **Verification:** an integration test against the real `run_charge_controller()` with stubbed `shared_state` and `ble_command_queue` (10 cases: Force, scheduled fixed, stop outside a window, Solar Auto, scheduled Solar Auto, red charger, green-inverter controls). On the unmodified code, Force start, fixed-current scheduled start and the stop outside a window sent **no** command without the inverter (the bug reproduced); after the change all three went out, and the other cases are unchanged.

### 2026-09-19

Fix for orphaned connections to the inverter (Deye WiFi logger). **Symptom (2026-09-18):** after 19 days of uptime the dashboard and Android widget became unreachable and the inverter status turned red, while the BLE heartbeat kept running; the log showed `[Errno 24] Too many open files` every ten seconds (the program could no longer open new sockets — neither toward the inverter nor for the web server). `htop` showed roughly 375 threads in a single process.

**Diagnosis (measured on the live process, with a py-spy dump):** 41-42 ESTABLISHED connections to the logger (`:8899`) instead of one; of 198 open descriptors, 126 were pipes (63 pipe pairs) and 70 sockets; 63 threads were sitting in `pysolarmanv5._data_receiver` (the library's reader thread) and 3 in the `multiprocessing` queue `_feed` thread. For each `PySolarmanV5` instance the library creates a `multiprocessing.Queue` (2 pipe descriptors), a socket and a reader thread — so roughly 63 instances were alive instead of one. The web server was not leaking. The orphaned connections also occupied the logger's very few free connection slots, which explains the frequent "red inverter" state and the need for logger resets; in the end the descriptors ran out (systemd default: 1024). The new process accumulated 63 orphans within a few hours of the 16:53 restart, so the leak is event-driven (logger outage, slow response, reset), not a function of uptime.

**Mechanism (reproduced with a local test):** `fetch_inverter_data_blocking()` created the connection with `PySolarmanV5(..., auto_reconnect=True)`. When the logger drops the connection, the library's *reader thread* calls `_reconnect()`, which can block on creating the new socket for up to `socket_timeout` seconds. If our query fails in the meantime and the error handling calls `disconnect()` and drops the instance, `_reconnect()` finishes by undoing the shutdown signal (`_reader_exit.clear()`) and starts a new socket and a new reader thread on an instance nobody references anymore — the orphan runs forever and reconnects on its own whenever the peer drops it. Each orphan takes up more logger slots, causing more timeouts (a self-reinforcing loop). Measured against a fake logger server (localhost, slowed reconnection), after 6 drop cycles: with the previous setting (`auto_reconnect=True`, `socket_timeout=60`) and the most recent one (`socket_timeout=15`) alike, 6 live orphan connections and 6 running reader threads remained — exactly +1 per cycle; with the fixed setting, 0 and 0. The test also showed that the 2026-08-14 timeout change (60 → 15 s) made no difference to the leak.

**Correction to the 2026-08-14 entry:** for the 2026-08-14 incident (~30 program instances in `htop`, decryption error on the dashboard/widget) we identified the root cause as the combined limitation of the Watchdog's `task.cancel()` and `asyncio.to_thread()`, and fixed that (`socket_timeout=15`, `_inverter_lock`). That limitation is real, but based on today's measurements the symptoms back then were most likely this same orphaned-instance leak — the earlier fix did not cover it.

* **`auto_reconnect=False`** (`charging_logic.py`, `_open_inverter()`): the library's internal reconnection is gone, so there is no orphan instance that can be resurrected. The program's own error handling already builds a new instance for the next call, so the built-in reconnection was redundant.
* **`socket_timeout` 15 → 10 s:** the worst case within one call is connect (≤10 s) + one read (≤10 s) = 20 s, well under the Watchdog's 30 s threshold.
* **Immediate rebuild on a closed connection:** if a read on an existing instance fails with an instant connection error (`NoSocketAvailableError`, `ConnectionError`, `AttributeError`), the instance is closed and rebuilt once within the same call, so a dropped persistent connection no longer flashes the inverter status red. There is deliberately no retry on a timeout (it would increase the call's running time).
* **Reliable teardown** (`_close_inverter()`): after `disconnect()` it waits for the reader thread to stop and also closes the instance's `multiprocessing.Queue` (freeing the pipe descriptors), but only once the reader thread is no longer running.
* **Watchdog resource monitoring** (`main.py`, `fd_census()`): every 10 minutes the Watchdog logs one summary line with the open descriptors broken down by type (`[WATCHDOG] Erőforrások: fd=… (socket=…, cső=…, egyéb=…), szál=…` — the logged text is in Hungarian; `cső` = pipe, `egyéb` = other, `szál` = threads), at 300 the line becomes a `[WATCHDOG WARNING]`, and above 700 the Watchdog logs a critical message and exits with `os._exit(1)` so that systemd (`Restart=on-failure`) restarts the process before the 1024 limit is reached. Linux only (`/proc/self/fd`); elsewhere, e.g. on Windows, it skips itself.

**BLE side (a long Bluetooth outage, e.g. a dropped USB receiver, and the reconnection cycle during it):** cannot be tested without hardware, so here there is observation rather than a guarantee. By reading it was structurally fine (each attempt builds a new `BleakClient`, and the `finally` branch closes it), and the BLE side showed no leak in the leak dump. One real gap was found and fixed: the four `client.disconnect()` calls (telemetry-watchdog branch, `finally` branch, `safe_ble_write()`, `safe_ble_start_notify()`) ran without a timeout, while `bleak`'s BlueZ backend waits for the D-Bus `Disconnect` call with no timeout (only the signal it waits for afterwards has its own 10-second limit). If BlueZ stops answering, the disconnect could hang and was only released by the Watchdog's forced `task.cancel()` (~30 s), leaving the teardown half-done. The new `safe_ble_disconnect()` (`charging_logic.py`) guards every disconnect with a 12-second `asyncio.wait_for` (deliberately above `bleak`'s own 10-second limit, so a healthy but slow disconnect is not cut short), logs a hang as a `[BLE DISCONNECT TIMEOUT/ERROR]` line, never raises, and lets `CancelledError` (the Watchdog's cancellation) through. This does **not** guarantee that `bleak`'s internal D-Bus connection is closed after a timeout (we deliberately do not build on `bleak` internals); the Watchdog's resource counter (300/700 thresholds) remains the safety net and will show, by source and over time, if the BLE side ever does leak.

### 2026-08-14

The scheduled-charging feature (`forced_schedule`) had never been tested live by the user — a thorough review found several related issues in the previous **single start-stop window per day** model: (1) `start == stop` ambiguity (produced an unintended ~16-hour window), (2) a silent priority conflict between adjacent days (an overnight window and the next day's own window — the latter always won, causing an unnoticed STOP+RESTART at midnight), (3) a fundamental data-model limit (only one window per day was representable at all).

* **Multiple time windows per day, midnight-spanning windows removed:** each day's `forced_schedule` entry is now `{day, enabled, windows: [...]}` — `windows` is a variable-length list of independent time windows. Every window is contained within a single calendar day (`start < stop`, `stop` accepts the special end-of-day value `"24:00"`) — there is no more cross-day wraparound logic in `charging_logic.py` (the previous `current_sched`/`prev_sched` dual-branch computation collapsed into a single loop over the current day's windows). Overnight charging is now configured as two consecutive daily windows. `config.py`'s `load_config()` automatically migrates old (single-window-per-day) `config.json` files to the new structure, without breaking existing deployments.
* **Same-day overlap rejected, touching windows allowed:** `validate_forced_schedule()` (dashboard.py) rejects a save if two windows on the same day actually overlap; touching windows (one's end equals the next one's start, e.g. `08:00-12:00` then `12:00-16:00`) are allowed — this is the normal way to represent continuous charging. No new runtime code was needed for this: the existing `active_current_limit != target_amps` restart logic (`charging_logic.py`) already only triggers a STOP+RESTART at a window boundary when the amperage actually changes.
* **`dashboard.py` UI (`renderScheduleFromDraft()`, `saveSchedule()`):** the previous fixed one-row-per-day UI was replaced with a variable number of time-window rows per day, with "+ Add charging time" / "✕" delete buttons. `saveSchedule()` now checks the server's response and shows an error message on a failed save (the previous version always alerted "Schedule saved!", even on failure).
* **Custom HH:MM time input for entering `"24:00"`:** the native `<input type="time">` element cannot physically hold `"24:00"` at the browser level (its max is `23:59`). After several intermediate approaches (a checkbox that silently substituted a fake `23:59` display value, a plain text field, a `<select>` dropdown), the final solution is a custom two-`<input type="number">` component (`scheduleTimeInputHTML()`, `scheduleTimeHourChanged()`, `scheduleTimeMinuteChanged()`, `scheduleTimeReadValue()`): the hour field accepts `0`-`24` (no wraparound, clamps at `24`), the minute field accepts `0`-`59` and wraps to `00` past `59`. When the hour is `24`, the minute field is automatically forced to `00` and disabled (`24:15` would be meaningless). Whatever is entered is carried through, verbatim, as an `"HH:MM"` string into `scheduleDraft` — no silently-substituted or misleading value.
* **Missing `load_config` import (a separate, previously undiscovered bug):** `dashboard.py` called `load_config()` in five places to roll back an invalid save (numeric fields, SoC thresholds, and now the schedule), but the function was never imported at the top of the file. Any rejected save would have thrown a confusing `NameError` instead of a clean error message — this was only discovered today, during live browser testing, before any real user ever hit it. Fixed: `load_config` added to the imports.
* **Inverter-polling zombie-thread accumulation during a sustained network outage:** starting around 11:16 today, the `[WATCHDOG CRITICAL] Freeze (Timeout): the inverter task hasn't sent a PONG in 30s...` line repeated continuously for several hours, while `htop` showed roughly 30 running processes/threads, and the web dashboard and Android widget both became unreachable with a decryption error. Root cause: `fetch_inverter_data_blocking()` (`charging_logic.py`) constructed its `PySolarmanV5` connection without an explicit `socket_timeout`, so the library's 60-second default applied — longer than the Watchdog's 30-second no-PONG tolerance. The Watchdog's `task.cancel()` only cancels the `asyncio.Task`, though; it cannot interrupt the actual OS thread the blocking call runs on via `asyncio.to_thread()`. Under a sustained outage (today likely caused by a broader radio/network disruption, which also produced a parallel series of BLE `br-connection-canceled` errors), this left one more stuck "zombie" thread behind on every restart cycle. Compounding the problem, the shared `_persistent_inverter` connection object was an unsynchronized global, so multiple concurrent zombie threads could touch it without coordination. Fix: an explicit `socket_timeout=15` (comfortably under the Watchdog's window), plus a new `_inverter_lock` (`threading.Lock()`) guarding the entire body of `fetch_inverter_data_blocking()`.

### 2026-08-13

A fatal, whole-process freeze fix, plus three related fixes that reduce how often the system runs into the state that triggered it. The incident: the process running on the NAS entered a Lockdown event on 2026-08-13 and froze **permanently** (not just temporarily) — the entire process (BLE, telemetry, web-server monitoring, even the Watchdog) stopped responding until the user manually restarted the systemd service. The Watchdog couldn't intervene because it runs on the very same asyncio event loop as the code that froze — if the loop itself blocks, the Watchdog's own `await asyncio.sleep(5)` freezes right along with it.

* **Self-deadlock in `log_message()` (the direct cause of the freeze):** `try_send_ble_action()` (`charging_logic.py`) called `log_message()` on several branches while still holding `state_lock`. `log_message()` (`config.py`) itself also tried to acquire the same lock — since it was a plain, non-reentrant `threading.Lock()`, this second acquisition attempt **blocked forever**, synchronously, on the process's single event-loop thread, freezing the entire process. Fix: `log_message()` calls were moved out of the `state_lock` block (only a flag is set inside the lock; logging happens after it's released); as a safety net, `state_lock` is now a `threading.RLock()` (`config.py`), which also prevents self-deadlock in any other, not-yet-discovered nested-lock pattern.
* **`consecutive_auto_commands` — unbounded counter replaced with a 5-minute sliding window:** see the updated Section 6.1, item 3. The old, never-decreasing counter meant a perfectly healthy, purely automated system would theoretically have hit the lockdown threshold eventually, purely with the passage of time — combined with the deadlock above, that would also have meant an immediate, permanent freeze.
* **Safe `pull_plug` default + explicit guard:** `pull_plug` (charging-cable state) now defaults to `True` instead of `False` (`config.py`) — so until real telemetry arrives from the charger, the system assumes "cable not plugged in" rather than "plugged in". An explicit `not pull_plug` condition was also added directly before both the Solar Auto and Scheduled START branches, so the protection no longer relies solely on the shared early-loop `continue`.
* **SoC hysteresis enforced on save:** see the updated Section 6.2 — `/api/config` now rejects a save if the gap between Start and Stop SoC % is less than 2 percentage points.

### 2026-08-08

Two independent fixes: web server thread accumulation (whose symptom was an occasionally recurring data-less/incomplete dashboard on mobile), and a mobile layout bug on the Measurements page.

* **Web server thread accumulation — socket timeout + Watchdog supervision:** the root cause was that `ThreadingHTTPServer` spawns a new thread per connection with no timeout — a stuck connection from a mobile client whose radio went to sleep mid-response never released its thread. `ControllerHTTPHandler.setup()` now sets a 30-second `socket.timeout` on every connection. A new `ControllerHTTPServer` subclass (of `ThreadingHTTPServer`) overrides `service_actions()` — called by `serve_forever()` on every cycle (roughly every 0.5s), regardless of client traffic — to send a `"web"` PONG heartbeat into `shared_state["task_pong"]`. The central Watchdog loop in `main.py` now also supervises the web server thread using this heartbeat: on a full crash (`web_thread.is_alive() == False`) it safely restarts just that thread; on a freeze (thread alive but no PONG for 30s) — since a native Python thread cannot be safely interrupted from the outside — it forces the entire process to exit (`os._exit(1)`), which the `deye-besen-controller.service`'s `Restart=on-failure` + `RestartSec=5` automatically restarts without human intervention.
* **`dashboard.py` — Measurements-page mobile layout bug (right-side icon dock pushed off-screen):** in mobile view, exclusively on the Measurements page, the right-side icon dock (see the 2026-07-31 entry below) rendered past the edge of the viewport — only partially visible, or fully invisible without zooming out. Three contributing causes/fixes:
    1.  **Swapped which element owns `overflow-x`:** the original `html, body { overflow-x: hidden; }` rule gave both elements an explicit overflow value, which — per the CSS Overflow spec — blocks the normal `body`→`html` propagation and makes `body` establish its own independent scrolling box (a double scrollbar; the Measurements page required two upward swipes to actually scroll). Fix: `overflow-x: hidden` now lives only on `body` (removed from `html`), so the standard propagation applies (a single scrolling box), while `body` still directly clips its own horizontal overflow (preventing the browser from widening the layout viewport because of an overflowing descendant).
    2.  **`.phase-table { table-layout: fixed !important; }`** (mobile media query): the phase voltage/current table inside `#active-charging-view` did not respect its shrunk flex container (`min-width: 0`) — a plain `<table>` without `table-layout: fixed` won't shrink below its content's natural minimum width, so it grew to 403px inside a 344px box.
    3.  **`.telemetry-status-rows > div > div:last-child .tooltip-text`** right-alignment (mobile media query): the tooltips on the "Belső hőfok" (internal temperature) and "Állapot" (status) rows — outside `.metric-grid`, in a separate `justify-content: space-between` section — never received the right-aligned tooltip treatment that `.metric-grid` items already had (`right: -10px; left: auto`), so with the default centered positioning, the right-column items' 220px-wide tooltip box extended past the screen edge. Adding the `.telemetry-status-rows` class brought this wrapper under the existing right-alignment rule.

### 2026-07-31

* **`dashboard.py` — mobile navigation: hamburger menu replaced with a right-side icon dock:** the previous full-screen, centered hamburger-overlay menu (5 text menu items) was replaced with an always-visible, semi-transparent, vertical icon dock on the right edge of the screen in mobile view (`position: fixed; right: 0; bottom: 12vh`), positioned in the lower third of the screen for comfortable one-handed, right-thumb reach. The 5 icons (sun/Auto Solar, clock/Scheduled, hand/Force mode, activity/Measurements, document/Log) are icon-only SVGs, no labels or tooltips. The Logout button moved into the header as a small icon, only visible when `web_auth_enabled` is true. Force mode (`#config-force`) now gets `min-height: calc(100dvh - 210px)` in mobile view so its control buttons always render in the lower third of the visible area, at a consistent position optimized for one-handed use.
* **`dashboard.py` — silent E2EE decryption failure fixed (forced re-login):** On mobile, reopening a previously loaded page (after closing/reopening the browser, likely due to mobile bfcache/tab-freeze behavior) sometimes skipped the login prompt and showed stale/incomplete data — e.g. the charger-amps slider stuck at the hardcoded `16` HTML default. Root cause: in the client-side `window.fetch` override, if `_pskDecrypt()`'s MAC check failed (because the page's frozen JS state — including a stale session key — survived the closure while the server had since restarted / issued a new session key), the `catch` block **silently swallowed the error** and returned the original, still-encrypted JSON blob to the caller instead of the real data. In `updateStatus()`, this made every numeric field `undefined`, which the DOM-update code (`.value = data.charger_max_amps`) silently dropped on a `range` input — no error message, no forced re-login. The `catch` block now mirrors the `401 Unauthorized` branch: it clears the local session key (`sessionStorage`) and forces a page reload on any decryption/MAC failure. This guarantees stale or incomplete data can never render silently, and also forces more frequent re-authentication as a security side benefit.

### 2026-07-29

* **`config.py` — hardcoded numeric defaults removed:** The six control-critical fields (`start_soc`, `stop_soc`, `stop_import_limit`, `grid_charge_duration_minutes`, `house_power_limit_w`, `charger_max_amps`) in both `DEFAULT_CONFIG` and the `shared_state` initializer now default to `None` instead of plausible-looking numbers. `load_config()` is now `None`-tolerant: it only calls `int()` on a field if the loaded value is not `None`. If `config.json` is missing, these fields remain `None`; any comparison in `charging_logic.py` will then raise `TypeError`, which the `main.py` watchdog catches and handles by restarting the task — charging never starts with fabricated values.
* **`charging_logic.py` — `0 → 16A` conversion removed:** Three occurrences of `start_amps = 16 if charger_max_amps == 0 else charger_max_amps` (and one for `target_amps`) deleted. The old dual meaning of `charger_max_amps == 0` ("unmanaged" vs. missing/corrupt data) is eliminated; `charger_max_amps` is now always a concrete 6-16A value enforced by the server-side validation.
* **`dashboard.py` — "software current-limit disable" feature removed:** The "Töltőáram szoftveres szabályzásának kikapcsolása" checkboxes (`auto_unmanaged_current`, `force-unmanaged-container`) and the `toggleUnmanagedCurrent(mode)` JS function, along with all `unmanaged` branches in `scheduleAmpsSave`, `checkAutoAmpsChanged`, `checkForceAmpsChanged`, and `updateStatus()`, have been deleted. The charger-amps slider always shows a concrete 6-16A value.
* **`dashboard.py` — `saveAutoConfig` empty-field coercions removed:** The `|| 100` and `|| 0` fallback coercions on five numeric fields have been removed. An empty input now sends `null` to the server (via `parseInt("") === NaN` → `JSON.stringify(NaN) === null`), which the server's atomic validator rejects. On error, the client shows the server's message and calls `updateStatus()` to repopulate the form with the last saved values.
* **`dashboard.py` — `/api/config` atomic validation:** See the updated Section 6.2. The original sequential field-by-field mutation replaced with a validate-first, apply-atomically model with `load_config()`-based rollback on any invalid input.

### 2026-07-08

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

`widget_info.xml` sets `updatePeriodMillis="0"` — the system performs **no** periodic update; the widget manages refreshes itself with a continuous loop. `WidgetUpdateWorker.doWork()` iterates while the screen is on (`PowerManager.isInteractive`). At the start of each round `currentNetMode()` decides: **WIFI** if any connected network is Wi-Fi (not just the active one — with Tailscale on, the active network is the VPN itself); **VPN** if there is no Wi-Fi but the active network is a VPN (`TRANSPORT_VPN`, e.g. Tailscale on mobile data); otherwise **NONE**. With NONE there is no request, a blank/transparent state is shown (it does not probe addresses on foreign networks). The pause between rounds is 5 s on Wi-Fi (and with NONE), 30 s over a VPN (to save mobile data). On a locked screen the loop stops on its own (power saving).

### 8.4 Wi-Fi resilience (network-switch handling)

A prior bug caused the widget's data to get "stuck" when the user left their own Wi-Fi range and later returned. The root causes were fixed across several layers:

* **`InterruptedException` handling and self-restart:** WorkManager stops the Worker at the 10-minute runtime limit by interrupting the thread, which makes `Thread.sleep()` throw `InterruptedException`. Previously nothing caught it, so `doWork()` died by exception and never restarted. Now a `try/finally` catches it, and the `finally` block re-enqueues the loop with the `REPLACE` policy, **guaranteeing** a restart as long as the screen is active.
* **15-minute heartbeat (`WidgetKeepAliveWorker`):** a periodic `PeriodicWorkRequest` that revives the main loop with the `KEEP` policy if it has died for any reason (process death, undelivered broadcast). It does nothing while the loop is alive. WorkManager persists it across device reboots.
* **Network callback:** while the loop runs, a `ConnectivityManager.registerNetworkCallback()` watches Wi-Fi (`TRANSPORT_WIFI`) availability. On returning home, as soon as Wi-Fi is available again the waiting cycle breaks immediately and refreshes — no need to wait out the 5 seconds.
* **Captive-portal login validation:** `doLogin()` no longer accepts any HTTP 200. It stores a session only if the response truly came from our server: a JSON `{"status":"success"}` body **and** a real `session=` cookie. Without this, a redirect page's HTTP 200 on a foreign network would "poison" the session with an empty token.
* **Tap fallback:** tapping the widget makes `onUpdate()` restart the loop with `KEEP`, as a manual last resort.

The `ScreenUnlockReceiver` (`USER_PRESENT` / `SCREEN_OFF` / `BOOT_COMPLETED`) is only an accelerator on devices where the event is delivered; because of Android 8+ implicit-broadcast restrictions (and the fact that `SCREEN_OFF` cannot be delivered to a manifest receiver), refresh reliability rests on the mechanisms above rather than on it.

### 8.5 The Otthonvezérlő app (in the same APK)
* **Screens:** `MainActivity` (launcher icon, main screen), `LedActivity`, `PlugActivity`, `RadioActivity` (internet radio: power button and daily timer), `ShadingActivity` (roller shutter and awning, `device` parameter), `AppSettingsActivity` (server address and password). The UI is built in code (`AppUi`), with light/dark colours (`values/app_colors.xml`, `values-night/app_colors.xml`, `Theme.Otthon` = `Theme.MaterialComponents.DayNight.NoActionBar`) and vector icons (`ic_app_*`). The widget code is unchanged.
* **`AppApi`:** the same protocol as the widget (PSK login, `CryptoUtils`) but its own session. The widget only decrypts; the app also encrypts requests, like the web dashboard (AES-256-CBC, HMAC-SHA256 over IV + ciphertext, `{"iv","data","mac","enc":true}`). On 401 it logs in again once. Address and password come from the `DeyePrefs` shared with the widget (the widget's settings screen cannot be opened without a widget ID, so the app got its own settings screen for the same stored setting).
* **Refresh:** `/api/status` every 2 s in the foreground, no queries in the background; immediate refresh after a command; on error a "Nincs kapcsolat" (no connection) bar and dimmed content.
* **Charger:** Start = `/api/force_submode` `manual_start` + `/api/mode` `force` (like the web "Kézi indítás"); Stop = `/api/config` with the settings from the latest state + `force_submode: schedule` + `apply_with_stop` (like the web "Ideiglenes leállítás"); with a missing value the server's atomic validation rejects it.
* **Layout:** two-column, equal-weight `LinearLayout` rows (`AppUi.row()`, `AppUi.twoColumns()`), so the tiles stay within the screen and text wraps.
* **Tapping:** on the tiles with a detail screen (roller shutter, awning, LED, plug, radio) the whole tile opens the details (`newTile()`, like the arrow); switching is only possible with the tile's buttons (LED and plug: Be / Ki, radio: Be/Ki (power)).
* **Pulsing:** a gentle, repeating colour transition of the tile background (`ValueAnimator`, 2.4 s cycle) while the automation is active (charger: `auto_enabled` or `schedule_enabled`; air conditioners: `climate.settings.auto_enabled`).
* **Building:** GitHub Actions (`assembleDebug`, JDK 17). Locally: JDK 17 is needed (Gradle 8.11's built-in Kotlin script compiler does not recognise Java 25), and a folder without accented characters (the Android plugin stops on accented paths) — so the local build runs on a copy of the project placed in a path without accents.
