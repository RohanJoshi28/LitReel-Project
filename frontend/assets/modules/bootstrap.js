import { collectRefs } from './dom.js';
import { initAuth } from './auth.js';
import { buildApi } from './api.js';
import { initProjects } from './projects.js';
import { initEditor } from './editor.js';
import { initUpload } from './upload.js';
import { initTts } from './tts.js';
import { initConceptLab } from './concept_lab.js';
import { state } from './state.js';

const TAB_META = {
  dashboard: {
    title: 'Studio Dashboard',
    subtitle: 'Create, edit, and download viral-ready book carousels.',
    path: '/studio',
  },
  books: {
    title: 'Books',
    subtitle: 'Pick a book to dive into its slides.',
    path: '/studio/books',
  },
  slides: {
    title: 'Slides',
    subtitle: 'Edit concepts, slides, and narration for a selected book.',
    path: '/studio/slides',
  },
  video: {
    title: 'Video',
    subtitle: 'Rendering controls coming soon.',
    path: '/studio/video',
  },
  account: {
    title: 'Account',
    subtitle: 'Profile and billing controls will live here.',
    path: '/studio/account',
  },
};

export async function bootstrap() {
  const refs = collectRefs();
  const auth = initAuth(refs);
  const api = buildApi({ handleUnauthorized: auth.handleUnauthorized });

  function applyTabMeta(tab) {
    const meta = TAB_META[tab] || TAB_META.dashboard;
    if (refs.workspaceTitle) refs.workspaceTitle.textContent = meta.title;
    if (refs.workspaceSubtitle) refs.workspaceSubtitle.textContent = meta.subtitle;
  }

  function pathForTab(tab) {
    return (TAB_META[tab] && TAB_META[tab].path) || TAB_META.dashboard.path;
  }

  function setActiveTab(tab, { projectId, pushState = true } = {}) {
    const targetTab = TAB_META[tab] ? tab : 'dashboard';
    const buttons = Array.from(refs.tabButtons || []);
    const panels = Array.from(refs.tabPanels || []);
    buttons.forEach((btn) => btn.classList.toggle('active', btn.dataset.tabTarget === targetTab));
    panels.forEach((panel) => panel.classList.toggle('active', panel.dataset.tabPanel === targetTab));
    applyTabMeta(targetTab);
    const slidesHeader = document.getElementById('slides-header');
    const defaultTitle = document.querySelector('.workspace-title.default-title');
    if (slidesHeader && defaultTitle) {
      const isSlides = targetTab === 'slides';
      slidesHeader.classList.toggle('hidden', !isSlides);
      defaultTitle.classList.toggle('hidden', isSlides);
    }
    if (pushState) {
      const newPath = pathForTab(targetTab);
      if (window.location.pathname !== newPath) {
        window.history.pushState({ tab: targetTab }, '', newPath);
      }
    }
    if (projectId && projects) {
      projects.setActiveProjectView(projectId);
    }
    return targetTab;
  }

  const nav = {
    goToTab: (tab, options = {}) => setActiveTab(tab, { ...options, pushState: true }),
  };

  function attachTabInteraction(btn) {
    if (!btn) return;
    btn.addEventListener('click', () => {
      const target = btn.dataset.tabTarget;
      if (target) setActiveTab(target);
    });
    btn.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        const target = btn.dataset.tabTarget;
        if (target) setActiveTab(target);
      }
    });
  }

  Array.from(refs.tabButtons || []).forEach(attachTabInteraction);

  let projects;

  api.saveSlide = async (id, payload) => {
    const res = await api.authenticatedFetch(`/api/slides/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to save slide');
    }
    await projects.fetchProjects();
  };

  api.saveProjectVoice = async (projectId, voice) => {
    const res = await api.authenticatedFetch(`/api/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to save voice');
    }
    const data = await res.json().catch(() => ({}));
    if (data.project) {
      const idx = state.projects.findIndex((p) => p.id === projectId);
      if (idx !== -1) {
        state.projects[idx] = { ...state.projects[idx], voice: data.project.voice };
      }
    }
  };

  api.setActiveConcept = async (projectId, conceptId) => {
    const res = await api.authenticatedFetch(`/api/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_concept_id: conceptId }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to save concept selection');
    }
    const data = await res.json().catch(() => ({}));
    if (data.project) {
      const idx = state.projects.findIndex((p) => p.id === projectId);
      if (idx !== -1) {
        state.projects[idx] = { ...state.projects[idx], active_concept_id: data.project.active_concept_id };
      }
    }
  };

  const editor = initEditor(refs, api);
  const tts = initTts(refs, api);
  projects = initProjects(refs, api, editor, tts, nav);
  const upload = initUpload(refs, api, projects, nav);
  const conceptLab = initConceptLab(refs, api, projects);
  // expose projects for editor delete callback refresh
  window.projects = projects;

  function prepareAnnouncers() {
    const generateBtnLabel = refs.generateBtn?.querySelector('.generate-btn-label');
    if (generateBtnLabel) {
      generateBtnLabel.setAttribute('aria-live', 'polite');
      generateBtnLabel.setAttribute('aria-atomic', 'true');
    }
  }

  prepareAnnouncers();
  editor.bindEditorListeners();
  projects.bindProjectControls();
  upload.bindUploadListeners();
  conceptLab.bind();
  tts.bindTtsListeners();
  auth.bindAuthListeners(api.authenticatedFetch);
  document.addEventListener('litr-auth-success', () => projects.fetchProjects());

  projects.updateDeleteProjectButtonVisibility();
  await auth.refreshSession(api.authenticatedFetch);
  if (state.user) {
    await projects.fetchProjects();
  } else {
    projects.renderProjects();
  }

  function tabFromPath() {
    const segments = window.location.pathname.split('/').filter(Boolean);
    const last = segments[segments.length - 1] || '';
    if (last === 'studio') return 'dashboard';
    if (TAB_META[last]) return last;
    return 'dashboard';
  }

  const initialTab = tabFromPath();
  setActiveTab(initialTab, { pushState: false });

  window.addEventListener('popstate', (event) => {
    const tab = (event.state && event.state.tab) || tabFromPath();
    setActiveTab(tab, { pushState: false });
  });

  // Profile card toggle
  function closeProfileCard() {
    if (!refs.profileCard || !refs.profileToggle) return;
    refs.profileCard.classList.remove('open');
    refs.profileToggle.setAttribute('aria-expanded', 'false');
    refs.profileCard.setAttribute('aria-hidden', 'true');
  }

  refs.profileToggle?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!refs.profileCard) return;
    const isOpen = refs.profileCard.classList.toggle('open');
    refs.profileToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    refs.profileCard.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
  });

  document.addEventListener('click', (e) => {
    if (!refs.profileCard || !refs.profileToggle) return;
    if (!refs.profileCard.contains(e.target) && !refs.profileToggle.contains(e.target)) {
      closeProfileCard();
    }
  });

  refs.profileSettings?.addEventListener('click', () => {
    closeProfileCard();
    setActiveTab('account');
  });
}
