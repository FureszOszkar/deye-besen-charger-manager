import asyncio
import sys
import threading

# 1. Először betöltjük a config modult és azonnal beolvassuk a fájlt
from config import load_config, log_message, shared_state, state_lock, save_config_file
load_config()

# 2. CSAK EZUTÁN importáljuk a többi modult, így ők már a friss IP-t és BLE adatokat látják
from dashboard import start_web_server
from charging_logic import (
    run_inverter_polling, run_ble_client, run_charge_controller,
    ble_command_queue, main_loop
)
from simulation import run_simulation_telemetry, console_simulation_input

# --- FŐ PROGRAM BELÉPÉSI PONT ---

async def main():
    """A program fő aszinkron belépési pontja."""
    global main_loop, ble_command_queue
    main_loop = asyncio.get_running_loop()
    
    print("=== Deye & BESEN Integrált Telemetria és Töltésvezérlő ===")
    
    # Konfigurációs fájl betöltése
    load_config()
    
    ble_command_queue = asyncio.Queue()
    
    # Visszaírjuk a charging_logic modul névterébe, hogy run_ble_client() és
    # run_charge_controller() lássák az inicializált értékeket (nem None-t).
    import charging_logic as _cl
    _cl.ble_command_queue = ble_command_queue
    _cl.main_loop = main_loop
    
    # Parancssori argumentumok ellenőrzése
    if len(sys.argv) > 1 and sys.argv[1] == "--sim":
        # SZIMULÁCIÓS MÓD
        with state_lock:
            shared_state["simulation"] = True
            shared_state["battery_soc"] = 75
            shared_state["grid_power"] = 1500
            shared_state["pv_power"] = 3200
            shared_state["battery_power"] = -1000
            shared_state["ups_load_power"] = 450
        
        log_message("A program SZIMULÁCIÓS módban indul.")
        
        # Konzolos beviteli szál indítása szimulációhoz
        sim_thread = threading.Thread(target=console_simulation_input, daemon=True)
        sim_thread.start()
        
        # Aszinkron szimulációs háttér task indítása
        asyncio.create_task(run_simulation_telemetry())
    else:
        # NORMÁL MÓD - Inverter és töltő konfigurációja
        from config import INVERTER_IP, INVERTER_PORT, LOGGER_SERIAL, CHARGER_NAME, CHARGER_MAC
        log_message(f"Deye Inverter Logger beállítva: {INVERTER_IP}:{INVERTER_PORT} (S/N: {LOGGER_SERIAL})")
        log_message(f"BESEN Charger BLE beállítva: {CHARGER_NAME} ({CHARGER_MAC})")
    
    # HTTP Dashboard Szerver indítása háttérszálban
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    
    # Összefogjuk és elindítjuk a párhuzamos aszinkron feladatokat
    tasks = {
        "inverter": asyncio.create_task(run_inverter_polling()),
        "ble": asyncio.create_task(run_ble_client()),
        "controller": asyncio.create_task(run_charge_controller())
    }

    import time
    import traceback
    
    log_message("[WATCHDOG] Központi Ping-Pong Watchdog elindítva.")
    # A függőben lévő megszakítások nyilvántartása: {task_name: a cancel() kiadásának időpontja}.
    # Erre azért van szükség, mert a cancel() nem állítja le azonnal a feladatot -- ha a pong
    # időbélyeget azonnal frissre állítanánk (ahogy korábban történt), egy elakadt megszakítás
    # 30 másodpercenként újra "friss"-nek tűnne, miközben a régi feladat zombiként tovább futhat.
    cancel_pending = {}
    while True:
        await asyncio.sleep(5)
        current_time = time.time()

        with state_lock:
            pongs = shared_state.get("task_pong", {})

        for task_name, task in tasks.items():
            # 1. Összeomlás / leállás ellenőrzése (Crash vagy sikeres cancel)
            if task.done():
                try:
                    task.result()
                    log_message(f"[WATCHDOG] A(z) {task_name} feladat váratlanul kilépett hiba nélkül.")
                except asyncio.CancelledError:
                    log_message(f"[WATCHDOG] A(z) {task_name} feladat le lett állítva (CancelledError).")
                except Exception as e:
                    err_msg = traceback.format_exc()
                    log_message(f"[WATCHDOG CRITICAL] Váratlan összeomlás a(z) {task_name} feladatban!\n{err_msg}")

                # Újraindítás -- a pong időt CSAK itt reseteljük, amikor a régi feladat
                # bizonyítottan leállt és tényleg új példány indul.
                log_message(f"[WATCHDOG] {task_name} újraindítása...")
                cancel_pending.pop(task_name, None)
                with state_lock:
                    shared_state["task_pong"][task_name] = current_time
                if task_name == "inverter":
                    tasks["inverter"] = asyncio.create_task(run_inverter_polling())
                elif task_name == "ble":
                    tasks["ble"] = asyncio.create_task(run_ble_client())
                elif task_name == "controller":
                    tasks["controller"] = asyncio.create_task(run_charge_controller())
                continue

            # 2. Befagyás ellenőrzése (Freeze - 30 másodperces limit)
            last_pong = pongs.get(task_name, current_time)
            if current_time - last_pong > 30:
                if task_name not in cancel_pending:
                    log_message(f"[WATCHDOG CRITICAL] Befagyás (Timeout): A(z) {task_name} feladat 30 másodperce nem küldött PONG jelet! Erőszakos újraindítás...")
                    task.cancel()  # A task.done() állapotot állítja be; az újraindítás a fenti ágban történik, miután tényleg leállt
                    cancel_pending[task_name] = current_time
                elif current_time - cancel_pending[task_name] > 30:
                    # A megszakítás 30 másodperce függőben van és a feladat még mindig nem állt le --
                    # ez kóros állapot, naplózzuk és újra próbáljuk a cancel-t.
                    log_message(f"[WATCHDOG CRITICAL] A(z) {task_name} feladat a cancel() után 30 másodperccel sem állt le! Ismételt megszakítási kísérlet...")
                    task.cancel()
                    cancel_pending[task_name] = current_time


def run_program():
    """Program futtatása."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram leállítva a felhasználó által.")
        sys.exit(0)


if __name__ == "__main__":
    run_program()
