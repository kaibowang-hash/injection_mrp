from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, now_datetime, today

from injection_mrp.services import stock_buffer


FIRM_APS_STATUSES = ("Approved", "Work Order Proposed", "Shift Proposed", "Applied")
OPEN_DOC_STATUSES = ("Closed", "Stopped", "Cancelled")
SUPPLY_MODES_REQUIRING_MR = {
	"Purchase",
	"Manufacture",
	"Subcontracting",
	"Customer Provided",
	"Material Transfer",
}
MR_TYPE_BY_SUPPLY_MODE = {
	"Purchase": "Purchase",
	"Manufacture": "Manufacture",
	"Subcontracting": "Subcontracting",
	"Customer Provided": "Customer Provided",
	"Material Transfer": "Material Transfer",
}
MRP_JOB_QUEUE = "long"
MRP_JOB_TIMEOUT = 3600
MRP_ACTIVE_STATUSES = {"Queued", "Running"}
PROPOSAL_ACTIVE_STATUSES = {"Draft", "Ready"}
PROPOSAL_RELEASEABLE_STATUS = "Ready"
PROPOSAL_VALIDATION_TOLERANCE = 0.000001


@dataclass
class DemandRow:
	demand_type: str
	item_code: str
	qty: float
	required_date: str | None
	company: str
	warehouse: str | None = None
	customer: str | None = None
	source_doctype: str | None = None
	source_name: str | None = None
	source_row: str | None = None
	aps_run: str | None = None
	aps_result: str | None = None
	linked_sales_order: str | None = None
	notes: str | None = None


@dataclass
class RequirementCandidate:
	demand_snapshot: str
	demand_item_code: str
	item_code: str
	item_name: str | None
	uom: str | None
	warehouse: str | None
	required_date: str | None
	gross_qty: float
	scrap_qty: float
	bom: str | None
	bom_item: str | None
	bom_level: int
	requirement_type: str
	bom_trace: list[dict[str, Any]] = field(default_factory=list)
	supply_mode: str | None = None
	material_request_type: str | None = None
	source_warehouse: str | None = None
	supplier: str | None = None
	customer: str | None = None
	parent_requirement: str | None = None
	sourced_by_supplier: int = 0
	purchase_uom: str | None = None
	min_order_qty: float = 0
	order_multiple_qty: float = 0
	supplier_lead_time_days: int = 0
	supplier_quotation: str | None = None
	item_price: str | None = None
	estimated_rate: float = 0
	estimated_amount: float = 0
	currency: str | None = None
	procurement_source: str | None = None
	procurement_constraint_summary: str | None = None
	missing_bom: int = 0
	stock_buffer: str | None = None
	buffer_priority: str | None = None
	buffer_nfp_percent: float = 0
	buffer_recommended_qty: float = 0
	buffer_top_of_red: float = 0
	buffer_top_of_yellow: float = 0
	buffer_top_of_green: float = 0
	buffer_lead_time_days: int = 0


@dataclass
class SupplyRecord:
	supply_type: str
	item_code: str
	company: str
	warehouse: str | None
	original_qty: float
	remaining_qty: float
	supply_date: str | None
	expected_arrival_date: str | None
	priority: int
	source_doctype: str | None = None
	source_name: str | None = None
	source_row: str | None = None
	commitment_type: str | None = None


def _warehouse_defaults_from_rows(rows) -> frappe._dict:
	defaults = frappe._dict()
	for row in rows or []:
		supply_mode = (row.get("supply_mode") if hasattr(row, "get") else None) or getattr(row, "supply_mode", None)
		if not supply_mode or supply_mode in defaults:
			continue
		defaults[supply_mode] = frappe._dict(
			{
				"source_warehouse": (row.get("source_warehouse") if hasattr(row, "get") else None) or getattr(row, "source_warehouse", None),
				"target_warehouse": (row.get("target_warehouse") if hasattr(row, "get") else None) or getattr(row, "target_warehouse", None),
			}
		)
	return defaults


def get_settings_dict() -> frappe._dict:
	settings = frappe.get_single("MRP Settings")
	return frappe._dict(
		{
			"company": settings.company or frappe.defaults.get_user_default("Company"),
			"firm_horizon_days": cint(settings.firm_horizon_days) or 45,
			"prebuy_horizon_days": cint(settings.prebuy_horizon_days) or 120,
			"firm_fence_days": cint(settings.get("firm_fence_days")) or cint(settings.firm_horizon_days) or 45,
			"forecast_skip_firm_fence": cint(settings.get("forecast_skip_firm_fence")),
			"forecast_consumption_window_days": cint(settings.forecast_consumption_window_days) or 30,
			"material_staging_days": cint(settings.material_staging_days) or 7,
			"early_supply_warning_days": cint(settings.early_supply_warning_days) or 7,
			"late_supply_tolerance_days": cint(settings.late_supply_tolerance_days) or 0,
			"warn_missing_lead_time": cint(settings.warn_missing_lead_time),
			"use_stock_buffer_for_safety_stock": (
				1
				if settings.get("use_stock_buffer_for_safety_stock") is None
				else cint(settings.get("use_stock_buffer_for_safety_stock"))
			),
			"sync_buffer_safety_stock_to_item": cint(settings.get("sync_buffer_safety_stock_to_item")),
			"sync_buffer_dlt_to_item_lead_time": cint(settings.get("sync_buffer_dlt_to_item_lead_time")),
			"use_material_need_date_for_pegging": cint(settings.use_material_need_date_for_pegging),
			"rolling_daily_horizon_days": cint(settings.get("rolling_daily_horizon_days")) or 60,
			"allow_prebuy_material_request": cint(settings.allow_prebuy_material_request),
			"include_production_plan_as_supply": cint(settings.include_production_plan_as_supply),
			"include_production_plan_as_demand": cint(settings.include_production_plan_as_demand),
			"auto_submit_material_request": cint(settings.auto_submit_material_request),
			"default_material_request_type": settings.default_material_request_type or "Purchase",
			"warehouse_defaults": _warehouse_defaults_from_rows(settings.get("warehouse_defaults")),
			"default_source_warehouse": settings.default_source_warehouse,
			"default_target_warehouse": settings.default_target_warehouse,
		}
	)


def create_mrp_run(
	run_type: str = "Forecast Prebuy",
	company: str | None = None,
	aps_run: str | None = None,
	item_code: str | None = None,
	customer: str | None = None,
	warehouse: str | None = None,
	planning_date: str | None = None,
	status: str = "Draft",
) -> str:
	settings = get_settings_dict()
	run_type = run_type or "Forecast Prebuy"
	company = company or settings.company
	if not company:
		frappe.throw(_("Company is required for MRP."))

	planning_date = getdate(planning_date or today())
	horizon_days = (
		settings.prebuy_horizon_days if run_type == "Forecast Prebuy" else settings.firm_horizon_days
	)
	run = frappe.get_doc(
		{
			"doctype": "MRP Run",
			"company": company,
			"run_type": run_type,
			"status": status,
			"planning_date": planning_date,
			"horizon_days": horizon_days,
			"horizon_start": planning_date,
			"horizon_end": add_days(planning_date, horizon_days),
			"aps_run": aps_run,
			"item_code": item_code,
			"customer": customer,
			"warehouse": warehouse,
		}
	)
	run.insert(ignore_permissions=True)
	return run.name


def run_mrp(
	mrp_run: str | None = None,
	run_type: str = "Forecast Prebuy",
	company: str | None = None,
	aps_run: str | None = None,
	item_code: str | None = None,
	customer: str | None = None,
	warehouse: str | None = None,
	planning_date: str | None = None,
) -> dict[str, Any]:
	_clear_planning_caches()
	created_run = False
	if not mrp_run:
		mrp_run = create_mrp_run(
			run_type=run_type,
			company=company,
			aps_run=aps_run,
			item_code=item_code,
			customer=customer,
			warehouse=warehouse,
			planning_date=planning_date,
		)
		created_run = True

	settings = get_settings_dict()
	run = frappe.get_doc("MRP Run", mrp_run)
	_ensure_run_can_recalculate(run.name)

	try:
		_prepare_run_window(run, settings)
		_set_run_execution_status(run.name, "Running")
		run.reload()
		_clear_run_outputs(run.name)

		demands = _collect_demands(run, settings)
		snapshots = _insert_demand_snapshots(run, demands)
		candidates = _explode_demand_snapshots(run, snapshots)
		requirements = _net_requirements(run, settings, candidates)
		batch = _create_proposal_batch(run, settings, requirements)
		_supersede_overlapping_proposal_batches(batch, run)
		rolling_summary = _calculate_rolling_availability(run, settings, requirements, batch)
		_create_excess_prebuy_exceptions(run, requirements)

		run.reload()
		run.status = "Proposal Generated" if batch else "Calculated"
		if run.meta.has_field("error_message"):
			run.error_message = ""
		run.demand_count = len(snapshots)
		run.requirement_count = len(requirements)
		run.exception_count = frappe.db.count("MRP Exception Log", {"mrp_run": run.name})
		run.total_gross_qty = sum(flt(row.gross_qty) for row in requirements)
		run.total_net_qty = sum(flt(row.net_qty) for row in requirements)
		run.total_proposed_qty = sum(flt(row.net_qty) + flt(row.prebuy_consumed_qty) for row in requirements)
		run.proposal_batch = batch.name if batch else ""
		run.source_summary = _build_source_summary(demands)
		run.save(ignore_permissions=True)

		frappe.db.commit()
		return {
			"mrp_run": run.name,
			"proposal_batch": batch.name if batch else None,
			"demand_count": len(snapshots),
			"requirement_count": len(requirements),
			"exception_count": run.exception_count,
			"total_net_qty": run.total_net_qty,
			"shortage_alert_count": rolling_summary.get("shortage_alert_count", 0),
		}
	except Exception as exc:
		frappe.db.rollback()
		if mrp_run:
			_set_run_execution_status(mrp_run, "Failed", _format_run_error(exc))
		if created_run:
			frappe.log_error(frappe.get_traceback(), _("MRP Run failed"))
		raise


def run_forecast_prebuy(**kwargs):
	kwargs["run_type"] = "Forecast Prebuy"
	return run_mrp(**kwargs)


def run_firm_aps_mrp(**kwargs):
	kwargs["run_type"] = "Firm APS"
	return run_mrp(**kwargs)


def enqueue_forecast_prebuy(**kwargs):
	kwargs["run_type"] = "Forecast Prebuy"
	return enqueue_mrp_run(**kwargs)


def enqueue_firm_aps_mrp(**kwargs):
	kwargs["run_type"] = "Firm APS"
	return enqueue_mrp_run(**kwargs)


def enqueue_recalculate_mrp_run(mrp_run: str):
	return enqueue_mrp_run(mrp_run=mrp_run)


def enqueue_mrp_run(
	mrp_run: str | None = None,
	run_type: str = "Forecast Prebuy",
	company: str | None = None,
	aps_run: str | None = None,
	item_code: str | None = None,
	customer: str | None = None,
	warehouse: str | None = None,
	planning_date: str | None = None,
) -> dict[str, Any]:
	if mrp_run:
		run = frappe.get_doc("MRP Run", mrp_run)
		_ensure_run_can_recalculate(run.name)
		if run.status not in MRP_ACTIVE_STATUSES:
			_set_run_execution_status(run.name, "Queued", "")
	else:
		mrp_run = create_mrp_run(
			run_type=run_type,
			company=company,
			aps_run=aps_run,
			item_code=item_code,
			customer=customer,
			warehouse=warehouse,
			planning_date=planning_date,
			status="Queued",
		)
	job_id = _mrp_job_id(mrp_run)
	frappe.enqueue(
		"injection_mrp.services.planning.run_mrp",
		queue=MRP_JOB_QUEUE,
		timeout=MRP_JOB_TIMEOUT,
		enqueue_after_commit=True,
		deduplicate=True,
		job_id=job_id,
		mrp_run=mrp_run,
	)
	return {"mrp_run": mrp_run, "job_id": job_id, "status": frappe.db.get_value("MRP Run", mrp_run, "status")}


def validate_proposal_batch_for_release(batch_name: str) -> dict[str, Any]:
	settings = get_settings_dict()
	batch = frappe.get_doc("MRP Proposal Batch", batch_name)
	result = _validate_proposal_batch_for_release(batch, settings)
	_save_batch_validation_result(batch.name, result, commit=True)
	return result


def apply_proposal_batch(batch_name: str) -> dict[str, Any]:
	settings = get_settings_dict()
	batch = _lock_proposal_batch(batch_name)
	if batch.status == "Applied":
		return {"material_requests": [], "consumed_prebuy_qty": 0, "message": _("Proposal already applied.")}
	if batch.status != PROPOSAL_RELEASEABLE_STATUS:
		frappe.throw(_("Only Ready proposal batches can be applied. Current status: {0}.").format(batch.status))

	validation = _validate_proposal_batch_for_release(batch, settings)
	_save_batch_validation_result(batch.name, validation, commit=not validation["valid"])
	if not validation["valid"]:
		frappe.throw(_("Proposal batch validation failed: {0}").format(validation["validation_message"]))

	if batch.proposal_type == "Forecast Prebuy" and not settings.allow_prebuy_material_request:
		frappe.throw(_("Prebuy Material Request generation is disabled in MRP Settings."))

	mr_groups: dict[tuple[str, str, str | None, str | None, str | None, str | None], list[Any]] = defaultdict(list)
	consumed_qty = 0.0
	for row in batch.items:
		if row.status != "Pending":
			continue
		if row.action == "Consume Prebuy":
			consumed_qty += _consume_prebuy_sources(
				row.item_code,
				batch.company,
				row.warehouse,
				flt(row.qty),
				row.requirement_line,
			)
			row.status = "Applied"
			continue
		if row.action != "Create Material Request" or flt(row.qty) <= 0:
			row.status = "Skipped"
			continue
		mr_type = row.material_request_type or settings.default_material_request_type
		_validate_proposal_row_for_release(row, mr_type)
		key = (mr_type, row.commitment_type, row.warehouse, row.from_warehouse, row.customer, row.supplier)
		mr_groups[key].append(row)

	material_requests = []
	for (mr_type, commitment_type, warehouse, from_warehouse, customer, supplier), rows in mr_groups.items():
		doc = _make_material_request(batch, rows, mr_type, commitment_type, warehouse, from_warehouse, customer, supplier, settings)
		material_requests.append(doc.name)
		for row in rows:
			row.status = "Applied"
			row.material_request = doc.name
			row.material_request_item = _find_material_request_item_name(doc, row)

	batch.material_request_count = len(material_requests)
	batch.applied_by = frappe.session.user
	batch.applied_on = now_datetime()
	batch.status = "Applied"
	batch.save(ignore_permissions=True)

	frappe.db.set_value(
		"MRP Run",
		batch.mrp_run,
		{
			"status": "Released",
			"proposal_batch": batch.name,
		},
	)
	frappe.db.commit()
	return {"material_requests": material_requests, "consumed_prebuy_qty": consumed_qty}


def save_proposal_batch_items(batch_name: str, items: list[dict[str, Any]] | str | None) -> dict[str, Any]:
	batch = frappe.get_doc("MRP Proposal Batch", batch_name)
	if batch.status not in {"Draft", "Ready"}:
		frappe.throw(_("Only Draft or Ready proposal batches can be edited."))

	items = frappe.parse_json(items) if isinstance(items, str) else (items or [])
	if not isinstance(items, list):
		frappe.throw(_("Invalid proposal item payload."))

	existing = {row.name: row for row in batch.items}
	remaining_names = set(existing)
	for item in items:
		if not isinstance(item, dict):
			continue
		row_name = item.get("name")
		row = existing.get(row_name) if row_name else None
		if item.get("_delete"):
			if row and row.requirement_line:
				row.action = "No Action"
				row.status = "Skipped"
				row.skip_reason = item.get("skip_reason") or row.skip_reason or _("Skipped by planner.")
				row.manual_override = 1
			elif row:
				batch.remove(row)
			continue
		if row:
			_apply_proposal_item_values(row, item, is_manual_row=not bool(row.requirement_line))
			remaining_names.discard(row.name)
			continue
		if item.get("item_code") and flt(item.get("qty")) > 0:
			new_row = batch.append("items", {})
			_apply_proposal_item_values(new_row, item, is_manual_row=True)
			new_row.original_qty = flt(item.get("original_qty") or item.get("qty"))
			new_row.original_schedule_date = item.get("original_schedule_date") or item.get("schedule_date")
			new_row.manual_override = 1

	_recalculate_proposal_batch_totals(batch)
	batch.save(ignore_permissions=True)
	frappe.db.commit()
	return {"batch": batch.name, "item_count": batch.item_count, "total_qty": batch.total_qty}


def get_run_console_data(limit: int = 20) -> dict[str, Any]:
	fields = [
		"name",
		"company",
		"run_type",
		"status",
		"planning_date",
		"horizon_start",
		"horizon_end",
		"aps_run",
		"item_code",
		"customer",
		"warehouse",
		"demand_count",
		"requirement_count",
		"exception_count",
		"total_net_qty",
		"proposal_batch",
		"modified",
	]
	if _has_field("MRP Run", "error_message"):
		fields.append("error_message")
	runs = frappe.get_all(
		"MRP Run",
		fields=fields,
		order_by="modified desc",
		limit_page_length=cint(limit) or 20,
	)
	for run in runs:
		run["previous_run"] = _find_previous_comparable_run(run)
	return {
		"cards": _summary_cards(),
		"runs": runs,
		"actions": [
			{"label": _("Run Forecast Prebuy"), "action_key": "enqueue_forecast_prebuy", "tone": "primary"},
			{"label": _("Run Firm APS"), "action_key": "enqueue_firm_aps_mrp", "tone": "primary"},
		],
	}


def get_run_comparison_data(mrp_run: str, previous_run: str | None = None) -> dict[str, Any]:
	current = frappe.get_doc("MRP Run", mrp_run)
	previous_run = previous_run or _find_previous_comparable_run(current)
	previous = frappe.get_doc("MRP Run", previous_run) if previous_run else None
	current_rows = _summarize_run_requirements(current.name)
	previous_rows = _summarize_run_requirements(previous.name) if previous else {}
	keys = sorted(set(current_rows) | set(previous_rows))
	rows = []
	summary = defaultdict(int)
	for key in keys:
		current_row = current_rows.get(key, {})
		previous_row = previous_rows.get(key, {})
		current_net = flt(current_row.get("net_qty"))
		previous_net = flt(previous_row.get("net_qty"))
		delta = current_net - previous_net
		if key not in previous_rows:
			change_type = "New"
		elif key not in current_rows:
			change_type = "Removed"
		elif abs(delta) <= PROPOSAL_VALIDATION_TOLERANCE:
			change_type = "Unchanged"
		elif delta > 0:
			change_type = "Increased"
		else:
			change_type = "Decreased"
		summary[change_type] += 1
		rows.append(
			{
				"change_type": change_type,
				"item_code": current_row.get("item_code") or previous_row.get("item_code"),
				"warehouse": current_row.get("warehouse") or previous_row.get("warehouse"),
				"required_date": current_row.get("required_date") or previous_row.get("required_date"),
				"commitment_type": current_row.get("commitment_type") or previous_row.get("commitment_type"),
				"previous_gross_qty": flt(previous_row.get("gross_qty")),
				"current_gross_qty": flt(current_row.get("gross_qty")),
				"previous_net_qty": previous_net,
				"current_net_qty": current_net,
				"delta_net_qty": delta,
				"previous_new_supply_qty": flt(previous_row.get("new_supply_qty")),
				"current_new_supply_qty": flt(current_row.get("new_supply_qty")),
				"previous_prebuy_consumed_qty": flt(previous_row.get("prebuy_consumed_qty")),
				"current_prebuy_consumed_qty": flt(current_row.get("prebuy_consumed_qty")),
			}
		)
	return {
		"current_run": current.as_dict(),
		"previous_run": previous.as_dict() if previous else None,
		"rows": rows,
		"summary": dict(summary),
		"buffer_rows": _get_run_buffer_summaries(current),
	}


def get_demand_console_data(
	filters: dict[str, Any] | None = None,
	limit_start: int = 0,
	limit_page_length: int = 500,
) -> dict[str, Any]:
	filters = filters or {}
	limit_start, limit_page_length = _pagination_args(limit_start, limit_page_length, 500)
	query_filters = {}
	for key in ("mrp_run", "company", "demand_type", "item_code", "customer"):
		if filters.get(key):
			query_filters[key] = filters.get(key)
	rows = frappe.get_all(
		"MRP Demand Snapshot",
		filters=query_filters,
		fields=[
			"name",
			"mrp_run",
			"company",
			"demand_type",
			"status",
			"customer",
			"item_code",
			"item_name",
			"warehouse",
			"required_date",
			"demand_qty",
			"remaining_qty",
			"source_doctype",
			"source_name",
		],
		order_by="required_date asc, modified desc",
		limit_start=limit_start,
		limit_page_length=limit_page_length,
	)
	return _paged_response("MRP Demand Snapshot", query_filters, rows, limit_start, limit_page_length)


