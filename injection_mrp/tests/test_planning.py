import unittest
from datetime import datetime

import frappe

from injection_mrp.services import planning
from injection_mrp.services import stock_buffer


class TestPlanningHelpers(unittest.TestCase):
	def setUp(self):
		self._translate = planning._
		self._today = planning.today
		planning._ = lambda value: value
		planning.today = lambda: "2026-04-27"

	def tearDown(self):
		planning._ = self._translate
		planning.today = self._today

	def test_material_need_and_order_dates_use_staging_and_lead_time(self):
		settings = frappe._dict(
			{
				"material_staging_days": 7,
				"use_material_need_date_for_pegging": 1,
			}
		)

		material_need_date = planning._get_material_need_date("2026-07-30", settings)
		suggested_order_date = planning._get_suggested_order_date(material_need_date, 60)

		self.assertEqual(str(material_need_date), "2026-07-23")
		self.assertEqual(str(suggested_order_date), "2026-05-24")

	def test_supply_timing_warnings(self):
		settings = frappe._dict(
			{
				"early_supply_warning_days": 7,
				"late_supply_tolerance_days": 0,
			}
		)

		late = planning._classify_supply_timing("Purchase Order", "2026-07-24", "2026-07-23", settings)
		early = planning._classify_supply_timing("Purchase Order", "2026-07-10", "2026-07-23", settings)
		buffer = planning._classify_supply_timing("Purchase Order", "2026-07-18", "2026-07-23", settings)

		self.assertEqual(late["category"], "Late Supply")
		self.assertEqual(late["action"], "Expedite")
		self.assertEqual(early["category"], "Early Supply")
		self.assertEqual(early["action"], "Delay")
		self.assertNotIn("category", buffer)

	def test_planned_supply_marks_past_due_and_missing_lead_time(self):
		candidate = planning.RequirementCandidate(
			demand_snapshot="TEST-DEMAND",
			demand_item_code="FG-001",
			item_code="RM-001",
			item_name="Raw Material",
			uom="Kg",
			warehouse="Stores",
			required_date="2026-04-30",
			gross_qty=100,
			scrap_qty=0,
			bom=None,
			bom_item=None,
			bom_level=1,
			requirement_type="Raw Material",
		)

		allocation = planning._make_planned_supply_allocation(
			run=None,
			settings=frappe._dict({}),
			candidate=candidate,
			qty=100,
			material_need_date="2000-01-08",
			suggested_order_date="2000-01-01",
			lead_time=0,
			line_warnings=[
				{
					"category": "Missing Lead Time",
					"level": "Warning",
					"reason": "Missing lead time",
				}
			],
		)

		self.assertEqual(allocation["supply_type"], "Planned Supply")
		self.assertEqual(allocation["adjustment_action"], "Create Material Request")
		self.assertIn("Missing Lead Time", allocation["warning_category"])
		self.assertIn("Past Due Order", allocation["warning_category"])

	def test_rolling_buckets_use_daily_then_weekly(self):
		buckets = planning._make_rolling_buckets("2026-04-01", "2026-06-10", 60)

		self.assertEqual(buckets[0]["type"], "Daily")
		self.assertEqual(str(buckets[0]["start"]), "2026-04-01")
		self.assertEqual(str(buckets[60]["start"]), "2026-05-31")
		self.assertEqual(buckets[60]["type"], "Daily")
		self.assertEqual(buckets[61]["type"], "Weekly")
		self.assertEqual(str(buckets[61]["start"]), "2026-06-01")
		self.assertEqual(str(buckets[-1]["end"]), "2026-06-10")

	def test_supply_mode_mapping_includes_customer_provided(self):
		settings = frappe._dict({"default_material_request_type": "Purchase"})

		self.assertEqual(planning._supply_mode_from_mr_type("Customer Provided"), "Customer Provided")
		self.assertEqual(planning._mr_type_from_supply_mode("Customer Provided", settings), "Customer Provided")
		self.assertEqual(planning._mr_type_from_supply_mode("Supplier Supplied", settings), "Purchase")

	def test_supply_mode_warehouse_defaults_are_used_before_global_defaults(self):
		settings = frappe._dict(
			{
				"default_material_request_type": "Purchase",
				"default_source_warehouse": "Global Source",
				"default_target_warehouse": "Global Target",
				"warehouse_defaults": {
					"Purchase": frappe._dict({"target_warehouse": "Raw Stores"}),
					"Material Transfer": frappe._dict({"source_warehouse": "Transfer Source", "target_warehouse": "Transfer Target"}),
				},
			}
		)

		purchase = planning._route_dict("Purchase", settings)
		transfer = planning._route_dict("Material Transfer", settings)
		manufacture = planning._route_dict("Manufacture", settings)

		self.assertEqual(purchase.target_warehouse, "Raw Stores")
		self.assertIsNone(purchase.source_warehouse)
		self.assertEqual(transfer.source_warehouse, "Transfer Source")
		self.assertEqual(transfer.target_warehouse, "Transfer Target")
		self.assertIsNone(manufacture.target_warehouse)

	def test_supply_rule_blank_warehouse_falls_back_to_supply_mode_default(self):
		settings = frappe._dict(
			{
				"default_material_request_type": "Purchase",
				"default_source_warehouse": "Global Source",
				"default_target_warehouse": "Global Target",
				"warehouse_defaults": {"Purchase": frappe._dict({"target_warehouse": "Raw Stores"})},
			}
		)

		blank_rule = frappe._dict({"name": "MRP-SR-001", "supply_mode": "Purchase", "material_request_type": "Purchase"})
		explicit_rule = frappe._dict(
			{
				"name": "MRP-SR-002",
				"supply_mode": "Purchase",
				"material_request_type": "Purchase",
				"target_warehouse": "Rule Stores",
			}
		)

		self.assertEqual(planning._route_from_rule(blank_rule, settings, frappe._dict({})).target_warehouse, "Raw Stores")
		self.assertEqual(planning._route_from_rule(explicit_rule, settings, frappe._dict({})).target_warehouse, "Rule Stores")

	def test_candidate_warehouse_priority_keeps_item_before_global_default(self):
		original_get_item_values = planning._get_item_values
		original_resolve = planning._resolve_supply_route
		original_requirement_type = planning._get_requirement_type
		planning._get_item_values = lambda item_code: frappe._dict(
			{"item_name": "Raw Material", "stock_uom": "Kg", "default_warehouse": "Item Stores"}
		)
		planning._resolve_supply_route = lambda item_code, company, customer, warehouse, settings: frappe._dict({"supply_mode": "Purchase"})
		planning._get_requirement_type = lambda item_code: "Raw Material"
		try:
			candidate = planning._candidate_from_item(
				frappe._dict({"name": "MRP-RUN-001", "company": "Test Company"}),
				frappe._dict({"default_target_warehouse": "Global Target"}),
				frappe._dict({"name": "SNAP-001", "item_code": "FG-001", "required_date": "2026-05-01", "warehouse": None}),
				"RM-001",
				1,
				None,
				None,
				1,
				"FG-001 > RM-001",
			)
		finally:
			planning._get_item_values = original_get_item_values
			planning._resolve_supply_route = original_resolve
			planning._get_requirement_type = original_requirement_type

		self.assertEqual(candidate.warehouse, "Item Stores")

	def test_purchase_order_qty_respects_moq_and_multiple(self):
		candidate = planning.RequirementCandidate(
			demand_snapshot="TEST-DEMAND",
			demand_item_code="FG-001",
			item_code="RM-001",
			item_name="Raw Material",
			uom="Kg",
			warehouse="Stores",
			required_date="2026-04-30",
			gross_qty=100,
			scrap_qty=0,
			bom=None,
			bom_item=None,
			bom_level=1,
			requirement_type="Raw Material",
			supply_mode="Purchase",
			min_order_qty=500,
			order_multiple_qty=25,
		)

		self.assertEqual(planning._get_planned_order_qty(120, candidate), 500)
		self.assertEqual(planning._get_planned_order_qty(512, candidate), 525)
		self.assertEqual(planning._get_planned_order_qty(500, candidate), 500)

	def test_buffer_zone_and_order_qty_calculation(self):
		zones = stock_buffer.calculate_zones(
			adu=2,
			dlt_days=10,
			lead_time_factor=0.5,
			variability_factor=0.5,
			order_cycle_days=8,
			min_order_qty=25,
		)

		self.assertEqual(zones.red_zone_qty, 15)
		self.assertEqual(zones.yellow_zone_qty, 20)
		self.assertEqual(zones.green_zone_qty, 25)
		self.assertEqual(zones.top_of_red, 15)
		self.assertEqual(zones.top_of_yellow, 35)
		self.assertEqual(zones.top_of_green, 60)
		self.assertEqual(stock_buffer.classify_priority(14, zones.top_of_red, zones.top_of_yellow), "Red")
		self.assertEqual(stock_buffer.classify_priority(20, zones.top_of_red, zones.top_of_yellow), "Yellow")
		self.assertEqual(stock_buffer.classify_priority(40, zones.top_of_red, zones.top_of_yellow), "Green")
		self.assertEqual(stock_buffer.adjust_order_qty(26, 12, 10), 30)

	def test_candidate_lead_time_uses_buffer_before_supplier_and_item(self):
		original_item_lead_time = planning._get_item_lead_time
		planning._get_item_lead_time = lambda item_code: 99
		try:
			candidate = planning.RequirementCandidate(
				demand_snapshot="TEST-DEMAND",
				demand_item_code="FG-001",
				item_code="RM-001",
				item_name="Raw Material",
				uom="Kg",
				warehouse="Stores",
				required_date="2026-04-30",
				gross_qty=100,
				scrap_qty=0,
				bom=None,
				bom_item=None,
				bom_level=1,
				requirement_type="Raw Material",
				supplier_lead_time_days=12,
				buffer_lead_time_days=18,
			)

			self.assertEqual(planning._get_candidate_lead_time(candidate), 18)
			candidate.buffer_lead_time_days = 0
			self.assertEqual(planning._get_candidate_lead_time(candidate), 12)
			candidate.supplier_lead_time_days = 0
			self.assertEqual(planning._get_candidate_lead_time(candidate), 99)
		finally:
			planning._get_item_lead_time = original_item_lead_time

	def test_safety_stock_demands_use_stock_buffer_top_up_without_persisting(self):
		original_collect = planning.stock_buffer.collect_buffer_top_up_demands
		original_item_safety = planning._collect_item_safety_stock_demands
		calls = []
		planning.stock_buffer.collect_buffer_top_up_demands = lambda run, persist=False: calls.append(persist) or [
			frappe._dict(
				{
					"name": "MRP-BUF-001",
					"item_code": "RM-001",
					"warehouse": "Stores",
					"recommended_qty": 75,
					"planning_priority": "Red",
					"net_flow_position_percent": 22.5,
				}
			)
		]
		planning._collect_item_safety_stock_demands = lambda run, skip_buffered_items=True: []
		try:
			run = frappe._dict({"name": "MRP-RUN-001", "company": "Test Company", "horizon_start": "2026-04-27"})
			demands = planning._collect_safety_stock_demands(
				run,
				frappe._dict({"use_stock_buffer_for_safety_stock": 1}),
			)
		finally:
			planning.stock_buffer.collect_buffer_top_up_demands = original_collect
			planning._collect_item_safety_stock_demands = original_item_safety

		self.assertEqual(calls, [False])
		self.assertEqual(len(demands), 1)
		self.assertEqual(demands[0].demand_type, "Stock Buffer")
		self.assertEqual(demands[0].qty, 75)
		self.assertEqual(demands[0].source_doctype, "MRP Stock Buffer")

	def test_item_safety_stock_falls_back_when_no_stock_buffer_exists(self):
		original_doctype_exists = planning._doctype_exists
		original_has_field = planning._has_field
		original_get_all = frappe.get_all
		original_available_qty = planning._get_available_qty
		original_buffer_name = planning.stock_buffer.get_buffer_name_for_item
		try:
			planning._doctype_exists = lambda doctype: doctype == "Item"
			planning._has_field = lambda doctype, fieldname: doctype == "Item" and fieldname in {"safety_stock", "default_warehouse"}
			frappe.get_all = lambda doctype, filters=None, fields=None, limit_page_length=None: [
				frappe._dict(
					{
						"name": "RM-001",
						"item_name": "Raw Material",
						"stock_uom": "Kg",
						"safety_stock": 100,
						"default_warehouse": "Stores",
					}
				)
			]
			planning._get_available_qty = lambda item_code, company, warehouse: 25
			planning.stock_buffer.get_buffer_name_for_item = lambda item_code, company, warehouse: None

			run = frappe._dict({"company": "Test Company", "horizon_start": "2026-04-27", "item_code": None, "warehouse": None})
			demands = planning._collect_item_safety_stock_demands(run, skip_buffered_items=True)

			planning.stock_buffer.get_buffer_name_for_item = lambda item_code, company, warehouse: "MRP-BUF-001"
			skipped = planning._collect_item_safety_stock_demands(run, skip_buffered_items=True)
		finally:
			planning._doctype_exists = original_doctype_exists
			planning._has_field = original_has_field
			frappe.get_all = original_get_all
			planning._get_available_qty = original_available_qty
			planning.stock_buffer.get_buffer_name_for_item = original_buffer_name

		self.assertEqual(len(demands), 1)
		self.assertEqual(demands[0].demand_type, "Safety Stock")
		self.assertEqual(demands[0].qty, 75)
		self.assertEqual(demands[0].warehouse, "Stores")
		self.assertEqual(skipped, [])

	def test_buffer_name_does_not_fall_back_to_default_when_warehouse_is_set(self):
		original_doctype_exists = stock_buffer._doctype_exists
		original_db = stock_buffer.frappe.db
		calls = []

		def fake_get_value(doctype, filters, fieldname=None, *args, **kwargs):
			calls.append(dict(filters))
			if filters.get("warehouse"):
				return None
			if filters.get("is_default_for_item"):
				return "MRP-BUF-DEFAULT"
			return None

		try:
			stock_buffer._doctype_exists = lambda doctype: doctype == "MRP Stock Buffer"
			stock_buffer.frappe.db = frappe._dict({"get_value": fake_get_value})

			self.assertIsNone(stock_buffer.get_buffer_name_for_item("RM-001", "Test Company", "Stores"))
			self.assertEqual(stock_buffer.get_buffer_name_for_item("RM-001", "Test Company", None), "MRP-BUF-DEFAULT")
		finally:
			stock_buffer._doctype_exists = original_doctype_exists
			stock_buffer.frappe.db = original_db

		self.assertEqual(calls[0]["warehouse"], "Stores")
		self.assertNotIn("is_default_for_item", calls[0])

	def test_default_buffer_sync_does_not_write_standard_item_lead_time_by_default(self):
		original_doctype_exists = stock_buffer._doctype_exists
		original_has_field = stock_buffer._has_field
		original_sync_enabled = stock_buffer._sync_standard_item_lead_time_enabled
		original_db = stock_buffer.frappe.db
		calls = []
		buffer = frappe._dict(
			{
				"name": "MRP-BUF-001",
				"active": 1,
				"is_default_for_item": 1,
				"item_code": "RM-001",
				"dlt_days": 18,
			}
		)

		try:
			stock_buffer._doctype_exists = lambda doctype: doctype == "Item"
			stock_buffer._has_field = lambda doctype, fieldname: doctype == "Item" and fieldname in {
				"custom_mrp_default_stock_buffer",
				"custom_mrp_lead_time_days",
				"lead_time_days",
			}
			stock_buffer.frappe.db = frappe._dict({"set_value": lambda doctype, name, values: calls.append((doctype, name, dict(values)))})

			stock_buffer._sync_standard_item_lead_time_enabled = lambda: False
			stock_buffer.sync_default_buffer_to_item(buffer)
			stock_buffer._sync_standard_item_lead_time_enabled = lambda: True
			stock_buffer.sync_default_buffer_to_item(buffer)
		finally:
			stock_buffer._doctype_exists = original_doctype_exists
			stock_buffer._has_field = original_has_field
			stock_buffer._sync_standard_item_lead_time_enabled = original_sync_enabled
			stock_buffer.frappe.db = original_db

		self.assertNotIn("lead_time_days", calls[0][2])
		self.assertEqual(calls[0][2]["custom_mrp_lead_time_days"], 18)
		self.assertEqual(calls[1][2]["lead_time_days"], 18)

	def test_stock_buffer_console_statuses_are_foolproof(self):
		original_has_field = stock_buffer._has_field
		original_now = stock_buffer.now_datetime
		try:
			stock_buffer._has_field = lambda doctype, fieldname: doctype == "Item" and fieldname == "is_stock_item"
			item = frappe._dict({"custom_mrp_use_stock_buffer": 1, "is_stock_item": 1})
			self.assertEqual(stock_buffer._get_console_status(item, None, [], 0), "Missing Warehouse")
			self.assertEqual(stock_buffer._get_console_status(item, "Stores", [], 0), "Missing Buffer")
			self.assertEqual(stock_buffer._get_console_status(item, "Stores", [], 1), "Conflict")
			self.assertEqual(
				stock_buffer._get_console_status(item, "Stores", [frappe._dict({"dlt_days": 0})], 0),
				"Missing DLT",
			)
			self.assertEqual(
				stock_buffer._get_console_status(
					item,
					"Stores",
					[frappe._dict({"dlt_days": 0, "suggested_dlt_days": 12})],
					0,
				),
				"Review DLT",
			)
			self.assertEqual(
				stock_buffer._get_console_status(
					item,
					"Stores",
					[frappe._dict({"dlt_days": 10, "is_default_for_item": 0})],
					1,
				),
				"Conflict",
			)
			stock_buffer.now_datetime = lambda: datetime(2026, 5, 2, 12, 0, 0)
			self.assertEqual(
				stock_buffer._get_console_status(
					item,
					"Stores",
					[frappe._dict({"dlt_days": 10, "last_calculated_on": datetime(2026, 5, 1, 0, 0, 0)})],
					0,
				),
				"Needs Refresh",
			)
			self.assertEqual(
				stock_buffer._get_console_status(
					item,
					"Stores",
					[
						frappe._dict(
							{
								"dlt_days": 10,
								"suggested_dlt_days": 14,
								"last_calculated_on": datetime(2026, 5, 2, 11, 0, 0),
							}
						)
					],
					0,
				),
				"DLT Mismatch",
			)
			self.assertEqual(
				stock_buffer._get_console_status(
					item,
					"Stores",
					[
						frappe._dict(
							{
								"dlt_days": 10,
								"min_order_qty": 0,
								"suggested_min_order_qty": 500,
								"last_calculated_on": datetime(2026, 5, 2, 11, 0, 0),
							}
						)
					],
					0,
				),
				"Procurement Mismatch",
			)
			self.assertEqual(
				stock_buffer._get_console_status(
					item,
					"Stores",
					[frappe._dict({"dlt_days": 10, "last_calculated_on": datetime(2026, 5, 2, 11, 0, 0)})],
					0,
				),
				"Active",
			)
		finally:
			stock_buffer._has_field = original_has_field
			stock_buffer.now_datetime = original_now

	def test_stock_buffer_group_defaults_apply_raw_material_and_packaging(self):
		original_has_field = stock_buffer._has_field
		original_get_all = stock_buffer.frappe.get_all
		original_db = vars(stock_buffer.frappe)["db"]
		original_group_default = stock_buffer._item_group_defaults_to_stock_buffer
		calls = []
		try:
			stock_buffer._has_field = lambda doctype, fieldname: (
				(doctype == "Item" and fieldname in {"custom_mrp_use_stock_buffer", "is_stock_item"})
			)
			stock_buffer.frappe.get_all = lambda doctype, filters=None, fields=None, limit_page_length=None: [
				frappe._dict({"name": "RM-001", "item_group": "Plastic Resin", "is_stock_item": 1}),
				frappe._dict({"name": "PK-001", "item_group": "Packaging", "is_stock_item": 1}),
				frappe._dict({"name": "FG-001", "item_group": "Plastic Part", "is_stock_item": 1}),
				frappe._dict({"name": "SRV-001", "item_group": "Service", "is_stock_item": 0}),
			]
			stock_buffer._item_group_defaults_to_stock_buffer = lambda item_group: item_group in {
				"Plastic Resin",
				"Packaging",
			}
			stock_buffer.frappe.db = frappe._dict(
				{"set_value": lambda doctype, name, fieldname, value, **kwargs: calls.append((name, value))}
			)

			result = stock_buffer.apply_stock_buffer_item_group_defaults()
		finally:
			stock_buffer._has_field = original_has_field
			stock_buffer.frappe.get_all = original_get_all
			stock_buffer.frappe.db = original_db
			stock_buffer._item_group_defaults_to_stock_buffer = original_group_default

		self.assertEqual(result["enabled"], 2)
		self.assertEqual(result["disabled"], 2)
		self.assertEqual(calls, [("RM-001", 1), ("PK-001", 1), ("FG-001", 0), ("SRV-001", 0)])

	def test_item_buffer_warehouse_uses_company_item_default_only(self):
		original_doctype_exists = stock_buffer._doctype_exists
		original_has_field = stock_buffer._has_field
		original_db = vars(stock_buffer.frappe)["db"]
		calls = []

		def fake_get_value(doctype, filters, fieldname=None, **kwargs):
			calls.append(dict(filters))
			if filters.get("company"):
				return None
			return "Fallback Stores"

		try:
			stock_buffer._doctype_exists = lambda doctype: doctype == "Item Default"
			stock_buffer._has_field = lambda doctype, fieldname: doctype == "Item Default" and fieldname == "company"
			stock_buffer.frappe.db = frappe._dict({"get_value": fake_get_value})

			warehouse = stock_buffer._get_item_buffer_warehouse(frappe._dict({"name": "RM-001"}), "Test Company")
		finally:
			stock_buffer._doctype_exists = original_doctype_exists
			stock_buffer._has_field = original_has_field
			stock_buffer.frappe.db = original_db

		self.assertIsNone(warehouse)
		self.assertEqual(calls[0]["company"], "Test Company")
		self.assertEqual(len(calls), 1)

	def test_stock_buffer_procurement_suggestions_do_not_override_current_fields(self):
		original_coerce = stock_buffer._coerce_item_row
		original_default_supplier = stock_buffer._get_item_default_supplier
		original_item_supplier = stock_buffer._get_item_supplier
		original_rule = stock_buffer._get_supply_rule_suggestion
		original_quotation = stock_buffer._get_supplier_quotation_suggestion
		original_item_price = stock_buffer._get_item_price_suggestion
		original_po_history = stock_buffer._get_purchase_order_history_suggestion
		original_today = stock_buffer.today
		original_now = stock_buffer.now_datetime
		original_translate = stock_buffer._
		try:
			stock_buffer._ = lambda value: value
			stock_buffer.today = lambda: "2026-05-07"
			stock_buffer.now_datetime = lambda: datetime(2026, 5, 7, 10, 0, 0)
			stock_buffer._coerce_item_row = lambda item_code: frappe._dict(
				{"name": item_code, "item_group": "Raw-material", "min_order_qty": 300}
			)
			stock_buffer._get_item_default_supplier = lambda item_code, company=None: "SUP-001"
			stock_buffer._get_item_supplier = lambda item_code: None
			stock_buffer._get_supply_rule_suggestion = lambda item, company, warehouse: frappe._dict(
				{
					"name": "MRP-SR-001",
					"item_code": item.name,
					"supplier": "SUP-001",
					"supplier_lead_time_days": 0,
					"min_order_qty": 500,
					"order_multiple_qty": 25,
				}
			)
			stock_buffer._get_supplier_quotation_suggestion = lambda item_code, supplier=None, as_of_date=None: frappe._dict(
				{"parent": "SQ-001", "supplier": "SUP-001", "lead_time_days": 14}
			)
			stock_buffer._get_item_price_suggestion = lambda item_code, supplier=None, as_of_date=None: frappe._dict(
				{"name": "IP-001", "supplier": "SUP-001", "lead_time_days": 7, "packing_unit": 50}
			)
			stock_buffer._get_purchase_order_history_suggestion = lambda item_code, supplier=None: frappe._dict(
				{"lead_time_days": 21, "sample_size": 8}
			)

			suggestion = stock_buffer.calculate_procurement_suggestions(
				frappe._dict({"item_code": "RM-001", "company": "Test Company", "warehouse": "Stores", "dlt_days": 10})
			)
		finally:
			stock_buffer._coerce_item_row = original_coerce
			stock_buffer._get_item_default_supplier = original_default_supplier
			stock_buffer._get_item_supplier = original_item_supplier
			stock_buffer._get_supply_rule_suggestion = original_rule
			stock_buffer._get_supplier_quotation_suggestion = original_quotation
			stock_buffer._get_item_price_suggestion = original_item_price
			stock_buffer._get_purchase_order_history_suggestion = original_po_history
			stock_buffer.today = original_today
			stock_buffer.now_datetime = original_now
			stock_buffer._ = original_translate

		self.assertEqual(suggestion.suggested_dlt_days, 14)
		self.assertIn("Supplier Quotation", suggestion.suggested_dlt_source)
		self.assertEqual(suggestion.suggested_dlt_confidence, "High")
		self.assertEqual(suggestion.suggested_min_order_qty, 500)
		self.assertEqual(suggestion.suggested_order_multiple_qty, 25)

	def test_non_persistent_buffer_refresh_skips_procurement_suggestions(self):
		original_on_hand = stock_buffer._get_on_hand_qty
		original_incoming = stock_buffer._get_incoming_dlt_qty
		original_demand = stock_buffer._get_qualified_demand_qty
		original_supports = stock_buffer._buffer_supports_suggestions
		original_suggestions = stock_buffer.calculate_procurement_suggestions
		original_today = stock_buffer.today
		original_now = stock_buffer.now_datetime
		original_translate = stock_buffer._
		calls = []
		try:
			stock_buffer._ = lambda value: value
			stock_buffer.today = lambda: "2026-05-07"
			stock_buffer.now_datetime = lambda: datetime(2026, 5, 7, 10, 0, 0)
			stock_buffer._get_on_hand_qty = lambda item_code, company, warehouse: 0
			stock_buffer._get_incoming_dlt_qty = lambda item_code, company, warehouse, cutoff_date: 0
			stock_buffer._get_qualified_demand_qty = lambda item_code, company, warehouse, start_date, end_date: 0
			stock_buffer._buffer_supports_suggestions = lambda: True
			stock_buffer.calculate_procurement_suggestions = lambda doc: calls.append(doc.item_code) or frappe._dict(
				{"suggested_dlt_days": 14}
			)
			buffer = frappe._dict(
				{
					"name": "MRP-BUF-001",
					"company": "Test Company",
					"item_code": "RM-001",
					"warehouse": "Stores",
					"dlt_days": 10,
					"lead_time_factor": 1,
					"variability_factor": 0,
					"minimum_order_cycle_days": 0,
					"fixed_adu": 2,
					"adu_calculation_method": "Fixed",
				}
			)

			transient_state = stock_buffer.refresh_buffer(buffer, persist=False)
			persistent_state = stock_buffer.refresh_buffer(buffer, persist=True)
		finally:
			stock_buffer._get_on_hand_qty = original_on_hand
			stock_buffer._get_incoming_dlt_qty = original_incoming
			stock_buffer._get_qualified_demand_qty = original_demand
			stock_buffer._buffer_supports_suggestions = original_supports
			stock_buffer.calculate_procurement_suggestions = original_suggestions
			stock_buffer.today = original_today
			stock_buffer.now_datetime = original_now
			stock_buffer._ = original_translate

		self.assertNotIn("suggested_dlt_days", transient_state)
		self.assertEqual(persistent_state.suggested_dlt_days, 14)
		self.assertEqual(calls, ["RM-001"])

	def test_order_qty_rounding_only_applies_to_purchase(self):
		candidate = planning.RequirementCandidate(
			demand_snapshot="TEST-DEMAND",
			demand_item_code="FG-001",
			item_code="SFG-001",
			item_name="Semi Finished Good",
			uom="Nos",
			warehouse="WIP",
			required_date="2026-04-30",
			gross_qty=100,
			scrap_qty=0,
			bom=None,
			bom_item=None,
			bom_level=1,
			requirement_type="Sub Assembly",
			supply_mode="Manufacture",
			min_order_qty=500,
			order_multiple_qty=25,
		)

		self.assertEqual(planning._get_planned_order_qty(120, candidate), 120)

	def test_sales_order_remaining_qty_uses_stock_uom(self):
		row = frappe._dict(
			{
				"qty": 50,
				"stock_qty": 100,
				"delivered_qty": 20,
				"conversion_factor": 2,
			}
		)

		self.assertEqual(planning._sales_order_remaining_stock_qty(row), 60)

	def test_open_supply_remaining_qty_uses_stock_uom(self):
		material_request_row = frappe._dict(
			{
				"qty": 10,
				"stock_qty": 50,
				"ordered_qty": 20,
				"received_qty": 15,
				"conversion_factor": 5,
			}
		)
		purchase_order_row = frappe._dict(
			{
				"qty": 10,
				"received_qty": 2,
				"conversion_factor": 5,
			}
		)

		self.assertEqual(planning._material_request_remaining_stock_qty(material_request_row), 30)
		self.assertEqual(planning._purchase_order_remaining_stock_qty(purchase_order_row), 40)

	def test_forecast_is_consumed_by_matching_sales_order(self):
		settings = frappe._dict({"forecast_consumption_window_days": 30})
		forecast = planning.DemandRow(
			demand_type="Forecast",
			item_code="FG-001",
			qty=100,
			required_date="2026-05-10",
			company="Test Company",
			warehouse="Stores",
			customer="Customer A",
			source_doctype="Customer Delivery Schedule",
			source_name="CDS-001",
			source_row="CDS-ITEM-001",
		)
		sales_order = planning.DemandRow(
			demand_type="Sales Order",
			item_code="FG-001",
			qty=40,
			required_date="2026-05-20",
			company="Test Company",
			warehouse="Stores",
			customer="Customer A",
			source_doctype="Sales Order",
			source_name="SO-001",
			source_row="SO-ITEM-001",
		)

		remaining_forecasts = planning._consume_forecast_with_sales_orders([forecast], [sales_order], settings)

		self.assertEqual(len(remaining_forecasts), 1)
		self.assertEqual(remaining_forecasts[0].qty, 60)
		self.assertEqual(sales_order.qty, 40)

	def test_forecast_consumption_respects_customer_and_window(self):
		settings = frappe._dict({"forecast_consumption_window_days": 7})
		forecast = planning.DemandRow(
			demand_type="Forecast",
			item_code="FG-001",
			qty=100,
			required_date="2026-05-10",
			company="Test Company",
			customer="Customer A",
		)
		sales_order = planning.DemandRow(
			demand_type="Sales Order",
			item_code="FG-001",
			qty=40,
			required_date="2026-06-10",
			company="Test Company",
			customer="Customer B",
		)

		remaining_forecasts = planning._consume_forecast_with_sales_orders([forecast], [sales_order], settings)

		self.assertEqual(remaining_forecasts[0].qty, 100)

	def test_aps_demand_uses_scheduled_qty_only(self):
		row = frappe._dict({"planned_qty": 100, "scheduled_qty": 35})

		self.assertEqual(planning._get_aps_demand_qty(row), 35)

	def test_excess_prebuy_gets_specific_review_action(self):
		run = frappe._dict({"run_type": "Firm APS"})
		supply = planning.SupplyRecord(
			supply_type="Prebuy",
			item_code="RM-001",
			company="Test Company",
			warehouse="Stores",
			original_qty=100,
			remaining_qty=30,
			supply_date="2026-05-01",
			expected_arrival_date="2026-05-01",
			priority=50,
			source_doctype="Material Request",
			source_name="MAT-MR-001",
			source_row="MRI-001",
			commitment_type="Prebuy",
		)

		self.assertEqual(planning._get_excess_supply_category(run, supply), "Excess Prebuy")
		self.assertEqual(planning._get_excess_supply_action(run, supply), "Review Excess Prebuy")

	def test_invalid_demand_item_is_filtered_to_exception(self):
		original_resolve_item_name = planning._resolve_item_name
		original_insert_invalid = planning._insert_invalid_demand_item_exception
		captured = []
		planning._resolve_item_name = lambda item_code: item_code if item_code == "VALID-ITEM" else None
		planning._insert_invalid_demand_item_exception = lambda run, demand: captured.append(demand.item_code)
		try:
			run = frappe._dict({"name": "MRP-RUN-TEST", "company": "Test Company"})
			demands = [
				planning.DemandRow(
					demand_type="Sales Order",
					item_code="VALID-ITEM",
					qty=1,
					required_date="2026-05-01",
					company="Test Company",
				),
				planning.DemandRow(
					demand_type="Sales Order",
					item_code="PICO AD AIRPATH BODY RING",
					qty=1,
					required_date="2026-05-01",
					company="Test Company",
				),
			]

			valid = planning._filter_valid_demands(run, demands)
		finally:
			planning._resolve_item_name = original_resolve_item_name
			planning._insert_invalid_demand_item_exception = original_insert_invalid

		self.assertEqual([row.item_code for row in valid], ["VALID-ITEM"])
		self.assertEqual(captured, ["PICO AD AIRPATH BODY RING"])

	def test_demand_item_code_is_resolved_to_item_name(self):
		original_resolve_item_name = planning._resolve_item_name
		original_insert_invalid = planning._insert_invalid_demand_item_exception
		captured = []
		planning._resolve_item_name = lambda item_code: {
			"ITEM-CODE-001": "ITEM-DOC-001",
			"ITEM-DOC-002": "ITEM-DOC-002",
		}.get(item_code)
		planning._insert_invalid_demand_item_exception = lambda run, demand: captured.append(demand.item_code)
		try:
			run = frappe._dict({"name": "MRP-RUN-TEST", "company": "Test Company"})
			demands = [
				planning.DemandRow(
					demand_type="Forecast",
					item_code="ITEM-CODE-001",
					qty=1,
					required_date="2026-05-01",
					company="Test Company",
					notes="Original note",
				),
				planning.DemandRow(
					demand_type="Forecast",
					item_code="ITEM-DOC-002",
					qty=1,
					required_date="2026-05-01",
					company="Test Company",
				),
			]

			valid = planning._filter_valid_demands(run, demands)
		finally:
			planning._resolve_item_name = original_resolve_item_name
			planning._insert_invalid_demand_item_exception = original_insert_invalid

		self.assertEqual([row.item_code for row in valid], ["ITEM-DOC-001", "ITEM-DOC-002"])
		self.assertIn("ITEM-CODE-001", valid[0].notes)
		self.assertEqual(captured, [])

	def test_clear_run_outputs_uses_bulk_deletes_in_dependency_order(self):
		original_doctype_exists = planning._doctype_exists
		original_get_all = planning.frappe.get_all
		original_sql = planning.frappe.db.sql
		original_delete_doc = planning.frappe.delete_doc
		sql_calls = []

		planning._doctype_exists = lambda doctype: True
		planning.frappe.get_all = lambda doctype, **kwargs: []

		def fake_sql(query, values=None, **kwargs):
			sql_calls.append((" ".join(query.split()), values))

		def fake_delete_doc(*args, **kwargs):
			raise AssertionError("MRP output cleanup must use bulk deletes, not delete_doc")

		planning.frappe.db.sql = fake_sql
		planning.frappe.delete_doc = fake_delete_doc
		try:
			planning._clear_run_outputs("MRP-RUN-TEST")
		finally:
			planning._doctype_exists = original_doctype_exists
			planning.frappe.get_all = original_get_all
			planning.frappe.db.sql = original_sql
			planning.frappe.delete_doc = original_delete_doc

		expected_doctypes = [
			"MRP Proposal Batch",
			"MRP Shortage Alert",
			"MRP Rolling Balance Line",
			"MRP Exception Log",
			"MRP Pegging Line",
			"MRP Requirement Line",
			"MRP Demand Snapshot",
		]
		self.assertEqual(len(sql_calls), len(expected_doctypes) + 1)
		self.assertIn("delete item from `tabMRP Proposal Item`", sql_calls[0][0])
		self.assertEqual(sql_calls[0][1], ("MRP-RUN-TEST",))
		for doctype, call in zip(expected_doctypes, sql_calls[1:]):
			self.assertIn(f"delete from `tab{doctype}`", call[0])
			self.assertEqual(call[1], ("MRP-RUN-TEST",))

	def test_clear_run_outputs_rejects_applied_proposal_batch(self):
		original_get_all = planning.frappe.get_all
		planning.frappe.get_all = lambda doctype, **kwargs: ["MRP-PB-APPLIED"]
		try:
			with self.assertRaises(Exception):
				planning._clear_run_outputs("MRP-RUN-TEST")
		finally:
			planning.frappe.get_all = original_get_all

	def test_enqueue_forecast_prebuy_uses_long_queue_and_marks_queued_run(self):
		original_create_mrp_run = planning.create_mrp_run
		original_enqueue = planning.frappe.enqueue
		original_get_value = planning.frappe.db.get_value
		captured = {}

		def fake_create_mrp_run(**kwargs):
			captured["create"] = kwargs
			return "MRP-RUN-QUEUE"

		def fake_enqueue(method, **kwargs):
			captured["enqueue_method"] = method
			captured["enqueue"] = kwargs

		planning.create_mrp_run = fake_create_mrp_run
		planning.frappe.enqueue = fake_enqueue
		planning.frappe.db.get_value = lambda doctype, name, fieldname: "Queued"
		try:
			result = planning.enqueue_forecast_prebuy(company="Test Company", item_code="FG-001")
		finally:
			planning.create_mrp_run = original_create_mrp_run
			planning.frappe.enqueue = original_enqueue
			planning.frappe.db.get_value = original_get_value

		self.assertEqual(captured["create"]["run_type"], "Forecast Prebuy")
		self.assertEqual(captured["create"]["status"], "Queued")
		self.assertEqual(captured["enqueue_method"], "injection_mrp.services.planning.run_mrp")
		self.assertEqual(captured["enqueue"]["queue"], planning.MRP_JOB_QUEUE)
		self.assertEqual(captured["enqueue"]["timeout"], planning.MRP_JOB_TIMEOUT)
		self.assertTrue(captured["enqueue"]["enqueue_after_commit"])
		self.assertTrue(captured["enqueue"]["deduplicate"])
		self.assertEqual(captured["enqueue"]["job_id"], "injection_mrp_run_MRP-RUN-QUEUE")
		self.assertEqual(captured["enqueue"]["mrp_run"], "MRP-RUN-QUEUE")
		self.assertEqual(result, {"mrp_run": "MRP-RUN-QUEUE", "job_id": "injection_mrp_run_MRP-RUN-QUEUE", "status": "Queued"})

	def test_run_mrp_sets_failed_status_when_job_raises(self):
		original_get_settings = planning.get_settings_dict
		original_get_doc = planning.frappe.get_doc
		original_ensure = planning._ensure_run_can_recalculate
		original_prepare = planning._prepare_run_window
		original_set_status = planning._set_run_execution_status
		original_clear_outputs = planning._clear_run_outputs
		original_rollback = planning.frappe.db.rollback
		statuses = []
		rollbacks = []
		run = frappe._dict({"name": "MRP-RUN-FAIL"})
		run.reload = lambda: None

		planning.get_settings_dict = lambda: frappe._dict({})
		planning.frappe.get_doc = lambda doctype, name: run
		planning._ensure_run_can_recalculate = lambda mrp_run: None
		planning._prepare_run_window = lambda run_doc, settings: None
		planning._set_run_execution_status = lambda mrp_run, status, error_message=None: statuses.append((status, error_message))
		planning._clear_run_outputs = lambda mrp_run: (_ for _ in ()).throw(RuntimeError("boom"))
		planning.frappe.db.rollback = lambda: rollbacks.append(True)
		try:
			with self.assertRaises(RuntimeError):
				planning.run_mrp(mrp_run="MRP-RUN-FAIL")
		finally:
			planning.get_settings_dict = original_get_settings
			planning.frappe.get_doc = original_get_doc
			planning._ensure_run_can_recalculate = original_ensure
			planning._prepare_run_window = original_prepare
			planning._set_run_execution_status = original_set_status
			planning._clear_run_outputs = original_clear_outputs
			planning.frappe.db.rollback = original_rollback

		self.assertTrue(rollbacks)
		self.assertEqual(statuses[0], ("Running", None))
		self.assertEqual(statuses[-1][0], "Failed")
		self.assertIn("boom", statuses[-1][1])

	def test_forecast_fence_skips_demands_inside_firm_fence_when_enabled(self):
		run = frappe._dict({"run_type": "Forecast Prebuy", "planning_date": "2026-04-27", "horizon_start": "2026-04-27"})
		settings = frappe._dict({"forecast_skip_firm_fence": 1, "firm_fence_days": 45})
		near = planning.DemandRow("Forecast", "FG-001", 1, "2026-05-15", "Test Company")
		far = planning.DemandRow("Forecast", "FG-002", 1, "2026-06-20", "Test Company")

		result = planning._filter_forecast_fence_demands(run, settings, [near, far])

		self.assertEqual([row.item_code for row in result], ["FG-002"])

	def test_forecast_fence_keeps_existing_behavior_when_disabled(self):
		run = frappe._dict({"run_type": "Forecast Prebuy", "planning_date": "2026-04-27", "horizon_start": "2026-04-27"})
		settings = frappe._dict({"forecast_skip_firm_fence": 0, "firm_fence_days": 45})
		near = planning.DemandRow("Forecast", "FG-001", 1, "2026-05-15", "Test Company")

		result = planning._filter_forecast_fence_demands(run, settings, [near])

		self.assertEqual(result, [near])

	def test_validate_release_blocks_fc_create_mr_with_firm_commitment(self):
		original_find_newer = planning._find_newer_overlapping_ready_batch
		planning._find_newer_overlapping_ready_batch = lambda batch, row: None
		try:
			batch = frappe._dict(
				{
					"name": "MRP-PROP-TEST",
					"company": "Test Company",
					"proposal_type": "Forecast Prebuy",
					"status": "Ready",
					"items": [
						frappe._dict(
							{
								"name": "ROW-001",
								"status": "Pending",
								"action": "Create Material Request",
								"commitment_type": "Firm",
								"qty": 10,
								"item_code": "RM-001",
								"warehouse": "Stores",
							}
						)
					],
				}
			)

			result = planning._validate_proposal_batch_for_release(batch, frappe._dict({}))
		finally:
			planning._find_newer_overlapping_ready_batch = original_find_newer

		self.assertFalse(result["valid"])
		self.assertEqual(result["blocking_issues"][0]["issue_type"], "Commitment Mismatch")

	def test_validate_release_blocks_reduced_current_shortage(self):
		original_find_newer = planning._find_newer_overlapping_ready_batch
		original_requirements = planning._get_requirement_validation_map
		original_get_run = planning._get_validation_run
		original_supply = planning._get_supply_records
		planning._find_newer_overlapping_ready_batch = lambda batch, row: None
		planning._get_requirement_validation_map = lambda names: {
			"REQ-001": frappe._dict(
				{
					"name": "REQ-001",
					"gross_qty": 100,
					"material_need_date": "2026-05-20",
				}
			)
		}
		planning._get_validation_run = lambda mrp_run: frappe._dict({"planning_date": "2026-04-27"})
		planning._get_supply_records = lambda item_code, company, warehouse, settings, run: [
			planning.SupplyRecord("Purchase Order", item_code, company, warehouse, 80, 80, "2026-05-01", "2026-05-01", 80)
		]
		try:
			batch = frappe._dict(
				{
					"name": "MRP-PROP-TEST",
					"company": "Test Company",
					"proposal_type": "Firm APS",
					"status": "Ready",
					"mrp_run": "MRP-RUN-TEST",
					"items": [
						frappe._dict(
							{
								"name": "ROW-001",
								"status": "Pending",
								"action": "Create Material Request",
								"commitment_type": "Firm",
								"requirement_line": "REQ-001",
								"qty": 50,
								"item_code": "RM-001",
								"warehouse": "Stores",
								"schedule_date": "2026-05-10",
							}
						)
					],
				}
			)

			result = planning._validate_proposal_batch_for_release(batch, frappe._dict({}))
		finally:
			planning._find_newer_overlapping_ready_batch = original_find_newer
			planning._get_requirement_validation_map = original_requirements
			planning._get_validation_run = original_get_run
			planning._get_supply_records = original_supply

		self.assertFalse(result["valid"])
		self.assertEqual(result["blocking_issues"][0]["issue_type"], "Reduced Shortage")

	def test_validate_release_blocks_insufficient_prebuy_consumption(self):
		original_find_newer = planning._find_newer_overlapping_ready_batch
		original_prebuy = planning._get_prebuy_supply_records
		planning._find_newer_overlapping_ready_batch = lambda batch, row: None
		planning._get_prebuy_supply_records = lambda item_code, company, warehouse: [
			planning.SupplyRecord("Prebuy", item_code, company, warehouse, 20, 20, "2026-05-01", "2026-05-01", 50)
		]
		try:
			batch = frappe._dict(
				{
					"name": "MRP-PROP-TEST",
					"company": "Test Company",
					"proposal_type": "Firm APS",
					"status": "Ready",
					"items": [
						frappe._dict(
							{
								"name": "ROW-001",
								"status": "Pending",
								"action": "Consume Prebuy",
								"commitment_type": "Prebuy",
								"qty": 50,
								"item_code": "RM-001",
								"warehouse": "Stores",
								"schedule_date": "2026-05-10",
							}
						)
					],
				}
			)

			result = planning._validate_proposal_batch_for_release(batch, frappe._dict({}))
		finally:
			planning._find_newer_overlapping_ready_batch = original_find_newer
			planning._get_prebuy_supply_records = original_prebuy

		self.assertFalse(result["valid"])
		self.assertEqual(result["blocking_issues"][0]["issue_type"], "Insufficient Prebuy")

	def test_supersede_overlapping_batches_only_updates_active_covered_batches(self):
		original_has_field = planning._has_field
		original_get_all = planning.frappe.get_all
		original_get_scope = planning._get_run_scope
		original_covers = planning._run_scope_covers
		original_set_value = planning.frappe.db.set_value
		updates = []
		planning._has_field = lambda doctype, fieldname: True
		planning.frappe.get_all = lambda doctype, **kwargs: [
			frappe._dict({"name": "MRP-PROP-OLD", "mrp_run": "MRP-RUN-OLD"}),
			frappe._dict({"name": "MRP-PROP-OTHER", "mrp_run": "MRP-RUN-OTHER"}),
		]
		planning._get_run_scope = lambda mrp_run: frappe._dict({"name": mrp_run})
		planning._run_scope_covers = lambda new_run, old_run: old_run.name == "MRP-RUN-OLD"
		planning.frappe.db.set_value = lambda doctype, name, values: updates.append((doctype, name, values))
		try:
			planning._supersede_overlapping_proposal_batches(
				frappe._dict({"name": "MRP-PROP-NEW", "proposal_type": "Firm APS"}),
				frappe._dict({"name": "MRP-RUN-NEW", "company": "Test Company", "run_type": "Firm APS"}),
			)
		finally:
			planning._has_field = original_has_field
			planning.frappe.get_all = original_get_all
			planning._get_run_scope = original_get_scope
			planning._run_scope_covers = original_covers
			planning.frappe.db.set_value = original_set_value

		self.assertEqual(len(updates), 1)
		self.assertEqual(updates[0][1], "MRP-PROP-OLD")
		self.assertEqual(updates[0][2]["status"], "Superseded")

	def test_run_comparison_classifies_delta_rows(self):
		original_get_all = planning.frappe.get_all
		def fake_get_all(doctype, **kwargs):
			mrp_run = kwargs["filters"]["mrp_run"]
			if mrp_run == "CUR":
				return [
					frappe._dict({"item_code": "RM-001", "warehouse": "Stores", "required_date": "2026-05-01", "commitment_type": "Firm", "gross_qty": 10, "net_qty": 8, "new_supply_qty": 8, "prebuy_consumed_qty": 0}),
					frappe._dict({"item_code": "RM-002", "warehouse": "Stores", "required_date": "2026-05-01", "commitment_type": "Firm", "gross_qty": 4, "net_qty": 4, "new_supply_qty": 4, "prebuy_consumed_qty": 0}),
				]
			return [
				frappe._dict({"item_code": "RM-001", "warehouse": "Stores", "required_date": "2026-05-01", "commitment_type": "Firm", "gross_qty": 10, "net_qty": 5, "new_supply_qty": 5, "prebuy_consumed_qty": 0}),
				frappe._dict({"item_code": "RM-003", "warehouse": "Stores", "required_date": "2026-05-01", "commitment_type": "Firm", "gross_qty": 2, "net_qty": 2, "new_supply_qty": 2, "prebuy_consumed_qty": 0}),
			]
		planning.frappe.get_all = fake_get_all
		try:
			current = planning._summarize_run_requirements("CUR")
			previous = planning._summarize_run_requirements("PREV")
		finally:
			planning.frappe.get_all = original_get_all

		self.assertEqual(current[("RM-001", "Stores", "2026-05-01", "Firm")]["net_qty"], 8)
		self.assertEqual(previous[("RM-003", "Stores", "2026-05-01", "Firm")]["net_qty"], 2)
