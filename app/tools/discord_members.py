"""Permission-filtered channel members, with honest bounded pagination."""
from .external_providers import ProviderError, segment

def visible(guild,channel,member):
 user=member.get('user',{});uid=user.get('id');gid=guild['id'];roles={r['id']:int(r['permissions']) for r in guild.get('roles',[])}
 permission=roles.get(gid,0)
 for role in member.get('roles',[]):permission|=roles.get(role,0)
 if uid==guild.get('owner_id') or permission&8:return True
 overwrites=channel.get('permission_overwrites',[])
 def apply(base,overwrite):return (base&~int(overwrite.get('deny',0)))|int(overwrite.get('allow',0))
 for overwrite in overwrites:
  if overwrite.get('id')==gid:permission=apply(permission,overwrite)
 deny=allow=0
 for overwrite in overwrites:
  if overwrite.get('type')==0 and overwrite.get('id') in member.get('roles',[]):deny|=int(overwrite.get('deny',0));allow|=int(overwrite.get('allow',0))
 permission=(permission&~deny)|allow
 for overwrite in overwrites:
  if overwrite.get('type')==1 and overwrite.get('id')==uid:permission=apply(permission,overwrite)
 return bool(permission&1024)

def partial_members(external,guild,channel,token,channel_id,checkpoint):
 # Bound the fallback to recent authors and verify their current guild roles.
 checkpoint()
 rows=external.fetch('discord','GET','/api/v10/channels/'+segment(channel_id)+'/messages?limit=20',token)
 if not isinstance(rows,list):raise ValueError('チャンネルの最近の発言を取得できません。')
 candidates={}
 for row in rows:
  if not isinstance(row,dict):continue
  author=row.get('author') or {}
  if not isinstance(author,dict):continue
  uid=author.get('id')
  if not row.get('webhook_id') and isinstance(uid,str) and uid.isascii() and uid.isdigit():
   candidates.setdefault(uid,None)
 raw=[]
 for uid in list(candidates)[:5]:
  checkpoint()
  try:member=external.fetch('discord','GET','/api/v10/guilds/'+segment(guild['id'])+'/members/'+segment(uid),token)
  except ProviderError as error:
   if error.status in (403,404):continue
   raise
  if member.get('user',{}).get('id')==uid and visible(guild,channel,member):raw.append(member)
 return {'channel':channel_id,'members':[member_summary(member) for member in raw],
         'complete':False,'more':False,'after':'',
         'meaning':'サーバーの全メンバー一覧を取得できなかったため、最近の発言者のうち現在の閲覧権限を確認できた人だけの部分一覧です。'}

def member_summary(member):
 user=member.get('user',{})
 return {'id':user.get('id'),'name':member.get('nick') or user.get('global_name') or user.get('username'),'bot':bool(user.get('bot'))}

def members(external,config,token,channel_id,after='',checkpoint=lambda:None):
 external._channel(config,channel_id)
 if after and (not isinstance(after,str) or not after.isascii() or not after.isdigit()):raise ValueError('次ページIDが不正です。')
 channel=external.fetch('discord','GET','/api/v10/channels/'+segment(channel_id),token)
 thread_type=channel.get('type')
 if thread_type not in (0,5,15,16,10,11,12):raise ValueError('サーバーのテキストチャンネル/スレッドを指定してください。')
 if thread_type in (10,11,12):
  parent=channel.get('parent_id')
  if not isinstance(parent,str) or not parent.isdigit():raise ValueError('親チャンネルを確認できません。')
  channel=external.fetch('discord','GET','/api/v10/channels/'+segment(parent),token)
 gid=channel.get('guild_id')
 if not isinstance(gid,str) or not gid.isdigit():raise ValueError('サーバー情報を取得できません。')
 guild=external.fetch('discord','GET','/api/v10/guilds/'+segment(gid),token)
 if thread_type==12:
  rows=external.fetch('discord','GET','/api/v10/channels/'+segment(channel_id)+'/thread-members?with_member=true&limit=100'+('&after='+segment(after) if after else ''),token)
  if not isinstance(rows,list):raise ValueError('スレッド参加者を取得できません。')
  raw=[r['member'] for r in rows if isinstance(r.get('member'),dict)]
 else:
  try:raw=external.fetch('discord','GET','/api/v10/guilds/'+segment(gid)+'/members?limit=100'+('&after='+segment(after) if after else ''),token)
  except ProviderError as error:
   if error.status==403 and not after and thread_type in (0,5,15,16):
    return partial_members(external,guild,channel,token,channel_id,checkpoint)
   raise
 if not isinstance(raw,list):raise ValueError('メンバー応答が不正です。')
 items=[]
 for member in raw:
  if visible(guild,channel,member):
   items.append(member_summary(member))
 page_ids=[str(r.get('user_id','')) for r in rows] if thread_type==12 else [str(m.get('user',{}).get('id','')) for m in raw]
 page_ids=[key for key in page_ids if key.isascii() and key.isdigit()]
 page_count=len(rows) if thread_type==12 else len(raw)
 next_id=max(page_ids,key=int) if page_count==100 and page_ids else ''
 return {'channel':channel_id,'members':items,'complete':thread_type!=12 and not after and len(raw)<100,'more':bool(next_id),'after':next_id,'meaning':'チャンネル閲覧権限を持つメンバーです。現在閲覧中/オンラインの一覧ではありません。'}
