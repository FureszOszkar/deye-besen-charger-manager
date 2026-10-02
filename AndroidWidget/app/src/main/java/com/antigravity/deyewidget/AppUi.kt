package com.antigravity.deyewidget

import android.animation.ArgbEvaluator
import android.animation.ValueAnimator
import android.app.Activity
import android.app.TimePickerDialog
import android.content.Context
import android.content.DialogInterface
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import com.google.android.material.switchmaterial.SwitchMaterial
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/** Közös felületi segédek az app képernyőihez (programból épített nézetek, világos/sötét színek). */
object AppUi {
    val HU: Locale = Locale("hu", "HU")
    val WEEK_DAYS = listOf("Hétfő", "Kedd", "Szerda", "Csütörtök", "Péntek", "Szombat", "Vasárnap")
    val WEEK_DAYS_SHORT = listOf("H", "K", "Sze", "Cs", "P", "Szo", "V")

    fun dp(ctx: Context, v: Int): Int = (v * ctx.resources.displayMetrics.density).toInt()
    fun color(ctx: Context, id: Int): Int = ContextCompat.getColor(ctx, id)

    fun rounded(ctx: Context, colorRes: Int, radiusDp: Int = 14): GradientDrawable =
        GradientDrawable().apply {
            setColor(color(ctx, colorRes))
            cornerRadius = dp(ctx, radiusDp).toFloat()
        }

    fun text(ctx: Context, s: String, sizeSp: Float, colorRes: Int, bold: Boolean = false): TextView =
        TextView(ctx).apply {
            text = s
            setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp)
            setTextColor(color(ctx, colorRes))
            if (bold) typeface = Typeface.DEFAULT_BOLD
        }

    fun icon(ctx: Context, drawableRes: Int, tintRes: Int, sizeDp: Int = 18): ImageView =
        ImageView(ctx).apply {
            setImageResource(drawableRes)
            setColorFilter(color(ctx, tintRes))
            layoutParams = LinearLayout.LayoutParams(dp(ctx, sizeDp), dp(ctx, sizeDp))
        }

    /** Lekerekített, színes gomb (TextView), a vázlat szerinti fűtés/hűtés/KI/indítás/leállítás színekkel. */
    fun button(ctx: Context, label: String, bgRes: Int, fgRes: Int, onClick: () -> Unit): TextView =
        TextView(ctx).apply {
            text = label
            gravity = Gravity.CENTER
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(color(ctx, fgRes))
            background = rounded(ctx, bgRes, 9)
            val p = dp(ctx, 7)
            setPadding(p, p, p, p)
            setOnClickListener { onClick() }
        }

    /**
     * Vízszintes sor egyenlő szélességű elemekkel (szélesség 0, súly 1): a képernyőn belül marad, a
     * szöveg tördelődik. (A GridLayout a hosszabb szövegeket a természetes szélességükön mérte, és a
     * csempék kilógtak a képből.) fillHeight: a sor elemei egyforma magasak (csempéknél).
     */
    fun row(ctx: Context, vararg views: View, fillHeight: Boolean = false): LinearLayout =
        LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            isBaselineAligned = false
            views.forEach { v ->
                val h = if (fillHeight) ViewGroup.LayoutParams.MATCH_PARENT else ViewGroup.LayoutParams.WRAP_CONTENT
                addView(v, LinearLayout.LayoutParams(0, h, 1f).apply {
                    val m = dp(ctx, 3)
                    setMargins(m, m, m, m)
                })
            }
        }

    /** Kétoszlopos elrendezés soronként (páratlan számú elemnél az utolsó mellé üres hely kerül). */
    fun twoColumns(ctx: Context, views: List<View>, fillHeight: Boolean = false): LinearLayout =
        LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            views.chunked(2).forEach { pair ->
                val cells = if (pair.size == 2) pair else listOf(pair[0], View(ctx))
                addView(row(ctx, *cells.toTypedArray(), fillHeight = fillHeight))
            }
        }

    /** A csempe hátterének lüktetése (automata aktív): enyhe szín, 2,4 mp-es teljes ciklus. */
    fun pulse(ctx: Context, bg: GradientDrawable): ValueAnimator =
        ValueAnimator.ofObject(ArgbEvaluator(), color(ctx, R.color.o_card), color(ctx, R.color.o_pulse)).apply {
            duration = 1200
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            addUpdateListener { bg.setColor(it.animatedValue as Int) }
        }

    /** Az internet-rádió állapotsora: „Utoljára: power 14:32 · időzítő aktív”. Az infra egyirányú,
     * ezért csak az utolsó elküldött gombnyomás ideje ismert, a rádió valódi állapota nem. */
    fun radioStatus(r: JSONObject?, withTimer: Boolean): String = when {
        r == null || !r.optBoolean("configured") -> "Nincs beállítva"
        !r.optBoolean("has_code") -> "A power kód nincs megtanítva"
        r.optString("busy") == "learn" -> "Tanítás…"
        else -> {
            val last = if (r.isNull("last_sent")) "–"
                else "power " + SimpleDateFormat("HH:mm", HU).format(Date((r.optDouble("last_sent") * 1000).toLong()))
            "Utoljára: $last" + if (withTimer && r.optBoolean("enabled")) " · időzítő aktív" else ""
        }
    }

    fun kw(watts: Double, signed: Boolean = false): String {
        val v = watts / 1000.0
        val s = String.format(HU, "%.1f kW", kotlin.math.abs(v))
        return if (signed && v < -0.05) "−$s" else s
    }

    fun toast(ctx: Context, msg: String) = Toast.makeText(ctx, short(msg), Toast.LENGTH_SHORT).show()

    /** Hibaüzenet rövidítése a felülethez: az első sor, legfeljebb 80 karakter (a nyers válasz ne nyomja le a képernyőt). */
    fun short(msg: String?): String {
        val line = (msg ?: "").lineSequence().firstOrNull()?.trim().orEmpty()
        return if (line.length > 80) line.take(79) + "…" else line
    }

    /** Az éppen futó műveletek (végpont + tartalom): ugyanaz a gomb a saját művelete alatt nem indít másikat. */
    private val runningActions: MutableSet<String> = ConcurrentHashMap.newKeySet()

    /**
     * Egy művelet háttérszálon; a szerver „error” válaszát és a hibát üzenetben mutatja.
     * Amíg ugyanez a művelet (ugyanaz a végpont ugyanazzal a tartalommal) fut, az újabb koppintás nem
     * indít másikat — egy lassú kapcsolatnál a dupla koppintás különben két parancsot küldene (a rádió
     * power gombjánál ez épp visszakapcsolna). A megszakítás (a képernyő elhagyása) nem hiba.
     */
    fun action(activity: Activity, scope: CoroutineScope, path: String, body: JSONObject, after: () -> Unit) {
        val key = path + "\n" + body
        if (!runningActions.add(key)) return
        scope.launch {
            val msg = try {
                val res = withContext(Dispatchers.IO) { AppApi.post(activity, path, body) }
                if (res.optString("status") == "success") null else res.optString("message", "Sikertelen művelet.")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                e.message ?: "Hiba"
            }
            if (msg != null) toast(activity, msg)
            after()
        }.invokeOnCompletion { runningActions.remove(key) }   // akkor is lefut, ha a művelet megszakadt vagy el sem indult
    }

    /** Kártya (lekerekített háttér, belső margó). */
    fun card(ctx: Context): LinearLayout = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        background = rounded(ctx, R.color.o_card)
        val p = dp(ctx, 12)
        setPadding(p, p, p, p)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { bottomMargin = dp(ctx, 10) }
    }

    /** Sor: felirat balra, kapcsoló jobbra. */
    fun switchRow(ctx: Context, label: String, onChange: (Boolean) -> Unit): Pair<LinearLayout, SwitchMaterial> {
        val row = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val tv = text(ctx, label, 15f, R.color.o_text)
        row.addView(tv, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val sw = SwitchMaterial(ctx)
        sw.setOnCheckedChangeListener { v, checked -> if (v.isPressed) onChange(checked) }
        row.addView(sw)
        return Pair(row, sw)
    }
}

