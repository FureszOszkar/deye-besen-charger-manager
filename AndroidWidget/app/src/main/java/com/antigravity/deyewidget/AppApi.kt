package com.antigravity.deyewidget

import android.content.Context
import android.util.Base64
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Az app (MainActivity és a részletképernyők) API-kliense. Ugyanazt a protokollt használja, mint a
 * widget: PSK kihívás-válasz bejelentkezés, a válaszok és a kérések AES-256-CBC + HMAC-SHA256
 * titkosítással. A szerver címét és a jelszót ugyanonnan olvassa, ahonnan a widget (DeyePrefs).
 * A widget saját munkamenetét nem érinti (külön süti és kulcs).
 */
object AppApi {
    class ApiException(message: String) : Exception(message)

    private val JSON = "application/json".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    @Volatile private var sessionToken: String? = null
    @Volatile private var sessionKey: ByteArray? = null

    fun reset() {
        sessionToken = null
        sessionKey = null
    }

    private fun baseUrl(ctx: Context): String {
        val prefs = ctx.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val ip = prefs.getString("ip", "")?.trim() ?: ""
        if (ip.isEmpty()) throw ApiException("Nincs beállítva a szerver címe.")
        return "http://$ip:8080"
    }

    /** A teljes állapot (/api/status). */
    @Synchronized
    fun status(ctx: Context): JSONObject = request(ctx, "/api/status", null, false)

    /** Titkosított POST; a szerver JSON-válaszát adja vissza (status/message). */
    @Synchronized
    fun post(ctx: Context, path: String, body: JSONObject): JSONObject = request(ctx, path, body, false)

    private fun request(ctx: Context, path: String, body: JSONObject?, retried: Boolean): JSONObject {
        val base = baseUrl(ctx)
        if (sessionToken == null || sessionKey == null) login(ctx, base)
        val builder = Request.Builder().url(base + path)
        sessionToken?.let { builder.header("Cookie", it) }
        if (body == null) builder.get() else builder.post(encrypt(body).toString().toRequestBody(JSON))
        client.newCall(builder.build()).execute().use { response ->
            if (response.code == 401 && !retried) {
                reset()
                return request(ctx, path, body, true)
            }
            val text = response.body?.string() ?: ""
            if (!response.isSuccessful) throw ApiException("A szerver hibát jelzett (${response.code}).")
            return decode(text)
        }
    }

    private fun decode(text: String): JSONObject {
        val json = JSONObject(text)
        if (!json.optBoolean("enc", false)) return json
        val key = sessionKey ?: throw ApiException("Nincs munkamenet-kulcs.")
        return try {
            JSONObject(CryptoUtils.decryptPayload(key, json.getString("iv"), json.getString("data"), json.getString("mac")))
        } catch (e: SecurityException) {
            reset()
            throw ApiException("A válasz ellenőrzése sikertelen, újra bejelentkezem.")
        }
    }

    /** A webes felülettel azonos titkosítás: AES-256-CBC, HMAC-SHA256(IV + rejtjel). */
    private fun encrypt(body: JSONObject): JSONObject {
        val key = sessionKey ?: return body
        val iv = ByteArray(16).also { SecureRandom().nextBytes(it) }
        val cipher = Cipher.getInstance("AES/CBC/PKCS5Padding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), IvParameterSpec(iv))
        val data = cipher.doFinal(body.toString().toByteArray(Charsets.UTF_8))
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        mac.update(iv)
        val tag = mac.doFinal(data)
        return JSONObject().apply {
            put("iv", Base64.encodeToString(iv, Base64.NO_WRAP))
            put("data", Base64.encodeToString(data, Base64.NO_WRAP))
            put("mac", Base64.encodeToString(tag, Base64.NO_WRAP))
            put("enc", true)
        }
    }

    private fun iterations(base: String): Int = try {
        client.newCall(Request.Builder().url("$base/api/login_info").get().build()).execute().use { r ->
            if (r.isSuccessful) JSONObject(r.body?.string() ?: "").optInt("pbkdf2_iterations", 100000) else 100000
        }
    } catch (e: Exception) {
        100000
    }

    private fun login(ctx: Context, base: String) {
        val prefs = ctx.getSharedPreferences("DeyePrefs", Context.MODE_PRIVATE)
        val password = prefs.getString("password", "") ?: ""
        val nonce = CryptoUtils.generateNonce()
        val key = CryptoUtils.deriveSessionKey(password, nonce, iterations(base))
        val loginJson = JSONObject().apply {
            put("clientNonce", nonce)
            put("authProof", CryptoUtils.generateAuthProof(key))
        }
        val request = Request.Builder().url("$base/api/login")
            .post(loginJson.toString().toRequestBody(JSON)).build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.string() ?: ""
            val setCookie = response.header("Set-Cookie")
            val ok = try { JSONObject(body).optString("status") == "success" } catch (e: Exception) { false }
            if (response.isSuccessful && ok && setCookie != null && setCookie.startsWith("session=")) {
                sessionToken = setCookie.split(";").first()
                sessionKey = key
            } else {
                reset()
                throw ApiException("A bejelentkezés sikertelen (jelszó?).")
            }
        }
    }
}
