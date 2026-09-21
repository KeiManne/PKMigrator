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
  childRems?: MockRem[];
  childrenThrows?: boolean;
  childCalls?: string[];
  text?: RichTextInterface;
  portalType?: number;
  portalTypeThrows?: boolean;
  members?: MockRem[];
  context?: MockRem[];
  contextThrows?: boolean;
  hidden?: HiddenState | string;
  hiddenRuntime?: HiddenState | string | undefined;
  collapsed?: boolean;
  collapseThrows?: boolean;
  positionThrows?: boolean;
  position?: number;
  visiblePosition?: number;
  backlinkTarget?: MockRem;
  backlinkRichText?: RichTextInterface | null;
  backlinkRichTextThrows?: boolean;
  backlinks?: MockRem[];
  document?: boolean;
  folder?: boolean;
  powerup?: boolean;
  slotCalls?: string[];
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

  async getPortalType(): Promise<number | undefined> {
    if (this.options.portalTypeThrows) throw new Error('portal type unavailable');
    return this.options.portalType;
  }
  async getChildrenRem(): Promise<MockRem[]> {
    this.options.childCalls?.push(this._id);
    if (this.options.childrenThrows) throw new Error('children unavailable');
    return this.options.childRems ?? [];
  }
  async getPortalDirectlyIncludedRem(): Promise<MockRem[]> {
    return this.options.members ?? [];
  }
  async allRemInDocumentOrPortal(): Promise<MockRem[]> {
    if (this.options.contextThrows) throw new Error('context unavailable');
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
    if ('hiddenRuntime' in this.options) {
      return this.options.hiddenRuntime as HiddenState | undefined;
    }
    return (this.options.hidden ?? 'none') as HiddenState;
  }
  async getPowerupPropertyAsRichText(_powerup: string, slot: string): Promise<RichTextInterface> {
    this.options.slotCalls?.push(`rich:${slot}`);
    if (slot === 'AutomaticBacklinkSearchPortalFor') {
      if (this.options.backlinkRichTextThrows) throw new Error('backlink target unavailable');
      if ('backlinkRichText' in this.options) {
        return this.options.backlinkRichText as RichTextInterface;
      }
      return this.options.backlinkTarget
        ? ([{ i: 'q', _id: this.options.backlinkTarget._id }] as RichTextInterface)
        : [];
    }
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
  const result = new MockRem({
    id: 'search-result',
    parent: 'source-root',
    hidden: 'root',
    position: 0,
    visiblePosition: 0,
  });
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

test('normalizes a successful undefined hidden result to none while preserving the wire value', async () => {
  const source = new MockRem({
    id: 'source',
    hiddenRuntime: undefined,
    position: 0,
    visiblePosition: 0,
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

  assert.equal(snapshot.portals.portal.visibility.states.source, 'none');
  assert.equal(snapshot.portals.portal.visibility.raw_states.source, 'undefined');
  assert.equal(snapshot.portals.portal.visibility.complete, true);
  assert.ok(
    !snapshot.errors.some(
      (error) => error.operation === 'Rem.getHiddenExplicitlyIncludedState',
    ),
  );
});

test('normalizes only recognized media-object URL prefixes in comparable rich-text fingerprints', async () => {
  const remote = new MockRem({
    id: 'remote',
    text: [
      {
        i: 'i',
        url: 'https://remnote-user-data.s3.amazonaws.com/folder/asset.png?token=literal',
        title: 'https://remnote-user-data.s3.amazonaws.com/title-must-stay-literal',
      },
    ] as RichTextInterface,
  });
  const local = new MockRem({
    id: 'local',
    text: [
      {
        i: 'i',
        url: '%LOCAL_FILE%folder/asset.png?token=literal',
        title: 'https://remnote-user-data.s3.amazonaws.com/title-must-stay-literal',
      },
    ] as RichTextInterface,
  });
  const nonMedia = new MockRem({
    id: 'non-media',
    text: [
      { i: 'm', url: '%LOCAL_FILE%folder/asset.png?token=literal' },
    ] as RichTextInterface,
  });
  const changedTitle = new MockRem({
    id: 'changed-title',
    text: [
      {
        i: 'i',
        url: '%LOCAL_FILE%folder/asset.png?token=literal',
        title: '%LOCAL_FILE%title-must-stay-literal',
      },
    ] as RichTextInterface,
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [remote, local, nonMedia, changedTitle] },
  };

  const snapshot = await buildSnapshot(plugin, () => undefined, { mode: 'complete' });
  const remoteFingerprint = snapshot.records.remote.export_comparable_rich_text_fingerprint;
  assert.equal(remoteFingerprint, snapshot.records.local.export_comparable_rich_text_fingerprint);
  assert.notEqual(
    remoteFingerprint,
    snapshot.records['non-media'].export_comparable_rich_text_fingerprint,
  );
  assert.notEqual(
    remoteFingerprint,
    snapshot.records['changed-title'].export_comparable_rich_text_fingerprint,
  );
  assert.match(remoteFingerprint, /^fnv1a64-canonical-richtext-v2-media-url:/);
  assert.equal(
    snapshot.capture.export_comparison.media_url_normalization.sentinel,
    '%REMNOTE_ASSET%',
  );
});

test('repairs only bulk child-membership mismatches with a parent-consistent getChildrenRem result', async () => {
  const childCalls: string[] = [];
  const kept = new MockRem({ id: 'kept', parent: 'filtered-parent' });
  const omitted = new MockRem({ id: 'omitted', parent: 'filtered-parent' });
  const filteredParent = new MockRem({
    id: 'filtered-parent',
    children: ['kept'],
    childRems: [omitted, kept],
    childCalls,
  });
  const completeChild = new MockRem({ id: 'complete-child', parent: 'complete-parent' });
  const completeParent = new MockRem({
    id: 'complete-parent',
    children: ['complete-child'],
    childRems: [],
    childCalls,
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: {
      getAll: async () => [
        filteredParent,
        kept,
        omitted,
        completeParent,
        completeChild,
      ],
    },
  };

  const snapshot = await buildSnapshot(plugin, () => undefined, { mode: 'complete' });
  assert.deepEqual(snapshot.records['filtered-parent'].bulk_child_ids, ['kept']);
  assert.deepEqual(snapshot.records['filtered-parent'].child_ids, ['omitted', 'kept']);
  assert.equal(snapshot.records['filtered-parent'].child_ids_source, 'getChildrenRem');
  assert.equal(snapshot.records['filtered-parent'].child_ids_probe, 'verified');
  assert.equal(snapshot.records['complete-parent'].child_ids_probe, 'not-needed');
  assert.deepEqual(childCalls, ['filtered-parent']);
  assert.deepEqual(snapshot.capture.export_comparison.child_membership_probe, {
    complete: true,
    mismatch_parent_count: 1,
    attempted: 1,
    verified: 1,
    failed: 0,
    skipped_by_limit: 0,
    method: 'Rem.getChildrenRem',
  });
});

test('retains bulk children and marks structural evidence incomplete when getChildrenRem fails', async () => {
  const child = new MockRem({ id: 'child', parent: 'parent' });
  const parent = new MockRem({ id: 'parent', children: [], childrenThrows: true });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [parent, child] },
  };

  const snapshot = await buildSnapshot(plugin, () => undefined, { mode: 'complete' });
  assert.deepEqual(snapshot.records.parent.child_ids, []);
  assert.deepEqual(snapshot.records.parent.bulk_child_ids, []);
  assert.equal(snapshot.records.parent.child_ids_source, 'bulk');
  assert.equal(snapshot.records.parent.child_ids_probe, 'failed');
  assert.equal(snapshot.capture.export_comparison.child_membership_probe.complete, false);
  assert.equal(snapshot.capture.diagnostics_complete, false);
  assert.ok(snapshot.errors.some((error) => error.operation === 'Rem.getChildrenRem'));
});

test('derives flat search roots from root state and visible sibling positions', async () => {
  const slotCalls: string[] = [];
  const first = new MockRem({
    id: 'first',
    hidden: 'root',
    position: 4,
    visiblePosition: 0,
  });
  const descendant = new MockRem({
    id: 'descendant',
    parent: 'second',
    hiddenRuntime: undefined,
    position: 0,
    visiblePosition: 0,
  });
  const second = new MockRem({
    id: 'second',
    children: ['descendant'],
    hidden: 'root',
    position: 2,
    visiblePosition: 1,
  });
  const search = new MockRem({
    id: 'search',
    type: RemType.PORTAL,
    portalType: PORTAL_TYPE.SEARCH_PORTAL,
    members: [second, descendant, first],
    context: [second, descendant, first],
    slotCalls,
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [search, first, second, descendant] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'complete',
    searchOrderValidated: true,
    visibilitySemanticsValidated: true,
  });

  assert.deepEqual(snapshot.portals.search.membership.member_ids, [
    'second',
    'descendant',
    'first',
  ]);
  assert.deepEqual(snapshot.portals.search.automatic_view?.root_result_ids, [
    'first',
    'second',
  ]);
  assert.equal(snapshot.portals.search.automatic_view?.root_result_complete, true);
  assert.deepEqual(snapshot.converter_projection.portal_snapshots.search.members, [
    'first',
    'second',
  ]);
  assert.deepEqual(slotCalls, [
    'rich:Query',
    'rich:Filter',
    'rich:DontIncludeNestedDescendants',
    'rich:AutomaticBacklinkSearchPortalFor',
  ]);
});

test('distinguishes default ordinary portal type from a failed portal type read', async () => {
  const ordinary = new MockRem({ id: 'ordinary', type: RemType.PORTAL });
  const failed = new MockRem({ id: 'failed', type: RemType.PORTAL, portalTypeThrows: true });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [ordinary, failed] },
  };

  const snapshot = await buildSnapshot(plugin, () => undefined, { mode: 'complete' });
  assert.equal(snapshot.portals.ordinary.portal_type, PORTAL_TYPE.PORTAL);
  assert.equal(snapshot.portals.ordinary.portal_type_raw, 'undefined');
  assert.equal(snapshot.portals.ordinary.portal_type_name, 'portal');
  assert.equal(snapshot.portals.failed.portal_type, null);
  assert.equal(snapshot.portals.failed.portal_type_raw, 'unknown');
  assert.ok(snapshot.errors.some((error) => error.operation === 'Rem.getPortalType'));
});

