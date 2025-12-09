import { state } from './state.js';
import { stopAudio } from './utils.js';
import { setDownloadState, clearDownloadState } from './download_tracker.js';
import { setRenderState, clearRenderState } from './render_tracker.js';

const SAMPLE_NARRATION = "Life is like a box of chocolates. You never know what you're gonna get.";

export function initTts(refs, api) {
  function setDownloadButtonLoading(button, isLoading, loadingText = 'Downloading…') {
    if (!button) return;
    const label = button.querySelector('.download-btn-label');
    if (label && !button.dataset.defaultLabel) {
      button.dataset.defaultLabel = label.textContent || 'Download Reel';
    }
    if (isLoading) {
      button.classList.add('loading');
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      if (label) {
        label.textContent = loadingText;
      }
    } else {
      button.classList.remove('loading');
      button.disabled = false;
      button.removeAttribute('aria-busy');
      if (label) {
        label.textContent = button.dataset.defaultLabel || 'Download Reel';
      }
    }
  }

  function setRenderButtonLoading(button, isLoading, loadingText = 'Working…') {
    if (!button) return;
    const label = button.querySelector('.render-btn-label');
    if (label && !button.dataset.defaultLabel) {
      button.dataset.defaultLabel = label.textContent || 'Render Reel';
    }
    if (isLoading) {
      button.classList.add('loading');
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      if (label && loadingText) {
        label.textContent = loadingText;
      }
    } else {
      button.classList.remove('loading');
      button.disabled = false;
      button.removeAttribute('aria-busy');
      if (label) {
        label.textContent = button.dataset.defaultLabel || 'Render Reel';
      }
    }
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function notifyRenderStateChange(projectId, conceptId, payload = {}) {
    if (!projectId || !conceptId) return;
    document.dispatchEvent(
      new CustomEvent('litr-render-state-changed', {
        detail: { projectId, conceptId, ...payload },
      }),
    );
  }

  function extractFilename(disposition) {
    if (!disposition) return '';
    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match && utf8Match[1]) {
      try {
        return decodeURIComponent(utf8Match[1]);
      } catch (_) {
        // ignore malformed encodings
      }
    }
    const basicMatch = disposition.match(/filename="?([^\";]+)"?/i);
    return basicMatch && basicMatch[1] ? basicMatch[1] : '';
  }

  async function downloadDirectResponse(response, fallbackName) {
    const filename =
      extractFilename(response.headers.get('content-disposition') || '') || fallbackName || 'litreel.mp4';
    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = blobUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(blobUrl);
  }

  async function downloadProject({ projectId, conceptId, jobId, filename }, btn) {
    if (!jobId) {
      alert('No completed render found. Please render the video first.');
      return;
    }
    if (!state.user) {
      api.handleUnauthorized('Log in to download reels.');
      return;
    }

    setDownloadState(projectId, conceptId, {
      isLoading: true,
      label: 'Preparing download…',
      startedAt: Date.now(),
    });
    if (btn) setDownloadButtonLoading(btn, true, 'Preparing…');
    try {
      const job = await fetchRenderJob(jobId);
      const status = (job.status || '').toLowerCase();
      if (status !== 'ready') {
        throw new Error('Render is still processing. Click render first.');
      }
      await triggerRenderDownload(job, filename);
      setDownloadState(projectId, conceptId, { label: 'Download started', status: 'ready' });
      await sleep(500);
    } catch (error) {
      console.error(error);
      alert(error.message || 'Failed to download reel.');
    } finally {
      clearDownloadState(projectId, conceptId);
      if (btn) {
        await sleep(200);
        setDownloadButtonLoading(btn, false);
      }
    }
  }

  async function fetchRenderJob(jobId) {
    const response = await api.authenticatedFetch(`/api/downloads/${jobId}`, { cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data?.error || 'Unable to load render status.');
    }
    if (!data?.job) {
      throw new Error('Render job not found.');
    }
    return data.job;
  }

  function buildRenderSnapshot(job) {
    if (!job || !job.job_id) return null;
    const completedAt = job.completed_at || new Date().toISOString();
    const updatedAt = job.updated_at || completedAt;
    return {
      job_id: job.job_id,
      status: job.status,
      download_type: job.download_type,
      download_url: job.download_url,
      storage_path: job.storage_path,
      file_size: job.file_size,
      suggested_filename: job.suggested_filename,
      completed_at: completedAt,
      updated_at: updatedAt,
      voice: job.voice,
    };
  }

  function dispatchRenderComplete(projectId, conceptId, job) {
    const snapshot = buildRenderSnapshot(job);
    if (!snapshot) return;
    document.dispatchEvent(
      new CustomEvent('litr-render-complete', {
        detail: { projectId, conceptId, job: snapshot },
      }),
    );
  }

  async function renderProject(projectId, conceptId, voice, btn) {
    if (!state.user) {
      api.handleUnauthorized('Log in to render reels.');
      return;
    }
    if (!conceptId) {
      alert('Select a concept before rendering.');
      return;
    }
    const payload = { concept_id: conceptId };
    if (typeof voice !== 'undefined') {
      payload.voice = voice === '' ? 'none' : voice;
    }

    const initialState = setRenderState(projectId, conceptId, {
      isLoading: true,
      label: 'Working…',
      status: 'queued',
    });
    notifyRenderStateChange(projectId, conceptId, initialState);
    setRenderButtonLoading(btn, true, 'Working…');

    try {
      const res = await api.authenticatedFetch(`/api/projects/${projectId}/renders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.job) {
        throw new Error(data?.error || 'Failed to start rendering.');
      }
      const job = data.job;
      if (!job.job_id) {
        throw new Error('Render job did not return an ID.');
      }
      const status = (job.status || '').toLowerCase();
      if (status === 'ready') {
        dispatchRenderComplete(projectId, conceptId, job);
        return;
      }
      const labelEl = btn ? btn.querySelector('.render-btn-label') : null;
      const finalJob = await pollRenderJob(job.job_id, {
        labelEl,
        onStatus: (currentStatus) => {
          const labelText = 'Working…';
          const updatedState = setRenderState(projectId, conceptId, {
            status: currentStatus,
            label: labelText,
            isLoading: true,
          });
          notifyRenderStateChange(projectId, conceptId, updatedState);
          setRenderButtonLoading(btn, true, labelText);
        },
      });
      dispatchRenderComplete(projectId, conceptId, finalJob);
    } catch (error) {
      console.error(error);
      alert(error.message || 'Failed to render reel.');
    } finally {
      clearRenderState(projectId, conceptId);
      notifyRenderStateChange(projectId, conceptId, { status: 'idle' });
      if (btn) {
        const label = btn.querySelector('.render-btn-label');
        if (label) {
          label.textContent = 'Rendered!';
        }
      }
      setTimeout(() => setRenderButtonLoading(btn, false), 800);
    }
  }

  async function pollRenderJob(jobId, { labelEl = null, projectId = null, conceptId = null, onStatus = null } = {}) {
    const maxWaitMs = 20 * 60 * 1000;
    const pollInterval = 3000;
    const startedAt = Date.now();
    while (Date.now() - startedAt < maxWaitMs) {
      await sleep(pollInterval);
      let response;
      try {
        response = await api.authenticatedFetch(`/api/downloads/${jobId}`, { cache: 'no-store' });
      } catch (error) {
        console.error(error);
        continue;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data?.error || 'Failed to poll render job.');
      }
      const job = data?.job;
      if (!job) {
        throw new Error('Render job not found. Please try again.');
      }
      const status = (job.status || '').toLowerCase();
      if (status === 'ready') {
        return job;
      }
      if (status === 'failed') {
        throw new Error(job.error || 'Render job failed. Please retry.');
      }
      const labelText = status === 'processing' ? 'Rendering…' : 'Queued…';
      if (labelEl) {
        labelEl.textContent = labelText;
      }
      if (typeof onStatus === 'function') {
        onStatus(status, job);
      }
      if (projectId && conceptId) {
        setDownloadState(projectId, conceptId, { label: labelText, status });
      }
    }
    throw new Error('Render job timed out. Please try again.');
  }

  async function triggerRenderDownload(job) {
    const filename = job?.suggested_filename || `litreel-${job?.project_id || 'project'}.mp4`;
    if (job?.download_type === 'url' && job.download_url) {
      const anchor = document.createElement('a');
      anchor.href = job.download_url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      return;
    }
    const res = await api.authenticatedFetch(`/api/downloads/${job.job_id}/file`, { cache: 'no-store' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to retrieve render file.');
    }
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(blobUrl);
  }

  function buildDownloadUrl(projectId, conceptId, voice) {
    const params = new URLSearchParams();
    params.set('concept_id', conceptId);
    if (typeof voice !== 'undefined') {
      params.set('voice', voice === '' ? 'none' : voice);
    }
    return `/api/projects/${projectId}/download?${params.toString()}`;
  }

  function buildDownloadFilename(projectId, conceptId) {
    return `project-${projectId}-concept-${conceptId}.mp4`;
  }

  function isNetworkError(error) {
    if (!error) return false;
    const msg = String(error.message || error).toLowerCase();
    return error.name === 'TypeError' || msg.includes('failed to fetch') || msg.includes('networkerror');
  }

  function triggerAnchorDownload(url) {
    return new Promise((resolve, reject) => {
      try {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.rel = 'noopener';
        anchor.style.display = 'none';
        anchor.setAttribute('download', '');
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        window.setTimeout(resolve, 1500);
      } catch (err) {
        reject(err);
      }
    });
  }

  async function handleDownloadResponse(res, { projectId, conceptId, labelEl }) {
    const contentType = (res.headers.get('content-type') || '').toLowerCase();
    if (contentType.includes('application/json')) {
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || 'Failed to enqueue render job.');
      }
      const jobId = data?.job?.job_id;
      if (!jobId) {
        throw new Error(data.error || 'Failed to enqueue render job.');
      }
      const finalJob = await pollRenderJob(jobId, { labelEl, projectId, conceptId });
      await triggerRenderDownload(finalJob);
      if (labelEl) {
        labelEl.textContent = 'Rendering… 100%';
      }
      setDownloadState(projectId, conceptId, { label: 'Rendering… 100%', status: 'ready' });
      return;
    }
    if (!res.ok) {
      const errorText = await res.text().catch(() => '');
      throw new Error(errorText || 'Failed to download reel.');
    }
    const fallbackName = buildDownloadFilename(projectId, conceptId);
    await downloadDirectResponse(res, fallbackName);
    if (labelEl) {
      labelEl.textContent = 'Rendering… 100%';
    }
    setDownloadState(projectId, conceptId, { label: 'Rendering… 100%', status: 'ready' });
  }

  async function generateTTS() {
    if (!state.user) {
      api.handleUnauthorized('Log in to generate audio.');
      return;
    }

    const text = (state.editor.text || '').trim();
    if (!text) {
      alert('Slide text is empty.');
      return;
    }

    const voice = refs.ttsVoiceSelect?.value || 'sarah';
    refs.ttsBtn.disabled = true;
    refs.ttsBtn.textContent = 'Generating…';

    try {
      const res = await api.authenticatedFetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'TTS failed');
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      refs.ttsAudio.src = url;
      refs.ttsAudio.load();
    } catch (error) {
      console.error(error);
      alert(error.message || 'Failed to generate audio.');
    } finally {
      refs.ttsBtn.disabled = false;
      refs.ttsBtn.textContent = 'Generate Audio';
    }
  }

  async function generateNarrationPreview() {
    const voice = refs.narrationVoiceSelect?.value || '';
    refs.narrationPreviewBtn.disabled = true;
    refs.narrationPreviewBtn.textContent = 'Generating…';

    try {
      const body = voice ? { text: SAMPLE_NARRATION, voice } : { text: SAMPLE_NARRATION };
      const res = await api.authenticatedFetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Preview TTS failed');
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);

      refs.narrationPreviewAudio.src = url;
      refs.narrationPreviewAudio.style.display = 'block';
      refs.narrationPreviewAudio.play();
    } catch (err) {
      console.error(err);
      alert('Failed to generate narration preview.');
    } finally {
      refs.narrationPreviewBtn.disabled = false;
      refs.narrationPreviewBtn.textContent = 'Preview Narration Voice';
    }
  }

  async function handleInlinePreviewClick(event) {
    if (!event.target.classList.contains('tts-preview-btn')) return;

    const projectId = event.target.dataset.projectId;
    const selectionExists = state.voiceSelections && Object.prototype.hasOwnProperty.call(state.voiceSelections, projectId);
    const voice = selectionExists ? (state.voiceSelections?.[projectId] || '') : '';
    const audioEl = event.target.closest('.project')?.querySelector('.tts-preview-audio');
    if (!audioEl) return;

    // If no voice selected, clear any current audio and skip preview.
    if (!voice) {
      stopAudio(audioEl);
      audioEl.style.display = 'none';
      return;
    }

    const payload = voice ? { text: SAMPLE_NARRATION, voice } : { text: SAMPLE_NARRATION };
    const resp = await fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      console.warn('TTS preview failed');
      return;
    }

    const blob = await resp.blob();
    audioEl.src = URL.createObjectURL(blob);
    audioEl.style.display = 'block';
    audioEl.play();
  }

  function bindTtsListeners() {
    refs.ttsBtn?.addEventListener('click', generateTTS);
    refs.narrationPreviewBtn?.addEventListener('click', generateNarrationPreview);
    document.addEventListener('click', handleInlinePreviewClick);
    refs.slideEditorClose?.addEventListener('click', () => stopAudio(refs.ttsAudio));
    refs.slideEditorCancelBtn?.addEventListener('click', () => stopAudio(refs.ttsAudio));
  }

  return {
    bindTtsListeners,
    downloadProject,
    renderProject,
  };
}
