"""Bounded room-owned Discord server/channel discovery."""
from .discord_access import channels

def snowflake(value):
 if not isinstance(value,str) or not value.isascii() or not value.isdigit() or not 1<=len(value)<=25:raise ValueError('Discordの数値IDを指定してください。')
 return value

def read_directory(external,common,config,action,args):
 token=external._token('discord',common)
 if action=='guilds':
  after=snowflake(args['after']) if args.get('after') else ''
  rows=external.fetch('discord','GET','/api/v10/users/@me/guilds?limit=200'+('&after='+after if after else ''),token)
  if not isinstance(rows,list) or len(rows)>200:raise ValueError('サーバー一覧の応答が不正です。')
  items=[{'id':snowflake(r.get('id')),'name':str(r.get('name',''))[:200]} for r in rows if isinstance(r,dict)]
  return {'guilds':items,'more':len(rows)==200,'after':max((r['id'] for r in items),key=int) if len(rows)==200 and items else ''}
 guild=snowflake(args.get('guild'))
 rows=external.fetch('discord','GET','/api/v10/guilds/'+guild+'/channels',token)
 if not isinstance(rows,list) or len(rows)>1000:raise ValueError('チャンネル一覧の応答が不正です。')
 return {'guild':guild,'channels':[{'id':snowflake(r.get('id')),'name':str(r.get('name',''))[:200],'allowed':r.get('id') in channels(config)} for r in rows if isinstance(r,dict) and r.get('type') in (0,5)]}
