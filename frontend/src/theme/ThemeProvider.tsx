import { useEffect, type ReactNode } from "react";
import { useThemeStore } from "../store/themeStore";
import { applyAccent } from "./themes";

export default function ThemeProvider({ children }: { children: ReactNode }) {
  const accent = useThemeStore((s) => s.accent);
  useEffect(() => {
    applyAccent(accent);
  }, [accent]);
  return <>{children}</>;
}
