package com.princeofworld.familyguard

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjection
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import org.webrtc.Camera2Enumerator
import org.webrtc.CameraVideoCapturer
import org.webrtc.DefaultVideoDecoderFactory
import org.webrtc.DefaultVideoEncoderFactory
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStreamTrack
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.ScreenCapturerAndroid
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.SurfaceTextureHelper
import org.webrtc.VideoCapturer
import org.webrtc.VideoSource
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * User-started, foreground-only WebRTC video sharing. Screen capture is started
 * only with the one-time MediaProjection grant returned by Android's system UI.
 * Camera mode requires the normal CAMERA runtime permission.
 */
class LiveSharingService : Service() {
    companion object {
        const val ACTION_START_SCREEN = "com.princeofworld.familyguard.START_SCREEN_SHARE"
        const val ACTION_START_CAMERA = "com.princeofworld.familyguard.START_CAMERA_SHARE"
        const val ACTION_STOP = "com.princeofworld.familyguard.STOP_MEDIA_SHARE"
        const val EXTRA_RESULT_CODE = "projection_result_code"
        const val EXTRA_PROJECTION_DATA = "projection_data"
        private const val CHANNEL_ID = "princecircle_live_sharing"
        private const val NOTIFICATION_ID = 7412

        fun startScreen(context: Context, resultCode: Int, data: Intent) {
            val intent = Intent(context, LiveSharingService::class.java).apply {
                action = ACTION_START_SCREEN
                putExtra(EXTRA_RESULT_CODE, resultCode)
                putExtra(EXTRA_PROJECTION_DATA, data)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun startCamera(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, LiveSharingService::class.java).apply {
                action = ACTION_START_CAMERA
            })
        }

        fun stop(context: Context) {
            context.startService(Intent(context, LiveSharingService::class.java).apply { action = ACTION_STOP })
        }
    }

