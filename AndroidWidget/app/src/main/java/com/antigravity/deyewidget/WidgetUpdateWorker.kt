package com.antigravity.deyewidget

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.widget.RemoteViews
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class WidgetUpdateWorker(appContext: Context, workerParams: WorkerParameters) :
    Worker(appContext, workerParams) {

    companion object {
        // Ezek osztályszintű változók – túlélik a REPLACE-et és az újraindítást
        private var sessionToken: String? = null
        private var sessionKey: ByteArray? = null
        private var lastSuccessTime: Long = 0L
    }

    override fun doWork(): Result {
        val client = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .build()

        // Belső frissítési hurok – addig fut, amíg a WorkManager le nem állítja
        while (!isStopped) {
            fetchAndUpdate(client)

            // 5 másodperces várakozás, de isStopped()-ot figyeli
            val sleepEnd = System.currentTimeMillis() + 5000
            while (System.currentTimeMillis() < sleepEnd && !isStopped) {
                Thread.sleep(100)
            }
        }

        // Ha a WorkManager 10 perces korlátja miatt állt le (és nem azért mert képernyő lement),
        // automatikusan újraindítjuk a Worker-t
        if (ScreenUnlockReceiver.isScreenActive) {
            val workRequest = OneTimeWorkRequest.Builder(WidgetUpdateWorker::class.java).build()
            WorkManager.getInstance(applicationContext)
                .enqueueUniqueWork("DeyeWidgetLoop", ExistingWorkPolicy.REPLACE, workRequest)
        }

        return Result.success()
    }

    private fun fetchAndUpdate(client: OkHttpClient) {
        // WiFi ellenőrzés
        val cm = applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
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

    private fun doLogin(client: OkHttpClient, baseUrl: String, password: String): Boolean {
        val nonce = CryptoUtils.generateNonce()
        val key = CryptoUtils.deriveSessionKey(password, nonce, 100000)
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
                if (response.isSuccessful) {
                    val setCookie = response.header("Set-Cookie")
                    sessionToken = setCookie?.split(";")?.firstOrNull() ?: ""
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
            // Kizárólag a szövegeket frissítük – nincs setImageAlpha, nincs setOnClickPendingIntent
            views.setViewVisibility(R.id.tv_title, android.view.View.VISIBLE)
            views.setTextViewText(R.id.tv_pv, "Napelem: ${data.optInt("pv_power", 0)} W")
            views.setTextViewText(R.id.tv_grid, "Hálózat: ${data.optInt("grid_power", 0)} W")
            views.setTextViewText(R.id.tv_soc, "Akku SoC: ${data.optInt("battery_soc", 0)} %")
            views.setTextViewText(R.id.tv_batt_power, "Akku Telj.: ${data.optInt("battery_power", 0)} W")
            views.setTextViewText(R.id.tv_ups, "Ház: ${data.optInt("ups_load_power", 0)} W")
            views.setTextViewText(R.id.tv_charger, "Autó töltés: ${data.optInt("charger_power", 0)} W")
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
