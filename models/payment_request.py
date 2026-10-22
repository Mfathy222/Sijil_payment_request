from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare
from odoo.fields import Command
from .constants import PAYMENT_REQUEST_COMPANY_IDS, SIJIL_PAYMENT_REQUEST_COMPANY_ID, ZOFIRM_PAYMENT_REQUEST_COMPANY_ID


class SalePaymentRequest(models.Model):
    _name = 'sale.payment.request'
    _description = 'Sale Payment Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _check_company_auto = True
    _order = 'create_date desc, id desc'

    name = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Quotation / Sales Order',
        required=True,
        index=True,
        ondelete='cascade',
        check_company=True,
        readonly=True,
    )
    import_sale_order_ref = fields.Char(
        string='Import Quotation',
        compute='_compute_import_sale_order_ref',
        inverse='_inverse_import_sale_order_ref',
        store=True,
        copy=False,
        help='Import/export helper: quotation/order reference such as S00006.',
    )

    @api.depends('sale_order_id')
    def _compute_import_sale_order_ref(self):
        for request in self:
            request.import_sale_order_ref = request.sale_order_id.name or ''

    def _inverse_import_sale_order_ref(self):
        # Import values are resolved by _prepare_named_product_import_vals()
        # before create/write. No direct inverse write is required.
        return
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        related='sale_order_id.partner_id',
        store=True,
        readonly=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
    )
    status = fields.Selection(
        [
            ('unpaid', 'Not Paid'),
            ('partial', 'Partially Paid'),
            ('paid', 'Paid'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status',
        required=True,
        default='unpaid',
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        related='sale_order_id.company_id',
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='sale_order_id.currency_id',
        store=True,
        readonly=True,
    )
    line_ids = fields.One2many(
        'sale.payment.request.line',
        'payment_request_id',
        string='Products',
        copy=True,
        help=(
            'Product lines of the Payment Request. Native Odoo import can '
            'populate these lines using Products / Quotation Line, '
            'Products / Quantity, Products / Payment %, Products / Due Date, '
            'and Products / Payment Status.'
        ),
    )
    # due_date = fields.Date(
    #     string='Due Date',
    #     compute='_compute_due_date',
    #     store=True,
    #     help='Latest due date among the Payment Request product lines.',
    #     tracking=True,
    # )
    
    due_date = fields.Date(
        string='Due Date',
        store=True,
        help='Latest due date among the Payment Request product lines.',
        tracking=True,
    )
        
    total_request_amount = fields.Monetary(
        string='Requested Amount',
        currency_field='currency_id',
        compute='_compute_total_request_amount',
        store=True,
    )
    report_untaxed_amount = fields.Monetary(
        string='Payment Value',
        currency_field='currency_id',
        compute='_compute_report_amounts',
    )
    report_tax_amount = fields.Monetary(
        string='Payment VAT',
        currency_field='currency_id',
        compute='_compute_report_amounts',
    )
    quotation_request_number = fields.Integer(
        string='Quotation Payment No.',
        compute='_compute_quotation_request_number',
        help='Sequential Payment Request number inside the same quotation/order.',
    )

    invoice_ids = fields.Many2many(
        'account.move',
        string='Invoices',
        compute='_compute_invoice_ids',
        readonly=True,
    )
    invoice_count = fields.Integer(
        string='Invoice Count',
        compute='_compute_invoice_count',
    )

    snb_print_visible = fields.Boolean(
        string='SNB Print Visible',
        compute='_compute_snb_print_visible',
    )

    @api.depends_context('company')
    def _compute_snb_print_visible(self):
        active_company_id = self.env.company.id
        for request in self:
            request.snb_print_visible = (
                active_company_id != SIJIL_PAYMENT_REQUEST_COMPANY_ID
            )


    def _compute_invoice_ids(self):
        if not self:
            return

        invoices = self.env['account.move'].search([
            '|',
            ('payment_request_id', 'in', self.ids),
            ('payment_request_ids', 'in', self.ids),
        ])
        for request in self:
            request.invoice_ids = invoices.filtered(
                lambda invoice: (
                    invoice.payment_request_id == request
                    or request in invoice.payment_request_ids
                )
            )

    def _compute_invoice_count(self):
        for request in self:
            request.invoice_count = len(request.invoice_ids.filtered(
                lambda invoice: invoice.state != 'cancel'
            ))

    @api.depends('sale_order_id', 'create_date')
    def _compute_quotation_request_number(self):
        """Number Payment Requests from 1 independently for each quotation.

        This is intentionally computed from creation order so existing records
        also receive the correct per-quotation number after module upgrade.
        The global ``name`` (PR/xxxxx) remains unchanged and unique system-wide.
        """
        rank_by_request_id = {}
        for order in self.mapped('sale_order_id'):
            request_ids = self.search(
                [('sale_order_id', '=', order.id)],
                order='create_date asc, id asc',
            ).ids
            rank_by_request_id.update({
                request_id: position
                for position, request_id in enumerate(request_ids, start=1)
            })

        for request in self:
            request.quotation_request_number = rank_by_request_id.get(request.id, 0)

    @api.depends('line_ids.due_date')
    def _compute_due_date(self):
        for request in self:
            due_dates = [date for date in request.line_ids.mapped('due_date') if date]
            request.due_date = max(due_dates) if due_dates else False

    @api.depends('line_ids.payment_amount')
    def _compute_total_request_amount(self):
        for request in self:
            request.total_request_amount = sum(request.line_ids.mapped('payment_amount'))

    @api.depends(
        'line_ids.sale_order_line_id.price_subtotal',
        'line_ids.sale_order_line_id.price_total',
        'line_ids.sale_order_line_id.product_uom_qty',
        'line_ids.product_uom_qty',
        'line_ids.payment_percentage',
    )
    
    def _compute_report_amounts(self):
        """Compute the requested installment value and tax for the PDF.

        The values are proportional to both the quantity selected in the
        Payment Request and the line's Payment %, so they represent this
        request only - not the whole quotation.
        """
        for request in self:
            untaxed_amount = 0.0
            tax_amount = 0.0
            for line in request.line_ids:
                sale_line = line.sale_order_line_id
                quotation_qty = sale_line.product_uom_qty if sale_line else 0.0
                if not sale_line or float_compare(
                    quotation_qty, 0.0, precision_digits=6
                ) == 0:
                    continue

                # The stored quantity already includes the Payment %
                # allocation, so use only the quantity ratio here.
                quantity_ratio = line.product_uom_qty / quotation_qty
                line_untaxed = sale_line.price_subtotal * quantity_ratio
                line_total = sale_line.price_total * quantity_ratio
                untaxed_amount += line_untaxed
                tax_amount += line_total - line_untaxed

            request.report_untaxed_amount = untaxed_amount
            request.report_tax_amount = tax_amount

    def action_view_payment_request_invoices(self):
        self.ensure_one()

        invoices = self.invoice_ids.filtered(
            lambda invoice: invoice.state != 'cancel'
        )

        if not invoices:
            return False

        # One active invoice: open it directly.
        if len(invoices) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Invoice'),
                'res_model': 'account.move',
                'view_mode': 'form',
                'res_id': invoices.id,
                'target': 'current',
            }

        # Multiple active invoices: show only invoices linked to this request.
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoices'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', invoices.ids)],
            'context': {
                'create': False,
                'default_move_type': 'out_invoice',
            },
            'target': 'current',
        }


    def _has_payment_request_access(self):
        user = self.env.user
        return any((
            user.has_group('Sijil_payment_request.group_payment_request_user'),
            user.has_group('Sijil_payment_request.group_payment_request_supervisor'),
            user.has_group('Sijil_payment_request.group_payment_request_reader'),
        ))

    def _check_can_create_payment_request_invoice(self):
        """Validate a selection before creating one combined draft invoice."""
        if not self:
            raise ValidationError(_('Please select at least one Payment Request.'))

        if self.env.company.id not in PAYMENT_REQUEST_COMPANY_IDS:
            return False

        cancelled_requests = self.filtered(
            lambda request: request.status == 'cancelled'
        )
        if cancelled_requests:
            raise ValidationError(_(
                'Cancelled Payment Requests cannot be invoiced: %(requests)s'
            ) % {
                'requests': ', '.join(
                    cancelled_requests.mapped(
                        lambda request: str(
                            request.quotation_request_number or request.name
                        )
                    )
                ),
            })

        user = self.env.user
        if not (
            user.has_group('Sijil_payment_request.group_payment_request_user')
            or user.has_group('Sijil_payment_request.group_payment_request_supervisor')
        ):
            raise AccessError(_('You are not allowed to create Payment Request invoices.'))

        sale_orders = self.mapped('sale_order_id')
        if len(sale_orders) != 1:
            raise ValidationError(_(
                'All selected Payment Requests must belong to the same Quotation / Sales Order.'
            ))

        already_invoiced = self.filtered(
            lambda request: request.invoice_ids.filtered(
                lambda invoice: invoice.state != 'cancel'
            )
        )
        if already_invoiced:
            raise ValidationError(_(
                'The following Payment Requests already have an active invoice: %(requests)s'
            ) % {
                'requests': ', '.join(
                    already_invoiced.mapped(
                        lambda request: str(request.quotation_request_number or request.name)
                    )
                ),
            })

        invalid_requests = self.filtered(
            lambda request: not request.line_ids.filtered(
                lambda line: (
                    line.product_id
                    and line.product_uom_qty > 0
                    and line.payment_percentage > 0
                    and line.payment_amount > 0
                )
            )
        )
        if invalid_requests:
            raise ValidationError(_(
                'The following Payment Requests have no invoiceable line with a positive Payment %%: %(requests)s'
            ) % {
                'requests': ', '.join(
                    invalid_requests.mapped(
                        lambda request: str(request.quotation_request_number or request.name)
                    )
                ),
            })

        return sale_orders

    def _create_combined_payment_request_invoice(self):
        """Create one draft invoice from one or more Payment Requests.

        Every selected request must belong to the same quotation.  Each
        Payment Request line becomes an invoice line using:
        - the Payment Request quantity;
        - the original sale-order product/taxes/discount;
        - price_unit multiplied by Payment %.
        """
        sale_orders = self._check_can_create_payment_request_invoice()
        if sale_orders is False:
            return self.env['account.move']

        order = sale_orders.ensure_one().with_company(sale_orders.company_id)
        report_lang = (
            order.partner_invoice_id.lang
            or order.partner_id.lang
            or self.env.lang
        )

        invoice_vals = order.with_context(lang=report_lang)._prepare_invoice()

        # Backward-compatible first request link + complete M2M linkage.
        first_request = self.sorted(
            key=lambda request: (
                request.create_date or fields.Datetime.from_string('1970-01-01 00:00:00'),
                request.id,
            )
        )[:1]
        invoice_vals.update({
            'payment_request_id': first_request.id if first_request else False,
            'payment_request_ids': [Command.set(self.ids)],
        })

        invoice_line_commands = []
        sequence = 1

        ordered_requests = self.sorted(
            key=lambda request: (
                request.create_date or fields.Datetime.from_string('1970-01-01 00:00:00'),
                request.id,
            )
        )

        for payment_request in ordered_requests:
            payable_lines = payment_request.line_ids.filtered(
                lambda line: (
                    line.product_id
                    and line.product_uom_qty > 0
                    and line.payment_percentage > 0
                    and line.payment_amount > 0
                )
            )

            for request_line in payable_lines:
                sale_line = request_line.sale_order_line_id.with_context(
                    lang=report_lang
                )

                # Payment Request quantity already contains the
                # Payment % allocation. Keep the original sale unit price.
                line_vals = sale_line._prepare_invoice_line(
                    quantity=request_line.product_uom_qty,
                    price_unit=sale_line.price_unit,
                    sequence=sequence,
                )

                invoice_line_commands.append(Command.create(line_vals))
                sequence += 1

        invoice_vals['invoice_line_ids'] = invoice_line_commands

        invoices = order._create_account_invoices(
            [invoice_vals],
            final=False,
        )
        invoice = invoices[:1]
        if not invoice:
            raise ValidationError(_('The Payment Request invoice could not be created.'))

        # Put the same draft invoice link in every selected Payment Request chatter.
        title = _('Draft Invoice')
        for payment_request in ordered_requests:
            payment_request.message_post(
                body=_(
                    '%(invoice)s has been created from this Payment Request.',
                    invoice=invoice._get_html_link(title=title),
                ),
                subtype_xmlid='mail.mt_note',
            )

            invoice.message_post(
                body=_(
                    'Created from Payment Request %(payment_request)s.',
                    payment_request=payment_request._get_html_link(),
                ),
                subtype_xmlid='mail.mt_note',
            )

        return invoices

    def action_create_invoice_from_selected_requests(self):
        """List-view button: create one draft invoice from selected requests."""
        invoices = self._create_combined_payment_request_invoice()
        if not invoices:
            return False

        invoice = invoices[:1]
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Invoice'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': invoice.id,
            'target': 'current',
            'context': {
                'default_move_type': 'out_invoice',
            },
        }



    def _check_payment_request_state_supervisor(self):
        if not self.env.user.has_group(
            'Sijil_payment_request.group_payment_request_supervisor'
        ):
            raise AccessError(_(
                'Only a Payment Request Supervisor can cancel or reset Payment Requests.'
            ))

    def action_cancel_payment_request(self):
        """Cancel the Payment Request when it has no active invoice."""
        self._check_payment_request_state_supervisor()
        invoiced_requests = self.filtered(
            lambda request: request.invoice_ids.filtered(
                lambda invoice: invoice.state != 'cancel'
            )
        )
        if invoiced_requests:
            raise UserError(_(
                'You cannot cancel Payment Request(s) %(requests)s because '
                'an active invoice already exists.'
            ) % {
                'requests': ', '.join(invoiced_requests.mapped('name')),
            })
        requests_to_cancel = self.filtered(
            lambda request: request.status != 'cancelled'
        )
        if requests_to_cancel:
            requests_to_cancel.write({'status': 'cancelled'})
        return True

    def action_set_to_pr(self):
        """Restore a cancelled Payment Request to its initial PR state."""
        self._check_payment_request_state_supervisor()
        cancelled_requests = self.filtered(
            lambda request: request.status == 'cancelled'
        )
        if cancelled_requests:
            cancelled_requests.write({'status': 'unpaid'})
        return True


    def unlink(self):
        user = self.env.user

        # Payment Request access level has priority over the user's global
        # Odoo Role (User / Administrator).  A Payment Request User or Reader
        # is never allowed to delete a Payment Request.
        if (
            user.has_group('Sijil_payment_request.group_payment_request_user')
            or user.has_group('Sijil_payment_request.group_payment_request_reader')
        ):
            raise AccessError(_(
                'Payment Request User and Reader permissions do not allow deleting Payment Requests.'
            ))

        if not user.has_group(
            'Sijil_payment_request.group_payment_request_supervisor'
        ):
            raise AccessError(_(
                'Only a Payment Request Supervisor can delete Payment Requests.'
            ))

        # Keep the existing invoice protection for Supervisor users.
        invoiced_requests = self.filtered(
            lambda request: request.invoice_ids.filtered(
                lambda invoice: invoice.state != 'cancel'
            )
        )
        if invoiced_requests:
            request_names = ', '.join(
                invoiced_requests.mapped('name')
            )
            raise UserError(_(
                'You cannot delete Payment Request(s) %(requests)s because '
                'an invoice has already been created from them. '
                'Cancel the related invoice first if you need to remove the Payment Request.'
            ) % {
                'requests': request_names,
            })

        return super().unlink()


    def action_print_audit_payment_request(self):
        self.ensure_one()
        if self.env.company.id not in PAYMENT_REQUEST_COMPANY_IDS:
            return False
        if self.env.company.id == SIJIL_PAYMENT_REQUEST_COMPANY_ID:
            return False
        if not self._has_payment_request_access():
            raise AccessError(_('You do not have access to Payment Requests.'))

        report_xmlid = (
            'Sijil_payment_request.action_report_payment_request_sijil'
            if self.company_id.id == SIJIL_PAYMENT_REQUEST_COMPANY_ID
            else 'Sijil_payment_request.action_report_payment_request'
        )
        return self.env.ref(report_xmlid).with_context(
            payment_request_print_type='audit'
        ).report_action(self)

    def action_print_bookeeping_payment_request(self):
        self.ensure_one()
        if self.env.company.id not in PAYMENT_REQUEST_COMPANY_IDS:
            return False
        if not self._has_payment_request_access():
            raise AccessError(_('You do not have access to Payment Requests.'))

        report_xmlid = (
            'Sijil_payment_request.action_report_payment_request_sijil'
            if self.company_id.id == SIJIL_PAYMENT_REQUEST_COMPANY_ID
            else 'Sijil_payment_request.action_report_payment_request'
        )
        return self.env.ref(report_xmlid).with_context(
            payment_request_print_type='bookkeeping'
        ).report_action(self)

    @api.model
    def _normalize_payment_request_import_text(self, value):
        """Normalize exported labels before matching them back to quotation lines."""
        return ' '.join(
            str(value or '').replace('\xa0', ' ').split()
        ).strip().casefold()

    @api.model
    def _resolve_import_sale_order_line_id(self, value):
        """Resolve an exported Quotation Line label to ``sale.order.line``.

        Native Odoo export may produce values such as::

            S00003 - Virtual Interior Design (Ready Mat)

        That string is a display label, not the actual ``sale.order.line`` name,
        so the standard importer cannot always match it.  Resolve it inside the
        referenced quotation and return the database id used by the importer.
        """
        raw = str(value or '').strip()
        if not raw:
            return False

        # Already a database id.
        if raw.isdigit():
            record = self.env['sale.order.line'].browse(int(raw)).exists()
            return record.id if record else False

        normalize = self._normalize_payment_request_import_text
        imported_full = normalize(raw)

        order = False
        exported_detail = raw
        if ' - ' in raw:
            order_name, exported_detail = raw.split(' - ', 1)
            order = self.env['sale.order'].search([
                ('name', '=', order_name.strip()),
            ], limit=1)

        # If the export did not contain the quotation reference, search all
        # active-company quotation lines as a fallback.  A unique match is
        # required in this case.
        candidates = (
            order.order_line.filtered(lambda line: not line.display_type)
            if order
            else self.env['sale.order.line'].search([
                ('display_type', '=', False),
                ('company_id', 'in', self.env.companies.ids),
            ])
        )

        imported_detail = normalize(exported_detail)
        detail_without_partner = exported_detail.strip()

        partner_labels = []
        if order and order.partner_id:
            partner_labels = list(filter(None, [
                order.partner_id.display_name,
                order.partner_id.name,
            ]))

        for partner_label in partner_labels:
            suffix = f' ({partner_label})'
            if detail_without_partner.endswith(suffix):
                detail_without_partner = detail_without_partner[:-len(suffix)].strip()
                break

        imported_core = normalize(detail_without_partner)
        matches = self.env['sale.order.line']

        for line in candidates.sorted(key=lambda rec: (rec.sequence, rec.id)):
            product = line.product_id
            line_name = line.name or ''
            first_line_name = line_name.splitlines()[0] if line_name else ''
            entered_name = getattr(line, 'zofirm_product_display_name', False) or ''

            labels = {
                normalize(line.display_name or ''),
                normalize(line_name),
                normalize(first_line_name),
                normalize(entered_name),
                normalize(product.display_name if product else ''),
                normalize(product.name if product else ''),
            }

            # Include common translated product labels too.  This makes an
            # exported file reusable even if the current Odoo UI language is
            # different from the language used when the file was exported.
            if product:
                for lang in ('en_US', 'ar_001'):
                    labels.add(normalize(product.with_context(lang=lang).display_name))
                    labels.add(normalize(product.with_context(lang=lang).name))

            order_ref = line.order_id.name or ''
            partner_name = (
                line.order_id.partner_id.display_name
                or line.order_id.partner_id.name
                or ''
            )
            base_labels = [
                first_line_name,
                entered_name,
                product.display_name if product else '',
                product.name if product else '',
            ]

            for label in filter(None, base_labels):
                labels.add(normalize(f'{order_ref} - {label}'))
                if partner_name:
                    labels.add(normalize(f'{order_ref} - {label} ({partner_name})'))

            if (
                imported_full in labels
                or imported_detail in labels
                or imported_core in labels
            ):
                matches |= line

        # With an order reference, the first matching line is deterministic.
        # Without an order reference, only accept a unique match.
        if order:
            return matches[:1].id if matches else False
        return matches.id if len(matches) == 1 else False


    @api.model
    def _resolve_import_sale_order(self, value):
        """Resolve Quotation / Sales Order from an import cell."""
        raw = str(value or '').strip()
        if not raw:
            return self.env['sale.order']

        if raw.isdigit():
            order = self.env['sale.order'].browse(int(raw)).exists()
            if order:
                return order

        order = self.env['sale.order'].search([
            ('name', '=', raw),
        ], limit=1)
        if order:
            return order

        # Fallback for values exported as display labels.
        normalized = self._normalize_payment_request_import_text(raw)
        for candidate in self.env['sale.order'].search([
            ('company_id', 'in', self.env.companies.ids),
        ]):
            if self._normalize_payment_request_import_text(candidate.display_name) == normalized:
                return candidate

        return self.env['sale.order']

    @api.model
    def _get_import_sale_order_line_matches(self, order, value):
        """Return quotation lines matching a product/service name."""
        if not order:
            return self.env['sale.order.line']

        raw = str(value or '').strip()
        if not raw:
            return self.env['sale.order.line']

        normalize = self._normalize_payment_request_import_text
        imported = normalize(raw)
        imported_no_ref = imported
        if raw.startswith('[') and '] ' in raw:
            imported_no_ref = normalize(raw.split('] ', 1)[1])

        matches = self.env['sale.order.line']
        candidates = order.order_line.filtered(lambda line: not line.display_type)

        for line in candidates.sorted(key=lambda rec: (rec.sequence, rec.id)):
            product = line.product_id
            line_name = line.name or ''
            first_line_name = line_name.splitlines()[0] if line_name else ''
            entered_name = getattr(line, 'zofirm_product_display_name', False) or ''

            labels = {
                normalize(line_name),
                normalize(first_line_name),
                normalize(entered_name),
            }

            if product:
                product_labels = set()
                for candidate in (product.name, product.display_name):
                    if candidate:
                        product_labels.add(candidate)

                for lang in ('en_US', 'ar_001'):
                    translated = product.with_context(lang=lang)
                    if translated.name:
                        product_labels.add(translated.name)
                    if translated.display_name:
                        product_labels.add(translated.display_name)

                for label in product_labels:
                    labels.add(normalize(label))
                    if label.startswith('[') and '] ' in label:
                        labels.add(normalize(label.split('] ', 1)[1]))

            if imported in labels or imported_no_ref in labels:
                matches |= line

        return matches

    @api.model
    def _prepare_named_product_import_vals(self, vals, existing_request=False):
        """Resolve import helper text into real quotation relations."""
        vals = dict(vals)

        raw_order = vals.pop('import_sale_order_ref', False)
        order = self.env['sale.order']

        if vals.get('sale_order_id'):
            order = self.env['sale.order'].browse(vals['sale_order_id']).exists()
        elif raw_order:
            order = self._resolve_import_sale_order(raw_order)
            if not order:
                raise ValidationError(_(
                    'Quotation / Sales Order "%(quotation)s" was not found.'
                ) % {'quotation': raw_order})
            vals['sale_order_id'] = order.id
        elif existing_request:
            order = existing_request.sale_order_id

        line_commands = vals.get('line_ids') or []
        if line_commands:
            prepared_commands = []
            occurrence_by_name = {}

            for command in line_commands:
                if (
                    not isinstance(command, (list, tuple))
                    or len(command) < 3
                    or not isinstance(command[2], dict)
                ):
                    prepared_commands.append(command)
                    continue

                operation = command[0]
                record_id = command[1]
                line_vals = dict(command[2])
                raw_product_name = line_vals.pop('import_product_name', False)

                if raw_product_name and not line_vals.get('sale_order_line_id'):
                    if not order:
                        raise ValidationError(_(
                            'Quotation / Sales Order is required before product '
                            '"%(product)s" can be matched.'
                        ) % {'product': raw_product_name})

                    matches = self._get_import_sale_order_line_matches(
                        order, raw_product_name
                    )
                    if not matches:
                        raise ValidationError(_(
                            'Product/service "%(product)s" was not found in '
                            'Quotation / Sales Order "%(quotation)s".'
                        ) % {
                            'product': raw_product_name,
                            'quotation': order.name,
                        })

                    key = self._normalize_payment_request_import_text(
                        raw_product_name
                    )
                    occurrence = occurrence_by_name.get(key, 0)
                    if occurrence >= len(matches):
                        raise ValidationError(_(
                            'Product/service "%(product)s" appears more times in '
                            'the import than matching quotation lines exist in '
                            '"%(quotation)s".'
                        ) % {
                            'product': raw_product_name,
                            'quotation': order.name,
                        })

                    selected_line = matches[occurrence]
                    occurrence_by_name[key] = occurrence + 1
                    line_vals['sale_order_line_id'] = selected_line.id

                prepared_commands.append((operation, record_id, line_vals))

            vals['line_ids'] = prepared_commands

        if not vals.get('sale_order_id') and vals.get('line_ids'):
            self._derive_sale_order_id_from_import_lines(vals)

        return vals

    @api.model
    def _resolve_import_sale_order_line_from_product_name(self, order, value):
        """Resolve a quotation line using only the product/service name.

        The imported Excel may contain only a value such as::

            Financial Statement Audit

        together with ``Quotation / Sales Order = S01941``.  Match the name
        against the quotation's product lines, including the custom
        ``zofirm_product_display_name`` and common Arabic/English product
        translations, then return the corresponding ``sale.order.line`` id.

        A unique match is required.  If the same product/name occurs more than
        once in the same quotation, name-only import is ambiguous and should
        not silently select the wrong quotation line.
        """
        matches = self._get_import_sale_order_line_matches(order, value)
        return matches.id if len(matches) == 1 else False


    @api.model
    def load(self, fields_list, data):
        """Support round-trip import using Quotation Line OR Product Name only.

        Preferred input still works with ``Products / Quotation Line``.
        Additionally, an Excel file may contain only::

            Quotation / Sales Order
            Products / Product / Display Name
            Products / Quantity
            Products / Payment %

        In that case the product name is resolved inside the quotation and the
        import column is converted to ``line_ids/sale_order_line_id/.id`` before
        Odoo creates the Payment Request lines.
        """
        import_fields = list(fields_list)
        rows = [list(row) for row in data]

        quotation_line_paths = {
            'line_ids/sale_order_line_id',
            'line_ids/sale_order_line_id/id',
            'line_ids/sale_order_line_id/.id',
        }
        product_name_paths = {
            'line_ids/product_id',
            'line_ids/product_id/id',
            'line_ids/product_id/name',
            'line_ids/product_id/display_name',
        }
        sale_order_paths = {
            'sale_order_id',
            'sale_order_id/id',
            'sale_order_id/.id',
        }

        # 1) Existing support: exported Quotation Line labels -> database ids.
        quotation_line_found = False
        for index, field_path in enumerate(import_fields):
            if field_path not in quotation_line_paths:
                continue

            quotation_line_found = True
            resolved_values = {}
            all_nonempty_resolved = True

            for row_index, row in enumerate(rows):
                if index >= len(row):
                    continue

                value = row[index]
                if value in (False, None, ''):
                    continue

                line_id = self._resolve_import_sale_order_line_id(value)
                if not line_id:
                    all_nonempty_resolved = False
                    break

                resolved_values[row_index] = line_id

            if resolved_values and all_nonempty_resolved:
                import_fields[index] = 'line_ids/sale_order_line_id/.id'
                for row_index, line_id in resolved_values.items():
                    rows[row_index][index] = str(line_id)

        # 2) New support: Product Name only + Quotation / Sales Order.
        # Do not run this path when a real Quotation Line column is already
        # present, because that is more precise.
        if not quotation_line_found:
            order_index = next(
                (
                    idx for idx, path in enumerate(import_fields)
                    if path in sale_order_paths
                ),
                None,
            )
            product_index = next(
                (
                    idx for idx, path in enumerate(import_fields)
                    if path in product_name_paths
                ),
                None,
            )

            if product_index is not None:
                last_order_value = False
                resolved_values = {}
                all_nonempty_resolved = True

                for row_index, row in enumerate(rows):
                    # Odoo exports One2many continuation rows with parent
                    # columns blank.  Carry forward the last quotation value.
                    if order_index is not None and order_index < len(row):
                        current_order_value = row[order_index]
                        if current_order_value not in (False, None, ''):
                            last_order_value = current_order_value

                    if product_index >= len(row):
                        continue

                    product_value = row[product_index]
                    if product_value in (False, None, ''):
                        continue

                    order = self._resolve_import_sale_order(last_order_value)
                    line_id = self._resolve_import_sale_order_line_from_product_name(
                        order,
                        product_value,
                    )
                    if not line_id:
                        all_nonempty_resolved = False
                        break

                    resolved_values[row_index] = line_id

                if resolved_values and all_nonempty_resolved:
                    import_fields[product_index] = 'line_ids/sale_order_line_id/.id'
                    for row_index, line_id in resolved_values.items():
                        rows[row_index][product_index] = str(line_id)

        return super(
            SalePaymentRequest,
            self.with_context(payment_request_import=True),
        ).load(import_fields, rows)

    @api.model
    def _derive_sale_order_id_from_import_lines(self, vals):
        """Fill the required quotation from imported product lines when absent."""
        if vals.get('sale_order_id'):
            return vals

        sale_line_ids = set()
        for command in vals.get('line_ids') or []:
            if not isinstance(command, (list, tuple)) or len(command) < 3:
                continue
            if command[0] != 0 or not isinstance(command[2], dict):
                continue

            sale_line_id = command[2].get('sale_order_line_id')
            if sale_line_id:
                sale_line_ids.add(sale_line_id)

        if not sale_line_ids:
            return vals

        orders = self.env['sale.order.line'].browse(
            list(sale_line_ids)
        ).mapped('order_id')

        if len(orders) > 1:
            raise ValidationError(_(
                'All imported Payment Request product lines must '
                'belong to the same Quotation / Sales Order.'
            ))

        if orders:
            vals['sale_order_id'] = orders.id

        return vals

    @api.model_create_multi
    def create(self, vals_list):
        is_supervisor = self.env.user.has_group(
            'Sijil_payment_request.group_payment_request_supervisor'
        )
        is_user = self.env.user.has_group(
            'Sijil_payment_request.group_payment_request_user'
        )
        if not (is_supervisor or is_user):
            raise AccessError(_('You are not allowed to create Payment Requests.'))

        prepared_vals_list = []
        for vals in vals_list:
            vals = self._prepare_named_product_import_vals(vals)
            prepared_vals_list.append(vals)

            if not is_supervisor:
                vals['user_id'] = self.env.user.id
                vals['status'] = 'unpaid'

            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'sale.payment.request'
                ) or _('New')

        # Native mail.thread create, same behavior as the working contract model.
        # The context cleanup only prevents a wizard context from suppressing
        # Odoo's standard creation log.
        return super(
            SalePaymentRequest,
            self.with_context(
                tracking_disable=False,
                mail_create_nolog=False,
                mail_notrack=False,
            ),
        ).create(prepared_vals_list)

    def write(self, vals):
        if (
            'import_sale_order_ref' in vals
            or any(
                isinstance(command, (list, tuple))
                and len(command) >= 3
                and isinstance(command[2], dict)
                and 'import_product_name' in command[2]
                for command in (vals.get('line_ids') or [])
            )
        ) and len(self) == 1:
            vals = self._prepare_named_product_import_vals(
                vals,
                existing_request=self,
            )

        is_supervisor = self.env.user.has_group(
            'Sijil_payment_request.group_payment_request_supervisor'
        )

        if 'status' in vals and not is_supervisor:
            if any(request.status != vals['status'] for request in self):
                raise AccessError(_(
                    'Only a Payment Request Supervisor can update the Payment Request status.'
                ))

        if 'user_id' in vals and not is_supervisor:
            if vals['user_id'] != self.env.user.id:
                raise AccessError(_(
                    'You are not allowed to assign a Payment Request to another user.'
                ))

        # Let mail.thread create the tracking entries natively.
        return super(
            SalePaymentRequest,
            self.with_context(
                tracking_disable=False,
                mail_notrack=False,
            ),
        ).write(vals)


