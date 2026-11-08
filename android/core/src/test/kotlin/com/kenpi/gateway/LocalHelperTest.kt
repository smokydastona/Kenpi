package com.kenpi.gateway

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalHelperTest {
    @Test fun unpairedPointsToPairing() =
        assertTrue(LocalHelper.explain(false, LiveState.Idle).contains("not paired"))

    @Test fun revokedAndDeniedAreDistinct() {
        assertTrue(LocalHelper.explain(true, LiveState.Revoked).contains("revoked"))
        assertTrue(LocalHelper.explain(true, LiveState.RemoteDenied(null)).contains("remote"))
    }

    @Test fun assistantNeedsLiveSession() {
        assertFalse(LocalHelper.assistantReachable(LiveState.Idle))
        assertFalse(LocalHelper.assistantReachable(LiveState.Disconnected(1, "x", null)))
    }
}
