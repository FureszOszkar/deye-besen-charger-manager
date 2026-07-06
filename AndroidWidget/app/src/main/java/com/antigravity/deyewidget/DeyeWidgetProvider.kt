package com.antigravity.deyewidget

import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.widget.RemoteViews
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager

class DeyeWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        for (appWidgetId in appWidgetIds) {
            updateAppWidget(context, appWidgetManager, appWidgetId)
        }
        // Worker azonnali indítása widget lerakáskor (ha már fut, KEEP nem szakítja meg)
        ScreenUnlockReceiver.isScreenActive = true
        val workRequest = OneTimeWorkRequest.Builder(WidgetUpdateWorker::class.java).build()
        WorkManager.getInstance(context)
            .enqueueUniqueWork("DeyeWidgetLoop", ExistingWorkPolicy.KEEP, workRequest)
    }

    companion object {
        fun updateAppWidget(context: Context, appWidgetManager: AppWidgetManager, appWidgetId: Int) {
            val views = RemoteViews(context.packageName, R.layout.widget_layout)

            val prefs = context.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
            val alpha = prefs.getInt("bg_alpha", 255)
            views.setInt(R.id.img_background, "setImageAlpha", alpha)

            appWidgetManager.updateAppWidget(appWidgetId, views)
        }
    }
}
