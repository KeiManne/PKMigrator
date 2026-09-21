import type { RichTextInterface } from '@remnote/plugin-sdk';

const REM_TYPE_PORTAL = 6;
const PORTAL_TYPE = {
  PORTAL: 0,
  EMBEDDED_QUEUE: 2,
  SCAFFOLD: 3,
  SEARCH_PORTAL: 4,
} as const;

export const SNAPSHOT_SCHEMA_VERSION = 'remnote-migration-snapshot/v1' as const;
export const SDK_VERSION = '0.0.46';
export const PLUGIN_VERSION = '0.1.2';

export type Progress = (message: string) => void;
export type HiddenState = 'hidden' | 'included' | 'root' | 'tab_included' | 'none';
export type HiddenRuntimeValue = HiddenState | 'undefined' | 'unknown';
export type CaptureMode = 'calibration' | 'complete';

export interface CaptureOptions {
  signal?: AbortSignal;
  mode?: CaptureMode;
  priorityPortalIds?: string[];
  maxContextProbes?: number;
  maxProbesPerPortal?: number;
  maxDetailedMembersPerPortal?: number;
  classificationRecordIds?: string[];
  systemDefinitionRecordIds?: string[];
  detailRecordIds?: string[];
  maxAutomaticTopLevelClassifications?: number;
  ordinaryOrderValidated?: boolean;
  searchOrderValidated?: boolean;
  visibilitySemanticsValidated?: boolean;
  expectedHiddenByPortal?: Record<string, string[]>;
  portalProbeSeedsByPortal?: Record<string, string[]>;
  richTextFingerprintCalibrated?: boolean;
  childOrderCalibrated?: boolean;
}

export interface SnapshotRem {
  readonly _id: string;
  readonly createdAt: number;
  readonly localUpdatedAt: number;
  readonly updatedAt: number;
  readonly parent: string | null;
  readonly children: string[] | undefined;
  readonly type: number;
  readonly text: RichTextInterface | undefined;
  readonly backText?: RichTextInterface;
  getChildrenRem?(): Promise<SnapshotRem[]>;
  getPortalType(): Promise<number | undefined>;
  getPortalDirectlyIncludedRem(): Promise<SnapshotRem[]>;
  allRemInDocumentOrPortal(): Promise<SnapshotRem[]>;
  isCollapsed(portalId: string): Promise<boolean>;
  positionAmongstSiblings(portalId?: string): Promise<number | undefined>;
  positionAmongstVisibleSiblings(portalId?: string): Promise<number | undefined>;
  getHiddenExplicitlyIncludedState?: (
    portalId?: string,
  ) => Promise<HiddenState | undefined>;
  getPowerupPropertyAsRichText(powerupCode: string, slotCode: string): Promise<RichTextInterface>;
  remsReferencingThis(): Promise<SnapshotRem[]>;
  isDocument(): Promise<boolean>;
  isFolder(): Promise<boolean>;
  isPowerup(): Promise<boolean>;
  isPowerupEnum(): Promise<boolean>;
  isPowerupPropertyListItem(): Promise<boolean>;
  isPowerupSlot(): Promise<boolean>;
  isPowerupProperty(): Promise<boolean>;
}

export interface SnapshotPlugin {
  app: {
    waitForInitialSync(): Promise<void>;
    getPlatform(): Promise<string>;
  };
  kb: {
    getCurrentKnowledgeBaseData(): Promise<{ _id: string; name: string }>;
  };
  rem: {
    getAll(): Promise<SnapshotRem[]>;
    findOne?(id: string): Promise<SnapshotRem | undefined>;
  };
}

export interface SnapshotError {
  id: string;
  severity: 'warning' | 'error';
  scope: 'capture' | 'record' | 'portal' | 'relation';
  operation: string;
  message: string;
  rem_id?: string;
  portal_id?: string;
}

export interface SourceRecord {
  id: string;
  type: number;
  parent_id: string | null;
  child_ids: string[];
  bulk_child_ids: string[];
  child_ids_source: 'bulk' | 'getChildrenRem';
  child_ids_probe:
    | 'not-needed'
    | 'verified'
    | 'method-unavailable'
    | 'failed'
    | 'inconsistent'
    | 'limit-exceeded';
  text: RichTextInterface | null;
  back_text: RichTextInterface | null;
  created_at: number;
  updated_at: number;
  local_updated_at: number;
  tag_ids: string[];
  tags_complete: boolean;
  detail_level: 'inventory' | 'rich';
  rich_text_fingerprint: string;
  export_comparable_rich_text_fingerprint: string;
}

export interface RuntimeReturnedRecord {
  id: string;
  type: number;
  parent_id: string | null;
  child_ids: string[];
  text: RichTextInterface | null;
  back_text: RichTextInterface | null;
  created_at: number;
  updated_at: number;
  local_updated_at: number;
  bulk_present: boolean;
  first_seen_via: string[];
}

export interface PortalRecord {
  portal_id: string;
  portal_type: number | null;
  portal_type_raw: number | 'undefined' | 'unknown';
  portal_type_contract: {
    source: 'public-host-implementation-and-sdk-enum';
    resolved_undefined_means: 'portal';
  };
  portal_type_name: string;
  membership: {
    complete: boolean;
    ordered: true;
    order_semantics: 'sdk-return-order';
    order_validation: 'operator-ui-validated' | 'unverified';
    method: 'Rem.getPortalDirectlyIncludedRem';
    captured_at: string;
    member_ids: string[];
  };
  context: {
    complete: boolean;
    ordered: false;
    method: 'Rem.allRemInDocumentOrPortal';
    rem_ids: string[];
  };
  visibility: {
    complete: boolean;
    method: 'Rem.getHiddenExplicitlyIncludedState';
    sdk_status: 'typed-but-undocumented';
    candidate_ids: string[];
    states: Record<string, HiddenState | 'unknown'>;
    raw_states: Record<string, HiddenRuntimeValue>;
    runtime_contract: {
      source: 'public-host-implementation-and-live-calibration';
      sdk_declaration_complete: false;
      resolved_undefined_means: 'none';
      tab_included_projection: 'unvalidated';
    };
    runtime_values_valid: boolean;
    semantics_validation: 'operator-ui-validated' | 'unverified';
    expected_hidden_ids: string[];
    expected_hidden_matches: string[];
    expected_hidden_mismatches: string[];
    probe_seeds: {
      requested_ids: string[];
      resolved_ids: string[];
      missing_ids: string[];
      supplemental_ids: string[];
      establishes_complete_scope: false;
    };
  };
  collapsed: {
    complete: boolean;
    method: 'Rem.isCollapsed';
    states: Record<string, boolean>;
  };
  positions: {
    complete: boolean;
    method: 'Rem.positionAmongstSiblings + Rem.positionAmongstVisibleSiblings';
    states: Record<
      string,
      { position: number | null; visible_position: number | null }
    >;
  };
  automatic_view: null | {
    kind: 'search' | 'backlink';
    complete: boolean;
    result_ids: string[];
    result_order: 'sdk-return-order';
    result_interpretation:
      | 'direct-members'
      | 'direct-members-unmapped-nested-contexts'
      | 'direct-members-context-discovery-incomplete';
    root_result_ids: string[] | null;
    root_result_complete: boolean;
    root_result_order: 'visible-sibling-position' | null;
    root_result_blockers: string[];
    backlink_target_rich_text: RichTextInterface | null;
    backlink_target_resolution:
      | 'absent'
      | 'single-reference'
      | 'ambiguous'
      | 'method-unavailable'
      | 'failed';
    backlink_target_id: string | null;
    query: RichTextInterface | null;
    filter: RichTextInterface | null;
    dont_include_nested_descendants: RichTextInterface | null;
    limitation: string;
  };
  nested_contexts: {
    status: 'none-detected' | 'detected-unresolved' | 'discovery-incomplete';
    detected_ids: string[];
    evidence: Record<string, Array<'direct-members' | 'context-array' | 'bulk-child'>>;
    projection_safe: boolean;
    limitation: string;
  };
  migration: {
    membership_complete: boolean;
    order_validated: boolean;
    visibility_complete: boolean;
    visibility_semantics_validated: boolean;
    complete: boolean;
  };
  diagnostics: {
    context_complete: boolean;
    collapsed_complete: boolean;
    positions_complete: boolean;
    complete: boolean;
  };
  error_ids: string[];
}

