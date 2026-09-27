import { useEffect, useState } from "react";
import { Card, Grid, IconButton } from "../components/ui";
import { useAsync } from "../state";
import { askApi, AskResult, PinTile } from "./api";
import AnswerView from "./AnswerView";

/** Answers a person pinned from Ask FTP. Each re-runs its query on every
 *  load -- no model involved -- so the tiles follow every upload. */
export default function PinnedAnswers() {
  const [tick, setTick] = useState(0);
  const pins = useAsync(() => askApi.pins(), [tick]);

  useEffect(() => {
    const on = () => setTick((t) => t + 1);
    window.addEventListener("ftp:pins", on);
    return () => window.removeEventListener("ftp:pins", on);
  }, []);

  const items = pins.data?.items ?? [];
  if (!items.length) return null;

  const remove = async (id: number) => { await askApi.unpin(id).catch(() => {}); setTick((t) => t + 1); };

  return (
    <Grid cols="repeat(auto-fill, minmax(min(100%, 420px), 1fr))" gap={14}>
      {items.map((p: PinTile) => {
        const failed = "error" in p.result;
        const r = p.result as AskResult;
        return (
          <Card key={p.id} title={p.title}
                subtitle={failed ? p.question : r.description}
                actions={<IconButton icon="close" label={`Unpin ${p.title}`} onClick={() => remove(p.id)} />}>
            {failed
              ? <p style={{ margin: 0, color: "var(--text-muted)" }}>{(p.result as { error: string }).error}</p>
              : <AnswerView r={r} compact />}
          </Card>);
      })}
    </Grid>
  );
}
