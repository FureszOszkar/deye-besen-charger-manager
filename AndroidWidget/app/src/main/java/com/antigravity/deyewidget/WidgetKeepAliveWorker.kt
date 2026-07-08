package com.antigravity.deyewidget

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import androidx.work.ExistingWorkPolicy
import androidx.work.Worker
import androidx.work.WorkerParameters

/**
 * 15 percenként futó biztonsági háló: ha a folyamatos frissítő hurok (WidgetUpdateWorker)
 * bármilyen okból meghalt (WorkManager futásidő-limit, process-halál, el nem kézbesített
 * képernyő-broadcast), újraéleszti -- de csak akkor, ha van kitett widget a kezdőképernyőn.
 * A KEEP policy miatt élő hurok mellett nem csinál semmit, így nincs felesleges újraindítás.
 */
class WidgetKeepAliveWorker(appContext: Context, params: WorkerParameters) :
    Worker(appContext, params) {

    override fun doWork(): Result {
        val appWidgetManager = AppWidgetManager.getInstance(applicationContext)
        val appWidgetIds = appWidgetManager.getAppWidgetIds(
            ComponentName(applicationContext, DeyeWidgetProvider::class.java)
        )
        if (appWidgetIds.isNotEmpty()) {
            WidgetUpdateWorker.enqueueLoop(applicationContext, ExistingWorkPolicy.KEEP)
        }
        return Result.success()
    }
}
