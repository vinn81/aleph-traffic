export const config = {
  runtime: "edge",
  regions: ["icn1"],
};

export default async function handler() {
  const url =
    "https://openapi.its.go.kr:9443/trafficInfo";

  try {
    const response = await fetch(url, {
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
      },
      signal: AbortSignal.timeout(8000),
    });

    const text = await response.text();

    return new Response(
      JSON.stringify({
        ok: true,
        status: response.status,
        statusText: response.statusText,
        preview: text.slice(0, 500),
      }),
      {
        status: 200,
        headers: {
          "content-type":
            "application/json; charset=utf-8",
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
          "content-type":
            "application/json; charset=utf-8",
        },
      }
    );
  }
}
