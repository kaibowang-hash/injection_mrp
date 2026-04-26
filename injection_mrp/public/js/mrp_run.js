frappe.ui.form.on("MRP Run", {
	refresh(frm) {
		if (frm.doc.__islocal) {
			return;
		}
		if (frm.__imrp_refresh_timer) {
			clearTimeout(frm.__imrp_refresh_timer);
			frm.__imrp_refresh_timer = null;
		}
		frm.add_custom_button(__("Recalculate MRP"), () => {
			frappe.call({
				method: "injection_mrp.api.app.enqueue_recalculate_mrp_run",
				args: { mrp_run: frm.doc.name },
				freeze: true,
				freeze_message: __("Submitting MRP job..."),
				callback(response) {
					if (response.message && response.message.mrp_run) {
						frappe.show_alert({ message: __("MRP job queued: {0}", [response.message.mrp_run]), indicator: "blue" });
					}
					frm.reload_doc();
				},
			});
		});
		if (frm.doc.proposal_batch) {
			frm.add_custom_button(__("Open Proposal Batch"), () => {
				frappe.set_route("Form", "MRP Proposal Batch", frm.doc.proposal_batch);
			});
		}
		if (["Queued", "Running"].includes(frm.doc.status)) {
			frm.__imrp_refresh_timer = setTimeout(() => frm.reload_doc(), 5000);
		}
	},
});
