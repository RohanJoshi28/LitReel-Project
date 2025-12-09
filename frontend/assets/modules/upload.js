import { state } from './state.js';

const PENDING_UPLOAD_STATUSES = new Set(['pending', 'processing', 'draft', 'queued']);
const FAILURE_UPLOAD_STATUSES = new Set(['failed']);

export function initUpload(refs, api, projects, nav = {}) {
  const uploadLog = (...args) => console.info('[upload]', ...args);

  function setGenerateButtonLoading(isLoading) {
    if (!refs.generateBtn) return;
    toggleUploadOverlay(isLoading);
    const label = refs.generateBtn.querySelector('.generate-btn-label');
    if (label && !refs.generateBtn.dataset.defaultLabel) {
      refs.generateBtn.dataset.defaultLabel = label.textContent || 'Generate Slides';
    }
    if (isLoading) {
      refs.generateBtn.classList.add('loading');
      refs.generateBtn.disabled = true;
      refs.generateBtn.setAttribute('aria-busy', 'true');
      if (label) {
        label.textContent = 'Generating…';
      }
    } else {
      refs.generateBtn.classList.remove('loading');
      refs.generateBtn.disabled = false;
      refs.generateBtn.removeAttribute('aria-busy');
      if (label) {
        label.textContent = refs.generateBtn.dataset.defaultLabel || 'Generate Slides';
      }
    }
  }

  function toggleUploadOverlay(isVisible) {
    if (!refs.uploadOverlay) return;
    refs.uploadOverlay.classList.toggle('active', Boolean(isVisible));
    refs.uploadOverlay.setAttribute('aria-hidden', isVisible ? 'false' : 'true');
  }

  function setGenerateEnabled(isEnabled) {
    if (!refs.generateBtn) return;
    refs.generateBtn.disabled = !isEnabled;
    refs.generateBtn.setAttribute('aria-disabled', isEnabled ? 'false' : 'true');
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let latestUploadSnapshot = new Set();

  function normalizeStatus(status) {
    return typeof status === 'string' ? status.toLowerCase() : '';
  }

  function isPendingStatus(status) {
    return PENDING_UPLOAD_STATUSES.has(normalizeStatus(status));
  }

  function isFailureStatus(status) {
    return FAILURE_UPLOAD_STATUSES.has(normalizeStatus(status));
  }

  function beginActiveUpload(tempProjectId = null) {
    state.activeUploadProjectId = tempProjectId ? String(tempProjectId) : '__uploading__';
    setGenerateButtonLoading(true);
    setGenerateEnabled(false);
    uploadLog('beginActiveUpload', { projectId: state.activeUploadProjectId });
  }

  function clearActiveUploadLock() {
    uploadLog('clearActiveUploadLock', { fileAttached: Boolean(refs.uploadFileInput?.files?.length) });
    state.activeUploadProjectId = null;
    setGenerateButtonLoading(false);
    setGenerateEnabled(Boolean(refs.uploadFileInput?.files && refs.uploadFileInput.files.length));
  }

  function hasActiveGeneration() {
    if (projects?.hasActiveProjectGeneration) {
      return Boolean(projects.hasActiveProjectGeneration());
    }
    if (!Array.isArray(state.projects)) {
      return false;
    }
    const pending = state.projects.some((project) => isPendingStatus(project?.status));
    uploadLog('hasActiveGeneration', { pending, projectCount: state.projects.length });
    return pending;
  }

  function waitForProjectStateCompletion(
    projectId,
    { timeoutMs = 900000, pollIntervalMs = 2000 } = {},
  ) {
    if (!projectId) {
      return Promise.resolve('missing');
    }
    const doc = typeof document !== 'undefined' ? document : null;
    const win = typeof window !== 'undefined' ? window : null;
    if (!doc || !win) {
      return Promise.resolve('unknown');
    }
    const normalizedId = String(projectId);
    uploadLog('waitForProjectStateCompletion:start', { projectId: normalizedId, timeoutMs, pollIntervalMs });

    return new Promise((resolve) => {
      const start = Date.now();
      let settled = false;
      let intervalId = null;

      const finish = (status) => {
        if (settled || !status) return;
        settled = true;
        if (intervalId) {
          win.clearInterval(intervalId);
        }
        doc.removeEventListener('litr-projects-updated', handleUpdate);
        uploadLog('waitForProjectStateCompletion:finish', { projectId: normalizedId, status, elapsedMs: Date.now() - start });
        resolve(status);
      };

      const evaluate = (projectsList = state.projects) => {
        const project = (projectsList || []).find((proj) => String(proj?.id) === normalizedId);
        if (!project) {
          uploadLog('waitForProjectStateCompletion:evaluate-miss', { projectId: normalizedId });
          return null;
        }
        const status = normalizeStatus(project.status);
        if (isFailureStatus(status)) {
          return 'failed';
        }
        return isPendingStatus(status) ? null : 'ready';
      };

      const handleUpdate = (event) => {
        const list = (event?.detail && event.detail.projects) || state.projects;
        const result = evaluate(list);
        if (result) {
          uploadLog('waitForProjectStateCompletion:event-update', { projectId: normalizedId, status: result });
          finish(result);
        }
      };

      const tick = () => {
        const result = evaluate();
        if (result) {
          finish(result);
          return;
        }
        if (Date.now() - start >= timeoutMs) {
          finish('timeout');
        }
      };

      doc.addEventListener('litr-projects-updated', handleUpdate);
      const first = evaluate();
      if (first) {
        uploadLog('waitForProjectStateCompletion:immediate', { projectId: normalizedId, status: first });
        finish(first);
        return;
      }
      intervalId = win.setInterval(tick, pollIntervalMs);
    });
  }

  async function waitForUploadCompletion(projectId) {
    let targetId = projectId;
    if (!targetId) {
      targetId = await waitForDetectedProjectId(latestUploadSnapshot);
      if (targetId) {
        state.activeUploadProjectId = targetId;
      }
    }
    uploadLog('waitForUploadCompletion:start', { providedProjectId: projectId, resolvedProjectId: targetId });
    if (!targetId) {
      return 'missing';
    }
    const stateResult = await waitForProjectStateCompletion(targetId);
    uploadLog('waitForUploadCompletion:state-result', { projectId: targetId, stateResult });
    if (stateResult === 'ready' || stateResult === 'failed') {
      return stateResult;
    }
    if (projects?.waitForProjectReady) {
      try {
        const ready = await projects.waitForProjectReady(targetId);
        uploadLog('waitForUploadCompletion:fallback-wait', { projectId: targetId, ready });
        return ready ? 'ready' : 'failed';
      } catch (error) {
        console.error('[upload] waitForUploadCompletion fallback error', error);
      }
    }
    return stateResult || 'unknown';
  }

  async function waitForDetectedProjectId(
    existingIds = new Set(),
    { timeoutMs = 60000, pollIntervalMs = 2000 } = {},
  ) {
    const start = Date.now();
    uploadLog('waitForDetectedProjectId:start', { snapshotSize: existingIds.size, timeoutMs, pollIntervalMs });
    while (Date.now() - start < timeoutMs) {
      const detected = detectNewProjectId(existingIds);
      if (detected) {
        uploadLog('waitForDetectedProjectId:found', { projectId: detected, elapsedMs: Date.now() - start });
        return detected;
      }
      if (projects?.fetchProjects) {
        try {
          await projects.fetchProjects({ silent: true });
        } catch (error) {
          console.error('[upload] waitForDetectedProjectId fetchProjects error', error);
        }
      }
      await sleep(pollIntervalMs);
    }
    uploadLog('waitForDetectedProjectId:timeout', { elapsedMs: Date.now() - start });
    return null;
  }

  function snapshotProjectIds(projectsList = state.projects) {
    const snapshot = new Set();
    if (!Array.isArray(projectsList)) {
      return snapshot;
    }
    projectsList.forEach((project) => {
      if (project?.id != null) {
        snapshot.add(String(project.id));
      }
    });
    return snapshot;
  }

  function detectNewProjectId(existingIds = new Set(), projectsList = state.projects) {
    if (!Array.isArray(projectsList) || !projectsList.length) {
      return null;
    }
    const unseenProject = projectsList.find((project) => {
      const id = project?.id;
      return id != null && !existingIds.has(String(id));
    });
    if (unseenProject?.id != null) {
      return String(unseenProject.id);
    }
    const sorted = projectsList
      .filter((project) => project?.id != null)
      .slice()
      .sort((a, b) => {
        const aTime = new Date(a.created_at || 0).getTime();
        const bTime = new Date(b.created_at || 0).getTime();
        return bTime - aTime;
      });
    const fallback = sorted[0];
    return fallback?.id != null ? String(fallback.id) : null;
  }

  function onUploadFileChange() {
    const file = refs.uploadFileInput?.files && refs.uploadFileInput.files[0] ? refs.uploadFileInput.files[0] : null;
    if (file) {
      uploadLog('uploadFileChange:file-selected', { name: file.name, size: file.size });
      projects.updateUploadFileSelection(file);
      refs.uploadStatus.textContent = 'Document attached. Click Generate Slides when ready.';
      const titleInput = refs.uploadForm?.querySelector('input[name="title"]');
      if (titleInput && !titleInput.value.trim()) {
        const name = file.name.replace(/\.[^.]+$/i, '');
        titleInput.value = name;
      }
      setGenerateEnabled(true);
    } else {
      uploadLog('uploadFileChange:file-cleared');
      projects.updateUploadFileSelection(null);
      refs.uploadStatus.textContent = '';
      setGenerateEnabled(false);
    }
  }

  function bindUploadListeners() {
    refs.uploadFileInput?.addEventListener('change', onUploadFileChange);
    refs.uploadForm?.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!state.user) {
        refs.uploadStatus.textContent = 'Please log in before uploading.';
        api.handleUnauthorized('Log in to upload a book.');
        return;
      }
      if (hasActiveGeneration()) {
        refs.uploadStatus.textContent =
          'Please wait for your current upload to finish generating before starting another book.';
        if (nav.goToTab) {
          nav.goToTab('books');
        }
        return;
      }
      const previousProjectIds = snapshotProjectIds();
      latestUploadSnapshot = new Set(previousProjectIds);
      uploadLog('uploadSubmit:start', {
        fileCount: refs.uploadFileInput?.files?.length || 0,
        previousProjectCount: previousProjectIds.size,
      });
      beginActiveUpload();
      let navigateAfterSpinner = false;
      const formData = new FormData(refs.uploadForm);
      refs.uploadStatus.textContent = 'Uploading and generating slides…';
      let createdProjectId = null;
      let isInline = false;
      try {
        const res = await api.authenticatedFetch('/api/projects', {
          method: 'POST',
          body: formData,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data.error || 'Upload failed');
        }
        if (data?.project?.id != null) {
          createdProjectId = String(data.project.id);
          state.activeUploadProjectId = createdProjectId;
        }
        const jobStatus = (data?.job?.status || '').toLowerCase();
        isInline = data?.job?.mode === 'inline' || jobStatus === 'inline';
        uploadLog('uploadSubmit:response', {
          projectId: createdProjectId,
          isInline,
          jobStatus,
          jobMode: data?.job?.mode,
        });
        if (isInline) {
          refs.uploadStatus.textContent = 'Slides generated successfully!';
        } else {
          refs.uploadStatus.textContent =
            'Upload received! Slides are generating — we’ll keep polling until they’re ready.';
        }
        refs.uploadForm.reset();
        projects.updateUploadFileSelection(null);
        await projects.fetchProjects();
        // Always show all projects after generation so existing ones remain visible.
        projects.setActiveProjectView('all');
          if (!createdProjectId) {
            const detected = detectNewProjectId(previousProjectIds);
            if (detected) {
              createdProjectId = detected;
              state.activeUploadProjectId = detected;
            } else {
              console.warn('Upload succeeded but the new project id could not be detected for polling.');
              uploadLog('uploadSubmit:missing-project-id');
            }
          }
        state.latestCreatedProjectId = createdProjectId;
        if (!isInline) {
          const finalState = await waitForUploadCompletion(createdProjectId);
          if (finalState === 'ready') {
            refs.uploadStatus.textContent = 'Slides generated successfully!';
          } else if (finalState === 'failed') {
            refs.uploadStatus.textContent =
              'We could not generate slides for this upload. Please try again from the Books tab.';
          } else if (finalState === 'missing') {
            refs.uploadStatus.textContent =
              'Upload finished but the book is not visible yet. Refresh the dashboard to continue.';
          } else if (finalState === 'timeout') {
            refs.uploadStatus.textContent =
              'Still working on your slides… we’ll keep checking, or you can monitor the Books tab.';
          } else {
            refs.uploadStatus.textContent =
              'Still working on your slides… check the Books tab for updates.';
          }
          navigateAfterSpinner = true;
        }
        if (isInline) {
          navigateAfterSpinner = true;
        }
      } catch (error) {
        console.error(error);
        uploadLog('uploadSubmit:error', { message: error?.message });
        refs.uploadStatus.textContent = error.message || 'Upload failed';
      } finally {
        clearActiveUploadLock();
        if (navigateAfterSpinner && nav.goToTab) {
          nav.goToTab('books');
        }
      }
    });
    // start disabled until a file is chosen
    setGenerateEnabled(Boolean(refs.uploadFileInput?.files && refs.uploadFileInput.files.length));
  }

  return { bindUploadListeners, onUploadFileChange };
}
