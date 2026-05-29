# APK Staging Directory

This directory is for local APK staging only.

APK files are ignored by git on purpose. Put only APKs that you are allowed to
use and redistribute here, then reference them from an APK manifest such as:

```text
termux	com.termux	assets/apks/termux.apk	-
```

For public releases, prefer uploading self-owned APK artifacts to GitHub
Releases and recording the URL plus SHA256 in the manifest. Do not commit
third-party APKs, emulator snapshots, app data, or model files to this repo.
