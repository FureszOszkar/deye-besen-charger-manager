package com.antigravity.deyewidget

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper

class ScreenUnlockReceiver : BroadcastReceiver() {
    private var handler: Handler? = null
    private var runnable: Runnable? = null

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        
        if (action == Intent.ACTION_USER_PRESENT) {
            // Képernyő feloldva - sűrű frissítés indul (pl. 5 másodpercenként, amíg aktív)
            startFrequentPolling(context.applicationContext)
        } else if (action == Intent.ACTION_SCREEN_OFF) {
            // Képernyő lezárva - sűrű frissítés leáll
            stopFrequentPolling()
        }
    }
    
    private fun startFrequentPolling(context: Context) {
        if (handler == null) {
            handler = Handler(Looper.getMainLooper())
        }
        runnable = object : Runnable {
            override fun run() {
                val updateIntent = Intent(context, WidgetUpdateWorker.UpdateReceiver::class.java)
                updateIntent.action = "com.antigravity.deyewidget.ACTION_REFRESH"
                context.sendBroadcast(updateIntent)
                handler?.postDelayed(this, 5000) // 5 másodpercenként frissít aktív képernyőnél
            }
        }
        handler?.post(runnable!!)
    }
    
    private fun stopFrequentPolling() {
        runnable?.let { handler?.removeCallbacks(it) }
    }
}
