frappe.ui.form.on("Retention Release Entry", {
	setup(frm) {
		frm.set_query("reference_name", () => {
			if (!frm.doc.reference_doctype) {
				return {};
			}

			return {
				filters: {
					company: frm.doc.company,
					docstatus: 1,
					enable_retention: 1,
				},
			};
		});
	},
});