def get_material_workbench_data(
	filters: dict[str, Any] | None = None,
	limit_start: int = 0,
	limit_page_length: int = 500,
) -> dict[str, Any]:
	filters = filters or {}
	limit_start, limit_page_length = _pagination_args(limit_start, limit_page_length, 500)
	query_filters = {}
	for key in ("mrp_run", "company", "run_type", "item_code", "warehouse", "commitment_type", "supply_mode"):
		if filters.get(key):
			query_filters[key] = filters.get(key)
	rows = frappe.get_all(
		"MRP Requirement Line",
		filters=query_filters,
		fields=[
			"name",
			"mrp_run",
			"company",
			"run_type",
			"status",
			"commitment_type",
			"supply_mode",
			"material_request_type",
			"item_code",
			"item_name",
			"warehouse",
			"source_warehouse",
			"supplier",
			"supplier_lead_time_days",
			"customer",
			"required_date",
			"material_need_date",
			"gross_qty",
			"available_qty",
			"open_mr_qty",
			"open_po_qty",
			"open_wo_qty",
			"prebuy_available_qty",
			"prebuy_consumed_qty",
			"pegged_supply_qty",
			"new_supply_qty",
			"net_qty",
			"shortage_qty",
			"purchase_uom",
			"min_order_qty",
			"order_multiple_qty",
			"order_excess_qty",
			"supplier_quotation",
			"item_price",
			"estimated_rate",
			"estimated_amount",
			"currency",
			"procurement_source",
			"procurement_constraint_summary",
			"first_shortage_date",
			"lowest_projected_qty",
			"suggested_order_date",
			"expected_arrival_date",
			"delivery_variance_days",
			"stock_buffer",
			"buffer_priority",
			"buffer_nfp_percent",
			"buffer_recommended_qty",
			"buffer_top_of_red",
			"buffer_top_of_yellow",
			"buffer_top_of_green",
			"buffer_lead_time_days",
			"warning_count",
			"adjustment_summary",
			"warning_summary",
		],
		order_by="required_date asc, item_code asc",
		limit_start=limit_start,
		limit_page_length=limit_page_length,
	)
	return _paged_response("MRP Requirement Line", query_filters, rows, limit_start, limit_page_length)


def get_pegging_detail_data(
	filters: dict[str, Any] | None = None,
	limit_start: int = 0,
	limit_page_length: int = 500,
) -> dict[str, Any]:
	filters = filters or {}
	limit_start, limit_page_length = _pagination_args(limit_start, limit_page_length, 500)
	query_filters = {}
	for key in (
		"mrp_run",
		"company",
		"item_code",
		"warehouse",
		"supply_type",
		"warning_level",
		"adjustment_action",
	):
		if filters.get(key):
			query_filters[key] = filters.get(key)
	rows = frappe.get_all(
		"MRP Pegging Line",
		filters=query_filters,
		fields=[
			"name",
			"mrp_run",
			"requirement_line",
			"demand_snapshot",
			"company",
			"run_type",
			"commitment_type",
			"status",
			"demand_type",
			"demand_source_doctype",
			"demand_source_name",
			"demand_item_code",
			"item_code",
			"item_name",
			"uom",
			"warehouse",
			"required_date",
			"material_need_date",
			"demand_qty",
			"supply_type",
			"supply_doctype",
			"supply_name",
			"supply_row",
			"original_supply_qty",
			"supply_qty",
			"remaining_supply_qty",
			"supply_date",
			"expected_arrival_date",
			"supply_priority",
			"lead_time_days",
			"suggested_order_date",
			"delivery_variance_days",
			"adjustment_action",
			"adjustment_qty",
			"adjustment_date",
			"warning_level",
			"warning_category",
			"warning_reason",
		],
		order_by="material_need_date asc, item_code asc, supply_priority desc, expected_arrival_date asc",
		limit_start=limit_start,
		limit_page_length=limit_page_length,
	)
	return _paged_response("MRP Pegging Line", query_filters, rows, limit_start, limit_page_length)


def get_shortage_timeline_data(
	filters: dict[str, Any] | None = None,
	limit_start: int = 0,
	limit_page_length: int = 500,
	balance_limit_start: int = 0,
	balance_limit_page_length: int = 1000,
) -> dict[str, Any]:
	filters = filters or {}
	limit_start, limit_page_length = _pagination_args(limit_start, limit_page_length, 500)
	balance_limit_start, balance_limit_page_length = _pagination_args(
		balance_limit_start, balance_limit_page_length, 1000
	)
	alert_filters = {}
	for key in ("mrp_run", "company", "item_code", "warehouse", "warning_level", "status"):
		if filters.get(key):
			alert_filters[key] = filters.get(key)
	alerts = frappe.get_all(
		"MRP Shortage Alert",
		filters=alert_filters,
		fields=[
			"name",
			"mrp_run",
			"company",
			"item_code",
			"item_name",
			"warehouse",
			"warning_level",
			"status",
			"first_shortage_date",
			"shortage_qty",
			"lowest_projected_qty",
			"safety_stock_qty",
			"safety_stock_gap_qty",
			"latest_order_date",
			"affected_requirement_count",
			"affected_requirements",
		],
		order_by="first_shortage_date asc, warning_level asc, item_code asc",
		limit_start=limit_start,
		limit_page_length=limit_page_length,
	)
	balance_filters = {}
	for key in ("mrp_run", "company", "item_code", "warehouse", "warning_level"):
		if filters.get(key):
			balance_filters[key] = filters.get(key)
	balances = frappe.get_all(
		"MRP Rolling Balance Line",
		filters=balance_filters,
		fields=[
			"name",
			"mrp_run",
			"company",
			"item_code",
			"item_name",
			"warehouse",
			"bucket_type",
			"bucket_start",
			"bucket_end",
			"opening_qty",
			"demand_qty",
			"supply_qty",
			"planned_supply_qty",
			"projected_qty",
			"safety_stock_qty",
			"shortage_qty",
			"safety_stock_gap_qty",
			"warning_level",
			"demand_trace",
			"supply_trace",
		],
		order_by="bucket_start asc, item_code asc",
		limit_start=balance_limit_start,
		limit_page_length=balance_limit_page_length,
	)
	return {
		"cards": _summary_cards(),
		"alerts": alerts,
		"balances": balances,
		"pagination": _pagination_meta(
			frappe.db.count("MRP Shortage Alert", alert_filters),
			limit_start,
			limit_page_length,
			len(alerts),
		),
		"balance_pagination": _pagination_meta(
			frappe.db.count("MRP Rolling Balance Line", balance_filters),
			balance_limit_start,
			balance_limit_page_length,
			len(balances),
		),
	}


def get_release_center_data(
	filters: dict[str, Any] | None = None,
	limit_start: int = 0,
	limit_page_length: int = 100,
) -> dict[str, Any]:
	filters = filters or {}
	limit_start, limit_page_length = _pagination_args(limit_start, limit_page_length, 100)
	query_filters = {}
	if filters.get("company"):
		query_filters["company"] = filters["company"]
	if filters.get("status"):
		query_filters["status"] = filters["status"]
	fields = [
		"name",
		"mrp_run",
		"company",
		"proposal_type",
		"status",
		"item_count",
		"total_qty",
		"material_request_count",
		"generated_by",
		"generated_on",
		"applied_by",
		"applied_on",
	]
	for fieldname in (
		"superseded_by_run",
		"superseded_by_batch",
		"superseded_on",
		"superseded_reason",
		"last_validation_on",
		"validation_message",
	):
		if _has_field("MRP Proposal Batch", fieldname):
			fields.append(fieldname)
	batches = frappe.get_all(
		"MRP Proposal Batch",
		filters=query_filters,
		fields=fields,
		order_by="modified desc",
		limit_start=limit_start,
		limit_page_length=limit_page_length,
	)
	response = _paged_response("MRP Proposal Batch", query_filters, batches, limit_start, limit_page_length)
	response["batches"] = response.pop("rows")
	return response


def get_requirement_detail(requirement_line: str) -> dict[str, Any]:
	row = frappe.get_doc("MRP Requirement Line", requirement_line)
	return {
		"requirement": row.as_dict(),
		"demand": frappe.get_doc("MRP Demand Snapshot", row.demand_snapshot).as_dict()
		if row.demand_snapshot
		else None,
		"supply_trace": _loads(row.supply_trace),
		"bom_trace": _loads(row.bom_trace),
		"bom_detail": _get_requirement_bom_detail(row),
		"exceptions": frappe.get_all(
			"MRP Exception Log",
			filters={"requirement_line": row.name},
			fields=["name", "severity", "category", "message", "resolution_status", "created_on"],
			order_by="modified desc",
		),
		"pegging_lines": frappe.get_all(
			"MRP Pegging Line",
			filters={"requirement_line": row.name},
			fields=[
				"name",
				"supply_type",
				"supply_doctype",
				"supply_name",
				"original_supply_qty",
				"supply_qty",
				"remaining_supply_qty",
				"expected_arrival_date",
				"delivery_variance_days",
				"adjustment_action",
				"adjustment_date",
				"warning_level",
				"warning_category",
				"warning_reason",
			],
			order_by="supply_priority desc, expected_arrival_date asc, creation asc",
		),
		"rolling_lines": frappe.get_all(
			"MRP Rolling Balance Line",
			filters={"mrp_run": row.mrp_run, "item_code": row.item_code, "warehouse": row.warehouse},
			fields=[
				"name",
				"bucket_type",
				"bucket_start",
				"bucket_end",
				"opening_qty",
				"demand_qty",
				"supply_qty",
				"planned_supply_qty",
				"projected_qty",
				"safety_stock_qty",
				"shortage_qty",
				"safety_stock_gap_qty",
				"warning_level",
			],
			order_by="bucket_start asc",
			limit_page_length=120,
		)
		if _doctype_exists("MRP Rolling Balance Line")
		else [],
		"shortage_alerts": frappe.get_all(
			"MRP Shortage Alert",
			filters={"mrp_run": row.mrp_run, "item_code": row.item_code, "warehouse": row.warehouse},
			fields=[
				"name",
				"warning_level",
				"status",
				"first_shortage_date",
				"shortage_qty",
				"lowest_projected_qty",
				"safety_stock_gap_qty",
				"latest_order_date",
				"affected_requirement_count",
			],
			order_by="first_shortage_date asc",
		)
		if _doctype_exists("MRP Shortage Alert")
		else [],
		"proposal_items": _find_proposal_items(row.name),
	}


def get_batch_detail(batch_name: str) -> dict[str, Any]:
	batch = frappe.get_doc("MRP Proposal Batch", batch_name)
	return {"batch": batch.as_dict(), "items": [row.as_dict() for row in batch.items]}


def _lock_proposal_batch(batch_name: str):
	rows = frappe.db.sql(
		"select name from `tabMRP Proposal Batch` where name = %s for update",
		(batch_name,),
		as_dict=True,
	)
	if not rows:
		frappe.throw(_("MRP Proposal Batch {0} was not found.").format(batch_name))
	return frappe.get_doc("MRP Proposal Batch", batch_name)


def _validate_proposal_batch_for_release(batch, settings) -> dict[str, Any]:
	blocking: list[dict[str, Any]] = []
	warnings: list[dict[str, Any]] = []
	rows: list[dict[str, Any]] = []
	if batch.status != PROPOSAL_RELEASEABLE_STATUS:
		_add_validation_issue(
			blocking,
			rows,
			"Batch Status",
			_("Only Ready proposal batches can be applied. Current status: {0}.").format(batch.status),
			batch=batch,
		)

	active_rows = [
		row
		for row in _proposal_batch_items(batch)
		if row.status == "Pending" and row.action != "No Action" and flt(row.qty) > PROPOSAL_VALIDATION_TOLERANCE
	]
	if not active_rows:
		_add_validation_issue(blocking, rows, "No Pending Items", _("No pending proposal items are available to apply."), batch=batch)

	for row in active_rows:
		_validate_proposal_commitment(batch, row, blocking, rows)
		newer_batch = _find_newer_overlapping_ready_batch(batch, row)
		if newer_batch:
			_add_validation_issue(
				blocking,
				rows,
				"Newer Proposal",
				_("A newer ready proposal batch {0} contains the same item, warehouse, date and commitment.").format(newer_batch),
				batch=batch,
				row=row,
			)

	_validate_create_mr_shortages(batch, settings, active_rows, blocking, warnings, rows)
	_validate_prebuy_consumption(batch, active_rows, blocking, rows)

	message = _validation_message(blocking, warnings)
	return {
		"valid": not blocking,
		"blocking_issues": blocking,
		"warnings": warnings,
		"rows": rows,
		"validation_message": message,
	}


def _validate_proposal_commitment(batch, row, blocking, rows):
	if row.action == "Create Material Request":
		if batch.proposal_type == "Forecast Prebuy" and row.commitment_type != "Prebuy":
			_add_validation_issue(
				blocking,
				rows,
				"Commitment Mismatch",
				_("Forecast Prebuy proposals can only release Prebuy commitments."),
				batch=batch,
				row=row,
			)
		if batch.proposal_type == "Firm APS" and row.commitment_type != "Firm":
			_add_validation_issue(
				blocking,
				rows,
				"Commitment Mismatch",
				_("Firm APS material request proposals must release Firm commitments."),
				batch=batch,
				row=row,
			)
	if row.action == "Consume Prebuy" and row.commitment_type != "Prebuy":
		_add_validation_issue(
			blocking,
			rows,
			"Commitment Mismatch",
			_("Consume Prebuy rows must keep Prebuy commitment type."),
			batch=batch,
			row=row,
		)


def _proposal_batch_items(batch) -> list[Any]:
	items = getattr(batch, "items", None)
	if callable(items):
		return batch.get("items") or []
	return items or []


def _validate_create_mr_shortages(batch, settings, active_rows, blocking, warnings, rows):
	create_rows = [row for row in active_rows if row.action == "Create Material Request" and row.requirement_line]
	if not create_rows:
		return
	requirements = _get_requirement_validation_map([row.requirement_line for row in create_rows])
	run = _get_validation_run(batch.mrp_run)
	grouped: dict[tuple[str, str | None], list[Any]] = defaultdict(list)
	for row in create_rows:
		requirement = requirements.get(row.requirement_line)
		if not requirement:
			_add_validation_issue(
				warnings,
				rows,
				"Missing Requirement",
				_("Requirement line was not found; live shortage cannot be revalidated."),
				batch=batch,
				row=row,
				severity="Warning",
			)
			continue
		grouped[(row.item_code, row.warehouse)].append(row)

	for (item_code, warehouse), group_rows in grouped.items():
		supply_records = _get_supply_records(item_code, batch.company, warehouse, settings, run)
		current_shortage_by_requirement = _get_current_shortage_by_requirement(
			batch.mrp_run,
			item_code,
			warehouse,
			supply_records,
			[row.requirement_line for row in group_rows],
			requirements,
		)
		for row in group_rows:
			current_shortage = current_shortage_by_requirement.get(row.requirement_line, flt(row.qty))
			if current_shortage + PROPOSAL_VALIDATION_TOLERANCE < flt(row.qty):
				_add_validation_issue(
					blocking,
					rows,
					"Reduced Shortage",
					_("Current shortage {0} is lower than proposal qty {1}; please recheck this row.").format(
						round(current_shortage, 6), round(flt(row.qty), 6)
					),
					batch=batch,
					row=row,
					current_shortage_qty=current_shortage,
				)
			elif current_shortage > flt(row.qty) + PROPOSAL_VALIDATION_TOLERANCE:
				_add_validation_issue(
					warnings,
					rows,
					"Increased Shortage",
					_("Current shortage {0} is higher than proposal qty {1}; this batch may not cover the full demand.").format(
						round(current_shortage, 6), round(flt(row.qty), 6)
					),
					batch=batch,
					row=row,
					severity="Warning",
					current_shortage_qty=current_shortage,
				)


def _get_current_shortage_by_requirement(
	mrp_run: str | None,
	item_code: str,
	warehouse: str | None,
	supply_records: list[SupplyRecord],
	proposal_requirement_names: list[str],
	fallback_requirements: dict[str, Any],
) -> dict[str, float]:
	requirement_rows = _get_run_requirement_validation_rows(mrp_run, item_code, warehouse)
	if not requirement_rows:
		requirement_rows = [fallback_requirements[name] for name in proposal_requirement_names if name in fallback_requirements]
	requirement_rows.sort(
		key=lambda row: (
			getdate(row.get("material_need_date") or row.get("required_date") or today()),
			row.get("name") or "",
		)
	)
	shortage_by_requirement = {}
	for requirement in requirement_rows:
		gross_qty = flt(requirement.get("gross_qty"))
		covered_qty = _consume_validation_supply(supply_records, gross_qty)
		shortage_by_requirement[requirement.name] = max(gross_qty - covered_qty, 0)
	return shortage_by_requirement


def _get_run_requirement_validation_rows(mrp_run: str | None, item_code: str, warehouse: str | None) -> list[Any]:
	if not mrp_run:
		return []
	rows = frappe.get_all(
		"MRP Requirement Line",
		filters={"mrp_run": mrp_run, "item_code": item_code},
		fields=[
			"name",
			"item_code",
			"warehouse",
			"required_date",
			"material_need_date",
			"gross_qty",
			"net_qty",
		],
		order_by="material_need_date asc, required_date asc, name asc",
		limit_page_length=100000,
	)
	return [row for row in rows if (row.warehouse or "") == (warehouse or "")]


def _validate_prebuy_consumption(batch, active_rows, blocking, rows):
	consume_rows = [row for row in active_rows if row.action == "Consume Prebuy"]
	if not consume_rows:
		return
	grouped: dict[tuple[str, str | None], list[Any]] = defaultdict(list)
	for row in consume_rows:
		grouped[(row.item_code, row.warehouse)].append(row)

	for (item_code, warehouse), group_rows in grouped.items():
		prebuy_records = _get_prebuy_supply_records(item_code, batch.company, warehouse)
		group_rows.sort(key=lambda row: (getdate(row.schedule_date or row.required_date or today()), row.name))
		for row in group_rows:
			available_qty = _consume_validation_supply(prebuy_records, flt(row.qty))
			if available_qty + PROPOSAL_VALIDATION_TOLERANCE < flt(row.qty):
				_add_validation_issue(
					blocking,
					rows,
					"Insufficient Prebuy",
					_("Available prebuy qty {0} is lower than consume qty {1}.").format(
						round(available_qty, 6), round(flt(row.qty), 6)
					),
					batch=batch,
					row=row,
					current_shortage_qty=max(flt(row.qty) - available_qty, 0),
				)


def _get_requirement_validation_map(requirement_names: list[str]) -> dict[str, Any]:
	names = [name for name in dict.fromkeys(requirement_names) if name]
	if not names:
		return {}
	rows = frappe.get_all(
		"MRP Requirement Line",
		filters={"name": ["in", names]},
		fields=[
			"name",
			"item_code",
			"warehouse",
			"required_date",
			"material_need_date",
			"gross_qty",
			"net_qty",
		],
		limit_page_length=len(names),
	)
	return {row.name: row for row in rows}


def _get_validation_run(mrp_run: str | None):
	if not mrp_run:
		return None
	try:
		return frappe.get_doc("MRP Run", mrp_run)
	except Exception:
		return None


def _consume_validation_supply(supply_records: list[SupplyRecord], qty: float) -> float:
	remaining = flt(qty)
	consumed = 0.0
	for supply in _ordered_supply_records(supply_records):
		if remaining <= PROPOSAL_VALIDATION_TOLERANCE:
			break
		available = flt(supply.remaining_qty)
		if available <= PROPOSAL_VALIDATION_TOLERANCE:
			continue
		consume = min(available, remaining)
		supply.remaining_qty = max(available - consume, 0)
		remaining -= consume
		consumed += consume
	return consumed


def _find_newer_overlapping_ready_batch(batch, row) -> str | None:
	date_value = row.schedule_date or row.required_date
	if not row.item_code or not date_value:
		return None
	rows = frappe.db.sql(
		"""
		select batch.name
		from `tabMRP Proposal Item` item
		inner join `tabMRP Proposal Batch` batch on batch.name = item.parent
		where batch.name != %(batch_name)s
			and batch.company = %(company)s
			and batch.status = 'Ready'
			and item.status = 'Pending'
			and item.action in ('Create Material Request', 'Consume Prebuy')
			and item.item_code = %(item_code)s
			and ifnull(item.warehouse, '') = %(warehouse)s
			and ifnull(coalesce(item.schedule_date, item.required_date), '') = %(date_value)s
			and ifnull(item.qty, 0) > 0
			and (
				ifnull(batch.generated_on, batch.creation) > %(generated_on)s
				or (
					ifnull(batch.generated_on, batch.creation) = %(generated_on)s
					and batch.name > %(batch_name)s
				)
			)
		order by ifnull(batch.generated_on, batch.creation) desc, batch.name desc
		limit 1
		""",
		{
			"batch_name": batch.name,
			"company": batch.company,
			"proposal_type": batch.proposal_type,
			"item_code": row.item_code,
			"warehouse": row.warehouse or "",
			"commitment_type": row.commitment_type or "",
			"date_value": date_value,
			"generated_on": batch.generated_on or batch.creation,
		},
		as_dict=True,
	)
	return rows[0].name if rows else None


def _add_validation_issue(
	target: list[dict[str, Any]],
	rows: list[dict[str, Any]],
	issue_type: str,
	message: str,
	batch=None,
	row=None,
	severity: str = "Blocker",
	current_shortage_qty: float | None = None,
):
	issue = {
		"severity": severity,
		"issue_type": issue_type,
		"message": message,
		"batch": batch.name if batch else None,
		"row": row.name if row else None,
		"item_code": row.item_code if row else None,
		"warehouse": row.warehouse if row else None,
		"schedule_date": row.schedule_date if row else None,
		"required_date": row.required_date if row else None,
		"action": row.action if row else None,
		"commitment_type": row.commitment_type if row else None,
		"proposal_qty": flt(row.qty) if row else 0,
		"current_shortage_qty": current_shortage_qty,
	}
	target.append(issue)
	rows.append(issue)


