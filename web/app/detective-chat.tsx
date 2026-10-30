"use client";

import type { Message } from "@ag-ui/client";
import {
  CopilotChat,
  CopilotChatConfigurationProvider,
  CopilotKitProvider,
  UseAgentUpdate,
  useAgent,
  useCopilotKit,
} from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { Board, Evidence, Todo, User } from "@/lib/api";
import { CaseBoard, type LastStep } from "./case-board";
import styles from "./evidence.module.css";
import { EvidenceLocker } from "./evidence-locker";

type Props = {
  user: User;
  caseId: string;
  initialMessages: Message[];
  initialEvidence: Evidence[];
  initialBoard: Board;
};

export function DetectiveChat({
  user,
  caseId,
  initialMessages,
  initialEvidence,
  initialBoard,
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
      {/* Keyed by case: a reset mounts a completely fresh board, chat and locker. */}
      <CopilotKitProvider
        key={caseId}
        runtimeUrl="/api/copilotkit"
        useSingleEndpoint={false}
      >
        {/* One chat configuration for the whole case view: every useAgent below (board, chat, refresh)
            then shares the agent on the case's thread, so a refresh run's state reaches the board. */}
        <CopilotChatConfigurationProvider agentId="detective" threadId={caseId}>
          <CaseView
            caseId={caseId}
            initialMessages={initialMessages}
            initialEvidence={initialEvidence}
            initialBoard={initialBoard}
          />
        </CopilotChatConfigurationProvider>
      </CopilotKitProvider>
    </div>
  );
}

function CaseView({
  caseId,
  initialMessages,
  initialEvidence,
  initialBoard,
}: Omit<Props, "user">) {
  // CopilotKit shared state: re-render on every STATE_SNAPSHOT the detective graph streams. One hook owns
  // the agent, so the board we show and the refresh we start are always the same agent object.
  const { agent } = useAgent({
    agentId: "detective",
    updates: [UseAgentUpdate.OnStateChanged, UseAgentUpdate.OnRunStatusChanged],
  });
  const { copilotkit } = useCopilotKit();
  // Until the first run on this page, the board is the one saved with the case.
  const board: Board = agent.state?.board ?? initialBoard;
  // The research agent's plan (S13c), as it writes it: a CUSTOM event each time. Kept apart from the agent's
  // state, which the detective's own snapshots replace mid-research (its `plan` only fills in when research
  // ends); a new run starts without one.
  const [livePlan, setLivePlan] = useState<Todo[] | null>(null);
  useEffect(() => {
    const { unsubscribe } = agent.subscribe({
      onRunStartedEvent: () => setLivePlan(null),
      onCustomEvent: ({ event }) => {
        if (event.name === "research_plan") setLivePlan(event.value);
      },
    });
    return unsubscribe;
  }, [agent]);
  const plan: Todo[] = livePlan ?? agent.state?.plan ?? [];
  // The loop's trail for the latest message (S12): e.g. research → conclude, with the decision's probability.
  const decided = agent.state?.decision;
  const lastStep: LastStep | null = agent.state?.steps
    ? {
        steps: agent.state.steps,
        probability: decided?.probabilities?.[decided.action],
      }
    : null;

  // CopilotKit can swap the agent object (e.g. once the runtime connects) while an upload is in flight;
  // the refresh then has to use the agent of *now*, not the one captured when the upload started.
  const latestAgent = useRef(agent);
  useEffect(() => {
    latestAgent.current = agent;
  }, [agent]);

  // A run that researched may have filed findings (S13): when it ends, the locker lists itself again.
  const [lockerVersion, setLockerVersion] = useState(0);
  const wasRunning = useRef(false);
  const researched = agent.state?.steps?.includes("research") ?? false;
  useEffect(() => {
    if (wasRunning.current && !agent.isRunning && researched)
      setLockerVersion((version) => version + 1);
    wasRunning.current = agent.isRunning;
  }, [agent.isRunning, researched]);

  // New evidence: a run without a new message, so the graph only rebuilds the board (and streams it here).
  async function refreshBoard() {
    const current = latestAgent.current;
    if (!current.isRunning) await copilotkit.runAgent({ agent: current });
  }

  return (
    <div className={styles.casePage}>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <CaseBoard board={board} lastStep={lastStep} plan={plan} />
        <div style={{ flex: 1, minHeight: 0 }}>
          <CaseChat caseId={caseId} initialMessages={initialMessages} />
        </div>
      </div>
      <EvidenceLocker
        initialEvidence={initialEvidence}
        version={lockerVersion}
        onAdded={refreshBoard}
      />
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
