export const state = {
  user: null,
  projects: [],
  selectedConcepts: {},
  voiceSelections: {},
  activeDownloads: {},
  renderJobs: {},
  activeUploadProjectId: null,
  editor: {
    slideId: null,
    text: '',
    textColor: '#ffffff',
    outlineColor: '#000000',
    fontWeight: '700',
    underline: false,
    imageUrl: '',
    originalImageUrl: '',
  },
  activeProjectId: '',
};

export const DEFAULT_STYLE = {
  text_color: '#FFFFFF',
  outline_color: '#000000',
  font_weight: '700',
  underline: false,
};

export const FILE_META_DEFAULT = 'No document selected yet.';
