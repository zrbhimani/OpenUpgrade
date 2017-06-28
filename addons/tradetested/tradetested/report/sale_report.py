# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError
from ..models.common import payment_methods


class sale_cost(models.Model):
    _name = 'sale.cost'
    _table = 'sale_cost'
    _access_log = False
    _auto = False

    cost_price = fields.Float('Cost')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'sale_cost')
        self._cr.execute("""
            create or replace view sale_cost as (
                select
                    l.id as id,
                    COALESCE(sum(m.cost_price * m.product_qty * m.cost_sign), (l.product_uom_qty * p.cost)) as cost_price
                from sale_order_line l
                    INNER JOIN product_product p on ( l.product_id = p.id)
                    LEFT JOIN stock_move m on (m.sale_line_id = l.id )
                group by l.id, p.cost
        )""")


class sale_report(models.Model):
    _inherit = 'sale.report'
    _auto = False

    revenue = fields.Float(string='Total Revenue')
    payment = fields.Selection([('partial', 'Partial'), ('full', 'Full')], string="Payment")
    frequency = fields.Selection([('first', 'First Time'), ('repeat', 'Repeat')], string="Customer Frequency")
    product_supplier_id = fields.Many2one('product.product.supplier', compute='_get_product_supplier', string="Supplier", fnct_search='_search_supplier')

    price_total_untaxed = fields.Float('Total Sales', readonly=True)
    cost_total = fields.Float('Total Cost', readonly=True)
    profit = fields.Float('Profit', digits=(20, 1), group_operator="avg")

    month = fields.Selection([('01', 'January'), ('02', 'February'), ('03', 'March'), ('04', 'April'),
                               ('05', 'May'), ('06', 'June'), ('07', 'July'), ('08', 'August'), ('09', 'September'),
                               ('10', 'October'), ('11', 'November'), ('12', 'December')], 'Month', readonly=True)
    delay = fields.Float('Commitment Delay', digits=(16, 2), readonly=True)

    @api.model
    def _get_product_supplier(self):
        return dict([(id, False) for id in self.ids])

    def _search_supplier(self, cursor, user, obj, name, args, comain=None, context=None):
        name_arg = filter(lambda x: x[0] == 'product_supplier_id', args)
        if name_arg:
            cursor.execute("""SELECT
                                        p.id
                                FROM
                                        product_product p,
                                        product_template t,
                                        product_supplierinfo psi
                                WHERE
                                        psi.product_id = t.id AND
                                        p.product_tmpl_id = t.id AND
                                        psi.name=%s""" % (name_arg[0][2]))
            resp = [('product_id', 'in', [x[0] for x in cursor.fetchall()])]
            return resp
        return []

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'sale_report')
        self._cr.execute("""
            create or replace view sale_report as (
                select
                    min(l.id) as id,
                    l.product_id as product_id,
                    t.uom_id as product_uom,
                    sum(l.product_uom_qty / u.factor * u2.factor) as product_uom_qty,
                    sum(l.product_uom_qty * l.price_unit * (100.0-l.discount) / 100.0) as price_total,
                    sum( ((l.product_uom_qty * l.price_unit * (100.0-l.discount) / 100.0)/s.amount_total) * payments_total_less_refunds ) as revenue,
                    CASE
                        WHEN ((s.payments_total_less_refunds > 0) AND (s.payments_total_less_refunds < s.amount_total)) THEN 'partial'
                        WHEN (s.payments_total_less_refunds >= s.amount_total) THEN 'full'
                        ELSE null
                    END AS payment,

                    l.price_subtotal as price_total_untaxed,
                    CASE WHEN (SELECT True FROM stock_move WHERE sale_line_id = l.id and picking_id is not null limit 1) THEN
                        (SELECT sum(cost_price * product_qty) from stock_move WHERE sale_line_id = l.id and picking_id is not null)
                    ELSE
                        sum( l.product_uom_qty * p.cost )
                    END as cost_total,

                    CASE WHEN (SELECT True FROM stock_move WHERE sale_line_id = l.id and picking_id is not null limit 1) AND ( sum( l.price_subtotal ) > 0 )
                            THEN ( (l.price_subtotal - (SELECT sum(cost_price * product_qty) from stock_move WHERE sale_line_id = l.id and picking_id is not null) )  / l.price_subtotal ) * 100
                        WHEN ( sum( l.price_subtotal ) > 0 )
                            THEN ( (l.price_subtotal - sum( l.product_uom_qty * p.cost ) ) / l.price_subtotal ) * 100
                        ELSE
                            0
                    END as profit,

                    1 as nbr,
                    s.date_order as date,
                    s.date_confirm as date_confirm,
                    to_char(s.date_order, 'YYYY') as year,
                    to_char(s.date_order, 'MM') as month,
                    to_char(s.date_order, 'YYYY-MM-DD') as day,
                    s.partner_id as partner_id,
                    s.user_id as user_id,



                    s.company_id as company_id,
                    extract(epoch from avg(date_trunc('day',s.date_confirm)-date_trunc('day',s.create_date)))/(24*60*60)::decimal(16,2) as delay,
                    s.state,
                    t.categ_id as categ_id,



                    s.pricelist_id as pricelist_id,
                    s.project_id as analytic_account_id,
                    s.frequency as frequency
                from
                    sale_order s
                    join sale_order_line l on (s.id=l.order_id)
                        left join product_product p on (l.product_id=p.id)
                            left join product_template t on (p.product_tmpl_id=t.id)
                    left join product_uom u on (u.id=l.product_uom)
                    left join product_uom u2 on (u2.id=t.uom_id)
                WHERE
                    s.amount_total>0
                group by
                    l.id,
                    l.product_id,
                    l.product_uom_qty,
                    l.order_id,
                    l.price_subtotal,
                    t.uom_id,
                    t.categ_id,
                    s.date_order,
                    s.date_confirm,
                    s.partner_id,
                    s.user_id,



                    s.company_id,
                    s.state,



                    s.pricelist_id,
                    s.project_id,
                    s.amount_total,
                    s.payments_total_less_refunds,
                    s.frequency
            )
        """)

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        # if 'price_total_untaxed' not in fields:
        #     fields.append('price_total_untaxed')
        #
        # if 'cost_total' not in fields:
        #     fields.append('cost_total')

        resp = super(sale_report, self).read_group(domain, fields, groupby, offset, limit, orderby, lazy)
        #
        # for rec in resp:
        #     if rec['price_total_untaxed'] > 0:
        #         rec['profit'] = ((rec['price_total_untaxed'] - rec['cost_total']) / rec['price_total_untaxed']) * 100.0
        return resp


class payment_report(models.Model):
    _name = 'payment.report'
    _auto = False
    _table = 'payment_report'

    type = fields.Selection((('payment', 'Payment'), ('refund', 'Refund')), 'Type')
    method = fields.Selection(payment_methods, 'Method', required=True)
    name = fields.Char('Sales Order', size=64)
    order_id = fields.Many2one('sale.order', 'Sales Order')
    partner_id = fields.Many2one('res.partner', 'Customer')
    date = fields.Datetime('Date')
    date_order = fields.Date('Date')
    user_id = fields.Many2one('res.users', 'Salesperson')
    comment = fields.Char('Comment')
    amount = fields.Float('Amount', digits=(11, 2))
    company_id = fields.Many2one('res.company', 'Company')
    reviewed = fields.Boolean('Reviewed')
    reviewed_date = fields.Date('Reviewed Date')
    reviewer_id = fields.Many2one('res.users', 'Reviewed by')
    payment_id = fields.Many2one('sale.order.payment', 'Payment')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'payment_report')
        self._cr.execute('''
            create or replace view payment_report as (
                select
                    sop.id as id,
                    sop.type,
                    sop.method,
                    so.name,
                    so.id as order_id,
                    so.partner_id,
                    sop.date,
                    so.date_order,
                    sop.user_id,
                    sop.comment,
                    sop.amount,
                    so.company_id,
                    sop.reviewed,
                    sop.reviewed_date,
                    sop.reviewer_id,
                    sop.id as payment_id
                FROM
                    sale_order so,
                    sale_order_payment sop
                WHERE
                    sop.sale_order_id = so.id
                order by
                    sop.date desc NULLS LAST
        )''')

    @api.multi
    def open_payment(self):
        sobj = self[0]
        view_id = self.env['ir.model.data'].xmlid_to_res_id('tradetested.view_sale_order_payment_form_readonly')
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'sale.order.payment',
            'res_id': sobj.payment_id.id,
            'target': 'new',
            'view_id': view_id,
        }

    @api.multi
    def open_order(self):
        sobj = self[0]
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'sale.order',
            'res_id': sobj.order_id.id,
            'target': 'new',
        }


