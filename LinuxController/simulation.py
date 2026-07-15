import asyncio
from config import shared_state, state_lock, log_message

# Szimulációs háttér taskok (ha nincs hardver)
async def run_simulation_telemetry():
    """Szimulációs telemetria adatok generálása."""
    log_message("Szimulációs telemetria hurok aktív.")
    
    while True:
        await asyncio.sleep(2)
        with state_lock:
            # Csak szimulációban frissítünk mesterséges értékeket
            if shared_state["simulation"]:
                shared_state["inverter_connected"] = True
                shared_state["charger_connected"] = True
                
                # Alapértelmezett UPS terhelés, ha nincs megadva
                if shared_state["ups_load_power"] == 0:
                    shared_state["ups_load_power"] = 450
                
                # Szimuláljuk a csatlakozást és a töltő működését a beállított mód szerint
                if not shared_state["pull_plug"]:
                    if shared_state["charging_active"]:
                        shared_state["voltages"] = [230.1, 229.4, 231.2]
                        # 3 fázison 10A-es töltés
                        shared_state["currents"] = [10.0, 10.0, 10.0]
                        shared_state["energy_total"] += 0.005
                        shared_state["temperature_internal"] = 38.5
                    else:
                        shared_state["voltages"] = [231.5, 230.8, 232.0]
                        shared_state["currents"] = [0.0, 0.0, 0.0]
                        shared_state["energy_total"] = 0.0
                        shared_state["temperature_internal"] = 24.2
                else:
                    shared_state["voltages"] = [0.0, 0.0, 0.0]
                    shared_state["currents"] = [0.0, 0.0, 0.0]
                    shared_state["charging_active"] = False


# Szimulációs konzol figyelő (ha tesztelés céljából fut és konzolról akarunk értéket állítani)
def console_simulation_input():
    """Konzolos parancsos bevitel szimulációhoz."""
    print("\n=======================================================")
    print("SZIMULÁCIÓS MÓD AKTÍV.")
    print("Parancsok hálózati értékek szimulálásához a konzolról:")
    print("  - Írj be egy számot (pl. -3000): Hálózati teljesítmény (- = felesleg, + = fogyasztás)")
    print("  - Írj be egy százalékot 0-100 között (pl. 85): Házi akkumulátor SoC%")
    print("  - 'ups <szám>' (pl. ups 2500): UPS házfogyasztás beállítása (W)")
    print("  - 'pv <szám>' (pl. pv 4000): Napelemes termelés beállítása (W)")
    print("  - 'bat <szám>' (pl. bat -1200): Akkumulátor terhelés beállítása (+/- W)")
    print("  - 'pull': Csatlakozó kábel kihúzása")
    print("  - 'plug': Csatlakozó kábel visszadugása")
    print("  - 'start': Manuálisan elindított szimulált töltés")
    print("  - 'stop': Manuálisan leállított szimulált töltés")
    print("=======================================================\n")
    
    while True:
        try:
            line = input().strip()
            if not line:
                continue
                
            if line.lower() == "pull":
                with state_lock:
                    shared_state["pull_plug"] = True
                    shared_state["charging_active"] = False
                log_message("[Szimuláció] Töltő csatlakozó kihúzva.")
            elif line.lower() == "plug":
                with state_lock:
                    shared_state["pull_plug"] = False
                log_message("[Szimuláció] Töltő csatlakozó visszadugva.")
            elif line.lower() == "start":
                with state_lock:
                    shared_state["charging_active"] = True
                log_message("[Szimuláció] Töltés futásállapot elindítva.")
            elif line.lower() == "stop":
                with state_lock:
                    shared_state["charging_active"] = False
                log_message("[Szimuláció] Töltés futásállapot leállítva.")
            elif line.lower().startswith("ups "):
                try:
                    val = int(line.split()[1])
                    with state_lock:
                        shared_state["ups_load_power"] = val
                    log_message(f"[Szimuláció] UPS házfogyasztás szint módosítva: {val} W")
                except Exception as ex:
                    print(f"Hibás UPS formátum: {ex}. Használat: ups <szám>")
            elif line.lower().startswith("pv "):
                try:
                    val = int(line.split()[1])
                    with state_lock:
                        shared_state["pv_power"] = val
                    log_message(f"[Szimuláció] Napelemes PV termelés szint módosítva: {val} W")
                except Exception as ex:
                    print(f"Hibás PV formátum: {ex}. Használat: pv <szám>")
            elif line.lower().startswith("bat "):
                try:
                    val = int(line.split()[1])
                    with state_lock:
                        shared_state["battery_power"] = val
                    log_message(f"[Szimuláció] Akkumulátor terhelés szint módosítva: {val} W")
                except Exception as ex:
                    print(f"Hibás akku terhelés formátum: {ex}. Használat: bat <szám>")
            elif line.startswith("-") or line.isdigit():
                val = int(line)
                if 0 <= val <= 100 and not line.startswith("-"):
                    with state_lock:
                        shared_state["battery_soc"] = val
                    log_message(f"[Szimuláció] Akku SoC szint módosítva: {val}%")
                else:
                    with state_lock:
                        shared_state["grid_power"] = val
                    log_message(f"[Szimuláció] Hálózati teljesítmény módosítva: {val} W")
            else:
                print("Nem támogatott szimulációs parancs.")
        except Exception as e:
            print(f"Hiba a konzolos bemenetnél: {e}")
