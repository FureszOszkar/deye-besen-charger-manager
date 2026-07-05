package com.antigravity.deyewidget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

class DeyeWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        for (appWidgetId in appWidgetIds) {
            updateAppWidget(context, appWidgetManager, appWidgetId)
        }
    }

    companion object {
        fun updateAppWidget(context: Context, appWidgetManager: AppWidgetManager, appWidgetId: Int) {
            // Trigger actual update in background, do NOT overwrite layout with default XML to prevent flashing
            val updateIntent = Intent(context, WidgetUpdateWorker.UpdateReceiver::class.java)
            updateIntent.action = "com.antigravity.deyewidget.ACTION_REFRESH"
            context.sendBroadcast(updateIntent)
        }
    }
}
