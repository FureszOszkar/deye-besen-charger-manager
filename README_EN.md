# Otthonvezérlő – Local EV Charging and Air Conditioner Controller
## System Documentation and User Manual

This software is a local, offline-running integrated controller solution that connects a **Deye three-phase hybrid inverter** and a **BESEN BS20 smart car charger (EVSE)**. The software aims to automatically, intelligently, and safely control electric vehicle charging based on solar energy generation and the home storage battery status.

Besides car charging, the program also handles further devices of the house, each on its own tab: **air conditioners** (via BroadLink infrared transmitters, with the thermometer of a Xiaomi air purifier — see Section 11) and **shading** (a roller shutter and an awning via radio control, with a weekly timer — see Section 12). **Boiler** control comes later (Section 13).

---

## 1. Hardware Models and Specifications

This software has been developed and tested in the following hardware environment:

*   **Hybrid Inverter:** **Deye 5 kW Hybrid Inverter** (e.g., SUN-5K-SG series, 5 kW maximum rated output)
    *   **Communication Interface:** Solarman LSW-3 Wi-Fi Logger (Modbus RTU over TCP protocol on port `8899`).
*   **Car Charger (EVSE):** **BESEN BS20-APP-3P16A** (3-phase, max 16A / 11 kW smart car charger)
    *   **Communication Interface:** Bluetooth Low Energy (BLE) connection.
*   **Home Storage Battery:** Low-voltage (48V) Lithium Iron Phosphate (LiFePO4 / LFP) battery pack (e.g., 20-30 kWh capacity) connected to the inverter.
*   **Air conditioner control:** 3 old, non-smart air conditioners, each with a BroadLink transmitter in front of it: 1 × **BroadLink RM4 Pro** (infrared + radio) and 2 × **BroadLink RM4C Mini** (infrared only).
    *   **Communication Interface:** local Wi-Fi (with the `broadlink` Python library), no cloud.
*   **Thermometer:** **Xiaomi Smart Air Purifier 4** (model: `zhimi.airp.mb5`) — temperature and humidity.
    *   **Communication Interface:** local Wi-Fi, using the manufacturer's miIO protocol (UDP port `54321`) with the device key (token).
*   **Shading:** a radio (RF) remote-controlled roller shutter and awning, controlled with the radio transmitter of the **BroadLink RM4 Pro** above.

---

## 2. Special Local Physical Conditions and Requirements

The stability of both Bluetooth Low Energy (BLE) and the local Wi-Fi network is critical for the continuous, unattended operation of the system. The following special hardware conditions must be met:

### A) High-Gain USB Bluetooth (BT) Antenna / Adapter
The default Bluetooth chip in the BESEN charger has a limited range. The computer running the controller software **must be equipped with an external USB Bluetooth 5.0 (or newer) adapter with a high-gain antenna** (the system has been successfully tested using the **Mercusys MA550H Long Range Bluetooth 5.4** adapter). Built-in motherboard Bluetooth chips or tiny USB dongles are not capable of maintaining a stable connection with a car charger placed outside the building.

- **Note on Timestamps:** The BESEN charger MCU checks the Unix timestamp in the START command for time synchronization. If there is a significant difference (e.g., Budapest vs. Shanghai), it may reject the package. To address this, the `get_shanghai_timestamp()` function converts the local time to a Unix timestamp with an 8-hour offset (Shanghai timezone). This adjusted timestamp is used in the START commands.

### Alternative Solution: Micro-computer (e.g. Raspberry Pi) near the charger
If the main computer running the controller is too far, a highly effective alternative to an expensive long-range antenna is placing a cheap, Wi-Fi and Bluetooth-enabled micro-computer (e.g., **Raspberry Pi Zero 2 W, Raspberry Pi 3, 4, or 5**) close to the charger (e.g., inside the garage). Since the software requires minimal resources, the entire controller can be run directly on this local device — in this setup, the micro-computer communicates with the charger via a stable, short-range Bluetooth connection, while accessing the inverter and the local network via the household Wi-Fi.

### B) Direct Line of Sight (LoS) to the Charger
You must ensure the clearest possible physical line of sight between the USB BT antenna and the BESEN charger.
*   Thick concrete walls, metal structures/covers, and the vehicle being charged can cause significant BLE signal attenuation and shadowing.
*   An unstable Bluetooth signal can lead to missing telemetry data, eventually triggering safety shutdowns. Position the antenna (e.g., near a window) to minimize physical obstacles.

### C) Wi-Fi Coverage at the Deye LSW-3 Logger
Continuous local network connection is required for the Deye inverter's Wi-Fi stick. Make sure the local router's 2.4 GHz signal stably reaches the inverter's installation location.

---

## 3. Inverter Battery Regulation

The controller software works in close harmony with the Deye inverter's internal battery management logic (Time-of-Use settings, charge/discharge priorities):

*   **Solar Priority:** Deye's internal regulation prioritizes supplying energy to the home loads first, charging the home battery second, and exporting any remaining excess power to the grid third.
*   **Battery Protection and Start SoC:** The controller monitors the home battery level (SoC %). Using the `start_soc` parameter (e.g., set to 100%), car charging only starts once the home battery is fully charged. This prevents the car from prematurely draining the home battery when solar excess is not yet sufficient.
*   **Critical Installation Detail – Entire House on the UPS (Backup) Branch, Charger on the Grid Branch:**
    *   Due to the specific physical wiring, the **entire house** is connected to the inverter's UPS (Backup) branch, but **ONLY** the house. The EV charger (EVSE) does not draw power through the house panel; it is wired directly next to the utility meter, before the inverter (on the Grid / utility side).
    *   Since the house is on the UPS branch, all household consumption flows through the inverter's internal power electronics, which has a strict hardware limit of exactly **5 kW**.
    *   If the household consumption (e.g., heat pump, washing machine, oven) approaches or exceeds this 5 kW limit, the inverter will trip on overload, causing an **instant and complete blackout** in the house (even if the utility grid is online).
    *   Therefore, the **House UPS Overload Protection (`house_power_limit_w`)** feature in this software is not an optional comfort feature, but a critical line of defense. The controller continuously monitors the UPS port load (`ups_load_power`). If it exceeds the safety threshold (e.g., 4000 W), it immediately stops the EV charger to relieve the system load and prevent a blackout.
    *   **Calculation Implication:** Since the EV charger is on the grid side, its power draw is calculated as the difference between the main grid utility meter (external CT) and the inverter's internal grid meter. On the UI dashboard, this is displayed as "Nem UPS ágon lévő fogyasztók" (Non-UPS Consumers), which represents the combined consumption of the EV charger and any other non-UPS loads.
