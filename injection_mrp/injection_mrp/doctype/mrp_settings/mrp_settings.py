import frappe
from frappe import _
from frappe.model.document import Document


class MRPSettings(Document):
	def validate(self):
		seen = set()
		for row in self.get("warehouse_defaults") or []:
			if not row.supply_mode:
				continue
			if row.supply_mode in seen:
				frappe.throw(_("Duplicate warehouse default for supply mode {0}.").format(row.supply_mode))
			seen.add(row.supply_mode)