def _validation_message(blocking, warnings) -> str:
	if blocking:
		first = blocking[0].get("message") or _("Release validation failed.")
		return _("Blocked: {0} issue(s). {1}").format(len(blocking), first)
	if warnings:
		first = warnings[0].get("message") or _("Release validation has warnings.")
		return _("Valid with {0} warning(s). {1}").format(len(warnings), first)
	return _("Ready for release.")


def _save_batch_validation_result(batch_name: str, result: dict[str, Any], commit: bool = False):
	values = {}
	if _has_field("MRP Proposal Batch", "last_validation_on"):
		values["last_validation_on"] = now_datetime()
	if _has_field("MRP Proposal Batch", "validation_message"):
		values["validation_message"] = result.get("validation_message") or ""
	if values:
		frappe.db.set_value("MRP Proposal Batch", batch_name, values)
		if commit:
			frappe.db.commit()


def _ensure_run_can_recalculate(mrp_run: str):
	applied_batches = frappe.get_all(
		"MRP Proposal Batch", filters={"mrp_run": mrp_run, "status": "Applied"}, pluck="name"
	)
	if applied_batches:
		frappe.throw(_("MRP Run {0} already has applied proposal batches and cannot be recalculated.").format(mrp_run))


def _set_run_execution_status(mrp_run: str, status: str, error_message: str | None = None):
	if not mrp_run or not frappe.db.exists("MRP Run", mrp_run):
		return
	values = {"status": status}
	if _has_field("MRP Run", "error_message"):
		values["error_message"] = error_message or ""
	frappe.db.set_value("MRP Run", mrp_run, values)
	frappe.db.commit()


def _format_run_error(exc: Exception) -> str:
	message = str(exc) or exc.__class__.__name__
	return message[:2000]


def _mrp_job_id(mrp_run: str) -> str:
	return f"injection_mrp_run_{mrp_run}"


def _find_previous_comparable_run(run) -> str | None:
	filters = {
		"company": run.get("company"),
		"run_type": run.get("run_type"),
		"name": ["!=", run.get("name")],
		"status": ["in", ["Calculated", "Proposal Generated", "Released"]],
	}
	if run.get("modified"):
		filters["modified"] = ["<", run.get("modified")]
	rows = frappe.get_all(
		"MRP Run",
		filters=filters,
		fields=["name", "item_code", "customer", "warehouse"],
		order_by="modified desc",
		limit_page_length=50,
	)
	for candidate in rows:
		if all((candidate.get(fieldname) or "") == (run.get(fieldname) or "") for fieldname in ("item_code", "customer", "warehouse")):
			return candidate.name
	return None


def _summarize_run_requirements(mrp_run: str) -> dict[tuple[str, str, str, str], dict[str, Any]]:
	rows = frappe.get_all(
		"MRP Requirement Line",
		filters={"mrp_run": mrp_run},
		fields=[
			"item_code",
			"warehouse",
			"required_date",
			"commitment_type",
			"gross_qty",
			"net_qty",
			"new_supply_qty",
			"prebuy_consumed_qty",
		],
		limit_page_length=100000,
	)
	result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
	for row in rows:
		key = (
			row.item_code or "",
			row.warehouse or "",
			str(row.required_date or ""),
			row.commitment_type or "",
		)
		target = result.setdefault(
			key,
			{
				"item_code": row.item_code,
				"warehouse": row.warehouse,
				"required_date": row.required_date,
				"commitment_type": row.commitment_type,
				"gross_qty": 0,
				"net_qty": 0,
				"new_supply_qty": 0,
				"prebuy_consumed_qty": 0,
			},
		)
		target["gross_qty"] += flt(row.gross_qty)
		target["net_qty"] += flt(row.net_qty)
		target["new_supply_qty"] += flt(row.new_supply_qty)
		target["prebuy_consumed_qty"] += flt(row.prebuy_consumed_qty)
	return result


def _get_run_buffer_summaries(run, limit: int = 8) -> list[dict[str, Any]]:
	if not _doctype_exists("MRP Stock Buffer") or not _has_field("MRP Requirement Line", "stock_buffer"):
		return []
	rows = frappe.get_all(
		"MRP Requirement Line",
		filters={"mrp_run": run.name},
		fields=[
			"stock_buffer",
			"item_code",
			"item_name",
			"warehouse",
			"required_date",
			"suggested_order_date",
			"net_qty",
			"new_supply_qty",
			"buffer_priority",
			"buffer_nfp_percent",
			"buffer_recommended_qty",
			"buffer_top_of_red",
			"buffer_top_of_yellow",
			"buffer_top_of_green",
			"buffer_lead_time_days",
		],
		limit_page_length=100000,
	)
	by_buffer: dict[str, dict[str, Any]] = {}
	for row in rows:
		if not row.stock_buffer:
			continue
		target = by_buffer.setdefault(
			row.stock_buffer,
			{
				"stock_buffer": row.stock_buffer,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"warehouse": row.warehouse,
				"requirement_count": 0,
				"current_net_qty": 0,
				"current_new_supply_qty": 0,
				"earliest_required_date": row.required_date,
				"suggested_order_date": row.suggested_order_date,
				"buffer_priority": row.buffer_priority,
				"buffer_nfp_percent": flt(row.buffer_nfp_percent),
				"buffer_recommended_qty": flt(row.buffer_recommended_qty),
				"buffer_top_of_red": flt(row.buffer_top_of_red),
				"buffer_top_of_yellow": flt(row.buffer_top_of_yellow),
				"buffer_top_of_green": flt(row.buffer_top_of_green),
				"buffer_lead_time_days": cint(row.buffer_lead_time_days),
			},
		)
		target["requirement_count"] += 1
		target["current_net_qty"] += flt(row.net_qty)
		target["current_new_supply_qty"] += flt(row.new_supply_qty)
		if row.required_date and (not target.get("earliest_required_date") or row.required_date < target["earliest_required_date"]):
			target["earliest_required_date"] = row.required_date
		if row.suggested_order_date and (
			not target.get("suggested_order_date") or row.suggested_order_date < target["suggested_order_date"]
		):
			target["suggested_order_date"] = row.suggested_order_date

	priority_order = {"Red": 0, "Yellow": 1, "Green": 2}
	row_limit = cint(limit) or 8
	candidate_summaries = sorted(
		by_buffer.items(),
		key=lambda item: (
			priority_order.get(item[1].get("buffer_priority"), 3),
			flt(item[1].get("buffer_nfp_percent")) if item[1].get("buffer_nfp_percent") not in (None, "") else 999999,
			-flt(item[1].get("buffer_recommended_qty")),
			item[1].get("item_code") or "",
		),
	)[:row_limit]

	buffers = []
	for buffer_name, summary in candidate_summaries:
		state = frappe._dict()
		try:
			state = stock_buffer.refresh_buffer(buffer_name, run=run, persist=False)
		except Exception:
			state = frappe._dict()
		top_red = flt(state.get("top_of_red")) or flt(summary.get("buffer_top_of_red"))
		top_yellow = flt(state.get("top_of_yellow")) or flt(summary.get("buffer_top_of_yellow"))
		top_green = flt(state.get("top_of_green")) or flt(summary.get("buffer_top_of_green"))
		nfp_percent = flt(state.get("net_flow_position_percent")) or flt(summary.get("buffer_nfp_percent"))
		net_flow_position = flt(state.get("net_flow_position"))
		if not net_flow_position and nfp_percent and top_green:
			net_flow_position = top_green * nfp_percent / 100
		row = dict(summary)
		row.update(
			{
				"name": buffer_name,
				"doctype": "MRP Stock Buffer",
				"adu": flt(state.get("adu")) or 0,
				"red_zone_qty": flt(state.get("red_zone_qty")) or top_red,
				"yellow_zone_qty": flt(state.get("yellow_zone_qty")) or max(top_yellow - top_red, 0),
				"green_zone_qty": flt(state.get("green_zone_qty")) or max(top_green - top_yellow, 0),
				"top_of_red": top_red,
				"top_of_yellow": top_yellow,
				"top_of_green": top_green,
				"net_flow_position": net_flow_position,
				"net_flow_position_percent": nfp_percent,
				"on_hand_qty": flt(state.get("on_hand_qty")) or 0,
				"incoming_dlt_qty": flt(state.get("incoming_dlt_qty")) or 0,
				"qualified_demand_qty": flt(state.get("qualified_demand_qty")) or 0,
				"planning_priority": state.get("planning_priority") or summary.get("buffer_priority"),
				"recommended_qty": flt(state.get("recommended_qty")) or flt(summary.get("buffer_recommended_qty")),
				"dlt_days": flt(state.get("dlt_days")) or cint(summary.get("buffer_lead_time_days")),
			}
		)
		row["buffer_priority"] = row["planning_priority"]
		row["buffer_nfp_percent"] = row["net_flow_position_percent"]
		row["buffer_recommended_qty"] = row["recommended_qty"]
		row["buffer_top_of_red"] = row["top_of_red"]
		row["buffer_top_of_yellow"] = row["top_of_yellow"]
		row["buffer_top_of_green"] = row["top_of_green"]
		row["buffer_lead_time_days"] = cint(row["dlt_days"])
		buffers.append(row)

	def sort_nfp(row):
		value = row.get("buffer_nfp_percent")
		return flt(value) if value not in (None, "") else 999999

	buffers.sort(
		key=lambda row: (
			priority_order.get(row.get("buffer_priority"), 3),
			sort_nfp(row),
			-flt(row.get("buffer_recommended_qty")),
			row.get("item_code") or "",
		)
	)
	return buffers[:row_limit]


def _pagination_args(limit_start: int | str | None, limit_page_length: int | str | None, default_length: int):
	start = max(cint(limit_start), 0)
	page_length = cint(limit_page_length) or default_length
	page_length = min(max(page_length, 1), 1000)
	return start, page_length


def _pagination_meta(total_count: int, limit_start: int, limit_page_length: int, row_count: int) -> dict[str, Any]:
	next_start = limit_start + row_count
	return {
		"total_count": cint(total_count),
		"limit_start": cint(limit_start),
		"limit_page_length": cint(limit_page_length),
		"row_count": cint(row_count),
		"has_previous": cint(limit_start) > 0,
		"has_next": next_start < cint(total_count),
		"next_start": next_start,
		"previous_start": max(cint(limit_start) - cint(limit_page_length), 0),
	}


def _paged_response(doctype: str, filters: dict[str, Any], rows: list[Any], limit_start: int, limit_page_length: int):
	return {
		"cards": _summary_cards(),
		"rows": rows,
		"pagination": _pagination_meta(
			frappe.db.count(doctype, filters),
			limit_start,
			limit_page_length,
			len(rows),
		),
	}


def _bulk_insert_records(doctype: str, rows: list[dict[str, Any]]):
	if not rows:
		return
	from frappe.model.naming import make_autoname

	meta = frappe.get_meta(doctype)
	autoname = meta.autoname or ""
	now = now_datetime()
	base_fields = ["name", "creation", "modified", "modified_by", "owner", "docstatus", "idx"]
	data_fields = list(rows[0])
	fields = base_fields + data_fields
	values = []
	for idx, row in enumerate(rows, start=1):
		name = make_autoname(autoname) if autoname and autoname != "hash" else frappe.generate_hash(length=10)
		values.append(
			[
				name,
				now,
				now,
				frappe.session.user,
				frappe.session.user,
				0,
				idx,
				*[row.get(fieldname) for fieldname in data_fields],
			]
		)
	frappe.db.bulk_insert(doctype, fields, values)


def _stock_qty(row, qty_field="qty", stock_qty_field="stock_qty", conversion_factor_field="conversion_factor") -> float:
	stock_qty = flt(row.get(stock_qty_field))
	if stock_qty > 0:
		return stock_qty
	return flt(row.get(qty_field)) * (flt(row.get(conversion_factor_field)) or 1)


def _sales_order_remaining_stock_qty(row) -> float:
	base_qty = _stock_qty(row)
	delivered_qty = flt(row.get("delivered_qty")) * (flt(row.get("conversion_factor")) or 1)
	return max(base_qty - delivered_qty, 0)


def _material_request_remaining_stock_qty(row) -> float:
	base_qty = _stock_qty(row)
	used_qty = max(flt(row.get("ordered_qty")), flt(row.get("received_qty")))
	return max(base_qty - used_qty, 0)


def _purchase_order_remaining_stock_qty(row) -> float:
	return max(flt(row.get("qty")) - flt(row.get("received_qty")), 0) * (
		flt(row.get("conversion_factor")) or 1
	)


def _production_plan_remaining_qty(row) -> float:
	pending_qty = flt(row.get("pending_qty"))
	if pending_qty > 0:
		return pending_qty
	used_qty = max(flt(row.get("ordered_qty")), flt(row.get("produced_qty")))
	return max(flt(row.get("planned_qty")) - used_qty, 0)


def _prepare_run_window(run, settings):
	run.planning_date = getdate(run.planning_date or today())
	if not run.horizon_days:
		run.horizon_days = (
			settings.prebuy_horizon_days
			if run.run_type == "Forecast Prebuy"
			else settings.firm_horizon_days
		)
	run.horizon_start = run.horizon_start or run.planning_date
	run.horizon_end = run.horizon_end or add_days(run.planning_date, cint(run.horizon_days))
	run.save(ignore_permissions=True)


def _clear_run_outputs(mrp_run: str):
	_ensure_run_can_recalculate(mrp_run)
	_delete_proposal_items_for_run(mrp_run)
	for doctype in (
		"MRP Proposal Batch",
		"MRP Shortage Alert",
		"MRP Rolling Balance Line",
		"MRP Exception Log",
		"MRP Pegging Line",
		"MRP Requirement Line",
		"MRP Demand Snapshot",
	):
		_delete_mrp_run_rows(doctype, mrp_run)


def _delete_proposal_items_for_run(mrp_run: str):
	if not _doctype_exists("MRP Proposal Item") or not _doctype_exists("MRP Proposal Batch"):
		return
	frappe.db.sql(
		"""
		delete item
		from `tabMRP Proposal Item` item
		inner join `tabMRP Proposal Batch` batch on batch.name = item.parent
		where batch.mrp_run = %s
		""",
		(mrp_run,),
	)


def _delete_mrp_run_rows(doctype: str, mrp_run: str):
	if not _doctype_exists(doctype):
		return
	frappe.db.sql(f"delete from `tab{doctype}` where mrp_run = %s", (mrp_run,))


def _collect_demands(run, settings) -> list[DemandRow]:
	if run.run_type == "Firm APS":
		return _dedupe_demands(_filter_valid_demands(run, _collect_firm_aps_demands(run)))

	demands = []
	forecast_demands = _filter_valid_demands(run, _collect_customer_schedule_demands(run))
	sales_order_demands = _filter_valid_demands(run, _collect_sales_order_demands(run))
	demands.extend(_consume_forecast_with_sales_orders(forecast_demands, sales_order_demands, settings))
	demands.extend(sales_order_demands)
	demands.extend(_filter_valid_demands(run, _collect_safety_stock_demands(run, settings)))
	if settings.include_production_plan_as_demand:
		demands.extend(_filter_valid_demands(run, _collect_production_plan_demands(run)))
	return _filter_forecast_fence_demands(run, settings, _dedupe_demands(demands))


def _filter_forecast_fence_demands(run, settings, demands: list[DemandRow]) -> list[DemandRow]:
	if run.run_type != "Forecast Prebuy" or not cint(settings.get("forecast_skip_firm_fence")):
		return demands
	fence_end = getdate(add_days(run.planning_date or run.horizon_start or today(), cint(settings.firm_fence_days)))
	return [
		row
		for row in demands
		if row.required_date and getdate(row.required_date) > fence_end
	]


def _collect_customer_schedule_demands(run) -> list[DemandRow]:
	if not _doctype_exists("Customer Delivery Schedule"):
		return []

	parent_filters = {"company": run.company, "status": "Active"}
	if run.customer:
		parent_filters["customer"] = run.customer
	parents = frappe.get_all(
		"Customer Delivery Schedule",
		filters=parent_filters,
		fields=["name", "customer", "company"],
		limit_page_length=5000,
	)
	if not parents:
		return []

	parent_map = {row.name: row for row in parents}
	child_filters = {
		"parent": ["in", list(parent_map)],
		"schedule_date": ["between", [run.horizon_start, run.horizon_end]],
	}
	if run.item_code:
		child_filters["item_code"] = run.item_code
	warehouse_field = _first_field("Customer Delivery Schedule Item", ("warehouse", "target_warehouse"))
	fields = [
		"name",
		"parent",
		"item_code",
		"schedule_date",
		"qty",
		"balance_qty",
		"sales_order",
		"status",
	]
	if warehouse_field:
		fields.append(warehouse_field)
	items = frappe.get_all(
		"Customer Delivery Schedule Item",
		filters=child_filters,
		fields=fields,
		limit_page_length=10000,
	)
	demands = []
	for row in items:
		if row.status == "Cancelled":
			continue
		qty = flt(row.balance_qty) if flt(row.balance_qty) > 0 else flt(row.qty)
		if qty <= 0:
			continue
		parent = parent_map.get(row.parent)
		demands.append(
			DemandRow(
				demand_type="Forecast",
				item_code=row.item_code,
				qty=qty,
				required_date=row.schedule_date,
				company=run.company,
				warehouse=(row.get(warehouse_field) if warehouse_field else None) or run.warehouse,
				customer=parent.customer if parent else None,
				source_doctype="Customer Delivery Schedule",
				source_name=row.parent,
				source_row=row.name,
				linked_sales_order=row.sales_order,
				notes=_("Customer schedule prebuy demand"),
			)
		)
	return demands


def _collect_sales_order_demands(run) -> list[DemandRow]:
	if not _doctype_exists("Sales Order"):
		return []

	so_filters = {"company": run.company, "docstatus": 1, "status": ["not in", ["Closed", "Completed", "Cancelled"]]}
	if run.customer:
		so_filters["customer"] = run.customer
	orders = frappe.get_all(
		"Sales Order",
		filters=so_filters,
		fields=["name", "customer", "company"],
		limit_page_length=5000,
	)
	if not orders:
		return []

	order_map = {row.name: row for row in orders}
	item_filters = {
		"parent": ["in", list(order_map)],
		"delivery_date": ["between", [run.horizon_start, run.horizon_end]],
	}
	if run.item_code:
		item_filters["item_code"] = run.item_code

	fields = [
		"name",
		"parent",
		"item_code",
		"item_name",
		"delivery_date",
		"qty",
		"delivered_qty",
		"warehouse",
	]
	if _has_field("Sales Order Item", "stock_qty"):
		fields.append("stock_qty")
	if _has_field("Sales Order Item", "conversion_factor"):
		fields.append("conversion_factor")
	rows = frappe.get_all(
		"Sales Order Item",
		filters=item_filters,
		fields=fields,
		limit_page_length=10000,
	)
	demands = []
	for row in rows:
		qty = _sales_order_remaining_stock_qty(row)
		if qty <= 0:
			continue
		parent = order_map.get(row.parent)
		demands.append(
			DemandRow(
				demand_type="Sales Order",
				item_code=row.item_code,
				qty=qty,
				required_date=row.delivery_date,
				company=run.company,
				warehouse=row.warehouse or run.warehouse,
				customer=parent.customer if parent else None,
				source_doctype="Sales Order",
				source_name=row.parent,
				source_row=row.name,
				linked_sales_order=row.parent,
				notes=_("Sales order backlog demand"),
			)
		)
	return demands


def _collect_safety_stock_demands(run, settings=None) -> list[DemandRow]:
	demands = []
	use_buffer = cint((settings or {}).get("use_stock_buffer_for_safety_stock", 1)) if hasattr(settings or {}, "get") else 1
	if use_buffer:
		for buffer in stock_buffer.collect_buffer_top_up_demands(run, persist=False):
			demands.append(
				DemandRow(
					demand_type="Stock Buffer",
					item_code=buffer.item_code,
					qty=buffer.recommended_qty,
					required_date=run.horizon_start,
					company=run.company,
					warehouse=buffer.warehouse,
					source_doctype="MRP Stock Buffer",
					source_name=buffer.name,
					notes=_("Stock buffer top-up demand. Priority: {0}, NFP: {1}%").format(
						buffer.planning_priority,
						buffer.net_flow_position_percent,
					),
				)
			)
		demands.extend(_collect_item_safety_stock_demands(run, skip_buffered_items=True))
	else:
		demands.extend(_collect_item_safety_stock_demands(run, skip_buffered_items=False))
	return demands


def _collect_item_safety_stock_demands(run, skip_buffered_items: bool = True) -> list[DemandRow]:
	if not _doctype_exists("Item") or not _has_field("Item", "safety_stock"):
		return []

	filters: dict[str, Any] = {"disabled": 0, "safety_stock": [">", 0]}
	if run.item_code:
		filters["name"] = run.item_code
	fields = ["name", "item_name", "stock_uom", "safety_stock"]
	if _has_field("Item", "default_warehouse"):
		fields.append("default_warehouse")
	items = frappe.get_all("Item", filters=filters, fields=fields, limit_page_length=5000)
	demands = []
	for item in items:
		warehouse = run.warehouse or item.get("default_warehouse")
		if skip_buffered_items and stock_buffer.get_buffer_name_for_item(item.name, run.company, warehouse):
			continue
		available = _get_available_qty(item.name, run.company, warehouse)
		shortage = max(flt(item.safety_stock) - available, 0)
		if shortage <= 0:
			continue
		demands.append(
			DemandRow(
				demand_type="Safety Stock",
				item_code=item.name,
				qty=shortage,
				required_date=run.horizon_start,
				company=run.company,
				warehouse=warehouse,
				source_doctype="Item",
				source_name=item.name,
				notes=_("Safety stock top-up demand"),
			)
		)
	return demands


