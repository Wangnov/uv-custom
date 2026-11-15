const SERVICE = "s3";
const ALGORITHM = "AWS4-HMAC-SHA256";
const UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD";
const ALLOWED_METHODS = new Set(["GET", "HEAD"]);
const REQUIRED_ENV_KEYS = [
  "S3_ORIGIN_ENDPOINT",
  "S3_BUCKET",
  "S3_REGION",
  "S3_ACCESS_KEY_ID",
  "S3_SECRET_ACCESS_KEY",
];
const DEFAULT_PRESIGN_TTL_SECONDS = 600;
const GITHUB_API_CACHE_TTL_SECONDS = 300;
const GITHUB_API_TAG_CACHE_TTL_SECONDS = 3600;
const GITHUB_RELEASES_PREFIXES = [
  "/github/api/v3/repos/astral-sh/uv/releases",
  "/api/v3/repos/astral-sh/uv/releases",
];
const UPSTREAM_UV_DOWNLOAD_PREFIX =
  "https://github.com/astral-sh/uv/releases/download/";
const UPSTREAM_GITHUB_API_PREFIX = "https://api.github.com/";
const SAFE_GITHUB_TAG_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$/;
const PROXIED_SUFFIXES = [".json", ".ps1", ".sh"];
// Object keys for Python runtimes are sanitized at sync time (see
// `uvmirror.metadata.mirror_path_for_python_download_url`), so `+` and `%` in the
// upstream filename become `-plus-` / `-pct-`. uv builds its own download URL by
// appending the upstream suffix to `UV_PYTHON_INSTALL_MIRROR`, which keeps the raw
// `+`. Normalizing here lets both spellings resolve to the same object.
const PYTHON_ASSET_PREFIXES = [
  "/python-build-standalone/releases/download/",
  "/pypy/",
  "/graalpython/releases/download/",
];
const textEncoder = new TextEncoder();

export default {
  async fetch(request, env, ctx) {
    if (!ALLOWED_METHODS.has(request.method)) {
      return jsonResponse(
        { error: "method_not_allowed", allow: [...ALLOWED_METHODS] },
        405,
        { allow: [...ALLOWED_METHODS].join(", ") },
      );
    }

    const config = getConfig(env);
    if ("error" in config) {
      return jsonResponse(
        {
          error: "worker_misconfigured",
          message: `Missing Worker bindings: ${config.error.join(", ")}`,
        },
        500,
      );
    }

    const requestUrl = new URL(request.url);
    const pypiCanonicalResponse = getPypiCanonicalResponse(requestUrl);
    if (pypiCanonicalResponse) {
      return pypiCanonicalResponse;
    }

    if (isPypiSimpleRequest(requestUrl.pathname)) {
      return proxyPypiSimple(request, requestUrl, env, config, ctx);
    }

    if (isPypiFileMetadataRequest(requestUrl.pathname)) {
      return proxyPypiFileMetadata(request, requestUrl, env, config, ctx);
    }

    if (isPypiFileRequest(requestUrl.pathname)) {
      return proxyPypiFile(request, requestUrl, env);
    }

    const githubReleasesRoute = matchGithubReleasesRoute(requestUrl, env);
    if (githubReleasesRoute) {
      if ("error" in githubReleasesRoute) {
        return githubReleasesRoute.error;
      }
      return proxyGithubReleases(
        request,
        requestUrl,
        env,
        config,
        ctx,
        githubReleasesRoute,
      );
    }

    normalizePythonAssetPath(requestUrl);

    const signedUrl = await buildPresignedUrl(
      request.method,
      requestUrl,
      config,
    );

    if (shouldProxyThroughWorker(requestUrl.pathname)) {
      const upstreamResponse = await fetch(
        new Request(signedUrl.toString(), {
          method: request.method,
          redirect: "manual",
          signal: request.signal,
        }),
      );
      const headers = new Headers(upstreamResponse.headers);
      headers.delete("alt-svc");
      return new Response(
        request.method === "HEAD" ? null : upstreamResponse.body,
        {
          status: upstreamResponse.status,
          statusText: upstreamResponse.statusText,
          headers,
        },
      );
    }

    return new Response(null, {
      status: 307,
      headers: {
        "cache-control": "no-store",
        location: signedUrl.toString(),
      },
    });
  },
};

function getConfig(env) {
  const missing = REQUIRED_ENV_KEYS.filter((key) => !env[key]);
  if (missing.length > 0) {
    return { error: missing };
  }

  return {
    accessKeyId: env.S3_ACCESS_KEY_ID,
    bucket: env.S3_BUCKET,
    keyPrefix: normalizeKeyPrefix(env.S3_KEY_PREFIX || ""),
    originEndpoint: env.S3_ORIGIN_ENDPOINT,
    presignTtlSeconds: getPresignTtlSeconds(env),
    region: env.S3_REGION,
    secretAccessKey: env.S3_SECRET_ACCESS_KEY,
    sessionToken: env.S3_SESSION_TOKEN || null,
  };
}

