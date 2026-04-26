from __future__ import annotations

import frappe


INDEXES = {
	"MRP Demand Snapshot": [
		("imrp_dem_run_item_date", ["mrp_run", "item_code", "required_date"]),
		("imrp_dem_company_type_item", ["company", "demand_type", "item_code"]),
	],
	"MRP Requirement Line": [
		("imrp_req_run_item_wh_date", ["mrp_run", "item_code", "warehouse", "material_need_date"]),
		("imrp_req_company_run_status", ["company", "run_type", "status"]),
	],
	"MRP Pegging Line": [
		("imrp_peg_run_item_wh_date", ["mrp_run", "item_code", "warehouse", "material_need_date"]),
		("imrp_peg_supply_action", ["supply_type", "adjustment_action", "warning_level"]),
	],
	"MRP Rolling Balance Line": [
		("imrp_rbl_run_item_wh_bucket", ["mrp_run", "item_code", "warehouse", "bucket_start"]),
	],
	"MRP Shortage Alert": [
		("imrp_short_run_item_wh_date", ["mrp_run", "item_code", "warehouse", "first_shortage_date"]),
		("imrp_short_company_status", ["company", "status", "warning_level"]),
	],
	"MRP Proposal Batch": [
		("imrp_batch_company_status", ["company", "status", "modified"]),
	],
	"MRP Exception Log": [
		("imrp_exc_run_item_status", ["mrp_run", "item_code", "resolution_status"]),
	],
	"MRP Supply Rule": [
		("imrp_rule_match", ["enabled", "company", "item_code", "item_group", "customer", "warehouse"]),
	],
}


def execute():
	for doctype, indexes in INDEXES.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		for index_name, fields in indexes:
			_add_index_if_possible(doctype, index_name, fields)


def _add_index_if_possible(doctype: str, index_name: str, fields: list[str]):
	table = f"tab{doctype}"
	existing_columns = {
		row.Field
		for row in frappe.db.sql(f"show columns from `{table}`", as_dict=True)
	}
	index_fields = [field for field in fields if field in existing_columns]
	if len(index_fields) != len(fields):
		return
	try:
		frappe.db.add_index(doctype, index_fields, index_name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Failed to add Injection MRP index {index_name}")
