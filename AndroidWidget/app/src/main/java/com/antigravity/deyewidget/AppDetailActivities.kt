package com.antigravity.deyewidget

import android.app.Activity
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.google.android.material.slider.Slider
import com.google.android.material.switchmaterial.SwitchMaterial
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** A részletképernyők közös váza: vissza-nyíl + cím, görgethető tartalom, állapot-lekérdezés. */
abstract class AppDetailActivity : Activity() {
    protected val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    protected lateinit var content: LinearLayout
    private var firstLoad = true

    protected abstract val screenTitle: String
    /** Lekérdezze-e megnyitáskor az állapotot (a beállító képernyőnek nem kell). */
    protected open val loadsStatus = true
    protected abstract fun build()
    /** Az állapot megjelenítése; first = az első betöltés (az űrlapokat csak ekkor töltjük ki). */
    protected abstract fun render(s: JSONObject, first: Boolean)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val scroll = ScrollView(this).apply { setBackgroundColor(AppUi.color(this@AppDetailActivity, R.color.o_bg)) }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val p = AppUi.dp(this@AppDetailActivity, 12)
            setPadding(p, p, p, p)
        }
        scroll.addView(content)
        setContentView(scroll)
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val back = AppUi.icon(this, R.drawable.ic_app_back, R.color.o_text, 26)
        back.setOnClickListener { finish() }
        head.addView(back)
        head.addView(AppUi.text(this, " $screenTitle", 20f, R.color.o_text, true))
        content.addView(head)
        content.addView(View(this), LinearLayout.LayoutParams(1, AppUi.dp(this, 12)))
        build()
    }

    override fun onResume() {
        super.onResume()
        if (loadsStatus) reload()
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }

    protected fun reload() {
        scope.launch {
            try {
                val s = withContext(Dispatchers.IO) { AppApi.status(this@AppDetailActivity) }
                render(s, firstLoad)
                firstLoad = false
            } catch (e: Exception) {
                AppUi.toast(this@AppDetailActivity, "Nincs kapcsolat: ${e.message ?: "hiba"}")
            }
        }
    }

    protected fun send(path: String, body: JSONObject) = AppUi.action(this, scope, path, body) { reload() }
}

/** Színárnyalat-sáv (0–360): húzással választ, elengedéskor jelez. */
class HueBarView(ctx: Context) : View(ctx) {
    var hue = 0
        set(v) { field = v.coerceIn(0, 360); invalidate() }
    var onPicked: ((Int) -> Unit)? = null
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val thumbPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 6f; color = Color.WHITE }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        val colors = IntArray(7) { Color.HSVToColor(floatArrayOf(it * 60f, 1f, 1f)) }
        barPaint.shader = LinearGradient(0f, 0f, w.toFloat(), 0f, colors, null, Shader.TileMode.CLAMP)
    }

    override fun onDraw(canvas: Canvas) {
        val r = height / 2f
        canvas.drawRoundRect(RectF(0f, height * 0.2f, width.toFloat(), height * 0.8f), r, r, barPaint)
        val x = width * hue / 360f
        canvas.drawCircle(x.coerceIn(r * 0.5f, width - r * 0.5f), height / 2f, height * 0.4f, thumbPaint)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        hue = (event.x / width * 360).toInt()
        if (event.action == MotionEvent.ACTION_UP) onPicked?.invoke(hue)
        parent?.requestDisallowInterceptTouchEvent(event.action != MotionEvent.ACTION_UP)
        return true
    }
}

class LedActivity : AppDetailActivity() {
    override val screenTitle = "LED-szalag"
    private lateinit var power: SwitchMaterial
    private lateinit var status: TextView
    private lateinit var brightness: Slider
    private lateinit var hueBar: HueBarView