test('parses backlink target references and preserves absent and ambiguous rich text states', async () => {
  const target = new MockRem({ id: 'target' });
  const single = new MockRem({
    id: 'single',
    type: RemType.PORTAL,
    portalType: PORTAL_TYPE.SEARCH_PORTAL,
    backlinkTarget: target,
  });
  const absentUndefined = new MockRem({
    id: 'absent-undefined', type: RemType.PORTAL, portalType: PORTAL_TYPE.SEARCH_PORTAL,
    backlinkRichText: undefined as unknown as RichTextInterface,
  });
  const absentNull = new MockRem({
    id: 'absent-null', type: RemType.PORTAL, portalType: PORTAL_TYPE.SEARCH_PORTAL,
    backlinkRichText: null,
  });
  const absentEmpty = new MockRem({
    id: 'absent-empty', type: RemType.PORTAL, portalType: PORTAL_TYPE.SEARCH_PORTAL,
    backlinkRichText: [],
  });
  const ambiguous = new MockRem({
    id: 'ambiguous', type: RemType.PORTAL, portalType: PORTAL_TYPE.SEARCH_PORTAL,
    backlinkRichText: [{ i: 'q', _id: 'one' }, { i: 'q', _id: 'two' }] as RichTextInterface,
  });
  const malformed = new MockRem({
    id: 'malformed', type: RemType.PORTAL, portalType: PORTAL_TYPE.SEARCH_PORTAL,
    backlinkRichText: { i: 'q', _id: 'one' } as unknown as RichTextInterface,
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [single, absentUndefined, absentNull, absentEmpty, ambiguous, malformed, target] },
  };

  const snapshot = await buildSnapshot(plugin, () => undefined, { mode: 'complete' });
  assert.equal(snapshot.portals.single.automatic_view?.backlink_target_id, 'target');
  assert.equal(snapshot.portals.single.automatic_view?.backlink_target_resolution, 'single-reference');
  for (const id of ['absent-undefined', 'absent-null', 'absent-empty']) {
    assert.equal(snapshot.portals[id].automatic_view?.backlink_target_id, null);
    assert.equal(snapshot.portals[id].automatic_view?.backlink_target_resolution, 'absent');
  }
  assert.equal(snapshot.portals.ambiguous.automatic_view?.backlink_target_resolution, 'ambiguous');
  assert.equal(snapshot.portals.ambiguous.automatic_view?.complete, false);
  assert.ok(snapshot.errors.some((error) =>
    error.operation === 'SearchPortal.AutomaticBacklinkSearchPortalFor.ambiguous'));
  assert.equal(snapshot.portals.malformed.automatic_view?.backlink_target_resolution, 'ambiguous');
  assert.ok(snapshot.errors.some((error) =>
    error.operation === 'SearchPortal.AutomaticBacklinkSearchPortalFor.runtime-value'));
});

