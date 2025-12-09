export function stopAudio(audioElement) {
  if (!audioElement) return;
  audioElement.pause();
  audioElement.src = '';
}

export function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const precision = value >= 10 || unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(precision)} ${units[unitIndex]}`;
}

export function bindColorInput(inputEl, handler) {
  if (!inputEl) return;
  ['input', 'change'].forEach((evt) => inputEl.addEventListener(evt, handler));
}

export function toggleToolbarActive(button, isActive) {
  if (!button) return;
  button.classList.toggle('active', Boolean(isActive));
}
