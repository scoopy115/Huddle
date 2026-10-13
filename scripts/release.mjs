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
// Linux → Huddle-<version>-linux-<x64|arm64>.AppImage and .deb (unsigned):
//   speaker models if missing → engine sidecar (build-engine-linux.sh, uv + Python 3.11) when the
//   sources are newer → AppImage → repack without the build machine's WebKitGTK/GStreamer → .deb.
//   Build on the oldest distribution you want to support (glibc). The AppImage pipeline is x86-64.
//
// The per-step scripts stay usable on their own; this file only decides the order and checks
// what can be skipped. Node is the one runtime both build machines have (Tauri needs it).
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const args = new Set(process.argv.slice(2));
const dryRun = args.has("--dry-run");
const unsigned = args.has("--unsigned");
const version = JSON.parse(readFileSync(join(root, "apps/desktop/src-tauri/tauri.conf.json"), "utf8")).version;
const win = process.platform === "win32";

function step(title, cmd, cmdArgs, env = {}, cwd = root) {
  console.log(`\n==> ${title}`);
  console.log(`    ${[cmd, ...cmdArgs].join(" ")}`);
  if (dryRun) return;
  const r = spawnSync(cmd, cmdArgs, { cwd, stdio: "inherit", env: { ...process.env, ...env }, shell: false });
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

  const sidecar = join(root, "engine/dist.nosync/huddle-engine/huddle-engine");
  if (sidecarFresh(sidecar)) skip("Engine sidecar", "up to date"); else sh("Engine sidecar", "build-engine-linux.sh");

  // linuxdeploy cannot find the libgomp copies that scikit-learn/ctranslate2 ship inside the
  // engine ("Could not find dependency: libgomp-…so"); pointing LD_LIBRARY_PATH at them fixes it.
  const dist = join(root, "engine/dist.nosync/huddle-engine/_internal");
  const libPath = [dist, join(dist, "ctranslate2.libs"), join(dist, "scikit_learn.libs")].join(":");
  step("App bundle (AppImage)", "npx", ["tauri", "build", "--bundles", "appimage"], { LD_LIBRARY_PATH: libPath }, join(root, "apps/desktop"));
  // Tauri bundles WebKitGTK and GStreamer from the build machine, which clash with the host's
  // (grey screen on Bazzite); the repack leaves them out so the host's versions are used.
  sh("Repack AppImage without WebKit/GStreamer", "repack-appimage-linux.sh");
  // The .deb uses the system's WebKitGTK as it is.
  step("Package (.deb)", "npx", ["tauri", "build", "--bundles", "deb"], {}, join(root, "apps/desktop"));

  const arch = process.arch === "arm64" ? "arm64" : "x64";
  const bundle = join(root, "apps/desktop/src-tauri/target.nosync/release/bundle");
  const debArch = arch === "arm64" ? "arm64" : "amd64";
  const pairs = [
    [join(bundle, `appimage/Huddle_${version}_amd64.AppImage`), join(bundle, `appimage/Huddle-${version}-linux-${arch}.AppImage`)],
    [join(bundle, `deb/Huddle_${version}_${debArch}.deb`), join(bundle, `deb/Huddle-${version}-linux-${arch}.deb`)],
  ];
  if (!dryRun) for (const [from, to] of pairs) { if (!existsSync(from)) fail(`Package not found at ${from}`); copyFileSync(from, to); }
  console.log(`\nRelease assets:\n  ${pairs.map((p) => p[1]).join("\n  ")}\nNo signing on Linux. Tag the GitHub release v${version} and attach them.`);
} else {
  fail(`Unsupported platform: ${process.platform}`);
}
