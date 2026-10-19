/* Same vendored palettes and contrast rule as the existing orchestrator. */
(() => {
  const flavors = ['latte', 'frappe', 'macchiato', 'mocha'];
  const accents = ['rosewater','flamingo','pink','mauve','red','maroon','peach','yellow','green','teal','sky','sapphire','blue','lavender'];
  const rgb = value => [0,2,4].map(offset => parseInt(value.slice(offset, offset+2), 16));
  const luminance = value => rgb(value).map(c => c/255).map(v => v <= .03928 ? v/12.92 : ((v+.055)/1.055)**2.4).reduce((sum,v,i) => sum+v*[.2126,.7152,.0722][i], 0);
  const contrast = (a,b) => (Math.max(luminance(a),luminance(b))+.05)/(Math.min(luminance(a),luminance(b))+.05);
  let flavor = 'mocha', accent = 'sky';
  try {
    const saved = localStorage.getItem('chimaera-latency-flavor') || localStorage.getItem('chimaera-theme');
    if (flavors.includes(saved)) flavor = saved;
    const savedAccent = localStorage.getItem('chimaera-latency-accent');
    accent = accents.includes(savedAccent) ? savedAccent : flavor === 'latte' ? 'maroon' : 'sky';
  } catch {}
  function apply(nextFlavor = flavor, nextAccent = accent) {
    if (!flavors.includes(nextFlavor) || !accents.includes(nextAccent)) return;
    flavor = nextFlavor; accent = nextAccent;
    const palette = window.CATPPUCCIN[flavor], root = document.documentElement;
    root.dataset.flavor = flavor; root.dataset.accent = accent;
    for (const [name,value] of Object.entries(palette)) root.style.setProperty('--ctp-'+name, rgb(value).join(' '));
    root.style.setProperty('--ctp-accent', rgb(palette[accent]).join(' '));
    const dark = flavor === 'latte' ? window.CATPPUCCIN.mocha.crust : palette.crust;
    const foreground = contrast(palette[accent], 'ffffff') >= contrast(palette[accent], dark) ? 'ffffff' : dark;
    root.style.setProperty('--ctp-on-accent', rgb(foreground).join(' '));
    try { localStorage.setItem('chimaera-latency-flavor', flavor); localStorage.setItem('chimaera-latency-accent', accent); } catch {}
    window.dispatchEvent(new Event('themechange'));
  }
  window.benchTheme = {apply, flavors, accents, rgb, contrast,
    get flavor() { return flavor; }, get accent() { return accent; },
    get palette() { return window.CATPPUCCIN[flavor]; }};
  apply();
})();