/**
 * Heti időzítő-szerkesztő (rádió: be/ki; árnyékolás: fel/le; konnektor: két be-ki pár). 7 nap × az oszlopok
 * (keys, labels) időpontjai; az üres időpont („–”) azt jelenti, hogy aznap nincs kapcsolás. Két oszlopnál
 * teljes, többnél rövid napnevek, hogy egy sor kiférjen. Koppintásra időválasztó, „Törlés” gombbal ürít.
 */
class ScheduleEditor(private val ctx: Context, private val keys: List<String>, labels: List<String>) {
    private val values = Array(7) { Array(keys.size) { "" } }
    private val cells = Array(7) { arrayOfNulls<TextView>(keys.size) }
    val view: LinearLayout = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }

    init {
        val compact = keys.size > 2      // négy oszlop (konnektor): rövid napnevek, középre igazított feliratok
        val head = listOf(AppUi.text(ctx, "", 12f, R.color.o_muted)) +
            labels.map { AppUi.text(ctx, it, 12f, R.color.o_muted).apply { if (compact) gravity = Gravity.CENTER_HORIZONTAL } }
        view.addView(AppUi.row(ctx, *head.toTypedArray()))
        val dayNames = if (compact) AppUi.WEEK_DAYS_SHORT else AppUi.WEEK_DAYS
        for (d in 0 until 7) {
            val day = AppUi.text(ctx, dayNames[d], 14f, R.color.o_text).apply { gravity = Gravity.CENTER_VERTICAL }
            for (k in keys.indices) cells[d][k] = AppUi.button(ctx, "–", R.color.o_card2, R.color.o_text) { pick(d, k) }
            view.addView(AppUi.row(ctx, day, *cells[d].map { it!! }.toTypedArray()))
        }
    }

    private fun pick(day: Int, k: Int) {
        val cur = values[day][k]
        val h = if (cur.length == 5) cur.substring(0, 2).toInt() else 7
        val m = if (cur.length == 5) cur.substring(3, 5).toInt() else 0
        val dialog = TimePickerDialog(ctx, { _, hh, mm ->
            set(day, k, String.format(AppUi.HU, "%02d:%02d", hh, mm))
        }, h, m, true)
        dialog.setButton(DialogInterface.BUTTON_NEUTRAL, "Törlés") { _, _ -> set(day, k, "") }
        dialog.show()
    }

    private fun set(day: Int, k: Int, v: String) {
        values[day][k] = v
        cells[day][k]?.text = if (v.isEmpty()) "–" else v
    }

    fun load(schedule: JSONArray?) {
        if (schedule == null) return
        for (d in 0 until minOf(7, schedule.length())) {
            val o = schedule.optJSONObject(d) ?: continue
            for (k in keys.indices) set(d, k, o.optString(keys[k], ""))
        }
    }

    fun toJson(): JSONArray = JSONArray().apply {
        for (d in 0 until 7) put(JSONObject().apply { keys.forEachIndexed { k, key -> put(key, values[d][k]) } })
    }
}
