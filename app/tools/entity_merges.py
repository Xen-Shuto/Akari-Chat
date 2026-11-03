"""Reviewed two-file merges with durable originals and explicit crash recovery."""
import hashlib
import json
import os
import re
import uuid
from litechat.file_editor import EditConflict, decode, normalize, safe_path
from litechat.retrieval import text_documents, MAX_BYTES, MAX_CHUNKS
from .contracts import ToolFailure


PENDING=('prepared','undo_prepared','conflict')


class EntityMerges:
    def __init__(self, resources, states):
        self.resources,self.states,self.store=resources,states,resources.store
        self.editor=resources.editor
        self.fault=lambda stage:None
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS entity_merges(id TEXT PRIMARY KEY,room TEXT,draft_id INTEGER,target TEXT,source TEXT,phase TEXT,action TEXT,snapshot TEXT,before_target BLOB,before_source BLOB,after_target BLOB,error TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE INDEX IF NOT EXISTS entity_merge_room ON entity_merges(room,created)')
            db.execute('CREATE TABLE IF NOT EXISTS entity_merge_requests(room TEXT,request_id TEXT,fingerprint TEXT,merge_id TEXT,action TEXT,result TEXT,PRIMARY KEY(room,request_id))')
        states.merges=self

    def room(self, room):return self.store.room_path(room).resolve().name

    def get(self, room, key, db=None):
        if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('統合IDが不正です。')
        if db is None:
            with self.store.connect() as connection:return self.get(room,key,connection)
        row=db.execute('SELECT * FROM entity_merges WHERE room=? AND id=?',(self.room(room),key)).fetchone()
        if row is None:raise ValueError('統合履歴がありません。')
        return dict(row)

    def annotations(self, room, db=None):
        if db is None:
            with self.store.connect() as connection:return self.annotations(room,connection)
        result={}
        for row in db.execute("SELECT id,target,source,phase FROM entity_merges WHERE room=? AND phase!='undone'",(self.room(room),)):
            if row['phase'] in PENDING:
                for name in (row['target'],row['source']):result[self.states.key(self.states.path(name))]={'merge_pending':True,'merge_id':row['id']}
            elif row['phase']=='applied':
                key=self.states.key(self.states.path(row['source']))
                result.setdefault(key,{}).update(merge_id=row['id'],merge_target=row['target'])
        return result

    def guard(self, room, path, *, source=False):
        key=self.states.key(path)
        with self.store.connect() as db:
            for row in db.execute("SELECT target,source,phase FROM entity_merges WHERE room=? AND phase!='undone'",(self.room(room),)):
                target,origin=(self.states.key(self.states.path(row[name])) for name in ('target','source'))
                if row['phase'] in PENDING and key in (target,origin):raise EditConflict('未完了の統合があります。統合履歴から復旧してください。')
                if row['phase']=='applied' and (key==origin or source and key==target):raise EditConflict('統合済みの退避元、または別の統合を含む記憶です。先に関連する統合を取り消してください。')

    def archive(self, row):return safe_path(self.store.root,'entity_merges/'+row['id']+'/source.md')

    def path(self, row, side):return safe_path(self.store.room_path(row['room']),self.states.path(row[side]))

    def raw(self, path):return self.editor.read_bytes(path) if path.is_file() else None

    def receipt(self, db, room, request, fingerprint):
        if not isinstance(request,str) or not re.fullmatch('[a-zA-Z0-9_-]{1,128}',request):raise ValueError('要求IDが不正です。')
        row=db.execute('SELECT * FROM entity_merge_requests WHERE room=? AND request_id=?',(room,request)).fetchone()
        if not row:return None
        if row['fingerprint']!=fingerprint:raise EditConflict('同じ要求IDに異なる操作が指定されました。')
        if row['result']:return {**json.loads(row['result']),'replayed':True}
        return {'id':row['merge_id'],'status':'unknown','replayed':True,'recovery_required':True}

    def claim(self, db, room, request, fingerprint, key, action):
        db.execute('INSERT INTO entity_merge_requests VALUES(?,?,?,?,?,NULL)',(room,request,fingerprint,key,action))

    def merge(self, room, draft_id, text, target_state_revision, source_state_revision, request, checkpoint=lambda:None):
        room=self.room(room)
        fingerprint=hashlib.sha256(json.dumps(['merge',draft_id,text,target_state_revision,source_state_revision],ensure_ascii=False).encode()).hexdigest()
        with self.store.lock:
            with self.store.connect() as db:
                checkpoint();saved=self.receipt(db,room,request,fingerprint)
                if saved:return saved
                registered=db.execute('SELECT 1 FROM entity_review_requests WHERE room=? AND draft_id=?',(room,draft_id)).fetchone()
                if not registered:raise ValueError('エンティティ整理専用の確認待ち案を選択してください。')
                review=self.resources.review(room,draft_id)
                if review['conflict'] or review['kind']!='entity' or len(review['source_documents'])!=1:raise EditConflict('資料が変わりました。整理案を読み直してください。')
                dep=review['source_documents'][0];target,source=review['name'],dep['name']
                if dep['kind']!='entity' or self.states.key(self.states.path(target))==self.states.key(self.states.path(source)):raise ValueError('異なる2件のエンティティが必要です。')
                self.guard(room,self.states.path(target));self.guard(room,self.states.path(source),source=True)
                a,b=review['current'],dep['document']
                if not all(doc['exists'] and doc['editable'] for doc in (a,b)):raise ValueError('両方が既存の編集可能な本文である必要があります。')
                if a['state_revision']!=target_state_revision or b['state_revision']!=source_state_revision:raise EditConflict('本文または状態が変わりました。両方を読み直してください。')
                root=self.store.room_path(room)
                if safe_path(root,a['path']).samefile(safe_path(root,b['path'])):raise ValueError('同じ実ファイルは統合できません。')
                if not isinstance(text,str) or not text.strip() or len(text)>64000:raise ValueError('統合後の本文は1～64000文字にしてください。')
                try:after=normalize(text).replace('\n',{'LF':'\n','CRLF':'\r\n','CR':'\r'}[a['newline']]).encode(a['encoding'])
                except UnicodeEncodeError as error:raise ValueError('保存先の文字コードでは表せない文字があります。') from error
                if a['bom']:after=b'\xef\xbb\xbf'+after
                if len(after)>1048576:raise ValueError('統合後の本文が1MiBを超えます。')
                before_a,before_b=(self.editor.read_bytes(safe_path(root,doc['path'])) for doc in (a,b))
                if any(hashlib.sha256(doc['path'].encode()+b'\x01'+raw).hexdigest()!=doc['revision'] for doc,raw in ((a,before_a),(b,before_b))):raise EditConflict('原本の読み込み中に内容が変わりました。')
                if any(self.resources.read(room,'entity',name)['revision']!=doc['revision'] for name,doc in ((target,a),(source,b))):raise EditConflict('保存直前に本文が変わりました。')
                key=uuid.uuid4().hex
                snapshot={'source_state':b['state'],'source_version':b['state_version'],'target_state_revision':a['state_revision'],'source_state_revision':b['state_revision'],'applied':False}
                db.execute("INSERT INTO entity_merges(id,room,draft_id,target,source,phase,action,snapshot,before_target,before_source,after_target) VALUES(?,?,?,?,?,'prepared','merge',?,?,?,?)",(key,room,draft_id,target,source,json.dumps(snapshot),before_a,before_b,after))
                self.claim(db,room,request,fingerprint,key,'merge')
            try:
                self.fault('prepared');checkpoint()
                row=self.get(room,key)
                for label in ('before_target','before_source','after_target'):
                    backup=safe_path(self.store.root,'entity_merges/'+key+'/'+label+'.bin',create=True)
                    with backup.open('xb') as stream:stream.write(row[label]);stream.flush();os.fsync(stream.fileno())
                self.write(row,'target',row['after_target'],(row['before_target'],));self.fault('target_written');checkpoint()
                archive=self.archive(row);archive.parent.mkdir(parents=True,exist_ok=True)
                if archive.exists():raise EditConflict('退避先に既存ファイルがあります。上書きしていません。')
                if self.raw(self.path(row,'source'))!=row['before_source']:raise EditConflict('退避前に元側が変わりました。')
                os.rename(self.path(row,'source'),archive)
                if self.raw(archive)!=row['before_source']:raise EditConflict('退避した元側の内容が変わっています。')
                self.fault('source_archived');checkpoint()
                result=self.finish_applied(row,request,text)
                self.fault('applied');return result
            except Exception as error:return self.failed(room,key,request,'merge',error)

    def write(self, row, side, desired, allowed):
        path=self.path(row,side);current=self.raw(path)
        if current not in allowed:raise EditConflict('統合後に本文が変更・再作成されています。上書きしません。')
        if current==desired:return
        temporary=path.with_name('.'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as stream:stream.write(desired);stream.flush();os.fsync(stream.fileno())
            path=self.path(row,side)
            if self.raw(path)!=current:raise EditConflict('書込直前に本文が変わりました。')
            if current is not None:os.replace(temporary,path)
            elif os.name=='nt':os.rename(temporary,path)
            else:os.link(temporary,path)
        finally:temporary.unlink(missing_ok=True)

    def source_state(self, db, row, state):
        path=self.states.path(row['source']);doc=self.editor.read_snapshot(row['room'],'resource','エンティティ記憶',path)
        before=self.states.describe(row['room'],row['source'],doc,db)
        db.execute('INSERT INTO entity_states(room,path,state,version,body_revision) VALUES(?,?,?,?,?) ON CONFLICT(room,path) DO UPDATE SET state=excluded.state,version=excluded.version,body_revision=excluded.body_revision,updated=CURRENT_TIMESTAMP',(row['room'],self.states.key(path),state,before['state_version']+1,doc['revision']))
        current=self.states.describe(row['room'],row['source'],doc,db)
        result={'previous':before['state'],'changed':True,'current':current,'reason':'entity_merge'}
        request='merge_'+row['id']+'_'+state
        db.execute('INSERT INTO entity_state_events(room,path,request_id,fingerprint,result) VALUES(?,?,?,?,?)',(row['room'],self.states.key(path),request,hashlib.sha256(request.encode()).hexdigest(),json.dumps(result)))
        return current

    def finish_applied(self, row, request, text):
        with self.store.connect() as db:
            snapshot=json.loads(row['snapshot']);snapshot['applied']=True
            db.execute("UPDATE entity_merges SET phase='applied' WHERE id=?",(row['id'],))
            source=self.source_state(db,row,'merged')
            target=self.editor.read_snapshot(row['room'],'resource','エンティティ記憶',self.states.path(row['target']))
            snapshot.update(target_after_state_revision=self.states.describe(row['room'],row['target'],target,db)['state_revision'],source_after_version=source['state_version'])
            db.execute("UPDATE entity_merges SET snapshot=?,updated=CURRENT_TIMESTAMP WHERE id=?",(json.dumps(snapshot),row['id']))
            db.execute("UPDATE resource_drafts SET status='approved',text=? WHERE room=? AND id=?",(text,row['room'],row['draft_id']))
            result={'id':row['id'],'status':'ok','phase':'applied','replayed':False}
            db.execute('UPDATE entity_merge_requests SET result=? WHERE room=? AND request_id=?',(json.dumps(result),row['room'],request))
        return result

    def check_restore(self, row):
        a,b=self.raw(self.path(row,'target')),self.raw(self.path(row,'source'))
        if a not in (row['before_target'],row['after_target']) or b not in (None,row['before_source']):raise EditConflict('処理後に本文が変更・再作成されています。保存済み原本と現在の本文を確認してください。')
        archive=self.raw(self.archive(row))
        if archive is not None and archive!=row['before_source']:raise EditConflict('退避ファイルが変更されています。原本を上書きせず確認してください。')

    def check_undo(self, row):
        if row['phase']!='applied':raise EditConflict('取り消し対象の統合ではありません。')
        snapshot=json.loads(row['snapshot'])
        a=self.resources.read(row['room'],'entity',row['target']);b=self.resources.read(row['room'],'entity',row['source'])
        if a.get('merge_pending'):raise EditConflict('先に未完了の統合を復旧してください。')
        if a['state_revision']!=snapshot['target_after_state_revision'] or b['exists'] or b['state']!='merged' or b['state_version']!=snapshot['source_after_version']:raise EditConflict('後の編集・状態変更・統合があります。最新の統合から取り消してください。')
        self.check_restore(row)

    def restore_files(self, row):
        self.check_restore(row)
        self.write(row,'target',row['before_target'],(row['before_target'],row['after_target']))
        self.write(row,'source',row['before_source'],(None,row['before_source']))

    def finish_undone(self, row, request, status='ok', error=''):
        with self.store.connect() as db:
            snapshot=json.loads(row['snapshot'])
            db.execute("UPDATE entity_merges SET phase='undone',error=?,updated=CURRENT_TIMESTAMP WHERE id=?",(error[:300],row['id']))
            if snapshot['applied']:
                self.source_state(db,row,snapshot['source_state'])
                db.execute("UPDATE resource_drafts SET status='undone' WHERE room=? AND id=?",(row['room'],row['draft_id']))
            result={'id':row['id'],'status':status,'phase':'undone','rolled_back':status!='ok','replayed':False}
            pending=db.execute('SELECT request_id,action FROM entity_merge_requests WHERE merge_id=? AND result IS NULL',(row['id'],)).fetchall()
            for receipt in pending:
                value=result if receipt['request_id']==request else dict(result,status='error' if receipt['action']=='merge' else 'ok')
                db.execute('UPDATE entity_merge_requests SET result=? WHERE room=? AND request_id=?',(json.dumps(value),row['room'],receipt['request_id']))
        return result

    def failed(self, room, key, request, action, error):
        row=self.get(room,key)
        with self.store.connect() as db:
            receipt=db.execute('SELECT result FROM entity_merge_requests WHERE room=? AND request_id=?',(room,request)).fetchone()
        if row['phase'] in ('applied','undone') and receipt and receipt[0]:return json.loads(receipt[0])
        try:
            self.restore_files(row)
            return self.finish_undone(row,request,'cancelled' if action=='merge' and isinstance(error,ToolFailure) and error.status=='cancelled' else 'error' if action=='merge' else 'ok',str(error))
        except Exception:
            try:
                with self.store.connect() as db:db.execute("UPDATE entity_merges SET phase='conflict',error=?,updated=CURRENT_TIMESTAMP WHERE id=?",(str(error)[:300],key))
            except Exception:pass  # Prepared journal and immutable blobs still block writes.
            return {'id':key,'status':'unknown','phase':'conflict','recovery_required':True,'replayed':False}

    def undo(self, room, key, revision, request, recover=False, checkpoint=lambda:None):
        room=self.room(room);action='recover' if recover else 'undo'
        fingerprint=hashlib.sha256(json.dumps([action,key,revision]).encode()).hexdigest()
        with self.store.lock:
            with self.store.connect() as db:
                checkpoint();saved=self.receipt(db,room,request,fingerprint)
                if saved:return saved
                row=self.get(room,key,db)
                if self.detail(room,key)['revision']!=revision:raise EditConflict('統合履歴または本文が変わりました。読み直してください。')
                if recover:
                    if row['phase'] not in PENDING:raise EditConflict('復旧対象の未完了処理ではありません。')
                    self.check_restore(row)
                else:self.check_undo(row)
                self.claim(db,room,request,fingerprint,key,action)
                db.execute("UPDATE entity_merges SET phase='undo_prepared',action=?,updated=CURRENT_TIMESTAMP WHERE id=?",(action,key))
            try:
                self.fault('undo_prepared');checkpoint()
                self.write(row,'target',row['before_target'],(row['before_target'],row['after_target']));self.fault('target_restored');checkpoint()
                self.write(row,'source',row['before_source'],(None,row['before_source']));self.fault('source_restored');checkpoint()
                result=self.finish_undone(row,request);self.fault('undone');return result
            except Exception as error:return self.failed(room,key,request,action,error)

    def listing(self, room, before=0):
        if type(before) is not int or not 0<=before<2**63:raise ValueError('履歴の開始位置が不正です。')
        with self.store.connect() as db:return [dict(row) for row in db.execute('SELECT rowid AS sequence,id,target,source,phase,created,error FROM entity_merges WHERE room=? AND (?=0 OR rowid<?) ORDER BY rowid DESC LIMIT 50',(self.room(room),before,before))]

    def detail(self, room, key):
        row=self.get(room,key)
        a,b=(self.resources.read(room,'entity',row[name]) for name in ('target','source'))
        archive=self.raw(self.archive(row));reason='';can_undo=can_recover=False
        try:
            if row['phase']=='applied':self.check_undo(row);can_undo=True
            elif row['phase'] in PENDING:self.check_restore(row);can_recover=True
        except ValueError as error:reason=str(error)
        revision=hashlib.sha256(json.dumps([key,row['phase'],row['snapshot'],a['state_revision'],b['state_revision'],hashlib.sha256(archive).hexdigest() if archive is not None else None]).encode()).hexdigest()
        return {'id':key,'draft_id':row['draft_id'],'created':row['created'],'phase':row['phase'],'target':row['target'],'source':row['source'],'revision':revision,'reason':reason,
                'can_undo':can_undo,'can_recover':can_recover,'current_target':a,'current_source':b,
                'before_target':decode(row['before_target'])['text'],'before_source':decode(row['before_source'])['text'],
                'planned_target':decode(row['after_target'])['text'],'archive':'app/data/entity_merges/'+key+'/source.md','archive_exists':archive is not None}

    def retained(self, room):
        documents=[];used=0
        with self.store.connect() as db:
            for row in db.execute("SELECT target,source,before_target,before_source FROM entity_merges WHERE room=? AND phase!='undone'",(self.room(room),)):
                for name in ('target','source'):
                    raw=row['before_'+name];used+=len(raw)
                    if used>MAX_BYTES:return [],['統合原本の検索キャッシュ保全上限を超えました。整理は中止します。']
                    documents.extend(text_documents(decode(raw)['text'],self.states.path(row[name]),'memory'))
                    if len(documents)>MAX_CHUNKS:return [],['統合原本の断片数が保全上限を超えました。整理は中止します。']
        return documents,[]
