# A `javitasok-2026-10-02` ág — mi változott és mi az állapota

**Ez az ág NEM a futó változat.** A `main` ágon a stabilan futó program van; ez az ág a 2026-10-02-i átnézés utáni javítások 1–2. lépését őrzi, hogy ne vesszen el.

## Állapot (fontos)

* A módosított fájlok 2026-10-02-án kb. 01:28-tól rövid ideig futottak a NAS-on.
* A felhasználó azt tapasztalta, hogy a program **ledobálja az inverter-kapcsolatot**, ezért a futó változat vissza lett állítva a `main` ág fájljaira.
* **Az ok nincs kivizsgálva.** Az inverter lekérdezésének kódja (`run_inverter_polling`) ezen az ágon nem változott, de ez nem bizonyítja, hogy a hiba független a módosításoktól.
* **Ne tedd élesbe, amíg az ok nincs tisztázva.**
* Valódi töltővel (Bluetooth) a módosítások nincsenek kipróbálva, csak tesztekkel és szimulációval.

## Mi változott a `main` ághoz képest

### `config.py` (és a `LinuxController/config.py` másolat)
* **A `config.json` mentése:** ideiglenes fájlba ír (`config.json.tmp`, lemezre kényszerítve), a régi fájl `config.json.bak` néven megmarad, és csak utána kerül a helyére az új. Egyszerre egy mentés fut (`_save_lock`), és egy régebbi pillanatkép nem írhat felül újabbat (sorszám).
* **Jogosultság és tulajdonos:** az új fájl a régi `config.json` jogosultságait és tulajdonosát kapja.
* **Ha a mappába nem lehet új fájlt létrehozni:** közvetlen írás, egyszeri naplófigyelmeztetéssel.
* **Betöltés (`_read_saved_config`):** sérült vagy hiányzó `config.json` esetén a `config.json.bak` töltődik be, a sérült fájl `config.json.corrupt-<dátum>` néven félrekerül, a hiba a naplóba és a felület hibadobozába kerül.
* **Új állapotmező:** `charger_telemetry_ok` (a csatlakozás óta érkezett-e már telemetria a töltőtől).

### `dashboard.py` (és a másolata)
* Az `/api/config` öt ellenőrzési hibaága már nem hívja a `load_config()`-ot, csak hibaüzenetet küld. (A `load_config()` kikapcsolt „üzemmód megjegyzése” mellett figyelés módra váltott, és kikapcsolta az automatizmusokat.)
* Az `/api/force_submode` elutasítja a kézi indítást, ha nincs megadva töltőáram.

### `charging_logic.py` (és a másolata)
* **Bluetooth-szakadás:** a „töltés befejeződött” ág csak élő kapcsolat és friss telemetria mellett fut (`charger_ready`); szakadáskor a `started_by_controller` jelzés megmarad.
* **Zárolás és kapcsolási korlátok:** csak az indítást tiltják; STOP mindig kimehet.
* **Várakozás szabály miatti leállítás után:** a Solar Auto 5 percig nem indít újra (`RULE_STOP_RESTART_WAIT_S`). A kézi indítást nem érinti.
* **Lehűlés:** csak az indítást tiltja; a leállítási szabályok közben is futnak, ha tölt az autó.
* **STOP megerősítése:** STOP után a telemetriából ellenőrzi a leállást; ha 60 mp múlva is tölt, újraküldi, és ezt ismétli (`STOP_CONFIRM_TIMEOUT_S`). A `started_by_controller` jelzést a megerősített leállás törli, nem a STOP kiküldése.
* **Energia-számláló:** 30 mp-nél hosszabb telemetria-szünet után is folytatja a számolást.
* **Hiányzó beállítások:** `None` értékű beállításnál a vezérlő egyszer naplóz, és azt a módot kihagyja (nem áll le kivétellel).
* **Szimulációs önellenőrzés:** lehűlés és a várakozás alatt nem hasonlít.

### `main.py` (és a másolata)
* Kiírás (`sys.stdout.flush()`) a webszerver-befagyás miatti kényszerleállítás előtt.

### `LinuxController/deye-besen-controller.service`
* `Environment=PYTHONUNBUFFERED=1`, hogy a napló azonnal a journalba kerüljön.

