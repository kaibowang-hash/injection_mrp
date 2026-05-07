from __future__ import annotations

import math
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime, today


BUFFER_PRIORITY_GREEN = "Green"
BUFFER_PRIORITY_YELLOW = "Yellow"
BUFFER_PRIORITY_RED = "Red"
DEFAULT_BUFFER_PROFILE = "standard-replenish"
DEFAULT_ADU_METHOD = "Past Actual"
DEFAULT_ADU_PAST_DAYS = 90
BUFFER_ITEM_GROUP_ROOTS = ("Raw-material", "Packaging")
BUFFER_REFRESH_STALE_HOURS = 24
BULK_PAGE_LENGTH = 1000
SUGGESTION_HIGH = "High"
SUGGESTION_MEDIUM = "Medium"
SUGGESTION_LOW = "Low"

OPEN_DOC_STATUSES = ("Closed", "Stopped", "Cancelled")
_FALLBACK_RUNTIME_CACHE: dict[tuple[str, str, str], frappe._dict] = {}


def calculate_zones(
	adu: float,
	dlt_days: float,
	lead_time_factor: float,
	variability_factor: float,
	order_cycle_days: float,
	min_order_qty: float,
) -> frappe._dict:
	adu = flt(adu)
	dlt_days = flt(dlt_days)
	lead_time_factor = flt(lead_time_factor)
	variability_factor = flt(variability_factor)
	order_cycle_days = flt(order_cycle_days)
	min_order_qty = flt(min_order_qty)

	red_base = dlt_days * adu * lead_time_factor
	red_safety = red_base * variability_factor
	red = red_base + red_safety
	yellow = dlt_days * adu
	green = max(order_cycle_days * adu, dlt_days * adu * lead_time_factor, min_order_qty)
	return frappe._dict(
		{
			"red_base_qty": round(red_base, 6),
			"red_safety_qty": round(red_safety, 6),
			"red_zone_qty": round(red, 6),
			"yellow_zone_qty": round(yellow, 6),
			"green_zone_qty": round(green, 6),
			"top_of_red": round(red, 6),
			"top_of_yellow": round(red + yellow, 6),
			"top_of_green": round(red + yellow + green, 6),
		}
	)


def classify_priority(net_flow_position: float, top_of_red: float, top_of_yellow: float) -> str:
	nfp = flt(net_flow_position)
	if nfp >= flt(top_of_yellow):
		return BUFFER_PRIORITY_GREEN
	if nfp >= flt(top_of_red):
		return BUFFER_PRIORITY_YELLOW
	return BUFFER_PRIORITY_RED


def adjust_order_qty(qty: float, min_order_qty: float = 0, order_multiple_qty: float = 0) -> float:
	order_qty = flt(qty)
	if order_qty <= 0:
		return 0
	if flt(min_order_qty) > 0:
		order_qty = max(order_qty, flt(min_order_qty))
	if flt(order_multiple_qty) > 0:
		multiple = flt(order_multiple_qty)
		order_qty = math.ceil(order_qty / multiple) * multiple
	return round(order_qty, 6)


def clear_runtime_cache() -> None:
	_runtime_cache().clear()


def validate_item(doc, method=None):
	apply_item_stock_buffer_default(doc)
	validate_item_lead_time_lock(doc, method=method)


def apply_item_stock_buffer_default(doc) -> None:
	if not _has_field("Item", "custom_mrp_use_stock_buffer") or not getattr(doc, "is_new", lambda: False)():
		return
	if cint(doc.get("custom_mrp_use_stock_buffer")):
		return
	if _item_group_defaults_to_stock_buffer(doc.get("item_group")) and _item_is_stock_enabled(doc):
		doc.custom_mrp_use_stock_buffer = 1


def ensure_item_stock_buffer(doc, method=None):
	if not _has_field("Item", "custom_mrp_use_stock_buffer"):
		return None
	if not cint(doc.get("custom_mrp_use_stock_buffer")) or not _item_is_stock_enabled(doc):
		return None
	try:
		return ensure_stock_buffer_for_item(doc, ignore_permissions=True)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			_("MRP Stock Buffer auto-create failed for Item {0}").format(doc.name),
		)
		return None


def ensure_stock_buffer_for_item(
	item,
	company: str | None = None,
	warehouse: str | None = None,
	make_default: bool = True,
	ignore_permissions: bool = False,
):
	if not _doctype_exists("MRP Stock Buffer"):
		return None
	item_doc = _coerce_item_row(item)
	item_code = item_doc.get("name") or item_doc.get("item_code")
	if not item_code or not _item_is_stock_enabled(item_doc):
		return None
	company = company or _get_default_buffer_company()
	if not company:
		return None
	warehouse = warehouse or _get_item_buffer_warehouse(item_doc, company)
	if not warehouse:
		return None

	existing = frappe.db.get_value(
		"MRP Stock Buffer",
		{"active": 1, "company": company, "item_code": item_code, "warehouse": warehouse},
		["name", "is_default_for_item"],
		as_dict=True,
	)
	if existing:
		if make_default and not cint(existing.is_default_for_item):
			_set_buffer_as_default_if_possible(existing.name, item_code, company)
		return frappe.get_doc("MRP Stock Buffer", existing.name)
	if make_default:
		default_buffer = frappe.db.get_value(
			"MRP Stock Buffer",
			{"active": 1, "company": company, "item_code": item_code, "is_default_for_item": 1},
			"name",
		)
		if default_buffer:
			return frappe.get_doc("MRP Stock Buffer", default_buffer)

	buffer = frappe.get_doc(
		{
			"doctype": "MRP Stock Buffer",
			"company": company,
			"item_code": item_code,
			"item_name": item_doc.get("item_name"),
			"stock_uom": item_doc.get("stock_uom"),
			"warehouse": warehouse,
			"active": 1,
			"is_default_for_item": 1 if make_default else 0,
			"buffer_profile": DEFAULT_BUFFER_PROFILE if _doctype_exists("MRP Buffer Profile") and frappe.db.exists("MRP Buffer Profile", DEFAULT_BUFFER_PROFILE) else None,
			"dlt_days": _get_item_lead_time_days(item_doc),
			"lead_time_factor": 1,
			"variability_factor": 0.5,
			"minimum_order_cycle_days": 0,
			"adu_calculation_method": DEFAULT_ADU_METHOD,
			"horizon_past_days": DEFAULT_ADU_PAST_DAYS,
			"horizon_future_days": DEFAULT_ADU_PAST_DAYS,
			"factor_past": 0.5,
			"factor_future": 0.5,
		}
	)
	buffer.insert(ignore_permissions=ignore_permissions)
	return buffer


def get_stock_buffer_console_data(
	filters: dict[str, Any] | None = None,
	limit_start: int | str | None = 0,
	limit_page_length: int | str | None = 500,
) -> dict[str, Any]:
	filters = filters or {}
	limit_start, limit_page_length = _pagination_args(limit_start, limit_page_length, 500)
	rows = list(_iter_stock_buffer_console_rows(filters))
	total_count = len(rows)
	page_rows = rows[limit_start : limit_start + limit_page_length]
	return {
		"cards": _stock_buffer_console_cards(rows),
		"rows": page_rows,
		"pagination": _pagination_meta(total_count, limit_start, limit_page_length, len(page_rows)),
	}


def create_missing_stock_buffers(
	filters: dict[str, Any] | None = None,
	item_codes: list[str] | None = None,
	ignore_permissions: bool = False,
) -> dict[str, Any]:
	filters = filters or {}
	item_set = set(item_codes or [])
	created = []
	skipped = []
	errors = []
	for row in _iter_stock_buffer_console_rows(filters):
		if item_set and row.item_code not in item_set:
			continue
		if row.status != "Missing Buffer":
			skipped.append({"item_code": row.item_code, "status": row.status})
			continue
		try:
			buffer = ensure_stock_buffer_for_item(
				row.item_code,
				company=row.company,
				warehouse=row.warehouse,
				ignore_permissions=ignore_permissions,
			)
			if buffer:
				created.append({"item_code": row.item_code, "stock_buffer": buffer.name})
			else:
				skipped.append({"item_code": row.item_code, "status": "Skipped"})
		except Exception as exc:
			errors.append({"item_code": row.item_code, "message": str(exc)})
			frappe.log_error(frappe.get_traceback(), _("MRP Stock Buffer bulk create failed"))
	return {"created": len(created), "skipped": len(skipped), "failed": len(errors), "rows": created, "errors": errors}