class SalePaymentRequestLine(models.Model):
    _name = 'sale.payment.request.line'
    _description = 'Sale Payment Request Line'
    _order = 'id'

    def unlink(self):
        user = self.env.user
        if (
            user.has_group('Sijil_payment_request.group_payment_request_user')
            or user.has_group('Sijil_payment_request.group_payment_request_reader')
        ):
            raise AccessError(_(
                'Payment Request User and Reader permissions do not allow deleting Payment Request lines.'
            ))

        if not user.has_group(
            'Sijil_payment_request.group_payment_request_supervisor'
        ):
            raise AccessError(_(
                'Only a Payment Request Supervisor can delete Payment Request lines.'
            ))

        return super().unlink()


    payment_request_id = fields.Many2one(
        'sale.payment.request',
        string='Payment Request',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    sale_order_line_id = fields.Many2one(
        'sale.order.line',
        string='Quotation Line',
        required=True,
        ondelete='restrict',
        index=True,
        check_company=True,
    )
    import_product_name = fields.Char(
        string='Import Product',
        compute='_compute_import_product_name',
        inverse='_inverse_import_product_name',
        store=True,
        copy=False,
        help='Import/export helper: product/service name only.',
    )

    @api.depends(
        'sale_order_line_id',
        'sale_order_line_id.product_id',
        'sale_order_line_id.name',
    )
    def _compute_import_product_name(self):
        for line in self:
            sale_line = line.sale_order_line_id
            if not sale_line:
                line.import_product_name = ''
                continue

            entered_name = getattr(
                sale_line,
                'zofirm_product_display_name',
                False,
            )
            product = sale_line.product_id
            line.import_product_name = (
                entered_name
                or (product.name if product else False)
                or ((sale_line.name or '').splitlines()[0])
                or ''
            )

    def _inverse_import_product_name(self):
        # Import values are resolved by the parent Payment Request before
        # child-line creation/update. No direct inverse write is required.
        return
    company_id = fields.Many2one(
        'res.company',
        related='payment_request_id.company_id',
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='payment_request_id.currency_id',
        store=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        related='sale_order_line_id.product_id',
        store=True,
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

            # The quotation customization stores the exact selected label and
            # the language/template used when the user chose the product.
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

            # 1) Exact label captured from the quotation autocomplete.
            if selected_name:
                line.quotation_product_display_name = selected_name
                continue

            # 2) If the exact label was not stored, rebuild it from the exact
            #    per-line language saved by the quotation customization.
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

            # Final fallback only for old quotation lines that predate the
            # per-line input-language fields.
            line.quotation_product_display_name = (
                sale_line.product_id.display_name
                if sale_line.product_id
                else (sale_line.name or '')
            )

    product_uom_qty = fields.Float(
        string='Quantity',
        required=True,
        digits=(16, 6),
        help=(
            'Quantity allocated to this Payment Request line. '
            'Six-decimal precision preserves percentage allocations exactly '
            '(for example, Qty 1 x 29.70% = 0.297000). '
            'Writable at ORM level to support native Odoo import; '
            'the Payment Request form keeps it readonly.'
        ),
    )
    price_unit = fields.Float(
        string='Unit Price',
        related='sale_order_line_id.price_unit',
        store=True,
        readonly=True,
        digits='Product Price',
    )
    base_amount = fields.Monetary(
        string='Line Total (Incl. Tax)',
        compute='_compute_base_amount',
        store=True,
        readonly=True,
        currency_field='currency_id',
    )
    payment_untaxed_amount = fields.Monetary(
        string='Total Before Tax',
        compute='_compute_payment_untaxed_amount',
        store=True,
        readonly=True,
        currency_field='currency_id',
        help='Untaxed amount allocated to this Payment Request line after applying the selected percentage/quantity.',
    )
    due_date = fields.Date(
        string='Due Date',
    )
    payment_status = fields.Selection(
        [
            ('paid', 'Paid'),
            ('partial', 'Partially Paid'),
            ('unpaid', 'Not Paid'),
        ],
        string='Payment Status',
        required=True,
        default='unpaid',
    )
    payment_percentage = fields.Float(
        string='Payment %',
        default=0.0,
        digits=(16, 2),
    )
    
    payment_amount = fields.Monetary(
        string='Payment Amount',
        currency_field='currency_id',
        compute='_compute_payment_amount',
        store=True,
        readonly=True,
    )

    @api.depends(
        'sale_order_line_id.price_total',
        'sale_order_line_id.product_uom_qty',
        'product_uom_qty',
    )
    
    def _compute_base_amount(self):
        for line in self:
            quotation_line = line.sale_order_line_id
            quotation_qty = quotation_line.product_uom_qty if quotation_line else 0.0
            if not quotation_line or float_compare(
                quotation_qty, 0.0, precision_digits=6
            ) == 0:
                line.base_amount = 0.0
                continue

            line.base_amount = (
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

            # price_subtotal is already after any Sales Order line discount
            # and excludes taxes. product_uom_qty is the quantity allocated
            # after applying Payment %.
            line.payment_untaxed_amount = (
                quotation_line.price_subtotal
                * line.product_uom_qty
                / quotation_qty
            )

    @api.constrains(
        'product_uom_qty',
        'payment_percentage',
        'sale_order_line_id',
    )
    
    def _check_requested_quantity(self):
        """Limit cumulative quantity using Quantity x Payment %.
        A request for Qty 1 at 50% consumes 0.5 effective units.  This keeps
        service instalments valid while preserving the old quantity protection:
        Qty 18 at 100% + Qty 5 at 100% still exceeds a quotation Qty of 20.
        """
        for line in self:
            if not line.sale_order_line_id:
                continue

            quotation_qty = line.sale_order_line_id.product_uom_qty
            if float_compare(line.product_uom_qty, 0.0, precision_digits=6) <= 0:
                raise ValidationError(_(
                    'Quantity for product "%(product)s" must be greater than zero.'
                ) % {'product': line.product_id.display_name})

            # Each individual request may reference at most the quotation's
            # physical quantity.  Repeated requests are allowed when their
            # weighted allocation remains within 100%.
            if float_compare(
                line.product_uom_qty,
                quotation_qty,
                precision_digits=6,
            ) > 0:
                raise ValidationError(_(
                    'Payment Request quantity for product "%(product)s" cannot exceed '
                    'the quotation quantity.\n'
                    'Quotation quantity: %(quotation)s'
                ) % {
                    'product': line.product_id.display_name,
                    'quotation': quotation_qty,
                })

            # Historical/imported Payment Requests may already exist in the
            # database. During native Payment Request import, keep the current
            # line validation above, but do not compare it against previously
            # allocated Payment Request lines.
            if self.env.context.get('payment_request_import'):
                continue

            other_lines = self.search([
                ('sale_order_line_id', '=', line.sale_order_line_id.id),
                ('id', '!=', line.id),
            ])
            # product_uom_qty is already the proportional quantity
            # allocated by Payment % when the request is created.
            already_requested_qty = sum(
                other_lines.mapped('product_uom_qty')
            )
            current_effective_qty = line.product_uom_qty
            total_requested_qty = already_requested_qty + current_effective_qty

            if float_compare(
                total_requested_qty,
                quotation_qty,
                precision_digits=6,
            ) > 0:
                remaining_qty = max(0.0, quotation_qty - already_requested_qty)
                raise ValidationError(_(
                    'Payment Requests for product "%(product)s" exceed the quotation '
                    'quantity after applying Payment %%.\n'
                    'Quotation quantity: %(quotation)s\n'
                    'Already allocated quantity: %(requested)s\n'
                    'Remaining allocation: %(remaining)s'
                ) % {
                    'product': line.product_id.display_name,
                    'quotation': quotation_qty,
                    'requested': already_requested_qty,
                    'remaining': remaining_qty,
                })

    @api.depends('base_amount')
    def _compute_payment_amount(self):
        for line in self:
            # Quantity already represents the Payment % portion.
            line.payment_amount = line.base_amount

    @api.constrains('payment_percentage')
    def _check_payment_percentage(self):
        for line in self:
            if line.payment_percentage < 0.0 or line.payment_percentage > 100.0:
                raise ValidationError(_('Payment percentage must be between 0 and 100.'))

    @api.constrains(
        'payment_percentage',
        'sale_order_line_id',
        'product_uom_qty',
        'base_amount',
    )
    def _check_cumulative_payment_percentage(self):
        """Never allocate more than the quotation line total across requests.

        Quantity selected in the wizard reduces the request line base amount
        proportionally.  The cumulative check is monetary, so several Payment
        Requests with different quantities and percentages may be created as
        long as their combined requested amount never exceeds the original
        quotation line ``price_total`` (quantity, discount and taxes included).
        """
        for line in self:
            if not line.sale_order_line_id:
                continue

            # Import is used to restore historical Payment Requests. Do not
            # count Payment Requests that are already present in the database
            # against the imported historical line. Normal UI/Wizard creation
            # keeps the cumulative protection unchanged.
            if self.env.context.get('payment_request_import'):
                continue

            other_lines = self.search([
                ('sale_order_line_id', '=', line.sale_order_line_id.id),
                ('id', '!=', line.id),
            ])
            already_requested = sum(other_lines.mapped('payment_amount'))
            total_requested = already_requested + line.payment_amount
            quotation_total = line.sale_order_line_id.price_total
            rounding = line.currency_id.rounding if line.currency_id else 0.01

            if float_compare(
                total_requested,
                quotation_total,
                precision_rounding=rounding,
            ) > 0:
                remaining = max(0.0, quotation_total - already_requested)
                raise ValidationError(_(
                    'Payment Requests for product "%(product)s" cannot exceed the '
                    'quotation line total including tax and quantity.\n'
                    'Already requested: %(already).2f\n'
                    'Remaining available: %(remaining).2f'
                ) % {
                    'product': line.product_id.display_name,
                    'already': already_requested,
                    'remaining': remaining,
                })

    @api.constrains('sale_order_line_id', 'payment_request_id')
    def _check_sale_order_line_matches_request(self):
        for line in self:
            if (
                line.sale_order_line_id
                and line.payment_request_id
                and line.sale_order_line_id.order_id != line.payment_request_id.sale_order_id
            ):
                raise ValidationError(
                    _('The selected quotation line does not belong to the Payment Request quotation.')
                )