test('distinguishes unavailable and failed backlink rich text reads', async () => {
  const unavailable = new MockRem({
    id: 'unavailable', type: RemType.PORTAL, portalType: PORTAL_TYPE.SEARCH_PORTAL,
  });
  Object.assign(unavailable, { getPowerupPropertyAsRichText: undefined });
  const failed = new MockRem({
    id: 'failed', type: RemType.PORTAL, portalType: PORTAL_TYPE.SEARCH_PORTAL,
    backlinkRichTextThrows: true,
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [unavailable, failed] },
  };

  const snapshot = await buildSnapshot(plugin, () => undefined, { mode: 'complete' });
  assert.equal(snapshot.portals.unavailable.automatic_view?.backlink_target_resolution, 'method-unavailable');
  assert.equal(snapshot.portals.failed.automatic_view?.backlink_target_resolution, 'failed');
});

test('blocks derived search projection when root positions are duplicated', async () => {
  const first = new MockRem({ id: 'first', hidden: 'root', visiblePosition: 0 });
  const second = new MockRem({ id: 'second', hidden: 'root', visiblePosition: 0 });
  const search = new MockRem({
    id: 'search',
    type: RemType.PORTAL,
    portalType: PORTAL_TYPE.SEARCH_PORTAL,
    members: [first, second],
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [search, first, second] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'complete',
    searchOrderValidated: true,
    visibilitySemanticsValidated: true,
  });

  assert.equal(snapshot.portals.search.automatic_view?.root_result_complete, false);
  assert.equal(snapshot.portals.search.automatic_view?.root_result_ids, null);
  assert.equal(snapshot.converter_projection.portal_snapshots.search, undefined);
  assert.ok(snapshot.errors.some((error) => error.operation === 'search-root-result-order'));
});