### Leírások
* `README.md`, `README_EN.md`: 7. fejezet (várakozási idők, STOP megerősítése, zárolás), 8. fejezet (biztonságos mentés, hibás űrlap).
* `ARCHITECTURE_HU.md`, `ARCHITECTURE_EN.md`: 2.2, 6.1/6.2/6.4 és a 2026-10-02-i bejegyzés.
* `LinuxController/README.md`: a szolgáltatásfájl frissítése meglévő telepítésen, a mappa írhatósága.

### `deye_besen_controller.exe`
* A módosított forrásból 2026-10-02 00:54-kor készült fordítás. A jogosultság- és tulajdonos-öröklés később került a `config.py`-ba; Windowson ennek nincs hatása.

### Sorvégek
* A NAS-ra felmásolt módosított `charging_logic.py` és `dashboard.py` LF sorvégű volt (a javító szkriptek átírták), a `main` ágon ezek CRLF sorvégűek. Ezen az ágon a `main` ág stílusához vannak igazítva, így az eltérés csak a valódi változás.

## Amit az éles próba felszínre hozott (élesítés előtt rendezni kell)

1. **Az inverter-kapcsolat ledobálása** — nincs kivizsgálva (lásd fent).
2. **A NAS mappájában már van egy kézi `config.json.bak`** (2026-09-27-i). Az új program ugyanezt a nevet használja: az első mentéskor felülírná, és addig egy sérült `config.json` helyett ezt az elavult fájlt töltené be. Élesítés előtt át kell nevezni.
3. **A `config.json` tulajdonosa a NAS-on `nas`, mindenki által írható.** A jogosultság- és tulajdonos-öröklés csak hamis rendszerhívásokkal van tesztelve, Linuxon nincs kipróbálva.
4. **Nem javított, ismert hiány:** ha a töltő maga bontja a kapcsolatot (nem hibával szakad meg), a „csatlakozva” jelzés nem nullázódik az újracsatlakozásig.

## Ellenőrzés

* `config_save_test`: 28/28.
* `charge_controller_l2_test` (a valódi vezérlő, virtuális órával): 38/38. Ugyanez a teszt a `main` ág kódján 12 helyen megbukik.
* Összehasonlítás a `main` ág vezérlőjével hat hétköznapi helyzeten (ütemezett ablak, Solar Auto, kézi indítás és leállítás, külső töltés, áramváltás két ablak között, Solar Auto ütemezett ablakban): a két kód másodpercre ugyanazokat a parancsokat küldi. A hetedik helyzetben (túlterhelés, majd megszűnik) szándékosan eltér: a `main` 5 mp múlva újraindít, ez az ág 5 percet vár.
* A tesztfájlok nincsenek a repóban.

## Ami a tervből nem készült el

* 3. lépés: app-javítások.
* 4. lépés: webes biztonság.
* 5. lépés: takarítás.

---

# Branch `javitasok-2026-10-02` — what changed and its status (English summary)

**This branch is NOT the running version.** `main` holds the stable program; this branch keeps steps 1–2 of the fixes made after the 2026-10-02 review.

**Status:** the modified files ran on the NAS for a short time from about 01:28 on 2026-10-02. The user saw the program **dropping the inverter connection**, so the running version was restored to the `main` files. **The cause has not been investigated.** The inverter polling code is unchanged on this branch, but that does not prove the problem is unrelated. **Do not deploy until the cause is clear.** Not tested with the real charger.

**Changes compared to `main`:**
* `config.py`: atomic `config.json` save (temp file + replace, `config.json.bak`, save lock, sequence number), owner/mode preserved, direct-write fallback; corrupt or missing `config.json` loads `config.json.bak` and moves the corrupt file aside; new `charger_telemetry_ok` state field.
* `dashboard.py`: `/api/config` validation errors no longer call `load_config()`; `/api/force_submode` rejects a manual start without a charging current.
* `charging_logic.py`: decisions only with a live connection and fresh telemetry; lockdown, rate limits and cooldown block only starting; 5-minute wait after a rule-based stop; STOP confirmation with a repeat every 60 s; energy counter continues after a gap; missing settings are skipped instead of crashing.
* `main.py`: flush before the forced exit. Service file: `PYTHONUNBUFFERED=1`. Docs updated (HU/EN).

**Found during the live attempt:** the inverter drops (uninvestigated); a pre-existing manual `config.json.bak` on the NAS that the new code would overwrite and could load as a stale backup; owner/mode preservation not tried on Linux.

**Verification:** `config_save_test` 28/28, `charge_controller_l2_test` 38/38 (fails in 12 places on `main`); six everyday scenarios give identical commands on both codes, the seventh differs on purpose. The test files are not in the repository.
