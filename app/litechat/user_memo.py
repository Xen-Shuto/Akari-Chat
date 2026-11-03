"""Human-authored instructions for ordinary autonomy, with original-byte backups."""
class UserMemo:
    def __init__(self,editor):self.editor=editor
    def read(self,room):return self.editor.read_snapshot(room,'user_memo','ユーザーの書き置き','user_memo.txt')
    def save(self,room,body,revision):
        if not isinstance(body,str) or len(body)>4000:raise ValueError('書き置きは4000文字までです。')
        return self.editor.save_snapshot(room,'user_memo',body,revision,lambda:self.read(room))
    def context(self,room):
        doc=self.read(room)
        return dict(text=doc['text'][:4000],revision=doc['revision'],truncated=len(doc['text'])>4000,used_chars=min(4000,len(doc['text'])))