    override fun build() {
        val c1 = AppUi.card(this)
        val (row, sw) = AppUi.switchRow(this, "Bekapcsolva") { on -> send("/api/led/set", JSONObject().put("on", on)) }
        power = sw
        c1.addView(row)
        status = AppUi.text(this, "", 12f, R.color.o_muted)
        c1.addView(status)
        content.addView(c1)

        val c2 = AppUi.card(this)
        c2.addView(AppUi.text(this, "Fényerő", 15f, R.color.o_text))
        brightness = Slider(this).apply { valueFrom = 0f; valueTo = 100f; stepSize = 1f }
        brightness.addOnSliderTouchListener(object : Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: Slider) {}
            override fun onStopTrackingTouch(slider: Slider) {
                send("/api/led/set", JSONObject().put("brightness", slider.value.toInt()))
            }
        })
        c2.addView(brightness)
        content.addView(c2)

        val c3 = AppUi.card(this)
        c3.addView(AppUi.text(this, "Szín", 15f, R.color.o_text))
        hueBar = HueBarView(this)
        hueBar.onPicked = { h -> send("/api/led/set", JSONObject().put("hue", h).put("saturation", 100)) }
        c3.addView(hueBar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, AppUi.dp(this, 36)))
        val quick = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, AppUi.dp(this@LedActivity, 8), 0, 0) }
        // gyorsszínek: piros, sárga, zöld, kék, fehér (telítettség 0)
        listOf(Triple(0, 100, "#FF3B30"), Triple(50, 100, "#FFCC00"), Triple(120, 100, "#34C759"),
            Triple(220, 100, "#0A84FF"), Triple(0, 0, "#FFFFFF")).forEach { (h, sat, hex) ->
            val dot = View(this).apply {
                background = android.graphics.drawable.GradientDrawable().apply {
                    shape = android.graphics.drawable.GradientDrawable.OVAL
                    setColor(Color.parseColor(hex))
                    setStroke(AppUi.dp(this@LedActivity, 1), AppUi.color(this@LedActivity, R.color.o_muted))
                }
                setOnClickListener { send("/api/led/set", JSONObject().put("hue", h).put("saturation", sat)) }
            }
            quick.addView(dot, LinearLayout.LayoutParams(AppUi.dp(this, 34), AppUi.dp(this, 34)).apply { rightMargin = AppUi.dp(this@LedActivity, 10) })
        }
        c3.addView(quick)
        content.addView(c3)
    }

    override fun render(s: JSONObject, first: Boolean) {
        val l = s.optJSONObject("home")?.optJSONObject("led") ?: return
        power.isChecked = l.optBoolean("on")
        status.text = when {
            !l.optBoolean("configured") -> "Nincs beállítva (IP-cím a config.json-ban)."
            !l.optBoolean("reachable") -> "Nem elérhető: ${AppUi.short(l.optString("error"))}"
            else -> ""
        }
        if (!l.isNull("brightness")) brightness.value = l.optInt("brightness").coerceIn(0, 100).toFloat()
        if (!l.isNull("hue")) hueBar.hue = l.optInt("hue")
    }
}

class PlugActivity : AppDetailActivity() {
    override val screenTitle = "Konnektor"
    private lateinit var power: SwitchMaterial
    private lateinit var timer: SwitchMaterial
    private lateinit var status: TextView
    private lateinit var editor: ScheduleEditor

    override fun build() {
        val c1 = AppUi.card(this)
        val (row, sw) = AppUi.switchRow(this, "Bekapcsolva") { on -> send("/api/plug/set", JSONObject().put("on", on)) }
        power = sw
        c1.addView(row)
        status = AppUi.text(this, "", 12f, R.color.o_muted)
        c1.addView(status)
        content.addView(c1)

        val c2 = AppUi.card(this)
        val (trow, tsw) = AppUi.switchRow(this, "Időzítő aktív") { }
        timer = tsw
        c2.addView(trow)
        // Naponta két be-ki pár (pl. lámpa reggel és este)
        editor = ScheduleEditor(this, listOf("on", "off", "on2", "off2"), listOf("1. Be", "1. Ki", "2. Be", "2. Ki"))
        c2.addView(editor.view)
        c2.addView(AppUi.button(this, "Mentés", R.color.o_accent, R.color.o_on_accent) {
            send("/api/plug/schedule", JSONObject().put("enabled", timer.isChecked).put("schedule", editor.toJson()))
        })
        content.addView(c2)
    }

    override fun render(s: JSONObject, first: Boolean) {
        val p = s.optJSONObject("home")?.optJSONObject("plug") ?: return
        power.isChecked = p.optBoolean("on")
        status.text = when {
            !p.optBoolean("configured") -> "Nincs beállítva (IP-cím és kulcs a config.json-ban)."
            !p.optBoolean("reachable") -> "Nem elérhető: ${AppUi.short(p.optString("error"))}"
            else -> p.optString("last_result")
        }
        if (first) {
            timer.isChecked = p.optBoolean("enabled")
            editor.load(p.optJSONArray("schedule"))
        }
    }
}

/** Internet-rádió (infra, egyetlen power gomb): gombnyomás és napi időzítő. A kód tanítása a webes felületen. */
class RadioActivity : AppDetailActivity() {
    override val screenTitle = "Internet-rádió"
    private lateinit var timer: SwitchMaterial
    private lateinit var status: TextView
    private lateinit var editor: ScheduleEditor

