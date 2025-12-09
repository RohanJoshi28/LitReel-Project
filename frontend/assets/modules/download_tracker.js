import { state } from './state.js';

function ensureMap() {
  if (!state.activeDownloads) {
    state.activeDownloads = {};
  }
  return state.activeDownloads;
}

function normalizeKey(projectId, conceptId) {
  if (projectId === null || typeof projectId === 'undefined') return null;
  if (conceptId === null || typeof conceptId === 'undefined') return null;
  return `${projectId}:${conceptId}`;
}

export function setDownloadState(projectId, conceptId, patch = {}) {
  const key = normalizeKey(projectId, conceptId);
  if (!key) return null;
  const map = ensureMap();
  const next = { ...(map[key] || {}), ...patch };
  map[key] = next;
  return next;
}

export function getDownloadState(projectId, conceptId) {
  const key = normalizeKey(projectId, conceptId);
  if (!key) return null;
  const map = state.activeDownloads;
  if (!map) return null;
  return map[key] || null;
}

export function clearDownloadState(projectId, conceptId) {
  const key = normalizeKey(projectId, conceptId);
  if (!key) return;
  const map = state.activeDownloads;
  if (!map || !Object.prototype.hasOwnProperty.call(map, key)) return;
  delete map[key];
}