def _collect_production_plan_demands(run) -> list[DemandRow]:
	if not _doctype_exists("Production Plan"):
		return []

	plans = frappe.get_all(
		"Production Plan",
		filters={"company": run.company, "docstatus": ["<", 2], "status": ["not in", list(OPEN_DOC_STATUSES)]},
		fields=["name", "company"],
		limit_page_length=1000,
	)
	if not plans or not _doctype_exists("Production Plan Item"):
		return []

	fields = ["name", "parent", "item_code", "planned_qty"]
	date_field = _first_field("Production Plan Item", ("planned_start_date", "schedule_date", "delivery_date"))
	warehouse_field = _first_field("Production Plan Item", ("warehouse", "fg_warehouse"))
	if date_field:
		fields.append(date_field)
	if warehouse_field:
		fields.append(warehouse_field)
	filters = {"parent": ["in", [row.name for row in plans]]}
	if run.item_code:
		filters["item_code"] = run.item_code
	rows = frappe.get_all("Production Plan Item", filters=filters, fields=fields, limit_page_length=5000)

	demands = []
	for row in rows:
		required_date = row.get(date_field) if date_field else run.horizon_start
		if required_date and not (getdate(run.horizon_start) <= getdate(required_date) <= getdate(run.horizon_end)):
			continue
		qty = flt(row.planned_qty)
		if qty <= 0:
			continue
		demands.append(
			DemandRow(
				demand_type="Production Plan",
				item_code=row.item_code,
				qty=qty,
				required_date=required_date,
				company=run.company,
				warehouse=row.get(warehouse_field) if warehouse_field else run.warehouse,
				source_doctype="Production Plan",
				source_name=row.parent,
				source_row=row.name,
				notes=_("Optional Production Plan demand"),
			)
		)
	return demands


def _collect_firm_aps_demands(run) -> list[DemandRow]:
	if not _doctype_exists("APS Schedule Result"):
		return []

	run_names = [run.aps_run] if run.aps_run else _get_open_aps_runs(run.company)
	if not run_names:
		return []

	filters: dict[str, Any] = {
		"company": run.company,
		"planning_run": ["in", run_names],
		"status": ["in", list(FIRM_APS_STATUSES)],
	}
	if run.item_code:
		filters["item_code"] = run.item_code
	fields = [
		"name",
		"planning_run",
		"company",
		"customer",
		"item_code",
		"requested_date",
		"planned_qty",
		"scheduled_qty",
		"status",
		"net_requirement",
	]
	rows = frappe.get_all(
		"APS Schedule Result",
		filters=filters,
		fields=fields,
		order_by="requested_date asc",
		limit_page_length=10000,
	)

	demands = []
	for row in rows:
		if row.requested_date and not (
			getdate(run.horizon_start) <= getdate(row.requested_date) <= getdate(run.horizon_end)
		):
			continue
		qty = _get_aps_demand_qty(row)
		if flt(row.planned_qty) > flt(row.scheduled_qty):
			_insert_exception(
				run.name,
				run.company,
				"Warning",
				"Unscheduled APS Quantity",
				row.item_code,
				"APS Schedule Result",
				row.name,
				_(
					"APS planned quantity is {0}, but only scheduled quantity {1} is firm enough for MRP."
				).format(flt(row.planned_qty), flt(row.scheduled_qty)),
			)
		if qty <= 0:
			continue
		demands.append(
			DemandRow(
				demand_type="APS",
				item_code=row.item_code,
				qty=qty,
				required_date=row.requested_date or run.horizon_start,
				company=run.company,
				warehouse=run.warehouse,
				customer=row.customer,
				source_doctype="APS Schedule Result",
				source_name=row.name,
				aps_run=row.planning_run,
				aps_result=row.name,
				notes=_("Firm APS approved or applied demand"),
			)
		)
	return demands


def _get_open_aps_runs(company: str) -> list[str]:
	if not _doctype_exists("APS Planning Run"):
		return []
	filters = {"company": company, "status": ["in", list(FIRM_APS_STATUSES)]}
	return frappe.get_all("APS Planning Run", filters=filters, pluck="name", limit_page_length=1000)


def _consume_forecast_with_sales_orders(
	forecast_demands: list[DemandRow],
	sales_order_demands: list[DemandRow],
	settings,
) -> list[DemandRow]:
	if not forecast_demands or not sales_order_demands:
		return forecast_demands

	window_days = cint(settings.forecast_consumption_window_days) or 30
	so_buckets = [{"row": row, "remaining_qty": flt(row.qty)} for row in sales_order_demands]
	result: list[DemandRow] = []

	for forecast in forecast_demands:
		remaining_forecast_qty = flt(forecast.qty)
		if remaining_forecast_qty <= 0:
			continue
		for bucket in so_buckets:
			if remaining_forecast_qty <= 0:
				break
			if flt(bucket["remaining_qty"]) <= 0:
				continue
			sales_order = bucket["row"]
			if not _forecast_matches_sales_order(forecast, sales_order, window_days):
				continue
			consume_qty = min(remaining_forecast_qty, flt(bucket["remaining_qty"]))
			remaining_forecast_qty -= consume_qty
			bucket["remaining_qty"] = max(flt(bucket["remaining_qty"]) - consume_qty, 0)

		if remaining_forecast_qty > 0:
			result.append(
				DemandRow(
					**{
						**forecast.__dict__,
						"qty": remaining_forecast_qty,
						"notes": _("Customer schedule prebuy demand, net of sales order consumption."),
					}
				)
			)
	return result


def _forecast_matches_sales_order(forecast: DemandRow, sales_order: DemandRow, window_days: int) -> bool:
	if forecast.item_code != sales_order.item_code:
		return False
	if forecast.customer and sales_order.customer and forecast.customer != sales_order.customer:
		return False
	if forecast.warehouse and sales_order.warehouse and forecast.warehouse != sales_order.warehouse:
		return False
	if forecast.linked_sales_order and sales_order.source_name and forecast.linked_sales_order != sales_order.source_name:
		return False
	if not forecast.required_date or not sales_order.required_date:
		return True
	return abs(date_diff(getdate(forecast.required_date), getdate(sales_order.required_date))) <= cint(window_days)


def _get_aps_demand_qty(row) -> float:
	return flt(row.get("scheduled_qty"))


def _dedupe_demands(demands: list[DemandRow]) -> list[DemandRow]:
	seen = set()
	result = []
	for row in demands:
		key = (
			row.demand_type,
			row.source_doctype,
			row.source_name,
			row.source_row,
			row.item_code,
			row.required_date,
		)
		if key in seen:
			continue
		seen.add(key)
		result.append(row)
	return result


def _insert_demand_snapshots(run, demands: list[DemandRow]) -> list[Any]:
	snapshots = []
	for demand in _filter_valid_demands(run, demands):
		item = _get_item_values(demand.item_code)
		bom = _get_default_bom(demand.item_code)
		doc = frappe.get_doc(
			{
				"doctype": "MRP Demand Snapshot",
				"mrp_run": run.name,
				"company": demand.company,
				"demand_type": demand.demand_type,
				"status": "Open",
				"source_doctype": demand.source_doctype,
				"source_name": demand.source_name,
				"source_row": demand.source_row,
				"customer": demand.customer,
				"aps_run": demand.aps_run,
				"aps_result": demand.aps_result,
				"item_code": demand.item_code,
				"item_name": item.get("item_name"),
				"bom": bom,
				"warehouse": demand.warehouse,
				"required_date": demand.required_date or run.horizon_start,
				"demand_qty": demand.qty,
				"remaining_qty": demand.qty,
				"notes": demand.notes,
			}
		)
		doc.insert(ignore_permissions=True)
		snapshots.append(doc)
	return snapshots


def _filter_valid_demands(run, demands: list[DemandRow]) -> list[DemandRow]:
	valid_demands = []
	for demand in demands:
		normalised_demand = _normalise_demand_item(demand)
		if normalised_demand:
			valid_demands.append(normalised_demand)
			continue
		_insert_invalid_demand_item_exception(run, demand)
	return valid_demands


def _normalise_demand_item(demand: DemandRow) -> DemandRow | None:
	item_name = _resolve_item_name(demand.item_code)
	if not item_name:
		return None
	if item_name == demand.item_code:
		return demand
	return replace(
		demand,
		item_code=item_name,
		notes=_append_note(
			demand.notes,
			_("Source item value '{0}' was resolved to Item {1}.").format(demand.item_code, item_name),
		),
	)


def _append_note(current: str | None, extra: str) -> str:
	return "; ".join(part for part in (current, extra) if part)


def _insert_invalid_demand_item_exception(run, demand: DemandRow):
	source = demand.source_name or demand.source_row or run.name
	source_doctype = demand.source_doctype or "MRP Run"
	_insert_exception(
		run.name,
		run.company,
		"Error",
		"Invalid Demand Item",
		None,
		source_doctype,
		source,
		_(
			"Demand source contains item value '{0}', but it does not match any ERPNext Item. "
			"Please correct the source document item code and rerun MRP. Source row: {1}."
		).format(demand.item_code or "", demand.source_row or ""),
	)


def _explode_demand_snapshots(run, snapshots: list[Any]) -> list[RequirementCandidate]:
	candidates = []
	for snapshot in snapshots:
		candidates.extend(_explode_snapshot(run, snapshot))
	return candidates


def _explode_snapshot(run, snapshot) -> list[RequirementCandidate]:
	if not snapshot.bom:
		return [
			_candidate_from_item(
				run,
				get_settings_dict(),
				snapshot,
				snapshot.item_code,
				snapshot.demand_qty,
				None,
				None,
				0,
				[],
				required_date=snapshot.required_date,
				missing_bom=_requires_bom_for_item(snapshot.item_code),
			)
		]

	try:
		settings = get_settings_dict()
		return _explode_bom(
			run=run,
			settings=settings,
			snapshot=snapshot,
			bom=snapshot.bom,
			required_qty=flt(snapshot.demand_qty),
			required_date=snapshot.required_date,
			level=0,
			trace=[],
			visited=set(),
		)
	except Exception as exc:
		_insert_exception(
			run.name,
			run.company,
			"Error",
			"BOM Explosion Failed",
			snapshot.item_code,
			"MRP Demand Snapshot",
			snapshot.name,
			str(exc),
		)
		return [
			_candidate_from_item(
				run,
				get_settings_dict(),
				snapshot,
				snapshot.item_code,
				snapshot.demand_qty,
				snapshot.bom,
				None,
				0,
				[],
				required_date=snapshot.required_date,
			)
		]


def _explode_bom(
	run,
	settings,
	snapshot,
	bom: str,
	required_qty: float,
	required_date,
	level: int,
	trace: list[dict[str, Any]],
	visited: set[str],
):
	if bom in visited:
		return [
			_candidate_from_item(
				run,
				settings,
				snapshot,
				snapshot.item_code,
				required_qty,
				bom,
				None,
				level,
				trace,
				required_date=required_date,
			)
		]
	visited.add(bom)
	bom_doc = frappe.get_doc("BOM", bom)
	parent_qty = flt(bom_doc.quantity) or 1
	candidates = []
	for row in bom_doc.items:
		row_qty = flt(row.get("stock_qty")) or flt(row.get("qty"))
		if row_qty <= 0:
			continue
		scrap_percent = flt(row.get("scrap")) or flt(row.get("scrap_percent"))
		gross = required_qty * row_qty / parent_qty
		scrap_qty = gross * scrap_percent / 100
		component_qty = gross + scrap_qty
		child_item = row.item_code
		child_bom = row.get("bom_no") or _get_default_bom(child_item)
		route = _resolve_supply_route(
			child_item,
			run.company,
			snapshot.customer,
			snapshot.warehouse,
			settings,
			bom_item=row,
		)
		child_trace = trace + [
			{
				"bom": bom,
				"parent_item": bom_doc.item,
				"component_item": child_item,
				"bom_qty": row_qty,
				"parent_qty": parent_qty,
				"required_qty": component_qty,
				"level": level + 1,
				"supply_mode": route.get("supply_mode"),
				"material_request_type": route.get("material_request_type"),
			}
		]
		should_explode = (
			child_bom
			and not cint(row.get("do_not_explode"))
			and route.get("supply_mode") in ("Manufacture", "Subcontracting")
		)
		candidate = _candidate_from_item(
			run,
			settings,
			snapshot,
			child_item,
			component_qty,
			bom,
			row.name,
			level + 1,
			child_trace,
			required_date=required_date,
			route=route,
			missing_bom=bool(
				not child_bom
				and route.get("supply_mode") in ("Manufacture", "Subcontracting")
				and _requires_bom_for_item(child_item)
			),
		)
		candidate.scrap_qty = scrap_qty
		candidates.append(candidate)
		if should_explode:
			child_lead_time = _get_item_lead_time(child_item)
			child_material_need_date = _get_material_need_date(required_date or snapshot.required_date, settings)
			component_required_date = _get_suggested_order_date(child_material_need_date, child_lead_time)
			candidates.extend(
				_explode_bom(
					run,
					settings,
					snapshot,
					child_bom,
					component_qty,
					component_required_date,
					level + 1,
					child_trace,
					set(visited),
				)
			)
	return candidates


def _candidate_from_item(
	run,
	settings,
	snapshot,
	item_code,
	qty,
	bom,
	bom_item,
	level,
	trace,
	required_date=None,
	route=None,
	missing_bom: bool = False,
):
	item = _get_item_values(item_code)
	requested_warehouse = snapshot.warehouse or item.get("default_warehouse")
	route = route or _resolve_supply_route(item_code, run.company, snapshot.customer, requested_warehouse, settings)
	return RequirementCandidate(
		demand_snapshot=snapshot.name,
		demand_item_code=snapshot.item_code,
		item_code=item_code,
		item_name=item.get("item_name"),
		uom=item.get("stock_uom"),
		warehouse=route.get("target_warehouse") or requested_warehouse or settings.default_target_warehouse,
		required_date=required_date or snapshot.required_date,
		gross_qty=flt(qty),
		scrap_qty=0,
		bom=bom,
		bom_item=bom_item,
		bom_level=level,
		requirement_type=_get_requirement_type(item_code),
		bom_trace=trace,
		supply_mode=route.get("supply_mode"),
		material_request_type=route.get("material_request_type"),
		source_warehouse=route.get("source_warehouse"),
		supplier=route.get("supplier"),
		customer=route.get("customer") or snapshot.customer or item.get("customer"),
		sourced_by_supplier=cint(route.get("sourced_by_supplier")),
		purchase_uom=route.get("purchase_uom"),
		min_order_qty=flt(route.get("min_order_qty")),
		order_multiple_qty=flt(route.get("order_multiple_qty")),
		supplier_lead_time_days=cint(route.get("supplier_lead_time_days")),
		procurement_source=route.get("procurement_source"),
		missing_bom=1 if missing_bom else 0,
	)


def _net_requirements(run, settings, candidates: list[RequirementCandidate]) -> list[Any]:
	grouped: dict[tuple[str, str | None], list[RequirementCandidate]] = defaultdict(list)
	for candidate in candidates:
		grouped[(candidate.item_code, candidate.warehouse)].append(candidate)

	requirements = []
	for (item_code, warehouse), rows in grouped.items():
		rows.sort(
			key=lambda row: (
				getdate(_get_material_need_date(row.required_date or run.horizon_start, settings)),
				row.demand_snapshot,
			)
		)
		supply_records = _get_supply_records(item_code, run.company, warehouse, settings, run)
		for candidate in rows:
			line = _insert_requirement_line(run, settings, candidate, supply_records)
			requirements.append(line)
		_insert_excess_supply_pegging(run, settings, item_code, warehouse, supply_records)
	return requirements


def _resolve_supply_route(item_code, company, customer, warehouse, settings, bom_item=None) -> frappe._dict:
	item = _get_item_values(item_code)
	rule = _find_supply_rule(item_code, item.get("item_group"), company, customer, warehouse)
	if rule:
		return _route_from_rule(rule, settings, item, customer)

	sourced_by_supplier = cint(bom_item.get("sourced_by_supplier")) if bom_item else 0
	if cint(item.get("is_customer_provided_item")):
		return _route_dict("Customer Provided", settings, item, customer=customer or item.get("customer"))
	if cint(item.get("is_sub_contracted_item")):
		return _route_dict("Subcontracting", settings, item, customer=customer)

	default_mr_type = item.get("default_material_request_type")
	if default_mr_type and default_mr_type != "Purchase":
		return _route_dict(_supply_mode_from_mr_type(default_mr_type), settings, item, customer=customer)
	if sourced_by_supplier:
		return _route_dict("Supplier Supplied", settings, item, customer=customer, sourced_by_supplier=1)
	if _get_default_bom(item_code):
		return _route_dict("Manufacture", settings, item, customer=customer)
	if cint(item.get("is_purchase_item")):
		return _route_dict("Purchase", settings, item, customer=customer)
	return _route_dict(_supply_mode_from_mr_type(settings.default_material_request_type), settings, item, customer=customer)


def _find_supply_rule(item_code, item_group, company, customer, warehouse):
	if not _doctype_exists("MRP Supply Rule"):
		return None
	params = {
		"company": company or "",
		"item_code": item_code or "",
		"item_group": item_group or "",
		"customer": customer or "",
		"warehouse": warehouse or "",
	}
	rows = frappe.db.sql(
		"""
		select
			name,
			company,
			item_code,
			item_group,
			customer,
			warehouse,
			supply_mode,
			material_request_type,
			source_warehouse,
			target_warehouse,
			supplier,
			purchase_uom,
			min_order_qty,
			order_multiple_qty,
			supplier_lead_time_days,
			priority
		from `tabMRP Supply Rule`
		where enabled = 1
			and (ifnull(company, '') = '' or company = %(company)s)
			and (ifnull(item_code, '') = '' or item_code = %(item_code)s)
			and (ifnull(item_group, '') = '' or item_group = %(item_group)s)
			and (ifnull(customer, '') = '' or customer = %(customer)s)
			and (ifnull(warehouse, '') = '' or warehouse = %(warehouse)s)
		order by
			ifnull(priority, 0) desc,
			case when ifnull(item_code, '') = %(item_code)s and %(item_code)s != '' then 0 else 1 end,
			case when ifnull(item_group, '') = %(item_group)s and %(item_group)s != '' then 0 else 1 end,
			case when ifnull(customer, '') = %(customer)s and %(customer)s != '' then 0 else 1 end,
			case when ifnull(warehouse, '') = %(warehouse)s and %(warehouse)s != '' then 0 else 1 end,
			modified desc
		limit 1
		""",
		params,
		as_dict=True,
	)
	return rows[0] if rows else None


def _route_from_rule(rule, settings, item, customer=None):
	supply_mode = rule.supply_mode or _supply_mode_from_mr_type(rule.material_request_type or settings.default_material_request_type)
	warehouse_defaults = _get_supply_mode_warehouse_defaults(settings, supply_mode)
	return frappe._dict(
		{
			"supply_mode": supply_mode,
			"material_request_type": rule.material_request_type or _mr_type_from_supply_mode(supply_mode, settings),
			"source_warehouse": rule.source_warehouse or warehouse_defaults.source_warehouse or (settings.default_source_warehouse if supply_mode == "Material Transfer" else None),
			"target_warehouse": rule.target_warehouse or warehouse_defaults.target_warehouse,
			"supplier": rule.supplier,
			"customer": customer or item.get("customer"),
			"sourced_by_supplier": 1 if supply_mode == "Supplier Supplied" else 0,
			"purchase_uom": rule.purchase_uom,
			"min_order_qty": flt(rule.min_order_qty),
			"order_multiple_qty": flt(rule.order_multiple_qty),
			"supplier_lead_time_days": cint(rule.supplier_lead_time_days),
			"procurement_source": rule.name,
		}
	)


def _get_supply_mode_warehouse_defaults(settings, supply_mode) -> frappe._dict:
	defaults = (settings or {}).get("warehouse_defaults") if hasattr(settings, "get") else None
	row = (defaults or {}).get(supply_mode) if hasattr(defaults, "get") else None
	return frappe._dict(
		{
			"source_warehouse": (row or {}).get("source_warehouse") if hasattr(row, "get") else None,
			"target_warehouse": (row or {}).get("target_warehouse") if hasattr(row, "get") else None,
		}
	)


def _route_dict(supply_mode, settings, item=None, customer=None, sourced_by_supplier=0):
	item = item or {}
	warehouse_defaults = _get_supply_mode_warehouse_defaults(settings, supply_mode)
	return frappe._dict(
		{
			"supply_mode": supply_mode,
			"material_request_type": _mr_type_from_supply_mode(supply_mode, settings),
			"source_warehouse": warehouse_defaults.source_warehouse or (settings.default_source_warehouse if supply_mode == "Material Transfer" else None),
			"target_warehouse": warehouse_defaults.target_warehouse,
			"supplier": item.get("default_supplier"),
			"customer": customer,
			"sourced_by_supplier": sourced_by_supplier,
			"purchase_uom": item.get("purchase_uom"),
			"min_order_qty": flt(item.get("min_order_qty")),
			"order_multiple_qty": 0,
			"supplier_lead_time_days": 0,
			"procurement_source": "Item",
		}
	)


