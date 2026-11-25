@echo off
echo ============================================================
echo   Habibi v2.0 - One-Click Push to GitHub APK Cloud Builder
echo   Repo: https://github.com/stefvs79-apk/habibi-arabic.git
echo ============================================================

where git >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [!] Git for Windows is not installed yet.
    echo     Download Git at https://git-scm.com/download/win OR drag-and-drop these folders into github.com/stefvs79-apk/habibi-arabic
    pause
    exit /b 1
)

if not exist ".git" (
    git init
    git branch -M main
    git remote add origin https://github.com/stefvs79-apk/habibi-arabic.git
)

git pull origin main --allow-unrelated-histories --no-edit 2>nul
git add -A
git commit -m "Upgrade Habibi v2.0: Settings, Themes, Tutor Guide, ML Kit, 320 Words & Icons"
git push -u origin main --force

echo.
echo [OK] Uploaded! Open https://github.com/stefvs79-apk/habibi-arabic/actions
echo      Wait ~2 minutes for the green checkmark and download Habibi-Android-Debug-APK!
pause
