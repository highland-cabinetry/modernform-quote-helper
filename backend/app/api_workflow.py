"""HTTP API for the 3.2 freight workflow (contract: docs/WORKFLOW.md §8).

Quote history, terms, price options, customer answer and approval; shipment actuals, extra revenue,
bills without a shipment; prepared emails; the needs-attention list; profit reports.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .api_freight import VoidRequest, _repository, handled
from .freight_models import (
    ActualMeasurementsInput,
    DeliveryTermsInput,
    EmailKind,
    PricingRule,
    QuoteOptionInput,
    ShipmentRevenueCreate,
    ShipmentRevenueInput,
    UnassignedExpenseCreate,
)
from .freight_repository import NotFoundError
from .services import attention as attention_service
from .services import emails, freight, profit_reports
from .services import quote_workflow as workflow

router = APIRouter(prefix="/api")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OptionCreate(QuoteOptionInput):
    select: bool = Field(default=False, description="Also make this the selected price option")


class OptionSelection(Strict):
    option_id: str | None = None


class CustomerPrice(Strict):
    cents: int | None = Field(default=None, ge=0, le=100_000_000)


class AnswerNote(Strict):
    note: str = Field(default="", max_length=1000)


class Approval(Strict):
    invoice_numbers: list[str] = Field(default_factory=list, max_length=20)
    order_invoices: dict[str, list[str]] = Field(default_factory=dict,
                                                 description="order id -> invoice numbers for that order only")
    note: str = Field(default="", max_length=1000)


class PricingPreview(Strict):
    company_id: str | None = None
    carrier_cents: int = Field(ge=0, le=100_000_000)
    pricing: PricingRule | None = Field(default=None, description="Unsaved rule to try; default: the company's")


class ShipmentLink(Strict):
    shipment_id: str


class EmailDraftRequest(Strict):
    kind: EmailKind
    quote_id: str | None = None
    shipment_id: str | None = None


class EmailLogRequest(Strict):
    kind: EmailKind
    entity_type: Literal["quote", "shipment", "order"]
    entity_id: str
    to: list[str] = Field(default_factory=list, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(default="", max_length=300)


def _company_id(company_id: str | None) -> str | None:
    """Accept a company id or code; None means every company."""

    return _repository().get_company(company_id).id if company_id else None


def _quote(quote) -> dict:
    return workflow.quote_detail(_repository(), quote)


def _shipment_view(shipment_id: str) -> dict:
    repo = _repository()
    shipment = repo.get_freight_shipment(shipment_id)
    if shipment is None:
        raise NotFoundError("Shipment not found")
    return freight.shipment_view(repo, shipment)


# ------------------------------------------------------------------ quote history and pricing


@router.get("/orders/{order_id}/quote-history")
@handled
def order_quote_history(order_id: str) -> dict:
    return workflow.quote_history(_repository(), order_id)


@router.post("/quotes/pricing-preview")
@handled
def pricing_preview(value: PricingPreview) -> dict:
    return workflow.pricing_preview(_repository(), value.company_id, value.carrier_cents, value.pricing)


@router.get("/quotes/{quote_id}/detail")
@handled
def quote_detail(quote_id: str) -> dict:
    return _quote(workflow.load_quote(_repository(), quote_id))


@router.put("/quotes/{quote_id}/terms")
@handled
def set_terms(quote_id: str, value: DeliveryTermsInput) -> dict:
    return _quote(workflow.set_terms(_repository(), quote_id, value))


@router.post("/quotes/{quote_id}/options")
@handled
def add_option(quote_id: str, value: OptionCreate) -> dict:
    option = QuoteOptionInput(**value.model_dump(exclude={"select"}))
    return _quote(workflow.add_option(_repository(), quote_id, option, select=value.select))


@router.put("/quotes/{quote_id}/options/{option_id}")
@handled
def edit_option(quote_id: str, option_id: str, value: QuoteOptionInput) -> dict:
    return _quote(workflow.edit_option(_repository(), quote_id, option_id, value))


@router.delete("/quotes/{quote_id}/options/{option_id}")
@handled
def remove_option(quote_id: str, option_id: str) -> dict:
    return _quote(workflow.remove_option(_repository(), quote_id, option_id))


@router.post("/quotes/{quote_id}/select")
@handled
def select_option(quote_id: str, value: OptionSelection) -> dict:
    return _quote(workflow.select_option(_repository(), quote_id, value.option_id))


@router.put("/quotes/{quote_id}/customer-price")
@handled
def customer_price(quote_id: str, value: CustomerPrice) -> dict:
    return _quote(workflow.set_customer_price(_repository(), quote_id, value.cents))


# ------------------------------------------------------------------ customer answer


@router.post("/quotes/{quote_id}/sent")
@handled
def mark_sent(quote_id: str, value: AnswerNote | None = None) -> dict:
    return _quote(workflow.mark_sent(_repository(), quote_id, value.note if value else ""))


@router.post("/quotes/{quote_id}/approve")
@handled
def approve(quote_id: str, value: Approval | None = None) -> dict:
    value = value or Approval()
    repo = _repository()
    quote, shipment, created = workflow.approve(repo, quote_id, invoice_numbers=value.invoice_numbers,
                                                order_invoices=value.order_invoices, note=value.note)
    return {"quote": _quote(quote), "shipment": freight.shipment_view(repo, shipment), "shipment_created": created}


@router.post("/quotes/{quote_id}/decline")
@handled
def decline(quote_id: str, value: AnswerNote | None = None) -> dict:
    return _quote(workflow.decline(_repository(), quote_id, value.note if value else ""))


@router.post("/quotes/{quote_id}/expire")
@handled
def expire(quote_id: str, value: AnswerNote | None = None) -> dict:
    return _quote(workflow.expire(_repository(), quote_id, value.note if value else ""))


@router.post("/quotes/{quote_id}/reopen")
@handled
def reopen(quote_id: str, value: AnswerNote | None = None) -> dict:
    return _quote(workflow.reopen(_repository(), quote_id, value.note if value else ""))


# ------------------------------------------------------------------ shipment actuals and revenue


@router.put("/freight-shipments/{shipment_id}/actual")
@handled
def record_actuals(shipment_id: str, value: ActualMeasurementsInput) -> dict:
    workflow.record_actuals(_repository(), shipment_id, value)
    return _shipment_view(shipment_id)


@router.delete("/freight-shipments/{shipment_id}/actual")
@handled
def clear_actuals(shipment_id: str) -> dict:
    workflow.record_actuals(_repository(), shipment_id, None)
    return _shipment_view(shipment_id)


@router.get("/freight-shipments/{shipment_id}/revenue")
@handled
def shipment_revenue(shipment_id: str) -> list[dict]:
    repo = _repository()
    if repo.get_freight_shipment(shipment_id) is None:
        raise NotFoundError("Shipment not found")
    return [r.model_dump(mode="json") for r in repo.list_revenue(shipment_id=shipment_id)]


@router.post("/freight-shipments/{shipment_id}/revenue")
@handled
def add_shipment_revenue(shipment_id: str, value: ShipmentRevenueInput) -> dict:
    workflow.add_revenue(_repository(), shipment_id, value)
    return _shipment_view(shipment_id)


@router.post("/freight-shipments/{shipment_id}/revenue/{revenue_id}/void")
@handled
def void_shipment_revenue(shipment_id: str, revenue_id: str, value: VoidRequest) -> dict:
    workflow.void_revenue(_repository(), revenue_id, value.reason, shipment_id=shipment_id)
    return _shipment_view(shipment_id)


@router.get("/revenue")
@handled
def list_revenue(company_id: str | None = None, unassigned: bool = True, include_voided: bool = False) -> list[dict]:
    return [r.model_dump(mode="json") for r in _repository().list_revenue(
        company_id=_company_id(company_id), unassigned=unassigned, include_voided=include_voided)]


@router.post("/revenue")
@handled
def add_revenue(value: ShipmentRevenueCreate) -> dict:
    payload = ShipmentRevenueInput(**value.model_dump(exclude={"company_id", "shipment_id"}))
    return workflow.add_company_revenue(_repository(), value.company_id, payload,
                                        shipment_id=value.shipment_id).model_dump(mode="json")


@router.post("/revenue/{revenue_id}/assign")
@handled
def assign_revenue(revenue_id: str, value: ShipmentLink) -> dict:
    workflow.assign_revenue(_repository(), revenue_id, value.shipment_id)
    return _shipment_view(value.shipment_id)


@router.post("/revenue/{revenue_id}/void")
@handled
def void_revenue(revenue_id: str, value: VoidRequest) -> dict:
    return workflow.void_revenue(_repository(), revenue_id, value.reason).model_dump(mode="json")


# ------------------------------------------------------------------ bills without a shipment


@router.get("/unassigned-expenses")
@handled
def list_unassigned(company_id: str | None = None, include_voided: bool = False) -> list[dict]:
    return [e.model_dump(mode="json") for e in _repository().list_unassigned_expenses(
        company_id=_company_id(company_id), include_voided=include_voided)]


@router.post("/unassigned-expenses")
@handled
def add_unassigned(value: UnassignedExpenseCreate) -> dict:
    return workflow.add_unassigned_expense(_repository(), value.company_id, value).model_dump(mode="json")


@router.post("/unassigned-expenses/{expense_id}/assign")
@handled
def assign_unassigned(expense_id: str, value: ShipmentLink) -> dict:
    workflow.assign_unassigned_expense(_repository(), expense_id, value.shipment_id)
    return _shipment_view(value.shipment_id)


@router.post("/unassigned-expenses/{expense_id}/void")
@handled
def void_unassigned(expense_id: str, value: VoidRequest) -> dict:
    return workflow.void_unassigned_expense(_repository(), expense_id, value.reason).model_dump(mode="json")


# ------------------------------------------------------------------ prepared emails


@router.post("/emails/draft")
@handled
def email_draft(value: EmailDraftRequest) -> dict:
    return emails.draft(_repository(), value.kind, quote_id=value.quote_id, shipment_id=value.shipment_id)


@router.post("/emails/log")
@handled
def email_log(value: EmailLogRequest) -> dict:
    return emails.log_email(_repository(), **value.model_dump()).model_dump(mode="json")


@router.get("/emails")
@handled
def email_records(entity_type: str | None = None, entity_id: str | None = None, company_id: str | None = None,
                  limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    return [r.model_dump(mode="json") for r in _repository().list_email_records(
        entity_type=entity_type, entity_id=entity_id, company_id=_company_id(company_id), limit=limit)]


@router.get("/emails/templates")
@handled
def email_templates(company_id: str | None = None) -> dict:
    return emails.template_catalog(_repository().get_company(company_id))


# ------------------------------------------------------------------ attention and reports


@router.get("/attention")
@handled
def attention(company_id: str | None = None) -> dict:
    return attention_service.attention(_repository(), _company_id(company_id))


def _report(company_id: str | None, start: date | None, end: date | None, group: str,
            exclude_sister: bool) -> dict[str, Any]:
    return profit_reports.summary(_repository(), company_id=_company_id(company_id), start=start, end=end, group=group,
                                  exclude_sister=exclude_sister)


ReportGroup = Literal["month", "rep", "carrier", "purpose", "customer"]


@router.get("/reports/summary")
@handled
def report_summary(company_id: str | None = None, start: Annotated[date | None, Query(alias="from")] = None,
                   end: Annotated[date | None, Query(alias="to")] = None, group: ReportGroup = "month",
                   exclude_sister: bool = False) -> dict:
    return _report(company_id, start, end, group, exclude_sister)


@router.get("/reports/summary.csv")
@handled
def report_summary_csv(company_id: str | None = None, start: Annotated[date | None, Query(alias="from")] = None,
                       end: Annotated[date | None, Query(alias="to")] = None, group: ReportGroup = "month",
                       exclude_sister: bool = False) -> Response:
    report = _report(company_id, start, end, group, exclude_sister)
    return Response(profit_reports.summary_csv(report), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="freight-profit-by-{group}.csv"'})
