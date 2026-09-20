import assert from 'node:assert/strict';
import test from 'node:test';
import type { RichTextInterface } from '@remnote/plugin-sdk';
import { buildSnapshot, type HiddenState, type SnapshotPlugin, type SnapshotRem } from './snapshot';

const RemType = { DEFAULT_TYPE: 0, PORTAL: 6 } as const;
const PORTAL_TYPE = { PORTAL: 0, SEARCH_PORTAL: 4 } as const;

interface MockOptions {
  id: string;
  type?: number;
  parent?: string | null;
  children?: string[];
  text?: RichTextInterface;
  portalType?: number;
  members?: MockRem[];
  context?: MockRem[];
  hidden?: HiddenState | string;
  collapsed?: boolean;
  collapseThrows?: boolean;
  positionThrows?: boolean;
  position?: number;
  visiblePosition?: number;
  backlinkTarget?: MockRem;
  backlinks?: MockRem[];
  document?: boolean;
  folder?: boolean;
  powerup?: boolean;
}

class MockRem implements SnapshotRem {
  readonly _id: string;
  readonly createdAt = 1;
  readonly localUpdatedAt = 2;
  readonly updatedAt = 3;
  readonly parent: string | null;
  readonly children: string[];
  readonly type: number;
  readonly text: RichTextInterface;
  readonly backText = undefined;
  private readonly options: MockOptions;

  constructor(options: MockOptions) {
    this.options = options;
    this._id = options.id;
    this.parent = options.parent ?? null;
    this.children = options.children ?? [];
    this.type = options.type ?? RemType.DEFAULT_TYPE;
    this.text = options.text ?? [options.id];
  }

  async getPortalType(): Promise<number> {
    return this.options.portalType ?? PORTAL_TYPE.PORTAL;
  }
  async getPortalDirectlyIncludedRem(): Promise<MockRem[]> {
    return this.options.members ?? [];
  }
  async allRemInDocumentOrPortal(): Promise<MockRem[]> {
    return this.options.context ?? this.options.members ?? [];
  }
  async isCollapsed(): Promise<boolean> {
    if (this.options.collapseThrows) throw new Error('collapse unavailable');
    return this.options.collapsed ?? false;
  }
  async positionAmongstSiblings(): Promise<number | undefined> {
    if (this.options.positionThrows) throw new Error('position unavailable');
    return this.options.position;
  }
  async positionAmongstVisibleSiblings(): Promise<number | undefined> {
    return this.options.visiblePosition;
  }
  async getHiddenExplicitlyIncludedState(): Promise<HiddenState | undefined> {
    return (this.options.hidden ?? 'none') as HiddenState;
  }
  async getPowerupPropertyAsRem(): Promise<MockRem | undefined> {
    return this.options.backlinkTarget;
  }
  async getPowerupPropertyAsRichText(_powerup: string, slot: string): Promise<RichTextInterface> {
    return [`slot:${slot}`];
  }
  async remsReferencingThis(): Promise<MockRem[]> {
    return this.options.backlinks ?? [];
  }
  async isDocument(): Promise<boolean> {
    return this.options.document ?? false;
  }
  async isFolder(): Promise<boolean> {
    return this.options.folder ?? false;
  }
  async isPowerup(): Promise<boolean> { return this.options.powerup ?? false; }
  async isPowerupEnum(): Promise<boolean> { return false; }
  async isPowerupPropertyListItem(): Promise<boolean> { return false; }
  async isPowerupSlot(): Promise<boolean> { return false; }
  async isPowerupProperty(): Promise<boolean> { return false; }
}

