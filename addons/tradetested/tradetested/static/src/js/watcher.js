odoo.define('tradetested.watcher', function (require) {
"use strict";

var Chatter = require('mail.Chatter');
var chat_manager = require('mail.chat_manager');
var ajax = require('web.ajax');
var core = require('web.core');
var data = require('web.data');
var Dialog = require('web.Dialog');
var form_common = require('web.form_common');
var session = require('web.session');

var _t = core._t;
var qweb = core.qweb;

var Watchers = form_common.AbstractField.extend({
    template: 'obj.Watchers',

    init: function () {
        this._super.apply(this, arguments);

        this.image = this.node.attrs.image || 'image_small';
        this.comment = this.node.attrs.help || false;
        this.ds_model = new data.DataSetSearch(this, this.view.model);

        this.value = [];
        this.watchers = [];
        this.watchers_fetched = $.Deferred();
        this.data_subtype = {};

        this.view_is_editable = this.__parentedParent.is_action_enabled('edit');
    },

    start: function () {
        // use actual_mode property on view to know if the view is in create mode anymore
        this.view.on("change:actual_mode", this, this.on_check_visibility_mode);
        this.on_check_visibility_mode();
        this.reinit();
        this.bind_events();
        return this._super();
    },

    on_check_visibility_mode: function () {
        this.set({"force_invisible": this.view.get("actual_mode") === "create"});
    },

    set_value: function (_value) {
        this.value = _value;
        this._super(_value);
    },

    reinit: function () {
        this.data_subtype = {};
        this.message_is_follower = undefined;
        this.display_buttons();
    },

    bind_events: function () {
        var self = this;

        // event: click on '(Un)Follow' button, that toggles the follow for uid
        this.$el.on('click', '.o_followers_follow_button', function () {
            if ($(this).hasClass('o_followers_notfollow')) {
                self.do_follow();
            } else {
                self.do_unfollow({user_ids: [session.uid]});
            }
        });

        // event: click on a subtype, that (un)subscribe for this subtype
        this.$el.on('click', '.o_subtypes_list input', function(event) {
            event.stopPropagation();
            self.do_update_subscription(event);
            var $list = self.$('.o_subtypes_list');
            if (!$list.hasClass('open')) {
                $list.addClass('open');
            }
            if (self.$('.o_subtypes_list ul')[0].children.length < 1) {
                $list.removeClass('open');
            }
        });

        // event: click on 'invite' button, that opens the invite wizard
        this.$el.on('click', '.o_add_watcher', function(event) {
            event.preventDefault();
            self.on_invite_follower(false);
        });
        this.$el.on('click', '.o_add_follower_channel', function(event) {
            event.preventDefault();
            self.on_invite_follower(true);
        });

        // event: click on 'edit_subtype(pencil)' button to edit subscription
        this.$el.on('click', '.o_edit_subtype', self.on_edit_subtype);
        this.$el.on('click', '.o_remove_follower', self.on_remove_follower);
        this.$el.on('click', '.o_mail_redirect', self.on_click_redirect);
    },

    on_edit_subtype: function (event) {
        var self = this;
        var $currentTarget = $(event.currentTarget);
        var watcher_id = $currentTarget.data('watcher-id'); // id of model mail_watcher
        var res_id = $currentTarget.data('oe-id'); // id of model res_partner or mail_channel
        var is_channel = $currentTarget.data('oe-model') === 'mail.channel';
        self.dialog = new Dialog(this, {
                        size: 'medium',
                        title: _t('Edit Subscription of ') + $currentTarget.siblings('a').text(),
                        buttons: [
                                {
                                    text: _t("Apply"),
                                    classes: 'btn-primary',
                                    click: function () {
                                        self.do_update_subscription(event, res_id, is_channel);
                                    },
                                    close: true
                                },
                                {
                                    text: _t("Cancel"),
                                    close: true,
                                },
                            ],
                }).open();
        return self.fetch_subtypes(watcher_id).then(function (data) {
            self.display_subtypes(data, true, is_channel);
        });
    },

    on_invite_follower: function (channel_only) {
        var self = this;
        var action = {
            type: 'ir.actions.act_window',
            res_model: 'obj.wizard.invite',
            view_mode: 'form',
            view_type: 'form',
            views: [[false, 'form']],
            name: _t('Invite Watcher'),
            target: 'new',
            context: {
                'default_res_model': this.view.dataset.model,
                'default_res_id': this.view.datarecord.id,
                'mail_invite_follower_channel_only': channel_only,
            },
        };
        this.do_action(action, {
            on_close: function () {
                self.read_value();
            },
        });
    },

    on_remove_follower: function (event) {
        var res_model = $(event.target).parent().find('a').data('oe-model');
        var res_id = $(event.target).parent().find('a').data('oe-id');
        return this.do_unfollow({user_ids: [res_id]});
    },

    on_click_redirect: function (event) {
        event.preventDefault();
        var res_id = $(event.target).data('oe-id');
        var res_model = $(event.target).data('oe-model');
        this.trigger('redirect', res_model, res_id);
    },

    read_value: function () {
        var self = this;
        return this.ds_model.read_ids([this.view.datarecord.id], ['obj_watcher_ids'])
            .then(function (results) {
                self.value = results[0].obj_watcher_ids;
                self.render_value();
            });
    },

    render_value: function () {
        var self = this;
        this.reinit();
        this.value = this.value || [];
        return this.fetch_watchers().then(function (results) {
            self.display_followers(results.watchers);
            if (results.subtypes) { // current user is follower
                self.display_subtypes(results.subtypes);
            }
            self.display_buttons();
        }).fail(this.display_generic.bind(this));
    },

    fetch_watchers: function () {
        return ajax.jsonRpc('/tradetested/read_watchers', 'call', {
            watcher_ids: this.value,
            res_model: this.view.model,
        });
    },

    /** Fetch subtypes of the given follower
     *  @param {int} [follower_id] the id of the follower
     *  @param {string} [follower_model] 'res.partner' or 'mail.channel'
     */
    fetch_subtypes: function (watcher_id) {
        return ajax.jsonRpc('/mail/read_subscription_data', 'call', {
            res_model: this.view.model,
            watcher_id: watcher_id,
        });
    },

    _format_watchers: function (count){
        var str = '';
        if(count <= 0){
            str = _t('No watcher');
        }else if(count === 1){
            str = _t('One watcher');
        }else{
            str = ''+count+' '+_t('watchers');
        }
        return str;
    },

    /** Read on res.partner failed: only display the number of followers */
    display_generic: function () {
        this.$('.o_followers_actions').hide();
        this.$('.o_followers_list').hide();
        this.$('.o_followers_title_box > button').prop('disabled', true);
        this.$('.o_followers_count').html(this.value.length).parent().attr("title", this._format_watchers(this.value.length));
    },

    /** Display the followers */
    display_followers: function (records) {
        var self = this;
        this.followers = records || this.followers;
        this.trigger('followers_update', this.followers);

        // clean and display title
        var $followers_list = this.$('.o_followers_list').empty();
        this.$('.o_followers_count').html(this.value.length).parent().attr("title", this._format_watchers(this.value.length));
        var user_follower = _.filter(this.followers, function (rec) { return rec.is_uid;});
        this.message_is_follower = user_follower.length >= 1;
        this.follower_id = this.message_is_follower ? user_follower[0].id : undefined;

        // render the dropdown content
        $(qweb.render('obj.Watchers.add_more', {'widget': self})).appendTo($followers_list);
        var $follower_li;
        _(this.followers).each(function (record) {
            $follower_li = $(qweb.render('mail.Followers.partner', {
                'record': _.extend(record, {'avatar_url': '/web/image/' + record.res_model + '/' + record.res_id + '/image_small'}),
                'widget': self})
            );
            $follower_li.appendTo($followers_list);

            // On mouse-enter it will show the edit_subtype pencil.
            if (record.is_editable) {
                $follower_li.on('mouseenter mouseleave', function(e) {
                    $(e.currentTarget).find('.o_edit_subtype').toggleClass('hide', e.type === 'mouseleave');
                });
            }
        });
    },

    display_buttons: function () {
        if (this.message_is_follower) {
            this.$('button.o_followers_follow_button').removeClass('o_followers_notfollow').addClass('o_followers_following');
            this.$('.o_subtypes_list > .dropdown-toggle').attr('disabled', false);
            this.$('.o_followers_actions .dropdown-toggle').addClass('o_followers_following')
        } else {
            this.$('button.o_followers_follow_button').removeClass('o_followers_following').addClass('o_followers_notfollow');
            this.$('.o_subtypes_list > .dropdown-toggle').attr('disabled', true);
            this.$('.o_followers_actions .dropdown-toggle').removeClass('o_followers_following')
        }
    },

    /** Display subtypes: {'name': default, followed} */
    display_subtypes:function (data, dialog, display_warning) {
        var old_parent_model;
        var $list;
        if (dialog) {
            $list = $('<ul>').appendTo(this.dialog.$el);
        } else {
            $list = this.$('.o_subtypes_list ul');
        }
        $list.empty();

        this.data_subtype = data;

        _.each(data, function (record) {
            if (old_parent_model !== record.parent_model && old_parent_model !== undefined) {
                $list.append($('<li>').addClass('divider'));
            }
            old_parent_model = record.parent_model;
            record.followed = record.followed || undefined;
            $(qweb.render('mail.Followers.subtype', {'record': record,
                                                     'dialog': dialog,
                                                     'display_warning': display_warning && record.internal}))
            .appendTo($list);
        });

        if (display_warning) {
            $(qweb.render('mail.Followers.subtypes.warning')).appendTo(this.dialog.$el);
        }
    },

    do_follow: function () {
        var context = new data.CompoundContext(this.build_context(), {});
        this.$('.o_subtypes_list > .dropdown-toggle').attr('disabled', false);
        this.ds_model.call('obj_subscribe_watcher', [[this.view.datarecord.id],
                                                       [session.uid],
                                                       undefined,
                                                       context])
            .then(this.proxy('read_value'));

        _.each(this.$('.o_subtypes_list input'), function (record) {
            $(record).attr('checked', 'checked');
        });
    },

    /**
     * Remove users, partners, or channels from the watchers
     * @param {Array} [ids.user_ids] the user ids
     * @param {Array} [ids.partner_ids] the partner ids
     * @param {Array} [ids.channel_ids] the channel ids
     */
    do_unfollow: function (ids) {
        _(this.$('.o_subtype_checkbox')).each(function (record) {
            $(record).attr('checked',false);
        });

        this.$('.o_subtypes_list > .dropdown-toggle').attr('disabled', true);
        var context = new data.CompoundContext(this.build_context(), {});

        if (ids.partner_ids || ids.channel_ids) {
            return this.ds_model.call(
                'obj_unsubscribe', [
                    [this.view.datarecord.id],
                    ids.user_ids,
                    context]
                ).then(this.proxy('read_value'));
        } else {
            return this.ds_model.call(
                'obj_unsubscribe_watcher', [
                    [this.view.datarecord.id],
                    ids.user_ids,
                    context]
                ).then(this.proxy('read_value'));
        }
        return false;
    },

    do_update_subscription: function (event, follower_id, is_channel) {
        var self = this;
        var kwargs = {};
        var ids = {};
        var action_subscribe;
        var subtypes;
        this.data_subtype = {};

        if (follower_id !== undefined) {
            // Subtypes edited from the modal
            action_subscribe = 'obj_subscribe';
            subtypes = this.dialog.$('input[type="checkbox"]');
            if (is_channel) {
                ids.channel_ids = [follower_id];
            } else {
                ids.partner_ids = [follower_id];
            }
        } else {
            action_subscribe = 'obj_subscribe_watcher';
            subtypes = this.$('.o_followers_actions input[type="checkbox"]');
            ids.user_ids = [session.uid];
        }
        kwargs = _.extend(kwargs, ids);

        // Get the subtype ids
        var checklist = [];
        _(subtypes).each(function (record) {
            if ($(record).is(':checked')) {
                checklist.push(parseInt($(record).data('id')));
            }
        });
        kwargs.subtype_ids = checklist;

        // If no more subtype followed, unsubscribe the follower
        if (!checklist.length) {
            if (!this.do_unfollow(ids)) {
                $(event.target).prop("checked", true);
            } else {
                self.$('.o_subtypes_list ul').empty();
            }
        } else {
            kwargs.context = new data.CompoundContext(this.build_context(), {});
            return this.ds_model._model.call(action_subscribe, [[this.view.datarecord.id]], kwargs).then(this.proxy('read_value'));
        }
    },
});

Chatter.include({

    start: function () {
        this._super.apply(this, arguments);
        var self = this;

        // Move the follower's widget (if any) inside the chatter
        this.watchers = this.field_manager.fields.obj_watcher_ids;
        if (this.watchers) {
            this.$('.o_chatter_topbar').append(this.watchers.$el);
            this.watchers.on('redirect', chat_manager, chat_manager.redirect);
            this.watchers.on('watchers_update', this, this.on_watchers_update);
        }
    },

    on_watchers_update: function (watchers) {
        this.mention_suggestions = [];
        var self = this;
        var prefetched_partners = chat_manager.get_mention_partner_suggestions();
        var watcher_suggestions = [];
        _.each(watchers, function (watcher) {
            if (watcher.res_model === 'res.partner') {
                watcher_suggestions.push({
                    id: watcher.res_id,
                    name: watcher.name,
                    email: watcher.email,
                });
            }
        });
        if (watcher_suggestions.length) {
            this.mention_suggestions.push(watcher_suggestions);
        }
        _.each(prefetched_partners, function (partners) {
            self.mention_suggestions.push(_.filter(partners, function (partner) {
                return !_.findWhere(watcher_suggestions, { id: partner.id });
            }));
        });
    },

})

core.form_widget_registry.add('obj_watchers', Watchers);

});
