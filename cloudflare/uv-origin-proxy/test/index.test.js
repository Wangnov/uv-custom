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

test("canonicalizes project names for pypi simple requests", async () => {
  const response = await worker.fetch(
    new Request("https://uv.agentsmirror.com/pypi/simple/Pillow"),
    ENV,
  );

  assert.equal(response.status, 308);
  assert.equal(
    response.headers.get("location"),
    "https://uv.agentsmirror.com/pypi/simple/pillow/",
  );
});

test("rewrites pypi simple json responses to self-hosted file urls", async (t) => {
  const originalFetch = globalThis.fetch;
  const upstreamCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    upstreamCalls.push(request);

    if (/\/pypi-cache\/simple\/json\/pillow$/.test(new URL(request.url).pathname)) {
      return new Response("missing", { status: 404 });
    }

    if (request.url === "https://pypi.org/simple/pillow/") {
      return new Response(
        JSON.stringify({
          files: [
            {
              filename: "pillow-12.1.1.whl",
              hashes: { sha256: "abc123" },
              url: "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl",
              "core-metadata": { sha256: "def456" },
            },
          ],
          meta: { "api-version": "1.4" },
          name: "pillow",
        }),
        {
          status: 200,
          headers: {
            "content-type": "application/vnd.pypi.simple.v1+json",
          },
        },
      );
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request("https://uv.agentsmirror.com/pypi/simple/pillow/", {
      headers: {
        accept: "application/vnd.pypi.simple.v1+json",
      },
    }),
    {
      ...ENV,
      PYPI_SIMPLE_UPSTREAM: "https://pypi.org/simple",
    },
  );

  assert.equal(response.status, 200);
  assert.equal(
    response.headers.get("content-type"),
    "application/vnd.pypi.simple.v1+json; charset=utf-8",
  );

  const payload = await response.json();
  assert.equal(payload.name, "pillow");
  assert.equal(
    payload.files[0].url,
    "https://uv.agentsmirror.com/pypi/files/files.pythonhosted.org/packages/example/pillow-12.1.1.whl",
  );
  assert.match(
    upstreamCalls[0].url,
    /^https:\/\/fgws3-ocloud\.ihep\.ac\.cn\/20830-uv-custom\/pypi-cache\/simple\/json\/pillow\?/,
  );
  assert.equal(upstreamCalls[1].url, "https://pypi.org/simple/pillow/");
  assert.equal(
    upstreamCalls[1].headers.get("accept"),
    "application/vnd.pypi.simple.v1+json",
  );
});

test("proxies dist-info metadata files from files.pythonhosted.org", async (t) => {
  const originalFetch = globalThis.fetch;
  const upstreamCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    upstreamCalls.push(request);

    if (
      /\/pypi-cache\/metadata\/files\.pythonhosted\.org\/packages\/example\/pillow-12\.1\.1\.whl\.metadata$/.test(
        new URL(request.url).pathname,
      )
    ) {
      return new Response("missing", { status: 404 });
    }

    if (
      request.url ===
      "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl.metadata"
    ) {
      return new Response("Metadata-Version: 2.4\nName: pillow\n", {
        status: 200,
        headers: {
          "content-type": "application/octet-stream",
        },
      });
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/pypi/files/files.pythonhosted.org/packages/example/pillow-12.1.1.whl.metadata",
    ),
    ENV,
  );

  assert.equal(response.status, 200);
  assert.equal(
    response.headers.get("content-type"),
    "application/octet-stream",
  );
  assert.equal(await response.text(), "Metadata-Version: 2.4\nName: pillow\n");
  assert.match(
    upstreamCalls[0].url,
    /^https:\/\/fgws3-ocloud\.ihep\.ac\.cn\/20830-uv-custom\/pypi-cache\/metadata\/files\.pythonhosted\.org\/packages\/example\/pillow-12\.1\.1\.whl\.metadata\?/,
  );
  assert.equal(
    upstreamCalls[1].url,
    "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl.metadata",
  );
});

