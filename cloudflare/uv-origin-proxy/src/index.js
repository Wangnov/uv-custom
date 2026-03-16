export default {
  async fetch(request, env) {
    const requestUrl = new URL(request.url);
    const originUrl = new URL(env.ORIGIN_BASE_URL);

    originUrl.pathname = requestUrl.pathname;
    originUrl.search = requestUrl.search;

    const upstreamRequest = new Request(originUrl, request);
    const response = await fetch(upstreamRequest);
    const headers = new Headers(response.headers);

    // Let the origin control cache semantics and avoid leaking edge-specific hints.
    headers.delete("alt-svc");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
