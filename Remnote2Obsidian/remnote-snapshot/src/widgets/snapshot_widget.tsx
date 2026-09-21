import React, { useRef, useState } from 'react';
import { renderWidget, usePlugin } from '@remnote/plugin-sdk';
import { buildSnapshot, downloadSnapshot, type CaptureMode, type SnapshotPlugin } from '../snapshot';

const ids = (value: string): string[] =>
  value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);

interface CaptureSettingsFile {
  schema_version?: unknown;
  mode?: unknown;
  priorityPortalIds?: unknown;
  classificationRecordIds?: unknown;
  systemDefinitionRecordIds?: unknown;
  detailRecordIds?: unknown;
  expectedHiddenByPortal?: unknown;
  portalProbeSeedsByPortal?: unknown;
  maxContextProbes?: unknown;
  maxProbesPerPortal?: unknown;
  ordinaryOrderValidated?: unknown;
  searchOrderValidated?: unknown;
  visibilitySemanticsValidated?: unknown;
  richTextFingerprintCalibrated?: unknown;
  childOrderCalibrated?: unknown;
  records?: Array<{ rem_id?: unknown }>;
}

function stringArray(value: unknown, field: string): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new Error(`${field} must be an array of IDs.`);
  }
  return value as string[];
}

function idArrayMap(value: unknown, field: string): Record<string, string[]> {
  if (value === null || Array.isArray(value) || typeof value !== 'object') {
    throw new Error(`${field} must be an object keyed by portal ID.`);
  }
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([portalId, item]) => [
      portalId,
      stringArray(item, `${field}.${portalId}`),
    ]),
  );
}

