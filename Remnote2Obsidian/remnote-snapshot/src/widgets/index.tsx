import { declareIndexPlugin, type ReactRNPlugin, WidgetLocation } from '@remnote/plugin-sdk';

async function onActivate(plugin: ReactRNPlugin): Promise<void> {
  await plugin.app.registerWidget('snapshot_widget', WidgetLocation.RightSidebar, {
    dimensions: { height: 'auto', width: 360 },
  });
  await plugin.app.registerWidget('snapshot_widget', WidgetLocation.Popup, {
    dimensions: { height: 720, width: 760 },
  });
  await plugin.app.registerCommand({
    id: 'open-pkmigrator-snapshot',
    name: 'Open PKMigrator snapshot',
    description: 'Open the read-only RemNote migration snapshot exporter.',
    keywords: 'PKMigrator snapshot export migration',
    action: async () => {
      void plugin.app.toast('PKMigrator: requesting snapshot form…').catch(() => undefined);
      let timeoutId: ReturnType<typeof setTimeout> | undefined;
      const result = await Promise.race([
        plugin.widget.openPopup('snapshot_widget', undefined, false).then(
          () => ({ status: 'accepted' as const }),
          (error: unknown) => ({ status: 'failed' as const, error }),
        ),
        new Promise<{ status: 'timeout' }>((resolve) => {
          timeoutId = setTimeout(() => resolve({ status: 'timeout' }), 5_000);
        }),
      ]);
      if (timeoutId !== undefined) clearTimeout(timeoutId);

      if (result.status === 'accepted') {
        void plugin.app
          .toast('PKMigrator: popup request accepted; waiting for form to load.')
          .catch(() => undefined);
      } else if (result.status === 'timeout') {
        void plugin.app
          .toast('PKMigrator: popup request timed out after 5 seconds.')
          .catch(() => undefined);
      } else {
        const message = result.error instanceof Error ? result.error.message : String(result.error);
        void plugin.app.toast(`PKMigrator popup failed: ${message}`).catch(() => undefined);
      }
    },
  });
}

async function onDeactivate(_: ReactRNPlugin): Promise<void> {}

declareIndexPlugin(onActivate, onDeactivate);