class sale_profit(models.Model):
    @api.model
    def _get_product_supplier(self):
        return dict([(id, False) for id in self.ids])

    def _search_supplier(self, cursor, user, obj, name, args, comain=None, context=None):
        name_arg = filter(lambda x: x[0] == 'product_supplier_id', args)
        if name_arg:
            cursor.execute("""SELECT
                                        p.id
                                FROM
                                        product_product p,
                                        product_template t,
                                        product_supplierinfo psi
                                WHERE
                                        psi.product_id = t.id AND
                                        p.product_tmpl_id = t.id AND
                                        psi.name=%s""" % (name_arg[0][2]))
            resp = [('product_id', 'in', [x[0] for x in cursor.fetchall()])]
            return resp
        return []

    _name = 'sale.profit'
    _description = 'Product Profitability'
    _auto = False

    date = fields.Date('Date Order', readonly=True)
    date_confirm = fields.Date('Date Confirm', readonly=True)
    year = fields.Char('Year', size=4, readonly=True)
    month = fields.Selection([('01', 'January'), ('02', 'February'), ('03', 'March'), ('04', 'April'),
                              ('05', 'May'), ('06', 'June'), ('07', 'July'), ('08', 'August'), ('09', 'September'),
                              ('10', 'October'), ('11', 'November'), ('12', 'December')], 'Month', readonly=True)
    day = fields.Char('Day', size=128, readonly=True)
    product_id = fields.Many2one('product.product', 'Product', readonly=True)
    categ_id = fields.Many2one('product.category', 'Category of Product', readonly=True)
    default_code = fields.Char('SKU')
    sale_ok = fields.Boolean('Can be Sold')
    purchase_ok = fields.Boolean('Can be Purchased')
    active = fields.Boolean('Active')
    company_id = fields.Many2one('res.company', 'Company', readonly=True)
    price_total = fields.Float('Total Sales', readonly=True)
    cost_total = fields.Float('Total Cost', readonly=True)
    profit = fields.Float('Profit', digits=(20, 1), group_operator="avg")
    product_uom = fields.Many2one('product.uom', 'Unit of Measure', readonly=True)
    product_uom_qty = fields.Float('# of Qty', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Partner', readonly=True)
    warehouse_id = fields.Many2one('stock.warehouse', 'Warehouse', readonly=True)
    user_id = fields.Many2one('res.users', 'Salesperson', readonly=True)
    nbr = fields.Integer('# of Lines', readonly=True)
    state = fields.Selection([
        ('draft', 'Quotation'),
        ('waiting_date', 'Waiting Schedule'),
        ('manual', 'Manual In Progress'),
        ('progress', 'In Progress'),
        ('invoice_except', 'Invoice Exception'),
        ('done', 'Done'),
        ('cancel', 'Cancelled')
    ], 'Order Status', readonly=True)
    product_supplier_id = fields.Many2one('product.product.supplier', compute='_get_product_supplier', string="Supplier", fnct_search='_search_supplier')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'sale_profit')
        self._cr.execute("""
            create or replace view sale_profit as (
                select
                    min(l.id) as id,
                    l.product_id as product_id,
                    t.categ_id as categ_id,
                    t.sale_ok as sale_ok,
                    t.purchase_ok as purchase_ok,
                    p.active as active,
                    p.default_code as default_code,
                    t.company_id as company_id,
                    t.uom_id as product_uom,
                    sum(l.product_uom_qty / u.factor * u2.factor) as product_uom_qty,
                    l.price_subtotal as price_total,
                    c.cost_price,
                    CASE WHEN sum( l.price_subtotal ) > 0 THEN
                        CASE WHEN (SELECT True from stock_move WHERE sale_line_id = l.id limit 1)  THEN
                            ((l.price_subtotal -  (SELECT sum(cost_price * product_qty * cost_sign) from stock_move WHERE sale_line_id = l.id and picking_id is not null) )  / l.price_subtotal ) * 100
                        ELSE
                            ((l.price_subtotal -  (p.cost * l.product_uom_qty))   / l.price_subtotal ) * 100
                        END
                    ELSE
                        0
                    END as profit,
                    1 as nbr,
                    s.date_order as date,
                    s.date_confirm as date_confirm,
                    to_char(s.date_order, 'YYYY') as year,
                    to_char(s.date_order, 'MM') as month,
                    to_char(s.date_order, 'YYYY-MM-DD') as day,
                    s.partner_id as partner_id,
                    s.user_id as user_id,
                    s.warehouse_id as warehouse_id,
                    s.state
                from
                    sale_order s
                    join sale_order_line l on (s.id=l.order_id)
                        left join sale_cost c on (c.id = l.id)
                        left join product_product p on (l.product_id=p.id)
                        left join product_template t on (p.product_tmpl_id=t.id)
                    left join product_uom u on (u.id=l.product_uom)
                    left join product_uom u2 on (u2.id=t.uom_id)
                WHERE
                    t.name not in ('Delivery NZ','Delivery AU','Delivery USA','Pickup')
                group by
                    l.id,
                    c.cost_price,
                    l.product_id,
                    l.product_uom_qty,
                    l.order_id,
                    t.uom_id,
                    t.categ_id,
                    t.sale_ok,
                    t.purchase_ok,
                    p.cost,
                    p.active,
                    l.price_subtotal,
                    p.default_code,
                    s.date_order,
                    s.date_confirm,
                    s.partner_id,
                    s.user_id,
                    s.warehouse_id,
                    t.company_id,
                    s.state
            )
        """)

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        resp = super(sale_profit, self).read_group(domain, fields, groupby, offset, limit, orderby, lazy)
        # for rec in resp:
        #     if rec['price_total'] > 0:
        # rec['profit'] = ((rec['price_total'] - rec['cost_total']) / rec['price_total']) * 100.0
        return resp

        # TODO standard_price
        # '''CASE WHEN (SELECT True from stock_move WHERE sale_line_id = l.id limit 1)  THEN
        #                   (SELECT sum(cost_price * product_qty * cost_sign) from stock_move WHERE sale_line_id = l.id and picking_id is not null)
        #                 ELSE
        #                   (t.standard_price * l.product_uom_qty)
        #                 END as cost_total,'''