*   Solar Auto rules (Grid Import Limit, Battery Stop SoC, House UPS Overload Protection) are evaluated sequentially and independently.
*   Setting "Grid charge delayed shutdown (minutes)" to 0 minutes means **IMMEDIATE** shutdown (0 minutes delay) rather than disabling the check — the check is active when the grid power threshold is greater than 0.
*   HTML input step values for Watt parameters are set to `step=1`, allowing single Watt resolution settings (e.g., 80 W).

---

## 4. Installation and Running

The software is written in Python and can be run on Windows either as Python source code, or compiled with PyInstaller into a single executable file (EXE).

### A) Python Environment Setup (Windows)
Install Python 3.14 (the Windows exe is built with it; the previously used 3.9 is end-of-life), preferably into a virtual environment, then install the required dependencies:
```bash
py -3.14 -m venv .venv314
.venv314\Scripts\python.exe -m pip install bleak pysolarmanv5 pycryptodome broadlink pyinstaller
```
Last verified versions: `bleak 3.0.2`, `pysolarmanv5 3.0.6`, `pycryptodome 3.23.0`, `broadlink 0.19.0`, `pyinstaller 6.22.3`.

### B) Running in Simulation Mode
To test the web interface and rules without any real hardware:
```bash
python main.py --sim
```
*Note: Place any image named `background.png` in the directory next to the file to test the background display.*

### C) Running in Production Mode
Run the script without parameters:
```bash
python main.py
```

### D) Compiling to a Standalone `.exe`
The `deye_besen_controller.spec` file in the project root holds the build configuration (icon, single-file packaging). To compile:
```powershell
.venv314\Scripts\python.exe -m PyInstaller deye_besen_controller.spec --noconfirm
```
After compilation, copy the resulting `dist\deye_besen_controller.exe` back to the project root. To run, the `crypto-js.min.js` file (and optionally `background.png`) must sit next to the exe, since the program loads them from the executable's own directory at runtime.

### E) Running on Linux (Debian 13)
The [`LinuxController`](LinuxController) folder is fully self-contained — it holds everything needed to run on Linux, and can be copied to any directory under any name on the Linux machine. For installation and running it as a systemd service, see the [`LinuxController/README.md`](LinuxController/README.md) guide.