def _supply_mode_from_mr_type(mr_type):
	if mr_type in {"Purchase", "Manufacture", "Subcontracting", "Customer Provided", "Material Transfer"}:
		return mr_type
	return "Purchase"


def _mr_type_from_supply_mode(supply_mode, settings):
	return MR_TYPE_BY_SUPPLY_MODE.get(supply_mode) or settings.default_material_request_type or "Purchase"


def _resolve_procurement_constraints(candidate: RequirementCandidate, company: str, material_need_date) -> frappe._dict:
	item = _get_item_values(candidate.item_code)
	result = frappe._dict(
		{
			"supplier": candidate.supplier or item.get("default_supplier"),
			"purchase_uom": candidate.purchase_uom or item.get("purchase_uom") or item.get("stock_uom"),
			"min_order_qty": flt(candidate.min_order_qty) or flt(item.get("min_order_qty")),
			"order_multiple_qty": flt(candidate.order_multiple_qty),
			"supplier_lead_time_days": cint(candidate.supplier_lead_time_days),
			"supplier_quotation": None,
			"item_price": None,
			"estimated_rate": 0,
			"currency": None,
			"procurement_source": candidate.procurement_source,
		}
	)
	if candidate.supply_mode != "Purchase":
		return result

	as_of_date = getdate(material_need_date or today())
	locked_supplier = bool(candidate.supplier)
	quotation = _get_supplier_quotation_option(candidate.item_code, result.supplier, as_of_date)
	if not quotation and not locked_supplier:
		quotation = _get_supplier_quotation_option(candidate.item_code, None, as_of_date)
	if quotation:
		if quotation.supplier and (not locked_supplier or not result.supplier):
			result.supplier = quotation.supplier
		result.supplier_quotation = quotation.parent
		result.estimated_rate = flt(quotation.price_list_rate or quotation.rate)
		result.currency = quotation.currency
		if cint(quotation.lead_time_days):
			result.supplier_lead_time_days = cint(quotation.lead_time_days)
			result.procurement_source = "Supplier Quotation"

	item_price = _get_item_price_option(candidate.item_code, result.supplier, as_of_date)
	if not item_price and not locked_supplier:
		item_price = _get_item_price_option(candidate.item_code, None, as_of_date)
	if item_price:
		if item_price.supplier and (not locked_supplier or not result.supplier):
			result.supplier = item_price.supplier
		result.item_price = item_price.name
		if not result.estimated_rate:
			result.estimated_rate = flt(item_price.price_list_rate)
			result.currency = item_price.currency
		if not result.order_multiple_qty:
			result.order_multiple_qty = flt(item_price.packing_unit)
		if not result.supplier_lead_time_days and cint(item_price.lead_time_days):
			result.supplier_lead_time_days = cint(item_price.lead_time_days)
		if not result.procurement_source or result.procurement_source == "Item":
			result.procurement_source = "Item Price"

	if not result.supplier:
		result.supplier = _get_item_supplier(candidate.item_code)
		if result.supplier and not result.procurement_source:
			result.procurement_source = "Item Supplier"
	if not result.procurement_source:
		result.procurement_source = "Item"
	return result


def _apply_procurement_to_candidate(candidate: RequirementCandidate, procurement):
	candidate.supplier = procurement.supplier
	candidate.purchase_uom = procurement.purchase_uom
	candidate.min_order_qty = flt(procurement.min_order_qty)
	candidate.order_multiple_qty = flt(procurement.order_multiple_qty)
	candidate.supplier_lead_time_days = cint(procurement.supplier_lead_time_days)
	candidate.supplier_quotation = procurement.supplier_quotation
	candidate.item_price = procurement.item_price
	candidate.estimated_rate = flt(procurement.estimated_rate)
	candidate.currency = procurement.currency
	candidate.procurement_source = procurement.procurement_source


def _apply_buffer_to_candidate(candidate: RequirementCandidate, run) -> frappe._dict | None:
	state = stock_buffer.get_buffer_state_for_item(candidate.item_code, run.company, candidate.warehouse, run=run, persist=False)
	if not state:
		return None
	candidate.stock_buffer = state.name
	candidate.buffer_priority = state.planning_priority
	candidate.buffer_nfp_percent = flt(state.net_flow_position_percent)
	candidate.buffer_recommended_qty = flt(state.recommended_qty)
	candidate.buffer_top_of_red = flt(state.top_of_red)
	candidate.buffer_top_of_yellow = flt(state.top_of_yellow)
	candidate.buffer_top_of_green = flt(state.top_of_green)
	candidate.buffer_lead_time_days = cint(state.get("dlt_days") or state.get("buffer_lead_time_days") or 0)
	if state.get("dlt_days") is None:
		candidate.buffer_lead_time_days = cint(
			frappe.db.get_value("MRP Stock Buffer", state.name, "dlt_days") if state.name else 0
		)
	if flt(state.get("min_order_qty")) > 0:
		candidate.min_order_qty = flt(state.min_order_qty)
	if flt(state.get("order_multiple_qty")) > 0:
		candidate.order_multiple_qty = flt(state.order_multiple_qty)
	return state


def _get_candidate_lead_time(candidate: RequirementCandidate):
	return cint(candidate.buffer_lead_time_days) or cint(candidate.supplier_lead_time_days) or _get_item_lead_time(candidate.item_code)


def _get_planned_order_qty(required_qty: float, candidate: RequirementCandidate) -> float:
	order_qty = flt(required_qty)
	if candidate.supply_mode != "Purchase" or order_qty <= 0:
		return order_qty
	if flt(candidate.min_order_qty) > 0:
		order_qty = max(order_qty, flt(candidate.min_order_qty))
	if flt(candidate.order_multiple_qty) > 0:
		multiple = flt(candidate.order_multiple_qty)
		order_qty = math.ceil(order_qty / multiple) * multiple
	return round(order_qty, 6)


def _get_procurement_constraint_warnings(candidate: RequirementCandidate, required_qty: float, order_qty: float):
	warnings = []
	if candidate.supply_mode != "Purchase":
		return warnings
	if not candidate.supplier:
		warnings.append(
			{
				"category": "Missing Supplier",
				"level": "Warning",
				"reason": _("No supplier was selected for this purchase suggestion."),
			}
		)
	if flt(order_qty) > flt(required_qty):
		warnings.append(
			{
				"category": "Purchase Constraint Rounding",
				"level": "Info",
				"reason": _(
					"Suggested order quantity was rounded from {0} to {1} by MOQ or order multiple."
				).format(round(flt(required_qty), 3), round(flt(order_qty), 3)),
			}
		)
	return warnings


def _build_procurement_summary(candidate: RequirementCandidate, required_qty: float, order_qty: float) -> str:
	parts = []
	if candidate.supplier:
		parts.append(_("Supplier: {0}").format(candidate.supplier))
	if candidate.supplier_lead_time_days:
		parts.append(_("Supplier Lead Time: {0} day(s)").format(candidate.supplier_lead_time_days))
	if candidate.min_order_qty:
		parts.append(_("MOQ: {0}").format(round(flt(candidate.min_order_qty), 3)))
	if candidate.order_multiple_qty:
		parts.append(_("Multiple: {0}").format(round(flt(candidate.order_multiple_qty), 3)))
	if flt(order_qty) > flt(required_qty):
		parts.append(_("Rounded Qty: {0}").format(round(flt(order_qty), 3)))
	if candidate.estimated_rate:
		parts.append(_("Estimated Rate: {0}").format(round(flt(candidate.estimated_rate), 4)))
	if candidate.procurement_source:
		parts.append(_("Source: {0}").format(candidate.procurement_source))
	return "; ".join(parts)


def _insert_requirement_line(run, settings, candidate: RequirementCandidate, supply_records: list[SupplyRecord]):
	gross = flt(candidate.gross_qty)
	material_need_date = _get_material_need_date(candidate.required_date or run.horizon_start, settings)
	procurement = _resolve_procurement_constraints(candidate, run.company, material_need_date)
	_apply_procurement_to_candidate(candidate, procurement)
	buffer_state = _apply_buffer_to_candidate(candidate, run)
	lead_time = _get_candidate_lead_time(candidate)
	suggested_order_date = _get_suggested_order_date(material_need_date, lead_time)
	if candidate.supply_mode in {"Supplier Supplied", "No Action"}:
		allocations = []
		remaining = 0
	else:
		allocations = _allocate_supply_records(run, settings, candidate, supply_records, gross, material_need_date, lead_time)
		remaining = max(gross - sum(flt(row["supply_qty"]) for row in allocations), 0)

	warnings: list[dict[str, Any]] = []
	if (
		buffer_state
		and cint(candidate.buffer_lead_time_days) > 0
		and cint(candidate.supplier_lead_time_days)
		and cint(candidate.supplier_lead_time_days) != cint(candidate.buffer_lead_time_days)
	):
		warnings.append(
			{
				"category": "Lead Time Source Mismatch",
				"level": "Info",
				"reason": _(
					"Supplier lead time {0} day(s) differs from Stock Buffer DLT {1} day(s); MRP used the Stock Buffer value."
				).format(candidate.supplier_lead_time_days, candidate.buffer_lead_time_days),
			}
		)
	elif buffer_state and cint(candidate.buffer_lead_time_days) <= 0 and lead_time > 0:
		warnings.append(
			{
				"category": "Lead Time Source Fallback",
				"level": "Info",
				"reason": _("Stock Buffer DLT is empty; MRP used supplier or item lead time."),
			}
		)
	if candidate.supply_mode not in {"Supplier Supplied", "No Action"} and settings.warn_missing_lead_time and lead_time <= 0:
		warnings.append(
			{
				"category": "Missing Lead Time",
				"level": "Warning",
				"reason": _("Item has no lead time. Please maintain Item Lead Time Days for more reliable MRP."),
			}
		)

	if remaining > 0:
		planned_order_qty = _get_planned_order_qty(remaining, candidate)
		order_excess_qty = max(planned_order_qty - remaining, 0)
		warnings.extend(_get_procurement_constraint_warnings(candidate, remaining, planned_order_qty))
		planned = _make_planned_supply_allocation(
			run,
			settings,
			candidate,
			planned_order_qty,
			material_need_date,
			suggested_order_date,
			lead_time,
			warnings,
		)
		allocations.append(planned)
	else:
		planned_order_qty = 0
		order_excess_qty = 0

	commitment_type = "Prebuy" if run.run_type == "Forecast Prebuy" else "Firm"
	consumed = _summarize_consumed_supply(allocations)
	prebuy_consumed = consumed.get("prebuy", 0) if run.run_type == "Firm APS" else 0
	prebuy_offset = consumed.get("prebuy", 0) if run.run_type != "Firm APS" else 0
	new_supply_qty = consumed.get("planned_supply", 0)
	pegged_supply_qty = sum(flt(row.get("supply_qty")) for row in allocations if row.get("supply_type") != "Planned Supply")
	expected_arrival_date = _get_latest_expected_arrival(allocations)
	delivery_variance_days = _get_delivery_variance(expected_arrival_date, material_need_date)
	early_supply_qty = sum(flt(row.get("supply_qty")) for row in allocations if row.get("warning_category") == "Early Supply")
	late_supply_qty = sum(flt(row.get("supply_qty")) for row in allocations if row.get("warning_category") == "Late Supply")
	line_warnings = _line_warnings_from_allocations(allocations, warnings)
	adjustment_summary = _summarize_adjustments(allocations)
	warning_summary = "; ".join(dict.fromkeys([row["reason"] for row in line_warnings if row.get("reason")]))
	status = "Ready" if remaining > 0 or prebuy_consumed > 0 else "Closed"
	supply_trace = {
		"allocations": allocations,
		"consumed": consumed,
		"prebuy_consumed": prebuy_consumed,
		"remaining_supply_after_line": _summarize_remaining_supply(supply_records),
	}
	doc = frappe.get_doc(
		{
			"doctype": "MRP Requirement Line",
			"mrp_run": run.name,
			"demand_snapshot": candidate.demand_snapshot,
			"company": run.company,
			"run_type": run.run_type,
			"status": status,
			"requirement_type": candidate.requirement_type,
			"commitment_type": commitment_type,
				"supply_mode": candidate.supply_mode,
				"material_request_type": candidate.material_request_type,
				"item_code": candidate.item_code,
				"item_name": candidate.item_name,
				"uom": candidate.uom,
				"warehouse": candidate.warehouse,
				"source_warehouse": candidate.source_warehouse,
				"supplier": candidate.supplier,
				"supplier_lead_time_days": candidate.supplier_lead_time_days,
				"customer": candidate.customer,
				"required_date": candidate.required_date,
				"material_need_date": material_need_date,
				"suggested_order_date": suggested_order_date,
				"expected_arrival_date": expected_arrival_date,
				"lead_time_days": lead_time,
				"delivery_variance_days": delivery_variance_days,
				"stock_buffer": candidate.stock_buffer,
				"buffer_priority": candidate.buffer_priority,
				"buffer_nfp_percent": candidate.buffer_nfp_percent,
				"buffer_recommended_qty": candidate.buffer_recommended_qty,
				"buffer_top_of_red": candidate.buffer_top_of_red,
				"buffer_top_of_yellow": candidate.buffer_top_of_yellow,
				"buffer_top_of_green": candidate.buffer_top_of_green,
				"buffer_lead_time_days": candidate.buffer_lead_time_days,
				"demand_item_code": candidate.demand_item_code,
			"bom": candidate.bom,
			"bom_item": candidate.bom_item,
			"bom_level": candidate.bom_level,
			"parent_requirement": candidate.parent_requirement,
			"gross_qty": gross,
			"scrap_qty": candidate.scrap_qty,
			"available_qty": consumed.get("available", 0),
			"open_mr_qty": consumed.get("open_mr", 0),
			"open_po_qty": consumed.get("open_po", 0),
			"open_wo_qty": consumed.get("open_wo", 0),
			"production_plan_supply_qty": consumed.get("production_plan", 0),
			"prebuy_available_qty": prebuy_offset + prebuy_consumed,
			"prebuy_consumed_qty": prebuy_consumed,
			"pegged_supply_qty": pegged_supply_qty,
			"new_supply_qty": new_supply_qty,
			"early_supply_qty": early_supply_qty,
			"late_supply_qty": late_supply_qty,
			"net_qty": remaining,
			"shortage_qty": remaining,
			"purchase_uom": candidate.purchase_uom,
			"min_order_qty": candidate.min_order_qty,
			"order_multiple_qty": candidate.order_multiple_qty,
			"order_excess_qty": order_excess_qty,
			"supplier_quotation": candidate.supplier_quotation,
			"item_price": candidate.item_price,
			"estimated_rate": candidate.estimated_rate,
			"estimated_amount": flt(candidate.estimated_rate) * flt(planned_order_qty),
			"currency": candidate.currency,
			"procurement_source": candidate.procurement_source,
			"procurement_constraint_summary": _build_procurement_summary(candidate, remaining, planned_order_qty),
			"warning_count": len(line_warnings),
			"adjustment_summary": adjustment_summary,
			"warning_summary": warning_summary,
			"supply_trace": _dumps(supply_trace),
			"bom_trace": _dumps(candidate.bom_trace),
		}
	)
	doc.insert(ignore_permissions=True)
	_insert_pegging_lines(run, settings, candidate, doc, allocations)
	_insert_line_warning_exceptions(run, candidate, doc, line_warnings)
	if candidate.missing_bom:
		_insert_exception(
			run.name,
			run.company,
			"Warning",
			"Missing BOM",
			candidate.item_code,
			"MRP Demand Snapshot",
			candidate.demand_snapshot,
			_("No submitted active BOM was found for an item that is planned as manufacture or subcontracting."),
			doc.name,
		)
	return doc


def _allocate_supply_records(
	run,
	settings,
	candidate: RequirementCandidate,
	supply_records: list[SupplyRecord],
	gross: float,
	material_need_date,
	lead_time: int,
) -> list[dict[str, Any]]:
	remaining = flt(gross)
	allocations = []
	for supply in _ordered_supply_records(supply_records):
		if remaining <= 0:
			break
		if flt(supply.remaining_qty) <= 0:
			continue
		qty = min(remaining, flt(supply.remaining_qty))
		supply.remaining_qty = max(flt(supply.remaining_qty) - qty, 0)
		allocations.append(
			_build_supply_allocation(
				settings=settings,
				supply=supply,
				qty=qty,
				material_need_date=material_need_date,
				lead_time=lead_time,
			)
		)
		remaining = max(remaining - qty, 0)
	return allocations


def _ordered_supply_records(supply_records: list[SupplyRecord]) -> list[SupplyRecord]:
	return sorted(
		supply_records,
		key=lambda row: (
			-row.priority,
			getdate(row.expected_arrival_date or row.supply_date or today()),
			row.source_doctype or "",
			row.source_name or "",
			row.source_row or "",
		),
	)


def _build_supply_allocation(settings, supply: SupplyRecord, qty: float, material_need_date, lead_time: int):
	warning = _classify_supply_timing(
		supply.supply_type,
		supply.expected_arrival_date or supply.supply_date,
		material_need_date,
		settings,
	)
	adjustment_action = warning.get("action") or "No Adjustment"
	if supply.supply_type == "Prebuy" and adjustment_action == "No Adjustment":
		adjustment_action = "Consume Prebuy"
	return {
		"supply_type": supply.supply_type,
		"supply_doctype": supply.source_doctype,
		"supply_name": supply.source_name,
		"supply_row": supply.source_row,
		"warehouse": supply.warehouse,
		"original_supply_qty": flt(supply.original_qty),
		"supply_qty": flt(qty),
		"remaining_supply_qty": flt(supply.remaining_qty),
		"supply_date": supply.supply_date,
		"expected_arrival_date": supply.expected_arrival_date or supply.supply_date,
		"supply_priority": supply.priority,
		"lead_time_days": lead_time,
		"delivery_variance_days": warning.get("variance_days"),
		"adjustment_action": adjustment_action,
		"adjustment_qty": flt(qty) if adjustment_action not in ("No Adjustment", "Consume Prebuy") else 0,
		"adjustment_date": material_need_date if adjustment_action in ("Expedite", "Delay") else None,
		"warning_level": warning.get("level") or "None",
		"warning_category": warning.get("category"),
		"warning_reason": warning.get("reason"),
	}


def _make_planned_supply_allocation(
	run,
	settings,
	candidate: RequirementCandidate,
	qty: float,
	material_need_date,
	suggested_order_date,
	lead_time: int,
	line_warnings: list[dict[str, Any]],
) -> dict[str, Any]:
	warnings = list(line_warnings)
	if suggested_order_date and getdate(suggested_order_date) < getdate(today()):
		warnings.append(
			{
				"category": "Past Due Order",
				"level": "Critical",
				"reason": _("Suggested order date is before today. Procurement is already behind the material need date."),
			}
		)
	expected_arrival = add_days(suggested_order_date or material_need_date, lead_time)
	return {
		"supply_type": "Planned Supply",
		"supply_doctype": "Material Request",
		"supply_name": None,
		"supply_row": None,
		"warehouse": candidate.warehouse,
		"original_supply_qty": flt(qty),
		"supply_qty": flt(qty),
		"remaining_supply_qty": 0,
		"supply_date": suggested_order_date,
		"expected_arrival_date": expected_arrival,
		"supply_priority": 0,
		"lead_time_days": lead_time,
		"delivery_variance_days": _get_delivery_variance(expected_arrival, material_need_date),
		"adjustment_action": "Create Material Request",
		"adjustment_qty": flt(qty),
		"adjustment_date": suggested_order_date,
		"warning_level": _highest_warning_level(warnings),
		"warning_category": ", ".join(dict.fromkeys([row["category"] for row in warnings if row.get("category")])),
		"warning_reason": "; ".join(dict.fromkeys([row["reason"] for row in warnings if row.get("reason")])),
	}


def _insert_pegging_lines(run, settings, candidate: RequirementCandidate, requirement_line, allocations):
	if not _doctype_exists("MRP Pegging Line"):
		return
	demand = frappe.db.get_value(
		"MRP Demand Snapshot",
		candidate.demand_snapshot,
		["demand_type", "source_doctype", "source_name", "source_row", "item_code"],
		as_dict=True,
	) or {}
	for allocation in allocations:
		doc = frappe.get_doc(
			{
				"doctype": "MRP Pegging Line",
				"mrp_run": run.name,
				"requirement_line": requirement_line.name,
				"demand_snapshot": candidate.demand_snapshot,
				"company": run.company,
				"run_type": run.run_type,
				"commitment_type": requirement_line.commitment_type,
				"status": "Proposed" if allocation.get("supply_type") == "Planned Supply" else "Allocated",
				"demand_type": demand.get("demand_type"),
				"demand_source_doctype": demand.get("source_doctype"),
				"demand_source_name": demand.get("source_name"),
				"demand_source_row": demand.get("source_row"),
				"demand_item_code": candidate.demand_item_code,
				"item_code": candidate.item_code,
				"item_name": candidate.item_name,
				"uom": candidate.uom,
				"warehouse": allocation.get("warehouse") or candidate.warehouse,
				"required_date": candidate.required_date,
				"material_need_date": requirement_line.material_need_date,
				"demand_qty": flt(candidate.gross_qty),
				"supply_type": allocation.get("supply_type"),
				"supply_doctype": allocation.get("supply_doctype"),
				"supply_name": allocation.get("supply_name"),
				"supply_row": allocation.get("supply_row"),
				"original_supply_qty": allocation.get("original_supply_qty"),
				"supply_qty": allocation.get("supply_qty"),
				"remaining_supply_qty": allocation.get("remaining_supply_qty"),
				"supply_date": allocation.get("supply_date"),
				"expected_arrival_date": allocation.get("expected_arrival_date"),
				"supply_priority": allocation.get("supply_priority"),
				"lead_time_days": allocation.get("lead_time_days"),
				"suggested_order_date": requirement_line.suggested_order_date,
				"delivery_variance_days": allocation.get("delivery_variance_days"),
				"adjustment_action": allocation.get("adjustment_action"),
				"adjustment_qty": allocation.get("adjustment_qty"),
				"adjustment_date": allocation.get("adjustment_date"),
				"warning_level": allocation.get("warning_level"),
				"warning_category": allocation.get("warning_category"),
				"warning_reason": allocation.get("warning_reason"),
			}
		)
		doc.insert(ignore_permissions=True)


