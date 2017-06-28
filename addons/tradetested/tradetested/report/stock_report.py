from odoo import tools, api, fields, models
import odoo.addons.decimal_precision as dp
from odoo.exceptions import UserError


class stock_analysis(models.Model):
    _name = 'stock.analysis'
    _auto = False
    _table = 'stock_analysis'

    id = fields.Integer('ID')
    product_id = fields.Many2one('product.product', 'Product')
    categ_id = fields.Many2one('product.category', 'Category')
    default_code = fields.Char('SKU', size=64)
    location_id = fields.Many2one('stock.location', 'Location')
    qty_available = fields.Float('Quantity on Hand')
    virtual_available = fields.Float('Forecasted Quantity')
    qty_available_value = fields.Float('Quantity on Hand Value')
    virtual_available_value = fields.Float('Forecasted Value')
    supply_method = fields.Selection([('produce', 'Manufacture'), ('buy', 'Buy')], 'Supply Method', required=True)
    usage = fields.Selection([
        ('supplier', 'Supplier Location'),
        ('view', 'View'),
        ('internal', 'Internal Location'),
        ('customer', 'Customer Location'),
        ('inventory', 'Inventory'),
        ('procurement', 'Procurement'),
        ('production', 'Production'),
        ('transit', 'Transit Location for Inter-Companies Transfers')], 'Location Type', required=True, index=True)
    purchase_ok = fields.Boolean('Can be Purchased')
    sale_ok = fields.Boolean('Can be Sold')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'stock_analysis')
        self._cr.execute('''
            create or replace view stock_analysis as (
                with stock_moves_unified as
                (
                    select
                        id,
                        product_id,
                        product_qty,
                        location_dest_id as loc_id,
                        state,
                        CASE
                            WHEN
                                state='done'
                            THEN
                                product_qty
                            ELSE 0
                        END as qty_available
                    from
                        stock_move
                    where
                        state != 'cancel'
                Union
                    select
                        id,
                        product_id,
                        -product_qty,
                        location_id as loc_id,
                        state,
                        CASE
                            WHEN
                                state='done'
                            THEN
                                -product_qty
                            ELSE 0
                        END as qty_available
                    from
                        stock_move
                    where
                        state != 'cancel'
                )
                SELECT
                    row_number() over (order by pr.default_code) as id,
                    pr.default_code,
                    product_id as product_id,
                    pt.categ_id as categ_id,
                    loc_id as location_id,
                    null as supply_method,
                    pt.sale_ok,
                    pt.purchase_ok,
                    loc.usage as usage,

                    sum(qty_available) as qty_available,
                    sum(qty_available) * pr.cost as qty_available_value,
                    sum(product_qty) as virtual_available,
                    sum(product_qty) * pr.cost as virtual_available_value
                from
                    stock_moves_unified sm,
                    stock_location loc,
                    product_product pr,
                    product_template pt
                WHERE
                    sm.loc_id=loc.id AND
                    sm.product_id = pr.id AND
                    pr.product_tmpl_id = pt.id
                group by
                    loc_id,
                    sm.product_id,
                    pt.categ_id,
                    pr.default_code,
                    pt.sale_ok,
                    pt.purchase_ok,
                    loc.usage,
                    pr.cost
                order by
                    pr.default_code
                )''')

    # loc_id as location_id,
    # pt.supply_method as supply_method, -- TODO supply_method

    # pt.standard_price change to pr.cost

    # TODO Eroor in filter Can be Purchase and Can be Sold

    @api.multi
    def unlink(self):
        raise UserError('You cannot delete this record....!!')