function getPresignTtlSeconds(env) {
  return getPositiveIntEnv(env.S3_PRESIGN_TTL_SECONDS, DEFAULT_PRESIGN_TTL_SECONDS);
}

function getPositiveIntEnv(rawValue, fallback) {
  if (!rawValue) {
    return fallback;
  }

  const parsed = Number.parseInt(rawValue, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return parsed;
}

function normalizeKeyPrefix(value) {
  return value.replace(/^\/+|\/+$/g, "");
}

async function proxyPypiSimple(request, requestUrl, env, config, ctx) {
  const projectName = getSimpleProjectName(requestUrl.pathname);
  const upstreamBaseUrl = (env.PYPI_SIMPLE_UPSTREAM || "https://pypi.org/simple").replace(/\/+$/g, "");
  const upstreamUrl = `${upstreamBaseUrl}/${projectName}/`;
  const accept = request.headers.get("accept") || "application/vnd.pypi.simple.v1+json";
  const format = wantsSimpleJson(accept) ? "json" : "html";
  const cacheKey = buildPypiSimpleCacheKey(env, projectName, format);
  const contentType = format === "json"
    ? "application/vnd.pypi.simple.v1+json; charset=utf-8"
    : "application/vnd.pypi.simple.v1+html; charset=utf-8";

  const cachedResponse = await fetchFreshCacheEntry(
    request,
    cacheKey,
    config,
    300,
    contentType,
  );
  if (cachedResponse) {
    return cachedResponse;
  }

  const upstreamResponse = await fetch(
    new Request(upstreamUrl, {
      method: request.method,
      headers: {
        accept,
      },
      redirect: "manual",
      signal: request.signal,
    }),
  );

  if (!upstreamResponse.ok) {
    return upstreamResponse;
  }

  if (request.method === "HEAD") {
    return new Response(null, {
      status: upstreamResponse.status,
      statusText: upstreamResponse.statusText,
      headers: {
        "content-type": contentType,
        "cache-control": "public, max-age=300",
      },
    });
  }

  let responseBody;
  if (format === "json") {
    const payload = await upstreamResponse.json();
    payload.files = (payload.files || []).map((file) => ({
      ...file,
      url: rewritePackageFileUrl(file.url, requestUrl.origin),
    }));
    responseBody = JSON.stringify(payload);
  } else {
    responseBody = rewriteSimpleHtml(
      await upstreamResponse.text(),
      requestUrl.origin,
    );
  }

  if (request.method === "GET" && typeof ctx?.waitUntil === "function") {
    ctx.waitUntil(storeCacheEntry(cacheKey, responseBody, config));
  }

  return new Response(responseBody, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: {
      "content-type": contentType,
    },
  });
}

async function proxyPypiFileMetadata(request, requestUrl, env, config, ctx) {
  const cacheKey = buildPypiMetadataCacheKey(env, requestUrl.pathname);
  const cachedResponse = await fetchCachedEntry(
    request,
    cacheKey,
    config,
    "application/octet-stream",
  );
  if (cachedResponse) {
    return cachedResponse;
  }

  const upstreamUrl = buildPypiFileUpstreamUrl(requestUrl.pathname);
  const upstreamResponse = await fetch(
    new Request(upstreamUrl, {
      method: request.method,
      redirect: "manual",
      signal: request.signal,
    }),
  );

  if (!upstreamResponse.ok) {
    return upstreamResponse;
  }

  const responseBody = request.method === "HEAD"
    ? null
    : await upstreamResponse.text();
  if (
    request.method === "GET" &&
    responseBody !== null &&
    typeof ctx?.waitUntil === "function"
  ) {
    ctx.waitUntil(storeCacheEntry(cacheKey, responseBody, config));
  }

  return new Response(responseBody, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: {
      "content-type": "application/octet-stream",
    },
  });
}

async function fetchFreshCacheEntry(request, key, config, maxAgeSeconds, contentType) {
  const cachedResponse = await fetchFromS3Key(request.method, key, config, request.signal);
  if (!cachedResponse.ok || !isResponseFresh(cachedResponse, maxAgeSeconds)) {
    return null;
  }

  const headers = new Headers();
  headers.set("content-type", contentType);
  headers.set("cache-control", `public, max-age=${maxAgeSeconds}`);
  return new Response(request.method === "HEAD" ? null : cachedResponse.body, {
    status: cachedResponse.status,
    statusText: cachedResponse.statusText,
    headers,
  });
}

