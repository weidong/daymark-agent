package com.daymark.app.trace

import com.daymark.api.Action
import com.daymark.api.ActionResult
import com.daymark.api.AppInfo
import com.daymark.api.Device
import com.daymark.api.ScreenState
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** A [Device] that hands every observed screenshot to [record] (the trace's `step-<n>.png`). */
class RecordingDevice(
    private val delegate: Device,
    private val record: (ByteArray) -> Unit,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) : Device {
    override suspend fun observe(): ScreenState {
        val screen = delegate.observe()
        screen.screenshotPng?.let { png -> withContext(io) { record(png) } }
        return screen
    }

    override suspend fun perform(action: Action): ActionResult = delegate.perform(action)

    override suspend fun installedApps(): List<AppInfo> = delegate.installedApps()
}