def apply_stock_buffer_suggestions(
	filters: dict[str, Any] | None = None,
	item_codes: list[str] | None = None,
	apply_dlt: bool = True,
	apply_order_constraints: bool = True,
	ignore_permissions: bool = False,
) -> dict[str, Any]:
	if not _doctype_exists("MRP Stock Buffer"):
		return {"updated": 0, "skipped": 0, "failed": 0, "rows": [], "errors": []}
	filters = filters or {}
	item_set = set(item_codes or [])
	updated = []
	skipped = []
	errors = []
	for row in _iter_stock_buffer_console_rows(filters):
		if item_set and row.item_code not in item_set:
			continue
		if not row.get("stock_buffer"):
			skipped.append({"item_code": row.item_code, "status": row.status})
			continue
		try:
			doc = frappe.get_doc("MRP Stock Buffer", row.stock_buffer)
			if not ignore_permissions:
				doc.check_permission("write")
			suggestion = calculate_procurement_suggestions(doc)
			changed = []
			if apply_dlt and _has_meaningful_suggestion_diff(doc.get("dlt_days"), suggestion.get("suggested_dlt_days")):
				doc.dlt_days = flt(suggestion.suggested_dlt_days)
				changed.append("dlt_days")
			if apply_order_constraints:
				if _has_meaningful_suggestion_diff(doc.get("min_order_qty"), suggestion.get("suggested_min_order_qty")):
					doc.min_order_qty = flt(suggestion.suggested_min_order_qty)
					changed.append("min_order_qty")
				if _has_meaningful_suggestion_diff(doc.get("order_multiple_qty"), suggestion.get("suggested_order_multiple_qty")):
					doc.order_multiple_qty = flt(suggestion.suggested_order_multiple_qty)
					changed.append("order_multiple_qty")
			if changed:
				state = refresh_buffer(doc, persist=True, ignore_permissions=ignore_permissions)
				updated.append({"stock_buffer": doc.name, "item_code": row.item_code, "fields": changed, "state": state})
			else:
				refresh_buffer(doc, persist=True, ignore_permissions=ignore_permissions)
				skipped.append({"stock_buffer": doc.name, "item_code": row.item_code, "status": "No Suggestion Change"})
		except Exception as exc:
			errors.append({"stock_buffer": row.get("stock_buffer"), "item_code": row.item_code, "message": str(exc)})
			frappe.log_error(frappe.get_traceback(), _("MRP Stock Buffer apply suggestions failed"))
	return {"updated": len(updated), "skipped": len(skipped), "failed": len(errors), "rows": updated, "errors": errors}


def apply_stock_buffer_item_group_defaults(
	filters: dict[str, Any] | None = None,
	item_codes: list[str] | None = None,
	ignore_permissions: bool = False,
) -> dict[str, Any]:
	if not _has_field("Item", "custom_mrp_use_stock_buffer"):
		return {"updated": 0, "enabled": 0, "disabled": 0}
	filters = filters or {}
	query_filters = _item_query_filters(filters)
	if item_codes:
		query_filters["name"] = ["in", item_codes]
	fields = ["name", "item_group"]
	if _has_field("Item", "is_stock_item"):
		fields.append("is_stock_item")
	if _has_field("Item", "disabled"):
		fields.append("disabled")
	updated = enabled = disabled = 0
	for items in _iter_get_all("Item", filters=query_filters, fields=fields, order_by="name asc"):
		for item in items:
			value = 1 if _item_is_stock_enabled(item) and _item_group_defaults_to_stock_buffer(item.item_group) else 0
			frappe.db.set_value("Item", item.name, "custom_mrp_use_stock_buffer", value, update_modified=False)
			updated += 1
			if value:
				enabled += 1
			else:
				disabled += 1
	return {"updated": updated, "enabled": enabled, "disabled": disabled}


def _iter_get_all(
	doctype: str,
	filters: dict[str, Any] | None = None,
	fields: list[str] | None = None,
	order_by: str | None = None,
	page_length: int = BULK_PAGE_LENGTH,
):
	limit_start = 0
	while True:
		rows = frappe.get_all(
			doctype,
			filters=filters,
			fields=fields,
			order_by=order_by,
			limit_start=limit_start,
			limit_page_length=page_length,
		)
		if not rows:
			break
		yield rows
		if len(rows) < page_length:
			break
		limit_start += len(rows)


def refresh_active_stock_buffers(
	company: str | None = None,
	item_code: str | None = None,
	warehouse: str | None = None,
	item_codes: list[str] | None = None,
	ignore_permissions: bool = False,
) -> dict[str, Any]:
	if not _doctype_exists("MRP Stock Buffer"):
		return {"count": 0, "refreshed": 0, "skipped": 0, "failed": 0, "buffers": [], "skipped_rows": [], "errors": []}
	filters: dict[str, Any] = {"active": 1}
	if company:
		filters["company"] = company
	if item_code:
		filters["item_code"] = item_code
	if item_codes:
		filters["item_code"] = ["in", item_codes]
	if warehouse:
		filters["warehouse"] = warehouse
	buffers = []
	skipped = []
	errors = []
	count = 0
	for rows in _iter_get_all(
		"MRP Stock Buffer",
		filters=filters,
		fields=["name", "item_code"],
		order_by="name asc",
	):
		for row in rows:
			count += 1
			if row.item_code and not _item_code_is_stock_enabled(row.item_code):
				skipped.append({"stock_buffer": row.name, "item_code": row.item_code, "status": "Disabled Item"})
				continue
			try:
				doc = frappe.get_doc("MRP Stock Buffer", row.name)
				if not ignore_permissions:
					doc.check_permission("write")
				buffers.append(refresh_buffer(doc, persist=True, ignore_permissions=ignore_permissions))
			except Exception as exc:
				errors.append({"stock_buffer": row.name, "message": str(exc)})
				frappe.log_error(frappe.get_traceback(), _("MRP Stock Buffer refresh failed"))
	return {
		"count": count,
		"refreshed": len(buffers),
		"skipped": len(skipped),
		"failed": len(errors),
		"buffers": buffers,
		"skipped_rows": skipped,
		"errors": errors,
	}


def refresh_stock_buffer_console_selection(filters: dict[str, Any] | None = None, ignore_permissions: bool = False) -> dict[str, Any]:
	if not _doctype_exists("MRP Stock Buffer"):
		return {"count": 0, "refreshed": 0, "skipped": 0, "failed": 0, "buffers": [], "skipped_rows": [], "errors": []}
	filters = filters or {}
	buffers = []
	skipped = []
	errors = []
	count = 0
	seen: set[str] = set()
	for row in _iter_stock_buffer_console_rows(filters):
		if not row.get("stock_buffer"):
			skipped.append({"item_code": row.item_code, "status": row.status})
			continue
		if row.stock_buffer in seen:
			continue
		seen.add(row.stock_buffer)
		count += 1
		if row.item_code and not _item_code_is_stock_enabled(row.item_code):
			skipped.append({"stock_buffer": row.stock_buffer, "item_code": row.item_code, "status": "Disabled Item"})
			continue
		try:
			doc = frappe.get_doc("MRP Stock Buffer", row.stock_buffer)
			if not ignore_permissions:
				doc.check_permission("write")
			buffers.append(refresh_buffer(doc, persist=True, ignore_permissions=ignore_permissions))
		except Exception as exc:
			errors.append({"stock_buffer": row.stock_buffer, "message": str(exc)})
			frappe.log_error(frappe.get_traceback(), _("MRP Stock Buffer refresh failed"))
	return {
		"count": count,
		"refreshed": len(buffers),
		"skipped": len(skipped),
		"failed": len(errors),
		"buffers": buffers,
		"skipped_rows": skipped,
		"errors": errors,
	}


def collect_buffer_top_up_demands(run, persist: bool = False) -> list[frappe._dict]:
	if not _doctype_exists("MRP Stock Buffer"):
		return []

	filters: dict[str, Any] = {"active": 1, "company": run.company}
	if getattr(run, "item_code", None):
		filters["item_code"] = run.item_code
	if getattr(run, "warehouse", None):
		filters["warehouse"] = run.warehouse

	demands = []
	for buffers in _iter_get_all(
		"MRP Stock Buffer",
		filters=filters,
		fields=["name", "item_code"],
		order_by="item_code asc, warehouse asc",
	):
		for row in buffers:
			if row.item_code and not _item_code_is_stock_enabled(row.item_code):
				continue
			state = refresh_buffer(row.name, run=run, persist=persist, ignore_permissions=True)
			if flt(state.recommended_qty) > 0:
				demands.append(state)
	return demands


def get_buffer_state_for_item(
	item_code: str,
	company: str | None,
	warehouse: str | None = None,
	run=None,
	persist: bool = False,
) -> frappe._dict | None:
	buffer_name = get_buffer_name_for_item(item_code, company, warehouse)
	if not buffer_name:
		return None
	cache_key = _state_cache_key(buffer_name, run)
	cache = _runtime_cache()
	if not persist and cache_key in cache:
		return cache[cache_key]
	state = refresh_buffer(buffer_name, run=run, persist=persist, ignore_permissions=True)
	cache[cache_key] = state
	return state


def get_buffer_name_for_item(item_code: str, company: str | None, warehouse: str | None = None) -> str | None:
	if not item_code or not company or not _doctype_exists("MRP Stock Buffer"):
		return None
	if not _item_code_is_stock_enabled(item_code):
		return None

	if warehouse:
		return frappe.db.get_value(
			"MRP Stock Buffer",
			{"active": 1, "company": company, "item_code": item_code, "warehouse": warehouse},
			"name",
		)

	return frappe.db.get_value(
		"MRP Stock Buffer",
		{"active": 1, "company": company, "item_code": item_code, "is_default_for_item": 1},
		"name",
	)


