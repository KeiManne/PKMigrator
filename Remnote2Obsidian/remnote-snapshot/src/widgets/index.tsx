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
    action: () => plugin.widget.openPopup('snapshot_widget', undefined, false),
  });
}

async function onDeactivate(_: ReactRNPlugin): Promise<void> {}

declareIndexPlugin(onActivate, onDeactivate);
