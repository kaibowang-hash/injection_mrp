from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from injection_mrp.services import stock_buffer


class MRPStockBuffer(Document):
	def validate(self):
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company")
		if not self.stock_uom and self.item_code:
			self.stock_uom = frappe.db.get_value("Item", self.item_code, "stock_uom")
		if self.buffer_profile:
			profile = frappe.db.get_value(
				"MRP Buffer Profile",
				self.buffer_profile,
				["lead_time_factor", "variability_factor", "default_order_cycle_days"],
				as_dict=True,
			)
			if profile:
				self.lead_time_factor = flt(profile.lead_time_factor) or self.lead_time_factor
				self.variability_factor = flt(profile.variability_factor)
				self.minimum_order_cycle_days = flt(profile.default_order_cycle_days)
		if not flt(self.lead_time_factor):
			self.lead_time_factor = 1
		if not cint(self.horizon_past_days):
			self.horizon_past_days = 90
		if not cint(self.horizon_future_days):
			self.horizon_future_days = 90
			if self.adu_calculation_method == "Blended" and not flt(self.factor_past) and not flt(self.factor_future):
				self.factor_past = 0.5
				self.factor_future = 0.5
			if flt(self.dlt_days) < 0:
				frappe.throw(_("DLT Days cannot be negative."))
			if flt(self.lead_time_factor) < 0 or flt(self.variability_factor) < 0:
				frappe.throw(_("Lead Time Factor and Variability Factor cannot be negative."))
			if flt(self.minimum_order_cycle_days) < 0:
				frappe.throw(_("Minimum Order Cycle Days cannot be negative."))
			if flt(self.fixed_adu) < 0:
				frappe.throw(_("Fixed ADU cannot be negative."))
			if flt(self.factor_past) < 0 or flt(self.factor_future) < 0:
				frappe.throw(_("ADU blend factors cannot be negative."))
			if flt(self.min_order_qty) < 0 or flt(self.order_multiple_qty) < 0:
				frappe.throw(_("Minimum order quantity and order multiple cannot be negative."))
		stock_buffer.validate_buffer_uniqueness(self)
		if not self.flags.get("ignore_mrp_buffer_refresh"):
			state = stock_buffer.calculate_buffer_state(self)
			for key, value in state.items():
				if key in {"name", "company", "item_code", "item_name", "stock_uom", "warehouse"}:
					continue
				self.set(key, value)

	def on_update(self):
		stock_buffer.sync_default_buffer_to_item(self)

	@frappe.whitelist()
	def refresh_buffer(self):
		self.check_permission("write")
		return stock_buffer.refresh_buffer(self.name, persist=True)
