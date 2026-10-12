import { useMemo } from "react";
import { BarChart3, Download, LoaderCircle } from "lucide-react";
import { EmptyState, ErrorBlock, FilterChips, Loading } from "../Common";
import useResource from "../../lib/useResource";
import { money, plural } from "../../lib/format";
import NumTable from "./NumTable";
import RangeControl from "./RangeControl";
import { companyQuery, count, GROUPS, groupLabel, percent } from "./shared";

// Profit report (WORKFLOW §7): one row per month / rep / carrier / purpose / customer with totals,
// unassigned bills and revenue, and a drill-down into the shipments ledger.
export default function Reports({ company, multiCompany, range, group, excludeSister, onChange, onDrill }) {
  const params = { group, from: range.from, to: range.to, exclude_sister: excludeSister ? "true" : "" };
  const path = `/api/reports/summary${companyQuery(company, multiCompany, params)}`;
  const csv = `/api/reports/summary.csv${companyQuery(company, multiCompany, params)}`;
  const { data, error, loading, reload } = useResource(path);
  const report = data && data.group === group ? data : null;
  const totals = report?.totals;
  const groupName = GROUPS.find(([key]) => key === group)?.[1] || group;

  const columns = useMemo(() => [
    { key: "key", header: true, label: groupName, width: 180, sort: (row) => (group === "month" ? row.key : groupLabel(group, row.key)),
      render: (row) => (
        <button type="button" className="ins-drill" onClick={() => onDrill(group, row.key)} title={`Show the shipments for ${groupLabel(group, row.key)}`}>
          {groupLabel(group, row.key)}
        </button>
      ) },
    { key: "shipments", label: "Shipments", num: true },
    { key: "revenue_cents", label: "Revenue", num: true, render: (row) => money(row.revenue_cents),
      title: "Customer charges plus extra revenue (re-bills, claim recoveries)" },
    { key: "cost_cents", label: "Cost", num: true, render: (row) => money(row.cost_cents), title: "Carrier bills on these shipments" },
    { key: "net_cents", label: "Net", num: true, render: (row) => <NetValue cents={row.net_cents} /> },
    { key: "margin_percent", label: "Margin", num: true, render: (row) => <Margin value={row.margin_percent} /> },
    { key: "quotes_sent", label: "Sent", num: true, title: "Quotes sent or answered (by quote date)" },
    { key: "quotes_approved", label: "Approved", num: true },
    { key: "quotes_declined", label: "Declined", num: true },
    { key: "conversion_percent", label: "Conv.", num: true, render: (row) => percent(row.conversion_percent), title: "Approved ÷ sent" },
    { key: "loss_shipments", label: "Losses", num: true, render: (row) => row.loss_shipments ? <span className="danger-text ins-strong">{row.loss_shipments}</span> : <span className="muted">0</span>, title: "Shipments with net below zero" },
    { key: "shipments_without_cost", label: "No bill", num: true, render: (row) => row.shipments_without_cost ? <span className="warn-text ins-strong">{row.shipments_without_cost}</span> : <span className="muted">0</span>, title: "Shipments without a carrier bill (net uses the quoted cost)" },
  ], [group, groupName, onDrill]);

  const footer = totals && <>
    <tr className="ins-total">
      <th scope="row">Total</th>
      <td className="num">{count(totals.shipments)}</td>
      <td className="num">{money(totals.revenue_cents)}</td>
      <td className="num">{money(totals.cost_cents)}</td>
      <td className="num"><NetValue cents={totals.net_cents} /></td>
      <td className="num"><Margin value={totals.margin_percent} /></td>
      <td className="num">{count(totals.quotes_sent)}</td>
      <td className="num">{count(totals.quotes_approved)}</td>
      <td className="num">{count(totals.quotes_declined)}</td>
      <td className="num">{percent(totals.conversion_percent)}</td>
      <td className="num">{count(totals.loss_shipments)}</td>
      <td className="num">{count(totals.shipments_without_cost)}</td>
    </tr>
    <tr className="ins-subtotal">
      <th scope="row">Bills without a shipment</th>
      <td /><td />
      <td className="num">{money(totals.unassigned_expense_cents)}</td>
      <td colSpan={8} />
    </tr>
    <tr className="ins-subtotal">
      <th scope="row">Revenue without a shipment</th>
      <td />
      <td className="num">{money(totals.unassigned_revenue_cents)}</td>
      <td colSpan={9} />
    </tr>
    <tr className="ins-total ins-total-final">
      <th scope="row">Net incl. unassigned</th>
      <td /><td /><td />
      <td className="num"><NetValue cents={totals.net_with_unassigned_cents} /></td>
      <td colSpan={7} />
    </tr>
  </>;

  return (
    <div className="ins-tab-body">
      <div className="list-toolbar ins-toolbar">
        <div className="ins-toolbar-group">
          <FilterChips label="Group by" value={group} onChange={(value) => onChange({ group: value })} options={GROUPS} />
          <RangeControl value={range} onChange={(value) => onChange(value)} idPrefix="report-range" />
          <label className="ins-toggle">
            <input type="checkbox" checked={excludeSister} onChange={(event) => onChange({ sister: event.target.checked ? "exclude" : "" })} />
            <span>Exclude sister company</span>
          </label>
        </div>
        <div className="ins-toolbar-group">
          {loading && data && <span className="muted ins-busy" role="status"><LoaderCircle size={14} className="spin" />Updating…</span>}
          <a className="btn btn-secondary btn-sm" href={csv} download><Download size={14} />CSV</a>
        </div>
      </div>
      {error && <ErrorBlock message={error} onRetry={reload} />}
      {!report && loading && <Loading label="Adding up shipments and quotes…" />}
      {report && <>
        <section className="ins-figures" aria-label="Report totals">
          <Figure label="Revenue" value={money(totals.revenue_cents)} hint={totals.extra_revenue_cents ? `incl. ${money(totals.extra_revenue_cents)} extra revenue` : "customer charges"} />
          <Figure label="Cost" value={money(totals.cost_cents)} hint={totals.shipments_without_cost ? `${plural(totals.shipments_without_cost, "shipment")} without a bill` : "carrier bills"} />
          <Figure label="Net" value={<NetValue cents={totals.net_cents} />} tone={totals.net_cents < 0 ? "danger" : "ok"}
            hint={`${money(totals.net_with_unassigned_cents)} incl. unassigned`} />
          <Figure label="Margin" value={percent(totals.margin_percent)} hint="net ÷ revenue" />
          <Figure label="Conversion" value={percent(totals.conversion_percent)} hint={`${count(totals.quotes_approved)} approved of ${count(totals.quotes_sent)} sent`} />
          <Figure label="Loss shipments" value={count(totals.loss_shipments)} tone={totals.loss_shipments ? "danger" : undefined}
            hint={totals.loss_shipments ? <button type="button" className="link-btn" onClick={() => onDrill(null, null, "loss")}>Show them</button> : "net below zero"} />
        </section>
        <section className="card card-flush">
          <NumTable columns={columns} rows={report.rows} rowKey={(row) => row.key} footer={footer} caption={`Profit by ${groupName.toLowerCase()}`}
            initialSort={group === "month" ? { key: "key", direction: "asc" } : { key: "revenue_cents", direction: "desc" }}
            key={group} minWidth={980}
            empty={<EmptyState icon={BarChart3} title="Nothing in this period">No shipments or quotes fall in these dates. Try a longer period.</EmptyState>} />
        </section>
        <p className="hint ins-footnote">Shipments count on their pickup date (else delivery, else creation); quotes on the day they were made. Cancelled shipments are left out. Click a row to see its shipments.</p>
      </>}
    </div>
  );
}

export function NetValue({ cents }) {
  if (cents === null || cents === undefined) return "—";
  return <span className={cents < 0 ? "danger-text ins-strong" : ""}>{money(cents)}</span>;
}

export function Margin({ value }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  return <span className={value < 0 ? "danger-text" : value < 10 ? "warn-text" : ""}>{percent(value)}</span>;
}

function Figure({ label, value, hint, tone }) {
  return (
    <div className={`ins-figure ${tone ? `is-${tone}` : ""}`}>
      <span className="ins-figure-label">{label}</span>
      <strong className="ins-figure-value">{value}</strong>
      {hint && <span className="ins-figure-hint">{hint}</span>}
    </div>
  );
}
export { Figure };
