import { declareIndexPlugin, type ReactRNPlugin, WidgetLocation } from '@remnote/plugin-sdk';

async function onActivate(plugin: ReactRNPlugin): Promise<void> {
  // RemNote deduplicates a plugin's widget registrations by filename, regardless of location.
  await plugin.app.registerWidget('snapshot_widget', WidgetLocation.Popup, {
    dimensions: { height: 720, width: 760 },
  });
  await plugin.app.registerCommand({
    id: 'open-pkmigrator-snapshot',
    name: 'Open PKMigrator snapshot',
    description: 'Open the read-only RemNote migration snapshot exporter.',
    keywords: 'PKMigrator snapshot export migration',
    action: async () => {
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

      // RemNote shows at most one toast per plugin every 10 seconds. Keep that slot for a
      // launch problem; the popup widget reports successful mounting itself.
      if (result.status === 'timeout') {
        void plugin.app
          .toast('PKMigrator: RemNote gave no popup response after 5 seconds.')
          .catch(() => undefined);
      } else if (result.status === 'failed') {
        const message = result.error instanceof Error ? result.error.message : String(result.error);
        void plugin.app.toast(`PKMigrator popup failed: ${message}`).catch(() => undefined);
      }
    },
  });
}

async function onDeactivate(_: ReactRNPlugin): Promise<void> {}

declareIndexPlugin(onActivate, onDeactivate);