def refresh_buffer(
	buffer,
	run=None,
	persist: bool = True,
	ignore_permissions: bool = False,
	include_suggestions: bool | None = None,
) -> frappe._dict:
	doc = frappe.get_doc("MRP Stock Buffer", buffer) if isinstance(buffer, str) else buffer
	if include_suggestions is None:
		include_suggestions = persist
	state = calculate_buffer_state(doc, run=run, include_suggestions=include_suggestions)
	for key, value in state.items():
		if key in {"name", "company", "item_code", "item_name", "stock_uom", "warehouse"}:
			continue
		if callable(getattr(doc, "set", None)):
			doc.set(key, value)
		else:
			doc[key] = value
	if persist and callable(getattr(doc, "save", None)):
		doc.flags.ignore_mrp_buffer_refresh = True
		doc.save(ignore_permissions=ignore_permissions)
	cache_key = _state_cache_key(state.name, run)
	_runtime_cache()[cache_key] = state
	return state


def calculate_buffer_state(buffer, run=None, include_suggestions: bool = True) -> frappe._dict:
	doc = frappe._dict(buffer.as_dict() if callable(getattr(buffer, "as_dict", None)) else dict(buffer))
	profile = _get_profile_values(doc.get("buffer_profile"))
	lead_time_factor = flt(doc.get("lead_time_factor")) or flt(profile.get("lead_time_factor")) or 1
	variability_factor = flt(doc.get("variability_factor")) or flt(profile.get("variability_factor"))
	order_cycle_days = flt(doc.get("minimum_order_cycle_days")) or flt(profile.get("default_order_cycle_days"))
	min_order_qty = flt(doc.get("min_order_qty"))
	order_multiple_qty = flt(doc.get("order_multiple_qty"))
	dlt_days = flt(doc.get("dlt_days"))

	adu = calculate_adu(doc, run=run)
	zones = calculate_zones(
		adu,
		dlt_days,
		lead_time_factor,
		variability_factor,
		order_cycle_days,
		min_order_qty,
	)
	as_of_date = getdate(getattr(run, "planning_date", None) or today())
	cutoff_date = add_days(as_of_date, cint(dlt_days))
	on_hand = _get_on_hand_qty(doc.item_code, doc.company, doc.warehouse)
	incoming = _get_incoming_dlt_qty(doc.item_code, doc.company, doc.warehouse, cutoff_date)
	qualified_demand = _get_qualified_demand_qty(doc.item_code, doc.company, doc.warehouse, as_of_date, cutoff_date)
	nfp = on_hand + incoming - qualified_demand
	recommended = 0
	if nfp < zones.top_of_yellow:
		recommended = adjust_order_qty(
			zones.top_of_green - nfp,
			min_order_qty=min_order_qty,
			order_multiple_qty=order_multiple_qty,
		)
	top_of_green = flt(zones.top_of_green)
	nfp_percent = round((nfp / top_of_green * 100), 2) if top_of_green else 0
	priority = classify_priority(nfp, zones.top_of_red, zones.top_of_yellow)

	state = frappe._dict(
		{
			"name": doc.get("name"),
			"company": doc.get("company"),
			"item_code": doc.get("item_code"),
			"item_name": doc.get("item_name"),
			"stock_uom": doc.get("stock_uom"),
			"warehouse": doc.get("warehouse"),
			"dlt_days": dlt_days,
			"min_order_qty": min_order_qty,
			"order_multiple_qty": order_multiple_qty,
			"adu": round(adu, 6),
			"lead_time_factor": lead_time_factor,
			"variability_factor": variability_factor,
			"minimum_order_cycle_days": order_cycle_days,
			"on_hand_qty": round(on_hand, 6),
			"incoming_dlt_qty": round(incoming, 6),
			"qualified_demand_qty": round(qualified_demand, 6),
			"net_flow_position": round(nfp, 6),
			"net_flow_position_percent": nfp_percent,
			"planning_priority": priority,
			"recommended_qty": recommended,
			"last_calculated_on": now_datetime(),
			"lead_time_sync_status": _("Default buffer controls MRP lead time.") if cint(doc.get("is_default_for_item")) else _("Not the default Item buffer."),
		}
	)
	state.update(zones)
	if include_suggestions and _buffer_supports_suggestions():
		state.update(calculate_procurement_suggestions(doc))
	return state


def calculate_adu(buffer, run=None) -> float:
	method = buffer.get("adu_calculation_method") or "Fixed"
	if method == "Fixed":
		return flt(buffer.get("fixed_adu"))
	if method == "Past Actual":
		return _calculate_past_actual_adu(buffer)
	if method == "Future MRP":
		return _calculate_future_mrp_adu(buffer, run=run)
	if method == "Blended":
		past = _calculate_past_actual_adu(buffer)
		future = _calculate_future_mrp_adu(buffer, run=run)
		past_factor = flt(buffer.get("factor_past"))
		future_factor = flt(buffer.get("factor_future"))
		if not past_factor and not future_factor:
			past_factor = future_factor = 0.5
		total = past_factor + future_factor
		if total <= 0:
			return 0
		return (past * past_factor + future * future_factor) / total
	return flt(buffer.get("fixed_adu"))


def calculate_procurement_suggestions(buffer) -> frappe._dict:
	doc = frappe._dict(buffer.as_dict() if callable(getattr(buffer, "as_dict", None)) else dict(buffer or {}))
	item_code = doc.get("item_code")
	company = doc.get("company")
	warehouse = doc.get("warehouse")
	item = _coerce_item_row(item_code) if item_code else frappe._dict()
	as_of_date = getdate(today())
	suggestion = frappe._dict(
		{
			"suggested_dlt_days": 0,
			"suggested_dlt_source": None,
			"suggested_dlt_confidence": None,
			"suggested_min_order_qty": 0,
			"suggested_order_multiple_qty": 0,
			"suggestions_calculated_on": now_datetime(),
			"suggestion_notes": None,
		}
	)
	notes = []
	supplier = _get_item_default_supplier(item_code, company) or _get_item_supplier(item_code)
	rule = _get_supply_rule_suggestion(item, company, warehouse)
	if rule:
		if rule.get("supplier"):
			supplier = rule.supplier
		if flt(rule.get("min_order_qty")) > 0:
			suggestion.suggested_min_order_qty = flt(rule.min_order_qty)
		if flt(rule.get("order_multiple_qty")) > 0:
			suggestion.suggested_order_multiple_qty = flt(rule.order_multiple_qty)
		_set_dlt_suggestion(
			suggestion,
			rule.get("supplier_lead_time_days"),
			_("MRP Supply Rule {0}").format(rule.name),
			SUGGESTION_HIGH if rule.get("item_code") else SUGGESTION_MEDIUM,
		)

	quotation = _get_supplier_quotation_suggestion(item_code, supplier, as_of_date)
	if not quotation and not supplier:
		quotation = _get_supplier_quotation_suggestion(item_code, None, as_of_date)
	if quotation:
		if quotation.get("supplier"):
			supplier = quotation.supplier
		_set_dlt_suggestion(
			suggestion,
			quotation.get("lead_time_days"),
			_("Supplier Quotation {0}").format(quotation.parent),
			SUGGESTION_HIGH if supplier and quotation.get("supplier") == supplier else SUGGESTION_MEDIUM,
		)

	item_price = _get_item_price_suggestion(item_code, supplier, as_of_date)
	if not item_price and not supplier:
		item_price = _get_item_price_suggestion(item_code, None, as_of_date)
	if item_price:
		_set_dlt_suggestion(
			suggestion,
			item_price.get("lead_time_days"),
			_("Item Price {0}").format(item_price.name),
			SUGGESTION_MEDIUM if item_price.get("supplier") else SUGGESTION_LOW,
		)
		if not flt(suggestion.suggested_order_multiple_qty) and flt(item_price.get("packing_unit")) > 0:
			suggestion.suggested_order_multiple_qty = flt(item_price.packing_unit)

	if not flt(suggestion.suggested_min_order_qty) and flt(item.get("min_order_qty")) > 0:
		suggestion.suggested_min_order_qty = flt(item.min_order_qty)
	if not flt(suggestion.suggested_dlt_days):
		item_lead_time = _get_item_lead_time_days(item)
		_set_dlt_suggestion(suggestion, item_lead_time, _("Item Lead Time"), SUGGESTION_MEDIUM)
	if not flt(suggestion.suggested_dlt_days):
		po_history = _get_purchase_order_history_suggestion(item_code, supplier)
		if po_history:
			sample_size = cint(po_history.get("sample_size"))
			_set_dlt_suggestion(
				suggestion,
				po_history.get("lead_time_days"),
				_("Purchase Order History"),
				SUGGESTION_MEDIUM if sample_size >= 5 else SUGGESTION_LOW,
			)
			notes.append(_("PO history sample size: {0}").format(sample_size))
	if not flt(suggestion.suggested_dlt_days):
		suggestion.suggested_dlt_confidence = SUGGESTION_LOW
		notes.append(_("No maintained lead time source found."))
	if notes:
		suggestion.suggestion_notes = "; ".join(notes)
	return suggestion


