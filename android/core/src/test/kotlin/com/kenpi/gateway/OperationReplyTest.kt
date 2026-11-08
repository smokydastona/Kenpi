package com.kenpi.gateway

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class OperationReplyTest {
    private fun parse(text: String) = OperationReply.parse(Json.parseToJsonElement(text).jsonObject)

    @Test fun confirmationCarriesToken() {
        val r = parse("""{"status":"confirmation_required","confirm_token":"t","expires_in":60}""")
        assertEquals(OperationReply.ConfirmationRequired("t", 60), r)
    }

    @Test fun failedOperationIsNotReportedAsSuccess() {
        val r = parse("""{"status":"done","result":{"ok":false,"detail":"denied"}}""") as OperationReply.Done
        assertFalse(r.ok)
    }

    @Test fun missingOkIsNotSuccess() {
        assertFalse((parse("""{"status":"done","result":{"units":[]}}""") as OperationReply.Done).ok)
        assertTrue((parse("""{"status":"done","result":{"ok":true}}""") as OperationReply.Done).ok)
    }

    @Test fun unknownStatusIsRejected() {
        try {
            parse("""{"status":"maybe"}""")
            fail()
        } catch (e: GatewayException) {
            assertEquals("bad_reply", e.code)
        }
    }
}
