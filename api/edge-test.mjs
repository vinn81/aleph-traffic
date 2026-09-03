export const config = {
  runtime: "edge",
  regions: ["icn1"],
};

const BASE = "https://openapi.its.go.kr:9443/trafficInfo";

const KEYLESS_PARAMS = new URLSearchParams({
  type: "all",
  drcType: "all",
  minX: "128.55",
  maxX: "128.60",
  minY: "35.84",
  maxY: "35.88",
  getType: "json",
}).toString();

async function attempt(label, url, init) {
  const started = Date.now();
  try {
    const res = await fetch(url, init);
    let head = null;
    try {
      head = (await res.text()).slice(0, 200);
    } catch (e) {
      head = `READ_ERROR: ${e?.name}`;
    }
    return {
      label,
      ok: true,
      status: res.status,
      ms: Date.now() - started,
      head,
    };
  } catch (error) {
    return {
      label,
      ok: false,
      ms: Date.now() - started,
      name: error?.name,
      message: error?.message,
    };
  }
}

export default async function handler() {
  const apiKey = process.env.ITS_API_KEY ?? "";
  const results = [];

  const plan = [
    // 대조군: Edge fetch 자체가 살아있는지
    ["control-example", "https://example.com", undefined],

    // A. 알려진 성공 케이스 그대로 (파라미터/헤더/시그널 전부 없음)
    ["A-bare", BASE, undefined],

    // B. A + Accept 헤더
    ["B-accept", BASE, { headers: { Accept: "application/json" } }],

    // C. A + AbortSignal
    ["C-signal", BASE, { signal: AbortSignal.timeout(6000) }],

    // D. A + 쿼리스트링 (키 없음)
    ["D-params", `${BASE}?${KEYLESS_PARAMS}`, undefined],

    // E. 443 포트로 같은 요청
    ["E-port443", "https://openapi.its.go.kr/trafficInfo", undefined],
  ];

  const deadline = Date.now() + 20000;

  for (const [label, url, init] of plan) {
    if (Date.now() > deadline) {
      results.push({ label, skipped: "time budget exceeded" });
      continue;
    }
    results.push(await attempt(label, url, init));
  }

  return Response.json({
    apiKeyPresent: apiKey.length > 0,
    results,
  });
}
