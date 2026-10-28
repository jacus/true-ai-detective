import { Suspense } from "react";
import { apiUrl } from "@/lib/api";
import styles from "../../forms.module.css";
import { SetPasswordForm } from "./set-password-form";

export default function InvitePage(props: PageProps<"/invite/[token]">) {
  return (
    <main className={styles.page}>
      <Suspense fallback={null}>
        <Invite params={props.params} />
      </Suspense>
    </main>
  );
}

async function Invite({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const response = await fetch(
    `${apiUrl}/auth/invites/${encodeURIComponent(token)}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    return (
      <div className={styles.card}>
        <h1>Invite</h1>
        <p className={styles.error} role="alert">
          This invite link is invalid or has expired.
        </p>
      </div>
    );
  }
  const invite: { email: string; name: string } = await response.json();
  return (
    <SetPasswordForm token={token} name={invite.name} email={invite.email} />
  );
}
