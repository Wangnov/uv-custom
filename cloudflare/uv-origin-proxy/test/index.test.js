import assert from "node:assert/strict";
import test from "node:test";

import worker from "../src/index.js";

const ENV = {
  S3_ORIGIN_ENDPOINT: "https://fgws3-ocloud.ihep.ac.cn",
  S3_BUCKET: "20830-uv-custom",
  S3_REGION: "us-east-1",
  S3_ACCESS_KEY_ID: "test-access-key",
  S3_SECRET_ACCESS_KEY: "test-secret-key",
};

test("redirects GET requests to presigned S3 urls", async (t) => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("upstream", { status: 200 });
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/python-build-standalone/releases/download/20260310/cpython-3.12.13-plus-20260310-aarch64-apple-darwin-install_only_stripped.tar.gz",
    ),
    ENV,
  );

  assert.equal(response.status, 307);

  const location = response.headers.get("location");
  assert.ok(location);

  const signedUrl = new URL(location);
  assert.equal(signedUrl.origin, "https://fgws3-ocloud.ihep.ac.cn");
  assert.equal(
    signedUrl.pathname,
    "/20830-uv-custom/python-build-standalone/releases/download/20260310/cpython-3.12.13-plus-20260310-aarch64-apple-darwin-install_only_stripped.tar.gz",
  );
  assert.equal(
    signedUrl.searchParams.get("X-Amz-Algorithm"),
    "AWS4-HMAC-SHA256",
  );
  assert.equal(signedUrl.searchParams.get("X-Amz-SignedHeaders"), "host");
  assert.ok(signedUrl.searchParams.get("X-Amz-Signature"));
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("maps requests into the configured origin prefix", async () => {
  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/python-build-standalone/releases/download/20260310/example.tar.gz",
    ),
    {
      ...ENV,
      S3_KEY_PREFIX: "mirror",
    },
  );

  assert.equal(response.status, 307);
  const location = response.headers.get("location");
  assert.ok(location);
  assert.equal(
    new URL(location).pathname,
    "/20830-uv-custom/mirror/python-build-standalone/releases/download/20260310/example.tar.gz",
  );
});

test("proxies small metadata files instead of redirecting", async (t) => {
  const originalFetch = globalThis.fetch;
  let fetchedUrl = null;
  globalThis.fetch = async (input) => {
    fetchedUrl = typeof input === "string" ? input : input.url;
    return new Response('{"ok":true}', {
      status: 200,
      headers: {
        "content-type": "application/json",
        "alt-svc": 'h3=":443"; ma=86400',
      },
    });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request("https://uv.agentsmirror.com/metadata/python-downloads.json"),
    {
      ...ENV,
      S3_KEY_PREFIX: "mirror",
    },
  );

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/json");
  assert.equal(response.headers.get("alt-svc"), null);
  assert.match(
    fetchedUrl,
    /^https:\/\/fgws3-ocloud\.ihep\.ac\.cn\/20830-uv-custom\/mirror\/metadata\/python-downloads\.json\?/,
  );
  assert.equal(await response.text(), '{"ok":true}');
});
