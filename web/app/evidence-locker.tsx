"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import type { Evidence } from "@/lib/api";
import styles from "./evidence.module.css";
import type { LatLng } from "./pin-map";

// Leaflet needs the browser's window, so the map renders on the client only.
const PinMap = dynamic(() => import("./pin-map"), {
  ssr: false,
  loading: () => null,
});

const EVIDENCE = "/api/cases/current/evidence";
// Shown as pictures; must match the api's INLINE_TYPES (everything else downloads).
const PHOTO_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];

/** Files, photos, links and map pins on the case. Each form posts to the api and appends what it returns. */
export function EvidenceLocker({
  initialEvidence,
}: {
  initialEvidence: Evidence[];
}) {
  const [evidence, setEvidence] = useState(initialEvidence);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<LatLng | null>(null);

  async function add(request: Promise<Response>): Promise<void> {
    const response = await request;
    if (!response.ok) {
      setError(
        response.status === 413
          ? "That file is too big (10 MB max)."
          : "Couldn't add that. Check it and try again.",
      );
      return;
    }
    setError(null);
    const added: Evidence = await response.json();
    setEvidence((current) => [...current, added]);
  }

  // React form actions: the form resets itself after each one.
  async function uploadFile(form: FormData) {
    await add(fetch(`${EVIDENCE}/files`, { method: "POST", body: form }));
  }

  async function addLink(form: FormData) {
    await add(postJson("links", { url: form.get("url") }));
  }

  async function addPin(form: FormData) {
    if (!draft) return;
    const [latitude, longitude] = draft;
    await add(
      postJson("pins", { label: form.get("label"), latitude, longitude }),
    );
    setDraft(null);
  }

  return (
    <aside className={styles.locker} aria-label="Evidence locker">
      <h2>Evidence</h2>
      {error && <p className={styles.error}>{error}</p>}

      <ul className={styles.items}>
        {evidence.map((item) => (
          <li key={item.id} data-testid="evidence-item">
            <EvidenceItem item={item} />
          </li>
        ))}
        {evidence.length === 0 && (
          <li className={styles.hint}>
            Nothing yet. Add photos, files, links or places.
          </li>
        )}
      </ul>

      <form action={uploadFile} className={styles.row}>
        <input type="file" name="file" aria-label="File or photo" required />
        <button type="submit">Upload</button>
      </form>

      <form action={addLink} className={styles.row}>
        <input
          type="url"
          name="url"
          aria-label="Link"
          placeholder="https://…"
          required
        />
        <button type="submit">Add link</button>
      </form>

      <div className={styles.map} data-testid="pin-map">
        <PinMap
          pins={evidence.filter((item) => item.kind === "pin")}
          draft={draft}
          onPick={setDraft}
        />
      </div>
      <form action={addPin} className={styles.row}>
        <input
          name="label"
          aria-label="Pin label"
          placeholder={
            draft ? "What happened here?" : "Click the map to drop a pin"
          }
          required
        />
        <button type="submit" disabled={!draft}>
          Add pin
        </button>
      </form>
    </aside>
  );
}

function postJson(kind: string, body: object): Promise<Response> {
  return fetch(`${EVIDENCE}/${kind}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

function EvidenceItem({ item }: { item: Evidence }) {
  // The api answers with a redirect to a presigned URL that works for a few minutes.
  const file = `${EVIDENCE}/${item.id}/file`;
  switch (item.kind) {
    case "file":
      if (PHOTO_TYPES.includes(item.content_type ?? "")) {
        return (
          <a href={file} target="_blank" rel="noopener noreferrer">
            {/* biome-ignore lint/performance/noImgElement: presigned bucket URLs, not optimizable assets */}
            <img src={file} alt={item.label} className={styles.photo} />
            <span>{item.label}</span>
          </a>
        );
      }
      return (
        <a href={file}>
          📄 {item.label} ({formatSize(item.size_bytes ?? 0)})
        </a>
      );
    case "link":
      return (
        <a href={item.url ?? ""} target="_blank" rel="noopener noreferrer">
          🔗 {item.label}
        </a>
      );
    case "pin":
      return (
        <span>
          📍 {item.label} ({item.latitude?.toFixed(4)},{" "}
          {item.longitude?.toFixed(4)})
        </span>
      );
  }
}

function formatSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.ceil(bytes / 1024)} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