class report_stock_move(models.Model):
    _name = "report.stock.move"
    _description = "Moves Statistics"
    _auto = False

    date = fields.Date('Date', readonly=True)
    year = fields.Char('Year', size=4, readonly=True)
    day = fields.Char('Day', size=128, readonly=True)
    month = fields.Selection([('01', 'January'), ('02', 'February'), ('03', 'March'), ('04', 'April'),
                              ('05', 'May'), ('06', 'June'), ('07', 'July'), ('08', 'August'), ('09', 'September'),
                              ('10', 'October'), ('11', 'November'), ('12', 'December')], 'Month', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Partner', readonly=True)
    product_id = fields.Many2one('product.product', 'Product', readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True)
    picking_id = fields.Many2one('stock.picking', 'Shipment', readonly=True)
    type = fields.Selection([('out', 'Sending Goods'), ('in', 'Getting Goods'), ('internal', 'Internal'), ('other', 'Others')], 'Shipping Type', required=True, index=True, help="Shipping type specify, goods coming in or going out.")
    location_id = fields.Many2one('stock.location', 'Source Location', readonly=True, index=True, help="Sets a location if you produce at a fixed location. This can be a partner location if you subcontract the manufacturing operations.")
    location_dest_id = fields.Many2one('stock.location', 'Dest. Location', readonly=True, index=True, help="Location where the system will stock the finished products.")
    state = fields.Selection([('draft', 'Draft'), ('waiting', 'Waiting'), ('confirmed', 'Confirmed'), ('assigned', 'Available'), ('done', 'Done'), ('cancel', 'Cancelled')], 'Status', readonly=True, index=True)
    product_qty = fields.Integer('Quantity', readonly=True)
    categ_id = fields.Many2one('product.category', 'Product Category', )
    product_qty_in = fields.Integer('In Qty', readonly=True)
    product_qty_out = fields.Integer('Out Qty', readonly=True)
    value = fields.Float('Total Value', required=True)
    day_diff2 = fields.Float('Lag (Days)', readonly=True, digits=dp.get_precision('Shipping Delay'), group_operator="avg")
    day_diff1 = fields.Float('Planned Lead Time (Days)', readonly=True, digits=dp.get_precision('Shipping Delay'), group_operator="avg")
    day_diff = fields.Float('Execution Lead Time (Days)', readonly=True, digits=dp.get_precision('Shipping Delay'), group_operator="avg")
    same_location = fields.Integer('Same Location')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'report_stock_move')
        self._cr.execute("""
            CREATE OR REPLACE view report_stock_move AS (
                SELECT
                        min(sm_id) as id,
                        date_trunc('day',al.dp) as date,
                        al.curr_year as year,
                        al.curr_month as month,
                        al.curr_day as day,
                        al.curr_day_diff as day_diff,
                        al.curr_day_diff1 as day_diff1,
                        al.curr_day_diff2 as day_diff2,
                        al.location_id as location_id,
                        al.picking_id as picking_id,
                        al.company_id as company_id,
                        al.location_dest_id as location_dest_id,
                        (select count(DISTINCT usage) from stock_location WHERE id in (al.location_id, al.location_dest_id)) as same_location,
                        al.product_qty,
                        al.out_qty as product_qty_out,
                        al.in_qty as product_qty_in,
                        al.partner_id as partner_id,
                        al.product_id as product_id,
                        al.state as state ,
                        al.product_uom as product_uom,
                        al.categ_id as categ_id,
                        null as type, /* coalesce(al.type, 'other') as type, */
                        sum(al.in_value - al.out_value) as value
                    FROM (SELECT
                    /*
                        CASE WHEN sp.type in ('out') THEN
                            sum(sm.product_qty * pu.factor / pu2.factor)
                            ELSE 0.0
                            END AS out_qty,
                        CASE WHEN sp.type in ('in') THEN
                            sum(sm.product_qty * pu.factor / pu2.factor)
                            ELSE 0.0
                            END AS in_qty,
                        CASE WHEN sp.type in ('out') THEN
                            sum(sm.product_qty * pu.factor / pu2.factor) * sm.cost_price
                            ELSE 0.0
                            END AS out_value,
                        CASE WHEN sp.type in ('in') THEN
                            sum(sm.product_qty * pu.factor / pu2.factor) * sm.cost_price
                            ELSE 0.0
                            END AS in_value,
                            */
                            0.0 AS out_qty, 0.0 as in_qty, 0.0 as out_value, 0.0 as in_value,
                        min(sm.id) as sm_id,
                        sm.date as dp,
                        to_char(date_trunc('day',sm.date), 'YYYY') as curr_year,
                        to_char(date_trunc('day',sm.date), 'MM') as curr_month,
                        to_char(date_trunc('day',sm.date), 'YYYY-MM-DD') as curr_day,
                        avg(date(sm.date)-date(sm.create_date)) as curr_day_diff,
                        avg(date(sm.date_expected)-date(sm.create_date)) as curr_day_diff1,
                        avg(date(sm.date)-date(sm.date_expected)) as curr_day_diff2,
                        sm.location_id as location_id,
                        sm.location_dest_id as location_dest_id,
                        sum(sm.product_qty) as product_qty,
                        pt.categ_id as categ_id ,
                        sm.partner_id as partner_id,
                        sm.product_id as product_id,
                        sm.picking_id as picking_id,
                            sm.company_id as company_id,
                            sm.state as state,
                            sm.product_uom as product_uom,
                            null as type, /* sp.type as type, */
                            null as stock_journal /* sp.stock_journal_id AS stock_journal */
                    FROM
                        stock_move sm
                        LEFT JOIN stock_picking sp ON (sm.picking_id=sp.id)
                        LEFT JOIN product_product pp ON (sm.product_id=pp.id)
                        LEFT JOIN product_uom pu ON (sm.product_uom=pu.id)
                          LEFT JOIN product_uom pu2 ON (sm.product_uom=pu2.id)
                        LEFT JOIN product_template pt ON (pp.product_tmpl_id=pt.id)
                    GROUP BY
                        sm.id,
                        /* sp.type, */
                        sm.date,sm.partner_id,
                        sm.product_id,sm.state,sm.product_uom,sm.date_expected,
                        sm.product_id,sm.cost_price, sm.picking_id, sm.product_qty,
                        sm.company_id,sm.product_qty, sm.location_id,sm.location_dest_id,pu.factor,pt.categ_id
                        /* ,sp.stock_journal_id */
                        )
                    AS al
                    GROUP BY
                        al.out_qty,al.in_qty,al.curr_year,al.curr_month,
                        al.curr_day,al.curr_day_diff,al.curr_day_diff1,al.curr_day_diff2,al.dp,al.location_id,al.location_dest_id,
                        al.partner_id,al.product_id,al.state,al.product_uom,
                        al.picking_id,al.company_id,
                        /*al.type,*/
                        al.product_qty, al.categ_id
                        /* , al.stock_journal */
               )
        """)

        # TODO type is null so in filter Incoming, Internal, Outgoing and in group by Month show error