test("falls back to files.pythonhosted.org when primary pypi file mirror fails", async (t) => {
  const originalFetch = globalThis.fetch;
  const upstreamCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    upstreamCalls.push(request);

    if (
      request.url ===
      "https://pypi.tuna.tsinghua.edu.cn/packages/example/pillow-12.1.1.whl"
    ) {
      return new Response("forbidden", { status: 403 });
    }

    if (
      request.url ===
      "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl"
    ) {
      return new Response("wheel-payload", {
        status: 200,
        headers: {
          "content-type": "application/octet-stream",
        },
      });
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/pypi/files/files.pythonhosted.org/packages/example/pillow-12.1.1.whl",
    ),
    {
      ...ENV,
      PYPI_FILE_PRIMARY_UPSTREAM: "https://pypi.tuna.tsinghua.edu.cn",
      PYPI_FILE_FALLBACK_UPSTREAM: "https://files.pythonhosted.org",
    },
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "wheel-payload");
  assert.deepEqual(
    upstreamCalls.map((request) => request.url),
    [
      "https://pypi.tuna.tsinghua.edu.cn/packages/example/pillow-12.1.1.whl",
      "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl",
    ],
  );
});

test("falls back to files.pythonhosted.org when primary pypi file mirror throws", async (t) => {
  const originalFetch = globalThis.fetch;
  const upstreamCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    upstreamCalls.push(request);

    if (
      request.url ===
      "https://pypi.tuna.tsinghua.edu.cn/packages/example/pillow-12.1.1.whl"
    ) {
      throw new Error("network down");
    }

    if (
      request.url ===
      "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl"
    ) {
      return new Response("wheel-payload", { status: 200 });
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/pypi/files/files.pythonhosted.org/packages/example/pillow-12.1.1.whl",
    ),
    {
      ...ENV,
      PYPI_FILE_PRIMARY_UPSTREAM: "https://pypi.tuna.tsinghua.edu.cn",
      PYPI_FILE_FALLBACK_UPSTREAM: "https://files.pythonhosted.org",
    },
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "wheel-payload");
  assert.deepEqual(
    upstreamCalls.map((request) => request.url),
    [
      "https://pypi.tuna.tsinghua.edu.cn/packages/example/pillow-12.1.1.whl",
      "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl",
    ],
  );
});

test("serves fresh cached pypi simple json from s3 before hitting upstream", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);

    if (/\/pypi-cache\/simple\/json\/pillow$/.test(new URL(request.url).pathname)) {
      return new Response(
        JSON.stringify({
          files: [
            {
              filename: "pillow-12.1.1.whl",
              url: "https://uv.agentsmirror.com/pypi/files/files.pythonhosted.org/packages/example/pillow-12.1.1.whl",
            },
          ],
          meta: { "api-version": "1.4" },
          name: "pillow",
        }),
        {
          status: 200,
          headers: {
            "last-modified": new Date().toUTCString(),
          },
        },
      );
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request("https://uv.agentsmirror.com/pypi/simple/pillow/", {
      headers: {
        accept: "application/vnd.pypi.simple.v1+json",
      },
    }),
    {
      ...ENV,
      PYPI_CACHE_PREFIX: "pypi-cache",
      S3_KEY_PREFIX: "mirror",
    },
  );

  assert.equal(response.status, 200);
  assert.equal(
    response.headers.get("content-type"),
    "application/vnd.pypi.simple.v1+json; charset=utf-8",
  );
  const payload = await response.json();
  assert.equal(payload.name, "pillow");
  assert.match(
    fetchCalls[0].url,
    /^https:\/\/fgws3-ocloud\.ihep\.ac\.cn\/20830-uv-custom\/mirror\/pypi-cache\/simple\/json\/pillow\?/,
  );
  assert.equal(
    fetchCalls.some((request) => request.url === "https://pypi.org/simple/pillow/"),
    false,
  );
});

