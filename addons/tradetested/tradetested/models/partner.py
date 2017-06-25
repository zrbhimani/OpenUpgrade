# -*- coding: utf-8 -*-

from odoo import tools, api, fields, models, _
from odoo.exceptions import UserError
import tt_fields, common


class res_partner(models.Model):
    _inherit = 'res.partner'

    delivery_instructions = fields.Text('Delivery Instructions')
    tt_company_name = fields.Char('Company')
    first_name = fields.Char('First Name')
    is_depot = fields.Boolean('Depot Location')
    is_agent = fields.Boolean('Service Agent')
    is_assembly_contractor = fields.Boolean('Assembly Contractor')

    bill_tt_company_name = fields.Char('Company')
    bill_street = fields.Char('Street')
    bill_street2 = fields.Char('Street2')
    bill_zip = fields.Char('Zip', change_default=True)
    bill_city = fields.Char('City')
    bill_state_id = fields.Many2one("res.country.state", 'State')
    bill_country_id = fields.Many2one('res.country', 'Country')
    bill_delivery_instructions = fields.Text('Delivery Instructions')

    sale_order_count = fields.Integer(compute='_sale_order', string='# of Sales Order', store=False)
    last_order_date = fields.Date(compute='_sale_order', string='Last Order Date', store=False)

    case_ids = fields.One2many('crm.helpdesk', 'agent_id', string='Cases')
    assembly_case_ids = fields.One2many('crm.helpdesk', 'assembly_contractor_id', string='Cases')
    supplier_case_ids = fields.One2many('crm.helpdesk', "supplier_id", string='Cases')

    magento_id = fields.Char('Magento ID')
    magento_create_date = fields.Datetime('Magento Created at')
    magento_update_date = fields.Datetime('Magento Created at')
    magento_website_id = fields.Integer('Website ID')
    xero_id = fields.Char('Xero ID')
    mc_export = fields.Boolean('Schedule MC Export')

    last_check = fields.Date('Last Check')
    next_check = fields.Date('Next Check')
    supplier_note = fields.Char('Note')
    ordering_notes = fields.Text('Ordering Notes')
    supplier_terms = fields.Char('Supplier Terms')
    supplier_sold_cost = fields.Float(compute='_supplier_sold_cost', string="Cost of Sales")

    @api.model
    def _address_fields(self):
        return ['tt_company_name', 'street', 'street2', 'zip', 'city', 'state_id', 'country_id']

    def dummy(self):
        return True

    # Customer
    @api.multi
    def create_new_order(self):
        sobj = self[0]
        so_pool = self.env['sale.order']

        so_vals = {
            'partner_id': sobj.id,
            'phone': sobj.phone,
            'email': sobj.email,
            'ship_tt_company_name': sobj.tt_company_name,
            'ship_street': sobj.street,
            'ship_street2': sobj.street2,
            'ship_city': sobj.city,
            'ship_zip': sobj.zip,
            'ship_state_id': sobj.state_id and sobj.state_id.id or False,
            'ship_country_id': sobj.country_id and sobj.country_id.id or False,
        }

        if sobj.sale_order_ids:
            so_vals['warehouse_id'] = sobj.sale_order_ids[0].warehouse_id.id
        elif sobj.country_id:
            warehouses = self.env['stock.warehouse'].search([('name', 'ilike', sobj.country_id.name)])
            so_vals['warehouse_id'] = warehouses[0].id
        else:
            so_vals['warehouse_id'] = 1

        order = so_pool.create(so_vals)
        order.onchange_partner_id()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Sales Orders'),
            'res_model': 'sale.order',
            'view_type': 'form',
            'view_mode': 'form,tree',
            'views': [(self.env.ref('tradetested.view_order_form').id, 'form'), (self.env.ref('tradetested.view_order_tree').id, 'tree')],
            'search_view_id': self.env.ref('tradetested.view_order_filter').id,
            'target': 'current',
            'res_id': order.id
        }

    @api.multi
    def action_sale_quotation_order(self):
        self.ensure_one()
        args = []
        if self.email and self.phone:
            args = ['|', ('email', '=', self.email), ('phone', '=', self.phone)]
        elif self.email:
            args = [('email', '=', self.email)]
        elif self.phone:
            args = [('email', '=', self.phone)]

        partners = self
        if args:
            partners += self.search(args)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Quotations and Sales'),
            'res_model': 'sale.order',
            'domain': [('partner_id', 'in', partners.ids)],
            'view_type': 'form',
            'view_mode': 'tree,form',
            'views': [(self.env.ref('sale.view_order_tree').id, 'tree'), (self.env.ref('sale.view_order_form').id, 'form')],
            'search_view_id': self.env.ref('sale.view_sales_order_filter').id,
            'target': 'current',
            'nodestroy': True,
        }

    @api.multi
    def _sale_order(self):
        for partner in self:
            if partner.sale_order_ids:
                partner.sale_order_count = len(partner.sale_order_ids)
                partner.last_order_date = partner.sale_order_ids[0].date_order

    # Supplier
    @api.multi
    def create_auto_orderpoints(self):
        order_points = []
        for prod in self.env['product.product'].search([('seller_ids.name', '=', self.id)]):
            op_exists = self.env['stock.warehouse.orderpoint'].search([('product_id', '=', prod.id)])
            if not op_exists:
                warehouse = self.env['stock.warehouse'].search([('name', '=', prod.company_id.name), ('company_id', '=', prod.company_id.id)])[0]
                new_op = self.env['stock.warehouse.orderpoint'].create({
                    'product_id': prod.id,
                    'company_id': prod.company_id.id,
                    'warehouse_id': warehouse.id,
                    'location_id': warehouse.wh_input_stock_loc_id.id,
                    'qty_automatic': 1,
                    'product_min_qty': 0,
                    'product_max_qty': 0,
                    'product_uom': prod.uom_id.id
                })
                new_op.update_min_max_qty()
                order_points.append(new_op.id)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Reordering Rules'),
            'res_model': 'stock.warehouse.orderpoint',
            'domain': [('id', 'in', order_points)],
            'view_type': 'form',
            'view_mode': 'tree,form',
            'target': 'current',
            'nodestroy': True,
        }

    @api.multi
    def show_supplier_products(self):
        return {
            'name': self.name,
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'product.product',
            'domain': [('seller_ids.name', '=', self.id)],
            'target': 'current',
        }

    @api.multi
    def open_supplier_check_note(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Supplier Note'),
            'res_model': 'res.partner',
            'res_id': self.id,
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('tradetested.view_supplier_check_note_form').id,
            'target': 'new',
            'nodestroy': True,
        }

    @api.multi
    def _supplier_sold_cost(self):
        sql = """
            SELECT
                psi.name as supplier_id,
                sum(cost_price)
            FROM
                stock_move sm,
                product_product p,
                product_template t,
                product_supplierinfo psi
            WHERE
                sm.product_id = p.id AND
                p.product_tmpl_id = t.id AND
                psi.product_tmpl_id = t.id AND
                --sm.create_date >= now()::date - 365 AND
                psi.name in (%s)
            GROUP BY psi.name""" % (",".join(map(str, self.ids)))
        self._cr.execute(sql)

        data = dict([(s[0], s[1]) for s in self._cr.fetchall()])
        for partner in self:
            partner.supplier_sold_cost = data.get(partner.id, False)

    @api.multi
    def _purchase_invoice_count(self):
        purchase_pool = self.env['purchase.order']
        invoice_pool = self.env['account.invoice']
        for partner in self:
            partner.purchase_order_count = purchase_pool.search_count([('partner_id', '=', partner.id)])
            partner.supplier_invoice_count = invoice_pool.search_count([('partner_id', '=', partner.id), ('type', '=', 'in_invoice')])

    # OnChange
    @api.onchange('state_id')
    def onchange_state(self):
        self.country_id = self.state_id.country_id.id

    @api.onchange('bill_state_id')
    def onchange_bill_state(self):
        self.bill_country_id = self.bill_state_id.country_id.id

    # ORM
    @api.model
    def create(self, vals):
        vals = common.strip_address(vals)
        return super(res_partner, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = common.strip_address(vals)
        if 'name' in vals or 'email' in vals:
            vals['mc_export'] = True

        resp = super(res_partner, self).write(vals)
        if vals.get('phone'):
            orders = self.env['sale.order'].search([('partner_id', '=', self[0].id), ('state', 'in', ['draft', 'quote']), '|', ('phone', '!=', vals['phone']), ('phone', '=', False)])
            if orders:
                orders.write({'phone': vals['phone']})
        return resp

    @api.multi
    def copy(self, default=None):
        default = default or {}
        default.update({
            'purchase_order_ids': [],
            'sale_order_ids': [],
            'invoice_ids': [],
        })
        return (models.Model).copy(self, default)

    @api.model
    def name_search(self, name, args=None, operator='ilike', limit=100):
        args = args or []
        if not self._context.get('restrict_when_empty'):
            return super(res_partner, self).name_search(name, args, operator, limit)
        if not name and self._context.get('restrict_when_empty'):
            return []
        if self._context.get('sale_id'):
            order = self.env['sale.order'].browse(self._context['sale_id'])
            args.append(('id', '=', order.partner_id.id))
        if name:
            sql_limit = ''
            if limit:
                sql_limit = 'limit ' + str(limit)
            if "'" in name:
                name = name.replace("'", "''")
            self._cr.execute("SELECT id from res_partner WHERE phone='%s' or email='%s' order by last_order_date desc NULLS LAST  %s" % (name, name, sql_limit))
            records = self.browse([x[0] for x in self._cr.fetchall()])
        if (not name) and (not ids):
            records = self.search(args, limit=limit)
        return records.name_get()

    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):
        if not order and self._context.get('order'):
            order = self._context.get('order')
        return super(res_partner, self).search(args=args, offset=offset, limit=limit, order=order, count=count)

    @api.multi
    def unlink(self):
        if self._uid != 1:
            raise UserError('Deletion Not Allowed, Only Administrator can delete, Please contact Administrator')
        super(res_partner, self).unlink()

