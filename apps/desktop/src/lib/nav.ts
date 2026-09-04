import { createContext, useContext } from "react";

export type View =
  | { kind: "meetings" }
  | { kind: "meeting"; id: string; seek?: number; segmentId?: number; nonce?: number }
  | { kind: "record" }
  | { kind: "search"; query?: string; nonce?: number }
  | { kind: "ask" }
  | { kind: "processes" }
  | { kind: "actions" }
  | { kind: "settings"; section?: string }
  | { kind: "onboarding" };

/** Whether summaries, Ask, Refine and action-item extraction can run (an AI model is resolved). */
export interface AiState { ready: boolean; reason: string | null; refresh: () => void }
export const AI_MISSING_HINT = "Needs an AI model — download one under Settings → Models.";

export const NavContext = createContext<{ view: View; go: (v: View) => void; ai: AiState }>({
  view: { kind: "meetings" },
  go: () => {},
  ai: { ready: true, reason: null, refresh: () => {} },
});

export const useNav = () => useContext(NavContext);
