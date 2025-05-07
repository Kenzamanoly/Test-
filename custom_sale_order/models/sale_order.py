from odoo import api, models, fields, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    manager_reference = fields.Char(
        string='Manager Reference',
        help="Reference for sales managers",
        readonly=True
    )

    company_order_limit = fields.Float(
        string='Company Order Limit',
        related='company_id.sale_order_limit',
        readonly=True,
    )

    auto_workflow = fields.Boolean(
        string="Auto Workflow",
    )

    display_limit_warning = fields.Boolean(
        string='Display Limit',
        compute='_compute_display_limit_warning',
    )

    workflow_process_id = fields.Many2one(
        comodel_name="sale.workflow.process",
        string="Sale Workflow Process",
        copy=False
    )

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        res = super().fields_get(allfields, attributes)
        if res.get('manager_reference'):
            res['manager_reference']['readonly'] = not self.env.user.has_group(
                'custom_sale_order.group_sale_admin'
            )
        return res

    def _check_order_limit(self):
        for order in self:
            limit = order.company_id.sale_order_limit
            if limit > 0 and order.amount_total > limit:
                if not self.env.user.has_group('custom_sale_order.group_sale_admin'):
                    return False
        return True

    @api.constrains('amount_total')
    def _constrain_amount_total(self):
        pass

    def _compute_display_limit_warning(self):
        for order in self:
            order.display_limit_warning = (
                    order.company_order_limit > 0 and
                    order.amount_total > order.company_order_limit
            )

    def action_confirm(self):
        if not self._check_order_limit():
            message = _(
                "Order amount (%s) exceeds the configured limit (%s). "
                "Only Sale Admins can confirm this order. Order remains in draft state."
            ) % (self.amount_total, self.company_id.sale_order_limit)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Order Limit Exceeded'),
                    'message': message,
                    'type': 'warning',
                    'sticky': True,
                }
            }

        res = super().action_confirm()

        # Trigger auto workflow if enabled
        if self.auto_workflow or self.workflow_process_id:
            if self.workflow_process_id:
                self.env['automatic.workflow.job'].run_with_workflow(self.workflow_process_id)
            else:
                self._process_auto_workflow()

        return res

    def _process_auto_workflow(self):
        self.ensure_one()
        self._process_deliveries()
        self._process_invoicing()

    def _process_deliveries(self):
        self._process_existing_pickings()
        self._create_grouped_pickings()

    def _process_existing_pickings(self):
        for picking in self.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
            if picking.state == 'draft':
                picking.action_confirm()
            if picking.state in ('confirmed', 'assigned'):
                picking.action_assign()
                for move_line in picking.move_line_ids:
                    move_line.qty_done = move_line.reserved_uom_qty or move_line.move_id.product_uom_qty
                picking.button_validate()

    def _create_grouped_pickings(self):
        processed_products = self.picking_ids.move_ids.mapped('product_id')
        remaining_lines = self.order_line.filtered(
            lambda l: l.product_id.type != 'service' and
                      (l.product_id not in processed_products or l.qty_delivered < l.product_uom_qty)
        )

        if not remaining_lines:
            return

        product_groups = {}
        for line in remaining_lines:
            key = (line.product_id, line.warehouse_id)
            product_groups.setdefault(key, {
                'qty': 0.0,
                'uom': line.product_uom,
                'lines': []
            })
            product_groups[key]['qty'] += line.product_uom_qty - line.qty_delivered
            product_groups[key]['lines'].append(line)

        for (product, warehouse), data in product_groups.items():
            picking = self.env['stock.picking'].create({
                'partner_id': self.partner_id.id,
                'picking_type_id': warehouse.out_type_id.id,
                'location_id': warehouse.lot_stock_id.id,
                'location_dest_id': self.partner_id.property_stock_customer.id,
                'origin': self.name,
                'sale_id': self.id,
            })

            self.env['stock.move'].create({
                'name': product.display_name,
                'product_id': product.id,
                'product_uom_qty': data['qty'],
                'product_uom': data['uom'].id,
                'location_id': warehouse.lot_stock_id.id,
                'location_dest_id': self.partner_id.property_stock_customer.id,
                'picking_id': picking.id,
                'sale_line_id': data['lines'][0].id,
            })

            picking.action_confirm()
            picking.action_assign()
            if picking.state == 'assigned':
                for move_line in picking.move_line_ids:
                    move_line.qty_done = move_line.reserved_uom_qty or move_line.move_id.product_uom_qty
                picking.button_validate()

    def _process_invoicing(self):
        if not self.invoice_status == 'to invoice':
            return

        invoice = self._create_invoices()
        if not invoice:
            return

        if invoice.state == 'draft':
            invoice.action_post()

        if invoice.amount_residual > 0:
            self._register_payment(invoice)

    def _register_payment(self, invoice):
        payment_method = self.env['account.payment.method'].search([
            ('code', '=', 'manual'),
            ('payment_type', '=', 'inbound')
        ], limit=1)

        journal = self.company_id.sale_journal_id or self.env['account.journal'].search([
            ('type', '=', 'bank'),
            ('company_id', '=', invoice.company_id.id)
        ], limit=1)

        if not journal or not payment_method:
            raise UserError(_("No valid payment method or journal found."))

        payment = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'amount': invoice.amount_residual,
            'journal_id': journal.id,
            'payment_method_id': payment_method.id,
            'invoice_ids': [(6, 0, [invoice.id])],
            'ref': invoice.name,
            'currency_id': self.currency_id.id,
            'date': fields.Date.context_today(self),
        })
        payment.action_post()
        return payment


class ResCompany(models.Model):
    _inherit = 'res.company'

    sale_order_limit = fields.Float(
        string='Sale Order Limit',
        default=0,
    )

    sale_journal_id = fields.Many2one(
        'account.journal',
        string="Sale Payment Journal",
        domain=[('type', '=', 'bank')],
    )


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sale_order_limit = fields.Float(
        related='company_id.sale_order_limit',
        string='Sale Order Limit',
        readonly=False,
    )

    sale_journal_id = fields.Many2one(
        related='company_id.sale_journal_id',
        string="Sale Payment Journal",
        readonly=False
    )

class AccountMove(models.Model):
    _inherit = "account.move"

    workflow_process_id = fields.Many2one(
        comodel_name="sale.workflow.process",
        string="Workflow Process",
        copy=False
    )