def sync_default_buffer_to_item(buffer) -> None:
	if not _doctype_exists("Item"):
		return

	before = buffer.get_doc_before_save() if callable(getattr(buffer, "get_doc_before_save", None)) else None
	if before and cint(before.get("active")) and cint(before.get("is_default_for_item")):
		still_controls_same_item = (
			cint(buffer.get("active"))
			and cint(buffer.get("is_default_for_item"))
			and buffer.get("item_code") == before.get("item_code")
		)
		if not still_controls_same_item:
			_clear_default_buffer_from_item(
				before.get("item_code"),
				before.get("name"),
				before.get("dlt_days"),
				before.get("top_of_red"),
			)

	if not cint(buffer.get("active")) or not cint(buffer.get("is_default_for_item")) or not buffer.get("item_code"):
		return

	values: dict[str, Any] = {}
	if _has_field("Item", "custom_mrp_default_stock_buffer"):
		values["custom_mrp_default_stock_buffer"] = buffer.name
	if _has_field("Item", "custom_mrp_lead_time_days"):
		values["custom_mrp_lead_time_days"] = cint(buffer.get("dlt_days"))
	if _sync_standard_item_lead_time_enabled() and _has_field("Item", "lead_time_days"):
		values["lead_time_days"] = cint(buffer.get("dlt_days"))
	if _sync_item_safety_stock_enabled() and _has_field("Item", "safety_stock"):
		values["safety_stock"] = _get_buffer_item_safety_stock(buffer)
	if values:
		frappe.db.set_value("Item", buffer.item_code, values)


def _clear_default_buffer_from_item(
	item_code: str | None,
	buffer_name: str | None,
	dlt_days: float | int | None = None,
	safety_stock_qty: float | int | None = None,
) -> None:
	if not item_code or not buffer_name:
		return
	fields = []
	for fieldname in ("custom_mrp_default_stock_buffer", "custom_mrp_lead_time_days", "lead_time_days", "safety_stock"):
		if _has_field("Item", fieldname):
			fields.append(fieldname)
	if not fields:
		return
	item = frappe.db.get_value("Item", item_code, fields, as_dict=True) or {}
	if item.get("custom_mrp_default_stock_buffer") != buffer_name:
		return
	values: dict[str, Any] = {}
	if _has_field("Item", "custom_mrp_default_stock_buffer"):
		values["custom_mrp_default_stock_buffer"] = None
	if _has_field("Item", "custom_mrp_lead_time_days"):
		values["custom_mrp_lead_time_days"] = 0
	if (
		_sync_standard_item_lead_time_enabled()
		and _has_field("Item", "lead_time_days")
		and cint(item.get("lead_time_days")) == cint(dlt_days)
	):
		values["lead_time_days"] = 0
	if (
		_sync_item_safety_stock_enabled()
		and _has_field("Item", "safety_stock")
		and abs(flt(item.get("safety_stock")) - flt(safety_stock_qty)) <= 0.000001
	):
		values["safety_stock"] = 0
	if values:
		frappe.db.set_value("Item", item_code, values)


def validate_item_lead_time_lock(doc, method=None):
	if getattr(doc, "flags", None) and (
		doc.flags.get("ignore_mrp_buffer_lead_time_lock") or doc.flags.get("ignore_mrp_buffer_item_lock")
	):
		return
	buffer_name = doc.get("custom_mrp_default_stock_buffer") if hasattr(doc, "get") else None
	if not buffer_name:
		return
	before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	if not before:
		return
	locked_fields = [
		fieldname
		for fieldname in ("custom_mrp_lead_time_days",)
		if _has_field("Item", fieldname)
	]
	if _sync_standard_item_lead_time_enabled() and _has_field("Item", "lead_time_days"):
		locked_fields.append("lead_time_days")
	for fieldname in locked_fields:
		if cint(before.get(fieldname)) != cint(doc.get(fieldname)):
			frappe.throw(
				_(
					"Item lead time is controlled by default MRP Stock Buffer {0}. Please update the Stock Buffer DLT instead."
				).format(buffer_name)
			)
	if (
		_sync_item_safety_stock_enabled()
		and _has_field("Item", "safety_stock")
		and abs(flt(before.get("safety_stock")) - flt(doc.get("safety_stock"))) > 0.000001
	):
		frappe.throw(
			_(
				"Item safety stock is controlled by default MRP Stock Buffer {0}. Please update the Stock Buffer instead."
			).format(buffer_name)
		)


def _get_buffer_item_safety_stock(buffer) -> float:
	return flt(buffer.get("top_of_red") or buffer.get("red_zone_qty"))


def validate_buffer_uniqueness(doc) -> None:
	if not cint(doc.get("active")):
		return
	duplicate = frappe.db.exists(
		"MRP Stock Buffer",
		{
			"company": doc.company,
			"item_code": doc.item_code,
			"warehouse": doc.warehouse,
			"active": 1,
			"name": ["!=", doc.name],
		},
	)
	if duplicate:
		frappe.throw(_("An active MRP Stock Buffer already exists for this company, item and warehouse."))

	if cint(doc.get("is_default_for_item")):
		default_duplicate = frappe.db.exists(
			"MRP Stock Buffer",
			{
				"company": doc.company,
				"item_code": doc.item_code,
				"is_default_for_item": 1,
				"active": 1,
				"name": ["!=", doc.name],
			},
		)
		if default_duplicate:
			frappe.throw(_("An active default MRP Stock Buffer already exists for this company and item."))


def get_chart_data(
	buffer_name: str | None = None,
	item_code: str | None = None,
	company: str | None = None,
	warehouse: str | None = None,
	include_suggestions: bool = True,
):
	if buffer_name:
		return _with_buffer_explanations(
			refresh_buffer(buffer_name, persist=False, include_suggestions=include_suggestions)
		)
	resolved_buffer = get_buffer_name_for_item(item_code or "", company, warehouse)
	if not resolved_buffer:
		return frappe._dict()
	return _with_buffer_explanations(
		refresh_buffer(resolved_buffer, persist=False, include_suggestions=include_suggestions)
	)


def _with_buffer_explanations(buffer) -> frappe._dict:
	row = frappe._dict(buffer or {})
	row.dlt_suggestion_detail = _get_dlt_suggestion_detail(row)
	row.dlt_confidence_detail = _get_dlt_confidence_detail(row)
	row.procurement_mismatch_detail = _get_procurement_mismatch_detail(row)
	return row


def _coerce_item_row(item) -> frappe._dict:
	if callable(getattr(item, "as_dict", None)):
		return frappe._dict(item.as_dict())
	if isinstance(item, str):
		fields = ["name", "item_name", "stock_uom", "item_group"]
		for fieldname in (
			"is_stock_item",
			"disabled",
			"default_warehouse",
			"custom_mrp_use_stock_buffer",
			"custom_mrp_lead_time_days",
			"lead_time_days",
			"lead_time",
			"min_order_qty",
			"purchase_uom",
		):
			if _has_field("Item", fieldname):
				fields.append(fieldname)
		return frappe._dict(frappe.db.get_value("Item", item, fields, as_dict=True) or {"name": item})
	return frappe._dict(dict(item or {}))


def _item_is_stock_enabled(item) -> bool:
	if _has_field("Item", "disabled") and cint(item.get("disabled")):
		return False
	if _has_field("Item", "is_stock_item"):
		return bool(cint(item.get("is_stock_item")))
	return True


def _item_code_is_stock_enabled(item_code: str | None) -> bool:
	if not item_code:
		return False
	if not _doctype_exists("Item"):
		return True
	cache = _runtime_cache()
	cache_key = ("__item_stock_enabled__", item_code, "")
	if cache_key not in cache:
		cache[cache_key] = _item_is_stock_enabled(_coerce_item_row(item_code))
	return bool(cache[cache_key])


def _get_default_buffer_company() -> str | None:
	try:
		company = frappe.db.get_single_value("MRP Settings", "company") if _doctype_exists("MRP Settings") else None
	except Exception:
		company = None
	return company or frappe.defaults.get_user_default("Company")


def _get_item_buffer_warehouse(item, company: str | None, item_default_warehouses: dict[str, str] | None = None) -> str | None:
	item_code = item.get("name") or item.get("item_code")
	warehouse = item.get("default_warehouse") if _has_field("Item", "default_warehouse") else None
	if warehouse:
		return warehouse
	if item_default_warehouses is not None:
		return item_default_warehouses.get(item_code)
	if not _doctype_exists("Item Default") or not item_code:
		return None
	filters = {"parent": item_code, "default_warehouse": ["is", "set"]}
	if company and _has_field("Item Default", "company"):
		filters["company"] = company
	return frappe.db.get_value("Item Default", filters, "default_warehouse", order_by="idx asc")


