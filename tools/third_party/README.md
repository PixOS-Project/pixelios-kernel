# tools/third_party: vendored AOSP boot tools (unmodified)

Tracked in git on purpose (T-93e6) so a plain `git worktree add` has them. Pinned, sha256-verified copies, never edited here:
- mkbootimg.py and gki/generate_gki_certificate.py (this directory): platform/system/tools/mkbootimg `d2bb0af5ba6d3198a3e99529c97eda1be0b5a093`, Apache-2.0 (`LICENSES/Apache-2.0.txt`)
- avbtool.py (this directory): platform/external/avb `761178607206f4cb2af79ed9eec52d8cbd814adb`, MIT-style notice in the file header (`LICENSES/MIT-AOSP-avbtool.txt`)

`tools/fetch-android-tools.sh` re-fetches the same pins and checks the sha256 (fails closed); a mismatch against the tracked files is a bug.
