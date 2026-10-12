import { useCallback, useEffect, useMemo, useState } from "react";
import { BarChart3, FileQuestion, Truck } from "lucide-react";
import { PageHeader } from "../components/Common";
import Ledger from "../components/insights/Ledger";
import Reports from "../components/insights/Reports";
import Unassigned from "../components/insights/Unassigned";
import { companyQuery, GROUPS, rangeFor } from "../components/insights/shared";
import useResource from "../lib/useResource";
import { useApp } from "../shell/AppShell";
import "../styles/insights.css";

const TABS = [
  { key: "reports", label: "Reports", icon: BarChart3 },
  { key: "ledger", label: "Shipments ledger", icon: Truck },
  { key: "unassigned", label: "Unassigned", icon: FileQuestion },
];
const DEFAULT_RANGE = "last_12";

// Freight Data: profit reports, the per-shipment ledger and money not yet on a shipment.
// Tab, period, grouping and filters live in the URL so a view can be bookmarked or linked.
export default function FreightData() {
  const { company, multiCompany, notify, route, go } = useApp();
  const query = route.query;
  const tab = TABS.some((entry) => entry.key === query.tab) ? query.tab : "reports";
  const rangeKey = query.range || DEFAULT_RANGE;
  const range = useMemo(() => ({ range: rangeKey, ...rangeFor(rangeKey, { from: query.from, to: query.to }) }), [rangeKey, query.from, query.to]);
  const group = GROUPS.some(([key]) => key === query.group) ? query.group : "month";
  const excludeSister = query.sister === "exclude";
  const drill = useMemo(() => ({ group: query.dg || "", key: query.dk ?? "" }), [query.dg, query.dk]);

  const update = useCallback((changes) => {
    const next = { ...route.query, ...changes };
    if (changes.range && changes.range !== "custom") { delete next.from; delete next.to; }
    if (next.range === DEFAULT_RANGE) delete next.range;
    go(route.path, next, { replace: true });
  }, [route.query, route.path, go]);

  const [visited, setVisited] = useState(() => new Set([tab]));
  useEffect(() => { setVisited((current) => (current.has(tab) ? current : new Set([...current, tab]))); }, [tab]);
  const [version, setVersion] = useState(0);

  const records = useResource(`/api/freight-records${companyQuery(company, multiCompany)}`, { enabled: visited.has("ledger") });
  const unassignedCount = useResource(`/api/unassigned-expenses${companyQuery(company, multiCompany)}`);
  const openBills = (unassignedCount.data || []).length;

  const selectTab = (key) => update({ tab: key === "reports" ? "" : key });
  const onKeyDown = (event) => {
    const index = TABS.findIndex((entry) => entry.key === tab);
    let next = null;
    if (event.key === "ArrowRight") next = TABS[(index + 1) % TABS.length];
    if (event.key === "ArrowLeft") next = TABS[(index + TABS.length - 1) % TABS.length];
    if (event.key === "Home") next = TABS[0];
    if (event.key === "End") next = TABS[TABS.length - 1];
    if (!next) return;
    event.preventDefault();
    selectTab(next.key);
    requestAnimationFrame(() => document.getElementById(`freight-tab-${next.key}`)?.focus());
  };

  const drillInto = useCallback((drillGroup, key, filter) => {
    update({ tab: "ledger", dg: drillGroup || "", dk: drillGroup ? key : "", filter: filter || "" });
    window.scrollTo?.({ top: 0 });
  }, [update]);

  const changed = () => {
    setVersion((value) => value + 1);
    unassignedCount.reload({ quiet: true });
    if (records.data) records.reload({ quiet: true });
  };

  return (
    <div className="page ins-page">
      <PageHeader title="Freight Data"
        lede="Freight revenue, carrier costs and profit by month, rep, carrier, purpose or customer, every shipment's quote versus actual, and money that is not on a shipment yet." />
      <div className="tabs ins-tabs" role="tablist" aria-label="Freight data views" onKeyDown={onKeyDown}>
        {TABS.map(({ key, label, icon: Icon }) => (
          <button key={key} id={`freight-tab-${key}`} type="button" role="tab" aria-selected={tab === key} aria-controls={`freight-panel-${key}`}
            tabIndex={tab === key ? 0 : -1} className={tab === key ? "active" : ""} onClick={() => selectTab(key)}>
            <Icon size={15} aria-hidden="true" />{label}
            {key === "unassigned" && openBills > 0 && <span className="tab-count is-warn" aria-label={`${openBills} open bills`}>{openBills}</span>}
          </button>
        ))}
      </div>
      {TABS.filter(({ key }) => visited.has(key) || key === tab).map(({ key }) => (
        <div key={key} id={`freight-panel-${key}`} role="tabpanel" aria-labelledby={`freight-tab-${key}`} hidden={tab !== key}>
          {key === "reports" && <Reports key={`reports-${version}`} company={company} multiCompany={multiCompany} range={range} group={group}
            excludeSister={excludeSister} onChange={update} onDrill={drillInto} />}
          {key === "ledger" && <Ledger records={records.data} error={records.error} loading={records.loading} reload={records.reload}
            company={company} multiCompany={multiCompany} range={range} excludeSister={excludeSister} filter={query.filter || "all"} drill={drill} onChange={update} />}
          {key === "unassigned" && <Unassigned company={company} multiCompany={multiCompany} notify={notify} onChanged={changed} />}
        </div>
      ))}
    </div>
  );
}
