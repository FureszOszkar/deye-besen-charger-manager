package com.antigravity.deyewidget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import androidx.work.ExistingWorkPolicy

class DeyeWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        for (appWidgetId in appWidgetIds) {
            updateAppWidget(context, appWidgetManager, appWidgetId)
        }
        // Frissítő hurok indítása (KEEP: ha már fut, nem szakítjuk meg) és a 15 perces
        // életben tartó heartbeat ütemezése. Az onUpdate koppintás-frissítéskor is lefut,
        // így egy beragadt widget a rákoppintással azonnal újraéleszthető.
        WidgetUpdateWorker.enqueueLoop(context, ExistingWorkPolicy.KEEP)
        WidgetUpdateWorker.ensureKeepAlive(context)
    }

    override fun onDisabled(context: Context) {
        // Az utolsó widget is lekerült a kezdőképernyőről: hurok és heartbeat leállítása
        WidgetUpdateWorker.cancelLoop(context)
        WidgetUpdateWorker.cancelKeepAlive(context)
        super.onDisabled(context)
    }

    companion object {
        fun updateAppWidget(context: Context, appWidgetManager: AppWidgetManager, appWidgetId: Int) {
            val views = RemoteViews(context.packageName, R.layout.widget_layout)

            val prefs = context.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
            val alpha = prefs.getInt("bg_alpha", 255)
            views.setInt(R.id.img_background, "setImageAlpha", alpha)

            // Koppintás a widgetre = kézi frissítés-kényszerítés. A broadcast az onUpdate-et
            // hívja meg, ami KEEP-pel újraindítja a frissítő hurkot, ha az meghalt volna.
            // (Élő hurok mellett a koppintásnak nincs mellékhatása.)
            val refreshIntent = Intent(context, DeyeWidgetProvider::class.java).apply {
                action = AppWidgetManager.ACTION_APPWIDGET_UPDATE
                putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, intArrayOf(appWidgetId))
            }
            val pendingIntent = PendingIntent.getBroadcast(
                context, appWidgetId, refreshIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            views.setOnClickPendingIntent(R.id.widget_base, pendingIntent)

            appWidgetManager.updateAppWidget(appWidgetId, views)
        }
    }
}