test('captures tab_included but keeps search projection closed until its meaning is calibrated', async () => {
  const tab = new MockRem({ id: 'tab', hidden: 'tab_included', visiblePosition: 0 });
  const search = new MockRem({
    id: 'search',
    type: RemType.PORTAL,
    portalType: PORTAL_TYPE.SEARCH_PORTAL,
    members: [tab],
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [search, tab] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'complete',
    searchOrderValidated: true,
    visibilitySemanticsValidated: true,
  });

  assert.equal(snapshot.portals.search.visibility.states.tab, 'tab_included');
  assert.equal(snapshot.portals.search.visibility.raw_states.tab, 'tab_included');
  assert.equal(snapshot.portals.search.automatic_view?.root_result_complete, false);
  assert.equal(snapshot.converter_projection.portal_snapshots.search, undefined);
  assert.ok(
    snapshot.errors.some((error) => error.operation === 'visibility-tab-included-unvalidated'),
  );
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

test('nested search contexts fail closed and preserve runtime Rems absent from bulk inventory', async () => {
  const bulkSource = new MockRem({ id: 'source', hidden: 'none' });
  const runtimeSource = new MockRem({ id: 'source', hidden: 'hidden' });
  const virtualContext = new MockRem({
    id: 'virtual-context',
    type: RemType.PORTAL,
    parent: 'search',
    members: [runtimeSource],
  });
  const search = new MockRem({
    id: 'search',
    type: RemType.PORTAL,
    portalType: PORTAL_TYPE.SEARCH_PORTAL,
    members: [runtimeSource],
    context: [runtimeSource, virtualContext],
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [search, bulkSource] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'calibration',
    priorityPortalIds: ['search'],
    searchOrderValidated: true,
    visibilitySemanticsValidated: true,
  });
  assert.equal(snapshot.portals.search.nested_contexts.status, 'detected-unresolved');
  assert.deepEqual(snapshot.portals.search.nested_contexts.detected_ids, ['virtual-context']);
  assert.equal(snapshot.portals.search.visibility.complete, false);
  assert.equal(snapshot.portals.search.migration.complete, false);
  assert.equal(snapshot.converter_projection.portal_snapshots.search, undefined);
  assert.equal(snapshot.converter_projection.visibility_overrides.search, undefined);
  assert.equal(snapshot.portals.search.automatic_view?.result_interpretation, 'direct-members-unmapped-nested-contexts');
  assert.equal(snapshot.runtime_returned_records['virtual-context'].bulk_present, false);
  assert.equal(snapshot.portals.search.visibility.states.source, 'hidden');
  assert.ok(snapshot.runtime_returned_records['virtual-context'].first_seen_via.some((value) => value.endsWith(':context-array')));
});

test('failed search context discovery suppresses otherwise validated flat projections', async () => {
  const result = new MockRem({
    id: 'result',
    hidden: 'included',
    position: 0,
    visiblePosition: 0,
  });
  const search = new MockRem({
    id: 'search',
    type: RemType.PORTAL,
    portalType: PORTAL_TYPE.SEARCH_PORTAL,
    members: [result],
    contextThrows: true,
  });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: { getAll: async () => [search, result] },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'calibration',
    priorityPortalIds: ['search'],
    searchOrderValidated: true,
    visibilitySemanticsValidated: true,
  });

  assert.equal(snapshot.portals.search.membership.complete, true);
  assert.equal(snapshot.portals.search.context.complete, false);
  assert.equal(snapshot.portals.search.nested_contexts.status, 'discovery-incomplete');
  assert.equal(snapshot.portals.search.nested_contexts.projection_safe, false);
  assert.equal(snapshot.portals.search.visibility.complete, false);
  assert.equal(snapshot.portals.search.migration.complete, false);
  assert.equal(snapshot.portals.search.automatic_view?.complete, false);
  assert.equal(
    snapshot.portals.search.automatic_view?.result_interpretation,
    'direct-members-context-discovery-incomplete',
  );
  assert.equal(snapshot.converter_projection.portal_snapshots.search, undefined);
  assert.equal(snapshot.converter_projection.visibility_overrides.search, undefined);
  assert.ok(
    snapshot.errors.some((error) => error.operation === 'search-context-discovery-incomplete'),
  );
});

