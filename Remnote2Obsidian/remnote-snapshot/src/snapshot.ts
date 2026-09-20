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
export const PLUGIN_VERSION = '0.1.0';

export type Progress = (message: string) => void;
export type HiddenState = 'hidden' | 'included' | 'none';
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
  getPortalType(): Promise<number>;
  getPortalDirectlyIncludedRem(): Promise<SnapshotRem[]>;
  allRemInDocumentOrPortal(): Promise<SnapshotRem[]>;
  isCollapsed(portalId: string): Promise<boolean>;
  positionAmongstSiblings(portalId?: string): Promise<number | undefined>;
  positionAmongstVisibleSiblings(portalId?: string): Promise<number | undefined>;
  getHiddenExplicitlyIncludedState?: (
    portalId?: string,
  ) => Promise<HiddenState | undefined>;
  getPowerupPropertyAsRem(powerupCode: string, slotCode: string): Promise<SnapshotRem | undefined>;
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

export interface PortalRecord {
  portal_id: string;
  portal_type: number | null;
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
    runtime_values_valid: boolean;
    semantics_validation: 'operator-ui-validated' | 'unverified';
    expected_hidden_ids: string[];
    expected_hidden_matches: string[];
    expected_hidden_mismatches: string[];
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
    backlink_target_id: string | null;
    query: RichTextInterface | null;
    filter: RichTextInterface | null;
    dont_include_nested_descendants: RichTextInterface | null;
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
      rich_text_algorithm: 'fnv1a64-canonical-richtext-v1';
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
  'Rem.getPortalType',
  'Rem.getPortalDirectlyIncludedRem',
  'Rem.allRemInDocumentOrPortal',
  'Rem.getHiddenExplicitlyIncludedState',
  'Rem.isCollapsed',
  'Rem.getPowerupPropertyAsRem',
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

