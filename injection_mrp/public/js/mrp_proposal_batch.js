frappe.ui.form.on("MRP Proposal Batch", {
	refresh(frm) {
		const locked = frm.doc.status === "Applied";
		frm.set_df_property("items", "read_only", locked ? 1 : 0);
		if (frm.doc.__islocal || frm.doc.status === "Applied") {
			return;
		}
		frm.add_custom_button(__("Apply Proposal"), () => {
			frappe.confirm(__("Create Material Requests and consume prebuy commitments for this proposal?"), () => {
				frappe.call({
					method: "injection_mrp.api.app.apply_proposal_batch",
					args: { batch_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Applying MRP proposal..."),
					callback() {
						frm.reload_doc();
					},
				});
			});
		}).addClass("btn-primary");
	},
	validate(frm) {
		(frm.doc.items || []).forEach((row) => {
			if (row.action === "No Action") {
				row.status = "Skipped";
				row.skip_reason = row.skip_reason || __("Skipped by planner.");
			}
			if (Number(row.qty || 0) <= 0) {
				row.action = "No Action";
				row.status = "Skipped";
				row.skip_reason = row.skip_reason || __("Quantity is zero.");
			}
			if (!row.requirement_line) {
				row.manual_override = 1;
			}
			if (row.original_qty && Number(row.qty || 0) !== Number(row.original_qty || 0)) {
				row.manual_override = 1;
			}
			if (row.original_schedule_date && row.schedule_date !== row.original_schedule_date) {
				row.manual_override = 1;
			}
		});
	},
});
