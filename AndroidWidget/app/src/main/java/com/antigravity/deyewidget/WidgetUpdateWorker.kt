package com.antigravity.deyewidget

import android.appwidget.AppWidgetManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.view.View
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
        private var sessionToken: String? = null
        private var sessionKey: ByteArray? = null
        private var lastDataHash: Int = 0
        private var isOfflineStateDisplayed: Boolean = false
    }

    class UpdateReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == "com.antigravity.deyewidget.ACTION_REFRESH") {
                val workRequest = OneTimeWorkRequest.Builder(WidgetUpdateWorker::class.java).build()
                WorkManager.getInstance(context).enqueueUniqueWork("DeyeWidgetUpdate", ExistingWorkPolicy.REPLACE, workRequest)
            }
        }
    }

    override fun doWork(): Result {
        val cm = applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val activeNetwork = cm.activeNetwork
        val networkCapabilities = cm.getNetworkCapabilities(activeNetwork)
        val hasWifi = networkCapabilities?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true

        if (!hasWifi) {
            updateUIOffline(applicationContext)
            return Result.failure()
        }

        val prefs = applicationContext.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val ip = prefs.getString("ip", "192.168.1.100") ?: "192.168.1.100"
        val password = prefs.getString("password", "") ?: ""
        val baseUrl = "http://$ip:8080"

        val client = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .build()

        try {
            if (sessionToken == null || sessionKey == null) {
                if (!doLogin(client, baseUrl, password)) {
                    updateUIOffline(applicationContext)
                    return Result.failure()
                }
            }

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
                    updateUIOffline(applicationContext)
                    return Result.failure()
                }

                if (response.isSuccessful) {
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

                    fetchSuccess = true
                    updateUIOnline(applicationContext, finalJson)
                } else {
                    updateUIOffline(applicationContext)
                }
            }
            return if (fetchSuccess) Result.success() else Result.failure()
        } catch (e: Exception) {
            e.printStackTrace()
            if (e is SecurityException) {
                sessionToken = null
                sessionKey = null
            }
            updateUIOffline(applicationContext)
            return Result.failure()
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

    private fun updateUIOnline(context: Context, data: JSONObject) {
        val currentHash = data.toString().hashCode()
        if (currentHash == lastDataHash && !isOfflineStateDisplayed) {
            // Nem változott semmi az előző sikeres lekérdezés óta, ÉS jelenleg is online vagyunk. Nincs teendő.
            return
        }

        lastDataHash = currentHash
        isOfflineStateDisplayed = false

        val appWidgetManager = AppWidgetManager.getInstance(context)
        val componentName = ComponentName(context, DeyeWidgetProvider::class.java)
        val appWidgetIds = appWidgetManager.getAppWidgetIds(componentName)

        for (appWidgetId in appWidgetIds) {
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
    }

    private fun updateUIOffline(context: Context) {
        if (isOfflineStateDisplayed) {
            // Már kiürítettük a feliratokat, nincs értelme minden másodpercben újraküldeni a parancsot (villogás elkerülése).
            return
        }

        isOfflineStateDisplayed = true
        lastDataHash = 0

        val appWidgetManager = AppWidgetManager.getInstance(context)
        val componentName = ComponentName(context, DeyeWidgetProvider::class.java)
        val appWidgetIds = appWidgetManager.getAppWidgetIds(componentName)

        for (appWidgetId in appWidgetIds) {
            val views = RemoteViews(context.packageName, R.layout.widget_layout)
            // Csendben kiürítjük a szövegeket, hogy ne villogjon a doboz eltüntetése miatt a Launcher hibája okán
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