def _get_item_default_warehouse_map(item_codes: list[str], company: str | None) -> dict[str, str]:
	if not item_codes or not _doctype_exists("Item Default"):
		return {}
	filters = {"parent": ["in", item_codes], "default_warehouse": ["is", "set"]}
	if company and _has_field("Item Default", "company"):
		filters["company"] = company
	result = {}
	for rows in _iter_get_all(
		"Item Default",
		filters=filters,
		fields=["parent", "default_warehouse"],
		order_by="parent asc, idx asc",
	):
		for row in rows:
			result.setdefault(row.parent, row.default_warehouse)
	return result


def _get_item_lead_time_days(item) -> int:
	for fieldname in ("custom_mrp_lead_time_days", "lead_time_days", "lead_time"):
		if item.get(fieldname):
			return cint(item.get(fieldname))
	item_code = item.get("name") or item.get("item_code")
	if not item_code:
		return 0
	for fieldname in ("custom_mrp_lead_time_days", "lead_time_days", "lead_time"):
		if _has_field("Item", fieldname):
			value = cint(frappe.db.get_value("Item", item_code, fieldname))
			if value:
				return value
	return 0


def _buffer_supports_suggestions() -> bool:
	return _has_field("MRP Stock Buffer", "suggested_dlt_days")


def _has_meaningful_suggestion_diff(current, suggested) -> bool:
	return flt(suggested) > 0 and abs(flt(current) - flt(suggested)) > 0.000001


def _set_dlt_suggestion(suggestion, value, source: str | None, confidence: str | None) -> None:
	if flt(suggestion.get("suggested_dlt_days")) or flt(value) <= 0:
		return
	suggestion.suggested_dlt_days = flt(value)
	suggestion.suggested_dlt_source = source
	suggestion.suggested_dlt_confidence = confidence or SUGGESTION_LOW


def _get_item_default_supplier(item_code: str | None, company: str | None = None) -> str | None:
	if not item_code or not _doctype_exists("Item Default"):
		return None
	filters = {"parent": item_code, "default_supplier": ["is", "set"]}
	if company and _has_field("Item Default", "company"):
		filters["company"] = company
	supplier = frappe.db.get_value("Item Default", filters, "default_supplier", order_by="idx asc")
	if supplier or not company or not _has_field("Item Default", "company"):
		return supplier
	return frappe.db.get_value(
		"Item Default",
		{"parent": item_code, "default_supplier": ["is", "set"]},
		"default_supplier",
		order_by="idx asc",
	)


def _get_item_supplier(item_code: str | None) -> str | None:
	if not item_code or not _doctype_exists("Item Supplier"):
		return None
	return frappe.db.get_value("Item Supplier", {"parent": item_code}, "supplier", order_by="idx asc")


def _get_supply_rule_suggestion(item, company: str | None, warehouse: str | None):
	if not _doctype_exists("MRP Supply Rule"):
		return None
	item_code = item.get("name") or item.get("item_code")
	params = {
		"company": company or "",
		"item_code": item_code or "",
		"item_group": item.get("item_group") or "",
		"warehouse": warehouse or "",
	}
	rows = frappe.db.sql(
		"""
		select
			name,
			company,
			item_code,
			item_group,
			warehouse,
			supplier,
			min_order_qty,
			order_multiple_qty,
			supplier_lead_time_days,
			priority
		from `tabMRP Supply Rule`
		where enabled = 1
			and (ifnull(company, '') = '' or company = %(company)s)
			and (ifnull(item_code, '') = '' or item_code = %(item_code)s)
			and (ifnull(item_group, '') = '' or item_group = %(item_group)s)
			and (ifnull(warehouse, '') = '' or warehouse = %(warehouse)s)
		order by
			ifnull(priority, 0) desc,
			case when ifnull(item_code, '') = %(item_code)s and %(item_code)s != '' then 0 else 1 end,
			case when ifnull(item_group, '') = %(item_group)s and %(item_group)s != '' then 0 else 1 end,
			case when ifnull(warehouse, '') = %(warehouse)s and %(warehouse)s != '' then 0 else 1 end,
			modified desc
		limit 1
		""",
		params,
		as_dict=True,
	)
	return rows[0] if rows else None


def _get_supplier_quotation_suggestion(item_code: str | None, supplier: str | None = None, as_of_date=None):
	if not item_code or not _doctype_exists("Supplier Quotation") or not _doctype_exists("Supplier Quotation Item"):
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
			{lead_time_expr} as lead_time_days,
			sq.transaction_date,
			sq.valid_till
		from `tabSupplier Quotation Item` sqi
		inner join `tabSupplier Quotation` sq on sq.name = sqi.parent
		where sqi.item_code = %(item_code)s
			and sq.docstatus = 1
			and (sq.valid_till is null or sq.valid_till >= %(as_of_date)s)
			{supplier_clause}
		order by
			case when ifnull({lead_time_expr}, 0) > 0 then 0 else 1 end,
			sq.transaction_date desc,
			sqi.creation desc
		limit 1
		""",
		params,
		as_dict=True,
	)
	return rows[0] if rows else None


def _get_item_price_suggestion(item_code: str | None, supplier: str | None = None, as_of_date=None):
	if not item_code or not _doctype_exists("Item Price"):
		return None
	params = {"item_code": item_code, "as_of_date": getdate(as_of_date or today())}
	supplier_expr = "ip.supplier" if _has_field("Item Price", "supplier") else "null"
	supplier_order_expr = "case when ifnull(ip.supplier, '') != '' then 0 else 1 end" if _has_field("Item Price", "supplier") else "1"
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
			{supplier_order_expr},
			case when ifnull({lead_time_expr}, 0) > 0 then 0 else 1 end,
			ip.price_list_rate asc,
			ip.valid_from desc,
			ip.creation desc
		limit 1
		""",
		params,
		as_dict=True,
	)
	return rows[0] if rows else None


def _get_purchase_order_history_suggestion(item_code: str | None, supplier: str | None = None):
	if not item_code or not _doctype_exists("Purchase Order") or not _doctype_exists("Purchase Order Item"):
		return None
	if not _has_field("Purchase Order", "transaction_date") or not _has_field("Purchase Order Item", "schedule_date"):
		return None
	params = {"item_code": item_code}
	supplier_clause = ""
	if supplier and _has_field("Purchase Order", "supplier"):
		supplier_clause = " and po.supplier = %(supplier)s"
		params["supplier"] = supplier
	rows = frappe.db.sql(
		f"""
		select
			avg(lead_time_days) as lead_time_days,
			count(*) as sample_size
		from (
			select datediff(poi.schedule_date, po.transaction_date) as lead_time_days
			from `tabPurchase Order Item` poi
			inner join `tabPurchase Order` po on po.name = poi.parent
			where po.docstatus = 1
				and poi.item_code = %(item_code)s
				and poi.schedule_date is not null
				and po.transaction_date is not null
				and datediff(poi.schedule_date, po.transaction_date) > 0
				{supplier_clause}
			order by po.transaction_date desc, poi.creation desc
			limit 20
		) history
		""",
		params,
		as_dict=True,
	)
	if not rows or not flt(rows[0].get("lead_time_days")):
		return None
	return rows[0]


def _set_buffer_as_default_if_possible(buffer_name: str, item_code: str, company: str | None) -> None:
	if not buffer_name or not item_code or not company:
		return
	duplicate = frappe.db.exists(
		"MRP Stock Buffer",
		{
			"active": 1,
			"company": company,
			"item_code": item_code,
			"is_default_for_item": 1,
			"name": ["!=", buffer_name],
		},
	)
	if duplicate:
		return
	frappe.db.set_value("MRP Stock Buffer", buffer_name, "is_default_for_item", 1)
	doc = frappe.get_doc("MRP Stock Buffer", buffer_name)
	sync_default_buffer_to_item(doc)


def _item_query_filters(filters: dict[str, Any]) -> dict[str, Any]:
	query_filters: dict[str, Any] = {}
	if filters.get("item_code"):
		query_filters["name"] = filters.get("item_code")
	if filters.get("item_group"):
		groups = _get_item_group_with_descendants(filters.get("item_group"))
		query_filters["item_group"] = ["in", groups] if len(groups) > 1 else filters.get("item_group")
	return query_filters


def _stock_buffer_console_item_fields() -> list[str]:
	fields = ["name", "item_name", "item_group", "stock_uom"]
	for fieldname in (
		"is_stock_item",
		"disabled",
		"default_warehouse",
		"custom_mrp_use_stock_buffer",
			"custom_mrp_default_stock_buffer",
			"custom_mrp_lead_time_days",
			"safety_stock",
			"lead_time_days",
			"lead_time",
		"min_order_qty",
		"purchase_uom",
	):
		if _has_field("Item", fieldname):
			fields.append(fieldname)
	return fields


def _iter_stock_buffer_console_items(filters: dict[str, Any], page_length: int = BULK_PAGE_LENGTH):
	query_filters = _item_query_filters(filters)
	for rows in _iter_get_all(
		"Item",
		filters=query_filters,
		fields=_stock_buffer_console_item_fields(),
		order_by="item_group asc, name asc",
		page_length=page_length,
	):
		yield rows


