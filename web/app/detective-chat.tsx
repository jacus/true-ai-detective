"use client";

import type { Message } from "@ag-ui/client";
import {
  CopilotChat,
  CopilotKitProvider,
  useAgent,
} from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { Evidence, User } from "@/lib/api";
import styles from "./evidence.module.css";
import { EvidenceLocker } from "./evidence-locker";

type Props = {
  user: User;
  caseId: string;
  initialMessages: Message[];
  initialEvidence: Evidence[];
};

export function DetectiveChat({
  user,
  caseId,
  initialMessages,
  initialEvidence,
}: Props) {
  const router = useRouter();

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  async function resetCase() {
    const sure = window.confirm(
      "Delete this case, its evidence and its whole conversation for good? This can't be undone.",
    );
    if (!sure) return;
    await fetch("/api/cases/current", { method: "DELETE" });
    router.refresh(); // re-renders with the fresh case
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
        <button type="button" onClick={resetCase}>
          Reset case
        </button>
        <button type="button" onClick={logout}>
          Log out
        </button>
      </header>
      <div className={styles.casePage}>
        <div style={{ flex: 1, minHeight: 0 }}>
          {/* Keyed by case: a reset mounts a completely fresh chat and locker. */}
          <CopilotKitProvider
            key={caseId}
            runtimeUrl="/api/copilotkit"
            useSingleEndpoint={false}
          >
            <CaseChat caseId={caseId} initialMessages={initialMessages} />
          </CopilotKitProvider>
        </div>
        <EvidenceLocker key={caseId} initialEvidence={initialEvidence} />
      </div>
    </div>
  );
}

function CaseChat({
  caseId,
  initialMessages,
}: {
  caseId: string;
  initialMessages: Message[];
}) {
  // The same shared agent CopilotChat uses; its thread comes from the chat (threadId below).
  const { agent, isReady } = useAgent({ agentId: "detective" });

  // Show the saved conversation as soon as the chat is connected (it lives in the api, not the browser).
  useEffect(() => {
    if (isReady && agent.messages.length === 0 && initialMessages.length > 0) {
      agent.setMessages(initialMessages);
    }
  }, [isReady, agent, initialMessages]);

  return (
    <CopilotChat
      agentId="detective"
      threadId={caseId}
      labels={{
        welcomeMessageText: "True AI Detective. Tell me about your case.",
        chatInputPlaceholder: "Describe what happened…",
      }}
    />
  );
}
