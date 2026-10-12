from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .api_freight import router as freight_router
from .api_workflow import router as workflow_router
from .database import PROJECT_ROOT, AliasConflictError, repository
from .edition import current as current_edition
from .models import (
    ItemMaster,
    MatchedLine,
    PackingRequest,
    PackingResult,
    PackingSettings,
    ShippingQuoteInfo,
    SkuAlias,
    SkuAliasCreate,
)
from .release import RELEASE
from .services.broker_handoff import (
    ActiveHandoffConflict,
    BridgeEvent,
    BrokerName,
    QuoteHandoffRequest,
    broker_handoffs,
    build_quote_packet,
)
from .services.freight import legacy_backfill, record_manual_quote, refresh_terms_from_source
from .services.imports import (
    parse_cabinet_colors,
    parse_item_master_with_report,
    parse_packing_slip_with_report,
)
from .services.packing_jobs import packing_jobs
from .services.reports import (
    item_master_template_xlsx,
    packing_result_csv,
    packing_result_json,
    packing_result_pdf,
    packing_result_xlsx,
)
from .services.shipments import build_shipment, match_request
from .static_frontend import FreshFrontend
from .workflow_models import ManualQuoteInput

log = logging.getLogger("quote_helper")
BUILD2QUOTE_EXTENSION_ID = "gnnpjdpaadkdbeceeheoiocbjccndlbb"
BUILD2QUOTE_EXTENSION_ORIGIN = f"chrome-extension://{BUILD2QUOTE_EXTENSION_ID}"


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.initialize()
    try:
        backfill = legacy_backfill(repository)
        if backfill["created"]:
            log.info("Recorded %s quotes from saved history", backfill["created"])
    except Exception:  # history backfill must never stop the app from starting
        log.exception("Quote history backfill failed; it will be retried on the next start")
    packing_jobs.activate()
    yield
    packing_jobs.shutdown()


app = FastAPI(
    title="Freight Quote Helper / Virtual Pallet Builder",
    version=RELEASE["version"],
    description="Safety-first cabinet pallet planning with auditable 3D coordinates.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        BUILD2QUOTE_EXTENSION_ORIGIN,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(freight_router)
app.include_router(workflow_router)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        **RELEASE,
        "item_master_count": repository.item_count(),
        "review_mode": os.environ.get("QUOTE_HELPER_REVIEW_MODE") == "1",
    }


@app.get("/api/settings", response_model=PackingSettings)
def get_settings() -> PackingSettings:
    return repository.get_settings()


@app.put("/api/settings", response_model=PackingSettings)
def save_settings(settings: PackingSettings) -> PackingSettings:
    repository.save_settings(settings)
    return settings


@app.get("/api/item-master", response_model=list[ItemMaster])
def list_item_master(search: str | None = None) -> list[ItemMaster]:
    return repository.list_items(search=search)


@app.post("/api/item-master", response_model=ItemMaster)
def upsert_item(item: ItemMaster) -> ItemMaster:
    repository.upsert_items([item])
    return item


