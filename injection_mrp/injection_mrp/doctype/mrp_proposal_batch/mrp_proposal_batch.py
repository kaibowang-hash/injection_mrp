import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class MRPProposalBatch(Document):
	def validate(self):
		if self.status == "Applied" and not self.get_doc_before_save():
			return
		before = self.get_doc_before_save()
		if before and before.status == "Applied":
			frappe.throw(_("Applied proposal batches cannot be edited."))

		for row in self.items:
			if row.action == "No Action":
				row.status = "Skipped"
			if flt(row.qty) <= 0:
				row.action = "No Action"
				row.status = "Skipped"
				row.skip_reason = row.skip_reason or _("Quantity is zero.")
			if row.status == "Skipped" and not row.skip_reason:
				row.skip_reason = _("Skipped by planner.")
			if not row.original_qty and flt(row.qty) > 0:
				row.original_qty = row.qty
			if not row.original_schedule_date and row.schedule_date:
				row.original_schedule_date = row.schedule_date
			if row.original_qty and flt(row.qty) != flt(row.original_qty):
				row.manual_override = 1
			if row.original_schedule_date and row.schedule_date != row.original_schedule_date:
				row.manual_override = 1
			if row.requirement_line and self._is_generated_row_changed(before, row):
				row.manual_override = 1
			if not row.requirement_line:
				row.manual_override = 1

		active_rows = [
			row
			for row in self.items
			if row.status != "Skipped" and row.action != "No Action" and flt(row.qty) > 0
		]
		self.item_count = len(active_rows)
		self.total_qty = sum(flt(row.qty) for row in active_rows)

	def _is_generated_row_changed(self, before, row):
		if not before:
			return False
		old_row = next((item for item in before.items if item.name == row.name), None)
		if not old_row:
			return False
		fields = (
			"warehouse",
			"from_warehouse",
			"schedule_date",
			"qty",
			"material_request_type",
			"supply_mode",
			"customer",
			"supplier",
			"commitment_type",
			"action",
			"status",
			"skip_reason",
		)
		return any(row.get(fieldname) != old_row.get(fieldname) for fieldname in fields)
