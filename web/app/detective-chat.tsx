"use client";

import { CopilotChat, CopilotKitProvider } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import { useRouter } from "next/navigation";
import type { User } from "@/lib/api";

export function DetectiveChat({ user }: { user: User }) {
  const router = useRouter();

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <header
        style={{
          display: "flex",
          gap: "1rem",
          alignItems: "center",
          padding: "0.5rem 1rem",
          fontFamily: "system-ui, sans-serif",
          fontSize: "0.9rem",
          borderBottom: "1px solid #eee",
        }}
      >
        <span style={{ flex: 1 }}>Signed in as {user.name}</span>
        {user.role === "owner" && <a href="/owner">Invite a client</a>}
        <button type="button" onClick={logout}>
          Log out
        </button>
      </header>
      <div style={{ flex: 1, minHeight: 0 }}>
        <CopilotKitProvider
          runtimeUrl="/api/copilotkit"
          useSingleEndpoint={false}
        >
          <CopilotChat
            agentId="detective"
            labels={{
              welcomeMessageText: "True AI Detective. Tell me about your case.",
              chatInputPlaceholder: "Describe what happened…",
            }}
          />
        </CopilotKitProvider>
      </div>
    </div>
  );
}
