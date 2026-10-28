"use client";

import { CopilotChat, CopilotKitProvider } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";

export function DetectiveChat() {
  return (
    <CopilotKitProvider runtimeUrl="/api/copilotkit" useSingleEndpoint={false}>
      <CopilotChat
        agentId="detective"
        labels={{
          welcomeMessageText: "True AI Detective. Tell me about your case.",
          chatInputPlaceholder: "Describe what happened…",
        }}
      />
    </CopilotKitProvider>
  );
}
