package com.kenpi.gateway

import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** The gateway answered with an error, or with something that is not the documented protocol. */
class GatewayException(val status: Int, val code: String, message: String = code) : IOException(message) {
    val isAuthFailure get() = status == 401
    val isRemoteDenied get() = status == 403
    val isRateLimited get() = status == 429
}

data class Tokens(val deviceId: String, val accessToken: String, val expiresInSeconds: Int, val refreshToken: String)

sealed interface CollectResult {
    data class Waiting(val state: String) : CollectResult
    data class Issued(val tokens: Tokens) : CollectResult
    data class Terminal(val state: String) : CollectResult
}

data class WireGuardParameters(
    val serverPublicKey: String,
    val listenPort: Int,
    val address: String,
    val gatewayAddress: String,
    val allowedIps: List<String>,
)

data class Snapshot(val streamId: String, val sequence: Long, val state: JsonObject)

/** REST client for the authenticated gateway. All bodies are size-bounded and strictly shaped. */
sealed interface OperationReply {
    data class ConfirmationRequired(val token: String, val expiresInSeconds: Int) : OperationReply
    data class Done(val ok: Boolean, val result: JsonObject) : OperationReply

    companion object {
        fun parse(body: JsonObject): OperationReply = when ((body["status"] as? JsonPrimitive)?.content) {
            "confirmation_required" -> ConfirmationRequired(
                (body["confirm_token"] as? JsonPrimitive)?.content ?: throw GatewayException(0, "bad_reply"),
                (body["expires_in"] as? JsonPrimitive)?.content?.toIntOrNull() ?: 0,
            )
            "done" -> {
                val result = body["result"] as? JsonObject ?: throw GatewayException(0, "bad_reply")
                // A missing "ok" is not success: report only what the Pi said.
                Done((result["ok"] as? JsonPrimitive)?.content == "true", result)
            }
            else -> throw GatewayException(0, "bad_reply")
        }
    }
}

