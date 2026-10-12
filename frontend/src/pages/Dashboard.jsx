import { useMemo } from "react";
import {
  AlertTriangle, ArrowRight, BarChart3, Boxes, ClipboardList, FileUp, History, RefreshCw, TrendingDown, TrendingUp,
  Truck, Weight,
} from "lucide-react";
import { Badge, EmptyState, ErrorBlock, Loading, PageHeader, StatusBadge } from "../components/Common";
import { AttentionList } from "../components/insights/Attention";
import { companyQuery, isoDay, percent } from "../components/insights/shared";
import useResource from "../lib/useResource";
import { href } from "../lib/route";
import { CUSTOMER_STATUS, date, money, plural, relative, weight } from "../lib/format";
import { useApp } from "../shell/AppShell";
import "../styles/insights.css";

const ENTITY_LINK = {
  order: (id) => href("/orders", { open: id }),
  quote: (id) => href("/quotes", { quote: id }),
  shipment: (id) => href(`/shipments/${id}`),
};

const ANSWER_ORDER = ["not_sent", "sent", "approved", "declined", "expired"];

function monthWindow(today = new Date()) {
  const first = new Date(today.getFullYear(), today.getMonth(), 1);
  const previous = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  return { from: isoDay(previous), current: isoDay(first).slice(0, 7), previous: isoDay(previous).slice(0, 7) };
}