async function fetchCachedEntry(request, key, config, contentType) {
  const cachedResponse = await fetchFromS3Key(request.method, key, config, request.signal);
  if (!cachedResponse.ok) {
    return null;
  }

  const headers = new Headers();
  headers.set("content-type", contentType);
  return new Response(request.method === "HEAD" ? null : cachedResponse.body, {
    status: cachedResponse.status,
    statusText: cachedResponse.statusText,
    headers,
  });
}

async function proxyPypiFile(request, requestUrl, env) {
  const primaryBaseUrl = (env.PYPI_FILE_PRIMARY_UPSTREAM || "https://pypi.tuna.tsinghua.edu.cn").replace(/\/+$/g, "");
  const fallbackBaseUrl = (env.PYPI_FILE_FALLBACK_UPSTREAM || "https://files.pythonhosted.org").replace(/\/+$/g, "");
  const upstreamPath = getPypiFilePath(requestUrl.pathname);
  const candidates = [
    `${primaryBaseUrl}${upstreamPath}`,
    `${fallbackBaseUrl}${upstreamPath}`,
  ];
  let lastResponse = null;

  for (const candidate of candidates) {
    try {
      const response = await fetch(
        new Request(candidate, {
          method: request.method,
          redirect: "manual",
          signal: request.signal,
        }),
      );
      if (response.status < 400) {
        return response;
      }
      lastResponse = response;
    } catch {
      continue;
    }
  }

  if (lastResponse) {
    return lastResponse;
  }

  return new Response("upstream file unavailable", { status: 502 });
}

