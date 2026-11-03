"""Optional headed-login / bounded browser actions in an isolated Edge context. No stealth."""
import base64,json,queue,re,sys,threading,time
from pathlib import Path
from urllib.parse import urlsplit,urljoin
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP/'runtime/social_packages'))

def emit(value):print(json.dumps(value,ensure_ascii=True),flush=True)
def identity(page):
 if '/account/access' in page.url or page.locator('iframe[src*="arkoselabs"],iframe[src*="captcha"]').count():raise ValueError('追加確認が必要です。自動操作を中止しました。')
 link=page.locator('a[data-testid="AppTabBar_Profile_Link"]').first
 href=link.get_attribute('href',timeout=10000) or ''
 match=re.fullmatch(r'/(?:@)?([A-Za-z0-9_]{1,15})/?',href)
 if not match:raise ValueError('ログイン中アカウントを確認できません。')
 return match[1]
def clean_state(state):
 return {'cookies':[c for c in state.get('cookies',[]) if c.get('domain','').lstrip('.') in ('x.com','twitter.com')], 'origins':[o for o in state.get('origins',[]) if o.get('origin') in ('https://x.com','https://twitter.com')]}
def execute(page,args):
 action=args['action'];target='https://x.com/home'
 if action=='mentions':target='https://x.com/notifications/mentions'
 elif action=='notifications':target='https://x.com/notifications'
 elif action=='thread':
  p=urlsplit(args.get('url',''))
  if p.scheme!='https' or p.hostname!='x.com' or not re.fullmatch(r'/[A-Za-z0-9_]{1,15}/status/[0-9]+',p.path) or p.query or p.fragment:raise ValueError('Xの投稿URLを指定してください。')
  target=args['url']
 elif action=='post':target='https://x.com/compose/post'
 elif action not in ('identity','timeline'):raise ValueError('ブラウザ操作が不正です。')
 page.goto(target,wait_until='domcontentloaded',timeout=20000)
 own=identity(page)
 if args.get('username') and own.casefold()!=args['username'].casefold():raise ValueError('ログイン中アカウントが登録先と違います。')
 if action=='identity':return {'username':own}
 if action=='post':
  editor_selector='[data-testid="tweetTextarea_0"]'
  composer=page.locator('[role="dialog"]').filter(has=page.locator(editor_selector))
  composer.locator(editor_selector).fill(args['text'])
  extensions={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}
  files=[{'name':'image'+str(i)+extensions[image['mime']],'mimeType':image['mime'],'buffer':base64.b64decode(image['data'],validate=True)} for i,image in enumerate(args.get('images',[]))]
  if files:
   emit({'phase':'attaching'})
   composer.locator('input[type="file"][data-testid="fileInput"]').set_input_files(files)
   composer.locator('[data-testid="attachments"]').first.wait_for(state='visible',timeout=15000)
  identity(page)
  emit({'phase':'waiting_post_button'})
  button=composer.locator('[data-testid="tweetButton"]')
  button.click(trial=True,timeout=15000)
  emit({'phase':'committing'})
  button.click(timeout=5000)
  emit({'phase':'clicked'})
  link=page.locator('[data-testid="toast"] a[href*="/status/"]').first
  link.wait_for(state='visible',timeout=20000);url=urljoin('https://x.com',link.get_attribute('href') or '')
  p=urlsplit(url)
  if p.hostname!='x.com' or not re.fullmatch('/'+re.escape(own)+r'/status/[0-9]+',p.path,re.I):raise ValueError('投稿完了URLを確認できません。')
  return {'url':url,'username':own}
 rows=[]
 page.locator('article').first.wait_for(state='visible',timeout=15000)
 for article in page.locator('article').all()[:10]:
  text=article.inner_text()[:3000];links=article.locator('a[href*="/status/"]').all();url=urljoin('https://x.com',links[0].get_attribute('href') or '') if links else ''
  rows.append({'text':text,'url':url})
 return {'username':own,'items':rows,'complete':False}
def main():
 args=json.loads(sys.stdin.readline());action=args['action']
 from playwright.sync_api import sync_playwright
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='msedge',headless=action!='login')
  try:
   context=browser.new_context(storage_state=args.get('state') or None,viewport={'width':1280,'height':900});page=context.new_page()
   if action=='login':
    page.goto('https://x.com/i/flow/login',wait_until='domcontentloaded',timeout=20000);emit({'ready':True});commands=queue.Queue()
    threading.Thread(target=lambda:commands.put(sys.stdin.readline()),daemon=True).start();deadline=time.monotonic()+600
    while time.monotonic()<deadline and not page.is_closed():
     if not commands.empty():
      command=json.loads(commands.get() or '{}')
      if command.get('action')!='complete':return
      own=identity(page);emit({'result':{'state':clean_state(context.storage_state()),'username':own}});return
     page.wait_for_timeout(250)
    raise ValueError('ログインの待機時間が終了しました。')
   emit({'result':execute(page,args)})
  finally:browser.close()
if __name__=='__main__':
 try:main()
 except Exception:emit({'error':'Xブラウザ操作を完了できませんでした。ログイン状態・追加確認・画面変更を確認してください。'});sys.exit(1)
