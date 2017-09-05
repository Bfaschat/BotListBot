import random
import re

import emoji

import captions
import helpers
import mdformat
import settings
import util
from components import botlistchat
from const import CallbackActions, CallbackStates
from dialog import messages
from lib import InlineCallbackButton
from model import Bot, Category, User, Keyword, Favorite
from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram.ext import CommandHandler
from telegram.ext import ConversationHandler
from util import track_groups, private_chat_only


def explore(bot, update, chat_data):
    cid = update.effective_chat.id
    uid = update.effective_user.id
    mid = util.mid_from_update(update)
    explorable_bots = Bot.explorable_bots()

    chat_data['explored'] = chat_data.get('explored', list())

    # don't explore twice
    for explored in chat_data['explored']:
        explorable_bots.remove(explored)

    if len(explorable_bots) == 0:
        util.send_md_message(bot, cid, mdformat.none_action(
            "You have explored all the bots. Congratulations, you might be the first 😜"
        ))
        return

    random_bot = random.choice(explorable_bots)

    buttons = [
        [
            InlineKeyboardButton(captions.ADD_TO_FAVORITES, callback_data=util.callback_for_action(
                CallbackActions.ADD_TO_FAVORITES, {'id': random_bot.id})),
            InlineKeyboardButton(captions.SHARE, switch_inline_query=random_bot.username)
        ], [
            InlineKeyboardButton(random_explore_text(), callback_data=util.callback_for_action(
                CallbackActions.EXPLORE_NEXT)),
        ]
    ]

    markup = InlineKeyboardMarkup(buttons)

    text = random_bot.detail_text

    if uid in settings.MODERATORS and util.is_private_message(update):
        text += '\n\n🛃 /edit{}'.format(random_bot.id)

    msg = bot.formatter.send_or_edit(cid, text, to_edit=mid, reply_markup=markup)
    chat_data['explored'].append(random_bot)

    # import time
    # time.sleep(2)
    # msg.edit_reply_markup(reply_markup=ForceReply(selective=True))


def random_explore_text():
    choices = ["Explore", "Get me another", "Next", "Another one", "More", "One more", "Next one", "Hit me"]
    return '{} 🔄'.format(random.choice(choices))


def _select_category_buttons(callback_action=None):
    if callback_action is None:
        # set default
        callback_action = CallbackActions.SELECT_BOT_FROM_CATEGORY
    categories = Category.select().order_by(Category.name.asc()).execute()

    buttons = util.build_menu([InlineKeyboardButton(
        '{}{}'.format(emoji.emojize(c.emojis, use_aliases=True), c.name),
        callback_data=util.callback_for_action(
            callback_action, {'id': c.id})) for c in categories], 2)
    buttons.insert(0, [InlineKeyboardButton(
        '🆕 New Bots', callback_data=util.callback_for_action(CallbackActions.NEW_BOTS_SELECTED))])
    return buttons


@track_groups
def select_category(bot, update, chat_data, callback_action=None):
    chat_id = update.effective_chat.id
    reply_markup = InlineKeyboardMarkup(_select_category_buttons(callback_action))
    reply_markup, callback = botlistchat.append_delete_button(update, chat_data, reply_markup)
    msg = bot.formatter.send_or_edit(chat_id, util.action_hint(messages.SELECT_CATEGORY),
                                       to_edit=util.mid_from_update(update),
                                       reply_markup=reply_markup)
    callback(msg)
    return ConversationHandler.END


def show_new_bots(bot, update, chat_data, back_button=False):
    chat_id = update.effective_chat.id
    channel = helpers.get_channel()
    buttons = [[
        InlineKeyboardButton("Show in BotList",
                             url="http://t.me/{}/{}".format(channel.username, channel.new_bots_mid)),
        InlineKeyboardButton("Share", switch_inline_query=messages.NEW_BOTS_INLINEQUERY)
    ]]
    if back_button:
        buttons[0].insert(0, InlineKeyboardButton(captions.BACK, callback_data=util.callback_for_action(
            CallbackActions.SELECT_CATEGORY
        )))
    reply_markup = InlineKeyboardMarkup(buttons)
    reply_markup, callback = botlistchat.append_delete_button(update, chat_data, reply_markup)
    msg = bot.formatter.send_or_edit(chat_id, _new_bots_text(), to_edit=util.mid_from_update(update),
                                       reply_markup=reply_markup, reply_to_message_id=util.mid_from_update(update))
    callback(msg)
    return ConversationHandler.END