class report_stock_aged(models.Model):
    _name = 'report.stock.aged'
    _auto = False

    product_id = fields.Many2one('product.product', 'Product')
    # company_id = fields.Many2one('res.company', 'Company')
    # categ_id = fields.Many2one('product.category', 'Category')

    stock_value = fields.Float('Stock Value')
    stock_value_6_month_ago = fields.Float('Stock Value 6 Months Ago')
    stock_value_12_month_ago = fields.Float('Stock Value 12 Months Ago')
    stock_value_24_month_ago = fields.Float('Stock Value 24 Months Ago')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'report_stock_aged')
        self._cr.execute("""
            CREATE OR REPLACE view report_stock_aged AS (
                with stock_move_unified as
                (
                        SELECT
                                id,
                                product_id,
                                product_qty,
                                location_dest_id as loc_id,
                                state,
                                CASE WHEN state='done' THEN product_qty ELSE 0 END as qty_onhand,
                                date
                        FROM
                                stock_move where state not in ('cancel', 'draft')
                    UNION
                        SELECT
                                id,
                                product_id,
                                -product_qty,
                                location_id as loc_id,
                                state,
                                CASE WHEN state='done' THEN -product_qty ELSE 0 END as qty_onhand,
                                date
                        FROM
                                stock_move where state not in ('cancel', 'draft')
                ),
                stock_value as
                (
                        SELECT
                                product_id,
                                sum(product_qty) * (select cost_price from stock_move WHERE product_id = sm.product_id order by id desc limit 1) as stock_value,
                                0 as stock_value_6_month_ago,
                                0 as stock_value_12_month_ago,
                                0 as stock_value_24_month_ago
                        FROM
                                stock_move_unified sm, stock_location sl
                        WHERE
                                sm.loc_id = sl.id AND sl.usage = 'internal'
                        GROUP BY product_id
                    UNION
                        SELECT
                                product_id,
                                0 as stock_value,
                                sum(product_qty) * (select cost_price from stock_move WHERE product_id = sm.product_id AND date<now()::date - interval '6 months' order by id desc limit 1) as stock_value_6_month_ago,
                                0 as stock_value_12_month_ago,
                                0 as stock_value_24_month_ago
                        FROM
                                stock_move_unified sm, stock_location sl
                        WHERE
                                sm.loc_id = sl.id AND sl.usage = 'internal' AND
                                sm.date <= now()::date - interval '6 months'
                        GROUP BY product_id
                    UNION
                        SELECT
                                product_id,
                                0 as stock_value,
                                0 as stock_value_6_month_ago,
                                sum(product_qty) * (select cost_price from stock_move WHERE product_id = sm.product_id AND date<now()::date - interval '12 months' order by id desc limit 1) as stock_value_12_month_ago,
                                0 as stock_value_24_month_ago
                        FROM
                                stock_move_unified sm, stock_location sl
                        WHERE
                                sm.loc_id = sl.id AND sl.usage = 'internal' AND
                                sm.date <= now()::date - interval '12 months'
                        GROUP BY product_id
                    UNION
                        SELECT
                                product_id,
                                0 as stock_value,
                                0 as stock_value_6_month_ago,
                                0 as stock_value_12_month_ago,
                                sum(product_qty) * (select cost_price from stock_move WHERE product_id = sm.product_id AND date<now()::date - interval '24 months' order by id desc limit 1) as stock_value_24_months_ago
                        FROM
                                stock_move_unified sm, stock_location sl
                        WHERE
                                sm.loc_id = sl.id AND sl.usage = 'internal' AND
                                sm.date <= now()::date - interval '24 months'
                        GROUP BY product_id
                )
                SELECT
                        row_number() over ( order by product_id ) as id,
                        product_id,
                        sum(stock_value) as stock_value,
                        sum(stock_value_6_month_ago)  as stock_value_6_month_ago,
                        sum(stock_value_12_month_ago) as stock_value_12_month_ago,
                        sum(stock_value_24_month_ago) as stock_value_24_month_ago
                FROM
                        stock_value
                GROUP BY product_id
            )""")


