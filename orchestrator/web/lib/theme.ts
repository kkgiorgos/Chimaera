/** Vendored Catppuccin palettes and client-safe theme helpers. */

export const FLAVORS = ["latte", "frappe", "macchiato", "mocha"] as const;
export type Flavor = (typeof FLAVORS)[number];

export const ACCENTS = [
  "rosewater",
  "flamingo",
  "pink",
  "mauve",
  "red",
  "maroon",
  "peach",
  "yellow",
  "green",
  "teal",
  "sky",
  "sapphire",
  "blue",
  "lavender",
] as const;
export type Accent = (typeof ACCENTS)[number];

type Palette = Record<string, string>;

const MOCHA: Palette = {
  base: "1e1e2e", mantle: "181825", crust: "11111b", text: "cdd6f4",
  subtext: "a6adc8", overlay2: "9399b2", overlay1: "7f849c", overlay0: "6c7086",
  surface2: "585b70", surface1: "45475a", surface0: "313244",
  rosewater: "f5e0dc", flamingo: "f2cdcd", pink: "f5c2e7", mauve: "cba6f7",
  red: "f38ba8", maroon: "eba0ac", peach: "fab387", yellow: "f9e2af",
  green: "a6e3a1", teal: "94e2d5", sky: "89dceb", sapphire: "74c7ec",
  blue: "89b4fa", lavender: "b4befe",
};
const MACCHIATO: Palette = {
  base: "24273a", mantle: "1e2030", crust: "181926", text: "cad3f5",
  subtext: "a5adcb", overlay2: "939ab7", overlay1: "8087a2", overlay0: "6e738d",
  surface2: "5b6078", surface1: "494d64", surface0: "363a4f",
  rosewater: "f4dbd6", flamingo: "f0c6c6", pink: "f5bde6", mauve: "c6a0f6",
  red: "ed8796", maroon: "ee99a0", peach: "f5a97f", yellow: "eed49f",
  green: "a6da95", teal: "8bd5ca", sky: "91d7e3", sapphire: "7dc4e4",
  blue: "8aadf4", lavender: "b7bdf8",
};
const FRAPPE: Palette = {
  base: "303446", mantle: "292c3c", crust: "232634", text: "c6d0f5",
  subtext: "a5adce", overlay2: "949cbb", overlay1: "838ba7", overlay0: "737994",
  surface2: "626880", surface1: "51576d", surface0: "414559",
  rosewater: "f2d5cf", flamingo: "eebebe", pink: "f4b8e4", mauve: "ca9ee6",
  red: "e78284", maroon: "ea999c", peach: "ef9f76", yellow: "e5c890",
  green: "a6d189", teal: "81c8be", sky: "99d1db", sapphire: "76c4ce",
  blue: "8caaee", lavender: "babbf1",
};
const LATTE: Palette = {
  base: "eff1f5", mantle: "e6e9ef", crust: "dce0e8", text: "4c4f69",
  subtext: "6c6f85", overlay2: "7c7f93", overlay1: "8c8fa1", overlay0: "9ca0b0",
  surface2: "acb0be", surface1: "bcc0cc", surface0: "ccd0da",
  rosewater: "dc8a78", flamingo: "dd7878", pink: "ea76cb", mauve: "8839ef",
  red: "d20f39", maroon: "e64553", peach: "fe640b", yellow: "df8e1d",
  green: "40a02b", teal: "179299", sky: "04a5e5", sapphire: "209fb5",
  blue: "1e66f5", lavender: "7287fd",
};

export const PALETTES: Record<Flavor, Palette> = {
  mocha: MOCHA,
  macchiato: MACCHIATO,
  frappe: FRAPPE,
  latte: LATTE,
};

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const l1 = luminance(a);
  const l2 = luminance(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

export function isFlavor(v: string): v is Flavor {
  return (FLAVORS as readonly string[]).includes(v);
}

export function isAccent(v: string): v is Accent {
  return (ACCENTS as readonly string[]).includes(v);
}

export function accentHex(flavor: Flavor, accent: Accent): string {
  const p = PALETTES[flavor] ?? MOCHA;
  return p[accent] ?? p.blue;
}

/** Readable foreground for content placed on the accent color. */
export function onAccentHex(flavor: Flavor, accent: Accent): string {
  const p = PALETTES[flavor] ?? MOCHA;
  const hex = accentHex(flavor, accent);
  const dark = flavor === "latte" ? MOCHA.crust : p.crust;
  return contrast(hex, "ffffff") >= contrast(hex, dark) ? "ffffff" : dark;
}

/** CSS variables (as `r g b` triplets) applied to <html>. */
export function themeVars(flavor: Flavor, accent: Accent): Record<string, string> {
  const p = PALETTES[flavor] ?? MOCHA;
  const rgb = (k: string) => hexToRgb(p[k]).join(" ");
  return {
    "--ctp-base": rgb("base"),
    "--ctp-mantle": rgb("mantle"),
    "--ctp-crust": rgb("crust"),
    "--ctp-text": rgb("text"),
    "--ctp-subtext": rgb("subtext"),
    "--ctp-overlay0": rgb("overlay0"),
    "--ctp-overlay1": rgb("overlay1"),
    "--ctp-overlay2": rgb("overlay2"),
    "--ctp-surface0": rgb("surface0"),
    "--ctp-surface1": rgb("surface1"),
    "--ctp-surface2": rgb("surface2"),
    "--ctp-accent": hexToRgb(accentHex(flavor, accent)).join(" "),
    "--ctp-on-accent": hexToRgb(onAccentHex(flavor, accent)).join(" "),
    "--ctp-green": rgb("green"),
    "--ctp-yellow": rgb("yellow"),
    "--ctp-red": rgb("red"),
    "--ctp-blue": rgb("blue"),
    "--ctp-teal": rgb("teal"),
    "--ctp-mauve": rgb("mauve"),
  };
}

/** Monaco theme definition values per flavor (editor follows the site). */
export function monacoThemeDef(flavor: Flavor, accent: Accent) {
  const p = PALETTES[flavor] ?? MOCHA;
  const hx = (k: string) => p[k];
  const accentH = accentHex(flavor, accent);
  return {
    base: (flavor === "latte" ? "vs" : "vs-dark") as "vs" | "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: hx("overlay0"), fontStyle: "italic" },
      { token: "keyword", foreground: hx("mauve") },
      { token: "string", foreground: hx("green") },
      { token: "number", foreground: hx("peach") },
      { token: "type", foreground: hx("yellow") },
      { token: "identifier", foreground: hx("text") },
      { token: "function", foreground: hx("blue") },
      { token: "operator", foreground: hx("sky") },
      { token: "delimiter", foreground: hx("overlay2") },
    ],
    colors: {
      "editor.background": `#${hx("base")}`,
      "editor.foreground": `#${hx("text")}`,
      "editorLineNumber.foreground": `#${hx("overlay1")}`,
      "editorLineNumber.activeForeground": `#${hx("rosewater")}`,
      "editorCursor.foreground": `#${accentH}`,
      "editor.lineHighlightBackground": `#${hx("surface0")}`,
      "editor.selectionBackground": `#${accentH}40`,
      "editor.inactiveSelectionBackground": `#${hx("surface1")}`,
      "editorIndentGuide.background1": `#${hx("surface1")}`,
      "editorIndentGuide.activeBackground1": `#${hx("overlay0")}`,
      "editorWidget.background": `#${hx("mantle")}`,
      "editorWidget.border": `#${hx("surface1")}`,
      "minimap.background": `#${hx("mantle")}`,
      "editorGutter.background": `#${hx("base")}`,
    },
  };
}
