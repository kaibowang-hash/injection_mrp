frappe.pages["mrp-run-console"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("MRP Run Console"),
		single_column: true,
	});
	const ui = injection_mrp.ui;
	const shell = ui.make_shell(page, __("MRP Run Console"), __("Forecast prebuy and firm APS material calculation."));
	let rows = [];

	const columns = [
		{ label: __("Run"), fieldname: "name", formatter: (value) => ui.doc_link("MRP Run", value) },
		{ label: __("Type"), fieldname: "run_type", formatter: (value) => ui.code_badge(value, { tone: value === "Firm APS" ? "green" : "blue" }) },
		{ label: __("Status"), fieldname: "status", formatter: (value) => ui.code_badge(value, { kind: "status" }) },
		{ label: __("Company"), fieldname: "company" },
		{ label: __("Planning Date"), fieldname: "planning_date", formatter: ui.format_date },
		{ label: __("Horizon End"), fieldname: "horizon_end", formatter: ui.format_date },
		{ label: __("APS Run"), fieldname: "aps_run", formatter: (value) => (value ? ui.doc_link("APS Planning Run", value) : "") },
		{ label: __("Demand"), fieldname: "demand_count", numeric: true },
		{ label: __("Requirements"), fieldname: "requirement_count", numeric: true },
		{ label: __("Exceptions"), fieldname: "exception_count", numeric: true },
		{ label: __("Net Qty"), fieldname: "total_net_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Proposal"), fieldname: "proposal_batch", formatter: (value) => (value ? ui.doc_link("MRP Proposal Batch", value) : "") },
	];

	async function load() {
		const data = await ui.with_busy(__("Loading MRP runs..."), () =>
			ui.xcall("injection_mrp.api.app.get_run_console_data", { limit: 50 })
		);
		rows = data.runs || [];
		ui.render_cards(shell.cards, data.cards || []);
		ui.render_status(shell.status, [__("Latest MRP calculations"), __("Rows: {0}", [rows.length])]);
		ui.render_table(shell.table, columns, rows, {
			empty: __("No MRP runs yet."),
			exportable: true,
			export_title: __("MRP Run Console"),
			export_file_name: "mrp_runs",
		});
	}

	function open_run_dialog(method, title) {
		frappe.prompt(
			[
				{ fieldtype: "Link", fieldname: "company", label: __("Company"), options: "Company", reqd: 1 },
				{ fieldtype: "Link", fieldname: "aps_run", label: __("APS Run"), options: "APS Planning Run", depends_on: "eval:doc.method === 'firm'" },
				{ fieldtype: "Link", fieldname: "item_code", label: __("Item"), options: "Item" },
				{ fieldtype: "Link", fieldname: "customer", label: __("Customer"), options: "Customer" },
				{ fieldtype: "Link", fieldname: "warehouse", label: __("Warehouse"), options: "Warehouse" },
				{ fieldtype: "Date", fieldname: "planning_date", label: __("Planning Date"), default: frappe.datetime.get_today() },
				{ fieldtype: "Data", fieldname: "method", hidden: 1, default: method.includes("firm") ? "firm" : "forecast" },
			],
			async (values) => {
				await ui.with_busy(__("Calculating MRP..."), () => ui.xcall(method, values));
				await load();
			},
			title,
			__("Run")
		);
	}

	ui.render_actions(shell.actions, [
		{
			label: __("Forecast Prebuy"),
			action_key: "run_forecast_prebuy",
			tone: "primary",
			on_click: () => open_run_dialog("injection_mrp.api.app.run_forecast_prebuy", __("Run Forecast Prebuy")),
		},
		{
			label: __("Firm APS"),
			action_key: "run_firm_aps_mrp",
			tone: "primary",
			on_click: () => open_run_dialog("injection_mrp.api.app.run_firm_aps_mrp", __("Run Firm APS MRP")),
		},
	]);

	shell.floating_filters = false;
	shell.filters.hide();
	load();
};
