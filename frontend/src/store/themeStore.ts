import { create } from "zustand";
import { applyAccent, type AccentId, readStoredAccent } from "../theme/themes";

type ThemeState = {
  accent: AccentId;
  setAccent: (id: AccentId) => void;
};

export const useThemeStore = create<ThemeState>((set) => ({
  accent: readStoredAccent(),
  setAccent: (id) => {
    applyAccent(id);
    set({ accent: id });
  },
}));
