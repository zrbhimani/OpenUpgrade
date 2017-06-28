# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _, SUPERUSER_ID
from odoo.addons import decimal_precision as dp
from odoo.exceptions import UserError

from datetime import datetime
from dateutil.relativedelta import relativedelta
from libthumbor import CryptoURL

import tt_fields
import common
import time
import random
import uuid
import cStringIO
import csv
import base64
import logging

_logger = logging.getLogger('Product')


class product_category(models.Model):
    _name = 'product.category'
    _inherit = ['product.category', 'mail.thread']

    case_ids = fields.One2many('crm.helpdesk', 'product_categ_id', 'Cases', order='create_date desc', copy=False)
    active = fields.Boolean('Active', default=True)
    categ_sales = fields.One2many('product.category.sales', 'categ_id', 'Category Sales', copy=False)

    @api.model
    def name_search(self, name, args=None, operator='ilike', limit=100):
        if 'product_tree_search' in self._context:
            operator = '='
            if '/' in name:
                name = name.split('/')[-1].strip()
        return super(product_category, self).name_search(name, args, operator, limit=limit)

    @api.multi
    def update_category_sales(self):
        return self.env['product.category.sales'].with_context({'categ_id': self.id}).generate_category_sales()


class product_supplierinfo(models.Model):
    _inherit = 'product.supplierinfo'

    delay = fields.Integer('Delivery Lead Time', required=True, default=0)
    default_code = fields.Char(related='product_id.default_code', string="SKU")
    company_id = fields.Many2one('res.company', related='product_id.company_id', string="Company")

    _sql_constraints = [
        ('check_delay', 'CHECK (delay>0)', 'Delivery Lead Time is Required'),
    ]


class product_product_supplier(models.Model):
    _name = 'product.product.supplier'
    _table = 'product_product_supplier'
    _auto = False

    name = fields.Char('Name', size=256)

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'product_product_supplier')
        self._cr.execute("""
            create or replace view product_product_supplier as (
                SELECT p.id, p.name from res_partner p WHERE id in ( SELECT distinct name from product_supplierinfo )
        )""")


class product_product_name(models.Model):
    _name = 'product.product.name'
    _table = 'product_product'
    _auto = False

    name = fields.Char("Name", size=128, index=True)
    active = fields.Boolean('Active')
    default_code = fields.Char('SKU', size=64, index=True)

    @api.multi  # ToDO check name field issue product_tmpl_id
    def name_get(self):
        result = []
        for prod in self:
            if prod.default_code:
                result.append((prod.id, "[%s] %s" % (prod.default_code, prod.product_tmpl_id.name)))
            else:
                result.append((prod.id, prod.product_tmpl_id.name))
        return result

    @api.model
    def name_search(self, name, args=None, operator='ilike', limit=100):
        return super(product_product_name, self).name_search(['|', ('name', 'ilike', name), ('default_code', 'ilike', name)] + args, limit=limit)


class product_product_trademe(models.Model):
    _name = 'product.product.trademe'

    product_id = fields.Many2one('product.product', 'Product Reference', required=True, ondelete='cascade', index=True)
    title = fields.Char('Title', size=50)
    subtitle = fields.Char('Subtitle', size=50)
    description = fields.Text('Description', size=2048)
    reserve = fields.Float('Reserve', digits=(11, 2))
    buy_now = fields.Float('Buy now', digits=(11, 2))
    extras = fields.Selection((('none', 'None'), ('gallery', 'Gallery'), ('feature_combo', 'Feature combo')), 'Extras', default='gallery')
    length = fields.Integer('Length', defalult=10)
    buy_now_only = fields.Boolean('Buy now only')

    tm_sku = fields.Char('TM SKU', size=64, required=False)
    category = fields.Integer('Category', required=False)
    default_code = fields.Char(related='product_id.default_code', string='SKU')
    active = fields.Boolean(related='product_id.active', string="Active")
    categ_id = fields.Many2one('product.category', related='product_id.categ_id', string="Category", store=True)
    type = fields.Selection(related='product_id.type', selection=[('consu', 'Consumable'), ('service', 'Service'), ('product', 'Product')], string='Product Type', store=True)
    product_name_id = fields.Many2one('product.product.name', related='product_id.name_id', string='Product')
    product_supplier_id = fields.Many2one('product.product.supplier', related='product_id.supplier_id', string="Supplier")


class product_related_product(models.Model):
    _name = 'product.related.product'

    product_id = fields.Many2one('product.product', 'Product', required=True, ondelete='cascade')
    type = fields.Selection([('Cross Sell', 'Cross Sell'), ('Related', 'Related'), ('Up Sell', 'Up Sell'), ('Part', 'Part'), ('Post Sale', 'Post Sale')], 'Type')
    rel_product_id = fields.Many2one('product.product', 'Related Product')
    default_code = fields.Char(related='rel_product_id.default_code', string='SKU')
    list_price = fields.Float(related='rel_product_id.list_price', string='Standard Price')
    special_price = fields.Float(related='rel_product_id.special_price', string='Special Price')


