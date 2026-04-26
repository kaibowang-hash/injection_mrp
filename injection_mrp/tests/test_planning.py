import unittest

import frappe

from injection_mrp.services import planning


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
		original_item_exists = planning._item_exists
		original_insert_invalid = planning._insert_invalid_demand_item_exception
		captured = []
		planning._item_exists = lambda item_code: item_code == "VALID-ITEM"
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
			planning._item_exists = original_item_exists
			planning._insert_invalid_demand_item_exception = original_insert_invalid

		self.assertEqual([row.item_code for row in valid], ["VALID-ITEM"])
		self.assertEqual(captured, ["PICO AD AIRPATH BODY RING"])