class sale_seasonal_analysis(models.Model):
    _name = 'sale.seasonal.analysis'
    _table = 'sale_seasonal_analysis'
    _access_log = False
    _auto = False
    _order = 'quarter_sale desc'

    product_id = fields.Many2one('product.product', 'Product')
    # default_code = fields.Char('Default Code')
    # name = fields.Char('Name')
    state = fields.Selection([
        ('draft', 'Draft Quotation'),
        ('sent', 'Quotation Sent'),
        ('cancel', 'Cancelled'),
        ('waiting_date', 'Waiting Schedule'),
        ('progress', 'Sales Order'),
        ('manual', 'Sale to Invoice'),
        ('invoice_except', 'Invoice Exception'),
        ('done', 'Done'),
    ], 'Status')

    user_id = fields.Many2one('res.users', 'Salesperson')
    company_id = fields.Many2one('res.company', 'Company')
    categ_id = fields.Many2one('product.category', 'Category')
    frequency = fields.Selection([('first', 'First Time'), ('repeat', 'Repeat')], string="Customer Frequency")

    # Month
    month_sale = fields.Float('Month Sales')
    month_cost = fields.Float('Month Cost')
    month_profit = fields.Float('Month Profit')
    month_profit_rate = fields.Float('Month Profit (%)')

    last_month_sale = fields.Float('Last Month Sales')
    last_month_cost = fields.Float('Last Month Cost')
    last_month_profit = fields.Float('Last Month Profit')
    last_month_profit_rate = fields.Float('Last Month Profit (%)')

    month_sale_gain = fields.Float('Month Sales (%)')
    month_profit_gain = fields.Float('Month Profit (%)')

    # Quarter
    quarter_sale = fields.Float('Quarter Sales')
    quarter_cost = fields.Float('Quarter Cost')
    quarter_profit = fields.Float('Quarter Profit')
    quarter_profit_rate = fields.Float('Quarter Profit (%)')

    last_quarter_sale = fields.Float('Last Quarter Sales')
    last_quarter_cost = fields.Float('Last Quarter Cost')
    last_quarter_profit = fields.Float('Last Quarter Profit')
    last_quarter_profit_rate = fields.Float('Last Quarter Profit')

    quarter_sale_gain = fields.Float('Quarter Sales (%)')
    quarter_profit_gain = fields.Float('Quarter Profit (%)')

    # Year
    year_sale = fields.Float('Year Sales')
    year_cost = fields.Float('Year Cost')
    year_profit = fields.Float('Year Profit')
    year_profit_rate = fields.Float('Year Profit (%)')

    last_year_sale = fields.Float('Last Year Sales')
    last_year_cost = fields.Float('Last Year Cost')
    last_year_profit = fields.Float('Last Year Profit')
    last_year_profit_rate = fields.Float('Last Year Profit (%)')

    year_sale_gain = fields.Float('Year Sales (%)')
    year_profit_gain = fields.Float('Year Profit (%)')

    # allready_in_comment_in_7
    # month_profit = fields.Float('Month Profit')
    # quarter_profit = fields.Float('Quarter Profit')

    # sale = fields.Dummy(type='float', string='Revenue ($)')
    # profit = fields.Dummy(type='float', string='Gross Profit (%)')
    # sale_rate = fields.Dummy(type='float', string='Revenue (% YOY)')
    # profit_rate = fields.Dummy(type='float', string='Gross Profit (% YOY)')

    @api.model_cr
    def init(self):
        return self.update_view()

    @api.multi
    def update_view(self):
        if self._context.get('date_from'):
            cur_date = "'%s'::date" % (self._context['date_from'])
        else:
            cur_date = 'current_date'

        tools.drop_view_if_exists(self._cr, 'sale_seasonal_analysis')
        self._cr.execute("""
        create or replace view sale_seasonal_analysis as (
            WITH sales_data as (
                (
                    SELECT
                        l.product_id,
                        s.state,
                        s.user_id,
                        s.company_id,
                        t.categ_id,
                        s.frequency,

                        l.price_subtotal as month_sale,
                        c.cost_price as month_cost,

                        0 as last_month_sale,
                        0 as last_month_cost,

                        0 as quarter_sale,
                        0 as quarter_cost,
                        0 as last_quarter_sale,
                        0 as last_quarter_cost,

                        0 as year_sale,
                        0 as year_cost,
                        0 as last_year_sale,
                        0 as last_year_cost
                    FROM
                        sale_order_line l,
                        sale_cost c,
                        sale_order s,
                        product_product p,
                        product_template t
                    WHERE
                        l.order_id = s.id AND
                        c.id = l.id AND
                        l.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.date_order>=(%(cur_date)s - interval '28 days')::date AND
                        s.date_order<%(cur_date)s AND
                        s.state not in ('cancel','quote') AND t.type = 'product'
                )
                UNION ALL
                (
                    SELECT
                        l.product_id,
                        s.state,
                        s.user_id,
                        s.company_id,
                        t.categ_id,
                        s.frequency,

                        0 as month_sale,
                        0 as month_cost,
                        l.price_subtotal as last_month_sale,
                        c.cost_price as last_month_cost,

                        0 as quarter_sale,
                        0 as quarter_cost,
                        0 as last_quarter_sale,
                        0 as last_quarter_cost,

                        0 as year_sale,
                        0 as year_cost,
                        0 as last_year_sale,
                        0 as last_year_cost
                    FROM
                        sale_order_line l,
                        sale_cost c,
                        sale_order s,
                        product_product p,
                        product_template t
                    WHERE
                        l.order_id = s.id AND
                        c.id = l.id AND
                        l.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.date_order>=(%(cur_date)s - interval '1 year 28 days')::date AND
                        s.date_order<(%(cur_date)s - interval '1 year')::date AND
                        s.state not in ('cancel','quote') AND t.type = 'product'
                )
                UNION ALL
                (
                    SELECT
                        l.product_id,
                        s.state,
                        s.user_id,
                        s.company_id,
                        t.categ_id,
                        s.frequency,

                        0 as month_sale,
                        0 as month_cost,
                        0 as last_month_sale,
                        0 as last_month_cost,

                        l.price_subtotal as quarter_sale,
                        c.cost_price as quarter_cost,
                        0 as last_quarter_sale,
                        0 as last_quarter_cost,

                        0 as year_sale,
                        0 as year_cost,
                        0 as last_year_sale,
                        0 as last_year_cost
                    FROM
                        sale_order_line l,
                        sale_cost c,
                        sale_order s,
                        product_product p,
                        product_template t
                    WHERE
                        l.order_id = s.id AND
                        c.id = l.id AND
                        l.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.date_order>=(%(cur_date)s - interval '91 days')::date AND
                        s.date_order<%(cur_date)s AND
                        s.state not in ('cancel','quote') AND t.type = 'product'
                )
                UNION ALL
                (
                    SELECT
                        l.product_id,
                        s.state,
                        s.user_id,
                        s.company_id,
                        t.categ_id,
                        s.frequency,

                        0 as month_sale,
                        0 as month_cost,
                        0 as last_month_sale,
                        0 as last_month_cost,

                        0 as quarter_sale,
                        0 as quarter_cost,
                        l.price_subtotal as last_quarter_sale,
                        c.cost_price as last_quarter_cost,

                        0 as year_sale,
                        0 as year_cost,
                        0 as last_year_sale,
                        0 as last_year_cost

                    FROM
                        sale_order_line l,
                        sale_cost c,
                        sale_order s,
                        product_product p,
                        product_template t
                    WHERE
                        l.order_id = s.id AND
                        c.id = l.id AND
                        l.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.date_order>=(%(cur_date)s - interval '1 year 91 days')::date AND
                        s.date_order<(%(cur_date)s - interval '1 year')::date AND
                        s.state not in ('cancel','quote') AND t.type = 'product'
                )
                UNION ALL
                (
                    SELECT
                        l.product_id,
                        s.state,
                        s.user_id,
                        s.company_id,
                        t.categ_id,
                        s.frequency,

                        0 as month_sale,
                        0 as month_cost,
                        0 as last_month_sale,
                        0 as last_month_cost,

                        0 as quarter_sale,
                        0 as quarter_cost,
                        0 as last_quarter_sale,
                        0 as last_quarter_cost,

                        l.price_subtotal as year_sale,
                        c.cost_price as year_cost,
                        0 as last_year_sale,
                        0 as last_year_cost

                    FROM
                        sale_order_line l,
                        sale_cost c,
                        sale_order s,
                        product_product p,
                        product_template t
                    WHERE
                        l.order_id = s.id AND
                        c.id = l.id AND
                        l.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.date_order>=(%(cur_date)s - interval '364 days')::date AND
                        s.date_order<%(cur_date)s AND
                        s.state not in ('cancel','quote') AND t.type = 'product'
                )
                UNION ALL
                (
                    SELECT
                        l.product_id,
                        s.state,
                        s.user_id,
                        s.company_id,
                        t.categ_id,
                        s.frequency,

                        0 as month_sale,
                        0 as month_cost,
                        0 as last_month_sale,
                        0 as last_month_cost,

                        0 as quarter_sale,
                        0 as quarter_cost,
                        0 as last_quarter_sale,
                        0 as last_quarter_cost,

                        0 as year_sale,
                        0 as year_cost,
                        l.price_subtotal as last_year_sale,
                        c.cost_price as last_year_cost
                    FROM
                        sale_order_line l,
                        sale_cost c,
                        sale_order s,
                        product_product p,
                        product_template t
                    WHERE
                        l.order_id = s.id AND
                        c.id = l.id AND
                        l.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.date_order>=(%(cur_date)s - interval '728 days')::date AND
                        s.date_order<(%(cur_date)s - interval '364 days')::date AND
                        s.state not in ('cancel','quote') AND t.type = 'product'
                )
            )
            SELECT
                row_number() over ( order by product_id) as id,
                product_id as product_id,
                state,
                user_id,
                categ_id,
                company_id,
                frequency,

                sum(month_sale) as month_sale,
                sum(month_cost) as month_cost,
                sum(month_sale) - sum(month_cost) as month_profit,

                sum(last_month_sale) as last_month_sale,
                sum(last_month_cost) as last_month_cost,
                sum(last_month_sale) - sum(last_month_cost) as last_month_profit,

                sum(quarter_sale) as quarter_sale,
                sum(quarter_cost) as quarter_cost,
                sum(quarter_sale) - sum(quarter_cost) as quarter_profit,

                sum(last_quarter_sale) as last_quarter_sale,
                sum(last_quarter_cost) as last_quarter_cost,
                sum(last_quarter_sale) - sum(last_quarter_cost) as last_quarter_profit,

                sum(year_sale) as year_sale,
                sum(year_cost) as year_cost,
                sum(year_sale) - sum(year_cost) as year_profit,

                sum(last_year_sale) as last_year_sale,
                sum(last_year_cost) as last_year_cost,
                sum(last_year_sale) - sum(last_year_cost) as last_year_profit,

                0 as month_profit_rate,
                0 as last_month_profit_rate,
                0 as quarter_profit_rate,
                0 as last_quarter_profit_rate,
                0 as year_profit_rate,
                0 as last_year_profit_rate,

                CASE WHEN sum(last_month_sale) != 0 THEN
                    ((sum(month_sale) - sum(last_month_sale)) / sum(last_month_sale) ) * 100.0
                ELSE 0 END as month_sale_gain,

                CASE WHEN sum(last_quarter_sale) != 0 THEN
                    ((sum(quarter_sale) - sum(last_quarter_sale)) / sum(last_quarter_sale) ) * 100.0
                ELSE 0 END as quarter_sale_gain,

                CASE WHEN sum(last_year_sale) != 0 THEN
                    ((sum(year_sale) - sum(last_year_sale)) / sum(last_year_sale) ) * 100.0
                ELSE 0 END as year_sale_gain
            FROM
                sales_data
            group by
                product_id,
                state,
                user_id,
                categ_id,
                company_id,
                frequency
            order by
                product_id )""" % {'cur_date': cur_date})

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        if self._context.get('show_year'):
            fields.extend(['year_sale', 'last_year_sale', 'year_cost', 'last_year_cost', 'year_profit', 'last_year_profit', 'year_profit_rate', 'last_year_profit_rate'])
        elif self._context.get('show_quarter'):
            fields.extend(['quarter_sale', 'last_quarter_sale', 'quarter_cost', 'last_quarter_cost', 'quarter_profit', 'last_quarter_profit', 'quarter_profit_rate', 'last_quarter_profit_rate'])
        else:
            fields.extend(['month_sale', 'month_cost', 'last_month_sale', 'last_month_cost', 'month_profit', 'last_month_profit', 'month_profit_rate', 'last_month_profit_rate'])

        resp = super(sale_seasonal_analysis, self).read_group(domain, fields, groupby, offset, limit, orderby, lazy)
        if self._context.get('show_year'):
            resp = sorted(resp, key=lambda x: x['year_sale'], reverse=True)
        elif self._context.get('show_quarter'):
            resp = sorted(resp, key=lambda x: x['quarter_sale'], reverse=True)
        else:
            resp = sorted(resp, key=lambda x: x['month_sale'], reverse=True)

        for rec in resp:

            if self._context.get('show_year'):
                if rec['year_sale'] > 0:
                    rec['year_profit_rate'] = (rec['year_profit'] / rec['year_sale']) * 100.0

                if rec['last_year_sale'] > 0:
                    rec['last_year_profit_rate'] = (rec['last_year_profit'] / rec['last_year_sale']) * 100.0

                if rec['last_year_sale'] > 0:
                    rec['year_sale_gain'] = ((rec['year_sale'] - rec['last_year_sale']) / rec['last_year_sale']) * 100.0

                if rec['last_year_profit_rate'] > 0:
                    rec['year_profit_gain'] = ((rec['year_profit_rate'] - rec['last_year_profit_rate']) / rec['last_year_profit_rate']) * 100.0

                if rec.get('year_sale', 0) > 0:
                    rec['profit'] = rec.get('year_profit', 0) / rec.get('year_sale') * 100
                else:
                    rec['profit'] = 0;

                rec['sale'] = rec.get('year_sale', 0)
                rec['sale_rate'] = rec.get('year_sale_gain', 0)
                rec['profit_rate'] = rec.get('year_profit_gain', 0)

            elif self._context.get('show_quarter'):
                if rec['quarter_sale'] > 0:
                    rec['quarter_profit_rate'] = (rec['quarter_profit'] / rec['quarter_sale']) * 100.0

                if rec['last_quarter_sale'] > 0:
                    rec['last_quarter_profit_rate'] = (rec['last_quarter_profit'] / rec['last_quarter_sale']) * 100.0

                if rec['last_quarter_sale'] > 0:
                    rec['quarter_sale_gain'] = ((rec['quarter_sale'] - rec['last_quarter_sale']) / rec['last_quarter_sale']) * 100.0

                if rec['last_quarter_profit_rate'] > 0:
                    rec['quarter_profit_gain'] = ((rec['quarter_profit_rate'] - rec['last_quarter_profit_rate']) / rec['last_quarter_profit_rate']) * 100.0

                if rec.get('quarter_sale', 0) > 0:
                    rec['profit'] = rec.get('quarter_profit', 0) / rec.get('quarter_sale') * 100
                else:
                    rec['profit'] = 0;

                rec['sale'] = rec.get('quarter_sale', 0)
                rec['sale_rate'] = rec.get('quarter_sale_gain', 0)
                rec['profit_rate'] = rec.get('quarter_profit_gain', 0)
            else:
                if rec['month_sale'] > 0:
                    rec['month_profit_rate'] = (rec['month_profit'] / rec['month_sale']) * 100.0

                if rec['last_month_sale'] > 0:
                    rec['last_month_profit_rate'] = (rec['last_month_profit'] / rec['last_month_sale']) * 100.0

                if rec['last_month_sale'] > 0:
                    rec['month_sale_gain'] = ((rec['month_sale'] - rec['last_month_sale']) / rec['last_month_sale']) * 100.0

                if rec['last_month_profit_rate'] > 0:
                    rec['month_profit_gain'] = ((rec['month_profit_rate'] - rec['last_month_profit_rate']) / rec['last_month_profit_rate']) * 100.0

                if rec.get('month_sale', 0) > 0:
                    rec['profit'] = rec.get('month_profit', 0) / rec.get('month_sale') * 100
                else:
                    rec['profit'] = 0;

                rec['sale'] = rec.get('month_sale', 0)
                rec['sale_rate'] = rec.get('month_sale_gain', 0)
                rec['profit_rate'] = rec.get('month_profit_gain', 0)

        return resp

    @api.multi
    def read(self, fields=None, load='_classic_read'):

        res = super(sale_seasonal_analysis, self).read(fields=fields, load=load)
        if self._context.get('show_year'):
            for rec in res:
                rec['sale_rate'] = rec.get('year_sale_gain', 0)
                rec['profit_rate'] = rec.get('year_profit_rate', 0)
        elif self._context.get('show_quarter'):
            for rec in res:
                rec['sale_rate'] = rec.get('quarter_sale_gain', 0)
                rec['profit_rate'] = rec.get('quarter_profit_rate', 0)
        else:
            for rec in res:
                rec['sale_rate'] = rec.get('month_sale_gain', 0)
                rec['profit_rate'] = rec.get('month_profit_rate', 0)
        return res


