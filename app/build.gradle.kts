plugins {
    id("com.android.application") version "8.5.2"
    id("org.jetbrains.kotlin.android") version "1.9.24"
}

android {
    namespace = "com.gamewalk"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.gamewalk"
        // 26 covers Android 8 and up. Wake-up sensors and foreground services
        // both predate it comfortably.
        minSdk = 26
        targetSdk = 34
        versionCode = 3
        versionName = "0.1.2"
    }

    signingConfigs {
        create("release") {
            // Committed on purpose. Android refuses to install an update signed
            // by a different key, and CI runners start empty, so the key has to
            // live somewhere stable. See tools/make_keystore.py for the full
            // reasoning and how to switch to a repository secret instead.
            storeFile = file("gamewalk.p12")
            storePassword = "gamewalk"
            keyAlias = "gamewalk"
            keyPassword = "gamewalk"
            storeType = "PKCS12"
        }
    }

    buildTypes {
        release {
            // Minification off: the app is a few hundred KB either way, and
            // R8 stripping something reflective would be a miserable bug to
            // chase on a phone with no debugger attached.
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
        debug {
            signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // Deliberately minimal. The UI is built programmatically from views, which
    // avoids the AGP/Kotlin/Compose-compiler version alignment that makes a
    // first CI build fail for reasons unrelated to the app.
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
}
