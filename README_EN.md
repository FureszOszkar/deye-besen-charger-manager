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
The default Bluetooth chip in the BESEN charger has a limited range. The computer running the controller software **must be equipped with an external USB Bluetooth 5.0 (or newer) adapter with a high-gain antenna** (the system has been successfully tested and runs using the **Mercusys MA550H Long Range Bluetooth 5.4** adapter). Built-in motherboard Bluetooth chips or tiny USB dongles are not capable of maintaining a stable connection with a car charger placed outside the building.

### Alternative Solution: Micro-computer (e.g. Raspberry Pi) near the charger
If the main computer running the controller is too far, a highly effective alternative to an expensive long-range antenna is placing a cheap, Wi-Fi and Bluetooth-enabled micro-computer (e.g., **Raspberry Pi Zero 2 W, Raspberry Pi 3, 4, or 5**) close to the charger (e.g., inside the garage).
Since the software requires minimal resources, the entire controller can be run directly on this local device. In this setup, the micro-computer communicates with the charger via a stable, short-range Bluetooth connection, while accessing the inverter and the local network via the household Wi-Fi.

### B) Direct Line of Sight (LoS) to the Charger
You must ensure the clearest possible physical line of sight between the USB BT antenna and the BESEN charger.
*   Thick concrete walls, metal structures/covers, and the vehicle being charged can cause significant BLE signal attenuation and shadowing.
*   An unstable Bluetooth signal can lead to missing telemetry data, eventually triggering safety shutdowns. Position the antenna (e.g., near a window) to minimize physical obstacles.

### C) Wi-Fi Coverage at the Deye LSW-3 Logger
Continuous local network connection is required for the Deye inverter's Wi-Fi stick. Make sure the local router's 2.4 GHz signal stably reaches the inverter's installation location.

---

## 3. Inverter Battery Regulation

The controller software works in close harmony with the Deye inverter's internal battery management logic (Time-of-Use settings, charge/discharge priorities):

*   **Solar Priority:** Deye's internal regulation prioritize supplying energy to the home loads first, charging the home battery second, and exporting any remaining excess power to the grid third.
*   **Battery Protection and Start SoC:** The controller monitors the home battery level (SoC %). Using the `start_soc` parameter (e.g., set to 100%), you can guarantee that the car charging only starts once the home battery is fully charged. This prevents the car from prematurely draining the home battery when solar excess is not yet sufficient.
*   **Critical Installation Detail – Entire House on the UPS (Backup) Branch, Charger on the Grid Branch:**
    *   **Due to the specific physical wiring, the entire house is connected to the inverter's UPS (Backup) branch, but ONLY the house.** The EV charger (EVSE) does not draw power through the house panel; it is wired directly next to the utility meter, before the inverter (on the Grid / utility side).
    *   Since the house is on the UPS branch, all household consumption flows through the inverter's internal power electronics, which has a strict hardware limit of **exactly 5 kW**.
    *   If the household consumption (e.g., heat pump, washing machine, oven) approaches or exceeds this 5 kW limit, the inverter will trip on overload, causing an **instant and complete blackout in the house** (even if the utility grid is online).
    *   Therefore, the **House UPS Overload Protection (`house_power_limit_w`)** feature in this software is not an optional comfort feature, but a critical line of defense. The controller continuously monitors the UPS port load (`ups_load_power`). If it exceeds the safety threshold (e.g., 4000 W), **it immediately stops the EV charger** to relieve the system load and prevent a blackout.
    *   **Calculation Implication:** Since the EV charger is on the grid side, its power draw is calculated as the difference between the main grid utility meter (external CT) and the inverter's internal grid meter. On the UI dashboard, this is displayed as **"Nem UPS ágon lévő fogyasztók" (Non-UPS Consumers)**, which represents the combined consumption of the EV charger and any other non-UPS loads.

---

## 4. User Interface and Dashboard Guide

The web interface is accessible at `http://localhost:8080` (or `http://127.0.0.1:8080`) from the host computer. It features a premium, translucent dark-grey glassmorphic design that lets the `background.png` image shine through the cards.

### A) Color-Coded Telemetry (Current Flow Direction)
On the **Mérések & Visszacsatolás (Measurements & Feedback)** card on the right, the most important power readings are color-coded:
*   **Hálózati egyenleg / Grid Balance (Grid):**
    *   **GREEN (Negative value):** Solar export / Grid feed-in (free solar energy is available).
    *   **RED (Positive value):** Grid import / Consumption (purchased grid electricity).
