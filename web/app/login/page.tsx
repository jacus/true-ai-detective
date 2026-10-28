"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import styles from "../forms.module.css";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        email: form.get("email"),
        password: form.get("password"),
      }),
    });
    if (response.ok) {
      router.replace("/");
      router.refresh();
    } else {
      setError("Wrong email or password.");
    }
  }

  return (
    <main className={styles.page}>
      <form className={styles.card} onSubmit={onSubmit}>
        <h1>True AI Detective</h1>
        <label className={styles.field}>
          Email
          <input name="email" type="email" autoComplete="username" required />
        </label>
        <label className={styles.field}>
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        <button className={styles.button} type="submit">
          Log in
        </button>
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
      </form>
    </main>
  );
}
