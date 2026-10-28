import "server-only";

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

/** The logged-in user according to the api, or null. Data Access Layer per Next.js auth guide. */
export async function getUser(): Promise<User | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return null;
  const response = await fetch(`${apiUrl}/auth/me`, {
    headers: { cookie: `${SESSION_COOKIE}=${token}` },
    cache: "no-store",
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(`api /auth/me failed: ${response.status}`);
  return response.json();
}

export async function requireUser(): Promise<User> {
  const user = await getUser();
  if (!user) redirect("/login");
  return user;
}