test("stores rewritten pypi simple json in s3 after a cache miss", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);

    if (/\/pypi-cache\/simple\/json\/pillow$/.test(new URL(request.url).pathname)) {
      if (request.method === "GET") {
        return new Response("missing", { status: 404 });
      }
      if (request.method === "PUT") {
        return new Response("", { status: 200 });
      }
    }

    if (request.url === "https://pypi.org/simple/pillow/") {
      return new Response(
        JSON.stringify({
          files: [
            {
              filename: "pillow-12.1.1.whl",
              url: "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl",
            },
          ],
          meta: { "api-version": "1.4" },
          name: "pillow",
        }),
        {
          status: 200,
          headers: {
            "content-type": "application/vnd.pypi.simple.v1+json",
          },
        },
      );
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const pending = [];
  const response = await worker.fetch(
    new Request("https://uv.agentsmirror.com/pypi/simple/pillow/", {
      headers: {
        accept: "application/vnd.pypi.simple.v1+json",
      },
    }),
    {
      ...ENV,
      PYPI_CACHE_PREFIX: "pypi-cache",
      S3_KEY_PREFIX: "mirror",
    },
    {
      waitUntil(promise) {
        pending.push(promise);
      },
    },
  );

  assert.equal(response.status, 200);
  await Promise.all(pending);

  const putRequest = fetchCalls.find((request) => request.method === "PUT");
  assert.ok(putRequest);
  assert.match(
    putRequest.url,
    /^https:\/\/fgws3-ocloud\.ihep\.ac\.cn\/20830-uv-custom\/mirror\/pypi-cache\/simple\/json\/pillow\?/,
  );
  assert.equal(
    await putRequest.text(),
    JSON.stringify({
      files: [
        {
          filename: "pillow-12.1.1.whl",
          url: "https://uv.agentsmirror.com/pypi/files/files.pythonhosted.org/packages/example/pillow-12.1.1.whl",
        },
      ],
      meta: { "api-version": "1.4" },
      name: "pillow",
    }),
  );
});

test("rewrites pypi simple html responses to self-hosted file urls", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);

    if (/\/pypi-cache\/simple\/html\/pillow$/.test(new URL(request.url).pathname)) {
      return new Response("missing", { status: 404 });
    }

    if (request.url === "https://pypi.org/simple/pillow/") {
      return new Response(
        '<!doctype html><html><body><a href="https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl#sha256=abc123" data-requires-python="&gt;=3.9">pillow-12.1.1.whl</a></body></html>',
        {
          status: 200,
          headers: {
            "content-type": "application/vnd.pypi.simple.v1+html; charset=UTF-8",
          },
        },
      );
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request("https://uv.agentsmirror.com/pypi/simple/pillow/", {
      headers: {
        accept: "application/vnd.pypi.simple.v1+html, text/html;q=0.9",
      },
    }),
    {
      ...ENV,
      PYPI_CACHE_PREFIX: "pypi-cache",
      PYPI_SIMPLE_UPSTREAM: "https://pypi.org/simple",
    },
  );

  assert.equal(response.status, 200);
  assert.equal(
    response.headers.get("content-type"),
    "application/vnd.pypi.simple.v1+html; charset=utf-8",
  );
  const body = await response.text();
  assert.match(
    body,
    /href="https:\/\/uv\.agentsmirror\.com\/pypi\/files\/files\.pythonhosted\.org\/packages\/example\/pillow-12\.1\.1\.whl#sha256=abc123"/,
  );
  assert.match(body, /data-requires-python="&gt;=3.9"/);
  assert.equal(fetchCalls[1].url, "https://pypi.org/simple/pillow/");
});