def _insert_excess_supply_pegging(run, settings, item_code, warehouse, supply_records: list[SupplyRecord]):
	if not _doctype_exists("MRP Pegging Line"):
		return
	item = _get_item_values(item_code)
	for supply in supply_records:
		if supply.supply_type == "Stock" or flt(supply.remaining_qty) <= 0:
			continue
		category = _get_excess_supply_category(run, supply)
		action = _get_excess_supply_action(run, supply)
		reason = _get_excess_supply_reason(run, supply)
		doc = frappe.get_doc(
			{
				"doctype": "MRP Pegging Line",
				"mrp_run": run.name,
				"company": run.company,
				"run_type": run.run_type,
				"commitment_type": supply.commitment_type or ("Prebuy" if supply.supply_type == "Prebuy" else "Firm"),
				"status": "Excess",
				"item_code": item_code,
				"item_name": item.get("item_name"),
				"uom": item.get("stock_uom"),
				"warehouse": supply.warehouse or warehouse,
				"supply_type": supply.supply_type,
				"supply_doctype": supply.source_doctype,
				"supply_name": supply.source_name,
				"supply_row": supply.source_row,
				"original_supply_qty": supply.original_qty,
				"supply_qty": 0,
				"remaining_supply_qty": supply.remaining_qty,
				"supply_date": supply.supply_date,
				"expected_arrival_date": supply.expected_arrival_date or supply.supply_date,
				"supply_priority": supply.priority,
				"adjustment_action": action,
				"adjustment_qty": supply.remaining_qty,
				"adjustment_date": supply.supply_date,
				"warning_level": "Warning",
				"warning_category": category,
				"warning_reason": reason,
			}
		)
		doc.insert(ignore_permissions=True)
		_insert_exception(
			run.name,
			run.company,
			"Warning",
			category,
			item_code,
			supply.source_doctype or "MRP Run",
			supply.source_name or run.name,
			reason,
		)


def _is_excess_prebuy(run, supply: SupplyRecord) -> bool:
	return getattr(run, "run_type", None) == "Firm APS" and supply.supply_type == "Prebuy"


def _get_excess_supply_category(run, supply: SupplyRecord) -> str:
	return "Excess Prebuy" if _is_excess_prebuy(run, supply) else "Excess Supply"


def _get_excess_supply_action(run, supply: SupplyRecord) -> str:
	if _is_excess_prebuy(run, supply):
		return "Review Excess Prebuy"
	if supply.supply_type in ("Purchase Order", "Material Request", "Prebuy"):
		return "Cancel"
	return "Review"


def _get_excess_supply_reason(run, supply: SupplyRecord) -> str:
	if _is_excess_prebuy(run, supply):
		return _(
			"Forecast prebuy supply remains after firm APS consumption. Please review whether to keep, delay, cancel, or reassign it."
		)
	return _(
		"Existing supply is not consumed by demand in this MRP run. Please review whether it should be delayed, cancelled, or kept."
	)


def _calculate_rolling_availability(run, settings, requirements: list[Any], batch=None) -> dict[str, Any]:
    if not _doctype_exists("MRP Rolling Balance Line") or not _doctype_exists("MRP Shortage Alert"):
        return {"shortage_alert_count": 0}

    grouped: dict[tuple[str, str | None], list[Any]] = defaultdict(list)
    for line in requirements:
        if line.supply_mode in {"Supplier Supplied", "No Action"}:
            continue
        grouped[(line.item_code, line.warehouse)].append(line)

    alert_count = 0
    for (item_code, warehouse), lines in grouped.items():
        item = _get_item_values(item_code)
        buckets = _make_rolling_buckets(run.horizon_start, run.horizon_end, settings.rolling_daily_horizon_days)
        demand_events = [
            {
                "date": getdate(line.material_need_date or line.required_date or run.horizon_start),
                "qty": flt(line.gross_qty),
                "requirement_line": line.name,
                "demand_snapshot": line.demand_snapshot,
            }
            for line in lines
            if flt(line.gross_qty) > 0
        ]
        supply_events = []
        planned_events = []
        for supply in _get_supply_records(item_code, run.company, warehouse, settings, run):
            if supply.supply_type == "Stock":
                continue
            supply_events.append(
                {
                    "date": getdate(supply.expected_arrival_date or supply.supply_date or run.horizon_start),
                    "qty": flt(supply.remaining_qty),
                    "supply_type": supply.supply_type,
                    "supply_doctype": supply.source_doctype,
                    "supply_name": supply.source_name,
                }
            )
        for line in lines:
            if flt(line.new_supply_qty) <= 0 or line.supply_mode not in SUPPLY_MODES_REQUIRING_MR:
                continue
            planned_events.append(
                {
                    "date": getdate(line.expected_arrival_date or line.material_need_date or line.required_date or run.horizon_start),
                    "qty": flt(line.new_supply_qty),
                    "supply_type": "Planned Supply",
                    "requirement_line": line.name,
                }
            )

        projected = _get_available_qty(item_code, run.company, warehouse)
        safety_stock = _get_buffer_warning_qty(item_code, run.company, warehouse, run)
        lowest_projected = projected
        first_hard_shortage = None
        first_safety_gap = None
        hard_shortage_qty = 0
        safety_gap_qty = 0
        rolling_rows = []
        for bucket in buckets:
            bucket_demand = _events_in_bucket(demand_events, bucket)
            bucket_supply = _events_in_bucket(supply_events, bucket)
            bucket_planned = _events_in_bucket(planned_events, bucket)
            demand_qty = sum(flt(row["qty"]) for row in bucket_demand)
            supply_qty = sum(flt(row["qty"]) for row in bucket_supply)
            planned_qty = sum(flt(row["qty"]) for row in bucket_planned)
            opening_qty = projected
            projected = opening_qty + supply_qty + planned_qty - demand_qty
            lowest_projected = min(lowest_projected, projected)
            shortage_qty = max(-projected, 0)
            gap_qty = max(flt(safety_stock) - projected, 0)
            if shortage_qty > 0 and not first_hard_shortage:
                first_hard_shortage = bucket["start"]
                hard_shortage_qty = shortage_qty
            if gap_qty > 0 and not first_safety_gap:
                first_safety_gap = bucket["start"]
                safety_gap_qty = gap_qty
            rolling_rows.append(
                {
                    "mrp_run": run.name,
                    "company": run.company,
                    "item_code": item_code,
                    "item_name": item.get("item_name"),
                    "warehouse": warehouse,
                    "bucket_type": bucket["type"],
                    "bucket_start": bucket["start"],
                    "bucket_end": bucket["end"],
                    "opening_qty": opening_qty,
                    "demand_qty": demand_qty,
                    "supply_qty": supply_qty,
                    "planned_supply_qty": planned_qty,
                    "projected_qty": projected,
                    "safety_stock_qty": safety_stock,
                    "shortage_qty": shortage_qty,
                    "safety_stock_gap_qty": gap_qty,
                    "warning_level": "Critical" if shortage_qty > 0 else ("Warning" if gap_qty > 0 else "None"),
                    "demand_trace": _dumps(bucket_demand),
                    "supply_trace": _dumps(bucket_supply + bucket_planned),
                }
            )
        _bulk_insert_records("MRP Rolling Balance Line", rolling_rows)

        alert_date = first_hard_shortage or first_safety_gap
        if alert_date:
            alert_count += 1
            affected = [row for row in demand_events if getdate(row["date"]) <= getdate(alert_date)]
            frappe.get_doc(
                {
                    "doctype": "MRP Shortage Alert",
                    "mrp_run": run.name,
                    "company": run.company,
                    "item_code": item_code,
                    "item_name": item.get("item_name"),
                    "warehouse": warehouse,
                    "warning_level": "Critical" if first_hard_shortage else "Warning",
                    "status": "Open",
                    "first_shortage_date": alert_date,
                    "shortage_qty": hard_shortage_qty,
                    "lowest_projected_qty": lowest_projected,
                    "safety_stock_qty": safety_stock,
                    "safety_stock_gap_qty": safety_gap_qty,
                    "latest_order_date": add_days(alert_date, -_get_buffer_lead_time(item_code, run.company, warehouse, run)),
                    "affected_requirement_count": len(affected),
                    "affected_requirements": _dumps(affected[:50]),
                }
            ).insert(ignore_permissions=True)
            _update_requirement_shortage_summary(lines, alert_date, lowest_projected)

    return {"shortage_alert_count": alert_count}


def _make_rolling_buckets(horizon_start, horizon_end, daily_days):
	start = getdate(horizon_start or today())
	end = getdate(horizon_end or add_days(start, 120))
	daily_until = getdate(add_days(start, cint(daily_days) or 60))
	buckets = []
	current = start
	while current <= end:
		if current <= daily_until:
			bucket_end = current
			bucket_type = "Daily"
		else:
			bucket_end = min(getdate(add_days(current, 6)), end)
			bucket_type = "Weekly"
		buckets.append({"type": bucket_type, "start": current, "end": bucket_end})
		current = getdate(add_days(bucket_end, 1))
	return buckets


def _events_in_bucket(events, bucket):
	start = getdate(bucket["start"])
	end = getdate(bucket["end"])
	return [row for row in events if start <= getdate(row["date"]) <= end]


@lru_cache(maxsize=10000)
def _get_item_safety_stock(item_code):
	if _has_field("Item", "safety_stock"):
		return flt(frappe.db.get_value("Item", item_code, "safety_stock"))
	return 0


def _get_buffer_warning_qty(item_code, company, warehouse, run=None):
	state = stock_buffer.get_buffer_state_for_item(item_code, company, warehouse, run=run, persist=False)
	if state:
		return flt(state.top_of_red)
	return _get_item_safety_stock(item_code)


def _get_buffer_lead_time(item_code, company, warehouse, run=None):
	state = stock_buffer.get_buffer_state_for_item(item_code, company, warehouse, run=run, persist=False)
	if state and cint(state.dlt_days) > 0:
		return cint(state.dlt_days)
	return _get_item_lead_time(item_code)


def _update_requirement_shortage_summary(lines, first_shortage_date, lowest_projected):
	if not _has_field("MRP Requirement Line", "first_shortage_date"):
		return
	for line in lines:
		frappe.db.set_value(
			"MRP Requirement Line",
			line.name,
			{
				"first_shortage_date": first_shortage_date,
				"lowest_projected_qty": lowest_projected,
			},
			update_modified=False,
		)


def _insert_line_warning_exceptions(run, candidate, line, warnings):
	for warning in warnings:
		category = warning.get("category")
		reason = warning.get("reason")
		if not category or not reason:
			continue
		_insert_exception(
			run.name,
			run.company,
			"Warning",
			category,
			candidate.item_code,
			"MRP Requirement Line",
			line.name,
			reason,
			line.name,
		)


def _summarize_consumed_supply(allocations) -> dict[str, float]:
	result: dict[str, float] = defaultdict(float)
	for row in allocations:
		key = _supply_type_bucket(row.get("supply_type"))
		result[key] += flt(row.get("supply_qty"))
	return result


def _supply_type_bucket(supply_type):
	return {
		"Stock": "available",
		"Material Request": "open_mr",
		"Purchase Order": "open_po",
		"Work Order": "open_wo",
		"Production Plan": "production_plan",
		"Prebuy": "prebuy",
		"Planned Supply": "planned_supply",
	}.get(supply_type or "", "other")


def _summarize_remaining_supply(supply_records: list[SupplyRecord]) -> dict[str, float]:
	result: dict[str, float] = defaultdict(float)
	for row in supply_records:
		result[_supply_type_bucket(row.supply_type)] += flt(row.remaining_qty)
	return dict(result)


def _line_warnings_from_allocations(allocations, extra_warnings):
	warnings = list(extra_warnings or [])
	for row in allocations:
		if row.get("warning_category") and row.get("warning_reason") and row.get("warning_level") != "None":
			for category, reason in zip(
				[str(part).strip() for part in str(row.get("warning_category") or "").split(",")],
				[str(part).strip() for part in str(row.get("warning_reason") or "").split(";")],
				strict=False,
			):
				if category and reason:
					warnings.append({"category": category, "level": row.get("warning_level"), "reason": reason})
	return _dedupe_warning_dicts(warnings)


def _dedupe_warning_dicts(warnings):
	seen = set()
	result = []
	for row in warnings:
		key = (row.get("category"), row.get("reason"))
		if key in seen:
			continue
		seen.add(key)
		result.append(row)
	return result


def _summarize_adjustments(allocations):
	actions = defaultdict(float)
	for row in allocations:
		action = row.get("adjustment_action")
		if not action or action == "No Adjustment":
			continue
		actions[action] += flt(row.get("adjustment_qty") or row.get("supply_qty"))
	return ", ".join(f"{action}: {flt(qty, 3)}" for action, qty in actions.items())


def _get_latest_expected_arrival(allocations):
	dates = [row.get("expected_arrival_date") for row in allocations if row.get("expected_arrival_date")]
	if not dates:
		return None
	return max(getdate(value) for value in dates)


def _get_delivery_variance(expected_arrival_date, material_need_date):
	if not expected_arrival_date or not material_need_date:
		return 0
	return cint(date_diff(getdate(expected_arrival_date), getdate(material_need_date)))


def _get_material_need_date(required_date, settings):
	base_date = getdate(required_date or today())
	if cint(settings.use_material_need_date_for_pegging):
		return add_days(base_date, -cint(settings.material_staging_days))
	return base_date


def _get_suggested_order_date(material_need_date, lead_time: int):
	return add_days(material_need_date or today(), -cint(lead_time))


def _get_material_request_schedule_date(*date_values):
	for date_value in date_values:
		if date_value:
			schedule_date = getdate(date_value)
			return max(schedule_date, getdate(today()))
	return getdate(today())


def _classify_supply_timing(supply_type, expected_arrival_date, material_need_date, settings) -> dict[str, Any]:
	if not expected_arrival_date or not material_need_date:
		return {"variance_days": 0}
	variance = _get_delivery_variance(expected_arrival_date, material_need_date)
	if variance > cint(settings.late_supply_tolerance_days):
		return {
			"category": "Late Supply",
			"level": "Warning",
			"action": "Expedite",
			"variance_days": variance,
			"reason": _("Expected arrival is {0} day(s) after the material need date.").format(variance),
		}
	if supply_type != "Stock" and variance < -cint(settings.early_supply_warning_days):
		return {
			"category": "Early Supply",
			"level": "Warning",
			"action": "Delay",
			"variance_days": variance,
			"reason": _("Expected arrival is {0} day(s) before the material need date.").format(abs(variance)),
		}
	return {"variance_days": variance}


def _highest_warning_level(warnings):
	levels = {"None": 0, "Info": 1, "Warning": 2, "Critical": 3}
	highest = "None"
	for row in warnings or []:
		level = row.get("level") or "None"
		if levels.get(level, 0) > levels[highest]:
			highest = level
	return highest


def _get_supply_records(item_code: str, company: str, warehouse: str | None, settings, run) -> list[SupplyRecord]:
	records = []
	records.extend(_get_stock_supply_records(item_code, company, warehouse, run))
	records.extend(_get_open_material_request_supply_records(item_code, company, warehouse, exclude_prebuy=True))
	records.extend(_get_open_purchase_order_supply_records(item_code, company, warehouse))
	records.extend(_get_open_work_order_supply_records(item_code, company, warehouse))
	records.extend(_get_production_plan_supply_records(item_code, company, warehouse, settings))
	records.extend(_get_prebuy_supply_records(item_code, company, warehouse))
	return [row for row in records if flt(row.remaining_qty) > 0]


def _create_proposal_batch(run, settings, requirements: list[Any]):
	rows = []
	for line in requirements:
		proposal_schedule_date = _get_material_request_schedule_date(
			line.material_need_date,
			line.required_date,
		)
		if flt(line.prebuy_consumed_qty) > 0:
			rows.append(
				{
					"requirement_line": line.name,
					"item_code": line.item_code,
					"item_name": line.item_name,
					"warehouse": line.warehouse,
					"from_warehouse": line.source_warehouse,
					"required_date": line.required_date,
					"schedule_date": proposal_schedule_date,
					"suggested_order_date": line.suggested_order_date,
					"qty": line.prebuy_consumed_qty,
					"original_qty": line.prebuy_consumed_qty,
					"original_schedule_date": proposal_schedule_date,
					"uom": line.uom,
					"material_request_type": line.material_request_type or settings.default_material_request_type,
					"supply_mode": line.supply_mode,
					"customer": line.customer,
					"supplier": line.supplier,
					"stock_buffer": line.stock_buffer,
					"buffer_priority": line.buffer_priority,
					"buffer_nfp_percent": line.buffer_nfp_percent,
					"buffer_recommended_qty": line.buffer_recommended_qty,
					"buffer_top_of_red": line.buffer_top_of_red,
					"buffer_top_of_yellow": line.buffer_top_of_yellow,
					"buffer_top_of_green": line.buffer_top_of_green,
					"buffer_lead_time_days": line.buffer_lead_time_days,
					"commitment_type": "Prebuy",
					"action": "Consume Prebuy",
					"status": "Pending",
					"notes": _("Consume previous forecast prebuy for firm APS demand."),
				}
			)
		if flt(line.net_qty) > 0 and line.supply_mode in SUPPLY_MODES_REQUIRING_MR:
			proposal_qty = flt(line.new_supply_qty) or flt(line.net_qty)
			rows.append(
				{
					"requirement_line": line.name,
					"item_code": line.item_code,
					"item_name": line.item_name,
					"warehouse": line.warehouse,
					"from_warehouse": line.source_warehouse,
					"required_date": line.required_date,
					"schedule_date": proposal_schedule_date,
					"suggested_order_date": line.suggested_order_date,
					"qty": proposal_qty,
					"original_qty": proposal_qty,
					"original_schedule_date": proposal_schedule_date,
					"uom": line.uom,
					"material_request_type": line.material_request_type or _get_material_request_type(line.item_code, settings),
					"supply_mode": line.supply_mode,
					"customer": line.customer,
					"supplier": line.supplier,
					"supplier_lead_time_days": line.supplier_lead_time_days,
					"purchase_uom": line.purchase_uom,
					"min_order_qty": line.min_order_qty,
					"order_multiple_qty": line.order_multiple_qty,
					"order_excess_qty": line.order_excess_qty,
					"stock_buffer": line.stock_buffer,
					"buffer_priority": line.buffer_priority,
					"buffer_nfp_percent": line.buffer_nfp_percent,
					"buffer_recommended_qty": line.buffer_recommended_qty,
					"buffer_top_of_red": line.buffer_top_of_red,
					"buffer_top_of_yellow": line.buffer_top_of_yellow,
					"buffer_top_of_green": line.buffer_top_of_green,
					"buffer_lead_time_days": line.buffer_lead_time_days,
					"supplier_quotation": line.supplier_quotation,
					"item_price": line.item_price,
					"estimated_rate": line.estimated_rate,
					"estimated_amount": line.estimated_amount,
					"currency": line.currency,
					"procurement_source": line.procurement_source,
					"procurement_constraint_summary": line.procurement_constraint_summary,
					"commitment_type": "Prebuy" if run.run_type == "Forecast Prebuy" else "Firm",
					"action": "Create Material Request",
					"status": "Pending",
				}
			)

	if not rows:
		return None

	batch = frappe.get_doc(
		{
			"doctype": "MRP Proposal Batch",
			"mrp_run": run.name,
			"company": run.company,
			"proposal_type": run.run_type,
			"status": "Ready",
			"generated_by": frappe.session.user,
			"generated_on": now_datetime(),
			"item_count": len(rows),
			"total_qty": sum(flt(row["qty"]) for row in rows),
			"items": rows,
		}
	)
	batch.insert(ignore_permissions=True)
	return batch


def _supersede_overlapping_proposal_batches(batch, run):
	if not _has_field("MRP Proposal Batch", "superseded_by_run"):
		return
	proposal_type = batch.proposal_type if batch else run.run_type
	candidates = frappe.get_all(
		"MRP Proposal Batch",
		filters={
			"company": run.company,
			"proposal_type": proposal_type,
			"status": ["in", list(PROPOSAL_ACTIVE_STATUSES)],
		},
		fields=["name", "mrp_run"],
		limit_page_length=5000,
	)
	if not candidates:
		return
	now_value = now_datetime()
	for candidate in candidates:
		if batch and candidate.name == batch.name:
			continue
		old_run = _get_run_scope(candidate.mrp_run)
		if not old_run or not _run_scope_covers(run, old_run):
			continue
		values = {
			"status": "Superseded",
			"superseded_by_run": run.name,
			"superseded_on": now_value,
			"superseded_reason": _("Superseded by newer MRP Run {0}.").format(run.name),
		}
		if batch:
			values["superseded_by_batch"] = batch.name
		frappe.db.set_value("MRP Proposal Batch", candidate.name, _existing_field_values("MRP Proposal Batch", values))


