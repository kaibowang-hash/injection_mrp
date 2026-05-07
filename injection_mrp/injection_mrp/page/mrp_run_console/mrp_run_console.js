frappe.pages["mrp-run-console"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("MRP Run Console"),
		single_column: true,
	});
	const ui = injection_mrp.ui;
	const shell = ui.make_shell(page, __("MRP Run Console"), __("Forecast prebuy and firm APS material calculation."));
	const activeStatuses = new Set(["Queued", "Running"]);
	let rows = [];
	let refreshTimer = null;

	const columns = [
		{ label: __("Run", null, "Injection MRP"), fieldname: "name", formatter: (value) => ui.doc_link("MRP Run", value) },
		{ label: __("Type"), fieldname: "run_type", formatter: (value) => ui.code_badge(value, { tone: value === "Firm APS" ? "green" : "blue" }) },
		{ label: __("Status"), fieldname: "status", formatter: (value) => ui.code_badge(value, { kind: "status" }) },
		{ label: __("Company"), fieldname: "company" },
		{ label: __("Planning Date"), fieldname: "planning_date", formatter: ui.format_date },
		{ label: __("Horizon End"), fieldname: "horizon_end", formatter: ui.format_date },
		{ label: __("APS Run"), fieldname: "aps_run", formatter: (value) => (value ? ui.doc_link("APS Planning Run", value) : "") },
		{ label: __("Previous"), fieldname: "previous_run", formatter: (value) => (value ? ui.doc_link("MRP Run", value) : "") },
		{ label: __("Demand"), fieldname: "demand_count", numeric: true },
		{ label: __("Requirements"), fieldname: "requirement_count", numeric: true },
		{ label: __("Exceptions"), fieldname: "exception_count", numeric: true },
		{ label: __("Net Qty"), fieldname: "total_net_qty", numeric: true, formatter: ui.format_number },
		{ label: __("Proposal"), fieldname: "proposal_batch", formatter: (value) => (value ? ui.doc_link("MRP Proposal Batch", value) : "") },
		{
			label: __("Last Error"),
			fieldname: "error_message",
			formatter: (value, row) => {
				if (row.status !== "Failed" || !value) {
					return "";
				}
				const message = String(value);
				const preview = message.length > 80 ? `${message.slice(0, 80)}...` : message;
				return `<span class="text-danger" data-imrp-tooltip="${ui.escape(message)}">${ui.escape(preview)}</span>`;
			},
		},
	];

	function has_active_jobs() {
		return rows.some((row) => activeStatuses.has(row.status));
	}

	function schedule_refresh() {
		if (refreshTimer) {
			clearTimeout(refreshTimer);
			refreshTimer = null;
		}
		if (!has_active_jobs()) {
			return;
		}
		refreshTimer = setTimeout(() => load({ silent: true }), 5000);
	}

	async function load(options) {
		options = options || {};
		const fetchRuns = () => ui.xcall("injection_mrp.api.app.get_run_console_data", { limit: 50 });
		const data = options.silent ? await fetchRuns() : await ui.with_busy(__("Loading MRP runs..."), fetchRuns);
		rows = data.runs || [];
		ui.render_cards(shell.cards, data.cards || []);
		ui.render_status(shell.status, [
			has_active_jobs() ? __("MRP job is running in the background.") : __("Latest MRP calculations"),
			__("Rows: {0}", [rows.length]),
		]);
		ui.render_table(shell.table, columns, rows, {
			empty: __("No MRP runs yet."),
			exportable: true,
			export_title: __("MRP Run Console"),
			export_file_name: "mrp_runs",
			legend_columns: [{ fieldname: "run_type" }, { fieldname: "status", kind: "status" }],
			on_row_click: open_comparison,
		});
		schedule_refresh();
	}

	async function open_comparison(row) {
		if (!row || !row.name) {
			return;
		}
		const data = await ui.with_busy(__("Loading run comparison..."), () =>
			ui.xcall("injection_mrp.api.app.get_run_comparison_data", { mrp_run: row.name })
		);
		const summary = data.summary || {};
		const comparisonColumns = [
			{ label: __("Change", null, "Injection MRP"), fieldname: "change_type", formatter: (value) => ui.code_badge(value, { tone: value === "Increased" ? "orange" : value === "Decreased" ? "blue" : value === "Removed" ? "red" : "green" }) },
			{ label: __("Item"), fieldname: "item_code" },
			{ label: __("Warehouse"), fieldname: "warehouse" },
			{ label: __("Required Date"), fieldname: "required_date", formatter: ui.format_date },
			{ label: __("Commitment"), fieldname: "commitment_type", formatter: (value) => ui.code_badge(value, { tone: value === "Prebuy" ? "orange" : "green" }) },
			{ label: __("Previous Net"), fieldname: "previous_net_qty", formatter: ui.format_number },
			{ label: __("Current Net"), fieldname: "current_net_qty", formatter: ui.format_number },
			{ label: __("Delta"), fieldname: "delta_net_qty", formatter: ui.format_number },
		];
		const bufferRows = data.buffer_rows || [];
		const bufferColumns = [
			{ label: __("Item"), fieldname: "item_code", formatter: (value, buffer) => ui.item_cell(value, buffer.item_name) },
			{ label: __("Buffer"), fieldname: "stock_buffer", formatter: (value) => (value ? ui.doc_link("MRP Stock Buffer", value) : "") },
			{ label: __("Warehouse"), fieldname: "warehouse" },
			{ label: __("Priority"), fieldname: "buffer_priority", formatter: (value, buffer) => ui.code_badge(value || buffer.planning_priority, { kind: "warning" }) },
			{ label: __("NFP %"), fieldname: "buffer_nfp_percent", formatter: (value) => `${ui.format_number(value, 2)}%` },
			{ label: __("Recommended Qty"), fieldname: "buffer_recommended_qty", formatter: ui.format_number },
			{ label: __("Current Net"), fieldname: "current_net_qty", formatter: ui.format_number },
			{ label: __("Planned", null, "Injection MRP"), fieldname: "current_new_supply_qty", formatter: ui.format_number },
			{ label: __("Suggested Order Date"), fieldname: "suggested_order_date", formatter: ui.format_date },
		];
		ui.open_drawer(row.name, [
			{
				title: __("Run Comparison"),
				rows: [
					{ label: __("Current Run"), html: ui.doc_link("MRP Run", row.name) },
					{ label: __("Previous Run"), html: data.previous_run ? ui.doc_link("MRP Run", data.previous_run.name) : "" },
					{ label: __("Summary"), value: Object.keys(summary).map((key) => `${__(key)}: ${summary[key]}`).join(" · ") },
				],
			},
			{
				title: __("Top Critical Buffers"),
				html: ui.mini_table_html(bufferColumns, bufferRows, __("No stock buffer data found.")),
			},
			{
				title: __("Requirement Changes"),
				html: ui.mini_table_html(comparisonColumns, data.rows || [], __("No comparable requirements found.")),
			},
		]);
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
				const result = await ui.with_busy(__("Submitting MRP job..."), () => ui.xcall(method, values));
				if (result && result.mrp_run) {
					frappe.show_alert({ message: __("MRP job queued: {0}", [result.mrp_run]), indicator: "blue" });
				}
				await load();
			},
			title,
			__("Queue", null, "Injection MRP")
		);
	}

	ui.render_actions(shell.actions, [
		{
			label: __("Forecast Prebuy"),
			action_key: "enqueue_forecast_prebuy",
			tone: "primary",
			on_click: () => open_run_dialog("injection_mrp.api.app.enqueue_forecast_prebuy", __("Run Forecast Prebuy")),
		},
		{
			label: __("Firm APS"),
			action_key: "enqueue_firm_aps_mrp",
			tone: "primary",
			on_click: () => open_run_dialog("injection_mrp.api.app.enqueue_firm_aps_mrp", __("Run Firm APS MRP")),
		},
	]);

	shell.floating_filters = false;
	shell.filters.hide();
	load();
};