function matchGithubReleasesRoute(requestUrl, env) {
  const { pathname, searchParams } = requestUrl;
  const matchedPrefix = GITHUB_RELEASES_PREFIXES.find(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
  if (!matchedPrefix) {
    return null;
  }

  const apiPrefix = matchedPrefix.startsWith("/github/api/v3")
    ? "/github/api/v3"
    : "/api/v3";
  const suffix = pathname.slice(matchedPrefix.length);

  if (suffix === "" || suffix === "/") {
    const page = parsePositiveBoundedInt(searchParams.get("page"), 1, 100);
    const perPage = parsePositiveBoundedInt(searchParams.get("per_page"), 1, 100);
    if (page === "invalid" || perPage === "invalid") {
      return {
        error: jsonResponse(
          { error: "invalid_query", message: "Invalid pagination parameters" },
          400,
        ),
      };
    }

    const isDefaultList =
      (page === null || page === 1) && (perPage === null || perPage === 30);
    const ttlSeconds = getPositiveIntEnv(
      env.GITHUB_API_CACHE_TTL_SECONDS,
      GITHUB_API_CACHE_TTL_SECONDS,
    );
    if (isDefaultList) {
      return {
        apiPrefix,
        syncedKey: "github/api/v3/repos/astral-sh/uv/releases.json",
        cacheKey: buildGithubApiCacheKey(
          env,
          "repos/astral-sh/uv/releases.json",
        ),
        upstreamPath: "/repos/astral-sh/uv/releases?per_page=30",
        ttlSeconds,
      };
    }

    const resolvedPage = page ?? 1;
    const resolvedPerPage = perPage ?? 30;
    return {
      apiPrefix,
      syncedKey: null,
      cacheKey: buildGithubApiCacheKey(
        env,
        `repos/astral-sh/uv/releases-p${resolvedPage}-pp${resolvedPerPage}.json`,
      ),
      upstreamPath: `/repos/astral-sh/uv/releases?per_page=${resolvedPerPage}&page=${resolvedPage}`,
      ttlSeconds,
    };
  }

  if (suffix === "/latest" || suffix === "/latest/") {
    return {
      apiPrefix,
      syncedKey: "github/api/v3/repos/astral-sh/uv/releases/latest.json",
      cacheKey: buildGithubApiCacheKey(
        env,
        "repos/astral-sh/uv/releases/latest.json",
      ),
      upstreamPath: "/repos/astral-sh/uv/releases/latest",
      ttlSeconds: getPositiveIntEnv(
        env.GITHUB_API_CACHE_TTL_SECONDS,
        GITHUB_API_CACHE_TTL_SECONDS,
      ),
    };
  }

  if (suffix.startsWith("/tags/")) {
    const rawTag = suffix.slice("/tags/".length);
    let tag;
    try {
      tag = decodeURIComponent(rawTag);
    } catch {
      return {
        error: jsonResponse(
          { error: "invalid_release_tag", message: "Invalid release tag" },
          400,
        ),
      };
    }
    if (!tag || tag.includes("..") || !SAFE_GITHUB_TAG_PATTERN.test(tag)) {
      return {
        error: jsonResponse(
          { error: "invalid_release_tag", message: "Invalid release tag" },
          400,
        ),
      };
    }

    return {
      apiPrefix,
      tag,
      syncedKey: `github/api/v3/repos/astral-sh/uv/releases/tags/${tag}.json`,
      cacheKey: buildGithubApiCacheKey(
        env,
        `repos/astral-sh/uv/releases/tags/${tag}.json`,
      ),
      upstreamPath: `/repos/astral-sh/uv/releases/tags/${encodeURIComponent(tag)}`,
      ttlSeconds: getPositiveIntEnv(
        env.GITHUB_API_TAG_CACHE_TTL_SECONDS,
        GITHUB_API_TAG_CACHE_TTL_SECONDS,
      ),
    };
  }

  return {
    error: jsonResponse({ message: "Not Found" }, 404),
  };
}

function parsePositiveBoundedInt(rawValue, min, max) {
  if (rawValue === null) {
    return null;
  }
  if (!/^[1-9]\d*$/.test(rawValue)) {
    return "invalid";
  }
  const parsed = Number.parseInt(rawValue, 10);
  if (!Number.isFinite(parsed) || parsed < min || parsed > max) {
    return "invalid";
  }
  return parsed;
}

function buildGithubApiCacheKey(env, suffix) {
  const prefix = normalizeKeyPrefix(
    env.GITHUB_API_CACHE_PREFIX || "github-api-cache",
  );
  return `${prefix}/${suffix}`;
}

async function proxyGithubReleases(request, requestUrl, env, config, ctx, route) {
  if (route.syncedKey) {
    const syncedResponse = await fetchFromS3Key(
      request.method,
      route.syncedKey,
      config,
      request.signal,
    );
    if (syncedResponse.ok) {
      return buildGithubReleaseResponse(
        request.method,
        syncedResponse,
        requestUrl.origin,
        route.apiPrefix,
        route.ttlSeconds,
      );
    }
  }

  const cachedResponse = await fetchFromS3Key(
    request.method,
    route.cacheKey,
    config,
    request.signal,
  );
  if (cachedResponse.ok && isResponseFresh(cachedResponse, route.ttlSeconds)) {
    return buildGithubReleaseResponse(
      request.method,
      cachedResponse,
      requestUrl.origin,
      route.apiPrefix,
      route.ttlSeconds,
    );
  }

  const githubApiBase = (
    env.GITHUB_API_UPSTREAM || "https://api.github.com"
  ).replace(/\/+$/g, "");
  const upstreamUrl = `${githubApiBase}${route.upstreamPath}`;
  const upstreamHeaders = new Headers({
    accept: "application/vnd.github+json",
    "user-agent": "uv-custom-origin-proxy",
    "x-github-api-version": "2022-11-28",
  });
  if (env.GITHUB_TOKEN) {
    upstreamHeaders.set("authorization", `Bearer ${env.GITHUB_TOKEN}`);
  }

  let upstreamResponse = null;
  try {
    upstreamResponse = await fetch(
      new Request(upstreamUrl, {
        method: request.method,
        headers: upstreamHeaders,
        redirect: "manual",
        signal: request.signal,
      }),
    );
  } catch {
    if (cachedResponse.ok) {
      return buildGithubReleaseResponse(
        request.method,
        cachedResponse,
        requestUrl.origin,
        route.apiPrefix,
        route.ttlSeconds,
      );
    }
    if (route.tag) {
      const webFallback = await fallbackGithubReleaseFromWeb(
        request,
        requestUrl,
        env,
        config,
        ctx,
        route,
      );
      if (webFallback) {
        return webFallback;
      }
    }
    return jsonResponse(
      {
        error: "upstream_unavailable",
        message: "GitHub Releases API unavailable",
      },
      502,
    );
  }

  if (!upstreamResponse.ok) {
    const isTransientOrRateLimit =
      upstreamResponse.status === 403 ||
      upstreamResponse.status === 429 ||
      upstreamResponse.status >= 500;
    if (cachedResponse.ok && isTransientOrRateLimit) {
      return buildGithubReleaseResponse(
        request.method,
        cachedResponse,
        requestUrl.origin,
        route.apiPrefix,
        route.ttlSeconds,
      );
    }
    if (route.tag && isTransientOrRateLimit) {
      const webFallback = await fallbackGithubReleaseFromWeb(
        request,
        requestUrl,
        env,
        config,
        ctx,
        route,
      );
      if (webFallback) {
        return webFallback;
      }
    }
    return new Response(
      request.method === "HEAD" ? null : upstreamResponse.body,
      {
        status: upstreamResponse.status,
        statusText: upstreamResponse.statusText,
        headers: {
          "content-type":
            upstreamResponse.headers.get("content-type") ||
            "application/json; charset=utf-8",
        },
      },
    );
  }

  if (request.method === "HEAD") {
    return new Response(null, {
      status: upstreamResponse.status,
      statusText: upstreamResponse.statusText,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": `public, max-age=${route.ttlSeconds}`,
      },
    });
  }

  const payload = await upstreamResponse.json();
  const rewritten = rewriteGithubReleasePayload(
    payload,
    requestUrl.origin,
    route.apiPrefix,
  );
  const responseBody = JSON.stringify(rewritten);

  if (typeof ctx?.waitUntil === "function") {
    ctx.waitUntil(storeCacheEntry(route.cacheKey, responseBody, config));
  }

  return new Response(responseBody, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `public, max-age=${route.ttlSeconds}`,
    },
  });
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function guessGithubAssetContentType(name) {
  const lower = name.toLowerCase();
  if (lower.endsWith(".sh")) return "application/x-sh";
  if (lower.endsWith(".ps1")) return "text/plain";
  if (lower.endsWith(".json")) return "application/json";
  if (lower.endsWith(".tar.gz") || lower.endsWith(".tgz")) return "application/gzip";
  if (lower.endsWith(".tar.xz") || lower.endsWith(".txz")) return "application/x-xz";
  if (lower.endsWith(".tar.zst")) return "application/zstd";
  if (lower.endsWith(".zip")) return "application/zip";
  if (lower.endsWith(".sha256") || lower.endsWith(".sum") || lower.endsWith(".txt")) {
    return "text/plain";
  }
  return "application/octet-stream";
}

