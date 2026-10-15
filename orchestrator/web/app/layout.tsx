import type { Metadata } from 'next';
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-mono/400.css';
import './globals.css';
import { ThemeProvider } from '@/components/theme-provider';
import { themeVars } from '@/lib/theme';

const initialTheme = `try{const light=localStorage.getItem('chimaera-theme')==='latte';const r=document.documentElement;r.dataset.flavor=light?'latte':'mocha';r.dataset.accent=light?'maroon':'sky';const vars=light?${JSON.stringify(themeVars('latte', 'maroon'))}:${JSON.stringify(themeVars('mocha', 'sky'))};for(const k in vars)r.style.setProperty(k,vars[k]);}catch{}`;

export const metadata: Metadata = {
  title: 'Chimaera · Ball catching',
  description: 'Distributed memory-contention experiments for Chimaera.',
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="en" data-flavor="mocha" data-accent="sky" suppressHydrationWarning>
    <head><script dangerouslySetInnerHTML={{ __html: initialTheme }} /></head>
    <body><ThemeProvider>{children}</ThemeProvider></body>
  </html>;
}
