export const config = {
  runtime: "edge",
  regions: ["icn1"],
};

export default async function handler(request) {
  const url = new URL(request.url);
  const useFake = url.searchParams.get("fake") === "1";

  const apiKey = useFake ? "INVALID_TEST_KEY" : process.env.ITS_API_KEY;

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

  const target = `https://openapi.its.go.kr:9443/trafficInfo?${params.toString()}`;

  let response;

  try {
    response = await fetch(target, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(20000),
    });
  } catch (error) {
    return Response.json({
      ok: false,
      stage: "fetch",
      keyMode: useFake ? "fake" : "real",
      name: error?.name,
      message: error?.message,
    });
  }

  let bodyHead = null;
  try {
    const text = await response.text();
    bodyHead = text.slice(0, 500);
  } catch (error) {
    bodyHead = `READ_ERROR: ${error?.name} ${error?.message}`;
  }

  return Response.json({
    ok: true,
    stage: "done",
    keyMode: useFake ? "fake" : "real",
    httpStatus: response.status,
    contentType: response.headers.get("content-type"),
    contentLength: response.headers.get("content-length"),
    bodyHead,
  });
}
