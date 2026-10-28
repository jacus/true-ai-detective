"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import styles from "../../forms.module.css";

type Props = { token: string; name: string; email: string };

export function SetPasswordForm({ token, name, email }: Props) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password"));
    if (password !== form.get("confirm")) {
      setError("The passwords don't match.");
      return;
    }
    const response = await fetch("/api/auth/invites/redeem", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token, password }),
    });
    if (response.ok) {
      router.replace("/");
      router.refresh();
    } else if (response.status === 422) {
      setError("Use at least 12 characters.");
    } else {
      setError("This invite link is invalid or has expired.");
    }
  }

  return (
    <form className={styles.card} onSubmit={onSubmit}>
      <h1>Welcome, {name}</h1>
      <p className={styles.muted}>
        Choose a password for {email}. You'll use it to log in.
      </p>
      <label className={styles.field}>
        Password (12+ characters)
        <input
          name="password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          required
        />
      </label>
      <label className={styles.field}>
        Repeat password
        <input
          name="confirm"
          type="password"
          autoComplete="new-password"
          minLength={12}
          required
        />
      </label>
      <button className={styles.button} type="submit">
        Start
      </button>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
