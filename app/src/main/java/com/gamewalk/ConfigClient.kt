package com.gamewalk

import android.os.Handler
import android.os.Looper
import org.json.JSONArray
import org.json.JSONObject

/**
 * Reads and writes the PC's config over the same UDP socket the steps use.
 *
 * Sensor traffic is fire-and-forget because a lost step self-heals. A lost
 * config write does not, so control messages are retried and made idempotent
 * by the version number.
 */
class ConfigClient(private val link: Link) {

    @Volatile var config: RemoteConfig? = null
        private set
    @Volatile var lastError: String? = null

    /** Live telemetry, for the tuning screen. */
    @Volatile var spm: Float = 0f
        private set
    @Volatile var tier: String = "-"
        private set
    @Volatile var signal: Float = 0f
        private set
    @Volatile var threshold: Float = 0f
        private set
    @Volatile var armed: Boolean = false
        private set

    /** name, value -- a setting the PC is asking the phone to change. */
    var onCommand: ((String, String) -> Unit)? = null
    var onConfig: ((RemoteConfig) -> Unit)? = null
    var onStat: (() -> Unit)? = null
    var onError: ((String) -> Unit)? = null

    private val main = Handler(Looper.getMainLooper())
    private var pendingRetries = 0

    fun request() {
        pendingRetries = RETRIES
        sendRequest()
    }

    private fun sendRequest() {
        link.send(Proto.CFG_GET)
        main.postDelayed({
            if (config == null && pendingRetries-- > 0) sendRequest()
        }, RETRY_MS)
    }

    /** Subscribe to STAT frames. Called when the tuning screen opens, and
     *  cancelled when it closes -- there is no reason to stream statistics at
     *  a phone that is in a pocket with its screen off. */
    fun subscribe(hz: Int = 5) = link.send(Proto.SUB, hz)

    fun unsubscribe() = link.send(Proto.UNSUB)

    /**
     * Change one setting.
     *
     * Lists replace wholesale on the PC (a partial list is ambiguous: is index
     * 1 an edit or an insert?), so editing one tier means sending the entire
     * tier list back with that element changed -- and the changed element must
     * itself be complete, or the PC rejects it. [patchTier] handles that;
     * this handles the scalar case.
     */
    fun set(path: String, value: Any) {
        val cfg = config ?: return
        val patch = nest(path.split("."), value)
        send(if (cfg.isProfileScoped(path)) wrapInProfile(cfg, patch) else patch)
    }

    /** Replace the whole tier list, having edited one field of one tier. */
    fun patchTier(index: Int, field: String, value: Any) {
        val cfg = config ?: return
        val tiers = cfg.profile().optJSONArray("tiers") ?: return
        val out = JSONArray()
        for (i in 0 until tiers.length()) {
            val t = tiers.getJSONObject(i)
            // Copy every field, always. Sending only the changed one would
            // have the PC fill the rest from defaults -- silently renaming the
            // tier and zeroing its thresholds while reporting success.
            val copy = JSONObject()
            copy.put("name", t.optString("name"))
            copy.put("keys", t.optJSONArray("keys") ?: JSONArray())
            copy.put("enter_spm", t.optDouble("enter_spm", 0.0))
            copy.put("exit_spm", t.optDouble("exit_spm", 0.0))
            if (i == index) copy.put(field, value)
            out.put(copy)
        }
        send(wrapInProfile(cfg, JSONObject().put("tiers", out)))
    }

    fun addTier() {
        val cfg = config ?: return
        val tiers = cfg.profile().optJSONArray("tiers") ?: return
        val last = tiers.optJSONObject(tiers.length() - 1)
        val topEnter = last?.optDouble("enter_spm", 0.0) ?: 0.0

        val out = JSONArray()
        for (i in 0 until tiers.length()) out.put(tiers.getJSONObject(i))
        out.put(
            JSONObject()
                .put("name", "tier${tiers.length()}")
                .put("keys", JSONArray().put("shift").put("w"))
                // Above the current top tier, with a hysteresis gap, so the
                // new tier validates on arrival instead of being rejected.
                .put("enter_spm", topEnter + 40)
                .put("exit_spm", topEnter + 25)
        )
        send(wrapInProfile(cfg, JSONObject().put("tiers", out)))
    }

    fun removeTier(index: Int) {
        val cfg = config ?: return
        val tiers = cfg.profile().optJSONArray("tiers") ?: return
        if (tiers.length() <= 1) return
        val out = JSONArray()
        for (i in 0 until tiers.length()) if (i != index) out.put(tiers.getJSONObject(i))
        send(wrapInProfile(cfg, JSONObject().put("tiers", out)))
    }

    private fun wrapInProfile(cfg: RemoteConfig, inner: JSONObject): JSONObject =
        JSONObject().put(
            "profiles",
            JSONObject().put(cfg.activeProfile, inner)
        )

    private fun nest(parts: List<String>, value: Any): JSONObject {
        var node = JSONObject().put(parts.last(), value)
        for (i in parts.size - 2 downTo 0) node = JSONObject().put(parts[i], node)
        return node
    }

    private fun send(patch: JSONObject) {
        link.send(Proto.CFG_SET, patch.toString())
    }

    // --- inbound ------------------------------------------------------------

    fun onPacket(pkt: Proto.Packet) {
        when (pkt.verb) {
            Proto.CFG_FULL -> {
                val parsed = try {
                    RemoteConfig(pkt.arg(0).toIntOrNull() ?: 0, JSONObject(pkt.arg(1)))
                } catch (e: Exception) {
                    lastError = "bad config from helper: ${e.message}"
                    main.post { onError?.invoke(lastError!!) }
                    return
                }
                config = parsed
                lastError = null
                main.post { onConfig?.invoke(parsed) }
            }

            Proto.CFG_OK -> {
                lastError = null
                // Re-fetch so the UI shows exactly what the PC stored, rather
                // than what we hoped it stored.
                link.send(Proto.CFG_GET)
            }

            Proto.CFG_ERR -> {
                lastError = pkt.arg(0).replace('_', ' ')
                main.post {
                    onError?.invoke(lastError!!)
                    // The PC kept its old config, so re-sync to undo whatever
                    // the UI optimistically showed.
                    link.send(Proto.CFG_GET)
                }
            }

            Proto.CMD -> {
                val name = pkt.arg(0)
                val value = pkt.arg(1)
                main.post { onCommand?.invoke(name, value) }
            }

            Proto.STAT -> {
                spm = pkt.farg(0)
                tier = pkt.arg(1, "-")
                signal = pkt.farg(2)
                threshold = pkt.farg(3)
                armed = pkt.arg(4) == "1"
                main.post { onStat?.invoke() }
            }
        }
    }

    companion object {
        const val RETRIES = 4
        const val RETRY_MS = 600L
    }
}
