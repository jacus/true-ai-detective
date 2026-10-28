import { HttpAgent } from "@ag-ui/client";
import {
  CopilotRuntime,
  createCopilotRuntimeHandler,
  InMemoryAgentRunner,
} from "@copilotkit/runtime/v2";

// The detective lives in the Python api service; this route relays AG-UI between the browser and it.
// Set in web/.env (local default) or by Railway / the e2e tests.
const agentUrl = process.env.DETECTIVE_AGENT_URL;
if (!agentUrl) throw new Error("DETECTIVE_AGENT_URL is not set");

const runtime = new CopilotRuntime({
  agents: {
    detective: new HttpAgent({ url: agentUrl }),
  },
  runner: new InMemoryAgentRunner(),
});

const handler = createCopilotRuntimeHandler({
  runtime,
  basePath: "/api/copilotkit",
});

export const GET = handler;
export const POST = handler;
export const PATCH = handler;
export const DELETE = handler;
