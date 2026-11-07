pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
    plugins {
        id("org.jetbrains.kotlin.jvm") version "2.1.20"
        // Android plugins are only resolvable where the Google repository is reachable.
        if (System.getenv("KENPI_CORE_ONLY") == null) {
            id("com.android.application") version "8.7.2"
            id("org.jetbrains.kotlin.android") version "2.1.20"
            id("org.jetbrains.kotlin.plugin.compose") version "2.1.20"
        }
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "kenpi-companion"
include(":core")
if (System.getenv("KENPI_CORE_ONLY") == null) include(":app")
