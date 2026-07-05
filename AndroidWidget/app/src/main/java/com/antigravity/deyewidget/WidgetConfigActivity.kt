package com.antigravity.deyewidget

import android.app.Activity
import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText

class WidgetConfigActivity : Activity() {
    private var appWidgetId = AppWidgetManager.INVALID_APPWIDGET_ID

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        setResult(RESULT_CANCELED)
        setContentView(R.layout.activity_widget_config)

        val intent = intent
        val extras = intent.extras
        if (extras != null) {
            appWidgetId = extras.getInt(
                AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID
            )
        }
        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            finish()
            return
        }

        val etIp = findViewById<EditText>(R.id.et_ip)
        val etPassword = findViewById<EditText>(R.id.et_password)
        val btnSave = findViewById<Button>(R.id.btn_save)
        val sbAlpha = findViewById<android.widget.SeekBar>(R.id.sb_alpha)
        val tvAlphaLabel = findViewById<android.widget.TextView>(R.id.tv_alpha_label)

        val prefs = getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        etIp.setText(prefs.getString("ip", "192.168.1.100"))
        etPassword.setText(prefs.getString("password", ""))
        sbAlpha.progress = prefs.getInt("bg_alpha", 255)
        tvAlphaLabel.text = "Háttér átlátszóság: ${((sbAlpha.progress / 255f) * 100).toInt()}%"

        sbAlpha.setOnSeekBarChangeListener(object : android.widget.SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: android.widget.SeekBar?, progress: Int, fromUser: Boolean) {
                tvAlphaLabel.text = "Háttér átlátszóság: ${((progress / 255f) * 100).toInt()}%"
            }
            override fun onStartTrackingTouch(seekBar: android.widget.SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: android.widget.SeekBar?) {}
        })

        btnSave.setOnClickListener {
            val ip = etIp.text.toString()
            val password = etPassword.text.toString()
            val alpha = sbAlpha.progress
            prefs.edit()
                .putString("ip", ip)
                .putString("password", password)
                .putInt("bg_alpha", alpha)
                .apply()

            val appWidgetManager = AppWidgetManager.getInstance(this)
            DeyeWidgetProvider.updateAppWidget(this, appWidgetManager, appWidgetId)

            val resultValue = Intent()
            resultValue.putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
            setResult(RESULT_OK, resultValue)
            finish()
        }
    }
}
