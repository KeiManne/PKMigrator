import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import type { ReactRNPlugin } from '@remnote/plugin-sdk';
import ts from 'typescript';

async function loadOnActivate(): Promise<(plugin: ReactRNPlugin) => Promise<void>> {
  const sourcePath = process.env.LAUNCHER_SOURCE_PATH ?? resolve(__dirname, 'index.tsx');
  const source = await readFile(sourcePath, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2021,
    },
    fileName: sourcePath,
  });
  let onActivate: ((plugin: ReactRNPlugin) => Promise<void>) | undefined;
  const module = { exports: {} };
  const context = vm.createContext({
    clearTimeout,
    console,
    exports: module.exports,
    module,
    Promise,
    require: (id: string) => {
      assert.equal(id, '@remnote/plugin-sdk');
      return {
        WidgetLocation: { Popup: 'Popup', RightSidebar: 'RightSidebar' },
        declareIndexPlugin: (activate: (plugin: ReactRNPlugin) => Promise<void>) => {
          onActivate = activate;
        },
      };
    },
    setTimeout,
  });
  new vm.Script(compiled.outputText, { filename: sourcePath }).runInContext(context);
  assert.ok(onActivate, 'index widget declared its activation callback');
  return onActivate;
}

test('launcher registers one popup filename and opens it through the real command action', async () => {
  const onActivate = await loadOnActivate();

  const registeredWidgets = new Map<string, string>();
  let commandAction: (() => Promise<void>) | undefined;
  let popupOpened = false;
  const toasts: string[] = [];

  const plugin = {
    app: {
      registerWidget: async (fileName: string, location: string) => {
        // Current RemNote host behavior: the first registration for a filename wins.
        if (!registeredWidgets.has(fileName)) registeredWidgets.set(fileName, location);
      },
      registerCommand: async (command: { action: () => Promise<void> }) => {
        commandAction = command.action;
      },
      toast: async (message: string) => {
        toasts.push(message);
      },
    },
    widget: {
      openPopup: async (fileName: string) => {
        if (registeredWidgets.get(fileName) !== 'Popup') {
          throw new Error('Widget location is not set to the popup.');
        }
        popupOpened = true;
      },
    },
  } as unknown as ReactRNPlugin;

  await onActivate(plugin);

  assert.deepEqual([...registeredWidgets], [['snapshot_widget', 'Popup']]);
  assert.ok(commandAction, 'launcher command was registered');
  await commandAction();
  assert.equal(popupOpened, true);
  assert.deepEqual(toasts, []);
});
