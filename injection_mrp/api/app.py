from __future__ import annotations

import inspect

import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.xlsxutils import make_xlsx

from injection_mrp.services import planning
from injection_mrp.services import stock_buffer


READ_ROLES = {
	"System Manager",
	"MPLM",
	"MPLP",
	"GMC",
	"PMC",
	"Manufacturing Manager",
	"Manufacturing User",
	"Purchase Manager",
	"Purchase User",
	"Stock Manager",
	"Stock User",
	"Sales Manager",
	"Sales User",
}
PLAN_ROLES = {
	"System Manager",
	"MPLM",
	"MPLP",
}
RELEASE_ROLES = {
	"System Manager",
	"MPLM",
	"MPLP",
}


@frappe.whitelist()
def run_forecast_prebuy(company=None, item_code=None, customer=None, warehouse=None, planning_date=None):
	_require_any_role(PLAN_ROLES)
	return planning.run_forecast_prebuy(
		company=company,
		item_code=item_code,
		customer=customer,
		warehouse=warehouse,
		planning_date=planning_date,
	)


@frappe.whitelist()
def enqueue_forecast_prebuy(company=None, item_code=None, customer=None, warehouse=None, planning_date=None):
	_require_any_role(PLAN_ROLES)
	return planning.enqueue_forecast_prebuy(
		company=company,
		item_code=item_code,
		customer=customer,
		warehouse=warehouse,
		planning_date=planning_date,
	)


@frappe.whitelist()
def run_firm_aps_mrp(company=None, aps_run=None, item_code=None, customer=None, warehouse=None, planning_date=None):
	_require_any_role(PLAN_ROLES)
	return planning.run_firm_aps_mrp(
		company=company,
		aps_run=aps_run,
		item_code=item_code,
		customer=customer,
		warehouse=warehouse,
		planning_date=planning_date,
	)


@frappe.whitelist()
def enqueue_firm_aps_mrp(company=None, aps_run=None, item_code=None, customer=None, warehouse=None, planning_date=None):
	_require_any_role(PLAN_ROLES)
	return planning.enqueue_firm_aps_mrp(
		company=company,
		aps_run=aps_run,
		item_code=item_code,
		customer=customer,
		warehouse=warehouse,
		planning_date=planning_date,
	)


@frappe.whitelist()
def recalculate_mrp_run(mrp_run):
	_require_any_role(PLAN_ROLES)
	return planning.run_mrp(mrp_run=mrp_run)


@frappe.whitelist()
def enqueue_recalculate_mrp_run(mrp_run):
	_require_any_role(PLAN_ROLES)
	return planning.enqueue_recalculate_mrp_run(mrp_run)


@frappe.whitelist()
def apply_proposal_batch(batch_name):
	_require_any_role(RELEASE_ROLES)
	return planning.apply_proposal_batch(batch_name)


@frappe.whitelist()
def validate_proposal_batch_for_release(batch_name):
	_require_any_role(RELEASE_ROLES)
	return planning.validate_proposal_batch_for_release(batch_name)


@frappe.whitelist()
def save_proposal_batch_items(batch_name, items=None):
	_require_any_role(RELEASE_ROLES)
	return planning.save_proposal_batch_items(batch_name, _parse_json(items) if isinstance(items, str) else items)


@frappe.whitelist()
def get_run_console_data(limit=20):
	_require_any_role(READ_ROLES)
	return planning.get_run_console_data(limit)


@frappe.whitelist()
def get_run_comparison_data(mrp_run, previous_run=None):
	_require_any_role(READ_ROLES)
	return planning.get_run_comparison_data(mrp_run, previous_run)


@frappe.whitelist()
def get_demand_console_data(filters=None, limit_start=0, limit_page_length=500):
	_require_any_role(READ_ROLES)
	return planning.get_demand_console_data(_parse_json(filters), limit_start, limit_page_length)


