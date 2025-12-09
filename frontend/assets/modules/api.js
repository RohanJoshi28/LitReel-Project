export function buildApi({ handleUnauthorized }) {
  async function authenticatedFetch(input, options = {}) {
    const response = await fetch(input, options);
    if (response.status === 401) {
      let message = 'Authentication required.';
      try {
        const data = await response.clone().json();
        message = data.error || message;
      } catch (_) {
        // ignore JSON parse errors
      }
      handleUnauthorized(message);
      throw new Error(message);
    }
    return response;
  }

  return { authenticatedFetch, handleUnauthorized };
}
