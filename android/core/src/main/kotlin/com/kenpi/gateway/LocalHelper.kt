package com.kenpi.gateway

/**
 * Offline connection troubleshooting that runs on the phone itself, used when the gateway (and
 * therefore the Kenpi assistant) cannot be reached. It only explains what the phone can observe.
 */
object LocalHelper {
    fun explain(paired: Boolean, state: LiveState): String = when {
        !paired -> "This phone is not paired. On the Pi run kenpi-devices pair --scopes status.read,vehicle.read,assistant.use, then scan the QR code."
        state is LiveState.Live -> "Connected to Kenpi."
        state is LiveState.Connecting -> "Still connecting. Check the phone is on the same Wi-Fi as the Pi, or that WireGuard is on."
        state is LiveState.Disconnected -> "Cannot reach Kenpi (${state.reason}). Check the Pi is powered, the gateway service is running, and the phone is on the same network."
        state is LiveState.RemoteDenied -> "Kenpi refused a remote connection. Join the Pi's local network, or have the administrator allow remote access for this device."
        state is LiveState.Revoked -> "This device was revoked on the Pi. Pair again."
        else -> "Not connected yet. Open the app while near the Pi."
    }

    /** Whether the gateway-side assistant can be asked: only when a live session exists. */
    fun assistantReachable(state: LiveState) = state is LiveState.Live
}
