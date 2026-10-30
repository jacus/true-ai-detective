import type { Board, Todo } from "@/lib/api";
import styles from "./board.module.css";

/** The detective's steps for the latest message, and how sure the decision model was of the last one. */
export type LastStep = {
  steps: string[];
  probability: number | undefined;
};

const MARKS: Record<Todo["status"], string> = {
  pending: "○",
  in_progress: "▶",
  completed: "✓",
};

/** The case board: leads, hypotheses, a timeline and contradictions (the detective graph's `board` state),
 * with the detective's last step and the research agent's plan for the latest message. */
export function CaseBoard({
  board,
  lastStep,
  plan,
}: {
  board: Board;
  lastStep: LastStep | null;
  plan: Todo[];
}) {
  return (
    <section className={styles.board} aria-label="Case board">
      {lastStep && lastStep.steps.length > 0 && (
        // <output> is a status live region: assistive tech announces each new step as it streams in.
        <output className={styles.lastStep} aria-label="Detective's last step">
          Detective's last step: {lastStep.steps.join(" → ")}
          {lastStep.probability !== undefined &&
            ` (${Math.round(lastStep.probability * 100)}%)`}
        </output>
      )}
      {plan.length > 0 && (
        <ol className={styles.plan} aria-label="Research plan">
          {plan.map((todo) => (
            <li key={todo.content} data-status={todo.status}>
              {MARKS[todo.status]} {todo.content}
            </li>
          ))}
        </ol>
      )}
      <Column title="Leads" empty="No leads yet.">
        {board.leads.map((lead) => (
          <li key={lead.what}>
            {lead.what} <small>{lead.why}</small>
          </li>
        ))}
      </Column>
      <Column title="Hypotheses" empty="No hypotheses yet.">
        {board.hypotheses.map((h) => (
          <li key={h.claim} title={h.sources.join("\n")}>
            {h.claim} <small>{h.confidence}</small>
          </li>
        ))}
      </Column>
      <Column title="Timeline" empty="Nothing dated yet.">
        {board.timeline.map((moment) => (
          <li key={`${moment.when}:${moment.what}`} title={moment.source}>
            <strong>{moment.when}</strong> {moment.what}
          </li>
        ))}
      </Column>
      <Column title="Contradictions" empty="Nothing contradicts so far.">
        {board.contradictions.map((c) => (
          <li key={c.what} title={c.sources.join("\n")}>
            {c.what}
          </li>
        ))}
      </Column>
    </section>
  );
}

function Column({
  title,
  empty,
  children,
}: {
  title: string;
  empty: string;
  children: React.ReactNode[];
}) {
  return (
    <div>
      <h3>{title}</h3>
      <ul aria-label={title}>
        {children.length ? children : <li className={styles.empty}>{empty}</li>}
      </ul>
    </div>
  );
}