class sale_seasonal_analysis_dynamic(models.TransientModel):
    _name = 'sale.seasonal.analysis.dynamic'

    date = fields.Date('Period ended', default=lambda *a: time.strftime('%Y-%m-%d'))

    @api.multi
    def open_seasonal_report(self):
        sobj = self[0]

        seasonal_analysis_pool = self.env['sale.seasonal.analysis']
        seasonal_analysis_pool.update_view()

        return {
            'name': 'Seasonal analysis : %s' % (datetime.strptime(sobj.date, '%Y-%m-%d').strftime('%d/%m/%Y')),
            'view_type': 'form',
            'view_mode': 'tree,form,graph',
            'res_model': 'sale.seasonal.analysis',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'context': {'search_default_fltr_sales': 1, 'search_default_group_company': 1, 'search_default_group_category': 1, 'search_default_group_product': 1, 'search_default_month': 1}
        }


class sale_seasonal_forecast(models.Model):
    _name = 'sale.seasonal.forecast'
    _table = 'sale_seasonal_forecast'
    _access_log = False
    _auto = False
    _order = 'quarter_sale desc'

    product_id = fields.Many2one('product.product', 'Product')
    company_id = fields.Many2one('res.company', 'Company')
    categ_id = fields.Many2one('product.category', 'Category')

    # Month
    next_month_sale = fields.Float('Month Sales')
    next_month_cost = fields.Float('Month Cost')
    next_month_profit = fields.Float('Month Profit')
    next_month_profit_rate = fields.Float('Month Profit (%)')

    month_sale = fields.Float('Month Sales')
    month_cost = fields.Float('Month Cost')
    month_profit = fields.Float('Month Profit')
    month_profit_rate = fields.Float('Month Profit (%)')

    month_sale_gain = fields.Float('Month Sales (%)')
    month_profit_gain = fields.Float('Month Profit (%)')

    # Quarter
    next_quarter_sale = fields.Float('Quarter Sales')
    next_quarter_cost = fields.Float('Quarter Cost')
    next_quarter_profit = fields.Float('Quarter Profit')
    next_quarter_profit_rate = fields.Float('Quarter Profit (%)')

    quarter_sale = fields.Float('Quarter Sales')
    quarter_cost = fields.Float('Quarter Cost')
    quarter_profit = fields.Float('Quarter Profit')
    quarter_profit_rate = fields.Float('Quarter Profit (%)')

    quarter_sale_gain = fields.Float('Quarter Sales (%)')
    quarter_profit_gain = fields.Float('Quarter Profit (%)')

    # Year
    next_year_sale = fields.Float('Year Sales')
    next_year_cost = fields.Float('Year Cost')
    next_year_profit = fields.Float('Year Profit')
    next_year_profit_rate = fields.Float('Year Profit (%)')

    year_sale = fields.Float('Year Sales')
    year_cost = fields.Float('Year Cost')
    year_profit = fields.Float('Year Profit')
    year_profit_rate = fields.Float('Year Profit (%)')

    year_sale_gain = fields.Float('Year Sales (%)')
    year_profit_gain = fields.Float('Year Profit (%)')

    # sale = fields.Dummy(type='float', string='Revenue ($)')
    # profit = fields.Dummy(type='float', string='Gross Profit (%)')
    # sale_rate = fields.Dummy(type='float', string='Revenue (% YOY)')
    # profit_rate = fields.Dummy(type='float', string='Gross Profit (% YOY)')


    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'sale_seasonal_forecast')
        self._cr.execute("""
        create or replace view sale_seasonal_forecast as (
            WITH sales_data as (
                (
                    SELECT
                        p.id as product_id,
                        t.company_id,
                        t.categ_id,
                        CASE WHEN p.special_price > 0 THEN
                            s.avg_for_estimate * p.special_price
                        ELSE
                            s.avg_for_estimate * t.list_price
                        END as next_month_sale,
                        s.avg_for_estimate * p.cost as next_month_cost,
                        0 as next_quarter_sale,
                        0 as next_quarter_cost,
                        0 as next_year_sale,
                        0 as next_year_cost,

                        0 as month_sale,
                        0 as month_cost,
                        0 as quarter_sale,
                        0 as quarter_cost,
                        0 as year_sale,
                        0 as year_cost
                    FROM
                        product_product p,
                        product_template t,
                        product_sales_data s
                    WHERE
                        s.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.sequence = -1
                )
                UNION ALL
                (
                    SELECT
                        p.id as product_id,
                        t.company_id,
                        t.categ_id,

                        0 as next_month_sale,
                        0 as next_month_cost,
                        CASE WHEN p.special_price > 0 THEN
                            s.avg_for_estimate * p.special_price
                        ELSE
                            s.avg_for_estimate * t.list_price
                        END as next_quarter_sale,
                        s.avg_for_estimate * p.cost as next_quarter_cost,
                        0 as next_year_sale,
                        0 as next_year_cost,

                        0 as month_sale,
                        0 as month_cost,
                        0 as quarter_sale,
                        0 as quarter_cost,
                        0 as year_sale,
                        0 as year_cost
                    FROM
                        product_product p,
                        product_template t,
                        product_sales_data s
                    WHERE
                        s.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.sequence in (-1,-2,-3)
                )
                UNION ALL
                (
                    SELECT
                        p.id as product_id,
                        t.company_id,
                        t.categ_id,

                        0 as next_month_sale,
                        0 as next_month_cost,
                        0 as next_quarter_sale,
                        0 as next_quarter_cost,
                        CASE WHEN p.special_price > 0 THEN
                            s.avg_for_estimate * p.special_price
                        ELSE
                            s.avg_for_estimate * t.list_price
                        END as next_year_sale,
                        s.avg_for_estimate * p.cost as next_year_cost,

                        0 as month_sale,
                        0 as month_cost,
                        0 as quarter_sale,
                        0 as quarter_cost,
                        0 as year_sale,
                        0 as year_cost
                    FROM
                        product_product p,
                        product_template t,
                        product_sales_data s
                    WHERE
                        p.product_tmpl_id = t.id AND
                        s.product_id = p.id and
                        s.sequence < 0
                )
                UNION ALL
                (
                    SELECT
                        l.product_id,
                        s.company_id,
                        t.categ_id,

                        0 as next_month_sale,
                        0 as next_month_cost,
                        0 as next_quarter_sale,
                        0 as next_quarter_cost,
                        0 as next_year_sale,
                        0 as next_year_cost,

                        l.price_subtotal as month_sale,
                        CASE WHEN (SELECT True from stock_move WHERE sale_line_id = l.id limit 1)  THEN
                          (SELECT sum(cost_price * product_qty * cost_sign) from stock_move WHERE sale_line_id = l.id and picking_id is not null)
                        ELSE
                          (p.cost * l.product_uom_qty)
                        END as month_cost,
                        0 as quarter_sale,
                        0 as quarter_cost,
                        0 as year_sale,
                        0 as year_cost
                    FROM
                        sale_order_line l,
                        sale_order s,
                        product_product p,
                        product_template t
                    WHERE
                        l.order_id = s.id AND
                        l.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.date_order>=(current_date - interval '28 days')::date AND
                        s.date_order<current_date AND
                        s.state not in ('cancel','quote') AND t.type = 'product'
                )
                UNION ALL
                (
                    SELECT
                        l.product_id,
                        s.company_id,
                        t.categ_id,

                        0 as next_month_sale,
                        0 as next_month_cost,
                        0 as next_quarter_sale,
                        0 as next_quarter_cost,
                        0 as next_year_sale,
                        0 as next_year_cost,

                        0 as month_sale,
                        0 as month_cost,
                        l.price_subtotal as quarter_sale,
                        CASE WHEN (SELECT True from stock_move WHERE sale_line_id = l.id and picking_id is not null limit 1)  THEN
                          (SELECT sum(cost_price * product_qty * cost_sign) from stock_move WHERE sale_line_id = l.id and picking_id is not null)
                        ELSE
                          (p.cost * l.product_uom_qty)
                        END as quarter_cost,
                        0 as year_sale,
                        0 as year_cost
                    FROM
                        sale_order_line l,
                        sale_order s,
                        product_product p,
                        product_template t
                    WHERE
                        l.order_id = s.id AND
                        l.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.date_order>=(current_date - interval '91 days')::date AND
                        s.date_order<current_date AND
                        s.state not in ('cancel','quote') AND t.type = 'product'
                )
                UNION ALL
                (
                    SELECT
                        l.product_id,
                        s.company_id,
                        t.categ_id,

                        0 as next_month_sale,
                        0 as next_month_cost,
                        0 as next_quarter_sale,
                        0 as next_quarter_cost,
                        0 as next_year_sale,
                        0 as next_year_cost,


                        0 as month_sale,
                        0 as month_cost,
                        0 as quarter_sale,
                        0 as quarter_cost,
                        l.price_subtotal as year_sale,
                        CASE WHEN (SELECT True from stock_move WHERE sale_line_id = l.id and picking_id is not null limit 1)  THEN
                          (SELECT sum(cost_price * product_qty * cost_sign) from stock_move WHERE sale_line_id = l.id and picking_id is not null)
                        ELSE
                          (p.cost * l.product_uom_qty)
                        END as year_cost
                    FROM
                        sale_order_line l,
                        sale_order s,
                        product_product p,
                        product_template t
                    WHERE
                        l.order_id = s.id AND
                        l.product_id = p.id AND
                        p.product_tmpl_id = t.id AND
                        s.date_order>=(current_date - interval '364 days')::date AND
                        s.date_order<current_date AND
                        s.state not in ('cancel','quote') AND t.type = 'product'
                )
            )
            SELECT
                row_number() over ( order by product_id) as id,
                product_id as product_id,
                categ_id,
                company_id,

                sum(next_month_sale) as next_month_sale,
                sum(next_month_cost) as next_month_cost,
                sum(next_month_sale) - sum(next_month_cost) as next_month_profit,
                sum(next_quarter_sale) as next_quarter_sale,
                sum(next_quarter_cost) as next_quarter_cost,
                sum(next_quarter_sale) - sum(next_quarter_cost) as next_quarter_profit,
                sum(next_year_sale) as next_year_sale,
                sum(next_year_cost) as next_year_cost,
                sum(next_year_sale) - sum(next_year_cost) as next_year_profit,

                sum(month_sale) as month_sale,
                sum(month_cost) as month_cost,
                sum(month_sale) - sum(month_cost) as month_profit,
                sum(quarter_sale) as quarter_sale,
                sum(quarter_cost) as quarter_cost,
                sum(quarter_sale) - sum(quarter_cost) as quarter_profit,
                sum(year_sale) as year_sale,
                sum(year_cost) as year_cost,
                sum(year_sale) - sum(year_cost) as year_profit,

                0 as month_profit_rate,
                0 as quarter_profit_rate,
                0 as year_profit_rate,
                0 as next_month_profit_rate,
                0 as next_quarter_profit_rate,
                0 as next_year_profit_rate,
                0 as profit,
                0 as sale,

                CASE WHEN sum(month_sale) != 0 THEN
                    ((sum(next_month_sale) - sum(month_sale)) / sum(month_sale) ) * 100.0
                ELSE 0 END as month_sale_gain,

                CASE WHEN sum(quarter_sale) != 0 THEN
                    ((sum(next_quarter_sale) - sum(quarter_sale)) / sum(quarter_sale) ) * 100.0
                ELSE 0 END as quarter_sale_gain,

                CASE WHEN sum(year_sale) != 0 THEN
                    ((sum(next_year_sale) - sum(year_sale)) / sum(year_sale) ) * 100.0
                ELSE 0 END as year_sale_gain,

                0 as month_profit_gain,
                0 as quarter_profit_gain,
                0 as year_profit_gain

            FROM
                sales_data
            group by
                product_id,
                categ_id,
                company_id
            order by
                product_id )""")

    # TODO change
    # v7_ t.standard_price
    # v10 p.cost

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        if self._context.get('show_year'):
            fields.extend(['year_sale', 'next_year_sale', 'year_cost', 'next_year_cost', 'year_profit', 'next_year_profit', 'year_profit_rate', 'next_year_profit_rate'])
        elif self._context.get('show_quarter'):
            fields.extend(['quarter_sale', 'next_quarter_sale', 'quarter_cost', 'next_quarter_cost', 'quarter_profit', 'next_quarter_profit', 'quarter_profit_rate', 'next_quarter_profit_rate'])
        else:
            fields.extend(['month_sale', 'month_cost', 'next_month_sale', 'next_month_cost', 'month_profit', 'next_month_profit', 'month_profit_rate', 'next_month_profit_rate'])

        resp = super(sale_seasonal_forecast, self).read_group(domain, fields, groupby, offset, limit, orderby, lazy)
        if self._context.get('show_year'):
            resp = sorted(resp, key=lambda x: x['year_sale'], reverse=True)
        elif self._context.get('show_quarter'):
            resp = sorted(resp, key=lambda x: x['quarter_sale'], reverse=True)
        else:
            resp = sorted(resp, key=lambda x: x['month_sale'], reverse=True)

        for rec in resp:

            if self._context.get('show_year'):
                if rec['next_year_sale'] > 0:
                    rec['next_year_profit_rate'] = (rec['next_year_profit'] / rec['next_year_sale']) * 100.0

                if rec['year_sale'] > 0:
                    rec['year_profit_rate'] = (rec['year_profit'] / rec['year_sale']) * 100.0

                if rec['year_sale'] > 0:
                    rec['year_sale_gain'] = ((rec['next_year_sale'] - rec['year_sale']) / rec['year_sale']) * 100.0

                if rec['year_profit_rate'] > 0:
                    rec['year_profit_gain'] = ((rec['next_year_profit_rate'] - rec['year_profit_rate']) / rec['year_profit_rate']) * 100.0

                if rec.get('next_year_sale', 0) > 0:
                    rec['profit'] = rec.get('next_year_profit', 0) / rec.get('next_year_sale') * 100
                else:
                    rec['profit'] = 0

                rec['sale'] = rec.get('next_year_sale', 0)
                rec['sale_rate'] = rec.get('year_sale_gain', 0)
                rec['profit_rate'] = rec.get('year_profit_gain', 0)

            elif self._context.get('show_quarter'):
                if rec['next_quarter_sale'] > 0:
                    rec['next_quarter_profit_rate'] = (rec['next_quarter_profit'] / rec['next_quarter_sale']) * 100.0

                if rec['quarter_sale'] > 0:
                    rec['quarter_profit_rate'] = (rec['quarter_profit'] / rec['quarter_sale']) * 100.0

                if rec['quarter_sale'] > 0:
                    rec['quarter_sale_gain'] = ((rec['next_quarter_sale'] - rec['quarter_sale']) / rec['quarter_sale']) * 100.0

                if rec['quarter_profit_rate'] > 0:
                    rec['quarter_profit_gain'] = ((rec['next_quarter_profit_rate'] - rec['quarter_profit_rate']) / rec['quarter_profit_rate']) * 100.0

                if rec.get('next_quarter_sale', 0) > 0:
                    rec['profit'] = rec.get('next_quarter_profit', 0) / rec.get('next_quarter_sale') * 100
                else:
                    rec['profit'] = 0

                rec['sale'] = rec.get('next_quarter_sale', 0)
                rec['sale_rate'] = rec.get('quarter_sale_gain', 0)
                rec['profit_rate'] = rec.get('quarter_profit_gain', 0)
            else:
                if rec['next_month_sale'] > 0:
                    rec['next_month_profit_rate'] = (rec['next_month_profit'] / rec['next_month_sale']) * 100.0

                if rec['month_sale'] > 0:
                    rec['month_profit_rate'] = (rec['month_profit'] / rec['month_sale']) * 100.0

                if rec['month_sale'] > 0:
                    rec['month_sale_gain'] = ((rec['next_month_sale'] - rec['month_sale']) / rec['month_sale']) * 100.0

                if rec['month_profit_rate'] > 0:
                    rec['month_profit_gain'] = ((rec['next_month_profit_rate'] - rec['month_profit_rate']) / rec['month_profit_rate']) * 100.0

                if rec.get('next_month_sale', 0) > 0:
                    rec['profit'] = rec.get('next_month_profit', 0) / rec.get('next_month_sale') * 100
                else:
                    rec['profit'] = 0

                rec['sale'] = rec.get('next_month_sale', 0)
                rec['sale_rate'] = rec.get('month_sale_gain', 0)
                rec['profit_rate'] = rec.get('month_profit_gain', 0)

        return resp

    @api.multi
    def read(self, fields=None, load='_classic_read'):

        res = super(sale_seasonal_forecast, self).read(fields=fields, load=load)
        if self._context.get('show_year'):
            for rec in res:
                rec['sale'] = 0
                rec['profit'] = 0
                rec['sale_rate'] = rec.get('year_sale_gain', 0)
                rec['profit_rate'] = rec.get('year_profit_rate', 0)
        elif self._context.get('show_quarter'):
            for rec in res:
                rec['sale'] = 0
                rec['profit'] = 0
                rec['sale_rate'] = rec.get('quarter_sale_gain', 0)
                rec['profit_rate'] = rec.get('quarter_profit_rate', 0)
        else:
            for rec in res:
                rec['sale'] = 0
                rec['profit'] = 0
                rec['sale_rate'] = rec.get('month_sale_gain', 0)
                rec['profit_rate'] = rec.get('month_profit_rate', 0)
        return res