function parseGithubExpandedAssetsHtml(html, tag, publicOrigin, apiPrefix) {
  const encodedTag = encodeURIComponent(tag);
  const tagVariants =
    encodedTag === tag
      ? escapeRegExp(tag)
      : `(?:${escapeRegExp(tag)}|${escapeRegExp(encodedTag)})`;
  const hrefPattern = new RegExp(
    `href="/astral-sh/uv/releases/download/${tagVariants}/([^"'?#<>\\s]+)"`,
    "g",
  );
  const seenNames = new Set();
  const assets = [];
  let match;
  while ((match = hrefPattern.exec(html)) !== null) {
    let assetName;
    try {
      assetName = decodeURIComponent(match[1]);
    } catch {
      assetName = match[1];
    }
    if (!assetName || assetName.includes("/") || seenNames.has(assetName)) {
      continue;
    }
    seenNames.add(assetName);
    const assetId = assets.length + 1;
    assets.push({
      url: `${publicOrigin}${apiPrefix}/repos/astral-sh/uv/releases/assets/${assetId}`,
      id: assetId,
      name: assetName,
      content_type: guessGithubAssetContentType(assetName),
      state: "uploaded",
      size: 0,
      browser_download_url: `${publicOrigin}/github/astral-sh/uv/releases/download/${encodedTag}/${encodeURIComponent(assetName)}`,
    });
  }
  if (assets.length === 0) {
    return null;
  }

  const timeMatch = html.match(/<relative-time[^>]*datetime="([^"]+)"/i);
  const publishedAt = timeMatch ? timeMatch[1] : new Date().toISOString();
  return {
    url: `${publicOrigin}${apiPrefix}/repos/astral-sh/uv/releases/1`,
    assets_url: `${publicOrigin}${apiPrefix}/repos/astral-sh/uv/releases/1/assets`,
    html_url: `https://github.com/astral-sh/uv/releases/tag/${encodedTag}`,
    id: 1,
    tag_name: tag,
    name: tag,
    draft: false,
    prerelease: false,
    published_at: publishedAt,
    assets,
  };
}

async function fallbackGithubReleaseFromWeb(
  request,
  requestUrl,
  env,
  config,
  ctx,
  route,
) {
  const githubWebBase = (
    env.GITHUB_WEB_UPSTREAM || "https://github.com"
  ).replace(/\/+$/g, "");
  const expandedUrl = `${githubWebBase}/astral-sh/uv/releases/expanded_assets/${encodeURIComponent(route.tag)}`;

  let webResponse;
  try {
    webResponse = await fetch(
      new Request(expandedUrl, {
        method: "GET",
        headers: {
          accept: "text/html",
          "user-agent": "uv-custom-origin-proxy",
        },
        redirect: "follow",
        signal: request.signal,
      }),
    );
  } catch {
    return null;
  }

  if (webResponse.status === 404) {
    return jsonResponse({ message: "Not Found" }, 404);
  }
  if (!webResponse.ok) {
    return null;
  }

  const html = await webResponse.text();
  const synthesized = parseGithubExpandedAssetsHtml(
    html,
    route.tag,
    requestUrl.origin,
    route.apiPrefix,
  );
  if (!synthesized) {
    return jsonResponse({ message: "Not Found" }, 404);
  }

  const headers = {
    "content-type": "application/json; charset=utf-8",
    "cache-control": `public, max-age=${route.ttlSeconds}`,
  };
  if (request.method === "HEAD") {
    return new Response(null, { status: 200, headers });
  }

  const responseBody = JSON.stringify(synthesized);
  if (typeof ctx?.waitUntil === "function") {
    ctx.waitUntil(storeCacheEntry(route.cacheKey, responseBody, config));
  }
  return new Response(responseBody, { status: 200, headers });
}

