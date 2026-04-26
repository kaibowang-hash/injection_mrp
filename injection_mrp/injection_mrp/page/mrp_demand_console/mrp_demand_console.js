frappe.pages["mrp-demand-console"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("MRP Demand Console"),
		single_column: true,
	});
	const ui = injection_mrp.ui;
	const shell = ui.make_shell(page, __("MRP Demand Console"), __("Demand snapshots from customer schedules, sales orders, safety stock and APS."));
	const filters = {};
	const pageState = { limit_start: 0, limit_page_length: 500 };
	let rows = [];
	const columns = [
		{ label: __("Snapshot"), fieldname: "name", formatter: (value) => ui.doc_link("MRP Demand Snapshot", value) },
		{ label: __("Run"), fieldname: "mrp_run", formatter: (value) => ui.doc_link("MRP Run", value) },
		{ label: __("Type"), fieldname: "demand_type", formatter: (value) => ui.code_badge(value, { tone: value === "APS" ? "green" : "blue" }) },
		{ label: __("Status"), fieldname: "status", formatter: (value) => ui.code_badge(value, { kind: "status" }) },
		{ label: __("Customer"), fieldname: "customer" },
		{ label: __("Item"), fieldname: "item_code", formatter: (value, row) => ui.item_cell(value, row.item_name || row.description) },
		{ label: __("Warehouse"), fieldname: "warehouse" },
		{ label: __("Required Date"), fieldname: "required_date", formatter: ui.format_date },
		{ label: __("Demand Qty"), fieldname: "demand_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Remaining Qty"), fieldname: "remaining_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Source"), fieldname: "source_name", formatter: (value, row) => (value ? ui.doc_link(row.source_doctype, value) : "") },
	];

	async function load(options) {
		if (!options || !options.keepPage) {
			pageState.limit_start = 0;
		}
		const data = await ui.with_busy(__("Loading MRP demand..."), () =>
			ui.xcall("injection_mrp.api.app.get_demand_console_data", { filters, ...pageState })
		);
		rows = data.rows || [];
		ui.render_cards(shell.cards, data.cards || []);
		ui.render_status(shell.status, [__("Demand snapshots"), __("Rows: {0}", [(data.pagination || {}).total_count || rows.length])]);
		ui.render_table(shell.table, columns, rows, {
			empty: __("No demand snapshots found."),
			exportable: true,
			export_title: __("MRP Demand Console"),
			export_file_name: "mrp_demand",
			pagination: data.pagination,
			on_page: (nextStart) => {
				pageState.limit_start = nextStart;
				load({ keepPage: true });
			},
		});
	}

	ui.add_text_filter(shell.filters, __("MRP Run"), "mrp_run", filters, load, "Link", "MRP Run");
	ui.add_text_filter(shell.filters, __("Company"), "company", filters, load, "Link", "Company");
	ui.add_text_filter(shell.filters, __("Demand Type"), "demand_type", filters, load, "Select", "\nForecast\nSales Order\nSafety Stock\nAPS\nProduction Plan\nManual");
	ui.add_text_filter(shell.filters, __("Item"), "item_code", filters, load, "Link", "Item");
	ui.add_text_filter(shell.filters, __("Customer"), "customer", filters, load, "Link", "Customer");
	load();
};
