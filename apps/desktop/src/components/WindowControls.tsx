import { useEffect, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { cn } from "@/lib/utils";

/** Minimize / maximize / close for Windows, where the shell draws no native title bar. The
 *  glyphs follow the Windows 11 caption buttons (1 px strokes, 10 px grid) so the window still
 *  reads as a window; closing goes through the same path as the native button (tray mode hides). */
export function WindowControls() {
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    const w = getCurrentWindow();
    let un: (() => void) | undefined;
    const sync = () => { w.isMaximized().then(setMaximized).catch(() => {}); };
    sync();
    w.onResized(sync).then((u) => (un = u)).catch(() => {});
    return () => un?.();
  }, []);
  const w = () => getCurrentWindow();
  const btn = "flex h-8 w-[46px] items-center justify-center text-fg/70 transition-colors hover:bg-fg/10 hover:text-fg focus:outline-none";
  return (
    <div className="absolute right-0 top-0 z-50 flex h-8 select-none">
      <button type="button" className={btn} onClick={() => w().minimize()} title="Minimize" aria-label="Minimize">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1"><path d="M0 5.5h10" /></svg>
      </button>
      <button type="button" className={btn} onClick={() => w().toggleMaximize()} title={maximized ? "Restore" : "Maximize"} aria-label={maximized ? "Restore" : "Maximize"}>
        {maximized ? (
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1"><path d="M2.5 2.5V0.5h7v7h-2" /><rect x="0.5" y="2.5" width="7" height="7" /></svg>
        ) : (
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1"><rect x="0.5" y="0.5" width="9" height="9" /></svg>
        )}
      </button>
      <button type="button" className={cn(btn, "hover:bg-[#c42b1c] hover:text-white")} onClick={() => w().close()} title="Close" aria-label="Close">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1"><path d="M0 0l10 10M10 0L0 10" /></svg>
      </button>
    </div>
  );
}