class cost_of_goods_sold(models.Model):
    _name = 'cost.of.goods.sold'
    _auto = False

    date = fields.Date('Date', readonly=True)
    year = fields.Char('Year', size=4, readonly=True)
    day = fields.Char('Day', size=128, readonly=True)
    month = fields.Selection([('01', 'January'), ('02', 'February'), ('03', 'March'), ('04', 'April'),
                              ('05', 'May'), ('06', 'June'), ('07', 'July'), ('08', 'August'), ('09', 'September'),
                              ('10', 'October'), ('11', 'November'), ('12', 'December')], 'Month', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Partner', readonly=True)
    product_id = fields.Many2one('product.product', 'Product', readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True)
    picking_id = fields.Many2one('stock.picking', 'Shipment', readonly=True)
    type = fields.Selection([('out', 'Sending Goods'), ('in', 'Getting Goods'), ('internal', 'Internal'), ('other', 'Others')], 'Shipping Type', required=True, index=True, help="Shipping type specify, goods coming in or going out.")
    location_id = fields.Many2one('stock.location', 'Source Location', readonly=True, index=True, help="Sets a location if you produce at a fixed location. This can be a partner location if you subcontract the manufacturing operations.")
    location_dest_id = fields.Many2one('stock.location', 'Dest. Location', readonly=True, index=True, help="Location where the system will stock the finished products.")
    state = fields.Selection([('draft', 'Draft'), ('waiting', 'Waiting'), ('confirmed', 'Confirmed'), ('assigned', 'Available'), ('done', 'Done'), ('cancel', 'Cancelled')], 'Status', readonly=True, index=True)
    product_qty = fields.Integer('Quantity', readonly=True)
    categ_id = fields.Many2one('product.category', 'Product Category', )
    product_qty_in = fields.Integer('In Qty', readonly=True)
    product_qty_out = fields.Integer('Out Qty', readonly=True)
    product_qty_net = fields.Integer('Net Qty', readonly=True)
    value = fields.Float('Net Cost', required=True)
    same_location = fields.Integer('Same Location')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'cost_of_goods_sold')
        self._cr.execute("""
            CREATE OR REPLACE view cost_of_goods_sold AS (
                SELECT
                        min(sm_id) as id,
                        date_trunc('day',al.dp) as date,
                        al.curr_year as year,
                        al.curr_month as month,
                        al.curr_day as day,
                        al.location_id as location_id,
                        al.picking_id as picking_id,
                        al.company_id as company_id,
                        al.location_dest_id as location_dest_id,
                        (select count(DISTINCT usage) from stock_location WHERE id in (al.location_id, al.location_dest_id)) as same_location,
                        al.product_qty,
                        al.out_qty as product_qty_out,
                        al.in_qty as product_qty_in,
                        sum(al.out_qty + al.in_qty) as product_qty_net,
                        al.partner_id as partner_id,
                        al.product_id as product_id,
                        al.state as state ,
                        al.product_uom as product_uom,
                        al.categ_id as categ_id,
                        coalesce( 'other') as type,
                        sum(al.in_value + al.out_value) as value
                    FROM (SELECT
                        CASE WHEN sl.id in ( select lot_stock_id from stock_warehouse) THEN
                            sum(sm.product_qty * pu.factor / pu2.factor) * -1
                            ELSE 0.0
                            END AS out_qty,
                        CASE WHEN sl_dest.id in ( select lot_stock_id from stock_warehouse) THEN
                            sum(sm.product_qty * pu.factor / pu2.factor)
                            ELSE 0.0
                            END AS in_qty,
                        CASE WHEN sl.id in ( select lot_stock_id from stock_warehouse) THEN
                            sum(sm.product_qty * pu.factor / pu2.factor) * sm.cost_price * -1
                            ELSE 0.0
                            END AS out_value,
                        CASE WHEN sl_dest.id in ( select lot_stock_id from stock_warehouse) THEN
                            sum(sm.product_qty * pu.factor / pu2.factor) * sm.cost_price
                            ELSE 0.0
                            END AS in_value,
                        min(sm.id) as sm_id,
                        sm.date as dp,
                        to_char(date_trunc('day',sm.date), 'YYYY') as curr_year,
                        to_char(date_trunc('day',sm.date), 'MM') as curr_month,
                        to_char(date_trunc('day',sm.date), 'YYYY-MM-DD') as curr_day,
                        sm.location_id as location_id,
                        sm.location_dest_id as location_dest_id,
                        sum(sm.product_qty) as product_qty,
                        pt.categ_id as categ_id ,
                        sm.partner_id as partner_id,
                        sm.product_id as product_id,
                        sm.picking_id as picking_id,
                        sm.company_id as company_id,
                        sm.state as state,
                        sm.product_uom as product_uom

                    FROM
                        stock_move sm
                        LEFT JOIN stock_picking sp ON (sm.picking_id=sp.id)
                        LEFT JOIN stock_location sl ON (sm.location_id=sl.id)
                        LEFT JOIN stock_location sl_dest ON (sm.location_dest_id=sl_dest.id)
                        LEFT JOIN product_product pp ON (sm.product_id=pp.id)
                        LEFT JOIN product_uom pu ON (sm.product_uom=pu.id)
                        LEFT JOIN product_uom pu2 ON (sm.product_uom=pu2.id)
                        LEFT JOIN product_template pt ON (pp.product_tmpl_id=pt.id)
                    WHERE
                        sm.location_id in ( select lot_stock_id from stock_warehouse) or sm.location_dest_id in (select lot_stock_id from stock_warehouse)
                    GROUP BY
                        sm.id,sl.id,sl_dest.id,sm.date,sm.partner_id,
                        sm.product_id,sm.state,sm.product_uom,sm.date_expected,
                        sm.product_id,sm.cost_price, sm.picking_id, sm.product_qty,
                        sm.company_id,sm.product_qty, sm.location_id,sm.location_dest_id,pu.factor,pt.categ_id)
                    AS al
                    GROUP BY
                        al.out_qty,al.in_qty,al.curr_year,al.curr_month,
                        al.curr_day,al.dp,al.location_id,al.location_dest_id,
                        al.partner_id,al.product_id,al.state,al.product_uom,
                        al.picking_id,al.company_id,al.product_qty, al.categ_id
               )
        """)

        # Todo sp.type as type and al.type show_error


