# Changelog

Huddle's version is bumped with every notable change; the updater compares it with the latest
GitHub release tag (`v<version>`). Keep this file in step with `apps/desktop/src-tauri/tauri.conf.json`,
`Cargo.toml` and `package.json`.

## 0.4.7 — 2026-09-04

- The system-audio permission probe (and its test tone) is removed altogether. macOS cannot report
  that permission, and recording never needed to know it: the first tap raises the one-time
  prompt, which Huddle now triggers silently at launch and in onboarding. Settings show the
  microphone state and an "Open System Settings" button for system audio.
- Fix: turning the menu bar off crashed Huddle. The status item's last reference was dropped on a
  worker thread; every tray operation now runs on the main thread.
- Fix: the "Interface sounds" switch only took effect after a restart. A settings echo from the
  shell reverted it to a stale copy; the copy is now kept in step.

## 0.4.6 — 2026-09-04

- Fix: a faint beep on the Record screen, when the window regained focus and during recordings.
  It was the system-audio permission probe's test tone (−54 dBFS). The tone is now −90 dBFS,
  below anything a speaker can reproduce but still non-zero to the tap; a granted answer is
  cached for ten minutes; and the probe never runs while a recording is in progress.

## 0.4.5 — 2026-09-04

- The start-up screen says "Loading…" with a spinner instead of "Starting local engine…".

## 0.4.4 — 2026-09-04

- Fix: the processing spinner no longer appeared after a menu-bar recording. The icon animation
  thread exited when it saw nothing active, and "processing" arriving during that teardown was
  lost; the thread now stays alive and idles instead.

## 0.4.3 — 2026-09-04

- Fix: the recording dot was still missing. The separate dot item is gone; the dot is drawn on
  the "h" again, and the "h" colour comes from the menu bar's real appearance, read from Huddle's
  own status-item window, so it stays white on a dark bar.

## 0.4.2 — 2026-09-04

- Fix: stopping a recording crashed Huddle, and the red recording dot never appeared. The dot's
  status item was created and removed from the animation thread; AppKit only allows that on the
  main thread. Both now go through the main thread.

## 0.4.1 — 2026-09-04

- Fix: the menu-bar "h" turned black while the red recording dot showed. The "h" is now always a
  template image (white on a dark menu bar, whatever the appearance setting); the red dot is a
  separate tiny status item that appears next to it only while recording.

## 0.4.0 — 2026-09-04

- Requires macOS 14.2 or newer. The ScreenCaptureKit fallback for system audio is gone; system
  audio always comes from a Core Audio process tap, and screen recording is never involved.
- Permissions are checked for real: microphone through macOS, system audio through a short probe
  (macOS offers no query for it). Every launch asks for whatever is still missing and shows a
  reminder with the buttons to fix it; onboarding and Settings → Recording show the same panel.

## 0.3.1 — 2026-09-04

- Fix: the menu-bar icon flashed black/white while recording or processing and stayed black
  afterwards. Swapping the icon dropped the template flag; frames are now set with the flag in
  one call, and the recording frame follows the system appearance reported by macOS.

## 0.3.0 — 2026-09-04

- Menu-bar icon states: a blinking red dot while recording, a spinning arc while meetings are
  processing; recording takes precedence when both apply.
- Louder record start/stop chimes, now also played for recordings started from the menu bar or
  with ⌥⌘R (the shell plays them, since a hidden window does not).

## 0.2.2 — 2026-09-04

- Fix: turning off MCP network access froze the app. The forwarder re-locked its own mutex when
  asked to start with unchanged ports; the stuck task then blocked the main thread on stop.
- Onboarding ends with an "Allow recording" step that asks for the Microphone and System Audio
  permissions up front, so the prompts never surface later from a menu-bar recording.

## 0.2.1 — 2026-09-04

- Fix: system audio recorded at double speed. The tap's aggregate device follows the built-in
  speakers' clock, which the microphone capture switches to 24 kHz right after the tap has
  started; the helper now tracks the device rate and resamples to a fixed 48 kHz.

## 0.2.0 — 2026-09-04

- Menu-bar mode, on by default: closing the window (or ⌘Q) leaves a recorder in the menu bar,
  the engine sleeps when idle. Left click opens a popover with timer, level meter, start/stop,
  microphone and system-audio pickers; ⌥⌘R starts or stops a recording from anywhere. Stopping
  opens Huddle and processes the recording.
- System audio is captured through a Core Audio process tap (macOS 14.2+) instead of
  ScreenCaptureKit: no screen-recording permission, no purple indicator, no Dock bouncing.
- Automatic update check against the GitHub releases (on launch, then daily) with a manual
  "Check for updates" button and menu item; updates install themselves and relaunch.
- "Export audio…" per meeting; version and build shown under Settings → Advanced.
- Models are unloaded when the job queue is empty, so the idle engine no longer holds gigabytes.
- Unfinished recordings can be discarded from the recovery prompt; quitting while recording
  finishes the file so it can be recovered.
- Default storage limit 10 GB; "Ask this meeting" appears only once notes are ready.

## 0.1.0 — 2026-09-03

- First packaged build: recording, transcription (MLX / CTranslate2 Whisper), speaker
  separation (sherpa-onnx), summaries through Ollama, search, MCP server.
