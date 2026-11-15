pluginManagement {
    repositories {
        google()
        maven("https://repo1.maven.org/maven2/")
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        maven("https://repo1.maven.org/maven2/")
    }
}

rootProject.name = "daymark"

include(
    ":core-api",
    ":core-llm",
    ":core-memory",
    ":core-skills",
    ":core-sentinel",
    ":core-agent",
    ":core-mcp",
    ":ios-hands",
    ":ios-runner",
    ":web-server",
)

// The iOS runner is built on a Mac that usually has no Android SDK: -Pdaymark.iosOnly=true skips the Android modules.
if (providers.gradleProperty("daymark.iosOnly").orNull != "true") {
    include(":hands", ":app")
}