### Accessing the Dashboard
Once started, the web interface is accessible over the local network.
*   **URL:** `http://localhost:8080` (or the machine's local IP, e.g., `http://192.168.0.100:8080`)
*   **Default Password:** `admin` (this can be changed in the code's password constant, or via the configuration if needed)

---

## 5. User Interface and Dashboard Guide

The web interface is accessible at `http://localhost:8080` (or `http://127.0.0.1:8080`) from the host computer. To access the dashboard from other devices on the same local network (such as a mobile phone or tablet), use the host computer's local IP address and port (e.g., `http://192.168.0.100:8080`). It features a premium, translucent dark-grey glassmorphic design that lets the `background.png` image shine through the cards.

### A) Color-Coded Telemetry (Current Flow Direction)
On the **"Mérések & Visszacsatolás" (Measurements & Feedback)** card on the right, the most important power readings are color-coded:
*   **Grid Balance:**
    *   **GREEN (Negative value):** Solar export / Grid feed-in (free solar energy is available).
    *   **RED (Positive value):** Grid import / Consumption (purchased grid electricity).
*   **Battery Power:**
    *   **GREEN (Positive value):** The battery is currently **charging** from solar power.
    *   **RED (Negative value):** The battery is currently **discharging** (supplying energy to the house).
*   **PV, House UPS load, and Non-UPS consumers load** are displayed in white for clean readability.

### B) Live Charging Power and Energy Correction
*   **Charging Power Panel:** A dedicated, compact panel next to the phase table displays the live total power delivered to the car in kilowatts (kW). It is calculated on the client-side as `(V1*I1 + V2*I2 + V3*I3) / 1000`. When charging is inactive, it naturally reads `0.00 kW`.
*   **Total Charging Energy:** The BESEN charger's raw telemetry registers only track energy accumulation for the primary phase (L1). In 3-phase charging mode (detected when current flows on L2 or L3), the controller automatically applies a 3.0x multiplier to the telemetry value so that the actual total energy delivered to the battery (kWh) is displayed on the dashboard.

### C) Mobile Navigation (Icon Dock)
In mobile view (narrow screen), instead of the traditional tab selector, a semi-transparent, right-side icon dock appears, positioned in the lower third of the screen for comfortable one-handed thumb reach. The meaning of the 8 icons, top to bottom:

| Icon | Meaning |
|---|---|
| ☀️ (sun) | Auto Solar mode |
| 🕐 (clock) | Scheduled mode |
| ✋ (hand) | Force (manual) mode |
| 📈 (activity) | Measurements |
| 🌡️ (thermometer) | Air conditioner control (Klímavezérlés) |
| 💧 (drop) | Boiler |
| ☰ (horizontal bars) | Shading (Árnyékolás) |
| 📄 (document) | Log |

The Logout button on mobile is available as a small dedicated icon in the header (only visible when web authentication is enabled). The button is visible even when the page loads broken or empty.

### D) Header: Status Badges and Tabs
Next to the title ("Otthonvezérlő", subtitle: "Helyi autótöltés és klíma vezérlő." — local EV charging and air conditioner controller) the header shows two badge groups:
*   **Connections (Kapcsolatok):** Inverter, Töltő (charger), Hőmérő (thermometer), Klíma1, Klíma2, Klíma3. **Green** = the connection is alive, **red** = not reachable, **grey** = not configured (the device IP address is not set in `config.json`). The air conditioner badges show the reachability of their BroadLink transmitter.
*   **Automations (Automatizmusok):** Auto solar, Auto ütemezett (scheduled), Klíma, Bojler, Árnyékolás (shading). A badge **lights up (cyan)** when that automation is enabled, and is grey when it is not. The Klíma badge lights up when "Automata bekapcsolva" (automation enabled) is saved in the air conditioner control; the Árnyékolás badge when at least one timer is active. The Bojler badge is always grey for now (comes later).

Below them are the **tabs**: Autótöltő (car charger), Klímavezérlés (air conditioner control), Bojler (boiler), Árnyékolás (shading), Egyéb (other). The **Egyéb** tab is only available in desktop view (it has no icon on mobile); it holds devices that fit nowhere else — currently the internet radio (left column) and the smart plug (right column), see Section 14. Each tab is a completely separate page; in desktop view the Log appears at the bottom of every page (on mobile it has its own icon). On the Klímavezérlés tab the Measurements card is shown without the car charger part, with the "Hőmérő és klímák" (thermometer and air conditioners) rows.

On mobile, instead of the badges, two short status rows are shown ("Kapcs.:" and "Auto.:" rows with coloured dots, same meaning), and the icon dock replaces the tabs.

---

## 6. Operating Modes

The controller offers three main operating modes, which you can select at the top of the left configuration card:

### 1. Auto (Solar Auto) Mode
A fully autonomous, "set and forget" mode designed to maximize the utilization of solar excess.
*   **Enable Solar Auto:** Activates the solar excess logic.
*   **Max Charger Current (6-16A):** Sets the maximum charging speed.
*   **Start Battery SoC (%):** The minimum home battery level below which charging cannot start (recommended: `100%`).
*   **Stop Battery SoC (%):** The minimum home battery level (SoC %) below which solar charging stops, to avoid excessively draining the home battery. `0%` disables this limit. **When enabled** (non-zero), the server requires it to be at least **2 percentage points** lower than the Start Battery SoC — this prevents the two thresholds coinciding (e.g. both at `99%`), which would otherwise let a single SoC measurement fluctuation trigger immediate, unnecessary start/stop flapping.
*   **Grid Consumption Limit (W):** The grid import threshold (e.g., `2000 W`) above which the delayed shutdown timer begins.
*   **Delayed Shutdown (minutes):** Helps bridge passing clouds. The system allows grid import for this many minutes before stopping. Setting this to `0` means IMMEDIATE shutdown, provided the grid power threshold is greater than `0`.
*   **UPS Power Limit (W):** If the load on the UPS port exceeds this value, charging stops instantly (recommended: `3000 W` – `5000 W`, depending on inverter and breaker ratings).
*   **Fixed Current Limit (No dynamic regulation):** The software starts charging at the configured fixed maximum current. To protect the car's battery and charging electronics, the controller does not continuously ramp the charging current up and down. Avoiding grid import is handled purely by the ON/OFF (Start/Stop) safety limits.

### 2. Scheduled (Calendar) Mode
Time-based charging control with weekly scheduling, allowing you to take advantage of cheap night-time electricity tariffs or defined charging windows.
*   **Enable Scheduled Mode:** Activates weekly schedule rules.
*   **Run Solar rules outside windows:** If enabled, the system falls back to Solar Auto rules outside of the scheduled time windows (charging from solar during the day, and scheduled grid power at night).
*   **Weekly Schedule Table:** Each day of the week can be configured individually, and **any number (up to 8) of separate time windows** can be added per day via the "+ Add charging time" button:
    *   Enable/Disable schedule (per day).
    *   Per time window: Start and Stop times (a custom two-field hour:minute input — the hour can be set from `00` to `24`, where `24:00` means the end of the day), its own current limit (6-16A), and its own **Override Solar Auto** toggle.
    *   **Overnight (midnight-spanning) charging:** configured as two separate, consecutive daily windows — e.g. today's last window from "22:00" to "24:00", and tomorrow's first window starting at "00:00". Windows within the same day must not overlap, but they may touch (one window's end equals the next one's start) — this is the normal way to represent continuous, uninterrupted charging; the charger only restarts at the boundary if the current limit actually changes.
    *   **Override Solar Auto:** If checked for a given window, solar and battery shutdown rules are ignored during that window (guaranteed night/timed charging).

### 3. Force (Manual Override) Mode
This mode lets you override all automation and manually issue Start/Stop commands, as well as set the current with the slider.
*   **Works without the inverter connection:** Force mode and fixed-current scheduled charging ("Override Solar Auto" enabled) do not use inverter data, so you can start and stop them even while the Deye Wi-Fi logger connection is red. Solar Auto rules, however, still require the inverter (they decide from battery and grid data), so they pause their decisions until it is back. The charger (BLE) connection is required in every mode.
*   **Kézi indítás (Start):** Immediately starts charging at the configured current. Once charging completes (e.g., the car is fully charged or unplugged), the manual override automatically clears and reverts to Solar/Scheduled automation.
*   **Kézi Stop (Hard Stop):** Immediately stops charging and **suspends all Solar/Scheduled automation** until you manually click the red "Visszavonás" (Cancel Override) button.
*   **Ideiglenes leállítás (Soft Stop):** Stops the current charge session but does not suspend automation rules. If Solar Auto conditions are met again later, charging can automatically restart.
*   **Note:** In Force mode, the system may also bypass the House Overload Protection (unless it conflicts with the additional safety features).

---

## 7. Advanced Safety and Encryption

The software features multiple safety mechanisms to protect the hardware, the electrical grid, and prevent unauthorized manipulation or accidental mis-clicks:

1.  **Web Password Authentication and Session Management:** Since the controller is accessible from other devices on the local network (bound to `0.0.0.0`, e.g., when hosted on a Raspberry Pi), it includes a password-protected authentication layer.
    *   Authentication is active by default (`"web_auth_enabled": true`), with the default password `"admin"`.
    *   Upon successful login, the server assigns a cryptographically secure session token to the browser, authorizing it to view telemetry and control the system.
    *   A **Logout** button in the header allows users to immediately clear their session.
    *   **Automatic re-login on a broken page:** if the browser loads the dashboard without the encryption key (e.g. in a new or restored tab — the page would otherwise stay empty/broken and reloading would not help), the page logs out by itself and the login page appears. If that fails for some reason (e.g. a network error), the Logout button is still visible and you can log out manually.
    *   If authentication is not required, it can be disabled in the configuration (`"web_auth_enabled": false`).
2.  **End-to-End Encryption (AES-256-GCM):** The communication between the web dashboard and the Python server is protected by built-in, military-grade encryption.
    *   **Challenge-Response Login:** The user's password is never transmitted over the network. The browser generates an HMAC-based authentication proof (Auth Proof) and sends it instead.
    *   **AES-256-GCM Payload Encryption:** Upon successful login, all API traffic (commands and telemetry) is encrypted and decrypted on the fly using a session key derived via PBKDF2-SHA256. This prevents local network sniffing.
    *   **Session Expiry:** Login session tokens now expire automatically after 24 hours, after which the client must log in again.
    *   **Authenticated Unlock Only:** Clearing a safety Lockdown (`/api/unlock`) also requires an authenticated session — no one on the local network can release it without logging in.
    *   **Weekly Schedule Validation:** The server strictly validates the weekly schedule payload (weekday names, time format, current range, same-day time-window overlaps) before saving it, protecting the system from malformed or malicious data.
3.  **Relay Protection (waiting times):** After a failed start attempt the program enforces a **2-minute (120 seconds) cooldown period**. When Solar Auto stops charging because of a rule (house overload, stop SoC, grid import), it **does not start again for 5 minutes** — otherwise it would restart a few seconds after the condition cleared, and the on/off cycling would end in a lockdown. A manual start is not affected. The waiting times block **only starting**: if the car is charging anyway, the stop rules keep running.
4.  **Fail-Safe Disarm:** If the charging fails to start within 60 seconds after a BLE start command, a failure is logged. If this happens 3 consecutive times, the system automatically stops further attempts and switches to **Monitoring** mode to prevent endless BLE command cycles.
    *   **Stop confirmation:** after a STOP the program checks the charger's telemetry to see whether charging really stopped. If it is still charging 60 seconds later (e.g. the command was lost because of a Bluetooth error), it **resends the STOP and repeats this until charging stops**; every repeat is logged.
    *   **Bluetooth dropout:** if the charger connection drops, the program does not treat its own session as finished. After reconnecting it makes no decision until the first telemetry arrives, and the session still counts as its own — so the end of the time window and the grid-import rule still stop it after a dropout.
5.  **Network Asynchronization and Telemetry Watchdog (Self-Healing):**
    *   Deye inverter synchronous Modbus requests (`pysolarmanv5`) run on a separate background worker thread, ensuring network interruptions do not freeze the main event loop.
    *   **A single inverter connection:** the program keeps at most one connection open to the Wi-Fi logger (which accepts only very few parallel connections). After an outage the old connection is closed and a new one is built — orphaned connections do not pile up and do not take over the logger's free slots.
    *   **Less frequent polling while charging:** by default the program polls the logger every 10 seconds, but every 20 seconds while the car is actively charging, because the logger often fails to respond when the inverter is under peak load. The trade-off is that during Solar Auto charging the stops for house overload, low SoC and grid import can react up to about 10 seconds later (the charger's own load management protects regardless).
    *   **Patient waiting for the logger:** the program waits up to 15 seconds for each response, because under load the logger often answers in 10-15 seconds. The Watchdog does not treat a slow but progressing poll as frozen (a heartbeat is sent after every step of the poll), but still notices a genuinely stuck one.
    *   **A single late response does not drop the connection:** if the logger once fails to answer in time, the connection is kept (the poll fails, the Inverter indicator is red for that one round), and the next poll uses the same connection. Only after **two consecutive** timeouts is the connection closed and rebuilt. For other errors (e.g. a connection error) the connection is still rebuilt immediately. A late-arriving old response is recognised and discarded.
    *   **One request per poll:** the program reads all needed inverter data with a single request instead of the previous 6, so the logger gets a sixth of the requests and a poll waits for a response only once. If the logger did not accept it, the program automatically switches back to the 6 separate requests (an `[INVERTER]` log line shows the mode).
    *   All Bluetooth write and notification requests are constrained by a strict 5-second timeout limit, and closing the connection has a 12-second timeout (a hung disconnect is logged and does not block the program).
    *   **Connection Timeout Protection:** `BleakClient` connection attempts (`client.connect()`) can occasionally hang indefinitely within the Windows Bluetooth stack. To mitigate this, connection attempts are wrapped in an explicit 20-second async timeout (`asyncio.wait_for`). If connection takes longer, it is aborted, the socket is cleaned up, and a fresh reconnection cycle is started.
    *   If the connection state is `LOGGED_IN` but no telemetry packets arrive from the charger for 15 seconds, the built-in watchdog logs a timeout, closes the dead connection, and cleanly restarts the BLE discovery and reconnection process.
    *   **Thread-Safe Telemetry Processing:** Notifications arriving from Bleak's background worker thread are dispatched back to the main event loop thread using `asyncio.run_coroutine_threadsafe` via the global `main_loop` reference, preventing thread-level `RuntimeError: no running event loop` exceptions.
6.  **Anti-Flapping Cooldown:** Prevents rapid Start/Stop cycles by enforcing a 20-second cooldown period after 2 consecutive state changes.
7.  **Safety Lockdown:** Blocks every **start** if 5 state changes occur within 40 seconds, or if 10 automatic commands run within a 5-minute sliding window without human intervention (commands older than 5 minutes drop out of the count, so a long-running, healthy automation never accumulates an unbounded count). Requires manual Unlock from the dashboard. **A stop (STOP) can always go out, even in lockdown** — the lockdown and the switching limits block only starting, so a running session still stops at the end of the time window or because of a rule.
8.  **Total House Load Protection:** The overload protection evaluates the sum of `(UPS Load + Charger Load)` to protect the main breakers. Stops (overload-triggered, the manual Hard STOP and every other STOP) always bypass the cooldown/lockdown restrictions.
9.  **Central Ping-Pong Watchdog (Supervisor):** A dedicated supervisory mechanism protects the software from stalling. It automatically handles two kinds of anomalies:
    *   *Crash protection:* If any background thread stops due to an unexpected exception, the Watchdog catches the error without crashing the main program, and immediately restarts that thread.
    *   *Freeze protection:* Threads cyclically leave a heartbeat (PONG) in memory. If the Watchdog detects no heartbeat from a thread for 30 seconds, it forcibly stops it and restarts it cleanly. The web server thread is also under Watchdog supervision: if it dies entirely, the Watchdog restarts it; if it's alive but frozen (not sending a PONG), the entire process is forced to exit, which systemd/`Restart=on-failure` automatically supervises and restarts.
10. **Unplugged Charging Cable Protection:** if the charging cable is not plugged into the car (as reported by the charger's own BLE telemetry), the system will not send an automatic (Solar Auto) or scheduled START command. On startup, until real telemetry arrives from the charger, the program safely assumes the cable is **not** plugged in — it only allows automatic commands once the charger's actual feedback confirms the state.
11. **Resource Monitoring (file-descriptor protection, Linux):** the Watchdog monitors the number of the program's open file descriptors (sockets, pipes) and writes a summary line to the log every 10 minutes (`[WATCHDOG] Erőforrások: …`). If the number grows dangerously because of a leak (above 700, with a system limit of 1024), the program exits in a controlled way and `systemd` restarts it automatically — so an unforeseen resource leak causes at most a short outage, not a days-long, unnoticed stall.

---

## 8. Configuration and Persistence

Settings are automatically saved to a local `config.json` file whenever an actual save occurs (e.g., you change something on the dashboard, or a charging session ends). If you restart the software (or the computer), it automatically reloads the last settings. **Important:** the `config.json` file is **not** automatically created just by starting the program, and the web interface cannot set the inverter IP, serial number, or charger MAC address — these must be provided by copying the included [`config_example.json`](config_example.json) to `config.json` and filling in your own values (or by copying an existing working `config.json` from another install).

**Safe saving:** the program saves `config.json` so that an interrupted save (power cut, forced exit) cannot leave a truncated file: it writes to a temporary file first and replaces the old file only after the full write. The copy from before the replacement is kept as **`config.json.bak`**. If `config.json` is corrupt or missing at startup, the program **loads the last good save (`config.json.bak`)**, moves the corrupt file aside (`config.json.corrupt-<date>`, without overwriting it), and writes an error message to the log and the dashboard. If the `.bak` is unusable too, it starts with the defaults. (If the program's user cannot create files in the folder, the save falls back to a direct write and the log warns about it once.)

**Rejected form:** if the server rejects a save from the dashboard (e.g. an empty field, Start % and Stop % too close), only an error message comes back; the running mode, the automations and the other settings **do not change**.

The dashboard provides the following settings:
*   **Start SoC (%)** - When reached, Solar Auto charging starts.
*   **Stop SoC (%)** - When dropped below, Solar Auto charging stops.
*   **House Overload Protection (W)** - If the Deye UPS load exceeds this (e.g., 3000W), charging stops for safety.
*   **Max Grid Import (W)** - Grid tolerance threshold. If exceeded, charging stops.
*   **Grid Import Time Limit (Minutes)** - How long the system tolerates the above grid import excess before stopping charging (e.g., 5 minutes, to ride out passing clouds).
*   **Remember Mode on Restart** - A toggle that makes the controller remember the last-used mode (Auto/Schedule/Force).
*   **Air conditioner control, Shading and the app's devices:** the air conditioner settings (Section 11), the shading timers (Section 12), the plug and internet radio timers (Section 14) and the infrared/radio codes learned on the dashboard are also saved to `config.json` (`"climate"`, `"shading"` and `"home_devices"` blocks), so they are reloaded after a power outage or restart. The device **IP addresses and the air purifier key (token), however, can only be set in `config.json`**, not on the dashboard — see `config_example.json` for the template.
*   *Hidden advanced setting (only changeable in `config.json`)*: `"pbkdf2_iterations"` - The strength of the password encryption (default: 100000). On weaker microcomputers (like a Raspberry Pi Zero), you might want to decrease this (e.g., to 50000) for faster logins. This value is safe to change: the web dashboard and the widget in the `AndroidWidget` folder both fetch the current setting dynamically from the server at login time, so no client-side value needs to match it.

---

## 9. Troubleshooting and the Dashboard

The dashboard includes a built-in "Console" and "Error boxes" that provide real-time feedback:
*   **Yellow Warning:** Cooldown timer active (prevents Bluetooth commands from spamming the charger too quickly).
*   **Red Error:** Connection problems with the Inverter (Modbus) or the Charger (BLE). With a red inverter, Solar Auto decisions pause but Force and fixed-current scheduled charging keep working; with a red charger, no charging control works at all.
*   **Red Lockdown:** Safety lockdown (Flapping protection) has activated, manual unlock required.
*   **Console output:** Logs detailed network and charging events.

**Safety Note:** This software interacts with hardware at the network level (Modbus/BLE). While it has built-in safety limits (e.g., Deye MAX current limits), the physical capacity of the local electrical network must be taken into account when configuring settings (such as the house overload limit)! Use at your own risk.

---

## 10. Android Widget

The project also includes a standalone Android application (the `AndroidWidget` folder) that displays the system's live data through a **home-screen widget**, without needing to open the browser-based dashboard. The app's APK is built automatically by GitHub Actions.

### What the widget shows

Over the same encrypted connection the server uses, the widget displays values that refresh roughly once per second:
*   **Napelem** (PV production, W)
*   **Hálózat** (grid import/export, W)
*   **Akku SoC** (battery state of charge, %)
*   **Akku Telj.** (battery power, W)
*   **Ház** (house consumption / UPS load, W)
*   **Autó töltés** (BESEN charger's current power, W)

### Installation and setup

1.  Download and install the APK on the phone (from the GitHub Actions build artifact).
2.  Place the **"Deye-Besen adatok"** widget on the home screen.
3.  On the configuration screen that opens automatically, enter:
    *   **Server IP address** – the local IP of the machine running the controller (the widget connects on port `8080`).
    *   **Password** – the same dashboard password you use to log in to the web interface.
    *   **Background transparency** – the slider adjusts the opacity of the widget's background image.
4.  Tap **Save** to activate the widget.

### Behavior

*   The widget **refreshes on Wi-Fi, or without Wi-Fi through an active VPN (e.g. Tailscale)**, while the server is reachable. On Wi-Fi roughly every 5 seconds; without Wi-Fi through a VPN (mobile data) every 30 seconds to save mobile data. On mobile data without a VPN it stays blank (transparent). For access away from home, enter the NAS's VPN address (e.g. Tailscale 100.x.y.z) as the server address in the widget settings; back on Wi-Fi the 5-second rhythm resumes immediately.
*   Refreshes happen while the screen is on, roughly every 5 seconds (paused on a locked phone to save battery).
*   **Tapping** the widget forces an immediate manual refresh; the numbers stay on screen while it happens.
*   The widget is resilient to **switching between Wi-Fi networks**: if you leave your own network's range and later return, the data recovers on its own within a few seconds.

### Security note

The widget stores the dashboard password locally, in the phone's private storage (`SharedPreferences`). The app disables Android backup (`android:allowBackup="false"`) so the password cannot be extracted via `adb backup`. Communication between the widget and the server is end-to-end encrypted (AES-256 + HMAC), the same way as the web interface.

---

## 11. Air Conditioner Control (Klímavezérlés)

On the **Klímavezérlés** tab 3 old, non-smart air conditioners can be controlled with their remote's infrared signal, through BroadLink transmitters. A Xiaomi air purifier serves as the thermometer (temperature; humidity is display only). The goal: during solar **export** the air conditioners should heat (in winter) or cool (in summer) from the surplus energy.

**Current state:** device connection, code learning, manual test and saving the settings work. **The automation does not switch anything yet** — it comes in the next development step (see "Planned behaviour" below).

### Prerequisites (one-time setup)
1.  **BroadLink transmitters:** add them to the Wi-Fi network in the BroadLink app, then in the device settings **turn off the "Lock device" option** (otherwise the program cannot talk to them). Give them a **fixed IP address** in the router.
2.  **Air purifier:** its **key (token)** is needed; it can be read from the Xiaomi account e.g. with the "Xiaomi Cloud Tokens Extractor" program. If you re-pair the purifier later, the key changes. Give it a **fixed IP address** too.
3.  **Network:** the machine running the controller and all devices must be on **the same local network** — devices on a guest network are not reachable.
4.  **`config.json`:** enter the IP addresses and the key (they cannot be set on the dashboard). Example (the numbers are only samples):
    ```json
    "climate": {
        "sensor": {"ip": "192.168.0.50", "token": "<32-character key>"},
        "units": [
            {"name": "Nappali", "broadlink_ip": "192.168.0.51"},
            {"name": "Háló", "broadlink_ip": "192.168.0.52"},
            {"name": "Dolgozó", "broadlink_ip": "192.168.0.53"}
        ]
    }
    ```
    `name` is the room name (shown next to the "Klíma1–3" label on the dashboard). The other fields (codes, settings) are filled in by the program from the dashboard.

### Measurements: "Hőmérő és klímák" (thermometer and air conditioners)
On the Measurements card of the Klímavezérlés tab (without the car charger part) you see the air purifier status (temperature, humidity, last update) and, per air conditioner, which codes have already been learned (Fűtés BE / Hűtés BE / KI — heating ON / cooling ON / OFF). The program reads the air purifier and checks the reachability of the BroadLink transmitters **every minute**; the result is shown on the Hőmérő and Klíma1–3 header badges, and changes are also recorded in the Log.

### Code learning and manual test
The collapsible **"Eszközök (tanítás, próba)"** (devices: learning, test) section at the bottom of the Klímavezérlés card contains the buttons per air conditioner. Each air conditioner needs three codes: **heating ON**, **cooling ON** and **OFF** (the air conditioners use separate ON and OFF codes, not a toggle).
1.  Set the desired state on the remote (e.g. heating, with the desired temperature).
2.  Press the matching **"Tanítás …"** (learn) button, then within 30 seconds press the remote's button pointed at the BroadLink transmitter.
3.  On success the code is saved to `config.json` and the ✓ mark appears. You can try it with the **"Próba …"** (test) button.

### Settings
The automation settings are at the top of the Klímavezérlés card. The **"Beállítások mentése"** (save settings) button only saves when every field is filled in and valid — with an invalid or empty field nothing changes, and the program tells which field is wrong.
*   **Fűtés / Hűtés (heating / cooling):** the operating mode; it decides which ON code is sent.
*   **Automata bekapcsolva (automation enabled):** turns the automation on or off (the header Klíma badge shows it).
*   **Akkuszint (%)** (battery level), **Célhőmérséklet (°C)** (target temperature, with one decimal, e.g. `22.1`), **Bekapcsolási idő (perc)** (switch-on time, minutes), **Kikapcsolási idő (perc)** (switch-off time, minutes).
*   **Per air conditioner (Klímánként):**
    *   **Visszatermelés a bekapcsoláshoz (W)** (export level to switch on): the export **level** at which this air conditioner should also run. Example: `1500`, `2000`, `2500` W = at this much export I want to use 1, 2 or 3 air conditioners. **These numbers give the switch-on order:** the air conditioner with the lowest threshold starts first (with equal thresholds the lower number first), and switching off goes in reverse order.
    *   **Várható fogyasztás (W)** (expected consumption): how much that air conditioner consumes while running (see below why it is needed).
*   **Explanation line:** under each air conditioner's row the program shows how it calculates in practice, updated while typing, e.g. *"Bekapcsol, ha a visszatermelés + 1. klíma várható fogyasztása (600 W) eléri a 2000 W-ot."* (switches on when the export + the expected consumption of air conditioner 1 (600 W) reaches 2000 W). If you rewrite the thresholds, the texts follow the order given by the thresholds.

### Planned behaviour (the next development step, not working yet)
*   **Calculated production:** the measured export plus the expected consumption of the air conditioners already running. On the grid meter the export is a negative value (e.g. `−1500 W`), so the program takes the export as the negative of the meter reading: positive when exporting, negative when importing from the grid. This way a running air conditioner does not switch off because of its own consumption, since its consumption is added back in the calculation.
*   **Switch-on:** the next air conditioner in the order switches on if the battery level is sufficient, the calculated production reaches its threshold for the "Bekapcsolási idő" minutes, and the temperature is suitable: **for heating the measured temperature ≤ target, for cooling ≥ target** (with one decimal, the same target for every air conditioner). The temperature only matters at switch-on; after that the air conditioners' own thermostats regulate.
*   **Switch-off:** the most recently switched-on air conditioner stops if the calculated production stays below its threshold for the "Kikapcsolási idő" minutes; then the next one likewise.
*   **Example** (thresholds 1500 / 2000 W, expected consumption 600 / 700 W):
    *   No air conditioner running, meter `−1500 W` → calculated production 1500 → air conditioner 1 switches on.
    *   Air conditioner 1 running, meter `−1400 W` → 1400 + 600 = 2000 → air conditioner 2 switches on too (if the temperature is still suitable).
    *   Cloud: meter `+200 W` (import) → −200 + 600 + 700 = 1100 < 2000 → air conditioner 2 stops; then the meter shows about `−500 W` → 500 + 600 = 1100 < 1500 → air conditioner 1 stops too (both after the waiting time).

---

## 12. Shading (Árnyékolás)

On the **Árnyékolás** tab a radio remote-controlled **roller shutter** (redőny) and **awning** (napellenző) can be controlled with the radio transmitter of the BroadLink RM4 Pro, with a daily timer.

### Setup
*   In `config.json` set the RM4 Pro IP address: `"shading": {"broadlink_ip": "192.168.0.51", ...}` (the number is only a sample; it can be the same RM4 Pro that also controls an air conditioner). The other fields are filled in by the program from the dashboard.
*   **Learning the radio codes** at the bottom of the page, in the collapsible **"Eszközök (RF-tanítás, próba)"** (devices: RF learning, test) section, in two steps:
    1.  **Hold down** the remote's button pointed at the BroadLink until the dashboard shows step 2 (the BroadLink is finding the remote's frequency meanwhile).
    2.  Release it, then press the button **once**.

    Each device needs two codes: for the roller shutter **Fel** (up) and **Le** (down), for the awning **Be (Fel)** (out/up) and **Ki (Le)** (in/down). They can be tried with the **"Próba …"** (test) buttons.

### Timer
*   On the **Redőny** (roller shutter) and **Napellenző** (awning) cards an **up** and a **down** time can be set separately for every day of the week. An **empty field** means no movement on that day.
*   With the **"Időzítő aktív"** (timer active) switch the timers of the two devices can be turned off independently.
*   The program sends the signal **once** in the given minute. If the program is not running in that minute (e.g. power outage), that movement is skipped and not made up later.
*   The setting is saved to `config.json` and reloaded after a restart or power outage. The header Árnyékolás badge lights up when at least one timer is active.

---

## 13. Boiler (Bojler)

The **Bojler** tab and the header Bojler badge are currently only placeholders: its control will be built later. Until then the badge is grey, and the page shows only a card saying "Később." (later) and (in desktop view) the Log.

---

## 14. Otthonvezérlő App (Android), LED Strip, Smart Plug and Internet Radio

The Android widget's application (the same APK) also gets an **"Otthonvezérlő" launcher icon**: a simplified app, similar to Xiaomi Home, for the basic functions. Detailed settings stay on the web dashboard. Away from home it works through Tailscale (see remote access), on mobile data too.

### First start
The app asks for the server address and the password — the same stored setting as the widget's (changing it in either place applies to both). At home the NAS's local IP can be used; for use away from home, the NAS's Tailscale address (100.x.y.z). The setting is available later via the gear icon on the main screen.

### Main screen
*   **Energy tiles** with small icons: solar, grid, battery, house — with the same colour code as the web Measurements card (grid green = export, red = import; battery green = charging, red = discharging).
*   **Car charger:** the mode, and while charging the current power **in red**; **Indítás** (start = the web "Kézi indítás") and **Leállítás** (stop = the web "Ideiglenes leállítás", soft stop). If the Solar Auto or Scheduled automation is enabled, **the whole tile pulses**.
*   **Air conditioners:** temperature and humidity (from the air purifier), "Utoljára: …" (the last command sent and its time — IR is one-way, the program does not know the real state of the air conditioner), **Fűtés BE / Hűtés BE / KI** (heating ON / cooling ON / OFF) buttons. If the climate automation is enabled, the tiles pulse.
*   **Roller shutter, awning, LED strip, plug, internet radio:** tapping anywhere on the tile (or the arrow ›) opens the details; **switching is only possible with the tile's buttons**, so opening the details never switches by accident. A button does not start a second action while its own is running (on a slow connection a double tap sends one command).
*   **Saving timers:** on the detail screens the **Mentés** (save) button is dimmed until the timer has been loaded from the server and does not save before that — so an empty form cannot erase the timer stored on the server.
*   **Roller shutter, awning:** up/down buttons; the details hold the weekly timer (the same as on the web Árnyékolás page).
*   **LED strip, plug:** **Be** (on) and **Ki** (off) buttons; the details hold LED: brightness, colour; plug: daily timer with two on/off pairs.
*   **Internet radio:** "Utoljára: power …" (the time of the last button press sent) and the timer state; the **"Be/Ki (power)"** button sends one power button press; the details hold the daily timer.
*   While the app is in the foreground it refreshes every 2 seconds (like the web dashboard); in the background it does not query.
*   The appearance follows the phone's setting: light or **dark mode**.

### LED strip (CozyLife)
*   Controlled **locally, without the cloud** (the strip accepts commands on TCP port 5555). **There is no key or password**; anyone on the local network can switch it.
*   Give it a fixed IP in the router and enter it in `config.json` (see below). The CozyLife app is not needed for it afterwards.

### Xiaomi smart plug
*   Controlled locally, with the same protocol as the air purifier (miIO). Its **key (token)** and **model identifier** (e.g. `chuangmi.plug.hmi206`) are needed — both are printed by the same "Xiaomi Cloud Tokens Extractor" as for the air purifier. Fixed IP in the router.
*   **Web dashboard:** it is also shown on the desktop view's **Egyéb** tab (right column): state (on/off, reachable or not), **Bekapcsolás** (switch on) and **Kikapcsolás** (switch off) buttons, and the timer below.
*   **Daily timer with two on/off pairs** (e.g. a lamp in the morning and in the evening): for every day of the week **1. Be / 1. Ki / 2. Be / 2. Ki** (1st on / 1st off / 2nd on / 2nd off) times (empty = no switching in that pair that day), with a "Időzítő aktív" (timer active) switch. The app and the web show the same timer; in the app the days are shown with short names (H, K, Sze…) in one row. The program switches **once** in the given minute; if it is not running in that minute, the switching is skipped (no catch-up). The setting is saved to `config.json`; an old single-pair setting goes into the 1st pair.
*   Only the new app can save the two-pair timer: a save from the old app (one pair) is rejected by the program, so it cannot delete the 2nd pair — update the app.

### Internet radio (infrared, BroadLink)
*   The radio's infrared remote is imitated by an existing BroadLink transmitter (e.g. the living-room air conditioner's). The remote has **a single power button**, so **one code** has to be learned: "Be" (on) and "Ki" (off) both send the same button press, and the radio switches to its opposite state. IR is one-way, the program does not know the real state of the radio — if it is not in the state you expect, the button press switches it the other way.
*   **Learning and testing on the web dashboard**, on the **Egyéb** tab (desktop view only): **"Tanítás (power)"** (learn), then within 30 seconds press the remote's power button pointed at the BroadLink; the **"Próba (power)"** (test) button tries it. The card shows whether the code is learned and when the last button press was sent.
*   **Daily timer** on the web Egyéb tab and in the app too (the same timer): an on and an off time for every day of the week (empty = no switching that day), with a "Időzítő aktív" (timer active) switch. At both times the power button press is sent, **once** in the given minute (no catch-up).
*   If the same BroadLink is also used by an air conditioner and an air conditioner command and a radio command go out at exactly the same time, one of them may occasionally fail; the dashboard shows this and it can be retried.

### `config.json`
```json
"home_devices": {
    "led":   {"name": "LED-szalag", "ip": "192.168.0.60"},
    "plug":  {"name": "Konnektor", "ip": "192.168.0.61", "token": "<32-character key>", "model": "<model identifier>"},
    "radio": {"name": "Internet-rádió", "broadlink_ip": "192.168.0.62"}
}
```
(The numbers are only samples. The timers and the radio's learned code are filled in by the program.) The program queries the state of the LED strip and the plug on the local network **every minute**, and right after a command, so the app shows a fresh state even if you switched them with the wall button or elsewhere. The LED strip appears only in the app; the plug and the radio are also on the web Egyéb tab.

---

## Acknowledgments

Special thanks to the [slespersen/evseMQTT](https://github.com/slespersen/evseMQTT) GitHub project! Their work on reverse-engineering and implementing the Bluetooth Low Energy (BLE) protocol for the BESEN BS20 charger provided a solid foundation for our controller's BLE communication. Special thanks also to the AI-powered pair programming assistant for refactoring the code, creating the asynchronous control and simulation loops, embedding safety guards, developing the premium glassmorphic web dashboard, and compiling the complete bilingual documentation.

---

## Disclaimer & Bug Reporting

This software interacts with real electrical/hardware equipment at the network level (Modbus/BLE). While the multi-layered safety mechanisms described above protect the system, use it at your own risk — the physical capacity of the local electrical network and grid must always be taken into account when configuring settings.

If you encounter any logical bugs, unexpected behavior, or malfunctions during use, please **report them in the GitHub Issues section of this repository** so we can fix them! Thank you very much for your feedback!
