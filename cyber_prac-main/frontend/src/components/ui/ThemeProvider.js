"use client";

import { createContext, useContext, useSyncExternalStore, useCallback } from "react";

/* ═════════════════════════════════════════════════════════════════════════════
   THEME PROVIDER
   ────────────────────────────────────────────────────────────────────────────
   Owns exactly one bit of state — light or dark — and nothing else. All the
   actual styling lives in `globals.css`, which keys off the `.dark` class on
   <html>. This component only:

     · exposes `theme` (read straight off the document element)
     · toggles the `.dark` class on <html> and persists the choice
     · exposes `toggleTheme` to the topbar

   The inline script in `app/theme-script.js` applies the class before first
   paint, so there is no light-mode flash on a dark-mode load. Reading the
   theme through `useSyncExternalStore` (with a MutationObserver on <html>)
   means React always agrees with the DOM — no mirroring effect, no cascading
   render, no hydration mismatch.
   ═════════════════════════════════════════════════════════════════════════════ */

export const THEME_STORAGE_KEY = "cyberai_theme";

const ThemeContext = createContext({ theme: "light", toggleTheme: () => {} });

export function readStoredTheme() {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    /* private mode / storage disabled — fall through to the OS preference */
  }
  return null;
}

export function applyTheme(theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
}

function subscribeToTheme(cb) {
  if (typeof document === "undefined") return () => {};
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => obs.disconnect();
}

function getThemeSnapshot() {
  if (typeof document === "undefined") return "light";
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

/* Server render never has a resolved preference. The inline script has already
   painted the correct class by hydration time, so the first client read is
   accurate and React re-renders once — no flash, no mismatch warning. */
function getThemeServerSnapshot() { return "light"; }

export function ThemeProvider({ children }) {
  const theme = useSyncExternalStore(subscribeToTheme, getThemeSnapshot, getThemeServerSnapshot);

  const toggleTheme = useCallback(() => {
    const next = getThemeSnapshot() === "dark" ? "light" : "dark";
    applyTheme(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      /* non-fatal — the in-memory theme still applies for this session */
    }
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}

export default ThemeProvider;
