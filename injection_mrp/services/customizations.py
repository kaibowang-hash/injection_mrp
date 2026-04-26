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
)


def ensure_standard_customizations():
	create_custom_fields(STANDARD_CUSTOM_FIELDS, update=True)
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