def _iter_stock_buffer_console_rows(filters: dict[str, Any] | None = None, page_length: int = BULK_PAGE_LENGTH):
	filters = filters or {}
	company = filters.get("company") or _get_default_buffer_company()
	status_filter = filters.get("status")
	for items in _iter_stock_buffer_console_items(filters, page_length=page_length):
		item_codes = [row.name for row in items]
		item_default_warehouses = _get_item_default_warehouse_map(item_codes, company)
		buffers_by_key, default_buffers_by_item = _get_console_buffer_maps(company, item_codes)
		for item in items:
			warehouse = _get_item_buffer_warehouse(item, company, item_default_warehouses)
			key = (item.name, warehouse or "")
			buffers = buffers_by_key.get(key, [])
			default_buffers = default_buffers_by_item.get(item.name, [])
			buffer = buffers[0] if buffers else frappe._dict()
			status = _get_console_status(item, warehouse, buffers, len(default_buffers))
			if status_filter and status != status_filter:
				continue
			yield _make_console_row(item, company, warehouse, buffer, status, buffers, default_buffers)


def _get_console_buffer_maps(company: str | None, item_codes: list[str]):
	if not company or not item_codes or not _doctype_exists("MRP Stock Buffer"):
		return {}, {}
	fields = [
		"name",
		"company",
		"item_code",
		"warehouse",
		"dlt_days",
		"min_order_qty",
		"order_multiple_qty",
		"adu",
		"red_zone_qty",
		"yellow_zone_qty",
		"green_zone_qty",
		"top_of_red",
		"top_of_yellow",
		"top_of_green",
		"on_hand_qty",
		"incoming_dlt_qty",
		"qualified_demand_qty",
		"net_flow_position",
		"net_flow_position_percent",
		"planning_priority",
		"recommended_qty",
		"last_calculated_on",
		"is_default_for_item",
	]
	for fieldname in (
		"suggested_dlt_days",
		"suggested_dlt_source",
		"suggested_dlt_confidence",
		"suggested_min_order_qty",
		"suggested_order_multiple_qty",
		"suggestions_calculated_on",
		"suggestion_notes",
	):
		if _has_field("MRP Stock Buffer", fieldname):
			fields.append(fieldname)
	by_key: dict[tuple[str, str], list[frappe._dict]] = {}
	default_buffers_by_item: dict[str, list[frappe._dict]] = {}
	for rows in _iter_get_all(
		"MRP Stock Buffer",
		filters={"active": 1, "company": company, "item_code": ["in", item_codes]},
		fields=fields,
		order_by="item_code asc, warehouse asc, name asc",
	):
		for row in rows:
			by_key.setdefault((row.item_code, row.warehouse or ""), []).append(row)
			if cint(row.is_default_for_item):
				default_buffers_by_item.setdefault(row.item_code, []).append(row)
	return by_key, default_buffers_by_item


def _get_console_status(item, warehouse: str | None, buffers: list[Any], default_count: int) -> str:
	if not cint(item.get("custom_mrp_use_stock_buffer")) or not _item_is_stock_enabled(item):
		return "Disabled"
	if not warehouse:
		return "Missing Warehouse"
	if len(buffers) > 1 or default_count > 1:
		return "Conflict"
	if not buffers:
		return "Conflict" if default_count else "Missing Buffer"
	buffer = buffers[0]
	if default_count and not cint(buffer.get("is_default_for_item")):
		return "Conflict"
	if not flt(buffer.get("dlt_days")):
		if flt(buffer.get("suggested_dlt_days")) > 0:
			return "Review DLT"
		return "Missing DLT"
	if _buffer_needs_refresh(buffer):
		return "Needs Refresh"
	if _has_meaningful_suggestion_diff(buffer.get("dlt_days"), buffer.get("suggested_dlt_days")):
		return "DLT Mismatch"
	if (
		_has_meaningful_suggestion_diff(buffer.get("min_order_qty"), buffer.get("suggested_min_order_qty"))
		or _has_meaningful_suggestion_diff(buffer.get("order_multiple_qty"), buffer.get("suggested_order_multiple_qty"))
	):
		return "Procurement Mismatch"
	if buffer.get("suggested_dlt_confidence") == SUGGESTION_LOW and flt(buffer.get("suggested_dlt_days")) > 0:
		return "Low Confidence"
	return "Active"


def _get_console_status_detail(item, warehouse: str | None, buffer, status: str, buffers: list[Any], default_buffers: list[Any]) -> str:
	if status == "Disabled":
		if not _item_is_stock_enabled(item):
			return _("Item is disabled or is not a stock item, so MRP stock buffer automation will not create or refresh a buffer.")
		return _("Use MRP Stock Buffer is off on the Item master, so this item is excluded from buffer automation.")
	if status == "Missing Warehouse":
		return _("No default warehouse was found for this item and company. Maintain Item Default Warehouse before creating a stock buffer.")
	if status == "Missing Buffer":
		return _("The item is enabled for MRP stock buffer automation, but no active buffer exists for this company, item and warehouse.")
	if status == "Conflict":
		return _get_console_conflict_detail(item, warehouse, buffers, default_buffers) or _(
			"Active stock buffer records conflict with the item default buffer setup."
		)
	if status == "Missing DLT":
		return _("The buffer DLT is empty and no DLT suggestion is available. Maintain DLT manually or add a lead-time source.")
	if status == "Review DLT":
		return _("The buffer DLT is empty, but the system found a suggested DLT. Review the source and apply it if it is correct.")
	if status == "Needs Refresh":
		return _("The buffer was not calculated recently. Refresh it so on-hand stock, incoming supply and qualified demand are recalculated.")
	if status == "DLT Mismatch":
		return _("The maintained buffer DLT differs from the system suggested DLT. Review the source before applying the suggestion.")
	if status == "Procurement Mismatch":
		return _("The maintained purchase constraints differ from the system suggestions.")
	if status == "Low Confidence":
		return _("The suggested DLT comes from a low-confidence source and should be reviewed manually before applying.")
	return _("No setup issue was detected for this stock buffer.")


def _get_console_conflict_detail(item, warehouse: str | None, buffers: list[Any], default_buffers: list[Any]) -> str:
	details = []
	if len(buffers) > 1:
		details.append(
			_("Multiple active buffers exist for the same company, item and warehouse: {0}.").format(
				_format_buffer_refs(buffers)
			)
		)
	if len(default_buffers) > 1:
		details.append(
			_("Multiple active default buffers exist for this item: {0}.").format(
				_format_buffer_refs(default_buffers)
			)
		)
	if not buffers and default_buffers:
		details.append(
			_("The item default buffer exists, but not for the resolved warehouse {0}: {1}.").format(
				warehouse or _("blank"),
				_format_buffer_refs(default_buffers),
			)
		)
	if buffers and default_buffers:
		default_names = {row.get("name") for row in default_buffers}
		if buffers[0].get("name") not in default_names:
			details.append(
				_("The buffer for the resolved warehouse is {0}, but the item default buffer is {1}.").format(
					buffers[0].get("name"),
					_format_buffer_refs(default_buffers),
				)
			)
	return " ".join(details)


def _format_buffer_refs(buffers: list[Any]) -> str:
	refs = []
	for row in buffers or []:
		refs.append("{0} ({1})".format(row.get("name"), row.get("warehouse") or _("blank warehouse")))
	return "; ".join(refs)


def _get_dlt_suggestion_detail(buffer) -> str:
	suggested_dlt = flt(buffer.get("suggested_dlt_days"))
	source = buffer.get("suggested_dlt_source")
	if suggested_dlt <= 0:
		return _("No maintained DLT source was found.")
	return _("Suggested DLT is {0} day(s), calculated from {1}.").format(
		_format_qty(suggested_dlt),
		source or _("an unspecified source"),
	)


def _get_dlt_confidence_detail(buffer) -> str:
	confidence = buffer.get("suggested_dlt_confidence")
	source = buffer.get("suggested_dlt_source") or ""
	if not confidence:
		return _("No confidence rating is available because no DLT suggestion was found.")
	if confidence == SUGGESTION_HIGH:
		return _(
			"High confidence means the source is specific to this item or matched supplier, such as an item-specific supply rule or supplier quotation."
		)
	if confidence == SUGGESTION_MEDIUM:
		return _(
			"Medium confidence means the source is maintained but less direct, such as item lead time, a broader supply rule, supplier item price, or enough purchase history."
		)
	if confidence == SUGGESTION_LOW:
		if "Purchase Order History" in source:
			return _("Low confidence means the purchase history sample is small. Review the suggested DLT before applying it.")
		return _("Low confidence means the source is generic, missing supplier context, or no maintained lead-time source was found.")
	return _("Review the DLT suggestion before applying it.")