test("serves cached dist-info metadata from s3 before hitting upstream", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);

    if (
      /\/pypi-cache\/metadata\/files\.pythonhosted\.org\/packages\/example\/pillow-12\.1\.1\.whl\.metadata$/.test(
        new URL(request.url).pathname,
      )
    ) {
      return new Response("Metadata-Version: 2.4\nName: pillow\n", {
        status: 200,
      });
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/pypi/files/files.pythonhosted.org/packages/example/pillow-12.1.1.whl.metadata",
    ),
    {
      ...ENV,
      PYPI_CACHE_PREFIX: "pypi-cache",
      S3_KEY_PREFIX: "mirror",
    },
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "Metadata-Version: 2.4\nName: pillow\n");
  assert.match(
    fetchCalls[0].url,
    /^https:\/\/fgws3-ocloud\.ihep\.ac\.cn\/20830-uv-custom\/mirror\/pypi-cache\/metadata\/files\.pythonhosted\.org\/packages\/example\/pillow-12\.1\.1\.whl\.metadata\?/,
  );
  assert.equal(
    fetchCalls.some(
      (request) =>
        request.url ===
        "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl.metadata",
    ),
    false,
  );
});

test("stores dist-info metadata in s3 after a cache miss", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);

    if (
      /\/pypi-cache\/metadata\/files\.pythonhosted\.org\/packages\/example\/pillow-12\.1\.1\.whl\.metadata$/.test(
        new URL(request.url).pathname,
      )
    ) {
      if (request.method === "GET") {
        return new Response("missing", { status: 404 });
      }
      if (request.method === "PUT") {
        return new Response("", { status: 200 });
      }
    }

    if (
      request.url ===
      "https://files.pythonhosted.org/packages/example/pillow-12.1.1.whl.metadata"
    ) {
      return new Response("Metadata-Version: 2.4\nName: pillow\n", {
        status: 200,
      });
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const pending = [];
  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/pypi/files/files.pythonhosted.org/packages/example/pillow-12.1.1.whl.metadata",
    ),
    {
      ...ENV,
      PYPI_CACHE_PREFIX: "pypi-cache",
      S3_KEY_PREFIX: "mirror",
    },
    {
      waitUntil(promise) {
        pending.push(promise);
      },
    },
  );

  assert.equal(response.status, 200);
  await Promise.all(pending);

  const putRequest = fetchCalls.find((request) => request.method === "PUT");
  assert.ok(putRequest);
  assert.match(
    putRequest.url,
    /^https:\/\/fgws3-ocloud\.ihep\.ac\.cn\/20830-uv-custom\/mirror\/pypi-cache\/metadata\/files\.pythonhosted\.org\/packages\/example\/pillow-12\.1\.1\.whl\.metadata\?/,
  );
  assert.equal(await putRequest.text(), "Metadata-Version: 2.4\nName: pillow\n");
});

test("handles head requests for pypi simple json without reading a body", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);

    if (/\/pypi-cache\/simple\/json\/pillow$/.test(new URL(request.url).pathname)) {
      return new Response(null, { status: 404 });
    }

    if (request.url === "https://pypi.org/simple/pillow/" && request.method === "HEAD") {
      return new Response(null, {
        status: 200,
        headers: {
          "content-type": "application/vnd.pypi.simple.v1+json",
        },
      });
    }

    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request("https://uv.agentsmirror.com/pypi/simple/pillow/", {
      method: "HEAD",
      headers: {
        accept: "application/vnd.pypi.simple.v1+json",
      },
    }),
    {
      ...ENV,
      PYPI_CACHE_PREFIX: "pypi-cache",
      PYPI_SIMPLE_UPSTREAM: "https://pypi.org/simple",
    },
  );

  assert.equal(response.status, 200);
  assert.equal(
    response.headers.get("content-type"),
    "application/vnd.pypi.simple.v1+json; charset=utf-8",
  );
  assert.equal(await response.text(), "");
  assert.equal(fetchCalls[1].method, "HEAD");
});

const PBS_BASE =
  "https://uv.agentsmirror.com/python-build-standalone/releases/download/20260718";
