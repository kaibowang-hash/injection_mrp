frappe.pages["mrp-pegging-detail"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("MRP Pegging Detail"),
		single_column: true,
	});
	const ui = injection_mrp.ui;
	const shell = ui.make_shell(page, __("MRP Pegging Detail"), __("Demand-supply pegging with lead time, arrival variance, adjustment suggestions and warnings."));
	const filters = {};
	const pageState = { limit_start: 0, limit_page_length: 500 };
	let rows = [];

	const columns = [
		{ label: __("Pegging"), fieldname: "name", formatter: (value) => ui.doc_link("MRP Pegging Line", value) },
		{ label: __("Run"), fieldname: "mrp_run", formatter: (value) => ui.doc_link("MRP Run", value) },
		{ label: __("Type"), fieldname: "run_type", formatter: (value) => ui.code_badge(value, { tone: value === "Firm APS" ? "green" : "blue" }) },
		{
			label: __("Warning"),
			fieldname: "warning_level",
			formatter: (value, row) => ui.warning_badges(row.warning_category, row.warning_reason, value),
		},
		{ label: __("Item"), fieldname: "item_code", formatter: (value, row) => ui.item_cell(value, row.item_name || row.description) },
		{ label: __("Warehouse"), fieldname: "warehouse" },
		{ label: __("Required Date"), fieldname: "required_date", formatter: ui.format_date },
		{ label: __("Material Need Date"), fieldname: "material_need_date", formatter: ui.format_date },
		{ label: __("Demand Qty"), fieldname: "demand_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Supply Type"), fieldname: "supply_type", formatter: (value) => ui.code_badge(value, { tone: value === "Planned Supply" ? "blue" : value === "Prebuy" ? "orange" : "green" }) },
		{ label: __("Supply Document"), fieldname: "supply_name", formatter: (value, row) => (value ? ui.doc_link(row.supply_doctype, value) : "") },
		{ label: __("Original Supply Qty"), fieldname: "original_supply_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Supply Qty"), fieldname: "supply_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Remaining Supply Qty"), fieldname: "remaining_supply_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Supply Date"), fieldname: "supply_date", formatter: ui.format_date },
		{ label: __("Expected Arrival"), fieldname: "expected_arrival_date", formatter: ui.format_date },
		{ label: __("Variance"), fieldname: "delivery_variance_days", numeric: true },
		{ label: __("Adjustment"), fieldname: "adjustment_action", formatter: (value) => ui.code_badge(value, { kind: "action" }) },
		{ label: __("Adjustment Date"), fieldname: "adjustment_date", formatter: ui.format_date },
	];

	function open_detail(row) {
		if (!row) {
			return;
		}
		ui.open_drawer(row.name, [
			{
				title: __("Demand"),
				rows: [
					{ label: __("Item"), html: ui.item_cell(row.item_code, row.item_name || row.description) },
					{ label: __("Demand Item"), html: ui.item_cell(row.demand_item_code, row.item_name || row.description) },
					{ label: __("Demand Type"), value: ui.translate(row.demand_type) },
					{ label: __("Demand Source"), html: row.demand_source_name ? ui.doc_link(row.demand_source_doctype, row.demand_source_name) : "" },
					{ label: __("Required Date"), value: ui.format_date(row.required_date) },
					{ label: __("Material Need Date"), value: ui.format_date(row.material_need_date) },
					{ label: __("Demand Qty"), value: ui.format_number(row.demand_qty) },
				],
			},
			{
				title: __("Supply"),
				rows: [
					{ label: __("Supply Type"), value: ui.translate(row.supply_type) },
					{ label: __("Supply Document"), html: row.supply_name ? ui.doc_link(row.supply_doctype, row.supply_name) : "" },
					{ label: __("Original Supply Qty"), value: ui.format_number(row.original_supply_qty) },
					{ label: __("Supply Qty"), value: ui.format_number(row.supply_qty) },
					{ label: __("Remaining Supply Qty"), value: ui.format_number(row.remaining_supply_qty) },
					{ label: __("Expected Arrival Date"), value: ui.format_date(row.expected_arrival_date) },
					{ label: __("Delivery Variance Days"), value: ui.format_number(row.delivery_variance_days, 0) },
				],
			},
			{
				title: __("Advice"),
				rows: [
					{ label: __("Adjustment Action"), value: ui.translate(row.adjustment_action) },
					{ label: __("Adjustment Qty"), value: ui.format_number(row.adjustment_qty) },
					{ label: __("Adjustment Date"), value: ui.format_date(row.adjustment_date) },
					{ label: __("Warning Level"), value: ui.translate(row.warning_level) },
					{ label: __("Warning Category"), value: ui.translate(row.warning_category) },
					{ label: __("Warning Reason"), value: row.warning_reason || "" },
				],
			},
		]);
	}

	async function load(options) {
		if (!options || !options.keepPage) {
			pageState.limit_start = 0;
		}
		const data = await ui.with_busy(__("Loading pegging detail..."), () =>
			ui.xcall("injection_mrp.api.app.get_pegging_detail_data", { filters, ...pageState })
		);
		rows = data.rows || [];
		ui.render_cards(shell.cards, data.cards || []);
		ui.render_status(shell.status, [__("Supply-demand pegging detail"), __("Rows: {0}", [(data.pagination || {}).total_count || rows.length])]);
		ui.render_table(shell.table, columns, rows, {
			empty: __("No pegging lines found."),
			on_row_click: open_detail,
			exportable: true,
			export_title: __("MRP Pegging Detail"),
			export_file_name: "mrp_pegging_detail",
			export_columns: columns.concat([{ label: __("Warning Reason"), fieldname: "warning_reason" }]),
			pagination: data.pagination,
			on_page: (nextStart) => {
				pageState.limit_start = nextStart;
				load({ keepPage: true });
			},
		});
	}

	ui.add_text_filter(shell.filters, __("MRP Run"), "mrp_run", filters, load, "Link", "MRP Run");
	ui.add_text_filter(shell.filters, __("Company"), "company", filters, load, "Link", "Company");
	ui.add_text_filter(shell.filters, __("Item"), "item_code", filters, load, "Link", "Item");
	ui.add_text_filter(shell.filters, __("Warehouse"), "warehouse", filters, load, "Link", "Warehouse");
	ui.add_text_filter(shell.filters, __("Supply Type"), "supply_type", filters, load, "Select", "\nStock\nMaterial Request\nPurchase Order\nWork Order\nProduction Plan\nPrebuy\nPlanned Supply");
	ui.add_text_filter(shell.filters, __("Warning Level"), "warning_level", filters, load, "Select", "\nNone\nInfo\nWarning\nCritical");
	ui.add_text_filter(shell.filters, __("Adjustment"), "adjustment_action", filters, load, "Select", "\nNo Adjustment\nExpedite\nDelay\nCancel\nReview\nCreate Material Request\nConsume Prebuy\nReview Excess Prebuy");
	load();
};
