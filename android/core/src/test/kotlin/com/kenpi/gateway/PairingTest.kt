package com.kenpi.gateway

import java.util.Base64
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class PairingTest {
    private fun uri(json: String) =
        "kenpi://pair?d=" + Base64.getUrlEncoder().withoutPadding().encodeToString(json.toByteArray())

    private val fp = "a".repeat(64)
    private val eid = "b".repeat(32)
    private fun body(address: String = "10.0.0.5", port: Int = 8443, expires: Long = 2000, v: Int = 1, f: String = fp) =
        """{"v":$v,"address":"$address","port":$port,"fingerprint":"$f","enrollment_id":"$eid","challenge":"${"c".repeat(20)}","expires":$expires}"""

    private fun reason(u: String, now: Double = 1000.0): PairingException.Reason {
        try {
            PairingPayload.parse(u, now)
        } catch (e: PairingException) {
            return e.reason
        }
        fail("accepted $u")
        throw AssertionError()
    }

    @Test fun acceptsValid() {
        val p = PairingPayload.parse(uri(body()), 1000.0)
        assertEquals("10.0.0.5", p.address)
        assertEquals(8443, p.port)
        assertEquals(fp, p.fingerprint)
    }

    @Test fun rejectsExpired() = assertEquals(PairingException.Reason.EXPIRED, reason(uri(body(expires = 999))))

    @Test fun rejectsHostname() = assertEquals(PairingException.Reason.BAD_ADDRESS, reason(uri(body(address = "evil.example.com"))))

    @Test fun rejectsBadPort() = assertEquals(PairingException.Reason.MALFORMED, reason(uri(body(port = 0))))

    @Test fun rejectsVersion() = assertEquals(PairingException.Reason.UNSUPPORTED_VERSION, reason(uri(body(v = 2))))

    @Test fun rejectsShortFingerprint() = assertEquals(PairingException.Reason.MALFORMED, reason(uri(body(f = "abc"))))

    @Test fun rejectsGarbage() {
        assertEquals(PairingException.Reason.MALFORMED, reason("https://pair?d=x"))
        assertEquals(PairingException.Reason.MALFORMED, reason("kenpi://pair?d=!!!"))
        assertEquals(PairingException.Reason.MALFORMED, reason("kenpi://pair?d=" + "A".repeat(3000)))
    }
}
