package com.daymark.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.daymark.api.ApprovalAnswer
import com.daymark.api.ApprovalScope

/** Not exported: receives only the PendingIntents of Daymark's own notifications. */
class NotificationActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val session = (context.applicationContext as DaymarkApp).graph.session
        val id = intent.getLongExtra(EXTRA_APPROVAL_ID, -1).takeIf { it >= 0 }
        when (intent.action) {
            ACTION_STOP -> session.stop()
            ACTION_APPROVE_ONCE -> session.answerApproval(ApprovalAnswer(true, ApprovalScope.ONCE), id)
            ACTION_APPROVE_TASK -> session.answerApproval(ApprovalAnswer(true, ApprovalScope.TASK), id)
            ACTION_DENY -> session.answerApproval(ApprovalAnswer(false), id)
        }
    }

    companion object {
        const val ACTION_STOP = "com.daymark.app.action.STOP"
        const val ACTION_APPROVE_ONCE = "com.daymark.app.action.APPROVE_ONCE"
        const val ACTION_APPROVE_TASK = "com.daymark.app.action.APPROVE_TASK"
        const val ACTION_DENY = "com.daymark.app.action.DENY"
        const val EXTRA_APPROVAL_ID = "approval_id"
    }
}
