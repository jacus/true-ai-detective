import "server-only";

import type { NextRequest } from "next/server";
import { apiUrl, SESSION_COOKIE } from "@/lib/api";

/**
 * Relays a browser request to the same path on the api, carrying only our session cookie in and the
 * api's Set-Cookie back out, so the cookie lives on the web app's own domain.
 */
export async function relay(
  request: NextRequest,
  apiPath: string,
): Promise<Response> {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  const hasBody = !["GET", "DELETE"].includes(request.method);
  const upstream = await fetch(`${apiUrl}${apiPath}`, {
    method: request.method,
    headers: {
      "content-type": request.headers.get("content-type") ?? "application/json",
      ...(token ? { cookie: `${SESSION_COOKIE}=${token}` } : {}),
    },
    body: hasBody ? await request.text() : undefined,
    cache: "no-store",
  });

  const headers = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  for (const cookie of upstream.headers.getSetCookie()) {
    headers.append("set-cookie", cookie);
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}
