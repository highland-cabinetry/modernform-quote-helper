"""HTTP API for companies, imports, orders, quotes, shipments, expenses and the catalog."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .freight_models import (
    AllocationRequest,
    Company,
    CompanyInput,
    FreightShipmentCreate,
    FreightShipmentInput,
    Order,
    OrderInput,
    QuoteCreate,
    QuoteUpdate,
    ShipmentExpenseInput,
)
from .freight_repository import ConflictError, NotFoundError
from .models import ItemMaster, PackingSettings, SkuAliasCreate
from .services import freight, profit_reports
from .services.adapters import adapter_catalog
from .services.adapters.base import canonical_reference
from .services.catalog import cabinet_colors, catalog_imports, catalog_report, export_catalog, item_issues
from .services.company_rules import describe_rule_sets
from .services.order_import import OrderImporter, dry_run, packing_lines_for_orders, refresh_order_issues

router = APIRouter(prefix="/api")


def _repository():
    from . import main

    return main.repository


def _http_error(exc: Exception) -> HTTPException | None:
    if isinstance(exc, ConflictError):
        return HTTPException(409, str(exc))
    if isinstance(exc, NotFoundError):
        return HTTPException(404, str(exc).strip("'\""))
    if isinstance(exc, ValueError):
        return HTTPException(422, str(exc))
    return None


def handled(function: Callable) -> Callable:
    """Translate domain errors into consistent HTTP responses.

    Synchronous handlers stay synchronous so FastAPI runs them in its thread pool; database
    waits and document parsing never block the event loop (health checks, packing-job polls).
    """

    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def async_wrapper(*args, **kwargs):
            try:
                return await function(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as exc:
                error = _http_error(exc)
                if error is None:
                    raise
                raise error from exc

        return async_wrapper

    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            error = _http_error(exc)
            if error is None:
                raise
            raise error from exc

    return wrapper


# ------------------------------------------------------------------ companies & formats


@router.get("/companies")
@handled
def list_companies() -> list[dict]:
    repo = _repository()
    default_id = repo.default_company().id
    return [{**c.model_dump(mode="json"), "is_default": c.id == default_id} for c in repo.list_companies()]


@router.post("/companies")
@handled
def create_company(value: CompanyInput) -> dict:
    return _repository().save_company(Company(**value.model_dump())).model_dump(mode="json")


@router.put("/companies/{company_id}")
@handled
def update_company(company_id: str, value: CompanyInput) -> dict:
    repo = _repository()
    current = repo.get_company(company_id)
    company = Company(**value.model_dump(), id=current.id, created_at=current.created_at)
    return repo.save_company(company).model_dump(mode="json")


@router.get("/companies/{company_id}/settings", response_model=PackingSettings)
@handled
def company_settings(company_id: str) -> PackingSettings:
    repo = _repository()
    return repo.company_settings(repo.get_company(company_id))


@router.put("/companies/{company_id}/settings", response_model=PackingSettings)
@handled
def save_company_settings(company_id: str, settings: PackingSettings) -> PackingSettings:
    repo = _repository()
    company = repo.get_company(company_id)
    saved = repo.save_company_settings(company, settings)
    with repo.connect() as connection:
        repo.audit(connection, "company", company.id, "settings_saved", "Packing settings saved")
    return saved


@router.get("/import-formats")
def import_formats() -> dict:
    return {"adapters": adapter_catalog(), "rule_sets": describe_rule_sets()}


# ------------------------------------------------------------------ imports


@router.post("/imports")
@handled
async def import_documents(
    files: Annotated[list[UploadFile], File()],
    company_id: Annotated[str | None, Form()] = None,
) -> dict:
    repo = _repository()
    company = repo.get_company(company_id)
    uploads = [(file.filename or "document", await file.read()) for file in files]
    return await run_in_threadpool(OrderImporter(repo, company).import_files, uploads)


@router.post("/imports/test")
@handled
async def test_import(
    files: Annotated[list[UploadFile], File()],
    company_id: Annotated[str | None, Form()] = None,
    config_json: Annotated[str | None, Form()] = None,
) -> dict:
    """Read sample documents with a company's settings (optionally unsaved) without storing anything."""

    from .freight_models import CompanyConfig

    company = _repository().get_company(company_id)
    if config_json:
        company = company.model_copy(update={"config": CompanyConfig.model_validate_json(config_json)})
    uploads = [(file.filename or "document", await file.read()) for file in files]
    return await run_in_threadpool(dry_run, company, uploads)