async function buildGithubReleaseResponse(
  method,
  s3Response,
  publicOrigin,
  apiPrefix,
  maxAgeSeconds,
) {
  const headers = {
    "content-type": "application/json; charset=utf-8",
    "cache-control": `public, max-age=${maxAgeSeconds}`,
  };
  if (method === "HEAD") {
    return new Response(null, {
      status: s3Response.status,
      statusText: s3Response.statusText,
      headers,
    });
  }

  const rawText = await s3Response.text();
  if (!needsGithubReleaseRewrite(rawText, publicOrigin)) {
    return new Response(rawText, {
      status: s3Response.status,
      statusText: s3Response.statusText,
      headers,
    });
  }

  const payload = JSON.parse(rawText);
  const rewritten = rewriteGithubReleasePayload(payload, publicOrigin, apiPrefix);
  return new Response(JSON.stringify(rewritten), {
    status: s3Response.status,
    statusText: s3Response.statusText,
    headers,
  });
}

function needsGithubReleaseRewrite(rawText, publicOrigin) {
  if (
    rawText.includes(UPSTREAM_UV_DOWNLOAD_PREFIX) ||
    rawText.includes(UPSTREAM_GITHUB_API_PREFIX)
  ) {
    return true;
  }
  return (
    !rawText.includes(publicOrigin) &&
    (rawText.includes("/github/astral-sh/uv/releases/download/") ||
      rawText.includes("/api/v3/"))
  );
}

function rewriteGithubReleasePayload(payload, publicOrigin, apiPrefix) {
  if (Array.isArray(payload)) {
    return payload.map((item) =>
      rewriteSingleGithubRelease(item, publicOrigin, apiPrefix, false),
    );
  }
  if (payload && typeof payload === "object") {
    return rewriteSingleGithubRelease(payload, publicOrigin, apiPrefix, true);
  }
  return payload;
}

function rewriteSingleGithubRelease(release, publicOrigin, apiPrefix, includeBody) {
  if (!release || typeof release !== "object") {
    return release;
  }

  const rewritten = { ...release };
  delete rewritten.author;
  delete rewritten.reactions;
  if (!includeBody) {
    delete rewritten.body;
  }

  for (const field of [
    "url",
    "assets_url",
    "upload_url",
    "tarball_url",
    "zipball_url",
  ]) {
    if (typeof rewritten[field] === "string") {
      rewritten[field] = rewriteGithubApiUrl(
        rewritten[field],
        publicOrigin,
        apiPrefix,
      );
    }
  }

  if (Array.isArray(release.assets)) {
    rewritten.assets = release.assets.map((asset) => {
      if (!asset || typeof asset !== "object") {
        return asset;
      }
      const nextAsset = { ...asset };
      delete nextAsset.uploader;
      if (typeof nextAsset.browser_download_url === "string") {
        nextAsset.browser_download_url = rewriteGithubDownloadUrl(
          nextAsset.browser_download_url,
          publicOrigin,
        );
      }
      if (typeof nextAsset.url === "string") {
        nextAsset.url = rewriteGithubApiUrl(
          nextAsset.url,
          publicOrigin,
          apiPrefix,
        );
      }
      return nextAsset;
    });
  }

  return rewritten;
}

function rewriteGithubDownloadUrl(value, publicOrigin) {
  if (value.startsWith(UPSTREAM_UV_DOWNLOAD_PREFIX)) {
    const suffix = value.slice(UPSTREAM_UV_DOWNLOAD_PREFIX.length);
    return `${publicOrigin}/github/astral-sh/uv/releases/download/${suffix}`;
  }
  try {
    const parsed = new URL(value);
    if (parsed.pathname.startsWith("/github/astral-sh/uv/releases/download/")) {
      return `${publicOrigin}${parsed.pathname}${parsed.search}${parsed.hash}`;
    }
  } catch {
    return value;
  }
  return value;
}

function rewriteGithubApiUrl(value, publicOrigin, apiPrefix) {
  if (value.startsWith(UPSTREAM_GITHUB_API_PREFIX)) {
    return `${publicOrigin}${apiPrefix}/${value.slice(UPSTREAM_GITHUB_API_PREFIX.length)}`;
  }
  try {
    const parsed = new URL(value);
    for (const prefix of ["/github/api/v3/", "/api/v3/"]) {
      if (parsed.pathname.startsWith(prefix)) {
        const rest = parsed.pathname.slice(prefix.length);
        return `${publicOrigin}${apiPrefix}/${rest}${parsed.search}${parsed.hash}`;
      }
    }
  } catch {
    return value;
  }
  return value;
}

async function fetchFromS3Key(method, key, config, signal) {
  const signedUrl = await buildPresignedUrlForPath(method, `/${key}`, config);
  return fetch(
    new Request(signedUrl.toString(), {
      method,
      redirect: "manual",
      signal,
    }),
  );
}

