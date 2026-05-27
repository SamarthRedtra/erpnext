// Copyright (c) 2026, ERPNext contributors
// List indicator uses custom `status` (Pending / Converted / Cancelled) instead of Draft / Submitted.

frappe.listview_settings["Post Dated Cheques"] = {
	add_fields: ["status", "invoice_links", "invoice_links_list", "party_type", "payment_type"],
	fields: JSON.stringify([
		{ fieldname: "status_field" },
		{ fieldname: "invoice_links_list" },
		{ fieldname: "party" },
		{ fieldname: "reference_no" },
		{ fieldname: "reference_date" },
		{ fieldname: "amount" },
	]),
	has_indicator_for_draft: true,
	has_indicator_for_cancelled: true,
	formatters: {
		invoice_links_list(value, df, doc) {
			if (!value) {
				return "";
			}

			const invoice_doctype = doc.party_type === "Supplier" || doc.payment_type === "Pay"
				? "Purchase Invoice"
				: "Sales Invoice";
			const invoice_route = frappe.router.slug(invoice_doctype);

			return value
				.split(",")
				.map(v => v.trim())
				.filter(Boolean)
				.map(name => {
					if (name.startsWith("+")) {
						return `<span class="text-muted">${frappe.utils.escape_html(name)}</span>`;
					}
					const route = `/app/${invoice_route}/${encodeURIComponent(name)}`;
					return `<a class="pdc-invoice-link" href="${route}" onclick="event.stopPropagation()">${frappe.utils.escape_html(name)}</a>`;
				})
				.join(", ");
		}
	},
	get_indicator(doc) {
		const s = doc.status;
		if (!s) {
			return [__("—"), "gray", "name,!=,"];
		}
		const color = { Pending: "orange", Converted: "green", Cancelled: "grey" }[s] || "gray";
		return [__(s), color, "status,=," + s];
	},
};
