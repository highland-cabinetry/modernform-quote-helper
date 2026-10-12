import { useEffect, useState } from "react";
import { ClipboardList, Truck } from "lucide-react";
import { DataTable, Drawer, EmptyState, Loading, SearchInput, StatusBadge } from "../Common";
import useResource, { useDebounced } from "../../lib/useResource";
import { useApp } from "../../shell/AppShell";

// Choose unshipped orders (used to create a shipment or add orders to one).
export default function OrderPicker({ open, title, actionLabel, onClose, onPick, exclude = [] }) {
  const { company, multiCompany } = useApp();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(new Set());
  const term = useDebounced(search.trim());
  const params = new URLSearchParams({ unshipped: "true", limit: "500", ...(multiCompany ? { company_id: company.id } : {}), ...(term ? { search: term } : {}) });
  const { data, loading } = useResource(open ? `/api/orders?${params}` : null);
  useEffect(() => { if (open) setSelected(new Set()); }, [open]);
  const rows = (data || []).filter((order) => !exclude.includes(order.id) && order.status !== "cancelled").map((order) => ({ ...order, label: order.order_number }));
  return (
    <Drawer open={open} title={title} subtitle="Orders that are not already in an active shipment." onClose={onClose} wide labelId="order-picker-title"
      footer={<button type="button" className="btn btn-primary" disabled={!selected.size} onClick={() => onPick([...selected])}><Truck size={15} />{actionLabel}{selected.size ? ` (${selected.size})` : ""}</button>}>
      <div className="list-toolbar"><SearchInput value={search} onChange={setSearch} placeholder="Search orders" autoFocus /></div>
      {loading && !data ? <Loading /> : <DataTable rows={rows} selectable selected={selected} onSelectedChange={setSelected} dense maxHeight="60vh"
        columns={[
          { key: "order_number", label: "Order", render: (order) => <b>{order.order_number}</b> },
          { key: "customer_name", label: "Customer" },
          { key: "zip", label: "ZIP", sort: (order) => order.ship_to?.postal_code, render: (order) => order.ship_to?.postal_code || "—" },
          { key: "unit_count", label: "Units", align: "right" },
          { key: "quotes", label: "Quotes", sort: (order) => order.quote_numbers?.length, render: (order) => order.quote_numbers?.at(-1) || <span className="muted">none</span> },
          { key: "status", label: "Status", render: (order) => <StatusBadge kind="order" status={order.status} /> },
        ]}
        empty={<EmptyState icon={ClipboardList} title="No unshipped orders">Import documents or add orders on the Orders page.</EmptyState>} />}
    </Drawer>
  );
}
