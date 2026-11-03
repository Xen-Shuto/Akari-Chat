"""Discord buttons for a room's existing X outbox review."""
import json
import re

from .external_connections import packed
from .external_providers import segment

_BUTTON = re.compile(r'akari-x:(approve|reject|show):([a-f0-9]{32})')
_TOKEN = re.compile(r'[A-Za-z0-9._-]{1,256}')


def notify(external, item):
    room = item['room']
    try:
        connection, local = external._guard(room, 'discord')
        settings = external._config_for('discord', connection, local)
        channel, owner = settings.get('default_channel'), settings.get('user_id')
        if not channel or not owner or settings.get('receive_mode') != 'gateway':
            raise ValueError('Discordの常時接続・メインチャンネル・本人IDが必要です。')
        token = external._token('discord', connection)
        body = {
            'content': 'Xへの投稿の承認依頼です。公開する内容を確認してください。\n\n' + (item['payload']['text'] or '（画像のみ）')[:1500],
            'allowed_mentions': {'parse': []},
            'components': [{'type': 1, 'components': [
                {'type': 2, 'style': style, 'label': label, 'custom_id': f"akari-x:{action}:{item['id']}"}
                for action, label, style in [('show', '詳細', 2), ('approve', '承認して投稿', 3), ('reject', '却下', 4)]
            ]}],
        }
        for key, label in [('reply_to','返信先'),('quote_post','引用元')]:
            if item['payload'].get(key):
                body['content'] += f"\n\n{label} https://x.com/i/status/{item['payload'][key]}"
        response = external.fetch('discord', 'POST', '/api/v10/channels/'+segment(channel)+'/messages', token, body)
        message = response.get('id')
        if message:
            binding = dict(message=message, channel=channel, owner=owner, profile=connection['id'],
                           revision=connection['revision'], local_revision=local['revision'])
            with external.store.connect() as db:
                db.execute("UPDATE external_outbox SET result=? WHERE id=? AND status='pending'",
                           (packed({'discord_approval': binding}), item['id']))
        outcome = 'sent'
    except Exception:
        # Preserve the draft and never repeat a notification with uncertain delivery.
        outcome = 'unconfirmed'
    with external.store.connect() as db:
        external._audit(db, room, 'discord', 'x-approval-notification', outcome, item['id'])


def handle(external, profile, data, expected_revision):
    match = _BUTTON.fullmatch(str((data.get('data') or {}).get('custom_id','')))
    token = str(data.get('token',''))
    interaction = str(data.get('id',''))
    application = str(data.get('application_id',''))
    if not match or not _TOKEN.fullmatch(token) or not interaction.isascii() or not interaction.isdigit() or not application.isascii() or not application.isdigit():
        return
    action, key = match.groups()
    with external.app.lock, external.lock:
        with external.store.connect() as db:
            row = db.execute("SELECT * FROM external_outbox WHERE id=? AND provider='x'", (key,)).fetchone()
        if not row:
            return
        binding = json.loads(row['result']).get('discord_approval') or {}
        user = (data.get('member') or {}).get('user') or data.get('user') or {}
        message = data.get('message') or {}
        if (binding.get('profile'), binding.get('message'), binding.get('channel')) != (profile, message.get('id'), data.get('channel_id')):
            return
        callback = '/api/v10/interactions/'+interaction+'/'+token+'/callback'
        if user.get('id') != binding.get('owner'):
            external.fetch('discord','POST',callback,body={'type':4,'data':{'flags':64,'content':'この操作はあかり利用者本人のみ行えます。'}})
            return
        try:
            connection, local = external._guard(row['room'], 'discord')
            if connection['id'] != profile or connection['revision'] != binding['revision'] or local['revision'] != binding['local_revision'] or (expected_revision is not None and connection['revision'] != expected_revision):
                raise ValueError('Discordの接続設定が変わりました。')
            x_connection, x_local = external._guard(row['room'],'x')
            if (x_connection['revision'],x_local['revision']) != (row['common_revision'],row['room_revision']):
                raise ValueError('Xの接続設定が変わりました。')
        except ValueError:
            external.fetch('discord','POST',callback,body={'type':4,'data':{'flags':64,'content':'設定が変わりました。あかりの承認待ち画面を確認してください。'}})
            return
        webhook = '/api/v10/webhooks/'+application+'/'+token
        original = webhook+'/messages/@original'
        if action=='show' and row['status']=='pending':
            external.fetch('discord','POST',callback,body={'type':5,'data':{'flags':64}})
            try:
                payload=json.loads(row['payload'])
                images=external.media.load(row['room'],payload['images'],'x') if payload.get('images') else []
                text=payload['text'] or '（画像のみ）'
                content='X投稿案'
                for target,label in [('reply_to','返信先'),('quote_post','引用元')]:
                    if payload.get(target):
                        content+=f"\n{label} https://x.com/i/status/{payload[target]}"
                if len(text.encode('utf-16-le'))//2<=1500:
                    content+='\n\n'+text
                else:
                    images=[*images,({'mime':'text/plain'},text.encode('utf-8'))]
                    content+='\n全文は添付の post.txt を確認してください。'
                body={'content':content,'allowed_mentions':{'parse':[]}}
                if images:
                    body,headers=external.media.discord_body(body,images)
                    external.fetch('discord','PATCH',original,body=body,headers=headers)
                else:
                    external.fetch('discord','PATCH',original,body=body)
            except (ValueError,OSError):
                external.fetch('discord','PATCH',original,body={'content':'下書きの詳細を表示できませんでした。'})
            return
        # A deferred component update targets the notification that owns the buttons.
        external.fetch('discord','POST',callback,body={'type':6})
        status=row['status']
        if status=='pending':
            try:
                status=external.review(row['room'],key,row['revision'],action=='approve')['status']
            except Exception:
                external.fetch('discord','POST',webhook,body={'flags':64,'content':'操作を完了できませんでした。あかりの承認待ち画面で状態を確認してください。'})
                return
        message={'sent':'（投稿されました。）','rejected':'（却下されました。）',
                 'unknown':'（投稿結果は未確認です。Xで確認してください。）'}.get(status,'（この下書きは処理済みです。）')
        external.fetch('discord','PATCH',original,
                       body={'content':f'X投稿案 `{key}`\n{message}','components':[],
                             'allowed_mentions':{'parse':[]}})