test('captures ordered portal/search state and emits conservative converter projection', async () => {
  const hidden = new MockRem({
    id: 'hidden-child',
    parent: 'source-root',
    hidden: 'hidden',
    position: 0,
  });
  const source = new MockRem({
    id: 'source-root',
    children: [hidden._id],
    hidden: 'included',
    collapsed: true,
    position: 0,
    visiblePosition: 0,
    document: true,
  });
  const backlink = new MockRem({ id: 'backlink', parent: 'source-root' });
  const target = new MockRem({ id: 'target', backlinks: [backlink] });
  const result = new MockRem({ id: 'search-result', parent: 'source-root' });
  const ordinaryPortal = new MockRem({
    id: 'ordinary-portal',
    type: RemType.PORTAL,
    portalType: PORTAL_TYPE.PORTAL,
    members: [source],
    context: [source, hidden],
  });
  const searchPortal = new MockRem({
    id: 'search-portal',
    type: RemType.PORTAL,
    portalType: PORTAL_TYPE.SEARCH_PORTAL,
    members: [result],
    context: [result],
    backlinkTarget: target,
  });
  const all = [ordinaryPortal, searchPortal, source, hidden, result, target, backlink];
  const plugin: SnapshotPlugin = {
    app: {
      waitForInitialSync: async () => undefined,
      getPlatform: async () => 'web',
    },
    kb: {
      getCurrentKnowledgeBaseData: async () => ({ _id: 'kb-test', name: 'Fixture KB' }),
    },
    rem: { getAll: async () => all },
  };

  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'complete',
    priorityPortalIds: ['ordinary-portal'],
    classificationRecordIds: ['source-root'],
    ordinaryOrderValidated: true,
    searchOrderValidated: true,
    visibilitySemanticsValidated: true,
    childOrderCalibrated: true,
  });

  assert.equal(snapshot.schema_version, 'remnote-migration-snapshot/v1');
  assert.equal(snapshot.capture.knowledgebase_id, 'kb-test');
  assert.deepEqual(snapshot.portals['ordinary-portal'].membership.member_ids, ['source-root']);
  assert.equal(snapshot.portals['ordinary-portal'].visibility.states['hidden-child'], 'hidden');
  assert.equal(snapshot.portals['ordinary-portal'].collapsed.states['source-root'], true);
  assert.deepEqual(snapshot.converter_projection.portal_snapshots['ordinary-portal'].members, [
    'source-root',
  ]);
  assert.deepEqual(snapshot.converter_projection.visibility_overrides['ordinary-portal'].states, {
    'source-root': 'visible',
    'hidden-child': 'hidden',
  });
  assert.equal(snapshot.portals['search-portal'].automatic_view?.kind, 'backlink');
  assert.deepEqual(snapshot.portals['search-portal'].automatic_view?.result_ids, ['search-result']);
  assert.deepEqual(snapshot.relations.backlinks.target.member_ids, ['backlink']);
  assert.deepEqual(snapshot.classifications.document_and_folder.states['source-root'], {
    is_document: true,
    is_folder: false,
  });
  assert.equal(snapshot.records['source-root'].detail_level, 'rich');
  assert.match(snapshot.records['search-result'].rich_text_fingerprint, /^fnv1a64-json:/);
  assert.equal(snapshot.capture.export_comparison.child_order_calibrated, true);
});

test('truncation is explicit and never emits partial visibility overrides', async () => {
  const child = new MockRem({ id: 'child', parent: 'root', hidden: 'hidden' });
  const root = new MockRem({ id: 'root', children: ['child'] });
  const portal = new MockRem({
    id: 'portal',
    type: RemType.PORTAL,
    members: [root],
    context: [root, child],
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [portal, root, child] },
  };

  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'complete',
    maxProbesPerPortal: 1,
    maxContextProbes: 1,
  });

  assert.equal(snapshot.portals.portal.visibility.complete, false);
  assert.equal(snapshot.converter_projection.visibility_overrides.portal, undefined);
  assert.equal(snapshot.capture.complete, false);
  assert.ok(snapshot.errors.some((error) => error.operation === 'portal-context-probe-limit'));
});