class product_product(models.Model):
    _name = 'product.product'
    _inherit = ['product.product', 'obj.watchers.base', 'base.activity']

    default_code = fields.Char('Internal Reference', index=True, required=True, copy=False)
    carton_qty = fields.Float('Carton Quantity', default=lambda *a: 1)
    shipping_group = fields.Selection([('CARRIER', 'CARRIER'), ('TOLL', 'TOLL'), ('TOLL_DEPOT', 'TOLL_DEPOT')], string='Shipping group', default=False)
    shipping_description = fields.Text('Shipping description')
    tech_info = fields.Text('Technical Information')
    pmp_note = fields.Char('Notes', size=1024)
    state_last_updated = fields.Date('Status Last Updated')

    main_location_id = fields.Many2one('stock.location', compute='_main_location', string="Main Location", store=True)
    case_ids = fields.One2many('crm.helpdesk', 'product_id', 'Cases', order='create_date desc', copy=False)
    related_product_ids = fields.One2many('product.related.product', 'product_id', 'Related Products')
    related_document_ids = fields.One2many('ir.attachment', 'res_id', 'Related Documents')  # 'product_id'
    reorder_ids = fields.One2many('stock.warehouse.orderpoint', 'product_id', 'Reordering Rules', domain=['|', ('active', '=', True), ('active', '=', False)])

    replacement_product_id = fields.Many2one('product.product', 'Replacement Product')
    replacing_product_id = fields.Many2one('product.product', compute='_replacing_product', string='Replacing Product')

    supplier_id = fields.Many2one('product.product.supplier', compute='_name_supplier', string='Supplier', fnct_inv='_set_supplier', fnct_search='_search_supplier')  # TODO fnct_inv=_set_suppl
    name_id = fields.Many2one('product.product.name', compute='_name_supplier', string='Product', search='_search_name')

    seller_id = fields.Many2one('res.partner', compute='_get_seller', string="Supplier", store=True)
    lead_time = fields.Float(compute='_get_seller', string="Lead Time", store=True)

    # Pricing
    price_at_20 = fields.Float(compute='_price_at_margin', string="Price @ 20%", store=True)
    price_at_30 = fields.Float(compute='_price_at_margin', string="Price @ 30%", store=True)
    price_at_40 = fields.Float(compute='_price_at_margin', string="Price @ 40%", store=True)
    price_at_50 = fields.Float(compute='_price_at_margin', string="Price @ 50%", store=True)
    price_at_60 = fields.Float(compute='_price_at_margin', string="Price @ 60%", store=True)
    price_for_margin = fields.Float('Price')
    margin_of_price = fields.Float(compute='_margin_of_price', string='Margin')
    margin_for_price = fields.Float('Margin')
    price_of_margin = fields.Float(compute='_price_of_margin', string='Price')
    special_from_date = fields.Datetime('Special from date')
    special_to_date = fields.Datetime('Special to date')
    special_price = fields.Float('Special price', digits=(11, 2))

    list_price_copy = fields.Float(related='list_price', string="Standard Price")
    special_price_copy = fields.Float(related='special_price', string="Special Price")
    special_from_date_copy = fields.Datetime(related='special_from_date', string="Special From")
    special_to_date_copy = fields.Datetime(related='special_to_date', string="Special To")

    comp_config_lines = fields.One2many('price.monitor.line', 'product_id', 'Configuration')
    comp_price_ids = fields.One2many("price.monitor.price", compute='_comp_price', string="Logs")
    comp_lowest = fields.Many2one('price.monitor', compute='_comp_price', string='Competitor')
    comp_lowest_price = fields.Float(compute='_comp_price', string='Competitor Price')
    comp_lowest_status = fields.Char(compute='_comp_price', string='Stock Status')

    fm_id = fields.Integer('Filemaker ID', size=11)
    volume = fields.Float('Volume', help="The volume in m3.")
    weight = fields.Float('Gross Weight', digits=dp.get_precision('Stock Weight'), help="The gross weight in Kg.")
    weight_net = fields.Float('Net Weight', digits=dp.get_precision('Stock Weight'), help="The net weight in Kg.")

    magento_enabled = fields.Boolean('Magento Enabled')
    magento_page_title = fields.Char('Page title', size=70)
    magento_description = fields.Text('Description', size=2000)
    magento_description_appendix = fields.Text('Description appendix', size=1000)
    magento_ebay_title_appendix = fields.Char('eBay title appendix', size=50)
    magento_short_description = fields.Text('Short description', size=1000)
    magento_url_key = fields.Char('URL Key', size=1024, copy=False)

    trademe_id = fields.One2many('product.product.trademe', 'product_id', 'Trademe Aliases')
    trademe_enabled = fields.Boolean('Enabled')
    trademe_allow_pickup = fields.Boolean('Allow pickup')
    trademe_reserve = fields.Selection([('no', 'No'), ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5'), ('6', '6'), ('7', '7')], string="Allow $1 Reserves")

    prod_seller_ids = fields.One2many('product.supplierinfo', 'product_id', 'Vendor')
    cost = fields.Float(compute='_cost', string='Cost Price', store=True)
    case_count_ids = fields.One2many('crm.helpdesk.count.report', 'product_id', 'Case Counts')
    guid = tt_fields.Uuid('GUID')

    # images = fields.List('Images', type='char')
    image = fields.Char('Image')
    image_large = fields.Char('Large Image', compute='_compute_image_large', store=False)

    last_out_of_stock_date = fields.Datetime(compute='_last_out_of_stock_date', string='Last Out of Stock Date', store=True)

    po_expected_date = fields.Date(compute='next_po_expected', string="PO Expected Date")  # ,
    po_expected_qty = fields.Float(compute='next_po_expected', string="PO Expected Qty")  # ,
    saleable_qty = fields.Float(compute='_saleable_qty', digits=dp.get_precision('Product Unit of Measure'), string='Saleable Quantity')

    product_manager = fields.Many2one('res.users', 'Product Manager')
    case_summary = fields.Text(compute='_case_summary', string="Case Summary")
    doc_count = fields.Integer(compute='_doc_count', string="Documents Count", fnct_search='_search_doc_count')
    xero_id = fields.Char('Xero ID')

    date_obsolete = fields.Datetime('Obsolete Date')
    volume_container = fields.Float('Container Volume')
    volume_storage = fields.Float('Storage Volume')

    parent_categ_id = fields.Many2one('product.category', related='categ_id.parent_id', string="Parent Category", store=True)
    state = fields.Selection([('', ''), ('draft', 'In Development'), ('sellable', 'Normal'), ('end', 'End of Lifecycle'), ('obsolete', 'Obsolete')], 'Status')

    _sql_constraints = [
        ('default_code_sku_uniq', 'unique (default_code)', 'The SKU must be unique!'),
        ('guid_uniq', 'unique(guid)', 'Unique GUID is required'),
    ]

    @api.multi
    def _check_list_price(self):
        for product in self:
            if not product.list_price or not product.special_price:
                return True
            elif product.list_price <= product.special_price:
                return False
        return True

    @api.multi
    def _check_prod_replacement(self):
        for product in self:
            if product.replacement_product_id:
                replacement_prod_ids = self.search([('replacement_product_id', '=', product.replacement_product_id.id)])
                if len(replacement_prod_ids) > 1:
                    raise UserError(' %s SKUs are replacing the same product' % (", ".join([r.default_code for r in self.browse(replacement_prod_ids)])))
        return True

    _constraints = [
        (_check_list_price, 'Special Price must be less then Standard Price ', ['list_price', 'special_price']),
        (_check_prod_replacement, 'Multiple Replacement is not possible', ['replacement_product_id']),
    ]

    def _search_name(self, operator, value):
        if operator == '=':
            return [('id', '=', value)]
        else:
            return ['|', ('name', operator, value), ('default_code', operator, value)]

    @api.multi
    def _name_supplier(self):
        for prod in self:
            prod.update({
                'name_id': prod.id,
                'supplier_id': prod.id
            })

    def _search_supplier(self, operator, value):
        if operator == '=':
            search_sql = 'psi.name = %s' % value
        else:
            search_sql = "p.name %s '%%%s%%'" % (operator, value)

        self._cr.execute("""SELECT psi.product_id FROM product_supplierinfo psi, res_partner p WHERE psi.name = p.id AND %s""" % search_sql)
        return [('id', 'in', [x[0] for x in self._cr.fetchall()])]

    @api.multi
    def _last_out_of_stock_date(self):
        for prod in self:
            for log in prod.log_ids:
                if log.activity == 'Out of Stock':
                    prod.last_out_of_stock_date = log.date
                    break

    @api.multi
    @api.depends('standard_price')
    def _cost(self):
        for prod in self:
            prod.cost = prod.standard_price

    @api.multi
    @api.depends('seller_ids', 'seller_ids.name', 'seller_ids.delay')
    def _get_seller(self):
        for prod in self:
            if prod.seller_ids:
                prod.update({
                    'seller_id': prod.seller_ids[0].name.id,
                    'lead_time': prod.seller_ids[0].delay
                })

    @api.multi
    @api.depends('company_id')
    def _main_location(self):
        for prod in self:
            if prod.company_id:
                warehouses = self.env['stock.warehouse'].search([('company_id', '=', prod.company_id.id)])
                if warehouses:
                    prod.main_location_id = warehouses[0].lot_stock_id.id

    @api.one
    def generate_barcode(self):
        while True:
            barcode = '2100000' + str(random.randint(000001, 999999)).zfill(6)
            if common.check_ean(barcode):
                ean_exists = self.search([('barcode', '=', barcode)])
                if not ean_exists:
                    self.barcode = barcode
                    return

    @api.multi
    def _compute_image_large(self):
        key = self.env['tradetested.config'].get('thumbor:thumbor_key')
        domain = self.env['tradetested.config'].get('thumbor:domain')
        if key and domain:
            domain = domain.rstrip('/')
            crypto = CryptoURL(key=key)
            for prod in self:
                url = crypto.generate(width=150, height=150, fit_in=True, image_url=prod.image)
                url = domain + url
                prod.image_large = url

    @api.multi
    def _replacing_product(self):
        for prod in self:
            repl_prod_ids = self.search([('replacement_product_id', '=', prod.id), '|', ('active', '=', True), ('active', '=', False)])
            prod.replacing_product_id = repl_prod_ids and repl_prod_ids[0].id or False

    @api.multi
    @api.depends('price_for_margin')
    def _margin_of_price(self):
        for prod in self:
            if prod.price_for_margin:
                prod.margin_of_price = ((prod.price_for_margin - (prod.standard_price * (1 + sum([t.amount / 100 for t in prod.taxes_id])))) / prod.price_for_margin) * 100

    @api.multi
    @api.depends('standard_price', 'taxes_id', 'taxes_id.amount')
    def _price_at_margin(self):
        for prod in self:
            base = (prod.standard_price * (1 + sum([t.amount / 100 for t in prod.taxes_id])))
            prod.update({
                'price_at_20': base / 0.8,
                'price_at_30': base / 0.7,
                'price_at_40': base / 0.6,
                'price_at_50': base / 0.5,
                'price_at_60': base / 0.4
            })

    @api.multi
    def _comp_price(self):
        for prod in self:
            if prod.id:
                self._cr.execute("select pmp.monitor_id, pmp.price, pmp.date  from price_monitor_price pmp order by price limit 1;".format(prod.id))
                # "select pmp.monitor_id, pmp.price, pmp.date  from price_monitor_price pmp WHERE current=True AND pmp.product_int=%s order by price limit 1"
                result = self._cr.fetchone()
                prod.update({
                    'comp_price_ids': [cp.id for cp in self.env['price.monitor.price'].search([('product_id', '=', prod.id)])],
                    'comp_lowest': result and result[0] or False,
                    'comp_lowest_price': result and result[1] or 0,
                    'comp_lowest_status': result and result[2] or 0
                })

    @api.multi
    @api.depends('standard_price')
    def _price_of_margin(self):
        for prod in self:
            if prod.margin_for_price:
                prod.price_of_margin = (prod.standard_price * (1 + sum([t.amount / 100 for t in prod.taxes_id]))) / (1 - prod.margin_for_price / 100)

    @api.multi
    def _generate_default_sku(self):
        while True:
            new_sku = '9' + str(random.randint(00001, 99999)).zfill(5)
            exist_ids = self.search([('default_code', '=', new_sku), '|', ('active', '=', True), ('active', '=', False)])
            if not exist_ids:
                return new_sku

    @api.multi
    def update_stock_activity(self):
        for prod in self:
            count = 0
            current_activity = [c for c in prod.log_ids if c.activity in ('Out of Stock', 'Stock In')]
            for move in self.env['stock.move'].search([('product_id', '=', prod.id), ('state', '=', 'done'), '|', ('location_dest_id', '=', prod.main_location_id.id), ('location_id', '=', prod.main_location_id.id)], order='date'):
                prev_count = count
                if move.location_id.id == self.main_location_id.id:
                    count -= move.product_qty
                else:
                    count += move.product_qty

                activity = False
                if prev_count == 0 and count > 0:
                    activity = 'Stock In'
                elif count == 0 and prev_count > 0:
                    activity = 'Out of Stock'

                if activity:
                    act_vals = {'move_id': move.id, 'date': move.date, 'res_model': 'product.product', 'res_id': prod.id, 'activity': activity, 'value_before': prev_count, 'value_after': count}
                    act_exists = self.env['res.activity.log'].search([('res_id', '=', prod.id), ('res_model', '=', 'product.product'), ('move_id', '=', move.id), ('activity', '=', activity)])
                    if act_exists:
                        current_activity.remove(act_exists[0])
                        act_exists[0].write(act_vals)
                    else:
                        self.env['res.activity.log'].create(act_vals)
            if current_activity:
                for activity in current_activity:
                    activity.unlink()

    @api.multi
    def low_stock_warning_msg(self):
        product = self.browse(self.product_id)
        msg = False
        if product.type == 'product' and product.saleable_qty <= 0:
            msg = u"Warning: Product This is Out Of Stock.          \n'%s'" % (product.code)
            msg += "\nOn Hand: %s units" % product.qty_available
            msg += "\nSaleable: %s units" % product.saleable_qty

            if product.po_expected_qty:
                msg += "\n\nExpected Date: %s" % datetime.strptime(product.po_expected_date, '%Y-%m-%d').strftime('%d %b %Y')
                msg += "\nExpected Quantity: %s\n\n" % product.po_expected_qty

        return msg

    @api.multi
    def export_to_magento(self):  # used for a button only
        self.env['tradetested.exporters.product'].export_products()
        return True

    @api.multi
    def product_open_documents(self):
        sobj = self[0]
        attach_ids = self.env['ir.attachment'].search(['|', ('product_ids', 'in', sobj.id), ('categ_ids', 'in', sobj.categ_id.id)])

        form_view_id = self.env.ref('tradetested.view_product_document_form', False).id
        tree_view_id = self.env.ref('tradetested.view_product_document_tree', False).id
        search_view_id = self.env.ref('tradetested.view_product_document_search', False).id

        return {
            'name': 'Documents',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'ir.attachment',
            'views': [(tree_view_id, 'tree'), (form_view_id, 'form')],
            'search_view_id': search_view_id,
            'domain': [('id', '=', attach_ids.ids)],
        }

    @api.multi
    def _doc_count(self):
        doc_pool = self.env['ir.attachment']
        for prod in self:
            doc_ids = doc_pool.search(['|', ('product_ids', 'in', prod.id), ('categ_ids', 'in', prod.categ_id.id)])
            prod.doc_count = len(doc_ids)

    @api.multi  # todo check
    def _search_doc_count(self, args):
        count_arg = filter(lambda x: x[0] == 'doc_count', args)
        if count_arg:
            counts = count_arg[0][2]
            operator = count_arg[0][1]

            if operator == '=' and not counts:
                op = '>'
            else:
                op = operator

            self._cr.execute("""
                WITH prod_doc_data as(
                    select
                        pp.id as product_id, ir.id as document_id
                    from
                        product_template pt,
                        product_product pp,
                        product_category pc,
                        ir_attachment ir,
                        rel_product_document rpd
                    WHERE
                        (pp.product_tmpl_id = pt.id AND
                        pc.id = pt.categ_id AND
                        rpd.document_id = ir.id and
                        rpd.product_id = pp.id)
                    UNION
                    select
                        pp.id as product_id, ir.id as document_id
                    from
                        product_template pt,
                        product_product pp,
                        product_category pc,
                        ir_attachment ir,
                        rel_product_category_document rpcd
                    WHERE
                        pp.product_tmpl_id = pt.id AND
                        pc.id = pt.categ_id AND
                        rpcd.document_id = ir.id AND
                        rpcd.category_id = pc.id
                ) select product_id from prod_doc_data group by product_id having count(DISTINCT document_id) %s %s
            """ % (op, int(counts)))
            resp = [x[0] for x in self._cr.fetchall()]
            if resp:
                if operator == '=' and not counts:
                    return [('id', 'not in', resp)]
                else:
                    return [('id', 'in', resp)]
        return []

    @api.multi
    def next_po_expected(self):

        for prod in self:
            expected_shipments = {}
            if prod.bom_ids:
                for bom in prod.bom_ids:
                    bom_parts = dict([(l.product_id.id, [l.product_id.qty_available, l.product_qty]) for l in bom.bom_line_ids])
                    po_lines = self.env['purchase.order.line'].search([('product_id', 'in', bom_parts.keys()), ('order_id.state', 'in', ['draft', 'sent', 'confirmed', 'approved'])], order='date_planned')  # , ('received', '=', False)

                    if po_lines:
                        expected_date = po_lines[0].date_planned
                        for po_line in po_lines:
                            if po_line.product_id.id in bom_parts:
                                bom_parts[po_line.product_id.id][0] += po_line.product_qty

                    qty_list = []
                    for bom_part in bom_parts.values():
                        possible_qty = bom_part[0] / bom_part[1]
                        qty_list.append(possible_qty)

                    expected_qty = min(qty_list)
                    expected_qty = expected_qty - prod.qty_available

                    # if expected_qty > 0:
                    #     if expected_date in expected_shipments:
                    #         if expected_shipments[expected_date] < expected_qty:
                    #             expected_shipments[expected_date] = expected_qty
                    #     else:
                    #         expected_shipments[expected_date] = expected_qty

            # Search for normal product + BOM header products
            po_lines = self.env['purchase.order.line'].search([('product_id', '=', prod.id), ('order_id.state', 'in', ['draft', 'sent', 'confirmed', 'approved'])], order='date_planned')  # , ('received', '=', False)
            for po_line in po_lines:
                if po_line.date_planned in expected_shipments:
                    expected_shipments[po_line.date_planned] += po_line.product_qty
                else:
                    expected_shipments[po_line.date_planned] = po_line.product_qty

            expected_shipments = expected_shipments.items()
            # expected_shipments = map(lambda x: [datetime.strptime(x[0], '%Y-%m-%d'), x[1]], expected_shipments) #TODO Change date_planned to date, from datetime
            expected_shipments = map(lambda x: [datetime.strptime(x[0], '%Y-%m-%d'), x[1]], expected_shipments)
            expected_shipments.sort(key=lambda x: x[0])
            # if prod.saleable_qty < 0 and len(expected_shipments) > 1:  # TODO Test this after implementiogn saleable_qty
            if len(expected_shipments) > 1:
                tot_exp_qty = 0
                for exp_date, exp_qty in expected_shipments:
                    tot_exp_qty += exp_qty
                    if abs(prod.saleable_qty) < tot_exp_qty:
                        prod.update({
                            'po_expected_date': exp_date.strftime('%Y-%m-%d'),
                            'po_expected_qty': exp_qty
                        })
                        break
            elif expected_shipments:
                prod.update({
                    'po_expected_date': expected_shipments[0][0].strftime('%Y-%m-%d'),
                    'po_expected_qty': expected_shipments[0][1]
                })

    @api.multi
    def _case_summary(self):
        self._cr.execute("""SELECT
                       ch.product_id,
                       replace(ch.name, so.name || ' - ', '') as name,
                       count(*)
                   from
                       crm_helpdesk ch, sale_order so
                   where
                       ch.order_id = so.id AND
                       ch.date > (now() - interval '3 months') AND
                       ch.product_id in (%s)
                   group by
                       ch.product_id, replace(ch.name, so.name || ' - ', '')
                   order by count(*) desc
                   """ % (','.join(map(str, self.ids))))

        recs = self._cr.dictfetchall()
        ret = dict([(id, '') for id in self.ids])
        totals = {}
        for rec in recs:
            if rec['product_id'] not in totals:
                totals[rec['product_id']] = 0
            totals[rec['product_id']] += rec['count']
        for rec in recs:
            if not ret[rec['product_id']]:
                ret[rec['product_id']] = '<b>Resolution Stats (last 3 months)</b><br/>'
            ret[rec['product_id']] += "%s %d %%<br/>" % (rec['name'], round((rec['count'] / float(totals[rec['product_id']])) * 100))
        return ret

    @api.multi
    def _saleable_qty(self):
        for sobj in self:
            sobj.saleable_qty = sobj.qty_available + sobj.outgoing_qty
        return True

    @api.multi
    def _set_supplier(self, rec_id, value):
        prod = self.browse(rec_id)
        if prod.product_tmpl_id.id not in [s.name for s in prod.seller_ids]:
            self.env['product.supplierinfo'].create({'name': value, 'product_id': prod.product_tmpl_id.id, 'min_qty': 0, 'delay': 1})
        return True

    # ORM
    @api.model
    def default_get(self, fields):
        resp = super(product_product, self).default_get(fields)
        resp.update({
            'default_code': self._generate_default_sku(),
            'categ_id': False,
            'type': 'product',
            'company_id': False,
            'warranty': 12,
            'list_price': 0
        })

        return resp

    @api.multi
    def write(self, vals):
        if vals.get('description'):
            for u_char in [u'\u2028']:
                if u_char in vals['description']:
                    vals['description'] = vals['description'].replace(u_char, '')

        if 'state' in vals:
            vals['state_last_updated'] = time.strftime('%Y-%m-%d')
            if vals['state'] == 'obsolete':
                vals['date_obsolete'] = time.strftime('%Y-%m-%d')
            else:
                vals['date_obsolete'] = False

        ret_val = super(product_product, self).write(vals)

        bom_update_products = []

        if vals.get('volume', 0) > 0.0 or vals.get('volume_storage', 0) > 0.0 or vals.get('volume_container', 0) > 0.0 or vals.get('weight', 0) > 0.0 or vals.get('weight_net', 0) > 0.0 or vals.get('standard_price', 0) > 0.0:
            # Mfg Products can not update
            for prod in self:
                if prod.bom_ids:
                    raise UserError('Can not update weight, volume and price related fields of BoM product directly, Instead should update parts')

            # Update Parent BoM
            bom_line_exist = self.env['mrp.bom.line'].search([('product_id', 'in', self.ids)])
            if bom_line_exist:
                bom_update_products += [l.bom_id.product_id.id for l in bom_line_exist]

        if bom_update_products:
            for product in self.browse(bom_update_products):
                if product.bom_ids:
                    latest_bom = product.bom_ids[-1]
                    if product.volume != latest_bom.bom_volume or product.weight != latest_bom.bom_weight or product.weight_net != latest_bom.bom_weight_net or product.standard_price != latest_bom.bom_standard_price:
                        bom_vals = {'volume': latest_bom.bom_volume, 'volume_storage': latest_bom.bom_volume_storage, 'volume_container': latest_bom.bom_volume_container, 'weight': latest_bom.bom_weight,
                                    'weight_net': latest_bom.bom_weight_net, 'standard_price': latest_bom.bom_standard_price}
                        super(models.Model, product).write(bom_vals)

        if 'state' in vals:
            for prod in self:
                ops = self.env['stock.warehouse.orderpoint'].search([('product_id', '=', prod.id), '|', ('active', '=', True), ('active', '=', False)])
                for op in ops:  # .browse(cr, SUPERUSER_ID, op_ids)
                    if prod.state == 'sellable' and op.active == False:
                        op.write({'active': True})
                    elif prod.state in ['draft', 'obsolete', 'end'] and op.active == True:
                        op.write({'active': False})

        if vals.get('replacement_product_id'):
            replacement_product = self.browse(vals['replacement_product_id'])
            replacement_product.write({'replacing_product_id': self[0].id})

        return ret_val

    @api.model
    def create(self, vals):
        if not vals.get('guid'):
            vals['guid'] = uuid.uuid4()
        return super(product_product, self).create(vals)

    @api.multi
    def copy(self, default=None):
        default = default or {}
        default.update({
            'default_code': self._generate_default_sku(),
            'case_ids': [],
            'magento_url_key': False,
            'sales_data': [],
            'log_ids': [],
            'comp_price_ids': [],
            'comp_config_lines': [],
            'guid': uuid.uuid4(),
            'case_count_ids': []
        })
        return super(models.Model, self).copy(default=default)

    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):
        if self.env.context.get('default_order'):
            order = self.env.context['default_order']

        if 'warehouse' in self._context:
            if not self._context['warehouse']:
                raise UserError('Please Select Shop/Warehouse')
            warehouse = self.env['stock.warehouse'].browse(self._context['warehouse'])
            args.append(('company_id', '=', warehouse.company_id.id))
        return super(product_product, self).search(args, offset, limit, order, count=count)

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        ret_val = []
        if 'warehouse' in self._context:
            if not self._context['warehouse']:
                raise UserError('Please Select Shop/Warehouse')
            warehouse = self.env['stock.warehouse'].browse(self._context['warehouse'])
            args.append(('company_id', '=', warehouse.company_id.id))

        if self._context.get('case_order_id'):
            case_order = self.env['sale.order'].browse(self._context['case_order_id'])
            case_prod_ids = [line.product_id.id for line in case_order.order_line if line.product_id]
            if case_prod_ids:
                args.append(('id', 'in', case_prod_ids))

        if 'po_company_id' in self._context:
            if not self._context.get('po_company_id'):
                raise UserError('Company!', 'Please Select Company')
            args.append(('company_id', '=', self._context['po_company_id']))

        if 'po_supplier_id' in self._context:
            if not self._context.get('po_supplier_id'):
                raise UserError('Supplier!', 'Please Select Supplier')
            args.append(('seller_ids.name', '=', self._context['po_supplier_id']))
            ret_val = super(product_product, self).search(args + [('seller_ids.product_code', '=ilike', name)])
            if ret_val:
                return self.name_get(ret_val)

        return super(product_product, self).name_search(name=name, args=args, operator=operator, limit=limit)

    # Actions/Links
    @api.multi
    def action_view_stock_moves(self):
        self.ensure_one()
        action = self.env.ref('stock.act_product_stock_move_open')
        product_ids = self.ids
        return {
            'name': action.name,
            'help': action.help,
            'type': action.type,
            'view_type': action.view_type,
            'view_mode': action.view_mode,
            'res_model': action.res_model,
            'target': action.target,
            'context': "{'default_product_id': " + str(product_ids[0]) + "}",
            'domain': "[('product_id','in',[" + ','.join(map(str, product_ids)) + "])]"
        }
        return result

    @api.multi
    def action_view_orderpoints(self):
        self.ensure_one()
        action = self.env.ref('stock.product_open_orderpoint')
        product_ids = self.ids
        return {
            'name': action.name,
            'help': action.help,
            'type': action.type,
            'view_type': action.view_type,
            'view_mode': action.view_mode,
            'res_model': action.res_model,
            'target': action.target,
            'context': action.context,
            'domain': "{'default_product_id': " + str(product_ids[0]) + ", 'search_default_product_id': " + str(product_ids[0]) + "}"
        }
        return result

    @api.multi
    def action_view_sales(self):
        self.ensure_one()
        action = self.env.ref('sale.action_product_sale_list')
        product_ids = self.ids
        return {
            'name': action.name,
            'help': action.help,
            'type': action.type,
            'view_type': action.view_type,
            'view_mode': action.view_mode,
            'target': action.target,
            'context': "{'default_product_id': " + str(product_ids[0]) + "}",
            'res_model': action.res_model,
            'domain': [('state', 'in', ['sale', 'done']), ('product_id', '=', self.id)],
        }

    @api.multi
    def action_open_quants(self):
        product_ids = self.ids
        action = self.env.ref('stock.product_open_quants')
        return {
            'name': action.name,
            'help': action.help,
            'type': action.type,
            'view_type': action.view_type,
            'view_mode': action.view_mode,
            'target': action.target,
            'context': "{'search_default_locationgroup': 1, 'search_default_internal_loc': 1}",
            'res_model': action.res_model,
            'domain': "[('product_id','in',[" + ','.join(map(str, product_ids)) + "])]",
        }

    # Crons
    @api.model
    def price_change_email_cron(self):

        last_date = self.env['ir.config_parameter'].get_param('last.change.price.email')
        if not last_date:
            last_date = time.strftime('%Y-%m-1')

        _logger.info('Price Change Email Method: %s' % last_date)

        self._cr.execute("""
                    WITH price_activity as
                    (
                        (
                            SELECT
                                DISTINCT on (p.default_code)
                                p.default_code,
                                t.name,
                                l.date::date as date_activity,
                                value_before::numeric,
                                value_after::numeric,
                                t.list_price,
                                p.special_price,
                                l.activity,
                                CASE WHEN now()::date BETWEEN special_from_date AND special_to_date then True ELSE FALSE END as special_active
                            FROM
                                res_activity_log l,
                                product_product p,
                                product_template t
                            WHERE
                                l.res_id = p.id AND
                                p.product_tmpl_id = t.id AND
                                t.sale_ok = True AND
                                l.date>='%s' and
                                l.activity in ('Standard Price', 'Special Price') and l.res_model='product.product'
                            order by default_code, date desc
                        )
                        UNION ALL
                        (
                            SELECT
                                p.default_code,
                                t.name,
                                p.special_to_date as date_activity,
                                p.special_price as value_before,
                                t.list_price as value_after,
                                t.list_price,
                                p.special_price,
                                'Special Price End' as activity,
                                CASE WHEN now()::date BETWEEN special_from_date AND special_to_date then True ELSE FALSE END as special_active
                            FROM
                                product_product p,
                                product_template t
                            WHERE
                                p.product_tmpl_id = t.id AND
                                t.sale_ok = True AND
                                special_price > 0 AND
                                special_to_date is not null AND
                                special_to_date between '%s' AND now()
                        )
                        UNION ALL
                        (
                            SELECT
                                p.default_code,
                                t.name,
                                p.special_from_date as date_activity,
                                t.list_price as value_before,
                                p.special_price as value_after,
                                t.list_price,
                                p.special_price,
                                'Special Price Start' as activity,
                                CASE WHEN now()::date BETWEEN special_from_date AND special_to_date then True ELSE FALSE END as special_active
                            FROM
                                product_product p,
                                product_template t
                            WHERE
                                p.product_tmpl_id = t.id AND
                                t.sale_ok = True AND
                                special_price > 0 AND
                                special_from_date is not null AND
                                special_from_date between '%s' AND now()
                        )
                    )
                    SELECT DISTINCT on (name) * from price_activity order by name, date_activity desc;""" % (last_date, last_date, last_date))
        resp = self._cr.dictfetchall()
        if not resp:
            _logger.info('No data for email')
            return True

        msg = '''


                <table style="font-family: "Lucida Sans Unicode", "Lucida Grande", Sans-Serif; font-size: 12px; background: #fff; margin: 45px; border-collapse: collapse; text-align: left;" width="550px">
                    <tr><th colspan="2" align="center" style="font-size: 14px; font-weight: normal; color: #039; padding: 10px 8px;">Price Change %s</th></tr>''' % last_date

        current_default_code = ''
        for rec in resp:
            if current_default_code != rec['default_code']:
                if rec['special_price'] > 0:
                    if rec['special_active'] == True:
                        curr_price = 'Current Price: <strike>$%s</strike>  $%s' % (rec['list_price'], rec['special_price'])
                    else:
                        curr_price = 'Current Price: $%s  <strike>$%s</strike>' % (rec['list_price'], rec['special_price'])
                else:
                    curr_price = 'Current Price: $%s' % rec['list_price']

                msg += '''
                            <tr><td colspan="2">&nbsp;</td></tr>
                            <tr>
                                <th colspan="2" style="text-align:left; font-size: 14px; font-weight: normal; color: #039; padding: 10px 8px; border-top: 2px solid #6678b1;">[%s]  %s  &nbsp; <br/><br/><span style="font-size:12px">%s</span> </th>
                            </tr>''' % (rec['default_code'], rec['name'], curr_price)
                current_default_code = rec['default_code']

            msg += '''<tr>
                        <td style="color: #669; padding: 9px 8px 0px 8px;">%s</td>
                        <td style="color: #669; padding: 9px 8px 0px 8px;">%s from $%s to $%s</td>
                     </tr>''' % (rec['date_activity'], rec['activity'], rec['value_before'] or 0, rec['value_after'] or 0)

        msg += '</table>'

        msg_vals = {
            'subject': "Price Change",
            'body_html': msg,
            'email_from': "support@tradetested.co.nz",
            'email_to': "pricechanges@tradetested.co.nz",
            'state': 'outgoing',
            'model': False,
            'res_id': False,
            'auto_delete': False,
        }

        self.env['mail.mail'].create(msg_vals)
        self.env['ir.config_parameter'].set_param("last.change.price.email", time.strftime('%Y-%m-%d'))

        return True

    @api.model
    def update_special_price_cron(self, min_days=0):
        _logger.info('Update Special Price Cron, Min. Days %s' % min_days)
        # 1. Extend existing specials

        mag_update = []

        today = datetime.strptime(time.strftime('%Y-%m-%d'), '%Y-%m-%d')
        days_to_add = 6 - today.weekday()
        if days_to_add <= 0:
            days_to_add += 7
        next_sunday = today + relativedelta(days=days_to_add, hour=10, minute=59)  # 10:59 AM in UTC will be 10:59 or 11:59 (DST) PM in NZST

        self._cr.execute(
            """SELECT pp.id FROM product_product pp, product_template pt WHERE pp.product_tmpl_id = pt.id AND pt.state!='obsolete' AND pp.active=True AND pp.special_price > 0 AND pp.special_to_date between now() and now() + interval '7 days' """)
        resp = self._cr.fetchall()
        if resp:
            to_update = []
            for prod in self.env['product.product'].browse([x[0] for x in resp]):
                period_2 = filter(lambda p: p.sequence == -2, prod.sales_data)
                if (prod.po_expected_qty > 0) or (period_2 and period_2[0].purchase_qty == 0) or (prod.saleable_qty > prod.recent_velocity * 6):
                    to_update.append(prod.id)

            if to_update:
                self._cr.execute('''UPDATE product_product SET special_to_date = '%s' WHERE id in (%s)''' % (next_sunday, ",".join(map(str, to_update))))
                mag_update += to_update

            _logger.info('Special Price to Date extended for %s Products' % len(to_update))

        # 2. Set New Specials

        if min_days:
            to_date = datetime.strptime(time.strftime('%Y-%m-%d'), '%Y-%m-%d') + relativedelta(days=min_days)
            days_to_add = 6 - to_date.weekday()  # if this procedure executed in weekdays, then will pick the next sunday for special to date, execution on sunday won't make difference  6 - 6 = 0
            to_date_sunday = to_date + relativedelta(days=days_to_add, hour=11, minute=59)  # 11:59 AM in UTC will be 11:59 PM in NZST

            self._cr.execute(
                """SELECT pp.id FROM product_product pp, product_template pt WHERE pp.product_tmpl_id = pt.id AND pt.state!='obsolete' AND pp.active=True AND pp.special_price > 0 AND ( pp.special_to_date < now() or pp.special_to_date is null) """)
            resp = self._cr.fetchall()
            if resp:
                to_update = []
                for prod in self.env['product.product'].browse([x[0] for x in resp]):
                    period_2 = filter(lambda p: p.sequence == -2, prod.sales_data)
                    if (prod.po_expected_qty > 0) or (period_2 and period_2[0].purchase_qty == 0) or (prod.saleable_qty > prod.recent_velocity * 6):
                        to_update.append(prod.id)

                if to_update:
                    self._cr.execute("""UPDATE product_product SET special_from_date = '%s', special_to_date = '%s' WHERE id in (%s)""" % (time.strftime('%Y-%m-%d %H:%M:00'), to_date_sunday, ",".join(map(str, to_update))))
                    mag_update += to_update

                _logger.info('"Special To" set for %s Products' % len(to_update))
        else:
            _logger.error('Please set min_days value in Scheduler')

        if mag_update:
            pass
            # TODO: Magento Export
            # self.env['tradetested.exporters.product'].export_products(mag_update, ['special'])

        return True

    @api.model
    def month_end_report_cron(self):

        def get_stock_report(company_name):
            buf = cStringIO.StringIO()

            headers = ['SKU', 'Name', 'Status', 'Quantity On Hand', 'Incoming', 'Outgoing', 'Special Price', 'Sale Price', 'Cost']
            buf.write(','.join(map(lambda x: '"' + str(x) + '"', headers)) + "\n")

            for prod in self.env['product.product'].search([('route_ids.name', '=', 'Buy'), ('company_id.name', '=', company_name)]):
                print prod.name, prod.default_code
                data = [
                    prod.default_code and prod.default_code.encode('utf-8') or '',
                    prod.name.encode('utf-8'),
                    prod.state and common.product_status[prod.state] or '',
                    str(prod.qty_available),
                    str(prod.incoming_qty),
                    str(prod.outgoing_qty),
                    str(prod.special_price),
                    str(prod.list_price),
                    str(prod.standard_price)
                ]
                buf.write(','.join(map(lambda x: '"' + str(x) + '"', data)) + "\n")
            file = base64.encodestring(buf.getvalue())
            buf.close()
            return file

        def get_purchase_report(company_name):

            buf = cStringIO.StringIO()
            headers = ['Reference', 'Supplier', 'Company', 'Status', 'Balance of Payments']
            buf.write(','.join(map(lambda x: '"' + str(x) + '"', headers)) + "\n")

            for po in self.env['purchase.order'].search([
                ('company_id.name', '=', company_name),
                ('order_line.received', '!=', True),
                ('state', '!=', 'cancel'),
                '|',
                ('held', '=', False),
                ('held_date', '<', time.strftime('%Y-%m-%d'))
            ]):
                data = [
                    po.name,
                    po.partner_id.name,
                    po.company_id.name,
                    po.state,
                    str(po.payments_total_less_refunds)
                ]
                buf.write(','.join(map(lambda x: '"' + str(x) + '"', data)) + "\n")
            file = base64.encodestring(buf.getvalue())
            buf.close()
            return file

        def send_mail(subject, file):
            file_name = subject.replace(' ', '_') + '.csv'
            msg_vals = {
                'subject': subject,
                'body_html': subject,
                'email_from': "support@tradetested.co.nz",
                'email_to': "accounts@tradetested.co.nz",
                'state': 'outgoing',
                'model': False,
                'res_id': False,
                'auto_delete': False,
                'attachment_ids': [(0, 0, {'name': file_name, 'datas_fname': file_name, 'datas': file})]
            }
            self.env['mail.mail'].create(msg_vals)

        file = get_stock_report('New Zealand')
        send_mail('Stock on Hand: New Zealand', file)

        file = get_stock_report('Australia')
        send_mail('Stock on Hand: Australia', file)

        file = get_purchase_report('New Zealand')
        send_mail('Prepayments: New Zealand', file)

        file = get_purchase_report('Australia')
        send_mail('Prepayments: Australia', file)