def _get_run_scope(mrp_run: str | None):
	if not mrp_run:
		return None
	fields = ["name", "company", "run_type", "horizon_start", "horizon_end", "item_code", "customer", "warehouse"]
	return frappe.db.get_value("MRP Run", mrp_run, fields, as_dict=True)


def _run_scope_covers(new_run, old_run) -> bool:
	if not old_run:
		return False
	if old_run.company != new_run.company or old_run.run_type != new_run.run_type:
		return False
	if old_run.horizon_start and new_run.horizon_start and getdate(old_run.horizon_start) < getdate(new_run.horizon_start):
		return False
	if old_run.horizon_end and new_run.horizon_end and getdate(old_run.horizon_end) > getdate(new_run.horizon_end):
		return False
	for fieldname in ("item_code", "customer", "warehouse"):
		old_value = old_run.get(fieldname)
		new_value = new_run.get(fieldname)
		if old_value and new_value and old_value != new_value:
			return False
		if not old_value and new_value:
			return False
	return True


def _existing_field_values(doctype: str, values: dict[str, Any]) -> dict[str, Any]:
	return {fieldname: value for fieldname, value in values.items() if _has_field(doctype, fieldname)}


def _apply_proposal_item_values(row, values: dict[str, Any], is_manual_row: bool = False):
	editable_fields = {
		"warehouse",
		"from_warehouse",
		"required_date",
		"schedule_date",
		"qty",
		"uom",
		"material_request_type",
		"supply_mode",
		"customer",
		"supplier",
		"commitment_type",
		"action",
		"status",
		"skip_reason",
		"notes",
	}
	if is_manual_row:
		editable_fields.update({"item_code", "item_name", "requirement_line"})

	before = {fieldname: row.get(fieldname) for fieldname in editable_fields if hasattr(row, "get")}
	for fieldname in editable_fields:
		if fieldname in values:
			row.set(fieldname, values.get(fieldname))

	if not row.status:
		row.status = "Pending"
	if not row.action:
		row.action = "Create Material Request"
	if row.action == "No Action":
		row.status = "Skipped"
	if row.status == "Skipped" and not row.skip_reason:
		row.skip_reason = _("Skipped by planner.")
	if flt(row.qty) <= 0:
		row.action = "No Action"
		row.status = "Skipped"
		row.skip_reason = row.skip_reason or _("Quantity is zero.")

	if not row.supply_mode:
		row.supply_mode = _supply_mode_from_mr_type(row.material_request_type)
	if not row.material_request_type:
		row.material_request_type = _mr_type_from_supply_mode(row.supply_mode, get_settings_dict())

	after = {fieldname: row.get(fieldname) for fieldname in editable_fields if hasattr(row, "get")}
	if is_manual_row or before != after:
		row.manual_override = 1
	if row.original_qty and flt(row.qty) != flt(row.original_qty):
		row.manual_override = 1
	if row.original_schedule_date and row.schedule_date != row.original_schedule_date:
		row.manual_override = 1
	if flt(row.estimated_rate) and flt(row.qty):
		row.estimated_amount = flt(row.estimated_rate) * flt(row.qty)


def _recalculate_proposal_batch_totals(batch):
	active_rows = [
		row
		for row in batch.items
		if row.status != "Skipped" and row.action != "No Action" and flt(row.qty) > 0
	]
	batch.item_count = len(active_rows)
	batch.total_qty = sum(flt(row.qty) for row in active_rows)


def _create_excess_prebuy_exceptions(run, requirements):
	if run.run_type != "Firm APS":
		return
	for line in requirements:
		if flt(line.prebuy_available_qty) > flt(line.prebuy_consumed_qty) and flt(line.net_qty) == 0:
			continue


def _validate_proposal_row_for_release(row, mr_type):
	if mr_type == "Subcontracting" and _has_field("Item", "is_sub_contracted_item"):
		if not cint(frappe.db.get_value("Item", row.item_code, "is_sub_contracted_item")):
			frappe.throw(_("Item {0} is not a subcontracted item.").format(row.item_code))
	if mr_type == "Customer Provided" and _has_field("Item", "is_customer_provided_item"):
		if not cint(frappe.db.get_value("Item", row.item_code, "is_customer_provided_item")):
			frappe.throw(_("Item {0} is not a customer provided item.").format(row.item_code))
	if mr_type == "Material Transfer" and not row.from_warehouse:
		frappe.throw(_("Source Warehouse is required for Material Transfer item {0}.").format(row.item_code))


def _make_material_request(batch, rows, mr_type, commitment_type, warehouse, from_warehouse, customer, supplier, settings):
	schedule_date = min(
		[
			_get_material_request_schedule_date(row.schedule_date, row.required_date)
			for row in rows
			if row.schedule_date or row.required_date
		]
		or [getdate(today())]
	)
	doc = frappe.new_doc("Material Request")
	doc.material_request_type = mr_type
	doc.company = batch.company
	doc.schedule_date = schedule_date
	if customer and doc.meta.has_field("customer"):
		doc.customer = customer
	_set_if_has(doc, "custom_mrp_run", batch.mrp_run)
	_set_if_has(doc, "custom_mrp_commitment_type", commitment_type)
	if len(rows) == 1:
		_set_if_has(doc, "custom_mrp_requirement", rows[0].requirement_line)
	for row in rows:
		item_row = {
			"item_code": row.item_code,
			"qty": row.qty,
			"schedule_date": _get_material_request_schedule_date(row.schedule_date, row.required_date, schedule_date),
			"warehouse": row.warehouse or warehouse or settings.default_target_warehouse,
			"from_warehouse": row.from_warehouse or from_warehouse,
			"uom": row.uom,
		}
		if mr_type != "Material Transfer":
			item_row.pop("from_warehouse", None)
		doc.append("items", {key: value for key, value in item_row.items() if value is not None})
		child = doc.items[-1]
		_set_if_has(child, "custom_mrp_run", batch.mrp_run)
		_set_if_has(child, "custom_mrp_requirement", row.requirement_line)
		_set_if_has(child, "custom_mrp_commitment_type", commitment_type)
		_set_if_has(child, "custom_mrp_consumed_qty", 0)
		_set_if_has(child, "custom_mrp_remaining_qty", row.qty)
		_set_if_has(child, "custom_mrp_supplier", row.supplier or supplier)
		_set_if_has(child, "custom_mrp_supplier_quotation", row.supplier_quotation)
		_set_if_has(child, "custom_mrp_item_price", row.item_price)
		_set_if_has(child, "custom_mrp_estimated_rate", row.estimated_rate)
		_set_if_has(child, "custom_mrp_estimated_amount", row.estimated_amount)
		_set_if_has(child, "custom_mrp_procurement_summary", row.procurement_constraint_summary)
		_apply_aps_trace(child, row.requirement_line)
	doc.insert(ignore_permissions=True)
	if settings.auto_submit_material_request:
		doc.submit()
	return doc


def _find_material_request_item_name(doc, proposal_row) -> str | None:
	for row in doc.items:
		if row.item_code == proposal_row.item_code and flt(row.qty) == flt(proposal_row.qty):
			return row.name
	return None


def _apply_aps_trace(child, requirement_line):
	if not requirement_line:
		return
	demand_snapshot = frappe.db.get_value("MRP Requirement Line", requirement_line, "demand_snapshot")
	if not demand_snapshot:
		return
	aps = frappe.db.get_value(
		"MRP Demand Snapshot",
		demand_snapshot,
		["aps_run", "aps_result"],
		as_dict=True,
	)
	if not aps:
		return
	_set_if_has(child, "custom_aps_run", aps.aps_run)
	_set_if_has(child, "custom_aps_result", aps.aps_result)


def _consume_prebuy_sources(item_code, company, warehouse, qty, requirement_line):
	if qty <= 0 or not _has_field("Material Request Item", "custom_mrp_commitment_type"):
		return 0

	params = {"item_code": item_code, "company": company}
	warehouse_clause = " and mri.warehouse = %(warehouse)s" if warehouse else ""
	if warehouse:
		params["warehouse"] = warehouse
	rows = frappe.db.sql(
		f"""
		select
			mri.name,
			mri.qty,
			mri.stock_qty,
			mri.conversion_factor,
			ifnull(mri.custom_mrp_consumed_qty, 0) as consumed_qty,
			case
				when ifnull(mri.custom_mrp_remaining_qty, 0) > 0 then mri.custom_mrp_remaining_qty
				else greatest(
					case
						when ifnull(mri.stock_qty, 0) > 0 then mri.stock_qty
						else ifnull(mri.qty, 0) * ifnull(nullif(mri.conversion_factor, 0), 1)
					end - ifnull(mri.custom_mrp_consumed_qty, 0),
					0
				)
			end as remaining_qty
		from `tabMaterial Request Item` mri
		inner join `tabMaterial Request` mr on mr.name = mri.parent
		where mri.item_code = %(item_code)s
			and mr.company = %(company)s
			and mr.docstatus < 2
			and ifnull(mr.status, '') not in ('Closed', 'Stopped', 'Cancelled')
			and ifnull(mri.custom_mrp_commitment_type, '') = 'Prebuy'
			{warehouse_clause}
		order by mri.schedule_date asc, mri.creation asc
		""",
		params,
		as_dict=True,
	)
	remaining_need = qty
	consumed_total = 0
	for row in rows:
		available = flt(row.remaining_qty)
		consume = min(available, remaining_need)
		if consume <= 0:
			continue
		frappe.db.set_value(
			"Material Request Item",
			row.name,
			{
				"custom_mrp_consumed_qty": flt(row.consumed_qty) + consume,
				"custom_mrp_remaining_qty": max(available - consume, 0),
				"custom_mrp_requirement": requirement_line,
			},
		)
		remaining_need -= consume
		consumed_total += consume
		if remaining_need <= 0:
			break
	return consumed_total


def _get_stock_supply_records(item_code, company, warehouse=None, run=None) -> list[SupplyRecord]:
	if not _doctype_exists("Bin") or not _doctype_exists("Warehouse"):
		return []
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse:
		warehouse_clause = " and bin.warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	rows = frappe.db.sql(
		f"""
		select
			bin.warehouse,
			sum(bin.actual_qty) as qty
		from `tabBin` bin
		inner join `tabWarehouse` wh on wh.name = bin.warehouse
		where bin.item_code = %(item_code)s
			and wh.company = %(company)s
			and ifnull(bin.actual_qty, 0) > 0
			{warehouse_clause}
		group by bin.warehouse
		""",
		params,
		as_dict=True,
	)
	supply_date = (run.planning_date if run else None) or today()
	return [
		SupplyRecord(
			supply_type="Stock",
			source_doctype="Bin",
			source_name=row.warehouse,
			item_code=item_code,
			company=company,
			warehouse=row.warehouse,
			original_qty=flt(row.qty),
			remaining_qty=flt(row.qty),
			supply_date=supply_date,
			expected_arrival_date=supply_date,
			priority=95,
		)
		for row in rows
		if flt(row.qty) > 0
	]


def _get_open_material_request_supply_records(item_code, company, warehouse=None, exclude_prebuy=False) -> list[SupplyRecord]:
	if not _doctype_exists("Material Request"):
		return []
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse:
		warehouse_clause = " and mri.warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	prebuy_clause = ""
	if exclude_prebuy and _has_field("Material Request Item", "custom_mrp_commitment_type"):
		prebuy_clause = " and ifnull(mri.custom_mrp_commitment_type, '') != 'Prebuy'"
	rows = frappe.db.sql(
		f"""
		select
			mri.name,
			mri.parent,
			mri.warehouse,
			mri.schedule_date,
			mri.qty,
			mri.ordered_qty,
			mri.received_qty,
			mri.stock_qty,
			mri.conversion_factor
		from `tabMaterial Request Item` mri
		inner join `tabMaterial Request` mr on mr.name = mri.parent
		where mri.item_code = %(item_code)s
			and mr.company = %(company)s
			and mr.docstatus < 2
			and ifnull(mr.status, '') not in ('Closed', 'Stopped', 'Cancelled')
			{warehouse_clause}
			{prebuy_clause}
		order by mri.schedule_date asc, mri.creation asc
		""",
		params,
		as_dict=True,
	)
	return [
		SupplyRecord(
			supply_type="Material Request",
			source_doctype="Material Request",
			source_name=row.parent,
			source_row=row.name,
			item_code=item_code,
			company=company,
			warehouse=row.warehouse,
			original_qty=_stock_qty(row),
			remaining_qty=_material_request_remaining_stock_qty(row),
			supply_date=row.schedule_date,
			expected_arrival_date=row.schedule_date,
			priority=90,
			commitment_type="Firm",
		)
		for row in rows
		if _material_request_remaining_stock_qty(row) > 0
	]


def _get_prebuy_supply_records(item_code, company, warehouse=None) -> list[SupplyRecord]:
	if not _has_field("Material Request Item", "custom_mrp_commitment_type"):
		return []
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse:
		warehouse_clause = " and mri.warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	rows = frappe.db.sql(
		f"""
		select
			mri.name,
			mri.parent,
			mri.warehouse,
			mri.schedule_date,
			mri.qty,
			mri.stock_qty,
			mri.conversion_factor,
			case
				when ifnull(mri.custom_mrp_remaining_qty, 0) > 0 then mri.custom_mrp_remaining_qty
				else greatest(
					case
						when ifnull(mri.stock_qty, 0) > 0 then mri.stock_qty
						else ifnull(mri.qty, 0) * ifnull(nullif(mri.conversion_factor, 0), 1)
					end - ifnull(mri.custom_mrp_consumed_qty, 0),
					0
				)
			end as remaining_qty
		from `tabMaterial Request Item` mri
		inner join `tabMaterial Request` mr on mr.name = mri.parent
		where mri.item_code = %(item_code)s
			and mr.company = %(company)s
			and mr.docstatus < 2
			and ifnull(mr.status, '') not in ('Closed', 'Stopped', 'Cancelled')
			and ifnull(mri.custom_mrp_commitment_type, '') = 'Prebuy'
			{warehouse_clause}
		order by mri.schedule_date asc, mri.creation asc
		""",
		params,
		as_dict=True,
	)
	return [
		SupplyRecord(
			supply_type="Prebuy",
			source_doctype="Material Request",
			source_name=row.parent,
			source_row=row.name,
			item_code=item_code,
			company=company,
			warehouse=row.warehouse,
			original_qty=_stock_qty(row),
			remaining_qty=flt(row.remaining_qty),
			supply_date=row.schedule_date,
			expected_arrival_date=row.schedule_date,
			priority=50,
			commitment_type="Prebuy",
		)
		for row in rows
		if flt(row.remaining_qty) > 0
	]


def _get_open_purchase_order_supply_records(item_code, company, warehouse=None) -> list[SupplyRecord]:
	if not _doctype_exists("Purchase Order"):
		return []
	date_field = _first_field("Purchase Order Item", ("schedule_date", "expected_delivery_date", "delivery_date"))
	date_expr = f"poi.`{date_field}`" if date_field else "po.transaction_date"
	warehouse_field = "warehouse" if _has_field("Purchase Order Item", "warehouse") else None
	warehouse_select = f"poi.`{warehouse_field}` as warehouse" if warehouse_field else "null as warehouse"
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse and warehouse_field:
		warehouse_clause = f" and poi.`{warehouse_field}` = %(warehouse)s"
		params["warehouse"] = warehouse
	rows = frappe.db.sql(
		f"""
		select
			poi.name,
			poi.parent,
			{warehouse_select},
			{date_expr} as supply_date,
			poi.qty,
			poi.received_qty,
			poi.conversion_factor
		from `tabPurchase Order Item` poi
		inner join `tabPurchase Order` po on po.name = poi.parent
		where poi.item_code = %(item_code)s
			and po.company = %(company)s
			and po.docstatus = 1
			and ifnull(po.status, '') not in ('Closed', 'Stopped', 'Cancelled')
			{warehouse_clause}
		order by {date_expr} asc, poi.creation asc
		""",
		params,
		as_dict=True,
	)
	return [
		SupplyRecord(
			supply_type="Purchase Order",
			source_doctype="Purchase Order",
			source_name=row.parent,
			source_row=row.name,
			item_code=item_code,
			company=company,
			warehouse=row.warehouse,
			original_qty=_stock_qty(row),
			remaining_qty=_purchase_order_remaining_stock_qty(row),
			supply_date=row.supply_date,
			expected_arrival_date=row.supply_date,
			priority=80,
			commitment_type="Firm",
		)
		for row in rows
		if _purchase_order_remaining_stock_qty(row) > 0
	]


def _get_open_work_order_supply_records(item_code, company, warehouse=None) -> list[SupplyRecord]:
	if not _doctype_exists("Work Order"):
		return []
	date_field = _first_field("Work Order", ("planned_end_date", "expected_delivery_date", "planned_start_date"))
	date_expr = f"wo.`{date_field}`" if date_field else "wo.creation"
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse and _has_field("Work Order", "fg_warehouse"):
		warehouse_clause = " and wo.fg_warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	rows = frappe.db.sql(
		f"""
		select
			wo.name,
			wo.fg_warehouse as warehouse,
			{date_expr} as supply_date,
			wo.qty,
			greatest(ifnull(wo.qty, 0) - ifnull(wo.produced_qty, 0), 0) as remaining_qty
		from `tabWork Order` wo
		where wo.production_item = %(item_code)s
			and wo.company = %(company)s
			and wo.docstatus < 2
			and ifnull(wo.status, '') not in ('Closed', 'Stopped', 'Completed', 'Cancelled')
			{warehouse_clause}
		order by {date_expr} asc, wo.creation asc
		""",
		params,
		as_dict=True,
	)
	return [
		SupplyRecord(
			supply_type="Work Order",
			source_doctype="Work Order",
			source_name=row.name,
			item_code=item_code,
			company=company,
			warehouse=row.warehouse,
			original_qty=flt(row.qty),
			remaining_qty=flt(row.remaining_qty),
			supply_date=row.supply_date,
			expected_arrival_date=row.supply_date,
			priority=70,
			commitment_type="Firm",
		)
		for row in rows
		if flt(row.remaining_qty) > 0
	]


def _get_production_plan_supply_records(item_code, company, warehouse, settings) -> list[SupplyRecord]:
	if not settings.include_production_plan_as_supply:
		return []
	if not _doctype_exists("Production Plan"):
		return []

	plans = frappe.get_all(
		"Production Plan",
		filters={"company": company, "docstatus": 1, "status": ["not in", list(OPEN_DOC_STATUSES)]},
		fields=["name"],
		limit_page_length=5000,
	)
	if not plans:
		return []

	plan_names = [row.name for row in plans]
	records = []
	records.extend(_get_production_plan_item_supply_records(item_code, company, warehouse, plan_names))
	records.extend(_get_production_plan_sub_assembly_supply_records(item_code, company, warehouse, plan_names))
	return records


def _get_production_plan_item_supply_records(item_code, company, warehouse, plan_names) -> list[SupplyRecord]:
	if not _doctype_exists("Production Plan Item"):
		return []
	fields = ["name", "parent", "item_code", "planned_qty"]
	for fieldname in ("pending_qty", "ordered_qty", "produced_qty"):
		if _has_field("Production Plan Item", fieldname):
			fields.append(fieldname)
	date_field = _first_field("Production Plan Item", ("planned_start_date", "schedule_date", "delivery_date"))
	warehouse_field = _first_field("Production Plan Item", ("warehouse", "fg_warehouse"))
	if date_field:
		fields.append(date_field)
	if warehouse_field:
		fields.append(warehouse_field)
	filters = {"parent": ["in", plan_names], "item_code": item_code}
	if warehouse and warehouse_field:
		filters[warehouse_field] = warehouse
	rows = frappe.get_all(
		"Production Plan Item",
		filters=filters,
		fields=fields,
		order_by=f"{date_field or 'creation'} asc, creation asc",
		limit_page_length=10000,
	)
	return [
		SupplyRecord(
			supply_type="Production Plan",
			source_doctype="Production Plan",
			source_name=row.parent,
			source_row=row.name,
			item_code=item_code,
			company=company,
			warehouse=row.get(warehouse_field) if warehouse_field else warehouse,
			original_qty=flt(row.planned_qty),
			remaining_qty=_production_plan_remaining_qty(row),
			supply_date=row.get(date_field) if date_field else today(),
			expected_arrival_date=row.get(date_field) if date_field else today(),
			priority=60,
			commitment_type="Firm",
		)
		for row in rows
		if _production_plan_remaining_qty(row) > 0
	]


