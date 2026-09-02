export const config = {
  runtime: "edge",
  regions: ["icn1"],
};

export default async function handler() {
  const apiKey = INVALID_TEST_KEY;

  if (!apiKey) {
    return Response.json({
      ok: false,
      stage: "env",
      message: "ITS_API_KEY가 없습니다.",
    });
  }

  const params = new URLSearchParams({
    apiKey,
    type: "all",
    drcType: "all",
    minX: "128.55",
    maxX: "128.60",
    minY: "35.84",
    maxY: "35.88",
    getType: "json",
  });

  const url =
    `https://openapi.its.go.kr:9443/trafficInfo?${params.toString()}`;

  let response;

  try {
    response = await fetch(url, {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(20000),
    });
  } catch (error) {
    return Response.json({
      ok: false,
      stage: "fetch",
      name: error?.name,
      message: error?.message,
    });
  }

  // 본문은 읽지 않고 바로 취소
  try {
    await response.body?.cancel();
  } catch (_) {}

  return Response.json({
    ok: true,
    stage: "headers",
    httpStatus: response.status,
    contentType: response.headers.get("content-type"),
    contentLength: response.headers.get("content-length"),
    transferEncoding: response.headers.get("transfer-encoding"),
  });
}
