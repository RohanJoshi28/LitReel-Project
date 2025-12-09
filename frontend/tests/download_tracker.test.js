import test from 'node:test';
import assert from 'node:assert/strict';

import { state } from '../assets/modules/state.js';
import { setDownloadState, getDownloadState, clearDownloadState } from '../assets/modules/download_tracker.js';

test('setDownloadState merges and returns the updated entry', () => {
  state.activeDownloads = {};
  const first = setDownloadState(7, 22, { isLoading: true, label: 'Queued…' });
  assert.equal(first.label, 'Queued…');
  assert.ok(first.isLoading);

  const second = setDownloadState(7, 22, { label: 'Rendering…', status: 'processing' });
  assert.equal(second.label, 'Rendering…');
  assert.equal(getDownloadState(7, 22).status, 'processing');
});

test('clearDownloadState removes entries and getDownloadState returns null when absent', () => {
  state.activeDownloads = {};
  setDownloadState('project-1', 'concept-5', { isLoading: true });
  assert.ok(getDownloadState('project-1', 'concept-5'));
  clearDownloadState('project-1', 'concept-5');
  assert.equal(getDownloadState('project-1', 'concept-5'), null);
});
