import { HttpAgent } from "@ag-ui/client";
import {
  CopilotRuntime,
  createCopilotRuntimeHandler,
  InMemoryAgentRunner,
} from "@copilotkit/runtime/v2";
import { apiUrl } from "@/lib/api";

// The detective lives in the Python api service; this route relays AG-UI between the browser and it.
const runtime = new CopilotRuntime({
  agents: {
    detective: new HttpAgent({ url: `${apiUrl}/agent` }),
  },
  runner: new InMemoryAgentRunner(),
  // The api authenticates every run itself, so pass the visitor's session cookie through.
  forwardHeaders: { allow: ["cookie"] },
});

const handler = createCopilotRuntimeHandler({
  runtime,
  basePath: "/api/copilotkit",
});

export const GET = handler;
export const POST = handler;
export const PATCH = handler;
export const DELETE = handler;
