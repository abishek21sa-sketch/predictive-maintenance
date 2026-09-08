// Shared browser boundary for the Vercel static observatory. The local
// Render-served pages still work because only absolute /api paths are routed.
window.__PDM_API_BASE__ = (window.__PDM_API_BASE__ || 'https://predictive-maintenance-intelligence-api.onrender.com').replace(/\/$/, '');
const _pdmFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  const url = typeof input === 'string' ? input : input.url;
  if (url.startsWith('/api/')) {
    const target = `${window.__PDM_API_BASE__}${url}`;
    return _pdmFetch(target, init);
  }
  return _pdmFetch(input, init);
};

