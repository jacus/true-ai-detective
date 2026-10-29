import { Suspense } from "react";
import {
  getCaseBoard,
  getCaseEvidence,
  getCaseMessages,
  getCurrentCase,
  requireUser,
} from "@/lib/api";
import { DetectiveChat } from "./detective-chat";

export default function Home() {
  return (
    <main style={{ height: "100dvh" }}>
      {/* Reading the session is per request, so it streams in behind Suspense (Cache Components). */}
      <Suspense fallback={null}>
        <Chat />
      </Suspense>
    </main>
  );
}

async function Chat() {
  const user = await requireUser();
  const currentCase = await getCurrentCase();
  const [messages, evidence, board] = await Promise.all([
    getCaseMessages(),
    getCaseEvidence(),
    getCaseBoard(),
  ]);
  return (
    <DetectiveChat
      user={user}
      caseId={currentCase.id}
      initialMessages={messages}
      initialEvidence={evidence}
      initialBoard={board}
    />
  );
}
