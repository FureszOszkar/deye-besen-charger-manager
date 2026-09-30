package com.antigravity.deyewidget

import android.animation.ValueAnimator
import android.app.Activity
import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date

/**
 * Az app főképernyője (a jóváhagyott vázlat szerint): felül energia-csempék, alatta eszközcsempék.
 * Klíma és autótöltő: a gombok a csempén, automata esetén az egész csempe lüktet.
 * Redőny, napellenző, LED, konnektor, internet-rádió: a nyíl (›) részletképernyőt nyit.
 * Amíg a képernyő előtérben van, 2 mp-enként frissít (mint a webes felület), háttérben nem.
 */
class MainActivity : Activity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var pollJob: Job? = null
    private var last: JSONObject? = null

    private lateinit var banner: TextView
    private lateinit var content: LinearLayout
    private val energy = HashMap<String, TextView>()

    private class Tile(val root: LinearLayout, val bg: GradientDrawable, val title: TextView, val status: TextView, val extra: TextView?) {
        var animator: ValueAnimator? = null
    }
    private lateinit var charger: Tile
    private val climates = ArrayList<Tile>()
    private lateinit var roller: Tile
    private lateinit var awning: Tile
    private lateinit var led: Tile
    private lateinit var plug: Tile
    private lateinit var radio: Tile

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val scroll = ScrollView(this).apply { setBackgroundColor(AppUi.color(this@MainActivity, R.color.o_bg)) }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val p = AppUi.dp(this@MainActivity, 12)
            setPadding(p, p, p, p)
        }
        scroll.addView(content)
        setContentView(scroll)
        buildHeader()
        buildEnergy()
        buildDevices()
        // Első indításkor (még nincs szervercím) a beállító képernyő nyílik meg
        val ip = getSharedPreferences("DeyePrefs", MODE_PRIVATE).getString("ip", "") ?: ""
        if (ip.isBlank()) startActivity(Intent(this, AppSettingsActivity::class.java))
    }

    override fun onResume() {
        super.onResume()
        pollJob = scope.launch {
            while (isActive) {
                refresh()
                delay(2000)
            }
        }
    }

    override fun onPause() {
        super.onPause()
        pollJob?.cancel()
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }

    // --- Felépítés ---------------------------------------------------------------------------

    private fun buildHeader() {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        row.addView(AppUi.text(this, "Otthonvezérlő", 22f, R.color.o_text, true),
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val settings = AppUi.icon(this, R.drawable.ic_app_settings, R.color.o_muted, 24)
        settings.setOnClickListener { startActivity(Intent(this, AppSettingsActivity::class.java)) }
        row.addView(settings)
        content.addView(row)
        banner = AppUi.text(this, "", 13f, R.color.o_red).apply {
            visibility = View.GONE
            setPadding(0, AppUi.dp(this@MainActivity, 6), 0, 0)
        }
        content.addView(banner)
        content.addView(View(this), LinearLayout.LayoutParams(1, AppUi.dp(this, 10)))
    }

    private fun buildEnergy() {
        val card = AppUi.card(this)
        val cells = ArrayList<View>()
        listOf(
            Triple("pv", "Napelem", R.drawable.ic_app_solar),
            Triple("grid", "Hálózat", R.drawable.ic_app_grid),
            Triple("battery", "Akku", R.drawable.ic_app_battery),
            Triple("house", "Ház", R.drawable.ic_app_home)
        ).forEach { (key, label, icon) ->
            val cell = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                background = AppUi.rounded(this@MainActivity, R.color.o_card2, 9)
                val p = AppUi.dp(this@MainActivity, 8)
                setPadding(p, p, p, p)
            }
            val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
            val tint = when (key) { "pv" -> R.color.o_amber; "house" -> R.color.o_accent; else -> R.color.o_muted }
            head.addView(AppUi.icon(this, icon, tint, 16))
            head.addView(AppUi.text(this, "  $label", 12f, R.color.o_muted))
            cell.addView(head)
            val value = AppUi.text(this, "–", 17f, R.color.o_text, true)
            cell.addView(value)
            energy[key] = value
            cells.add(cell)
        }
        card.addView(AppUi.twoColumns(this, cells, fillHeight = true))
        content.addView(card)
    }

    private fun newTile(name: String, icon: Int, withArrow: Boolean, onArrow: (() -> Unit)? = null): Tile {
        val bg = AppUi.rounded(this, R.color.o_card)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = bg
            val p = AppUi.dp(this@MainActivity, 10)
            setPadding(p, p, p, p)
        }
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(AppUi.icon(this, icon, R.color.o_text, 18))
        val title = AppUi.text(this, " $name", 14f, R.color.o_text, true)
        head.addView(title, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        if (withArrow) {
            val arrow = AppUi.icon(this, R.drawable.ic_app_chevron, R.color.o_muted, 22)
            arrow.setOnClickListener { onArrow?.invoke() }
            head.addView(arrow)
        }
        root.addView(head)
        val status = AppUi.text(this, "–", 12f, R.color.o_muted)
        root.addView(status)
        return Tile(root, bg, title, status, null)
    }

    private fun twoButtons(tile: Tile, a: TextView, b: TextView) {
        tile.root.addView(AppUi.row(this, a, b))
    }

    private fun buildDevices() {
        val tiles = ArrayList<View>()

        // Autótöltő: mód, töltési teljesítmény pirossal, Indítás / Leállítás
        charger = newTile("Autótöltő", R.drawable.ic_app_charger, false).let {
            val power = AppUi.text(this, "", 16f, R.color.o_red, true)
            it.root.addView(power)
            Tile(it.root, it.bg, it.title, it.status, power)
        }
        twoButtons(charger,
            AppUi.button(this, "Indítás", R.color.o_start_bg, R.color.o_start_fg) { chargerStart() },
            AppUi.button(this, "Leállítás", R.color.o_stop_bg, R.color.o_stop_fg) { chargerSoftStop() })
        tiles.add(charger.root)

        // Klímák: Fűtés BE / Hűtés BE egymás mellett, alattuk dupla szélességű KI
        for (i in 0 until 3) {
            val t = newTile("Klíma${i + 1}", R.drawable.ic_app_ac, false)
            t.root.addView(AppUi.row(this,
                AppUi.button(this, "Fűtés BE", R.color.o_heat_bg, R.color.o_heat_fg) { climate(i, "heat_on") },
                AppUi.button(this, "Hűtés BE", R.color.o_cool_bg, R.color.o_cool_fg) { climate(i, "cool_on") }))
            t.root.addView(AppUi.row(this, AppUi.button(this, "KI", R.color.o_off_bg, R.color.o_text) { climate(i, "off") }))
            climates.add(t)
            tiles.add(t.root)
        }

        // Árnyékolás
        roller = newTile("Redőny", R.drawable.ic_app_blinds, true) { openShading("roller") }
        twoButtons(roller,
            AppUi.button(this, "Fel", R.color.o_off_bg, R.color.o_text) { shading("roller", "up") },
            AppUi.button(this, "Le", R.color.o_off_bg, R.color.o_text) { shading("roller", "down") })
        tiles.add(roller.root)
        awning = newTile("Napellenző", R.drawable.ic_app_awning, true) { openShading("awning") }
        twoButtons(awning,
            AppUi.button(this, "Be", R.color.o_off_bg, R.color.o_text) { shading("awning", "up") },
            AppUi.button(this, "Ki", R.color.o_off_bg, R.color.o_text) { shading("awning", "down") })
        tiles.add(awning.root)

        // LED-szalag és konnektor: koppintás = be/ki, nyíl = részletek
        led = newTile("LED-szalag", R.drawable.ic_app_bulb, true) { startActivity(Intent(this, LedActivity::class.java)) }
        led.root.setOnClickListener { toggleLed() }
        tiles.add(led.root)
        plug = newTile("Konnektor", R.drawable.ic_app_plug, true) { startActivity(Intent(this, PlugActivity::class.java)) }
        plug.root.setOnClickListener { togglePlug() }
        tiles.add(plug.root)

        // Internet-rádió (infra, egyetlen power gomb): koppintás és a gomb = egy power gombnyomás, nyíl = időzítő
        radio = newTile("Internet-rádió", R.drawable.ic_app_radio, true) { startActivity(Intent(this, RadioActivity::class.java)) }
        radio.root.setOnClickListener { radioPress() }
        radio.root.addView(AppUi.row(this, AppUi.button(this, "Be/Ki (power)", R.color.o_off_bg, R.color.o_text) { radioPress() }))
        tiles.add(radio.root)
        content.addView(AppUi.twoColumns(this, tiles, fillHeight = true))
    }

    // --- Frissítés ---------------------------------------------------------------------------

    private suspend fun refresh() {
        try {
            val s = withContext(Dispatchers.IO) { AppApi.status(this@MainActivity) }
            last = s
            banner.visibility = View.GONE
            content.alpha = 1f
            render(s)
        } catch (e: Exception) {
            banner.text = "Nincs kapcsolat: ${AppUi.short(e.message ?: "ismeretlen hiba")}"
            banner.visibility = View.VISIBLE
            content.alpha = 0.55f
            banner.alpha = 1f
        }
    }

    private fun setPulse(tile: Tile, active: Boolean) {
        if (active && tile.animator == null) {
            tile.animator = AppUi.pulse(this, tile.bg).also { it.start() }
        } else if (!active && tile.animator != null) {
            tile.animator?.cancel()
            tile.animator = null
            tile.bg.setColor(AppUi.color(this, R.color.o_card))
        }
    }

    private fun setOn(tile: Tile, on: Boolean) {
        if (tile.animator == null) tile.bg.setColor(AppUi.color(this, if (on) R.color.o_tile_on else R.color.o_card))
    }

    private fun render(s: JSONObject) {
        // Energia (színkód, mint a webes Mérések kártyán)
        energy["pv"]?.text = AppUi.kw(s.optDouble("pv_power", 0.0))
        val grid = s.optDouble("grid_power", 0.0)
        energy["grid"]?.apply {
            text = AppUi.kw(grid, true)
            setTextColor(AppUi.color(this@MainActivity, if (grid < 0) R.color.o_green else if (grid > 0) R.color.o_red else R.color.o_text))
        }
        val bp = s.optDouble("battery_power", 0.0)
        val arrow = if (bp > 0) "↑" else if (bp < 0) "↓" else ""
        energy["battery"]?.apply {
            text = "${s.optInt("battery_soc", 0)} % $arrow${AppUi.kw(bp)}"
            setTextColor(AppUi.color(this@MainActivity, if (bp > 0) R.color.o_green else if (bp < 0) R.color.o_red else R.color.o_text))
        }
        energy["house"]?.text = AppUi.kw(s.optDouble("ups_load_power", 0.0))

        // Autótöltő
        val auto = s.optBoolean("auto_enabled") || s.optBoolean("schedule_enabled")
        setPulse(charger, auto)
        charger.status.text = when (s.optString("control_mode")) {
            "auto" -> "Solar Auto"; "schedule" -> "Ütemezett"; "force" -> "Kézi"; else -> "Figyelés"
        } + if (!s.optBoolean("charger_connected")) " · töltő nem elérhető" else ""
        val volts = s.optJSONArray("voltages")
        val amps = s.optJSONArray("currents")
        var watts = 0.0
        if (volts != null && amps != null) for (i in 0 until minOf(volts.length(), amps.length())) watts += volts.optDouble(i, 0.0) * amps.optDouble(i, 0.0)
        charger.extra?.text = if (s.optBoolean("charging_active")) AppUi.kw(watts) else ""

        // Klímák
        val climate = s.optJSONObject("climate")
        val sensor = climate?.optJSONObject("sensor")
        val units = climate?.optJSONArray("units")
        val climateAuto = climate?.optJSONObject("settings")?.optBoolean("auto_enabled") == true
        val fmt = SimpleDateFormat("HH:mm", AppUi.HU)
        for (i in 0 until 3) {
            val t = climates[i]
            setPulse(t, climateAuto)
            val u = units?.optJSONObject(i)
            val temp = if (sensor != null && sensor.optBoolean("connected"))
                String.format(AppUi.HU, "%.1f °C · %.0f %%", sensor.optDouble("temperature"), sensor.optDouble("humidity")) else "hőmérő –"
            val ls = u?.optJSONObject("last_sent")
            val lastText = if (ls != null) {
                val label = when (ls.optString("code")) { "heat_on" -> "fűtés BE"; "cool_on" -> "hűtés BE"; else -> "KI" }
                "$label ${fmt.format(Date((ls.optDouble("time") * 1000).toLong()))}"
            } else "–"
            val name = u?.optString("name").orEmpty()
            t.title.text = if (name.isNotEmpty()) " $name klíma" else " Klíma${i + 1}"
            t.status.text = "$temp\nUtoljára: $lastText"
        }

        // Árnyékolás
        val shadingDevs = s.optJSONObject("shading")?.optJSONObject("devices")
        roller.status.text = "Időzítő: " + if (shadingDevs?.optJSONObject("roller")?.optBoolean("enabled") == true) "aktív" else "ki"
        awning.status.text = "Időzítő: " + if (shadingDevs?.optJSONObject("awning")?.optBoolean("enabled") == true) "aktív" else "ki"

        // LED-szalag, konnektor
        val home = s.optJSONObject("home")
        val l = home?.optJSONObject("led")
        led.status.text = deviceText(l) { if (it.optBoolean("on")) "Be · ${it.optInt("brightness")} %" else "Ki" }
        setOn(led, l != null && l.optBoolean("on") && l.optBoolean("reachable"))
        val p = home?.optJSONObject("plug")
        plug.status.text = deviceText(p) {
            (if (it.optBoolean("on")) "Be" else "Ki") + if (it.optBoolean("enabled")) " · időzítő aktív" else ""
        }
        setOn(plug, p != null && p.optBoolean("on") && p.optBoolean("reachable"))

        // Internet-rádió: az infra egyirányú, ezért csak az utolsó elküldött gombnyomás ideje látszik
        radio.status.text = AppUi.radioStatus(home?.optJSONObject("radio"), withTimer = true)
    }

    private fun deviceText(d: JSONObject?, ok: (JSONObject) -> String): String = when {
        d == null || !d.optBoolean("configured") -> "Nincs beállítva"
        d.isNull("reachable") -> "Lekérdezés…"
        !d.optBoolean("reachable") -> "Nem elérhető"
        else -> ok(d)
    }

    // --- Műveletek ---------------------------------------------------------------------------

    private fun done() { scope.launch { refresh() } }

    private fun climate(unit: Int, code: String) =
        AppUi.action(this, scope, "/api/climate/send", JSONObject().put("unit", unit).put("code", code)) { done() }

    private fun shading(device: String, action: String) =
        AppUi.action(this, scope, "/api/shading/send", JSONObject().put("device", device).put("action", action)) { done() }

    private fun openShading(device: String) =
        startActivity(Intent(this, ShadingActivity::class.java).putExtra("device", device))

    private fun toggleLed() {
        val l = last?.optJSONObject("home")?.optJSONObject("led") ?: return
        if (!l.optBoolean("configured")) return
        AppUi.action(this, scope, "/api/led/set", JSONObject().put("on", !l.optBoolean("on"))) { done() }
    }

    private fun togglePlug() {
        val p = last?.optJSONObject("home")?.optJSONObject("plug") ?: return
        if (!p.optBoolean("configured")) return
        AppUi.action(this, scope, "/api/plug/set", JSONObject().put("on", !p.optBoolean("on"))) { done() }
    }

    /** Egy power gombnyomás (a szerver érthető hibát ad, ha nincs beállítva vagy megtanítva). */
    private fun radioPress() = AppUi.action(this, scope, "/api/radio/send", JSONObject()) { done() }

    /** Kézi indítás: ugyanaz, mint a webes „Kézi indítás (Start)” gomb (force_submode, majd force mód). */
    private fun chargerStart() {
        scope.launch {
            val msg = try {
                withContext(Dispatchers.IO) {
                    val r1 = AppApi.post(this@MainActivity, "/api/force_submode", JSONObject().put("force_submode", "manual_start"))
                    if (r1.optString("status") != "success") return@withContext r1.optString("message", "Sikertelen indítás.")
                    val r2 = AppApi.post(this@MainActivity, "/api/mode", JSONObject().put("control_mode", "force"))
                    if (r2.optString("status") != "success") r2.optString("message", "Nem sikerült a kézi módot bekapcsolni.") else null
                }
            } catch (e: Exception) {
                e.message ?: "Hiba"
            }
            if (msg != null) AppUi.toast(this@MainActivity, msg)
            refresh()
        }
    }

    /** Ideiglenes leállítás (Soft Stop): ugyanaz, mint a webes gomb — a mentett beállítások
     * változatlanul, force_submode = schedule és apply_with_stop. Hiányzó érték esetén a szerver elutasítja. */
    private fun chargerSoftStop() {
        val s = last ?: return AppUi.toast(this, "Még nincs adat a szervertől.")
        val body = JSONObject()
        listOf("start_soc", "stop_soc", "stop_import_limit", "grid_charge_duration_minutes", "house_power_limit_w",
            "persist_mode_on_restart", "charger_max_amps", "schedule_solar_auto", "forced_schedule",
            "auto_enabled", "schedule_enabled").forEach { k -> body.put(k, s.opt(k) ?: JSONObject.NULL) }
        body.put("force_submode", "schedule")
        body.put("apply_with_stop", true)
        AppUi.action(this, scope, "/api/config", body) { done() }
    }
}