@app.delete("/api/item-master/{item_id}")
def delete_item(item_id: int) -> dict:
    if not repository.delete_item(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    return {"deleted": True}


@app.get("/api/sku-aliases", response_model=list[SkuAlias])
def list_sku_aliases(search: str | None = None) -> list[SkuAlias]:
    return repository.list_sku_aliases(search=search)


@app.post("/api/sku-aliases", response_model=SkuAlias)
def save_sku_alias(alias: SkuAliasCreate) -> SkuAlias:
    try:
        return repository.save_sku_alias(
            alias.alias_sku,
            alias.target_item_id,
            ignored_prefixes=repository.get_settings().sku_prefixes_to_ignore,
        )
    except AliasConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/sku-aliases/{alias_id}")
def delete_sku_alias(alias_id: int) -> dict:
    if not repository.delete_sku_alias(alias_id):
        raise HTTPException(status_code=404, detail="SKU alias not found")
    return {"deleted": True}


@app.post("/api/item-master/import")
async def import_master(file: Annotated[UploadFile, File()]) -> dict:
    try:
        result = parse_item_master_with_report(file.filename or "item-master.xlsx", await file.read())
        inserted, updated = repository.upsert_items(result.items)
        return {
            "rows": len(result.items),
            "inserted": inserted,
            "updated": updated,
            "skipped_count": len(result.skipped),
            "skipped": result.skipped,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/item-master/replace/preview")
async def preview_master_replacement(file: Annotated[UploadFile, File()]) -> dict:
    try:
        filename = file.filename or "item-master.xlsx"
        result = parse_item_master_with_report(filename, await file.read())
        return {
            "filename": filename,
            **repository.preview_item_master_replacement(result.items),
            "skipped_count": len(result.skipped),
            "skipped": result.skipped,
            "can_replace": not result.skipped,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/item-master/replace")
async def replace_master(
    file: Annotated[UploadFile, File()],
    confirmation: Annotated[str, Form()],
) -> dict:
    if confirmation != "REPLACE":
        raise HTTPException(status_code=400, detail="Type REPLACE exactly to confirm the database reset")
    try:
        filename = file.filename or "item-master.xlsx"
        content = await file.read()
        result = parse_item_master_with_report(filename, content)
        if result.skipped:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Replacement was blocked because the workbook contains invalid or skipped rows",
                    "skipped_count": len(result.skipped),
                    "skipped": result.skipped,
                },
            )
        replacement = repository.replace_all_data(result.items, backup_retention=10)
        colors = parse_cabinet_colors(filename, content) if current_edition().name != "highland" else []
        if colors:   # the workbook's Cabinet Colors sheet replaces the color list too
            from .models import CabinetColor
            from .services.catalog import save_cabinet_colors

            save_cabinet_colors(repository, "default",
                                [CabinetColor(name=name, prefix=prefix) for name, prefix in colors])
        return {
            "filename": filename,
            "incoming_rows": len(result.items),
            "skipped_count": 0,
            **replacement,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/item-master/template")
def export_master_template(include_current: bool = Query(default=False)) -> Response:
    content = item_master_template_xlsx(repository.list_items() if include_current else None)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="quote-helper-v2-item-master.xlsx"'},
    )


@app.post("/api/import/packing-slips")
async def import_packing_slips(files: Annotated[list[UploadFile], File()]) -> dict:
    lines = []
    ignored_rows: list[dict] = []
    errors: list[dict] = []
    file_summaries: list[dict] = []
    shipping_suggestions: list[dict] = []
    documents: list[dict] = []
    suggested_names: list[str] = []
    for file in files:
        try:
            filename = file.filename or "packing-slip"
            result = parse_packing_slip_with_report(filename, await file.read())
            lines.extend(result.lines)
            ignored_rows.extend(result.ignored)
            shipping_suggestions.extend(result.shipping_suggestions)
            documents.extend(result.documents)
            if result.suggested_shipment_name:
                suggested_names.extend(result.suggested_shipment_name.split(" + "))
            file_summaries.append(
                {
                    "file": filename,
                    "accepted_count": len(result.lines),
                    "ignored_count": len(result.ignored),
                }
            )
        except ValueError as exc:
            errors.append({"file": file.filename, "error": str(exc)})
    if not lines:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "No physical item rows were imported",
                "ignored_rows": ignored_rows,
                "files": errors,
            },
        )
    return {
        "lines": [line.model_dump(mode="json") for line in lines],
        "ignored_rows": ignored_rows,
        "file_summaries": file_summaries,
        "accepted_count": len(lines),
        "ignored_count": len(ignored_rows),
        "errors": errors,
        "shipping_suggestions": shipping_suggestions,
        "documents": documents,
        "suggested_shipment_name": " + ".join(dict.fromkeys(suggested_names)) or None,
    }


@app.post("/api/shipments/match", response_model=list[MatchedLine])
def preview_matches(request: PackingRequest) -> list[MatchedLine]:
    return match_request(request, repository)


@app.post("/api/shipments/pack", response_model=PackingResult, response_model_exclude={"broker_rates"})
def pack_shipment(request: PackingRequest) -> PackingResult:
    try:
        return build_shipment(request, repository)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc).strip("'\"")) from exc


def _quote_source(request: QuoteHandoffRequest):
    return (
        repository.get_manual_quote(request.shipment_id)
        if request.source_kind == "manual"
        else repository.get_shipment(request.shipment_id)
    )


def _manual_response(quote):
    if not quote:
        raise HTTPException(404, "Manual quote not found")
    return {
        **quote.model_dump(mode="json", exclude={"broker_rates"}),
        "quote_packet": build_quote_packet(quote),
        "handoff_validation": broker_handoffs.preview(quote),
        "quote_history": repository.quote_history("manual", quote.id),
        "freight_quote": _freight_quote(manual_quote_id=quote.id),
    }


def _freight_quote(**source):
    from .services.freight import quote_view

    recorded = repository.quote_for_source(**source)
    return quote_view(repository, recorded) if recorded else None


