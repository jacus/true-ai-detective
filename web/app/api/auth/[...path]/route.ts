import type { NextRequest } from "next/server";
import { apiUrl, SESSION_COOKIE } from "@/lib/api";

// Relays /api/auth/* from the browser to the api's /auth/*, carrying only our session cookie
// in and the api's Set-Cookie back out, so the cookie lives on the web app's own domain.
async function relay(
  request: NextRequest,
  ctx: RouteContext<"/api/auth/[...path]">,
): Promise<Response> {
  const { path } = await ctx.params;
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  const upstream = await fetch(`${apiUrl}/auth/${path.join("/")}`, {
    method: request.method,
    headers: {
      "content-type": request.headers.get("content-type") ?? "application/json",
      ...(token ? { cookie: `${SESSION_COOKIE}=${token}` } : {}),
    },
    body: request.method === "GET" ? undefined : await request.text(),
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

export const GET = relay;
export const POST = relay;
