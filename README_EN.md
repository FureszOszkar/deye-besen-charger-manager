# Deye & BESEN Integrated Charger Controller System
## System Documentation and User Manual

This software is a local, offline-running integrated controller solution that connects a **Deye three-phase hybrid inverter** and a **BESEN BS20 smart car charger (EVSE)**. The software aims to automatically, intelligently, and safely control electric vehicle charging based on solar energy generation and the home storage battery status.

---

## 1. Hardware Models and Specifications

This software has been developed and tested in the following hardware environment:

*   **Hybrid Inverter:** **Deye 5 kW Hybrid Inverter** (e.g., SUN-5K-SG series, 5 kW maximum rated output)
    *   **Communication Interface:** Solarman LSW-3 Wi-Fi Logger (Modbus RTU over TCP protocol on port `8899`).
*   **Car Charger (EVSE):** **BESEN BS20-APP-3P16A** (3-phase, max 16A / 11 kW smart car charger)
    *   **Communication Interface:** Bluetooth Low Energy (BLE) connection.
*   **Home Storage Battery:** Low-voltage (48V) Lithium Iron Phosphate (LiFePO4 / LFP) battery pack (e.g., 20-30 kWh capacity) connected to the inverter.

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
Install Python 3.9+, then install the required dependencies:
```bash
pip install bleak==0.20.2 bleak-winrt==1.2.0 pysolarmanv5 pyinstaller
```

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
py -m PyInstaller deye_besen_controller.spec --noconfirm
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
In mobile view (narrow screen), instead of the traditional tab selector, a semi-transparent, right-side icon dock appears, positioned in the lower third of the screen for comfortable one-handed thumb reach. The meaning of the 5 icons, top to bottom:

| Icon | Meaning |
|---|---|
| ☀️ (sun) | Auto Solar mode |
| 🕐 (clock) | Scheduled mode |
| ✋ (hand) | Force (manual) mode |
| 📈 (activity) | Measurements |
| 📄 (document) | Log |

The Logout button on mobile is available as a small dedicated icon in the header (only visible when web authentication is enabled).

---

## 6. Operating Modes

The controller offers three main operating modes, which you can select at the top of the left configuration card:

### 1. Auto (Solar Auto) Mode
A fully autonomous, "set and forget" mode designed to maximize the utilization of solar excess.
*   **Enable Solar Auto:** Activates the solar excess logic.
*   **Max Charger Current (6-16A):** Sets the maximum charging speed.
*   **Start Battery SoC (%):** The minimum home battery level below which charging cannot start (recommended: `100%`).
*   **Grid Consumption Limit (W):** The grid import threshold (e.g., `2000 W`) above which the delayed shutdown timer begins.
*   **Delayed Shutdown (minutes):** Helps bridge passing clouds. The system allows grid import for this many minutes before stopping. Setting this to `0` means IMMEDIATE shutdown, provided the grid power threshold is greater than `0`.
*   **UPS Power Limit (W):** If the load on the UPS port exceeds this value, charging stops instantly (recommended: `3000 W` – `5000 W`, depending on inverter and breaker ratings).
*   **Fixed Current Limit (No dynamic regulation):** The software starts charging at the configured fixed maximum current. To protect the car's battery and charging electronics, the controller does not continuously ramp the charging current up and down. Avoiding grid import is handled purely by the ON/OFF (Start/Stop) safety limits.

### 2. Scheduled (Calendar) Mode
Time-based charging control with weekly scheduling, allowing you to take advantage of cheap night-time electricity tariffs or defined charging windows.
*   **Enable Scheduled Mode:** Activates weekly schedule rules.
*   **Run Solar rules outside windows:** If enabled, the system falls back to Solar Auto rules outside of the scheduled time windows (charging from solar during the day, and scheduled grid power at night).
*   **Weekly Schedule Table:** Each day of the week can be configured individually:
    *   Enable/Disable schedule.
    *   Start and Stop times (HH:MM).
    *   Current limit (6-16A).
    *   **Override Solar Auto:** If checked, solar and battery shutdown rules are ignored during this window (guaranteed night/timed charging).