@app.get("/api/manual-quotes")
def list_manual_quotes():
    return repository.list_manual_quotes()


@app.post("/api/manual-quotes/preview")
def preview_manual_quote(value: ManualQuoteInput):
    from .workflow_models import ManualQuoteShipment

    quote = ManualQuoteShipment(**value.model_dump())
    return {"quote_packet": build_quote_packet(quote), "handoff_validation": broker_handoffs.preview(quote)}


@app.post("/api/manual-quotes")
def create_manual_quote(value: ManualQuoteInput):
    quote = repository.save_manual_quote(value)
    record_manual_quote(repository, quote)
    return _manual_response(quote)


@app.get("/api/manual-quotes/{quote_id}")
def get_manual_quote(quote_id: str):
    return _manual_response(repository.get_manual_quote(quote_id))


@app.put("/api/manual-quotes/{quote_id}")
def update_manual_quote(quote_id: str, value: ManualQuoteInput):
    recorded = repository.quote_for_source(manual_quote_id=quote_id)
    if recorded and recorded.freight_shipment_id:
        shipment = repository.get_freight_shipment(recorded.freight_shipment_id)
        if shipment and shipment.combined_quote_id == recorded.id and shipment.status in ("closed", "cancelled"):
            raise HTTPException(409, f"These measurements are the combined quote of {shipment.number}, which is "
                                     f"{shipment.status}. Reopen the shipment before changing them.")
    try:
        quote = repository.save_manual_quote(value, quote_id)
        record_manual_quote(repository, quote)
        return _manual_response(quote)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/quote-history/{source_kind}/{source_id}")
def source_quote_history(source_kind: Literal["packed", "manual"], source_id: str):
    return repository.quote_history(source_kind, source_id)


@app.post("/api/quote-handoffs/preview")
def preview_quote_handoff(request: QuoteHandoffRequest) -> dict:
    result = _quote_source(request)
    if not result:
        raise HTTPException(status_code=404, detail="Saved shipment not found")
    return broker_handoffs.preview(result)


@app.post("/api/quote-handoffs/prepare-all")
def prepare_all_quote_handoffs(payload: QuoteHandoffRequest, request: Request) -> dict:
    result = _quote_source(payload)
    if not result:
        raise HTTPException(status_code=404, detail="Saved shipment not found")
    try:
        return {
            "handoffs": broker_handoffs.prepare_many(
                ("echoship", "tql", "dsv", "myunishippers", "odfl"),
                result,
                repository.save_quote_snapshots,
                bridge_origin=str(request.base_url).rstrip("/"),
                replace_active=payload.replace_active,
            )
        }
    except ActiveHandoffConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "active_brokers": exc.brokers},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/quote-handoffs/{broker}/prepare")
def prepare_quote_handoff(broker: BrokerName, payload: QuoteHandoffRequest, request: Request) -> dict:
    result = _quote_source(payload)
    if not result:
        raise HTTPException(status_code=404, detail="Saved shipment not found")
    try:
        return broker_handoffs.prepare_many(
            (broker,),
            result,
            repository.save_quote_snapshots,
            bridge_origin=str(request.base_url).rstrip("/"),
            replace_active=payload.replace_active,
        )[0]
    except ActiveHandoffConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "active_brokers": exc.brokers},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _require_build2quote_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    extension_id = request.headers.get("x-build2quote-extension-id")
    # MV3 service-worker fetches can omit Origin. In that case the stable,
    # manifest-key-derived extension ID is required in addition to the
    # expiring handoff token validated by the session manager.
    recognized = origin == BUILD2QUOTE_EXTENSION_ORIGIN or (
        origin in {None, "null"} and extension_id == BUILD2QUOTE_EXTENSION_ID
    )
    if not recognized:
        raise HTTPException(status_code=403, detail="Build2Quote Bridge origin was not recognized")


@app.get("/api/quote-handoffs/{request_id}/packet")
def quote_handoff_packet(request_id: str, request: Request, token: str = Query(min_length=20)) -> dict:
    _require_build2quote_origin(request)
    try:
        return broker_handoffs.packet(request_id, token)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/quote-handoffs/{request_id}/status")