class sale_mtd_report(models.Model):
    _name = 'sale.mtd.report'
    _table = 'sale_mtd_report'
    _access_log = False
    _auto = False

    company_id = fields.Many2one('res.company', 'Company')
    # date_from = fields.Dummy(type='char', string='Date From')
    # date_to = fields.Dummy(type='char', string='Date to')

    mtd = fields.Float('MTD')
    pm_mtd = fields.Float('vs PM MTD(%)')
    py_mtd = fields.Float('vs PY MTD(%)')
    pm = fields.Float('PM')
    py = fields.Float('PY')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'sale_mtd_report')
        self._cr.execute("""
            create or replace view sale_mtd_report as (
                select
                    id as id,
                    id as company_id,
                    0 as mtd,
                    0 as pm_mtd,
                    0 as py_mtd,
                    0 as pm,
                    0 as py
                from
                    res_company
                WHERE id in (1,3)
            )""")

    @api.multi
    def read(self, fields=None, load='_classic_read'):
        resp = super(sale_mtd_report, self).read(fields=fields, load=load)

        date_from = time.strftime('%Y-%m-01')
        date_to = (datetime.now() - relativedelta(days=1)).strftime('%Y-%m-%d')
        try:
            if self._context.get('date_from') and self._context.get('date_to'):
                date_from = datetime.strptime(self._context['date_from'], '%m/%d/%Y').strftime('%Y-%m-%d')
                date_to = datetime.strptime(self._context['date_to'], '%m/%d/%Y').strftime('%Y-%m-%d')
        except ValueError, e:
            raise UserError('Date Format', 'Please provide dates in MM/DD/YYYY Format')

        date_year_from = (datetime.strptime(date_from, '%Y-%m-%d') - relativedelta(years=1)).strftime('%Y-%m-%d')
        date_year_to = (datetime.strptime(date_to, '%Y-%m-%d') - relativedelta(years=1)).strftime('%Y-%m-%d')

        date_month_from = (datetime.strptime(date_from, '%Y-%m-%d') - relativedelta(months=1)).strftime('%Y-%m-%d')
        date_month_to = (datetime.strptime(date_to, '%Y-%m-%d') - relativedelta(months=1)).strftime('%Y-%m-%d')

        self._cr.execute("""
            select company_id, sum(amount_total) amount, 'MTD' as period from sale_order WHERE state not in ('cancel','quote') and (date_order>='%s' and date_order<='%s') GROUP BY company_id
            UNION
            select company_id, sum(amount_total) amount, 'PY' as period from sale_order WHERE state not in ('cancel','quote') and (date_order>='%s' and date_order<='%s') GROUP BY company_id
            UNION
            select company_id, sum(amount_total) amount, 'PM' as period from sale_order WHERE state not in ('cancel','quote') and (date_order>='%s' and date_order<='%s') GROUP BY company_id
        """ % (date_from, date_to, date_year_from, date_year_to, date_month_from, date_month_to))

        sql_res = dict([((x['company_id'], x['period']), x['amount']) for x in self._cr.dictfetchall()])

        for rec in resp:
            company_id = rec['company_id'][0]
            rec['mtd'] = sql_res.get((company_id, 'MTD'), 0)
            pm = sql_res.get((company_id, 'PM'), 0)
            py = sql_res.get((company_id, 'PY'), 0)
            rec['pm_mtd'] = pm > 0 and round((rec['mtd'] - pm) / pm, 2) * 100 or 0
            rec['py_mtd'] = py > 0 and round((rec['mtd'] - py) / py, 2) * 100 or 0
            rec['pm'] = sql_res.get((company_id, 'PM'), 0)
            rec['py'] = sql_res.get((company_id, 'PY'), 0)
        return resp


