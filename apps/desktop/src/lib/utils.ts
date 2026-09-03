import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** macOS uses ⌘ for app shortcuts; Windows/Linux builds use Ctrl. */
export const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
export const modKey = isMac ? "⌘" : "Ctrl+";
/** True when the platform's command modifier is held for this key event. */
export const hasMod = (e: KeyboardEvent | { metaKey: boolean; ctrlKey: boolean }) => (isMac ? e.metaKey : e.ctrlKey);

/** Subtle, accessible speaker differentiation — 8 hues, low saturation. `solid` is for avatars with white initials. */
export const SPEAKER_COLORS = [
  { dot: "bg-sky-500/80", solid: "bg-sky-500", text: "text-sky-700 dark:text-sky-300", bg: "bg-sky-500/10" },
  { dot: "bg-amber-500/80", solid: "bg-amber-500", text: "text-amber-700 dark:text-amber-300", bg: "bg-amber-500/10" },
  { dot: "bg-emerald-500/80", solid: "bg-emerald-500", text: "text-emerald-700 dark:text-emerald-300", bg: "bg-emerald-500/10" },
  { dot: "bg-rose-500/80", solid: "bg-rose-500", text: "text-rose-700 dark:text-rose-300", bg: "bg-rose-500/10" },
  { dot: "bg-violet-500/80", solid: "bg-violet-500", text: "text-violet-700 dark:text-violet-300", bg: "bg-violet-500/10" },
  { dot: "bg-teal-500/80", solid: "bg-teal-500", text: "text-teal-700 dark:text-teal-300", bg: "bg-teal-500/10" },
  { dot: "bg-orange-500/80", solid: "bg-orange-500", text: "text-orange-700 dark:text-orange-300", bg: "bg-orange-500/10" },
  { dot: "bg-fuchsia-500/80", solid: "bg-fuchsia-500", text: "text-fuchsia-700 dark:text-fuchsia-300", bg: "bg-fuchsia-500/10" },
];

export const speakerColor = (index: number) => SPEAKER_COLORS[Math.abs(index) % SPEAKER_COLORS.length];
