#!/usr/bin/env sh
# Run from the repo root after extracting this overlay on top of it.
cat README.android.md >> README.md && rm README.android.md
cat gitignore.add.txt >> .gitignore && rm gitignore.add.txt
rm apply-android-update.sh
echo "Done. Now: git add . && git commit -m 'Add Android app and APK build workflow' && git push"
