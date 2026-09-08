package com.gamewalk

import android.Manifest
import android.app.AlertDialog
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var discovery: Discovery
    private lateinit var root: LinearLayout

    private var service: StepService? = null
    private val ui = Handler(Looper.getMainLooper())

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = (binder as? StepService.LocalBinder)?.service
            service?.config?.let { c ->
                c.onConfig = { render() }
                c.onStat = { renderStatusOnly() }
                c.onError = { msg ->
                    Toast.makeText(this@MainActivity, msg, Toast.LENGTH_LONG).show()
                }
            }
            render()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            render()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        discovery = Discovery(this)

        root = column(14)
        val scroll = ScrollView(this).apply {
            setBackgroundColor(BG)
            addView(root, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        }
        setContentView(scroll)

        requestPermissions()
        render()
    }

    override fun onStart() {
        super.onStart()
        bindService(Intent(this, StepService::class.java), connection, BIND_AUTO_CREATE)
        ui.post(refresh)
    }

    override fun onStop() {
        super.onStop()
        // Stop telemetry when the screen is not being looked at. The whole
        // point of this app is a phone in a pocket; streaming statistics to it
        // then would be spending battery on nothing.
        service?.config?.unsubscribe()
        try { unbindService(connection) } catch (_: Exception) {}
        ui.removeCallbacks(refresh)
    }

    /** Repaint the live numbers; the PC pushes STAT but the service's own
     *  counters (steps, sensor state) are polled. */
    private val refresh = object : Runnable {
        override fun run() {
            renderStatusOnly()
            ui.postDelayed(this, 500)
        }
    }

    // --- permissions --------------------------------------------------------

    private fun requestPermissions() {
        // All three fail identically from the outside -- steps simply stop
        // arriving -- so they are asked for up front rather than discovered
        // later as a mystery.
        val wanted = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            wanted.add(Manifest.permission.ACTIVITY_RECOGNITION)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            wanted.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        val missing = wanted.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), 1)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        render()
    }

    private fun hasPermission(p: String) =
        ContextCompat.checkSelfPermission(this, p) == PackageManager.PERMISSION_GRANTED

    private fun batteryExempt(): Boolean {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isIgnoringBatteryOptimizations(packageName)
    }

    private fun askBatteryExemption() {
        try {
            startActivity(
                Intent(
                    Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName")
                )
            )
        } catch (e: Exception) {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }

    // --- service control ----------------------------------------------------

    private fun startWalking() {
        if (!prefs.paired) {
            toast("Connect to your PC first")
            return
        }
        val intent = Intent(this, StepService::class.java)
        ContextCompat.startForegroundService(this, intent)
        bindService(intent, connection, BIND_AUTO_CREATE)
        ui.postDelayed({ render() }, 400)
    }

    private fun stopWalking() {
        startService(
            Intent(this, StepService::class.java).setAction(StepService.ACTION_STOP)
        )
        service = null
        ui.postDelayed({ render() }, 400)
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    // --- rendering ----------------------------------------------------------

    private var statusCard: LinearLayout? = null

    private fun render() {
        root.removeAllViews()
        root.addView(label("GameWalk", 26f, FG, bold = true))
        root.addView(label("Walk in place. Your PC holds W.", 13f, DIM))
        root.addView(spacer(16))

        if (!prefs.paired) {
            root.addView(setupCard())
            root.addView(spacer(12))
            root.addView(permissionsCard())
            return
        }

        root.addView(controlCard())
        root.addView(spacer(12))
        val status = statusCardView()
        statusCard = status
        root.addView(status)
        root.addView(spacer(12))

        if (!permissionsOk()) {
            root.addView(permissionsCard())
            root.addView(spacer(12))
        }

        root.addView(modeCard())
        root.addView(spacer(12))

        val cfg = service?.config?.config
        if (cfg != null) {
            root.addView(tiersCard(cfg))
            root.addView(spacer(12))
            for (group in settingGroups(cfg)) {
                root.addView(group)
                root.addView(spacer(12))
            }
        } else if (service?.running == true) {
            root.addView(card("Settings").apply {
                addView(label("Waiting for the helper to send its settings...", 13f, DIM))
            })
            root.addView(spacer(12))
        }

        root.addView(connectionCard())
        root.addView(spacer(24))
    }

    private fun permissionsOk(): Boolean {
        val activity = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
                hasPermission(Manifest.permission.ACTIVITY_RECOGNITION)
        val notif = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                hasPermission(Manifest.permission.POST_NOTIFICATIONS)
        return activity && notif && batteryExempt()
    }

    // --- cards --------------------------------------------------------------

    private fun setupCard(): View = card("Connect to your PC").apply {
        addView(label("Run run.bat on your PC, then tap Find.", 13f, DIM))
        addView(spacer(10))
        addView(button("Find my PC", primary = true) { scan() })
        addView(spacer(8))
        addView(button("Enter address manually") { manualEntryDialog() })
    }

    private fun permissionsCard(): View = card("Permissions").apply {
        addView(
            label(
                "Each of these fails the same way if missing: steps just stop arriving.",
                12f, DIM
            )
        )
        addView(spacer(10))

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
            !hasPermission(Manifest.permission.ACTIVITY_RECOGNITION)
        ) {
            addView(label("Physical activity - the step sensor returns nothing without it",
                13f, BAD))
            addView(button("Grant") { requestPermissions() })
            addView(spacer(8))
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            !hasPermission(Manifest.permission.POST_NOTIFICATIONS)
        ) {
            addView(label("Notifications - Android may kill the service without one",
                13f, BAD))
            addView(button("Grant") { requestPermissions() })
            addView(spacer(8))
        }
        if (!batteryExempt()) {
            addView(label("Unrestricted battery - or the system throttles it when idle",
                13f, BAD))
            addView(button("Allow") { askBatteryExemption() })
        }
        if (permissionsOk()) addView(label("All set.", 13f, GOOD))
    }

    private fun controlCard(): View = card().apply {
        val running = service?.running == true
        addView(
            button(if (running) "Stop" else "Start walking", primary = !running) {
                if (running) stopWalking() else startWalking()
            }.apply {
                layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, dp(56))
            }
        )
        addView(spacer(8))
        addView(
            label(
                if (running)
                    "Pocket it. The screen can go off."
                else "Nothing is being sent.",
                13f, if (running) GOOD else DIM
            )
        )
    }

    private fun statusCardView(): LinearLayout = card("Status").apply {
        renderStatusInto(this)
    }

    private fun renderStatusOnly() {
        statusCard?.let {
            it.removeAllViews()
            it.addView(label("STATUS", 11f, DIM, bold = true).apply { letterSpacing = 0.12f })
            it.addView(spacer(8))
            renderStatusInto(it)
        }
    }

    private fun renderStatusInto(v: LinearLayout) {
        val svc = service
        val cfgClient = svc?.config

        if (svc == null || !svc.running) {
            val why = svc?.startFailure
            if (why != null) v.addView(label(why, 14f, BAD))
            else v.addView(label("Stopped", 15f, DIM))
            return
        }

        val replyAge = System.currentTimeMillis() - svc.lastReplyAt
        val connected = svc.lastReplyAt > 0 && replyAge < 5000
        v.addView(
            label(
                if (connected) "Connected to ${prefs.host}"
                else "No reply from ${prefs.host} - is run.bat running?",
                15f, if (connected) GOOD else BAD
            )
        )
        v.addView(spacer(6))
        v.addView(label("Sensor: ${svc.sensorDescription}", 12f, DIM))
        v.addView(label("Steps sent: ${svc.steps}", 12f, DIM))
        svc.linkError?.let { v.addView(label(it, 12f, BAD)) }

        if (cfgClient != null && cfgClient.config != null) {
            v.addView(spacer(10))
            val r = row()
            r.addView(
                label("%.0f".format(cfgClient.spm), 30f, ACCENT, bold = true)
            )
            r.addView(label("  spm", 13f, DIM))
            r.addView(
                label("   ${cfgClient.tier}", 18f, FG, bold = true)
            )
            v.addView(r)
            if (prefs.mode == "B") {
                v.addView(
                    label(
                        "signal %.2f / threshold %.2f".format(
                            cfgClient.signal, cfgClient.threshold
                        ), 12f, DIM
                    )
                )
            }
            // Only subscribe while this screen is actually visible.
            cfgClient.subscribe(5)
        }
    }

    private fun modeCard(): View = card("Sensor mode").apply {
        val mode = prefs.mode
        addView(
            label(
                if (mode == "A")
                    "Hardware step detector. Best battery and latency."
                else "Raw accelerometer, the PC finds the steps. Use this if " +
                        "walking in place isn't detected.",
                12f, DIM
            )
        )
        addView(spacer(10))
        val r = row()
        r.addView(button("A - step detector", primary = mode == "A") {
            prefs.mode = "A"; restartIfRunning()
        }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        r.addView(button("B - accelerometer", primary = mode == "B") {
            prefs.mode = "B"; restartIfRunning()
        }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        addView(r)
    }

    private fun restartIfRunning() {
        if (service?.running == true) {
            stopWalking()
            ui.postDelayed({ startWalking() }, 700)
        } else render()
    }

    private fun tiersCard(cfg: RemoteConfig): View = card("Speed tiers").apply {
        addView(
            label(
                "Keys held at each pace. Drop-below must stay under enter-above, " +
                        "or the tier flickers when your cadence sits on the line.",
                12f, DIM
            )
        )
        addView(spacer(10))

        val tiers = cfg.profile().optJSONArray("tiers") ?: JSONArray()
        val client = service?.config ?: return@apply

        for (i in 0 until tiers.length()) {
            val t = tiers.getJSONObject(i)
            addView(label(t.optString("name", "tier$i"), 16f, FG, bold = true))
            addView(spacer(4))

            val keys = t.optJSONArray("keys") ?: JSONArray()
            val keyText = (0 until keys.length()).joinToString(" + ") { keys.getString(it) }
            addView(button("Keys:  $keyText") { keyPicker(cfg, i, keys) })

            if (i > 0) {
                addView(spacer(6))
                addView(
                    slider(
                        "Enter above", null,
                        t.optDouble("enter_spm", 0.0), 0.0, 260.0, 5.0,
                        { "%.0f spm".format(it) }
                    ) { client.patchTier(i, "enter_spm", it) }
                )
                addView(
                    slider(
                        "Drop below", null,
                        t.optDouble("exit_spm", 0.0), 0.0, 260.0, 5.0,
                        { "%.0f spm".format(it) }
                    ) { client.patchTier(i, "exit_spm", it) }
                )
                addView(button("Remove ${t.optString("name")}") { client.removeTier(i) })
            }
            addView(spacer(12))
        }
        addView(button("Add a faster tier") { client.addTier() })
    }

    private fun keyPicker(cfg: RemoteConfig, tierIndex: Int, current: JSONArray) {
        val all = cfg.validKeys.toTypedArray()
        val chosen = (0 until current.length()).map { current.getString(it) }.toMutableSet()
        val checked = BooleanArray(all.size) { all[it] in chosen }

        AlertDialog.Builder(this)
            .setTitle("Keys to hold")
            .setMultiChoiceItems(all, checked) { _, which, isChecked ->
                if (isChecked) chosen.add(all[which]) else chosen.remove(all[which])
            }
            .setPositiveButton("Save") { _, _ ->
                if (chosen.isEmpty()) {
                    toast("Pick at least one key")
                } else {
                    val arr = JSONArray()
                    chosen.forEach { arr.put(it) }
                    service?.config?.patchTier(tierIndex, "keys", arr)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /**
     * Settings rendered from the descriptors the PC sends, grouped as it says.
     * Nothing here knows the names of any particular setting, which is what
     * lets the PC gain a config field without the app being rebuilt.
     */
    private fun settingGroups(cfg: RemoteConfig): List<View> {
        val client = service?.config ?: return emptyList()
        val byGroup = LinkedHashMap<String, MutableList<String>>()

        val keys = cfg.meta.keys()
        while (keys.hasNext()) {
            val path = keys.next()
            // Tier fields have their own card above; skip the templates.
            if (path.startsWith("tiers[]")) continue
            val m = cfg.meta.optJSONObject(path) ?: continue
            if (path.startsWith("detector.") && prefs.mode != "B") continue
            byGroup.getOrPut(m.optString("group", "Other")) { mutableListOf() }.add(path)
        }

        return byGroup.map { (group, paths) ->
            card(group).apply {
                for (path in paths.sorted()) {
                    val m = cfg.meta.optJSONObject(path) ?: continue
                    val view = control(cfg, client, path, m) ?: continue
                    addView(view)
                    addView(spacer(10))
                }
            }
        }
    }

    private fun control(
        cfg: RemoteConfig, client: ConfigClient, path: String, m: JSONObject,
    ): View? {
        val title = m.optString("label", path)
        val help = m.optString("help").takeIf { it.isNotEmpty() }
        val value = cfg.value(path) ?: return null

        return when (m.optString("type")) {
            "int" -> slider(
                title, help, (value as Number).toDouble(),
                m.optDouble("min", 0.0), m.optDouble("max", 100.0),
                m.optDouble("step", 1.0), { "%.0f".format(it) }
            ) { client.set(path, it.toInt()) }

            "float" -> slider(
                title, help, (value as Number).toDouble(),
                m.optDouble("min", 0.0), m.optDouble("max", 10.0),
                m.optDouble("step", 0.1), { "%.2f".format(it) }
            ) { client.set(path, it) }

            "enum" -> column().apply {
                addView(label(title, 15f))
                if (!help.isNullOrEmpty()) addView(label(help, 12f, DIM))
                val values = m.optJSONArray("values") ?: JSONArray()
                val r = row()
                for (i in 0 until values.length()) {
                    val v = values.getString(i)
                    r.addView(
                        button(v, primary = v == value.toString()) { client.set(path, v) },
                        LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f)
                    )
                }
                addView(r)
            }

            "key" -> column().apply {
                addView(label(title, 15f))
                if (!help.isNullOrEmpty()) addView(label(help, 12f, DIM))
                addView(button(value.toString()) {
                    val all = cfg.validKeys.toTypedArray()
                    AlertDialog.Builder(this@MainActivity)
                        .setTitle(title)
                        .setItems(all) { _, which -> client.set(path, all[which]) }
                        .show()
                })
            }

            else -> null
        }
    }

    private fun connectionCard(): View = card("Connection").apply {
        addView(label("${prefs.host}:${prefs.port}", 14f, FG))
        addView(label("token ${prefs.token.take(6)}...", 12f, DIM))
        addView(spacer(10))
        addView(button("Reconnect / find again") { scan() })
        addView(spacer(6))
        addView(button("Forget this PC") {
            prefs.clearPairing()
            stopWalking()
            render()
        })
    }

    // --- discovery and pairing ---------------------------------------------

    private fun scan() {
        val dialog = AlertDialog.Builder(this)
            .setTitle("Looking for your PC")
            .setMessage("Make sure run.bat is running and both are on the same Wi-Fi.")
            .setCancelable(true)
            .show()

        discovery.scan(prefs.discoveryPort) { found ->
            dialog.dismiss()
            when {
                found.isEmpty() -> AlertDialog.Builder(this)
                    .setTitle("Nothing answered")
                    .setMessage(
                        "Check that run.bat is running, that the phone is on the " +
                                "same Wi-Fi (not mobile data), and that the router " +
                                "isn't isolating wireless clients from each other."
                    )
                    .setPositiveButton("Try again") { _, _ -> scan() }
                    .setNegativeButton("Enter manually") { _, _ -> manualEntryDialog() }
                    .show()

                found.size == 1 -> connectTo(found[0])

                else -> {
                    val names = found.map { "${it.host}  (${it.ip})" }.toTypedArray()
                    AlertDialog.Builder(this)
                        .setTitle("Which PC?")
                        .setItems(names) { _, which -> connectTo(found[which]) }
                        .show()
                }
            }
        }
    }

    private fun connectTo(f: Discovery.Found) {
        prefs.host = f.ip
        prefs.port = f.port
        if (f.open) {
            prefs.token = ""
            toast("Connected to ${f.host}")
            render()
            return
        }
        pinDialog(f)
    }

    private fun pinDialog(f: Discovery.Found) {
        val input = field("6-digit PIN", "", numeric = true)
        AlertDialog.Builder(this)
            .setTitle("Pair with ${f.host}")
            .setMessage("Type the PIN shown in the run.bat window. Only needed once.")
            .setView(input)
            .setPositiveButton("Pair") { _, _ ->
                discovery.pair(f.ip, prefs.discoveryPort, input.text.toString()) { token ->
                    if (token == null) {
                        AlertDialog.Builder(this)
                            .setTitle("Pairing failed")
                            .setMessage(
                                "Wrong PIN, or the pairing window expired. Restart " +
                                        "run.bat for a fresh PIN and try again."
                            )
                            .setPositiveButton("Retry") { _, _ -> pinDialog(f) }
                            .setNegativeButton("Cancel", null)
                            .show()
                    } else {
                        prefs.token = token
                        toast("Paired with ${f.host}")
                        render()
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun manualEntryDialog() {
        val host: EditText = field("192.168.1.50", prefs.host)
        val port: EditText = field("5599", prefs.port.toString(), numeric = true)
        val token: EditText = field("token from config.json", prefs.token)
        val box = column(16).apply {
            addView(label("PC address", 12f, DIM)); addView(host)
            addView(spacer(8))
            addView(label("Port", 12f, DIM)); addView(port)
            addView(spacer(8))
            addView(label("Token", 12f, DIM)); addView(token)
        }
        AlertDialog.Builder(this)
            .setTitle("Enter details")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                prefs.host = host.text.toString().trim()
                prefs.port = port.text.toString().toIntOrNull() ?: 5599
                prefs.token = token.text.toString().trim()
                render()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
