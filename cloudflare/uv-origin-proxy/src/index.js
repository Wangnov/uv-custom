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
const textEncoder = new TextEncoder();

export default {
  async fetch(request, env) {
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
    const signedUrl = await buildPresignedUrl(
      request.method,
      requestUrl,
      config,
    );

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
