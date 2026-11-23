# Habibi (حَبِيبِي) — Android APK Build & Offline SQLite Guide

This bundle contains the complete **Habibi** mobile front-end (`www/index.html`), the pre-seeded offline SQLite 3 database (`www/assets/databases/habibiSQLite.db`), the relational DDL & FSRS Python engine (`database/`), and the [Capacitor Android](https://capacitorjs.com/docs/android) configuration (`capacitor.config.json` & `package.json`).

## Included Files
* `www/index.html` — Mobile-optimized Habibi web app with Android bottom thumb navigation, haptic feedback, and offline storage sync.
* `www/assets/databases/habibiSQLite.db` — Pre-built SQLite database populated with [Arramooz Alwaseet](https://github.com/linuxscout/arramooz) trilateral roots, [CAMeL Lab Frequency Lists](https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists), [Mishkal](https://github.com/linuxscout/mishkal) 3-tier vowel scaffolding, [Tatoeba](https://www.manythings.org/bilingual/ara/) bilingual sentences, and [FSRS](https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler) review cards.
* `capacitor.config.json` & `package.json` — Configured for `com.habibi.arabic` using [`@capacitor-community/sqlite`](https://github.com/capacitor-community/sqlite).
* `build_habibi_apk.sh` — Automated one-step build script using the [Capacitor CLI](https://capacitorjs.com/docs/cli/commands/build) and Android Gradle wrapper.

## How to Build & Sideload the APK onto Your Android Phone
1. Unzip `habibi_android_apk.zip` on a machine with **Node.js 18+** and **Android Studio / Android SDK (API 34)** installed.
2. Run the automated build script:
   ```bash
   ./build_habibi_apk.sh
   ```
3. Grab your installable APK at `android/app/build/outputs/apk/debug/app-debug.apk` and install it via `adb install -r android/app/build/outputs/apk/debug/app-debug.apk` or copy it directly to your phone.

## Cloud Build Without Android Studio (GitHub Actions CI/CD)
This bundle includes `.github/workflows/build-android-apk.yml` ([GitHub Actions `setup-java@v4`](https://github.com/actions/setup-java) & [Ionic/Capacitor Android CI Guide](https://ionic.io/docs/tutorials/integrations/github-actions/build-android)):
1. Initialize a Git repository in the unzipped folder and push to GitHub:
   ```bash
   git init
   git add .
   git commit -m "Initial commit: Habibi Arabic Android app"
   git branch -M main
   git remote add origin https://github.com/<your-username>/habibi-arabic.git
   git push -u origin main
   ```
2. Open the **Actions** tab on your GitHub repository. The **Build Habibi Android APK** workflow runs automatically on `ubuntu-latest` (or manually via **Run workflow**).
3. Download **`Habibi-Android-Debug-APK`** (`Habibi-Arabic-debug.apk`) from the workflow run's **Artifacts** section and tap to install on your Android phone.
