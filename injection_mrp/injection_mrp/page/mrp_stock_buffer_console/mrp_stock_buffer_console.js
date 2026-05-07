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
		"Review DLT": "orange",
		"DLT Mismatch": "orange",
		"Procurement Mismatch": "orange",
		"Low Confidence": "orange",
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
		{ label: __("Suggested DLT Days"), fieldname: "suggested_dlt_days", numeric: true, formatter: (value) => (Number(value || 0) ? ui.format_number(value, 0) : "") },
		{ label: __("Suggested DLT Source"), fieldname: "suggested_dlt_source" },
		{ label: __("Suggested DLT Confidence"), fieldname: "suggested_dlt_confidence", formatter: (value) => ui.code_badge(value) },
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

	function has_value(value) {
		return value !== undefined && value !== null && value !== "";
	}

	function optional_number(value, digits) {
		return has_value(value) ? ui.format_number(value, digits) : "";
	}

	function first_value(value, fallback) {
		return has_value(value) ? value : fallback;
	}

	function detail_rows(rows) {
		return (rows || []).filter((row) => row && (has_value(row.value) || has_value(row.html)));
	}

	function recommendation_rule(row) {
		if (!row.stock_buffer) {
			return "";
		}
		const nfp = Number(row.net_flow_position || 0);
		const topYellow = Number(first_value(row.top_of_yellow, row.buffer_top_of_yellow) || 0);
		if (nfp < topYellow) {
			return __("NFP is below Top of Yellow. Recommended Qty refills to Top of Green, then applies Minimum Order Qty and Order Multiple Qty.");
		}
		return __("NFP is at or above Top of Yellow, so no top-up is recommended.");
	}

	function merge_live_buffer_state(row, state) {
		const detail = Object.assign({}, row, state || {});
		detail.stock_buffer = (state || {}).name || row.stock_buffer;
		detail.buffer_priority = detail.planning_priority || row.buffer_priority;
		detail.buffer_nfp_percent = has_value(detail.net_flow_position_percent) ? detail.net_flow_position_percent : row.buffer_nfp_percent;
		detail.buffer_recommended_qty = has_value(detail.recommended_qty) ? detail.recommended_qty : row.buffer_recommended_qty;
		detail.buffer_top_of_red = has_value(detail.top_of_red) ? detail.top_of_red : row.buffer_top_of_red;
		detail.buffer_top_of_yellow = has_value(detail.top_of_yellow) ? detail.top_of_yellow : row.buffer_top_of_yellow;
		detail.buffer_top_of_green = has_value(detail.top_of_green) ? detail.top_of_green : row.buffer_top_of_green;
		return detail;
	}

	async function open_detail(row) {
		if (!row) {
			return;
		}
		if (row.stock_buffer) {
			try {
				const state = await ui.with_busy(__("Loading stock buffer..."), () =>
					ui.xcall("injection_mrp.api.app.get_stock_buffer_chart_data", { buffer_name: row.stock_buffer })
				);
				if (state && state.name) {
					row = merge_live_buffer_state(row, state);
				}
			} catch (error) {
				frappe.show_alert({ message: __("Could not load live stock buffer values."), indicator: "orange" });
			}
		}
		const hasBuffer = Boolean(row.stock_buffer);
		const hasDltSuggestion = Number(row.suggested_dlt_days || 0) > 0 || has_value(row.suggested_dlt_source);
		const hasProcurementSuggestion =
			hasBuffer ||
			Number(row.min_order_qty || 0) > 0 ||
			Number(row.order_multiple_qty || 0) > 0 ||
			Number(row.suggested_min_order_qty || 0) > 0 ||
			Number(row.suggested_order_multiple_qty || 0) > 0;
		const drawerSections = [
			{
				title: __("Stock Buffer"),
				rows: detail_rows([
					{ label: __("Item"), html: ui.item_cell(row.item_code, row.item_name) },
					{ label: __("Item Group"), value: row.item_group || "" },
					{ label: __("Company"), value: row.company || "" },
					{ label: __("Warehouse"), value: row.warehouse || "" },
					{ label: __("Use Stock Buffer"), value: Number(row.use_stock_buffer || 0) ? __("Yes") : __("No") },
					{ label: __("Status"), html: ui.code_badge(row.status, { tone: statusTone[row.status] || "blue" }) },
					{ label: __("Buffer"), html: row.stock_buffer ? ui.doc_link("MRP Stock Buffer", row.stock_buffer) : "" },
					{ label: __("DLT Days"), value: hasBuffer ? ui.format_number(row.dlt_days, 0) : "" },
					{ label: __("Item Safety Stock"), value: optional_number(row.item_safety_stock) },
					{ label: __("Buffer Safety Stock"), value: hasBuffer ? optional_number(first_value(row.top_of_red, row.buffer_top_of_red)) : "" },
					{ label: __("ADU"), value: hasBuffer ? ui.format_number(row.adu) : "" },
					{ label: __("Last Calculated On"), value: row.last_calculated_on ? frappe.datetime.str_to_user(row.last_calculated_on) : "" },
				]),
			},
			{
				title: __("Buffer Chart"),
				html: row.stock_buffer ? ui.buffer_chart_html(row) : `<div class="ia-muted">${__("No stock buffer data found.")}</div>`,
			},
			{
				title: __("Status Details"),
				rows: detail_rows([
					{ label: __("Status"), html: ui.code_badge(row.status, { tone: statusTone[row.status] || "blue" }) },
					{ label: __("Explanation"), value: row.status_detail || "" },
					{ label: __("Conflict Details"), value: row.conflict_detail || "" },
					{ label: __("Active Buffers"), value: row.active_buffer_detail || "" },
					{ label: __("Default Buffers"), value: row.default_buffer_detail || "" },
				]),
			},
		];

		if (hasBuffer) {
			drawerSections.push({
				title: __("Net Flow Calculation"),
				rows: detail_rows([
					{ label: __("On Hand Qty"), value: optional_number(row.on_hand_qty) },
					{ label: __("Incoming DLT Qty"), value: optional_number(row.incoming_dlt_qty) },
					{ label: __("Qualified Demand Qty"), value: optional_number(row.qualified_demand_qty) },
					{ label: __("Net Flow Position"), value: optional_number(row.net_flow_position) },
					{ label: __("Formula"), value: __("On Hand Qty + Incoming DLT Qty - Qualified Demand Qty.") },
					{ label: __("NFP %"), value: `${optional_number(first_value(row.net_flow_position_percent, row.buffer_nfp_percent), 2)}%` },
					{ label: __("Top of Red"), value: optional_number(first_value(row.top_of_red, row.buffer_top_of_red)) },
					{ label: __("Top of Yellow"), value: optional_number(first_value(row.top_of_yellow, row.buffer_top_of_yellow)) },
					{ label: __("Top of Green"), value: optional_number(first_value(row.top_of_green, row.buffer_top_of_green)) },
					{ label: __("Recommended Qty"), value: optional_number(first_value(row.recommended_qty, row.buffer_recommended_qty)) },
					{ label: __("Recommendation Rule"), value: recommendation_rule(row) },
				]),
			});
		}

		if (hasBuffer || hasDltSuggestion) {
			drawerSections.push({
				title: __("DLT Suggestion"),
				rows: detail_rows([
					{ label: __("Current DLT Days"), value: optional_number(row.dlt_days, 0) },
					{ label: __("Suggested DLT Days"), value: optional_number(row.suggested_dlt_days, 0) },
					{ label: __("Suggested DLT Source"), value: row.suggested_dlt_source || "" },
					{ label: __("Source Explanation"), value: row.dlt_suggestion_detail || "" },
					{ label: __("Suggested DLT Confidence"), html: ui.code_badge(row.suggested_dlt_confidence) },
					{ label: __("Confidence Explanation"), value: row.dlt_confidence_detail || "" },
					{ label: __("Suggestions Calculated On"), value: row.suggestions_calculated_on ? frappe.datetime.str_to_user(row.suggestions_calculated_on) : "" },
					{ label: __("Suggestion Notes"), value: row.suggestion_notes || "" },
				]),
			});
		}

		if (hasProcurementSuggestion) {
			drawerSections.push({
				title: __("Procurement Constraints"),
				rows: detail_rows([
					{ label: __("Minimum Order Qty"), value: optional_number(row.min_order_qty) },
					{ label: __("Suggested Minimum Order Qty"), value: optional_number(row.suggested_min_order_qty) },
					{ label: __("Order Multiple Qty"), value: optional_number(row.order_multiple_qty) },
					{ label: __("Suggested Order Multiple Qty"), value: optional_number(row.suggested_order_multiple_qty) },
					{ label: __("Mismatch Explanation"), value: row.procurement_mismatch_detail || "" },
				]),
			});
		}

		ui.open_drawer(row.item_code, drawerSections);
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
		{
			label: __("Apply Suggestions"),
			action_key: "apply_stock_buffer_suggestions",
			on_click: () =>
				run_action("injection_mrp.api.app.apply_stock_buffer_suggestions", __("Applying stock buffer suggestions..."), {
					filters,
					item_codes: selected_item_codes(),
				}),
		},
	]);

	ui.add_text_filter(shell.filters, __("Company"), "company", filters, load, "Link", "Company");
	ui.add_text_filter(shell.filters, __("Item"), "item_code", filters, load, "Link", "Item");
	ui.add_text_filter(shell.filters, __("Item Group"), "item_group", filters, load, "Link", "Item Group");
	ui.add_text_filter(shell.filters, __("Status"), "status", filters, load, "Select", "\nActive\nMissing Buffer\nMissing Warehouse\nMissing DLT\nNeeds Refresh\nReview DLT\nDLT Mismatch\nProcurement Mismatch\nLow Confidence\nDisabled\nConflict");
	load();
};
