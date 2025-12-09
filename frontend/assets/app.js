// Compatibility entrypoint kept for pages that still load /assets/app.js
import { injectFragments } from './modules/fragments.js';
import { bootstrap } from './modules/bootstrap.js';

document.addEventListener('DOMContentLoaded', async () => {
  await injectFragments();
  await bootstrap();
});
