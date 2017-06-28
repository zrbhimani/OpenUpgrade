# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from odoo.exceptions import UserError
from odoo.exceptions import except_orm, ValidationError
import StringIO
import xlsxwriter
import base64, time
import traceback, sys
from datetime import datetime
import math

import logging
_logger = logging.getLogger('Forecast')


def median(mylist):
    if not mylist:
        return 0
    sorts = sorted(mylist)
    length = len(sorts)
    if not length % 2:
        return (sorts[length / 2] + sorts[length / 2 - 1]) / 2.0
    return sorts[length / 2]

def update_sql(table, data, identifier):
    sql = list()
    sql.append("UPDATE %s SET " % table)
    sql.append(", ".join("%s = '%s'" % (k, v) if v is not None else "%s = Null" %k for k, v in data.iteritems()))
    sql.append(" WHERE ")
    sql.append(" AND ".join("%s = '%s'" % (k, v) for k, v in identifier.iteritems()))
    sql.append(";")
    return "".join(sql)


class product_category_sales(models.Model):
    _name = 'product.category.sales'
    _log_access = False
    _order = 'start_date desc'

    sequence = fields.Integer('Sequence')
    period = fields.Char('Period', size=128)
    start_date = fields.Date('Start Date')
    end_date = fields.Date('End Date')
    categ_id = fields.Many2one('product.category', 'Category')
    unique_sales = fields.Integer('Unique Products Sold U(t)')
    total_sales = fields.Integer('Total Products Sold C(t)')
    avg_sales = fields.Float('Av. Per product A(t)')

    @api.multi
    def generate_category_sales(self):
        if self._context.get('categ_id'):
            categ_ids = [self._context['categ_id']]
        else:
            categ_ids = self.env['product.category'].search([]).ids

        for cat_id in categ_ids:
            sql = '''
                WITH categ_data as (
                    WITH periods as
                    (
                        select
                            d::timestamp without time zone as end_date,
                            (d + '-27 days')::timestamp without time zone as start_date,
                            to_char(d + '-27 days', 'dd Mon YY') || to_char(d, ' - dd Mon YY') as period,
                            c.id as categ_id
                        FROM
                            generate_series(
                                (select (now()::timestamp with time zone at time zone 'Pacific/Auckland' - interval '1 day')::date), 
                                (select (now()::timestamp with time zone at time zone 'Pacific/Auckland' - interval '1 day')::date) - interval '727 day', '-28 day') as dates(d)
                            CROSS JOIN ( select id from product_category WHERE id in (%s)) c
                        ORDER By categ_id, start_date desc
                    ),
                    categ_sales_data as
                    (
                        (
                            SELECT
                                pt.categ_id as categ_id,
                                sp.date_order as date_order,
                                sm.product_id as product_id,
                                sm.product_qty as product_uom_qty
                            FROM
                                stock_picking sp,
                                stock_move sm,
                                product_product pp,
                                product_template pt,
                                product_category pc
                            WHERE
                                sm.picking_id = sp.id AND
                                sm.product_id = pp.id AND
                                pp.product_tmpl_id = pt.id AND
                                pt.categ_id = pc.id AND
                                sp.state not in ('cancel') AND
                                sm.is_profit = True AND
                                sp.type = 'out' AND
                                pt.categ_id in (%s)

                        )
                        UNION ALL
                        (
                            SELECT
                                pt.categ_id as categ_id,
                                so.date_order as date_order,
                                sol.product_id as product_id,
                                sol.product_uom_qty as product_uom_qty
                            FROM
                                sale_order so,
                                sale_order_line sol,
                                product_product pp,
                                product_template pt,
                                product_category pc
                            WHERE
                                sol.order_id = so.id AND
                                sol.product_id = pp.id AND
                                pp.product_tmpl_id = pt.id AND
                                pt.categ_id = pc.id AND
                                /*sol.price_unit >= pt.standard_price AND*/
                                NOT EXISTS (SELECT 1 FROM stock_picking WHERE sale_id = so.id) AND
                                so.state != 'cancel' AND
                                not (so.state in ('draft','quote') and so.payments_total_less_refunds<=0) AND
                                pt.categ_id in (%s)
                        )
                    )
                    SELECT
                            row_number() over ( partition by p.categ_id order by start_date desc) as sequence,
                            p.start_date,
                            p.end_date,
                            p.period,
                            p.categ_id as categ_id,
                            count(DISTINCT d.product_id) as unique_sales,
                            sum(d.product_uom_qty) as total_sales,
                            CASE WHEN count(DISTINCT d.product_id) > 0 THEN
                                    (sum(d.product_uom_qty) / count(DISTINCT d.product_id))
                            ELSE
                                    0
                            END as avg_sales
                    FROM
                        periods p
                        LEFT JOIN categ_sales_data d on (d.date_order >= p.start_date and d.date_order <= p.end_date)
                        GROUP BY p.start_date, p.end_date, p.period, p.categ_id
                ),
                upsert as
                (
                    update product_category_sales cs
                        set avg_sales = cd.avg_Sales,
                            end_date = cd.end_date,
                            sequence = cd.sequence,
                            categ_id = cd.categ_id,
                            total_sales = cd.total_sales,
                            period = cd.period,
                            unique_sales = cd.unique_sales,
                            start_date = cd.start_date
                    FROM categ_data cd
                    WHERE cs.sequence = cd.sequence and cs.categ_id = cd.categ_id
                    RETURNING cs.*
                )
                INSERT INTO product_category_sales (avg_sales, end_date, sequence, categ_id, total_sales, period, unique_sales, start_date)
                SELECT avg_sales, end_date, sequence, categ_id, total_sales, period, unique_sales, start_date
                FROM categ_data
                WHERE NOT EXISTS (SELECT 1 FROM upsert up WHERE up.categ_id = categ_data.categ_id AND up.sequence = categ_data.sequence)
                ''' % (cat_id, cat_id, cat_id)

            self._cr.execute(sql)


