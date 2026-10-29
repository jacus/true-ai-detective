import type { Board } from "@/lib/api";
import styles from "./board.module.css";

/** The case board: leads, hypotheses, a timeline and contradictions (the detective graph's `board` state). */
export function CaseBoard({ board }: { board: Board }) {
  return (
    <section className={styles.board} aria-label="Case board">
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
