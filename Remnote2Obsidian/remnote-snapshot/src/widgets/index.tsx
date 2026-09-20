import { declareIndexPlugin, type ReactRNPlugin, WidgetLocation } from '@remnote/plugin-sdk';

async function onActivate(plugin: ReactRNPlugin): Promise<void> {
  await plugin.app.registerWidget('snapshot_widget', WidgetLocation.RightSidebar, {
    dimensions: { height: 'auto', width: 360 },
  });
}

async function onDeactivate(_: ReactRNPlugin): Promise<void> {}

declareIndexPlugin(onActivate, onDeactivate);