test('per-portal raw probe seeds are diagnostic, prioritized, and may resolve through findOne', async () => {
  const seed = new MockRem({ id: 'raw-hidden-root', hidden: 'hidden' });
  const portal = new MockRem({ id: 'portal', type: RemType.PORTAL, members: [] });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: {
      getAll: async () => [portal],
      findOne: async (id) => (id === seed._id ? seed : undefined),
    },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'calibration',
    priorityPortalIds: ['portal'],
    maxContextProbes: 1,
    maxProbesPerPortal: 1,
    portalProbeSeedsByPortal: { portal: [seed._id] },
  });
  assert.deepEqual(snapshot.portals.portal.visibility.probe_seeds, {
    requested_ids: ['raw-hidden-root'],
    resolved_ids: ['raw-hidden-root'],
    missing_ids: [],
    supplemental_ids: ['raw-hidden-root'],
    establishes_complete_scope: false,
  });
  assert.equal(snapshot.portals.portal.visibility.states['raw-hidden-root'], 'hidden');
  assert.equal(snapshot.portals.portal.visibility.complete, false);
  assert.equal(snapshot.runtime_returned_records['raw-hidden-root'].bulk_present, false);
});

test('a rejected findOne lookup is recorded without aborting the capture', async () => {
  const portal = new MockRem({ id: 'portal', type: RemType.PORTAL, members: [] });
  const plugin: SnapshotPlugin = {
    app: { waitForInitialSync: async () => undefined, getPlatform: async () => 'web' },
    kb: { getCurrentKnowledgeBaseData: async () => ({ _id: 'kb', name: 'KB' }) },
    rem: {
      getAll: async () => [portal],
      findOne: async () => { throw new Error('lookup rejected'); },
    },
  };
  const snapshot = await buildSnapshot(plugin, () => undefined, {
    mode: 'calibration',
    priorityPortalIds: ['portal'],
    portalProbeSeedsByPortal: { portal: ['missing-seed'] },
  });
  assert.deepEqual(snapshot.portals.portal.visibility.probe_seeds.missing_ids, ['missing-seed']);
  assert.ok(snapshot.errors.some((error) => error.operation === 'RemNamespace.findOne'));
  assert.ok(snapshot.errors.some((error) => error.operation === 'resolve-portal-candidate'));
});
