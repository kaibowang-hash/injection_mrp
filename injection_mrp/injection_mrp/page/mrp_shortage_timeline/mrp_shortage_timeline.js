frappe.pages["mrp-shortage-timeline"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("MRP Shortage Timeline"),
		single_column: true,
	});
	const ui = injection_mrp.ui;
	const shell = ui.make_shell(page, __("MRP Shortage Timeline"), __("Rolling material availability with first shortage date, safety stock risk and latest order date."));
	const filters = {};
	const pageState = { limit_start: 0, limit_page_length: 500 };
	let alerts = [];
	let balances = [];

	const alertColumns = [
		{ label: __("Alert"), fieldname: "name", formatter: (value) => ui.doc_link("MRP Shortage Alert", value) },
		{ label: __("Run"), fieldname: "mrp_run", formatter: (value) => ui.doc_link("MRP Run", value) },
		{ label: __("Warning"), fieldname: "warning_level", formatter: (value) => ui.code_badge(value, { kind: "warning" }) },
		{ label: __("Status"), fieldname: "status", formatter: (value) => ui.code_badge(value, { kind: "status" }) },
		{ label: __("Item"), fieldname: "item_code", formatter: (value, row) => ui.item_cell(value, row.item_name || row.description) },
		{ label: __("Warehouse"), fieldname: "warehouse" },
		{ label: __("First Shortage Date"), fieldname: "first_shortage_date", formatter: ui.format_date },
		{ label: __("Shortage Qty"), fieldname: "shortage_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Lowest Projected"), fieldname: "lowest_projected_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Safety Stock"), fieldname: "safety_stock_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Safety Gap"), fieldname: "safety_stock_gap_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Latest Order Date"), fieldname: "latest_order_date", formatter: ui.format_date },
		{ label: __("Affected Requirements"), fieldname: "affected_requirement_count", numeric: true },
	];

	const balanceColumns = [
		{ label: __("Run"), fieldname: "mrp_run", formatter: (value) => ui.doc_link("MRP Run", value) },
		{ label: __("Bucket Type"), fieldname: "bucket_type", formatter: (value) => ui.code_badge(value, { tone: value === "Daily" ? "blue" : "green" }) },
		{ label: __("Bucket Start"), fieldname: "bucket_start", formatter: ui.format_date },
		{ label: __("Bucket End"), fieldname: "bucket_end", formatter: ui.format_date },
		{ label: __("Warning"), fieldname: "warning_level", formatter: (value) => ui.code_badge(value, { kind: "warning" }) },
		{ label: __("Item"), fieldname: "item_code", formatter: (value, row) => ui.item_cell(value, row.item_name || row.description) },
		{ label: __("Warehouse"), fieldname: "warehouse" },
		{ label: __("Opening"), fieldname: "opening_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Demand Qty"), fieldname: "demand_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Supply Qty"), fieldname: "supply_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Planned Supply"), fieldname: "planned_supply_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Projected Qty"), fieldname: "projected_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Shortage Qty"), fieldname: "shortage_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Safety Gap"), fieldname: "safety_stock_gap_qty", numeric: true, formatter: ui.format_number },
	];

	function open_alert(row) {
		if (!row) {
			return;
		}
		const affected = JSON.parse(row.affected_requirements || "[]");
		const affectedColumns = [
			{ label: __("Requirement"), fieldname: "requirement_line", formatter: (value) => ui.doc_link("MRP Requirement Line", value) },
			{ label: __("Demand Snapshot"), fieldname: "demand_snapshot", formatter: (value) => ui.doc_link("MRP Demand Snapshot", value) },
			{ label: __("Date"), fieldname: "date", formatter: ui.format_date },
			{ label: __("Qty"), fieldname: "qty", formatter: ui.format_number },
		];
		const relatedBalances = balances.filter(
			(balance) => balance.mrp_run === row.mrp_run && balance.item_code === row.item_code && balance.warehouse === row.warehouse
		);
		ui.open_drawer(row.name, [
			{
				title: __("Shortage Alert"),
				rows: [
					{ label: __("Item"), html: ui.item_cell(row.item_code, row.item_name) },
					{ label: __("Run"), html: ui.doc_link("MRP Run", row.mrp_run) },
					{ label: __("Warehouse"), value: row.warehouse },
					{ label: __("First Shortage Date"), value: ui.format_date(row.first_shortage_date) },
					{ label: __("Shortage Qty"), value: ui.format_number(row.shortage_qty) },
					{ label: __("Lowest Projected"), value: ui.format_number(row.lowest_projected_qty) },
					{ label: __("Latest Order Date"), value: ui.format_date(row.latest_order_date) },
				],
			},
			{
				title: __("Affected Requirements"),
				html: ui.mini_table_html(affectedColumns, affected, __("No affected requirements found.")),
			},
			{
				title: __("Rolling Balance"),
				html: ui.mini_table_html(balanceColumns, relatedBalances, __("No rolling balance lines found.")),
			},
		]);
	}

	async function load(options) {
		if (!options || !options.keepPage) {
			pageState.limit_start = 0;
		}
		const data = await ui.with_busy(__("Loading shortage timeline..."), () =>
			ui.xcall("injection_mrp.api.app.get_shortage_timeline_data", { filters, ...pageState })
		);
		alerts = data.alerts || [];
		balances = data.balances || [];
		ui.render_cards(shell.cards, data.cards || []);
		ui.render_status(shell.status, [__("Rolling shortage alerts"), __("Rows: {0}", [(data.pagination || {}).total_count || alerts.length])]);
		ui.render_table(shell.table, alertColumns, alerts, {
			empty: __("No shortage alerts found."),
			on_row_click: open_alert,
			exportable: true,
			export_title: __("MRP Shortage Timeline"),
			export_file_name: "mrp_shortage_timeline",
			toolbar_html: ui.icon_button("download", __("Export Rolling Balance"), { "data-imrp-export-balance": "1" }),
			pagination: data.pagination,
			on_page: (nextStart) => {
				pageState.limit_start = nextStart;
				load({ keepPage: true });
			},
		});
		shell.table.find("[data-imrp-export-balance='1']").on("click", () => {
			ui.export_rows("mrp_rolling_balance", balanceColumns, balances, {
				export_title: __("MRP Rolling Balance"),
			});
		});
	}

	ui.add_text_filter(shell.filters, __("MRP Run"), "mrp_run", filters, load, "Link", "MRP Run");
	ui.add_text_filter(shell.filters, __("Company"), "company", filters, load, "Link", "Company");
	ui.add_text_filter(shell.filters, __("Item"), "item_code", filters, load, "Link", "Item");
	ui.add_text_filter(shell.filters, __("Warehouse"), "warehouse", filters, load, "Link", "Warehouse");
	ui.add_text_filter(shell.filters, __("Warning Level"), "warning_level", filters, load, "Select", "\nNone\nWarning\nCritical");
	ui.add_text_filter(shell.filters, __("Status"), "status", filters, load, "Select", "\nOpen\nReviewed\nClosed");
	load();
};
