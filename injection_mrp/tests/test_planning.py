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