function SnapshotWidget(): JSX.Element {
  const plugin = usePlugin();
  const [running, setRunning] = useState(false);
  const [mode, setMode] = useState<CaptureMode>('calibration');
  const [priorityPortals, setPriorityPortals] = useState('');
  const [classificationIds, setClassificationIds] = useState('');
  const [systemDefinitionIds, setSystemDefinitionIds] = useState('');
  const [detailIds, setDetailIds] = useState('');
  const [expectedHiddenByPortal, setExpectedHiddenByPortal] = useState('{}');
  const [portalProbeSeedsByPortal, setPortalProbeSeedsByPortal] = useState('{}');
  const [maxContextProbes, setMaxContextProbes] = useState(10_000);
  const [maxProbesPerPortal, setMaxProbesPerPortal] = useState(10_000);
  const [ordinaryOrderValidated, setOrdinaryOrderValidated] = useState(false);
  const [searchOrderValidated, setSearchOrderValidated] = useState(false);
  const [visibilitySemanticsValidated, setVisibilitySemanticsValidated] = useState(false);
  const [richTextFingerprintCalibrated, setRichTextFingerprintCalibrated] = useState(false);
  const [childOrderCalibrated, setChildOrderCalibrated] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const [status, setStatus] = useState('Nothing is read until you press the download button.');

  const changeMode = (next: CaptureMode): void => {
    setMode(next);
    setMaxContextProbes(next === 'complete' ? 250_000 : 10_000);
    setMaxProbesPerPortal(next === 'complete' ? 100_000 : 10_000);
  };

  const loadSettingsFile = async (file: File | undefined): Promise<void> => {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as CaptureSettingsFile;
      if (Array.isArray(parsed.records)) {
        const candidates = parsed.records
          .map((record) => record.rem_id)
          .filter((value): value is string => typeof value === 'string' && value.length > 0);
        if (!candidates.length) throw new Error('No records[].rem_id values were found.');
        setClassificationIds(candidates.join('\n'));
        setStatus(`Loaded ${candidates.length} document/folder candidates from ${file.name}.`);
        return;
      }
      if (parsed.schema_version !== undefined && parsed.schema_version !== 'remnote-migration-capture-settings/v1') {
        throw new Error('Unsupported capture-settings schema_version.');
      }
      if (parsed.mode !== undefined && parsed.mode !== 'calibration' && parsed.mode !== 'complete') {
        throw new Error('mode must be "calibration" or "complete".');
      }
      if (parsed.mode) changeMode(parsed.mode);
      const portalIds = stringArray(parsed.priorityPortalIds, 'priorityPortalIds');
      const candidates = stringArray(parsed.classificationRecordIds, 'classificationRecordIds');
      const systemDefinitions = stringArray(parsed.systemDefinitionRecordIds, 'systemDefinitionRecordIds');
      const details = stringArray(parsed.detailRecordIds, 'detailRecordIds');
      let hiddenByPortal: Record<string, string[]> = {};
      if (parsed.expectedHiddenByPortal !== undefined) {
        hiddenByPortal = idArrayMap(parsed.expectedHiddenByPortal, 'expectedHiddenByPortal');
      }
      let probeSeedsByPortal: Record<string, string[]> = {};
      if (parsed.portalProbeSeedsByPortal !== undefined) {
        probeSeedsByPortal = idArrayMap(
          parsed.portalProbeSeedsByPortal,
          'portalProbeSeedsByPortal',
        );
      }
      setPriorityPortals(portalIds.join('\n'));
      setClassificationIds(candidates.join('\n'));
      setSystemDefinitionIds(systemDefinitions.join('\n'));
      setDetailIds(details.join('\n'));
      setExpectedHiddenByPortal(JSON.stringify(hiddenByPortal, null, 2));
      setPortalProbeSeedsByPortal(JSON.stringify(probeSeedsByPortal, null, 2));
      if (parsed.maxContextProbes !== undefined) {
        if (!Number.isSafeInteger(parsed.maxContextProbes) || Number(parsed.maxContextProbes) <= 0) throw new Error('maxContextProbes must be a positive safe integer.');
        setMaxContextProbes(Number(parsed.maxContextProbes));
      }
      if (parsed.maxProbesPerPortal !== undefined) {
        if (!Number.isSafeInteger(parsed.maxProbesPerPortal) || Number(parsed.maxProbesPerPortal) <= 0) throw new Error('maxProbesPerPortal must be a positive safe integer.');
        setMaxProbesPerPortal(Number(parsed.maxProbesPerPortal));
      }
      setOrdinaryOrderValidated(parsed.ordinaryOrderValidated === true);
      setSearchOrderValidated(parsed.searchOrderValidated === true);
      setVisibilitySemanticsValidated(parsed.visibilitySemanticsValidated === true);
      setRichTextFingerprintCalibrated(parsed.richTextFingerprintCalibrated === true);
      setChildOrderCalibrated(parsed.childOrderCalibrated === true);
      setStatus(`Loaded capture settings from ${file.name}. Review the validation checkboxes before capture.`);
    } catch (error) {
      setStatus(`Settings file rejected: ${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const capture = async (): Promise<void> => {
    if (running) return;
    const priorityPortalIds = ids(priorityPortals);
    if (mode === 'calibration' && priorityPortalIds.length === 0) {
      setStatus('Calibration mode requires at least one portal ID.');
      return;
    }
    if (!Number.isSafeInteger(maxContextProbes) || maxContextProbes <= 0 || !Number.isSafeInteger(maxProbesPerPortal) || maxProbesPerPortal <= 0) {
      setStatus('Probe limits must be positive safe integers.');
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setRunning(true);
    try {
      const snapshot = await buildSnapshot(plugin as unknown as SnapshotPlugin, setStatus, {
        signal: controller.signal,
        mode,
        priorityPortalIds,
        classificationRecordIds: ids(classificationIds),
        systemDefinitionRecordIds: ids(systemDefinitionIds),
        detailRecordIds: ids(detailIds),
        expectedHiddenByPortal: idArrayMap(
          JSON.parse(expectedHiddenByPortal) as unknown,
          'expectedHiddenByPortal',
        ),
        portalProbeSeedsByPortal: idArrayMap(
          JSON.parse(portalProbeSeedsByPortal) as unknown,
          'portalProbeSeedsByPortal',
        ),
        maxContextProbes,
        maxProbesPerPortal,
        ordinaryOrderValidated,
        searchOrderValidated,
        visibilitySemanticsValidated,
        richTextFingerprintCalibrated,
        childOrderCalibrated,
      });
      if (controller.signal.aborted) return;
      const filename = downloadSnapshot(snapshot);
      setStatus(`Downloaded ${filename}. Migration complete for this ${mode} scope: ${snapshot.capture.migration_complete ? 'yes' : 'no'}; diagnostics complete: ${snapshot.capture.diagnostics_complete ? 'yes' : 'no'}; ${snapshot.errors.length} explicit issue${snapshot.errors.length === 1 ? '' : 's'}.`);
    } catch (error) {
      setStatus(error instanceof DOMException && error.name === 'AbortError' ? 'Capture cancelled. No file was downloaded.' : `Capture failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      setRunning(false);
    }
  };

  const checkbox = (checked: boolean, setter: (value: boolean) => void, text: string): JSX.Element => (
    <label style={{ display: 'block', marginBottom: 8, fontSize: 12, lineHeight: 1.4 }}>
      <input type="checkbox" checked={checked} disabled={running} onChange={(event) => setter(event.target.checked)} style={{ marginRight: 6 }} />{text}
    </label>
  );

  return (
    <div style={{ padding: 12, fontFamily: 'system-ui, sans-serif' }}>
      <h3 style={{ margin: '0 0 8px' }}>PKMigrator snapshot</h3>
      <p style={{ margin: '0 0 12px', lineHeight: 1.4 }}>Reads this knowledge base and downloads one JSON file locally. It never edits Rems or uploads the snapshot.</p>
      <label style={{ display: 'block', marginBottom: 12 }}><span style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>Capture mode</span><select value={mode} disabled={running} onChange={(event) => changeMode(event.target.value as CaptureMode)}><option value="calibration">Calibration: selected portals only</option><option value="complete">Complete migration: every portal</option></select></label>
      <label style={{ display: 'block', marginBottom: 12 }}><span style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>Capture settings or document-candidates JSON</span><input type="file" accept="application/json,.json" disabled={running} onChange={(event) => loadSettingsFile(event.target.files?.[0])} /></label>
      <label style={{ display: 'block', marginBottom: 12 }}><span style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>Portal IDs (required for calibration; priority in complete mode)</span><textarea value={priorityPortals} disabled={running} onChange={(event) => setPriorityPortals(event.target.value)} rows={3} style={{ boxSizing: 'border-box', width: '100%', padding: 6 }} /></label>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}><label style={{ flex: 1, fontSize: 12 }}>Total context-probe limit<input type="number" min={1} step={1} value={maxContextProbes} disabled={running} onChange={(event) => setMaxContextProbes(Number(event.target.value))} style={{ boxSizing: 'border-box', width: '100%' }} /></label><label style={{ flex: 1, fontSize: 12 }}>Per-portal probe limit<input type="number" min={1} step={1} value={maxProbesPerPortal} disabled={running} onChange={(event) => setMaxProbesPerPortal(Number(event.target.value))} style={{ boxSizing: 'border-box', width: '100%' }} /></label></div>
      {checkbox(ordinaryOrderValidated, setOrdinaryOrderValidated, 'I compared ordinary-portal SDK member order with the live UI.')}
      {checkbox(searchOrderValidated, setSearchOrderValidated, 'I compared search/backlink SDK result order with the live UI.')}
      {checkbox(visibilitySemanticsValidated, setVisibilitySemanticsValidated, 'I compared hidden/included/none getter results with live portal state.')}
      {checkbox(richTextFingerprintCalibrated, setRichTextFingerprintCalibrated, 'I compared canonical SDK rich-text fingerprints with the matching raw export records.')}
      {checkbox(childOrderCalibrated, setChildOrderCalibrated, 'I compared SDK child array order with raw fractional-f sibling order.')}
      <label style={{ display: 'block', marginBottom: 12 }}><span style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>Known hidden IDs by portal, as JSON (optional)</span><textarea value={expectedHiddenByPortal} disabled={running} onChange={(event) => setExpectedHiddenByPortal(event.target.value)} rows={3} style={{ boxSizing: 'border-box', width: '100%', padding: 6 }} /></label>
      <label style={{ display: 'block', marginBottom: 12 }}><span style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>Raw probe candidate IDs by portal, as JSON (optional)</span><textarea value={portalProbeSeedsByPortal} disabled={running} onChange={(event) => setPortalProbeSeedsByPortal(event.target.value)} rows={3} style={{ boxSizing: 'border-box', width: '100%', padding: 6 }} /></label>
      <label style={{ display: 'block', marginBottom: 12 }}><span style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>Document/folder candidate IDs</span><textarea value={classificationIds} disabled={running} onChange={(event) => setClassificationIds(event.target.value)} rows={3} style={{ boxSizing: 'border-box', width: '100%', padding: 6 }} /></label>
      <label style={{ display: 'block', marginBottom: 12 }}><span style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>System-definition candidate IDs</span><textarea value={systemDefinitionIds} disabled={running} onChange={(event) => setSystemDefinitionIds(event.target.value)} rows={3} style={{ boxSizing: 'border-box', width: '100%', padding: 6 }} /></label>
      <label style={{ display: 'block', marginBottom: 12 }}><span style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>Extra rich-record IDs</span><textarea value={detailIds} disabled={running} onChange={(event) => setDetailIds(event.target.value)} rows={3} style={{ boxSizing: 'border-box', width: '100%', padding: 6 }} /></label>
      <div style={{ display: 'flex', gap: 8 }}><button type="button" disabled={running} onClick={capture} style={{ padding: '8px 12px' }}>{running ? 'Capturing…' : 'Download read-only snapshot'}</button>{running ? <button type="button" onClick={() => { setStatus('Cancelling after the current SDK call…'); controllerRef.current?.abort(); }} style={{ padding: '8px 12px' }}>Cancel</button> : null}</div>
      <p aria-live="polite" style={{ margin: '12px 0 0', fontSize: 12, lineHeight: 1.4 }}>{status}</p>
    </div>
  );
}

renderWidget(SnapshotWidget);