const SANITIZED_KEY =
  "/20830-uv-custom/python-build-standalone/releases/download/20260718/cpython-3.12.13-plus-20260718-aarch64-apple-darwin-install_only_stripped.tar.gz";

async function signedPathnameFor(url) {
  const response = await worker.fetch(new Request(url), ENV);
  assert.equal(response.status, 307);
  return new URL(response.headers.get("location")).pathname;
}

test("resolves python assets whose filename keeps the upstream plus sign", async () => {
  // This is the URL uv builds when UV_PYTHON_INSTALL_MIRROR points at the mirror.
  const pathname = await signedPathnameFor(
    `${PBS_BASE}/cpython-3.12.13+20260718-aarch64-apple-darwin-install_only_stripped.tar.gz`,
  );

  assert.equal(pathname, SANITIZED_KEY);
});

test("resolves python assets whose plus sign is percent-encoded", async () => {
  const pathname = await signedPathnameFor(
    `${PBS_BASE}/cpython-3.12.13%2B20260718-aarch64-apple-darwin-install_only_stripped.tar.gz`,
  );

  assert.equal(pathname, SANITIZED_KEY);
});

test("leaves already sanitized python asset keys untouched", async () => {
  const pathname = await signedPathnameFor(
    `${PBS_BASE}/cpython-3.12.13-plus-20260718-aarch64-apple-darwin-install_only_stripped.tar.gz`,
  );

  assert.equal(pathname, SANITIZED_KEY);
});

test("sanitizes pypy and graalpy asset paths as well", async () => {
  assert.equal(
    await signedPathnameFor(
      "https://uv.agentsmirror.com/pypy/pypy3.11-v7.3.20+src-linux64.tar.bz2",
    ),
    "/20830-uv-custom/pypy/pypy3.11-v7.3.20-plus-src-linux64.tar.bz2",
  );

  assert.equal(
    await signedPathnameFor(
      "https://uv.agentsmirror.com/graalpython/releases/download/24.1/graalpy-24.1+1-linux.tar.gz",
    ),
    "/20830-uv-custom/graalpython/releases/download/24.1/graalpy-24.1-plus-1-linux.tar.gz",
  );
});

test("does not rewrite plus signs outside python asset paths", async () => {
  const pathname = await signedPathnameFor(
    "https://uv.agentsmirror.com/github/astral-sh/uv/releases/download/latest/uv+installer.sh.gz",
  );

  assert.equal(
    pathname,
    "/20830-uv-custom/github/astral-sh/uv/releases/download/latest/uv+installer.sh.gz",
  );
});

function makeUpstreamRelease(tag = "0.11.5") {
  return {
    url: `https://api.github.com/repos/astral-sh/uv/releases/100`,
    assets_url: `https://api.github.com/repos/astral-sh/uv/releases/100/assets`,
    html_url: `https://github.com/astral-sh/uv/releases/tag/${tag}`,
    tag_name: tag,
    name: tag,
    draft: false,
    prerelease: false,
    published_at: "2026-04-01T00:00:00Z",
    author: { login: "astral-bot" },
    reactions: { total_count: 3 },
    body: "Release notes",
    assets: [
      {
        name: "uv-installer.sh",
        size: 1234,
        content_type: "application/x-sh",
        url: "https://api.github.com/repos/astral-sh/uv/releases/assets/200",
        browser_download_url: `https://github.com/astral-sh/uv/releases/download/${tag}/uv-installer.sh`,
        uploader: { login: "astral-bot" },
      },
    ],
  };
}