@frappe.whitelist()
def get_material_workbench_data(filters=None, limit_start=0, limit_page_length=500):
	_require_any_role(READ_ROLES)
	return planning.get_material_workbench_data(_parse_json(filters), limit_start, limit_page_length)


@frappe.whitelist()
def get_pegging_detail_data(filters=None, limit_start=0, limit_page_length=500):
	_require_any_role(READ_ROLES)
	return planning.get_pegging_detail_data(_parse_json(filters), limit_start, limit_page_length)


@frappe.whitelist()
def get_shortage_timeline_data(
	filters=None,
	limit_start=0,
	limit_page_length=500,
	balance_limit_start=0,
	balance_limit_page_length=1000,
):
	_require_any_role(READ_ROLES)
	return planning.get_shortage_timeline_data(
		_parse_json(filters),
		limit_start,
		limit_page_length,
		balance_limit_start,
		balance_limit_page_length,
	)


@frappe.whitelist()
def get_release_center_data(filters=None, limit_start=0, limit_page_length=100):
	_require_any_role(READ_ROLES)
	return planning.get_release_center_data(_parse_json(filters), limit_start, limit_page_length)


@frappe.whitelist()
def get_requirement_detail(requirement_line):
	_require_any_role(READ_ROLES)
	return planning.get_requirement_detail(requirement_line)


@frappe.whitelist()
def get_stock_buffer_chart_data(buffer_name=None, item_code=None, company=None, warehouse=None):
	_require_any_role(READ_ROLES)
	if buffer_name:
		_require_doc_permission("MRP Stock Buffer", buffer_name, "read")
		return stock_buffer.get_chart_data(buffer_name=buffer_name)
	resolved_buffer = stock_buffer.get_buffer_name_for_item(item_code or "", company, warehouse)
	if not resolved_buffer:
		return frappe._dict()
	_require_doc_permission("MRP Stock Buffer", resolved_buffer, "read")
	return stock_buffer.get_chart_data(buffer_name=resolved_buffer)


@frappe.whitelist()
def get_stock_buffer_console_data(filters=None, limit_start=0, limit_page_length=500):
	_require_any_role(READ_ROLES)
	return stock_buffer.get_stock_buffer_console_data(_parse_json(filters), limit_start, limit_page_length)


@frappe.whitelist()
def refresh_stock_buffer(buffer_name):
	_require_any_role(PLAN_ROLES)
	doc = _require_doc_permission("MRP Stock Buffer", buffer_name, "write")
	return stock_buffer.refresh_buffer(doc, persist=True)


@frappe.whitelist()
def recalculate_stock_buffers(company=None, item_code=None, warehouse=None, item_codes=None, filters=None):
	_require_any_role(PLAN_ROLES)
	parsed_filters = _parse_json(filters)
	parsed_item_codes = _parse_json(item_codes) if isinstance(item_codes, str) else item_codes
	if parsed_filters and not parsed_item_codes and not item_code and not warehouse:
		console_data = stock_buffer.get_stock_buffer_console_data(parsed_filters, limit_start=0, limit_page_length=10000)
		parsed_item_codes = [row.item_code for row in console_data.get("rows", []) if row.get("stock_buffer")]
		company = company or parsed_filters.get("company")
	return stock_buffer.refresh_active_stock_buffers(
		company=company,
		item_code=item_code,
		warehouse=warehouse,
		item_codes=parsed_item_codes,
	)


@frappe.whitelist()
def create_missing_stock_buffers(filters=None, item_codes=None):
	_require_any_role(PLAN_ROLES)
	return stock_buffer.create_missing_stock_buffers(
		_parse_json(filters),
		_parse_json(item_codes) if isinstance(item_codes, str) else item_codes,
	)


@frappe.whitelist()
def apply_stock_buffer_item_group_defaults(filters=None, item_codes=None):
	_require_any_role(PLAN_ROLES)
	return stock_buffer.apply_stock_buffer_item_group_defaults(
		_parse_json(filters),
		_parse_json(item_codes) if isinstance(item_codes, str) else item_codes,
	)


