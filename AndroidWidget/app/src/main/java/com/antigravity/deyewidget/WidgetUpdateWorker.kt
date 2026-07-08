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

        fun enqueueLoop(context: Context, policy: ExistingWorkPolicy) {
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
            while (!isStopped && isScreenOn()) {
                fetchAndUpdate(cm, client)

                // 5 mp várakozás, de a stop jelzésre és a WiFi visszatérésére is figyelünk
                val sleepEnd = System.currentTimeMillis() + 5000
                while (System.currentTimeMillis() < sleepEnd && !isStopped) {
                    if (wifiReconnected.getAndSet(false)) break
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
            // nem fut le a finally, így nem ütemez újabbat.
            if (isScreenOn()) {
                enqueueLoop(applicationContext, ExistingWorkPolicy.REPLACE)
            }
        }

        return Result.success()
    }

    private fun fetchAndUpdate(cm: ConnectivityManager, client: OkHttpClient) {
        // WiFi ellenőrzés
        val hasWifi = cm.getNetworkCapabilities(cm.activeNetwork)
            ?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true

        if (!hasWifi) {
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

        for (appWidgetId in appWidgetIds) {
            val views = RemoteViews(applicationContext.packageName, R.layout.widget_layout)
            val chargerVal = if (data.optInt("charger_power", 0) < 100) 0 else data.optInt("charger_power", 0)
            views.setViewVisibility(R.id.online_dark_overlay, android.view.View.VISIBLE)
            views.setViewVisibility(R.id.tv_title, android.view.View.VISIBLE)
            views.setTextViewText(R.id.tv_pv, "Napelem: ${data.optInt("pv_power", 0)} W")
            views.setTextViewText(R.id.tv_grid, "Hálózat: ${data.optInt("grid_power", 0)} W")
            views.setTextViewText(R.id.tv_soc, "Akku SoC: ${data.optInt("battery_soc", 0)} %")
            views.setTextViewText(R.id.tv_batt_power, "Akku Telj.: ${data.optInt("battery_power", 0)} W")
            views.setTextViewText(R.id.tv_ups, "Ház: ${data.optInt("ups_load_power", 0)} W")
            views.setTextViewText(R.id.tv_charger, "Autó töltés: $chargerVal W")
            appWidgetManager.partiallyUpdateAppWidget(appWidgetId, views)
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
