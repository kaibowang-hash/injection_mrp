from __future__ import annotations

import frappe


INDEXES = [
	("imrp_buffer_item_wh", ["active", "company", "item_code", "warehouse"]),
	("imrp_buffer_default_item", ["active", "company", "item_code", "is_default_for_item"]),
]


def execute():
	if not frappe.db.exists("DocType", "MRP Stock Buffer"):
		return
	for index_name, fields in INDEXES:
		_add_index_if_possible(index_name, fields)


def _add_index_if_possible(index_name: str, fields: list[str]):
	table = "tabMRP Stock Buffer"
	existing_columns = {row.Field for row in frappe.db.sql(f"show columns from `{table}`", as_dict=True)}
	if any(field not in existing_columns for field in fields):
		return
	try:
		frappe.db.add_index("MRP Stock Buffer", fields, index_name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Failed to add Injection MRP stock buffer index {index_name}")