export default function Dashboard() {
  const { company, multiCompany } = useApp();
  const period = useMemo(() => monthWindow(), []);
  const dashboard = useResource(`/api/dashboard${companyQuery(company, multiCompany)}`);
  const attention = useResource(`/api/attention${companyQuery(company, multiCompany)}`);
  const months = useResource(`/api/reports/summary${companyQuery(company, multiCompany, { group: "month", from: period.from })}`);
  const data = dashboard.data;
  const items = attention.data?.items || data?.attention?.items || [];
  const severity = useMemo(() => items.reduce((acc, item) => ({ ...acc, [item.severity]: (acc[item.severity] || 0) + 1 }), {}), [items]);
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  return (
    <div className="page ins-page">
      <PageHeader
        eyebrow={today}
        title="Dashboard"
        lede="Today's freight desk: what needs attention, how quotes are answered and how this month is doing."
        actions={<>
          <a className="btn btn-secondary" href={href("/quote/new", { mode: "measured" })}><Weight size={16} />Measured pallets</a>
          <a className="btn btn-primary" href={href("/quote/new")}><FileUp size={16} />Import slips &amp; quote</a>
        </>}
      />
      {dashboard.loading && !data && <Loading />}
      {dashboard.error && <ErrorBlock message={dashboard.error} onRetry={dashboard.reload} />}
      {data && <>
        <div className="ins-cockpit">
          <section className="card ins-attention-card" aria-labelledby="attention-title">
            <header className="card-head">
              <div className="card-head-text">
                <h2 className="card-title" id="attention-title">Needs attention</h2>
                <p className="card-description">
                  {attention.data ? (attention.data.total
                    ? [severity.danger && `${severity.danger} urgent`, severity.warn && `${severity.warn} to act on`, severity.info && `${severity.info} for info`].filter(Boolean).join(" · ")
                    : "All clear") : "Checking quotes, shipments and bills…"}
                </p>
              </div>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => attention.reload({ quiet: true })} disabled={attention.loading}>
                <RefreshCw size={14} className={attention.loading ? "spin" : undefined} />Refresh
              </button>
            </header>
            {attention.error && <ErrorBlock message={attention.error} onRetry={attention.reload} />}
            {!attention.error && <AttentionList items={items} loading={attention.loading && !attention.data} />}
          </section>

          <div className="ins-rail">
            <MonthCard report={months.data} error={months.error} period={period} />
            <AnswersCard answers={data.quote_answers} />
            <section className="card ins-workload" aria-label="Work queues">
              <a href={href("/orders", { status: "needs_review" })} className={data.counts.orders_needing_review ? "is-warn" : ""}>
                <ClipboardList size={16} aria-hidden="true" /><span>Orders to review</span><b>{data.counts.orders_needing_review}</b>
              </a>
              <a href={href("/orders", { view: "quoted" })}>
                <History size={16} aria-hidden="true" /><span>Quoted, not shipped</span><b>{data.counts.quoted_orders_not_shipped}</b>
              </a>
              <a href={href("/shipments", { status: "open" })}>
                <Truck size={16} aria-hidden="true" /><span>Open shipments</span><b>{data.counts.open_shipments}</b>
              </a>
              <a href={href("/shipments", { status: "missing_expense" })} className={data.counts.shipments_missing_expense ? "is-warn" : ""}>
                <AlertTriangle size={16} aria-hidden="true" /><span>In transit or delivered, no bill</span><b>{data.counts.shipments_missing_expense}</b>
              </a>
            </section>
          </div>
        </div>

        <div className="dash-grid ins-dash-lists">
          <section className="card">
            <header className="card-head">
              <div className="card-head-text"><h2 className="card-title">Open shipments</h2><p className="card-description">Planning through delivered.</p></div>
              <a className="btn btn-ghost btn-sm" href={href("/shipments")}>All shipments<ArrowRight size={14} /></a>
            </header>
            {data.open_shipments.length ? (
              <ul className="item-list">
                {data.open_shipments.map((shipment) => (
                  <li key={shipment.id}>
                    <a href={href(`/shipments/${shipment.id}`)} className="item-link">
                      <span className="kind-icon" aria-hidden="true"><Truck size={16} /></span>
                      <span className="item-main">
                        <b>{shipment.number} · {shipment.name}</b>
                        <small>{plural(shipment.order_count, "order")}{shipment.carrier ? ` · ${shipment.carrier}` : ""}{shipment.pickup_date ? ` · pickup ${date(shipment.pickup_date)}` : ""}</small>
                      </span>
                      <span className="item-side"><StatusBadge kind="shipment" status={shipment.status} /></span>
                    </a>
                  </li>
                ))}
              </ul>
            ) : <EmptyState icon={Truck} title="No open shipments">Approve a quote or group quoted orders into a shipment.</EmptyState>}
          </section>

          <section className="card">
            <header className="card-head">
              <div className="card-head-text"><h2 className="card-title">Recent quotes</h2><p className="card-description">Latest estimates and their customer answer.</p></div>
              <a className="btn btn-ghost btn-sm" href={href("/quotes")}>All quotes<ArrowRight size={14} /></a>
            </header>
            {data.recent_quotes.length ? (
              <ul className="item-list">
                {data.recent_quotes.map((quote) => {
                  const answer = CUSTOMER_STATUS[quote.customer_status];
                  const price = quote.cost?.customer_price_cents ?? quote.cost?.amount_cents;
                  return (
                    <li key={quote.id}>
                      <a href={href("/quotes", { quote: quote.id })} className="item-link">
                        <span className={`kind-icon kind-${quote.kind}`} aria-hidden="true">{quote.kind === "packed" ? <Boxes size={16} /> : <Weight size={16} />}</span>
                        <span className="item-main">
                          <b>{quote.number} · {(quote.orders || []).map((order) => order.order_number).join(", ") || quote.name}</b>
                          <small>{plural(quote.summary?.pallets ?? 0, "pallet")} · {weight(quote.summary?.total_weight)} · {relative(quote.created_at)}</small>
                        </span>
                        <span className="item-side">
                          {price !== null && price !== undefined && <b className="ins-num">{money(price)}</b>}
                          {answer ? <Badge tone={answer.tone}>{answer.label}</Badge> : <StatusBadge kind="quote" status={quote.status} />}
                        </span>
                      </a>
                    </li>
                  );
                })}
              </ul>
            ) : <EmptyState icon={History} title="No quotes yet">Import packing slips or enter measured pallets to create the first quote.</EmptyState>}
          </section>

          <section className="card ins-activity-card">
            <header className="card-head"><div className="card-head-text"><h2 className="card-title">Recent activity</h2><p className="card-description">Imports, quotes, shipment and cost changes.</p></div></header>
            {data.activity.length ? (
              <ul className="activity-list">
                {data.activity.map((entry) => (
                  <li key={entry.id}>
                    <Badge tone="neutral">{entry.entity_type.replaceAll("_", " ")}</Badge>
                    <span className="activity-text">
                      {ENTITY_LINK[entry.entity_type]
                        ? <a className="inline-link" href={ENTITY_LINK[entry.entity_type](entry.entity_id)}>{entry.summary || entry.action}</a>
                        : entry.summary || entry.action}
                    </span>
                    <small>{relative(entry.at)}</small>
                  </li>
                ))}
              </ul>
            ) : <p className="muted card-empty-line">No activity yet.</p>}
          </section>
        </div>
      </>}
    </div>
  );
}

