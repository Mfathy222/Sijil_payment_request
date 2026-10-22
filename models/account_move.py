from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    payment_request_id = fields.Many2one(
        'sale.payment.request',
        string='Payment Request',
        copy=False,
        readonly=True,
        index=True,
        check_company=True,
    )


    payment_request_ids = fields.Many2many(
        'sale.payment.request',
        'account_move_payment_request_rel',
        'move_id',
        'payment_request_id',
        string='Payment Requests',
        copy=False,
        readonly=True,
        check_company=True,
    )