  onProgress(`Indexing ${allRem.length} source records from the bulk response…`);
  const recordEntries = allRem.map((rem): [string, SourceRecord] => {
    const richTextPair = [rem.text ?? null, rem.backText ?? null];
    const richFingerprint = fnv1a64(JSON.stringify(richTextPair));
    const comparableFingerprint = fnv1a64(JSON.stringify(canonicalJsonValue(richTextPair)));
    return [
      rem._id,
      {
        id: rem._id,
        type: rem.type,
        parent_id: rem.parent,
        child_ids: [...(rem.children ?? [])],
        text: null,
        back_text: null,
        created_at: rem.createdAt,
        updated_at: rem.updatedAt,
        local_updated_at: rem.localUpdatedAt,
        tag_ids: [],
        tags_complete: false,
        detail_level: 'inventory',
        rich_text_fingerprint: `fnv1a64-json:${richFingerprint}`,
        export_comparable_rich_text_fingerprint: `fnv1a64-canonical-richtext-v1:${comparableFingerprint}`,
      },
    ];
  });
  const records = Object.fromEntries(recordEntries);
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
  const allPortalRems = allRem.filter((rem) => rem.type === REM_TYPE_PORTAL);
  const missingRequestedPortalIds = [...priorityPortalIds]
    .filter((id) => remById.get(id)?.type !== REM_TYPE_PORTAL)
    .sort();
  for (const id of missingRequestedPortalIds) {
    addError({
      severity: 'error',
      scope: 'portal',
      operation: 'resolve-calibration-portal',
      portal_id: id,
      message: 'Requested calibration ID was not returned as a portal by RemNamespace.getAll.',
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
    try {
      portalType = await portal.getPortalType();
      throwIfAborted(options.signal);
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
    try {
      memberIds = (await portal.getPortalDirectlyIncludedRem()).map((rem) => rem._id);
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
    try {
      contextIds = (await portal.allRemInDocumentOrPortal()).map((rem) => rem._id);
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

    const allCandidateIds = descendantCandidates(memberIds, records);
    const probeCount = Math.min(
      allCandidateIds.length,
      maxProbesPerPortal,
      remainingContextProbes,
    );
    const candidateIds = allCandidateIds.slice(0, probeCount);
    remainingContextProbes -= probeCount;
    const visibilityStates: Record<string, HiddenState | 'unknown'> = {};
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
    const hiddenMethodAvailable =
      candidateIds.length === 0 ||
      typeof remById.get(candidateIds[0])?.getHiddenExplicitlyIncludedState === 'function';
    if (!hiddenMethodAvailable) {
      visibilityComplete = false;
      portalErrorIds.push(
        addError({
          severity: 'warning',
          scope: 'portal',
          operation: 'Rem.getHiddenExplicitlyIncludedState',
          portal_id: portal._id,
          message:
            'The method exists in @remnote/plugin-sdk 0.0.46 types but is unavailable in this RemNote host.',
        }),
      );
    }

    const probeCandidate = async (candidateId: string) => {
      throwIfAborted(options.signal);
      const candidate = remById.get(candidateId);
      if (!candidate) {
        return {
          candidateId,
          hidden: undefined,
          hiddenFailed: false,
          hiddenInvalid: false,
          collapsed: undefined,
          position: undefined,
          visiblePosition: undefined,
          missing: true,
        };
      }
      let hidden: HiddenState | undefined;
      let collapsed: boolean | undefined;
      let position: number | undefined;
      let visiblePosition: number | undefined;
      let hiddenFailed = false;
      let hiddenInvalid = false;
      try {
        const rawHidden: unknown = hiddenMethodAvailable
          ? await candidate.getHiddenExplicitlyIncludedState?.(portal._id)
          : undefined;
        if (
          rawHidden === 'hidden' ||
          rawHidden === 'included' ||
          rawHidden === 'none' ||
          rawHidden === undefined
        ) {
          hidden = rawHidden;
        } else {
          hiddenInvalid = true;
          portalErrorIds.push(
            addError({
              severity: 'error',
              scope: 'portal',
              operation: 'Rem.getHiddenExplicitlyIncludedState.runtime-value',
              rem_id: candidateId,
              portal_id: portal._id,
              message: `Host returned an unsupported runtime value: ${JSON.stringify(rawHidden)}.`,
            }),
          );
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
        hiddenFailed,
        hiddenInvalid,
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
        portalErrorIds.push(
          addError({
            severity: 'error',
            scope: 'portal',
            operation: 'resolve-portal-candidate',
            rem_id: result.candidateId,
            portal_id: portal._id,
            message: 'Portal member or descendant was not returned by RemNamespace.getAll.',
          }),
        );
        continue;
      }
      if (result.hidden === undefined) {
        visibilityComplete = false;
        visibilityStates[result.candidateId] = 'unknown';
        if (!result.hiddenFailed && !result.hiddenInvalid && hiddenMethodAvailable) {
          portalErrorIds.push(
            addError({
              severity: 'warning',
              scope: 'portal',
              operation: 'Rem.getHiddenExplicitlyIncludedState',
              rem_id: result.candidateId,
              portal_id: portal._id,
              message: 'The typed-but-undocumented SDK method returned undefined.',
            }),
          );
        }
      } else {
        visibilityStates[result.candidateId] = result.hidden;
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
    const runtimeValuesValid = candidateResults.every((result) => !result.hiddenInvalid);
    const visibilitySemanticsValidated = options.visibilitySemanticsValidated === true;
    const portalMigrationComplete =
      membershipComplete &&
      orderValidated &&
      visibilityComplete &&
      runtimeValuesValid &&
      visibilitySemanticsValidated;
    const portalDiagnosticsComplete = contextComplete && collapsedComplete && positionsComplete;

    let automaticView: PortalRecord['automatic_view'] = null;
    if (portalType === PORTAL_TYPE.SEARCH_PORTAL) {
      let backlinkTarget: SnapshotRem | undefined;
      let query: RichTextInterface | null = null;
      let filter: RichTextInterface | null = null;
      let dontIncludeNested: RichTextInterface | null = null;
      let automaticViewComplete = membershipComplete;
      for (const [operation, slot, setter] of [
        ['SearchPortal.Query', 'q', (value: RichTextInterface) => (query = cloneRichText(value))],
        ['SearchPortal.Filter', 'f', (value: RichTextInterface) => (filter = cloneRichText(value))],
        [
          'SearchPortal.DontIncludeNestedDescendants',
          's',
          (value: RichTextInterface) => (dontIncludeNested = cloneRichText(value)),
        ],
      ] as const) {
        try {
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
      try {
        backlinkTarget = await portal.getPowerupPropertyAsRem('sp', 'b');
        throwIfAborted(options.signal);
      } catch (error) {
        rethrowAbort(error);
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

      automaticView = {
        kind: backlinkTarget ? 'backlink' : 'search',
        complete: automaticViewComplete,
        result_ids: [...memberIds],
        result_order: 'sdk-return-order',
        backlink_target_id: backlinkTarget?._id ?? null,
        query,
        filter,
        dont_include_nested_descendants: dontIncludeNested,
        limitation:
          'The SDK exposes live portal members and the backlink target slot, but no documented discriminator for tag views or guarantee that result order matches every UI section.',
      };

      if (backlinkTarget && !(backlinkTarget._id in backlinkRelations)) {
        try {
          backlinkRelations[backlinkTarget._id] = {
            complete: true,
            ordered: false,
            method: 'Rem.remsReferencingThis',
            member_ids: (await backlinkTarget.remsReferencingThis()).map((rem) => rem._id).sort(),
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

    portals[portal._id] = {
      portal_id: portal._id,
      portal_type: portalType,
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
        runtime_values_valid: runtimeValuesValid,
        semantics_validation: visibilitySemanticsValidated
          ? 'operator-ui-validated'
          : 'unverified',
        expected_hidden_ids: expectedHiddenIds,
        expected_hidden_matches: expectedHiddenMatches,
        expected_hidden_mismatches: expectedHiddenMismatches,
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

    if (
      membershipComplete &&
      orderValidated &&
      (portalType === PORTAL_TYPE.PORTAL || portalType === PORTAL_TYPE.SEARCH_PORTAL)
    ) {
      converterProjection.portal_snapshots[portal._id] = {
        evidence: `RemNote SDK Rem.getPortalDirectlyIncludedRem captured ${captureTime}; ${
          portalType === PORTAL_TYPE.SEARCH_PORTAL ? 'search portal live result' : 'portal membership'
        } order`,
        members: [...memberIds],
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
          evidence: `RemNote SDK Rem.getHiddenExplicitlyIncludedState captured ${captureTime}; runtime tri-state and semantics operator-validated; hidden/included projected, none retained only in full contract as no local override`,
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
    portalScopeComplete && Object.values(portals).every((portal) => portal.diagnostics.complete);

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
          'A portal membership projection is emitted only for a portal type whose SDK return order was compared with the live UI. Visibility projection requires complete runtime-valid tri-state results plus operator UI validation; none remains only in the full contract as no local override.',
      },
      export_comparison: {
        structural_fields: ['id', 'parent_id', 'child_ids'],
        child_order_calibrated: options.childOrderCalibrated === true,
        child_order_raw_basis:
          'Compare SDK children array order with raw siblings sorted by fractional f, using raw record ordinal as the tie-break.',
        rich_text_algorithm: 'fnv1a64-canonical-richtext-v1',
        raw_input_fields: ['key', 'value'],
        sdk_input_fields: ['text', 'backText'],
        calibrated_equivalent: options.richTextFingerprintCalibrated === true,
        limitation:
          'The canonical rich-text digest is a hard drift gate only after a real record sample proves raw key/value and SDK text/backText are representation-equivalent.',
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
