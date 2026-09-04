fn main() {
  // Short git hash shown as the build identifier in Settings → Advanced.
  let sha = std::process::Command::new("git")
    .args(["rev-parse", "--short=9", "HEAD"])
    .output()
    .ok()
    .filter(|o| o.status.success())
    .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
    .unwrap_or_else(|| "unknown".into());
  println!("cargo:rustc-env=HUDDLE_GIT_SHA={sha}");
  println!("cargo:rerun-if-changed=../../../.git/HEAD");
  tauri_build::build()
}