### 3. Force (Manual Override) Mode
This mode lets you override all automation and manually issue Start/Stop commands, as well as set the current with the slider.
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
    *   If authentication is not required, it can be disabled in the configuration (`"web_auth_enabled": false`).
2.  **End-to-End Encryption (AES-256-GCM):** The communication between the web dashboard and the Python server is protected by built-in, military-grade encryption.
    *   **Challenge-Response Login:** The user's password is never transmitted over the network. The browser generates an HMAC-based authentication proof (Auth Proof) and sends it instead.
    *   **AES-256-GCM Payload Encryption:** Upon successful login, all API traffic (commands and telemetry) is encrypted and decrypted on the fly using a session key derived via PBKDF2-SHA256. This prevents local network sniffing.
    *   **Session Expiry:** Login session tokens now expire automatically after 24 hours, after which the client must log in again.
    *   **Authenticated Unlock Only:** Clearing a safety Lockdown (`/api/unlock`) also requires an authenticated session — no one on the local network can release it without logging in.
    *   **Weekly Schedule Validation:** The server strictly validates the weekly schedule payload (weekday names, time format, current range) before saving it, protecting the system from malformed or malicious data.
3.  **Relay Protection (Cooldown):** After any stopped or failed charging attempt, the program enforces a **2-minute (120 seconds) cooldown period**. During this time, no automation is allowed to restart charging, protecting the charger's physical relays from premature wear and welding.
4.  **Fail-Safe Disarm:** If the charging fails to start within 60 seconds after a BLE start command, a failure is logged. If this happens 3 consecutive times, the system automatically stops further attempts and switches to **Monitoring** mode to prevent endless BLE command cycles.
5.  **Network Asynchronization and Telemetry Watchdog (Self-Healing):**
    *   Deye inverter synchronous Modbus requests (`pysolarmanv5`) run on a separate background worker thread, ensuring network interruptions do not freeze the main event loop.
    *   All Bluetooth write and notification requests are constrained by a strict 5-second timeout limit.
    *   **Connection Timeout Protection:** `BleakClient` connection attempts (`client.connect()`) can occasionally hang indefinitely within the Windows Bluetooth stack. To mitigate this, connection attempts are wrapped in an explicit 20-second async timeout (`asyncio.wait_for`). If connection takes longer, it is aborted, the socket is cleaned up, and a fresh reconnection cycle is started.
    *   If the connection state is `LOGGED_IN` but no telemetry packets arrive from the charger for 15 seconds, the built-in watchdog logs a timeout, closes the dead connection, and cleanly restarts the BLE discovery and reconnection process.
    *   **Thread-Safe Telemetry Processing:** Notifications arriving from Bleak's background worker thread are dispatched back to the main event loop thread using `asyncio.run_coroutine_threadsafe` via the global `main_loop` reference, preventing thread-level `RuntimeError: no running event loop` exceptions.
6.  **Anti-Flapping Cooldown:** Prevents rapid Start/Stop cycles by enforcing a 20-second cooldown period after 2 consecutive state changes.
7.  **Safety Lockdown:** Fully locks the system if 5 state changes occur within 40 seconds, or if 10 consecutive automatic commands run without human intervention. Requires manual Unlock from the dashboard.
8.  **Total House Load Protection:** The overload protection evaluates the sum of `(UPS Load + Charger Load)` to protect the main breakers. Overload-triggered shutdowns and manual Hard STOP commands always bypass the cooldown/lockdown restrictions.
9.  **Central Ping-Pong Watchdog (Supervisor):** A dedicated supervisory mechanism protects the software from stalling. It automatically handles two kinds of anomalies:
    *   *Crash protection:* If any background thread stops due to an unexpected exception, the Watchdog catches the error without crashing the main program, and immediately restarts that thread.
    *   *Freeze protection:* Threads cyclically leave a heartbeat (PONG) in memory. If the Watchdog detects no heartbeat from a thread for 30 seconds, it forcibly stops it and restarts it cleanly. The web server thread is also under Watchdog supervision: if it dies entirely, the Watchdog restarts it; if it's alive but frozen (not sending a PONG), the entire process is forced to exit, which systemd/`Restart=on-failure` automatically supervises and restarts.

