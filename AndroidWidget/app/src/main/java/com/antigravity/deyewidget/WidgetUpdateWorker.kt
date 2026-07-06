package com.antigravity.deyewidget

import android.appwidget.AppWidgetManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.util.Log
import android.view.View
import android.widget.RemoteViews
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class WidgetUpdateWorker(appContext: Context, workerParams: WorkerParameters) :
    Worker(appContext, workerParams) {

    companion object {
        private var sessionToken: String? = null
        private var sessionKey: ByteArray? = null
        // Singleton OkHttpClient: saját szálkészletet és kapcsolatpool-t tart fenn, nem kell minden hívásnál újat létrehozni
        private val httpClient: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .build()
    }

    override fun doWork(): Result {
        val appWidgetManager = AppWidgetManager.getInstance(applicationContext)
        val componentName = ComponentName(applicationContext, DeyeWidgetProvider::class.java)
        val appWidgetIds = appWidgetManager.getAppWidgetIds(componentName)

        val prefs = applicationContext.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val ip = prefs.getString("ip", "192.168.1.100") ?: "192.168.1.100"
        val password = prefs.getString("password", "") ?: ""
        val baseUrl = "http://$ip:8080"
        val client = httpClient

        val cm = applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val activeNetwork = cm.activeNetwork
        val networkCapabilities = cm.getNetworkCapabilities(activeNetwork)
        val hasWifi = networkCapabilities?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true

        if (!hasWifi) {
            updateUI(applicationContext, appWidgetManager, appWidgetIds, null, false)
            return Result.failure()
        }

        try {
            // LOGIN IF NO SESSION
            if (sessionToken == null || sessionKey == null) {
                val nonce = CryptoUtils.generateNonce()
                val key = CryptoUtils.deriveSessionKey(password, nonce, 100000)
                val authProof = CryptoUtils.generateAuthProof(key)

                val loginJson = JSONObject().apply {
                    put("clientNonce", nonce)
                    put("authProof", authProof)
                }.toString()

                val loginRequest = Request.Builder()
                    .url("$baseUrl/api/login")
                    .post(loginJson.toRequestBody("application/json".toMediaType()))
                    .build()

                val loginResult = client.newCall(loginRequest).execute().use { loginResponse ->
                    if (!loginResponse.isSuccessful) {
                        sessionToken = null
                        sessionKey = null
                        null // jelzi a sikertelen bejelentkezést
                    } else {
                        val setCookie = loginResponse.header("Set-Cookie")
                        Pair(setCookie?.split(";")?.firstOrNull() ?: "", key)
                    }
                }
                if (loginResult == null) {
                    updateUI(applicationContext, appWidgetManager, appWidgetIds, null, false)
                    return Result.failure()
                }
                sessionToken = loginResult.first
                sessionKey = loginResult.second
            }

            // FETCH DATA WITH SESSION
            val request = Request.Builder()
                .url("$baseUrl/api/status")
                .header("Cookie", sessionToken ?: "")
                .get()
                .build()

            var fetchSuccess = false
            client.newCall(request).execute().use { response ->
                if (response.code == 401) {
                    sessionToken = null
                    sessionKey = null
                    return@use // kilépünk a use blokkból, a body le lesz zárva
                }

                if (response.isSuccessful) {
                    val responseBody = response.body?.string() ?: ""
                    val encryptedJson = JSONObject(responseBody)

                    if (encryptedJson.has("enc") && encryptedJson.getBoolean("enc")) {
                        val iv = encryptedJson.getString("iv")
                        val data = encryptedJson.getString("data")
                        val mac = encryptedJson.getString("mac")

                        val decryptedString = CryptoUtils.decryptPayload(sessionKey!!, iv, data, mac)
                        val json = JSONObject(decryptedString)
                        updateUI(applicationContext, appWidgetManager, appWidgetIds, json, true)
                    } else {
                        val json = JSONObject(responseBody)
                        updateUI(applicationContext, appWidgetManager, appWidgetIds, json, true)
                    }
                    fetchSuccess = true
                } else {
                    updateUI(applicationContext, appWidgetManager, appWidgetIds, null, false)
                }
            }
            // 401 esetén sessionToken már null -> következő futásnál újra bejelentkezik
            if (sessionToken == null && sessionKey == null) {
                return Result.failure()
            }
        } catch (e: Exception) {
            e.printStackTrace()
            if (e is SecurityException) {
                sessionToken = null
                sessionKey = null
            }
            updateUI(applicationContext, appWidgetManager, appWidgetIds, null, false)
            return Result.failure()
        }

        return Result.success()
    }


    private fun updateUI(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray, data: JSONObject?, success: Boolean) {
        val prefs = context.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)

        if (success && data != null) {
            // --- SIKERES FRISSÍTÉS ---
            prefs.edit().putLong("last_success_time", System.currentTimeMillis()).apply()

            for (appWidgetId in appWidgetIds) {
                // Csak a View láthatóságok és a szövegek frissülnek részlegesen
                val views = RemoteViews(context.packageName, R.layout.widget_layout)
                views.setViewVisibility(R.id.online_dark_overlay, View.VISIBLE)
                views.setViewVisibility(R.id.layout_content, View.VISIBLE)
                views.setViewVisibility(R.id.layout_data, View.VISIBLE)
                views.setTextViewText(R.id.tv_pv, "Napelem: ${data.optInt("pv_power", 0)} W")
                views.setTextViewText(R.id.tv_grid, "Hálózat: ${data.optInt("grid_power", 0)} W")
                views.setTextViewText(R.id.tv_soc, "Akku SoC: ${data.optInt("battery_soc", 0)} %")
                views.setTextViewText(R.id.tv_batt_power, "Akku Telj.: ${data.optInt("battery_power", 0)} W")
                views.setTextViewText(R.id.tv_ups, "Ház: ${data.optInt("ups_load_power", 0)} W")
                views.setTextViewText(R.id.tv_charger, "Autó töltés: ${data.optInt("charger_power", 0)} W")
                appWidgetManager.partiallyUpdateAppWidget(appWidgetId, views)
            }
        } else {
            // --- SIKERTELEN FRISSÍTÉS / OFFLINE ---
            val lastSuccessTime = prefs.getLong("last_success_time", 0L)
            val isStale = System.currentTimeMillis() - lastSuccessTime > 15000

            if (isStale) {
                // 15 másodpercnél régebbi adat: tartalmat elrejtjük, offline nézet
                for (appWidgetId in appWidgetIds) {
                    val views = RemoteViews(context.packageName, R.layout.widget_layout)
                    views.setViewVisibility(R.id.layout_content, View.GONE)
                    views.setViewVisibility(R.id.layout_data, View.GONE)
                    views.setViewVisibility(R.id.online_dark_overlay, View.GONE)
                    
                    // JAVÍTÁS: updateAppWidget helyett partiallyUpdateAppWidget
                    appWidgetManager.partiallyUpdateAppWidget(appWidgetId, views)
                }
            }
        }
    }

    class UpdateReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == "com.antigravity.deyewidget.ACTION_REFRESH") {
                val workRequest = OneTimeWorkRequest.Builder(WidgetUpdateWorker::class.java).build()
                WorkManager.getInstance(context).enqueueUniqueWork("DeyeWidgetUpdate", ExistingWorkPolicy.REPLACE, workRequest)
            }
        }
    }
}
