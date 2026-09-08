package com.gamewalk

import android.content.Context
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/**
 * Finding the PC without typing an IP address, and exchanging a PIN for the
 * token once.
 *
 * "Install it and it works" cannot include reading an IP off one screen and
 * typing it into another, so the phone broadcasts and the PC answers.
 */
class Discovery(private val context: Context) {

    data class Found(val host: String, val ip: String, val port: Int, val open: Boolean)

    private val main = Handler(Looper.getMainLooper())

    /**
     * Broadcast and collect answers for [timeoutMs]. Runs on its own thread;
     * [onDone] arrives on the main thread.
     */
    fun scan(discoveryPort: Int, timeoutMs: Int = 2000, onDone: (List<Found>) -> Unit) {
        Thread({
            val found = LinkedHashMap<String, Found>()
            // Some devices drop inbound broadcast traffic without this held.
            val wm = context.applicationContext
                .getSystemService(Context.WIFI_SERVICE) as WifiManager
            val lock = wm.createMulticastLock("gamewalk-discovery").apply {
                setReferenceCounted(false)
                acquire()
            }
            var sock: DatagramSocket? = null
            try {
                sock = DatagramSocket().apply {
                    broadcast = true
                    soTimeout = 300
                }
                val msg = "GW-DISCOVER? ${BuildInfo.VERSION}".toByteArray()
                for (addr in broadcastAddresses()) {
                    try {
                        sock.send(DatagramPacket(msg, msg.size, addr, discoveryPort))
                    } catch (_: Exception) {
                    }
                }

                val buf = ByteArray(2048)
                val deadline = System.currentTimeMillis() + timeoutMs
                while (System.currentTimeMillis() < deadline) {
                    try {
                        val dp = DatagramPacket(buf, buf.size)
                        sock.receive(dp)
                        val parts = String(dp.data, 0, dp.length).trim().split(" ")
                        if (parts.size >= 5 && parts[0] == "GW-HERE") {
                            val f = Found(
                                host = parts[1],
                                ip = parts[2],
                                port = parts[3].toIntOrNull() ?: 5599,
                                open = parts[4] == "open",
                            )
                            found[f.ip] = f
                        }
                    } catch (_: Exception) {
                        // socket timeout; keep waiting until the deadline
                    }
                }
            } catch (_: Exception) {
            } finally {
                sock?.close()
                try { lock.release() } catch (_: Exception) {}
            }
            main.post { onDone(found.values.toList()) }
        }, "gw-discover").start()
    }

    /**
     * Exchange a PIN for the helper's token.
     *
     * Sent to the helper directly rather than broadcast: by this point we know
     * its address, and there is no reason to shout a PIN across the network.
     */
    fun pair(ip: String, discoveryPort: Int, pin: String, onDone: (String?) -> Unit) {
        Thread({
            var token: String? = null
            var sock: DatagramSocket? = null
            try {
                sock = DatagramSocket().apply { soTimeout = 2000 }
                val msg = "GW-PAIR $pin".toByteArray()
                sock.send(
                    DatagramPacket(msg, msg.size, InetAddress.getByName(ip), discoveryPort)
                )
                val buf = ByteArray(2048)
                val dp = DatagramPacket(buf, buf.size)
                sock.receive(dp)
                val parts = String(dp.data, 0, dp.length).trim().split(" ")
                if (parts.size >= 2 && parts[0] == "GW-PAIRED") token = parts[1]
            } catch (_: Exception) {
            } finally {
                sock?.close()
            }
            main.post { onDone(token) }
        }, "gw-pair").start()
    }

    /** Every /24-style broadcast address we can see, plus the global one.
     *  Phones often have several interfaces and the global broadcast is
     *  dropped by some Wi-Fi drivers. */
    private fun broadcastAddresses(): List<InetAddress> {
        val out = mutableListOf<InetAddress>()
        try {
            val ifaces = java.net.NetworkInterface.getNetworkInterfaces()
            while (ifaces.hasMoreElements()) {
                val nif = ifaces.nextElement()
                if (!nif.isUp || nif.isLoopback) continue
                for (ia in nif.interfaceAddresses) {
                    ia.broadcast?.let { out.add(it) }
                }
            }
        } catch (_: Exception) {
        }
        try {
            out.add(InetAddress.getByName("255.255.255.255"))
        } catch (_: Exception) {
        }
        return out
    }
}