test('cancellation escapes SDK error handling and returns no snapshot', async () => {
  const controller = new AbortController();
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: {
      getAll: async () => {
        controller.abort();
        return [];
      },
    },
  };

  await assert.rejects(
    buildSnapshot(plugin, () => undefined, { signal: controller.signal }),
    (error: unknown) => error instanceof DOMException && error.name === 'AbortError',
  );
});

test('calibration captures only requested portals and never projects unvalidated order', async () => {
  const source = new MockRem({ id: 'source' });
  const selected = new MockRem({ id: 'selected', type: RemType.PORTAL, members: [source] });
  const skipped = new MockRem({ id: 'skipped', type: RemType.PORTAL, members: [source] });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [selected, skipped, source] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'calibration',
    priorityPortalIds: ['selected'],
  });
  assert.deepEqual(Object.keys(snapshot.portals), ['selected']);
  assert.equal(snapshot.capture.scope.expected_portal_count, 1);
  assert.equal(snapshot.converter_projection.portal_snapshots.selected, undefined);
  assert.equal(snapshot.portals.selected.membership.order_validation, 'unverified');
});

test('optional collapse and position failures do not invalidate migration evidence', async () => {
  const source = new MockRem({
    id: 'source',
    hidden: 'included',
    collapseThrows: true,
    positionThrows: true,
  });
  const portal = new MockRem({ id: 'portal', type: RemType.PORTAL, members: [source] });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [portal, source] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'complete',
    ordinaryOrderValidated: true,
    visibilitySemanticsValidated: true,
  });
  assert.equal(snapshot.portals.portal.migration.complete, true);
  assert.equal(snapshot.portals.portal.diagnostics.complete, false);
  assert.equal(snapshot.capture.migration_complete, true);
  assert.equal(snapshot.capture.diagnostics_complete, false);
});

test('rejects an unexpected hidden getter runtime value', async () => {
  const source = new MockRem({ id: 'source', hidden: 'surprise' });
  const portal = new MockRem({ id: 'portal', type: RemType.PORTAL, members: [source] });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [portal, source] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'complete',
    ordinaryOrderValidated: true,
    visibilitySemanticsValidated: true,
  });
  assert.equal(snapshot.portals.portal.visibility.runtime_values_valid, false);
  assert.equal(snapshot.capture.migration_complete, false);
  assert.equal(snapshot.converter_projection.visibility_overrides.portal, undefined);
  assert.ok(snapshot.errors.some((error) => error.operation.endsWith('.runtime-value')));
});

test('detects a knowledge-base switch and removes converter projections', async () => {
  const source = new MockRem({ id: 'source', hidden: 'included' });
  const portal = new MockRem({ id: 'portal', type: RemType.PORTAL, members: [source] });
  let calls = 0;
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: {
      getCurrentKnowledgeBaseData: async () => ({
        _id: calls++ === 0 ? 'kb-start' : 'kb-other',
        name: 'KB',
      }),
    },
    rem: { getAll: async () => [portal, source] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'complete',
    ordinaryOrderValidated: true,
    visibilitySemanticsValidated: true,
  });
  assert.equal(snapshot.capture.knowledgebase_consistent, false);
  assert.equal(snapshot.capture.migration_complete, false);
  assert.deepEqual(snapshot.converter_projection.portal_snapshots, {});
  assert.deepEqual(snapshot.converter_projection.visibility_overrides, {});
});

test('classifies only requested system-definition candidates with positive predicates', async () => {
  const system = new MockRem({ id: 'system', powerup: true });
  const ordinary = new MockRem({ id: 'ordinary' });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [system, ordinary] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'complete',
    systemDefinitionRecordIds: ['system'],
  });
  assert.deepEqual(Object.keys(snapshot.classifications.system_definition.states), ['system']);
  assert.equal(snapshot.classifications.system_definition.states.system.is_powerup, true);
  assert.equal(snapshot.classifications.system_definition.states.ordinary, undefined);
});