@router.get("/source-files/{source_id}")
@handled
def download_source_file(source_id: str):
    repo = _repository()
    source = repo.get_source_file(source_id)
    if not source or not source.stored_path or not Path(source.stored_path).exists():
        raise HTTPException(404, "The original file is not available")
    return FileResponse(source.stored_path, filename=Path(source.filename).name)


# ------------------------------------------------------------------ orders


class OrderCreate(OrderInput):
    company_id: str | None = None


class OrderCorrection(OrderInput):
    reason: str = Field(default="Corrected in review", max_length=300)


class OrderStatusChange(BaseModel):
    status: Literal["needs_review", "ready", "reference_only", "cancelled"]
    reason: str = Field(min_length=1, max_length=300)


class OrderSelection(BaseModel):
    order_ids: list[str] = Field(min_length=1)


@router.get("/orders")
@handled
def list_orders(search: str | None = None, status: str | None = None, company_id: str | None = None,
                unshipped: bool = False, limit: int = Query(default=200, ge=1, le=2000)) -> list[dict]:
    return _repository().list_orders(company_id=company_id, search=search, status=status, limit=limit,
                                     unshipped_only=unshipped)


@router.post("/orders")
@handled
def create_order(value: OrderCreate) -> dict:
    repo = _repository()
    company = repo.get_company(value.company_id)
    number = canonical_reference(value.order_number, company.config.bare_number_prefix)
    if not number:
        raise ValueError("Enter an order number with letters or digits")
    order = Order(company_id=company.id, **value.model_dump(exclude={"company_id", "order_number"}),
                  order_number=number, status="ready" if value.lines else "reference_only", document_type="entered")
    if not order.lines:
        order.issues = ["Reference only: no packing slip has been imported."]
    with repo.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if repo.find_order_by_reference(connection, company.id, [order.order_number, *order.references]):
            raise ConflictError(f"Order {order.order_number} already exists")
        repo.insert_order(connection, order, "Entered by hand")
    return order.model_dump(mode="json")


@router.get("/orders/{order_id}")
@handled
def get_order(order_id: str) -> dict:
    repo = _repository()
    order = repo.get_order(order_id)
    if not order:
        raise NotFoundError("Order not found")
    return freight.order_trace(repo, order)


@router.put("/orders/{order_id}")
@handled
def correct_order(order_id: str, value: OrderCorrection) -> dict:
    repo = _repository()
    current = repo.get_order(order_id)
    if not current:
        raise NotFoundError("Order not found")
    data = value.model_dump(exclude={"reason"})
    order = Order.model_validate({**current.model_dump(), **data})
    if order.lines and order.lines != current.lines:
        refresh_order_issues(repo, repo.get_company(order.company_id), order)
    if order.status in ("needs_review", "reference_only") and order.lines:
        # The user has reviewed and saved the lines; remaining catalog issues stay listed and
        # still block a freight-ready plan until the SKUs resolve.
        order.status = "ready"
        order.issues = [issue for issue in order.issues if not issue.startswith("Reference only")]
    return repo.update_order(order, reason=value.reason).model_dump(mode="json")


@router.post("/orders/{order_id}/status")
@handled
def change_order_status(order_id: str, value: OrderStatusChange) -> dict:
    repo = _repository()
    current = repo.get_order(order_id)
    if not current:
        raise NotFoundError("Order not found")
    if current.status == "shipped":
        # Only reopening its closed shipment may unlock a shipped order.
        raise ConflictError("This order shipped in a closed shipment; reopen the shipment to change it")
    with repo.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if value.status == "cancelled":
            busy = repo.active_shipment_for_orders(connection, [order_id])
            if busy:
                raise ConflictError(f"Remove the order from shipment {next(iter(busy.values()))} first")
        repo.set_order_status(connection, order_id, value.status, value.reason)
    return repo.get_order(order_id).model_dump(mode="json")


