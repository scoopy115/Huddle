#!/usr/bin/env node
// One release build for every platform. Run from anywhere in the repo:
//
//   node scripts/release.mjs              signed release for the OS this runs on
//   node scripts/release.mjs --unsigned   local build without signing/notarization
//   node scripts/release.mjs --dry-run    print the steps, run nothing
//
// macOS → Huddle-<version>-macos-arm64.dmg (signed, notarized, stapled):
//   audio-tap helper if missing → speaker models if missing → packaging Python if missing →
//   engine sidecar (when the sources are newer) → build-app.sh → package-release.sh.
//   Needs HUDDLE_SIGN_IDENTITY ("Developer ID Application: …") and HUDDLE_NOTARY_PROFILE
//   (notarytool keychain profile); see docs/PACKAGING.md. `--unsigned` builds ad hoc.
// Windows → Huddle-<version>-windows-x64-setup.exe (NSIS):
//   speaker models if missing → engine venv if missing → engine sidecar (when newer) →
//   build-app.ps1. Signs with HUDDLE_WIN_CERT_THUMBPRINT when set.
//
// Linux → Huddle-<version>-linux-<x64|arm64>.deb and .AppImage (unsigned):
//   speaker models if missing → engine venv if missing → engine sidecar (when newer) →
//   build-app-linux.sh. Build on the oldest distribution you want to support (glibc).
//
// The per-step scripts stay usable on their own; this file only decides the order and checks
// what can be skipped. Node is the one runtime both build machines have (Tauri needs it).
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const args = new Set(process.argv.slice(2));
const dryRun = args.has("--dry-run");
const unsigned = args.has("--unsigned");
const version = JSON.parse(readFileSync(join(root, "apps/desktop/src-tauri/tauri.conf.json"), "utf8")).version;
const win = process.platform === "win32";

function step(title, cmd, cmdArgs, env = {}) {
  console.log(`\n==> ${title}`);
  console.log(`    ${[cmd, ...cmdArgs].join(" ")}`);
  if (dryRun) return;
  const r = spawnSync(cmd, cmdArgs, { cwd: root, stdio: "inherit", env: { ...process.env, ...env }, shell: false });
  if (r.status !== 0) {
    console.error(`\n${title} failed (exit ${r.status ?? r.signal}).`);
    process.exit(r.status || 1);
  }
}
const sh = (title, script, env) => step(title, "bash", [join(root, "scripts", script)], env);
const ps = (title, script) => step(title, "powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", join(root, "scripts", script)]);
const skip = (title, why) => console.log(`\n==> ${title}: skipped (${why})`);
const fail = (msg) => { console.error(`\n${msg}`); process.exit(1); };

/** Newest mtime of the engine's Python sources, to decide whether the sidecar is stale. */
function newestSource(dir) {
  let newest = 0;
  for (const name of readdirSync(dir)) {
    if (name === "__pycache__") continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) newest = Math.max(newest, newestSource(p));
    else if (name.endsWith(".py")) newest = Math.max(newest, st.mtimeMs);
  }
  return newest;
}
function sidecarFresh(sidecar) {
  if (!existsSync(sidecar)) return false;
  return statSync(sidecar).mtimeMs >= newestSource(join(root, "engine/huddle_engine"));
}

console.log(`Huddle ${version} release build for ${win ? "Windows" : process.platform === "darwin" ? "macOS" : process.platform === "linux" ? "Linux" : process.platform}${dryRun ? " (dry run)" : ""}`);

