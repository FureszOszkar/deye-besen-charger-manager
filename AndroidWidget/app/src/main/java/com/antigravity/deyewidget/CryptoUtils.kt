package com.antigravity.deyewidget

import android.util.Base64
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

object CryptoUtils {

    fun generateNonce(): String {
        val bytes = ByteArray(16)
        SecureRandom().nextBytes(bytes)
        return bytes.joinToString("") { "%02x".format(it) }
    }

    fun deriveSessionKey(password: String, nonceHex: String, iterations: Int): ByteArray {
        val factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
        val spec = PBEKeySpec(password.toCharArray(), nonceHex.toByteArray(Charsets.UTF_8), iterations, 256)
        return factory.generateSecret(spec).encoded
    }

    fun generateAuthProof(sessionKey: ByteArray): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(sessionKey, "HmacSHA256"))
        val authProof = mac.doFinal("AUTH_PROOF".toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(authProof, Base64.NO_WRAP)
    }

    fun decryptPayload(sessionKey: ByteArray, ivB64: String, dataB64: String, macB64: String): String {
        val iv = Base64.decode(ivB64, Base64.NO_WRAP)
        val data = Base64.decode(dataB64, Base64.NO_WRAP)
        val receivedMac = Base64.decode(macB64, Base64.NO_WRAP)

        // Verify MAC
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(sessionKey, "HmacSHA256"))
        mac.update(iv)
        val expectedMac = mac.doFinal(data)
        
        if (!MessageDigest.isEqual(expectedMac, receivedMac)) {
            throw SecurityException("MAC ellenorzes sikertelen!")
        }

        // Decrypt
        val cipher = Cipher.getInstance("AES/CBC/PKCS5Padding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(sessionKey, "AES"), IvParameterSpec(iv))
        val plaintext = cipher.doFinal(data)
        return String(plaintext, Charsets.UTF_8)
    }
}
