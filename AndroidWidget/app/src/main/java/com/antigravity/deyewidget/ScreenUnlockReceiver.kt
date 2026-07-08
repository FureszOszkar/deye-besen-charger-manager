package com.antigravity.deyewidget

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.ExistingWorkPolicy

/**
 * Best-effort képernyő-esemény figyelő. FONTOS korlát: az ACTION_SCREEN_OFF manifest-ben
 * deklarált receivernek egyáltalán nem kézbesíthető (csak futásidőben regisztráltnak), és
 * az ACTION_USER_PRESENT kézbesítése sem garantált az Android 8+ implicit broadcast
 * korlátozásai miatt. Ezért a frissítési lánc már NEM erre a receiverre épül:
 *  - a hurok (WidgetUpdateWorker) maga figyeli a képernyő állapotát (PowerManager.isInteractive),
 *  - az újraélesztést a 15 perces WidgetKeepAliveWorker és a widgetre koppintás garantálja.
 * Ez a receiver csak gyorsítás: azokon az eszközökön, ahol az esemény mégis megérkezik,
 * a feloldás utáni újraindulás azonnali.
 */
class ScreenUnlockReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_USER_PRESENT -> {
                // Képernyő feloldva: ha a hurok él, a KEEP nem nyúl hozzá; ha halott, újraindul
                WidgetUpdateWorker.enqueueLoop(context, ExistingWorkPolicy.KEEP)
            }
            Intent.ACTION_SCREEN_OFF -> {
                // A hurok magától is leáll (isInteractive ellenőrzés a ciklusfeltételben),
                // ez csak gyorsítás, ha a broadcast mégis megérkezne
                WidgetUpdateWorker.cancelLoop(context)
            }
            Intent.ACTION_BOOT_COMPLETED -> {
                // A periodikus keep-alive-ot a WorkManager újraindítás után is megőrzi,
                // itt csak biztosítjuk, hogy tényleg ütemezve legyen
                WidgetUpdateWorker.ensureKeepAlive(context)
            }
        }
    }
}
