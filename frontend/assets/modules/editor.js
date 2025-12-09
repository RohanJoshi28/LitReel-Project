import { state } from './state.js';
import { bindColorInput, toggleToolbarActive } from './utils.js';
import { getSlideStyle, applySlideStyles, applyEffectClass } from './style.js';

export function initEditor(refs, api) {
  function normalizeSlideText(value) {
    if (!value) return '';
    return value
      .replace(/\u00a0/g, ' ')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .join(' ');
  }

  function updateEditorPreview() {
    refs.slideEditorPreview.style.backgroundImage = state.editor.imageUrl
      ? `url('${state.editor.imageUrl}')`
      : '';
    const displayText = state.editor.text || 'Add a gripping hook…';
    refs.slideEditorPreviewText.textContent = displayText;
    refs.slideEditorPreviewText.style.setProperty('--slide-text-color', state.editor.textColor || '#ffffff');
    refs.slideEditorPreviewText.style.setProperty('--slide-outline-color', state.editor.outlineColor || '#000000');
    const bold = state.editor.fontWeight !== '400';
    refs.slideEditorPreviewText.style.fontWeight = bold ? '700' : '400';
    refs.slideEditorPreviewText.style.letterSpacing = bold ? '0.015em' : '0.008em';
    if (state.editor.underline) {
      refs.slideEditorPreviewText.style.textDecoration = 'underline';
      refs.slideEditorPreviewText.style.textDecorationColor = state.editor.textColor || '#ffffff';
      refs.slideEditorPreviewText.style.textDecorationThickness = '0.15em';
      refs.slideEditorPreviewText.style.textUnderlineOffset = '0.25em';
    } else {
      refs.slideEditorPreviewText.style.textDecoration = 'none';
    }
  }

  function openSlideEditor(slide) {
    const style = getSlideStyle(slide);
    state.editor = {
      slideId: slide.id,
      text: slide.text || '',
      textColor: (style.text_color || '#FFFFFF').toUpperCase(),
      outlineColor: (style.outline_color || '#000000').toUpperCase(),
      fontWeight: style.font_weight,
      underline: Boolean(style.underline),
      imageUrl: slide.image_url || '',
      originalImageUrl: slide.image_url || '',
    };
    refs.slideEditorTextInput.value = state.editor.text;
    refs.slideEditorTextColorInput.value = (state.editor.textColor || '#FFFFFF').toLowerCase();
    refs.slideEditorOutlineInput.value = (state.editor.outlineColor || '#000000').toLowerCase();
    toggleToolbarActive(refs.toolbarBoldBtn, state.editor.fontWeight !== '400');
    toggleToolbarActive(refs.toolbarUnderlineBtn, state.editor.underline);
    refs.slideEditorImageQuery.value = '';
    refs.slideEditorImageResults.innerHTML = '';
    updateEditorPreview();
    refs.slideEditorModal.classList.remove('hidden');
    refs.slideEditorTextInput.focus();
  }

  function closeSlideEditor() {
    refs.slideEditorModal.classList.add('hidden');
    state.editor = {
      slideId: null,
      text: '',
      textColor: '#ffffff',
      outlineColor: '#000000',
      fontWeight: '700',
      underline: false,
      imageUrl: '',
      originalImageUrl: '',
    };
  }

  async function handleEditorSave() {
    if (!state.editor.slideId) return;
    const text = normalizeSlideText(refs.slideEditorTextInput.value);
    if (!text) {
      alert('Slide text cannot be empty.');
      return;
    }
    refs.slideEditorSaveBtn.disabled = true;
    const payload = {
      text,
      style: {
        text_color: state.editor.textColor,
        outline_color: state.editor.outlineColor,
        font_weight: state.editor.fontWeight,
        underline: state.editor.underline,
      },
    };
    if (state.editor.imageUrl !== state.editor.originalImageUrl) {
      payload.image_url = state.editor.imageUrl;
    }
    try {
      await api.saveSlide(state.editor.slideId, payload);
      closeSlideEditor();
    } finally {
      refs.slideEditorSaveBtn.disabled = false;
    }
  }

  function renderEditorImageResults(results) {
    if (!results.length) {
      refs.slideEditorImageResults.innerHTML = '<p>No images found.</p>';
      return;
    }
    refs.slideEditorImageResults.innerHTML = '';
    results.forEach((img) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'image-option';
      option.innerHTML = `
        <img src="${img.thumbnail}" alt="${img.photographer}">
        <span>${img.photographer}</span>
      `;
      option.addEventListener('click', () => {
        state.editor.imageUrl = img.url;
        updateEditorPreview();
      });
      refs.slideEditorImageResults.appendChild(option);
    });
  }

  function bindEditorListeners() {
    refs.slideEditorClose?.addEventListener('click', closeSlideEditor);
    refs.slideEditorCancelBtn?.addEventListener('click', closeSlideEditor);
    refs.slideEditorDeleteBtn?.addEventListener('click', async () => {
      if (!state.editor.slideId) return;
      const confirmed = window.confirm('Delete this slide? This cannot be undone.');
      if (!confirmed) return;
      try {
        const res = await api.authenticatedFetch(`/api/slides/${state.editor.slideId}`, { method: 'DELETE' });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.error || 'Unable to delete slide.');
        }
        closeSlideEditor();
        // refresh projects to reflect removal
        if (window.projects?.fetchProjects) {
          await window.projects.fetchProjects();
        }
      } catch (err) {
        console.error(err);
        alert(err.message || 'Failed to delete slide.');
      }
    });
    refs.slideEditorTextInput?.addEventListener('input', () => {
      state.editor.text = refs.slideEditorTextInput.value;
      updateEditorPreview();
    });
    bindColorInput(refs.slideEditorTextColorInput, () => {
      const value = refs.slideEditorTextColorInput.value || '#ffffff';
      state.editor.textColor = value.toUpperCase();
      updateEditorPreview();
    });
    bindColorInput(refs.slideEditorOutlineInput, () => {
      const value = refs.slideEditorOutlineInput.value || '#000000';
      state.editor.outlineColor = value.toUpperCase();
      updateEditorPreview();
    });
    refs.toolbarBoldBtn?.addEventListener('click', () => {
      state.editor.fontWeight = state.editor.fontWeight === '400' ? '700' : '400';
      toggleToolbarActive(refs.toolbarBoldBtn, state.editor.fontWeight !== '400');
      updateEditorPreview();
    });
    refs.toolbarUnderlineBtn?.addEventListener('click', () => {
      state.editor.underline = !state.editor.underline;
      toggleToolbarActive(refs.toolbarUnderlineBtn, state.editor.underline);
      updateEditorPreview();
    });
    refs.slideEditorSaveBtn?.addEventListener('click', handleEditorSave);
    refs.slideEditorImageSearchForm?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const query = refs.slideEditorImageQuery.value.trim();
      if (!query) return;
      if (!state.user) {
        api.handleUnauthorized('Log in to search stock images.');
        return;
      }
      refs.slideEditorImageResults.innerHTML = '<p>Searching…</p>';
      try {
        const res = await api.authenticatedFetch(`/api/stock/search?q=${encodeURIComponent(query)}`);
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || 'Lookup failed');
        }
        const data = await res.json();
        renderEditorImageResults(data.results || []);
      } catch (error) {
        console.error(error);
        refs.slideEditorImageResults.innerHTML = `<p>${error.message || 'Lookup failed.'}</p>`;
      }
    });
  }

  return {
    openSlideEditor,
    closeSlideEditor,
    updateEditorPreview,
    handleEditorSave,
    renderEditorImageResults,
    bindEditorListeners,
    applySlideStyles,
    applyEffectClass,
    getSlideStyle,
  };
}
