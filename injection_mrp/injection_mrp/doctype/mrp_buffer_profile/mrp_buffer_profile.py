from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class MRPBufferProfile(Document):
	def validate(self):
		if not flt(self.lead_time_factor):
			self.lead_time_factor = 1
		if flt(self.lead_time_factor) < 0:
			frappe.throw("Lead Time Factor cannot be negative.")
		if flt(self.variability_factor) < 0:
			frappe.throw("Variability Factor cannot be negative.")
		if flt(self.default_order_cycle_days) < 0:
			frappe.throw("Default Order Cycle Days cannot be negative.")
