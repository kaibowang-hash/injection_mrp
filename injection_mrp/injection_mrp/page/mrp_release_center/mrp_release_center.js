frappe.pages["mrp-release-center"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("MRP Release Center"),
		single_column: true,
	});
	const ui = injection_mrp.ui;
	const shell = ui.make_shell(page, __("MRP Release Center"), __("Review proposal batches, create Material Requests, and consume prebuy commitments."));
	const filters = {};
	const pageState = { limit_start: 0, limit_page_length: 100 };
	let rows = [];
	const mrTypes = ["Purchase", "Material Transfer", "Material Issue", "Manufacture", "Subcontracting", "Customer Provided"];
	const supplyModes = ["Purchase", "Manufacture", "Subcontracting", "Customer Provided", "Material Transfer", "Supplier Supplied", "No Action"];
	const proposalActions = ["Create Material Request", "Consume Prebuy", "Review Excess Prebuy", "No Action"];
	const itemStatuses = ["Pending", "Applied", "Skipped", "Exception"];
	const columns = [
		{ label: __("Batch"), fieldname: "name", formatter: (value) => ui.doc_link("MRP Proposal Batch", value) },
		{ label: __("Run", null, "Injection MRP"), fieldname: "mrp_run", formatter: (value) => ui.doc_link("MRP Run", value) },
		{ label: __("Type"), fieldname: "proposal_type", formatter: (value) => ui.code_badge(value, { tone: value === "Firm APS" ? "green" : "blue" }) },
		{ label: __("Status"), fieldname: "status", formatter: (value) => ui.code_badge(value, { kind: "status" }) },
		{ label: __("Company"), fieldname: "company" },
		{ label: __("Items"), fieldname: "item_count", numeric: true },
		{ label: __("Total Qty"), fieldname: "total_qty", numeric: true, formatter: ui.format_number },
		{ label: __("MR Count"), fieldname: "material_request_count", numeric: true },
		{ label: __("Superseded By"), fieldname: "superseded_by_batch", formatter: (value) => (value ? ui.doc_link("MRP Proposal Batch", value) : "") },
		{ label: __("Validation"), fieldname: "validation_message" },
		{ label: __("Generated On"), fieldname: "generated_on", formatter: (value) => (value ? frappe.datetime.str_to_user(value) : "") },
		{ label: __("Applied On"), fieldname: "applied_on", formatter: (value) => (value ? frappe.datetime.str_to_user(value) : "") },
	];

	function select_html(fieldname, values, selected, readOnly) {
		if (readOnly) {
			return ui.escape(ui.translate(selected || ""));
		}
		return `
			<select class="form-control input-xs imrp-edit-input" data-field="${fieldname}">
				<option value=""></option>
				${values
					.map((value) => `<option value="${ui.escape(value)}" ${value === selected ? "selected" : ""}>${ui.escape(ui.translate(value))}</option>`)
					.join("")}
			</select>
		`;
	}

	function input_html(fieldname, value, type, readOnly) {
		return `<input class="form-control input-xs imrp-edit-input" data-field="${fieldname}" type="${type || "text"}" value="${ui.escape(value || "")}" ${readOnly ? "readonly" : ""}>`;
	}

	function constraint_text(item) {
		const parts = [];
		if (item.min_order_qty) {
			parts.push(`${__("MOQ")} ${ui.format_number(item.min_order_qty)}`);
		}
		if (item.order_multiple_qty) {
			parts.push(`${__("Multiple")} ${ui.format_number(item.order_multiple_qty)}`);
		}
		if (item.purchase_uom) {
			parts.push(item.purchase_uom);
		}
		if (item.procurement_constraint_summary) {
			parts.push(item.procurement_constraint_summary);
		}
		return parts.join(" · ");
	}

	function estimate_text(item) {
		if (!item.estimated_rate && !item.estimated_amount) {
			return "";
		}
		const currency = item.currency || "";
		const rate = item.estimated_rate ? `${__("Rate", null, "Injection MRP")} ${ui.format_number(item.estimated_rate)}` : "";
		const amount = item.estimated_amount ? `${__("Amount")} ${ui.format_number(item.estimated_amount)}` : "";
		return [currency, rate, amount].filter(Boolean).join(" · ");
	}

	function editor_row_html(item, editable, isManual) {
		const itemControl = isManual
			? input_html("item_code", item.item_code, "text", !editable)
			: `${ui.item_cell(item.item_code, item.item_name)}${input_html("item_code", item.item_code, "hidden", true)}`;
		return `
			<tr data-row-name="${ui.escape(item.name || "")}" data-manual="${isManual ? "1" : "0"}">
				<td>${itemControl}</td>
				<td>${input_html("qty", item.qty, "number", !editable)}</td>
				<td>${input_html("schedule_date", item.schedule_date, "date", !editable)}</td>
				<td>${select_html("material_request_type", mrTypes, item.material_request_type, !editable)}</td>
				<td>${select_html("supply_mode", supplyModes, item.supply_mode, !editable)}</td>
				<td>${ui.code_badge(item.commitment_type, { tone: item.commitment_type === "Prebuy" ? "orange" : "green" })}</td>
				<td>${input_html("warehouse", item.warehouse, "text", !editable)}</td>
				<td>${input_html("from_warehouse", item.from_warehouse, "text", !editable)}</td>
				<td>${input_html("customer", item.customer, "text", !editable)}</td>
				<td>${input_html("supplier", item.supplier, "text", !editable)}</td>
				<td class="imrp-readonly-cell">${ui.escape(constraint_text(item))}</td>
				<td class="imrp-readonly-cell">${item.order_excess_qty ? ui.format_number(item.order_excess_qty) : ""}</td>
				<td class="imrp-readonly-cell">${ui.escape(estimate_text(item))}</td>
				<td>${select_html("action", proposalActions, item.action, !editable)}</td>
				<td>${select_html("status", itemStatuses, item.status, !editable)}</td>
				<td>${input_html("skip_reason", item.skip_reason, "text", !editable)}</td>
				<td>
					${editable ? ui.icon_button("x", __("Skip"), { "data-imrp-skip-row": "1" }) : ""}
					${editable ? `<button type="button" class="btn btn-default btn-xs" data-imrp-restore-row="1">${__("Restore")}</button>` : ""}
				</td>
			</tr>
		`;
	}

	function proposal_editor_html(batch, items) {
		const editable = ["Draft", "Ready"].includes(batch.status) && ui.can_run_action("save_proposal_batch_items");
		return `
			<div class="imrp-release-editor">
				<div class="ia-table-toolbar">
					<div class="ia-table-count">${__("Rows: {0}", [items.length])}</div>
					<div class="ia-table-actions">
						${ui.icon_button("download", __("Export Excel"), { "data-imrp-export-items": "1" })}
						<button type="button" class="btn btn-default btn-xs" data-imrp-open-batch="1">${__("Open Proposal Batch")}</button>
						${batch.status === "Ready" && ui.can_run_action("validate_proposal_batch_for_release") ? `<button type="button" class="btn btn-default btn-xs" data-imrp-validate-batch="1">${__("Validate")}</button>` : ""}
						${editable ? `<button type="button" class="btn btn-default btn-xs" data-imrp-add-row="1">${__("Add Row")}</button>` : ""}
						${editable ? `<button type="button" class="btn btn-primary btn-xs" data-imrp-save-batch="1">${__("Save Changes")}</button>` : ""}
						${batch.status === "Ready" && ui.can_run_action("apply_proposal_batch") ? `<button type="button" class="btn btn-primary btn-xs" data-imrp-apply-batch="1">${__("Apply Proposal")}</button>` : ""}
					</div>
				</div>
				<div class="imrp-mini-table-wrap imrp-release-editor-wrap">
					<table class="ia-table imrp-mini-table imrp-release-editor-table">
						<thead>
							<tr>
								<th>${__("Item")}</th>
								<th>${__("Qty")}</th>
								<th>${__("Schedule Date")}</th>
								<th>${__("MR Type")}</th>
								<th>${__("Supply Mode")}</th>
								<th>${__("Commitment")}</th>
								<th>${__("Warehouse")}</th>
								<th>${__("Source Warehouse")}</th>
								<th>${__("Customer")}</th>
								<th>${__("Supplier")}</th>
								<th>${__("Purchase Constraints")}</th>
								<th>${__("Order Excess")}</th>
								<th>${__("Estimated")}</th>
								<th>${__("Action", null, "Injection MRP")}</th>
								<th>${__("Status")}</th>
								<th>${__("Skip Reason")}</th>
								<th>${__("Actions")}</th>
							</tr>
						</thead>
						<tbody>${items.map((item) => editor_row_html(item, editable, !item.name || !item.requirement_line)).join("")}</tbody>
					</table>
				</div>
			</div>
		`;
	}

	function collect_editor_items(drawer) {
		const payload = [];
		drawer.find(".imrp-release-editor-table tbody tr").each(function () {
			const row = { name: $(this).attr("data-row-name") || "" };
			$(this)
				.find("[data-field]")
				.each(function () {
					row[$(this).data("field")] = $(this).val();
				});
			payload.push(row);
		});
		return payload;
	}

	function bind_editor(drawer, batch, items, refresh_detail) {
		drawer.find("[data-imrp-open-batch='1']").on("click", () => frappe.set_route("Form", "MRP Proposal Batch", batch.name));
		drawer.find("[data-imrp-export-items='1']").on("click", () => {
			ui.export_rows(
				`mrp_proposal_${batch.name}`,
				[
					{ label: __("Item"), fieldname: "item_code" },
					{ label: __("Qty"), fieldname: "qty", numeric: true },
					{ label: __("Schedule Date"), fieldname: "schedule_date" },
					{ label: __("Material Request Type"), fieldname: "material_request_type" },
					{ label: __("Supply Mode"), fieldname: "supply_mode" },
					{ label: __("Commitment Type"), fieldname: "commitment_type" },
					{ label: __("Warehouse"), fieldname: "warehouse" },
					{ label: __("Source Warehouse"), fieldname: "from_warehouse" },
					{ label: __("Customer"), fieldname: "customer" },
					{ label: __("Supplier"), fieldname: "supplier" },
					{ label: __("Purchase UOM"), fieldname: "purchase_uom" },
					{ label: __("Minimum Order Qty"), fieldname: "min_order_qty", numeric: true },
					{ label: __("Order Multiple Qty"), fieldname: "order_multiple_qty", numeric: true },
					{ label: __("Order Excess Qty"), fieldname: "order_excess_qty", numeric: true },
					{ label: __("Supplier Lead Time Days"), fieldname: "supplier_lead_time_days", numeric: true },
					{ label: __("Supplier Quotation"), fieldname: "supplier_quotation" },
					{ label: __("Item Price"), fieldname: "item_price" },
					{ label: __("Estimated Rate"), fieldname: "estimated_rate", numeric: true },
					{ label: __("Estimated Amount"), fieldname: "estimated_amount", numeric: true },
					{ label: __("Currency"), fieldname: "currency" },
					{ label: __("Procurement Source"), fieldname: "procurement_source" },
					{ label: __("Procurement Constraint Summary"), fieldname: "procurement_constraint_summary" },
					{ label: __("Action", null, "Injection MRP"), fieldname: "action" },
					{ label: __("Status"), fieldname: "status" },
					{ label: __("Skip Reason"), fieldname: "skip_reason" },
				],
				items,
				{ export_title: __("MRP Proposal Items") }
			);
		});
		drawer.find("[data-imrp-add-row='1']").on("click", () => {
			drawer.find(".imrp-release-editor-table tbody").append(
				editor_row_html(
					{
						qty: 0,
						material_request_type: "Purchase",
						supply_mode: "Purchase",
						action: "Create Material Request",
						status: "Pending",
					},
					true,
					true
				)
			);
		});
		drawer.on("click", "[data-imrp-skip-row='1']", function () {
			const tr = $(this).closest("tr");
			tr.find("[data-field='action']").val("No Action");
			tr.find("[data-field='status']").val("Skipped");
			if (!tr.find("[data-field='skip_reason']").val()) {
				tr.find("[data-field='skip_reason']").val(__("Skipped by planner."));
			}
		});
		drawer.on("click", "[data-imrp-restore-row='1']", function () {
			const tr = $(this).closest("tr");
			tr.find("[data-field='action']").val("Create Material Request");
			tr.find("[data-field='status']").val("Pending");
			tr.find("[data-field='skip_reason']").val("");
		});
		drawer.find("[data-imrp-save-batch='1']").on("click", async () => {
			await ui.with_busy(__("Saving proposal items..."), () =>
				ui.xcall("injection_mrp.api.app.save_proposal_batch_items", {
					batch_name: batch.name,
					items: JSON.stringify(collect_editor_items(drawer)),
				})
			);
			await refresh_detail();
			await load();
		});
		drawer.find("[data-imrp-validate-batch='1']").on("click", async () => {
			const result = await ui.with_busy(__("Validating proposal..."), () =>
				ui.xcall("injection_mrp.api.app.validate_proposal_batch_for_release", { batch_name: batch.name })
			);
			frappe.msgprint({
				title: result.valid ? __("Release Validation Passed") : __("Release Validation Blocked"),
				indicator: result.valid ? "green" : "red",
				message: ui.escape(result.validation_message || ""),
			});
			await refresh_detail();
			await load();
		});
		drawer.find("[data-imrp-apply-batch='1']").on("click", async () => {
			await ui.with_busy(__("Applying proposal..."), () => ui.xcall("injection_mrp.api.app.apply_proposal_batch", { batch_name: batch.name }));
			drawer.remove();
			await load();
		});
	}

	async function open_detail(row) {
		if (!row || !row.name) {
			return;
		}
		const data = await ui.with_busy(__("Loading proposal batch..."), () =>
			ui.xcall("injection_mrp.api.app.get_batch_detail", { batch_name: row.name })
		);
		const batch = data.batch || {};
		const items = data.items || [];
		const drawer = ui.open_drawer(batch.name, [
			{
				title: __("Proposal"),
				rows: [
					{ label: __("Run", null, "Injection MRP"), html: ui.doc_link("MRP Run", batch.mrp_run) },
					{ label: __("Type"), value: batch.proposal_type },
					{ label: __("Status"), value: batch.status },
					{ label: __("Items"), value: items.length },
					{ label: __("Total Qty"), value: ui.format_number(batch.total_qty) },
					{ label: __("Superseded By"), html: batch.superseded_by_batch ? ui.doc_link("MRP Proposal Batch", batch.superseded_by_batch) : "" },
					{ label: __("Validation"), value: batch.validation_message || "" },
				],
			},
			{
				title: __("Next Action"),
				rows: [
					{ label: __("Apply"), value: batch.status === "Ready" ? __("Use Apply Proposal to generate MR / consume prebuy.") : __("No release action required.") },
					{ label: __("Generated By"), value: batch.generated_by },
					{ label: __("Applied By"), value: batch.applied_by },
					{ label: __("Superseded Reason"), value: batch.superseded_reason || "" },
				],
			},
			{
				title: __("Proposal Items"),
				html: proposal_editor_html(batch, items),
			},
		]);
		bind_editor(drawer, batch, items, () => open_detail({ name: batch.name }));
	}

	async function load(options) {
		if (!options || !options.keepPage) {
			pageState.limit_start = 0;
		}
		const data = await ui.with_busy(__("Loading proposal batches..."), () =>
			ui.xcall("injection_mrp.api.app.get_release_center_data", { filters, ...pageState })
		);
		rows = data.batches || [];
		ui.render_cards(shell.cards, data.cards || []);
		ui.render_status(shell.status, [__("Click a batch to inspect release actions."), __("Rows: {0}", [(data.pagination || {}).total_count || rows.length])]);
		ui.render_table(shell.table, columns, rows, {
			empty: __("No proposal batches found."),
			on_row_click: open_detail,
			exportable: true,
			export_title: __("MRP Release Center"),
			export_file_name: "mrp_release_batches",
			legend_columns: [{ fieldname: "proposal_type" }, { fieldname: "status", kind: "status" }],
			pagination: data.pagination,
			on_page: (nextStart) => {
				pageState.limit_start = nextStart;
				load({ keepPage: true });
			},
		});
	}

	function apply_dialog() {
		frappe.prompt(
			[{ fieldtype: "Link", fieldname: "batch_name", label: __("Proposal Batch"), options: "MRP Proposal Batch", reqd: 1 }],
			async (values) => {
				await ui.with_busy(__("Applying proposal..."), () => ui.xcall("injection_mrp.api.app.apply_proposal_batch", values));
				await load();
			},
			__("Apply MRP Proposal"),
			__("Apply")
		);
	}

	ui.add_text_filter(shell.filters, __("Company"), "company", filters, load, "Link", "Company");
	ui.add_text_filter(shell.filters, __("Status"), "status", filters, load, "Select", "\nDraft\nReady\nApplied\nSuperseded\nExpired\nRejected\nClosed");
	ui.render_actions(shell.actions, [
		{ label: __("Apply Proposal"), action_key: "apply_proposal_batch", tone: "primary", on_click: apply_dialog },
	]);
	load();
};
