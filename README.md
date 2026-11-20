# FamilyGuard

A consent-first family safety and parental controls Android app.

## Step 1: Android foundation
- Kotlin + Jetpack Compose
- Gradle Kotlin DSL
- Android Gradle Plugin and JDK 17
- GitHub Actions builds a debug APK on every push and pull request to `main`

No monitoring, recording, location collection, or photo access is implemented in this foundation. Those features will be designed in later steps with explicit consent, runtime permissions, visible indicators, and platform-policy checks.

## Build
The GitHub Actions workflow uses Gradle 8.13 and runs `assembleDebug`.
