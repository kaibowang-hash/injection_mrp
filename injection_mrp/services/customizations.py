from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from injection_mrp.setup.resources import STANDARD_CUSTOM_FIELDS, get_standard_custom_field_names


MRP_TRANSACTION_DOCTYPES = (
	"MRP Run",
	"MRP Demand Snapshot",
	"MRP Requirement Line",
	"MRP Pegging Line",
	"MRP Rolling Balance Line",
	"MRP Shortage Alert",
	"MRP Proposal Batch",
	"MRP Exception Log",
	"MRP Stock Buffer",
)


def ensure_standard_customizations():
	create_custom_fields(_standard_custom_fields_for_site(), update=True)
	frappe.clear_cache()


def ensure_default_settings():
	settings = frappe.get_single("MRP Settings")
	settings.company = settings.company or frappe.defaults.get_user_default("Company")
	settings.firm_horizon_days = settings.firm_horizon_days or 45
	settings.prebuy_horizon_days = settings.prebuy_horizon_days or 120
	settings.forecast_consumption_window_days = settings.forecast_consumption_window_days or 30
	settings.material_staging_days = settings.material_staging_days or 7
	settings.early_supply_warning_days = settings.early_supply_warning_days or 7
	settings.late_supply_tolerance_days = settings.late_supply_tolerance_days or 0
	settings.warn_missing_lead_time = (
		1 if settings.warn_missing_lead_time is None else settings.warn_missing_lead_time
	)
	if settings.meta.has_field("use_stock_buffer_for_safety_stock"):
		settings.use_stock_buffer_for_safety_stock = (
			1
			if settings.use_stock_buffer_for_safety_stock is None
			else settings.use_stock_buffer_for_safety_stock
		)
	if settings.meta.has_field("sync_buffer_safety_stock_to_item"):
		settings.sync_buffer_safety_stock_to_item = (
			1
			if settings.sync_buffer_safety_stock_to_item is None
			else settings.sync_buffer_safety_stock_to_item
		)
	if settings.meta.has_field("sync_buffer_dlt_to_item_lead_time"):
		settings.sync_buffer_dlt_to_item_lead_time = (
			0
			if settings.sync_buffer_dlt_to_item_lead_time is None
			else settings.sync_buffer_dlt_to_item_lead_time
		)
	settings.use_material_need_date_for_pegging = (
		1
		if settings.use_material_need_date_for_pegging is None
		else settings.use_material_need_date_for_pegging
	)
	settings.rolling_daily_horizon_days = settings.rolling_daily_horizon_days or 60
	settings.allow_prebuy_material_request = 1 if settings.allow_prebuy_material_request is None else settings.allow_prebuy_material_request
	settings.include_production_plan_as_supply = (
		1
		if settings.include_production_plan_as_supply is None
		else settings.include_production_plan_as_supply
	)
	settings.include_production_plan_as_demand = (
		0
		if settings.include_production_plan_as_demand is None
		else settings.include_production_plan_as_demand
	)
	settings.default_material_request_type = settings.default_material_request_type or "Purchase"
	settings.flags.ignore_mandatory = True
	settings.save(ignore_permissions=True)
	ensure_default_buffer_profile()


def ensure_default_buffer_profile():
	if not frappe.db.exists("DocType", "MRP Buffer Profile"):
		return
	if frappe.db.exists("MRP Buffer Profile", "standard-replenish"):
		return
	frappe.get_doc(
		{
			"doctype": "MRP Buffer Profile",
			"profile_name": "standard-replenish",
			"active": 1,
			"lead_time_factor": 1,
			"variability_factor": 0.5,
			"default_order_cycle_days": 0,
		}
	).insert(ignore_permissions=True)


def ensure_stock_buffer_item_defaults():
	if not _has_field("Item", "custom_mrp_use_stock_buffer"):
		return
	if frappe.db.get_global("injection_mrp_stock_buffer_item_defaults_applied"):
		return
	from injection_mrp.services import stock_buffer

	stock_buffer.apply_stock_buffer_item_group_defaults(ignore_permissions=True)
	frappe.db.set_global("injection_mrp_stock_buffer_item_defaults_applied", "1")


def _standard_custom_fields_for_site():
	fields = {doctype: [dict(field) for field in field_list] for doctype, field_list in STANDARD_CUSTOM_FIELDS.items()}
	item_fields = fields.get("Item") or []
	if item_fields and not _has_field("Item", item_fields[0].get("insert_after")):
		for anchor in ("lead_time_days", "safety_stock", "reorder_levels", "item_group"):
			if _has_field("Item", anchor):
				item_fields[0]["insert_after"] = anchor
				break
	return fields


def _has_field(doctype, fieldname):
	return frappe.db.exists("DocType", doctype) and frappe.get_meta(doctype).has_field(fieldname)


def ensure_safe_to_uninstall():
	blockers = []

	for doctype in MRP_TRANSACTION_DOCTYPES:
		if frappe.db.exists("DocType", doctype) and frappe.db.count(doctype):
			blockers.append(doctype)

	for doctype, fields in STANDARD_CUSTOM_FIELDS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		for field in fields:
			fieldname = field.get("fieldname")
			if not fieldname or not meta.has_field(fieldname):
				continue
			if frappe.db.sql(
				f"""
				select name
				from `tab{doctype}`
				where ifnull(`{fieldname}`, '') != ''
				limit 1
				"""
			):
				blockers.append(f"{doctype}.{fieldname}")

	if blockers:
		raise frappe.ValidationError(
			_(
				"Cannot uninstall Injection MRP while MRP business data or standard-document references still exist: {0}"
			).format(", ".join(blockers))
		)


def remove_standard_customizations():
	for name in get_standard_custom_field_names():
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, force=1, ignore_permissions=True)

	frappe.clear_cache()
