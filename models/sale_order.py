from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.float_utils import float_compare
from .constants import PAYMENT_REQUEST_COMPANY_IDS


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    payment_request_ids = fields.One2many(
        'sale.payment.request',
        'sale_order_id',
        string='Payment Requests',
    )
    payment_request_count = fields.Integer(
        string='Payment Request Count',
        compute='_compute_payment_request_count',
    )

    payment_request_active_company_visible = fields.Boolean(
        string='Payment Request Active Company Visible',
        compute='_compute_payment_request_active_company_visible',
    )

    @api.depends_context('company')
    def _compute_payment_request_active_company_visible(self):
        visible = self.env.company.id in PAYMENT_REQUEST_COMPANY_IDS
        for order in self:
            order.payment_request_active_company_visible = visible

    @api.depends('payment_request_ids')
    def _compute_payment_request_count(self):
        for order in self:
            order.payment_request_count = len(order.payment_request_ids)

    def _is_payment_request_active_company(self):
        self.ensure_one()
        return self.env.company.id in PAYMENT_REQUEST_COMPANY_IDS

    def _check_payment_request_permission(self, allow_reader=False):
        user = self.env.user
        allowed = (
            user.has_group('Sijil_payment_request.group_payment_request_user')
            or user.has_group('Sijil_payment_request.group_payment_request_supervisor')
            or (
                allow_reader
                and user.has_group('Sijil_payment_request.group_payment_request_reader')
            )
        )
        if not allowed:
            raise AccessError(_('You do not have access to this Payment Request action.'))

    def action_open_payment_request_wizard(self):
        self.ensure_one()
        if not self._is_payment_request_active_company():
            return False
        self._check_payment_request_permission(allow_reader=False)

        quotation_lines = self.order_line.filtered(
            lambda line: not line.display_type and line.product_id
        )
        if not quotation_lines:
            raise UserError(_('There are no product lines available in this quotation.'))

        # Allocation is weighted by Payment %.  Quantity alone must not consume
        # a whole quotation unit when only part of that unit's value is requested.
        # Example: Qty 1 at 50% consumes 0.5 effective units.
        effective_qty_by_line = {line.id: 0.0 for line in quotation_lines}
        requested_amount_by_line = {line.id: 0.0 for line in quotation_lines}
        existing_request_lines = self.env['sale.payment.request.line'].search([
            ('sale_order_line_id', 'in', quotation_lines.ids),
        ])
        for request_line in existing_request_lines:
            sale_line_id = request_line.sale_order_line_id.id
            effective_qty_by_line[sale_line_id] = (
                effective_qty_by_line.get(sale_line_id, 0.0)
                + (request_line.product_uom_qty * request_line.payment_percentage / 100.0)
            )
            requested_amount_by_line[sale_line_id] = (
                requested_amount_by_line.get(sale_line_id, 0.0)
                + request_line.payment_amount
            )

        wizard_lines = []
        for line in quotation_lines:
            effective_requested_qty = effective_qty_by_line.get(line.id, 0.0)
            remaining_qty = max(0.0, line.product_uom_qty - effective_requested_qty)
            requested_amount = requested_amount_by_line.get(line.id, 0.0)
            rounding = line.currency_id.rounding if line.currency_id else 0.01

            quantity_fully_allocated = float_compare(
                effective_requested_qty,
                line.product_uom_qty,
                precision_digits=6,
            ) >= 0
            amount_fully_allocated = (
                line.price_total > 0
                and float_compare(
                    requested_amount,
                    line.price_total,
                    precision_rounding=rounding,
                ) >= 0
            )
            if quantity_fully_allocated or amount_fully_allocated:
                continue

            wizard_lines.append((0, 0, {
                'sale_order_line_id': line.id,
                # Quantity and Payment % are independent inputs.
                # Always start from the quotation quantity; the user explicitly
                # chooses the percentage in the same wizard.
                'product_uom_qty': 0.0,
                'payment_percentage': 0.0,
            }))

        if not wizard_lines:
            raise UserError(_(
                'All product values in this quotation have already been allocated '
                'to Payment Requests.'
            ))

        wizard = self.env['sale.payment.request.wizard'].create({
            'sale_order_id': self.id,
            'line_ids': wizard_lines,
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Payment Request'),
            'res_model': 'sale.payment.request.wizard',
            'view_mode': 'form',
            'res_id': wizard.id,
            'target': 'new',
        }

    def action_view_payment_requests(self):
        self.ensure_one()
        if not self._is_payment_request_active_company():
            return False
        self._check_payment_request_permission(allow_reader=True)
        list_view = self.env.ref(
            'Sijil_payment_request.view_Sijil_payment_request_quotation_list'
        )
        form_view = self.env.ref(
            'Sijil_payment_request.view_Sijil_payment_request_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payment Requests'),
            'res_model': 'sale.payment.request',
            'view_mode': 'list,form',
            'views': [
                (list_view.id, 'list'),
                (form_view.id, 'form'),
            ],
            'search_view_id': self.env.ref(
                'Sijil_payment_request.view_Sijil_payment_request_search'
            ).id,
            'domain': [('sale_order_id', '=', self.id)],
            'context': {
                'default_sale_order_id': self.id,
                'search_default_sale_order_id': self.id,
            },
        }