class freight_report(models.Model):
    _name = "freight.report"
    _description = "Freight Report"
    _auto = False
    # _rec_name = 'date'

    company_id = fields.Many2one('res.company', 'Company')
    user_id = fields.Many2one('res.users', 'Salesperson')
    channel = fields.Selection([('not_defined', 'Not defined'), ('phone', 'Phone'), ('showroom', 'Showroom'), ('website', 'Website'), ('trademe', 'Trade Me'), ('ebay', 'eBay'), ('daily_deal', 'Daily Deal'), ('wholesale', 'Wholesale')],
                               'Sale channel')
    marketing_method_id = fields.Many2one('sale.order.marketing.method', 'Marketing Method')
    year = fields.Char('Year', size=4, readonly=True)
    month = fields.Selection(
        [('01', 'January'), ('02', 'February'), ('03', 'March'), ('04', 'April'), ('05', 'May'), ('06', 'June'), ('07', 'July'), ('08', 'August'), ('09', 'September'), ('10', 'October'), ('11', 'November'), ('12', 'December')], 'Month',
        readonly=True)
    day = fields.Char('Day', size=128, readonly=True)
    product_id = fields.Many2one('product.product', 'Product')
    categ_id = fields.Many2one('product.category', 'Category')
    state_id = fields.Many2one('res.country.state', 'State')
    freight_revenue = fields.Float('Freight Revenue')
    freight_cost = fields.Float('Freight Cost')
    margin = fields.Float('Margin')
    state = fields.Char('state', size=64)
    weight = fields.Float('Weight')
    order_id = fields.Many2one('sale.order', 'Sales Order')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'freight_report')
        self._cr.execute("""
            create or replace view freight_report as (
                SELECT
                    min(l.id) as id,
                    s.company_id as company_id,
                    s.user_id as user_id,
                    s.channel as channel,
                    s.marketing_method_id as marketing_method_id,
                    s.state as state,
                    s.id as order_id,
                    to_char(s.date_order, 'YYYY') as year,
                    to_char(s.date_order, 'MM') as month,
                    to_char(s.date_order, 'YYYY-MM-DD') as day,
                    l.product_id as product_id,
                    t.categ_id as categ_id,
                    part.state_id as state_id,
                    t.weight as weight,

                    CASE WHEN s.order_weight > 0 THEN
                        round(( (fl.price_subtotal/fl.product_uom_qty) * (t.weight/s.order_weight))::numeric,2 )
                    ELSE
                        fl.price_subtotal/fl.product_uom_qty
                    END as freight_revenue,

                    CASE WHEN s.order_weight > 0 THEN
                        round( (sum(sp.freight_cost) * (t.weight/s.order_weight))::numeric,2 )
                    ELSE
                        sum (sp.freight_cost)
                    END as freight_cost,

                    CASE WHEN sum(fl.price_unit) > 0 THEN
                        round(   ( sum((fl.price_subtotal/fl.product_uom_qty)) - sum(sp.freight_cost) ) / sum(fl.price_unit)    )
                    ELSE
                        0
                    END as margin

                FROM
                    sale_order s
                    join sale_order_line l on (s.id=l.order_id)
                    left join product_product p on (l.product_id=p.id)
                    left join product_template t on (p.product_tmpl_id=t.id)
                    left join sale_order_line fl on (s.id=fl.order_id and fl.product_id in (select distinct product_id from delivery_carrier) )
                    left join res_partner part on (s.partner_id=part.id)
                    left join stock_picking sp on (sp.sale_id = s.id)
                WHERE
                    s.id in (
                                select distinct order_id from sale_order_line
                                WHERE product_id in (select distinct product_id from delivery_carrier)
                            ) AND
                    s.state not in ('cancel', 'draft','sent') AND
                    fl.product_uom_qty > 0
                GROUP BY
                    s.company_id,
                    s.user_id,
                    s.channel,
                    s.marketing_method_id,
                    s.state,
                    s.date_order,
                    s.order_weight,
                    s.id,
                    l.product_id,
                    t.categ_id,
                    t.weight,
                    fl.price_subtotal,
                    fl.product_uom_qty,
                    part.state_id
            )
        """)

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        resp = super(freight_report, self).read_group(domain, fields, groupby, offset, limit, orderby)
        for rec in resp:
            if rec['freight_revenue'] > 0:
                rec['margin'] = ((rec['freight_revenue'] - rec['freight_cost']) / rec['freight_revenue'])
        return resp
