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
const PROXIED_SUFFIXES = [".json", ".ps1", ".sh"];
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
  if (!env.S3_PRESIGN_TTL_SECONDS) {
    return DEFAULT_PRESIGN_TTL_SECONDS;
  }

  const parsed = Number.parseInt(env.S3_PRESIGN_TTL_SECONDS, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return DEFAULT_PRESIGN_TTL_SECONDS;
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
