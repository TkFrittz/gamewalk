package com.gamewalk

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.PowerManager
import android.util.Log

/**
 * The foreground service: everything that has to keep working with the screen
 * off.
 *
 * Five separate mechanisms on modern Android stop a naive app the moment the
 * screen goes dark, and each needs its own countermeasure:
 *
 *  - the process is killed          -> this is a foreground service
 *  - sensor events stop             -> wake-up sensor + partial wake lock
 *  - the Wi-Fi radio sleeps         -> WifiLock(FULL_HIGH_PERF)
 *  - Doze defers network            -> foreground services are exempt, plus
 *                                      the battery-optimisation exemption the
 *                                      UI asks for
 *  - OEM battery managers           -> nothing here can fix those; documented
 */
class StepService : Service() {

    private lateinit var prefs: Prefs
    private lateinit var link: Link
    private lateinit var sensors: SensorSource
    lateinit var config: ConfigClient
        private set

    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    private var worker: HandlerThread? = null
    private var handler: Handler? = null

    @Volatile var steps: Int = 0
        private set
    @Volatile var running: Boolean = false
        private set

    /** Set when Android refuses the foreground service, so the UI can say
     *  why instead of just showing "stopped". */
    @Volatile var startFailure: String? = null
        private set

    val sensorDescription: String get() = sensors.descriptor
    val linkError: String? get() = link.lastError
    val lastReplyAt: Long get() = link.lastReplyAt

    override fun onBind(intent: Intent?): IBinder = LocalBinder()

    inner class LocalBinder : android.os.Binder() {
        val service: StepService get() = this@StepService
    }

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        link = Link { pkt -> config.onPacket(pkt) }
        config = ConfigClient(link)
        sensors = SensorSource(this, ::onStep, ::onSample)

        worker = HandlerThread("gw-net").also { it.start() }
        handler = Handler(worker!!.looper)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopEverything()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
        }

        try {
            startForeground(NOTIF_ID, buildNotification("Starting..."))
        } catch (e: Exception) {
            // On Android 14 a "health" foreground service is refused outright
            // unless ACTIVITY_RECOGNITION has been granted. The UI asks for it
            // first, but a denial here would otherwise crash the process with
            // a stack trace nobody can see on a phone.
            Log.e(Link.TAG, "startForeground refused", e)
            startFailure = "Android refused to start the service. Grant the " +
                    "Physical activity permission, then try again."
            stopSelf()
            return START_NOT_STICKY
        }
        startFailure = null
        handler?.post { startEverything() }
        // START_STICKY so the system brings us back if it kills the process
        // for memory -- the user asked for this to keep running.
        return START_STICKY
    }

    private fun startEverything() {
        if (running) return

        link.host = prefs.host
        link.port = prefs.port
        link.token = prefs.token
        link.start()

        acquireLocks()

        val ok = sensors.start(prefs.mode, prefs.accHz)
        running = true
        steps = 0

        link.send(Proto.HELLO, Build.MODEL.replace(' ', '_'), prefs.mode, BuildInfo.VERSION)
        config.request()
        scheduleHeartbeat()

        notify(if (ok) "Connected to ${prefs.host}" else sensors.descriptor)
        Log.i(Link.TAG, "service started, sensor=${sensors.descriptor}")
    }

    private fun acquireLocks() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "GameWalk::steps").apply {
            setReferenceCounted(false)
            acquire()
        }

        // FULL_HIGH_PERF rather than LOW_LATENCY: the latter only takes effect
        // while the app is in the foreground with the screen on, which is the
        // one situation this app is never in.
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "GameWalk::wifi")
            .apply {
                setReferenceCounted(false)
                acquire()
            }
    }

    private fun releaseLocks() {
        try { wakeLock?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        try { wifiLock?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        wakeLock = null
        wifiLock = null
    }

    private fun stopEverything() {
        if (!running) return
        running = false
        link.send(Proto.STOP)
        sensors.stop()
        handler?.removeCallbacksAndMessages(null)
        link.stop()
        releaseLocks()
        Log.i(Link.TAG, "service stopped")
    }

    // --- sensor callbacks (arrive on the sensor thread) ---------------------

    private fun onStep() {
        steps++
        link.send(Proto.STEP, System.currentTimeMillis() % 100_000_000)
    }

    private fun onSample(x: Float, y: Float, z: Float) {
        link.send(
            Proto.ACC, System.currentTimeMillis() % 100_000_000,
            fmt(x), fmt(y), fmt(z)
        )
    }

    /** Three decimals is well under sensor noise and keeps datagrams small. */
    private fun fmt(v: Float): String = String.format("%.3f", v)

    // --- heartbeat ----------------------------------------------------------

    private fun scheduleHeartbeat() {
        val h = handler ?: return
        h.postDelayed(object : Runnable {
            override fun run() {
                if (!running) return
                // Sent whether or not we are moving. Without it, silence is
                // ambiguous -- the PC cannot tell "standing still" from "phone
                // fell off Wi-Fi" -- and its dead-man switch would be guessing.
                link.send(Proto.HB)
                h.postDelayed(this, HEARTBEAT_MS)
            }
        }, HEARTBEAT_MS)
    }

    // --- notification -------------------------------------------------------

    private fun buildNotification(text: String): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "GameWalk", NotificationManager.IMPORTANCE_LOW)
                    .apply { description = "Shown while steps are being sent" }
            )
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, StepService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("GameWalk")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_directions)
            .setContentIntent(open)
            .addAction(
                Notification.Action.Builder(
                    Icon.createWithResource(
                        this, android.R.drawable.ic_menu_close_clear_cancel),
                    "Stop", stop
                ).build()
            )
            .setOngoing(true)
            .build()
    }

    fun notify(text: String) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIF_ID, buildNotification(text))
    }

    override fun onDestroy() {
        stopEverything()
        worker?.quitSafely()
        super.onDestroy()
    }

    companion object {
        const val CHANNEL = "gamewalk"
        const val NOTIF_ID = 1
        const val ACTION_STOP = "com.gamewalk.STOP"
        const val HEARTBEAT_MS = 250L
    }
}

object BuildInfo {
    const val VERSION = "0.1.0"
}