---

## 8. Configuration and Persistence

Settings are automatically saved to a local `config.json` file whenever an actual save occurs (e.g., you change something on the dashboard, or a charging session ends). If you restart the software (or the computer), it automatically reloads the last settings. **Important:** the `config.json` file is **not** automatically created just by starting the program, and the web interface cannot set the inverter IP, serial number, or charger MAC address — these must be provided by copying the included [`config_example.json`](config_example.json) to `config.json` and filling in your own values (or by copying an existing working `config.json` from another install).

The dashboard provides the following settings:
*   **Start SoC (%)** - When reached, Solar Auto charging starts.
*   **Stop SoC (%)** - When dropped below, Solar Auto charging stops.
*   **House Overload Protection (W)** - If the Deye UPS load exceeds this (e.g., 3000W), charging stops for safety.
*   **Max Grid Import (W)** - Grid tolerance threshold. If exceeded, charging stops.
*   **Grid Import Time Limit (Minutes)** - How long the system tolerates the above grid import excess before stopping charging (e.g., 5 minutes, to ride out passing clouds).
*   **Remember Mode on Restart** - A toggle that makes the controller remember the last-used mode (Auto/Schedule/Force).
*   *Hidden advanced setting (only changeable in `config.json`)*: `"pbkdf2_iterations"` - The strength of the password encryption (default: 100000). On weaker microcomputers (like a Raspberry Pi Zero), you might want to decrease this (e.g., to 50000) for faster logins. This value is safe to change: the web dashboard and the widget in the `AndroidWidget` folder both fetch the current setting dynamically from the server at login time, so no client-side value needs to match it.

---

## 9. Troubleshooting and the Dashboard

The dashboard includes a built-in "Console" and "Error boxes" that provide real-time feedback:
*   **Yellow Warning:** Cooldown timer active (prevents Bluetooth commands from spamming the charger too quickly).
*   **Red Error:** Connection problems with the Inverter (Modbus) or the Charger (BLE).
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

*   The widget **only refreshes while the phone is on Wi-Fi** and the server is reachable. On a foreign network or mobile data it stays blank (transparent), then restores the data automatically once you return home.
*   Refreshes happen while the screen is on, roughly every 5 seconds (paused on a locked phone to save battery).
*   **Tapping** the widget forces an immediate manual refresh.
*   The widget is resilient to **switching between Wi-Fi networks**: if you leave your own network's range and later return, the data recovers on its own within a few seconds.

### Security note

The widget stores the dashboard password locally, in the phone's private storage (`SharedPreferences`). The app disables Android backup (`android:allowBackup="false"`) so the password cannot be extracted via `adb backup`. Communication between the widget and the server is end-to-end encrypted (AES-256 + HMAC), the same way as the web interface.

---

## Acknowledgments

Special thanks to the [slespersen/evseMQTT](https://github.com/slespersen/evseMQTT) GitHub project! Their work on reverse-engineering and implementing the Bluetooth Low Energy (BLE) protocol for the BESEN BS20 charger provided a solid foundation for our controller's BLE communication. Special thanks also to the AI-powered pair programming assistant for refactoring the code, creating the asynchronous control and simulation loops, embedding safety guards, developing the premium glassmorphic web dashboard, and compiling the complete bilingual documentation.

---

## Disclaimer & Bug Reporting

This software interacts with real electrical/hardware equipment at the network level (Modbus/BLE). While the multi-layered safety mechanisms described above protect the system, use it at your own risk — the physical capacity of the local electrical network and grid must always be taken into account when configuring settings.

If you encounter any logical bugs, unexpected behavior, or malfunctions during use, please **report them in the GitHub Issues section of this repository** so we can fix them! Thank you very much for your feedback!