def _get_procurement_mismatch_detail(buffer) -> str:
	parts = []
	if _has_meaningful_suggestion_diff(buffer.get("min_order_qty"), buffer.get("suggested_min_order_qty")):
		parts.append(
			_("Minimum Order Qty is {0}, but the suggested value is {1}.").format(
				_format_qty(buffer.get("min_order_qty")),
				_format_qty(buffer.get("suggested_min_order_qty")),
			)
		)
	if _has_meaningful_suggestion_diff(buffer.get("order_multiple_qty"), buffer.get("suggested_order_multiple_qty")):
		parts.append(
			_("Order Multiple Qty is {0}, but the suggested value is {1}.").format(
				_format_qty(buffer.get("order_multiple_qty")),
				_format_qty(buffer.get("suggested_order_multiple_qty")),
			)
		)
	return " ".join(parts) or _("Maintained procurement constraints currently match the available suggestions.")


def _format_qty(value) -> str:
	text = f"{flt(value):.6f}".rstrip("0").rstrip(".")
	return text or "0"


def _buffer_needs_refresh(buffer) -> bool:
	last_calculated_on = buffer.get("last_calculated_on")
	if not last_calculated_on:
		return True
	try:
		age_hours = (now_datetime() - get_datetime(last_calculated_on)).total_seconds() / 3600
	except Exception:
		return True
	return age_hours > BUFFER_REFRESH_STALE_HOURS


def _make_console_row(
	item,
	company: str | None,
	warehouse: str | None,
	buffer,
	status: str,
	buffers: list[Any] | None = None,
	default_buffers: list[Any] | None = None,
) -> frappe._dict:
	buffers = buffers or []
	default_buffers = default_buffers or []
	return frappe._dict(
		{
			"name": item.name,
			"item_code": item.name,
			"item_name": item.get("item_name"),
			"item_group": item.get("item_group"),
			"stock_uom": item.get("stock_uom"),
			"item_safety_stock": flt(item.get("safety_stock")),
			"company": company,
			"warehouse": warehouse,
			"use_stock_buffer": cint(item.get("custom_mrp_use_stock_buffer")),
			"status": status,
			"status_detail": _get_console_status_detail(item, warehouse, buffer, status, buffers, default_buffers),
			"conflict_detail": _get_console_conflict_detail(item, warehouse, buffers, default_buffers),
			"stock_buffer": buffer.get("name"),
			"active_buffer_count": len(buffers),
			"active_buffer_detail": _format_buffer_refs(buffers),
			"default_buffer_count": len(default_buffers),
			"default_buffer_detail": _format_buffer_refs(default_buffers),
			"buffer_priority": buffer.get("planning_priority"),
			"buffer_nfp_percent": flt(buffer.get("net_flow_position_percent")),
			"buffer_recommended_qty": flt(buffer.get("recommended_qty")),
			"buffer_top_of_red": flt(buffer.get("top_of_red")),
			"buffer_top_of_yellow": flt(buffer.get("top_of_yellow")),
			"buffer_top_of_green": flt(buffer.get("top_of_green")),
			"planning_priority": buffer.get("planning_priority"),
			"net_flow_position_percent": flt(buffer.get("net_flow_position_percent")),
			"recommended_qty": flt(buffer.get("recommended_qty")),
			"dlt_days": flt(buffer.get("dlt_days")),
			"min_order_qty": flt(buffer.get("min_order_qty")),
			"order_multiple_qty": flt(buffer.get("order_multiple_qty")),
			"suggested_dlt_days": flt(buffer.get("suggested_dlt_days")),
			"suggested_dlt_source": buffer.get("suggested_dlt_source"),
			"suggested_dlt_confidence": buffer.get("suggested_dlt_confidence"),
			"suggested_min_order_qty": flt(buffer.get("suggested_min_order_qty")),
			"suggested_order_multiple_qty": flt(buffer.get("suggested_order_multiple_qty")),
			"suggestions_calculated_on": buffer.get("suggestions_calculated_on"),
			"suggestion_notes": buffer.get("suggestion_notes"),
			"dlt_suggestion_detail": _get_dlt_suggestion_detail(buffer),
			"dlt_confidence_detail": _get_dlt_confidence_detail(buffer),
			"procurement_mismatch_detail": _get_procurement_mismatch_detail(buffer),
			"adu": flt(buffer.get("adu")),
			"red_zone_qty": flt(buffer.get("red_zone_qty")),
			"yellow_zone_qty": flt(buffer.get("yellow_zone_qty")),
			"green_zone_qty": flt(buffer.get("green_zone_qty")),
			"top_of_red": flt(buffer.get("top_of_red")),
			"top_of_yellow": flt(buffer.get("top_of_yellow")),
			"top_of_green": flt(buffer.get("top_of_green")),
			"on_hand_qty": flt(buffer.get("on_hand_qty")),
			"incoming_dlt_qty": flt(buffer.get("incoming_dlt_qty")),
			"qualified_demand_qty": flt(buffer.get("qualified_demand_qty")),
			"net_flow_position": flt(buffer.get("net_flow_position")),
			"last_calculated_on": buffer.get("last_calculated_on"),
		}
	)


def _stock_buffer_console_cards(rows: list[Any]) -> list[dict[str, Any]]:
	counts: dict[str, int] = {}
	for row in rows:
		counts[row.status] = counts.get(row.status, 0) + 1
	return [
		{"label": _("Enabled Items"), "value": sum(1 for row in rows if row.use_stock_buffer)},
		{"label": _("Active Buffers"), "value": counts.get("Active", 0)},
		{"label": _("Missing Buffers"), "value": counts.get("Missing Buffer", 0)},
		{"label": _("Needs Refresh"), "value": counts.get("Needs Refresh", 0)},
		{"label": _("Review Suggestions"), "value": counts.get("Review DLT", 0) + counts.get("DLT Mismatch", 0) + counts.get("Procurement Mismatch", 0) + counts.get("Low Confidence", 0)},
	]


def _item_group_defaults_to_stock_buffer(item_group: str | None) -> bool:
	return any(_item_group_is_descendant_of(item_group, root) for root in BUFFER_ITEM_GROUP_ROOTS)


def _get_item_group_with_descendants(item_group: str | None) -> list[str]:
	if not item_group or not _doctype_exists("Item Group"):
		return [item_group] if item_group else []
	bounds = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
	if not bounds:
		return [item_group]
	groups = []
	for rows in _iter_get_all(
		"Item Group",
		filters={"lft": [">=", bounds.lft], "rgt": ["<=", bounds.rgt]},
		fields=["name"],
		order_by="lft asc",
	):
		groups.extend(row.name for row in rows)
	return groups or [item_group]


def _item_group_is_descendant_of(item_group: str | None, root_group: str) -> bool:
	if not item_group or not root_group or not _doctype_exists("Item Group"):
		return False
	if item_group == root_group:
		return True
	bounds = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
	root_bounds = frappe.db.get_value("Item Group", root_group, ["lft", "rgt"], as_dict=True)
	if not bounds or not root_bounds:
		return False
	return cint(bounds.lft) >= cint(root_bounds.lft) and cint(bounds.rgt) <= cint(root_bounds.rgt)


def _pagination_args(limit_start: int | str | None, limit_page_length: int | str | None, default_length: int):
	start = max(cint(limit_start), 0)
	page_length = cint(limit_page_length) or default_length
	page_length = min(max(page_length, 1), 10000)
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


def _calculate_past_actual_adu(buffer) -> float:
	horizon = max(cint(buffer.get("horizon_past_days")) or 90, 1)
	if not _doctype_exists("Stock Ledger Entry"):
		return 0
	date_to = getdate(today())
	date_from = add_days(date_to, -horizon + 1)
	params = {
		"item_code": buffer.item_code,
		"warehouse": buffer.warehouse,
		"company": buffer.company,
		"date_from": date_from,
		"date_to": date_to,
	}
	company_clause = "and company = %(company)s" if _has_field("Stock Ledger Entry", "company") else ""
	value = frappe.db.sql(
		f"""
		select sum(abs(actual_qty))
		from `tabStock Ledger Entry`
		where item_code = %(item_code)s
			and warehouse = %(warehouse)s
			and actual_qty < 0
			and posting_date between %(date_from)s and %(date_to)s
			{company_clause}
		""",
		params,
	)[0][0]
	return flt(value) / horizon


def _calculate_future_mrp_adu(buffer, run=None) -> float:
	horizon = max(cint(buffer.get("horizon_future_days")) or 90, 1)
	as_of_date = getdate(getattr(run, "planning_date", None) or today())
	date_to = add_days(as_of_date, horizon - 1)
	mrp_runs = []
	if getattr(run, "name", None):
		mrp_runs.append(run.name)
	latest_completed_run = _get_latest_completed_run(buffer.company)
	if latest_completed_run and latest_completed_run not in mrp_runs:
		mrp_runs.append(latest_completed_run)
	for mrp_run in mrp_runs:
		total = _get_future_mrp_total(buffer, mrp_run, as_of_date, date_to)
		if total:
			return total / horizon
	return 0


