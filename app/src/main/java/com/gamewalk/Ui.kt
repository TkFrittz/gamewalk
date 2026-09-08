package com.gamewalk

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.SeekBar
import android.widget.TextView

/**
 * Small view builders. The UI is written in code rather than XML so that the
 * settings screen can be generated from the descriptors the PC sends -- a new
 * config field then appears on the phone with no new APK.
 */

const val BG = 0xFF10141C.toInt()
const val CARD = 0xFF1A2030.toInt()
const val FG = 0xFFE8ECF4.toInt()
const val DIM = 0xFF8792A8.toInt()
const val ACCENT = 0xFF4C8DFF.toInt()
const val GOOD = 0xFF43C97C.toInt()
const val BAD = 0xFFFF6B6B.toInt()

fun Context.dp(v: Int): Int = TypedValue.applyDimension(
    TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics
).toInt()

fun Context.column(pad: Int = 0): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL
    if (pad > 0) setPadding(dp(pad), dp(pad), dp(pad), dp(pad))
}

fun Context.row(): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.HORIZONTAL
    gravity = Gravity.CENTER_VERTICAL
}

fun Context.label(
    text: String, size: Float = 15f, color: Int = FG, bold: Boolean = false,
): TextView = TextView(this).apply {
    this.text = text
    setTextColor(color)
    setTextSize(TypedValue.COMPLEX_UNIT_SP, size)
    if (bold) setTypeface(typeface, Typeface.BOLD)
}

fun Context.card(title: String? = null): LinearLayout = column(14).apply {
    setBackgroundColor(CARD)
    if (title != null) {
        addView(label(title.uppercase(), 11f, DIM, bold = true).apply {
            letterSpacing = 0.12f
        })
        addView(spacer(8))
    }
}

fun Context.spacer(h: Int): View = View(this).apply {
    layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, dp(h))
}

fun Context.button(text: String, primary: Boolean = false, onClick: () -> Unit): Button =
    Button(this).apply {
        this.text = text
        isAllCaps = false
        setTextColor(if (primary) Color.WHITE else FG)
        setBackgroundColor(if (primary) ACCENT else 0xFF2A3346.toInt())
        setOnClickListener { onClick() }
    }

fun Context.field(hint: String, value: String, numeric: Boolean = false): EditText =
    EditText(this).apply {
        this.hint = hint
        setText(value)
        setTextColor(FG)
        setHintTextColor(DIM)
        setBackgroundColor(0xFF232B3C.toInt())
        setPadding(dp(10), dp(10), dp(10), dp(10))
        inputType = if (numeric) InputType.TYPE_CLASS_NUMBER else InputType.TYPE_CLASS_TEXT
    }

/**
 * A labelled slider that reports only when the finger lifts.
 *
 * Sending a config patch on every pixel of drag would put dozens of writes a
 * second on the wire and rewrite config.json just as often, for values the
 * user is only passing through on the way to the one they want.
 */
fun Context.slider(
    title: String,
    help: String?,
    value: Double,
    min: Double,
    max: Double,
    step: Double,
    format: (Double) -> String,
    onCommit: (Double) -> Unit,
): View {
    val steps = maxOf(1, Math.round((max - min) / step).toInt())
    val holder = column()
    val header = row()
    val name = label(title, 15f)
    val readout = label(format(value), 15f, ACCENT, bold = true)

    header.addView(name, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
    header.addView(readout)
    holder.addView(header)

    if (!help.isNullOrEmpty()) {
        holder.addView(label(help, 12f, DIM).apply {
            setPadding(0, dp(2), 0, 0)
        })
    }

    val bar = SeekBar(this).apply {
        this.max = steps
        progress = Math.round((value - min) / step).toInt().coerceIn(0, steps)
        setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar, p: Int, fromUser: Boolean) {
                readout.text = format(min + p * step)
            }

            override fun onStartTrackingTouch(sb: SeekBar) = Unit
            override fun onStopTrackingTouch(sb: SeekBar) {
                onCommit(min + sb.progress * step)
            }
        })
    }
    holder.addView(bar)
    return holder
}
