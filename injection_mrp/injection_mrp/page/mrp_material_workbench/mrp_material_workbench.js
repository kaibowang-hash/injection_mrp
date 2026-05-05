frappe.pages["mrp-material-workbench"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("MRP Material Workbench"),
		single_column: true,
	});
	const ui = injection_mrp.ui;
	const shell = ui.make_shell(page, __("MRP Material Workbench"), __("Material requirements with stock, open supply and prebuy consumption offsets."));
	const filters = {};
	const pageState = { limit_start: 0, limit_page_length: 500 };
	let rows = [];
	const columns = [
		{ label: __("Requirement"), fieldname: "name", formatter: (value) => ui.doc_link("MRP Requirement Line", value) },
		{ label: __("Run", null, "Injection MRP"), fieldname: "mrp_run", formatter: (value) => ui.doc_link("MRP Run", value) },
		{ label: __("Type"), fieldname: "run_type", formatter: (value) => ui.code_badge(value, { tone: value === "Firm APS" ? "green" : "blue" }) },
		{ label: __("Commitment"), fieldname: "commitment_type", formatter: (value) => ui.code_badge(value, { tone: value === "Prebuy" ? "orange" : "green" }) },
		{ label: __("Status"), fieldname: "status", formatter: (value) => ui.code_badge(value, { kind: "status" }) },
		{ label: __("Item"), fieldname: "item_code", formatter: (value, row) => ui.item_cell(value, row.item_name || row.description) },
		{ label: __("Supply Mode"), fieldname: "supply_mode", formatter: (value) => ui.code_badge(value, { tone: value === "No Action" || value === "Supplier Supplied" ? "orange" : "blue" }) },
		{ label: __("MR Type"), fieldname: "material_request_type", formatter: (value) => ui.code_badge(value, { tone: "blue" }) },
		{ label: __("Supplier"), fieldname: "supplier" },
		{ label: __("Warehouse"), fieldname: "warehouse" },
		{ label: __("First Shortage Date"), fieldname: "first_shortage_date", formatter: ui.format_date },
		{ label: __("Lowest Projected"), fieldname: "lowest_projected_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Required Date"), fieldname: "required_date", formatter: ui.format_date },
		{ label: __("Material Need Date"), fieldname: "material_need_date", formatter: ui.format_date },
		{ label: __("Gross"), fieldname: "gross_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Stock"), fieldname: "available_qty", numeric: true, formatter: ui.format_number },
		{ label: __("MR"), fieldname: "open_mr_qty", numeric: true, formatter: ui.format_number },
		{ label: __("PO"), fieldname: "open_po_qty", numeric: true, formatter: ui.format_number },
		{ label: __("WO"), fieldname: "open_wo_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Prebuy Available"), fieldname: "prebuy_available_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Prebuy"), fieldname: "prebuy_consumed_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Planned", null, "Injection MRP"), fieldname: "new_supply_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Order Excess"), fieldname: "order_excess_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Net"), fieldname: "net_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Order Date"), fieldname: "suggested_order_date", formatter: ui.format_date },
		{ label: __("Expected Arrival"), fieldname: "expected_arrival_date", formatter: ui.format_date },
		{ label: __("Variance"), fieldname: "delivery_variance_days", numeric: true },
		{
			label: __("Warnings"),
			fieldname: "warning_summary",
			formatter: (value, row) => ui.warning_badges("", value, row.warning_count ? "Warning" : "None"),
		},
	];

	async function open_detail(row) {
		if (!row || !row.name) {
			return;
		}
		const data = await ui.with_busy(__("Loading requirement detail..."), () =>
			ui.xcall("injection_mrp.api.app.get_requirement_detail", { requirement_line: row.name })
		);
		const requirement = data.requirement || {};
		const demand = data.demand || {};
		const bomDetail = data.bom_detail || {};
		const demandBom = bomDetail.demand_bom || {};
		const requirementBom = bomDetail.requirement_bom || {};
		const currentBomItem = bomDetail.current_bom_item || {};
		const rollingColumns = [
			{ label: __("Bucket"), fieldname: "bucket_type", formatter: (value, item) => `${ui.translate(value)} ${ui.format_date(item.bucket_start)}${item.bucket_end && item.bucket_end !== item.bucket_start ? ` - ${ui.format_date(item.bucket_end)}` : ""}` },
			{ label: __("Opening"), fieldname: "opening_qty", formatter: ui.format_number },
			{ label: __("Demand Qty"), fieldname: "demand_qty", formatter: ui.format_number },
			{ label: __("Supply Qty"), fieldname: "supply_qty", formatter: ui.format_number },
			{ label: __("Planned Supply"), fieldname: "planned_supply_qty", formatter: ui.format_number },
			{ label: __("Projected Qty"), fieldname: "projected_qty", formatter: ui.format_number },
			{ label: __("Shortage Qty"), fieldname: "shortage_qty", formatter: ui.format_number },
			{ label: __("Safety Gap"), fieldname: "safety_stock_gap_qty", formatter: ui.format_number },
			{ label: __("Warning"), fieldname: "warning_level", formatter: (value) => ui.code_badge(value, { kind: "warning" }) },
		];
		const shortageColumns = [
			{ label: __("Warning"), fieldname: "warning_level", formatter: (value) => ui.code_badge(value, { kind: "warning" }) },
			{ label: __("First Shortage Date"), fieldname: "first_shortage_date", formatter: ui.format_date },
			{ label: __("Shortage Qty"), fieldname: "shortage_qty", formatter: ui.format_number },
			{ label: __("Lowest Projected"), fieldname: "lowest_projected_qty", formatter: ui.format_number },
			{ label: __("Safety Gap"), fieldname: "safety_stock_gap_qty", formatter: ui.format_number },
			{ label: __("Latest Order Date"), fieldname: "latest_order_date", formatter: ui.format_date },
			{ label: __("Affected Requirements"), fieldname: "affected_requirement_count" },
		];
		const bomTraceColumns = [
			{ label: __("Level"), fieldname: "level" },
			{ label: __("BOM"), fieldname: "bom", formatter: (value) => (value ? ui.doc_link("BOM", value) : "") },
			{ label: __("Parent Item"), fieldname: "parent_item", formatter: (value, item) => ui.item_cell(value, item.parent_item_name) },
			{ label: __("Component Item"), fieldname: "component_item", formatter: (value, item) => ui.item_cell(value, item.component_item_name) },
			{ label: __("BOM Qty"), fieldname: "bom_qty", formatter: ui.format_number },
			{ label: __("Parent Qty"), fieldname: "parent_qty", formatter: ui.format_number },
			{ label: __("Required Qty"), fieldname: "required_qty", formatter: ui.format_number },
		];
		const bomExplosionColumns = [
			{ label: __("Level"), fieldname: "level" },
			{ label: __("BOM"), fieldname: "bom", formatter: (value) => (value ? ui.doc_link("BOM", value) : "") },
			{ label: __("Component Item"), fieldname: "component_item", formatter: (value, item) => ui.item_cell(value, item.component_item_name) },
			{ label: __("BOM Qty"), fieldname: "bom_qty", formatter: ui.format_number },
			{ label: __("Parent Qty"), fieldname: "parent_qty", formatter: ui.format_number },
			{ label: __("Required Qty"), fieldname: "required_qty", formatter: ui.format_number },
			{ label: __("UOM"), fieldname: "uom" },
			{ label: __("Child BOM"), fieldname: "child_bom", formatter: (value) => (value ? ui.doc_link("BOM", value) : "") },
			{ label: __("Do Not Explode"), fieldname: "do_not_explode", formatter: (value) => (Number(value || 0) ? __("Yes") : "") },
			{ label: __("Matched"), fieldname: "is_selected", formatter: (value) => (Number(value || 0) ? ui.code_badge(__("Current Item"), { code: "CUR", tone: "orange" }) : "") },
		];
		const peggingColumns = [
			{ label: __("Supply Type"), fieldname: "supply_type", formatter: (value) => ui.code_badge(value, { tone: value === "Planned Supply" ? "blue" : "green" }) },
			{ label: __("Supply"), fieldname: "supply_name", formatter: (value, item) => (value ? ui.doc_link(item.supply_doctype, value) : "") },
			{ label: __("Supply Qty"), fieldname: "supply_qty", formatter: ui.format_number },
			{ label: __("Expected Arrival"), fieldname: "expected_arrival_date", formatter: ui.format_date },
			{ label: __("Variance"), fieldname: "delivery_variance_days" },
			{ label: __("Adjustment"), fieldname: "adjustment_action", formatter: (value) => ui.code_badge(value, { kind: "action" }) },
			{ label: __("Warning"), fieldname: "warning_category", formatter: (value, item) => ui.warning_badges(value, item.warning_reason, item.warning_level) },
		];
		ui.open_drawer(requirement.name, [
			{
				title: __("Requirement"),
				rows: [
					{ label: __("Item"), html: ui.item_cell(requirement.item_code, requirement.item_name || requirement.description) },
					{ label: __("Run", null, "Injection MRP"), html: ui.doc_link("MRP Run", requirement.mrp_run) },
					{ label: __("Demand Item"), html: ui.item_cell(requirement.demand_item_code, demand.item_name || demand.description) },
					{ label: __("Supply Mode"), value: ui.translate(requirement.supply_mode) },
					{ label: __("Material Request Type"), value: ui.translate(requirement.material_request_type) },
					{ label: __("Source Warehouse"), value: requirement.source_warehouse || "" },
					{ label: __("Supplier"), value: requirement.supplier || "" },
					{ label: __("Supplier Lead Time Days"), value: ui.format_number(requirement.supplier_lead_time_days, 0) },
					{ label: __("Customer"), value: requirement.customer || "" },
					{ label: __("Required Date"), value: ui.format_date(requirement.required_date) },
					{ label: __("Material Need Date"), value: ui.format_date(requirement.material_need_date) },
					{ label: __("Suggested Order Date"), value: ui.format_date(requirement.suggested_order_date) },
					{ label: __("Expected Arrival Date"), value: ui.format_date(requirement.expected_arrival_date) },
					{ label: __("Lead Time Days"), value: ui.format_number(requirement.lead_time_days, 0) },
					{ label: __("Gross Qty"), value: ui.format_number(requirement.gross_qty) },
					{ label: __("Net Qty"), value: ui.format_number(requirement.net_qty) },
					{ label: __("Warning Summary"), value: requirement.warning_summary || "" },
					{ label: __("Adjustment Summary"), value: requirement.adjustment_summary || "" },
				],
			},
			{
				title: __("Procurement Constraints"),
				rows: [
					{ label: __("Purchase UOM"), value: requirement.purchase_uom || "" },
					{ label: __("Minimum Order Qty"), value: ui.format_number(requirement.min_order_qty) },
					{ label: __("Order Multiple Qty"), value: ui.format_number(requirement.order_multiple_qty) },
					{ label: __("Order Excess Qty"), value: ui.format_number(requirement.order_excess_qty) },
					{ label: __("Supplier Quotation"), html: requirement.supplier_quotation ? ui.doc_link("Supplier Quotation", requirement.supplier_quotation) : "" },
					{ label: __("Item Price"), html: requirement.item_price ? ui.doc_link("Item Price", requirement.item_price) : "" },
					{ label: __("Estimated Rate"), value: requirement.estimated_rate ? `${ui.format_number(requirement.estimated_rate)} ${requirement.currency || ""}` : "" },
					{ label: __("Estimated Amount"), value: requirement.estimated_amount ? `${ui.format_number(requirement.estimated_amount)} ${requirement.currency || ""}` : "" },
					{ label: __("Procurement Source"), value: ui.translate(requirement.procurement_source || "") },
					{ label: __("Procurement Constraint Summary"), value: requirement.procurement_constraint_summary || "" },
				],
			},
			{
				title: __("Shortage Alerts"),
				html: ui.mini_table_html(shortageColumns, data.shortage_alerts || [], __("No shortage alerts found.")),
			},
			{
				title: __("Rolling Balance"),
				html: ui.mini_table_html(rollingColumns, data.rolling_lines || [], __("No rolling balance lines found.")),
			},
			{
				title: __("Demand Source"),
				rows: [
					{ label: __("Type"), value: demand.demand_type },
					{ label: __("Source"), html: demand.source_name ? ui.doc_link(demand.source_doctype, demand.source_name) : "" },
					{ label: __("APS Run"), html: demand.aps_run ? ui.doc_link("APS Planning Run", demand.aps_run) : "" },
					{ label: __("APS Result"), html: demand.aps_result ? ui.doc_link("APS Schedule Result", demand.aps_result) : "" },
				],
			},
			{
				title: __("BOM Confirmation"),
				rows: [
					{ label: __("Demand BOM"), html: demandBom.name ? ui.doc_link("BOM", demandBom.name) : __("No BOM detail found.") },
					{ label: __("BOM Item"), html: demandBom.item ? ui.item_cell(demandBom.item, demandBom.item_name) : "" },
					{ label: __("BOM Qty"), value: demandBom.quantity ? `${ui.format_number(demandBom.quantity)} ${demandBom.uom || ""}` : "" },
					{ label: __("BOM Status"), value: demandBom.name ? `${demandBom.docstatus === 1 ? __("Submitted") : __("Draft")} / ${Number(demandBom.is_active || 0) ? __("Active") : __("Inactive")} / ${Number(demandBom.is_default || 0) ? __("Default") : __("Non Default")}` : "" },
					{ label: __("Requirement BOM"), html: requirementBom.name ? ui.doc_link("BOM", requirementBom.name) : "" },
					{ label: __("BOM Row"), value: currentBomItem.idx || "" },
					{ label: __("BOM Row Item"), html: currentBomItem.item_code ? ui.item_cell(currentBomItem.item_code, currentBomItem.item_name) : "" },
					{ label: __("Row Qty"), value: currentBomItem.qty ? `${ui.format_number(currentBomItem.qty)} ${currentBomItem.uom || ""}` : "" },
					{ label: __("Stock Qty"), value: currentBomItem.stock_qty ? `${ui.format_number(currentBomItem.stock_qty)} ${currentBomItem.stock_uom || ""}` : "" },
					{ label: __("Child BOM"), html: currentBomItem.bom_no ? ui.doc_link("BOM", currentBomItem.bom_no) : "" },
					{ label: __("Do Not Explode"), value: Number(currentBomItem.do_not_explode || 0) ? __("Yes") : __("No") },
					{ label: __("Source Warehouse"), value: currentBomItem.source_warehouse || "" },
					{ label: __("Operation"), value: currentBomItem.operation || "" },
				],
			},
			{
				title: __("BOM Explosion Path"),
				html: ui.mini_table_html(bomTraceColumns, bomDetail.trace || [], __("No BOM trace found.")),
			},
			{
				title: __("BOM Expanded Items"),
				html: ui.mini_table_html(bomExplosionColumns, bomDetail.exploded_items || [], __("No BOM expanded items found.")),
			},
			{
				title: __("Supply Offset"),
				rows: [
					{ label: __("Stock"), value: ui.format_number(requirement.available_qty) },
					{ label: __("Open MR"), value: ui.format_number(requirement.open_mr_qty) },
					{ label: __("Open PO"), value: ui.format_number(requirement.open_po_qty) },
					{ label: __("Open WO"), value: ui.format_number(requirement.open_wo_qty) },
					{ label: __("Prebuy Available"), value: ui.format_number(requirement.prebuy_available_qty) },
					{ label: __("Prebuy Consumed"), value: ui.format_number(requirement.prebuy_consumed_qty) },
					{ label: __("Planned Supply"), value: ui.format_number(requirement.new_supply_qty) },
				],
			},
			{
				title: __("Pegging Detail"),
				html: ui.mini_table_html(peggingColumns, data.pegging_lines || [], __("No pegging lines found.")),
			},
			{
				title: __("Exceptions"),
				rows: (data.exceptions || []).length
					? (data.exceptions || []).map((item) => ({ label: item.category, value: item.message }))
					: [{ label: __("Status"), value: __("No open exception.") }],
			},
		]);
	}

	async function load(options) {
		if (!options || !options.keepPage) {
			pageState.limit_start = 0;
		}
		const data = await ui.with_busy(__("Loading material requirements..."), () =>
			ui.xcall("injection_mrp.api.app.get_material_workbench_data", { filters, ...pageState })
		);
		rows = data.rows || [];
		ui.render_cards(shell.cards, data.cards || []);
		ui.render_status(shell.status, [__("Click a row to inspect BOM expansion, supply offsets and next actions."), __("Rows: {0}", [(data.pagination || {}).total_count || rows.length])]);
		ui.render_table(shell.table, columns, rows, {
			empty: __("No material requirements found."),
			on_row_click: open_detail,
			exportable: true,
			export_title: __("MRP Material Workbench"),
			export_file_name: "mrp_materials",
			export_columns: columns.concat([{ label: __("Warning Count"), fieldname: "warning_count", numeric: true }]),
			legend_columns: [
				{ fieldname: "run_type" },
				{ fieldname: "commitment_type" },
				{ fieldname: "status", kind: "status" },
				{ fieldname: "supply_mode" },
				{ fieldname: "material_request_type" },
				{ fieldname: "warning_summary", kind: "warning" },
			],
			pagination: data.pagination,
			on_page: (nextStart) => {
				pageState.limit_start = nextStart;
				load({ keepPage: true });
			},
		});
	}

	ui.add_text_filter(shell.filters, __("MRP Run"), "mrp_run", filters, load, "Link", "MRP Run");
	ui.add_text_filter(shell.filters, __("Company"), "company", filters, load, "Link", "Company");
	ui.add_text_filter(shell.filters, __("Run Type"), "run_type", filters, load, "Select", "\nForecast Prebuy\nFirm APS\nManual");
	ui.add_text_filter(shell.filters, __("Item"), "item_code", filters, load, "Link", "Item");
	ui.add_text_filter(shell.filters, __("Warehouse"), "warehouse", filters, load, "Link", "Warehouse");
	ui.add_text_filter(shell.filters, __("Commitment"), "commitment_type", filters, load, "Select", "\nPrebuy\nFirm");
	ui.add_text_filter(shell.filters, __("Supply Mode"), "supply_mode", filters, load, "Select", "\nPurchase\nManufacture\nSubcontracting\nCustomer Provided\nMaterial Transfer\nSupplier Supplied\nNo Action");
	load();
};
