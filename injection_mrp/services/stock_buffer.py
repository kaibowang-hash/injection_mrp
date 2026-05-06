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
	company = filters.get("company") or _get_default_buffer_company()
	items = _get_stock_buffer_console_items(filters)
	item_codes = [row.name for row in items]
	item_default_warehouses = _get_item_default_warehouse_map(item_codes, company)
	buffers_by_key, default_counts = _get_console_buffer_maps(company, item_codes)
	rows = []
	for item in items:
		warehouse = _get_item_buffer_warehouse(item, company, item_default_warehouses)
		key = (item.name, warehouse or "")
		buffers = buffers_by_key.get(key, [])
		buffer = buffers[0] if buffers else frappe._dict()
		status = _get_console_status(item, warehouse, buffers, default_counts.get(item.name, 0))
		rows.append(_make_console_row(item, company, warehouse, buffer, status))

	if filters.get("status"):
		rows = [row for row in rows if row.status == filters.get("status")]
	total_count = len(rows)
	page_rows = rows[limit_start : limit_start + limit_page_length]
	return {
		"cards": _stock_buffer_console_cards(rows),
		"rows": page_rows,
		"pagination": _pagination_meta(total_count, limit_start, limit_page_length, len(page_rows)),
	}


def create_missing_stock_buffers(filters: dict[str, Any] | None = None, item_codes: list[str] | None = None) -> dict[str, Any]:
	filters = filters or {}
	data = get_stock_buffer_console_data(filters, limit_start=0, limit_page_length=10000)
	item_set = set(item_codes or [])
	created = []
	skipped = []
	errors = []
	for row in data.get("rows", []):
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
				ignore_permissions=True,
			)
			if buffer:
				created.append({"item_code": row.item_code, "stock_buffer": buffer.name})
			else:
				skipped.append({"item_code": row.item_code, "status": "Skipped"})
		except Exception as exc:
			errors.append({"item_code": row.item_code, "message": str(exc)})
			frappe.log_error(frappe.get_traceback(), _("MRP Stock Buffer bulk create failed"))
	return {"created": len(created), "skipped": len(skipped), "failed": len(errors), "rows": created, "errors": errors}


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
	items = frappe.get_all("Item", filters=query_filters, fields=fields, limit_page_length=10000)
	updated = enabled = disabled = 0
	for item in items:
		value = 1 if _item_is_stock_enabled(item) and _item_group_defaults_to_stock_buffer(item.item_group) else 0
		frappe.db.set_value("Item", item.name, "custom_mrp_use_stock_buffer", value, update_modified=False)
		updated += 1
		if value:
			enabled += 1
		else:
			disabled += 1
	return {"updated": updated, "enabled": enabled, "disabled": disabled}


