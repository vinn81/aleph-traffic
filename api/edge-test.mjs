export const config = {
  runtime: "edge",
  regions: ["icn1"],
};

export default async function handler() {
  const apiKey = process.env.ITS_API_KEY;

  if (!apiKey) {
    return new Response(
      JSON.stringify({
        ok: false,
        message: "ITS_API_KEY가 설정되지 않았습니다.",
      }),
      {
        status: 200,
        headers: {
          "content-type": "application/json; charset=utf-8",
        },
      }
    );
  }

  const params = new URLSearchParams({
    apiKey,
    type: "all",
    drcType: "all",
    minX: "128.40",
    maxX: "128.80",
    minY: "35.75",
    maxY: "36.00",
    getType: "json",
  });

  const url =
    `https://openapi.its.go.kr:9443/trafficInfo?${params.toString()}`;

  try {
    const started = Date.now();

    const response = await fetch(url, {
      headers: {
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(20000),
    });

    const text = await response.text();

    return new Response(
      JSON.stringify({
        ok: response.ok,
        httpStatus: response.status,
        elapsedMs: Date.now() - started,
        responseLength: text.length,
        preview: text.slice(0, 1500),
      }),
      {
        status: 200,
        headers: {
          "content-type": "application/json; charset=utf-8",
        },
      }
    );
  } catch (error) {
    return new Response(
      JSON.stringify({
        ok: false,
        name: error?.name,
        message: error?.message,
      }),
      {
        status: 200,
        headers: {
          "content-type": "application/json; charset=utf-8",
        },
      }
    );
  }
}
