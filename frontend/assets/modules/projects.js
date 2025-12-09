import { state, FILE_META_DEFAULT } from './state.js';
import { formatFileSize } from './utils.js';
import { getDownloadState } from './download_tracker.js';
import { getRenderState } from './render_tracker.js';
import Sortable from '../vendor/sortable.esm.js';

export function initProjects(refs, api, editor, tts, nav = {}) {
  const errorBanner = document.createElement('div');
  errorBanner.className = 'status error';
  if (refs.projectsContainer?.parentElement) {
    refs.projectsContainer.parentElement.insertBefore(errorBanner, refs.projectsContainer);
  }

  const PENDING_STATUSES = new Set(['pending', 'processing', 'draft', 'queued']);
  const ACTIVE_GENERATION_STATUSES = new Set(['pending', 'processing', 'queued']);
  const FAILURE_STATUSES = new Set(['failed']);
  let pendingPollTimer = null;
  let renderPendingAfterPreview = false;

  const normalizeStatus = (status) => (status || '').toLowerCase();

  function findProjectById(projectId) {
    if (projectId == null) return null;
    return (
      state.projects.find((project) => String(project.id) === String(projectId)) || null
    );
  }

  function getActiveNarrationPreviewAudios() {
    if (!refs.projectsContainer) return [];
    return Array.from(refs.projectsContainer.querySelectorAll('.tts-preview-audio')).filter(
      (audio) => !audio.paused && !audio.ended,
    );
  }

  function queueRenderAfterPreviewStops() {
    const activeAudios = getActiveNarrationPreviewAudios();
    if (!activeAudios.length) {
      return false;
    }
    if (renderPendingAfterPreview) {
      return true;
    }
    renderPendingAfterPreview = true;
    const resumeRender = () => {
      if (getActiveNarrationPreviewAudios().length > 0) {
        return;
      }
      if (!renderPendingAfterPreview) {
        return;
      }
      renderPendingAfterPreview = false;
      renderProjects();
    };
    activeAudios.forEach((audio) => {
      audio.addEventListener('ended', resumeRender, { once: true });
      audio.addEventListener('pause', resumeRender, { once: true });
    });
    return true;
  }

  function restoreDownloadButtonState(projectId, conceptId, button) {
    if (!button) {
      return;
    }
    const tracked = getDownloadState(projectId, conceptId);
    if (!tracked || !tracked.isLoading) {
      return;
    }
    button.classList.add('loading');
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    const label = button.querySelector('.download-btn-label');
    if (label) {
      label.textContent = tracked.label || 'Rendering…';
    }
  }

  function restoreRenderButtonState(projectId, conceptId, button) {
    if (!button) return;
    const tracked = getRenderState(projectId, conceptId);
    if (!tracked || !tracked.isLoading) return;
    button.classList.add('loading');
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    const label = button.querySelector('.render-btn-label');
    if (label) {
      label.textContent = tracked.label || 'Rendering…';
    }
  }

  function conceptHasReadyRender(concept) {
    if (!concept || !concept.latest_render) return false;
    const status = (concept.latest_render.status || '').toLowerCase();
    return status === 'ready';
  }

  function hasPendingProjects() {
    return state.projects.some((project) => PENDING_STATUSES.has(normalizeStatus(project.status)));
  }

  function hasActiveProjectGeneration() {
    return state.projects.some((project) =>
      ACTIVE_GENERATION_STATUSES.has(normalizeStatus(project.status)),
    );
  }

  function isProjectPending(projectId) {
    const project = findProjectById(projectId);
    if (!project) return false;
    return PENDING_STATUSES.has(normalizeStatus(project.status));
  }

  function isProjectActivelyGenerating(projectId) {
    const project = findProjectById(projectId);
    if (!project) return false;
    return ACTIVE_GENERATION_STATUSES.has(normalizeStatus(project.status));
  }

  function stopPendingPoller() {
    if (pendingPollTimer) {
      window.clearInterval(pendingPollTimer);
      pendingPollTimer = null;
    }
  }

  function ensurePendingPoller() {
    const needsPoll = hasPendingProjects();
    if (needsPoll && !pendingPollTimer) {
      pendingPollTimer = window.setInterval(() => {
        fetchProjects({ silent: true, deferRenderIfPreviewPlaying: true });
      }, 5000);
    } else if (!needsPoll && pendingPollTimer) {
      stopPendingPoller();
    }
  }

  const goToTab = nav.goToTab || (() => {});

  function setSlidesPanelsVisibility(isVisible) {
    const conceptPanel = document.getElementById('concept-lab-panel');
    if (conceptPanel) {
      conceptPanel.classList.toggle('hidden', !isVisible);
      conceptPanel.setAttribute('aria-hidden', isVisible ? 'false' : 'true');
    }
    if (refs.projectFilterSelect) {
      refs.projectFilterSelect.disabled = !isVisible || !state.projects.length;
    }
  }

  function formatProjectStatus(status) {
    const normalized = normalizeStatus(status);
    if (PENDING_STATUSES.has(normalized)) {
      return 'Generating slides…';
    }
    if (FAILURE_STATUSES.has(normalized)) {
      return 'Generation failed';
    }
    if (normalized === 'generated-local') return '';
    if (normalized === 'generated') return '';
    return normalized ? normalized : 'Unknown';
  }

  function syncProjectFilterSelection(preferredValue = 'all') {
    if (!refs.projectFilterSelect) return preferredValue;
    const values = Array.from(refs.projectFilterSelect.options).map((option) => option.value);
    if (values.includes(preferredValue)) {
      refs.projectFilterSelect.value = preferredValue;
      return preferredValue;
    }
    const fallback = values.find((v) => v) || '';
    refs.projectFilterSelect.value = fallback;
    return fallback;
  }

  function setActiveProjectView(value = '', { render = true } = {}) {
    const normalized = value ? String(value) : '';
    state.activeProjectId = syncProjectFilterSelection(normalized);
    document.dispatchEvent(
      new CustomEvent('litr-active-project-changed', { detail: { projectId: state.activeProjectId } }),
    );
    updateDeleteProjectButtonVisibility();
    if (render) {
      renderProjects();
    }
  }

  function updateCounts(projects = state.projects) {
    const totalSlides = projects.reduce((projTotal, project) => {
      const concepts = project.concepts || [];
      return (
        projTotal +
        concepts.reduce(
          (conceptTotal, concept) => conceptTotal + (concept.slides || []).length,
          0,
        )
      );
    }, 0);
    if (refs.bookCountEl) refs.bookCountEl.textContent = projects.length;
    if (refs.slideCountEl) refs.slideCountEl.textContent = totalSlides;
  }

  function updateDeleteProjectButtonVisibility() {
    if (!refs.deleteProjectBtn) return;
    const selectedId = state.activeProjectId;
    const isLoading = refs.deleteProjectBtn.dataset.loading === 'true';
    const selectedProject =
      state.projects.find((project) => String(project.id) === String(selectedId)) || null;
    const hasSelection = Boolean(state.user) && Boolean(selectedProject);
    const shouldDisplay = (hasSelection || isLoading) && Boolean(state.user);

    refs.deleteProjectBtn.classList.toggle('visible', shouldDisplay);
    refs.deleteProjectBtn.disabled = !hasSelection || isLoading;
    refs.deleteProjectBtn.setAttribute('aria-hidden', shouldDisplay ? 'false' : 'true');

    if (hasSelection && selectedProject) {
      refs.deleteProjectBtn.removeAttribute('tabindex');
      refs.deleteProjectBtn.title = `Delete "${selectedProject.title}"`;
      refs.deleteProjectBtn.setAttribute(
        'aria-label',
        `Delete ${selectedProject.title} and all related slideshows`,
      );
    } else {
      refs.deleteProjectBtn.setAttribute('tabindex', '-1');
      refs.deleteProjectBtn.title = 'Select a book to delete';
      refs.deleteProjectBtn.setAttribute('aria-label', 'Delete selected book');
    }
  }

  function setDeleteProjectButtonLoading(isLoading) {
    if (!refs.deleteProjectBtn) return;
    refs.deleteProjectBtn.dataset.loading = isLoading ? 'true' : 'false';
    refs.deleteProjectBtn.classList.toggle('loading', Boolean(isLoading));
    updateDeleteProjectButtonVisibility();
  }

  function updateUploadFileSelection(file, { message } = {}) {
    if (!refs.uploadFileMeta || !refs.uploadFileField) return;
    if (file) {
      const sizeLabel = formatFileSize(file.size);
      const summary = sizeLabel ? `${file.name} • ${sizeLabel}` : file.name;
      refs.uploadFileMeta.textContent = message || `Ready: ${summary}`;
      refs.uploadFileMeta.classList.add('confirmed');
      refs.uploadFileField.classList.add('has-file');
    } else {
      refs.uploadFileMeta.textContent = message || FILE_META_DEFAULT;
      refs.uploadFileMeta.classList.remove('confirmed');
      refs.uploadFileField.classList.remove('has-file');
    }
  }

  async function renameProject(projectId, newTitle) {
    const cleaned = (newTitle || '').trim();
    if (!cleaned) return;
    try {
      const res = await api.authenticatedFetch(`/api/projects/${projectId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: cleaned }),
      });
      const data = await res.json();
      if (!data.project) return;
      const idx = state.projects.findIndex((p) => p.id === projectId);
      if (idx !== -1) {
        state.projects[idx] = { ...state.projects[idx], title: data.project.title };
      }
      populateProjectFilter();
      renderBooksList();
      updateDeleteProjectButtonVisibility();
    } catch (err) {
      console.error(err);
    }
  }

  async function addSlide(conceptId) {
    const scrollY = window.scrollY;
    try {
      const res = await api.authenticatedFetch(`/api/concepts/${conceptId}/slides`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: '' }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || 'Unable to add slide right now.');
      }
      await fetchProjects({ preserveScroll: true });
      requestAnimationFrame(() => window.scrollTo(0, scrollY));
    } catch (err) {
      console.error(err);
      alert(err.message || 'Failed to add slide.');
    }
  }

  async function fetchProjects(options = {}) {
    const {
      silent = false,
      preserveScroll = false,
      deferRenderIfPreviewPlaying = false,
    } = options;
    const scrollY = window.scrollY;
    if (!state.user) {
      clearProjectState();
      return;
    }
    try {
      const res = await api.authenticatedFetch('/api/projects', { cache: 'no-store' });
      const data = await res.json();
      state.projects = data.projects || [];
      state.voiceSelections = state.voiceSelections || {};
      state.projects.forEach((proj) => {
        const v = ((proj.voice ?? '') || '').toLowerCase();
        state.voiceSelections[proj.id] = v;
      });
      state.selectedConcepts = state.selectedConcepts || {};
      state.projects.forEach((proj) => {
        if (proj.active_concept_id) {
          state.selectedConcepts[proj.id] = proj.active_concept_id;
        }
      });
      document.dispatchEvent(
        new CustomEvent('litr-projects-updated', { detail: { projects: state.projects } }),
      );
      errorBanner.textContent = '';
      populateProjectFilter();
      renderBooksList();
      let deferred = false;
      if (deferRenderIfPreviewPlaying) {
        deferred = queueRenderAfterPreviewStops();
      }
      if (!deferred) {
        renderPendingAfterPreview = false;
        renderProjects();
      }
      ensurePendingPoller();
      if (preserveScroll) {
        requestAnimationFrame(() => window.scrollTo(0, scrollY));
      }
    } catch (error) {
      console.error(error);
      if (silent || !state.user) return;
      errorBanner.textContent = error.message || 'Unable to load projects right now.';
      state.projects = [];
      document.dispatchEvent(
        new CustomEvent('litr-projects-updated', { detail: { projects: state.projects } }),
      );
      if (refs.projectsContainer) {
        refs.projectsContainer.innerHTML = '<p class="muted">No projects could be loaded.</p>';
      }
      updateDeleteProjectButtonVisibility();
      updateCounts([]);
      if (refs.booksList) {
        refs.booksList.innerHTML = '<p class="muted">No books could be loaded.</p>';
      }
    }
  }

  async function waitForProjectReady(
    projectId,
    { timeoutMs = 600000, pollIntervalMs = 3000 } = {},
  ) {
    if (!projectId || !state.user) return false;
    const normalizedId = String(projectId);
    const start = Date.now();
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    while (Date.now() - start < timeoutMs) {
      try {
        const res = await api.authenticatedFetch(`/api/projects/${normalizedId}`, {
          cache: 'no-store',
        });
        if (res.ok) {
          const data = await res.json().catch(() => ({}));
          const project = data.project;
          if (project) {
            const status = (project.status || '').toLowerCase();
            if (!PENDING_STATUSES.has(status)) {
              await fetchProjects({ silent: true, deferRenderIfPreviewPlaying: true });
              return status !== 'failed';
            }
          }
        } else if (res.status === 404) {
          return false;
        }
      } catch (error) {
        console.error(error);
      }
      await sleep(pollIntervalMs);
    }
    return false;
  }

  async function deleteConcept(conceptId, projectId, projectTitle, triggerBtn) {
    if (!conceptId) return;
    const scrollY = window.scrollY;
    const confirmMessage = `Delete this concept from "${projectTitle}"? Slides inside it will be removed.`;
    if (!window.confirm(confirmMessage)) return;
    if (triggerBtn) {
      triggerBtn.classList.add('loading');
      triggerBtn.disabled = true;
      triggerBtn.setAttribute('aria-busy', 'true');
      const label = triggerBtn.querySelector('.delete-concept-label');
      if (label) label.textContent = 'Deleting…';
    }
    try {
      const res = await api.authenticatedFetch(`/api/concepts/${conceptId}`, {
        method: 'DELETE',
        cache: 'no-store',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || 'Unable to delete concept right now.');
      }
      if (projectId && state.selectedConcepts) {
        delete state.selectedConcepts[projectId];
      }
      await fetchProjects({ preserveScroll: true });
      requestAnimationFrame(() => window.scrollTo(0, scrollY));
    } catch (error) {
      console.error(error);
      alert(error.message || 'Failed to delete concept.');
    } finally {
      if (triggerBtn) {
        triggerBtn.classList.remove('loading');
        triggerBtn.disabled = false;
        triggerBtn.removeAttribute('aria-busy');
        const label = triggerBtn.querySelector('.delete-concept-label');
        if (label) label.textContent = 'Delete Concept';
      }
    }
  }

  function reorderSlide(conceptId, slideId, targetIndex) {
    const scrollY = window.scrollY;
    api
      .saveSlide(slideId, { order_index: targetIndex })
      .then(() => fetchProjects({ preserveScroll: true }))
      .then(() => requestAnimationFrame(() => window.scrollTo(0, scrollY)))
      .catch((err) => {
        console.error(err);
      alert(err.message || 'Failed to reorder slides.');
    });
  }

  let edgeScrollCarousel = null;
  function edgeScrollHandler(evt) {
    if (!edgeScrollCarousel || !evt) return;
    const rect = edgeScrollCarousel.getBoundingClientRect();
    const buffer = 80;
    const step = 6;
    if (evt.clientX < rect.left + buffer) {
      edgeScrollCarousel.scrollLeft -= step;
    } else if (evt.clientX > rect.right - buffer) {
      edgeScrollCarousel.scrollLeft += step;
    }
  }

  function startEdgeScroll(carouselEl) {
    edgeScrollCarousel = carouselEl;
    window.addEventListener('pointermove', edgeScrollHandler);
  }

  function stopEdgeScroll() {
    window.removeEventListener('pointermove', edgeScrollHandler);
    edgeScrollCarousel = null;
  }

  function initCarouselSort(carouselEl, conceptId) {
    if (!carouselEl) return;
    if (carouselEl._sortable) {
      carouselEl._sortable.destroy();
    }
    let placeholderEl = null;
    const getPlaceholder = (dragEl) => {
      if (!placeholderEl) {
        const ph = document.createElement('div');
        ph.className = 'drop-placeholder';
        ph.textContent = 'Drop here';
        placeholderEl = ph;
      }
      if (dragEl) {
        placeholderEl.style.width = `${dragEl.offsetWidth}px`;
        placeholderEl.style.minHeight = `${dragEl.offsetHeight}px`;
      }
      return placeholderEl;
    };
    const clearPlaceholder = () => {
      placeholderEl?.remove();
      placeholderEl = null;
    };
    carouselEl._sortable = new Sortable(carouselEl, {
      animation: 120,
      ghostClass: 'slide-ghost',
      chosenClass: 'slide-chosen',
      dragClass: 'slide-dragging',
      handle: '.slide',
      draggable: '.slide',
      direction: 'horizontal',
      filter: '.add-slide-card',
      easing: 'ease',
      swapThreshold: 0.5,
      scroll: false,
      onStart: (evt) => {
        startEdgeScroll(carouselEl);
        const ph = getPlaceholder(evt.item);
        if (ph && evt.item.nextSibling) {
          evt.item.parentElement.insertBefore(ph, evt.item.nextSibling);
        }
      },
      onChange: (evt) => {
        const ph = getPlaceholder(evt.dragged);
        const target = evt.related;
        if (!ph || !target) return;
        const insertBefore = evt.willInsertAfter ? target.nextSibling : target;
        if (insertBefore) {
          insertBefore.parentElement.insertBefore(ph, insertBefore);
        } else {
          target.parentElement.appendChild(ph);
        }
      },
      onEnd: (evt) => {
        stopEdgeScroll();
        clearPlaceholder();
        const slideId = Number(evt.item?.dataset?.slideId);
        if (!slideId) return;
        const targetIndex = Number.isInteger(evt.newIndex) ? evt.newIndex : -1;
        if (targetIndex < 0) return;
        reorderSlide(conceptId, slideId, targetIndex);
      },
    });
  }

  function createSlideElement(slide, conceptId, carouselEl) {
    const slideNode = refs.slideTemplate.content.firstElementChild.cloneNode(true);
    slideNode.dataset.slideId = slide.id;
    slideNode.dataset.conceptId = conceptId;
    slideNode.draggable = true;
    const visual = slideNode.querySelector('.slide-visual');
    const copyEl = slideNode.querySelector('.slide-copy');
    const effectSelect = slideNode.querySelector('.effect-select');
    const transitionSelect = slideNode.querySelector('.transition-select');
    const editBtn = slideNode.querySelector('.edit-slide');

    copyEl.textContent = slide.text || '';
    editor.applySlideStyles(copyEl, editor.getSlideStyle(slide));
    effectSelect.value = slide.effect || 'none';
    transitionSelect.value = slide.transition || 'fade';

    editor.applyEffectClass(slideNode, slide.effect);

    if (slide.image_url) {
      visual.style.backgroundImage = `url('${slide.image_url}')`;
      visual.classList.add('has-image');
    } else {
      visual.style.backgroundImage = '';
      visual.classList.remove('has-image');
    }

    visual.classList.add('slide-thumbnail');
    visual.addEventListener('click', () => editor.openSlideEditor(slide));
    editBtn?.addEventListener('click', () => editor.openSlideEditor(slide));

    effectSelect.addEventListener('change', (event) => {
      editor.applyEffectClass(slideNode, event.target.value);
      api.saveSlide(slide.id, { effect: event.target.value }).catch((err) => {
        console.error(err);
        alert(err.message || 'Failed to save effect.');
      });
    });

    transitionSelect.addEventListener('change', (event) => {
      const value = event.target.value;
      api.saveSlide(slide.id, { transition: value }).catch((err) => {
        console.error(err);
        alert(err.message || 'Failed to save transition.');
      });
    });

    return slideNode;
  }

  function renderBooksList() {
    if (!refs.booksList) return;
    refs.booksList.innerHTML = '';
    if (!state.user) {
      refs.booksList.innerHTML = '<p class="muted">Sign in to view books.</p>';
      return;
    }
    if (!state.projects.length) {
      refs.booksList.innerHTML = '<p class="muted">No books yet. Upload one from the Dashboard.</p>';
      return;
    }

    const header = document.createElement('div');
    header.className = 'book-row book-row--header';
    header.innerHTML = `
      <span class="book-col book-col--title" style="color: ${getComputedStyle(document.documentElement).getPropertyValue('--muted')};">Title</span>
      <span class="book-col book-col--date">Date Created</span>
      <span class="book-col book-col--concepts" title="Slideshows Generated">Slideshows</span>
      <span class="book-col book-col--actions">Actions</span>
    `;
    header.style.cursor = 'default';
    Array.from(header.querySelectorAll('.book-col')).forEach((el) => {
      el.style.color = 'var(--muted)';
      el.style.fontWeight = '600';
    });
    refs.booksList.appendChild(header);

    state.projects.forEach((project) => {
      const row = document.createElement('div');
      row.className = 'book-row';
      row.innerHTML = `
        <span class="book-col book-col--title" title="${project.title}">${project.title}</span>
        <span class="book-col book-col--date">${new Date(project.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</span>
        <span class="book-col book-col--concepts">${(project.concepts || []).length}</span>
        <span class="book-col book-col--actions">
          <button class="icon-btn open-slides-btn" type="button" title="Open in Slides" aria-label="Open ${project.title} in Slides" data-tooltip="Open project">
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m12.75 4.75 6 6v.5l-6 6-1.5-1.5 3.75-3.75H5.25v-2h9.75L11.25 6.25z"/></svg>
          </button>
          <button class="icon-btn delete-book-btn-inline" type="button" title="Delete book" aria-label="Delete ${project.title}" data-tooltip="Delete book">
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 3.5c0-.83.67-1.5 1.5-1.5h3A1.5 1.5 0 0 1 15 3.5V5h4.5a.75.75 0 0 1 0 1.5H4.5A.75.75 0 0 1 4.5 5H9V3.5Zm.75 15.25a.75.75 0 0 1-1.5 0L8 9a.75.75 0 0 1 1.5 0l.25 9.75Zm3.75 0a.75.75 0 0 0 1.5 0L15 9a.75.75 0 0 0-1.5 0l-.25 9.75Zm-7.97-10h13.44L17.8 19.32a2 2 0 0 1-1.99 1.68H8.2a2 2 0 0 1-1.99-1.68Z"/></svg>
          </button>
        </span>
      `;
      row.querySelector('.open-slides-btn')?.addEventListener('click', (event) => {
        event.stopPropagation();
        setActiveProjectView(project.id);
        goToTab('slides', { projectId: project.id });
      });
      row.querySelector('.delete-book-btn-inline')?.addEventListener('click', async (event) => {
        event.stopPropagation();
        if (!state.user) {
          api.handleUnauthorized('Log in to delete a book.');
          return;
        }
        const confirmMessage = `Delete "${project.title}" and all related slideshows? This cannot be undone.`;
        const shouldDelete = window.confirm(confirmMessage);
        if (!shouldDelete) return;
        try {
          const res = await api.authenticatedFetch(`/api/projects/${project.id}`, {
            method: 'DELETE',
            cache: 'no-store',
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.error || 'Unable to delete this book right now.');
          await fetchProjects();
        } catch (error) {
          console.error(error);
          alert(error.message || 'Failed to delete the selected book.');
        }
      });
      row.addEventListener('click', () => {
        setActiveProjectView(project.id);
        goToTab('slides', { projectId: project.id });
      });
      refs.booksList.appendChild(row);
    });
  }

  function renderProjects() {
    if (!refs.projectsContainer) return;
    renderPendingAfterPreview = false;
    refs.projectsContainer.innerHTML = '';
    updateCounts();
    if (!state.user) {
      refs.projectsContainer.innerHTML =
        '<p class="muted">Sign in and pick a book from Books to edit slides.</p>';
      updateDeleteProjectButtonVisibility();
      setSlidesPanelsVisibility(false);
      return;
    }
    if (!state.projects.length) {
      refs.projectsContainer.innerHTML =
        '<p class="muted">No projects yet. Upload a book file to get started.</p>';
      updateDeleteProjectButtonVisibility();
      setSlidesPanelsVisibility(false);
      return;
    }

    setSlidesPanelsVisibility(true);

    const selectedProjectId = state.activeProjectId ? String(state.activeProjectId) : '';
    let visibleProjects = state.projects || [];
    if (selectedProjectId) {
      visibleProjects = visibleProjects.filter((project) => String(project.id) === selectedProjectId);
      if (!visibleProjects.length) {
        setActiveProjectView('', { render: false });
        visibleProjects = state.projects || [];
      }
    }
    const existingProjectIds = new Set((state.projects || []).map((project) => project.id));
    Object.keys(state.selectedConcepts).forEach((projectId) => {
      if (!existingProjectIds.has(Number(projectId))) {
        delete state.selectedConcepts[projectId];
      }
    });

    visibleProjects.forEach((project) => {
      const projectEl = document.createElement('section');
      projectEl.className = 'project project-card';
      const concepts = project.concepts || [];
      const hasConcepts = concepts.length > 0;

      let activeConcept = null;
      if (hasConcepts) {
        const storedSelection = state.selectedConcepts[project.id];
        const fallbackId = project.active_concept_id || concepts[0]?.id;
        const selectedConceptId = storedSelection || fallbackId;
        if (!state.selectedConcepts[project.id] && selectedConceptId) {
          state.selectedConcepts[project.id] = selectedConceptId;
        }
        activeConcept = concepts.find((concept) => concept.id === selectedConceptId) || concepts[0];
        if (activeConcept && state.selectedConcepts[project.id] !== activeConcept.id) {
          state.selectedConcepts[project.id] = activeConcept.id;
        }
      }
      const conceptRenderState = activeConcept ? getRenderState(project.id, activeConcept.id) : null;
      const conceptRendering = Boolean(conceptRenderState?.isLoading);

      const projectHeader = document.createElement('div');
      projectHeader.className = 'project-header';
      const projectMeta = document.createElement('div');
      projectMeta.className = 'project-meta';
      const titleInput = document.createElement('input');
      titleInput.type = 'text';
      titleInput.className = 'project-title-input';
      titleInput.value = project.title;
      titleInput.placeholder = 'Project title';
      titleInput.title = 'Click to rename project';
      titleInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          titleInput.blur();
        }
      });
      titleInput.addEventListener('blur', () => renameProject(project.id, titleInput.value));
      projectMeta.appendChild(titleInput);
      const timestamp = document.createElement('p');
      timestamp.className = 'timestamp';
      timestamp.textContent = new Date(project.created_at).toLocaleString();
      projectMeta.appendChild(timestamp);

      const formattedStatus = formatProjectStatus(project.status);
      if (formattedStatus) {
        const statusPill = document.createElement('p');
        statusPill.className = `project-status ${project.status || ''}`;
        statusPill.textContent = formattedStatus;
        projectMeta.appendChild(statusPill);
      }

      const headerActions = document.createElement('div');
      headerActions.className = 'project-header-actions';

      const renderBtn = document.createElement('button');
      renderBtn.type = 'button';
      renderBtn.className = 'primary-btn render-btn';
      renderBtn.innerHTML =
        '<span class="render-btn-label">Render Reel</span><span class="render-spinner" aria-hidden="true"></span>';
      const renderLabel = renderBtn.querySelector('.render-btn-label');
      if (renderLabel) {
        renderLabel.setAttribute('aria-live', 'polite');
        renderLabel.setAttribute('aria-atomic', 'true');
      }
      renderBtn.dataset.defaultLabel = 'Render Reel';
      if (hasConcepts && activeConcept) {
        renderBtn.addEventListener('click', () => {
          const selectionExists =
            state.voiceSelections && Object.prototype.hasOwnProperty.call(state.voiceSelections, project.id);
          const selectedVoice = selectionExists ? state.voiceSelections[project.id] : project.voice;
          tts.renderProject(project.id, activeConcept.id, selectedVoice || '', renderBtn);
        });
      } else {
        renderBtn.disabled = true;
        renderBtn.title = 'Slides are still generating.';
      }

      const conceptReady = conceptHasReadyRender(activeConcept);
      if (!conceptReady || conceptRendering) {
        restoreRenderButtonState(project.id, activeConcept?.id, renderBtn);
      }
      headerActions.append(renderBtn);

      const downloadBtnHeader = document.createElement('button');
      downloadBtnHeader.type = 'button';
      downloadBtnHeader.className = 'ghost-btn download-btn';
      downloadBtnHeader.innerHTML =
        '<span class="download-btn-label">Download Reel</span><span class="download-spinner" aria-hidden="true"></span>';
      const dlLabel = downloadBtnHeader.querySelector('.download-btn-label');
      if (dlLabel) {
        dlLabel.setAttribute('aria-live', 'polite');
        dlLabel.setAttribute('aria-atomic', 'true');
      }
      downloadBtnHeader.dataset.defaultLabel = 'Download Reel';
      const activeRender = activeConcept?.latest_render;
      const canDownload = conceptReady && !conceptRendering && Boolean(activeRender?.job_id);
      if (canDownload) {
        downloadBtnHeader.disabled = false;
        downloadBtnHeader.classList.remove('disabled');
        downloadBtnHeader.title = 'Download your latest reel.';
        downloadBtnHeader.addEventListener('click', () => {
          tts.downloadProject(
            {
              projectId: project.id,
              conceptId: activeConcept.id,
              jobId: activeRender?.job_id,
              filename: activeRender?.suggested_filename,
            },
            downloadBtnHeader,
          );
        });
        restoreDownloadButtonState(project.id, activeConcept.id, downloadBtnHeader);
      } else {
        downloadBtnHeader.disabled = true;
        downloadBtnHeader.classList.add('disabled');
        downloadBtnHeader.setAttribute('aria-disabled', 'true');
        downloadBtnHeader.title = conceptRendering
          ? 'Rendering in progress…'
          : 'Render a reel to enable downloading.';
      }
      headerActions.append(downloadBtnHeader);
      projectHeader.append(projectMeta, headerActions);
      projectEl.appendChild(projectHeader);

      if (!hasConcepts) {
        const pendingState = document.createElement('div');
        pendingState.className = 'pending-project-state';
        const statusMessage = document.createElement('p');
        const normalizedStatus = (project.status || '').toLowerCase();
        const isFailure = FAILURE_STATUSES.has(normalizedStatus);
        statusMessage.className = `status ${isFailure ? 'error' : ''}`;
        statusMessage.textContent = isFailure
          ? 'We could not generate slides for this upload. Please try again.'
          : 'Hang tight! Your slides are still generating in the background.';
        pendingState.appendChild(statusMessage);
        if (!isFailure) {
          const spinner = document.createElement('div');
          spinner.className = 'upload-loading-spinner';
          spinner.setAttribute('role', 'status');
          spinner.setAttribute('aria-live', 'polite');
          pendingState.appendChild(spinner);
        }
        projectEl.appendChild(pendingState);
        refs.projectsContainer.appendChild(projectEl);
        return;
      }

      if (!activeConcept) {
        refs.projectsContainer.appendChild(projectEl);
        return;
      }

      const conceptBlock = document.createElement('div');
      conceptBlock.className = 'concept';

      const conceptHeader = document.createElement('div');
      conceptHeader.className = 'concept-header';

      const conceptTop = document.createElement('div');
      conceptTop.className = 'concept-top';

      const conceptTitleWrap = document.createElement('div');
      conceptTitleWrap.className = 'concept-title-wrap';
      const conceptTitle = document.createElement('h4');
      conceptTitle.className = 'concept-title';
      conceptTitle.textContent = activeConcept.name;
      conceptTitleWrap.appendChild(conceptTitle);
      const slideBadge = document.createElement('div');
      slideBadge.className = 'slide-count-badge';
      slideBadge.textContent = `${(activeConcept.slides || []).length} slides`;
      conceptTitleWrap.appendChild(slideBadge);

      const conceptControls = document.createElement('div');
      conceptControls.className = 'concept-actions selector-row';

      const conceptSelector = document.createElement('select');
      conceptSelector.className = 'concept-switcher';
      conceptSelector.title = 'Choose a different concept generated from your upload.';
      concepts.forEach((concept) => {
        const option = document.createElement('option');
        option.value = concept.id;
        option.textContent = concept.name;
        conceptSelector.appendChild(option);
      });
      conceptSelector.value = activeConcept.id;
      conceptSelector.addEventListener('change', async (event) => {
        const newConceptId = Number(event.target.value);
        state.selectedConcepts[project.id] = newConceptId;
        const audioEl = projectEl.querySelector('.tts-preview-audio');
        if (audioEl) {
          audioEl.pause();
          audioEl.src = '';
        }
        try {
          await api.setActiveConcept(project.id, newConceptId);
        } catch (err) {
          console.error(err);
          alert(err.message || 'Failed to save concept selection.');
        }
        renderProjects();
      });
      conceptControls.appendChild(conceptSelector);

      conceptTop.append(conceptTitleWrap, conceptControls);
      conceptHeader.append(conceptTop);
      conceptBlock.appendChild(conceptHeader);

      const conceptDesc = document.createElement('p');
      conceptDesc.className = 'muted concept-description';
      conceptDesc.textContent = activeConcept.description;
      conceptBlock.appendChild(conceptDesc);

      const conceptVoice = document.createElement('div');
      conceptVoice.className = 'voice-row';
      conceptVoice.innerHTML = `
        <label>Voice</label>
        <select class="voice-select" data-project-id="${project.id}">
          <option value="">None (no narration)</option>
          <option value="sarah">Sarah</option>
          <option value="bella">Bella</option>
          <option value="adam">Adam</option>
          <option value="liam">Liam</option>
        </select>
        <button class="tts-preview-btn preview-btn" data-project-id="${project.id}">Preview Narration</button>
      `;
      const conceptPreview = document.createElement('section');
      conceptPreview.className = 'narration-preview-panel';
      conceptPreview.innerHTML = `<audio class="tts-preview-audio" controls style="display:none;"></audio>`;
      const voiceSelect = conceptVoice.querySelector('.voice-select');
      const currentVoice = state.voiceSelections?.[project.id] || project.voice || '';
      voiceSelect.value = currentVoice;
      const previewBtn = conceptVoice.querySelector('.tts-preview-btn');
      const syncPreviewState = (v) => {
        if (!previewBtn) return;
        const disabled = !v;
        previewBtn.disabled = disabled;
        previewBtn.classList.toggle('disabled', disabled);
      };
      syncPreviewState(currentVoice);
      voiceSelect.addEventListener('change', async (e) => {
        const voice = e.target.value;
        state.voiceSelections = state.voiceSelections || {};
        state.voiceSelections[project.id] = voice;
        syncPreviewState(voice);
        try {
          await api.saveProjectVoice(project.id, voice);
        } catch (err) {
          console.error(err);
          alert(err.message || 'Failed to save voice.');
        }
      });
      conceptBlock.appendChild(conceptVoice);
      conceptBlock.appendChild(conceptPreview);

      const carousel = document.createElement('div');
      carousel.className = 'carousel';
      (activeConcept.slides || []).forEach((slide) => {
        carousel.appendChild(createSlideElement(slide, activeConcept.id, carousel));
      });
      const addSlideCard = document.createElement('button');
      addSlideCard.type = 'button';
      addSlideCard.className = 'add-slide-card';
      addSlideCard.innerHTML = `
        <span class="add-icon">+</span>
        <span>Add Slide</span>
      `;
      addSlideCard.addEventListener('click', () => addSlide(activeConcept.id));
      carousel.appendChild(addSlideCard);
      initCarouselSort(carousel, activeConcept.id);

      const conceptFooter = document.createElement('div');
      conceptFooter.className = 'project-footer';
      conceptFooter.innerHTML = `
        <button class="delete-concept-btn" type="button">
          <span class="delete-concept-icon" aria-hidden="true">
            <svg viewBox="0 0 20 20" focusable="false" aria-hidden="true">
              <path d="M5.5 6.5v8.25c0 .69.56 1.25 1.25 1.25h6.5c.69 0 1.25-.56 1.25-1.25V6.5m-9.5 0h9.5m-6.75-2.5h4a.75.75 0 0 1 .7.48l.3.77h2.25M7.5 4H5.25" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </span>
          <span class="delete-concept-label">Delete Concept</span>
          <span class="delete-concept-spinner" aria-hidden="true"></span>
        </button>`;
      conceptFooter.querySelector('.delete-concept-btn')?.addEventListener('click', (evt) =>
        deleteConcept(activeConcept.id, project.id, project.title, evt.currentTarget),
      );

      conceptBlock.append(carousel, conceptFooter);
      projectEl.appendChild(conceptBlock);
      refs.projectsContainer.appendChild(projectEl);
    });

    updateDeleteProjectButtonVisibility();
  }

  function populateProjectFilter() {
    if (!refs.projectFilterSelect) return;
    const current = state.activeProjectId || '';
    refs.projectFilterSelect.innerHTML = '<option value="">Select a book</option>';
    state.projects.forEach((project) => {
      const option = document.createElement('option');
      option.value = String(project.id);
      option.textContent = project.title;
      refs.projectFilterSelect.appendChild(option);
    });
    const desired = current || (state.projects[0] ? String(state.projects[0].id) : '');
    state.activeProjectId = syncProjectFilterSelection(desired);
    document.dispatchEvent(
      new CustomEvent('litr-active-project-changed', { detail: { projectId: state.activeProjectId } }),
    );
    updateDeleteProjectButtonVisibility();
  }

  async function deleteActiveProject() {
    if (!state.user) {
      api.handleUnauthorized('Log in to delete a book.');
      return;
    }
    const selectedId = Number(state.activeProjectId);
    if (!Number.isFinite(selectedId) || selectedId <= 0) {
      return;
    }
    const targetProject = state.projects.find((project) => project.id === selectedId);
    if (!targetProject) {
      return;
    }
    const confirmMessage = `Delete "${targetProject.title}" and all related slideshows? This cannot be undone.`;
    const shouldDelete = window.confirm(confirmMessage);
    if (!shouldDelete) {
      return;
    }
    setDeleteProjectButtonLoading(true);
    try {
      const res = await api.authenticatedFetch(`/api/projects/${targetProject.id}`, {
        method: 'DELETE',
        cache: 'no-store',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || 'Unable to delete this book right now.');
      }
      state.activeProjectId = '';
      populateProjectFilter();
      updateDeleteProjectButtonVisibility();
      await fetchProjects();
    } catch (error) {
      console.error(error);
      alert(error.message || 'Failed to delete the selected book.');
    } finally {
      setDeleteProjectButtonLoading(false);
    }
  }

  function clearProjectState() {
    stopPendingPoller();
    state.projects = [];
    state.selectedConcepts = {};
    document.dispatchEvent(new CustomEvent('litr-projects-updated', { detail: { projects: state.projects } }));
    if (refs.projectsContainer) {
      refs.projectsContainer.innerHTML = '<p class="muted">Sign in to view your slide projects.</p>';
    }
    if (refs.booksList) {
      refs.booksList.innerHTML = '<p class="muted">Sign in to view books.</p>';
    }
    if (refs.bookCountEl) refs.bookCountEl.textContent = '0';
    if (refs.slideCountEl) refs.slideCountEl.textContent = '0';
    populateProjectFilter();
    state.activeProjectId = syncProjectFilterSelection('');
    errorBanner.textContent = '';
    updateDeleteProjectButtonVisibility();
    setSlidesPanelsVisibility(false);
  }

  function handleRenderCompleteEvent(event) {
    const detail = event.detail || {};
    const projectId = detail.projectId;
    const conceptId = detail.conceptId;
    const job = detail.job;
    if (!projectId || !conceptId || !job) {
      return;
    }
    const projectIndex = state.projects.findIndex((project) => String(project.id) === String(projectId));
    if (projectIndex === -1) {
      return;
    }
    const project = state.projects[projectIndex];
    const conceptIndex = (project.concepts || []).findIndex((concept) => String(concept.id) === String(conceptId));
    if (conceptIndex === -1) {
      return;
    }
    const updatedConcepts = [...project.concepts];
    updatedConcepts[conceptIndex] = { ...updatedConcepts[conceptIndex], latest_render: job };
    const updatedProject = { ...project, concepts: updatedConcepts };
    state.projects = [
      ...state.projects.slice(0, projectIndex),
      updatedProject,
      ...state.projects.slice(projectIndex + 1),
    ];
    renderProjects();
  }

  document.addEventListener('litr-render-complete', handleRenderCompleteEvent);
  document.addEventListener('litr-render-state-changed', () => {
    renderProjects();
  });

  function bindProjectControls() {
    refs.projectFilterSelect?.addEventListener('change', (event) => {
      setActiveProjectView(event.target.value || '');
    });
    refs.deleteProjectBtn?.addEventListener('click', deleteActiveProject);
    refs.refreshBtn?.addEventListener('click', () => {
      if (!state.user) {
        api.handleUnauthorized('Log in to refresh your projects.');
        return;
      }
      fetchProjects();
    });
  }

  return {
    fetchProjects,
    waitForProjectReady,
    hasPendingProjects,
    hasActiveProjectGeneration,
    isProjectPending,
    isProjectActivelyGenerating,
    renderProjects,
    populateProjectFilter,
    setActiveProjectView,
    clearProjectState,
    bindProjectControls,
    updateUploadFileSelection,
    updateDeleteProjectButtonVisibility,
    renderBooksList,
  };
}
