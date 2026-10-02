package com.antigravity.deyewidget

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.PowerManager
import android.widget.RemoteViews
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.PeriodicWorkRequest
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class WidgetUpdateWorker(appContext: Context, workerParams: WorkerParameters) :
    Worker(appContext, workerParams) {

    companion object {
        const val LOOP_WORK_NAME = "DeyeWidgetLoop"
        const val KEEPALIVE_WORK_NAME = "DeyeWidgetKeepAlive"

        // Ezek osztályszintű változók – túlélik a Worker-példány cserét és az újraindítást
        private var sessionToken: String? = null
        private var sessionKey: ByteArray? = null
        private var lastSuccessTime: Long = 0L

        // A widgetre koppintás ezzel kér azonnali frissítést a futó huroktól (a várakozás megszakad)
        val refreshNow = AtomicBoolean(false)

        private val VALUE_IDS = intArrayOf(
            R.id.tv_pv, R.id.tv_grid, R.id.tv_soc, R.id.tv_batt_power, R.id.tv_ups, R.id.tv_charger
        )
        private const val PREF_WIDGET_TEXTS = "widget_texts"
        private const val PREF_WIDGET_ONLINE = "widget_online"

        fun hasWidgets(context: Context): Boolean =
            AppWidgetManager.getInstance(context.applicationContext)
                .getAppWidgetIds(ComponentName(context.applicationContext, DeyeWidgetProvider::class.java))
                .isNotEmpty()

        /**
         * A widgeten utoljára megjelenített értékek beírása egy új RemoteViews-ba. A provider teljes
         * frissítése (pl. koppintáskor) az alap-elrendezést küldi ki: e nélkül a számok a következő
         * lekérdezésig eltűnnének.
         */
        fun applyCached(context: Context, views: RemoteViews) {
            val prefs = context.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
            if (!prefs.getBoolean(PREF_WIDGET_ONLINE, false)) return
            val texts = (prefs.getString(PREF_WIDGET_TEXTS, null) ?: return).split("\n")
            if (texts.size != VALUE_IDS.size) return
            views.setViewVisibility(R.id.online_dark_overlay, android.view.View.VISIBLE)
            views.setViewVisibility(R.id.tv_title, android.view.View.VISIBLE)
            VALUE_IDS.forEachIndexed { i, id -> views.setTextViewText(id, texts[i]) }
        }

        fun enqueueLoop(context: Context, policy: ExistingWorkPolicy) {
            // Widget nélkül nincs mit frissíteni: az utolsó widget levétele után a hurok ne induljon újra
            if (!hasWidgets(context)) return
            val workRequest = OneTimeWorkRequest.Builder(WidgetUpdateWorker::class.java).build()
            WorkManager.getInstance(context.applicationContext)
                .enqueueUniqueWork(LOOP_WORK_NAME, policy, workRequest)
        }

        fun cancelLoop(context: Context) {
            WorkManager.getInstance(context.applicationContext).cancelUniqueWork(LOOP_WORK_NAME)
        }

        // 15 perces periodikus "szívverés" (WidgetKeepAliveWorker): ha a frissítő hurok
        // bármilyen okból meghalt (WorkManager futásidő-limit, process-halál, el nem
        // kézbesített képernyő-broadcast), legfeljebb 15 percen belül újraéleszti.
        // A WorkManager a periodikus munkát a telefon újraindítása után is megőrzi.
        fun ensureKeepAlive(context: Context) {
            val request = PeriodicWorkRequest.Builder(
                WidgetKeepAliveWorker::class.java, 15, TimeUnit.MINUTES
            ).build()
            WorkManager.getInstance(context.applicationContext)
                .enqueueUniquePeriodicWork(KEEPALIVE_WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        fun cancelKeepAlive(context: Context) {
            WorkManager.getInstance(context.applicationContext).cancelUniqueWork(KEEPALIVE_WORK_NAME)
        }
    }

    private fun isScreenOn(): Boolean {
        val pm = applicationContext.getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isInteractive
    }

    // Hálózati állapot a lekérdezéshez:
    // - WIFI: van csatlakozott Wi-Fi (bármelyik hálózat, nem csak az aktív: bekapcsolt
    //   Tailscale mellett az aktív hálózat maga a VPN, így otthon is "nem Wi-Fi"-nek látszana);
    // - VPN: Wi-Fi nincs, de aktív VPN (Tailscale) van -> mobilneten is hazaér;
    // - NONE: egyik sem -> nem kérdezünk (idegen hálózat címeit nem próbálgatjuk).
    private enum class NetMode { WIFI, VPN, NONE }

    private fun currentNetMode(cm: ConnectivityManager): NetMode {
        @Suppress("DEPRECATION")
        val anyWifi = cm.allNetworks.any {
            cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        }
        if (anyWifi) return NetMode.WIFI
        val activeIsVpn = cm.getNetworkCapabilities(cm.activeNetwork)
            ?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
        return if (activeIsVpn) NetMode.VPN else NetMode.NONE
    }

    override fun doWork(): Result {
        val client = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .build()

        val cm = applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

        // WiFi visszatérés figyelése: ha a hurok futása közben újra elérhetővé válik a WiFi
        // (pl. hazaérünk az idegen hálózatból), nem várjuk ki az 5 mp-es ciklust, hanem
        // azonnal frissítünk.
        val wifiReconnected = AtomicBoolean(false)
        val wifiCallback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                wifiReconnected.set(true)
            }
        }
        var callbackRegistered = false
        try {
            cm.registerNetworkCallback(
                NetworkRequest.Builder()
                    .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                    .build(),
                wifiCallback
            )
            callbackRegistered = true
        } catch (e: Exception) {
            e.printStackTrace()
        }

        try {
            // Belső frissítési hurok. Kilépési okok: WorkManager stop (10 perces futásidő-limit
            // vagy cancel), illetve a képernyő kikapcsolása (lezárt telefonon nem pazarlunk
            // akkumulátort és hálózatot).
            while (!isStopped && isScreenOn() && hasWidgets(applicationContext)) {
                val mode = currentNetMode(cm)
                fetchAndUpdate(mode, client)

                // Várakozás: Wi-Fin 5 mp, Wi-Fi nélkül VPN-en át (mobilnet) 30 mp a mobiladat
                // kímélése miatt. A stop jelzésre és a WiFi visszatérésére is figyelünk: hazaérve
                // azonnal frissít, és visszaáll az 5 mp-es ütem.
                val pauseMs = if (mode == NetMode.VPN) 30_000L else 5_000L
                val sleepEnd = System.currentTimeMillis() + pauseMs
                while (System.currentTimeMillis() < sleepEnd && !isStopped) {
                    if (wifiReconnected.getAndSet(false)) break
                    if (refreshNow.getAndSet(false)) break      // koppintás a widgetre: azonnali frissítés
                    Thread.sleep(100)
                }
            }
        } catch (e: InterruptedException) {
            // A WorkManager a stop-ot (pl. a 10 perces futásidő-limit lejártakor) a szál
            // megszakításával (interrupt) jelzi, amitől a Thread.sleep() InterruptedException-t
            // dob. Ezt el KELL kapni: enélkül a doWork() kivétellel halna meg, és a lenti
            // finally-beli önújraindítás sosem futna le -- korábban pontosan emiatt "ragadt be"
            // a widget, miután a felhasználó elhagyta a WiFi hatósugarát, majd visszatért.
            Thread.currentThread().interrupt()
        } finally {
            if (callbackRegistered) {
                try {
                    cm.unregisterNetworkCallback(wifiCallback)
                } catch (e: Exception) {
                    e.printStackTrace()
                }
            }
            // Önújraindítás: ha nem a képernyő lekapcsolása miatt álltunk le, új hurkot
            // ütemezünk. REPLACE-t használunk, mert a saját, épp lezáruló rekordunk még
            // "futó" állapotú lehet, amin a KEEP fennakadna. A REPLACE-lánc nem tud
            // elszabadulni: egy még el sem indult (csak sorban álló) worker megszakításakor
            // nem fut le a finally, így nem ütemez újabbat. Ha közben az utolsó widget is lekerült
            // a kezdőképernyőről, az enqueueLoop nem ütemez (nincs mit frissíteni).
            if (isScreenOn()) {
                enqueueLoop(applicationContext, ExistingWorkPolicy.REPLACE)
            }
        }

        return Result.success()
    }

    private fun fetchAndUpdate(mode: NetMode, client: OkHttpClient) {
        // Csak Wi-Fin vagy aktív VPN-en (Tailscale) át kérdezünk
        if (mode == NetMode.NONE) {
            updateUIOffline()
            return
        }

        val prefs = applicationContext.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val ip = prefs.getString("ip", "192.168.1.100") ?: "192.168.1.100"
        val password = prefs.getString("password", "") ?: ""
        val baseUrl = "http://$ip:8080"

        try {
            // Login csak akkor, ha nincs érvényes session
            if (sessionToken == null || sessionKey == null) {
                if (!doLogin(client, baseUrl, password)) {
                    updateUIOffline()
                    return
                }
            }

            val request = Request.Builder()
                .url("$baseUrl/api/status")
                .header("Cookie", sessionToken ?: "")
                .get()
                .build()

            client.newCall(request).execute().use { response ->
                when {
                    response.code == 401 -> {
                        // Session lejárt – töröljük, következő körben újra loginol
                        sessionToken = null
                        sessionKey = null
                        updateUIOffline()
                    }
                    response.isSuccessful -> {
                        val responseBody = response.body?.string() ?: ""
                        val encryptedJson = JSONObject(responseBody)
                        val finalJson: JSONObject

                        if (encryptedJson.has("enc") && encryptedJson.getBoolean("enc")) {
                            val iv = encryptedJson.getString("iv")
                            val data = encryptedJson.getString("data")
                            val mac = encryptedJson.getString("mac")
                            val decryptedString = CryptoUtils.decryptPayload(sessionKey!!, iv, data, mac)
                            finalJson = JSONObject(decryptedString)
                        } else {
                            finalJson = JSONObject(responseBody)
                        }

                        // Sikeres frissítés – időt elmentjük memóriába és tartósan is
                        lastSuccessTime = System.currentTimeMillis()
                        prefs.edit().putLong("lastSuccessTime", lastSuccessTime).apply()
                        updateUIOnline(finalJson)
                    }
                    else -> updateUIOffline()
                }
            }
        } catch (e: InterruptedException) {
            // A stop jelzést tovább kell engedni a doWork() felé, nem szabad itt elnyelni
            throw e
        } catch (e: Exception) {
            e.printStackTrace()
            if (e is SecurityException) {
                // MAC ellenőrzés sikertelen – session érvénytelen
                sessionToken = null
                sessionKey = null
            }
            updateUIOffline()
        }
    }

    private fun fetchPbkdf2Iterations(client: OkHttpClient, baseUrl: String): Int {
        // A szerveren a pbkdf2_iterations konfigurálható (pl. gyengébb hardveren, mint egy
        // Raspberry Pi Zero, a README ajánlása szerint csökkenthető). A widget nem hardkódolhatja
        // ezt az értéket, különben a szerverrel eltérő session kulcsot származtatna és a
        // bejelentkezés "Helytelen jelszó"-val hiúsulna meg, holott a jelszó helyes.
        return try {
            val request = Request.Builder().url("$baseUrl/api/login_info").get().build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val body = response.body?.string() ?: ""
                    JSONObject(body).optInt("pbkdf2_iterations", 100000)
                } else {
                    100000
                }
            }
        } catch (e: Exception) {
            100000
        }
    }

    private fun doLogin(client: OkHttpClient, baseUrl: String, password: String): Boolean {
        val iterations = fetchPbkdf2Iterations(client, baseUrl)
        val nonce = CryptoUtils.generateNonce()
        val key = CryptoUtils.deriveSessionKey(password, nonce, iterations)
        val authProof = CryptoUtils.generateAuthProof(key)

        val loginJson = JSONObject().apply {
            put("clientNonce", nonce)
            put("authProof", authProof)
        }.toString()

        val request = Request.Builder()
            .url("$baseUrl/api/login")
            .post(loginJson.toRequestBody("application/json".toMediaType()))
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                val body = response.body?.string() ?: ""
                val setCookie = response.header("Set-Cookie")
                // Szigorú validáció: idegen hálózaton (pl. captive portálos vendég WiFi) egy
                // átirányító oldal is válaszolhat HTTP 200-zal. Csak akkor fogadjuk el a
                // bejelentkezést, ha tényleg a mi szerverünk válaszolt: JSON {"status":"success"}
                // body ÉS valódi "session=..." süti is érkezett. Enélkül a sessionToken üres
                // stringgel ("") töltődött fel, ami nem null, így hazaérve a widget nem
                // loginolt újra, csak egy felesleges 401-es kör után.
                val statusOk = try {
                    JSONObject(body).optString("status") == "success"
                } catch (e: Exception) {
                    false
                }
                if (response.isSuccessful && statusOk && setCookie != null && setCookie.startsWith("session=")) {
                    sessionToken = setCookie.split(";").first()
                    sessionKey = key
                    true
                } else {
                    sessionToken = null
                    sessionKey = null
                    false
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
            false
        }
    }

    private fun updateUIOnline(data: JSONObject) {
        val appWidgetManager = AppWidgetManager.getInstance(applicationContext)
        val componentName = ComponentName(applicationContext, DeyeWidgetProvider::class.java)
        val appWidgetIds = appWidgetManager.getAppWidgetIds(componentName)

        val chargerVal = if (data.optInt("charger_power", 0) < 100) 0 else data.optInt("charger_power", 0)
        // A VALUE_IDS sorrendjében
        val texts = listOf(
            "Napelem: ${data.optInt("pv_power", 0)} W",
            "Hálózat: ${data.optInt("grid_power", 0)} W",
            "Akku SoC: ${data.optInt("battery_soc", 0)} %",
            "Akku Telj.: ${data.optInt("battery_power", 0)} W",
            "Ház: ${data.optInt("ups_load_power", 0)} W",
            "Autó töltés: $chargerVal W"
        )
        for (appWidgetId in appWidgetIds) {
            val views = RemoteViews(applicationContext.packageName, R.layout.widget_layout)
            views.setViewVisibility(R.id.online_dark_overlay, android.view.View.VISIBLE)
            views.setViewVisibility(R.id.tv_title, android.view.View.VISIBLE)
            VALUE_IDS.forEachIndexed { i, id -> views.setTextViewText(id, texts[i]) }
            appWidgetManager.partiallyUpdateAppWidget(appWidgetId, views)
        }
        // A megjelenített értékek megjegyzése: a provider teljes frissítése (koppintás) ezekből tölti vissza
        val prefs = applicationContext.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val joined = texts.joinToString("\n")
        if (!prefs.getBoolean(PREF_WIDGET_ONLINE, false) || prefs.getString(PREF_WIDGET_TEXTS, null) != joined) {
            prefs.edit().putString(PREF_WIDGET_TEXTS, joined).putBoolean(PREF_WIDGET_ONLINE, true).apply()
        }
    }

    private fun updateUIOffline() {
        // 15 másodperces türelmi idő – ha még friss az adat, nem töröljük le
        val prefs = applicationContext.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val storedLastSuccess = prefs.getLong("lastSuccessTime", lastSuccessTime)
        val effectiveLastSuccess = maxOf(lastSuccessTime, storedLastSuccess)

        if (System.currentTimeMillis() - effectiveLastSuccess < 15000) {
            return // Grace period – megtartjuk a régi adatot
        }
        if (prefs.getBoolean(PREF_WIDGET_ONLINE, false)) {
            prefs.edit().putBoolean(PREF_WIDGET_ONLINE, false).apply()   // a widget mostantól üres: koppintásra se töltsük vissza a régit
        }

        val appWidgetManager = AppWidgetManager.getInstance(applicationContext)
        val componentName = ComponentName(applicationContext, DeyeWidgetProvider::class.java)
        val appWidgetIds = appWidgetManager.getAppWidgetIds(componentName)

        for (appWidgetId in appWidgetIds) {
            val views = RemoteViews(applicationContext.packageName, R.layout.widget_layout)
            views.setViewVisibility(R.id.online_dark_overlay, android.view.View.GONE)
            views.setViewVisibility(R.id.tv_title, android.view.View.GONE)
            views.setTextViewText(R.id.tv_pv, "")
            views.setTextViewText(R.id.tv_grid, "")
            views.setTextViewText(R.id.tv_soc, "")
            views.setTextViewText(R.id.tv_batt_power, "")
            views.setTextViewText(R.id.tv_ups, "")
            views.setTextViewText(R.id.tv_charger, "")
            appWidgetManager.partiallyUpdateAppWidget(appWidgetId, views)
        }
    }
}