export interface MigrationSnapshot {
  schema_version: typeof SNAPSHOT_SCHEMA_VERSION;
  capture: {
    mode: CaptureMode;
    knowledgebase_id: string | null;
    knowledgebase_name: string | null;
    knowledgebase_id_at_end: string | null;
    knowledgebase_consistent: boolean;
    started_at: string;
    completed_at: string;
    complete: boolean;
    migration_complete: boolean;
    diagnostics_complete: boolean;
    platform: string | null;
    plugin_version: string;
    sdk_package: '@remnote/plugin-sdk';
    sdk_version: string;
    payload_sha256: string | null;
    limits: {
      max_context_probes: number;
      max_probes_per_portal: number;
      max_detailed_members_per_portal: number;
      max_child_membership_probes: 256;
    };
    scope: {
      expected_portal_count: number;
      processed_portal_count: number;
      requested_portal_ids: string[];
      missing_requested_portal_ids: string[];
    };
    projection_policy: {
      ordinary_order_validated: boolean;
      search_order_validated: boolean;
      visibility_semantics_validated: boolean;
      reason: string;
    };
    export_comparison: {
      structural_fields: ['id', 'parent_id', 'child_ids'];
      child_order_calibrated: boolean;
      child_order_raw_basis: string;
      child_membership_probe: {
        complete: boolean;
        mismatch_parent_count: number;
        attempted: number;
        verified: number;
        failed: number;
        skipped_by_limit: number;
        method: 'Rem.getChildrenRem';
      };
      rich_text_algorithm: 'fnv1a64-canonical-richtext-v2-media-url';
      media_url_normalization: {
        scope: 'rich-text-media-object-url-only';
        media_type: 'i';
        prefixes: ['https://remnote-user-data.s3.amazonaws.com/', '%LOCAL_FILE%'];
        sentinel: '%REMNOTE_ASSET%';
      };
      raw_input_fields: ['key', 'value'];
      sdk_input_fields: ['text', 'backText'];
      calibrated_equivalent: boolean;
      limitation: string;
    };
    provenance: {
      producer: 'PKMigrator Read-Only Snapshot';
      data_location: 'local-download';
      source_mutation: false;
      transactional: false;
      record_inventory_count: number;
      methods: string[];
      documentation: string[];
    };
  };
  records: Record<string, SourceRecord>;
  runtime_returned_records: Record<string, RuntimeReturnedRecord>;
  portals: Record<string, PortalRecord>;
  relations: {
    backlinks: Record<
      string,
      {
        complete: boolean;
        ordered: false;
        method: 'Rem.remsReferencingThis';
        member_ids: string[];
      }
    >;
    tags: {
      complete: false;
      ordered: false;
      method: null;
      members_by_tag: Record<string, never>;
      limitation: string;
    };
  };
  classifications: {
    document_and_folder: {
      complete: boolean;
      method: 'Rem.isDocument + Rem.isFolder';
      scope: 'all-top-level-plus-explicit-candidates';
      requested_ids: string[];
      states: Record<string, { is_document: boolean; is_folder: boolean }>;
      missing_ids: string[];
    };
    system_definition: {
      complete: boolean;
      method: 'Rem.isPowerup + Rem.isPowerupEnum + Rem.isPowerupPropertyListItem + Rem.isPowerupSlot + Rem.isPowerupProperty';
      requested_ids: string[];
      states: Record<
        string,
        {
          is_powerup: boolean;
          is_powerup_enum: boolean;
          is_powerup_property_list_item: boolean;
          is_powerup_slot: boolean;
          is_powerup_property: boolean;
        }
      >;
      missing_ids: string[];
      limitation: string;
    };
  };
  converter_projection: {
    portal_snapshots: Record<string, { evidence: string; members: string[] }>;
    visibility_overrides: Record<
      string,
      { evidence: string; states: Record<string, 'hidden' | 'visible'> }
    >;
  };
  errors: SnapshotError[];
}

const API_METHODS = [
  'App.waitForInitialSync',
  'KnowledgeBase.getCurrentKnowledgeBaseData',
  'RemNamespace.getAll',
  'RemNamespace.findOne',
  'Rem.getChildrenRem',
  'Rem.getPortalType',
  'Rem.getPortalDirectlyIncludedRem',
  'Rem.allRemInDocumentOrPortal',
  'Rem.getHiddenExplicitlyIncludedState',
  'Rem.isCollapsed',
  'Rem.getPowerupPropertyAsRichText',
  'Rem.remsReferencingThis',
  'Rem.isDocument',
  'Rem.isFolder',
  'Rem.isPowerup',
  'Rem.isPowerupEnum',
  'Rem.isPowerupPropertyListItem',
  'Rem.isPowerupSlot',
  'Rem.isPowerupProperty',
];

function cloneRichText(value: RichTextInterface | undefined): RichTextInterface | null {
  if (value === undefined) return null;
  return JSON.parse(JSON.stringify(value)) as RichTextInterface;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException('Snapshot capture cancelled.', 'AbortError');
}

function rethrowAbort(error: unknown): void {
  if (error instanceof DOMException && error.name === 'AbortError') throw error;
}

function fnv1a64(value: string): string {
  let hash = 0xcbf29ce484222325n;
  for (let index = 0; index < value.length; index++) {
    hash ^= BigInt(value.charCodeAt(index));
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return hash.toString(16).padStart(16, '0');
}

function canonicalJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalJsonValue);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
        .map(([key, item]) => [key, canonicalJsonValue(item)]),
    );
  }
  return value;
}

function normalizeComparableMediaUrls(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(normalizeComparableMediaUrls);
  if (value !== null && typeof value === 'object') {
    const source = value as Record<string, unknown>;
    const normalized = Object.fromEntries(
      Object.entries(source).map(([key, item]) => [key, normalizeComparableMediaUrls(item)]),
    );
    if (
      source.i === 'i' &&
      Object.prototype.hasOwnProperty.call(source, 'url') &&
      typeof source.url === 'string'
    ) {
      for (const prefix of [
        'https://remnote-user-data.s3.amazonaws.com/',
        '%LOCAL_FILE%',
      ] as const) {
        if (source.url.startsWith(prefix)) {
          normalized.url = `%REMNOTE_ASSET%${source.url.slice(prefix.length)}`;
          break;
        }
      }
    }
    return normalized;
  }
  return value;
}

async function mapLimit<T, R>(
  values: readonly T[],
  limit: number,
  fn: (value: T, index: number) => Promise<R>,
): Promise<R[]> {
  const output = new Array<R>(values.length);
  let cursor = 0;
  async function worker(): Promise<void> {
    while (true) {
      const index = cursor++;
      if (index >= values.length) return;
      output[index] = await fn(values[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, values.length) }, worker));
  return output;
}

function portalTypeName(value: number | null): string {
  if (value === PORTAL_TYPE.PORTAL) return 'portal';
  if (value === PORTAL_TYPE.EMBEDDED_QUEUE) return 'embedded_queue';
  if (value === PORTAL_TYPE.SCAFFOLD) return 'scaffold';
  if (value === PORTAL_TYPE.SEARCH_PORTAL) return 'search_portal';
  return value === null ? 'unknown' : `unknown_${value}`;
}

function descendantCandidates(rootIds: string[], records: Record<string, SourceRecord>): string[] {
  const seen = new Set<string>();
  const pending = [...rootIds].reverse();
  while (pending.length) {
    const id = pending.pop()!;
    if (seen.has(id)) continue;
    seen.add(id);
    const children = records[id]?.child_ids ?? [];
    for (let i = children.length - 1; i >= 0; i--) pending.push(children[i]);
  }
  return [...seen];
}