def _get_production_plan_sub_assembly_supply_records(item_code, company, warehouse, plan_names) -> list[SupplyRecord]:
	if not _doctype_exists("Production Plan Sub Assembly Item"):
		return []
	fields = ["name", "parent", "production_item", "qty"]
	for fieldname in ("ordered_qty", "received_qty", "wo_produced_qty"):
		if _has_field("Production Plan Sub Assembly Item", fieldname):
			fields.append(fieldname)
	date_field = _first_field("Production Plan Sub Assembly Item", ("schedule_date", "planned_start_date"))
	warehouse_field = _first_field("Production Plan Sub Assembly Item", ("fg_warehouse", "warehouse"))
	if date_field:
		fields.append(date_field)
	if warehouse_field:
		fields.append(warehouse_field)
	filters = {"parent": ["in", plan_names], "production_item": item_code}
	if warehouse and warehouse_field:
		filters[warehouse_field] = warehouse
	rows = frappe.get_all(
		"Production Plan Sub Assembly Item",
		filters=filters,
		fields=fields,
		order_by=f"{date_field or 'creation'} asc, creation asc",
		limit_page_length=10000,
	)
	return [
		SupplyRecord(
			supply_type="Production Plan",
			source_doctype="Production Plan",
			source_name=row.parent,
			source_row=row.name,
			item_code=item_code,
			company=company,
			warehouse=row.get(warehouse_field) if warehouse_field else warehouse,
			original_qty=flt(row.qty),
			remaining_qty=max(flt(row.qty) - max(flt(row.get("ordered_qty")), flt(row.get("received_qty")), flt(row.get("wo_produced_qty"))), 0),
			supply_date=row.get(date_field) if date_field else today(),
			expected_arrival_date=row.get(date_field) if date_field else today(),
			priority=60,
			commitment_type="Firm",
		)
		for row in rows
		if max(flt(row.qty) - max(flt(row.get("ordered_qty")), flt(row.get("received_qty")), flt(row.get("wo_produced_qty"))), 0) > 0
	]


def _get_available_qty(item_code, company, warehouse=None) -> float:
	if not _doctype_exists("Bin") or not _doctype_exists("Warehouse"):
		return 0
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse:
		warehouse_clause = " and bin.warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	value = frappe.db.sql(
		f"""
		select sum(bin.actual_qty)
		from `tabBin` bin
		inner join `tabWarehouse` wh on wh.name = bin.warehouse
		where bin.item_code = %(item_code)s
			and wh.company = %(company)s
			{warehouse_clause}
		""",
		params,
	)[0][0]
	return flt(value)


def _get_open_material_request_qty(item_code, company, warehouse=None, exclude_prebuy=False) -> float:
	if not _doctype_exists("Material Request"):
		return 0
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse:
		warehouse_clause = " and mri.warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	prebuy_clause = ""
	if exclude_prebuy and _has_field("Material Request Item", "custom_mrp_commitment_type"):
		prebuy_clause = " and ifnull(mri.custom_mrp_commitment_type, '') != 'Prebuy'"
	value = frappe.db.sql(
		f"""
		select sum(
			greatest(
				case
					when ifnull(mri.stock_qty, 0) > 0 then mri.stock_qty
					else ifnull(mri.qty, 0) * ifnull(nullif(mri.conversion_factor, 0), 1)
				end - greatest(ifnull(mri.ordered_qty, 0), ifnull(mri.received_qty, 0)),
				0
			)
		)
		from `tabMaterial Request Item` mri
		inner join `tabMaterial Request` mr on mr.name = mri.parent
		where mri.item_code = %(item_code)s
			and mr.company = %(company)s
			and mr.docstatus < 2
			and ifnull(mr.status, '') not in ('Closed', 'Stopped', 'Cancelled')
			{warehouse_clause}
			{prebuy_clause}
		""",
		params,
	)[0][0]
	return flt(value)


def _get_prebuy_available_qty(item_code, company, warehouse=None) -> float:
	if not _has_field("Material Request Item", "custom_mrp_commitment_type"):
		return 0
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse:
		warehouse_clause = " and mri.warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	value = frappe.db.sql(
		f"""
		select sum(
			case
				when ifnull(mri.custom_mrp_remaining_qty, 0) > 0 then mri.custom_mrp_remaining_qty
				else greatest(
					case
						when ifnull(mri.stock_qty, 0) > 0 then mri.stock_qty
						else ifnull(mri.qty, 0) * ifnull(nullif(mri.conversion_factor, 0), 1)
					end - ifnull(mri.custom_mrp_consumed_qty, 0),
					0
				)
			end
		)
		from `tabMaterial Request Item` mri
		inner join `tabMaterial Request` mr on mr.name = mri.parent
		where mri.item_code = %(item_code)s
			and mr.company = %(company)s
			and mr.docstatus < 2
			and ifnull(mr.status, '') not in ('Closed', 'Stopped', 'Cancelled')
			and ifnull(mri.custom_mrp_commitment_type, '') = 'Prebuy'
			{warehouse_clause}
		""",
		params,
	)[0][0]
	return flt(value)


def _get_open_purchase_order_qty(item_code, company, warehouse=None) -> float:
	if not _doctype_exists("Purchase Order"):
		return 0
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse and _has_field("Purchase Order Item", "warehouse"):
		warehouse_clause = " and poi.warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	value = frappe.db.sql(
		f"""
		select sum(
			greatest(ifnull(poi.qty, 0) - ifnull(poi.received_qty, 0), 0)
			* ifnull(nullif(poi.conversion_factor, 0), 1)
		)
		from `tabPurchase Order Item` poi
		inner join `tabPurchase Order` po on po.name = poi.parent
		where poi.item_code = %(item_code)s
			and po.company = %(company)s
			and po.docstatus = 1
			and ifnull(po.status, '') not in ('Closed', 'Stopped', 'Cancelled')
			{warehouse_clause}
		""",
		params,
	)[0][0]
	return flt(value)


def _get_open_work_order_qty(item_code, company, warehouse=None) -> float:
	if not _doctype_exists("Work Order"):
		return 0
	params = {"item_code": item_code, "company": company}
	warehouse_clause = ""
	if warehouse and _has_field("Work Order", "fg_warehouse"):
		warehouse_clause = " and wo.fg_warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	value = frappe.db.sql(
		f"""
		select sum(greatest(ifnull(wo.qty, 0) - ifnull(wo.produced_qty, 0), 0))
		from `tabWork Order` wo
		where wo.production_item = %(item_code)s
			and wo.company = %(company)s
			and wo.docstatus < 2
			and ifnull(wo.status, '') not in ('Closed', 'Stopped', 'Completed', 'Cancelled')
			{warehouse_clause}
		""",
		params,
	)[0][0]
	return flt(value)


def _get_production_plan_supply_qty(item_code, company, warehouse, settings) -> float:
	if not settings.include_production_plan_as_supply:
		return 0
	return sum(
		flt(row.remaining_qty)
		for row in _get_production_plan_supply_records(item_code, company, warehouse, settings)
	)


def _get_item_values(item_code):
	return frappe._dict(_get_item_values_cached(item_code or ""))


@lru_cache(maxsize=10000)
def _get_item_values_cached(item_code):
	fields = ["name", "item_name", "stock_uom"]
	for fieldname in (
		"default_warehouse",
		"lead_time_days",
		"custom_mrp_lead_time_days",
		"custom_mrp_default_stock_buffer",
		"purchase_uom",
		"min_order_qty",
		"is_purchase_item",
		"is_stock_item",
		"is_customer_provided_item",
		"is_sub_contracted_item",
		"default_material_request_type",
		"item_group",
		"customer",
		"default_bom",
	):
		if _has_field("Item", fieldname):
			fields.append(fieldname)
	row = frappe.db.get_value("Item", item_code, fields, as_dict=True) or {}
	row["default_supplier"] = _get_item_default_supplier(item_code)
	return dict(row)


@lru_cache(maxsize=10000)
def _item_exists(item_code) -> bool:
	return bool(item_code and frappe.db.exists("Item", item_code))


@lru_cache(maxsize=10000)
def _resolve_item_name(item_identifier) -> str | None:
	if not item_identifier:
		return None
	if _item_exists(item_identifier):
		return item_identifier
	if _has_field("Item", "item_code"):
		return frappe.db.get_value("Item", {"item_code": item_identifier}, "name")
	return None


@lru_cache(maxsize=10000)
def _get_item_default_supplier(item_code):
	if not _doctype_exists("Item Default") or not item_code:
		return None
	return frappe.db.get_value(
		"Item Default",
		{"parent": item_code, "default_supplier": ["is", "set"]},
		"default_supplier",
	)


@lru_cache(maxsize=10000)
def _get_item_supplier(item_code):
	if not _doctype_exists("Item Supplier") or not item_code:
		return None
	return frappe.db.get_value("Item Supplier", {"parent": item_code}, "supplier", order_by="idx asc")


def _get_supplier_quotation_option(item_code, supplier=None, as_of_date=None):
	if not _doctype_exists("Supplier Quotation") or not _doctype_exists("Supplier Quotation Item"):
		return None
	params = {"item_code": item_code, "as_of_date": getdate(as_of_date or today())}
	supplier_clause = ""
	if supplier:
		supplier_clause = " and sq.supplier = %(supplier)s"
		params["supplier"] = supplier
	lead_time_expr = "sqi.lead_time_days" if _has_field("Supplier Quotation Item", "lead_time_days") else "0"
	rows = frappe.db.sql(
		f"""
		select
			sqi.name,
			sqi.parent,
			sq.supplier,
			sq.currency,
			{lead_time_expr} as lead_time_days,
			sqi.price_list_rate,
			sqi.rate,
			sq.transaction_date,
			sq.valid_till
		from `tabSupplier Quotation Item` sqi
		inner join `tabSupplier Quotation` sq on sq.name = sqi.parent
		where sqi.item_code = %(item_code)s
			and sq.docstatus = 1
			and (sq.valid_till is null or sq.valid_till >= %(as_of_date)s)
			{supplier_clause}
		order by
			case when ifnull(sqi.price_list_rate, 0) > 0 then sqi.price_list_rate else sqi.rate end asc,
			sq.transaction_date desc,
			sqi.creation desc
		limit 1
		""",
		params,
		as_dict=True,
	)
	return rows[0] if rows else None


def _get_item_price_option(item_code, supplier=None, as_of_date=None):
	if not _doctype_exists("Item Price"):
		return None
	params = {"item_code": item_code, "as_of_date": getdate(as_of_date or today())}
	supplier_expr = "ip.supplier" if _has_field("Item Price", "supplier") else "null"
	packing_unit_expr = "ip.packing_unit" if _has_field("Item Price", "packing_unit") else "0"
	lead_time_expr = "ip.lead_time_days" if _has_field("Item Price", "lead_time_days") else "0"
	supplier_clause = ""
	if supplier and _has_field("Item Price", "supplier"):
		supplier_clause = " and (ifnull(ip.supplier, '') in ('', %(supplier)s))"
		params["supplier"] = supplier
	rows = frappe.db.sql(
		f"""
		select
			ip.name,
			{supplier_expr} as supplier,
			ip.currency,
			ip.price_list_rate,
			{packing_unit_expr} as packing_unit,
			{lead_time_expr} as lead_time_days,
			ip.valid_from,
			ip.valid_upto
		from `tabItem Price` ip
		where ip.item_code = %(item_code)s
			and ifnull(ip.buying, 0) = 1
			and (ip.valid_from is null or ip.valid_from <= %(as_of_date)s)
			and (ip.valid_upto is null or ip.valid_upto >= %(as_of_date)s)
			{supplier_clause}
		order by
			case when ifnull(ip.supplier, '') != '' then 0 else 1 end,
			ip.price_list_rate asc,
			ip.valid_from desc,
			ip.creation desc
		limit 1
		""",
		params,
		as_dict=True,
	)
	return rows[0] if rows else None


def _get_requirement_bom_detail(requirement) -> dict[str, Any]:
	demand = (
		frappe.db.get_value(
			"MRP Demand Snapshot",
			requirement.demand_snapshot,
			["bom", "item_code", "item_name", "demand_qty"],
			as_dict=True,
		)
		if requirement.demand_snapshot
		else None
	) or frappe._dict()
	trace = _loads(requirement.bom_trace) or []
	return {
		"demand_bom": _get_bom_summary(demand.get("bom")),
		"requirement_bom": _get_bom_summary(requirement.bom),
		"current_bom_item": _get_bom_item_detail(requirement.bom_item),
		"trace": _enrich_bom_trace(trace),
		"exploded_items": _get_bom_exploded_rows(
			demand.get("bom"),
			flt(demand.get("demand_qty")),
			selected_item_code=requirement.item_code,
		),
	}


def _get_bom_summary(bom: str | None) -> dict[str, Any] | None:
	if not bom or not frappe.db.exists("BOM", bom):
		return None
	fields = ["name", "item", "item_name", "quantity", "uom", "is_active", "is_default", "docstatus"]
	for fieldname in ("custom_temporary_bom", "custom_remark"):
		if _has_field("BOM", fieldname):
			fields.append(fieldname)
	return frappe.db.get_value("BOM", bom, fields, as_dict=True)


def _get_bom_item_detail(bom_item: str | None) -> dict[str, Any] | None:
	if not bom_item or not frappe.db.exists("BOM Item", bom_item):
		return None
	fields = [
		"name",
		"parent",
		"idx",
		"item_code",
		"item_name",
		"qty",
		"uom",
		"stock_qty",
		"stock_uom",
		"bom_no",
		"do_not_explode",
		"source_warehouse",
		"operation",
		"description",
	]
	for fieldname in ("sourced_by_supplier", "include_item_in_manufacturing", "is_sub_assembly_item", "is_phantom_item"):
		if _has_field("BOM Item", fieldname):
			fields.append(fieldname)
	return frappe.db.get_value("BOM Item", bom_item, fields, as_dict=True)


def _enrich_bom_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
	rows = []
	for row in trace:
		parent_item = row.get("parent_item")
		component_item = row.get("component_item")
		parent_values = _get_item_values(parent_item) if parent_item else {}
		component_values = _get_item_values(component_item) if component_item else {}
		rows.append(
			{
				**row,
				"parent_item_name": parent_values.get("item_name"),
				"component_item_name": component_values.get("item_name"),
				"component_uom": component_values.get("stock_uom"),
			}
		)
	return rows


def _get_bom_exploded_rows(
	bom: str | None,
	required_qty: float,
	selected_item_code: str | None = None,
	level: int = 0,
	visited: set[str] | None = None,
	row_limit: int = 300,
) -> list[dict[str, Any]]:
	if not bom or not frappe.db.exists("BOM", bom) or row_limit <= 0:
		return []
	visited = visited or set()
	if bom in visited:
		return []
	visited.add(bom)
	bom_doc = frappe.get_doc("BOM", bom)
	parent_qty = flt(bom_doc.quantity) or 1
	rows = []
	for row in bom_doc.items:
		if len(rows) >= row_limit:
			break
		row_qty = flt(row.get("stock_qty")) or flt(row.get("qty"))
		if row_qty <= 0:
			continue
		scrap_percent = flt(row.get("scrap")) or flt(row.get("scrap_percent"))
		gross = flt(required_qty) * row_qty / parent_qty if required_qty else row_qty
		scrap_qty = gross * scrap_percent / 100
		required_component_qty = gross + scrap_qty
		child_bom = row.get("bom_no") or _get_default_bom(row.item_code)
		should_explode = child_bom and not cint(row.get("do_not_explode"))
		rows.append(
			{
				"level": level + 1,
				"bom": bom,
				"bom_item": row.name,
				"parent_item": bom_doc.item,
				"parent_item_name": bom_doc.item_name,
				"component_item": row.item_code,
				"component_item_name": row.get("item_name") or _get_item_values(row.item_code).get("item_name"),
				"bom_qty": row_qty,
				"parent_qty": parent_qty,
				"required_qty": required_component_qty,
				"scrap_percent": scrap_percent,
				"scrap_qty": scrap_qty,
				"uom": row.get("stock_uom") or row.get("uom"),
				"child_bom": child_bom,
				"do_not_explode": cint(row.get("do_not_explode")),
				"source_warehouse": row.get("source_warehouse"),
				"operation": row.get("operation"),
				"is_selected": 1 if row.item_code == selected_item_code else 0,
			}
		)
		if should_explode and len(rows) < row_limit:
			rows.extend(
				_get_bom_exploded_rows(
					child_bom,
					required_component_qty,
					selected_item_code=selected_item_code,
					level=level + 1,
					visited=set(visited),
					row_limit=row_limit - len(rows),
				)
			)
	return rows


@lru_cache(maxsize=10000)
def _get_default_bom(item_code):
	if not _doctype_exists("BOM"):
		return None
	bom = frappe.db.get_value("Item", item_code, "default_bom") if _has_field("Item", "default_bom") else None
	if bom and frappe.db.exists("BOM", {"name": bom, "docstatus": 1, "is_active": 1}):
		return bom
	return frappe.db.get_value(
		"BOM",
		{"item": item_code, "is_default": 1, "docstatus": 1, "is_active": 1},
		"name",
	)


def _get_requirement_type(item_code):
	item = _get_item_values(item_code)
	if item.get("is_sub_contracted_item"):
		return "Subcontract"
	if item.get("is_purchase_item"):
		return "Raw Material"
	if _get_default_bom(item_code):
		return "Intermediate"
	if item.get("is_stock_item"):
		return "Raw Material"
	return "Subcontract"


def _requires_bom_for_item(item_code) -> bool:
	item = _get_item_values(item_code)
	if not item_code or not item:
		return False
	default_mr_type = item.get("default_material_request_type")
	if default_mr_type in {"Manufacture", "Subcontracting"}:
		return True
	if cint(item.get("is_sub_contracted_item")):
		return True
	if cint(item.get("is_stock_item")) and not cint(item.get("is_purchase_item")) and not cint(item.get("is_customer_provided_item")):
		return True
	return False


@lru_cache(maxsize=10000)
def _get_item_lead_time(item_code):
	if _has_field("Item", "custom_mrp_lead_time_days"):
		mrp_lead_time = cint(frappe.db.get_value("Item", item_code, "custom_mrp_lead_time_days"))
		if mrp_lead_time:
			return mrp_lead_time
	if _has_field("Item", "lead_time_days"):
		return cint(frappe.db.get_value("Item", item_code, "lead_time_days"))
	if _has_field("Item", "lead_time"):
		return cint(frappe.db.get_value("Item", item_code, "lead_time"))
	return 0


def _get_material_request_type(item_code, settings) -> str:
	item = _get_item_values(item_code)
	if item.get("is_customer_provided_item"):
		return "Customer Provided"
	if item.get("is_sub_contracted_item"):
		return "Subcontracting"
	return _resolve_supply_route(item_code, settings.company, None, None, settings).material_request_type


def _insert_exception(
	mrp_run,
	company,
	severity,
	category,
	item_code,
	source_doctype,
	source_name,
	message,
	requirement_line=None,
):
	if item_code and not _item_exists(item_code):
		message = _("Item value '{0}' is invalid. {1}").format(item_code, message)
		item_code = None
	doc = frappe.get_doc(
		{
			"doctype": "MRP Exception Log",
			"mrp_run": mrp_run,
			"requirement_line": requirement_line,
			"company": company,
			"severity": severity,
			"category": category,
			"resolution_status": "Open",
			"item_code": item_code,
			"source_doctype": source_doctype,
			"source_name": source_name,
			"message": message,
			"created_on": now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)


def _summary_cards() -> list[dict[str, Any]]:
	cards = [
		{"label": _("Open Runs"), "value": frappe.db.count("MRP Run", {"status": ["not in", ["Closed", "Cancelled"]]})},
		{"label": _("Open Requirements"), "value": frappe.db.count("MRP Requirement Line", {"status": ["in", ["Ready", "Exception"]]})},
		{"label": _("Ready Batches"), "value": frappe.db.count("MRP Proposal Batch", {"status": "Ready"})},
		{"label": _("Open Exceptions"), "value": frappe.db.count("MRP Exception Log", {"resolution_status": "Open"})},
	]
	if _doctype_exists("MRP Shortage Alert"):
		cards.append({"label": _("Open Shortages"), "value": frappe.db.count("MRP Shortage Alert", {"status": "Open"})})
	return cards


def _build_source_summary(demands: list[DemandRow]) -> str:
	counts = defaultdict(int)
	for row in demands:
		counts[row.demand_type] += 1
	return ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))


def _find_proposal_items(requirement_line):
	rows = frappe.db.sql(
		"""
		select
			parent,
			item_code,
			qty,
			action,
			status,
			commitment_type,
			material_request
		from `tabMRP Proposal Item`
		where requirement_line = %s
		order by idx asc
		""",
		(requirement_line,),
		as_dict=True,
	)
	return rows


def _set_if_has(doc, fieldname, value):
	if value is None:
		return
	try:
		if doc.meta.has_field(fieldname):
			doc.set(fieldname, value)
	except Exception:
		return


@lru_cache(maxsize=1000)
def _doctype_exists(doctype: str) -> bool:
	return bool(frappe.db.exists("DocType", doctype))


@lru_cache(maxsize=10000)
def _has_field(doctype: str, fieldname: str) -> bool:
	return _doctype_exists(doctype) and frappe.get_meta(doctype).has_field(fieldname)


@lru_cache(maxsize=10000)
def _first_field(doctype: str, candidates: tuple[str, ...]) -> str | None:
	for fieldname in candidates:
		if _has_field(doctype, fieldname):
			return fieldname
	return None


def _clear_planning_caches():
	for func in (
		_doctype_exists,
		_has_field,
		_first_field,
		_get_item_values_cached,
		_item_exists,
		_resolve_item_name,
		_get_item_default_supplier,
		_get_item_supplier,
		_get_default_bom,
		_get_item_lead_time,
		_get_item_safety_stock,
	):
		func.cache_clear()
	stock_buffer.clear_runtime_cache()


def _dumps(value: Any) -> str:
	return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None) -> Any:
	if not value:
		return None
	try:
		return json.loads(value)
	except Exception:
		return value
