import type { NextRequest } from "next/server";
import { relay } from "@/lib/relay";

// /api/auth/* → api /auth/* (login, logout, invites, me)
async function handler(
  request: NextRequest,
  ctx: RouteContext<"/api/auth/[...path]">,
): Promise<Response> {
  const { path } = await ctx.params;
  return relay(request, `/auth/${path.join("/")}`);
}

export const GET = handler;
export const POST = handler;