class product_sales(models.Model):
    _name = 'product.sales.data'
    _log_access = False
    _order = 'product_id_int, sequence'

    sequence = fields.Integer('Sequence')
    period = fields.Char('Period', size=128)
    start_date = fields.Date('Start Date')
    end_date = fields.Date('End Date')
    product_id_int = fields.Integer('Product ID')
    categ_id = fields.Many2one('product.category', 'Category')
    product_sales = fields.Integer('Product Sales S(t)')
    unique_sales = fields.Integer('Unique Products Sold U(t)', group_operator="max")
    total_sales = fields.Integer('Total Products Sold C(t)', group_operator="max")
    avg_sales = fields.Float('Av. Per product A(t)', group_operator="max")
    percent_of_avg = fields.Float('% of average V(t)', group_operator="avg")
    avg_for_estimate = fields.Float('Avg for Estimate', group_operator="max")
    forecasted = fields.Boolean('Forecasted', default=False)
    incoming_qty = fields.Integer('Incoming Stock')
    remaining_qty = fields.Integer('Remaining Stock')
    purchase_qty = fields.Integer('Purchase Qty')
    error_msg = fields.Char('Err.', size=8)
    method = fields.Char('Method', size=32)

    @api.multi
    def generate_product_sales(self, prod, fc_datas_all):

        replacement_prod_ids = prod.get_replacement_products()
        replacing_prod_ids = prod.get_replacing_products()

        repl_prod_ids = list(set([prod.id] + replacement_prod_ids + replacing_prod_ids))
        repl_prod_ids_str = ",".join(map(str, repl_prod_ids))

        self._cr.execute("""
                        WITH periods as
                        (
                            select
                                d::timestamp without time zone as end_date,
                                (d + '-27 days')::timestamp without time zone as start_date,
                                to_char(d + '-27 days', 'dd Mon YY') || to_char(d, ' - dd Mon YY') as period,
                                pp.id as product_id
                            FROM
                                generate_series((SELECT (now()::timestamp with time zone at time zone 'Pacific/Auckland' - interval '1 day')::date), 
                                (select (now()::timestamp with time zone at time zone 'Pacific/Auckland' - interval '1 day')::date) - interval '727 day', '-28 day') as dates(d)
                                CROSS JOIN ( select id from product_product WHERE id in (%s)) pp
                        ),
                        product_sales_data as
                        (
                            (
                                SELECT
                                    pt.categ_id as categ_id,
                                    sp.date_order as date_order,
                                    sm.product_id as product_id,
                                    sm.product_qty as product_uom_qty
                                FROM
                                    stock_picking sp,
                                    stock_move sm,
                                    product_product pp,
                                    product_template pt,
                                    product_category pc
                                WHERE
                                    sm.picking_id = sp.id AND
                                    sm.product_id = pp.id AND
                                    pp.product_tmpl_id = pt.id AND
                                    pt.categ_id = pc.id AND
                                    sp.state not in ('cancel') AND
                                    sm.is_profit = True AND
                                    sp.type = 'out' AND
                                    sm.product_id in (select distinct product_id from periods)
                            )
                            UNION ALL
                            (
                                SELECT
                                    pt.categ_id as categ_id,
                                    so.date_order as date_order,
                                    sol.product_id as product_id,
                                    sol.product_uom_qty as product_uom_qty
                                FROM
                                    sale_order so,
                                    sale_order_line sol,
                                    product_product pp,
                                    product_template pt,
                                    product_category pc
                                WHERE
                                    sol.order_id = so.id AND
                                    sol.product_id = pp.id AND
                                    pp.product_tmpl_id = pt.id AND
                                    pt.categ_id = pc.id AND
                                    /*sol.price_unit >= pt.standard_price AND*/
                                    NOT EXISTS (SELECT 1 FROM stock_picking WHERE sale_id = so.id) AND
                                    so.state != 'cancel' AND
                                    not (so.state in ('draft','quote') and so.payments_total_less_refunds<=0) AND
                                    sol.product_id in (select distinct product_id from periods)
                           )
                        ),
                           forecast_data as (
                            select
                                row_number() over (order by p.start_date desc) as sequence,
                                p.start_date,
                                p.end_date,
                                p.period,
                                %s as product_id,
                                COALESCE(sum(d.product_uom_qty),0)::integer as product_sales,
                                pcs.unique_sales as unique_sales,
                                pcs.total_sales as total_sales,
                                pcs.avg_sales as avg_sales,
                                CASE WHEN pcs.avg_sales > 0 THEN (sum(d.product_uom_qty) / pcs.avg_sales) ELSE 0 END as   percent_of_avg
                            from
                                periods p
                                LEFT JOIN product_sales_data d on (d.date_order >= p.start_date and d.date_order <= p.end_date and d.product_id=p.product_id)
                                LEFT JOIN product_category_sales pcs on (p.period = pcs.period AND d.categ_id = pcs.categ_id)
                            GROUP BY p.start_date, p.end_date, p.period, pcs.unique_sales, pcs.total_sales, pcs.avg_sales
                        )
                        select
                            fd.sequence,
                            start_date,
                            end_date,
                            period,
                            fd.product_id,
                            product_sales,
                            unique_sales,
                            total_sales,
                            avg_sales,
                            percent_of_avg,
                            COALESCE(sum(pol.product_qty),0)::integer as incoming_qty
                        from
                            forecast_data fd
                        LEFT JOIN purchase_order_line pol INNER JOIN purchase_order po on (pol.order_id = po.id and po.shipped=True)
                            on (pol.date_planned >= fd.start_date and pol.date_planned <= fd.end_date and pol.product_id = fd.product_id)
                        group by 1,2,3,4,5,6,7,8,9,10
                        order by sequence
                    """ % (repl_prod_ids_str, prod.id))
        fc_datas = self._cr.dictfetchall()

        # BOM Product parts
        # if prod.supply_method == 'produce':
        boms = {}
        parts_data = {}
        self._cr.execute(
            "select b.id, l.product_id, l.product_qty from mrp_bom b, mrp_bom_line l WHERE l.bom_id = b.id AND b.product_id = %s" % prod.id)
        for bom_rec in self._cr.fetchall():
            if bom_rec[0] not in boms:
                boms[bom_rec[0]] = []

            boms[bom_rec[0]].append(bom_rec[1])
            if bom_rec[1] in fc_datas_all:
                parts_data[bom_rec[1]] = dict(
                    [(psd['period'], (psd['product_sales'] or 0) / bom_rec[2]) for psd in fc_datas_all[bom_rec[1]]
                     if psd['sequence'] > 0])
            else:
                self._cr.execute(
                    "SELECT period, product_sales from product_sales_data where product_id=%s" % bom_rec[1])
                parts_data[bom_rec[1]] = dict([(psd[0], (psd[1] or 0) / bom_rec[2]) for psd in self._cr.fetchall()])

        for fc_data in fc_datas:
            if fc_data['sequence'] > 0:
                product_sales = fc_data['product_sales'] or 0
                for bom_id, bom_products in boms.items():
                    bom_sales = []
                    for bom_prod_id in bom_products:
                        bom_sales.append(parts_data[bom_prod_id][fc_data['period']])
                    product_sales += min(bom_sales)

            fc_data.update({
                'product_sales': int(product_sales),
                'percent_of_avg': product_sales / fc_data['avg_sales'] if fc_data['avg_sales'] > 0 else 0
            })

        list_of_vt = [fc_data['percent_of_avg'] for fc_data in fc_datas]

        while not list_of_vt[-1]:
            list_of_vt.pop()
            if not list_of_vt:
                break

        avg_of_vt = 0
        if list_of_vt:
            avg_of_vt = sum(list_of_vt) / float(len(list_of_vt))

        for fc_data in fc_datas:
            fc_data['avg_for_estimate'] = avg_of_vt * (fc_data['avg_sales'] or 0)

        # Velocity based on product sales data
        sales_data = [fc_data['product_sales'] for fc_data in fc_datas if fc_data['sequence'] < 14]
        recent_velocity = median([v / 4.0 for v in sales_data[:5] if v > 0])
        year_velocity = median([v / 4.0 for v in sales_data if v > 0])

        return (fc_datas, {'recent_velocity': recent_velocity, 'year_velocity': year_velocity})

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False):
        if not orderby:
            orderby = 'start_date desc'

        return super(product_sales, self).read_group(domain, fields, groupby, offset, limit, orderby)


