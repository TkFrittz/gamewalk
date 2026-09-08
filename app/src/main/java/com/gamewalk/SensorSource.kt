package com.gamewalk

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.util.Log

/**
 * Sensor acquisition, in the two shapes described in DESIGN.md.
 *
 * Mode A uses the hardware step detector: one event per footfall, near-zero
 * battery cost. Mode B streams raw acceleration and lets the PC find the steps,
 * for the case where the hardware detector turns out not to fire for walking in
 * *place* -- it is tuned for real gait with forward translation, and that is the
 * biggest unverified assumption in this project.
 */
class SensorSource(
    context: Context,
    private val onStep: () -> Unit,
    private val onSample: (Float, Float, Float) -> Unit,
) : SensorEventListener {

    private val sm = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager

    var mode: String = "A"
        private set
    var descriptor: String = "not started"
        private set
    var usingWakeUpSensor: Boolean = false
        private set

    private var sensor: Sensor? = null

    fun hasStepDetector(): Boolean =
        sm.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR) != null

    fun start(mode: String, accHz: Int, deliverOn: Handler? = null): Boolean {
        stop()
        this.mode = mode

        // The wake-up variant wakes the application processor to deliver each
        // event, which is exactly what "screen off, phone in a pocket" needs.
        // Where a device has no wake-up variant we fall back to the ordinary
        // one and lean on the service's partial wake lock instead: more
        // battery, same behaviour.
        val (chosen, wake) = if (mode == "B") {
            pick(Sensor.TYPE_LINEAR_ACCELERATION) ?: pick(Sensor.TYPE_ACCELEROMETER)
                ?: (null to false)
        } else {
            pick(Sensor.TYPE_STEP_DETECTOR) ?: (null to false)
        }

        if (chosen == null) {
            descriptor = if (mode == "B") "no accelerometer" else
                "no step detector on this device -- switch to Mode B"
            return false
        }

        sensor = chosen
        usingWakeUpSensor = wake

        // maxReportLatencyUs = 0 disables hardware batching. Sensor FIFOs will
        // happily buffer step events and deliver them in a burst seconds later,
        // which is a battery win for a pedometer and fatal here -- you would
        // stand still and then lurch forward. Screen-off is precisely when the
        // OS most wants to batch, so this cannot be left to defaults.
        val periodUs = if (mode == "B") 1_000_000 / accHz.coerceIn(10, 200)
        else SensorManager.SENSOR_DELAY_FASTEST

        // Without a Handler, SensorManager delivers events on the MAIN
        // thread. That matters twice over: a socket write there throws
        // NetworkOnMainThreadException, and in Mode B it would run the UI
        // thread at 50Hz. Both are avoided by naming the thread we want.
        val ok = if (deliverOn != null) {
            sm.registerListener(this, chosen, periodUs, 0, deliverOn)
        } else {
            sm.registerListener(this, chosen, periodUs, 0)
        }
        descriptor = buildString {
            append(chosen.name)
            append(if (wake) " (wake-up)" else " (non-wake)")
            if (mode == "B") append(" @${accHz}Hz")
        }
        if (!ok) descriptor = "registration refused: $descriptor"
        Log.i(Link.TAG, "sensor: $descriptor")
        return ok
    }

    /** Prefer the wake-up variant; fall back to the ordinary one. */
    private fun pick(type: Int): Pair<Sensor, Boolean>? {
        sm.getDefaultSensor(type, true)?.let { return it to true }
        sm.getDefaultSensor(type)?.let { return it to false }
        return null
    }

    fun stop() {
        if (sensor != null) sm.unregisterListener(this)
        sensor = null
        usingWakeUpSensor = false
    }

    override fun onSensorChanged(e: SensorEvent) {
        when (e.sensor.type) {
            Sensor.TYPE_STEP_DETECTOR -> onStep()
            Sensor.TYPE_LINEAR_ACCELERATION, Sensor.TYPE_ACCELEROMETER ->
                onSample(e.values[0], e.values[1], e.values[2])
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
}
