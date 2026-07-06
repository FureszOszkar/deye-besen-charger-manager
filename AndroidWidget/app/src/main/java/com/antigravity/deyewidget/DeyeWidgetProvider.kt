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
            // Regisztráljuk a frissítés gomb eseményét anélkül, hogy a teljes layoutot felülírnánk (így elkerüljük a villogást)
            val views = RemoteViews(context.packageName, R.layout.widget_layout)
            
            val intent = Intent(context, WidgetUpdateWorker.UpdateReceiver::class.java)
            intent.action = "com.antigravity.deyewidget.ACTION_REFRESH"
            val pendingIntent = PendingIntent.getBroadcast(
                context, appWidgetId, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            views.setOnClickPendingIntent(R.id.btn_refresh, pendingIntent)
            
            // A partiallyUpdateAppWidget csak a módosított tulajdonságot (kattintás) érvényesíti a meglévő widgeten
            appWidgetManager.partiallyUpdateAppWidget(appWidgetId, views)

            // És el is indítunk egy háttérbeli frissítést
            context.sendBroadcast(intent)
        }
    }
}
