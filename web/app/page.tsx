import { Suspense } from "react";
import { DetectiveChat } from "./detective-chat";

export default function Home() {
  return (
    <main style={{ height: "100dvh" }}>
      {/* The chat is per-visitor and reads the clock, so it renders on request, not at build time. */}
      <Suspense fallback={null}>
        <DetectiveChat />
      </Suspense>
    </main>
  );
}
