import { FILE_META_DEFAULT, state } from './state.js';

export function initAuth(refs) {
  let authMode = 'login';

  function setCurrentUser(user) {
    state.user = user || null;
    if (refs.sessionEmailEl) {
      refs.sessionEmailEl.textContent = state.user?.email || '—';
    }
  }

  function setAuthFeedback(message = '', tone = 'info') {
    if (!refs.authMessage) return;
    refs.authMessage.textContent = message;
    refs.authMessage.className = 'auth-message';
    if (message) {
      refs.authMessage.classList.add(tone === 'error' ? 'error' : 'success');
    }
  }

  function setAuthMode(mode = 'login') {
    authMode = mode;
    refs.loginForm?.classList.toggle('hidden', mode !== 'login');
    refs.signupForm?.classList.toggle('hidden', mode !== 'signup');
    refs.authTabs.forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.authMode === mode);
    });
  }

  function showAppShell() {
    refs.authShell?.classList.add('hidden');
    refs.appShell?.classList.remove('hidden');
    setAuthMode('login');
    setAuthFeedback('');
  }

  function showAuthShell(message = '') {
    refs.appShell?.classList.add('hidden');
    refs.authShell?.classList.remove('hidden');
    if (message) {
      setAuthFeedback(message, 'error');
    }
  }

  function resetUploadFileSelection(message) {
    if (!refs.uploadFileMeta || !refs.uploadFileField) return;
    refs.uploadFileMeta.textContent = message || FILE_META_DEFAULT;
    refs.uploadFileMeta.classList.remove('confirmed');
    refs.uploadFileField.classList.remove('has-file');
  }

  function clearProjectState() {
    state.projects = [];
    state.selectedConcepts = {};
    if (refs.projectsContainer) {
      refs.projectsContainer.innerHTML = '<p class="muted">Sign in to view your slide projects.</p>';
    }
    if (refs.bookCountEl) refs.bookCountEl.textContent = '0';
    if (refs.slideCountEl) refs.slideCountEl.textContent = '0';
    state.activeProjectId = 'all';
    resetUploadFileSelection();
  }

  function handleUnauthorized(message = 'Please log in again.') {
    setCurrentUser(null);
    clearProjectState();
    showAuthShell(message);
  }

  function getAuthPayload(form) {
    const formData = new FormData(form);
    const email = (formData.get('email') || '').toString().trim().toLowerCase();
    const password = (formData.get('password') || '').toString().trim();
    return { email, password };
  }

  async function handleAuthSubmit(event, endpoint, authenticatedFetch) {
    event.preventDefault();
    const form = event.target;
    const submitBtn = form.querySelector('button[type="submit"]');
    const payload = getAuthPayload(form);
    if (submitBtn) submitBtn.disabled = true;
    setAuthFeedback('');
    if (!payload.email || !payload.password) {
      setAuthFeedback('Email and password are required.', 'error');
      if (submitBtn) submitBtn.disabled = false;
      return;
    }
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setAuthFeedback(data.error || 'Unable to authenticate right now.', 'error');
        return;
      }
      setCurrentUser(data.user);
      form.reset();
      showAppShell();
      setAuthFeedback(
        endpoint.includes('signup') ? 'Account created. Upload your first book file!' : 'Welcome back!',
        'success',
      );
      document.dispatchEvent(new Event('litr-auth-success'));
    } catch (error) {
      console.error(error);
      setAuthFeedback(error.message || 'Unable to reach the server.', 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function refreshSession(authenticatedFetch) {
    try {
      const res = await fetch('/api/auth/me', { cache: 'no-store' });
      if (res.status === 401) {
        handleUnauthorized('Log in to manage your reels.');
        return;
      }
      if (!res.ok) {
        throw new Error('Unable to verify session.');
      }
      const data = await res.json();
      if (data?.user) {
        setCurrentUser(data.user);
        showAppShell();
      } else {
        handleUnauthorized('Log in to continue.');
      }
    } catch (error) {
      console.error(error);
      handleUnauthorized('Unable to verify your session. Please log in.');
    }
  }

  function bindAuthListeners(authenticatedFetch) {
    refs.authTabs.forEach((tab) => {
      tab.addEventListener('click', () => setAuthMode(tab.dataset.authMode || 'login'));
    });
    refs.loginForm?.addEventListener('submit', (event) => handleAuthSubmit(event, '/api/auth/login', authenticatedFetch));
    refs.signupForm?.addEventListener('submit', (event) => handleAuthSubmit(event, '/api/auth/signup', authenticatedFetch));
    refs.logoutBtn?.addEventListener('click', async (event) => {
      if (!state.user) {
        showAuthShell();
        return;
      }
      const button = event.currentTarget;
      if (button?.disabled !== undefined) {
        button.disabled = true;
      }
      try {
        await fetch('/api/auth/logout', { method: 'POST' });
      } catch (error) {
        console.error(error);
      } finally {
        if (button?.disabled !== undefined) {
          button.disabled = false;
        }
        handleUnauthorized('Logged out successfully.');
      }
    });
  }

  return {
    setCurrentUser,
    setAuthFeedback,
    setAuthMode,
    showAppShell,
    showAuthShell,
    handleUnauthorized,
    bindAuthListeners,
    refreshSession,
    resetUploadFileSelection,
  };
}