*   **Akkumulátor teljesítmény / Battery Power:**
    *   **GREEN (Positive value):** The battery is currently **charging** from solar power.
    *   **RED (Negative value):** The battery is currently **discharging** (supplying energy to the house).
*   **PV, House UPS load, and Non-UPS consumers load** are displayed in white for clean readability.

---

## 5. Operating Modes

The controller offers three main operating modes, which you can select at the top of the left configuration card:

### 1. Auto (Solar Auto) Mode
An intelligent mode designed to maximize the utilization of solar excess.
*   **Napelemes mód bekapcsolása (Enable Solar Auto):** Toggle to activate the solar excess logic.
*   **Maximális töltőáram (Max Charger Current, 6-16A):** Sets the maximum charging speed. If the "Disable software current regulation" checkbox is ticked, the vehicle will charge at its own physical maximum speed (or the charger's physical limit).
*   **Indítási akku szint (Start Battery SoC %):** The minimum home battery level below which charging cannot start (recommended: `100%`).
*   **Hálózati fogyasztás küszöbérték (Grid Consumption Limit, W):** The grid import threshold (e.g., `2000 W`) above which the delayed shutdown timer begins.
*   **Hálózati töltés késleltetett leállítása (Delayed Shutdown, minutes):** Helps bridge passing clouds. The system allows grid import for this many minutes before stopping (if set to `0`, it stops immediately).
*   **Ház UPS túlterhelés-védelem (UPS Power Limit, W):** If the load on the UPS port exceeds this value, charging stops instantly (recommended: `3000 W` - `5000 W`, depending on inverter and breaker ratings).

### 2. Scheduled (Calendar) Mode
Time-based charging control with weekly scheduling.
*   **Időzített mód bekapcsolása (Enable Scheduled Mode):** Activates weekly schedule rules.
*   **Napelemes szabályok futtatása az időablakokon kívül (Run Solar rules outside windows):** If enabled, the system falls back to Solar Auto rules outside of the scheduled time windows (charging from solar during the day, and scheduled grid power at night).
*   **Weekly Schedule Table:** Each day of the week can be configured individually:
    *   Enable/Disable schedule.
    *   Start and Stop times (HH:MM).
    *   Current limit (6-16A).
    *   **Solar Auto felülírása (Override Solar Auto):** If checked, solar and battery shutdown rules are ignored during this window (guaranteed night/timed charging).

### 3. Force (Manual Override) Mode
For immediate manual intervention and testing.
*   **Kézi indítás (Start):** Immediately starts charging at the configured current. Once charging completes (e.g., car is fully charged or unplugged), the manual override automatically clears and reverts to Solar/Scheduled automation.
*   **Kézi Stop (Hard Stop):** Immediately stops charging and **suspends all Solar/Scheduled automation** until you manually click the red "Visszavonás" (Cancel Override) button.
*   **Ideiglenes leállítás (Soft Stop):** Stops the current charge session but does not suspend automation rules. If Solar Auto conditions are met again later, charging can automatically restart.

---

## 6. Built-in Safety Guards

The software features multiple safety mechanisms to protect the hardware, the electrical grid, and prevent unauthorized manipulation or accidental mis-clicks:

1.  **Web Password Authentication and Session Management:** Since the controller is accessible from other devices on the local network (bound to `0.0.0.0`, e.g., when hosted on a Raspberry Pi), it includes a password-protected authentication layer.
    *   Authentication is active by default (`"web_auth_enabled": true`), with the default password `"admin"`.
    *   Upon successful login, the server assigns a cryptographically secure session token to the browser, authorizing it to view telemetry and control the system.
    *   A **Kijelentkezés (Logout)** button in the header allows users to immediately clear their session.
    *   If authentication is not required, it can be disabled in the configuration (`"web_auth_enabled": false`).
2.  **Relay Protection (Cooldown):** After any stopped or failed charging attempt, the program enforces a **2-minute (120 seconds) cooldown period**. During this time, no automation is allowed to restart charging, protecting the charger's physical relays from premature wear and welding.
3.  **Fail-Safe Disarm:** If the charging fails to start within 60 seconds after a BLE start command, a failure is logged. If this happens 3 consecutive times, the system automatically stops further attempts and switches to **Figyelés (Monitoring)** mode to prevent endless BLE command cycles.


---

## 7. Running and Compilation Guide

### A) Python Environment Setup (Windows)
Install Python 3.9+, then install the required dependencies:
```bash
pip install bleak==0.20.2 bleak-winrt==1.2.0 pysolarmanv5 pyinstaller
```

### B) Running in Simulation Mode
To test the web interface and rules without any real hardware:
```bash
python deye_besen_controller.py --sim
```
*Note: Place any image named `background.png` in the directory next to the file to test the background display.*

### C) Running in Production Mode
Run the script without parameters:
```bash
python deye_besen_controller.py
```

### D) Compiling to a Standalone `.exe`
To avoid path character encoding errors in Windows paths with accents, compilation is recommended via temporary clean directories:
```powershell
py -m PyInstaller --onefile --clean --distpath "C:\Users\Ignis\.gemini\antigravity\dist_temp" --workpath "C:\Users\Ignis\.gemini\antigravity\build_temp" deye_besen_controller.py
```
Once compilation completes, copy the generated `deye_besen_controller.exe` from `dist_temp` back to the project root directory.

---

## 8. Configuration File (config.json) Guide

Upon startup, the program reads the `config.json` file. If it does not exist, it will be automatically created with built-in default values (`DEFAULT_CONFIG`). The table below describes the function of each configuration key:

| Key | Type | Description | Example Value |
|---|---|---|---|
| `inverter_ip` | string | The local IP address of your Deye inverter's Wi-Fi logger stick (LSW-3). | `"192.168.0.100"` |
| `inverter_port` | integer | Modbus TCP connection port for the logger stick (typically `8899`). | `8899` |
| `logger_serial` | integer | The unique 10-digit serial number of your Solarman logger stick. | `1234567890` |
| `http_port` | integer | The port used by the local web dashboard and REST API (default: `8080`). | `8080` |
| `charger_name` | string | Bluetooth name of the BESEN BS20 car charger. | `"ACP#DefaultName"` |
| `charger_mac` | string | Bluetooth MAC address of the BESEN BS20 car charger. | `"00:11:22:33:44:55"` |
| `charger_password` | string | Charger authorization password (6-byte PIN or 12-character hex key). | `"YOURPIN"` |
| `start_soc` | integer | Home battery SoC % threshold above which car charging is allowed to start. | `100` |
| `stop_import_limit` | integer | Maximum allowed grid import power (W) before starting delayed shutdown. | `2000` |
| `grid_charge_duration_minutes` | integer | Delayed shutdown grace period (minutes) for cloud cover. If `0`, stops immediately. | `30` |
| `house_power_limit_w` | integer | House UPS overload protection limit (W). Recommended for 5 kW inverter: `4000`. | `4000` |
| `control_mode` | string | Startup control mode (`monitoring` / `auto` / `schedule` / `force`). | `"monitoring"` |
| `persist_mode_on_restart` | boolean | If `true`, the system restarts in the last configured mode and state. | `true` |
| `charger_max_amps` | integer | Maximum current limit (A) sent to the EV (must be between `6` and `16`). | `16` |
| `force_submode` | string | Default manual override submode (`manual_start` / `manual_stop`). | `"manual_stop"` |
| `schedule_solar_auto` | boolean | If `true`, Solar Auto rules are applied outside of scheduled time windows. | `false` |
| `auto_enabled` | boolean | Active status of Solar Auto mode from the last save. | `false` |
| `schedule_enabled` | boolean | Active status of Scheduled mode from the last save. | `false` |
| `web_auth_enabled` | boolean | If `true`, requires password login to access the web UI and REST API. | `true` |
| `web_password` | string | The custom password used for web interface authentication. | `"admin"` |
| `forced_schedule` | array | Weekly calendar schedule settings array containing time windows, currents, etc. | *(See config_example.json)* |

---

## Acknowledgments

*   **slespersen:** Special thanks for the [slespersen/evseMQTT](https://github.com/slespersen/evseMQTT) GitHub project! His work on reverse-engineering and implementing the Bluetooth Low Energy (BLE) protocol for the BESEN BS20 charger saved us a significant amount of time and experimentation, providing the solid foundation for our controller's BLE communication.
*   **Antigravity (Google DeepMind):** Special thanks to the AI-powered pair programming assistant for refactoring the code, creating the asynchronous control and simulation loops, embedding safety guards, developing the premium glassmorphic web dashboard, and compiling the complete bilingual documentation.

---

## Disclaimer & Bug Reporting

> [!WARNING]
> **Disclaimer:** Despite thorough testing and built-in safety mechanisms (cooldown, overload protection, fail-safe), unexpected logical or software errors may occur. Use this software entirely at your own risk. The developers accept no liability for any damage to the inverter, battery, car charger, or household electrical grid.

If you encounter any logical bugs, unexpected behavior, or malfunctions during use, please **report them in the GitHub Issues section of this repository** so we can fix them! Thank you very much for your feedback!