@router.post("/orders/packing-lines")
@handled
def packing_lines(value: OrderSelection) -> dict:
    repo = _repository()
    if len(set(value.order_ids)) != len(value.order_ids):
        raise ConflictError("An order can be included only once")
    orders = repo.get_orders(value.order_ids)
    lines, warnings = packing_lines_for_orders(orders)
    company = repo.get_company(orders[0].company_id)
    zips = [o.ship_to.postal_code for o in orders if o.ship_to.postal_code]
    return {
        "company_id": company.id,
        "lines": [line.model_dump(mode="json") for line in lines],
        "warnings": warnings,
        "suggested_name": " + ".join(o.order_number for o in orders),
        "shipping": {
            "customer_references": [ref for o in orders for ref in dict.fromkeys([o.order_number, *o.references])],
            "destination": orders[0].ship_to.model_dump() if len(set(zips)) == 1 and zips else {},
            "residential": any(o.residential for o in orders),
            "liftgate": any(o.liftgate for o in orders),
        },
        "settings": repo.company_settings(company).model_dump(mode="json"),
    }


# ------------------------------------------------------------------ quotes


@router.get("/quotes")
@handled
def list_quotes(search: str | None = None, status: str | None = None, kind: str | None = None,
                order_id: str | None = None, company_id: str | None = None,
                limit: int = Query(default=300, ge=1, le=5000)) -> list[dict]:
    return freight.list_quote_views(_repository(), search=search, status=status, kind=kind, order_id=order_id,
                                    company_id=company_id, limit=limit)


@router.post("/quotes")
@handled
def create_quote(value: QuoteCreate) -> dict:
    repo = _repository()
    return freight.quote_view(repo, freight.record_quote(repo, value))


@router.get("/quotes/compare")
@handled
def compare_quotes(ids: str) -> list[dict]:
    repo = _repository()
    quotes = [repo.get_quote(value) for value in ids.split(",") if value.strip()]
    if any(q is None for q in quotes):
        raise NotFoundError("One or more quotes were not found")
    return [freight.quote_view(repo, q) for q in quotes]


@router.get("/quotes/for-source")
@handled
def quote_for_source(packing_result_id: str | None = None, manual_quote_id: str | None = None) -> dict | None:
    repo = _repository()
    quote = repo.quote_for_source(packing_result_id=packing_result_id, manual_quote_id=manual_quote_id)
    return freight.quote_view(repo, quote) if quote else None


@router.get("/quotes/{quote_id}")
@handled
def get_quote(quote_id: str) -> dict:
    repo = _repository()
    quote = repo.get_quote(quote_id)
    if not quote:
        raise NotFoundError("Quote not found")
    view = freight.quote_view(repo, quote)
    shipment = repo.get_freight_shipment(quote.freight_shipment_id) if quote.freight_shipment_id else None
    view["shipment"] = {"id": shipment.id, "number": shipment.number, "status": shipment.status} if shipment else None
    view["source_available"] = bool(
        repo.get_shipment(quote.packing_result_id)
        if quote.packing_result_id
        else repo.get_manual_quote(quote.manual_quote_id)
    )
    view["history"] = [entry.model_dump(mode="json") for entry in repo.audit_trail("quote", quote.id)]
    return view


@router.put("/quotes/{quote_id}")
@handled
def update_quote(quote_id: str, value: QuoteUpdate) -> dict:
    repo = _repository()
    return freight.quote_view(repo, freight.update_quote(repo, quote_id, value))


# ------------------------------------------------------------------ shipments


class OrderChange(BaseModel):
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class QuoteLink(BaseModel):
    quote_id: str


class VoidRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


@router.get("/freight-shipments")
@handled
def list_shipments(search: str | None = None, status: str | None = None, company_id: str | None = None) -> list[dict]:
    repo = _repository()
    shipments = repo.list_freight_shipments(company_id=company_id, status=status, search=search)
    numbers = repo.order_numbers(oid for s in shipments for oid in s.order_ids)
    with repo.connect() as connection:
        totals = dict(connection.execute(
            "SELECT shipment_id, SUM(amount_cents) FROM shipment_expenses WHERE voided=0 GROUP BY shipment_id"))
    return [
        {**s.model_dump(mode="json"), "order_numbers": [numbers.get(o, o) for o in s.order_ids],
         "expense_total_cents": totals.get(s.id)}
        for s in shipments
    ]


