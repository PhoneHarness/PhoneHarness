# Required and Optional Android Apps

PhoneHarness separates app requirements from APK redistribution.

## Policy

- Do commit source code for PhoneHarness-owned helper apps.
- Do use GitHub Releases or local files for PhoneHarness-owned APK artifacts.
- Do not commit third-party APKs, app data, login state, emulator snapshots, or model files to git.
- Do record package names, versions, download source, and SHA256 when a benchmark depends on a real app.

## Baseline Packages

| Purpose | Package | Notes |
| --- | --- | --- |
| Device shell | `com.termux` | Required for on-device server mode |
| Termux Android APIs | `com.termux.api` | Required for Termux API integrations |
| Text input bridge | ADBKeyboard package varies by build | Required for robust non-ASCII text input |
| Virtual display helper | `com.phoneharness.vdhelper` | Built from `vdisplay-helper/` when needed |

## Optional Real-App Coverage Set

These package names are useful for reproducing broader GUI tasks, but their APKs
are not redistributed by this repository:

| App family | Package |
| --- | --- |
| Bilibili | `tv.danmaku.bili` |
| Xiaohongshu | `com.xingin.xhs` |
| QQ Browser | `com.tencent.mtt` |
| Meituan | `com.sankuai.meituan` |
| Meituan Waimai | `com.sankuai.meituan.takeoutnew` |
| Tencent Maps | `com.tencent.map` |
| Kuwo Music | `cn.kuwo.player` |
| WeRead | `com.tencent.weread` |

To reproduce a run that depends on these apps, place legally obtained APK files
under `assets/apks/` locally or provide official HTTPS URLs in an APK manifest.
Then run:

```bash
scripts/install_apps.sh --serial emulator-5554 --manifest path/to/apk-manifest.tsv
```

Use `scripts/collect_emulator_info.sh` after installation to record the exact
package set and versions present on the emulator.
