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
      "https://uv.agentsmirror.com/github/astral-sh/uv/releases/download/latest/uv-installer.sh",
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
    "/20830-uv-custom/github/astral-sh/uv/releases/download/latest/uv-installer.sh",
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
    new Request("https://uv.agentsmirror.com/install-cn.sh"),
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
    "/20830-uv-custom/mirror/install-cn.sh",
  );
});
