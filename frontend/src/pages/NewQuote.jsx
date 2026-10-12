import { useEffect, useMemo, useRef, useState } from "react";
import { Boxes, Weight } from "lucide-react";
import App from "../App";
import { getJson } from "../api";
import OrderDrawer from "../components/OrderDrawer";
import { href } from "../lib/route";
import { useApp } from "../shell/AppShell";

// New Quote: the pallet-plan workspace and the measured-pallet (Quick Quote) workspace, opened
// with the orders, shipment or saved plan named in the address (#/quote/new?orders=…&shipment=…).
export default function NewQuote({ active = true }) {
  const { route, company, notify } = useApp();
  const query = active && route.parts[0] === "quote" ? route.query : null;
  const [context, setContext] = useState(null);
  const [mode, setMode] = useState("builder");
  const [openOrder, setOpenOrder] = useState(null);
  const [orderRefresh, setOrderRefresh] = useState(0);
  // The workspace stays mounted while other pages are open, so a context is applied only when the
  // address names something new: returning to the same address keeps the work in progress, and the
  // mode tabs switch workspaces without reloading the orders.
  const signature = query ? JSON.stringify([query.orders, query.shipment, query.result, query.manual, query.t]) : null;
  const applied = useRef(null);

  useEffect(() => {
    if (query) setMode(query.mode === "measured" ? "quick" : "builder");
  }, [query?.mode, Boolean(query)]);

  useEffect(() => {
    if (!query || signature === applied.current) return;
    applied.current = signature;
    const orderIds = (query.orders || "").split(",").map((value) => value.trim()).filter(Boolean);
    const base = { token: Date.now(), orderIds, shipmentId: query.shipment || null, resultId: query.result || null, manualId: query.manual || null, orders: [] };
    // Plain "New quote": keep whatever is in progress (or a restored draft).
    if (!orderIds.length && !base.shipmentId && !base.resultId && !base.manualId) return;
    if (orderIds.length) {
      Promise.all(orderIds.map((id) => getJson(`/api/orders/${id}`).then((trace) => trace.order)))
        .then((orders) => setContext({ ...base, orders }))
        .catch((error) => { notify(error.message, "danger"); setContext(base); });
    } else {
      setContext(base);
    }
  }, [signature]);

  const tabs = useMemo(() => {
    const keep = { orders: query?.orders, shipment: query?.shipment };
    return [
      ["builder", "Pallet plan from items", Boxes, href("/quote/new", keep)],
      ["quick", "Measured pallets", Weight, href("/quote/new", { ...keep, mode: "measured" })],
    ];
  }, [query?.orders, query?.shipment]);

  return (
    <div className="new-quote">
      <nav className="mode-tabs" aria-label="Quote type">
        {tabs.map(([key, label, Icon, link]) => (
          <a key={key} href={link} className={mode === key ? "active" : ""} aria-current={mode === key ? "page" : undefined}><Icon size={16} aria-hidden="true" />{label}</a>
        ))}
      </nav>
      <App key={company.id} mode={mode} company={company} context={context} orderRefresh={orderRefresh}
        onOrderOpen={setOpenOrder} onRecorded={(quote) => notify(`Saved as quote ${quote.number}.`)} />
      <OrderDrawer orderId={openOrder} notify={notify} onClose={() => setOpenOrder(null)}
        onSaved={() => setOrderRefresh((value) => value + 1)} />
    </div>
  );
}