class product_product(models.Model):
    _inherit = 'product.product'

    seasonal_multiplier = fields.Float('Seasonal Multiplier')
    forecast_velocity = fields.Float("Forecast Velocity")
    forecast_purchase_qty = fields.Float("Purchase Quantity (Forecast Model)")
    forecast_purchase_cost = fields.Float("Purchase Cost (Forecast Model)")
    recent_purchase_qty = fields.Float("Purchase Quantity (Recent Model)")
    recent_purchase_cost = fields.Float("Purchase Cost (Recent Model)")
    tex_purchase_qty = fields.Float('Purchase Quantity(TEX)')
    tex_purchase_cost = fields.Float("Purchase Cost (TEX)")

    dist_weeks = fields.Float("Weeks to sell out")
    dist_qty = fields.Float("Distressed Quantity")
    dist_cost = fields.Float("Distressed Cost")
    forecast_model = fields.Char('Model')
    afv_purchase_qty = fields.Float('Quantity')
    afv_purchase_cost = fields.Float("Cost")
    recent_velocity = fields.Float('Recent Velocity')
    year_velocity = fields.Float('Year Velocity')
    afv_yoy = fields.Float('YOY Growth')
    gmros = fields.Float('GMROS')
    gmroi = fields.Float('GMROI')
    avg_price = fields.Float('Avg Selling Price')
    avg_stock = fields.Float('Avg Stock')
    sales_data = fields.One2many('product.sales.data', 'product_id_int', 'Sales Data')


    forecasted_sales = fields.Float('Forecasted Sales')
    forecasted_sales_list = fields.Char('Forecasted Sales', size=512)
    forecast_qty = fields.Float("Forecaset Qty after Lead time")

    @api.model
    def get_products_avg_price(self, product_ids):
        self._cr.execute('''
                    SELECT
                        product_id,
                        CASE WHEN sum(product_uom_qty) > 0 THEN sum(price_subtotal)/sum(product_uom_qty)
                        ELSE 0
                        END as avg_price
                    FROM
                        sale_order_line l, sale_order so
                    WHERE
                        l.order_id = so.id AND
                        product_id in (%s) AND
                        so.date_order >= (select (now()::timestamp with time zone at time zone 'Pacific/Auckland' - interval '336 day')::date)
                    GROUP by product_id''' % ','.join(map(str, product_ids)))

        return dict((gm['product_id'], gm['avg_price']) for gm in self._cr.dictfetchall())

    @api.model
    def get_future_periods_and_po_qty(self, ids):
        self._cr.execute("""
            WITH periods as
            (
                SELECT
                    d::timestamp without time zone as start_date,
                    (d + '27 days')::timestamp without time zone as end_date,
                    to_char(d, 'dd Mon YY') ||  to_char(d + '27 days', ' - dd Mon YY') as period,
                    pp.id as product_id
                FROM
                    generate_series(
                        (select (now()::timestamp with time zone at time zone 'Pacific/Auckland')::date), 
                        (select (now()::timestamp with time zone at time zone 'Pacific/Auckland' - interval '1 day')::date) + interval '364 days', '28 day') as dates(d)
                    CROSS JOIN ( select id from product_product WHERE id in (%s)) pp
                ORDER By product_id, start_date desc
            ),
            incoming_pos as (
                select
                    p.period,
                    p.start_date,
                    p.end_date,
                    pol.product_id,
                    sum(pol.product_qty) as product_qty
                FROM
                    purchase_order_line pol, purchase_order po, periods p
                WHERE
                     p.start_date = (select (now()::timestamp with time zone at time zone 'Pacific/Auckland')::date) AND
                     pol.product_id = p.product_id and
                     pol.date_planned < p.start_date and
                     pol.order_id = po.id and
                     po.state in ('draft','confirmed','approved') and
                     po.shipped=False
                GROUP BY
                    pol.product_id, pol.date_planned, p.period, p.start_date, p.end_date
                UNION
                    SELECT
                        p.period,
                        p.start_date,
                        p.end_date,
                        p.product_id,
                        sum(pol.product_qty) as product_qty
                    from
                        periods p
                    LEFT JOIN
                        purchase_order_line pol INNER JOIN purchase_order po on (pol.order_id = po.id and po.state in ('draft','confirmed','approved') and po.shipped=False)
                        on (pol.date_planned>=p.start_date and pol.date_planned <= p.end_date and pol.product_id = p.product_id)
                    group by
                        p.product_id, p.period, p.start_date, p.end_date
                    order by product_id, start_date
            )
            select
                start_date, end_date, product_id, sum(product_qty)::integer as incoming_qty
            from
                incoming_pos
            group by
                start_date, end_date, product_id
            order by
                product_id, start_date
        """ %(','.join(map(str, ids))))

        return self._cr.dictfetchall()

    @api.model
    def initialize_sales_data(self, product_ids):
        self._cr.execute("""
            WITH sd_gen as(
                SELECT  generate_series(-13, 26) as seq
                EXCEPT SELECT 0
                order by 1
            ),
            products as (SELECT id from product_product where id in (%s))
            INSERT INTO product_sales_data (product_id, sequence) SELECT products.id, sd_gen.seq from products, sd_gen
            WHERE NOT EXISTS (SELECT id FROM product_sales_data WHERE sequence = sd_gen.seq and product_id=products.id);""" %",".join(map(str, product_ids)))


    @api.multi
    def update_sales_data_and_forecast(self):
        # with allow_idle_transaction(cr):

        _logger.info('Forecast Cron')
        prod_pool = self.env['product.product']
        sd_pool = self.env['product.sales.data']

        if not self:
            ids = prod_pool.search(['|', ('purchase_ok', '=', True), ('sale_ok', '=', True)], order="id")#, ('supply_method', '!=', 'produce')
            prod_ids = prod_pool.search(['|', ('purchase_ok', '=', True), ('sale_ok', '=', True)], order="id")#, ('supply_method', '=', 'produce')
            self._cr.execute(
                "SELECT l.product_id from mrp_bom b, mrp_bom_line l WHERE l.bom_id = b.id AND b.product_id in (%s)" % ','.join( map(str, prod_ids)))
            for rec in self._cr.fetchall():
                if rec[0] not in ids:
                    ids.append(rec[0])
            ids = ids + prod_ids
        else:
            self._cr.execute(
                "SELECT l.product_id from mrp_bom b, mrp_bom_line l WHERE l.bom_id = b.id AND b.product_id in (%s)" % ','.join( map(str, self._ids)))
            ids = [x[0] for x in self._cr.fetchall()] + self.ids

        self.initialize_sales_data(ids)
        future_periods = self.get_future_periods_and_po_qty(ids)

        chunk_size = 25
        for prod_ids in [ids[x:x + chunk_size] for x in xrange(0, len(ids), chunk_size)]:
            fc_datas_all = {}
            prod_data_all = {}
            prod = {}

            start = time.time()
            prod_pool.update_stock_activity()
            prod_avg_price = self.get_products_avg_price(prod_ids)
            for prod in prod_pool.browse(prod_ids):
                fc_datas, prod_data = sd_pool.generate_product_sales(prod, fc_datas_all)
                fc_datas, prod_data = self.backfilling_by_recent_velocity(fc_datas, prod_data)
                self.manual_smoothing(prod, fc_datas)
                fc_datas, prod_data, ft_periods = self.cal_yoy_and_afv(prod, fc_datas, prod_data, ft_periods=filter(
                    lambda fp: fp['product_id'] == prod.id, future_periods))
                fc_datas, prod_data, ft_periods = self.cal_forecasts(prod, fc_datas, prod_data, ft_periods)
                fc_datas, prod_data = self.cal_gmros_gmroi(prod, fc_datas, prod_data, prod_avg_price.get(prod, 0))

                fc_datas_all[prod.id] = fc_datas + ft_periods
                prod_data_all[prod.id] = prod_data.copy()

            sqls = []
            for product_id, periods in fc_datas_all.iteritems():
                for period in periods:
                    sql = update_sql('product_sales_data', period,
                                     {'product_id': product_id, 'sequence': period['sequence']})
                    sqls.append(sql)

            for product_id, product_data in prod_data_all.iteritems():
                sql = update_sql('product_product', product_data, {'id': product_id})
                sqls.append(sql)

            self._cr.execute("\n".join(sqls))
            self._cr.commit()

        _logger.info('Forecast end')
        return True

    @api.multi
    def cal_yoy_and_afv(self, prod, fc_datas, prod_data, ft_periods):

        data_feed = [fc_data['avg_for_estimate'] for fc_data in fc_datas]

        if len(data_feed) < 26:
            data_feed.extend([0] * (26 - len(data_feed)))
            data_feed.reverse()
            yoy = 1
        elif min(data_feed) <= 0:
            data_feed.reverse()
            yoy = 1
        else:
            data_feed.reverse()
            # -- YOY Calculation
            if sum(data_feed[0:13]) > 0:
                yoy = sum(data_feed[13:26]) / sum(data_feed[0:13])
            else:
                yoy = 1

        index = 0;
        remaining_qty = prod.saleable_qty
        yoy_log = (1 + math.log10(yoy))
        for x in range(13, 26, 1):
            incoming_qty = ft_periods[index]['incoming_qty'] or 0

            est_value = data_feed[x] * yoy_log
            remaining_qty = remaining_qty - est_value + incoming_qty
            purchase_qty = max((0 - remaining_qty), 0)

            ft_periods[index].update({
                'sequence': (index + 1) * -1,
                'categ_id': prod.categ_id.id,
                'avg_for_estimate': est_value,
                'forecasted': True,
                'remaining_qty': int(remaining_qty),
                'purchase_qty': int(purchase_qty),
                'method': 'AFV',
            })

            index += 1

        prod_data['afv_yoy'] = yoy

        return fc_datas, prod_data, ft_periods

    @api.multi
    def cal_forecasts(self, prod, fc_datas, prod_data, ft_periods):

        weeks_to_po_expected = 0
        if prod.po_expected_date:
            po_pool = self.env['purchase.order.line']
            self._cr.execute("SELECT pol.date_planned from purchase_order_line pol, purchase_order po WHERE pol.order_id = po.id AND po.state in ('draft', 'confirmed', 'approved') AND po.shipped=False and pol.product_id=%s order by po.state, date_planned desc limit 1" % prod.id)
            max_po_expected_date = self._cr.fetchone()
            if max_po_expected_date and max_po_expected_date[0]:
                weeks_to_po_expected = (datetime.strptime(max_po_expected_date[0], '%Y-%m-%d') - datetime.today()).days / 7.0

        weeks_to_buy_forward = max([(prod.lead_time + 56) / 7, 8])

        weeks_to_consider = max([weeks_to_buy_forward, weeks_to_po_expected])

        leadtime_weeks = float(weeks_to_consider)
        start_seq = int(leadtime_weeks // 4) * -1
        period_part = (leadtime_weeks % 4) / 4.0 if (leadtime_weeks % 4 > 0) else 0

        prod_data['afv_purchase_qty'] = ft_periods[abs(start_seq) - 1]['purchase_qty'] + (ft_periods[abs(start_seq - 1) - 1]['purchase_qty'] * period_part)
        prod_data['afv_purchase_cost'] = prod_data['afv_purchase_qty'] * prod.standard_price

        # Distressed Stocks and Values
        prod.refresh()
        next_year_avg_est_sales = sum([ft_period['avg_for_estimate'] for ft_period in ft_periods]) / 52
        dist_velocity = max([prod_data['recent_velocity'], next_year_avg_est_sales])
        if dist_velocity > 0:
            prod_data['dist_weeks'] = prod.virtual_available / dist_velocity
        else:
            prod_data['dist_weeks'] = 0

        if prod.sale_ok == True:
            dist_weeks = max([prod_data['dist_weeks'] - 52, 0])
            prod_data['dist_qty'] = dist_weeks * dist_velocity
            prod_data['dist_cost'] = prod_data['dist_qty'] * prod.standard_price

        return fc_datas, prod_data, ft_periods


    @api.model
    def backfilling_by_recent_velocity(self, fc_datas, prod_data):

        for fc_data in fc_datas:
            if fc_data['sequence'] >= 1 and fc_data['sequence'] < 14 and fc_data['product_sales'] < 0.01:
                fc_data['avg_for_estimate'] = prod_data['recent_velocity'] * 4

            if fc_data['sequence'] >= 14 and fc_data['sequence'] < 27 and fc_data['product_sales'] < 0.01:
                fc_data['avg_for_estimate'] = fc_datas[fc_data['sequence'] - 14]['avg_for_estimate']

        return fc_datas, prod_data

    @api.model
    def cal_gmros_gmroi(self, prod, fc_datas, prod_data, avg_price):

        sales_year = sum([sd['product_sales'] for sd in fc_datas if sd['sequence'] > 0 and sd['sequence'] < 14])
        purchase_year = sum([sd['incoming_qty'] for sd in fc_datas if sd['sequence'] > 0 and sd['sequence'] < 14])

        cost_price = prod.standard_price
        avg_stock = ((prod.qty_available * 2) + sales_year - purchase_year) / 2

        storage_vol = prod.volume_storage
        cost_cubic = 3.2
        cost_capital = 20  # 5%

        gross_margin = (sales_year * (avg_price - cost_price))
        storage_cost = (avg_stock * storage_vol * cost_cubic * 52)

        gmroi = 0
        gmros = 0
        if avg_stock != 0 and cost_price != 0:
            gmroi = gross_margin / (avg_stock * cost_price)

        if storage_cost:
            gmros = gross_margin / storage_cost / cost_capital

        prod_data.update({'gmros': gmros, 'gmroi': gmroi, 'avg_price': avg_price, 'avg_stock': avg_stock})

        return fc_datas, prod_data


    def manual_smoothing(self, prod, fc_datas):

        sales_data = []
        for fc_data in fc_datas:

            if fc_data['product_sales'] > 0:
                sales_data.append([fc_data['sequence'], fc_data['product_sales']])
            else:
                self._cr.execute("SELECT activity from res_activity_log WHERE res_id = %s and res_model='product.product' and activity in ('Out of Stock','Stock In') AND date < '%s 23:59:59' order by date desc limit 1" % (prod.id, fc_data['end_date'][:10]))
                res = self._cr.dictfetchone()
                if (res and res['activity'] == 'Out of Stock') or (not res):
                    sales_data.append([fc_data['sequence'], fc_data['avg_for_estimate']])
                else:
                    sales_data.append([fc_data['sequence'], fc_data['product_sales']])

        for sale_ind in range(len(sales_data)):
            if sale_ind == 0:
                sales_data[sale_ind].append((sales_data[sale_ind][1] + sales_data[sale_ind+1][1] ) / 2.0 )
            elif sale_ind == 25:
                sales_data[sale_ind].append((sales_data[sale_ind-1][1] + sales_data[sale_ind][1]) / 2.0 )
            else:
                sales_data[sale_ind].append((sales_data[sale_ind-1][1] + sales_data[sale_ind][1] + sales_data[sale_ind+1][1]) / 3.0 )

        for fc_data in fc_datas:
            fc_data.update({
                'avg_for_estimate': sales_data[fc_data['sequence'] - 1][2]
            })
        return fc_datas


    @api.multi
    def get_replacement_products(self):
        repl_prod_ids = []
        for prod in self:
            if prod.state in ['obsolete','end'] and prod.replacement_product_id:
                repl_prod_ids.append(prod.replacement_product_id.id)

        if repl_prod_ids:
            return repl_prod_ids + self.get_replacement_products(repl_prod_ids)
        else:
            return repl_prod_ids

    @api.multi
    def get_replacement_products(self):
        repl_prod_ids = []
        for prod in self:
            if prod.state in ['obsolete','end'] and prod.replacement_product_id:
                repl_prod_ids.append(prod.replacement_product_id.id)

        if repl_prod_ids:
            return repl_prod_ids + self.get_replacement_products(cr, uid, repl_prod_ids, context)
        else:
            return repl_prod_ids


    @api.multi
    def get_replacing_products(self):
        repl_prod_ids = []
        for prod in self:
            if prod.replacing_product_id:
                repl_prod_ids.append(prod.replacing_product_id.id)

        if repl_prod_ids:
            return repl_prod_ids + self.get_replacing_products(repl_prod_ids)
        else:
            return repl_prod_ids


class report_buying_forecast(models.Model):
    _name = 'report.buying.forecast'
    _auto = False

    default_code = fields.Char('SKU', size=64)
    name = fields.Char('Name', size=256)
    supply_method = fields.Selection([('produce', 'Manufacture'), ('buy', 'Buy')], 'Supply Method', required=True)
    product_id = fields.Many2one('product.product', 'Product')
    company_id = fields.Many2one('res.company', 'Company')
    forecast_qty = fields.Float('Forecast Quantity')
    purchase_qty = fields.Float('Forecast Purchase Quantity')
    moq = fields.Integer('Min. Qty')
    cbm = fields.Float('Product Volume Purchase Quantity')
    cost = fields.Float('Cost')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'report_buying_forecast')
        self._cr.execute("""
            create or replace view report_buying_forecast as (
                SELECT
                    p.id as id,
                    t.name as name,
                    p.default_code as default_code,
                    p.id as product_id,
                    /*t.supply_method as supply_method,*/
                    t.company_id as company_id,
                    p.forecast_qty as forecast_qty
                FROM
                    product_product p, product_template t
                WHERE
                    p.product_tmpl_id = t.id
            )""")