@router.post("/freight-shipments")
@handled
def create_shipment(value: FreightShipmentCreate) -> dict:
    repo = _repository()
    return freight.shipment_view(repo, freight.create_shipment(repo, value))


@router.get("/freight-shipments/{shipment_id}")
@handled
def get_shipment(shipment_id: str) -> dict:
    repo = _repository()
    shipment = repo.get_freight_shipment(shipment_id)
    if not shipment:
        raise NotFoundError("Shipment not found")
    return freight.shipment_view(repo, shipment)


@router.put("/freight-shipments/{shipment_id}")
@handled
def update_shipment(shipment_id: str, value: FreightShipmentInput, reason: str = "") -> dict:
    repo = _repository()
    return freight.shipment_view(repo, freight.update_shipment(repo, shipment_id, value, reason=reason))


@router.post("/freight-shipments/{shipment_id}/orders")
@handled
def change_orders(shipment_id: str, value: OrderChange) -> dict:
    repo = _repository()
    return freight.shipment_view(repo, freight.change_shipment_orders(repo, shipment_id, add=value.add,
                                                                      remove=value.remove))


@router.post("/freight-shipments/{shipment_id}/combined-quote")
@handled
def link_quote(shipment_id: str, value: QuoteLink) -> dict:
    repo = _repository()
    return freight.shipment_view(repo, freight.link_combined_quote(repo, shipment_id, value.quote_id))


@router.post("/freight-shipments/{shipment_id}/expenses")
@handled
def add_expense(shipment_id: str, value: ShipmentExpenseInput) -> dict:
    repo = _repository()
    freight.add_expense(repo, shipment_id, value)
    return freight.shipment_view(repo, repo.get_freight_shipment(shipment_id))


@router.post("/freight-shipments/{shipment_id}/expenses/{expense_id}/void")
@handled
def void_expense(shipment_id: str, expense_id: str, value: VoidRequest) -> dict:
    repo = _repository()
    freight.void_expense(repo, shipment_id, expense_id, value.reason)
    return freight.shipment_view(repo, repo.get_freight_shipment(shipment_id))


@router.post("/freight-shipments/{shipment_id}/allocation")
@handled
def allocate(shipment_id: str, value: AllocationRequest) -> dict:
    repo = _repository()
    freight.allocate(repo, shipment_id, value)
    return freight.shipment_view(repo, repo.get_freight_shipment(shipment_id))


# ------------------------------------------------------------------ catalog


class CatalogItemPayload(BaseModel):
    item: ItemMaster
    reason: str = Field(default="edited in Product Catalog", max_length=200)


class PreviewApply(BaseModel):
    preview_id: str


def _catalog_prefixes(repo, catalog: str) -> list[str]:
    company = next((c for c in repo.list_companies() if c.config.catalog == catalog), None) or repo.default_company()
    return repo.company_settings(company).sku_prefixes_to_ignore


@router.get("/catalogs")
@handled
def list_catalogs() -> list[dict]:
    repo = _repository()
    companies = repo.list_companies()
    return [
        {"name": name, "item_count": len(repo.catalog_items(name)),
         "companies": [c.code for c in companies if c.config.catalog == name]}
        for name in repo.catalog_names()
    ]


@router.get("/catalog/{catalog}/items")
@handled
def catalog_items(catalog: str, search: str | None = None, include_inactive: bool = False) -> list[dict]:
    items = _repository().catalog_items(catalog, active_only=not include_inactive, search=search)
    return [{**item.model_dump(mode="json"), "issues": item_issues(item)} for item in items]


@router.post("/catalog/{catalog}/items")
@handled
def save_catalog_item(catalog: str, value: CatalogItemPayload) -> dict:
    repo = _repository()
    item = value.item
    if item.id is not None:
        current = repo.catalog_item(catalog, item.id)
        if not current:
            raise NotFoundError("Item not found")
        if current.sku.upper() != item.sku.upper():
            clash = [i for i in repo.catalog_items(catalog, active_only=False) if i.sku.upper() == item.sku.upper()]
            if clash:
                raise ConflictError(f"SKU {item.sku} already exists")
            raise ConflictError("Changing a SKU is not supported; add the new SKU and deactivate the old one, "
                                "or save the old SKU as an alias")
    repo.catalog_upsert(catalog, [item], reason=value.reason)
    return {**item.model_dump(mode="json"), "issues": item_issues(item)}


