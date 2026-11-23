#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo " Building Habibi (حَبِيبِي) Android APK (com.habibi.arabic)"
echo "=========================================================="

# 1. Install Capacitor & SQLite plugin dependencies
npm install

# 2. Add Android native project if not already initialized
if [ ! -d "android" ]; then
  npx cap add android
fi

# 3. Sync www/ assets and pre-seeded habibiSQLite.db into Android project
npx cap copy android
npx cap sync android

# 4. Compile installable Debug APK via Gradle
cd android
chmod +x gradlew
./gradlew assembleDebug

echo ""
echo "✅ APK Build Complete!"
echo "📲 Installable APK Path: android/app/build/outputs/apk/debug/app-debug.apk"
echo "👉 To install directly onto a USB-connected Pixel/Android phone:"
echo "   adb install -r app/build/outputs/apk/debug/app-debug.apk"
