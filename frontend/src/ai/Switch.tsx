/** An on/off switch. A real `role="switch"` so assistive tech announces the
 *  state, and the label says what it does, not just "on". */
export default function Switch({ checked, onChange, label, disabled, size = "md" }: {
  checked: boolean; onChange: (v: boolean) => void; label: string;
  disabled?: boolean; size?: "md" | "lg";
}) {
  const w = size === "lg" ? 52 : 40, h = size === "lg" ? 30 : 22, k = h - 6;
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label}
            title={label} disabled={disabled} onClick={() => onChange(!checked)}
            style={{
              width: w, height: h, borderRadius: 999, padding: 0, flexShrink: 0,
              border: "1px solid " + (checked ? "var(--accent-solid)" : "var(--border-strong)"),
              background: checked ? "var(--accent-solid)" : "var(--surface-sunken)",
              position: "relative", cursor: disabled ? "not-allowed" : "pointer",
              opacity: disabled ? 0.55 : 1, transition: "background .15s, border-color .15s",
            }}>
      <span aria-hidden style={{
        position: "absolute", top: 2, left: checked ? w - k - 4 : 2, width: k, height: k,
        borderRadius: 999, background: "#fff", boxShadow: "0 1px 3px rgba(16,24,40,.25)",
        transition: "left .15s",
      }} />
    </button>
  );
}
