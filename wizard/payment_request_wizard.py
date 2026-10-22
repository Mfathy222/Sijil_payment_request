from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class SalePaymentRequestWizard(models.TransientModel):
    _name = 'sale.payment.request.wizard'
    _description = 'Select Products for Payment Request'

    sale_order_id = fields.Many2one(
        'sale.order',
        string='Quotation / Sales Order',
        required=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        related='sale_order_id.partner_id',
        readonly=True,
    )
    line_ids = fields.One2many(
        'sale.payment.request.wizard.line',
        'wizard_id',
        string='Products',
    )

    def action_confirm(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_('Please keep at least one product before confirming.'))

        request_line_model = self.env['sale.payment.request.line']

        for wizard_line in self.line_ids:
            quotation_line = wizard_line.sale_order_line_id
            quotation_qty = quotation_line.product_uom_qty
            quotation_total = quotation_line.price_total
            rounding = wizard_line.currency_id.rounding if wizard_line.currency_id else 0.01

            if float_compare(
                wizard_line.product_uom_qty,
                0.0,
                precision_digits=6,
            ) <= 0:
                raise UserError(_(
                    'Quantity for product "%(product)s" must be greater than zero.'
                ) % {
                    'product': wizard_line.product_id.display_name,
                })

            # Quantity and Payment % are independent.
            # Never convert the remaining percentage/allocation into a smaller
            # Quantity automatically.
            if float_compare(
                wizard_line.product_uom_qty,
                quotation_qty,
                precision_digits=6,
            ) > 0:
                raise UserError(_(
                    'Quantity for product "%(product)s" cannot exceed the quotation quantity.\n'
                    'Quotation quantity: %(quotation)s'
                ) % {
                    'product': wizard_line.product_id.display_name,
                    'quotation': quotation_qty,
                })

            if (
                wizard_line.payment_percentage <= 0.0
                or wizard_line.payment_percentage > 100.0
            ):
                raise UserError(_(
                    'Payment %% for product "%(product)s" must be greater than 0 and not exceed 100.'
                ) % {
                    'product': wizard_line.product_id.display_name,
                })

            existing_lines = request_line_model.search([
                ('sale_order_line_id', '=', quotation_line.id),
            ])

            already_effective_qty = sum(
                existing_lines.mapped('product_uom_qty')
            )
            # The wizard Quantity is the selected/base quantity.  The actual
            # quantity stored on the Payment Request is its Payment % share.
            current_effective_qty = (
                wizard_line.product_uom_qty
                * wizard_line.payment_percentage
                / 100.0
            )
            total_effective_qty = (
                already_effective_qty + current_effective_qty
            )

            if float_compare(
                total_effective_qty,
                quotation_qty,
                precision_digits=6,
            ) > 0:
                remaining_allocation = max(
                    0.0,
                    quotation_qty - already_effective_qty,
                )
                raise UserError(_(
                    'Payment Request allocation for product "%(product)s" exceeds 100%%.\n'
                    'Quotation quantity: %(quotation)s\n'
                    'Already allocated quantity: %(allocated)s\n'
                    'Remaining allocation: %(remaining)s'
                ) % {
                    'product': wizard_line.product_id.display_name,
                    'quotation': quotation_qty,
                    'allocated': already_effective_qty,
                    'remaining': remaining_allocation,
                })

            already_requested_amount = sum(
                existing_lines.mapped('payment_amount')
            )
            current_requested_amount = wizard_line.payment_amount
            total_requested_amount = (
                already_requested_amount + current_requested_amount
            )

            if float_compare(
                total_requested_amount,
                quotation_total,
                precision_rounding=rounding,
            ) > 0:
                remaining_amount = max(
                    0.0,
                    quotation_total - already_requested_amount,
                )
                raise UserError(_(
                    'Payment Requests for product "%(product)s" cannot exceed the '
                    'quotation line total including tax and quantity.\n'
                    'Already requested: %(already).2f\n'
                    'Remaining available: %(remaining).2f'
                ) % {
                    'product': wizard_line.product_id.display_name,
                    'already': already_requested_amount,
                    'remaining': remaining_amount,
                })

        payment_request = self.env['sale.payment.request'].create({
            'sale_order_id': self.sale_order_id.id,
            'line_ids': [
                (0, 0, {
                    'sale_order_line_id': wizard_line.sale_order_line_id.id,
                    'product_uom_qty': (
                        wizard_line.product_uom_qty
                        * wizard_line.payment_percentage
                        / 100.0
                    ),
                    'payment_percentage': wizard_line.payment_percentage,
                })
                for wizard_line in self.line_ids
            ],
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Payment Request'),
            'res_model': 'sale.payment.request',
            'view_mode': 'form',
            'res_id': payment_request.id,
            'target': 'current',
        }


class SalePaymentRequestWizardLine(models.TransientModel):
    _name = 'sale.payment.request.wizard.line'
    _description = 'Payment Request Product Selection Line'
    _order = 'id'

    wizard_id = fields.Many2one(
        'sale.payment.request.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    sale_order_line_id = fields.Many2one(
        'sale.order.line',
        string='Quotation Line',
        required=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        related='sale_order_line_id.product_id',
        readonly=True,
    )
    quotation_product_display_name = fields.Char(
        string='Product',
        compute='_compute_quotation_product_display_name',
        readonly=True,
        help='Exact product/service label selected on the source quotation line.',
    )

    @api.depends('sale_order_line_id')
    def _compute_quotation_product_display_name(self):
        for line in self:
            sale_line = line.sale_order_line_id
            if not sale_line:
                line.quotation_product_display_name = ''
                continue

            selected_name = (
                sale_line['zofirm_product_display_name']
                if 'zofirm_product_display_name' in sale_line._fields
                else False
            )
            selected_lang = (
                sale_line['zofirm_product_display_lang']
                if 'zofirm_product_display_lang' in sale_line._fields
                else False
            )
            selected_template = (
                sale_line['zofirm_product_display_template_id']
                if 'zofirm_product_display_template_id' in sale_line._fields
                else False
            )

            if selected_name:
                line.quotation_product_display_name = selected_name
                continue

            product_template = (
                selected_template
                or (
                    sale_line.product_id.product_tmpl_id
                    if sale_line.product_id
                    else False
                )
            )
            if product_template and selected_lang:
                line.quotation_product_display_name = (
                    product_template.with_context(lang=selected_lang).display_name
                    or product_template.with_context(lang=selected_lang).name
                    or ''
                )
                continue

            line.quotation_product_display_name = (
                sale_line.product_id.display_name
                if sale_line.product_id
                else (sale_line.name or '')
            )

    quotation_qty = fields.Float(
        string='Quotation Qty',
        compute='_compute_quantity_balance',
        digits=(16, 6),
        readonly=True,
    )
    already_requested_qty = fields.Float(
        string='Allocated Qty',
        compute='_compute_quantity_balance',
        digits=(16, 6),
        readonly=True,
    )
    remaining_qty = fields.Float(
        string='Remaining Allocation',
        compute='_compute_quantity_balance',
        digits=(16, 6),
        readonly=True,
    )
    product_uom_qty = fields.Float(
        string='Quantity',
        required=True,
        digits=(16, 6),
    )
    payment_percentage = fields.Float(
        string='Payment %',
        default=0.0,
        digits=(16, 2),
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='sale_order_line_id.currency_id',
        readonly=True,
    )
    payment_untaxed_amount = fields.Monetary(
        string='Total Before Tax',
        compute='_compute_payment_untaxed_amount',
        currency_field='currency_id',
        readonly=True,
        help='Untaxed amount after applying the selected payment percentage/quantity.',
    )
    price_total = fields.Monetary(
        string='Total (Incl. Tax)',
        compute='_compute_price_total',
        currency_field='currency_id',
        readonly=True,
    )
    payment_amount = fields.Monetary(
        string='Payment Amount',
        compute='_compute_payment_amount',
        currency_field='currency_id',
        readonly=True,
    )

    @api.depends('sale_order_line_id')
    def _compute_quantity_balance(self):
        request_line_model = self.env['sale.payment.request.line']
        for line in self:
            quotation_line = line.sale_order_line_id
            if not quotation_line:
                line.quotation_qty = 0.0
                line.already_requested_qty = 0.0
                line.remaining_qty = 0.0
                continue

            existing_lines = request_line_model.search([
                ('sale_order_line_id', '=', quotation_line.id),
            ])
            # Stored Payment Request quantity already represents the
            # allocated portion (e.g. Qty 1 x 50% is stored as Qty 0.50).
            already_requested = sum(
                existing_lines.mapped('product_uom_qty')
            )
            line.quotation_qty = quotation_line.product_uom_qty
            line.already_requested_qty = already_requested
            line.remaining_qty = max(
                0.0, quotation_line.product_uom_qty - already_requested
            )

    @api.depends(
        'sale_order_line_id.price_total',
        'sale_order_line_id.product_uom_qty',
        'product_uom_qty',
    )
    def _compute_price_total(self):
        for line in self:
            quotation_line = line.sale_order_line_id
            quotation_qty = quotation_line.product_uom_qty if quotation_line else 0.0
            if not quotation_line or float_compare(
                quotation_qty, 0.0, precision_digits=6
            ) == 0:
                line.price_total = 0.0
                continue

            line.price_total = (
                quotation_line.price_total
                * line.product_uom_qty
                / quotation_qty
            )

    @api.depends(
        'sale_order_line_id.price_subtotal',
        'sale_order_line_id.product_uom_qty',
        'product_uom_qty',
    )
    def _compute_payment_untaxed_amount(self):
        for line in self:
            quotation_line = line.sale_order_line_id
            quotation_qty = quotation_line.product_uom_qty if quotation_line else 0.0

            if not quotation_line or float_compare(
                quotation_qty, 0.0, precision_digits=6
            ) == 0:
                line.payment_untaxed_amount = 0.0
                continue

            # price_subtotal = before tax and after any quotation-line discount.
            # product_uom_qty already reflects the Payment % allocation.
            line.payment_untaxed_amount = (
                quotation_line.price_subtotal
                * line.product_uom_qty
                / quotation_qty
            )

    @api.depends('price_total', 'payment_percentage')
    def _compute_payment_amount(self):
        for line in self:
            line.payment_amount = (
                line.price_total * line.payment_percentage / 100.0
            )

    def action_remove_line(self):
        self.ensure_one()
        wizard = self.wizard_id
        self.unlink()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payment Request'),
            'res_model': 'sale.payment.request.wizard',
            'view_mode': 'form',
            'res_id': wizard.id,
            'target': 'new',
        }