async function storeCacheEntry(key, body, config) {
  const signedUrl = await buildPresignedUrlForPath("PUT", `/${key}`, config);
  await fetch(
    new Request(signedUrl.toString(), {
      method: "PUT",
      body,
      redirect: "manual",
    }),
  );
}

function getPypiCanonicalResponse(requestUrl) {
  const projectName = getSimpleProjectName(requestUrl.pathname);
  if (!projectName) {
    return null;
  }

  const normalizedProjectName = normalizeProjectName(projectName);
  if (
    requestUrl.pathname === `/pypi/simple/${normalizedProjectName}/`
  ) {
    return null;
  }

  const canonicalUrl = new URL(requestUrl.toString());
  canonicalUrl.pathname = `/pypi/simple/${normalizedProjectName}/`;
  return new Response(null, {
    status: 308,
    headers: {
      location: canonicalUrl.toString(),
    },
  });
}

function getSimpleProjectName(pathname) {
  const match = pathname.match(/^\/pypi\/simple\/([^/]+)\/?$/);
  return match ? match[1] : null;
}

function isPypiSimpleRequest(pathname) {
  return getSimpleProjectName(pathname) !== null;
}

function isPypiFileMetadataRequest(pathname) {
  return pathname.startsWith("/pypi/files/") && pathname.endsWith(".metadata");
}

function isPypiFileRequest(pathname) {
  return pathname.startsWith("/pypi/files/");
}

function normalizeProjectName(projectName) {
  return projectName.toLowerCase().replace(/[-_.]+/g, "-");
}

function wantsSimpleJson(acceptHeader) {
  return acceptHeader.includes("application/vnd.pypi.simple.v1+json");
}

function rewritePackageFileUrl(fileUrl, publicOrigin) {
  if (!fileUrl) {
    return fileUrl;
  }

  const parsed = new URL(fileUrl);
  return `${publicOrigin}/pypi/files/${parsed.host}${parsed.pathname}${parsed.search}${parsed.hash}`;
}

