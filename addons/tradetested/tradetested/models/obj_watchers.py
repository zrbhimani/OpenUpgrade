# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from openerp import SUPERUSER_ID
from openerp.tools.translate import _

from lxml import etree

map = {
    'Sale Orders': 'sale.order',
    'Purchase Orders': 'purchase.order',
    'Cases': 'crm.helpdesk',
    'Products': 'product.product',
    'Delivery Orders': 'stock.picking'
}


class obj_watchers(models.Model):
    _name = 'obj.watchers'
    _rec_name = 'user_id'
    _log_access = False
    _description = 'Document Watchers'

    res_model = fields.Char('Related Document Model', size=128, required=True, index=1, help='Model of the followed resource')
    res_id = fields.Integer('Related Document ID', index=1, help='Id of the followed resource')
    user_id = fields.Many2one('res.users', string='Related Partner', ondelete='cascade', required=True, index=1)


class obj_watchers_base(models.AbstractModel):
    _name = 'obj.watchers.base'

    obj_is_follower = fields.Boolean(compute='_get_watchers', type='boolean', string='Is a Follower')
    obj_watcher_ids = fields.One2many('obj.watchers', 'res_id', string='Watchers', domain=lambda self: [('res_model', '=', self._name)])
    obj_user_ids = fields.Many2many(comodel_name='res.users', string='Watchers (Users)', compute='_get_watchers', search='_search_watcher_users')

    @api.one
    @api.depends('obj_watcher_ids')
    def _get_watchers(self):
        self.obj_user_ids = self.obj_watcher_ids.mapped('user_id')

    @api.model
    def _search_watcher_users(self, operator, operand):
        assert operator != "not in", "Do not search message_follower_ids with 'not in'"
        watchers = self.env['obj.watchers'].sudo().search([('res_model', '=', self._name), ('partner_id', operator, operand)])
        return [('id', 'in', watchers.mapped('res_id'))]

    @api.multi
    def obj_subscribe(self, user_ids):
        for record in self.sudo():
            new_uids = set(user_ids) - set([u.id for u in record.obj_user_ids])
            for new_uid in new_uids:
                self.env['obj.watchers'].create({'res_model': self._name, 'res_id': record.id, 'user_id': new_uid})
        return True

    @api.multi
    def obj_subscribe_watcher(self, user_ids=None):
        if user_ids is None:
            user_ids = [self._uid]
        result = self.obj_subscribe(user_ids)
        if user_ids and result:
            self.pool['ir.ui.menu'].clear_caches()
        return result

    @api.multi
    def obj_unsubscribe(self, user_ids=None):
        if not user_ids:
            return True

        self.env['obj.watchers'].search([
            ('res_model', '=', self._name),
            ('res_id', 'in', self.ids),
            ('user_id', 'in', user_ids or [])
        ]).sudo().unlink()

    @api.multi
    def obj_unsubscribe_watcher(self, user_ids=None):
        if user_ids is None:
            user_ids = [self._uid]
        result = self.obj_unsubscribe(user_ids)
        if user_ids and result:
            self.pool['ir.ui.menu'].clear_caches()
        return result

    @api.model
    def search(self, args, offset=0, limit=0, order=None, count=False):
        if self._context and 'filter_my_watchlist' in self._context:
            matched_records = self.env['obj.watchers'].search([('res_model', '=', self._name), ('user_id', '=', self._uid)])
            args.append(['id', 'in', [r.res_id for r in matched_records]])
        return super(obj_watchers_base, self).search(args, offset=offset, limit=limit, order=order, count=count)


class invite_wizard(models.TransientModel):
    _name = 'obj.wizard.invite'
    _description = 'Invite wizard'

    res_model = fields.Char('Related Document Model', size=128, required=True, index=1, help='Model of the followed resource')
    res_id = fields.Integer('Related Document ID', index=1, help='Id of the followed resource')
    user_ids = fields.Many2many('res.users', string='Users')

    @api.model
    def default_get(self, fields):
        result = super(invite_wizard, self).default_get(fields)
        if 'message' in fields and result.get('res_model') and result.get('res_id'):
            document_name = self.pool.get(result.get('res_model')).name_get(cr, uid, [result.get('res_id')], context=context)[0][1]
            message = _('<div>You have been invited to follow %s.</div>' % document_name)
            result['message'] = message
        elif 'message' in fields:
            result['message'] = _('<div>You have been invited to follow a new document.</div>')
        return result

    @api.multi
    def add_watchers(self):
        self.env[self.res_model].browse(self.res_id).obj_subscribe([u.id for u in self.user_ids])


