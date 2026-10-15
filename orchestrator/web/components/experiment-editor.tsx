'use client';
import Editor, { loader, type BeforeMount } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import { useEffect, useRef } from 'react';
import { monacoThemeDef } from '@/lib/theme';
import { useTheme } from './theme-provider';
import { experimentSchema } from '@/lib/schema';

// Bundle both the editor and its workers locally; the VPN UI needs no public CDN.
if (typeof window !== 'undefined') {
  (self as typeof self & { MonacoEnvironment?: unknown }).MonacoEnvironment = {
    getWorker(_: unknown, label: string) {
      return label === 'json'
        ? new Worker(new URL('monaco-editor/language/json/json.worker.js', import.meta.url), { type: 'module' })
        : new Worker(new URL('monaco-editor/editor/editor.worker.js', import.meta.url), { type: 'module' });
    },
  };
  loader.config({ monaco });
}

export default function ExperimentEditor({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { light } = useTheme();
  const instance = useRef<typeof monaco | null>(null);
  const name = light ? 'catppuccin-latte' : 'catppuccin-mocha';
  const themes = (editor: typeof monaco) => {
    editor.editor.defineTheme('catppuccin-mocha', monacoThemeDef('mocha', 'sky'));
    editor.editor.defineTheme('catppuccin-latte', monacoThemeDef('latte', 'maroon'));
  };
  useEffect(() => {
    if (instance.current) { themes(instance.current); instance.current.editor.setTheme(name); }
  }, [name]);
  const beforeMount: BeforeMount = editor => {
    instance.current = editor;
    themes(editor);
    editor.json.jsonDefaults.setDiagnosticsOptions({ validate: true,
      schemas: [{ uri: 'chimaera://memory-experiment', fileMatch: ['*'], schema: experimentSchema }] });
  };
  return <Editor height="100%" language="json" path="experiment.json" value={value}
    onChange={value => onChange(value || '')} theme={name} beforeMount={beforeMount}
    options={{ fontFamily: 'IBM Plex Mono', fontSize: 12, minimap: { enabled: false },
      scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2, padding: { top: 16 },
      renderLineHighlight: 'line', wordWrap: 'on', ariaLabel: 'Memory-contention experiment JSON' }} />;
}
