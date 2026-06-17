# Android Emulator Setup

This page documents the reference emulator shape used for PhoneHarness runs.
The goal is to make emulator setup reproducible without publishing local AVD
images, app data, credentials, or third-party APK files.

## Reference AVD

Use one Android Studio emulator per PhoneHarness slot.

| Setting | Reference value |
| --- | --- |
| AVD name | `AndroidWorldAvd` |
| Device profile | `pixel_6` |
| System image | `system-images;android-33;google_apis_playstore;arm64-v8a` |
| Android API | 33 |
| RAM | 2048 MB |
| Data partition | 32G recommended |
| Screen assumption | 1080x2400 |
| Display density | Pixel 6 default; GUI helpers assume 1080-wide coordinates |
| Start flags | `-no-snapshot -no-audio -no-boot-anim` |

Disk guidance:

- 16G is the practical minimum for light UI smoke tests.
- 24G is safer once Termux packages, Python wheels, app APKs, traces, and caches accumulate.
- 32G is the recommended default for repeatable PhoneHarness development.

Create or update the reference AVD:

```bash
scripts/create_avd.sh --install-sdk --start
```

The script creates `AndroidWorldAvd`, sets the data partition to 32G, and starts
it on port `5554` by default. Use another serial for parallel slots:

```bash
scripts/create_avd.sh --name AndroidWorldAvd_2 --serial emulator-5556 --start
```

## Runtime Layout

The default single-emulator layout is:

| Component | Location | Default |
| --- | --- | --- |
| `gui_proxy` | host | `127.0.0.1:8919` |
| PhoneHarness server | emulator Termux | device port `8920` |
| Host to device | adb forward | `host:8920 -> device:8920` |
| Device to host GUI | adb reverse | `device:8919 -> host:8919` |

For multiple emulators, keep device-side ports stable and offset host ports by
slot:

| Slot | Serial | Host GUI proxy | Host PhoneHarness forward |
| --- | --- | --- | --- |
| 0 | `emulator-5554` | 8919 | 8920 |
| 1 | `emulator-5556` | 8929 | 8930 |
| 2 | `emulator-5558` | 8939 | 8940 |
| 3 | `emulator-5560` | 8949 | 8950 |
| 4 | `emulator-5562` | 8959 | 8960 |
| 5 | `emulator-5564` | 8969 | 8970 |

Always match `--serial` to `adb devices`; the `gui_proxy.py` default is only a
fallback.

## Required Device Apps

The emulator should have:

- Termux (`com.termux`)
- Termux:API (`com.termux.api`)
- ADBKeyboard or another input method that can accept adb text broadcasts
- Any task-specific real apps needed by a benchmark subset

PhoneHarness does not commit third-party APKs to git. Use
`config/apk-manifest.example.tsv` as a template and install from local APK files
or public release URLs:

```bash
scripts/install_apps.sh --serial emulator-5554 --manifest config/apk-manifest.example.tsv
```

Wire adb ports, disable animations, optionally install apps, and start the host
GUI proxy:

```bash
scripts/setup_emulator.sh --serial emulator-5554 --manifest config/apk-manifest.example.tsv
```

Self-owned helper APKs may be built from source or distributed as GitHub Release
assets. Third-party APKs should be downloaded from their official source or
provided locally by the user running the benchmark.

## Device State Capture

Capture the current emulator shape before sharing results:

```bash
scripts/collect_emulator_info.sh --serial emulator-5554 --out artifacts/emulator-info.md
```

The report includes Android build properties, screen size, density, disk usage,
installed package names, current input method, and animation settings. Commit the
report only when it is intentionally part of a reproducibility artifact and does
not reveal private apps or account state.

## Host Requirements

Install Android Studio or the Android command-line tools so these binaries are
available:

- `sdkmanager`
- `avdmanager`
- `emulator`
- `adb`

Default macOS SDK path:

```bash
export ANDROID_HOME="$HOME/Library/Android/sdk"
```

The virtual-display helper source builds against Android platform `android-33`
by default and creates a 1080x1920 virtual display at 320 dpi.

## Virtual Display Activity Handoff

Android may move a follow-up activity back to display 0 if the target app starts
it without display-aware launch options. PhoneHarness mitigates this only for
explicit launches that go through `scripts/vdisplay.sh launch`, which calls
`am start --display <id>` after `force_resizable_activities` is enabled. If an
app internally opens another activity a few seconds later, that second launch is
controlled by the app/Android task stack rather than by PhoneHarness.

For debugging this case, run `scripts/vdisplay.sh status` and inspect
`adb shell dumpsys activity activities` to see which display owns the resumed
activity. When possible, relaunch the final component with
`scripts/vdisplay.sh launch <package/activity>` or keep the workflow on display 0
for apps that do not preserve virtual-display affinity across internal activity
transitions.
