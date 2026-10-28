"use client";

import { type FormEvent, useState } from "react";
import styles from "../forms.module.css";

export function InviteClientForm() {
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLink(null);
    setError(null);
    const form = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/invites", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        email: form.get("email"),
        name: form.get("name"),
      }),
    });
    if (response.ok) {
      const { invite_path } = await response.json();
      setLink(`${window.location.origin}${invite_path}`);
    } else if (response.status === 409) {
      setError("A user with this email already exists.");
    } else {
      setError("Could not create the invite.");
    }
  }

  return (
    <form className={styles.card} onSubmit={onSubmit}>
      <h1>Invite a client</h1>
      <label className={styles.field}>
        Client's name
        <input name="name" required />
      </label>
      <label className={styles.field}>
        Client's email
        <input name="email" type="email" required />
      </label>
      <button className={styles.button} type="submit">
        Create invite link
      </button>
      {link && (
        <label className={styles.field}>
          Send this one-time link to the client (valid 7 days):
          <input
            readOnly
            value={link}
            data-testid="invite-link"
            onFocus={(e) => e.currentTarget.select()}
          />
        </label>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <a href="/">Back to the chat</a>
    </form>
  );
}
