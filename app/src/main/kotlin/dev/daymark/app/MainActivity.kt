package com.daymark.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.daymark.app.ui.DaymarkRoot
import com.daymark.app.ui.DaymarkTheme

class MainActivity : ComponentActivity() {
    override fun onStart() {
        super.onStart()
        (application as DaymarkApp).graph.uiVisible.value = true
    }

    override fun onStop() {
        (application as DaymarkApp).graph.uiVisible.value = false
        super.onStop()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val graph = (application as DaymarkApp).graph
        setContent { DaymarkTheme { DaymarkRoot(graph) } }
    }
}
