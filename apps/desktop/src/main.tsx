import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { TrayPanel } from "./screens/TrayPanel";
import { initAppearance } from "./lib/theme";
import "./index.css";

initAppearance();

// The same bundle serves the main window and the menu-bar popover (`?window=tray`).
const isTray = new URLSearchParams(window.location.search).get("window") === "tray";
if (isTray) document.documentElement.classList.add("tray");

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {isTray ? <TrayPanel /> : <App />}
  </React.StrictMode>,
);
