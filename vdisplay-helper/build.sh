#!/bin/bash
set -e
cd "$(dirname "$0")"

JAVA_HOME="${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
export JAVA_HOME
ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
ANDROID_PLATFORM="${ANDROID_PLATFORM:-android-33}"
ANDROID_BUILD_TOOLS="${ANDROID_BUILD_TOOLS:-36.1.0}"
ANDROID_JAR="$ANDROID_HOME/platforms/$ANDROID_PLATFORM/android.jar"
BT="$ANDROID_HOME/build-tools/$ANDROID_BUILD_TOOLS"
AAPT2=$BT/aapt2
D8=$BT/d8
APKSIGNER=$BT/apksigner
ZIPALIGN=$BT/zipalign

PKG=com.phoneharness.vdhelper
OUT=build

rm -rf $OUT
mkdir -p $OUT/gen $OUT/classes $OUT/dex

# 1. Generate R.java (even though we have no real resources, aapt2 needs manifest)
echo "=== Compile resources ==="
$AAPT2 compile --dir res -o $OUT/compiled_res.zip 2>/dev/null || true

echo "=== Link ==="
$AAPT2 link \
    -I $ANDROID_JAR \
    --manifest AndroidManifest.xml \
    --java $OUT/gen \
    --auto-add-overlay \
    -o $OUT/base.apk \
    $OUT/compiled_res.zip 2>/dev/null || \
$AAPT2 link \
    -I $ANDROID_JAR \
    --manifest AndroidManifest.xml \
    --java $OUT/gen \
    --auto-add-overlay \
    -o $OUT/base.apk

# 2. Compile Java
echo "=== Compile Java ==="
JAVA_FILES=$(find src -name "*.java")
R_FILES=$(find $OUT/gen -name "*.java" 2>/dev/null)
"$JAVA_HOME/bin/javac" \
    -source 11 -target 11 \
    -cp $ANDROID_JAR \
    -d $OUT/classes \
    $JAVA_FILES $R_FILES

# 3. Dex
echo "=== Dex ==="
$D8 --output $OUT/dex \
    --lib $ANDROID_JAR \
    $(find $OUT/classes -name "*.class")

# 4. Add dex to APK
echo "=== Package ==="
cp $OUT/base.apk $OUT/unsigned.apk
cd $OUT/dex
zip -j ../unsigned.apk classes.dex
cd ../..

# 5. Zipalign
echo "=== Zipalign ==="
$ZIPALIGN -f 4 $OUT/unsigned.apk $OUT/aligned.apk

# 6. Sign with caller-provided local credentials
echo "=== Sign ==="
if [ -z "${PHONEHARNESS_APK_KEYSTORE:-}" ] || [ -z "${PHONEHARNESS_APK_KEY_ALIAS:-}" ]; then
    echo "Set PHONEHARNESS_APK_KEYSTORE, PHONEHARNESS_APK_KEY_ALIAS,"
    echo "PHONEHARNESS_APK_KEYSTORE_PASS, and PHONEHARNESS_APK_KEY_PASS to sign the APK."
    exit 1
fi
$APKSIGNER sign \
    --ks "$PHONEHARNESS_APK_KEYSTORE" \
    --ks-key-alias "$PHONEHARNESS_APK_KEY_ALIAS" \
    --ks-pass "pass:$PHONEHARNESS_APK_KEYSTORE_PASS" \
    --key-pass "pass:$PHONEHARNESS_APK_KEY_PASS" \
    --out $OUT/vdhelper.apk \
    $OUT/aligned.apk

echo ""
echo "=== Done ==="
ls -la $OUT/vdhelper.apk