@router.delete("/catalog/{catalog}/items/{item_id}")
@handled
def deactivate_catalog_item(catalog: str, item_id: int) -> dict:
    if not _repository().catalog_deactivate(catalog, item_id):
        raise NotFoundError("Item not found")
    return {"deactivated": True}


@router.get("/catalog/{catalog}/items/{item_id}/history")
@handled
def catalog_item_history(catalog: str, item_id: int) -> list[dict]:
    repo = _repository()
    item = repo.catalog_item(catalog, item_id)
    if not item:
        raise NotFoundError("Item not found")
    return [e.model_dump(mode="json") for e in repo.audit_trail("catalog_item", f"{catalog}:{item.sku.upper()}")]


@router.get("/catalog/{catalog}/report")
@handled
def catalog_validation(catalog: str) -> dict:
    repo = _repository()
    return catalog_report(repo.catalog_items(catalog), _catalog_prefixes(repo, catalog))


@router.post("/catalog/{catalog}/import/preview")
@handled
async def catalog_import_preview(catalog: str, file: Annotated[UploadFile, File()]) -> dict:
    repo = _repository()
    content = await file.read()
    return await run_in_threadpool(catalog_imports.preview, repo, catalog, file.filename or "catalog.xlsx", content,
                                   _catalog_prefixes(repo, catalog))


@router.post("/catalog/{catalog}/import/apply")
@handled
def catalog_import_apply(catalog: str, value: PreviewApply) -> dict:
    return catalog_imports.apply(_repository(), value.preview_id)


@router.get("/catalog/{catalog}/export/{export_format}")
@handled
def catalog_export(catalog: str, export_format: Literal["xlsx", "csv"], include_inactive: bool = False) -> Response:
    repo = _repository()
    content, media_type, suffix = export_catalog(
        repo.catalog_items(catalog, active_only=not include_inactive), export_format, cabinet_colors(repo, catalog))
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="catalog-{catalog}.{suffix}"'})


@router.get("/catalog/{catalog}/aliases")
@handled
def catalog_aliases(catalog: str, search: str | None = None) -> list[dict]:
    return [a.model_dump() for a in _repository().catalog_aliases(catalog, search=search)]


@router.post("/catalog/{catalog}/aliases")
@handled
def save_catalog_alias(catalog: str, value: SkuAliasCreate) -> dict:
    repo = _repository()
    return repo.catalog_save_alias(catalog, value.alias_sku, value.target_item_id,
                                   ignored_prefixes=_catalog_prefixes(repo, catalog)).model_dump()


@router.delete("/catalog/{catalog}/aliases/{alias_id}")
@handled
def delete_catalog_alias(catalog: str, alias_id: int) -> dict:
    if not _repository().catalog_delete_alias(catalog, alias_id):
        raise NotFoundError("Alias not found")
    return {"deleted": True}


# ------------------------------------------------------------------ freight data, dashboard, audit


@router.get("/dashboard")
@handled
def dashboard(company_id: str | None = None) -> dict:
    return freight.dashboard(_repository(), company_id)


@router.get("/freight-records")
@handled
def freight_records(company_id: str | None = None) -> list[dict]:
    repo = _repository()
    return profit_reports.freight_records(repo, repo.get_company(company_id).id if company_id else None)


@router.get("/freight-records/export.csv")
@handled
def freight_records_csv(company_id: str | None = None) -> Response:
    repo = _repository()
    rows = profit_reports.freight_records(repo, repo.get_company(company_id).id if company_id else None)
    return Response(profit_reports.freight_records_csv(rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="freight-records.csv"'})


@router.get("/audit")
@handled
def audit(entity_type: str, entity_id: str) -> list[dict]:
    return [e.model_dump(mode="json") for e in _repository().audit_trail(entity_type, entity_id)]
