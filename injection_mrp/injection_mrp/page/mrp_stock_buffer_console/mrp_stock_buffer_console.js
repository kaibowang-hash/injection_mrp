frappe.pages["mrp-stock-buffer-console"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("MRP Stock Buffer Console"),
		single_column: true,
	});
	const ui = injection_mrp.ui;
	const shell = ui.make_shell(page, __("MRP Stock Buffer Console"), __("Create, refresh and review item-driven stock buffers."));
	const filters = {};
	const pageState = { limit_start: 0, limit_page_length: 500 };
	let rows = [];
	const statusTone = {
		Active: "green",
		Disabled: "blue",
		"Missing Buffer": "orange",
		"Missing Warehouse": "red",
		"Missing DLT": "red",
		"Needs Refresh": "orange",
		Conflict: "red",
	};
	const columns = [
		{
			label: "",
			fieldname: "_select",
			exportable: false,
			formatter: (value, row) => `<input type="checkbox" data-imrp-buffer-select="${ui.escape(row.item_code || "")}">`,
		},
		{ label: __("Item"), fieldname: "item_code", formatter: (value, row) => ui.item_cell(value, row.item_name) },
		{ label: __("Item Group"), fieldname: "item_group" },
		{ label: __("Warehouse"), fieldname: "warehouse" },
		{ label: __("Company"), fieldname: "company" },
		{ label: __("Use Stock Buffer"), fieldname: "use_stock_buffer", formatter: (value) => (Number(value || 0) ? __("Yes") : __("No")) },
		{ label: __("Status"), fieldname: "status", formatter: (value) => ui.code_badge(value, { tone: statusTone[value] || "blue" }) },
		{ label: __("Buffer"), fieldname: "stock_buffer", formatter: (value) => (value ? ui.doc_link("MRP Stock Buffer", value) : "") },
		{ label: __("Buffer Priority"), fieldname: "buffer_priority", formatter: (value) => ui.code_badge(value, { kind: "warning" }) },
		{ label: __("NFP %"), fieldname: "buffer_nfp_percent", numeric: true, formatter: ui.format_number },
		{ label: __("Recommended Qty"), fieldname: "buffer_recommended_qty", numeric: true, formatter: ui.format_number },
		{ label: __("DLT Days"), fieldname: "dlt_days", numeric: true, formatter: (value) => ui.format_number(value, 0) },
		{ label: __("Last Calculated On"), fieldname: "last_calculated_on", formatter: (value) => (value ? frappe.datetime.str_to_user(value) : "") },
	];

	function selected_item_codes() {
		return shell.table
			.find("[data-imrp-buffer-select]:checked")
			.map(function () {
				return $(this).attr("data-imrp-buffer-select");
			})
			.get()
			.filter(Boolean);
	}

	async function run_action(method, message, args) {
		const result = await ui.with_busy(message, () => ui.xcall(method, args || {}));
		frappe.show_alert({
			message: __("Done. Updated: {0}", [
				ui.format_number(result.updated || result.created || result.refreshed || result.count || 0, 0),
			]),
			indicator: result.failed ? "orange" : "green",
		});
		await load({ keepPage: true });
	}

	async function open_detail(row) {
		if (!row) {
			return;
		}
		ui.open_drawer(row.item_code, [
			{
				title: __("Stock Buffer"),
				rows: [
					{ label: __("Item"), html: ui.item_cell(row.item_code, row.item_name) },
					{ label: __("Item Group"), value: row.item_group || "" },
					{ label: __("Company"), value: row.company || "" },
					{ label: __("Warehouse"), value: row.warehouse || "" },
					{ label: __("Use Stock Buffer"), value: Number(row.use_stock_buffer || 0) ? __("Yes") : __("No") },
					{ label: __("Status"), html: ui.code_badge(row.status, { tone: statusTone[row.status] || "blue" }) },
					{ label: __("Buffer"), html: row.stock_buffer ? ui.doc_link("MRP Stock Buffer", row.stock_buffer) : "" },
					{ label: __("DLT Days"), value: ui.format_number(row.dlt_days, 0) },
					{ label: __("ADU"), value: ui.format_number(row.adu) },
					{ label: __("Last Calculated On"), value: row.last_calculated_on ? frappe.datetime.str_to_user(row.last_calculated_on) : "" },
				],
			},
			{
				title: __("Buffer Summary"),
				html: row.stock_buffer ? ui.buffer_chart_html(row) : `<div class="ia-muted">${__("No stock buffer data found.")}</div>`,
			},
		]);
	}

	async function load(options) {
		if (!options || !options.keepPage) {
			pageState.limit_start = 0;
		}
		const data = await ui.with_busy(__("Loading stock buffers..."), () =>
			ui.xcall("injection_mrp.api.app.get_stock_buffer_console_data", { filters, ...pageState })
		);
		rows = data.rows || [];
		ui.render_cards(shell.cards, data.cards || []);
		ui.render_status(shell.status, [__("Stock buffer automation"), __("Rows: {0}", [(data.pagination || {}).total_count || rows.length])]);
		ui.render_table(shell.table, columns, rows, {
			empty: __("No stock buffer rows found."),
			on_row_click: open_detail,
			exportable: true,
			export_title: __("MRP Stock Buffer Console"),
			export_file_name: "mrp_stock_buffers",
			legend_columns: [{ fieldname: "status" }, { fieldname: "buffer_priority", kind: "warning" }],
			pagination: data.pagination,
			on_page: (nextStart) => {
				pageState.limit_start = nextStart;
				load({ keepPage: true });
			},
		});
		shell.table.find("[data-imrp-buffer-select]").on("click", (event) => event.stopPropagation());
	}

	ui.render_actions(shell.actions, [
		{
			label: __("Create Missing Buffers"),
			action_key: "create_missing_stock_buffers",
			tone: "primary",
			on_click: () =>
				run_action("injection_mrp.api.app.create_missing_stock_buffers", __("Creating missing stock buffers..."), {
					filters,
					item_codes: selected_item_codes(),
				}),
		},
		{
			label: __("Refresh Buffers"),
			action_key: "recalculate_stock_buffers",
			on_click: () => {
				const selected = selected_item_codes();
				const args = { company: filters.company };
				if (selected.length) {
					args.item_codes = selected;
				} else {
					args.filters = filters;
				}
				return run_action("injection_mrp.api.app.recalculate_stock_buffers", __("Refreshing stock buffers..."), args);
			},
		},
		{
			label: __("Apply Group Defaults"),
			action_key: "apply_stock_buffer_item_group_defaults",
			on_click: () =>
				run_action("injection_mrp.api.app.apply_stock_buffer_item_group_defaults", __("Applying stock buffer defaults..."), {
					filters,
					item_codes: selected_item_codes(),
				}),
		},
	]);

	ui.add_text_filter(shell.filters, __("Company"), "company", filters, load, "Link", "Company");
	ui.add_text_filter(shell.filters, __("Item"), "item_code", filters, load, "Link", "Item");
	ui.add_text_filter(shell.filters, __("Item Group"), "item_group", filters, load, "Link", "Item Group");
	ui.add_text_filter(shell.filters, __("Status"), "status", filters, load, "Select", "\nActive\nMissing Buffer\nMissing Warehouse\nMissing DLT\nNeeds Refresh\nDisabled\nConflict");
	load();
};
