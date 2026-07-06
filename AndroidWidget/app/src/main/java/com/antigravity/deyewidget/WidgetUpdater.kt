package com.antigravity.deyewidget

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.view.View
import android.widget.RemoteViews
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

object WidgetUpdater {
    private var sessionToken: String? = null
    private var sessionKey: ByteArray? = null
    private var lastDataHash: Int = 0

    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .build()

    // Ezt a függvényt hívjuk a ScreenUnlockReceiverből és a manuális frissítés gombból
    fun fetchAndUpdate(context: Context) {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val activeNetwork = cm.activeNetwork
        val networkCapabilities = cm.getNetworkCapabilities(activeNetwork)
        val hasWifi = networkCapabilities?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true

        // 1. OFFLINE KEZELÉS: Ha nincs WiFi, egyszerűen megszakítjuk a frissítést.
        // Nincs parancsküldés a Launchernek, így garantáltan nincs villogás sem.
        if (!hasWifi) {
            return
        }

        val prefs = context.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val ip = prefs.getString("ip", "192.168.1.100") ?: "192.168.1.100"
        val password = prefs.getString("password", "") ?: ""
        val baseUrl = "http://$ip:8080"

        // Aszinkron Coroutine indítása a hálózati kéréshez
        CoroutineScope(Dispatchers.IO).launch {
            try {
                if (sessionToken == null || sessionKey == null) {
                    if (!doLogin(baseUrl, password)) return@launch
                }

                val request = Request.Builder()
                    .url("$baseUrl/api/status")
                    .header("Cookie", sessionToken ?: "")
                    .get()
                    .build()

                httpClient.newCall(request).execute().use { response ->
                    if (response.code == 401) {
                        sessionToken = null
                        sessionKey = null
                        return@use
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

                        // 2. OKOS FRISSÍTÉS: Csak akkor frissítjük a felületet, ha változott az adat
                        val currentHash = finalJson.toString().hashCode()
                        if (currentHash != lastDataHash) {
                            lastDataHash = currentHash
                            withContext(Dispatchers.Main) {
                                updateWidgetUI(context, finalJson)
                            }
                        }
                    }
                }
            } catch (e: Exception) {
                e.printStackTrace()
                if (e is SecurityException) {
                    sessionToken = null
                    sessionKey = null
                }
            }
        }
    }

    private fun doLogin(baseUrl: String, password: String): Boolean {
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

        return try {
            httpClient.newCall(loginRequest).execute().use { loginResponse ->
                if (loginResponse.isSuccessful) {
                    val setCookie = loginResponse.header("Set-Cookie")
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

    private fun updateWidgetUI(context: Context, data: JSONObject) {
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
            
            // Szigorúan csak partiallyUpdateAppWidget a villogás elkerülésére!
            appWidgetManager.partiallyUpdateAppWidget(appWidgetId, views)
        }
    }
}
