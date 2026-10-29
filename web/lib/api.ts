import "server-only";

import type { Message } from "@ag-ui/client";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

// The Python api owns identity. Set in web/.env (local) or by Railway / the e2e tests.
const apiUrlFromEnv = process.env.DETECTIVE_API_URL;
if (!apiUrlFromEnv) throw new Error("DETECTIVE_API_URL is not set");
export const apiUrl: string = apiUrlFromEnv;

export const SESSION_COOKIE = "detective_session";

export type User = {
  id: string;
  email: string;
  name: string;
  role: "owner" | "client";
};

export type Case = { id: string; created_at: string };

async function fromApi(path: string): Promise<Response | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return null;
  return fetch(`${apiUrl}${path}`, {
    headers: { cookie: `${SESSION_COOKIE}=${token}` },
    cache: "no-store",
  });
}

/** The logged-in user according to the api, or null. Data Access Layer per Next.js auth guide. */
export async function getUser(): Promise<User | null> {
  const response = await fromApi("/auth/me");
  if (!response) return null;
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(`api /auth/me failed: ${response.status}`);
  return response.json();
}

export async function requireUser(): Promise<User> {
  const user = await getUser();
  if (!user) redirect("/login");
  return user;
}

/** The logged-in user's case (one per client for now); its id is the chat thread id. */
export async function getCurrentCase(): Promise<Case> {
  const response = await fromApi("/cases/current");
  if (!response || response.status === 401) redirect("/login");
  if (!response.ok)
    throw new Error(`api /cases/current failed: ${response.status}`);
  return response.json();
}

/** The case's conversation so far (from the api's checkpoint), as AG-UI messages. */
export async function getCaseMessages(): Promise<Message[]> {
  const response = await fromApi("/cases/current/messages");
  if (!response || response.status === 401) redirect("/login");
  if (!response.ok)
    throw new Error(`api /cases/current/messages failed: ${response.status}`);
  return response.json();
}

export type Evidence = {
  id: string;
  kind: "file" | "link" | "pin";
  label: string;
  content_type: string | null;
  size_bytes: number | null;
  url: string | null;
  latitude: number | null;
  longitude: number | null;
  created_at: string;
  /** What the api learned from it (photos: EXIF metadata and what the vision model saw). */
  facts: Fact[];
};

/** Facts read in text carry their citation: the exact quote and where it is (e.g. "page 3"). */
export type Fact = {
  kind: string;
  statement: string;
  quote: string | null;
  location: string | null;
};

/** Files, links and map pins attached to the case (file bytes are fetched separately, via a presigned URL). */
export async function getCaseEvidence(): Promise<Evidence[]> {
  const response = await fromApi("/cases/current/evidence");
  if (!response || response.status === 401) redirect("/login");
  if (!response.ok)
    throw new Error(`api /cases/current/evidence failed: ${response.status}`);
  return response.json();
}
