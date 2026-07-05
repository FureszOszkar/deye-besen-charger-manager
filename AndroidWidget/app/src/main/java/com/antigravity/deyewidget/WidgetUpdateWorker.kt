package com.antigravity.deyewidget

import android.appwidget.AppWidgetManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.util.Log
import android.view.View
import android.widget.RemoteViews
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
    }

    override fun doWork(): Result {
        val appWidgetManager = AppWidgetManager.getInstance(applicationContext)
        val componentName = ComponentName(applicationContext, DeyeWidgetProvider::class.java)
        val appWidgetIds = appWidgetManager.getAppWidgetIds(componentName)

        val prefs = applicationContext.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val ip = prefs.getString("ip", "192.168.1.100") ?: "192.168.1.100"
        val password = prefs.getString("password", "") ?: ""
        val baseUrl = "http://$ip:8080"

        val client = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .build()

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

                val loginResponse = client.newCall(loginRequest).execute()
                if (!loginResponse.isSuccessful) {
                    sessionToken = null
                    sessionKey = null
                    updateUI(applicationContext, appWidgetManager, appWidgetIds, null, false)
                    return Result.retry()
                }

                val setCookie = loginResponse.header("Set-Cookie")
                sessionToken = setCookie?.split(";")?.firstOrNull() ?: ""
                sessionKey = key
            }

            // FETCH DATA WITH SESSION
            val request = Request.Builder()
                .url("$baseUrl/api/status")
                .header("Cookie", sessionToken ?: "")
                .get()
                .build()

            val response = client.newCall(request).execute()
            
            if (response.code == 401) {
                sessionToken = null
                sessionKey = null
                return Result.retry()
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
            } else {
                updateUI(applicationContext, appWidgetManager, appWidgetIds, null, false)
            }
        } catch (e: Exception) {
            e.printStackTrace()
            if (e is SecurityException) {
                sessionToken = null
                sessionKey = null
            }
            updateUI(applicationContext, appWidgetManager, appWidgetIds, null, false)
            return Result.retry()
        }

        return Result.success()
    }

    private fun updateUI(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray, data: JSONObject?, success: Boolean) {
        val prefs = context.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val alpha = prefs.getInt("bg_alpha", 255)

        for (appWidgetId in appWidgetIds) {
            val views = RemoteViews(context.packageName, R.layout.widget_layout)
            views.setInt(R.id.img_background, "setImageAlpha", alpha)
            
            val intent = Intent(context, UpdateReceiver::class.java)
            intent.action = "com.antigravity.deyewidget.ACTION_REFRESH"
            intent.putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
            val pendingIntent = android.app.PendingIntent.getBroadcast(
                context, appWidgetId, intent,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE
            )
            views.setOnClickPendingIntent(R.id.btn_refresh, pendingIntent)
            
            if (success && data != null) {
                views.setViewVisibility(R.id.online_dark_overlay, View.VISIBLE)
                views.setViewVisibility(R.id.layout_data, View.VISIBLE)
                views.setTextViewText(R.id.tv_pv, "Napelem: ${data.optInt("pv_power", 0)} W")
                views.setTextViewText(R.id.tv_grid, "Hálózat: ${data.optInt("grid_power", 0)} W")
                views.setTextViewText(R.id.tv_soc, "Akku SoC: ${data.optInt("battery_soc", 0)} %")
                views.setTextViewText(R.id.tv_batt_power, "Akku Telj.: ${data.optInt("battery_power", 0)} W")
                views.setTextViewText(R.id.tv_ups, "Ház: ${data.optInt("ups_load_power", 0)} W")
                views.setTextViewText(R.id.tv_charger, "Autó töltés: ${data.optInt("charger_power", 0)} W")
            } else {
                views.setViewVisibility(R.id.layout_data, View.GONE)
                views.setViewVisibility(R.id.online_dark_overlay, View.GONE)
            }
            appWidgetManager.updateAppWidget(appWidgetId, views)
        }
    }

    class UpdateReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == "com.antigravity.deyewidget.ACTION_REFRESH") {
                val workRequest = OneTimeWorkRequest.Builder(WidgetUpdateWorker::class.java).build()
                WorkManager.getInstance(context).enqueue(workRequest)
            }
        }
    }
}
