from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..models.constants import (
    PAYMENT_REQUEST_COMPANY_IDS,
    SIJIL_PAYMENT_REQUEST_COMPANY_ID,
    ZOFIRM_PAYMENT_REQUEST_COMPANY_ID,
)


class SalePaymentRequestPrintWizard(models.TransientModel):
    _name = 'sale.payment.request.print.wizard'
    _description = 'Payment Request Print Choice'

    request_ids = fields.Many2many(
        'sale.payment.request',
        string='Payment Requests',
        required=True,
        readonly=True,
    )
    request_count = fields.Integer(
        string='Selected Requests',
        compute='_compute_print_info',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        compute='_compute_print_info',
        readonly=True,
    )
    snb_visible = fields.Boolean(
        compute='_compute_print_info',
    )

    @api.depends('request_ids', 'request_ids.company_id')
    def _compute_print_info(self):
        for wizard in self:
            requests = wizard.request_ids
            companies = requests.mapped('company_id')
            company = companies[0] if len(companies) == 1 else False

            wizard.request_count = len(requests)
            wizard.company_id = company
            wizard.snb_visible = bool(
                company
                and company.id == ZOFIRM_PAYMENT_REQUEST_COMPANY_ID
            )

    def _get_requests_and_company(self):
        self.ensure_one()

        requests = self.request_ids.exists()
        if not requests:
            raise UserError(_('Please select at least one Payment Request.'))

        if not requests._has_payment_request_access():
            raise AccessError(_('You do not have access to Payment Requests.'))

        companies = requests.mapped('company_id')
        if len(companies) != 1:
            raise UserError(_(
                'Please select Payment Requests belonging to the same company.'
            ))

        company = companies[0]
        if company.id not in PAYMENT_REQUEST_COMPANY_IDS:
            raise UserError(_(
                'Payment Request printing is not enabled for this company.'
            ))

        return requests, company

    def _print_requests(self, print_type):
        requests, company = self._get_requests_and_company()

        if (
            print_type == 'audit'
            and company.id == SIJIL_PAYMENT_REQUEST_COMPANY_ID
        ):
            raise UserError(_(
                'Print SNB is not available for this company.'
            ))

        report_xmlid = (
            'Sijil_payment_request.action_report_payment_request_sijil'
            if company.id == SIJIL_PAYMENT_REQUEST_COMPANY_ID
            else 'Sijil_payment_request.action_report_payment_request'
        )

        return self.env.ref(report_xmlid).with_context(
            payment_request_print_type=print_type,
            allowed_company_ids=[company.id],
        ).report_action(requests)

    def action_print_snb(self):
        self.ensure_one()
        return self._print_requests('audit')

    def action_print_alinma(self):
        self.ensure_one()
        return self._print_requests('bookkeeping')
