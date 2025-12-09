import { state } from './state.js';

function ensureMap() {
  if (!state.renderJobs) {
    state.renderJobs = {};
  }
  return state.renderJobs;
}

function normalizeKey(projectId, conceptId) {
  if (!projectId || !conceptId) return null;
  return `${projectId}:${conceptId}`;
}

export function setRenderState(projectId, conceptId, patch = {}) {
  const key = normalizeKey(projectId, conceptId);
  if (!key) return null;
  const map = ensureMap();
  const next = { ...(map[key] || {}), ...patch };
  map[key] = next;
  return next;
}

export function getRenderState(projectId, conceptId) {
  const key = normalizeKey(projectId, conceptId);
  if (!key || !state.renderJobs) return null;
  return state.renderJobs[key] || null;
}

export function clearRenderState(projectId, conceptId) {
  const key = normalizeKey(projectId, conceptId);
  if (!key || !state.renderJobs || !state.renderJobs[key]) return;
  delete state.renderJobs[key];
}