test("serves synced github releases latest from s3 for both /api/v3 and /github/api/v3", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);
    if (
      /\/github\/api\/v3\/repos\/astral-sh\/uv\/releases\/latest\.json$/.test(
        new URL(request.url).pathname,
      )
    ) {
      return new Response(
        JSON.stringify({
          url: "https://uv.agentsmirror.com/api/v3/repos/astral-sh/uv/releases/100",
          tag_name: "0.13.8",
          name: "0.13.8",
          draft: false,
          prerelease: false,
          published_at: "2026-11-12T17:03:07Z",
          assets: [
            {
              name: "uv-installer.sh",
              size: 1000,
              content_type: "application/x-sh",
              url: "https://uv.agentsmirror.com/api/v3/repos/astral-sh/uv/releases/assets/1002",
              browser_download_url:
                "https://uv.agentsmirror.com/github/astral-sh/uv/releases/download/0.13.8/uv-installer.sh",
            },
          ],
        }),
        { status: 200 },
      );
    }
    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  for (const path of [
    "/api/v3/repos/astral-sh/uv/releases/latest",
    "/github/api/v3/repos/astral-sh/uv/releases/latest",
  ]) {
    const response = await worker.fetch(
      new Request(`https://uv.agentsmirror.com${path}`),
      { ...ENV, S3_KEY_PREFIX: "mirror2" },
    );
    assert.equal(response.status, 200);
    assert.equal(
      response.headers.get("content-type"),
      "application/json; charset=utf-8",
    );
    const payload = await response.json();
    assert.equal(payload.tag_name, "0.13.8");
    assert.equal(
      payload.assets[0].browser_download_url,
      "https://uv.agentsmirror.com/github/astral-sh/uv/releases/download/0.13.8/uv-installer.sh",
    );
  }
  assert.equal(fetchCalls.length, 2);
});

test("falls back to api.github.com on s3 miss, rewrites asset urls, and caches in s3", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);
    const pathname = new URL(request.url).pathname;

    if (
      /\/github\/api\/v3\/repos\/astral-sh\/uv\/releases\/tags\/0\.11\.5\.json$/.test(
        pathname,
      )
    ) {
      return new Response("missing", { status: 404 });
    }
    if (
      /\/github-api-cache\/repos\/astral-sh\/uv\/releases\/tags\/0\.11\.5\.json$/.test(
        pathname,
      )
    ) {
      if (request.method === "GET") {
        return new Response("missing", { status: 404 });
      }
      if (request.method === "PUT") {
        return new Response("", { status: 200 });
      }
    }
    if (
      request.url ===
      "https://api.github.com/repos/astral-sh/uv/releases/tags/0.11.5"
    ) {
      assert.equal(request.headers.get("user-agent"), "uv-custom-origin-proxy");
      return new Response(JSON.stringify(makeUpstreamRelease("0.11.5")), {
        status: 200,
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }
    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const pending = [];
  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/api/v3/repos/astral-sh/uv/releases/tags/0.11.5",
    ),
    { ...ENV, S3_KEY_PREFIX: "mirror2" },
    {
      waitUntil(promise) {
        pending.push(promise);
      },
    },
  );

  assert.equal(response.status, 200);
  await Promise.all(pending);

  const payload = await response.json();
  assert.equal(payload.tag_name, "0.11.5");
  assert.equal(payload.author, undefined);
  assert.equal(payload.reactions, undefined);
  assert.equal(payload.assets[0].uploader, undefined);
  assert.equal(
    payload.assets[0].browser_download_url,
    "https://uv.agentsmirror.com/github/astral-sh/uv/releases/download/0.11.5/uv-installer.sh",
  );
  assert.equal(
    payload.url,
    "https://uv.agentsmirror.com/api/v3/repos/astral-sh/uv/releases/100",
  );
  assert.equal(
    payload.assets[0].url,
    "https://uv.agentsmirror.com/api/v3/repos/astral-sh/uv/releases/assets/200",
  );

  const putRequest = fetchCalls.find((request) => request.method === "PUT");
  assert.ok(putRequest);
  assert.match(
    putRequest.url,
    /\/20830-uv-custom\/mirror2\/github-api-cache\/repos\/astral-sh\/uv\/releases\/tags\/0\.11\.5\.json\?/,
  );
});

