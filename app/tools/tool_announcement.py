"""Conservative text-only continuation hint, never an executor or permission grant."""
import re


def _prose(text):
    text=re.sub(r'```[\s\S]*?(?:```|$)', '', text)
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('>'))


# Require both an actual request and an affirmative announcement. This deliberately
# misses ambiguous wording rather than making consultation trigger operations.
ACTION=re.compile(r'して(?:みて|ください|くれ|ほしい|欲しい|ね|。|！|!|$)|して[、,]|描いて|作って|調べて|探して|読んで|残して|覚えて|メモして')
META=re.compile(r'使い方|仕組み|例文|説明して|方法を|できる(?:の|かな|か|？|\?)|可能(?:ですか|か|？|\?)')
PLEDGE=re.compile(r'(?:要求|実行|利用|参照|確認|保存|記録|更新|登録|作成|生成|取得|検索|お願い)(?:します|する|してみます|してみる)(?=ね|よ|[。！!\s]|$)|(?:呼び出します|呼び出す|使います|使う|調べます|調べてみる|探します|描いてみる|描きます)(?=ね|よ|[。！!\s]|$)')
CLARIFY=re.compile(r'教えて(?:ください|くれ|ほしい)|指定して(?:ください|くれ)|どんな.*[？?]|どの.*[？?]|しますか[？?]')

# Only recognize concrete operations with known names. Exact tool mentions also
# cover other tools without guessing an operation from an entire category.
OPERATIONS=(
    ('images','画像生成',r'描いて|(?:画像|イラスト|絵).*(?:生成して|生成を|作って)',r'画像|イラスト|絵|姿.*描|描いて',('generate_image',)),
    ('web','Web検索',r'検索して|(?:Web|ウェブ|ネット).*(?:調べて|探して)',r'Web|ウェブ|ネット|検索',('search_web',)),
    ('notes','短期メモの更新案',r'メモ.*(?:残して|保存して|更新して|書いて)|メモして',r'メモ',('propose_notepad',)),
)

# Discord accepts the room's full tool set. Keep matching concrete requests,
# not category mentions in ordinary RP or consultation.
EXTENDED_OPERATIONS=(
    ('notes','短期メモの参照',r'メモ.*(?:読んで|見せて|確認して)',r'メモ',('read_notepad',)),
    ('memory','記憶・知識の参照',r'(?:記憶|日記|知識|エピソード|ノート).*(?:読んで|探して|検索して|確認して)',r'記憶|日記|知識|エピソード|ノート',('search_memory','read_memory_document','read_memory_entry')),
    ('memory','記憶・ノートの更新案',r'(?:記憶|日記|知識|ノート).*(?:更新して|記録して|保存して|残して)',r'記憶|日記|知識|ノート',('propose_document',)),
    ('world','場所の参照',r'(?:現在地|場所|世界設定).*(?:確認して|調べて|見せて|読んで)',r'現在地|場所|世界設定',('read_world',)),
    ('world','移動・場所の更新',r'移動して|(?:場所|世界設定).*(?:更新して|登録して)',r'移動|場所|世界設定',('change_location',)),
    ('items','所持品・衣装の参照',r'(?:所持品|アイテム|衣装|服装).*(?:確認して|調べて|見せて)',r'所持品|アイテム|衣装|服装',('read_lifestyle',)),
    ('items','所持品・衣装の操作',r'着替えて|(?:アイテム|所持品|衣装).*(?:作って|登録して|消費して|配置して|回収して|更新して)',r'アイテム|所持品|衣装|着替',('change_lifestyle',)),
    ('time','タイマー・予約',r'(?:タイマー|アラーム|予約).*(?:設定して|登録して|予約して)',r'タイマー|アラーム|予約',('change_schedule','manage_action_schedule')),
    ('time','手紙・お知らせ',r'(?:手紙|お知らせ|通知).*(?:残して|作って|登録して)',r'手紙|お知らせ|通知',('leave_letter_for_user','send_user_notification')),
    ('web','URLの本文取得',r'(?:https?://|URL|ページ|PDF).*(?:読んで|取得して|確認して)',r'URL|ページ|PDF|本文',('read_url',)),
    ('external','外部連携の参照',r'(?:カレンダー|Discord|X|Twitter).*(?:確認して|調べて|読んで|取得して)',r'カレンダー|Discord|X|Twitter',('read_external',)),
    ('external','外部送信の確認待ち案',r'(?:Discord|X|Twitter|カレンダー).*(?:送って|投稿して|登録して|更新して)',r'Discord|X|Twitter|カレンダー',('propose_external',)),
    ('autonomy','目標・研究の参照',r'(?:目標|研究|手順|振返り|問い).*(?:読んで|確認して|調べて)',r'目標|研究|手順|振返り|問い',('read_autonomy','read_purpose','read_research')),
    ('autonomy','目標・研究の更新',r'(?:目標|研究|手順|振返り).*(?:記録して|保存して|更新して|作って)',r'目標|研究|手順|振返り',('change_autonomy',)),
)
EXTENDED_PLEDGE=re.compile(r'(?:移動|着替え|予約|配置|回収|消費|送信|投稿)(?:します|する)(?=ね|よ|[。！!\s]|$)|(?:読みます|読んでみる|見せます|見せる)(?=ね|よ|[。！!\s]|$)')


def announcement_followup(request, answer, pool, visible, registry, *, extended=False):
    """Return a gentle hint only when the original permitted operation is live.

    The caller enforces once-per-job, zero formal calls, cancellation and budget.
    No classifier call, new tool visibility, or operation takes place here.
    """
    request=_prose(request);answer=_prose(answer)
    # Ignore quotations of sample predictions, while retaining category labels.
    pledge=lambda text: PLEDGE.search(text) or extended and EXTENDED_PLEDGE.search(text)
    answer=re.sub(r'[「『“\"]([^」』”\"\n]+)[」』”\"]',
                  lambda m:'' if pledge(m.group(1)) else m.group(0),answer)
    action=ACTION.search(request) or extended and re.search(r'着替えて|送って|見せて',request)
    if not action or META.search(request) or not pledge(answer) or CLARIFY.search(answer):return None
    matches=[]
    for category,label,request_pattern,answer_pattern,names in OPERATIONS+(EXTENDED_OPERATIONS if extended else ()):
        if re.search(request_pattern,request) and (re.search(answer_pattern,answer) or any(name in answer for name in names)):
            matches.append((category,label,names))
    # Prefer a concrete image request over incidental words such as "探す".
    if matches:category,label,candidates=matches[0]
    else:
        mentioned=[name for name in pool if name not in ('request_capability','request_tool_permission') and re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',answer)]
        if len(mentioned)!=1:return None
        name=mentioned[0];category=registry.get(name).category;label=name;candidates=(name,)
    available=set(candidates)&set(pool)
    if not available:return None
    if available & set(visible):hint='必要なら、現在提示されているツールで続きを進められます。'
    elif 'request_capability' in visible:
        hint=f'必要なら、提示されている request_capability で {category} を要求できます。'
    else:return None
    return dict(categories=[category],instruction='【アプリからの続行案内・今回1回】\n'+label+'の依頼を続けられます。'+hint+'確認が必要な点があれば質問できます。')
