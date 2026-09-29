import { useRef, useState } from "react";
import { api, Batch, DeletionImpact, Probe, UploadResult } from "../api";
import { Button, Card, Empty, Grid, MiniButton, Pill, Stat, Table } from "../components/ui";
import type { ExceptionRow } from "../api";
import { longDate, money } from "../format";
import UploadCheck from "../ai/UploadCheck";
import { useApp, useAsync } from "../state";

type Stage = "idle" | "probing" | "probed" | "uploading" | "done" | "error";

export default function Upload() {
  const { can, refreshData } = useApp();
  const [deleting, setDeleting] = useState<DeletionImpact | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [businessDate, setBusinessDate] = useState("");
  const [sheet, setSheet] = useState("");
  const [mode, setMode] = useState<"replace" | "merge">("replace");
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const batches = useAsync(() => api.batches(), [stage === "done", historyKey]);
  const exceptions = useAsync(
    () => (result ? api.batchExceptions(result.batch_ref) : Promise.resolve([])),
    [result?.batch_ref],
  );

  const reset = () => {
    setFile(null); setProbe(null); setResult(null);
    setStage("idle"); setError(null); setBusinessDate(""); setSheet("");
  };

  async function handleFile(f: File) {
    reset();
    setFile(f);
    setStage("probing");
    try {
      const p = await api.probe(f);
      setProbe(p);
      setStage("probed");
      // A completion file tops a day up; replacing on one would delete the rows
      // that loaded fine and leave only the corrected handful.
      setMode(p.suggested_mode);
      // Preselect the sheet when there is only one with data, but NOT the
      // date: that stays blank and has to be typed. A sheet name is a label
      // somebody entered, and accepting it silently books the figures against
      // whatever date the tab happens to claim.
      const withData = p.sheets.filter((s) => s.data_rows > 0);
      if (withData.length === 1) setSheet(withData[0].name);
    } catch (e) {
      setError((e as Error).message); setStage("error");
    }
  }

  async function submit() {
    if (!file) return;
    setStage("uploading"); setError(null);
    try {
      const r = await api.upload(file, {
        business_date: businessDate,
        sheet_name: sheet || undefined,
        mode,
      });
      setResult(r);
      setStage("done");
      // Push the new numbers to every open view immediately rather than
      // waiting for the next poll.
      refreshData();
    } catch (e) {
      setError((e as Error).message); setStage("error");
    }
  }

  if (!can("UPLOAD_CREATE")) {
    return <Empty title="Not permitted"
                  hint="Uploading data needs the UPLOAD_CREATE permission." />;
  }

  const sheetsWithData = probe?.sheets.filter((s) => s.data_rows > 0) ?? [];

  async function askDelete(ref: string) {
    setNotice(null);
    try {
      setDeleting(await api.deletionImpact(ref));
      setDeleteReason("");
    } catch (e) { setNotice((e as Error).message); }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      const r = await api.deleteBatch(deleting.batch_ref, deleteReason || undefined);
      setNotice(
        `Deleted ${r.batch_ref}: removed ${r.fact_rows_removed.toLocaleString()} ` +
        `calculated rows worth ${money(r.ftp_profit_removed)}` +
        (r.rows_restored
          ? `, restored ${r.rows_restored.toLocaleString()} rows from ` +
            `${r.batches_restored.join(", ")}` : "") +
        (r.dates_recalculated.length
          ? `, recalculated ${r.dates_recalculated.join(", ")}` : "") +
        (r.dates_emptied.length
          ? `. ${r.dates_emptied.join(", ")} now has no data.` : "."),
      );
      setDeleting(null);
      setHistoryKey((k) => k + 1);
      refreshData();
    } catch (e) { setNotice((e as Error).message); }
    finally { setDeleteBusy(false); }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Card title="Upload bank data"
            subtitle="Excel or CSV. A single daily sheet is the normal case; a multi-sheet workbook loads as a historical backfill."
            footnote="The file is fingerprinted on arrival, so re-uploading identical content is rejected rather than double-counted. Re-uploading a date that already has data supersedes it — nothing is deleted.">
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault(); setDrag(false);
            const f = e.dataTransfer.files?.[0];
            if (f) handleFile(f);
          }}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") inputRef.current?.click(); }}
          style={{
            border: `2px dashed ${drag ? "var(--series-1)" : "var(--border-strong)"}`,
            borderRadius: 10, padding: "30px 20px", textAlign: "center",
            background: drag ? "var(--surface-2)" : "transparent",
            cursor: "pointer", transition: "background .15s, border-color .15s",
          }}>
          <p style={{ margin: 0, fontSize: "var(--fs-md)", fontWeight: 550 }}>
            {file ? file.name : "Drop a workbook here, or click to choose"}
          </p>
          <p style={{ margin: "6px 0 0", fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
            {file
              ? `${(file.size / 1024).toFixed(0)} KB`
              : ".xlsx, .xlsm or .csv — macros are never opened"}
          </p>
          <input ref={inputRef} type="file" accept=".xlsx,.xlsm,.csv" hidden
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
        </div>

        {error && (
          <div style={{
            marginTop: 12, padding: "10px 12px", borderRadius: 8,
            border: "1px solid var(--status-critical)",
            background: "var(--surface-2)", fontSize: "var(--fs-base)",
          }}>
            <Pill tone="critical">Upload failed</Pill>
            <p style={{ margin: "6px 0 0", color: "var(--text-secondary)" }}>{error}</p>
          </div>
        )}

        {probe && stage !== "done" && (
          <div style={{ marginTop: 14 }}>
            <h4 style={{ margin: "0 0 8px", fontSize: "var(--fs-base)", fontWeight: 600 }}>
              What the file contains
            </h4>
            <Table rows={probe.sheets}
                   cols={[
                     { key: "name", label: "Sheet" },
                     { key: "inc", label: "Status",
                       render: (r) => r.data_rows > 0
                         ? <Pill tone="good">has data</Pill>
                         : <Pill tone="neutral">empty</Pill>,
                       value: (r) => (r.data_rows > 0 ? "has data" : "empty") },
                     { key: "rows", label: "Data rows", align: "right",
                       render: (r) => r.data_rows.toLocaleString(),
                       value: (r) => r.data_rows },
                     { key: "d", label: "Name suggests",
                       render: (r) => (r.business_date
                         ? <span style={{ color: "var(--text-muted)" }}>
                             {longDate(r.business_date)}
                           </span>
                         : "—"),
                       value: (r) => r.business_date },
                     { key: "why", label: "Reason", render: (r) => r.reason },
                   ]} />

            {probe.header_issues.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <Pill tone="warning">Header drift</Pill>
                <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: "var(--fs-sm)",
                             color: "var(--text-secondary)" }}>
                  {probe.header_issues.map((h, i) => <li key={i}>{h}</li>)}
                </ul>
              </div>
            )}

            {probe.looks_like_rejects_export && (
              <div style={{ marginTop: 12, padding: "10px 12px", borderRadius: 8,
                            background: "var(--surface-2)",
                            border: "1px solid var(--series-1)" }}>
                <Pill tone="info">Completion file</Pill>
                <p style={{ margin: "6px 0 0", fontSize: "var(--fs-base)",
                            color: "var(--text-secondary)" }}>
                  This is a rejected-rows workbook produced by this system. It
                  will be <b>merged</b> into the day already loaded, so the rows
                  that loaded fine are kept.
                </p>
              </div>
            )}

            <div style={{ display: "flex", gap: 12, flexWrap: "wrap",
                          alignItems: "flex-end", marginTop: 14 }}>
              <div>
                <label htmlFor="up-date"
                       style={{ display: "block", color: "var(--text-muted)",
                                fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".05em",
                                textTransform: "uppercase", marginBottom: 4 }}>
                  Business date <span style={{ color: "var(--status-critical)" }}>*</span>
                </label>
                <input id="up-date" type="date" value={businessDate} required
                       onChange={(e) => setBusinessDate(e.target.value)}
                       style={{ borderColor: businessDate
                                  ? undefined : "var(--status-critical)" }} />
                {probe.suggested_date && (
                  <div style={{ marginTop: 4 }}>
                    <button type="button"
                            onClick={() => setBusinessDate(probe.suggested_date!)}
                            style={{ background: "none", border: "none", padding: 0,
                                     fontSize: "var(--fs-xs)", color: "var(--accent)",
                                     cursor: "pointer", textDecoration: "underline" }}>
                      the sheet is named {longDate(probe.suggested_date)} — use it
                    </button>
                  </div>
                )}
              </div>
              <div>
                <span style={{ display: "block", color: "var(--text-muted)",
                               fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".05em",
                               textTransform: "uppercase", marginBottom: 4 }}>
                  If the date is already loaded
                </span>
                <div style={{ display: "flex", gap: 5 }}>
                  <MiniButton active={mode === "replace"} onClick={() => setMode("replace")}
                              title="The bank resent the whole day; anything absent is gone">
                    Replace the day
                  </MiniButton>
                  <MiniButton active={mode === "merge"} onClick={() => setMode("merge")}
                              title="Top up the day; only the accounts in this file are updated">
                    Merge into it
                  </MiniButton>
                </div>
              </div>
              {sheetsWithData.length > 1 && (
                <div>
                  <label htmlFor="up-sheet"
                         style={{ display: "block", color: "var(--text-muted)",
                                  fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".05em",
                                  textTransform: "uppercase", marginBottom: 4 }}>
                    Sheet <span style={{ color: "var(--status-critical)" }}>*</span>
                  </label>
                  <select id="up-sheet" value={sheet}
                          onChange={(e) => setSheet(e.target.value)}
                          style={{ borderColor: sheet
                                     ? undefined : "var(--status-critical)" }}>
                    <option value="">choose…</option>
                    {sheetsWithData.map((s) => (
                      <option key={s.name} value={s.name}>
                        {s.name} ({s.data_rows.toLocaleString()} rows)
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                <Button onClick={reset}>Cancel</Button>
                <Button variant="primary" onClick={submit}
                        disabled={!probe.ok || !businessDate || stage === "uploading"
                                  || (sheetsWithData.length > 1 && !sheet)}>
                  {stage === "uploading" ? "Processing…" : "Load and calculate"}
                </Button>
              </div>
            </div>
          </div>
        )}

        {result && (
          <div style={{ marginTop: 14 }}>
            <Grid cols="repeat(auto-fit, minmax(140px, 1fr))" gap={10}>
              <Stat label="Rows read" value={result.total_rows.toLocaleString()} />
              <Stat label="Accepted" value={result.accepted_rows.toLocaleString()} tone="good" />
              <Stat label="Warned" value={result.warned_rows.toLocaleString()} />
              <Stat label="Rejected" value={result.rejected_rows.toLocaleString()}
                    tone={result.rejected_rows ? "bad" : "neutral"} />
              <Stat label="Subtotal rows" value={result.structural_rows.toLocaleString()}
                    hint="excluded by rule" />
            </Grid>
            <p style={{ marginTop: 10, fontSize: "var(--fs-base)", color: "var(--text-secondary)" }}>
              Batch <b>{result.batch_ref}</b> · {result.status}
              {result.run_ref && <> · calculation {result.run_ref}</>}
              {result.business_dates.length > 0 && <>
                {" "}· {longDate(result.business_dates[0])}
                {result.business_dates.length > 1 &&
                  ` to ${longDate(result.business_dates.at(-1)!)}`}
              </>}
            </p>
            {result.rejected_rows > 0 && (
              <div style={{
                marginTop: 12, padding: "11px 13px", borderRadius: 8,
                background: "var(--surface-2)",
                border: "1px solid var(--status-critical)",
              }}>
                <Pill tone="critical">
                  {result.rejected_rows.toLocaleString()} rows not loaded
                </Pill>
                <p style={{ margin: "7px 0 0", fontSize: "var(--fs-base)",
                            color: "var(--text-secondary)" }}>
                  These rows reference master data that does not exist yet. The
                  rest of the file loaded and the figures above already include
                  it. Download the rejected rows, register what is missing, then
                  re-upload the same file — it merges into the day.
                </p>
                {(() => {
                  const groups = groupReasons(exceptions.data ?? []);
                  const counted = Object.values(groups).reduce((a, b) => a + b, 0);
                  return (
                    <>
                      <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: "var(--fs-sm)",
                                   color: "var(--text-secondary)" }}>
                        {Object.entries(groups).map(([what, rows]) => (
                          <li key={what}><b>{what}</b> — {rows} row{rows === 1 ? "" : "s"}</li>
                        ))}
                      </ul>
                      {counted > result.rejected_rows && (
                        // The counts are per reason, so a row failing on both a
                        // missing branch and a missing product is counted twice.
                        // Saying so is cheaper than leaving the arithmetic to
                        // look wrong.
                        <p style={{ margin: "6px 0 0", fontSize: "var(--fs-sm)",
                                    color: "var(--text-muted)" }}>
                          {counted - result.rejected_rows} row
                          {counted - result.rejected_rows === 1 ? "" : "s"} fail more
                          than one check, so the counts above add up to more than{" "}
                          {result.rejected_rows}.
                        </p>
                      )}
                    </>
                  );
                })()}
                <div style={{ marginTop: 10 }}>
                  <Button variant="danger"
                          onClick={() => api.downloadRejects(result.batch_ref)}>
                    Download rejected rows (.xlsx)
                  </Button>
                </div>
              </div>
            )}

            {/* The optional AI module's plain-language check; renders nothing when it is off. */}
            <UploadCheck batchRef={result.batch_ref} onDelete={askDelete} />

            <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
              <Button variant="primary" onClick={() => { location.hash = "#/overview"; }}>
                View the dashboard
              </Button>
              <Button onClick={reset}>Upload another</Button>
            </div>

            {(exceptions.data ?? []).length > 0 && (
              <div style={{ marginTop: 14 }}>
                <h4 style={{ margin: "0 0 6px", fontSize: "var(--fs-base)", fontWeight: 600 }}>
                  Exceptions
                </h4>
                <Table rows={exceptions.data ?? []} maxHeight={240}
                       csvName={`${result.batch_ref}-exceptions.csv`}
                       cols={[
                         { key: "sev", label: "Severity",
                           render: (r) => <Pill tone={r.severity === "REJECT" ? "critical"
                             : r.severity === "WARN" ? "warning" : "info"}>{r.severity}</Pill>,
                           value: (r) => r.severity },
                         { key: "rule_code", label: "Rule" },
                         { key: "origin", label: "Where",
                           render: (r) => r.origin ?? `row ${r.source_row_no ?? "—"}` },
                         { key: "message", label: "Message" },
                       ]} />
              </div>
            )}
          </div>
        )}
      </Card>

      {notice && (
        <Card>
          <div style={{ display: "flex", gap: 10, alignItems: "flex-start",
                        padding: "2px 4px" }}>
            <Pill tone="info">Done</Pill>
            <span style={{ fontSize: "var(--fs-base)", color: "var(--text-secondary)" }}>
              {notice}
            </span>
          </div>
        </Card>
      )}

      {deleting && (
        <Card title={`Delete batch ${deleting.batch_ref}?`}
              subtitle={deleting.deletable
                ? "This removes published figures. The audit record survives it."
                : "This batch cannot be deleted yet"}>
          {!deleting.deletable ? (
            <>
              <Pill tone="critical">Blocked</Pill>
              <p style={{ margin: "8px 0 0", fontSize: "var(--fs-base)",
                          color: "var(--text-secondary)" }}>
                {deleting.blocked_by}
              </p>
              <div style={{ marginTop: 12 }}>
                <Button onClick={() => setDeleting(null)}>Close</Button>
              </div>
            </>
          ) : (
            <>
              <Grid cols="repeat(auto-fit, minmax(140px, 1fr))" gap={10}>
                <Stat label="Calculated rows"
                      value={deleting.fact_rows.toLocaleString()} tone="bad" />
                <Stat label="FTP profit removed"
                      value={money(deleting.ftp_profit_removed)} tone="bad" />
                <Stat label="Raw rows"
                      value={deleting.bank_rows.toLocaleString()} />
                <Stat label="Rows restored"
                      value={deleting.rows_restored.toLocaleString()}
                      tone={deleting.rows_restored ? "good" : "neutral"}
                      hint={deleting.batches_restored.length
                        ? `from ${deleting.batches_restored.join(", ")}` : undefined} />
              </Grid>

              <ul style={{ margin: "12px 0 0", paddingLeft: 18, fontSize: "var(--fs-base)",
                           color: "var(--text-secondary)", lineHeight: 1.7 }}>
                <li>
                  Affects {deleting.business_dates.map(longDate).join(", ")} — each
                  is recalculated from whatever remains.
                </li>
                {deleting.rows_restored > 0 && (
                  <li>
                    {deleting.rows_restored.toLocaleString()} rows this batch
                    replaced are put back, and{" "}
                    {deleting.batches_restored.join(", ")} becomes current again.
                  </li>
                )}
                {deleting.dates_left_empty.length > 0 && (
                  <li style={{ color: "var(--status-critical)" }}>
                    <b>{deleting.dates_left_empty.map(longDate).join(", ")}</b> will
                    be left with no data at all.
                  </li>
                )}
              </ul>

              <label style={{ display: "block", marginTop: 12, fontSize: "var(--fs-sm)" }}>
                <span style={{ display: "block", color: "var(--text-muted)",
                               fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".05em",
                               textTransform: "uppercase", marginBottom: 4 }}>
                  Reason (recorded in the audit trail)
                </span>
                <input value={deleteReason}
                       onChange={(e) => setDeleteReason(e.target.value)}
                       placeholder="e.g. loaded against the wrong business date"
                       style={{ width: "100%", maxWidth: 520 }} />
              </label>

              <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
                <Button variant="danger" onClick={confirmDelete}
                        disabled={deleteBusy || !deleteReason.trim()}>
                  {deleteBusy ? "Deleting…" : "Delete and recalculate"}
                </Button>
                <Button onClick={() => setDeleting(null)}>Cancel</Button>
              </div>
              {!deleteReason.trim() && (
                <p style={{ margin: "7px 0 0", fontSize: "var(--fs-sm)",
                            color: "var(--text-muted)" }}>
                  A reason is required: the deletion outlives the batch in the
                  audit trail, and a bare record of it is no use six months on.
                </p>
              )}
            </>
          )}
        </Card>
      )}

      <Card title="Upload history"
            subtitle="Every file, what it replaced, and what it produced"
            footnote="A superseded batch is retained and remains queryable; dashboards read only the current one.">
        <Table rows={batches.data ?? []} maxHeight={340} csvName="ftp-upload-history.csv"
               cols={[
                 { key: "batch_ref", label: "Batch" },
                 { key: "file_name", label: "File" },
                 { key: "bd", label: "Business date",
                   render: (r) => (r.business_date ? longDate(r.business_date) : "—"),
                   value: (r) => r.business_date },
                 { key: "st", label: "Status",
                   render: (r) => <Pill tone={r.status === "COMPLETED" ? "good"
                     : r.status === "FAILED" ? "critical" : "info"}>{r.status}</Pill>,
                   value: (r) => r.status },
                 { key: "rows", label: "Accepted", align: "right",
                   render: (r) => `${r.accepted_rows.toLocaleString()} / ${r.total_rows.toLocaleString()}`,
                   value: (r) => r.accepted_rows },
                 { key: "rej", label: "Rejected", align: "right",
                   render: (r) => r.rejected_rows.toLocaleString(),
                   value: (r) => r.rejected_rows },
                 { key: "cur", label: "Current",
                   render: (r) => r.is_current
                     ? <Pill tone="good">current</Pill>
                     : <Pill tone="neutral">superseded</Pill>,
                   value: (r) => r.is_current },
                 { key: "at", label: "Uploaded",
                   render: (r) => new Date(r.uploaded_at).toLocaleString("en-GB",
                     { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }),
                   value: (r) => r.uploaded_at },
                 ...(can("UPLOAD_DELETE") ? [{
                   key: "act", label: "", align: "right" as const,
render: (r: Batch) => (
                      <MiniButton onClick={() => askDelete(r.batch_ref)}
                                  title="Delete this batch and recalculate"
                                  style={{ color: "var(--status-critical)",
                                           borderColor: "var(--status-critical)" }}>
                        Delete
                      </MiniButton>
                    ),
                 }] : []),
               ]} />
      </Card>
    </div>
  );
}

/** Group rejections by what is actually missing, so the operator reads a short
 *  list of master records to create rather than one line per row. */
function groupReasons(rows: ExceptionRow[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) {
    if (r.severity !== "REJECT") continue;
    const m = r.message.match(/branch '([^']+)'/) ?? r.message.match(/product '([^']+)'/);
    const key = m
      ? (r.message.includes("branch")
          ? `Branch ${m[1]} is not registered`
          : `Product ${m[1]} is not registered`)
      : `${r.rule_code}: ${r.message}`;
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
}