function Delta({ now, before, label }) {
  if (before === null || before === undefined || now === null || now === undefined || before === 0) return null;
  const change = now - before;
  if (!change) return <small className="ins-delta">same as {label}</small>;
  const Icon = change > 0 ? TrendingUp : TrendingDown;
  return <small className={`ins-delta ${change > 0 ? "is-up" : "is-down"}`}><Icon size={12} aria-hidden="true" />{money(change, { signed: true })} vs {label}</small>;
}

function MonthCard({ report, error, period }) {
  const row = report?.rows.find((entry) => entry.key === period.current);
  const before = report?.rows.find((entry) => entry.key === period.previous);
  const value = (key) => row ? row[key] : (report ? 0 : null);
  const net = value("net_cents");
  const monthName = new Date().toLocaleDateString(undefined, { month: "long" });
  return (
    <section className="card ins-month" aria-labelledby="month-title">
      <header className="card-head">
        <div className="card-head-text">
          <h2 className="card-title" id="month-title">{monthName} so far</h2>
          <p className="card-description">Shipments by pickup date, all freight revenue and carrier bills.</p>
        </div>
        <a className="btn btn-ghost btn-sm" href={href("/freight", { tab: "reports", range: "this_month" })}><BarChart3 size={14} />Report</a>
      </header>
      {error && <p className="danger-text small-line">{error}</p>}
      {!report && !error && <Loading label="Adding up this month…" />}
      {report && <>
        <dl className="ins-month-figures">
          <div className="is-lead"><dt>Net</dt><dd className={net < 0 ? "danger-text" : ""}>{money(net)}</dd>
            <Delta now={net} before={before?.net_cents} label="last month" /></div>
          <div><dt>Revenue</dt><dd>{money(value("revenue_cents"))}</dd></div>
          <div><dt>Cost</dt><dd>{money(value("cost_cents"))}</dd></div>
          <div><dt>Margin</dt><dd>{percent(row?.margin_percent)}</dd></div>
        </dl>
        <p className="ins-month-foot">
          {plural(value("shipments") || 0, "shipment")}
          {row?.loss_shipments ? <> · <span className="danger-text">{plural(row.loss_shipments, "loss", "losses")}</span></> : null}
          {row?.shipments_without_cost ? <> · <span className="warn-text">{row.shipments_without_cost} without a bill (net estimated)</span></> : null}
        </p>
      </>}
    </section>
  );
}

function AnswersCard({ answers }) {
  const total = ANSWER_ORDER.reduce((sum, key) => sum + (answers?.[key] || 0), 0);
  const sent = (answers?.sent || 0) + (answers?.approved || 0) + (answers?.declined || 0) + (answers?.expired || 0);
  const conversion = sent ? (answers.approved / sent) * 100 : null;
  return (
    <section className="card ins-answers" aria-labelledby="answers-title">
      <header className="card-head">
        <div className="card-head-text">
          <h2 className="card-title" id="answers-title">Quote answers</h2>
          <p className="card-description">{conversion === null ? "No quotes sent yet." : `${percent(conversion, { digits: 0 })} of sent quotes approved.`}</p>
        </div>
      </header>
      {total > 0 && (
        <div className="ins-answer-bar" aria-hidden="true">
          {ANSWER_ORDER.filter((key) => answers[key]).map((key) => (
            <span key={key} className={`tone-bar-${CUSTOMER_STATUS[key].tone}`} style={{ flexGrow: answers[key] }} />
          ))}
        </div>
      )}
      <ul className="ins-answer-list">
        {ANSWER_ORDER.map((key) => (
          <li key={key}>
            <a href={href("/quotes", { customer_status: key })}>
              <span className={`ins-swatch tone-bar-${CUSTOMER_STATUS[key].tone}`} aria-hidden="true" />
              <span>{CUSTOMER_STATUS[key].label}</span>
              <b className="ins-num">{answers?.[key] ?? 0}</b>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
