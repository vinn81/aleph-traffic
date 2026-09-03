export const config = {
  runtime: "edge",
  regions: ["icn1"],
};

const BASE = "https://openapi.its.go.kr:9443/trafficInfo";

async function attempt(label, url) {
  const started = Date.now();
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3500) });
    let head = null;
    try {
      head = (await res.text()).slice(0, 200);
    } catch (e) {
      head = `READ_ERROR: ${e?.name}`;
    }
    return { label, ok: true, status: res.status, ms: Date.now() - started, head };
  } catch (error) {
    return {
      label,
      ok: false,
      ms: Date.now() - started,
      name: error?.name,
      message: error?.message,
      cause: String(error?.cause ?? ""),
    };
  }
}

export default async function handler() {
  const runtime = {
    hasEdgeRuntimeGlobal: typeof EdgeRuntime !== "undefined",
    edgeRuntimeValue:
      typeof EdgeRuntime !== "undefined" ? String(EdgeRuntime) : null,
    hasProcess: typeof process !== "undefined",
    nodeVersion:
      typeof process !== "undefined" ? process.version ?? null : null,
    vercelRegion:
      typeof process !== "undefined"
        ? process.env?.VERCEL_REGION ?? null
        : null,
  };

  const results = [];
  for (const [label, url] of [
    ["control-example", "https://example.com"],
    ["A-bare-9443", BASE],
    ["E-port443", "https://openapi.its.go.kr/trafficInfo"],
  ]) {
    results.push(await attempt(label, url));
  }

  return Response.json({ runtime, results });
}
