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

export const NavContext = createContext<{ view: View; go: (v: View) => void }>({
  view: { kind: "meetings" },
  go: () => {},
});

export const useNav = () => useContext(NavContext);
