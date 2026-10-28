import { notFound } from "next/navigation";
import { Suspense } from "react";
import { requireUser } from "@/lib/api";
import styles from "../forms.module.css";
import { InviteClientForm } from "./invite-client-form";

export default function OwnerPage() {
  return (
    <main className={styles.page}>
      <Suspense fallback={null}>
        <Owner />
      </Suspense>
    </main>
  );
}

async function Owner() {
  const user = await requireUser();
  if (user.role !== "owner") notFound();
  return <InviteClientForm />;
}