class product_sales_ranking(models.Model):
    _name = 'product.sales.ranking'
    _auto = False
    _order = 'id'

    product_id = fields.Many2one('product.product', 'Product')
    default_code = fields.Char('SKU')
    # name = fields.Char('Name')                                    #TODO still remaining due to name_template issue
    current_rank = fields.Integer('Rank')
    current_sold = fields.Integer('Units Sold (28 Days)')
    previous_rank = fields.Integer('Previous Rank')
    previous_sold = fields.Integer('Previous Sold (28 Days)')
    rank_change = fields.Integer('Rank Change')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'product_sales_ranking')
        self._cr.execute("""
               create or replace view product_sales_ranking as (
                                   WITH sales_ranking_data as(
                    WITH current_sales_data as (
                        select
                            sol.product_id,
                            sum(product_uom_qty) as units_sold
                        from
                            sale_order_line sol,
                            sale_order so
                        WHERE
                            sol.order_id = so.id AND
                            sol.state not in ('cancel','quote','draft') AND
                            so.date_order >= (now() - interval '28 days')::date AND
                            so.date_order < now()::date
                        group by
                            sol.product_id
                    ),
                    previous_sales_data as (
                        select
                            sol.product_id,
                            sum(product_uom_qty) as units_sold
                        from
                            sale_order_line sol,
                            sale_order so
                        WHERE
                            sol.order_id = so.id AND
                            sol.state not in ('cancel','quote','draft') AND
                            so.date_order >= (now() - interval '56 days')::date AND
                            so.date_order < (now() - interval '28 days')::date
                        group by
                            sol.product_id
                    )
                    select
                        pp.default_code as default_code,

                        pt.categ_id,
                        pt.company_id,
                        csd.units_sold as current_sold,
                        psd.units_sold as previous_sold,
                        rank() OVER (ORDER BY coalesce(csd.units_sold,0) DESC) as current_rank,
                        rank() OVER (ORDER BY coalesce(psd.units_sold,0) DESC) as previous_rank,
                        CASE WHEN coalesce(psd.units_sold,0) <= 0 THEN
                            null
                        ELSE
                            (rank() OVER (ORDER BY coalesce(psd.units_sold,0) DESC)) - (rank() OVER (ORDER BY coalesce(csd.units_sold,0) DESC))
                        END as rank_change
                    from
                        product_product pp
                    LEFT JOIN product_template pt on (pp.product_tmpl_id = pt.id)
                    LEFT JOIN current_sales_data csd ON (pp.id = csd.product_id)
                    LEFT JOIN previous_sales_data psd ON (pp.id = psd.product_id)
                    WHERE
                        pt.type != 'service' AND pt.sale_ok = True
                    order by rank_change desc NULLS LAST
                )SELECT row_number() over (order by current_rank NULLS LAST) as id, * from sales_ranking_data
               )""")

        # TODO still_remaining_  name_template
        # select
        # pp.default_code as default_code,
        # pp.name_template as name,

        # WHERE active=True and pt.type