class GatewayApi(
    private val host: String,
    private val port: Int,
    private val http: OkHttpClient,
) {
    private val json = "application/json".toMediaType()

    private fun url(path: String) = "https://${bracket(host)}:$port$path"

    private suspend fun call(request: Request): JsonObject = withContext(Dispatchers.IO) {
        http.newCall(request).execute().use { response ->
            val source = response.body?.source() ?: throw GatewayException(response.code, "empty_response")
            source.request(MAX_BODY + 1)
            if (source.buffer.size > MAX_BODY) throw GatewayException(response.code, "response_too_large")
            val text = source.buffer.readUtf8()
            val body = try {
                Json.parseToJsonElement(text) as? JsonObject
            } catch (e: Exception) {
                null
            } ?: throw GatewayException(response.code, "malformed_response")
            if (!response.isSuccessful) {
                throw GatewayException(response.code, (body["error"] as? JsonPrimitive)?.contentOrNull ?: "error")
            }
            body
        }
    }

    private suspend fun post(path: String, body: JsonObject) =
        call(Request.Builder().url(url(path)).post(body.toString().toRequestBody(json)).build())

    private suspend fun get(path: String, accessToken: String) =
        call(Request.Builder().url(url(path)).header("Authorization", "Bearer $accessToken").get().build())

    suspend fun enroll(payload: PairingPayload, name: String, key: DeviceKey, wireGuardPublicKey: String? = null) {
        val reply = post(
            "/pair/enroll",
            buildJsonObject {
                put("enrollment_id", payload.enrollmentId)
                put("challenge", payload.challenge)
                put("name", name)
                put("public_key", key.publicKeyBase64)
                put(
                    "signature",
                    key.sign(Proofs.enroll(payload.enrollmentId, payload.challenge, name, key.publicKeyBase64)),
                )
                if (wireGuardPublicKey != null) put("wireguard_key", wireGuardPublicKey)
            },
        )
        if (str(reply, "state") != "submitted") throw GatewayException(200, "unexpected_enroll_state")
    }

    suspend fun collect(enrollmentId: String, key: DeviceKey): CollectResult {
        val reply = post(
            "/pair/status",
            buildJsonObject {
                put("enrollment_id", enrollmentId)
                put("signature", key.sign(Proofs.collect(enrollmentId)))
            },
        )
        val state = str(reply, "state")
        return when {
            reply.containsKey("access_token") -> CollectResult.Issued(tokens(reply))
            state == "submitted" || state == "approved" -> CollectResult.Waiting(state)
            else -> CollectResult.Terminal(state)
        }
    }

    suspend fun refresh(refreshToken: String, key: DeviceKey): Tokens =
        tokens(
            post(
                "/auth/refresh",
                buildJsonObject {
                    put("refresh_token", refreshToken)
                    put("signature", key.sign(Proofs.refresh(refreshToken)))
                },
            ),
        )

    private suspend fun postAuth(path: String, body: JsonObject, accessToken: String) =
        call(Request.Builder().url(url(path)).header("Authorization", "Bearer $accessToken").post(body.toString().toRequestBody(json)).build())

    /** Needs the assistant.use scope; the gateway never lets the phone approve actions. */
    suspend fun assistantAsk(accessToken: String, text: String): JsonObject =
        postAuth("/api/v1/assistant/ask", buildJsonObject { put("text", text) }, accessToken)

    suspend fun assistantStatus(accessToken: String) = get("/api/v1/assistant/status", accessToken)

    suspend fun assistantDiagnostics(accessToken: String) = get("/api/v1/assistant/diagnostics", accessToken)

    suspend fun kenpiLogs(accessToken: String) = get("/api/v1/assistant/logs", accessToken)

    /** Phone-side lines are redacted again by the gateway, but callers should not include secrets. */
    suspend fun uploadLogs(accessToken: String, appVersion: String, connection: String, lines: List<String>) =
        postAuth(
            "/api/v1/assistant/logs",
            buildJsonObject {
                put("app_version", appVersion.take(32))
                put("connection", connection.take(64))
                put("lines", JsonArray(lines.takeLast(200).map { JsonPrimitive(it.take(300)) }))
            },
            accessToken,
        )

    /** Operations this device may run (needs ops.read / ops.manage scopes on the gateway). */
    suspend fun operations(accessToken: String): List<String> =
        (get("/api/v1/ops", accessToken)["operations"] as? JsonArray)
            ?.mapNotNull { ((it as? JsonObject)?.get("id") as? JsonPrimitive)?.content }
            ?: emptyList()

    /** Dangerous operations answer ConfirmationRequired first; resend with the token to execute. */
    suspend fun runOperation(accessToken: String, operation: String, args: JsonObject = JsonObject(emptyMap()), confirmToken: String? = null): OperationReply =
        OperationReply.parse(
            postAuth(
                "/api/v1/ops/run",
                buildJsonObject {
                    put("operation", operation)
                    put("args", args)
                    if (confirmToken != null) put("confirm_token", confirmToken)
                },
                accessToken,
            ),
        )

    suspend fun snapshot(accessToken: String): Snapshot = parseSnapshot(get("/api/v1/snapshot", accessToken))

    suspend fun wireGuard(accessToken: String): WireGuardParameters? {
        val reply = try {
            get("/api/v1/wireguard", accessToken)
        } catch (e: GatewayException) {
            if (e.status == 404) return null
            throw e
        }
        val wg = reply["wireguard"] as? JsonObject ?: throw GatewayException(200, "malformed_response")
        return WireGuardParameters(
            str(wg, "server_public_key"),
            (wg["listen_port"] as? JsonPrimitive)?.intOrNull ?: throw GatewayException(200, "malformed_response"),
            str(wg, "address"),
            str(wg, "gateway_address"),
            (wg["allowed_ips"] as? JsonArray)?.map { (it as? JsonPrimitive)?.contentOrNull ?: throw GatewayException(200, "malformed_response") }
                ?: throw GatewayException(200, "malformed_response"),
        )
    }

    private fun tokens(reply: JsonObject) = Tokens(
        str(reply, "device_id"),
        str(reply, "access_token"),
        (reply["expires_in"] as? JsonPrimitive)?.intOrNull?.takeIf { it in 1..86_400 }
            ?: throw GatewayException(200, "malformed_response"),
        str(reply, "refresh_token"),
    )

    companion object {
        const val MAX_BODY = 1_048_576L

        fun bracket(host: String) = if (host.contains(':')) "[$host]" else host

        fun str(obj: JsonObject, key: String): String =
            (obj[key] as? JsonPrimitive)?.takeIf { it.isString }?.content
                ?: throw GatewayException(200, "malformed_response", "missing $key")

        fun parseSnapshot(message: JsonObject): Snapshot {
            if (str(message, "type") != "snapshot" && message["type"] != null && str(message, "type") != "resync") {
                throw GatewayException(200, "malformed_response")
            }
            return Snapshot(
                str(message, "stream_id"),
                (message["sequence"] as? JsonPrimitive)?.longOrNull?.takeIf { it >= 0 }
                    ?: throw GatewayException(200, "malformed_response"),
                message["state"] as? JsonObject ?: throw GatewayException(200, "malformed_response"),
            )
        }
    }
}