class leadtime_multiplier(models.TransientModel):
    _name = 'leadtime.multipllier'

    leadtime_multiplier = fields.Float('Lead Time Multiplier')
    leadtime_weeks = fields.Float('Lead Time in Weeks', digits=(20, 1),help="If this value is set, lead time multiplier will be ignored")

    @api.model
    def default_get(self, fields):
        resp = super(leadtime_multiplier, self).default_get(fields)
        lm = self.env["ir.config_parameter"].get_param("leadtime.multiplier")
        resp['leadtime_multiplier'] = lm and float(lm) or lm

        lw = self.env["ir.config_parameter"].get_param("leadtime.weeks")
        resp['leadtime_weeks'] = lw and float(lw) or lw
        return resp

    @api.multi
    def save_leadtime_multiplier(self):
        sobj = self[0]
        self.env["ir.config_parameter"].set_param("leadtime.multiplier", sobj.leadtime_multiplier or 0.000000001)
        self.env["ir.config_parameter"].set_param("leadtime.weeks", sobj.leadtime_weeks or 0.000000001)
        return True


class purchase_calc_export(models.TransientModel):
    _name = 'purchase.calc.export'

    export_xlsx = fields.Binary('Xlsx')
    export_fname = fields.Char('Filename', size=64)

    @api.multi
    def update_and_print_excel(self):
        for prod_id in self._context.get('active_ids'):
            self.env['product.product'].update_sales_data_and_forecast()
        return self.print_excel()

    @api.multi
    def print_excel(self):

        prod_pool = self.env['product.product']

        sales_dates = {}
        msgs = []
        for prod_id in self._context['active_ids']:
            prod = prod_pool.browse(prod_id)
            if not prod.sales_data:
                msgs.append('"%s": No Forecast Data' % prod.name)

            for sd in prod.sales_data:
                if sd.sequence > 0:
                    if sd.sequence not in sales_dates:
                        sales_dates[sd.sequence] = sd.end_date
                    elif sd.end_date != sales_dates[sd.sequence]:
                        msgs.append('"%s", End Date %s (%s) for Sequence %s' % (prod.name, sd.end_date, sales_dates[sd.sequence], sd.sequence))
                        break;

        if msgs:
            raise UserError(u'Below products have different periods, please use the other button "Update Forecast and Download"', '\n' + '\n'.join(msgs))

        def colnum_string(n):
            div = n
            string = ""
            temp = 0
            while div > 0:
                module = (div - 1) % 26
                string = chr(65 + module) + string
                div = int((div - module) / 26)
            return string

        buf = StringIO.StringIO()
        workbook = xlsxwriter.Workbook(buf)
        sheet_f = workbook.add_worksheet('Forecast')
        sheet_s = workbook.add_worksheet('Actual Sales')

        sheet_f.hide_gridlines(2)
        sheet_s.hide_gridlines(2)

        bold_left = workbook.add_format({'bold': True, 'align': 'left', })
        bold_center = workbook.add_format({'bold': True, 'align': 'center', })
        bold_right = workbook.add_format({'bold': True, 'align': 'right'})
        bold_right.set_bottom()
        bold_right.set_top()
        merge_format = workbook.add_format({'bold': 1, 'border': 1, 'align': 'center', 'valign': 'vcenter'})
        merge_format_right = workbook.add_format(
            {'bold': 1, 'border': 1, 'align': 'center', 'valign': 'vcenter', 'align': 'right', })
        merge_format_bottom = workbook.add_format({'bold': 1, 'bottom': 1, 'align': 'center', 'valign': 'vcenter'})

        bold_left = workbook.add_format({'bold': True, 'align': 'left', })
        bold_center = workbook.add_format({'bold': True, 'align': 'center', })
        bold_right = workbook.add_format({'bold': True, 'align': 'right', 'top': 1, 'bottom': 1})
        bold_90 = workbook.add_format(
            {'bold': True, 'align': 'right', 'bottom': 1, 'rotation': 45, 'num_format': 'DD - MMM'})
        bold_90_right = workbook.add_format(
            {'bold': True, 'align': 'right', 'bottom': 1, 'right': 1, 'rotation': 45, 'num_format': 'DD - MMM'})

        border_right = workbook.add_format({'right': 1})
        border_bottom = workbook.add_format({'bottom': 1})
        border_bottom_right = workbook.add_format({'bottom': 1, 'right': 1})

        merge_format = workbook.add_format({'bold': 1, 'border': 1, 'align': 'center', 'valign': 'vcenter'})
        merge_format_wrap = workbook.add_format(
            {'bold': 1, 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': 1})

        bold_rightm = workbook.add_format({'bold': True, 'align': 'right', 'border': 1, })

        cell_borders = workbook.add_format()
        cell_borders.set_bottom()
        cell_borders.set_top()

        cell_borders2 = workbook.add_format()
        cell_borders2.set_right()

        cell_borders4 = workbook.add_format()
        cell_borders4.set_left()

        cell_borders3 = workbook.add_format()
        cell_borders3.set_bottom()
        cell_borders3.set_top()
        cell_borders3.set_right()

        format_currency = workbook.add_format()
        format_currency.set_num_format('"$"#,##0.00;[RED]"$"#,##0.00')

        format_currency_b = workbook.add_format()
        format_currency_b.set_num_format('"$"#,##0;[RED]"$"#,##0')
        format_currency_b.set_bottom()
        format_currency_b.set_top()

        format_currency_br = workbook.add_format()
        format_currency_br.set_num_format('"$"#,##0;[RED]"$"#,##0')
        format_currency_br.set_bottom()
        format_currency_br.set_top()
        format_currency_br.set_right()
        format_currency_br.set_left()

        format_decimal = workbook.add_format()
        format_decimal.set_num_format('_-* #,##0.00_-;-* #,##0.00_-;_-* "-"??_-;_-@_-')

        format_decimal_b = workbook.add_format()
        format_decimal_b.set_num_format('_-* #,##0.00_-;-* #,##0.00_-;_-* "-"??_-;_-@_-')
        format_decimal_b.set_bottom()
        format_decimal_b.set_top()

        format_decimal_br = workbook.add_format()
        format_decimal_br.set_num_format('_-* #,##0.00_-;-* #,##0.00_-;_-* "-"??_-;_-@_-')
        format_decimal_br.set_bottom()
        format_decimal_br.set_top()
        format_decimal_br.set_right()
        format_decimal_br.set_left()

        sheet_f.merge_range('K1:T1', 'Weeks', merge_format)
        sheet_f.write('U1', 'Custom Weeks', merge_format)

        sheet_f.set_row(1, 30)

        sheet_f.set_column('A:A', 15)
        sheet_f.set_column('B:B', 50)
        sheet_f.set_column('C:C', 6.7)
        sheet_f.set_column('D:D', 6.7)
        sheet_f.set_column('E:E', 6.7)
        sheet_f.set_column('F:F', 6.7)
        sheet_f.set_column('G:G', 6.7)
        sheet_f.set_column('H:H', 6.7)
        sheet_f.set_column('I:I', 6.7)
        sheet_f.set_column('J:J', 6.7)

        sheet_f.set_column('K:K', 6.7)
        sheet_f.set_column('L:L', 6.7)
        sheet_f.set_column('M:M', 6.7)
        sheet_f.set_column('N:N', 6.7)
        sheet_f.set_column('O:O', 6.7)
        sheet_f.set_column('P:P', 6.7)
        sheet_f.set_column('Q:Q', 6.7)
        sheet_f.set_column('R:R', 6.7)
        sheet_f.set_column('S:S', 6.7)
        sheet_f.set_column('T:T', 6.7)
        sheet_f.set_column('U:U', 12.23)

        sheet_f.merge_range('A1:A2', 'SKU', merge_format)
        sheet_f.merge_range('B1:B2', 'Product', merge_format)

        sheet_f.merge_range('C1:C2', 'Saleble Qty', merge_format_wrap)
        sheet_f.merge_range('D1:D2', 'Qty on Order', merge_format_wrap)
        sheet_f.merge_range('E1:E2', 'Product Cases 6m', merge_format_wrap)
        sheet_f.merge_range('F1:F2', 'Lead Time (days)', merge_format_wrap)
        sheet_f.merge_range('G1:G2', 'Cubic Volume', merge_format_wrap)
        sheet_f.merge_range('H1:H2', 'Price', merge_format_wrap)
        sheet_f.merge_range('I1:I2', 'Recent Velocity', merge_format_wrap)
        sheet_f.merge_range('J1:J2', 'Recent Velocity Annual', merge_format_wrap)

        sheet_f.write('K2', 12, bold_right)
        sheet_f.write('L2', 16, bold_right)
        sheet_f.write('M2', 20, bold_right)
        sheet_f.write('N2', 24, bold_right)
        sheet_f.write('O2', 28, bold_right)
        sheet_f.write('P2', 32, bold_right)
        sheet_f.write('Q2', 36, bold_right)
        sheet_f.write('R2', 40, bold_right)
        sheet_f.write('S2', 44, bold_right)
        sheet_f.write('T2', 48, bold_right)
        sheet_f.write('U2', 16, bold_rightm)

        # range = "$H$2:$R$" + str(len(context['active_ids'])+2)

        records = str(len(context['active_ids']) + 2)

        formula_q = "=CEILING((HLOOKUP(INDEX($K$2:$U$2,0,MATCH($U$2,$K$2:$U$2,1)),$K$2:$T$%s,ROW()-1,FALSE))+(((HLOOKUP((INDEX($K$2:$U$2,0,MATCH($U$2,$K$2:$U$2,1))+4),$K$2:$T$%s,ROW()-1,FALSE))-(HLOOKUP(INDEX($K$2:$U$2,0,MATCH($U$2,$K$2:$U$2,1)),$K$2:$T$%s,ROW()-1,FALSE)))/4)*($U$2-INDEX($K$2:$U$2,0,MATCH($U$2,$K$2:$U$2,1))),1)" % (
        records, records, records)

        supplier_ids = []
        index = 3
        for prod_id in context['active_ids']:
            prod = prod_pool.browse(cr, uid, prod_id)
            for seller in prod.seller_ids:
                if seller.name.id not in supplier_ids:
                    supplier_ids.append(seller.name.id)

            index_str = str(index)
            sheet_f.write('A' + str(index), prod.default_code)
            sheet_f.write('B' + str(index), prod.name)
            sheet_f.write('C' + str(index), prod.saleable_qty)
            sheet_f.write('D' + str(index), sum([sd.incoming_qty for sd in prod.sales_data if sd.sequence < 0]))
            sheet_f.write('E' + str(index), sum([c.months_6 for c in prod.case_count_ids]))

            sheet_f.write('F' + str(index), prod.seller_ids and prod.seller_ids[0].delay or 0)
            sheet_f.write('G' + str(index), prod.volume)
            sheet_f.write('H' + str(index), prod.standard_price, format_currency)
            sheet_f.write('I' + str(index), prod.recent_velocity)
            sheet_f.write('J' + str(index), prod.year_velocity, border_right)

            if not prod.sales_data:
                prod_pool.update_sales_data_and_forecast()
                prod.refresh()

            sd = prod.sales_data

            sheet_f.write('K' + index_str, sd[10].purchase_qty)
            sheet_f.write('L' + index_str, sd[9].purchase_qty)
            sheet_f.write('M' + index_str, sd[8].purchase_qty)
            sheet_f.write('N' + index_str, sd[7].purchase_qty)
            sheet_f.write('O' + index_str, sd[6].purchase_qty)
            sheet_f.write('P' + index_str, sd[5].purchase_qty)
            sheet_f.write('Q' + index_str, sd[4].purchase_qty)
            sheet_f.write('R' + index_str, sd[3].purchase_qty)
            sheet_f.write('S' + index_str, sd[2].purchase_qty)
            sheet_f.write('T' + index_str, sd[1].purchase_qty, cell_borders2)

            sheet_f.write('U' + index_str, formula_q, cell_borders2)

            index += 1

        vol_range = "$G$3:$G$" + str(index - 1)
        formula_s = "=SUMPRODUCT(%s3:%s" + str(index - 1) + "," + vol_range + ")"

        index_str = str(index)

        sheet_f.merge_range('A' + index_str + ':J' + index_str, 'Total Cubic', merge_format_right)
        for ltr in ['K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T']:
            sheet_f.write(ltr + index_str, formula_s % (ltr, ltr), format_decimal_b)
        sheet_f.write('U' + index_str, formula_s % ('U', 'U'), format_decimal_br)

        # -------
        index += 1

        p_range = "$H$3:$H$" + str(index - 2)
        formula_p = "=SUMPRODUCT(%s3:%s" + str(index - 2) + "," + p_range + ")"

        index_str = str(index)
        sheet_f.merge_range('A' + index_str + ':J' + index_str, 'Total Price', merge_format_right)
        for ltr in ['K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T']:
            sheet_f.write(ltr + index_str, formula_p % (ltr, ltr), format_currency_b)
        sheet_f.write('U' + index_str, formula_p % ('U', 'U'), format_currency_br)

        for supplier in self.env['res.partner'].browse(supplier_ids):
            index += 2
            sheet_f.merge_range('A%s:B%s' % (index, index), 'Supplier', bold_left)
            index += 1
            sheet_f.merge_range('A%s:B%s' % (index, index), supplier.name)

            index += 2
            sheet_f.merge_range('A%s:B%s' % (index, index), 'Ordering Notes', bold_left)
            index += 1
            sheet_f.merge_range('A%s:B%s' % (index, index), supplier.ordering_notes or '')
            index += 1

        # Sheet Actual Sales
        cell_num = workbook.add_format({'num_format': '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)'})
        border_right_num = workbook.add_format({'right': 1, 'num_format': '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)'})
        border_bottom_num = workbook.add_format({'bottom': 1, 'num_format': '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)'})
        border_bottom_num_right = workbook.add_format(
            {'bottom': 1, 'right': 1, 'num_format': '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)'})

        sheet_s.set_row(1, 70)
        sheet_s.set_column('A:A', 15)
        sheet_s.set_column('B:B', 50)

        sheet_s.set_column('C:AB', 3)

        sheet_s.merge_range('A1:A2', 'SKU', merge_format)
        sheet_s.merge_range('B1:B2', 'Product', merge_format)
        sheet_s.merge_range('C1:AB1', '4 Week Period Ending', merge_format_bottom)

        for x in range(1, 26):
            sheet_s.write(colnum_string(x + 2) + '2', datetime.strptime(sales_dates[x], '%Y-%m-%d'), bold_90)
        sheet_s.write(colnum_string(26 + 2) + '2', datetime.strptime(sales_dates[26], '%Y-%m-%d'), bold_90_right)

        index = 3
        for prod_id in self._context['active_ids']:
            prod = prod_pool.browse(prod_id)
            index_str = str(index)

            sales_data = dict([(sd.sequence, sd.product_sales) for sd in prod.sales_data if sd.sequence > 0])

            if prod_id == self._context['active_ids'][-1]:
                sheet_s.write('A' + index_str, prod.default_code, border_bottom_right)
                sheet_s.write('B' + index_str, prod.name, border_bottom_right)
                for col_ind in range(1, 26):
                    sheet_s.write(colnum_string(col_ind + 2) + index_str, sales_data[col_ind], border_bottom_num)
                sheet_s.write(colnum_string(26 + 2) + index_str, sales_data[26], border_bottom_num_right)
            else:
                sheet_s.write('A' + index_str, prod.default_code, border_right)
                sheet_s.write('B' + index_str, prod.name, border_right)
                for col_ind in range(1, 26):
                    sheet_s.write(colnum_string(col_ind + 2) + index_str, sales_data[col_ind], cell_num)
                sheet_s.write(colnum_string(26 + 2) + index_str, sales_data[26], border_right_num)

            index += 1

        workbook.close()

        sobj = self[0]
        sobj.write({
            'export_xlsx': base64.encodestring(buf.getvalue()),
            'export_fname': 'Purchase_Calc_' + time.strftime('%Y_%m_%d_%H_%M_%S') + '.xlsx'
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.calc.export',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': self.id,
            'target': 'new',
            'name': 'Purchase Calc Export'
        }