class product_cases(models.Model):
    _name = 'product.cases'
    _auto = False
    _order = 'bayesian desc'

    product_id = fields.Many2one('product.product', 'Product', readonly=True)
    company_id = fields.Many2one('res.company', 'Company')
    categ_id = fields.Many2one('product.category', 'Category')
    default_code = fields.Char('SKU')
    name = fields.Char('Name')
    sold = fields.Integer('Sold (6 months)')
    sold_distributed = fields.Float('Sold distributed in lines')
    product_case_count = fields.Integer('Product Cases count')
    product_case = fields.Float('% Product Cases')
    bayesian = fields.Float('Bayesian')

    # issue_id = fields.Many2one('crm.lead.tag', 'Issue')
    # resolution_id = fields.Many2one('crm.helpdesk.resolution','Resolution')
    # all_case_count = fields.Integer('All Cases count')
    # all_case = fields.Float('% All Cases')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'product_cases')
        self._cr.execute("""
            create or replace view product_cases as (
                WITH products_sold as (
                    SELECT
                        pp.default_code,
                        pt.name as name,
                        pt.categ_id,
                        pt.company_id as company_id,
                        pp.id as product_id,
                        sum(sol.product_uom_qty) as sold,
                        avg(sum(sol.product_uom_qty)) over () as sold_avg
                    FROM
                        product_product pp, product_template pt, sale_order_line sol, sale_order so
                    WHERE
                        pp.product_tmpl_id = pt.id AND
                        sol.product_id = pp.id AND
                        sol.order_id = so.id AND
                        so.state not in ('quote','cancel') AND
                        pt.type != 'service' AND
                        pp.active=True AND
                        so.date_order > now() - interval'6 months'
                    GROUP BY
                        pp.id,
                        pp.default_code,
                        pt.name,
                        pt.categ_id,
                        pt.company_id
                    order by pp.id
                ), case_count as (
                    SELECT
                        ch.product_id,
                        cg.product_case as product_case,
                        count(ch.id) as case_count
                    FROM
                        crm_helpdesk ch, crm_lead_tag cg, sale_order so
                    WHERE
                        ch.categ_id = cg.id AND
                        ch.order_id = so.id AND
                        so.date_order > now() - interval'6 months' and
                        so.state not in ('quote','cancle')
                    GROUP BY
                        ch.product_id,
                        cg.product_case
                ),
                rpt_data as (
                    select
                        ps.default_code,
                        ps.name,
                        ps.categ_id,
                        ps.company_id,
                        ps.product_id,
                        ps.sold,
                        ps.sold_avg,
                        sum(CASE WHEN cc.product_case = True THEN cc.case_count ELSE 0 END ) as product_case_count,
                        CASE WHEN ps.sold > 0 THEN
                            round((sum(CASE WHEN cc.product_case = True THEN cc.case_count ELSE 0 END ) / ps.sold) * 100, 2)
                        END as product_case,
                        avg (CASE WHEN ps.sold > 0 THEN
                            round((sum(CASE WHEN cc.product_case = True THEN cc.case_count ELSE 0 END ) / ps.sold) * 100, 2)
                        END) over () as product_case_avg,
                        row_number() over (order by ps.product_id) as id,
                        ps.sold/count(*) over (partition by ps.product_id) as sold_distributed
                    from
                        products_sold ps
                    LEFT JOIN
                        case_count cc on ( ps.product_id = cc.product_id )
                    group by
                          ps.default_code, ps.name, ps.categ_id, ps.company_id, ps.product_id, ps.sold, ps.sold_avg
                ) select
                *,
                coalesce((sold / (sold + sold_avg)) * product_case + (sold_avg / (sold + sold_avg))  * product_case_avg,0) as bayesian
                from rpt_data
            )""")

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        self._cr.execute("select %s, round(sum(sold_distributed)) from product_cases group by %s" % (groupby[0], groupby[0]))
        sql_resp = dict(self._cr.fetchall())
        resp = super(product_cases, self).read_group(domain, fields, groupby, offset, limit, orderby, lazy)
        for rec in resp:
            if rec[groupby[0]]:
                rec['sold'] = sql_resp.get(rec[groupby[0]][0])
                if rec['sold']:
                    rec['product_case'] = (rec['product_case_count'] / rec['sold']) * 100

        return resp

    @api.multi
    def open_product(self):
        sobj = self[0]
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'product.product',
            'res_id': sobj.product_id.id,
            'target': 'current',
        }