def send_category(bot, update, chat_data, category=None):
    print(category)
    uid = util.uid_from_update(update)
    cid = update.effective_chat.id
    bots = Bot.of_category_without_new(category)[:settings.MAX_BOTS_PER_MESSAGE]
    bots_with_description = [b for b in bots if b.description is not None]
    detailed_buttons_enabled = len(bots_with_description) > 0 and util.is_private_message(update)

    callback = CallbackActions.SEND_BOT_DETAILS

    if detailed_buttons_enabled:
        buttons = [InlineKeyboardButton(x.username, callback_data=util.callback_for_action(
            callback, {'id': x.id})) for x in bots_with_description]
    else:
        buttons = []
    menu = util.build_menu(buttons, 2)
    menu.insert(0, [
        InlineKeyboardButton(captions.BACK, callback_data=util.callback_for_action(
            CallbackActions.SELECT_CATEGORY
        )),
        InlineKeyboardButton("Show in BotList", url='http://t.me/botlist/{}'.format(category.current_message_id)),
        InlineKeyboardButton("Share", switch_inline_query=category.name)
    ])
    txt = "There are *{}* bots in the category *{}*:\n\n".format(len(bots), str(category))

    if uid in settings.MODERATORS and util.is_private_message(update):
        # append admin edit buttons
        txt += '\n'.join(["{} — /edit{} 🛃".format(b, b.id) for b in bots])
    else:
        txt += '\n'.join([str(b) for b in bots])

    if detailed_buttons_enabled:
        txt += "\n\n" + util.action_hint("Press a button below to get a detailed description.")

    reply_markup = InlineKeyboardMarkup(menu)
    reply_markup, callback = botlistchat.append_delete_button(update, chat_data, reply_markup)
    msg = bot.formatter.send_or_edit(cid, txt, to_edit=util.mid_from_update(update), reply_markup=reply_markup)
    callback(msg)


@private_chat_only
def send_bot_details(bot, update, chat_data, item=None):
    uid = util.uid_from_update(update)
    user = User.from_update(update)
    first_row = list()

    if item is None:
        try:
            text = update.message.text
            bot_in_text = re.findall(settings.REGEX_BOT_IN_TEXT, text)[0]
            item = Bot.by_username(bot_in_text)

        # except (AttributeError, Bot.DoesNotExist):
        except Bot.DoesNotExist:
            update.message.reply_text(util.failure(
                "This bot is not in the @BotList. If you think this is a mistake, see the /examples for /contributing."))
            return

    if item.approved:
        # bot is already in the botlist => show information
        txt = item.detail_text
        if item.description is None and not Keyword.select().where(Keyword.entity == item).exists():
            txt += ' is in the @BotList.'
        btn = InlineCallbackButton(captions.BACK_TO_CATEGORY, CallbackActions.SELECT_BOT_FROM_CATEGORY,
                                   {'id': item.category.id})
        # btn = InlineKeyboardButton(captions.BACK_TO_CATEGORY, callback_data=util.callback_for_action(
        #     CallbackActions.SELECT_BOT_FROM_CATEGORY, {'id': item.category.id}
        # ))
        first_row.insert(0, btn)
        first_row.append(InlineKeyboardButton(captions.SHARE, switch_inline_query=item.username))

        if uid in settings.MODERATORS:
            first_row.append(InlineKeyboardButton(
                "🛃 Edit", callback_data=util.callback_for_action(
                    CallbackActions.EDIT_BOT,
                    {'id': item.id}
                )))
    else:
        txt = '{} is currently pending to be accepted for the @BotList.'.format(item)
        if uid in settings.MODERATORS:
            first_row.append(InlineKeyboardButton(
                "🛃 Accept / Reject", callback_data=util.callback_for_action(
                    CallbackActions.APPROVE_REJECT_BOTS,
                    {'id': item.id}
                )))

    buttons = [first_row]
    favorite_found = Favorite.search_by_bot(user, item)
    if favorite_found:
        buttons.append([
            InlineKeyboardButton(captions.REMOVE_FAVORITE_VERBOSE,
                                 callback_data=util.callback_for_action(CallbackActions.REMOVE_FAVORITE,
                                                                        {'id': favorite_found.id, 'details': True}))
        ])
    else:
        buttons.append([
            InlineKeyboardButton(captions.ADD_TO_FAVORITES,
                                 callback_data=util.callback_for_action(CallbackActions.ADD_TO_FAVORITES,
                                                                        {'id': item.id, 'details': True}))
        ])
    reply_markup = InlineKeyboardMarkup(buttons)
    reply_markup, callback = botlistchat.append_delete_button(update, chat_data, reply_markup)
    msg = bot.formatter.send_or_edit(uid,
                                       txt,
                                       to_edit=util.mid_from_update(update),
                                       reply_markup=reply_markup
                                       )
    callback(msg)
    return CallbackStates.SHOWING_BOT_DETAILS


def _new_bots_text():
    new_bots = Bot.select_new_bots()
    if len(new_bots) > 0:
        txt = "Fresh new bots since the last update 💙:\n\n{}".format(
            Bot.get_new_bots_markdown())
    else:
        txt = 'No new bots available.'
    return txt