    private val worker = Executors.newSingleThreadExecutor()
    private val poller = Executors.newSingleThreadScheduledExecutor()
    private val clientId = UUID.randomUUID().toString()
    private var sessionId: String? = null
    private var factory: PeerConnectionFactory? = null
    private var peer: PeerConnection? = null
    private var capturer: VideoCapturer? = null
    private var videoSource: VideoSource? = null
    private var egl: EglBase? = null
    private var surfaceHelper: SurfaceTextureHelper? = null
    private var remoteDescriptionSet = false
    private val seenSignals = mutableSetOf<String>()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                worker.execute { stopSharing(true) }
                return START_NOT_STICKY
            }
            ACTION_START_SCREEN, ACTION_START_CAMERA -> {
                val type = if (intent.action == ACTION_START_SCREEN) {
                    if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION else 0
                } else {
                    if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA else 0
                }
                val notification = buildNotification("Sharing is starting. Tap Stop to end the session.")
                if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION_ID, notification, type)
                else startForeground(NOTIFICATION_ID, notification)
                worker.execute {
                    try {
                        startSharing(intent)
                    } catch (error: Exception) {
                        updateNotification("Sharing could not start: ${error.message ?: "unknown error"}")
                        stopSharing(false)
                    }
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun startSharing(startIntent: Intent) {
        val prefs = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
        val deviceId = prefs.getString("deviceId", null)?.takeIf { it.isNotBlank() }
            ?: throw IllegalStateException("Device registration is not ready. Sign in and retry.")
        val screenMode = startIntent.action == ACTION_START_SCREEN
        if (screenMode && (startIntent.getIntExtra(EXTRA_RESULT_CODE, 0) == 0 ||
                startIntent.getParcelableExtra<Intent>(EXTRA_PROJECTION_DATA) == null)) {
            throw IllegalStateException("Android screen-capture permission was not provided.")
        }

        val response = FamilyGuardApi.post(this, "/api/webrtc/sessions",
            JSONObject().put("deviceId", deviceId).put("consentAccepted", true))
        sessionId = response.getString("sessionId")
        val iceArray = response.optJSONArray("iceServers") ?: JSONArray()
        val servers = mutableListOf<PeerConnection.IceServer>()
        for (i in 0 until iceArray.length()) {
            val item = iceArray.optJSONObject(i) ?: continue
            val urls = item.opt("urls")
            val urlList = when (urls) {
                is JSONArray -> (0 until urls.length()).mapNotNull { urls.optString(it).takeIf(String::isNotBlank) }
                is String -> listOf(urls)
                else -> emptyList()
            }
            if (urlList.isNotEmpty()) {
                val builder = PeerConnection.IceServer.builder(urlList)
                item.optString("username").takeIf(String::isNotBlank)?.let(builder::setUsername)
                item.optString("credential").takeIf(String::isNotBlank)?.let(builder::setPassword)
                servers += builder.createIceServer()
            }
        }
        check(servers.isNotEmpty()) { "Temporary ICE credentials were not returned." }

        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(applicationContext).createInitializationOptions()
        )
        egl = EglBase.create()
        factory = PeerConnectionFactory.builder()
            .setVideoEncoderFactory(DefaultVideoEncoderFactory(egl!!.eglBaseContext, true, true))
            .setVideoDecoderFactory(DefaultVideoDecoderFactory(egl!!.eglBaseContext))
            .createPeerConnectionFactory()

        val config = PeerConnection.RTCConfiguration(servers).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
            continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_CONTINUALLY
        }
        peer = factory!!.createPeerConnection(config, object : PeerConnection.Observer {
            override fun onSignalingChange(state: PeerConnection.SignalingState) = Unit
            override fun onIceConnectionChange(state: PeerConnection.IceConnectionState) {
                updateNotification(if (state == PeerConnection.IceConnectionState.CONNECTED) {
                    "Live sharing connected. Tap Stop to end."
                } else "Live sharing active (${state.name.lowercase()}). Tap Stop to end.")
            }
            override fun onIceConnectionReceivingChange(receiving: Boolean) = Unit
            override fun onIceGatheringChange(state: PeerConnection.IceGatheringState) = Unit
            override fun onIceCandidate(candidate: IceCandidate) {
                sendSignal("ice", JSONObject()
                    .put("sdpMid", candidate.sdpMid)
                    .put("sdpMLineIndex", candidate.sdpMLineIndex)
                    .put("candidate", candidate.sdp))
            }
            override fun onIceCandidatesRemoved(candidates: Array<IceCandidate>) = Unit
            override fun onAddStream(stream: org.webrtc.MediaStream) = Unit
            override fun onRemoveStream(stream: org.webrtc.MediaStream) = Unit
            override fun onDataChannel(channel: org.webrtc.DataChannel) = Unit
            override fun onRenegotiationNeeded() = Unit
            override fun onAddTrack(receiver: org.webrtc.RtpReceiver, streams: Array<out org.webrtc.MediaStream>) = Unit
            override fun onTrack(transceiver: org.webrtc.RtpTransceiver) = Unit
        }) ?: throw IllegalStateException("Could not create WebRTC connection.")

        videoSource = factory!!.createVideoSource(false)
        surfaceHelper = SurfaceTextureHelper.create("PrinceCircleCapture", egl!!.eglBaseContext)
        capturer = if (screenMode) {
            val projectionData = startIntent.getParcelableExtra<Intent>(EXTRA_PROJECTION_DATA)!!
            ScreenCapturerAndroid(projectionData, object : MediaProjection.Callback() {
                override fun onStop() {
                    worker.execute { stopSharing(true) }
                }
            })
        } else {
            val enumerator = Camera2Enumerator(this)
            val cameraName = enumerator.deviceNames.firstOrNull { enumerator.isFrontFacing(it) }
                ?: enumerator.deviceNames.firstOrNull()
                ?: throw IllegalStateException("No camera is available.")
            enumerator.createCapturer(cameraName, null) as? CameraVideoCapturer
                ?: throw IllegalStateException("Could not open the camera.")
        }
        capturer!!.initialize(surfaceHelper, this, videoSource!!.capturerObserver)
        if (screenMode) capturer!!.startCapture(1280, 720, 15)
        else capturer!!.startCapture(1280, 720, 24)
        val track = factory!!.createVideoTrack("princecircle-video", videoSource)
        track.setEnabled(true)
        peer!!.addTrack(track, listOf("princecircle-stream"))

        peer!!.createOffer(object : SdpObserver {
            override fun onCreateSuccess(description: SessionDescription) {
                peer?.setLocalDescription(object : SdpObserver {
                    override fun onSetSuccess() {
                        sendSignal("offer", JSONObject().put("type", description.type.canonicalForm()).put("sdp", description.description))
                        startPolling()
                    }
                    override fun onSetFailure(error: String) = fail("Could not set local offer: $error")
                    override fun onCreateSuccess(description: SessionDescription) = Unit
                    override fun onCreateFailure(error: String) = Unit
                }, description)
            }
            override fun onSetSuccess() = Unit
            override fun onCreateFailure(error: String) = fail("Could not create offer: $error")
            override fun onSetFailure(error: String) = Unit
        }, MediaConstraints())
        updateNotification(if (screenMode) "Screen sharing is active. Tap Stop to end." else "Camera sharing is active. Tap Stop to end.")
    }

    private fun startPolling() {
        poller.scheduleWithFixedDelay({
            try {
                val sid = sessionId ?: return@scheduleWithFixedDelay
                val result = FamilyGuardApi.get(this, "/api/webrtc/${sid}/signals?sender_id=${clientId}")
                val rows = result.optJSONArray("signals") ?: JSONArray()
                for (i in 0 until rows.length()) {
                    val signal = rows.optJSONObject(i) ?: continue
                    val signalId = signal.optString("signalId")
                    if (signalId.isBlank() || !seenSignals.add(signalId)) continue
                    val payload = signal.optJSONObject("payload") ?: continue
                    when (signal.optString("kind")) {
                        "answer" -> {
                            val description = SessionDescription(
                                SessionDescription.Type.fromCanonicalForm(payload.optString("type")),
                                payload.optString("sdp")
                            )
                            peer?.setRemoteDescription(object : SdpObserver {
                                override fun onSetSuccess() { remoteDescriptionSet = true }
                                override fun onSetFailure(error: String) = fail("Remote answer rejected: $error")
                                override fun onCreateSuccess(description: SessionDescription) = Unit
                                override fun onCreateFailure(error: String) = Unit
                            }, description)
                        }
                        "ice" -> {
                            if (remoteDescriptionSet) peer?.addIceCandidate(IceCandidate(
                                payload.optString("sdpMid"),
                                payload.optInt("sdpMLineIndex"),
                                payload.optString("candidate")
                            ))
                        }
                        "end" -> stopSharing(false)
                    }
                }
            } catch (error: Exception) {
                updateNotification("Sharing signal reconnecting: ${error.message ?: "network unavailable"}")
            }
        }, 1, 1, TimeUnit.SECONDS)
    }

    private fun sendSignal(kind: String, payload: JSONObject) {
        val sid = sessionId ?: return
        worker.execute {
            try {
                FamilyGuardApi.post(this, "/api/webrtc/${sid}/signals",
                    JSONObject().put("kind", kind).put("senderId", clientId).put("payload", payload))
            } catch (error: Exception) {
                updateNotification("Sharing signal failed: ${error.message ?: "network unavailable"}")
            }
        }
    }

    private fun fail(message: String) {
        updateNotification(message)
        worker.execute { stopSharing(true) }
    }

    private fun stopSharing(notifyPeer: Boolean) {
        val sid = sessionId
        if (notifyPeer && sid != null) {
            try { FamilyGuardApi.post(this, "/api/webrtc/${sid}/signals",
                JSONObject().put("kind", "end").put("senderId", clientId).put("payload", JSONObject())) } catch (_: Exception) { }
        }
        sessionId = null
        try { poller.shutdownNow() } catch (_: Exception) { }
        try { capturer?.stopCapture() } catch (_: Exception) { }
        try { capturer?.dispose() } catch (_: Exception) { }
        capturer = null
        try { peer?.close(); peer?.dispose() } catch (_: Exception) { }
        peer = null
        try { videoSource?.dispose() } catch (_: Exception) { }
        videoSource = null
        try { surfaceHelper?.dispose() } catch (_: Exception) { }
        surfaceHelper = null
        try { factory?.dispose() } catch (_: Exception) { }
        factory = null
        try { egl?.release() } catch (_: Exception) { }
        egl = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(CHANNEL_ID, "Live sharing", NotificationManager.IMPORTANCE_LOW))
        }
    }

    private fun buildNotification(message: String): Notification {
        val stopIntent = PendingIntent.getService(this, 7413,
            Intent(this, LiveSharingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setContentTitle("PrinceCircle live sharing")
            .setContentText(message)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .addAction(android.R.drawable.ic_media_pause, "Stop sharing", stopIntent)
            .build()
    }

    private fun updateNotification(message: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, buildNotification(message))
    }

    override fun onDestroy() {
        try { poller.shutdownNow() } catch (_: Exception) { }
        try { worker.shutdownNow() } catch (_: Exception) { }
        super.onDestroy()
    }
}
