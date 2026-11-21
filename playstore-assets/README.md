# FamilyGuard: SafeCircle — Play Store artwork

This directory contains editable vector source artwork for the launcher icon and Play Store feature graphic.

- `app_icon.svg`: square icon artwork, designed for export at 512 × 512 PNG.
- `feature_graphic.svg`: feature graphic artwork, designed for export at 1024 × 500 PNG.

The Android adaptive launcher icon is implemented separately in `app/src/main/res/mipmap-anydpi-v26/`.

**Release note:** Google Play Console accepts raster PNG/JPEG artwork, not these SVG source files. Export these SVGs to PNG at the exact dimensions above before uploading the store listing. PNG exports are not included in this commit.