function rewriteSimpleHtml(html, publicOrigin) {
  return html.replace(/href=(["'])(https:\/\/[^"']+)\1/g, (full, quote, href) => {
    return `href=${quote}${rewritePackageFileUrl(href, publicOrigin)}${quote}`;
  });
}

function buildPypiSimpleCacheKey(env, projectName, format) {
  const prefix = normalizeKeyPrefix(env.PYPI_CACHE_PREFIX || "pypi-cache");
  return `${prefix}/simple/${format}/${projectName}`;
}

function buildPypiMetadataCacheKey(env, pathname) {
  const prefix = normalizeKeyPrefix(env.PYPI_CACHE_PREFIX || "pypi-cache");
  return `${prefix}/metadata/${pathname.slice("/pypi/files/".length)}`;
}

function buildPypiFileUpstreamUrl(pathname) {
  const filePath = getPypiFilePath(pathname);
  const host = getPypiFileHost(pathname);
  return `https://${host}${filePath}`;
}

function getPypiFileHost(pathname) {
  const suffix = pathname.slice("/pypi/files/".length);
  const slashIndex = suffix.indexOf("/");
  return suffix.slice(0, slashIndex);
}

function getPypiFilePath(pathname) {
  const suffix = pathname.slice("/pypi/files/".length);
  const slashIndex = suffix.indexOf("/");
  return suffix.slice(slashIndex);
}

function isResponseFresh(response, maxAgeSeconds) {
  const lastModified = response.headers.get("last-modified");
  if (!lastModified) {
    return false;
  }

  const lastModifiedMs = Date.parse(lastModified);
  if (Number.isNaN(lastModifiedMs)) {
    return false;
  }

  return Date.now() - lastModifiedMs <= maxAgeSeconds * 1000;
}

function shouldProxyThroughWorker(pathname) {
  return PROXIED_SUFFIXES.some((suffix) => pathname.endsWith(suffix));
}

function isPythonAssetRequest(pathname) {
  return PYTHON_ASSET_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function sanitizePythonAssetSegment(segment) {
  let decoded;
  try {
    decoded = decodeURIComponent(segment);
  } catch {
    // Leave malformed percent-escapes untouched; the origin decides the outcome.
    return segment;
  }
  return decoded.replace(/%/g, "-pct-").replace(/\+/g, "-plus-");
}

/**
 * Rewrite a Python runtime request in place so the raw upstream filename and the
 * sanitized object key both resolve. Already-sanitized paths contain no `+` or `%`,
 * so this is a no-op for them and stays idempotent.
 */
function normalizePythonAssetPath(requestUrl) {
  if (!isPythonAssetRequest(requestUrl.pathname)) {
    return requestUrl;
  }

  requestUrl.pathname = requestUrl.pathname
    .split("/")
    .map((segment) => sanitizePythonAssetSegment(segment))
    .join("/");
  return requestUrl;
}

function buildOriginUrl(originEndpoint, bucket, requestUrl, keyPrefix = "") {
  const originUrl = new URL(originEndpoint);
  const requestPath = requestUrl.pathname.startsWith("/")
    ? requestUrl.pathname
    : `/${requestUrl.pathname}`;
  const basePath = originUrl.pathname === "/"
    ? ""
    : originUrl.pathname.replace(/\/+$/, "");
  const prefixPath = keyPrefix ? `/${keyPrefix}` : "";
  originUrl.pathname = `${basePath}/${bucket}${prefixPath}${requestPath}`;
  originUrl.search = requestUrl.search;
  return originUrl;
}

async function buildPresignedUrlForPath(method, pathname, config, now = new Date()) {
  const requestUrl = new URL("https://cache.internal");
  requestUrl.pathname = pathname;
  return buildPresignedUrl(method, requestUrl, config, now);
}

async function buildPresignedUrl(method, requestUrl, config, now = new Date()) {
  const originUrl = buildOriginUrl(
    config.originEndpoint,
    config.bucket,
    requestUrl,
    config.keyPrefix,
  );
  const amzDate = formatAmzDate(now);
  const dateStamp = amzDate.slice(0, 8);
  const signedHeaders = "host";
  const credentialScope = `${dateStamp}/${config.region}/${SERVICE}/aws4_request`;

  originUrl.searchParams.set("X-Amz-Algorithm", ALGORITHM);
  originUrl.searchParams.set(
    "X-Amz-Credential",
    `${config.accessKeyId}/${credentialScope}`,
  );
  originUrl.searchParams.set("X-Amz-Date", amzDate);
  originUrl.searchParams.set(
    "X-Amz-Expires",
    String(config.presignTtlSeconds),
  );
  originUrl.searchParams.set("X-Amz-SignedHeaders", signedHeaders);
  if (config.sessionToken) {
    originUrl.searchParams.set("X-Amz-Security-Token", config.sessionToken);
  }

  const canonicalRequest = [
    method,
    canonicalizePath(originUrl.pathname),
    buildCanonicalQuery(originUrl.searchParams),
    `host:${originUrl.host}\n`,
    signedHeaders,
    UNSIGNED_PAYLOAD,
  ].join("\n");

  const stringToSign = [
    ALGORITHM,
    amzDate,
    credentialScope,
    await sha256Hex(canonicalRequest),
  ].join("\n");

  const signingKey = await deriveSigningKey(
    config.secretAccessKey,
    dateStamp,
    config.region,
    SERVICE,
  );
  const signature = await hmacHex(signingKey, stringToSign);
  originUrl.searchParams.set("X-Amz-Signature", signature);
  return originUrl;
}

function buildCanonicalQuery(searchParams) {
  return Array.from(searchParams.entries())
    .map(([key, value]) => [awsEncode(key), awsEncode(value)])
    .sort(([leftKey, leftValue], [rightKey, rightValue]) => {
      if (leftKey === rightKey) {
        return leftValue.localeCompare(rightValue);
      }
      return leftKey.localeCompare(rightKey);
    })
    .map(([key, value]) => `${key}=${value}`)
    .join("&");
}

function canonicalizePath(pathname) {
  return pathname
    .split("/")
    .map((segment) => awsEncode(decodeURIComponent(segment)))
    .join("/");
}

function awsEncode(value) {
  return encodeURIComponent(value).replace(
    /[!'()*]/g,
    (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

function formatAmzDate(date) {
  return date.toISOString().replace(/[:-]|\.\d{3}/g, "");
}

async function sha256Hex(value) {
  const bytes = typeof value === "string" ? textEncoder.encode(value) : value;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return toHex(digest);
}

async function deriveSigningKey(secretAccessKey, dateStamp, region, service) {
  const kDate = await hmacBytes(`AWS4${secretAccessKey}`, dateStamp);
  const kRegion = await hmacBytes(kDate, region);
  const kService = await hmacBytes(kRegion, service);
  return hmacBytes(kService, "aws4_request");
}

async function hmacHex(key, value) {
  const signature = await hmacBytes(key, value);
  return toHex(signature);
}

async function hmacBytes(key, value) {
  const rawKey = typeof key === "string" ? textEncoder.encode(key) : key;
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    rawKey,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );

  return new Uint8Array(
    await crypto.subtle.sign("HMAC", cryptoKey, textEncoder.encode(value)),
  );
}

function toHex(input) {
  return Array.from(new Uint8Array(input))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function jsonResponse(payload, status, headers = {}) {
  const responseHeaders = new Headers(headers);
  responseHeaders.set("content-type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(payload), { status, headers: responseHeaders });
}
