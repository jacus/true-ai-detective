import type { NextRequest } from "next/server";
import { relay } from "@/lib/relay";

// /api/cases/* → api /cases/* (current case, its messages, reset, evidence locker)
async function handler(
  request: NextRequest,
  ctx: RouteContext<"/api/cases/[...path]">,
): Promise<Response> {
  const { path } = await ctx.params;
  return relay(request, `/cases/${path.join("/")}`);
}

export const GET = handler;
export const POST = handler;
export const DELETE = handler;