def quote_handoff_status(request_id: str) -> dict:
    try:
        return broker_handoffs.status(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/quote-handoffs/{request_id}/events")
def quote_handoff_event(
    request_id: str,
    event: BridgeEvent,
    request: Request,
    token: str = Query(min_length=20),
) -> dict:
    _require_build2quote_origin(request)
    try:
        broker_handoffs.event(request_id, token, event)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/quote-handoffs/{request_id}/cancel")
def cancel_quote_handoff(request_id: str) -> dict:
    try:
        broker_handoffs.cancel(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"cancelled": True}


@app.get("/api/shipments")
def shipment_history(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    rows = [{**row, "source_kind": "packed"} for row in repository.list_shipments(limit)]
    rows.extend(repository.list_manual_quotes())
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)[:limit]


@app.get("/api/shipments/{shipment_id}", response_model=PackingResult, response_model_exclude={"broker_rates"})
def get_shipment(shipment_id: str) -> PackingResult:
    result = repository.get_shipment(shipment_id)
    if not result:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return result


@app.get("/api/shipments/{shipment_id}/request")
def saved_request(shipment_id: str):
    import json

    with repository.connect() as connection:
        row = connection.execute("SELECT request_json FROM shipments WHERE id=?", (shipment_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Shipment not found")
    return json.loads(row[0])


@app.put(
    "/api/shipments/{shipment_id}/shipping-info",
    response_model=PackingResult,
    response_model_exclude={"broker_rates"},
)
def update_shipment_shipping_info(shipment_id: str, shipping_info: ShippingQuoteInfo) -> PackingResult:
    result = repository.get_shipment(shipment_id)
    if not result:
        raise HTTPException(status_code=404, detail="Shipment not found")
    result.shipping_info = shipping_info
    repository.update_shipment_result(result)
    # The plan's quote keeps the delivery terms it is quoted with (until the customer approves it).
    refresh_terms_from_source(repository, shipping_info, packing_result_id=result.id)
    return result


@app.get("/api/shipments/{shipment_id}/export/{export_format}")
def export_shipment(
    shipment_id: str,
    export_format: Literal["pdf", "xlsx", "csv", "json"],
) -> Response:
    result = repository.get_shipment(shipment_id)
    if not result:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if not result.freight_ready or result.purpose == "prototype":
        raise HTTPException(
            status_code=422,
            detail=(
                "Blocked pallet plans cannot be exported as freight-ready reports; resolve every unpacked item first."
            ),
        )
    builders = {
        "pdf": (packing_result_pdf, "application/pdf"),
        "xlsx": (packing_result_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "csv": (packing_result_csv, "text/csv; charset=utf-8"),
        "json": (packing_result_json, "application/json"),
    }
    builder, media_type = builders[export_format]
    safe_name = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in result.shipment_name
    )
    return Response(
        builder(result),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-pallet-plan.{export_format}"'},
    )


@app.get("/api/engine")
def engine_information() -> dict:
    return {
        "production_engine": "warehouse-layered-safety-first",
        "replaceable_interface": True,
        "support_ratio": "configurable; default 75%",
        "candidate_strategies": 5,
        "near_level_support_tolerance": "configurable; default 0.45 in with dunnage warning",
        "bundling": "reviewed same-SKU accessories only; maximum 5 physical cartons",
        "post_pack_validation": True,
        "approximate": True,
        "human_review": "Required for fuzzy/manual items, warnings, and unpacked units; recommended for every release.",
    }


@app.post("/api/packing-jobs")
def start_packing_job(value: PackingRequest):
    try:
        return packing_jobs.start(value, repository)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc).strip("'\"")) from exc


@app.get("/api/packing-jobs/{job_id}")
def get_packing_job(job_id: str):
    try:
        return packing_jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Packing job not found; restart the build.") from exc


@app.post("/api/packing-jobs/{job_id}/cancel")
def cancel_packing_job(job_id: str):
    try:
        return packing_jobs.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Packing job not found") from exc


@app.post("/api/packing-jobs/{job_id}/refine")
def refine_packing_job(job_id: str, value: PackingRequest):
    try:
        return packing_jobs.start(value, repository, parent_id=job_id)
    except KeyError as exc:
        raise HTTPException(404, "Packing job not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/packing-jobs/{job_id}/accept", response_model=PackingResult)
def accept_packing_job(job_id: str, value: PackingRequest):
    try:
        return packing_jobs.accept(job_id, value, repository)
    except KeyError as exc:
        raise HTTPException(404, "Packing job not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


FRONTEND_DIST = Path(os.getenv("QUOTE_HELPER_FRONTEND_DIST", str(PROJECT_ROOT / "frontend" / "dist")))
if FRONTEND_DIST.exists():
    app.mount("/", FreshFrontend(directory=FRONTEND_DIST, html=True), name="frontend")