@frappe.whitelist()
def get_batch_detail(batch_name):
	_require_any_role(READ_ROLES)
	return planning.get_batch_detail(batch_name)


@frappe.whitelist()
def export_table_xlsx(payload_json):
	_require_any_role(READ_ROLES)
	payload = frappe.parse_json(payload_json) if payload_json else {}
	if not isinstance(payload, dict):
		frappe.throw(_("Invalid export payload."))

	columns = payload.get("columns") or []
	rows = payload.get("rows") or []
	if not columns or not rows:
		frappe.throw(_("No rows available to export."))

	title = str(payload.get("title") or _("Export Excel"))
	subtitle = str(payload.get("subtitle") or "")
	sheet_name = str(payload.get("sheet_name") or title)[:28]
	file_name = str(payload.get("file_name") or "mrp_export.xlsx")
	if not file_name.lower().endswith(".xlsx"):
		file_name = f"{file_name}.xlsx"

	header_row = [str(column.get("label") or column.get("fieldname") or "") for column in columns]
	fieldnames = [str(column.get("fieldname") or "") for column in columns]
	fieldtypes = [str(column.get("fieldtype") or "") for column in columns]
	column_count = max(len(columns), 1)

	def pad_row(values):
		row_values = list(values)[:column_count]
		if len(row_values) < column_count:
			row_values.extend([""] * (column_count - len(row_values)))
		return row_values

	data = [pad_row([title])]
	if subtitle:
		data.append(pad_row([subtitle]))
	data.append(pad_row([_("Generated On"), now_datetime()]))
	data.append([""] * column_count)
	header_index = len(data)
	data.append(header_row)

	export_rows = []
	for row in rows:
		export_rows.append(
			[
				_coerce_export_value((row or {}).get(fieldname), fieldtype)
				for fieldname, fieldtype in zip(fieldnames, fieldtypes, strict=False)
			]
		)
	data.extend(export_rows)

	column_widths = [
		_estimate_column_width(header_row[idx], [export_row[idx] for export_row in export_rows])
		for idx in range(len(header_row))
	]

	xlsx_file = _make_xlsx_compat(data, sheet_name, column_widths=column_widths, header_index=header_index)
	frappe.local.response.filecontent = xlsx_file.getvalue()
	frappe.local.response.type = "download"
	frappe.local.response.filename = file_name
	frappe.local.response.content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_json(value):
	if not value:
		return {}
	if isinstance(value, dict):
		return value
	return frappe.parse_json(value)


def _require_any_role(roles):
	if frappe.session.user == "Administrator":
		return
	user_roles = set(frappe.get_roles(frappe.session.user))
	if not user_roles.intersection(roles):
		frappe.throw(_("Not permitted for Injection MRP."), frappe.PermissionError)


def _require_doc_permission(doctype, name, permission_type="read"):
	if not name:
		frappe.throw(_("Missing document name."), frappe.PermissionError)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission(permission_type)
	return doc


def _make_xlsx_compat(data, sheet_name, column_widths=None, header_index=None):
	kwargs = {"column_widths": column_widths}
	if "header_index" in inspect.signature(make_xlsx).parameters:
		kwargs["header_index"] = header_index
	return make_xlsx(data, sheet_name, **kwargs)


def _coerce_export_value(value, fieldtype=None):
	if value in (None, ""):
		return ""
	if fieldtype in {"Float", "Currency", "Percent"}:
		try:
			return float(value)
		except Exception:
			return str(value)
	if fieldtype in {"Int", "Check"}:
		try:
			return int(value)
		except Exception:
			return str(value)
	return str(value)


def _estimate_column_width(label, values):
	width = len(str(label or ""))
	for value in values:
		width = max(width, len(str(value or "")))
	return min(max(width + 2, 12), 42)
