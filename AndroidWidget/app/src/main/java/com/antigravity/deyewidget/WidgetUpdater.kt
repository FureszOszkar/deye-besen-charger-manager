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
import java.util.concurrent.atomic.AtomicBoolean

class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == "com.antigravity.deyewidget.ACTION_REFRESH") {
            // goAsync() biztosítja, hogy az Android ne lője le a memóriát azonnal
            val pendingResult = goAsync()
            WidgetUpdater.fetchAndUpdate(context, pendingResult)
        }
    }
}

object WidgetUpdater {
    private var sessionToken: String? = null
    private var sessionKey: ByteArray? = null
    private var lastDataHash: Int = 0
    private var isOfflineStateDisplayed: Boolean = false
    private var lastSuccessTime: Long = 0L
    
    // Szálbiztos zárolás a dupla indítás és beragadás ellen
    private val isFetching = AtomicBoolean(false)

    // Szigorú 5 másodperces időkorlát, hogy hálózati fagyás esetén megszakítsa a kérést
    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .build()

    fun fetchAndUpdate(context: Context, pendingResult: BroadcastReceiver.PendingResult?) {
        if (!isFetching.compareAndSet(false, true)) {
            // Ha már fut egy kérés, azonnal visszatérünk
            pendingResult?.finish()
            return
        }

        CoroutineScope(Dispatchers.IO).launch {
            try {
                performFetch(context)
            } finally {
                // A lockot mindig feloldjuk, még Exception vagy 5 másodperces Timeout esetén is!
                isFetching.set(false)
                pendingResult?.finish()
            }
        }
    }

    private suspend fun performFetch(context: Context) {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val activeNetwork = cm.activeNetwork
        val networkCapabilities = cm.getNetworkCapabilities(activeNetwork)
        val hasWifi = networkCapabilities?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true

        if (!hasWifi) {
            handleOfflineState(context)
            return
        }

        val prefs = context.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val ip = prefs.getString("ip", "192.168.1.100") ?: "192.168.1.100"
        val password = prefs.getString("password", "") ?: ""
        val baseUrl = "http://$ip:8080"

        try {
            if (sessionToken == null || sessionKey == null) {
                if (!doLogin(baseUrl, password)) {
                    handleOfflineState(context)
                    return
                }
            }

            val request = Request.Builder()
                .url("$baseUrl/api/status")
                .header("Cookie", sessionToken ?: "")
                .get()
                .build()

            var fetchSuccess = false
            httpClient.newCall(request).execute().use { response ->
                if (response.code == 401) {
                    sessionToken = null
                    sessionKey = null
                    handleOfflineState(context)
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

                    fetchSuccess = true
                    lastSuccessTime = System.currentTimeMillis()
                    updateUIOnline(context, finalJson)
                } else {
                    handleOfflineState(context)
                }
            }
            if (!fetchSuccess) {
                handleOfflineState(context)
            }
        } catch (e: Exception) {
            e.printStackTrace()
            if (e is SecurityException) {
                sessionToken = null
                sessionKey = null
            }
            handleOfflineState(context)
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

        val request = Request.Builder()
            .url("$baseUrl/api/login")
            .post(loginJson.toRequestBody("application/json".toMediaType()))
            .build()

        return try {
            httpClient.newCall(request).execute().use { response ->
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

    private suspend fun updateUIOnline(context: Context, data: JSONObject) {
        val currentHash = data.toString().hashCode()
        if (currentHash == lastDataHash && !isOfflineStateDisplayed) {
            // Nincs változás az adatban, nem küldünk parancsot a Launchernek
            return
        }

        lastDataHash = currentHash
        isOfflineStateDisplayed = false

        withContext(Dispatchers.Main) {
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
    }

    private suspend fun handleOfflineState(context: Context) {
        // Tolerancia: Csak 15 másodpercnyi folyamatos hiba után törlünk
        val isStale = System.currentTimeMillis() - lastSuccessTime > 15000
        
        if (!isStale) {
            return // Még türelmi időn belül vagyunk, az utolsó adat marad a képernyőn!
        }

        if (isOfflineStateDisplayed) {
            return // Már töröltük a számokat, nem küldünk ismételt parancsot!
        }

        isOfflineStateDisplayed = true
        lastDataHash = 0

        withContext(Dispatchers.Main) {
            val appWidgetManager = AppWidgetManager.getInstance(context)
            val componentName = ComponentName(context, DeyeWidgetProvider::class.java)
            val appWidgetIds = appWidgetManager.getAppWidgetIds(componentName)

            for (appWidgetId in appWidgetIds) {
                val views = RemoteViews(context.packageName, R.layout.widget_layout)
                // Offline állapot: csak a számokat ürítjük ki (így nincs "maradék felirat")
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
}
