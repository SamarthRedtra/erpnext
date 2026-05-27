frappe.query_reports["Retention Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "invoice_type",
			label: __("Invoice Type"),
			fieldtype: "Select",
			options: "\nSales Invoice\nPurchase Invoice",
			on_change: function () {
				const invoice_type = frappe.query_report.get_filter_value("invoice_type");
				frappe.query_report.set_filter_value(
					"party_type",
					invoice_type === "Purchase Invoice" ? "Supplier" : "Customer"
				);
				frappe.query_report.set_filter_value("party", "");
			},
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			fieldname: "party",
			label: __("Party"),
			fieldtype: "Dynamic Link",
			options: "party_type",
		},
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Select",
			options: "\nCustomer\nSupplier",
			hidden: 1,
			default: "Customer",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "release_date",
			label: __("Release Due By"),
			fieldtype: "Date",
		},
	],
};
