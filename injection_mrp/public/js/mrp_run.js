frappe.ui.form.on("MRP Run", {
	refresh(frm) {
		if (frm.doc.__islocal) {
			return;
		}
		frm.add_custom_button(__("Recalculate MRP"), () => {
			frappe.call({
				method: "injection_mrp.api.app.recalculate_mrp_run",
				args: { mrp_run: frm.doc.name },
				freeze: true,
				freeze_message: __("Calculating MRP..."),
				callback() {
					frm.reload_doc();
				},
			});
		});
		if (frm.doc.proposal_batch) {
			frm.add_custom_button(__("Open Proposal Batch"), () => {
				frappe.set_route("Form", "MRP Proposal Batch", frm.doc.proposal_batch);
			});
		}
	},
});
