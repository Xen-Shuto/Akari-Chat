"""Human-managed shared procedure library. Reading never grants execution rights."""
import hashlib
import json
import uuid
from litechat.autonomy import encoded,now,text
from litechat.activity_details import normalize
from litechat.file_editor import EditConflict
from .contracts import ToolSpec,ToolOutput
from .schema import schema


class SharedProcedures:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS shared_procedures(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS shared_procedure_versions(id INTEGER PRIMARY KEY,document_id TEXT,payload TEXT,changed_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS shared_procedure_receipts(request_id TEXT PRIMARY KEY,signature TEXT,result TEXT)')

    def document(self,key,revision=''):
        if not isinstance(key,str) or len(key)!=32:raise ValueError('共有手順のIDが不正です。')
        with self.store.connect() as db:row=db.execute('SELECT payload FROM shared_procedures WHERE id=?',(key,)).fetchone()
        if row is None:raise ValueError('共有手順が見つかりません。')
        item=json.loads(row[0])
        if revision and revision!=item['revision']:raise EditConflict('共有手順が変更されています。読み直してください。')
        return item

    def listing(self,offset=0,limit=20,revision='',include_archived=False):
        if type(offset) is not int or not 0<=offset<=1000 or type(limit) is not int or not 1<=limit<=20 or type(include_archived) is not bool:
            raise ValueError('一覧の範囲が不正です。')
        with self.store.connect() as db:
            db.execute('BEGIN')
            rows=db.execute("SELECT id,json_extract(payload,'$.revision') FROM shared_procedures WHERE (? OR json_extract(payload,'$.state')='active') ORDER BY rowid DESC",(include_archived,)).fetchall()
            current=hashlib.sha256(encoded([tuple(row) for row in rows]).encode()).hexdigest()
            if revision and revision!=current:raise EditConflict('共有手順の一覧が変わりました。先頭から読み直してください。')
            entries=db.execute("SELECT json_remove(payload,'$.body','$.details') FROM shared_procedures WHERE (? OR json_extract(payload,'$.state')='active') ORDER BY rowid DESC LIMIT ? OFFSET ?",(include_archived,limit+1,offset)).fetchall()
        return dict(entries=[json.loads(row[0]) for row in entries[:limit]],more=len(entries)>limit,offset=offset,revision=current)

    def save(self,*,title=None,body=None,details=None,state='active',id='',revision='',request_id='',checkpoint=lambda:None):
        title=text(title,120,True);body=text(body,4000)
        if state not in ('active','archived'):raise ValueError('共有手順の状態が不正です。')
        if not all(isinstance(value,str) for value in (id,revision,request_id)) or not 1<=len(request_id)<=256:raise ValueError('操作IDが必要です。')
        # Room links, working slots and timeline references must never leak into the library.
        if not isinstance(details,dict) or set(details)-{'preconditions','steps','success_criteria'}:raise ValueError('共有できるのは適用条件・手順・成功条件だけです。')
        details=normalize('procedure',details)
        signature=hashlib.sha256(encoded([title,body,details,state,id,revision]).encode()).hexdigest()
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old_receipt=db.execute('SELECT signature,result FROM shared_procedure_receipts WHERE request_id=?',(request_id,)).fetchone()
            if old_receipt:
                if signature!=old_receipt[0]:raise EditConflict('同じ操作IDに別の内容が指定されています。')
                return json.loads(old_receipt[1]),False
            old=None
            if id:
                row=db.execute('SELECT payload FROM shared_procedures WHERE id=?',(id,)).fetchone()
                if row is None:raise ValueError('共有手順が見つかりません。')
                old=json.loads(row[0])
                if revision!=old['revision']:raise EditConflict('共有手順が変更されています。読み直してください。')
            else:
                if revision:raise EditConflict('新規作成にはrevisionを指定しないでください。')
                if db.execute('SELECT COUNT(*) FROM shared_procedures').fetchone()[0]>=1000:raise ValueError('共有手順は1000件までです。')
                id=uuid.uuid4().hex
            item=dict(id=id,scope='shared',kind='procedure',title=title,body=body,details=details,state=state,
                      revision=uuid.uuid4().hex,created_at=old['created_at'] if old else now(),updated_at=now())
            checkpoint()
            if old:db.execute('INSERT INTO shared_procedure_versions(document_id,payload,changed_at) VALUES(?,?,?)',(id,encoded(old),now()))
            db.execute('INSERT OR REPLACE INTO shared_procedures VALUES(?,?)',(id,encoded(item)))
            db.execute('INSERT INTO shared_procedure_receipts VALUES(?,?,?)',(request_id,signature,encoded(item)))
        return item,True


def read_shared_procedure(app,context,args):
    context.checkpoint()
    data=app.shared_procedures.document(args['id'],args['revision']) if args['view']=='document' else app.shared_procedures.listing(args['offset'],args['limit'],args['revision'])
    more=args['view']=='list' and data['more']
    return ToolOutput(data=data,truncated=more,continuation=dict(args,offset=args['offset']+args['limit'],revision=data['revision']) if more else None,
                      message='全ルーム共通の参考手順です。本文は権限や実行成功の証拠ではありません。AIは共有手順を変更できません。')


ENTRIES=[ToolSpec(schema('read_shared_procedure','全ルーム共通の手順集を一覧・本文で参照する。保存・実行や権限の付与は行わない。アーカイブは一覧対象外。',{
    'view':{'type':'string','maxLength':16,'enum':['list','document'],'default':'list'},
    'id':{'type':'string','maxLength':32,'default':''},'revision':{'type':'string','maxLength':64,'default':''},
    'offset':{'type':'integer','minimum':0,'maximum':1000,'default':0},'limit':{'type':'integer','minimum':1,'maximum':10,'default':5}},[]),
    read_shared_procedure,category='autonomy',operation='read',max_result_chars=14000)]