class report_sale_period_target(models.Model):
    _name = 'report.sale.period.target'
    _auto = False

    period_id = fields.Many2one('sale.period', 'Target Period')
    user_id = fields.Many2one('res.users', 'User')
    sales = fields.Float('Sales')
    pro_rata_target = fields.Float('Pro Rata Target')
    target = fields.Float('Target')
    unpaid_sales = fields.Float('Unpaid orders + Quotes')
    weeks_left = fields.Integer('Weeks left in quarter')
    target_rate = fields.Float('%age of target')
    pre_order_value = fields.Float('$ value of pre order with in stock products')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'report_sale_period_target')
        self._cr.execute("""
            create or replace view report_sale_period_target as (
                SELECT
                    row_number() over() as id,
                    p.id as period_id,
                    t.user_id as user_id,
                    SUM(
                        CASE WHEN so.payments_total_less_refunds > 0 and so.state not in ('cancel', 'quote')  THEN so.amount_total ELSE 0 END
                    ) as sales,
                    SUM(
                        CASE WHEN so.payments_total_less_refunds <= 0 and so.state !='cancel' THEN so.amount_total ELSE 0 END
                    ) as unpaid_sales,
                    CASE WHEN now()::date <= p.period_end THEN
                        (t.amount / (p.period_end - p.period_start)) * (now()::date - p.period_start)
                    ELSE 0 END as pro_rata_target,
                    t.amount as target,
                    CASE WHEN now() <= p.period_end THEN
                        (EXTRACT(days FROM (p.period_end - now())) / 7)::int
                    ELSE 0 END as weeks_left,
                    round(SUM(
                        CASE WHEN so.payments_total_less_refunds > 0 and so.state not in ('cancel', 'quote') THEN so.amount_total ELSE 0 END
                    )/t.amount * 100) as target_rate,
                    0 as pre_order_value
                FROM
                    sale_period p,
                    sale_period_target t,
                    sale_order so
                WHERE
                    t.period_id = p.id AND
                    so.user_id = t.user_id AND
                    so.date_order between p.period_start AND p.period_end
                group by
                    p.id,
                    t.user_id,
                    t.amount
            )
            """)