def _get_future_mrp_total(buffer, mrp_run: str, as_of_date, date_to) -> float:
	total = 0
	if _doctype_exists("MRP Requirement Line"):
		params = {
			"mrp_run": mrp_run,
			"item_code": buffer.item_code,
			"warehouse": buffer.warehouse,
			"date_from": as_of_date,
			"date_to": date_to,
		}
		total = frappe.db.sql(
			"""
			select sum(gross_qty)
			from `tabMRP Requirement Line`
			where mrp_run = %(mrp_run)s
				and item_code = %(item_code)s
				and ifnull(warehouse, '') = ifnull(%(warehouse)s, '')
				and material_need_date between %(date_from)s and %(date_to)s
			""",
			params,
		)[0][0]
	if not total and _doctype_exists("MRP Demand Snapshot"):
		params = {
			"mrp_run": mrp_run,
			"item_code": buffer.item_code,
			"warehouse": buffer.warehouse,
			"date_from": as_of_date,
			"date_to": date_to,
		}
		total = frappe.db.sql(
			"""
			select sum(demand_qty)
			from `tabMRP Demand Snapshot`
			where mrp_run = %(mrp_run)s
				and item_code = %(item_code)s
				and ifnull(warehouse, '') = ifnull(%(warehouse)s, '')
				and required_date between %(date_from)s and %(date_to)s
			""",
			params,
		)[0][0]
	return flt(total)


def _get_on_hand_qty(item_code: str, company: str, warehouse: str | None) -> float:
	if not _doctype_exists("Bin") or not _doctype_exists("Warehouse"):
		return 0
	params = {"item_code": item_code, "company": company, "warehouse": warehouse}
	warehouse_clause = "and bin.warehouse = %(warehouse)s" if warehouse else ""
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


def _get_incoming_dlt_qty(item_code: str, company: str, warehouse: str | None, cutoff_date) -> float:
	return (
		_get_open_material_request_qty(item_code, company, warehouse, cutoff_date)
		+ _get_open_purchase_order_qty(item_code, company, warehouse, cutoff_date)
		+ _get_open_work_order_qty(item_code, company, warehouse, cutoff_date)
	)


def _get_qualified_demand_qty(item_code: str, company: str, warehouse: str | None, date_from, cutoff_date) -> float:
	mrp_run = _get_latest_completed_run(company)
	if not mrp_run or not _doctype_exists("MRP Requirement Line"):
		return 0
	params = {
		"mrp_run": mrp_run,
		"item_code": item_code,
		"warehouse": warehouse,
		"date_from": date_from,
		"date_to": cutoff_date,
	}
	value = frappe.db.sql(
		"""
		select sum(gross_qty)
		from `tabMRP Requirement Line`
		where mrp_run = %(mrp_run)s
			and item_code = %(item_code)s
			and ifnull(warehouse, '') = ifnull(%(warehouse)s, '')
			and material_need_date between %(date_from)s and %(date_to)s
		""",
		params,
	)[0][0]
	return flt(value)


def _get_open_material_request_qty(item_code: str, company: str, warehouse: str | None, cutoff_date) -> float:
	if not _doctype_exists("Material Request"):
		return 0
	params = {"item_code": item_code, "company": company, "warehouse": warehouse, "cutoff_date": cutoff_date}
	warehouse_clause = "and mri.warehouse = %(warehouse)s" if warehouse else ""
	value = frappe.db.sql(
		f"""
		select sum(greatest(
			case
				when ifnull(mri.stock_qty, 0) > 0 then mri.stock_qty
				else ifnull(mri.qty, 0) * ifnull(nullif(mri.conversion_factor, 0), 1)
			end - greatest(ifnull(mri.ordered_qty, 0), ifnull(mri.received_qty, 0)),
			0
		))
		from `tabMaterial Request Item` mri
		inner join `tabMaterial Request` mr on mr.name = mri.parent
		where mri.item_code = %(item_code)s
			and mr.company = %(company)s
			and mr.docstatus < 2
			and ifnull(mr.status, '') not in ('Closed', 'Stopped', 'Cancelled')
			and mri.schedule_date <= %(cutoff_date)s
			{warehouse_clause}
		""",
		params,
	)[0][0]
	return flt(value)


def _get_open_purchase_order_qty(item_code: str, company: str, warehouse: str | None, cutoff_date) -> float:
	if not _doctype_exists("Purchase Order"):
		return 0
	date_field = _first_field("Purchase Order Item", ("schedule_date", "expected_delivery_date", "delivery_date"))
	date_expr = f"poi.`{date_field}`" if date_field else "po.transaction_date"
	warehouse_clause = "and poi.warehouse = %(warehouse)s" if warehouse and _has_field("Purchase Order Item", "warehouse") else ""
	params = {"item_code": item_code, "company": company, "warehouse": warehouse, "cutoff_date": cutoff_date}
	value = frappe.db.sql(
		f"""
		select sum(greatest(ifnull(poi.qty, 0) - ifnull(poi.received_qty, 0), 0) * ifnull(nullif(poi.conversion_factor, 0), 1))
		from `tabPurchase Order Item` poi
		inner join `tabPurchase Order` po on po.name = poi.parent
		where poi.item_code = %(item_code)s
			and po.company = %(company)s
			and po.docstatus = 1
			and ifnull(po.status, '') not in ('Closed', 'Stopped', 'Cancelled')
			and {date_expr} <= %(cutoff_date)s
			{warehouse_clause}
		""",
		params,
	)[0][0]
	return flt(value)


def _get_open_work_order_qty(item_code: str, company: str, warehouse: str | None, cutoff_date) -> float:
	if not _doctype_exists("Work Order"):
		return 0
	date_field = _first_field("Work Order", ("planned_end_date", "expected_delivery_date", "planned_start_date"))
	date_expr = f"wo.`{date_field}`" if date_field else "wo.creation"
	warehouse_clause = "and wo.fg_warehouse = %(warehouse)s" if warehouse and _has_field("Work Order", "fg_warehouse") else ""
	params = {"item_code": item_code, "company": company, "warehouse": warehouse, "cutoff_date": cutoff_date}
	value = frappe.db.sql(
		f"""
		select sum(greatest(ifnull(wo.qty, 0) - ifnull(wo.produced_qty, 0), 0))
		from `tabWork Order` wo
		where wo.production_item = %(item_code)s
			and wo.company = %(company)s
			and wo.docstatus < 2
			and ifnull(wo.status, '') not in ('Closed', 'Stopped', 'Completed', 'Cancelled')
			and {date_expr} <= %(cutoff_date)s
			{warehouse_clause}
		""",
		params,
	)[0][0]
	return flt(value)


def _get_profile_values(profile_name: str | None) -> frappe._dict:
	if not profile_name or not _doctype_exists("MRP Buffer Profile"):
		return frappe._dict()
	return frappe._dict(
		frappe.db.get_value(
			"MRP Buffer Profile",
			profile_name,
			["lead_time_factor", "variability_factor", "default_order_cycle_days"],
			as_dict=True,
		)
		or {}
	)


def _runtime_cache() -> dict[tuple[str, str, str], frappe._dict]:
	try:
		if not hasattr(frappe.local, "injection_mrp_stock_buffer_cache"):
			frappe.local.injection_mrp_stock_buffer_cache = {}
		return frappe.local.injection_mrp_stock_buffer_cache
	except RuntimeError:
		return _FALLBACK_RUNTIME_CACHE


def _state_cache_key(buffer_name: str | None, run=None) -> tuple[str, str, str]:
	return (
		buffer_name or "",
		str(getattr(run, "name", None) or ""),
		str(getattr(run, "planning_date", None) or ""),
	)


def _sync_standard_item_lead_time_enabled() -> bool:
	if not _has_field("MRP Settings", "sync_buffer_dlt_to_item_lead_time"):
		return False
	try:
		return bool(cint(frappe.db.get_single_value("MRP Settings", "sync_buffer_dlt_to_item_lead_time")))
	except Exception:
		return False


def _sync_item_safety_stock_enabled() -> bool:
	if not _has_field("MRP Settings", "sync_buffer_safety_stock_to_item"):
		return False
	try:
		return bool(cint(frappe.db.get_single_value("MRP Settings", "sync_buffer_safety_stock_to_item")))
	except Exception:
		return False


def _get_latest_completed_run(company: str | None) -> str | None:
	if not company or not _doctype_exists("MRP Run"):
		return None
	rows = frappe.get_all(
		"MRP Run",
		filters={"company": company, "status": ["in", ["Calculated", "Proposal Generated", "Released"]]},
		fields=["name"],
		order_by="planning_date desc, modified desc",
		limit_page_length=1,
	)
	return rows[0].name if rows else None


def _first_field(doctype: str, fieldnames: tuple[str, ...]) -> str | None:
	for fieldname in fieldnames:
		if _has_field(doctype, fieldname):
			return fieldname
	return None


def _doctype_exists(doctype: str) -> bool:
	try:
		return bool(frappe.db.exists("DocType", doctype))
	except Exception:
		return False


def _has_field(doctype: str, fieldname: str) -> bool:
	if not _doctype_exists(doctype):
		return False
	try:
		return frappe.get_meta(doctype).has_field(fieldname)
	except Exception:
		return False
