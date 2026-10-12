import { AlertTriangle, CheckCircle2, Receipt, Ruler } from "lucide-react";
import { Badge } from "../Common";
import { CHARGE_SOURCES, date, dims, money, weight } from "../../lib/format";
import { href } from "../../lib/route";
import { ACTUAL_SOURCES, percent, signedWeight } from "./model";
import "../../styles/shipments.css";

const FLAGS = [
  ["weight_over", "Heavier than quoted", "warn"],
  ["pallets_changed", "Pallet count changed", "warn"],
  ["cost_over", "Cost over quote", "danger"],
];

function Variance({ value, format = money, goodWhenNegative = true }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  const tone = value === 0 ? "" : (value > 0) === goodWhenNegative ? "is-bad" : "is-good";
  return <span className={`variance ${tone}`}>{format(value, { signed: true })}</span>;
}

function rowText(row) {
  const quantity = row.quantity && row.quantity > 1 ? `${row.quantity} × ` : "";
  return `${quantity}${dims(row.length, row.width, row.height)} in · ${weight(row.weight)}${row.freight_class ? ` · class ${row.freight_class}` : ""}`;
}

// Quoted vs actual for one shipment, from the shipment view's `comparison` (WORKFLOW §4).
export default function ComparisonCard({ comparison, onRecordActuals, onRecordCost, compact = false }) {
  const quoted = comparison?.quoted || null;
  const actual = comparison?.actual || null;
  const cost = comparison?.cost || {};
  const revenue = comparison?.revenue || {};
  const flags = comparison?.flags || {};
  const raised = FLAGS.filter(([key]) => flags[key]);
  const measured = Boolean(actual && (actual.pallets || actual.weight));
  const profit = comparison?.profit_cents ?? null;
  const estimated = Boolean(comparison?.profit_estimated);
  const palletDiff = quoted && actual?.pallets != null ? actual.pallets - quoted.pallets : null;

  return (
    <section className="card compare-card" aria-labelledby="compare-title">
      <header className="card-head">
        <div className="card-head-text">
          <h2 className="card-title" id="compare-title">Quoted vs actual</h2>
          <p className="card-description">
            {quoted ? <>Against <a className="inline-link" href={href("/quotes", { quote: quoted.quote_id })}>{quoted.number}</a>, the quote the customer accepted.</>
              : "No quote is linked to this shipment, so there is nothing to compare against yet."}
          </p>
        </div>
        <div className="compare-flags" aria-label="Comparison flags">
          {raised.map(([key, label, tone]) => <Badge key={key} tone={tone}><AlertTriangle size={12} aria-hidden="true" />{label}</Badge>)}
          {!raised.length && measured && cost.actual_cents != null && <Badge tone="ok"><CheckCircle2 size={12} aria-hidden="true" />In line with the quote</Badge>}
        </div>
      </header>

      <div className={`compare-grid ${compact ? "is-compact" : ""}`}>
        <div className="compare-block">
          <h3 className="compare-heading">Measurements</h3>
          <div className="compare-table-wrap"><table className="data-table is-dense compare-table">
            <thead><tr><th scope="col"><span className="sr-only">Measure</span></th><th scope="col" className="num">Quoted</th><th scope="col" className="num">Actual</th><th scope="col" className="num">Difference</th></tr></thead>
            <tbody>
              <tr>
                <th scope="row">Pallets</th>
                <td className="num">{quoted ? quoted.pallets : "—"}</td>
                <td className="num">{actual?.pallets ?? "—"}</td>
                <td className="num">{palletDiff === null ? <span className="muted">—</span> : <span className={`variance ${palletDiff ? "is-bad" : ""}`}>{palletDiff > 0 ? `+${palletDiff}` : palletDiff < 0 ? `−${-palletDiff}` : "same"}</span>}</td>
              </tr>
              <tr>
                <th scope="row">Total weight</th>
                <td className="num">{quoted ? weight(quoted.total_weight) : "—"}</td>
                <td className="num">{actual?.weight != null ? weight(actual.weight) : "—"}{actual && !actual.pallets && actual.billed_weight ? <small className="muted"> billed</small> : null}</td>
                <td className="num">{comparison?.weight_variance_lb == null ? <span className="muted">—</span>
                  : <span className={`variance ${flags.weight_over ? "is-bad" : ""}`}>{signedWeight(comparison.weight_variance_lb)} <small>({percent(comparison.weight_variance_percent, { signed: true })})</small></span>}</td>
              </tr>
              <tr>
                <th scope="row">Billed weight · class</th>
                <td className="num muted">—</td>
                <td className="num">{actual?.billed_weight ? weight(actual.billed_weight) : "—"}{actual?.billed_class ? ` · class ${actual.billed_class}` : ""}</td>
                <td />
              </tr>
            </tbody>
          </table></div>
          <div className="dims-compare">
            <div>
              <h4>Quoted pallets</h4>
              {quoted?.rows?.length ? <ul className="dims-list">{quoted.rows.map((row, index) => <li key={index}>{rowText(row)}</li>)}</ul>
                : <p className="muted small-line">{quoted ? "No pallet rows on the quote." : "—"}</p>}
            </div>
            <div>
              <h4>Actual pallets</h4>
              {actual?.rows?.length ? <ul className="dims-list">{actual.rows.map((row, index) => <li key={index}>{rowText(row)}{row.note ? <small className="muted"> · {row.note}</small> : null}</li>)}</ul>
                : <p className="muted small-line">Not measured yet.</p>}
              {actual && <p className="small-line muted">{ACTUAL_SOURCES[actual.source] || actual.source}{actual.measured_on ? ` · ${date(actual.measured_on)}` : ""}{actual.notes ? ` · ${actual.notes}` : ""}</p>}
            </div>
          </div>
          {!measured && onRecordActuals && <button type="button" className="btn btn-secondary btn-sm" onClick={onRecordActuals}><Ruler size={14} />Record actual measurements</button>}
        </div>

        <div className="compare-block">
          <h3 className="compare-heading">Money</h3>
          <div className="compare-table-wrap"><table className="data-table is-dense compare-table">
            <thead><tr><th scope="col"><span className="sr-only">Amount</span></th><th scope="col" className="num">Expected</th><th scope="col" className="num">Actual</th><th scope="col" className="num">Variance</th></tr></thead>
            <tbody>
              <tr>
                <th scope="row">Freight cost</th>
                <td className="num">{money(cost.expected_cents)}</td>
                <td className="num">{cost.actual_cents != null ? money(cost.actual_cents) : <span className="muted">no bills yet</span>}</td>
                <td className="num"><Variance value={cost.variance_cents} />{cost.variance_percent != null && <small className="muted"> ({percent(cost.variance_percent, { signed: true })})</small>}</td>
              </tr>
            </tbody>
          </table></div>
          <dl className="money-lines">
            <div><dt>Customer charge <small className="muted">{CHARGE_SOURCES[revenue.charge_source]?.label || ""}</small></dt><dd>{money(revenue.customer_charge_cents)}</dd></div>
            <div><dt>Extra revenue</dt><dd>{money(revenue.extra_cents || 0)}</dd></div>
            <div className="money-total"><dt>Revenue</dt><dd>{money(revenue.total_cents)}</dd></div>
            <div className="money-total">
              <dt>Profit {estimated && <Badge tone="info" title="No carrier bill yet: revenue minus the expected cost">estimated</Badge>}</dt>
              <dd className={profit != null && profit < 0 ? "is-bad" : ""}>{money(profit)}</dd>
            </div>
            <div><dt>Margin</dt><dd className={comparison?.margin_percent != null && comparison.margin_percent < 0 ? "is-bad" : ""}>{percent(comparison?.margin_percent)}{estimated && comparison?.margin_percent != null ? <small className="muted"> est.</small> : null}</dd></div>
          </dl>
          {cost.actual_cents == null && onRecordCost && <button type="button" className="btn btn-secondary btn-sm" onClick={onRecordCost}><Receipt size={14} />Record the carrier bill</button>}
        </div>
      </div>
    </section>
  );
}
