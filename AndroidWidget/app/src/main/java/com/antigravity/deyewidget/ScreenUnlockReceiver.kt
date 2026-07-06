package com.antigravity.deyewidget

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager

class ScreenUnlockReceiver : BroadcastReceiver() {

    companion object {
        var isScreenActive: Boolean = false
    }

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action

        if (action == Intent.ACTION_USER_PRESENT) {
            // Képernyő feloldva – egyetlen Worker indul, belső hurokkal frissít
            isScreenActive = true
            val workRequest = OneTimeWorkRequest.Builder(WidgetUpdateWorker::class.java).build()
            WorkManager.getInstance(context.applicationContext)
                .enqueueUniqueWork("DeyeWidgetLoop", ExistingWorkPolicy.REPLACE, workRequest)
        } else if (action == Intent.ACTION_SCREEN_OFF) {
            // Képernyő lezárva – Worker leáll
            isScreenActive = false
            WorkManager.getInstance(context.applicationContext)
                .cancelUniqueWork("DeyeWidgetLoop")
        } else if (action == Intent.ACTION_BOOT_COMPLETED) {
            // Telefon újraindult, képernyő zárva – nem csinálunk semmit
            isScreenActive = false
        }
    }
}
