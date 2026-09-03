export type Appearance = "system" | "light" | "dark";

const mq = window.matchMedia("(prefers-color-scheme: dark)");
let current: Appearance = (localStorage.getItem("huddle.appearance") as Appearance) || "system";

export function applyAppearance(a: Appearance) {
  current = a;
  try { localStorage.setItem("huddle.appearance", a); } catch { /* ignore */ }
  const dark = a === "dark" || (a === "system" && mq.matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function initAppearance() {
  applyAppearance(current);
  mq.addEventListener("change", () => applyAppearance(current));
}
