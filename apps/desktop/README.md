# Huddle desktop shell

Tauri 2 + React 19 + TypeScript + Tailwind v4. The shell owns recording, devices, permissions,
the menu bar and the lifecycle of the Python engine; everything about meetings lives in the
engine (`../../engine`) and is reached through the `engine_fetch` proxy command.

```bash
npm install
../../scripts/build-audio-tap.sh   # once: the ScreenCaptureKit system-audio helper (externalBin)
npx tauri dev                       # starts Vite + the app; the app starts the engine from engine/.venv
```

Browser-only UI work: run the engine with `HUDDLE_DEV_CORS=1 HUDDLE_NO_JOBS=1 HUDDLE_TOKEN=devtoken
--port 48731` and open http://localhost:1420 (`.env.development`).

Packaging: see `../../docs/PACKAGING.md` (`scripts/build-engine.sh`, then `scripts/build-app.sh`).