test("supports HEAD requests for github releases api routes", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);
    const pathname = new URL(request.url).pathname;

    if (/\/github\/api\/v3\/repos\/astral-sh\/uv\/releases\.json$/.test(pathname)) {
      assert.equal(request.method, "HEAD");
      return new Response(null, { status: 200 });
    }
    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/github/api/v3/repos/astral-sh/uv/releases",
      { method: "HEAD" },
    ),
    ENV,
  );

  assert.equal(response.status, 200);
  assert.equal(
    response.headers.get("content-type"),
    "application/json; charset=utf-8",
  );
  assert.equal(await response.text(), "");
  assert.equal(fetchCalls.length, 1);
});

test("serves fresh cached github release tag from s3 without calling api.github.com", async (t) => {
  const originalFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    fetchCalls.push(request);
    const pathname = new URL(request.url).pathname;

    if (
      /\/github\/api\/v3\/repos\/astral-sh\/uv\/releases\/tags\/0\.11\.5\.json$/.test(
        pathname,
      )
    ) {
      return new Response("missing", { status: 404 });
    }
    if (
      /\/github-api-cache\/repos\/astral-sh\/uv\/releases\/tags\/0\.11\.5\.json$/.test(
        pathname,
      )
    ) {
      return new Response(
        JSON.stringify(makeUpstreamRelease("0.11.5")),
        {
          status: 200,
          headers: { "last-modified": new Date().toUTCString() },
        },
      );
    }
    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request(
      "https://uv.agentsmirror.com/github/api/v3/repos/astral-sh/uv/releases/tags/0.11.5",
    ),
    ENV,
  );

  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(
    payload.assets[0].browser_download_url,
    "https://uv.agentsmirror.com/github/astral-sh/uv/releases/download/0.11.5/uv-installer.sh",
  );
  assert.equal(
    payload.url,
    "https://uv.agentsmirror.com/github/api/v3/repos/astral-sh/uv/releases/100",
  );
  assert.equal(fetchCalls.length, 2);
});

test("falls back to stale s3 cache when upstream github api errors", async (t) => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const pathname = new URL(request.url).pathname;

    if (
      /\/github\/api\/v3\/repos\/astral-sh\/uv\/releases\.json$/.test(pathname)
    ) {
      return new Response("missing", { status: 404 });
    }
    if (
      /\/github-api-cache\/repos\/astral-sh\/uv\/releases\.json$/.test(pathname)
    ) {
      return new Response(
        JSON.stringify([makeUpstreamRelease("0.13.8")]),
        {
          status: 200,
          headers: { "last-modified": new Date(0).toUTCString() },
        },
      );
    }
    if (
      request.url ===
      "https://api.github.com/repos/astral-sh/uv/releases?per_page=30"
    ) {
      return new Response(JSON.stringify({ message: "rate limit" }), {
        status: 403,
      });
    }
    throw new Error(`unexpected fetch: ${request.url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await worker.fetch(
    new Request("https://uv.agentsmirror.com/api/v3/repos/astral-sh/uv/releases"),
    ENV,
  );

  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload[0].tag_name, "0.13.8");
  assert.equal(payload[0].body, undefined);
  assert.equal(
    payload[0].assets[0].browser_download_url,
    "https://uv.agentsmirror.com/github/astral-sh/uv/releases/download/0.13.8/uv-installer.sh",
  );
});

test("rejects invalid github release tags and pagination params", async () => {
  for (const badUrl of [
    "https://uv.agentsmirror.com/api/v3/repos/astral-sh/uv/releases/tags/..%2Ffoo",
    "https://uv.agentsmirror.com/github/api/v3/repos/astral-sh/uv/releases/tags/",
    "https://uv.agentsmirror.com/api/v3/repos/astral-sh/uv/releases?page=0",
  ]) {
    const response = await worker.fetch(new Request(badUrl), ENV);
    assert.equal(response.status, 400);
  }
});
