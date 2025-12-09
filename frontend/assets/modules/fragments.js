export async function injectFragments() {
  const targets = Array.from(document.querySelectorAll('[data-fragment]'));
  await Promise.all(
    targets.map(async (placeholder) => {
      const url = placeholder.getAttribute('data-fragment');
      if (!url) return;
      const res = await fetch(url);
      const html = await res.text();
      placeholder.outerHTML = html;
    })
  );
}