async function sha256Hex(value: string): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const digest = await globalThis.crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(value),
  );
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function buildSnapshot(
  plugin: SnapshotPlugin,
  onProgress: Progress = () => undefined,
  options: CaptureOptions = {},
): Promise<MigrationSnapshot> {
  const mode = options.mode ?? 'calibration';
  const maxContextProbes = options.maxContextProbes ??
    (mode === 'complete' ? 250_000 : 10_000);
  const maxProbesPerPortal = options.maxProbesPerPortal ??
    (mode === 'complete' ? 100_000 : 10_000);
  const maxDetailedMembersPerPortal =
    options.maxDetailedMembersPerPortal ?? (mode === 'calibration' ? 100 : 0);
  const maxAutomaticTopLevelClassifications =
    options.maxAutomaticTopLevelClassifications ?? 2_000;
  const startedAt = new Date().toISOString();
  const errors: SnapshotError[] = [];
  let errorCounter = 0;
  const addError = (
    error: Omit<SnapshotError, 'id'>,
  ): string => {
    const id = `snapshot-error-${++errorCounter}`;
    errors.push({ id, ...error });
    return id;
  };

  onProgress('Waiting for RemNote initial sync…');
  throwIfAborted(options.signal);
  try {
    await plugin.app.waitForInitialSync();
    throwIfAborted(options.signal);
  } catch (error) {
    rethrowAbort(error);
    addError({
      severity: 'error',
      scope: 'capture',
      operation: 'App.waitForInitialSync',
      message: errorMessage(error),
    });
  }

  let kbId: string | null = null;
  let kbName: string | null = null;
  try {
    const kb = await plugin.kb.getCurrentKnowledgeBaseData();
    kbId = kb._id;
    kbName = kb.name;
  } catch (error) {
    rethrowAbort(error);
    addError({
      severity: 'error',
      scope: 'capture',
      operation: 'KnowledgeBase.getCurrentKnowledgeBaseData',
      message: errorMessage(error),
    });
  }

  let platform: string | null = null;
  try {
    platform = await plugin.app.getPlatform();
  } catch (error) {
    rethrowAbort(error);
    addError({
      severity: 'warning',
      scope: 'capture',
      operation: 'App.getPlatform',
      message: errorMessage(error),
    });
  }

  onProgress('Reading Rem records…');
  throwIfAborted(options.signal);
  let allRem: SnapshotRem[] = [];
  try {
    allRem = (await plugin.rem.getAll()).slice().sort((a, b) => a._id.localeCompare(b._id));
    throwIfAborted(options.signal);
  } catch (error) {
    rethrowAbort(error);
    addError({
      severity: 'error',
      scope: 'capture',
      operation: 'RemNamespace.getAll',
      message: errorMessage(error),
    });
  }
  const remById = new Map(allRem.map((rem) => [rem._id, rem]));
  const runtimeRemById = new Map<string, SnapshotRem>();
  const runtimeReturnedRecords: Record<string, RuntimeReturnedRecord> = {};
  const rememberRuntimeRem = (rem: SnapshotRem, via: string): void => {
    runtimeRemById.set(rem._id, rem);
    const existing = runtimeReturnedRecords[rem._id];
    if (existing) {
      if (!existing.first_seen_via.includes(via)) existing.first_seen_via.push(via);
      return;
    }
    runtimeReturnedRecords[rem._id] = {
      id: rem._id,
      type: rem.type,
      parent_id: rem.parent,
      child_ids: [...(rem.children ?? [])],
      text: cloneRichText(rem.text),
      back_text: cloneRichText(rem.backText),
      created_at: rem.createdAt,
      updated_at: rem.updatedAt,
      local_updated_at: rem.localUpdatedAt,
      bulk_present: remById.has(rem._id),
      first_seen_via: [via],
    };
  };
  const resolveRuntimeRem = async (id: string, via: string): Promise<SnapshotRem | undefined> => {
    const runtimeRem = runtimeRemById.get(id);
    if (runtimeRem) {
      rememberRuntimeRem(runtimeRem, via);
      return runtimeRem;
    }
    const bulkRem = remById.get(id);
    if (bulkRem) return bulkRem;
    if (!plugin.rem.findOne) return undefined;
    try {
      const found = await plugin.rem.findOne(id);
      throwIfAborted(options.signal);
      if (found) rememberRuntimeRem(found, via);
      return found;
    } catch (error) {
      rethrowAbort(error);
      addError({
        severity: 'warning',
        scope: 'record',
        operation: 'RemNamespace.findOne',
        rem_id: id,
        message: `${via}: ${errorMessage(error)}`,
      });
      return undefined;
    }
  };

  onProgress(`Indexing ${allRem.length} source records from the bulk response…`);
  const recordEntries = allRem.map((rem): [string, SourceRecord] => {
    const richTextPair = [rem.text ?? null, rem.backText ?? null];
    const richFingerprint = fnv1a64(JSON.stringify(richTextPair));
    const comparableFingerprint = fnv1a64(
      JSON.stringify(canonicalJsonValue(normalizeComparableMediaUrls(richTextPair))),
    );
    return [
      rem._id,
      {
        id: rem._id,
        type: rem.type,
        parent_id: rem.parent,
        child_ids: [...(rem.children ?? [])],
        bulk_child_ids: [...(rem.children ?? [])],
        child_ids_source: 'bulk',
        child_ids_probe: 'not-needed',
        text: null,
        back_text: null,
        created_at: rem.createdAt,
        updated_at: rem.updatedAt,
        local_updated_at: rem.localUpdatedAt,
        tag_ids: [],
        tags_complete: false,
        detail_level: 'inventory',
        rich_text_fingerprint: `fnv1a64-json:${richFingerprint}`,
        export_comparable_rich_text_fingerprint: `fnv1a64-canonical-richtext-v2-media-url:${comparableFingerprint}`,
      },
    ];
  });
  const records = Object.fromEntries(recordEntries);
  const maxChildMembershipProbes = 256 as const;
  const expectedChildrenByParent = new Map<string, string[]>();
  for (const rem of allRem) {
    if (!rem.parent || !remById.has(rem.parent)) continue;
    const children = expectedChildrenByParent.get(rem.parent) ?? [];
    children.push(rem._id);
    expectedChildrenByParent.set(rem.parent, children);
  }
  const childMembershipMismatchIds = allRem
    .filter((rem) => {
      const bulkIds = rem.children ?? [];
      const expectedIds = expectedChildrenByParent.get(rem._id) ?? [];
      const bulkSet = new Set(bulkIds);
      const expectedSet = new Set(expectedIds);
      return (
        bulkSet.size !== bulkIds.length ||
        expectedSet.size !== expectedIds.length ||
        bulkSet.size !== expectedSet.size ||
        [...bulkSet].some((id) => !expectedSet.has(id))
      );
    })
    .map((rem) => rem._id);
  const childMembershipProbeIds = childMembershipMismatchIds.slice(0, maxChildMembershipProbes);
  const childMembershipSkippedIds = childMembershipMismatchIds.slice(maxChildMembershipProbes);
  for (const id of childMembershipSkippedIds) records[id].child_ids_probe = 'limit-exceeded';
  if (childMembershipSkippedIds.length > 0) {
    addError({
      severity: 'error',
      scope: 'capture',
      operation: 'Rem.getChildrenRem.probe-limit',
      message: `Skipped ${childMembershipSkippedIds.length} of ${childMembershipMismatchIds.length} child-membership mismatch parent(s); the hard limit is ${maxChildMembershipProbes}.`,
    });
  }

  let verifiedChildMembershipProbes = 0;
  if (childMembershipProbeIds.length > 0) {
    onProgress(
      `Reconciling ${childMembershipProbeIds.length.toLocaleString()} bulk child-membership mismatch parent(s)…`,
    );
  }
  await mapLimit(childMembershipProbeIds, 4, async (parentId) => {
    throwIfAborted(options.signal);
    const parent = remById.get(parentId)!;
    const record = records[parentId];
    if (typeof parent.getChildrenRem !== 'function') {
      record.child_ids_probe = 'method-unavailable';
      addError({
        severity: 'error',
        scope: 'record',
        operation: 'Rem.getChildrenRem.unavailable',
        rem_id: parentId,
        message: 'Bulk children disagree with live parent pointers, and getChildrenRem is unavailable.',
      });
      return;
    }
    let returnedChildren: SnapshotRem[];
    try {
      returnedChildren = await parent.getChildrenRem();
      throwIfAborted(options.signal);
    } catch (error) {
      rethrowAbort(error);
      record.child_ids_probe = 'failed';
      addError({
        severity: 'error',
        scope: 'record',
        operation: 'Rem.getChildrenRem',
        rem_id: parentId,
        message: errorMessage(error),
      });
      return;
    }

    const returnedIds = returnedChildren.map((child) => child._id);
    const returnedSet = new Set(returnedIds);
    const expectedIds = expectedChildrenByParent.get(parentId) ?? [];
    const expectedSet = new Set(expectedIds);
    const returnedParentsAgree = returnedChildren.every(
      (child) =>
        child.parent === parentId &&
        remById.get(child._id)?.parent === parentId,
    );
    const membershipConsistent =
      returnedSet.size === returnedIds.length &&
      returnedSet.size === expectedSet.size &&
      [...returnedSet].every((id) => expectedSet.has(id)) &&
      returnedParentsAgree;
    if (!membershipConsistent) {
      record.child_ids_probe = 'inconsistent';
      addError({
        severity: 'error',
        scope: 'record',
        operation: 'Rem.getChildrenRem.inconsistent',
        rem_id: parentId,
        message:
          'getChildrenRem did not return a unique child set exactly matching the complete parent-pointer graph.',
      });
      return;
    }
    record.child_ids = returnedIds;
    record.child_ids_source = 'getChildrenRem';
    record.child_ids_probe = 'verified';
    verifiedChildMembershipProbes += 1;
  });
  const childMembershipProbeComplete =
    childMembershipSkippedIds.length === 0 &&
    verifiedChildMembershipProbes === childMembershipProbeIds.length;
  const tagRelations: MigrationSnapshot['relations']['tags'] = {
    complete: false,
    ordered: false,
    method: null,
    members_by_tag: {},
    limitation:
      'The SDK has no bulk tag-view API. Per-Rem tag calls were intentionally skipped for a large knowledge base; evaluated tag searches remain captured as search portals.',
  };

  const allTopLevelIds = allRem.filter((rem) => rem.parent === null).map((rem) => rem._id);
  const automaticTopLevelIds = allTopLevelIds.slice(0, maxAutomaticTopLevelClassifications);
  const classificationIds = [
    ...new Set([...automaticTopLevelIds, ...(options.classificationRecordIds ?? [])]),
  ].sort();
  let classificationsComplete = allTopLevelIds.length === automaticTopLevelIds.length;
  if (!classificationsComplete) {
    addError({
      severity: 'warning',
      scope: 'capture',
      operation: 'document-folder-classification-limit',
      message: `Classifying ${automaticTopLevelIds.length} of ${allTopLevelIds.length} top-level Rem plus explicit candidates.`,
    });
  }
  const classificationStates: Record<
    string,
    { is_document: boolean; is_folder: boolean }
  > = {};
  const missingClassificationIds: string[] = [];
  onProgress(`Checking ${classificationIds.length} document/folder candidates…`);
  await mapLimit(classificationIds, 12, async (id) => {
    throwIfAborted(options.signal);
    const rem = remById.get(id);
    if (!rem) {
      classificationsComplete = false;
      missingClassificationIds.push(id);
      addError({
        severity: 'error',
        scope: 'record',
        operation: 'resolve-document-folder-candidate',
        rem_id: id,
        message: 'Explicit document/folder candidate was not returned by RemNamespace.getAll.',
      });
      return;
    }
    try {
      const [isDocument, isFolder] = await Promise.all([rem.isDocument(), rem.isFolder()]);
      throwIfAborted(options.signal);
      classificationStates[id] = { is_document: isDocument, is_folder: isFolder };
    } catch (error) {
      rethrowAbort(error);
      classificationsComplete = false;
      addError({
        severity: 'error',
        scope: 'record',
        operation: 'Rem.isDocument/Rem.isFolder',
        rem_id: id,
        message: errorMessage(error),
      });
    }
  });

  const systemDefinitionIds = [...new Set(options.systemDefinitionRecordIds ?? [])].sort();
  const systemDefinitionStates: MigrationSnapshot['classifications']['system_definition']['states'] = {};
  const missingSystemDefinitionIds: string[] = [];
  let systemDefinitionsComplete = true;
  onProgress(`Checking ${systemDefinitionIds.length} system-definition candidates…`);
  await mapLimit(systemDefinitionIds, 12, async (id) => {
    throwIfAborted(options.signal);
    const rem = remById.get(id);
    if (!rem) {
      systemDefinitionsComplete = false;
      missingSystemDefinitionIds.push(id);
      addError({
        severity: 'error',
        scope: 'record',
        operation: 'resolve-system-definition-candidate',
        rem_id: id,
        message: 'Explicit system-definition candidate was not returned by RemNamespace.getAll.',
      });
      return;
    }
    try {
      const [isPowerup, isPowerupEnum, isPowerupPropertyListItem, isPowerupSlot, isPowerupProperty] =
        await Promise.all([
          rem.isPowerup(),
          rem.isPowerupEnum(),
          rem.isPowerupPropertyListItem(),
          rem.isPowerupSlot(),
          rem.isPowerupProperty(),
        ]);
      throwIfAborted(options.signal);
      systemDefinitionStates[id] = {
        is_powerup: isPowerup,
        is_powerup_enum: isPowerupEnum,
        is_powerup_property_list_item: isPowerupPropertyListItem,
        is_powerup_slot: isPowerupSlot,
        is_powerup_property: isPowerupProperty,
      };
    } catch (error) {
      rethrowAbort(error);
      systemDefinitionsComplete = false;
      addError({
        severity: 'error',
        scope: 'record',
        operation: 'Rem.isPowerup/system-definition predicates',
        rem_id: id,
        message: errorMessage(error),
      });
    }
  });

  const portals: Record<string, PortalRecord> = {};
  const backlinkRelations: MigrationSnapshot['relations']['backlinks'] = {};
  const converterProjection: MigrationSnapshot['converter_projection'] = {
    portal_snapshots: {},
    visibility_overrides: {},
  };
  const priorityPortalIds = new Set(options.priorityPortalIds ?? []);
  const resolvedPriorityPortalRems: SnapshotRem[] = [];
  const missingRequestedPortalIds: string[] = [];
  for (const id of [...priorityPortalIds].sort()) {
    const rem = await resolveRuntimeRem(id, 'explicit-portal-lookup');
    if (rem?.type === REM_TYPE_PORTAL) resolvedPriorityPortalRems.push(rem);
    else missingRequestedPortalIds.push(id);
  }
  const allPortalRems = [
    ...new Map(
      [...allRem.filter((rem) => rem.type === REM_TYPE_PORTAL), ...resolvedPriorityPortalRems].map(
        (rem) => [rem._id, rem],
      ),
    ).values(),
  ];
  for (const id of missingRequestedPortalIds) {
    addError({
      severity: 'error',
      scope: 'portal',
      operation: 'resolve-calibration-portal',
      portal_id: id,
      message: 'Requested calibration ID was not resolved as a portal by getAll() or findOne().',
    });
  }
  if (mode === 'calibration' && priorityPortalIds.size === 0) {
    addError({
      severity: 'error',
      scope: 'capture',
      operation: 'calibration-scope',
      message: 'Calibration mode requires at least one portal ID.',
    });
  }
  const portalRems = (mode === 'calibration'
    ? allPortalRems.filter((rem) => priorityPortalIds.has(rem._id))
    : allPortalRems)
    .sort((a, b) =>
      Number(priorityPortalIds.has(b._id)) - Number(priorityPortalIds.has(a._id)) ||
      a._id.localeCompare(b._id),
    );
  onProgress(
    `${mode === 'calibration' ? 'Calibration' : 'Complete migration'} scope: ${portalRems.length} portal${
      portalRems.length === 1 ? '' : 's'
    }, up to ${maxContextProbes.toLocaleString()} context probes…`,
  );
  let remainingContextProbes = maxContextProbes;
  const detailedIds = new Set([
    ...portalRems.map((portal) => portal._id),
    ...(options.detailRecordIds ?? []),
    ...classificationIds,
    ...systemDefinitionIds,
  ]);
  const bulkPortalChildrenByParent = new Map<string, SnapshotRem[]>();
  for (const rem of allRem) {
    if (rem.type !== REM_TYPE_PORTAL || rem.parent === null) continue;
    const children = bulkPortalChildrenByParent.get(rem.parent) ?? [];
    children.push(rem);
    bulkPortalChildrenByParent.set(rem.parent, children);
  }
  let kbIdAtEnd: string | null = kbId;
  let kbConsistent = kbId !== null;
  let knowledgeBaseChanged = false;

  const verifyKnowledgeBase = async (operation: string): Promise<boolean> => {
    try {
      throwIfAborted(options.signal);
      const current = await plugin.kb.getCurrentKnowledgeBaseData();
      throwIfAborted(options.signal);
      kbIdAtEnd = current._id;
      if (kbId === null || current._id !== kbId) {
        kbConsistent = false;
        knowledgeBaseChanged = true;
        addError({
          severity: 'error',
          scope: 'capture',
          operation,
          message: `Knowledge base changed during capture (start=${kbId ?? 'unknown'}, current=${current._id}).`,
        });
        return false;
      }
      return true;
    } catch (error) {
      rethrowAbort(error);
      kbConsistent = false;
      addError({
        severity: 'error',
        scope: 'capture',
        operation,
        message: errorMessage(error),
      });
      return false;
    }
  };

  for (let portalIndex = 0; portalIndex < portalRems.length; portalIndex++) {
    throwIfAborted(options.signal);
    if (portalIndex > 0 && portalIndex % 25 === 0) {
      if (!(await verifyKnowledgeBase('KnowledgeBase.identity-checkpoint'))) break;
    }
    const portal = portalRems[portalIndex];
    onProgress(
      `Portal ${portalIndex + 1}/${portalRems.length}; ${(
        maxContextProbes - remainingContextProbes
      ).toLocaleString()}/${maxContextProbes.toLocaleString()} context probes used`,
    );
    const portalErrorIds: string[] = [];
    const captureTime = new Date().toISOString();

    let portalType: number | null = null;
    let portalTypeRaw: number | 'undefined' | 'unknown' = 'unknown';
    try {
      const runtimePortalType: unknown = await portal.getPortalType();
      throwIfAborted(options.signal);
      if (runtimePortalType === undefined) {
        // The host omits the portal-type property for the default ordinary portal.
        portalTypeRaw = 'undefined';
        portalType = PORTAL_TYPE.PORTAL;
      } else if (typeof runtimePortalType === 'number' && Number.isInteger(runtimePortalType)) {
        portalTypeRaw = runtimePortalType;
        portalType = runtimePortalType;
      } else {
        portalErrorIds.push(
          addError({
            severity: 'error',
            scope: 'portal',
            operation: 'Rem.getPortalType.runtime-value',
            portal_id: portal._id,
            message: `Host returned an unsupported portal type: ${JSON.stringify(runtimePortalType)}.`,
          }),
        );
      }
    } catch (error) {
      rethrowAbort(error);
      portalErrorIds.push(
        addError({
          severity: 'error',
          scope: 'portal',
          operation: 'Rem.getPortalType',
          portal_id: portal._id,
          message: errorMessage(error),
        }),
      );
    }

    let membershipComplete = true;
    let memberIds: string[] = [];
    let memberRems: SnapshotRem[] = [];
    try {
      memberRems = await portal.getPortalDirectlyIncludedRem();
      for (const rem of memberRems) rememberRuntimeRem(rem, `portal:${portal._id}:direct-members`);
      memberIds = memberRems.map((rem) => rem._id);
      throwIfAborted(options.signal);
    } catch (error) {
      rethrowAbort(error);
      membershipComplete = false;
      portalErrorIds.push(
        addError({
          severity: 'error',
          scope: 'portal',
          operation: 'Rem.getPortalDirectlyIncludedRem',
          portal_id: portal._id,
          message: errorMessage(error),
        }),
      );
    }

    let contextComplete = true;
    let contextIds: string[] = [];
    let contextRems: SnapshotRem[] = [];
    try {
      contextRems = await portal.allRemInDocumentOrPortal();
      for (const rem of contextRems) rememberRuntimeRem(rem, `portal:${portal._id}:context-array`);
      contextIds = contextRems.map((rem) => rem._id);
      throwIfAborted(options.signal);
    } catch (error) {
      rethrowAbort(error);
      contextComplete = false;
      portalErrorIds.push(
        addError({
          severity: 'warning',
          scope: 'portal',
          operation: 'Rem.allRemInDocumentOrPortal',
          portal_id: portal._id,
          message: errorMessage(error),
        }),
      );
    }

    const nestedContextEvidence: PortalRecord['nested_contexts']['evidence'] = {};
    const noteNestedContext = (
      rem: SnapshotRem,
      source: 'direct-members' | 'context-array' | 'bulk-child',
    ): void => {
      if (rem._id === portal._id || rem.type !== REM_TYPE_PORTAL) return;
      const sources = nestedContextEvidence[rem._id] ?? [];
      if (!sources.includes(source)) sources.push(source);
      nestedContextEvidence[rem._id] = sources;
    };
    for (const rem of memberRems) noteNestedContext(rem, 'direct-members');
    for (const rem of contextRems) noteNestedContext(rem, 'context-array');
    for (const rem of bulkPortalChildrenByParent.get(portal._id) ?? []) {
      noteNestedContext(rem, 'bulk-child');
    }
    const nestedContextIds = Object.keys(nestedContextEvidence).sort();
    const searchContextDiscoveryIncomplete =
      portalType === PORTAL_TYPE.SEARCH_PORTAL && !contextComplete;
    const nestedContextUnresolved =
      portalType === PORTAL_TYPE.SEARCH_PORTAL && nestedContextIds.length > 0;
    if (nestedContextUnresolved) {
      portalErrorIds.push(
        addError({
          severity: 'error',
          scope: 'portal',
          operation: 'nested-search-context-unresolved',
          portal_id: portal._id,
          message: `Detected ${nestedContextIds.length} nested portal context(s); outer membership and visibility remain diagnostic until live source-result mapping is calibrated.`,
        }),
      );
    }
    if (searchContextDiscoveryIncomplete) {
      portalErrorIds.push(
        addError({
          severity: 'error',
          scope: 'portal',
          operation: 'search-context-discovery-incomplete',
          portal_id: portal._id,
          message:
            'Search portal context discovery failed; absence of nested result contexts cannot be established, so flat membership and visibility projections are suppressed.',
        }),
      );
    }

    const probeSeedIds = [...new Set(options.portalProbeSeedsByPortal?.[portal._id] ?? [])].sort();
    const discoveredCandidateIds = [
      ...new Set([...descendantCandidates(memberIds, records), ...contextIds]),
    ].filter((id) => id !== portal._id);
    const discoveredCandidateSet = new Set(discoveredCandidateIds);
    const supplementalProbeSeedIds = probeSeedIds.filter(
      (id) => !discoveredCandidateSet.has(id),
    );
    const allCandidateIds = [
      ...new Set([...probeSeedIds, ...discoveredCandidateIds]),
    ].filter((id) => id !== portal._id);
    const probeCount = Math.min(
      allCandidateIds.length,
      maxProbesPerPortal,
      remainingContextProbes,
    );
    const candidateIds = allCandidateIds.slice(0, probeCount);
    remainingContextProbes -= probeCount;
    const visibilityStates: Record<string, HiddenState | 'unknown'> = {};
    const rawVisibilityStates: Record<string, HiddenRuntimeValue> = {};
    const collapsedStates: Record<string, boolean> = {};
    const positionStates: PortalRecord['positions']['states'] = {};
    let visibilityComplete = membershipComplete && probeCount === allCandidateIds.length;
    let collapsedComplete = membershipComplete && probeCount === allCandidateIds.length;
    let positionsComplete = membershipComplete && probeCount === allCandidateIds.length;
    if (probeCount < allCandidateIds.length) {
      portalErrorIds.push(
        addError({
          severity: 'warning',
          scope: 'portal',
          operation: 'portal-context-probe-limit',
          portal_id: portal._id,
          message: `Probed ${probeCount} of ${allCandidateIds.length} portal members/descendants; visibility, collapse, and position evidence is incomplete.`,
        }),
      );
    }
    if (supplementalProbeSeedIds.length) {
      visibilityComplete = false;
      portalErrorIds.push(
        addError({
          severity: 'warning',
          scope: 'portal',
          operation: 'raw-probe-seed-outside-sdk-context',
          portal_id: portal._id,
          message: `${supplementalProbeSeedIds.length} raw probe seed(s) were absent from SDK direct-member/portal-context discovery; captured values remain diagnostic and visibility scope is incomplete.`,
        }),
      );
    }
    const probeCandidate = async (candidateId: string) => {
      throwIfAborted(options.signal);
      const candidate = await resolveRuntimeRem(
        candidateId,
        `portal:${portal._id}:visibility-probe`,
      );
      if (!candidate) {
        return {
          candidateId,
          hidden: undefined,
          rawHidden: 'unknown' as const,
          hiddenFailed: false,
          hiddenInvalid: false,
          hiddenMethodUnavailable: false,
          collapsed: undefined,
          position: undefined,
          visiblePosition: undefined,
          missing: true,
        };
      }
      let hidden: HiddenState | undefined;
      let rawHidden: HiddenRuntimeValue = 'unknown';
      let collapsed: boolean | undefined;
      let position: number | undefined;
      let visiblePosition: number | undefined;
      let hiddenFailed = false;
      let hiddenInvalid = false;
      const hiddenMethodUnavailable =
        typeof candidate.getHiddenExplicitlyIncludedState !== 'function';
      try {
        if (!hiddenMethodUnavailable) {
          const wireValue: unknown = await candidate.getHiddenExplicitlyIncludedState?.(portal._id);
          if (wireValue === undefined) {
            // The current host represents its NONE enum as undefined. This is distinct from
            // an unavailable method, a rejected call, or an unresolved Rem.
            rawHidden = 'undefined';
            hidden = 'none';
          } else if (
            wireValue === 'hidden' ||
            wireValue === 'included' ||
            wireValue === 'root' ||
            wireValue === 'tab_included' ||
            wireValue === 'none'
          ) {
            rawHidden = wireValue;
            hidden = wireValue;
          } else {
            hiddenInvalid = true;
            portalErrorIds.push(
              addError({
                severity: 'error',
                scope: 'portal',
                operation: 'Rem.getHiddenExplicitlyIncludedState.runtime-value',
                rem_id: candidateId,
                portal_id: portal._id,
                message: `Host returned an unsupported runtime value: ${JSON.stringify(wireValue)}.`,
              }),
            );
          }
        }
        throwIfAborted(options.signal);
      } catch (error) {
        rethrowAbort(error);
        hiddenFailed = true;
        portalErrorIds.push(
          addError({
            severity: 'error',
            scope: 'portal',
            operation: 'Rem.getHiddenExplicitlyIncludedState',
            rem_id: candidateId,
            portal_id: portal._id,
            message: errorMessage(error),
          }),
        );
      }
      try {
        collapsed = await candidate.isCollapsed(portal._id);
        throwIfAborted(options.signal);
      } catch (error) {
        rethrowAbort(error);
        portalErrorIds.push(
          addError({
            severity: 'warning',
            scope: 'portal',
            operation: 'Rem.isCollapsed',
            rem_id: candidateId,
            portal_id: portal._id,
            message: errorMessage(error),
          }),
        );
      }
      try {
        position = await candidate.positionAmongstSiblings(portal._id);
        throwIfAborted(options.signal);
        visiblePosition = await candidate.positionAmongstVisibleSiblings(portal._id);
        throwIfAborted(options.signal);
      } catch (error) {
        rethrowAbort(error);
        portalErrorIds.push(
          addError({
            severity: 'warning',
            scope: 'portal',
            operation: 'Rem.positionAmongstSiblings/positionAmongstVisibleSiblings',
            rem_id: candidateId,
            portal_id: portal._id,
            message: errorMessage(error),
          }),
        );
      }
      return {
        candidateId,
        hidden,
        rawHidden,
        hiddenFailed,
        hiddenInvalid,
        hiddenMethodUnavailable,
        collapsed,
        position,
        visiblePosition,
        missing: false,
      };
    };
    const candidateResults: Awaited<ReturnType<typeof probeCandidate>>[] = [];
    const probeBatchSize = 250;
    for (let offset = 0; offset < candidateIds.length; offset += probeBatchSize) {
      throwIfAborted(options.signal);
      onProgress(
        `Portal ${portalIndex + 1}/${portalRems.length}: probing ${Math.min(
          offset + probeBatchSize,
          candidateIds.length,
        ).toLocaleString()}/${candidateIds.length.toLocaleString()} members/descendants; ${(
          maxContextProbes - remainingContextProbes
        ).toLocaleString()}/${maxContextProbes.toLocaleString()} budget assigned`,
      );
      candidateResults.push(
        ...(await mapLimit(candidateIds.slice(offset, offset + probeBatchSize), 12, probeCandidate)),
      );
    }

    for (const result of candidateResults) {
      if (result.missing) {
        visibilityComplete = false;
        collapsedComplete = false;
        positionsComplete = false;
        visibilityStates[result.candidateId] = 'unknown';
        rawVisibilityStates[result.candidateId] = 'unknown';
        portalErrorIds.push(
          addError({
            severity: 'error',
            scope: 'portal',
            operation: 'resolve-portal-candidate',
            rem_id: result.candidateId,
            portal_id: portal._id,
            message: 'Portal probe candidate was not resolved by getAll(), a live method response, or findOne().',
          }),
        );
        continue;
      }
      if (result.hidden === undefined) {
        visibilityComplete = false;
        visibilityStates[result.candidateId] = 'unknown';
        rawVisibilityStates[result.candidateId] = result.rawHidden;
        if (result.hiddenMethodUnavailable) {
          portalErrorIds.push(
            addError({
              severity: 'warning',
              scope: 'portal',
              operation: 'Rem.getHiddenExplicitlyIncludedState.unavailable',
              rem_id: result.candidateId,
              portal_id: portal._id,
              message:
                'The method exists in @remnote/plugin-sdk 0.0.46 types but is unavailable on this runtime Rem object.',
            }),
          );
        }
      } else {
        visibilityStates[result.candidateId] = result.hidden;
        rawVisibilityStates[result.candidateId] = result.rawHidden;
        if (result.hidden === 'tab_included') {
          visibilityComplete = false;
          portalErrorIds.push(
            addError({
              severity: 'warning',
              scope: 'portal',
              operation: 'visibility-tab-included-unvalidated',
              rem_id: result.candidateId,
              portal_id: portal._id,
              message:
                'The host returned tab_included, whose migration visibility semantics have not been calibrated.',
            }),
          );
        }
      }
      if (result.collapsed === undefined) collapsedComplete = false;
      else collapsedStates[result.candidateId] = result.collapsed;
      if (result.position === undefined && result.visiblePosition === undefined) {
        positionsComplete = false;
      }
      positionStates[result.candidateId] = {
        position: result.position ?? null,
        visible_position: result.visiblePosition ?? null,
      };
    }

    if (nestedContextUnresolved || searchContextDiscoveryIncomplete) visibilityComplete = false;

    let rootResultIds: string[] | null = null;
    let rootResultComplete = false;
    const rootResultBlockers: string[] = [];
    if (portalType === PORTAL_TYPE.SEARCH_PORTAL) {
      if (!membershipComplete) rootResultBlockers.push('membership-incomplete');
      if (nestedContextUnresolved) rootResultBlockers.push('nested-contexts-unresolved');
      if (searchContextDiscoveryIncomplete) rootResultBlockers.push('context-discovery-incomplete');

      const memberStates = memberIds.map((id) => visibilityStates[id] ?? 'unknown');
      if (memberStates.includes('unknown')) rootResultBlockers.push('member-state-unknown');
      if (memberStates.includes('tab_included')) {
        rootResultBlockers.push('tab-included-semantics-unvalidated');
      }

      const rootIds = memberIds.filter((id) => visibilityStates[id] === 'root');
      if (memberIds.length > 0 && rootIds.length === 0) {
        rootResultBlockers.push('nonempty-membership-without-root');
      }

      const rootIdSet = new Set(rootIds);
      const hasRootAncestor = (id: string): boolean => {
        const seen = new Set<string>();
        let cursor = runtimeRemById.get(id)?.parent ?? remById.get(id)?.parent ?? null;
        while (cursor && !seen.has(cursor)) {
          if (rootIdSet.has(cursor)) return true;
          seen.add(cursor);
          cursor = runtimeRemById.get(cursor)?.parent ?? remById.get(cursor)?.parent ?? null;
        }
        return false;
      };
      const unexplainedNonRoots = memberIds.filter(
        (id) => !rootIdSet.has(id) && !hasRootAncestor(id),
      );
      if (unexplainedNonRoots.length > 0) {
        rootResultBlockers.push('non-root-members-without-root-ancestor');
      }

      const positionedRoots = rootIds.map((id) => ({
        id,
        visiblePosition: positionStates[id]?.visible_position,
      }));
      if (
        positionedRoots.some(
          ({ visiblePosition }) =>
            !Number.isInteger(visiblePosition) || (visiblePosition as number) < 0,
        )
      ) {
        rootResultBlockers.push('root-visible-position-missing-or-invalid');
      }
      const rootPositions = positionedRoots.map(({ visiblePosition }) => visiblePosition);
      if (new Set(rootPositions).size !== rootPositions.length) {
        rootResultBlockers.push('root-visible-position-duplicate');
      }

      if (rootResultBlockers.length === 0) {
        rootResultIds = positionedRoots
          .slice()
          .sort((left, right) => (left.visiblePosition as number) - (right.visiblePosition as number))
          .map(({ id }) => id);
        rootResultComplete = true;
      } else {
        portalErrorIds.push(
          addError({
            severity: 'error',
            scope: 'portal',
            operation: 'search-root-result-order',
            portal_id: portal._id,
            message: `Could not derive ordered top-level search results: ${rootResultBlockers.join(', ')}.`,
          }),
        );
      }
    }

    const orderValidated =
      portalType === PORTAL_TYPE.PORTAL
        ? options.ordinaryOrderValidated === true
        : portalType === PORTAL_TYPE.SEARCH_PORTAL
          ? options.searchOrderValidated === true
          : false;
    const expectedHiddenIds = [...new Set(options.expectedHiddenByPortal?.[portal._id] ?? [])].sort();
    const expectedHiddenMatches = expectedHiddenIds.filter(
      (id) => visibilityStates[id] === 'hidden',
    );
    const expectedHiddenMismatches = expectedHiddenIds.filter(
      (id) => visibilityStates[id] !== 'hidden',
    );
    if (expectedHiddenMismatches.length) {
      portalErrorIds.push(
        addError({
          severity: 'warning',
          scope: 'portal',
          operation: 'visibility-runtime-calibration',
          portal_id: portal._id,
          message: `Expected hidden IDs did not return "hidden": ${expectedHiddenMismatches.join(', ')}.`,
        }),
      );
    }
    const runtimeValuesValid = candidateResults.every(
      (result) => !result.hiddenInvalid && !result.hiddenMethodUnavailable,
    );
    const visibilitySemanticsValidated = options.visibilitySemanticsValidated === true;
    let portalMigrationComplete =
      membershipComplete &&
      orderValidated &&
      visibilityComplete &&
      runtimeValuesValid &&
      visibilitySemanticsValidated;
    const portalDiagnosticsComplete = contextComplete && collapsedComplete && positionsComplete;

    let automaticView: PortalRecord['automatic_view'] = null;
    if (portalType === PORTAL_TYPE.SEARCH_PORTAL) {
      let backlinkTarget: SnapshotRem | undefined;
      let backlinkTargetId: string | null = null;
      let backlinkTargetRichText: RichTextInterface | null = null;
      let backlinkTargetResolution: NonNullable<PortalRecord['automatic_view']>['backlink_target_resolution'] = 'absent';
      let query: RichTextInterface | null = null;
      let filter: RichTextInterface | null = null;
      let dontIncludeNested: RichTextInterface | null = null;
      let automaticViewComplete =
        membershipComplete &&
        rootResultComplete &&
        !nestedContextUnresolved &&
        !searchContextDiscoveryIncomplete;
      for (const [operation, slot, setter] of [
        ['SearchPortal.Query', 'Query', (value: RichTextInterface) => (query = cloneRichText(value))],
        ['SearchPortal.Filter', 'Filter', (value: RichTextInterface) => (filter = cloneRichText(value))],
        [
          'SearchPortal.DontIncludeNestedDescendants',
          'DontIncludeNestedDescendants',
          (value: RichTextInterface) => (dontIncludeNested = cloneRichText(value)),
        ],
      ] as const) {
        try {
          if (typeof portal.getPowerupPropertyAsRichText !== 'function') {
            throw new Error('Rem.getPowerupPropertyAsRichText is unavailable on this runtime Rem object.');
          }
          setter(await portal.getPowerupPropertyAsRichText('sp', slot));
          throwIfAborted(options.signal);
        } catch (error) {
          rethrowAbort(error);
          automaticViewComplete = false;
          portalErrorIds.push(
            addError({
              severity: 'warning',
              scope: 'portal',
              operation,
              portal_id: portal._id,
              message: errorMessage(error),
            }),
          );
        }
      }
      if (typeof portal.getPowerupPropertyAsRichText !== 'function') {
        backlinkTargetResolution = 'method-unavailable';
        automaticViewComplete = false;
        portalErrorIds.push(
          addError({
            severity: 'warning',
            scope: 'portal',
            operation: 'SearchPortal.AutomaticBacklinkSearchPortalFor.unavailable',
            portal_id: portal._id,
            message: 'Rem.getPowerupPropertyAsRichText is unavailable on this runtime Rem object.',
          }),
        );
      } else {
        try {
          const backlinkRichText = await portal.getPowerupPropertyAsRichText(
            'sp',
            'AutomaticBacklinkSearchPortalFor',
          );
          throwIfAborted(options.signal);
          if (backlinkRichText == null) {
            backlinkTargetRichText = null;
          } else if (!Array.isArray(backlinkRichText)) {
            backlinkTargetResolution = 'ambiguous';
            automaticViewComplete = false;
            portalErrorIds.push(
              addError({
                severity: 'warning',
                scope: 'portal',
                operation: 'SearchPortal.AutomaticBacklinkSearchPortalFor.runtime-value',
                portal_id: portal._id,
                message: `Host returned non-array rich text: ${JSON.stringify(backlinkRichText)}.`,
              }),
            );
          } else {
            backlinkTargetRichText = cloneRichText(backlinkRichText);
          }
          if (Array.isArray(backlinkRichText) && backlinkRichText.length > 0) {
            const referenceItems = backlinkRichText.filter(
              (item): item is { i: 'q'; _id: string } =>
                typeof item === 'object' &&
                item !== null &&
                item.i === 'q' &&
                typeof item._id === 'string' &&
                item._id.length > 0,
            );
            const referenceIds = [...new Set(referenceItems.map((item) => item._id))];
            if (referenceIds.length === 1) {
              backlinkTargetResolution = 'single-reference';
              backlinkTargetId = referenceIds[0];
              backlinkTarget = await resolveRuntimeRem(
                backlinkTargetId,
                `portal:${portal._id}:backlink-target`,
              );
              if (!backlinkTarget) {
                automaticViewComplete = false;
                portalErrorIds.push(
                  addError({
                    severity: 'warning',
                    scope: 'portal',
                    operation: 'SearchPortal.AutomaticBacklinkSearchPortalFor.target-unresolved',
                    rem_id: backlinkTargetId,
                    portal_id: portal._id,
                    message: 'Backlink target reference was captured but its Rem object could not be resolved.',
                  }),
                );
              }
            } else {
              backlinkTargetResolution = 'ambiguous';
              automaticViewComplete = false;
              portalErrorIds.push(
                addError({
                  severity: 'warning',
                  scope: 'portal',
                  operation: 'SearchPortal.AutomaticBacklinkSearchPortalFor.ambiguous',
                  portal_id: portal._id,
                  message: `Non-empty backlink target rich text contained ${referenceIds.length} unique Rem references; exactly one is required.`,
                }),
              );
            }
          }
        } catch (error) {
          rethrowAbort(error);
          backlinkTargetResolution = 'failed';
          automaticViewComplete = false;
          portalErrorIds.push(
            addError({
              severity: 'warning',
              scope: 'portal',
              operation: 'SearchPortal.AutomaticBacklinkSearchPortalFor',
              portal_id: portal._id,
              message: errorMessage(error),
            }),
          );
        }
      }

      automaticView = {
        kind: backlinkTargetId ? 'backlink' : 'search',
        complete: automaticViewComplete,
        result_ids: [...memberIds],
        result_order: 'sdk-return-order',
        result_interpretation: nestedContextUnresolved
          ? 'direct-members-unmapped-nested-contexts'
          : searchContextDiscoveryIncomplete
            ? 'direct-members-context-discovery-incomplete'
            : 'direct-members',
        root_result_ids: rootResultIds,
        root_result_complete: rootResultComplete,
        root_result_order: rootResultComplete ? 'visible-sibling-position' : null,
        root_result_blockers: rootResultBlockers,
        backlink_target_rich_text: backlinkTargetRichText,
        backlink_target_resolution: backlinkTargetResolution,
        backlink_target_id: backlinkTargetId,
        query,
        filter,
        dont_include_nested_descendants: dontIncludeNested,
        limitation:
          'result_ids preserves SDK return order for diagnostics. root_result_ids is emitted only when every top-level root is identified by the host root state and has a unique nonnegative visible sibling position; nested contexts remain unresolved.',
      };

      if (backlinkTarget && !(backlinkTarget._id in backlinkRelations)) {
        try {
          const referencingRems = await backlinkTarget.remsReferencingThis();
          for (const rem of referencingRems) {
            rememberRuntimeRem(rem, `portal:${portal._id}:backlink-relation`);
          }
          backlinkRelations[backlinkTarget._id] = {
            complete: true,
            ordered: false,
            method: 'Rem.remsReferencingThis',
            member_ids: referencingRems.map((rem) => rem._id).sort(),
          };
        } catch (error) {
          rethrowAbort(error);
          backlinkRelations[backlinkTarget._id] = {
            complete: false,
            ordered: false,
            method: 'Rem.remsReferencingThis',
            member_ids: [],
          };
          portalErrorIds.push(
            addError({
              severity: 'warning',
              scope: 'relation',
              operation: 'Rem.remsReferencingThis',
              rem_id: backlinkTarget._id,
              portal_id: portal._id,
              message: errorMessage(error),
            }),
          );
        }
      }
    }

    if (portalType === PORTAL_TYPE.SEARCH_PORTAL) {
      portalMigrationComplete = portalMigrationComplete && automaticView?.complete === true;
    }

    const candidateResultById = new Map(
      candidateResults.map((result) => [result.candidateId, result]),
    );
    const resolvedProbeSeedIds = probeSeedIds.filter(
      (id) => candidateResultById.has(id) && !candidateResultById.get(id)?.missing,
    );
    const missingProbeSeedIds = probeSeedIds.filter(
      (id) => !candidateResultById.has(id) || candidateResultById.get(id)?.missing,
    );

    portals[portal._id] = {
      portal_id: portal._id,
      portal_type: portalType,
      portal_type_raw: portalTypeRaw,
      portal_type_contract: {
        source: 'public-host-implementation-and-sdk-enum',
        resolved_undefined_means: 'portal',
      },
      portal_type_name: portalTypeName(portalType),
      membership: {
        complete: membershipComplete,
        ordered: true,
        order_semantics: 'sdk-return-order',
        order_validation: orderValidated ? 'operator-ui-validated' : 'unverified',
        method: 'Rem.getPortalDirectlyIncludedRem',
        captured_at: captureTime,
        member_ids: memberIds,
      },
      context: {
        complete: contextComplete,
        ordered: false,
        method: 'Rem.allRemInDocumentOrPortal',
        rem_ids: contextIds,
      },
      visibility: {
        complete: visibilityComplete,
        method: 'Rem.getHiddenExplicitlyIncludedState',
        sdk_status: 'typed-but-undocumented',
        candidate_ids: candidateIds,
        states: visibilityStates,
        raw_states: rawVisibilityStates,
        runtime_contract: {
          source: 'public-host-implementation-and-live-calibration',
          sdk_declaration_complete: false,
          resolved_undefined_means: 'none',
          tab_included_projection: 'unvalidated',
        },
        runtime_values_valid: runtimeValuesValid,
        semantics_validation: visibilitySemanticsValidated
          ? 'operator-ui-validated'
          : 'unverified',
        expected_hidden_ids: expectedHiddenIds,
        expected_hidden_matches: expectedHiddenMatches,
        expected_hidden_mismatches: expectedHiddenMismatches,
        probe_seeds: {
          requested_ids: probeSeedIds,
          resolved_ids: resolvedProbeSeedIds,
          missing_ids: missingProbeSeedIds,
          supplemental_ids: supplementalProbeSeedIds,
          establishes_complete_scope: false,
        },
      },
      collapsed: {
        complete: collapsedComplete,
        method: 'Rem.isCollapsed',
        states: collapsedStates,
      },
      positions: {
        complete: positionsComplete,
        method: 'Rem.positionAmongstSiblings + Rem.positionAmongstVisibleSiblings',
        states: positionStates,
      },
      automatic_view: automaticView,
      nested_contexts: {
        status: nestedContextUnresolved
          ? 'detected-unresolved'
          : searchContextDiscoveryIncomplete
            ? 'discovery-incomplete'
            : 'none-detected',
        detected_ids: nestedContextIds,
        evidence: nestedContextEvidence,
        projection_safe: !nestedContextUnresolved && !searchContextDiscoveryIncomplete,
        limitation: nestedContextUnresolved
          ? 'Direct-member IDs are not assumed to be rendered source-result IDs. Nested context membership and portal-local visibility must be calibrated independently.'
          : searchContextDiscoveryIncomplete
            ? 'Context discovery failed, so the capture cannot establish whether nested result-context portals exist. Direct members remain diagnostic only.'
          : 'No nested portal context was detected in direct members, the context array, or bulk portal children; absence is capture evidence, not an SDK guarantee.',
      },
      migration: {
        membership_complete: membershipComplete,
        order_validated: orderValidated,
        visibility_complete: visibilityComplete && runtimeValuesValid,
        visibility_semantics_validated: visibilitySemanticsValidated,
        complete: portalMigrationComplete,
      },
      diagnostics: {
        context_complete: contextComplete,
        collapsed_complete: collapsedComplete,
        positions_complete: positionsComplete,
        complete: portalDiagnosticsComplete,
      },
      error_ids: portalErrorIds,
    };

    for (const id of memberIds.slice(0, maxDetailedMembersPerPortal)) detailedIds.add(id);
    if (automaticView?.backlink_target_id) detailedIds.add(automaticView.backlink_target_id);

    const projectionMembers =
      portalType === PORTAL_TYPE.SEARCH_PORTAL
        ? automaticView?.complete && automaticView.root_result_ids
          ? automaticView.root_result_ids
          : null
        : memberIds;
    if (
      membershipComplete &&
      orderValidated &&
      !nestedContextUnresolved &&
      !searchContextDiscoveryIncomplete &&
      projectionMembers !== null &&
      (portalType === PORTAL_TYPE.PORTAL || portalType === PORTAL_TYPE.SEARCH_PORTAL)
    ) {
      converterProjection.portal_snapshots[portal._id] = {
        evidence: `RemNote SDK Rem.getPortalDirectlyIncludedRem captured ${captureTime}; ${
          portalType === PORTAL_TYPE.SEARCH_PORTAL
            ? 'host root state ordered by visible sibling position'
            : 'portal membership order'
        } order`,
        members: [...projectionMembers],
      };
    }
    if (visibilityComplete && runtimeValuesValid && visibilitySemanticsValidated) {
      const explicitStates = Object.fromEntries(
        Object.entries(visibilityStates)
          .filter(([, state]) => state === 'hidden' || state === 'included')
          .map(([id, state]) => [id, state === 'hidden' ? ('hidden' as const) : ('visible' as const)]),
      );
      if (Object.keys(explicitStates).length) {
        converterProjection.visibility_overrides[portal._id] = {
          evidence: `RemNote SDK Rem.getHiddenExplicitlyIncludedState captured ${captureTime}; runtime state semantics operator-validated; hidden/included projected, other states retained only in the full contract`,
          states: explicitStates,
        };
      }
    }
  }

  for (const id of detailedIds) {
    const rem = remById.get(id);
    const record = records[id];
    if (!rem || !record) {
      if ((options.detailRecordIds ?? []).includes(id)) {
        addError({
          severity: 'error',
          scope: 'record',
          operation: 'resolve-rich-record-candidate',
          rem_id: id,
          message: 'Explicit rich-record candidate was not returned by RemNamespace.getAll.',
        });
      }
      continue;
    }
    record.text = cloneRichText(rem.text);
    record.back_text = cloneRichText(rem.backText);
    record.detail_level = 'rich';
  }

  if (!knowledgeBaseChanged) {
    await verifyKnowledgeBase('KnowledgeBase.identity-final');
  }
  if (!kbConsistent) {
    for (const key of Object.keys(converterProjection.portal_snapshots)) {
      delete converterProjection.portal_snapshots[key];
    }
    for (const key of Object.keys(converterProjection.visibility_overrides)) {
      delete converterProjection.visibility_overrides[key];
    }
  }

  const expectedPortalCount =
    mode === 'calibration' ? priorityPortalIds.size : allPortalRems.length;
  const processedPortalCount = Object.keys(portals).length;
  const portalScopeComplete =
    missingRequestedPortalIds.length === 0 && processedPortalCount === expectedPortalCount;
  const migrationComplete =
    kbConsistent &&
    portalScopeComplete &&
    classificationsComplete &&
    systemDefinitionsComplete &&
    errors.every((error) => error.severity !== 'error') &&
    Object.values(portals).every((portal) => portal.migration.complete);
  const diagnosticsComplete =
    childMembershipProbeComplete &&
    portalScopeComplete &&
    Object.values(portals).every((portal) => portal.diagnostics.complete);

  const completedAt = new Date().toISOString();
  const snapshot: MigrationSnapshot = {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    capture: {
      mode,
      knowledgebase_id: kbId,
      knowledgebase_name: kbName,
      knowledgebase_id_at_end: kbIdAtEnd,
      knowledgebase_consistent: kbConsistent,
      started_at: startedAt,
      completed_at: completedAt,
      complete: migrationComplete,
      migration_complete: migrationComplete,
      diagnostics_complete: diagnosticsComplete,
      platform,
      plugin_version: PLUGIN_VERSION,
      sdk_package: '@remnote/plugin-sdk',
      sdk_version: SDK_VERSION,
      payload_sha256: null,
      limits: {
        max_context_probes: maxContextProbes,
        max_probes_per_portal: maxProbesPerPortal,
        max_detailed_members_per_portal: maxDetailedMembersPerPortal,
        max_child_membership_probes: maxChildMembershipProbes,
      },
      scope: {
        expected_portal_count: expectedPortalCount,
        processed_portal_count: processedPortalCount,
        requested_portal_ids: [...priorityPortalIds].sort(),
        missing_requested_portal_ids: missingRequestedPortalIds,
      },
      projection_policy: {
        ordinary_order_validated: options.ordinaryOrderValidated === true,
        search_order_validated: options.searchOrderValidated === true,
        visibility_semantics_validated: options.visibilitySemanticsValidated === true,
        reason:
          'Ordinary portal projection requires validated SDK membership order. Search projection uses host root states sorted by unique nonnegative visible sibling positions and remains blocked for nested contexts or unaccounted members. Visibility projection requires complete runtime-valid state results plus operator UI validation.',
      },
      export_comparison: {
        structural_fields: ['id', 'parent_id', 'child_ids'],
        child_order_calibrated: options.childOrderCalibrated === true,
        child_order_raw_basis:
          'Raw siblings use null-first fractional f; every SDK child array must have complete parent-consistent membership; SDK order resolves null/tied f.',
        child_membership_probe: {
          complete: childMembershipProbeComplete,
          mismatch_parent_count: childMembershipMismatchIds.length,
          attempted: childMembershipProbeIds.length,
          verified: verifiedChildMembershipProbes,
          failed: childMembershipProbeIds.length - verifiedChildMembershipProbes,
          skipped_by_limit: childMembershipSkippedIds.length,
          method: 'Rem.getChildrenRem',
        },
        rich_text_algorithm: 'fnv1a64-canonical-richtext-v2-media-url',
        media_url_normalization: {
          scope: 'rich-text-media-object-url-only',
          media_type: 'i',
          prefixes: ['https://remnote-user-data.s3.amazonaws.com/', '%LOCAL_FILE%'],
          sentinel: '%REMNOTE_ASSET%',
        },
        raw_input_fields: ['key', 'value'],
        sdk_input_fields: ['text', 'backText'],
        calibrated_equivalent: options.richTextFingerprintCalibrated === true,
        limitation:
          'The canonical rich-text digest normalizes only recognized RemNote asset prefixes in media-object url fields. It is a hard drift gate only when calibrated_equivalent is true.',
      },
      provenance: {
        producer: 'PKMigrator Read-Only Snapshot',
        data_location: 'local-download',
        source_mutation: false,
        transactional: false,
        record_inventory_count: allRem.length,
        methods: API_METHODS,
        documentation: [
          'https://plugins.remnote.com/api/classes/Rem',
          'https://plugins.remnote.com/api/classes/RemNamespace',
          'https://plugins.remnote.com/api/classes/KnowledgeBaseNamespace',
          'https://plugins.remnote.com/api/enums/PORTAL_TYPE',
        ],
      },
    },
    records,
    runtime_returned_records: runtimeReturnedRecords,
    portals,
    relations: { backlinks: backlinkRelations, tags: tagRelations },
    classifications: {
      document_and_folder: {
        complete: classificationsComplete,
        method: 'Rem.isDocument + Rem.isFolder',
        scope: 'all-top-level-plus-explicit-candidates',
        requested_ids: classificationIds,
        states: classificationStates,
        missing_ids: missingClassificationIds.sort(),
      },
      system_definition: {
        complete: systemDefinitionsComplete,
        method:
          'Rem.isPowerup + Rem.isPowerupEnum + Rem.isPowerupPropertyListItem + Rem.isPowerupSlot + Rem.isPowerupProperty',
        requested_ids: systemDefinitionIds,
        states: systemDefinitionStates,
        missing_ids: missingSystemDefinitionIds.sort(),
        limitation:
          'Only explicitly requested records are classified. Descendants are never inferred to be system definitions from an ancestor or title.',
      },
    },
    converter_projection: converterProjection,
    errors,
  };
  snapshot.capture.payload_sha256 = await sha256Hex(
    JSON.stringify({
      records: snapshot.records,
      runtime_returned_records: snapshot.runtime_returned_records,
      portals: snapshot.portals,
      relations: snapshot.relations,
      classifications: snapshot.classifications,
      converter_projection: snapshot.converter_projection,
    }),
  );
  return snapshot;
}

export function downloadSnapshot(snapshot: MigrationSnapshot): string {
  const timestamp = snapshot.capture.completed_at.replace(/[:.]/g, '-');
  const filename = `remnote-migration-snapshot-${timestamp}.json`;
  const blob = new Blob([`${JSON.stringify(snapshot, null, 2)}\n`], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return filename;
}
