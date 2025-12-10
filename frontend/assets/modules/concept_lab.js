import { state } from './state.js';

const MAX_CONTEXT_CHARS = 280;

export function initConceptLab(refs, api, projects) {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function getActiveProject() {
    const activeId = state.activeProjectId;
    if (!activeId || activeId === 'all') {
      return null;
    }
    return state.projects.find((project) => String(project.id) === String(activeId)) || null;
  }

  function isRandomSliceSelected() {
    if (!refs.ragConceptSelect) return false;
    return refs.ragConceptSelect.value === '__random__';
  }

  function setStatus(message = '', tone = 'muted') {
    if (!refs.ragStatus) return;
    refs.ragStatus.textContent = message;
    refs.ragStatus.classList.remove('error', 'success');
    if (!message) return;
    if (tone === 'error') {
      refs.ragStatus.classList.add('error');
    } else if (tone === 'success') {
      refs.ragStatus.classList.add('success');
    }
  }

  function updateHint(project) {
    if (!refs.ragProjectHint) return;
    if (!project) {
      refs.ragProjectHint.textContent = 'Pick a book to start shaping a new idea.';
      return;
    }
    if (!project.supabase_book_id) {
      refs.ragProjectHint.textContent = 'Indexing this book. Concept Lab unlocks when it finishes reading.';
      return;
    }
    refs.ragProjectHint.textContent = `Working from: ${project.title}`;
  }

  function setButtonReady(isReady) {
    if (!refs.ragGenerateBtn) return;
    refs.ragGenerateBtn.dataset.ready = isReady ? 'true' : 'false';
    if (refs.ragGenerateBtn.dataset.loading === 'true') {
      return;
    }
    refs.ragGenerateBtn.disabled = !isReady;
  }

  function applyRandomSliceContext(baseEnabled) {
    if (!refs.ragContextInput) return;
    if (isRandomSliceSelected()) {
      refs.ragContextInput.value = '';
      refs.ragContextInput.disabled = true;
    } else {
      refs.ragContextInput.disabled = !baseEnabled;
    }
    updateCharCount();
  }

  function evaluateReadyState() {
    const project = getActiveProject();
    const hasBook = Boolean(project?.supabase_book_id);
    const contextValue = (refs.ragContextInput?.value || '').trim();
    const hasSelectedConcept = Boolean(normalizedConceptSelection());
    const ready =
      hasBook &&
      (isRandomSliceSelected() || contextValue.length > 0 || hasSelectedConcept);
    setButtonReady(ready);
  }

  function setLoading(isLoading) {
    if (!refs.ragGenerateBtn) return;
    refs.ragGenerateBtn.dataset.loading = isLoading ? 'true' : 'false';
    refs.ragGenerateBtn.classList.toggle('loading', Boolean(isLoading));
    refs.ragGenerateBtn.disabled = true;
    const label = refs.ragGenerateBtn.querySelector('.rag-btn-label');
    if (label) {
      label.textContent = isLoading ? 'Generating…' : 'Generate Concept';
    }
    if (!isLoading) {
      const ready = refs.ragGenerateBtn.dataset.ready === 'true';
      refs.ragGenerateBtn.disabled = !ready;
    }
  }

  function updateCharCount() {
    if (!refs.ragContextInput || !refs.ragCharCount) return;
    const current = refs.ragContextInput.value.length;
    refs.ragCharCount.textContent = `${current} / ${MAX_CONTEXT_CHARS}`;
    evaluateReadyState();
  }

  function populateConceptSelector() {
    if (!refs.ragConceptSelect) return;
    const project = getActiveProject();
    refs.ragConceptSelect.innerHTML = `
      <option value="__none__">No scene – use my direction only</option>
      <option value="__random__">Select emotionally charged passage from book</option>
    `;
    if (!project) {
      refs.ragConceptSelect.disabled = true;
      if (refs.ragContextInput) {
        refs.ragContextInput.disabled = true;
        refs.ragContextInput.value = '';
      }
      updateHint(null);
      setButtonReady(false);
      updateCharCount();
      return;
    }
    const concepts = project.concepts || [];
    concepts.forEach((concept) => {
      const option = document.createElement('option');
      option.value = concept.id;
      option.textContent = concept.name;
      refs.ragConceptSelect.appendChild(option);
    });
    refs.ragConceptSelect.disabled = false;
    applyRandomSliceContext(true);
    updateHint(project);
    evaluateReadyState();
  }

  function normalizedConceptSelection() {
    if (!refs.ragConceptSelect) return undefined;
    const value = refs.ragConceptSelect.value;
    if (!value || value === '__none__' || value === '__random__') {
      return undefined;
    }
    return Number(value);
  }

  async function handleGenerate() {
    const project = getActiveProject();
    if (!project) {
      setStatus('Select a book first.', 'error');
      return;
    }
    if (!project.supabase_book_id) {
      setStatus('Still reading this book. Try again once indexing finishes.', 'error');
      return;
    }
    const randomSliceSelected = isRandomSliceSelected();
    const context = randomSliceSelected ? '' : (refs.ragContextInput?.value || '').trim();
    const conceptId = randomSliceSelected ? undefined : normalizedConceptSelection();
    if (!randomSliceSelected && !conceptId && !context) {
      setStatus('Add a short creative brief or pick a concept to mirror first.', 'error');
      return;
    }
    setStatus('');
    setLoading(true);
    try {
      const res = await api.authenticatedFetch(`/api/projects/${project.id}/concepts/rag`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          context,
          concept_id: conceptId,
          random_slice: randomSliceSelected,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.job) {
        throw new Error(data?.error || 'Failed to start concept generation.');
      }
      const job = data.job;
      if (!job?.job_id) {
        throw new Error('Concept job did not return an id.');
      }
      const finalJob = await awaitConceptJob(job);
      setStatus('New concept generated!', 'success');
      const createdConceptId = finalJob?.concept_ids?.[0];
      await projects.fetchProjects();
      if (createdConceptId && project.id) {
        state.selectedConcepts[project.id] = createdConceptId;
      }
      refs.ragContextInput?.focus();
    } catch (error) {
      console.error(error);
      setStatus(error.message || 'Unable to generate concept.', 'error');
    } finally {
      setLoading(false);
      evaluateReadyState();
    }
  }

  async function awaitConceptJob(initialJob) {
    const status = (initialJob?.status || '').toLowerCase();
    if (status === 'succeeded') {
      return initialJob;
    }
    const jobId = initialJob?.job_id;
    if (!jobId) {
      throw new Error('Concept job was missing an id.');
    }
    return pollConceptJob(jobId);
  }

  async function pollConceptJob(jobId) {
    const maxWaitMs = 5 * 60 * 1000;
    const pollInterval = 2500;
    const startedAt = Date.now();
    while (Date.now() - startedAt < maxWaitMs) {
      await sleep(pollInterval);
      const response = await api.authenticatedFetch(`/api/concept-jobs/${jobId}`, { cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data?.job) {
        throw new Error(data?.error || 'Concept job not found.');
      }
      const job = data.job;
      const status = (job.status || '').toLowerCase();
      if (status === 'succeeded') {
        return job;
      }
      if (status === 'failed') {
        throw new Error(job.error || 'Concept generation failed.');
      }
      setStatus(`Concept Lab ${status === 'processing' ? 'working…' : 'queued…'}`);
    }
    throw new Error('Concept generation timed out. Please try again.');
  }

  function bind() {
    refs.ragGenerateBtn?.addEventListener('click', handleGenerate);
    refs.ragConceptSelect?.addEventListener('change', () => {
      setStatus('');
      applyRandomSliceContext(Boolean(getActiveProject()));
      evaluateReadyState();
    });
    refs.ragContextInput?.addEventListener('input', updateCharCount);
    document.addEventListener('litr-projects-updated', () => {
      populateConceptSelector();
      updateCharCount();
    });
    document.addEventListener('litr-active-project-changed', () => {
      populateConceptSelector();
      updateCharCount();
    });
    populateConceptSelector();
    updateCharCount();
  }

  return { bind, populateConceptSelector };
}
