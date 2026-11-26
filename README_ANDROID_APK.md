# Habibi (حَبِيبِي) — Commercial-Clean Android APK & Offline SQLite Bundle

This bundle contains the complete **Habibi** Flagship front-end (`www/index.html`), the pre-seeded offline SQLite 3 database (`www/assets/databases/habibiSQLite.db`), the original curriculum seeder & MIT-licensed FSRS memory scheduler (`database/`), Capacitor Android configuration (`capacitor.config.json` & `package.json`), and full third-party open-source attribution (`THIRD_PARTY_LICENSES.md`).

## Commercial & IP Cleanliness Summary
* **Original Curriculum & Code**: All lesson screens, 28-letter visual mnemonics, 10 root builders (**Word-Root Forge**), 320 tridialectal Arabic words (Formal, Levantine, Cairo), and 10 real-world roleplays are original Habibi works—free of third-party trademarks, textbooks, or GPL/copyleft dictionaries.
* **Permissive Open-Source Stack**: Uses MIT-licensed libraries (`@capacitor/*`, `@capacitor-community/*`, `@capacitor-mlkit/translation`, Open Spaced Repetition `FSRS` equations, `Tailwind CSS`), SIL Open Font License 1.1 typography (`Plus Jakarta Sans`, `Noto Naskh Arabic`, `Cairo`), and the on-device ML Kit Translate SDK under the [ML Kit Terms of Service](https://developers.google.com/ml-kit/terms).
* **Pre-Launch Artwork Note**: Prototype illustrations in `www/index.html` and `assets/` were generated via AI preview tools (which embed a subtle preview watermark). Before publishing a paid listing on Google Play, replace those preview images with clean vector/custom unwatermarked brand artwork.

## Updating Your APK via GitHub Actions
1. Upload `www/index.html` (and optionally `database/`, `README_ANDROID_APK.md`, `THIRD_PARTY_LICENSES.md`) to your GitHub repository.
2. Click **Commit changes**—`.github/workflows/build-android-apk.yml` automatically compiles `Habibi-Arabic-debug.apk` under the **Actions** tab.
