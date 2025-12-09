import { bootstrap } from './modules/bootstrap.js';
import { injectFragments } from './modules/fragments.js';

async function init() {
  await injectFragments();
  await bootstrap();
}

document.addEventListener('DOMContentLoaded', init);