    override fun build() {
        val c1 = AppUi.card(this)
        c1.addView(AppUi.row(this, AppUi.button(this, "Be/Ki (power)", R.color.o_off_bg, R.color.o_text) {
            send("/api/radio/send", JSONObject())
        }))
        status = AppUi.text(this, "", 12f, R.color.o_muted)
        c1.addView(status)
        content.addView(c1)

        val c2 = AppUi.card(this)
        val (trow, tsw) = AppUi.switchRow(this, "Időzítő aktív") { }
        timer = tsw
        c2.addView(trow)
        c2.addView(AppUi.text(this, "A power gomb vált (Be és Ki ugyanaz a gombnyomás).", 12f, R.color.o_muted))
        editor = ScheduleEditor(this, listOf("on", "off"), listOf("Be", "Ki"))
        c2.addView(editor.view)
        c2.addView(AppUi.button(this, "Mentés", R.color.o_accent, R.color.o_on_accent) {
            send("/api/radio/schedule", JSONObject().put("enabled", timer.isChecked).put("schedule", editor.toJson()))
        })
        content.addView(c2)
    }

    override fun render(s: JSONObject, first: Boolean) {
        val r = s.optJSONObject("home")?.optJSONObject("radio") ?: return
        status.text = when {
            !r.optBoolean("configured") -> "Nincs beállítva (a BroadLink IP-címe a config.json-ban)."
            !r.optBoolean("has_code") -> "A power kód nincs megtanítva (a webes felület Egyéb fülén)."
            else -> AppUi.radioStatus(r, withTimer = false) +
                if (!r.isNull("last_result_ok") && !r.optBoolean("last_result_ok")) "\n" + AppUi.short(r.optString("last_result")) else ""
        }
        if (first) {
            timer.isChecked = r.optBoolean("enabled")
            editor.load(r.optJSONArray("schedule"))
        }
    }
}

class ShadingActivity : AppDetailActivity() {
    private val device by lazy { intent.getStringExtra("device") ?: "roller" }
    override val screenTitle: String get() = if (device == "awning") "Napellenző" else "Redőny"
    private lateinit var timer: SwitchMaterial
    private lateinit var status: TextView
    private lateinit var editor: ScheduleEditor

    override fun build() {
        val up = if (device == "awning") "Be (Fel)" else "Fel"
        val down = if (device == "awning") "Ki (Le)" else "Le"
        val c1 = AppUi.card(this)
        c1.addView(AppUi.row(this,
            AppUi.button(this, up, R.color.o_off_bg, R.color.o_text) {
                send("/api/shading/send", JSONObject().put("device", device).put("action", "up"))
            },
            AppUi.button(this, down, R.color.o_off_bg, R.color.o_text) {
                send("/api/shading/send", JSONObject().put("device", device).put("action", "down"))
            }))
        status = AppUi.text(this, "", 12f, R.color.o_muted)
        c1.addView(status)
        content.addView(c1)

        val c2 = AppUi.card(this)
        val (trow, tsw) = AppUi.switchRow(this, "Időzítő aktív") { }
        timer = tsw
        c2.addView(trow)
        editor = ScheduleEditor(this, listOf("up", "down"), listOf(up, down))
        c2.addView(editor.view)
        c2.addView(AppUi.button(this, "Mentés", R.color.o_accent, R.color.o_on_accent) {
            send("/api/shading/config", JSONObject().put("device", device).put("enabled", timer.isChecked)
                .put("schedule", editor.toJson()))
        })
        content.addView(c2)
    }

    override fun render(s: JSONObject, first: Boolean) {
        val d = s.optJSONObject("shading")?.optJSONObject("devices")?.optJSONObject(device) ?: return
        status.text = d.optString("last_result")
        if (first) {
            timer.isChecked = d.optBoolean("enabled")
            editor.load(d.optJSONArray("schedule"))
        }
    }
}

/** A szerver címe és a jelszó (ugyanaz a tárolt beállítás, mint a widgeté: DeyePrefs). */
class AppSettingsActivity : AppDetailActivity() {
    override val screenTitle = "Beállítások"

    override fun build() {
        val prefs = getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val card = AppUi.card(this)
        card.addView(AppUi.text(this, "Szerver címe (pl. a NAS Tailscale-címe)", 13f, R.color.o_muted))
        val ip = EditText(this).apply {
            setText(prefs.getString("ip", ""))
            setTextColor(AppUi.color(this@AppSettingsActivity, R.color.o_text))
            inputType = InputType.TYPE_CLASS_TEXT
        }
        card.addView(ip)
        card.addView(AppUi.text(this, "Jelszó", 13f, R.color.o_muted))
        val pw = EditText(this).apply {
            setText(prefs.getString("password", ""))
            setTextColor(AppUi.color(this@AppSettingsActivity, R.color.o_text))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        card.addView(pw)
        card.addView(AppUi.button(this, "Mentés", R.color.o_accent, R.color.o_on_accent) {
            prefs.edit().putString("ip", ip.text.toString().trim()).putString("password", pw.text.toString()).apply()
            AppApi.reset()
            finish()
        })
        card.addView(AppUi.text(this, "A widget is ezt a címet és jelszót használja.", 12f, R.color.o_muted))
        content.addView(card)
    }

    override val loadsStatus = false

    override fun render(s: JSONObject, first: Boolean) {}
}
