"""Room-owned Discord channel and sender boundaries."""
def snowflake(value,label,optional=False):
 if optional and value=='':return value
 if not isinstance(value,str) or not value.isascii() or not value.isdigit() or not 1<=len(value)<=25:raise ValueError(label+'はDiscordの数値IDで指定してください。')
 return value

def channels(values):
 return list(dict.fromkeys(([values['default_channel']] if values.get('default_channel') else [])+values.get('channels',[])))

def user_allowed(values,user):
 return isinstance(user,str) and bool(user) and user in [values.get('user_id'),*values.get('allowed_user_ids',[])]

def checked(values):
 out=dict(values)
 for key,label in [('default_channel','デフォルトのチャンネルID'),('user_id','ユーザID')]:
  value=out.get(key,'')
  if isinstance(value,str):value=value.strip()
  if out.get('enabled') and not value:raise ValueError('Discordを有効にするには'+label+'が必要です。')
  out[key]=snowflake(value,label,optional=not out.get('enabled'))
 for key,label,limit in [('channels','許可するチャンネルID',20),('allowed_user_ids','許可するユーザID',100)]:
  items=out.get(key,[])
  if not isinstance(items,list) or len(items)>limit:raise ValueError(label+'は'+str(limit)+'件以内で指定してください。')
  out[key]=list(dict.fromkeys(snowflake(item,label) for item in items))
 out['channels']=[item for item in out['channels'] if item!=out['default_channel']]
 out['allowed_user_ids']=[item for item in out['allowed_user_ids'] if item!=out['user_id']]
 return out
