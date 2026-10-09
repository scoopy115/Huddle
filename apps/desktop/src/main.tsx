import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { TrayPanel } from "./screens/TrayPanel";
import { initAppearance } from "./lib/theme";
import { isMac, isWindows } from "./lib/utils";
import "./index.css";

initAppearance();
// Window chrome differs per OS: macOS overlays the traffic lights on the page (the title bar
// spacers make room), Windows and Linux draw a native title bar above it.
document.documentElement.dataset.os = isMac ? "macos" : isWindows ? "windows" : "linux";

// The same bundle serves the main window and the menu-bar popover (`?window=tray`).
const isTray = new URLSearchParams(window.location.search).get("window") === "tray";
if (isTray) document.documentElement.classList.add("tray");

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {isTray ? <TrayPanel /> : <App />}
  </React.StrictMode>,
);
