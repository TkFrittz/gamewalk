package com.gamewalk

import android.util.Log
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.concurrent.atomic.AtomicInteger

/**
 * The SL1 wire protocol, phone side. Mirrors pc/protocol.py.
 *
 * One ASCII line per datagram. Sensor traffic is fire-and-forget: a lost step
 * costs nothing because another arrives in ~500ms, and that self-healing
 * property is why this is UDP rather than a socket with state to get stuck in.
 * Config messages are the exception and are retried by [ConfigClient].
 */
object Proto {
    const val MAGIC = "SL1"

    const val HELLO = "HELLO"
    const val STEP = "STEP"
    const val ACC = "ACC"
    const val HB = "HB"
    const val STOP = "STOP"
    const val CFG_GET = "CFG?"
    const val CFG_SET = "CFG="
    const val SUB = "SUB"
    const val UNSUB = "UNSUB"
    const val PING = "PING"

    const val CFG_FULL = "CFG!"
    const val CFG_OK = "CFGOK"
    const val CFG_ERR = "CFGERR"
    const val STAT = "STAT"
    const val PONG = "PONG"

    /** Verbs whose trailing JSON blob must survive intact, and how many plain
     *  arguments precede it. CFG! carries a version before the blob; treating
     *  the whole tail as one argument would glue them together. */
    private val jsonTail = mapOf(CFG_SET to 0, CFG_FULL to 1)

    fun build(token: String, verb: String, seq: Int, vararg args: Any): ByteArray {
        val sb = StringBuilder(MAGIC).append(' ').append(token).append(' ')
            .append(verb).append(' ').append(seq)
        for (a in args) sb.append(' ').append(a)
        return sb.toString().toByteArray(Charsets.UTF_8)
    }

    class Packet(val token: String, val verb: String, val seq: Int, val args: List<String>) {
        fun arg(i: Int, fallback: String = ""): String = args.getOrElse(i) { fallback }
        fun farg(i: Int, fallback: Float = 0f): Float =
            args.getOrNull(i)?.toFloatOrNull() ?: fallback
    }

    /** Never throws. Junk from something else on the LAN must not kill the loop. */
    fun parse(data: ByteArray, length: Int): Packet? {
        val line = try {
            String(data, 0, length, Charsets.UTF_8).trim()
        } catch (e: Exception) {
            return null
        }
        if (!line.startsWith("$MAGIC ")) return null

        val head = line.split(" ", limit = 5)
        if (head.size < 4) return null
        val seq = head[3].toIntOrNull() ?: return null
        val tail = if (head.size > 4) head[4] else ""

        val args = when {
            tail.isEmpty() -> emptyList()
            jsonTail.containsKey(head[2]) ->
                tail.split(" ", limit = jsonTail.getValue(head[2]) + 1)
            else -> tail.split(" ").filter { it.isNotEmpty() }
        }
        return Packet(head[1], head[2], seq, args)
    }
}

/**
 * One UDP socket shared by the sender and the receive loop.
 *
 * Android forbids network I/O on the main thread, so every call here is made
 * from the service's own thread or the receive thread.
 */
class Link(private val onPacket: (Proto.Packet) -> Unit) {
    private val seq = AtomicInteger(0)
    private var socket: DatagramSocket? = null
    private var receiver: Thread? = null

    @Volatile var host: String = ""
    @Volatile var port: Int = 5599
    @Volatile var token: String = ""
    @Volatile var lastError: String? = null

    /** Set when the helper answers anything at all, so the UI can tell
     *  "sending into the void" from "connected". */
    @Volatile var lastReplyAt: Long = 0

    fun start() {
        if (socket != null) return
        try {
            val s = DatagramSocket()
            s.broadcast = true
            socket = s
            receiver = Thread({ receiveLoop(s) }, "gw-recv").apply {
                isDaemon = true
                start()
            }
            lastError = null
        } catch (e: Exception) {
            lastError = "socket: ${e.message}"
            Log.e(TAG, "socket open failed", e)
        }
    }

    fun stop() {
        receiver = null
        socket?.close()
        socket = null
    }

    private fun receiveLoop(s: DatagramSocket) {
        val buf = ByteArray(65535)
        while (!s.isClosed) {
            try {
                val dp = DatagramPacket(buf, buf.size)
                s.receive(dp)
                lastReplyAt = System.currentTimeMillis()
                Proto.parse(dp.data, dp.length)?.let(onPacket)
            } catch (e: Exception) {
                if (s.isClosed) return
                // A single bad datagram must not end the loop.
                Log.w(TAG, "receive: ${e.message}")
            }
        }
    }

    fun send(verb: String, vararg args: Any) {
        val s = socket ?: return
        if (host.isEmpty()) return
        try {
            val payload = Proto.build(token, verb, seq.incrementAndGet(), *args)
            s.send(DatagramPacket(payload, payload.size, InetAddress.getByName(host), port))
            lastError = null
        } catch (e: Exception) {
            lastError = "send: ${e.message}"
        }
    }

    fun sendTo(address: InetAddress, port: Int, text: String) {
        val s = socket ?: return
        try {
            val b = text.toByteArray(Charsets.UTF_8)
            s.send(DatagramPacket(b, b.size, address, port))
        } catch (e: Exception) {
            lastError = "send: ${e.message}"
        }
    }

    companion object {
        const val TAG = "GameWalk"
    }
}

/** Parsed once from CFG!, then used to render the settings screen. */
class RemoteConfig(val version: Int, val json: JSONObject) {
    val meta: JSONObject = json.optJSONObject("meta") ?: JSONObject()
    val validKeys: List<String> =
        json.optJSONArray("keys")?.let { arr ->
            (0 until arr.length()).map { arr.getString(it) }
        } ?: emptyList()

    val activeProfile: String = json.optString("active_profile", "default")

    fun profile(): JSONObject =
        json.optJSONObject("profiles")?.optJSONObject(activeProfile) ?: JSONObject()

    /** Resolve a dotted path like "hold.multiplier" against the active profile,
     *  falling back to the top level for global settings such as deadman_ms. */
    fun value(path: String): Any? {
        for (root in listOf(profile(), json)) {
            var node: Any? = root
            var ok = true
            for (part in path.split(".")) {
                node = (node as? JSONObject)?.opt(part) ?: run { ok = false; null }
                if (!ok) break
            }
            if (ok && node != null) return node
        }
        return null
    }

    /** Whether [path] lives inside the profile rather than at the top level,
     *  which decides how a patch for it has to be nested. */
    fun isProfileScoped(path: String): Boolean {
        var node: Any? = profile()
        for (part in path.split(".")) {
            node = (node as? JSONObject)?.opt(part) ?: return false
        }
        return true
    }
}
