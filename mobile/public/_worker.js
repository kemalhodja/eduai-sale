// TalkCash Pages worker: /__api/* isteklerini backend'e proksiler, geri kalani
// statik asset olarak servis eder (SPA fallback _redirects ile birlikte calisir).
//
// BACKEND_ORIGIN wrangler.toml [vars] icinden gelir; yoksa default'a duser.
const DEFAULT_BACKEND = "https://talkcash-api-prod.onrender.com";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/__api" || url.pathname.startsWith("/__api/")) {
      const backend = (env && env.BACKEND_ORIGIN) || DEFAULT_BACKEND;
      const stripped = url.pathname.replace(/^\/__api/, "") || "/";
      const target = backend + stripped + url.search;

      if (request.headers.get("Upgrade") === "websocket") {
        return new Response("WebSocket proxy not supported on web", { status: 501 });
      }

      const proxied = new Request(target, request);
      proxied.headers.set("X-Forwarded-Host", url.hostname);
      let resp;
      try {
        resp = await fetch(proxied, { redirect: "manual" });
      } catch (err) {
        return new Response(
          JSON.stringify({ detail: "Backend unreachable" }),
          { status: 502, headers: { "Content-Type": "application/json" } },
        );
      }
      const headers = new Headers(resp.headers);
      headers.delete("set-cookie");
      return new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers });
    }

    if (url.pathname === "/health" || url.pathname === "/__health") {
      const backend = (env && env.BACKEND_ORIGIN) || DEFAULT_BACKEND;
      try {
        const resp = await fetch(backend + "/health", { signal: AbortSignal.timeout(8000) });
        const body = await resp.text();
        return new Response(body, { status: resp.status, headers: { "Content-Type": "application/json" } });
      } catch (err) {
        return new Response(JSON.stringify({ status: "down" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        });
      }
    }

    return env.ASSETS.fetch(request);
  },
};
