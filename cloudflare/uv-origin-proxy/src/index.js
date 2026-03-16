const SERVICE = "s3";
const ALGORITHM = "AWS4-HMAC-SHA256";
const EMPTY_PAYLOAD_SHA256 =
  "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
const ALLOWED_METHODS = new Set(["GET", "HEAD"]);
const FORWARDED_REQUEST_HEADERS = [
  "if-match",
  "if-modified-since",
  "if-none-match",
  "if-range",
  "if-unmodified-since",
  "range",
];
const REQUIRED_ENV_KEYS = [
  "S3_ORIGIN_ENDPOINT",
  "S3_BUCKET",
  "S3_REGION",
  "S3_ACCESS_KEY_ID",
  "S3_SECRET_ACCESS_KEY",
];
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
    const originUrl = buildOriginUrl(config.originEndpoint, config.bucket, requestUrl);
    const upstreamMethod = request.method === "HEAD" ? "GET" : request.method;
    const signingHeaders = await buildSigningHeaders(
      upstreamMethod,
      originUrl,
      config,
    );

    const upstreamHeaders = new Headers();
    for (const [name, value] of Object.entries(signingHeaders)) {
      upstreamHeaders.set(name, value);
    }
    for (const headerName of FORWARDED_REQUEST_HEADERS) {
      const headerValue = request.headers.get(headerName);
      if (headerValue) {
        upstreamHeaders.set(headerName, headerValue);
      }
    }

    const upstreamRequest = new Request(originUrl.toString(), {
      method: upstreamMethod,
      headers: upstreamHeaders,
      redirect: "manual",
      signal: request.signal,
    });

    const response = await fetch(upstreamRequest);
    const headers = new Headers(response.headers);
    headers.delete("alt-svc");

    return new Response(request.method === "HEAD" ? null : response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
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
    originEndpoint: env.S3_ORIGIN_ENDPOINT,
    region: env.S3_REGION,
    secretAccessKey: env.S3_SECRET_ACCESS_KEY,
    sessionToken: env.S3_SESSION_TOKEN || null,
  };
}

function buildOriginUrl(originEndpoint, bucket, requestUrl) {
  const originUrl = new URL(originEndpoint);
  const requestPath = requestUrl.pathname.startsWith("/")
    ? requestUrl.pathname
    : `/${requestUrl.pathname}`;
  const basePath = originUrl.pathname === "/"
    ? ""
    : originUrl.pathname.replace(/\/+$/, "");

  originUrl.pathname = `${basePath}/${bucket}${requestPath}`;
  originUrl.search = requestUrl.search;
  return originUrl;
}

async function buildSigningHeaders(method, originUrl, config) {
  const now = new Date();
  const amzDate = formatAmzDate(now);
  const dateStamp = amzDate.slice(0, 8);
  const payloadHash = EMPTY_PAYLOAD_SHA256;

  const headersToSign = {
    host: originUrl.host,
    "x-amz-content-sha256": payloadHash,
    "x-amz-date": amzDate,
  };
  if (config.sessionToken) {
    headersToSign["x-amz-security-token"] = config.sessionToken;
  }

  const { canonicalHeaders, signedHeaders } = buildCanonicalHeaders(headersToSign);
  const canonicalRequest = [
    method,
    canonicalizePath(originUrl.pathname),
    buildCanonicalQuery(originUrl.searchParams),
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");

  const credentialScope = `${dateStamp}/${config.region}/${SERVICE}/aws4_request`;
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
  const authorization =
    `${ALGORITHM} Credential=${config.accessKeyId}/${credentialScope}, ` +
    `SignedHeaders=${signedHeaders}, Signature=${signature}`;

  return {
    authorization,
    ...headersToSign,
  };
}

function buildCanonicalHeaders(headers) {
  const normalizedEntries = Object.entries(headers)
    .map(([name, value]) => [
      name.toLowerCase(),
      String(value).trim().replace(/\s+/g, " "),
    ])
    .sort(([left], [right]) => left.localeCompare(right));

  return {
    canonicalHeaders: normalizedEntries
      .map(([name, value]) => `${name}:${value}\n`)
      .join(""),
    signedHeaders: normalizedEntries.map(([name]) => name).join(";"),
  };
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