if (win) {
  const models = join(root, "apps/desktop/src-tauri/resources/models/speaker/nemo_titanet_large.onnx");
  if (existsSync(models)) skip("Speaker models", "present"); else ps("Speaker models", "fetch-speaker-models.ps1");

  const venvPy = join(root, "engine/.venv/Scripts/python.exe");
  if (existsSync(venvPy)) {
    skip("Engine venv", "present");
  } else {
    step("Engine venv", "py", ["-3.11", "-m", "venv", join(root, "engine/.venv")]);
    step("Engine requirements", venvPy, ["-m", "pip", "install", "-q", "-r", join(root, "engine/requirements.txt"), "pyinstaller"]);
  }

  const sidecar = join(root, "engine/dist.nosync/huddle-engine/huddle-engine.exe");
  if (sidecarFresh(sidecar)) skip("Engine sidecar", "up to date"); else ps("Engine sidecar", "build-engine.ps1");

  if (!process.env.HUDDLE_WIN_CERT_THUMBPRINT && !unsigned) {
    fail("HUDDLE_WIN_CERT_THUMBPRINT is not set: the installer would be unsigned (SmartScreen warns). Set it, or pass --unsigned.");
  }
  ps("Installer", "build-app.ps1");
  const out = join(root, `apps/desktop/src-tauri/target.nosync/release/bundle/nsis/Huddle-${version}-windows-x64-setup.exe`);
  if (!dryRun && !existsSync(out)) fail(`Installer not found at ${out}`);
  console.log(`\nRelease asset: ${out}\nTag the GitHub release v${version} and attach it.`);
} else if (process.platform === "darwin") {
  if (!unsigned && (!process.env.HUDDLE_SIGN_IDENTITY || !process.env.HUDDLE_NOTARY_PROFILE)) {
    fail("HUDDLE_SIGN_IDENTITY and HUDDLE_NOTARY_PROFILE must be set for a release (Developer ID + notarytool profile, see docs/PACKAGING.md). Pass --unsigned for a local, ad-hoc build.");
  }
  const env = unsigned ? { HUDDLE_SIGN_IDENTITY: "-", HUDDLE_NOTARY_PROFILE: "" } : {};

  const triple = (() => {
    const r = spawnSync("rustc", ["-vV"], { encoding: "utf8" });
    return (r.stdout || "").match(/host: (\S+)/)?.[1] ?? "aarch64-apple-darwin";
  })();
  const helper = join(root, `apps/desktop/src-tauri/binaries/huddle-audio-tap-${triple}`);
  if (existsSync(helper)) skip("System-audio helper", "present"); else sh("System-audio helper", "build-audio-tap.sh");

  const models = join(root, "apps/desktop/src-tauri/resources/models/speaker/nemo_titanet_large.onnx");
  if (existsSync(models)) skip("Speaker models", "present"); else sh("Speaker models", "fetch-speaker-models.sh");

  if (existsSync(join(root, "engine/.venv-pack.nosync/bin/python"))) skip("Packaging Python", "present"); else sh("Packaging Python", "setup-pack-python.sh");

  const sidecar = join(root, "engine/dist.nosync/huddle-engine/huddle-engine");
  if (sidecarFresh(sidecar)) skip("Engine sidecar", "up to date"); else sh("Engine sidecar", "build-engine.sh");

  sh(unsigned ? "App bundle (ad hoc)" : "App bundle (signed)", "build-app.sh", env);
  sh(unsigned ? "Disk image" : "Notarize + disk image", "package-release.sh", env);
  const out = join(root, `apps/desktop/src-tauri/target.nosync/release/bundle/macos/Huddle-${version}-macos-arm64.dmg`);
  if (!dryRun && !existsSync(out)) fail(`Disk image not found at ${out}`);
  console.log(`\nRelease asset: ${out}\nTag the GitHub release v${version} and attach it.`);
} else if (process.platform === "linux") {
  const models = join(root, "apps/desktop/src-tauri/resources/models/speaker/nemo_titanet_large.onnx");
  if (existsSync(models)) skip("Speaker models", "present"); else sh("Speaker models", "fetch-speaker-models.sh");

  const venvPy = join(root, "engine/.venv/bin/python");
  if (existsSync(venvPy)) {
    skip("Engine venv", "present");
  } else {
    step("Engine venv", "python3.11", ["-m", "venv", join(root, "engine/.venv")]);
    step("Engine requirements", venvPy, ["-m", "pip", "install", "-q", "-r", join(root, "engine/requirements.txt"), "pyinstaller"]);
  }

  const sidecar = join(root, "engine/dist.nosync/huddle-engine/huddle-engine");
  if (sidecarFresh(sidecar)) skip("Engine sidecar", "up to date"); else sh("Engine sidecar", "build-engine.sh");

  sh("Packages (.deb + AppImage)", "build-app-linux.sh");
  const arch = process.arch === "arm64" ? "arm64" : "x64";
  const dir = join(root, "apps/desktop/src-tauri/target.nosync/release/bundle");
  const out = [`deb/Huddle-${version}-linux-${arch}.deb`, `appimage/Huddle-${version}-linux-${arch}.AppImage`].map((f) => join(dir, f));
  if (!dryRun) for (const f of out) if (!existsSync(f)) fail(`Package not found at ${f}`);
  console.log(`\nRelease assets:\n  ${out.join("\n  ")}\nTag the GitHub release v${version} and attach them.`);
} else {
  fail(`No packaging for ${process.platform}.`);
}
