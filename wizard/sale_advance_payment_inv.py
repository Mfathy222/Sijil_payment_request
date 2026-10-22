from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tools.float_utils import float_compare

from ..models.constants import PAYMENT_REQUEST_COMPANY_IDS


class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = 'sale.advance.payment.inv'

    advance_payment_method = fields.Selection(
        selection_add=[('payment_request', 'Payment Request')],
        ondelete={'payment_request': 'set default'},
    )

    payment_request_ids = fields.Many2many(
        'sale.payment.request',
        string='Payment Requests',
        domain="[('sale_order_id', 'in', sale_order_ids)]",
        check_company=True,
        groups=(
            'Sijil_payment_request.group_payment_request_user,'
            'Sijil_payment_request.group_payment_request_supervisor'
        ),
    )

    @api.model
    def _payment_request_invoice_option_allowed(self):
        user = self.env.user
        return (
            self.env.company.id in PAYMENT_REQUEST_COMPANY_IDS
            and (
                user.has_group(
                    'Sijil_payment_request.group_payment_request_user'
                )
                or user.has_group(
                    'Sijil_payment_request.group_payment_request_supervisor'
                )
            )
        )

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        """Hide the Payment Request invoice method outside its feature scope.

        This keeps the standard Odoo invoice wizard unchanged for all other
        companies and for Reader/No Access Payment Request users.
        """
        result = super().fields_get(
            allfields=allfields,
            attributes=attributes,
        )
        method_field = result.get('advance_payment_method')
        if (
            method_field
            and 'selection' in method_field
            and not self._payment_request_invoice_option_allowed()
        ):
            method_field['selection'] = [
                option
                for option in method_field['selection']
                if option[0] != 'payment_request'
            ]
        return result

    @api.onchange('advance_payment_method')
    def _onchange_advance_payment_method_payment_request(self):
        # Reader / No Access users must never touch the restricted
        # payment_request_ids field while using the standard invoice wizard.
        if not self._payment_request_invoice_option_allowed():
            return

        if self.advance_payment_method != 'payment_request':
            self.payment_request_ids = False

    @api.onchange('sale_order_ids')
    def _onchange_sale_order_ids_payment_request(self):
        # Do not access the Payment Request relation for users who do not
        # have User/Supervisor Payment Request permissions.
        if not self._payment_request_invoice_option_allowed():
            return

        if self.payment_request_ids:
            self.payment_request_ids = self.payment_request_ids.filtered(
                lambda request: request.sale_order_id in self.sale_order_ids
            )

    def _check_payment_request_invoice_values(self):
        self.ensure_one()

        if self.advance_payment_method != 'payment_request':
            return

        if not self._payment_request_invoice_option_allowed():
            raise AccessError(_(
                'You are not allowed to create an invoice from a Payment Request.'
            ))

        self.sale_order_ids.ensure_one()
        order = self.sale_order_ids

        if not self.payment_request_ids:
            raise UserError(_('Please select at least one Payment Request.'))

        invalid_requests = self.payment_request_ids.filtered(
            lambda request: request.sale_order_id != order
        )
        if invalid_requests:
            raise UserError(_(
                'All selected Payment Requests must belong to this quotation.'
            ))

        # Reuse the shared validation used by the list/form Create Invoice
        # buttons so duplicate-invoice and invoiceable-line checks stay identical.
        self.payment_request_ids._check_can_create_payment_request_invoice()

    def create_invoices(self):
        if self.advance_payment_method == 'payment_request':
            self._check_payment_request_invoice_values()
        return super().create_invoices()

    def _create_invoices(self, sale_orders):
        self.ensure_one()

        if self.advance_payment_method != 'payment_request':
            return super()._create_invoices(sale_orders)

        self._check_payment_request_invoice_values()
        return self.payment_request_ids._create_combined_payment_request_invoice()