def refresh_active_stock_buffers(
	company: str | None = None,
	item_code: str | None = None,
	warehouse: str | None = None,
	item_codes: list[str] | None = None,
	ignore_permissions: bool = False,
) -> dict[str, Any]:
	if not _doctype_exists("MRP Stock Buffer"):
		return {"count": 0, "refreshed": 0, "failed": 0, "buffers": [], "errors": []}
	filters: dict[str, Any] = {"active": 1}
	if company:
		filters["company"] = company
	if item_code:
		filters["item_code"] = item_code
	if item_codes:
		filters["item_code"] = ["in", item_codes]
	if warehouse:
		filters["warehouse"] = warehouse
	rows = frappe.get_all("MRP Stock Buffer", filters=filters, fields=["name"], limit_page_length=10000)
	buffers = []
	errors = []
	for row in rows:
		try:
			doc = frappe.get_doc("MRP Stock Buffer", row.name)
			if not ignore_permissions:
				doc.check_permission("write")
			buffers.append(refresh_buffer(doc, persist=True, ignore_permissions=ignore_permissions))
		except Exception as exc:
			errors.append({"stock_buffer": row.name, "message": str(exc)})
			frappe.log_error(frappe.get_traceback(), _("MRP Stock Buffer refresh failed"))
	return {
		"count": len(rows),
		"refreshed": len(buffers),
		"failed": len(errors),
		"buffers": buffers,
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

	buffers = frappe.get_all(
		"MRP Stock Buffer",
		filters=filters,
		fields=["name"],
		order_by="item_code asc, warehouse asc",
		limit_page_length=10000,
	)
	demands = []
	for row in buffers:
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


def refresh_buffer(buffer, run=None, persist: bool = True, ignore_permissions: bool = False) -> frappe._dict:
	doc = frappe.get_doc("MRP Stock Buffer", buffer) if isinstance(buffer, str) else buffer
	state = calculate_buffer_state(doc, run=run)
	for key, value in state.items():
		if key in {"name", "company", "item_code", "item_name", "stock_uom", "warehouse"}:
			continue
		if hasattr(doc, "set"):
			doc.set(key, value)
		else:
			doc[key] = value
	if persist and hasattr(doc, "save"):
		doc.flags.ignore_mrp_buffer_refresh = True
		doc.save(ignore_permissions=ignore_permissions)
	cache_key = _state_cache_key(state.name, run)
	_runtime_cache()[cache_key] = state
	return state


def calculate_buffer_state(buffer, run=None) -> frappe._dict:
	doc = frappe._dict(buffer.as_dict() if hasattr(buffer, "as_dict") else dict(buffer))
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


def sync_default_buffer_to_item(buffer) -> None:
	if not cint(buffer.get("active")) or not cint(buffer.get("is_default_for_item")) or not buffer.get("item_code"):
		return
	if not _doctype_exists("Item"):
		return

	values: dict[str, Any] = {}
	if _has_field("Item", "custom_mrp_default_stock_buffer"):
		values["custom_mrp_default_stock_buffer"] = buffer.name
	if _has_field("Item", "custom_mrp_lead_time_days"):
		values["custom_mrp_lead_time_days"] = cint(buffer.get("dlt_days"))
	if _sync_standard_item_lead_time_enabled() and _has_field("Item", "lead_time_days"):
		values["lead_time_days"] = cint(buffer.get("dlt_days"))
	if values:
		frappe.db.set_value("Item", buffer.item_code, values)


def validate_item_lead_time_lock(doc, method=None):
	if getattr(doc, "flags", None) and doc.flags.get("ignore_mrp_buffer_lead_time_lock"):
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


def get_chart_data(buffer_name: str | None = None, item_code: str | None = None, company: str | None = None, warehouse: str | None = None):
	if buffer_name:
		return refresh_buffer(buffer_name, persist=False)
	return get_buffer_state_for_item(item_code or "", company, warehouse, persist=False) or frappe._dict()


def _coerce_item_row(item) -> frappe._dict:
	if hasattr(item, "as_dict"):
		return frappe._dict(item.as_dict())
	if isinstance(item, str):
		fields = ["name", "item_name", "stock_uom", "item_group"]
		for fieldname in (
			"is_stock_item",
			"default_warehouse",
			"custom_mrp_use_stock_buffer",
			"custom_mrp_lead_time_days",
			"lead_time_days",
			"lead_time",
		):
			if _has_field("Item", fieldname):
				fields.append(fieldname)
		return frappe._dict(frappe.db.get_value("Item", item, fields, as_dict=True) or {"name": item})
	return frappe._dict(dict(item or {}))


def _item_is_stock_enabled(item) -> bool:
	if _has_field("Item", "is_stock_item"):
		return bool(cint(item.get("is_stock_item")))
	return True


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
	rows = frappe.get_all(
		"Item Default",
		filters=filters,
		fields=["parent", "default_warehouse"],
		order_by="idx asc",
		limit_page_length=10000,
	)
	result = {}
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


def _get_stock_buffer_console_items(filters: dict[str, Any]) -> list[frappe._dict]:
	query_filters = _item_query_filters(filters)
	fields = ["name", "item_name", "item_group", "stock_uom"]
	for fieldname in (
		"is_stock_item",
		"disabled",
		"default_warehouse",
		"custom_mrp_use_stock_buffer",
		"custom_mrp_default_stock_buffer",
		"custom_mrp_lead_time_days",
		"lead_time_days",
		"lead_time",
	):
		if _has_field("Item", fieldname):
			fields.append(fieldname)
	return frappe.get_all(
		"Item",
		filters=query_filters,
		fields=fields,
		order_by="item_group asc, name asc",
		limit_page_length=10000,
	)


def _get_console_buffer_maps(company: str | None, item_codes: list[str]):
	if not company or not item_codes or not _doctype_exists("MRP Stock Buffer"):
		return {}, {}
	rows = frappe.get_all(
		"MRP Stock Buffer",
		filters={"active": 1, "company": company, "item_code": ["in", item_codes]},
		fields=[
			"name",
			"company",
			"item_code",
			"warehouse",
			"dlt_days",
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
		],
		limit_page_length=10000,
	)
	by_key: dict[tuple[str, str], list[frappe._dict]] = {}
	default_counts: dict[str, int] = {}
	for row in rows:
		by_key.setdefault((row.item_code, row.warehouse or ""), []).append(row)
		if cint(row.is_default_for_item):
			default_counts[row.item_code] = default_counts.get(row.item_code, 0) + 1
	return by_key, default_counts


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
		return "Missing DLT"
	if _buffer_needs_refresh(buffer):
		return "Needs Refresh"
	return "Active"


def _buffer_needs_refresh(buffer) -> bool:
	last_calculated_on = buffer.get("last_calculated_on")
	if not last_calculated_on:
		return True
	try:
		age_hours = (now_datetime() - get_datetime(last_calculated_on)).total_seconds() / 3600
	except Exception:
		return True
	return age_hours > BUFFER_REFRESH_STALE_HOURS


def _make_console_row(item, company: str | None, warehouse: str | None, buffer, status: str) -> frappe._dict:
	return frappe._dict(
		{
			"name": item.name,
			"item_code": item.name,
			"item_name": item.get("item_name"),
			"item_group": item.get("item_group"),
			"stock_uom": item.get("stock_uom"),
			"company": company,
			"warehouse": warehouse,
			"use_stock_buffer": cint(item.get("custom_mrp_use_stock_buffer")),
			"status": status,
			"stock_buffer": buffer.get("name"),
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
	]


def _item_group_defaults_to_stock_buffer(item_group: str | None) -> bool:
	return any(_item_group_is_descendant_of(item_group, root) for root in BUFFER_ITEM_GROUP_ROOTS)


def _get_item_group_with_descendants(item_group: str | None) -> list[str]:
	if not item_group or not _doctype_exists("Item Group"):
		return [item_group] if item_group else []
	bounds = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
	if not bounds:
		return [item_group]
	rows = frappe.get_all(
		"Item Group",
		filters={"lft": [">=", bounds.lft], "rgt": ["<=", bounds.rgt]},
		fields=["name"],
		limit_page_length=10000,
	)
	return [row.name for row in rows] or [item_group]


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
