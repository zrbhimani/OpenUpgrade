from openerp import http
from openerp.http import request
from openerp.addons.web.controllers.main import Database

class Database(Database):
    def create(self, req, fields):
        raise openerp.exceptions.AccessDenied

    def duplicate(self, req, fields):
        raise openerp.exceptions.AccessDenied

    def drop(self, req, fields):
        raise openerp.exceptions.AccessDenied

    def backup(self, req, backup_db, backup_pwd, token):
        raise openerp.exceptions.AccessDenied

    def restore(self, req, db_file, restore_pwd, new_db):
        raise openerp.exceptions.AccessDenied

    def change_password(self, req, fields):
        raise openerp.exceptions.AccessDenied

    def manager(self, req, fields):
        raise openerp.exceptions.AccessDenied


class TradetestedController(http.Controller):
    _cp_path = '/tradetested'

    @http.route('/tradetested/read_watchers', type='json', auth='user')
    def read_watchers(self, watcher_ids, res_model):
        watchers = []
        is_editable = request.env.user.has_group('base.group_no_one')
        partner_id = request.env.user.partner_id
        watcher_id = None
        watcher_recs = request.env['obj.watchers'].sudo().browse(watcher_ids)
        res_ids = watcher_recs.mapped('res_id')
        request.env[res_model].browse(res_ids).check_access_rule("write")

        for watcher in watcher_recs:
            is_uid = request.env.user == watcher.user_id
            watcher_id = watcher.id if is_uid else watcher_id
            watchers.append({
                'id': watcher.id,
                'name': watcher.user_id.name,
                'email': watcher.user_id.email if watcher.user_id else None,
                'res_model': 'res.users',
                'res_id': watcher.user_id.id,
                'is_editable': is_editable,
                'is_uid': is_uid,
            })

        return {
            'watchers': watchers,
            'subtypes': None
        }

    # @http.route('/mail/read_followers', type='json', auth='user')
    # def read_followers(self, follower_ids, res_model):
    #     followers = []
    #     is_editable = request.env.user.has_group('base.group_no_one')
    #     partner_id = request.env.user.partner_id
    #     follower_id = None
    #     follower_recs = request.env['mail.followers'].sudo().browse(follower_ids)
    #     res_ids = follower_recs.mapped('res_id')
    #     request.env[res_model].browse(res_ids).check_access_rule("write")
    #     for follower in follower_recs:
    #         is_uid = partner_id == follower.partner_id
    #         follower_id = follower.id if is_uid else follower_id
    #         followers.append({
    #             'id': follower.id,
    #             'name': follower.partner_id.name or follower.channel_id.name,
    #             'email': follower.partner_id.email if follower.partner_id else None,
    #             'res_model': 'res.partner' if follower.partner_id else 'mail.channel',
    #             'res_id': follower.partner_id.id or follower.channel_id.id,
    #             'is_editable': is_editable,
    #             'is_uid': is_uid,
    #         })
    #     return {
    #         'followers': followers,
    #         'subtypes': self.read_subscription_data(res_model, follower_id) if follower_id else None
    #     }
