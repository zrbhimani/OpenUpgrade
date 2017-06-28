# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _


class merge_purchase_orders(models.TransientModel):
    _name = 'merge.purchase.orders'

    related_orders = fields.Many2many('purchase.order', 'merge_purchase_order_rel', 'mpo_id', 'po_id', string='Related Orders')
    message = fields.Text('Message')
    not_allowed = fields.Boolean('No Merge')


    @api.model
    def default_get(self,fields):
        purchase_pool = self.env['purchase.order']
        res = {}
        purchase_order = purchase_pool.browse(self._context.get('active_id'))
        if purchase_order.state not in ['draft']:
            res['message'] = 'Only draft sale orders can merge'
            res['not_allowed'] = True
        else:
            rel_po_ids = purchase_pool.search([('id','!=',purchase_order.id),('partner_id','=',purchase_order.partner_id.id),('state','=','draft'),('sale_id','=',False)])

            if not rel_po_ids:
                res['message'] = "No related draft purchase orders"
                res['not_allowed'] = True
            else:
                res['message'] = "This will merge "+ str(len(rel_po_ids)) +" draft purchase orders in to this order, Continue?"
                res['not_allowed'] = False
                res['related_orders'] = rel_po_ids.ids
        return res


    @api.multi
    def merge_purchase_orders(self):
        po_pool    = self.env['purchase.order']
        line_pool  = self.env['purchase.order.line']
        proc_pool  = self.env['procurement.order']
        wf_service = netsvc.LocalService('workflow')
        po_list    = []

        order_obj = po_pool.browse(self._context.get('active_id'))
        sobj = self[0]
        for order in sobj.related_orders:
            po_list.append(order.name)
            for line in order.order_line:
                line.copy({'order_id': order_obj.id})

            proc_ids = proc_pool.search([('purchase_id', '=', order.id)])
            for proc in proc_ids:
                proc.write({'purchase_id': order_obj.id})

        sobj.refresh()
        user = self.env['res.users'].browse()

        for order in sobj.related_orders:
            order.write({'state':'cancel'})
            vals = {
                        'body': u'<p>Merged to %s ' %order_obj.name,
                        'model': 'purchase.order',
                        'res_id': order.id,
                        'subtype_id': False,
                        'author_id': user.partner_id.id,
                        'type': 'comment', }

            self.env['mail.message'].create(vals)


        vals = {
                    'body': u'<p>Merged Purchase Order(s) : %s ' %(", ".join(po_list)),
                    'model': 'purchase.order',
                    'res_id': order_obj.id,
                    'subtype_id': False,
                    'author_id': user.partner_id.id,
                    'type': 'comment', }

        self.env['mail.message'].create(vals)

        return True


class eta_update(models.TransientModel):
    _name = 'eta.update'

    @api.multi
    def _get_count(self):
        for update in self:
            update.count_orders = len(update.order_ids)

    po_id = fields.Many2one('purchase.order', 'Purchase Order')
    order_ids = fields.Many2many('sale.order', 'rel_eta_update_sale_order', 'eta_update_id', 'order_id', 'Orders')
    count_orders = fields.Integer(compute=_get_count, string='Total Orders')

    @api.model
    def default_get(self, fields):
        resp = super(eta_update, self).default_get(fields)
        resp['po_id'] = self._context.get('active_id')

        product_ids = [l.product_id.id for l in self.env['purchase.order'].browse(resp['po_id']).order_line]
        order_ids = self.env['sale.order'].search([('order_line.product_id','in',product_ids),('state','not in',['done','cancel'])])
        resp['order_ids'] = order_ids.ids
        resp['count_orders'] = len(order_ids)
        return resp

    @api.multi
    def send_eta_update(self):
        sobj = self[0]
        template = self.env['ir.model.data'].get_object('tradetested', 'email_template_eta_update')

        for order in sobj.order_ids:
            mail_id = self.env['email.template'].send_mail(template.id, order.id)

        return True

