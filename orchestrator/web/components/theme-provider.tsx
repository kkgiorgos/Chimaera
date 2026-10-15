'use client';
import { createContext, useContext, useEffect, useState } from 'react';
import { themeVars, type Flavor } from '@/lib/theme';

const Theme = createContext({ light: false, toggle: () => {} });
export const useTheme = () => useContext(Theme);
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [light, setLight] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try { setLight(localStorage.getItem('chimaera-theme') === 'latte'); } catch {}
    setReady(true);
  }, []);
  useEffect(() => {
    if (!ready) return;
    const flavor: Flavor = light ? 'latte' : 'mocha';
    const accent = light ? 'maroon' : 'sky';
    const root = document.documentElement;
    root.dataset.flavor = flavor;
    root.dataset.accent = accent;
    for (const [key, value] of Object.entries(themeVars(flavor, accent))) root.style.setProperty(key, value);
    if (ready) try { localStorage.setItem('chimaera-theme', flavor); } catch {}
  }, [light, ready]);
  return <Theme.Provider value={{ light, toggle: () => setLight(value => !value) }}>{children}</Theme.Provider>;
}
